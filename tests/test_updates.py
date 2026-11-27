from datetime import date

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
    info = parse_release(release(), "4.0.0")
    assert info == UpdateInfo("4.1.0", "https://github.com/airtzro/mamarmiton/releases/tag/v4.1.0",
                              "https://x/recette.exe")


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


def test_banner_buttons(monkeypatch):
    import tkinter

    import pytest

    from mamarmiton.ui.app import App
    opened = []
    monkeypatch.setattr("mamarmiton.ui.app.webbrowser.open", lambda url: opened.append(url))
    try:
        app = App()
    except tkinter.TclError as exc:
        pytest.skip(f"no usable display: {exc}")
    try:
        info = UpdateInfo("9.9.9", "https://github.com/airtzro/mamarmiton/releases/tag/v9.9.9")
        app.show_update(info)
        app.update()
        app._update_banner.download_button.invoke()
        assert opened == [info.url]
        app.show_update(info)
        app._update_banner.later_button.invoke()
        assert app.settings.snoozed_version == "9.9.9"
        app._update_banner = None
        app.show_update(info)  # snoozed: nothing shown
        assert app._update_banner is None
    finally:
        app.destroy()
