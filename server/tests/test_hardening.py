import io

from PIL import Image

from mamarmiton_server import images, security
from mamarmiton_server.api import MAX_JSON_BODY

from test_api import PASSWORD, RID, RID2, client, jpeg_bytes, put, recipe, register, settings  # noqa: F401


def test_oversized_json_body_refused(client, settings):
    h = register(client, settings, "alice")
    big = {"recipe": recipe(notes="x" * (MAX_JSON_BODY + 10)), "base_version": None}
    assert client.put(f"/recipes/{RID}", json=big, headers=h).status_code == 413


def test_oversized_streamed_body_refused(client, settings):
    h = register(client, settings, "alice")

    def chunks():  # no Content-Length: chunked
        for _ in range(MAX_JSON_BODY // 65536 + 2):
            yield b"x" * 65536

    r = client.put(f"/recipes/{RID}", content=chunks(), headers={**h, "content-type": "application/json"})
    assert r.status_code == 413


def test_oversized_upload_refused_before_parsing(client, settings):
    h = register(client, settings, "alice")
    assert put(client, h).status_code == 200
    blob = b"\0" * ((settings.max_image_mb + 2) * 1024 * 1024)
    r = client.post(f"/recipes/{RID}/images", files={"file": ("a.jpg", blob, "image/jpeg")}, headers=h)
    assert r.status_code == 413


def test_infinity_and_huge_numbers_are_422_not_500(client, settings):
    h = register(client, settings, "alice")
    r = client.put(f"/recipes/{RID}", content=b'{"recipe": {"nom": "a", "temps": Infinity}, "base_version": null}',
                   headers={**h, "content-type": "application/json"})
    assert r.status_code == 422
    assert put(client, h, pour=10**9).status_code == 422


def test_pixel_bomb_refused_before_decode(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 2000)
    try:
        images.process(jpeg_bytes((64, 48)))
    except images.BadImage:
        return
    raise AssertionError("image above the pixel limit was accepted")


def test_since_out_of_range_is_422(client, settings):
    h = register(client, settings, "alice")
    assert client.get("/recipes?since=99999999999999999999999", headers=h).status_code == 422
    assert client.get("/recipes?since=-1", headers=h).status_code == 422


def test_responses_not_cacheable_and_nosniff(client, settings):
    h = register(client, settings, "alice")
    r = client.get("/me", headers=h)
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "hash" not in r.text and "password" not in r.text


def test_password_change_is_rate_limited(client, settings):
    h = register(client, settings, "alice")
    for _ in range(settings.login_attempts):
        assert client.post("/me/password", json={"old_password": "wrong-one!", "new_password": "another pass 1"},
                           headers=h).status_code == 403
    r = client.post("/me/password", json={"old_password": PASSWORD, "new_password": "another pass 1"}, headers=h)
    assert r.status_code == 429


def test_limiter_forgets_empty_entries():
    lim = security.LoginLimiter(2, 0)  # zero window: everything expires at once
    for i in range(6000):
        lim.failed("1.1.1.1", f"user{i}")
    lim.failed("1.1.1.1", "x")
    assert len(lim._fails) < 2000  # swept at 5000 entries, not growing forever


def test_share_authz_matrix(client, settings):
    a, b, c = (register(client, settings, n) for n in ("alice", "bob", "carol"))
    assert put(client, a).status_code == 200
    img = client.post(f"/recipes/{RID}/images", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")}, headers=a).json()["id"]
    # strangers see and touch nothing
    assert client.get(f"/recipes/{RID}", headers=b).status_code == 404
    assert client.get(f"/recipes/{RID}/images/{img}", headers=b).status_code == 404
    assert client.delete(f"/recipes/{RID}", headers=b).status_code == 404
    assert put(client, b, base=1).status_code == 404
    # read share: no write, no image changes, no re-share
    assert client.put(f"/recipes/{RID}/shares/bob", json={"can_edit": False}, headers=a).status_code == 204
    assert client.get(f"/recipes/{RID}/images/{img}", headers=b).status_code == 200
    assert put(client, b, base=1).status_code == 403
    assert client.post(f"/recipes/{RID}/images", files={"file": ("a.jpg", jpeg_bytes(), "image/jpeg")}, headers=b).status_code == 403
    assert client.delete(f"/recipes/{RID}/images/{img}", headers=b).status_code == 403
    assert client.put(f"/recipes/{RID}/shares/carol", json={"can_edit": True}, headers=b).status_code == 403
    assert client.put("/shares/book/carol", json={"can_edit": True}, headers=b).status_code == 204  # bob's own (empty) book
    assert client.get(f"/recipes/{RID}", headers=c).status_code == 404
    # edit share can write but not delete or re-share
    client.put(f"/recipes/{RID}/shares/bob", json={"can_edit": True}, headers=a)
    assert put(client, b, base=1).status_code == 200
    assert client.delete(f"/recipes/{RID}", headers=b).status_code == 403
    assert client.put(f"/recipes/{RID}/shares/carol", json={"can_edit": True}, headers=b).status_code == 403
    # revoked: gone again
    client.delete(f"/recipes/{RID}/shares/bob", headers=a)
    assert client.get(f"/recipes/{RID}", headers=b).status_code == 404
    assert client.get(f"/recipes/{RID}/images/{img}", headers=b).status_code == 404
    # B cannot hijack A's id by creating it
    assert put(client, b).status_code == 404
