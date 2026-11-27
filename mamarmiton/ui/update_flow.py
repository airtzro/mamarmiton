"""The one-click update: banner offer -> download with progress -> swap the executable -> restart.

All network and disk work runs in a worker thread; the Tk thread polls a queue with ``after``.
"""

from __future__ import annotations

import logging
import queue
import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from mamarmiton import updates, usage
from mamarmiton.ui.widgets import UpdateBanner

if TYPE_CHECKING:
    from mamarmiton.ui.app import App

log = logging.getLogger(__name__)

POLL_MS = 150


def poll_thread(widget, work: Callable[[], Any], on_ok: Callable[[Any], None],
                on_error: Callable[[BaseException], None] | None = None) -> None:
    """Run ``work`` in a thread and deliver its result on the Tk thread through a polled queue.

    Nothing is delivered once ``widget`` has been destroyed.
    """
    results: queue.Queue = queue.Queue()

    def target() -> None:
        try:
            results.put((True, work()))
        except BaseException as exc:  # noqa: BLE001 - reported to the UI
            results.put((False, exc))

    def poll() -> None:
        try:
            if not widget.winfo_exists():
                return
        except Exception:  # noqa: BLE001 - Tk root gone
            return
        try:
            ok, value = results.get_nowait()
        except queue.Empty:
            widget.after(POLL_MS, poll)
            return
        if ok:
            on_ok(value)
        elif on_error:
            on_error(value)

    threading.Thread(target=target, daemon=True, name="update-worker").start()
    widget.after(POLL_MS, poll)


def percent_text(done: int, total: int) -> str:
    if total > 0:
        return f"Téléchargement… {min(100, done * 100 // total)} %"
    return f"Téléchargement… {done / 1_000_000:.0f} Mo"


class UpdateFlow:
    """Owns one update banner and walks it through offer / downloading / restarting / error."""

    def __init__(self, app: "App", info: updates.UpdateInfo, message: str | None = None,
                 on_later: Callable[[], None] | None = None):
        self.app = app
        self.info = info
        self.message = message or f"Nouvelle version {info.version} disponible"
        self.on_later = on_later
        self.state = "offer"
        self._cancel = threading.Event()
        self._progress = (0, info.size)
        self.banner = UpdateBanner(app, self.message, self.start, self.later, download_text="Mettre à jour",
                                   primary=True, close_on_download=False)
        self._stacked = message is not None  # a long custom message: buttons go underneath
        if self._stacked:
            self.offer()

    # ------------------------------------------------------------ states
    def alive(self) -> bool:
        try:
            return bool(self.banner.winfo_exists())
        except Exception:  # noqa: BLE001
            return False

    def offer(self) -> None:
        self.state = "offer"
        self.banner.download_button, self.banner.later_button = self.banner.set_buttons(self.message, [
            ("Mettre à jour", self.start, "primary", False),
            ("Plus tard", self.later, "link", True),
        ], stacked=self._stacked)

    def later(self) -> None:
        if self.on_later:
            self.on_later()

    def fail(self, message: str) -> None:
        self.state = "error"
        self.banner.set_buttons(message, [
            ("Ouvrir la page de téléchargement", self.open_page, "link", True),
            ("Fermer", lambda: None, "link", True),
        ], stacked=True)

    def open_page(self) -> None:
        usage.hit("update_banner_click")
        webbrowser.open(self.info.url)

    # ------------------------------------------------------------ download
    def start(self) -> None:
        if self.state != "offer":
            return
        reason = updates.self_update_blocker(self.info)
        if reason:
            self.fail(reason)
            return
        if not self.app.can_leave_editor():
            return
        usage.hit("update_install")
        self.state = "downloading"
        self._cancel.clear()
        self._progress = (0, self.info.size)
        self.banner.show_progress(percent_text(0, self.info.size), 0, self.cancel)
        poll_thread(self.banner, self._download, self._downloaded, self._download_failed)
        self.banner.after(POLL_MS, self._show_progress)

    def _download(self) -> Path:
        def progress(done: int, total: int) -> None:
            self._progress = (done, total)

        return updates.download(self.info, progress, self._cancel.is_set)

    def _show_progress(self) -> None:
        if self.state != "downloading" or not self.alive():
            if not self.alive():
                self._cancel.set()  # window closed: stop the download, the partial file is removed
            return
        done, total = self._progress
        self.banner.set_progress(percent_text(done, total), done / total if total else 0)
        self.banner.after(POLL_MS, self._show_progress)

    def cancel(self) -> None:
        if self.state == "downloading":
            self._cancel.set()
            self.state = "cancelling"
            self.banner.show_status("Annulation…")

    def _download_failed(self, exc: BaseException) -> None:
        if isinstance(exc, updates.UpdateCancelled):
            self.offer()
            return
        if not isinstance(exc, updates.UpdateError):
            log.error("Update download failed", exc_info=exc)
            exc = updates.UpdateError("Le téléchargement a échoué.")
        self.fail(str(exc))

    def _downloaded(self, path: Path) -> None:
        if self.state == "cancelling":
            poll_thread(self.banner, lambda: path.unlink(missing_ok=True), lambda _r: self.offer())
            return
        # the user may have started editing during the download
        if not self.app.can_leave_editor():
            poll_thread(self.banner, lambda: path.unlink(missing_ok=True), lambda _r: self.offer())
            return
        self.state = "restarting"
        self.banner.show_status("Redémarrage…")
        poll_thread(self.banner, lambda: updates.install_and_restart(path), self._restarted, self._install_failed)

    # ------------------------------------------------------------ install
    def _restarted(self, _process) -> None:
        log.info("Update to %s installed, restarting", self.info.version)
        self.app.shutdown()  # the normal close path: saves and sync flush, then the window goes

    def _install_failed(self, exc: BaseException) -> None:
        if not isinstance(exc, updates.UpdateError):
            log.error("Update install failed", exc_info=exc)
            exc = updates.UpdateError("La mise à jour n'a pas pu être installée.")
        self.fail(str(exc))
