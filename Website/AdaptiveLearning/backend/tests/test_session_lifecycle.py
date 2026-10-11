"""Why each session ended (`close_reason`), today's counts and the stations view for the admin console."""

import ast
import inspect
import os
import pathlib
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

import eeg_poller  # noqa: E402
import main  # noqa: E402
import ops_metrics  # noqa: E402
from conftest import close_sites  # noqa: E402

MIGRATION = (pathlib.Path(__file__).resolve().parents[4] / "supabase" / "migrations"
             / "20261010000000_session_close_reason.sql")


# ─── which close site stores which reason ────────────────────────────────

# The reason each close site passes, by the name it uses; a new site fails until it is added here.
EXPECTED_REASONS = {
    "end_session": "reason",
    "start_session": "CLOSE_REASON_SUPERSEDED",
    "class_live": "CLOSE_REASON_LIVE_STALE",
    "_sweep_abandoned_sessions": "CLOSE_REASON_SWEEP",
}


def _reason_args(source: str) -> list[str]:
    """The `reason=` argument of every `_close_session(...)` call, as written (parsed, not text)."""
    tree = ast.parse(inspect.cleandoc("\n" + source) if source.startswith(" ") else source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_close_session":
            kw = next((k.value for k in node.keywords if k.arg == "reason"), None)
            found.append(ast.unparse(kw) if kw is not None else "<none>")
    return found


def test_every_close_site_says_why_the_session_ended():
    sites = {name: source for name, source in close_sites()}
    assert set(sites) == set(EXPECTED_REASONS), "a close site was added or removed; classify its reason"
    for name, expected in EXPECTED_REASONS.items():
        assert _reason_args(sites[name]) == [expected], name


def test_the_reasons_the_code_writes_are_the_ones_the_column_allows():
    allowed = re.findall(r"'(\w+)'", MIGRATION.read_text(encoding="utf-8").split("CHECK", 1)[1].split(");", 1)[0])
    assert sorted(allowed) == sorted(main.CLOSE_REASONS)
    assert set(main.END_REASONS) == set(main.EndSessionRequest.model_fields["reason"].annotation.__args__[0].__args__)


# ─── the claim stamps both, or neither ───────────────────────────────────

class _Update:
    def __init__(self, owner, fields):
        self.owner, self.fields, self.filters = owner, fields, []

    def eq(self, col, value):
        self.filters.append(("eq", col, value))
        return self

    def is_(self, col, value):
        self.filters.append(("is", col, value))
        return self

    def execute(self):
        self.owner.updates.append((self.fields, self.filters))
        if self.owner.missing_column and "close_reason" in self.fields:
            # PostgREST's answer for a column the schema cache does not have.
            raise RuntimeError("{'code': 'PGRST204', 'message': \"Could not find the 'close_reason' column of "
                               "'sessions' in the schema cache\"}")
        if self.owner.check_violation and "close_reason" in self.fields:
            # Postgres's answer for a value the CHECK refuses: it names the column too.
            raise RuntimeError("{'code': '23514', 'message': 'new row for relation \"sessions\" violates check "
                               "constraint \"sessions_close_reason_check\"'}")
        if self.owner.fail:
            raise RuntimeError("connection reset")
        return type("R", (), {"data": [{"id": "s1"}] if self.owner.wins else []})()


class _Sessions:
    def __init__(self, wins=True, missing_column=False, fail=False, check_violation=False):
        self.wins, self.missing_column, self.fail = wins, missing_column, fail
        self.check_violation = check_violation
        self.updates = []

    def table(self, name):
        assert name == "sessions"
        return type("T", (), {"update": lambda _s, fields, **_k: _Update(self, fields)})()


def test_the_claim_stamps_the_reason_with_the_end_on_an_open_session_only(monkeypatch):
    fake = _Sessions()
    monkeypatch.setattr(main, "supabase", fake)

    assert main._claim_session_close("s1", "2026-10-08T10:00:00+00:00", "finish") is True

    [(fields, filters)] = fake.updates
    assert fields == {"ended_at": "2026-10-08T10:00:00+00:00", "close_reason": "finish"}
    assert ("is", "ended_at", "null") in filters and ("eq", "id", "s1") in filters


@pytest.mark.parametrize("failure", ["missing_column", "check_violation", "fail"])
def test_a_failed_claim_is_never_retried_without_the_reason(monkeypatch, failure):
    """The column is in every database now: no close is ever stamped without why."""
    fake = _Sessions(**{failure: True})
    monkeypatch.setattr(main, "supabase", fake)

    # None, not False: the session is still open, which is not "another close won".
    assert main._claim_session_close("s1", "2026-10-08T10:00:00+00:00", "sweep") is None
    assert [u[0] for u in fake.updates] == [{"ended_at": "2026-10-08T10:00:00+00:00", "close_reason": "sweep"}]


def test_a_claim_another_close_won_is_false_not_a_failure(monkeypatch):
    monkeypatch.setattr(main, "supabase", _Sessions(wins=False))
    assert main._claim_session_close("s1", "2026-10-08T10:00:00+00:00", "finish") is False


def test_end_says_a_close_that_did_not_land_failed_rather_than_that_it_was_already_closed(_end, monkeypatch):
    monkeypatch.setattr(main, "_close_session", lambda *_a, **_k: {"discarded": False, "failed": True})

    with pytest.raises(main.HTTPException) as e:
        main.end_session("s1", None, main.EndSessionRequest(reason="finish"))
    assert e.value.status_code == 503


def test_a_close_whose_claim_failed_does_nothing_else_and_says_so(monkeypatch):
    monkeypatch.setattr(main, "_claim_session_close", lambda *_a: None)
    monkeypatch.setattr(main, "_drop_prefetched",
                        lambda *_a: pytest.fail("a close that did not land went on to drop the queue"))

    assert main._close_session("u1", {"id": "s1"}, "2026-10-08T10:00:00+00:00") == \
        {"discarded": False, "failed": True}


def test_a_discarded_session_is_counted_by_its_reason(monkeypatch):
    monkeypatch.setattr(main, "_claim_session_close", lambda *_a: True)
    monkeypatch.setattr(main, "_drop_prefetched", lambda *_a: None)
    monkeypatch.setattr(main, "_forget_session_pairings", lambda *_a: None)
    monkeypatch.setattr(main, "_answer_counts", lambda *_a: (0, 0, True))
    monkeypatch.setattr(main, "_discard_if_nothing_recorded", lambda *_a, **_k: True)

    out = main._close_session("u1", {"id": "s1"}, "2026-10-08T10:00:00+00:00", reason="page_closed")

    assert out == {"discarded": True}
    assert ops_metrics.pending() == {("session_discarded", "page_closed"): 1}


# ─── /end takes the page's reason ────────────────────────────────────────

@pytest.fixture
def _end(monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u1"})
    monkeypatch.setattr(main, "_session_or_403", lambda *_a: {"id": "s1", "ended_at": None})
    monkeypatch.setattr(main.eeg_poller, "stop", lambda *_a, **_k: {"running": False})
    monkeypatch.setattr(main, "_close_session",
                        lambda *_a, **k: seen.update(k) or {"discarded": False})
    return seen


@pytest.mark.parametrize("reason", ["finish", "sign_out", "page_closed"])
def test_end_stores_the_reason_the_page_sends(_end, reason):
    main.end_session("s1", None, main.EndSessionRequest(reason=reason))
    assert _end["reason"] == reason


@pytest.mark.parametrize("payload", [None, main.EndSessionRequest()])
def test_an_end_that_names_no_reason_is_the_students_not_a_guess(_end, payload):
    main.end_session("s1", None, payload)
    assert _end["reason"] == "student"


@pytest.mark.parametrize("bad", ["sweep", "superseded", "Finish", ""])
def test_a_page_cannot_claim_a_reason_only_the_backend_gives(bad):
    with pytest.raises(ValidationError):
        main.EndSessionRequest(reason=bad)


# ─── the admin Today read ────────────────────────────────────────────────

@pytest.fixture
def _admin(monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda _r: {"id": "admin"})


class _Rpc:
    def __init__(self, owner, name, params):
        self.owner, self.name, self.params = owner, name, params

    def execute(self):
        self.owner.calls.append((self.name, self.params))
        if self.owner.rpc_error:
            raise RuntimeError("rpc down")
        return type("R", (), {"data": self.owner.today})()


class _Query:
    def __init__(self, owner, table):
        self.owner, self.table_name, self.filters = owner, table, []

    def __getattr__(self, name):
        def chain(*a, **k):
            self.filters.append((name, *a, *sorted(k.items())))
            return self
        return chain

    @property
    def not_(self):
        # postgrest's `not_` is a property negating the next filter.
        self.filters.append(("not_",))
        return self

    def execute(self):
        self.owner.reads.append((self.table_name, self.filters))
        if self.table_name in self.owner.failing:
            raise RuntimeError(f"{self.table_name} down")
        return type("R", (), {"data": self.owner.rows.get(self.table_name, [])})()


class _Db:
    def __init__(self, today=None, rpc_error=False, rows=None, failing=()):
        self.today, self.rpc_error = today, rpc_error
        self.rows, self.failing = rows or {}, set(failing)
        self.calls, self.reads = [], []

    def rpc(self, name, params):
        return _Rpc(self, name, params)

    def table(self, name):
        return _Query(self, name)


TODAY = {"started": 4, "open_now": 2, "answers": 37, "active_students": 3,
         "ended_by_reason": {"finish": 1, "unrecorded": 1}}


def test_today_reads_from_the_school_days_midnight(monkeypatch, _admin):
    db = _Db(today=TODAY)
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(main, "_school_timezone", lambda: ZoneInfo("America/Chicago"))
    monkeypatch.setattr(main, "_utc_now", lambda: datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc))

    got = main.admin_today(None)

    # 03:00 UTC is 22:00 the day before in Chicago, so "today" began at 05:00 UTC on the 7th.
    assert db.calls == [("admin_today", {"p_since": "2026-10-07T05:00:00+00:00"})]
    assert got["retrieved"] is True and got["counts"] == TODAY
    assert got["timezone"] == "America/Chicago"


def test_today_adds_the_discards_the_counters_hold(monkeypatch, _admin):
    monkeypatch.setattr(main, "supabase", _Db(today=TODAY))
    ops_metrics.bump("session_discarded", "page_closed", 2)

    assert main.admin_today(None)["discarded"] == {"page_closed": 2}


def test_today_passes_the_rpcs_practice_answers_through(monkeypatch, _admin):
    monkeypatch.setattr(main, "supabase", _Db(today={**TODAY, "practice_answers": 12}))

    got = main.admin_today(None)

    assert got["practice_answers"] == 12 and got["counts"]["answers"] == 37


def test_a_function_that_predates_practice_answers_gives_none_not_zero(monkeypatch, _admin):
    monkeypatch.setattr(main, "supabase", _Db(today=TODAY))
    assert main.admin_today(None)["practice_answers"] is None

    monkeypatch.setattr(main, "supabase", _Db(rpc_error=True))
    assert main.admin_today(None)["practice_answers"] is None


def test_a_failed_today_read_is_not_a_quiet_day(monkeypatch, _admin):
    monkeypatch.setattr(main, "supabase", _Db(rpc_error=True))

    got = main.admin_today(None)
    assert got["retrieved"] is False and got["counts"] is None


# ─── the admin Stations read ─────────────────────────────────────────────

def _pairing(device, user, seconds_ago, session="s-a"):
    seen = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc).timestamp() - seconds_ago
    return {"device_id": device, "user_id": user, "session_id": session,
            "seen_at": datetime.fromtimestamp(seen, timezone.utc).isoformat()}


