import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mamarmiton_server.api import WEB_DIR, create_app
from mamarmiton_server.config import Settings


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path)))


def test_index_is_served_with_a_strict_csp(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    for part in ("default-src 'self'", "img-src 'self' blob: data:", "frame-ancestors 'none'", "script-src 'self'"):
        assert part in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "<script" in r.text and 'type="module"' in r.text


def test_page_has_no_inline_script_or_style(client):
    html = client.get("/").text
    assert "<style" not in html and " style=" not in html
    assert "onclick=" not in html
    for tag in html.split("<script")[1:]:
        assert 'src="' in tag.split(">")[0]


@pytest.mark.parametrize("path,content_type", [
    ("/app/app.js", "text/javascript"),
    ("/app/quantities.js", "text/javascript"),
    ("/app/style.css", "text/css"),
    ("/app/manifest.webmanifest", "application/manifest+json"),
    ("/app/icon.svg", "image/svg+xml"),
    ("/app/fonts/AtkinsonHyperlegible-Regular.ttf", "font/ttf"),
    ("/app/fonts/DMSerifDisplay-Regular.ttf", "font/ttf"),
    ("/app/fonts/OFL-Atkinson.txt", "text/plain"),
])
def test_assets_have_the_right_content_type(client, path, content_type):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(content_type)
    assert r.headers["x-content-type-options"] == "nosniff"


def test_code_is_never_cached_but_fonts_are(client):
    assert client.get("/app/app.js").headers["cache-control"] == "no-cache"
    assert "max-age" in client.get("/app/fonts/DMSerifDisplay-Regular.ttf").headers["cache-control"]


def test_every_module_imported_by_the_page_exists(client):
    import re
    for js in WEB_DIR.glob("*.js"):
        for target in re.findall(r'from "\./([\w.]+)"', js.read_text(encoding="utf-8")):
            assert (WEB_DIR / target).is_file(), f"{js.name} imports missing {target}"
            assert client.get(f"/app/{target}").status_code == 200


def test_fonts_ship_with_their_licences():
    names = {p.name for p in (WEB_DIR / "fonts").iterdir()}
    assert {"OFL-Atkinson.txt", "OFL-DMSerif.txt"} <= names


def test_unknown_asset_is_404(client):
    assert client.get("/app/nope.js").status_code == 404
    assert client.get("/app/").status_code == 404
    assert client.get("/app/fonts").status_code == 404  # a directory


@pytest.mark.parametrize("path", [
    "/app/../api.py", "/app/%2e%2e/api.py", "/app/..%2fapi.py", "/app/fonts/../../api.py",
    "/app/%2e%2e%2f%2e%2e%2fapi.py", "/app/..\\api.py", "/app//etc/passwd", "/app/%00.js",
])
def test_path_traversal_is_refused(client, path):
    r = client.get(path)
    assert r.status_code in (400, 404)
    assert "create_app" not in r.text


def test_only_known_file_types_are_served(client, tmp_path, monkeypatch):
    (WEB_DIR / "secret.py").write_text("x = 1")
    try:
        assert client.get("/app/secret.py").status_code == 404
    finally:
        (WEB_DIR / "secret.py").unlink()


def test_api_routes_are_unaffected(client):
    assert client.get("/health").json()["ok"] is True
    assert client.get("/recipes").status_code == 401
    assert client.get("/me").status_code == 401
    assert client.post("/auth/login", json={"username": "x", "password": "y"}).status_code == 401
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert "content-security-policy" not in client.get("/health").headers


def test_service_worker_is_served_with_whole_site_scope(client):
    from mamarmiton_server import __version__
    r = client.get("/app/sw.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.headers["service-worker-allowed"] == "/"
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "__VERSION__" not in r.text and f'"{__version__}"' in r.text
    assert client.head("/app/sw.js").status_code == 200


def test_service_worker_does_not_touch_the_api_or_cache_tokens(client):
    code = client.get("/app/sw.js").text
    assert "Authorization" not in code and "/auth/" not in code
    # only the shell and the photo URLs are handled
    assert "PHOTO_PATH" in code and 'startsWith("/app/")' in code


def test_service_worker_precaches_only_files_the_server_serves(client):
    import re
    code = client.get("/app/sw.js").text
    block = code.split("const SHELL = [", 1)[1].split("];", 1)[0]
    urls = re.findall(r'"(/[^"]*)"', block)
    assert "/" in urls and "/app/offline.js" in urls
    for url in urls:
        assert client.get(url).status_code == 200, url
    # every script and the stylesheet of the page must be precached
    for js in WEB_DIR.glob("*.js"):
        if js.name != "sw.js":
            assert f"/app/{js.name}" in urls, js.name


def test_csp_needs_no_change_for_the_service_worker(client):
    csp = client.get("/").headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert "worker-src" not in csp  # falls back to script-src 'self'


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_javascript_unit_tests():
    """Pure web modules (timers, shopping, quantities port) are tested with node's built-in runner."""
    tests = Path(__file__).parent / "js"
    files = sorted(str(f) for f in tests.glob("*.test.mjs"))  # node 24 does not accept a directory here
    assert files
    result = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
