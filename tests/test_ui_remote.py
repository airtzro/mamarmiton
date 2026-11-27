"""Non-Tk logic behind the account / sync UI."""

import json
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from mamarmiton.models import Ingredient, Recipe
from mamarmiton.remote import ApiError, AuthError, OfflineError
from mamarmiton.ui import remote_logic as rl


class FakeTk:
    """Minimal ``after`` / ``after_cancel`` with manual time."""

    def __init__(self):
        self.jobs: dict[int, tuple[int, object]] = {}
        self.now, self._id = 0, 0

    def after(self, ms, func=None):
        self._id += 1
        self.jobs[self._id] = (self.now + ms, func)
        return self._id

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def advance(self, ms):
        self.now += ms
        for job, (when, func) in sorted(self.jobs.items(), key=lambda kv: kv[1][0]):
            if when <= self.now and job in self.jobs:
                del self.jobs[job]
                func()


def test_status_lines():
    assert rl.status_line("ok", 0)[1] == "À jour"
    assert rl.status_line("syncing")[1] == "Synchronisation…"
    icon, text, kind = rl.status_line("offline", 3)
    assert "Hors ligne" in text and "(3 en attente)" in text and kind == "warn"
    assert rl.status_line("offline", 0)[1] == "Hors ligne"
    assert rl.status_line("auth")[1] == "Reconnectez-vous"
    assert rl.status_line("error", 0, "Oups")[1] == "Oups"
    assert "2 changements" in rl.status_line("ok", 2)[1]


def test_last_sync_text():
    now = datetime(2026, 5, 4, 15, 0)
    assert rl.last_sync_text(None) == "Pas encore synchronisé"
    assert rl.last_sync_text(now - timedelta(seconds=20), now) == "Synchronisé à l'instant"
    assert rl.last_sync_text(datetime(2026, 5, 4, 9, 5), now) == "Synchronisé aujourd'hui à 09:05"
    assert rl.last_sync_text(datetime(2026, 5, 1, 9, 5), now) == "Synchronisé le 01/05/2026 à 09:05"


def test_error_text():
    assert "connexion" in rl.error_text(OfflineError())
    assert rl.error_text(AuthError(401, "Mot de passe incorrect.")) == "Mot de passe incorrect."
    assert rl.error_text(ApiError(422, "Code invalide.")) == "Code invalide."
    assert rl.error_text(PermissionError("Lecture seule")) == "Lecture seule"
    assert "inattendue" in rl.error_text(RuntimeError("boom"))


def test_normalize_server_url():
    assert rl.normalize_server_url(" mamarmiton.example.org:51820/ ") == "https://mamarmiton.example.org:51820"
    assert rl.normalize_server_url("http://localhost:8766") == "http://localhost:8766"
    assert rl.normalize_server_url("") == ""


def test_permissions_fall_back_to_everything_allowed_for_local_stores():
    local = SimpleNamespace()
    assert rl.can_edit(local, "x") and rl.can_delete(local, "x") and rl.is_owner(local, "x")
    assert rl.sharer_label(local, "x", {}) == ""
    assert rl.unknown_owners(local, {}) is False


class FakeRemote:
    def __init__(self):
        self.access_by_id = {"mine": "owner", "rw": "edit", "ro": "read"}
        self.owners = {"mine": "maman", "rw": "tata", "ro": "tonton"}

    def access(self, rid):
        return self.access_by_id.get(rid, "owner")

    def owner(self, rid):
        return self.owners[rid]

    def can_edit(self, rid):
        return self.access(rid) != "read"

    def can_delete(self, rid):
        return self.access(rid) == "owner"

    def all(self):
        return [SimpleNamespace(id=i) for i in self.access_by_id]


def test_remote_permissions_and_sharer_label():
    store = FakeRemote()
    assert rl.can_edit(store, "rw") and not rl.can_edit(store, "ro")
    assert rl.can_delete(store, "mine") and not rl.can_delete(store, "rw")
    people = {"tata": "Tante Anne"}
    assert rl.sharer_label(store, "mine", people) == ""
    assert rl.sharer_label(store, "rw", people) == "Tante Anne"
    assert rl.sharer_label(store, "ro", people) == "tonton"  # name not known yet: username
    assert rl.unknown_owners(store, people) is True
    assert rl.unknown_owners(store, {"tata": "A", "tonton": "B"}) is False