def _poller(device, user, page_ago, session="s-a"):
    return {"session_id": session, "user_id": user, "device_id": device, "running": True,
            "recording": True, "withheld": False, "page_seen_seconds_ago": page_ago,
            "samples": 120, "errors": 0}


@pytest.fixture
def _clock(monkeypatch):
    monkeypatch.setattr(main, "_utc_now", lambda: datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc))
    # Each test reads names and counters afresh.
    monkeypatch.setattr(main, "_stations_slow", {"at": None})


def test_stations_join_the_pairing_its_poller_and_todays_refusals(monkeypatch, _admin, _clock):
    db = _Db(rows={"station_pairings": [_pairing("station1", "u-ada", 10)],
                   "profiles": [{"id": "u-ada", "display_name": "Ada"}]})
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [_poller("station1", "u-ada", 4)])
    ops_metrics.bump("station_refused", "station1", 3)

    got = main.admin_stations(None)

    [s] = got["stations"]
    assert s["device_id"] == "station1" and s["refused_today"] == 3
    assert s["pairing"]["name"] == "Ada" and s["pairing"]["idle"] is False
    assert s["pollers"][0]["page_watching"] is True
    assert got["retrieved"] is True and got["names_retrieved"] is True
    # Names for the holders only, by id.
    profiles = next(f for t, f in db.reads if t == "profiles")
    assert ("in_", "id", ["u-ada"]) in profiles


