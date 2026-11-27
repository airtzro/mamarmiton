"""Usage counters and "Signaler un problème" on the desktop (no Tk)."""

import json
from datetime import date, timedelta

import pytest

from mamarmiton import config, report, usage
from mamarmiton.remote.client import ApiError, AuthError, OfflineError
from mamarmiton.ui.remote_logic import SyncRunner

TODAY = date.today().isoformat()


class StubClient:
    def __init__(self, fail=None):
        self.usage, self.reports, self.fail = [], [], fail

    def send_usage(self, client_id, days):
        if self.fail:
            raise self.fail
        self.usage.append((client_id, days))

    def send_report(self, payload):
        if self.fail:
            raise self.fail
        self.reports.append(payload)


@pytest.fixture
def counter():
    return usage.counter()


def test_counts_per_day_and_event_and_ignores_unknown(counter):
    for _ in range(3):
        usage.hit("recipe_open")
    usage.hit("search")
    usage.hit("not_a_feature")
    usage.hit("active")  # only the counter itself sets this one
    assert counter.snapshot() == {TODAY: {"recipe_open": 3, "search": 1, "active": 1}}


def test_persistence_between_runs(counter):
    usage.hit("recipe_open")
    saved = json.loads((config.data_dir() / "usage.json").read_text(encoding="utf-8"))
    assert saved == {TODAY: {"recipe_open": 1, "active": 1}}
    again = usage.Counter(config.data_dir() / "usage.json")
    again.hit("recipe_open")
    assert again.snapshot()[TODAY]["recipe_open"] == 2


def test_unreadable_file_starts_empty():
    (config.data_dir() / "usage.json").write_text("{not json", encoding="utf-8")
    c = usage.Counter(config.data_dir() / "usage.json")
    c.hit("search")
    assert c.snapshot()[TODAY]["search"] == 1


def test_pruned_after_90_days(counter):
    old = (date.today() - timedelta(days=91)).isoformat()
    keep = (date.today() - timedelta(days=89)).isoformat()
    (config.data_dir() / "usage.json").write_text(json.dumps({old: {"search": 4}, keep: {"search": 2}}))
    c = usage.Counter(config.data_dir() / "usage.json")
    assert list(c.snapshot()) == [keep]
    c.hit("search")
    assert old not in json.loads((config.data_dir() / "usage.json").read_text())


def test_flush_sends_a_batch_and_clears_on_success(counter):
    usage.hit("recipe_open")
    usage.hit("recipe_open")
    client = StubClient()
    assert counter.flush(client, force=True) is True
    (client_id, days), = client.usage
    assert client_id.startswith("desktop/")
    assert days == {TODAY: {"recipe_open": 2, "active": 1}}
    assert counter.snapshot() == {}
    assert counter.flush(client, force=True) is False  # nothing left: no empty request
    usage.hit("recipe_open")  # the same day again: "active" is not reported twice
    counter.flush(client, force=True)
    assert client.usage[1][1] == {TODAY: {"recipe_open": 1}}


def test_flush_failure_keeps_counts_and_is_silent(counter):
    usage.hit("search")
    for error in (OfflineError(), AuthError(401, "x"), ApiError(404, "old server"), RuntimeError("boom")):
        assert counter.flush(StubClient(fail=error), force=True) is False
    assert counter.snapshot() == {TODAY: {"search": 1, "active": 1}}


def test_hits_during_a_send_are_kept(counter):
    usage.hit("search")

    class Racy(StubClient):
        def send_usage(self, client_id, days):
            usage.hit("search")  # clicked while the request was in flight
            super().send_usage(client_id, days)

    counter.flush(Racy(), force=True)
    assert counter.snapshot() == {TODAY: {"search": 1}}


def test_attempts_are_throttled(counter):
    usage.hit("search")
    failing = StubClient(fail=OfflineError())
    counter.flush(failing)
    ok = StubClient()
    assert counter.flush(ok) is False and ok.usage == []  # tried a moment ago
    assert counter.flush(ok, force=True) is True