def test_upload_candidates(tmp_path):
    local = SimpleNamespace(all=lambda: [SimpleNamespace(id="a"), SimpleNamespace(id="b"), SimpleNamespace(id="c")])
    store = SimpleNamespace(cache_dir=tmp_path)
    assert rl.upload_candidates(store, local) == 3
    (tmp_path / "upload_state.json").write_text(json.dumps({"a": {"done": True}, "b": {"done": False}}))
    assert rl.upload_candidates(store, local) == 2


def test_debouncer_fires_once_after_the_last_trigger():
    tk, calls = FakeTk(), []
    d = rl.Debouncer(tk, 2000, lambda: calls.append(tk.now))
    d.trigger()
    tk.advance(1500)
    d.trigger()  # restarts the countdown
    tk.advance(1500)
    assert calls == []
    tk.advance(600)
    assert calls == [3600]
    tk.advance(10_000)
    assert len(calls) == 1
    d.trigger()
    d.cancel()
    tk.advance(5000)
    assert len(calls) == 1


class ImmediateTk(FakeTk):
    """``after(0, f)`` from worker threads is queued; ``pump`` runs it on the "main" thread."""

    def __init__(self):
        super().__init__()
        self.queue: list = []

    def after(self, ms, func=None):
        if ms == 0:
            self.queue.append(func)
            return 0
        return super().after(ms, func)

    def pump(self, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.queue:
                self.queue.pop(0)()
                return True
            time.sleep(0.01)
        return False


class FakeStore:
    def __init__(self):
        self.calls = 0
        self.gate = threading.Event()
        self.gate.set()

    def sync(self):
        self.calls += 1
        self.gate.wait(2)
        return SimpleNamespace(changed=True)


def test_sync_runner_syncs_now_every_minute_and_after_changes():
    tk, done = ImmediateTk(), []
    runner = rl.SyncRunner(tk, done.append, interval_ms=60_000, debounce_ms=2000)
    store = FakeStore()
    runner.attach(store)
    assert tk.pump() and store.calls == 1 and len(done) == 1
    tk.advance(60_000)  # periodic
    assert tk.pump() and store.calls == 2
    runner.changed()
    tk.advance(1000)
    assert store.calls == 2  # debounced
    tk.advance(1500)
    assert tk.pump() and store.calls == 3
    runner.stop()
    tk.advance(300_000)
    assert store.calls == 3


def test_sync_runner_replays_a_request_made_during_a_sync_and_ignores_local_stores():
    tk, done = ImmediateTk(), []
    runner = rl.SyncRunner(tk, done.append)
    store = FakeStore()
    store.gate.clear()
    runner.attach(store)
    runner.sync_now()  # while the first is running
    store.gate.set()
    assert tk.pump() and tk.pump()
    assert store.calls == 2
    local = SimpleNamespace()  # no sync(): local mode
    runner.attach(local)
    runner.changed()
    runner.sync_now()
    tk.advance(100_000)
    assert not tk.queue


def test_run_in_thread_reports_result_and_error():
    tk = ImmediateTk()
    tk.winfo_exists = lambda: True
    got = []
    rl.run_in_thread(tk, lambda: 41 + 1, got.append, lambda e: got.append(e))
    assert tk.pump() and got == [42]
    err = ValueError("x")

    def boom():
        raise err

    rl.run_in_thread(tk, boom, got.append, lambda e: got.append(("err", e)))
    assert tk.pump() and got[-1] == ("err", err)


def test_conflict_rows_flag_differences():
    mine = Recipe(nom="Gâteau", pour=4, ingredients=[Ingredient("farine", "200 g")], etapes=["Mélanger"], notes="")
    theirs = Recipe(nom="Gâteau", pour=6, ingredients=[Ingredient("farine", "200 g")], etapes=["Mélanger", "Cuire"])
    rows = {title: (a, b, differs) for title, a, b, differs in rl.conflict_rows(mine, theirs)}
    assert rows["Nom"][2] is False
    assert rows["Détails"][2] is True
    assert rows["Ingrédients"][2] is False and "200 g farine" in rows["Ingrédients"][0]
    assert rows["Étapes"][2] is True
    assert rl.format_date("2026-05-04T09:05:00") == "04/05/2026 à 09:05"
    assert rl.format_date("") == "date inconnue"