def test_a_poller_whose_page_went_silent_is_shown_as_such(monkeypatch, _admin, _clock):
    monkeypatch.setattr(main, "supabase", _Db(rows={"station_pairings": [_pairing("station1", "u1", 300)]}))
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [_poller("station1", "u1", 300)])

    [s] = main.admin_stations(None)["stations"]
    assert s["pollers"][0]["page_watching"] is False
    assert s["pairing"]["idle"] is True


def test_unreadable_names_are_null_and_say_so(monkeypatch, _admin, _clock):
    monkeypatch.setattr(main, "supabase", _Db(rows={"station_pairings": [_pairing("station1", "u1", 5)]},
                                             failing={"profiles"}))
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [])

    got = main.admin_stations(None)
    assert got["names_retrieved"] is False
    assert got["stations"][0]["pairing"]["name"] is None


def test_an_unreadable_pairing_table_is_not_an_empty_one(monkeypatch, _admin, _clock):
    monkeypatch.setattr(main, "supabase", _Db(failing={"station_pairings"}))
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [])

    got = main.admin_stations(None)
    assert got["retrieved"] is False


def _reads_of(db, table):
    return sum(1 for t, _f in db.reads if t == table)


def test_a_five_second_poll_reads_holders_afresh_and_names_and_counts_at_most_every_30_s(
        monkeypatch, _admin, _clock):
    db = _Db(rows={"station_pairings": [_pairing("station1", "u-ada", 10)],
                   "profiles": [{"id": "u-ada", "display_name": "Ada"}]})
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [])

    main.admin_stations(None)
    main.admin_stations(None)

    assert _reads_of(db, "station_pairings") == 2
    assert _reads_of(db, "profiles") == 1
    assert _reads_of(db, "ops_counters") == 1
    # Last lessons are read every poll: one can end with no station changing hands.
    assert _reads_of(db, "sessions") == 2


