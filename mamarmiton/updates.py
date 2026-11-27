"""Check GitHub for a newer release, download it and swap it in place of the running executable.

The flow, driven by the UI (``ui/update_flow.py``), off the Tk thread:

1. ``fetch_latest`` / ``check_latest``: read the latest release, its asset for this platform, the asset's
   size and (when GitHub gives it) its sha256 digest.
2. ``download``: stream the asset into a temporary file next to the executable, so the final rename is
   atomic and stays on one filesystem; verify the digest (or at least the size).
3. ``install_and_restart``: put the new file in place of the executable (Windows: rename the running exe
   to ``.old`` first, since it cannot be overwritten), start it, and let the UI close the old instance
   through its normal close path. The new instance waits for the old one to exit (``wait_for_previous``)
   before it reads the recipes, and deletes the ``.old`` file (``cleanup_old``).

Only the packaged executable (``sys.frozen``) replaces itself; from source the UI opens the release page.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from mamarmiton import tls

log = logging.getLogger(__name__)

API_URL = "https://api.github.com/repos/airtzro/mamarmiton/releases/latest"
RELEASES_URL = "https://github.com/airtzro/mamarmiton/releases/latest"
USER_AGENT = "mamarmiton-update-check"
# Testing only: point the check at a local fake release (http://127.0.0.1:port/...). Loopback addresses
# may then be served over plain http; everything else still has to be https from GitHub.
API_ENV = "MAMARMITON_UPDATE_API"
# Set by the old instance for the new one: the process id to wait for before reading the recipes.
WAIT_PID_ENV = "MAMARMITON_WAIT_PID"

MIN_SIZE = 1_000_000  # a real build is tens of MB; anything smaller is an error page or a truncated file
MAX_SIZE = 1_500_000_000
PARTIAL_PREFIX = ".mamarmiton-update-"
CHUNK = 256 * 1024

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")
_SHA256_RE = re.compile(r"^sha256:([0-9a-fA-F]{64})$")


class UpdateError(Exception):
    """A failed download or install. ``str(exc)`` is a short French message for the user."""


class UpdateCancelled(UpdateError):
    def __init__(self) -> None:
        super().__init__("Mise à jour annulée.")


@dataclass(frozen=True)
class UpdateInfo:
    version: str  # "4.1.0"
    url: str  # release page
    download_url: str = ""  # this platform's asset (.exe, or the Linux binary), "" when absent
    size: int = 0  # asset size in bytes, 0 when unknown
    digest: str = ""  # lowercase hex sha256 of the asset, "" when GitHub does not give one


def parse_version(text: str) -> tuple[int, int, int] | None:
    """"v4.1.0" -> (4, 1, 0). None for anything else (prereleases such as "4.1.0-rc1" included)."""
    m = _VERSION_RE.match((text or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


LINUX_ASSET = "mamarmiton-linux-x86_64"


def _is_our_asset(name: str, platform: str) -> bool:
    name = name.lower()
    if platform.startswith("linux"):
        return name == LINUX_ASSET
    return name.endswith(".exe")


def _digest(value: object) -> str:
    m = _SHA256_RE.match(str(value or "").strip())
    return m.group(1).lower() if m else ""


def _size(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def parse_release(data: object, current_version: str, platform: str = sys.platform) -> UpdateInfo | None:
    """The update described by a GitHub "latest release" payload, if newer than ``current_version``."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    latest = parse_version(str(data.get("tag_name") or ""))
    current = parse_version(current_version)
    if latest is None or current is None or latest <= current:
        return None
    download, size, digest = "", 0, ""
    for asset in data.get("assets") or []:
        if isinstance(asset, dict) and _is_our_asset(str(asset.get("name", "")), platform):
            download = str(asset.get("browser_download_url") or "")
            size = _size(asset.get("size"))
            digest = _digest(asset.get("digest"))
            break
    url = str(data.get("html_url") or "")
    if not url.startswith("https://"):
        return None
    return UpdateInfo(".".join(map(str, latest)), url, download, size, digest)


def api_url() -> str:
    return os.environ.get(API_ENV) or API_URL