def test_opt_out_counts_nothing_and_forgets(counter):
    usage.hit("search")
    usage.set_enabled(False)
    usage.hit("search")
    assert counter.snapshot() == {} and counter.flush(StubClient(), force=True) is False
    usage.set_enabled(True)
    usage.hit("search")
    assert counter.snapshot()[TODAY]["search"] == 1


def test_hit_never_raises(monkeypatch):
    monkeypatch.setattr(usage.Counter, "hit", lambda *a: 1 / 0)
    usage.hit("search")


def test_local_only_users_never_send():
    usage.hit("search")

    class LocalStore:  # a plain RecipeStore has no client
        pass

    SyncRunner._send_extras(LocalStore())  # must not raise or send
    assert usage.counter().snapshot()[TODAY]["search"] == 1


def test_sync_sends_usage_and_queued_reports():
    usage.hit("search")
    report.enqueue({"message": "bug", "client": "desktop"})
    client = StubClient()

    class Store:
        status = "ok"

    store = Store()
    store.client = client
    SyncRunner._send_extras(store)
    assert client.usage and client.reports == [{"message": "bug", "client": "desktop"}]
    assert report.pending() == 0
    offline = Store()
    offline.status, offline.client = "offline", StubClient()
    usage.hit("search")
    SyncRunner._send_extras(offline)
    assert offline.client.usage == []


def test_every_desktop_event_is_documented_in_the_explanation():
    assert usage.EXPLANATION and "anonymes" in usage.EXPLANATION
    assert len(set(usage.EVENTS)) == len(usage.EVENTS)


# ------------------------------------------------------------------ reports
def test_redact_hides_secrets():
    text = ("GET /recipes Authorization: Bearer abc.def-123_XYZ\n"
            'body {"password": "mon mot de passe", "username": "maman"}\n'
            "login password=hunter2 ok\ncode ABCD-EFGH-JKLM-NPQR\n"
            "token " + "x" * 43 + "\nnormal line about a gâteau")
    out = report.redact(text)
    for secret in ("abc.def", "mon mot de passe", "hunter2", "ABCD-EFGH", "x" * 40):
        assert secret not in out
    assert "normal line about a gâteau" in out and "maman" in out


def test_log_tail_is_last_200_lines_redacted_and_bounded():
    lines = [f"2026-11-27 INFO x: line {i} token=secret{i}" for i in range(500)]
    config.log_file().write_text("\n".join(lines), encoding="utf-8")
    tail = report.log_tail().splitlines()
    assert len(tail) == 200 and tail[-1].startswith("2026-11-27 INFO x: line 499") and "secret" not in "".join(tail)
    config.log_file().write_text("é" * 200_000, encoding="utf-8")
    assert len(report.log_tail().encode("utf-8")) <= report.MAX_LOG_BYTES
    config.log_file().unlink()
    assert report.log_tail() == ""


def test_build_report_fields():
    payload = report.build("  ça plante  ", attach_log=False)
    assert payload["message"] == "ça plante" and payload["client"] == "desktop" and payload["log_tail"] == ""
    assert payload["app_version"] and payload["os"]
    assert len(report.build("x" * 9000)["message"]) == report.MAX_MESSAGE


def test_report_is_queued_when_offline_and_sent_later():
    payload = report.build("bug", attach_log=False)
    assert report.submit(StubClient(fail=OfflineError()), payload) is False
    assert report.submit(None, payload) is False  # not signed in
    assert report.pending() == 2
    client = StubClient()
    assert report.send_pending(client) == 2 and report.pending() == 0 and len(client.reports) == 2
    assert report.submit(client, payload) is True


def test_queue_stops_on_failure_drops_refused_and_is_bounded():
    for i in range(8):
        report.enqueue({"message": f"m{i}"})
    assert report.pending() == report.MAX_QUEUED
    assert report.send_pending(StubClient(fail=OfflineError())) == 0 and report.pending() == report.MAX_QUEUED
    assert report.send_pending(StubClient(fail=ApiError(422, "no"))) == report.MAX_QUEUED  # refused for good: dropped
    assert report.pending() == 0
    queue_file = report.queue_file()
    queue_file.write_text("garbage")
    assert report.pending() == 0