def test_a_new_holder_is_named_at_once_not_after_the_cache_expires(monkeypatch, _admin, _clock):
    db = _Db(rows={"station_pairings": [_pairing("station1", "u-ada", 10)],
                   "profiles": [{"id": "u-ada", "display_name": "Ada"}, {"id": "u-bo", "display_name": "Bo"}]})
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [])
    main.admin_stations(None)

    db.rows["station_pairings"] = [_pairing("station1", "u-ada", 10), _pairing("station2", "u-bo", 1)]
    got = main.admin_stations(None)

    assert _reads_of(db, "profiles") == 2
    assert [s["pairing"]["name"] for s in got["stations"]] == ["Ada", "Bo"]


def test_a_failed_name_read_is_tried_again_on_the_next_poll(monkeypatch, _admin, _clock):
    db = _Db(rows={"station_pairings": [_pairing("station1", "u-ada", 10)],
                   "profiles": [{"id": "u-ada", "display_name": "Ada"}]}, failing={"profiles"})
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [])
    assert main.admin_stations(None)["names_retrieved"] is False

    db.failing.clear()
    got = main.admin_stations(None)

    assert got["names_retrieved"] is True
    assert got["stations"][0]["pairing"]["name"] == "Ada"


def test_a_refresh_never_leaves_a_reader_holding_a_half_built_cache(monkeypatch, _admin, _clock):
    db = _Db(rows={"station_pairings": [_pairing("station1", "u-ada", 10)],
                   "profiles": [{"id": "u-ada", "display_name": "Ada"}]})
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [])
    main.admin_stations(None)
    held = main._stations_slow            # what a concurrent request read before the refresh

    monkeypatch.setattr(main, "_STATIONS_SLOW_SEC", 0.0)
    main.admin_stations(None)

    assert held["names"] == {"u-ada": "Ada"} and held["refused"] == {}
    assert main._stations_slow is not held


def _ended(device, reason, at):
    return {"eeg_device_id": device, "close_reason": reason, "ended_at": f"2026-10-08T{at}:00+00:00"}