def fetch_latest(current_version: str, timeout: float = 5) -> UpdateInfo | None:
    """Ask GitHub for the latest release: None when this version is the latest. Raises when offline."""
    req = urllib.request.Request(api_url(), headers={"User-Agent": USER_AGENT,
                                                     "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout, context=tls.context()) as resp:
        data = json.loads(resp.read(1_000_000).decode("utf-8"))
    return parse_release(data, current_version)


def check_latest(current_version: str, timeout: float = 5) -> UpdateInfo | None:
    """Like ``fetch_latest``, but any failure (offline, private repo, bad JSON) gives None."""
    try:
        return fetch_latest(current_version, timeout)
    except Exception as exc:  # silent by design
        log.debug("Update check failed: %s", exc)
        return None


def enabled() -> bool:
    """Only the packaged .exe checks by default; ``MAMARMITON_UPDATE_CHECK=1`` forces it from source."""
    return bool(getattr(sys, "frozen", False)) or os.environ.get("MAMARMITON_UPDATE_CHECK") == "1"


def due(last_check: str, today: date | None = None) -> bool:
    """True when the last check (ISO date) was not today."""
    return last_check != (today or date.today()).isoformat()


# ------------------------------------------------------------------ install
def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable() -> Path:
    """The running program. In a PyInstaller one-file build this is the .exe / binary itself."""
    return Path(sys.executable).resolve()


def old_path(exe: Path) -> Path:
    return exe.with_name(exe.name + ".old")


def _folder_writable(folder: Path) -> bool:
    """Really try: ``os.access`` says yes for Program Files on Windows."""
    try:
        fd, name = tempfile.mkstemp(dir=folder, prefix=PARTIAL_PREFIX)
    except OSError:
        return False
    os.close(fd)
    try:
        os.unlink(name)
    except OSError:
        pass
    return True


def self_update_blocker(info: UpdateInfo) -> str | None:
    """Why this app cannot replace itself with ``info`` (a French sentence), or None when it can."""
    if not frozen():
        return "La mise à jour automatique ne concerne que l'application installée."
    if not info.download_url:
        return "Cette version n'existe pas encore en mise à jour automatique pour cet ordinateur."
    if not _folder_writable(executable().parent):
        return "Mamarmiton est installé dans un dossier protégé : il ne peut pas se mettre à jour tout seul."
    return None


def _testing() -> bool:
    return bool(os.environ.get(API_ENV))


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _allowed_url(url: str) -> bool:
    """https from GitHub (the release link redirects to *.githubusercontent.com); loopback when testing."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if _testing() and parts.scheme in ("http", "https") and _is_loopback(host):
        return True
    return parts.scheme == "https" and (host == "github.com" or host.endswith(".githubusercontent.com"))


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed_url(newurl):
            raise UpdateError("Le téléchargement a été redirigé vers une adresse inconnue : il est annulé.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, timeout: float):
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=tls.context()), _SafeRedirects)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"})
    return opener.open(req, timeout=timeout)


def download(info: UpdateInfo, progress: Callable[[int, int], None] | None = None,
             cancelled: Callable[[], bool] | None = None, folder: Path | None = None,
             timeout: float = 30) -> Path:
    """Download the release asset next to the executable (or into ``folder``) and verify it.

    ``progress(done, total)`` is called from this thread as bytes arrive (total 0 when unknown);
    ``cancelled()`` is polled between chunks. Returns the verified temporary file. Raises
    ``UpdateError`` (``UpdateCancelled`` when cancelled); the partial file is always removed then.
    """
    if not info.download_url or not _allowed_url(info.download_url):
        raise UpdateError("Adresse de téléchargement refusée.")
    folder = folder or executable().parent
    try:
        fd, name = tempfile.mkstemp(dir=folder, prefix=PARTIAL_PREFIX)
    except OSError as exc:
        raise UpdateError("Impossible d'écrire le fichier de mise à jour dans le dossier de Mamarmiton.") from exc
    path = Path(name)
    sha = hashlib.sha256()
    done = 0
    try:
        with os.fdopen(fd, "wb") as out:
            try:
                resp = _open(info.download_url, timeout)
            except UpdateError:
                raise
            except (OSError, ValueError) as exc:
                raise UpdateError("Le téléchargement a échoué. Vérifiez la connexion à internet.") from exc
            with resp:
                header = resp.headers.get("Content-Length")
                total = info.size or (int(header) if header and header.isdigit() else 0)
                limit = info.size or MAX_SIZE
                if progress:
                    progress(0, total)
                while True:
                    if cancelled and cancelled():
                        raise UpdateCancelled()
                    try:
                        chunk = resp.read(CHUNK)
                    except OSError as exc:
                        raise UpdateError("Le téléchargement s'est interrompu. Vérifiez la connexion à internet.") from exc
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > limit:
                        raise UpdateError("Le fichier téléchargé est plus gros que prévu : la mise à jour est annulée.")
                    sha.update(chunk)
                    out.write(chunk)
                    if progress:
                        progress(done, total)
            out.flush()
            os.fsync(out.fileno())
        if done < MIN_SIZE:
            raise UpdateError("Le fichier téléchargé est incomplet : la mise à jour est annulée.")
        if info.digest:
            if sha.hexdigest() != info.digest:
                raise UpdateError("Le fichier téléchargé est abîmé : la mise à jour est annulée.")
        elif info.size and done != info.size:
            raise UpdateError("Le fichier téléchargé est incomplet : la mise à jour est annulée.")
        return path
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def child_env(environ: dict[str, str] | None = None, meipass: str | None = None) -> dict[str, str]:
    """The environment for the new instance: as if started by the user, not by this PyInstaller process.

    ``PYINSTALLER_RESET_ENVIRONMENT=1`` (PyInstaller >= 6.9) makes the new one-file process unpack its
    own copy instead of reusing this process's temporary folder, which is deleted when we exit. The
    bootloader's private variables go, and so does anything pointing into that folder (Tcl/Tk paths,
    the library path, which is restored from ``LD_LIBRARY_PATH_ORIG``).
    """
    env = dict(os.environ if environ is None else environ)
    meipass = meipass if meipass is not None else getattr(sys, "_MEIPASS", "")
    for key in list(env):
        if key == "_MEIPASS2" or key.startswith("_PYI_"):
            del env[key]
    orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig is not None:
        env["LD_LIBRARY_PATH"] = orig
    if meipass:
        base = os.path.normcase(os.path.normpath(meipass))

        def inside(part: str) -> bool:
            part = os.path.normcase(os.path.normpath(part)) if part else ""
            return bool(part) and (part == base or part.startswith(base + os.sep))

        for key, value in list(env.items()):
            if base not in os.path.normcase(value):
                continue
            kept = [p for p in value.split(os.pathsep) if not inside(p)]
            if kept and any(kept):
                env[key] = os.pathsep.join(kept)
            else:
                del env[key]
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env[WAIT_PID_ENV] = str(os.getpid())
    return env


def _spawn(exe: Path) -> subprocess.Popen:
    kwargs: dict = dict(close_fds=True, env=child_env(), stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen([str(exe)], **kwargs)


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def install_and_restart(new_file: Path) -> subprocess.Popen:
    """Put ``new_file`` in place of the running executable and start it.

    The caller then closes this instance through its normal close path (saves, sync); the new one
    waits for this process to exit before it starts. Raises ``UpdateError`` and leaves the current
    version in place when anything fails. Safe to call off the Tk thread.
    """
    new_file = Path(new_file)
    if not frozen():
        _discard(new_file)
        raise UpdateError("La mise à jour automatique ne concerne que l'application installée.")
    target = executable()
    if sys.platform.startswith("win"):
        return _install_windows(new_file, target)
    return _install_posix(new_file, target)


def _install_windows(new_file: Path, target: Path) -> subprocess.Popen:
    # A running .exe can be renamed but not overwritten or deleted.
    old = old_path(target)
    failed = UpdateError("La mise à jour n'a pas pu être installée. Votre version actuelle reste en place.")
    try:
        if os.path.lexists(old):
            os.remove(old)
        os.rename(target, old)
    except OSError as exc:
        log.exception("Update: cannot move the running executable aside")
        _discard(new_file)
        raise failed from exc
    try:
        os.rename(new_file, target)
        return _spawn(target)
    except OSError as exc:
        log.exception("Update: cannot install or start the new version, rolling back")
        try:
            if os.path.lexists(target):
                os.rename(target, new_file)  # the new file is not running: it can be moved back out
            os.rename(old, target)
        except OSError:
            log.exception("Update: rollback failed")
        _discard(new_file)
        raise failed from exc


def _install_posix(new_file: Path, target: Path) -> subprocess.Popen:
    try:
        os.chmod(new_file, 0o755)
        os.replace(new_file, target)  # the running program keeps its (now unlinked) file
    except OSError as exc:
        log.exception("Update: cannot replace the executable")
        _discard(new_file)
        raise UpdateError("La mise à jour n'a pas pu être installée. Votre version actuelle reste en place.") from exc
    try:
        return _spawn(target)
    except OSError as exc:
        log.exception("Update: installed but cannot start the new version")
        raise UpdateError("La mise à jour est installée, mais Mamarmiton n'a pas pu redémarrer : "
                          "fermez-le et rouvrez-le.") from exc


# ------------------------------------------------------------------ the new instance, at startup
def _pid_alive(pid: int) -> bool:
    if sys.platform.startswith("win"):
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT: still running
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # never on Windows: there os.kill terminates the process
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def wait_for_previous(timeout: float = 20, poll: float = 0.2) -> None:
    """After an update, wait until the old instance has saved and exited before reading the recipes."""
    raw = os.environ.pop(WAIT_PID_ENV, "")
    os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)  # our own children start fresh anyway
    if not raw.isdigit() or int(raw) == os.getpid():
        return
    pid = int(raw)
    deadline = time.monotonic() + timeout
    while _pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(poll)
    if _pid_alive(pid):
        log.warning("Update: the previous instance (%s) is still running, starting anyway", pid)


def cleanup_old(attempts: int = 1, delay: float = 2) -> None:
    """Delete the ``.old`` executable and any partial download left next to the executable.

    On Windows the old exe may still be closing: retry a few times. Never raises.
    """
    if not frozen():
        return
    try:
        exe = executable()
        leftovers = [old_path(exe)] + list(exe.parent.glob(PARTIAL_PREFIX + "*"))
    except OSError:
        return
    for attempt in range(max(1, attempts)):
        remaining = []
        for path in leftovers:
            try:
                if os.path.lexists(path):
                    os.remove(path)
            except OSError:
                remaining.append(path)
        leftovers = remaining
        if not leftovers:
            return
        if attempt + 1 < attempts:
            time.sleep(delay)
    log.debug("Update: could not delete %s yet", leftovers)


def cleanup_old_in_background() -> None:
    threading.Thread(target=cleanup_old, kwargs={"attempts": 6, "delay": 2}, daemon=True,
                     name="update-cleanup").start()
