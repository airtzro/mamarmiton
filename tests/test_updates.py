import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from mamarmiton import updates
from mamarmiton.updates import UpdateInfo, due, parse_release, parse_version


def release(tag="v4.1.0", **kw):
    data = {"tag_name": tag, "html_url": "https://github.com/airtzro/mamarmiton/releases/tag/" + tag,
            "assets": [{"name": "notes.txt", "browser_download_url": "https://x/notes.txt"},
                       {"name": "recette.exe", "browser_download_url": "https://x/recette.exe"}]}
    data.update(kw)
    return data


def test_parse_version():
    assert parse_version("v4.1.0") == (4, 1, 0)
    assert parse_version("4.10") == (4, 10, 0)
    assert parse_version("v4.1.0-rc1") is None
    assert parse_version("") is None
    assert parse_version("nightly") is None


def test_newer_release():
    info = parse_release(release(), "4.0.0", platform="win32")
    assert info == UpdateInfo("4.1.0", "https://github.com/airtzro/mamarmiton/releases/tag/v4.1.0",
                              "https://x/recette.exe")


def test_linux_gets_the_linux_binary_not_the_exe():
    data = release()
    assert parse_release(data, "4.0.0", platform="linux").download_url == ""  # no Linux build in this release
    data["assets"].append({"name": "mamarmiton-linux-x86_64", "browser_download_url": "https://x/mamarmiton-linux-x86_64"})
    assert parse_release(data, "4.0.0", platform="linux").download_url == "https://x/mamarmiton-linux-x86_64"
    assert parse_release(data, "4.0.0", platform="win32").download_url == "https://x/recette.exe"


def test_numeric_not_lexical():
    assert parse_release(release("v4.10.0"), "4.9.0") is not None
    assert parse_release(release("v4.9.0"), "4.10.0") is None


def test_same_or_older():
    assert parse_release(release("v4.0.0"), "4.0.0") is None
    assert parse_release(release("v3.9.9"), "4.0.0") is None


def test_ignores_prerelease_draft_and_garbage():
    assert parse_release(release(prerelease=True), "4.0.0") is None
    assert parse_release(release(draft=True), "4.0.0") is None
    assert parse_release(release("v5.0.0-beta"), "4.0.0") is None
    assert parse_release([], "4.0.0") is None
    assert parse_release({"message": "Not Found"}, "4.0.0") is None
    assert parse_release(release(html_url="javascript:x"), "4.0.0") is None


def test_missing_asset():
    info = parse_release(release(assets=[]), "4.0.0")
    assert info and info.download_url == ""


def test_check_latest_fails_silently(monkeypatch):
    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(updates.urllib.request, "urlopen", boom)
    assert updates.check_latest("4.0.0") is None


def test_due():
    assert due("", date(2026, 1, 2))
    assert due("2026-01-01", date(2026, 1, 2))
    assert not due("2026-01-02", date(2026, 1, 2))


def test_enabled(monkeypatch):
    monkeypatch.delenv("MAMARMITON_UPDATE_CHECK", raising=False)
    assert not updates.enabled()
    monkeypatch.setenv("MAMARMITON_UPDATE_CHECK", "1")
    assert updates.enabled()


# ---------------------------------------------------------------- size and digest
DIGEST = "AB" * 32


def test_parse_size_and_digest():
    data = release()
    data["assets"][1].update(size=34_567_890, digest="sha256:" + DIGEST)
    info = parse_release(data, "4.0.0", platform="win32")
    assert info.size == 34_567_890
    assert info.digest == DIGEST.lower()


def test_size_and_digest_absent_or_garbage():
    info = parse_release(release(), "4.0.0", platform="win32")  # older releases: no digest field
    assert (info.size, info.digest) == (0, "")
    data = release()
    data["assets"][1].update(size="12", digest="md5:abc")
    info = parse_release(data, "4.0.0", platform="win32")
    assert (info.size, info.digest) == (0, "")
    data["assets"][1].update(size=True, digest="sha256:xyz")
    assert parse_release(data, "4.0.0", platform="win32").digest == ""


# ---------------------------------------------------------------- download
PAYLOAD = os.urandom(1_300_000)