def test_a_released_station_says_how_its_last_lesson_today_ended(monkeypatch, _admin, _clock):
    # Newest first, as the read orders them.
    db = _Db(rows={"station_pairings": [_pairing("station1", "u-ada", 10)],
                   "sessions": [_ended("station2", "sweep", "11:40"), _ended("station1", "finish", "11:00"),
                                _ended("station2", "finish", "10:00"), _ended("station2", None, "09:00")]})
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(main, "_school_timezone", lambda: ZoneInfo("America/Chicago"))
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [])

    got = main.admin_stations(None)

    by = {s["device_id"]: s for s in got["stations"]}
    assert by["station2"]["pairing"] is None
    assert by["station2"]["last_ended"] == {"close_reason": "sweep", "ended_at": "2026-10-08T11:40:00+00:00"}
    assert by["station1"]["last_ended"]["close_reason"] == "finish"
    assert got["last_ended_retrieved"] is True
    [filters] = [f for t, f in db.reads if t == "sessions"]
    # Today's ended lessons with a station, from the school's midnight, newest first.
    assert ("gte", "ended_at", "2026-10-08T05:00:00+00:00") in filters
    assert filters[filters.index(("not_",)) + 1] == ("is_", "eeg_device_id", "null")
    assert ("order", "ended_at", ("desc", True)) in filters
    assert ("limit", main._STATIONS_LAST_ENDED_ROWS) in filters


def test_a_release_is_shown_at_once_not_after_the_cache_expires(monkeypatch, _admin, _clock):
    db = _Db(rows={"station_pairings": [_pairing("station1", "u-ada", 10)], "sessions": []})
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [])
    main.admin_stations(None)

    # Ada finishes: the pairing goes and her lesson has ended, well inside the 30 s cache.
    db.rows["station_pairings"] = []
    db.rows["sessions"] = [_ended("station1", "finish", "11:59")]
    got = main.admin_stations(None)

    assert _reads_of(db, "sessions") == 2
    [s] = got["stations"]
    assert s["pairing"] is None and s["last_ended"]["close_reason"] == "finish"


def test_a_lesson_ending_with_no_change_of_holder_is_shown_on_the_next_poll(monkeypatch, _admin, _clock):
    # The headband was stopped earlier, so nobody holds station1; the lesson finishes later.
    db = _Db(rows={"sessions": [_ended("station1", "superseded", "10:00")]})
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [])
    main.admin_stations(None)

    db.rows["sessions"] = [_ended("station1", "finish", "11:59"), _ended("station1", "superseded", "10:00")]
    [s] = main.admin_stations(None)["stations"]

    assert s["last_ended"]["close_reason"] == "finish"


def test_the_school_timezone_rides_along_for_the_end_times(monkeypatch, _admin, _clock):
    monkeypatch.setattr(main, "supabase", _Db())
    monkeypatch.setattr(main, "_school_timezone_name", lambda: "America/Chicago")
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [])
    assert main.admin_stations(None)["timezone"] == "America/Chicago"


def test_a_null_reason_stays_null_not_a_guess(monkeypatch, _admin, _clock):
    monkeypatch.setattr(main, "supabase", _Db(rows={"sessions": [_ended("station2", None, "09:00")]}))
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [])
    [s] = main.admin_stations(None)["stations"]
    assert s["last_ended"]["close_reason"] is None


def test_an_unread_last_lesson_is_not_none_ended_and_is_read_again(monkeypatch, _admin, _clock):
    db = _Db(rows={"station_pairings": [_pairing("station1", "u-ada", 10)],
                   "sessions": [_ended("station1", "finish", "11:00")]}, failing={"sessions"})
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(eeg_poller, "snapshot", lambda: [])

    got = main.admin_stations(None)
    assert got["last_ended_retrieved"] is False and got["stations"][0]["last_ended"] is None

    db.failing.clear()
    got = main.admin_stations(None)
    assert got["last_ended_retrieved"] is True
    assert got["stations"][0]["last_ended"]["close_reason"] == "finish"


def test_the_poller_snapshot_carries_ids_and_state_never_a_reading(monkeypatch):
    poller = eeg_poller._Poller(None, "u1", "s1", "station1")   # never started
    poller.last_ts = 1234.5
    monkeypatch.setitem(eeg_poller._active, "s1", poller)

    [row] = eeg_poller.snapshot()

    assert set(row) == {"session_id", "user_id", "device_id", "running", "recording", "withheld",
                        "page_seen_seconds_ago", "samples", "errors"}
    assert row["device_id"] == "station1" and row["running"] is False


def test_a_refused_station_claim_is_counted_by_station(monkeypatch):
    monkeypatch.setattr(main, "_station_open_to", lambda *_a: False)

    with pytest.raises(main.HTTPException) as e:
        main._reserve_and_call("u1", "station1", lambda: None)

    assert e.value.status_code == 403
    assert ops_metrics.pending() == {("station_refused", "station1"): 1}
