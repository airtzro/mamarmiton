"""Import from a website address (POST /import/url): the network is replaced by a local test server
and a fake DNS, and the address guard (safeweb) is checked on its own."""

import base64
import http.server
import json
import socket
import threading

import pytest
from fastapi.testclient import TestClient

from mamarmiton_server import safeweb
from mamarmiton_server.api import create_app
from mamarmiton_server.config import Settings

from test_api import register  # noqa: F401
from test_import import FakeModel, FakeOcr, jpeg

PUBLIC = "93.184.216.34"
LD = {"@context": "https://schema.org", "@type": "Recipe", "name": "Gratin dauphinois", "recipeYield": "4",
      "recipeIngredient": ["800 g de pommes de terre", "20 cl de crème"],
      "recipeInstructions": [{"@type": "HowToStep", "text": "Éplucher."}, {"@type": "HowToStep", "text": "Cuire."}],
      "image": "/photo.jpg"}


def page(ld=LD):
    return f'<html><body><script type="application/ld+json">{json.dumps(ld)}</script></body></html>'.encode()


class Site(http.server.BaseHTTPRequestHandler):
    routes: dict = {}
    seen: list = []

    def do_GET(self):
        Site.seen.append((self.path, self.headers.get("Host")))
        status, headers, body = Site.routes.get(self.path, (404, {}, b""))
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def site(monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Site.routes = {"/gratin": (200, {"Content-Type": "text/html; charset=utf-8"}, page()),
                   "/photo.jpg": (200, {"Content-Type": "image/jpeg"}, jpeg((400, 300)))}
    Site.seen = []
    connected = []
    dns = {"recettes.example": [PUBLIC], "cache.example": ["10.0.0.5"], "mixed.example": [PUBLIC, "192.168.1.9"],
           "v6.example": ["::1"], "mapped.example": ["::ffff:127.0.0.1"]}

    real = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kw):
        if host == "inconnu.example":
            raise socket.gaierror("unknown")
        if host not in dns:
            return real(host, port, *args, **kw)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in dns[host]]

    def open_socket(ip, port, host, tls_on, timeout):  # the vetted address is recorded, the test server answers
        connected.append((ip, port, host))
        return socket.create_connection(("127.0.0.1", server.server_address[1]), timeout)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(safeweb, "_open_socket", open_socket)
    yield connected
    server.shutdown()


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, login_attempts=3, login_window_s=60)


@pytest.fixture
def client(settings):
    app = create_app(settings)
    app.state.importer.ocr = FakeOcr(ok=False)
    app.state.importer.model = FakeModel(ok=False)
    return TestClient(app)


@pytest.fixture
def auth(client, settings):
    return register(client, settings, "mamounette")


def post(client, auth, url):
    return client.post("/import/url", headers=auth, json={"url": url})


def test_needs_login(client):
    assert client.post("/import/url", json={"url": "https://recettes.example/gratin"}).status_code == 401


def test_imports_recipe_and_photo(client, auth, site):
    r = post(client, auth, "https://recettes.example/gratin")  # only the socket is faked, not the protocol
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recipe"]["nom"] == "Gratin dauphinois"
    assert body["recipe"]["ingredients"][0]["nom"].startswith("pommes de terre")
    assert body["method"] == "site" and body["dish_photo"] is None
    assert body["photo"]["content_type"] == "image/jpeg"
    assert base64.b64decode(body["photo"]["data"])[:2] == b"\xff\xd8"
    assert site and all(c == (PUBLIC, 443, "recettes.example") for c in site)  # the vetted address, the real name
    assert Site.seen[0][1] == "recettes.example"  # Host header keeps the name


def test_page_without_photo_warns(client, auth, site):
    Site.routes["/photo.jpg"] = (404, {}, b"")
    body = post(client, auth, "http://recettes.example/gratin").json()
    assert body["photo"] is None and body["warnings"]


def test_no_recipe_on_page(client, auth, site):
    Site.routes["/vide"] = (200, {"Content-Type": "text/html"}, b"<html>rien</html>")
    r = post(client, auth, "http://recettes.example/vide")
    assert r.status_code == 422 and "Aucune recette" in r.json()["detail"]


def test_status_advertises_url(client, auth):
    assert client.get("/import/status", headers=auth).json()["url"] is True


def test_redirect_to_a_public_page_is_followed(client, auth, site):
    Site.routes["/court"] = (302, {"Location": "/gratin"}, b"")
    assert post(client, auth, "http://recettes.example/court").status_code == 200