@pytest.fixture
def server(monkeypatch):
    """A local HTTP server: /asset serves PAYLOAD, /small a tiny file, /redirect points elsewhere."""
    routes: dict[str, tuple[int, dict, bytes]] = {
        "/asset": (200, {}, PAYLOAD),
        "/small": (200, {}, b"<html>Not found</html>"),
        "/redirect": (302, {"Location": "http://example.invalid/evil"}, b""),
        "/hop": (302, {"Location": "/asset"}, b""),
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            code, headers, body = routes.get(self.path, (404, {}, b"nope"))
            self.send_response(code)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class Server(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # a cancelled download closes the connection mid-transfer

    httpd = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setenv(updates.API_ENV, base + "/api")  # testing mode: loopback http allowed
    yield base, routes
    httpd.shutdown()
    httpd.server_close()


def info_for(url, size=0, digest=""):
    return UpdateInfo("9.9.9", "https://github.com/airtzro/mamarmiton/releases/tag/v9.9.9", url, size, digest)


def leftovers(folder: Path):
    return sorted(p.name for p in folder.iterdir())


def test_download_verifies_the_digest(server, tmp_path):
    base, _ = server
    seen = []
    path = updates.download(info_for(base + "/asset", len(PAYLOAD), hashlib.sha256(PAYLOAD).hexdigest()),
                            progress=lambda done, total: seen.append((done, total)), folder=tmp_path)
    assert path.parent == tmp_path and path.read_bytes() == PAYLOAD
    assert seen[0] == (0, len(PAYLOAD)) and seen[-1] == (len(PAYLOAD), len(PAYLOAD))


def test_download_follows_same_host_redirects(server, tmp_path):
    base, _ = server
    path = updates.download(info_for(base + "/hop", len(PAYLOAD)), folder=tmp_path)
    assert path.read_bytes() == PAYLOAD


def test_bad_digest_is_refused_and_partial_file_removed(server, tmp_path):
    base, _ = server
    with pytest.raises(updates.UpdateError, match="abîmé"):
        updates.download(info_for(base + "/asset", len(PAYLOAD), "0" * 64), folder=tmp_path)
    assert leftovers(tmp_path) == []


def test_without_digest_the_size_is_checked(server, tmp_path):
    base, _ = server
    with pytest.raises(updates.UpdateError):
        updates.download(info_for(base + "/asset", len(PAYLOAD) + 10), folder=tmp_path)
    assert leftovers(tmp_path) == []
    with pytest.raises(updates.UpdateError, match="plus gros"):
        updates.download(info_for(base + "/asset", len(PAYLOAD) - 10), folder=tmp_path)
    assert leftovers(tmp_path) == []
    assert updates.download(info_for(base + "/asset"), folder=tmp_path).read_bytes() == PAYLOAD  # size unknown


def test_tiny_files_are_refused(server, tmp_path):
    base, _ = server
    with pytest.raises(updates.UpdateError, match="incomplet"):
        updates.download(info_for(base + "/small"), folder=tmp_path)
    assert leftovers(tmp_path) == []


def test_http_errors_and_cancel(server, tmp_path):
    base, _ = server
    with pytest.raises(updates.UpdateError, match="connexion"):
        updates.download(info_for(base + "/missing"), folder=tmp_path)
    with pytest.raises(updates.UpdateCancelled):
        updates.download(info_for(base + "/asset"), cancelled=lambda: True, folder=tmp_path)
    assert leftovers(tmp_path) == []


def test_only_https_github_outside_tests(server, tmp_path, monkeypatch):
    base, _ = server
    with pytest.raises(updates.UpdateError, match="redirigé"):  # redirect to a non-loopback http host
        updates.download(info_for(base + "/redirect"), folder=tmp_path)
    assert leftovers(tmp_path) == []
    monkeypatch.delenv(updates.API_ENV)
    with pytest.raises(updates.UpdateError, match="refusée"):
        updates.download(info_for(base + "/asset"), folder=tmp_path)
    assert updates._allowed_url("https://github.com/airtzro/mamarmiton/releases/download/v4.1.0/recette.exe")
    assert updates._allowed_url("https://objects.githubusercontent.com/github-production-release-asset/1")
    assert not updates._allowed_url("http://github.com/x")
    assert not updates._allowed_url("https://github.com.evil.example/x")
    assert not updates._allowed_url("https://127.0.0.1/x")


def test_fetch_latest_uses_the_override(server):
    base, routes = server
    routes["/api"] = (200, {}, json.dumps(release()).encode())
    assert updates.fetch_latest("4.0.0").version == "4.1.0"
    assert updates.fetch_latest("4.1.0") is None
    routes["/api"] = (500, {}, b"down")
    with pytest.raises(Exception):
        updates.fetch_latest("4.0.0")
    assert updates.check_latest("4.0.0") is None


# ---------------------------------------------------------------- install
class FakePopen:
    calls: list = []

    def __init__(self, args, **kwargs):
        FakePopen.calls.append((args, kwargs))


@pytest.fixture
def frozen_exe(tmp_path, monkeypatch):
    folder = tmp_path / "app"
    folder.mkdir()
    exe = folder / "recette.exe"
    exe.write_bytes(b"old version")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI123"), raising=False)
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", str(tmp_path / "_MEI123"))
    monkeypatch.setenv("_MEIPASS2", str(tmp_path / "_MEI123"))
    FakePopen.calls = []
    monkeypatch.setattr(updates.subprocess, "Popen", FakePopen)
    new = folder / ".mamarmiton-update-abc"
    new.write_bytes(b"new version")
    return exe, new


def test_linux_swap_and_restart(frozen_exe, monkeypatch):
    exe, new = frozen_exe
    monkeypatch.setattr(sys, "platform", "linux")
    updates.install_and_restart(new)
    assert exe.read_bytes() == b"new version" and not new.exists()
    assert os.stat(exe).st_mode & 0o777 == 0o755
    (args, kwargs), = FakePopen.calls
    assert args == [str(exe)]
    assert kwargs["start_new_session"] and kwargs["close_fds"] and "creationflags" not in kwargs
    env = kwargs["env"]
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert env[updates.WAIT_PID_ENV] == str(os.getpid())
    assert "_MEIPASS2" not in env and not any(k.startswith("_PYI_") for k in env)


def test_linux_replace_failure_keeps_the_old_version(frozen_exe, monkeypatch):
    exe, new = frozen_exe
    monkeypatch.setattr(sys, "platform", "linux")

    def refuse(*_a):
        raise PermissionError("read-only")

    monkeypatch.setattr(updates.os, "replace", refuse)
    with pytest.raises(updates.UpdateError, match="actuelle reste"):
        updates.install_and_restart(new)
    assert exe.read_bytes() == b"old version" and not new.exists() and FakePopen.calls == []


def test_windows_renames_the_running_exe_aside(frozen_exe, monkeypatch):
    exe, new = frozen_exe
    monkeypatch.setattr(sys, "platform", "win32")
    stale = exe.with_name("recette.exe.old")
    stale.write_bytes(b"older version")
    updates.install_and_restart(new)
    assert exe.read_bytes() == b"new version"
    assert stale.read_bytes() == b"old version"  # the stale .old was removed first
    (args, kwargs), = FakePopen.calls
    assert args == [str(exe)]
    assert kwargs["creationflags"] == 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    assert kwargs["close_fds"] and "start_new_session" not in kwargs
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_windows_rolls_back_when_the_new_exe_cannot_start(frozen_exe, monkeypatch):
    exe, new = frozen_exe
    monkeypatch.setattr(sys, "platform", "win32")

    def no_start(*_a, **_k):
        raise OSError("blocked by antivirus")

    monkeypatch.setattr(updates.subprocess, "Popen", no_start)
    with pytest.raises(updates.UpdateError, match="actuelle reste"):
        updates.install_and_restart(new)
    assert exe.read_bytes() == b"old version"
    assert leftovers(exe.parent) == ["recette.exe"]


def test_windows_rolls_back_when_the_move_fails(frozen_exe, monkeypatch):
    exe, new = frozen_exe
    monkeypatch.setattr(sys, "platform", "win32")
    real_rename = os.rename
    renames = []

    def rename(src, dst):
        renames.append((Path(src).name, Path(dst).name))
        if Path(src) == new:
            raise PermissionError("in use")
        real_rename(src, dst)

    monkeypatch.setattr(updates.os, "rename", rename)
    with pytest.raises(updates.UpdateError):
        updates.install_and_restart(new)
    assert renames == [("recette.exe", "recette.exe.old"), (new.name, "recette.exe"),
                       ("recette.exe.old", "recette.exe")]
    assert exe.read_bytes() == b"old version" and leftovers(exe.parent) == ["recette.exe"]
    assert FakePopen.calls == []


def test_windows_cannot_move_the_running_exe(frozen_exe, monkeypatch):
    exe, new = frozen_exe
    monkeypatch.setattr(sys, "platform", "win32")

    def rename(src, dst):
        raise PermissionError("denied")

    monkeypatch.setattr(updates.os, "rename", rename)
    with pytest.raises(updates.UpdateError):
        updates.install_and_restart(new)
    assert exe.read_bytes() == b"old version" and leftovers(exe.parent) == ["recette.exe"]


def test_install_from_source_refuses(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    new = tmp_path / "new"
    new.write_bytes(b"x")
    with pytest.raises(updates.UpdateError, match="installée"):
        updates.install_and_restart(new)
    assert not new.exists()


def test_child_env_forgets_the_pyinstaller_parent():
    mei = "/tmp/_MEIabc"
    env = updates.child_env({
        "HOME": "/home/me", "_MEIPASS2": mei, "_PYI_ARCHIVE_FILE": "/x", "_PYI_PARENT_PROCESS_LEVEL": "1",
        "LD_LIBRARY_PATH": mei, "LD_LIBRARY_PATH_ORIG": "/opt/lib", "TCL_LIBRARY": mei + "/_tcl_data",
        "PATH": os.pathsep.join([mei, "/usr/bin"]), "OTHER": "/tmp/_MEIabcdef/not-ours",
    }, meipass=mei)
    assert env == {"HOME": "/home/me", "LD_LIBRARY_PATH": "/opt/lib", "PATH": "/usr/bin",
                   "OTHER": "/tmp/_MEIabcdef/not-ours", "PYINSTALLER_RESET_ENVIRONMENT": "1",
                   updates.WAIT_PID_ENV: str(os.getpid())}
    env = updates.child_env({"LD_LIBRARY_PATH": mei}, meipass=mei)
    assert "LD_LIBRARY_PATH" not in env


def test_self_update_blocker(frozen_exe, monkeypatch):
    info = info_for("https://github.com/x/recette.exe")
    assert updates.self_update_blocker(info) is None
    assert "cet ordinateur" in updates.self_update_blocker(info_for(""))
    monkeypatch.setattr(updates, "_folder_writable", lambda _f: False)
    assert "protégé" in updates.self_update_blocker(info)
    monkeypatch.delattr(sys, "frozen")
    assert "installée" in updates.self_update_blocker(info)


def test_folder_writable(tmp_path):
    assert updates._folder_writable(tmp_path) and leftovers(tmp_path) == []
    assert not updates._folder_writable(tmp_path / "missing")


def test_cleanup_old(frozen_exe):
    exe, new = frozen_exe
    old = exe.with_name("recette.exe.old")
    old.write_bytes(b"old")
    updates.cleanup_old()
    assert leftovers(exe.parent) == ["recette.exe"]  # the .old and the partial download are gone
    updates.cleanup_old()  # nothing left: no error


def test_cleanup_old_ignores_errors_and_source_runs(frozen_exe, monkeypatch):
    exe, new = frozen_exe
    old = exe.with_name("recette.exe.old")
    old.write_bytes(b"old")

    def locked(_p):
        raise PermissionError("still running")

    monkeypatch.setattr(updates.os, "remove", locked)
    updates.cleanup_old(attempts=2, delay=0)  # does not raise
    assert old.exists()
    monkeypatch.setattr(updates.os, "remove", os.remove)
    monkeypatch.delattr(sys, "frozen")
    updates.cleanup_old()
    assert old.exists()  # from source: never touches files next to the interpreter


def test_wait_for_previous(monkeypatch):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.4)"])
    monkeypatch.setenv(updates.WAIT_PID_ENV, str(proc.pid))
    threading.Thread(target=proc.wait, daemon=True).start()  # reap it, as its parent would
    updates.wait_for_previous(timeout=10, poll=0.05)
    assert proc.poll() is not None
    assert updates.WAIT_PID_ENV not in os.environ
    monkeypatch.setenv(updates.WAIT_PID_ENV, str(os.getpid()))
    updates.wait_for_previous(timeout=10)  # never waits for itself
    updates.wait_for_previous(timeout=10)  # unset: returns at once


# ---------------------------------------------------------------- UI
def make_app():
    import tkinter

    from mamarmiton.ui.app import App
    try:
        return App()
    except tkinter.TclError as exc:
        pytest.skip(f"no usable display: {exc}")


def pump_until(app, condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if condition():
            return True
        time.sleep(0.02)
    return condition()


INFO = UpdateInfo("9.9.9", "https://github.com/airtzro/mamarmiton/releases/tag/v9.9.9",
                  "https://github.com/airtzro/mamarmiton/releases/download/v9.9.9/recette.exe", 100, "")


def test_banner_later_snoozes():
    app = make_app()
    try:
        app.show_update(INFO)
        app.update()
        assert app._update_flow.banner.download_button.cget("text") == "Mettre à jour"
        app._update_flow.banner.later_button.invoke()
        assert app.settings.snoozed_version == "9.9.9"
        assert not app._update_flow.alive()
        app._update_flow = None
        app.show_update(INFO)  # snoozed: nothing shown
        assert app._update_flow is None
        app.show_update(INFO, force=True)  # "Rechercher une mise à jour" ignores the snooze
        assert app._update_flow.alive()
    finally:
        app.destroy()


def test_banner_from_source_falls_back_to_the_release_page(monkeypatch):
    opened = []
    monkeypatch.setattr("mamarmiton.ui.update_flow.webbrowser.open", lambda url: opened.append(url))
    monkeypatch.delattr(sys, "frozen", raising=False)
    app = make_app()
    try:
        app.show_update(INFO)
        flow = app._update_flow
        flow.banner.download_button.invoke()
        assert flow.state == "error" and "installée" in flow.banner.label.cget("text")
        page, = [b for b in flow.banner.actions.winfo_children()
                 if b.cget("text") == "Ouvrir la page de téléchargement"]
        page.invoke()
        assert opened == [INFO.url] and not flow.alive()
    finally:
        app.destroy()


def test_banner_progress_cancel_and_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "self_update_blocker", lambda _info: None)
    release_download = threading.Event()
    downloaded = tmp_path / "new.exe"

    def fake_download(info, progress, cancelled):
        progress(45, 100)
        while not release_download.wait(0.02):
            if cancelled():
                raise updates.UpdateCancelled()
        downloaded.write_bytes(b"new")
        return downloaded

    installed, closed = [], []
    monkeypatch.setattr(updates, "download", fake_download)
    monkeypatch.setattr(updates, "install_and_restart", lambda path: installed.append(path))
    app = make_app()
    monkeypatch.setattr(app, "shutdown", lambda: closed.append(1))
    try:
        app.show_update(INFO)
        flow = app._update_flow
        flow.banner.download_button.invoke()
        assert flow.state == "downloading" and flow.banner.progress_bar is not None
        assert pump_until(app, lambda: "45" in flow.banner.label.cget("text"))
        assert flow.banner.label.cget("text") == "Téléchargement… 45 %"
        flow.banner.cancel_button.invoke()
        assert pump_until(app, lambda: flow.state == "offer")
        assert flow.banner.download_button.cget("text") == "Mettre à jour"

        flow.banner.download_button.invoke()
        release_download.set()
        assert pump_until(app, lambda: closed)
        assert installed == [downloaded]
        assert flow.state == "restarting" and flow.banner.label.cget("text") == "Redémarrage…"
    finally:
        app.destroy()


def test_banner_shows_download_errors(monkeypatch):
    monkeypatch.setattr(updates, "self_update_blocker", lambda _info: None)

    def broken(info, progress, cancelled):
        raise updates.UpdateError("Le fichier téléchargé est abîmé : la mise à jour est annulée.")

    monkeypatch.setattr(updates, "download", broken)
    app = make_app()
    try:
        app.show_update(INFO)
        flow = app._update_flow
        flow.banner.download_button.invoke()
        assert pump_until(app, lambda: flow.state == "error")
        assert "abîmé" in flow.banner.label.cget("text")
    finally:
        app.destroy()


def test_banner_asks_before_dropping_unsaved_edits(monkeypatch):
    from tkinter import messagebox
    monkeypatch.setattr(updates, "self_update_blocker", lambda _info: None)
    started = []
    monkeypatch.setattr(updates, "download", lambda *a: started.append(1) or Path("x"))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
    app = make_app()
    try:
        app.new_recipe()
        app.update()
        app.editor.e_nom.insert(0, "Presque finie")
        app.show_update(INFO)
        app._update_flow.banner.download_button.invoke()
        assert app._update_flow.state == "offer" and started == []
    finally:
        app.destroy()


def test_help_dialog_check_now(monkeypatch):
    from mamarmiton.ui.dialogs import HelpDialog
    answers = [None, INFO]
    monkeypatch.setattr(updates, "fetch_latest", lambda _v: answers.pop(0))
    app = make_app()
    try:
        app.settings.snoozed_version = "9.9.9"
        dialog = HelpDialog(app, app)
        dialog.update_button.invoke()
        assert pump_until(app, lambda: dialog.update_status.cget("text") == "Vous avez la dernière version.")
        dialog.update_button.invoke()
        assert pump_until(app, lambda: app._update_flow is not None)
        assert app._update_flow.alive() and not dialog.winfo_exists()
    finally:
        app.destroy()


