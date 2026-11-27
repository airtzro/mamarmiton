"""ApiClient.import_status / import_recipe against a stub server (httpx MockTransport, no server package)."""

import httpx
import pytest

from mamarmiton.remote import ApiClient, AuthError, OfflineError
from mamarmiton.remote.client import IMPORT_TIMEOUT, ApiError


def client_for(handler, token="t"):
    return ApiClient("http://x", token=token, http=httpx.Client(transport=httpx.MockTransport(handler)))


STATUS = {"available": True, "ocr": True, "ai": False, "max_files": 8, "max_mb": 30, "accepts": [".pdf", ".jpg"]}
ANSWER = {"recipe": {"nom": "Crêpes", "pour": 4, "ingredients": [{"nom": "farine", "quantité": "250 g"}],
                     "etapes": ["Mélanger."], "tags": [], "source": ""},
          "dish_photo": 1, "method": "ia", "warnings": ["Vérifiez les quantités."]}


def test_import_status_is_sent_with_the_session():
    seen = {}

    def handler(request):
        seen.update(method=request.method, path=request.url.path, auth=request.headers.get("authorization"))
        return httpx.Response(200, json=STATUS)

    assert client_for(handler).import_status() == STATUS
    assert seen == {"method": "GET", "path": "/import/status", "auth": "Bearer t"}


def test_old_server_without_import_is_unavailable_not_an_error():
    client = client_for(lambda request: httpx.Response(404, json={"detail": "Not Found"}))
    assert client.import_status() == {"available": False}


def test_import_status_keeps_auth_and_network_errors():
    with pytest.raises(AuthError):
        client_for(lambda r: httpx.Response(401, json={"detail": "Reconnectez-vous."})).import_status()

    def down(request):
        raise httpx.ConnectError("down")

    with pytest.raises(OfflineError):
        client_for(down).import_status()


def test_import_recipe_sends_files_and_text_as_multipart(tmp_path):
    page1 = tmp_path / "page1.jpg"
    page1.write_bytes(b"\xff\xd8 jpeg one")
    page2 = tmp_path / "page2.png"
    page2.write_bytes(b"\x89PNG two")
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, type=request.headers["content-type"], body=request.read(),
                    timeout=request.extensions.get("timeout", {}), auth=request.headers.get("authorization"))
        return httpx.Response(200, json=ANSWER)

    assert client_for(handler).import_recipe([page1, page2], "Pour 4 personnes") == ANSWER
    assert seen["path"] == "/import/recipe" and seen["auth"] == "Bearer t"
    assert seen["type"].startswith("multipart/form-data")
    body = seen["body"]
    assert body.count(b'name="files"') == 2
    assert b'filename="page1.jpg"' in body and b"jpeg one" in body and b"Content-Type: image/jpeg" in body
    assert b'filename="page2.png"' in body and b"Content-Type: image/png" in body
    assert b'name="text"' in body and "Pour 4 personnes".encode() in body
    assert seen["timeout"]["read"] >= 180 and IMPORT_TIMEOUT >= 180  # the server can take two minutes


def test_import_recipe_text_only_is_still_multipart():
    seen = {}

    def handler(request):
        seen.update(type=request.headers["content-type"], body=request.read())
        return httpx.Response(200, json=ANSWER)

    client_for(handler).import_recipe([], "Gâteau au yaourt")
    assert seen["type"].startswith("multipart/form-data")
    assert b'name="files"' not in seen["body"] and "Gâteau au yaourt".encode() in seen["body"]


def test_import_recipe_needs_something():
    with pytest.raises(ValueError):
        client_for(lambda r: httpx.Response(200, json=ANSWER)).import_recipe([], "  ")


@pytest.mark.parametrize("status", [400, 413, 415, 422, 429])
def test_import_refusals_keep_the_french_detail(tmp_path, status):
    page = tmp_path / "p.jpg"
    page.write_bytes(b"x")
    client = client_for(lambda r: httpx.Response(status, json={"detail": "Aucune recette trouvée sur ces photos."}))
    with pytest.raises(ApiError) as err:
        client.import_recipe([page])
    assert err.value.status == status and err.value.message == "Aucune recette trouvée sur ces photos."
    assert not isinstance(err.value, OfflineError)


def test_busy_server_is_offline_with_its_reason(tmp_path):
    page = tmp_path / "p.jpg"
    page.write_bytes(b"x")
    client = client_for(lambda r: httpx.Response(503, json={"detail": "Le serveur est occupé, réessayez."}))
    with pytest.raises(OfflineError) as err:
        client.import_recipe([page])
    assert err.value.status == 503 and err.value.message == "Le serveur est occupé, réessayez."
    # a 5xx without a JSON reason keeps the generic sentence
    client = client_for(lambda r: httpx.Response(502, text="<html>Bad gateway</html>"))
    with pytest.raises(OfflineError) as err:
        client.import_recipe([page])
    assert err.value.message == "Le serveur ne répond pas correctement."