def test_redirect_loop_stops(client, auth, site):
    Site.routes["/boucle"] = (302, {"Location": "/boucle"}, b"")
    r = post(client, auth, "http://recettes.example/boucle")
    assert r.status_code == 422 and "trop de pages" in r.json()["detail"]
    assert len(Site.seen) == safeweb.MAX_REDIRECTS + 1


@pytest.mark.parametrize("target", ["http://127.0.0.1/", "http://10.1.2.3/admin", "http://192.168.1.1/",
                                    "http://169.254.169.254/latest/meta-data", "http://[::1]/",
                                    "http://cache.example/", "ftp://recettes.example/x",
                                    "http://recettes.example:8080/"])
def test_redirect_to_internal_address_is_refused(client, auth, site, target):
    Site.routes["/piege"] = (301, {"Location": target}, b"")
    r = post(client, auth, "http://recettes.example/piege")
    assert r.status_code == 422 and "pas autorisée" in r.json()["detail"]
    assert [path for path, _ in Site.seen] == ["/piege"]  # the second hop never connected


@pytest.mark.parametrize("url", ["http://127.0.0.1/gratin", "http://localhost/", "http://10.0.0.1/",
                                 "http://192.168.0.254/", "http://169.254.169.254/", "http://[::1]/",
                                 "http://[::ffff:10.0.0.1]/", "http://0.0.0.0/", "http://cache.example/",
                                 "http://mixed.example/", "http://v6.example/", "http://mapped.example/"])
def test_internal_addresses_are_refused(client, auth, site, url):
    r = post(client, auth, url)
    assert r.status_code in (400, 422)
    assert Site.seen == [] and site == []  # nothing was contacted


@pytest.mark.parametrize("url", ["ftp://recettes.example/x", "file:///etc/passwd", "gopher://recettes.example/",
                                 "javascript:alert(1)", "http://recettes.example:8080/",
                                 "https://recettes.example:22/", "http://recettes.example:6379/",
                                 "http://user:pw@recettes.example/gratin", ""])
def test_odd_schemes_ports_and_credentials_are_refused(client, auth, site, url):
    r = post(client, auth, url)
    assert r.status_code == 400
    assert Site.seen == [] and site == []


def test_refused_address_does_not_use_up_the_daily_limit(client, auth, site):
    for _ in range(50):
        assert post(client, auth, "http://10.0.0.1/").status_code in (400, 422)
    assert post(client, auth, "http://recettes.example/gratin").status_code == 200


def test_photo_on_an_internal_address_is_skipped(client, auth, site):
    Site.routes["/gratin"] = (200, {"Content-Type": "text/html"}, page(dict(LD, image="http://192.168.1.5/p.jpg")))
    body = post(client, auth, "http://recettes.example/gratin").json()
    assert body["photo"] is None and body["recipe"]["nom"] == "Gratin dauphinois"
    assert [path for path, _ in Site.seen] == ["/gratin"]


def test_photo_that_is_not_an_image_is_skipped(client, auth, site):
    Site.routes["/photo.jpg"] = (200, {"Content-Type": "image/jpeg"}, b"<html>pas une image</html>")
    assert post(client, auth, "http://recettes.example/gratin").json()["photo"] is None


def test_page_too_big(client, auth, site, monkeypatch):
    monkeypatch.setattr(safeweb, "MAX_PAGE_BYTES", 100)
    r = post(client, auth, "http://recettes.example/gratin")
    assert r.status_code == 422 and "volumineuse" in r.json()["detail"]


def test_site_errors_are_in_french(client, auth, site):
    r = post(client, auth, "http://recettes.example/nulle-part")
    assert r.status_code == 422 and "404" in r.json()["detail"]
    Site.routes["/interdit"] = (403, {}, b"")
    assert "refuse" in post(client, auth, "http://recettes.example/interdit").json()["detail"]


def test_unknown_host(client, auth, site):
    r = post(client, auth, "http://inconnu.example/")
    assert r.status_code == 422 and "Impossible de joindre" in r.json()["detail"]


# ------------------------------------------------------------------ the guard alone
@pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"])
def test_public_addresses(ip):
    assert safeweb.is_public(ip)


@pytest.mark.parametrize("ip", ["127.0.0.1", "127.1.2.3", "10.0.0.1", "172.16.5.5", "192.168.1.1",
                                "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1",
                                "255.255.255.255", "::1", "::", "fe80::1", "fc00::1", "fd12::1", "ff02::1",
                                "::ffff:127.0.0.1", "::ffff:192.168.1.1", "::ffff:10.0.0.1",
                                "2002:7f00:1::", "2002:c0a8:101::", "64:ff9b::7f00:1", "2001:0:4136:e378::1",
                                "nonsense"])
def test_non_public_addresses(ip):
    assert not safeweb.is_public(ip)
