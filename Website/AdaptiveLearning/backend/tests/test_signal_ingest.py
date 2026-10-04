"""Ingestion trust boundary: may this be recorded (consent), and how much of it (bounds)."""

import os
import re
import time
from collections import deque
from pathlib import Path

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402
from conftest import tighten  # noqa: E402
import signal_mapping  # noqa: E402

STUDENT = {"id": "student-1"}
SESSION = "session-1"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Enough of the PostgREST builder for insert/upsert/select."""

    def __init__(self, store, table):
        self._store, self._table = store, table
        self._filters, self._mode, self._pending = {}, None, None
        self._ignore_dupes = False
        self._conflict = None

    def select(self, *_a, **_k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, *_a, **_k):
        return self

    def single(self):
        # Unused on these paths; implement properly when one needs it.
        return self

    def insert(self, rows, **_k):
        self._mode, self._pending = "insert", rows
        return self

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False, **_k):
        self._mode, self._pending = "upsert", rows
        self._conflict = (on_conflict or "").split(",")
        self._ignore_dupes = ignore_duplicates
        return self

    def execute(self):
        rows = self._store.setdefault(self._table, [])
        if self._mode == "select":
            return _Result([r for r in rows
                            if all(r.get(c) == v for c, v in self._filters.items())])
        if self._mode == "insert":
            rows.extend(dict(r) for r in self._pending)
            return _Result(self._pending)
        if self._mode == "upsert":
            written = []
            for r in self._pending:
                key = tuple(r.get(c) for c in self._conflict)
                clash = any(tuple(e.get(c) for c in self._conflict) == key
                            for e in rows)
                if clash and self._ignore_dupes:
                    continue                  # ON CONFLICT DO NOTHING
                rows.append(dict(r))
                written.append(r)
            return _Result(written)
        raise AssertionError("unreachable")


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        self.store["_tables_read"].append(name)
        return _Query(self.store, name)

    def rpc(self, name, params):
        store = self.store

        class _Call:
            def execute(self):
                assert name == "ingest_gate", name
                # The session lookup, recorded so a test can assert the rate limit kept it from being reached.
                store["_owner_checks"].append((params["p_session_id"], params["p_user_id"]))
                down = store.get("_gate_down")
                if down:
                    raise down if isinstance(down, Exception) else RuntimeError("ingest_gate read failed")
                if "_gate_answer" in store:
                    return _Result(store["_gate_answer"])
                # As the function answers: the session by id whoever owns it, the caller's consent.
                session = next((s for s in store["sessions"] if s["id"] == params["p_session_id"]), None)
                consent = next((r for r in store["signal_consent"]
                                if r["user_id"] == params["p_user_id"]), None)
                return _Result({"session": session and {k: session.get(k) for k in
                                                        ("user_id", "started_at", "ended_at")},
                                "consent": consent})

        return _Call()


@pytest.fixture
def store(monkeypatch):
    st = {"signal_consent": [], "heart_signals": [], "face_signals": [],
          # Unbounded, so a sample's time never decides a test that is not about it.
          "sessions": [{"id": SESSION, "user_id": STUDENT["id"], "started_at": None, "ended_at": None}]}
    monkeypatch.setattr(main, "supabase", _FakeSupabase(st))
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    st["_owner_checks"] = []
    st["_tables_read"] = []
    # Each test gets its own rate-limit budget; the limiter has its own tests.
    monkeypatch.setattr(main._INGEST_LIMITER, "hits", {})
    return st


def _consent(store, **flags):
    row = {"user_id": STUDENT["id"], "eeg_enabled": False,
           "headband_optical_enabled": False, "camera_enabled": False}
    row.update(flags)
    store["signal_consent"] = [row]


def _heart(source="muse_optics", ts="2026-08-09T10:00:00Z", **kw):
    return {"ts": ts, "source": source, "heart_rate_bpm": 72.0,
            "stress_category": "low", "trusted": True, **kw}


def _post_heart(samples):
    return main.ingest_heart(
        main.HeartBatch(session_id=SESSION, samples=samples), request=None
    )


# ── a sample's time must fall inside its session ────────────────────────────

_BOUNDED = {"user_id": STUDENT["id"], "started_at": "2026-08-09T10:00:00+00:00",
            "ended_at": "2026-08-09T11:00:00+00:00"}


@pytest.mark.parametrize("ts,inside", [
    ("2026-08-09T10:30:00Z", True),
    ("2026-08-09T09:51:00Z", True),         # within the slack before the start
    ("2026-08-09T11:09:00Z", True),         # and after the end
    ("2026-08-08T10:30:00Z", False),        # a laptop clock a day behind
    ("2026-08-09T11:30:00Z", False),
    (None, True),                           # stamped server-side
    ("not a time", False),
])
def test_a_sample_is_placed_only_inside_its_session(ts, inside):
    assert main._ingest_ts_filter(_BOUNDED)(ts) is inside


def test_an_open_session_is_bounded_by_now(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(main, "_utc_now", lambda: datetime(2026, 8, 9, 10, 30, tzinfo=timezone.utc))
    inside = main._ingest_ts_filter({**_BOUNDED, "ended_at": None})
    assert inside("2026-08-09T10:35:00Z") and not inside("2026-08-09T12:00:00Z")


def test_the_endpoint_drops_and_counts_what_falls_outside(store, monkeypatch):
    """Rows a day off land on a day no rollup covers, and expire unsummarised."""
    _consent(store, headband_optical_enabled=True)
    store["sessions"] = [{"id": SESSION, **_BOUNDED}]
    out = _post_heart([_heart(ts="2026-08-09T10:30:00Z"), _heart(ts="2026-08-08T10:30:00Z")])
    assert (out["inserted"], out["out_of_window"]) == (1, 1)
    assert [r["ts"] for r in store["heart_signals"]] == ["2026-08-09T10:30:00Z"]
    # The bounds came from the caller's own session, asked for by id.
    assert store["_owner_checks"] == [(SESSION, STUDENT["id"])]


# ── consent decides what is written ──────────────────────────────────────────

def test_a_heart_sample_from_a_declined_sensor_is_not_stored(store):
    """Per sample, against the sensor named in `source`."""
    _consent(store, headband_optical_enabled=True)      # camera declined

    out = _post_heart([_heart(source="rppg")])

    assert out["inserted"] == 0
    assert out["dropped"] == 1
    assert store["heart_signals"] == []


def test_a_mixed_batch_keeps_the_consented_samples(store):
    _consent(store, headband_optical_enabled=True)

    out = _post_heart([
        _heart(source="muse_optics", ts="2026-08-09T10:00:00Z"),
        _heart(source="rppg",        ts="2026-08-09T10:00:01Z"),
        _heart(source="muse_ppg",    ts="2026-08-09T10:00:02Z"),
    ])

    assert (out["inserted"], out["dropped"]) == (2, 1)
    assert {r["source"] for r in store["heart_signals"]} == {"muse_optics", "muse_ppg"}


def test_no_consent_row_records_nothing(store):
    out = _post_heart([_heart()])
    assert (out["inserted"], out["dropped"]) == (0, 1)


def test_camera_consent_alone_permits_only_the_camera_source(store):
    _consent(store, camera_enabled=True)

    out = _post_heart([_heart(source="rppg"), _heart(source="muse_optics",
                                                     ts="2026-08-09T10:00:05Z")])
    assert (out["inserted"], out["dropped"]) == (1, 1)
    assert store["heart_signals"][0]["source"] == "rppg"


def test_face_ingestion_stops_when_the_camera_is_declined(store):
    """A stale sidecar keeps sending; the withdrawal must hold at the boundary."""
    _consent(store, eeg_enabled=True)                    # camera declined

    out = main.ingest_face(
        main.FaceBatch(session_id=SESSION,
                       samples=[{"emotion": "happy", "emotion_confidence": 0.9}]),
        request=None,
    )
    assert out["inserted"] == 0
    assert store["face_signals"] == []


def test_face_ingestion_stores_both_emotion_fields(store):
    """`emotion_confidence` and `emotion_trusted` are what the fusion gate reads."""
    _consent(store, camera_enabled=True)

    main.ingest_face(
        main.FaceBatch(session_id=SESSION, samples=[{
            "emotion": "sad", "emotion_confidence": 0.81,
            "emotion_trusted": True, "identity_confidence": 0.4,
        }]),
        request=None,
    )
    row = store["face_signals"][0]
    assert row["emotion_confidence"] == 0.81
    assert row["emotion_trusted"] is True
    # Retired column: Pydantic drops it, or the INSERT would fail at the database.
    assert "identity_confidence" not in row


def _post(endpoint):
    if endpoint == "cognitive":
        return main.ingest_cognitive(main.CognitiveBatch(
            session_id=SESSION, samples=[{"ts": "2026-08-09T10:00:00Z", "focus": 0.5}]), request=None)
    if endpoint == "face":
        return main.ingest_face(main.FaceBatch(
            session_id=SESSION, samples=[{"emotion": "happy"}]), request=None)
    return _post_heart([_heart()])


@pytest.mark.parametrize("endpoint", ["cognitive", "face", "heart"])
def test_a_failed_gate_read_is_a_503_that_writes_nothing(store, endpoint):
    """Session and consent are one read: failing, it is an outage the push client retries, not a refusal."""
    from fastapi import HTTPException
    _consent(store, eeg_enabled=True, headband_optical_enabled=True, camera_enabled=True)
    store["_gate_down"] = True
    with pytest.raises(HTTPException) as exc:
        _post(endpoint)
    assert exc.value.status_code == 503
    assert not any(store.get(t) for t in ("cognitive_signals", "face_signals", "heart_signals"))


def test_a_session_id_that_cannot_be_a_uuid_names_no_session(store):
    """Postgres's 22P02 from the uuid parameter is a 404, as `_row_or_404` treats it, not an outage."""
    from fastapi import HTTPException
    store["_gate_down"] = type("E", (Exception,), {"code": "22P02"})("invalid input syntax for type uuid")
    with pytest.raises(HTTPException) as exc:
        _post_heart([_heart()])
    assert exc.value.status_code == 404


def _pgrst202(function: str):
    """PostgREST's PGRST202 as postgrest-py raises it; message and details as the local stack words them."""
    from postgrest.exceptions import APIError
    return APIError({
        "code": "PGRST202", "hint": None,
        "message": f"Could not find the function public.{function}(p_session_id, p_user_id) in the schema cache",
        "details": f"Searched for the function public.{function} with parameters p_session_id, p_user_id "
                   "or with a single unnamed json/jsonb parameter, but no matches were found in the schema cache."})


@pytest.mark.parametrize("error", [
    RuntimeError("{'code': 'PGRST202', 'message': 'no function'}"),
    _pgrst202("ingest_gate"),                                  # as PostgREST words it, naming the function
], ids=["names-none", "postgrest"])
def test_a_missing_gate_function_names_its_migration(store, capsys, error):
    """Deployed ahead of 20261003000000, every batch is a 503; the log has to say why."""
    from fastapi import HTTPException
    store["_gate_down"] = error
    with pytest.raises(HTTPException) as exc:
        _post_heart([_heart()])
    assert exc.value.status_code == 503
    line = capsys.readouterr().out
    assert "20261003000000" in line
    # This site's own cost: batches the push client resends, never ones refused for good.
    assert "every signal batch is a 503" in line and "refus" not in line


@pytest.mark.parametrize("error", [
    _pgrst202("ingest_gate"),
    RuntimeError(str(_pgrst202("ingest_gate"))),
    RuntimeError("{'code': 'PGRST202', 'message': 'no function'}"),   # the site knows which call it made
], ids=["api-error", "its-text", "names-none"])
def test_a_missing_function_is_named_with_its_migration(capsys, error):
    assert main._missing_rpc(error, "ingest_gate", "20261003000000", "batches wait") is True
    line = capsys.readouterr().out
    assert "ingest_gate is missing" in line and "apply 20261003000000; batches wait:" in line


_MISSING_RPC_SITES_FLOOR = 6


def test_every_missing_function_site_says_what_it_costs():
    """One sentence for every site cannot tell an operator which feature has stopped."""
    import ast
    import inspect
    sites = [n for n in ast.walk(ast.parse(inspect.getsource(main)))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_missing_rpc"]
    assert len(sites) >= _MISSING_RPC_SITES_FLOOR, "the scan found fewer call sites than exist"
    said = {}
    for site in sites:
        literal = len(site.args) == 4 and all(isinstance(a, ast.Constant) and a.value for a in site.args[1:])
        assert literal, f"no consequence of its own: {ast.unparse(site)}"
        said.setdefault(site.args[3].value, set()).add(site.args[1].value)
    shared = {consequence: fns for consequence, fns in said.items() if len(fns) > 1}
    assert not shared, f"one consequence for different functions: {shared}"


@pytest.mark.parametrize("error", [
    _pgrst202("record_answer"),
    RuntimeError(str(_pgrst202("record_answer"))),
    _pgrst202("ingest_gate_v2"),                               # a name it is a prefix of
    RuntimeError("connection reset"),
    RuntimeError("{'code': '42883', 'message': 'function public.ingest_gate(uuid, uuid) does not exist'}"),
], ids=["other-function", "other-function-text", "longer-name", "not-postgrest", "other-code"])
def test_anything_else_is_not_a_missing_function(capsys, error):
    """False and silent: the caller logs its own failed read, so a wrong True misnames the outage."""
    assert main._missing_rpc(error, "ingest_gate", "20261003000000", "batches wait") is False
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("answer,shape", [
    (None, "a NoneType"),
    ([], "a list"),
    ({}, "keys []"),
    ({"session": None}, "keys ['session']"),
    ({"session": [], "consent": None}, "session as a list"),
    ({"session": {"user_id": STUDENT["id"]}, "consent": "yes"}, "consent as a str"),
    ({"session": {"started_at": None}, "consent": None}, "a session with keys ['started_at']"),
], ids=["null", "list", "empty", "no-consent-key", "session-list", "consent-str", "no-owner"])
def test_a_gate_answer_that_is_not_its_object_is_a_failed_read(store, capsys, answer, shape):
    """A 503 the push client resends, never a 404 that drops the batch: nobody read that it is gone."""
    from fastapi import HTTPException
    _consent(store, headband_optical_enabled=True)
    store["_gate_answer"] = answer
    with pytest.raises(HTTPException) as exc:
        _post_heart([_heart()])
    assert exc.value.status_code == 503
    assert store["heart_signals"] == []
    log = capsys.readouterr().out
    assert "ingest_gate answered" in log and shape in log


def test_a_gate_answer_with_no_session_is_a_404(store):
    from fastapi import HTTPException
    store["_gate_answer"] = {"session": None, "consent": None}
    with pytest.raises(HTTPException) as exc:
        _post_heart([_heart()])
    assert exc.value.status_code == 404


@pytest.mark.parametrize("endpoint", ["cognitive", "face", "heart"])
def test_the_gate_is_the_only_read_before_the_write(store, endpoint):
    """Session and consent come back together; a second consent read is the round trip this removed."""
    _consent(store, eeg_enabled=True, headband_optical_enabled=True, camera_enabled=True)
    _post(endpoint)
    assert store["_owner_checks"] == [(SESSION, STUDENT["id"])]
    assert "signal_consent" not in store["_tables_read"]
    assert "sessions" not in store["_tables_read"]


@pytest.mark.parametrize("owner,status", [(None, 404), ("student-2", 403)])
def test_a_session_that_is_not_the_callers_writes_nothing(store, owner, status):
    from fastapi import HTTPException
    _consent(store, headband_optical_enabled=True)
    store["sessions"] = [] if owner is None else [
        {"id": SESSION, "user_id": owner, "started_at": None, "ended_at": None}]
    with pytest.raises(HTTPException) as exc:
        _post_heart([_heart()])
    assert exc.value.status_code == status
    assert store["heart_signals"] == []


# ── the retry that would otherwise double every average ──────────────────────

def test_replaying_a_batch_inserts_nothing_the_second_time(store):
    """ON CONFLICT DO NOTHING on (session_id, source, ts); a doubled row only shows as a wrong average."""
    _consent(store, headband_optical_enabled=True)
    batch = [_heart(ts="2026-08-09T10:00:00Z"), _heart(ts="2026-08-09T10:00:01Z")]

    first = _post_heart(batch)
    second = _post_heart(batch)

    assert first["inserted"] == 2
    assert len(store["heart_signals"]) == 2, "the replay doubled the rows"
    # Counted from what the database wrote, not from what was sent.
    assert second["inserted"] == 0
    assert second["duplicates"] == 2


def test_replaying_a_cognitive_batch_inserts_nothing_the_second_time(store):
    """`cog_session_ts_key`: this channel has two writers (the poller and push)."""
    store["cognitive_signals"] = []
    _consent(store, eeg_enabled=True)
    batch = [{"ts": "2026-08-10T10:00:00Z", "focus": 0.7},
             {"ts": "2026-08-10T10:00:01Z", "focus": 0.8}]

    first = main.ingest_cognitive(
        main.CognitiveBatch(session_id=SESSION, samples=batch), request=None)
    second = main.ingest_cognitive(
        main.CognitiveBatch(session_id=SESSION, samples=batch), request=None)

    assert first["inserted"] == 2
    assert len(store["cognitive_signals"]) == 2, "the replay doubled the rows"
    # From what the database wrote: `push_client` counts delivery off this number.
    assert second["inserted"] == 0


def test_replaying_a_face_batch_inserts_nothing_the_second_time(store):
    """`face_session_ts_key`; production had no duplicates (961 rows, 961 distinct)."""
    _consent(store, camera_enabled=True)
    batch = [{"ts": "2026-08-10T10:00:00Z", "emotion": "neutral"},
             {"ts": "2026-08-10T10:00:01Z", "emotion": "happy"}]

    first = main.ingest_face(
        main.FaceBatch(session_id=SESSION, samples=batch), request=None)
    second = main.ingest_face(
        main.FaceBatch(session_id=SESSION, samples=batch), request=None)

    assert first["inserted"] == 2
    assert len(store["face_signals"]) == 2, "the replay doubled the rows"
    assert second["inserted"] == 0


def test_two_samples_a_second_apart_are_both_kept(store):
    """A key on `session_id` alone would pass the replay tests and collapse a session to one row."""
    store["cognitive_signals"] = []
    _consent(store, eeg_enabled=True)

    out = main.ingest_cognitive(main.CognitiveBatch(session_id=SESSION, samples=[
        {"ts": "2026-08-10T10:00:00Z", "focus": 0.7},
        {"ts": "2026-08-10T10:00:01Z", "focus": 0.8},
        {"ts": "2026-08-10T10:00:02Z", "focus": 0.9},
    ]), request=None)

    assert out["inserted"] == 3
    assert len(store["cognitive_signals"]) == 3


def test_two_sources_may_report_the_same_instant(store):
    """Which is why `source` is in the key."""
    _consent(store, headband_optical_enabled=True, camera_enabled=True)

    out = _post_heart([_heart(source="muse_optics", ts="2026-08-09T10:00:00Z"),
                       _heart(source="rppg",        ts="2026-08-09T10:00:00Z")])

    assert out["inserted"] == 2
    assert len(store["heart_signals"]) == 2


# ── volume bounds ────────────────────────────────────────────────────────────

def test_an_oversized_batch_is_refused_by_the_model(store):
    """Bounded at the schema, so it never reaches a database call."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        main.HeartBatch(session_id=SESSION,
                        samples=[_heart()] * (main._INGEST_MAX_BATCH + 1))


def test_a_flooding_client_is_rate_limited(store, monkeypatch):
    """Neither the owner check nor consent bounds volume."""
    from fastapi import HTTPException

    _consent(store, headband_optical_enabled=True)
    tighten(monkeypatch, main._INGEST_LIMITER, limit=3)

    for _ in range(3):
        _post_heart([_heart()])

    with pytest.raises(HTTPException) as exc:
        _post_heart([_heart()])
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_the_limit_is_per_caller(store, monkeypatch):
    _consent(store, headband_optical_enabled=True)
    tighten(monkeypatch, main._INGEST_LIMITER, limit=2)

    for _ in range(2):
        _post_heart([_heart()])

    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "student-2"})
    store["signal_consent"].append({"user_id": "student-2",
                                    "headband_optical_enabled": True,
                                    "camera_enabled": False, "eeg_enabled": False})
    store["sessions"].append({"id": "session-b", "user_id": "student-2",
                              "started_at": None, "ended_at": None})
    assert main.ingest_heart(main.HeartBatch(session_id="session-b", samples=[_heart()]),
                             request=None)["ok"]


def test_the_rate_limit_runs_before_the_session_lookup(store, monkeypatch):
    """A flooding client should cost nothing but the limiter."""
    from fastapi import HTTPException

    _consent(store, headband_optical_enabled=True)
    tighten(monkeypatch, main._INGEST_LIMITER, limit=1)

    _post_heart([_heart()])
    assert len(store["_owner_checks"]) == 1

    with pytest.raises(HTTPException) as exc:
        _post_heart([_heart(ts="2026-08-09T10:00:09Z")])
    assert exc.value.status_code == 429
    assert len(store["_owner_checks"]) == 1, "the refused request still hit the database"


def test_a_genuine_refusal_says_so(store):
    """A consent row read and declining names the declined sensor; an unread one is a 503 first."""
    _consent(store, eeg_enabled=True)          # both sensors declined, readably

    assert _post_heart([_heart()])["reason"] == "no consented heart sensor"

    out = main.ingest_face(
        main.FaceBatch(session_id=SESSION, samples=[{"emotion": "happy"}]),
        request=None,
    )
    assert out["reason"] == "camera not consented"


def test_a_fully_consented_batch_reports_no_reason(store):
    _consent(store, headband_optical_enabled=True)
    assert _post_heart([_heart()])["reason"] is None


def test_stale_callers_are_evicted_rather_than_accumulating(store, monkeypatch):
    """Entries prune on the caller's next request, which never comes for one who left."""
    _consent(store, headband_optical_enabled=True)
    monkeypatch.setattr(main._INGEST_LIMITER, "_sweep_above", 5)
    monkeypatch.setattr(main._INGEST_LIMITER, "_sweep_every", 0.0)

    # Callers who posted a full window ago and never came back.
    stale = time.monotonic() - (main._INGEST_LIMITER.window + 1)
    main._INGEST_LIMITER.hits.update({f"gone-{i}": deque([[stale, 1]]) for i in range(10)})
    main._INGEST_LIMITER.sweep_at = 0.0

    _post_heart([_heart()])

    assert not [k for k in main._INGEST_LIMITER.hits if k.startswith("gone-")], (
        "stale callers were not evicted"
    )
    assert STUDENT["id"] in main._INGEST_LIMITER.hits, "the live caller was evicted too"


def test_an_active_caller_is_never_swept(store, monkeypatch):
    """The sweep must only drop entries whose every hit has aged out."""
    _consent(store, headband_optical_enabled=True)
    monkeypatch.setattr(main._INGEST_LIMITER, "_sweep_above", 0)
    monkeypatch.setattr(main._INGEST_LIMITER, "_sweep_every", 0.0)

    main._INGEST_LIMITER.hits["busy"] = deque([[time.monotonic(), 1]])   # a hit just now
    main._INGEST_LIMITER.sweep_at = 0.0

    _post_heart([_heart()])
    assert "busy" in main._INGEST_LIMITER.hits


def test_a_clients_raw_blob_survives_the_mapper(store):
    _consent(store, headband_optical_enabled=True, camera_enabled=True)

    _post_heart([_heart(raw={"probe": "kept"})])
    assert store["heart_signals"][0]["raw"]["probe"] == "kept"

    main.ingest_face(
        main.FaceBatch(session_id=SESSION, samples=[
            {"emotion": "happy", "raw": {"probe": "kept"}}]),
        request=None,
    )
    assert store["face_signals"][0]["raw"]["probe"] == "kept"


def test_rmssd_gating_fields_reach_the_stored_row(store):
    """RMSSD's own gates, apart from `rejected_by`: they say why a null rmssd_ms is null."""
    _consent(store, headband_optical_enabled=True)

    _post_heart([_heart(rmssd_ms=None, beat_coverage=0.91,
                         rmssd_rejected_by="coverage")])
    row = store["heart_signals"][0]
    assert row["raw"]["beat_coverage"] == 0.91
    assert row["raw"]["rmssd_rejected_by"] == "coverage"


def test_the_stress_baseline_reaches_the_stored_row(store):
    _consent(store, headband_optical_enabled=True)

    _post_heart([_heart(stress_score=77.5, stress_category="high", stress_baseline_bpm=70.0)])
    row = store["heart_signals"][0]
    assert (row["stress_score"], row["stress_category"]) == (77.5, "high")
    assert row["raw"]["stress_baseline_bpm"] == 70.0


def test_a_non_finite_heart_value_is_malformed_and_the_rest_of_the_batch_lands(store):
    """NaN/Infinity pass `float | None`, then fail the insert. Refused per sample: refused as a
    batch, the push client retried it for ever and it never landed."""
    _consent(store, headband_optical_enabled=True)
    for field in ("heart_rate_bpm", "rmssd_ms", "beat_coverage",
                  "sqi", "stress_score", "stress_baseline_bpm"):
        store["heart_signals"].clear()
        out = _post_heart([_heart(**{field: float("nan")}),
                           _heart(ts="2026-08-09T10:00:05Z")])
        assert (out["malformed"], out["inserted"]) == (1, 1), field


@pytest.mark.parametrize("field", ["attention", "gaze_x", "gaze_y", "head_yaw", "head_pitch",
                                   "head_roll", "emotion_confidence"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_a_non_finite_face_value_is_malformed_and_the_rest_of_the_batch_lands(store, field, value):
    _consent(store, camera_enabled=True)
    out = main.ingest_face(
        main.FaceBatch(session_id=SESSION, samples=[
            {"ts": "2026-08-09T10:00:00Z", "emotion": "happy", field: value},
            {"ts": "2026-08-09T10:00:05Z", "emotion": "happy", "emotion_confidence": 0.9}]),
        request=None)
    assert (out["malformed"], out["inserted"]) == (1, 1), field


@pytest.mark.parametrize("field, value", [
    ("heart_rate_bpm", 19.0), ("heart_rate_bpm", 251.0), ("rmssd_ms", -1.0),
    ("rmssd_ms", 1001.0), ("sqi", 1.5), ("stress_score", 100.5), ("stress_score", -0.1),
    ("stress_category", "elevated"),
])
def test_a_value_the_table_would_refuse_is_malformed_and_the_rest_of_the_batch_lands(
        store, field, value):
    """Past the model, one CHECK violation fails the whole upsert and the client retries it for ever."""
    _consent(store, headband_optical_enabled=True)
    out = _post_heart([_heart(**{field: value}), _heart(ts="2026-08-09T10:00:05Z")])
    assert (out["malformed"], out["inserted"]) == (1, 1)


def _latest_check(name):
    """A constraint's CHECK text as the newest migration defining it leaves it."""
    migrations = sorted((Path(__file__).resolve().parents[4] / "supabase/migrations").glob("*.sql"))
    found = None
    for path in migrations:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(rf'(ADD|DROP) CONSTRAINT (IF EXISTS )?"{name}"([^;]*);', text):
            found = m.group(3) if m.group(1) == "ADD" else None
    assert found is not None, f"{name} is not defined by any migration"
    return found


def test_the_heart_sample_bounds_are_the_tables_checks():
    """A bound looser than the CHECK is the failed-batch path again; a tighter one refuses good data."""
    category_check = _latest_check("heart_signals_stress_category_check")
    assert set(re.findall(r"'(\w+)'::\"text\"", category_check)) == main._HEART_STRESS_CATEGORIES
    for col in ("heart_rate_bpm", "rmssd_ms", "sqi", "stress_score"):
        lo, hi = re.search(rf'"{col}" >= ([\d.]+) AND "{col}" <= ([\d.]+)',
                           _latest_check(f"heart_signals_{col}_range")).groups()
        meta = main.HeartSample.model_fields[col].metadata
        ge = [m.ge for m in meta if hasattr(m, "ge")]
        le = [m.le for m in meta if hasattr(m, "le")]
        assert (ge, le) == ([float(lo)], [float(hi)]), col


def test_a_malformed_face_sample_is_counted_and_the_rest_of_the_batch_lands(store):
    _consent(store, camera_enabled=True)
    out = main.ingest_face(main.FaceBatch(session_id=SESSION, samples=[
        "not a sample", {"emotion": "happy", "ts": "2026-08-09T10:00:05Z"}]), request=None)
    assert (out["malformed"], out["inserted"]) == (1, 1)


def test_derived_fields_win_over_a_client_supplied_key(store):
    _consent(store, headband_optical_enabled=True)

    _post_heart([_heart(raw={"rejected_by": "client says none"})])
    # Derived None removes the key; the client's value must not survive.
    assert "rejected_by" not in store["heart_signals"][0]["raw"]


def test_gaze_survives_the_face_mapper(store):
    _consent(store, camera_enabled=True)

    main.ingest_face(
        main.FaceBatch(session_id=SESSION, samples=[
            {"emotion": "happy", "gaze_x": 0.3, "gaze_y": -0.2}]),
        request=None,
    )
    row = store["face_signals"][0]
    assert (row["gaze_x"], row["gaze_y"]) == (0.3, -0.2)


def test_every_column_the_mapper_writes_can_be_supplied_by_the_endpoint(store):
    """`FaceSample` drops undeclared keys, so every mapper column must be a declared field."""
    supplied_by_the_endpoint = {"session_id", "user_id", "ts", "raw"}
    written = set(signal_mapping.map_face_to_face_signal(
        {"timestamp": "t", "face": {"emotion": "happy"}}, SESSION, "u"))

    missing = (written - supplied_by_the_endpoint) - set(main.FaceSample.model_fields)
    # The endpoint's field is `emotion_trusted`, renamed on the way through.
    missing -= {"emotion_trusted"}
    assert not missing, (
        f"{sorted(missing)} are written by the mapper but cannot be sent: "
        "Pydantic drops undeclared keys, so those columns stay NULL for ever")


def test_head_pose_survives_the_round_trip(store):
    """Distinct values, so an axis mix-up is visible."""
    _consent(store, camera_enabled=True)

    main.ingest_face(
        main.FaceBatch(session_id=SESSION, samples=[{
            "emotion": "happy", "gaze_x": 0.1, "gaze_y": -0.2,
            "head_yaw": 12.5, "head_pitch": -3.25, "head_roll": 7.75,
            "raw": {"pose_rejected_by": None},
        }]),
        request=None,
    )

    row = store["face_signals"][-1]
    assert (row["head_yaw"], row["head_pitch"], row["head_roll"]) == (12.5, -3.25, 7.75)
    assert (row["gaze_x"], row["gaze_y"]) == (0.1, -0.2)


def test_the_synthetic_mark_is_derived_from_the_sample_never_from_the_posted_raw(store):
    """A client cannot mark or unmark a synthetic (EEG_SIM_OPTICS) row through `raw`."""
    _consent(store, headband_optical_enabled=True)

    out = _post_heart([
        _heart(ts="2026-08-09T10:00:00Z", synthetic=True),
        _heart(ts="2026-08-09T10:00:01Z"),
        _heart(ts="2026-08-09T10:00:02Z", raw={"synthetic": True}),
    ])

    assert out["inserted"] == 3
    marks = [row["raw"].get("synthetic") for row in store["heart_signals"]]
    assert marks == [True, None, None]


# ── rows per student per channel: the batch rate alone allowed ~86M a day ───

def _hearts(n, start=0):
    return [_heart(ts=f"2026-08-09T10:{(start + i) // 60:02d}:{(start + i) % 60:02d}Z") for i in range(n)]


def test_rows_past_the_sessions_ceiling_are_refused_whole_and_nothing_is_written(store, monkeypatch):
    """A 429 keeps the batch in the push client's queue; a partial write would lose the rest."""
    from conftest import tighten
    tighten(monkeypatch, main._INGEST_ROW_LIMITER, limit=5)
    _consent(store, headband_optical_enabled=True)
    recorded = []
    monkeypatch.setattr(main, "_record_security_event", lambda kind, actor, **d: recorded.append((kind, d)))
    assert _post_heart(_hearts(3))["inserted"] == 3
    with pytest.raises(main.HTTPException) as e:
        _post_heart(_hearts(3, start=3))
    assert e.value.status_code == 429 and int(e.value.headers["Retry-After"]) >= 1
    assert len(store["heart_signals"]) == 3
    assert recorded == [("rate_limited", {"limiter": main._INGEST_ROW_LIMITER.name})]


def test_the_ceiling_is_per_channel_and_shared_by_a_students_sessions(store, monkeypatch):
    """Per student, not per session: a closed session's window still ingests, so each one would add a budget."""
    from conftest import tighten
    tighten(monkeypatch, main._INGEST_ROW_LIMITER, limit=3)
    _consent(store, headband_optical_enabled=True, camera_enabled=True)
    assert _post_heart(_hearts(3))["inserted"] == 3
    # Another channel has its own budget.
    out = main.ingest_face(main.FaceBatch(session_id=SESSION, samples=[
        {"ts": f"2026-08-09T10:00:0{i}Z", "emotion": "happy"} for i in range(3)]), request=None)
    assert out["inserted"] == 3
    # Another session of the same student does not.
    store["sessions"].append({"id": "session-2", "user_id": STUDENT["id"],
                              "started_at": None, "ended_at": None})
    with pytest.raises(main.HTTPException) as e:
        main.ingest_heart(main.HeartBatch(session_id="session-2", samples=_hearts(3)), request=None)
    assert e.value.status_code == 429


def test_a_replayed_batch_costs_nothing_against_the_ceiling(store, monkeypatch):
    """The push client resends a batch whose answer it lost; only new rows stay charged.

    Admission still needs room for the batch, since duplicates are known only after the write.
    """
    tighten(monkeypatch, main._INGEST_ROW_LIMITER, limit=6)
    _consent(store, headband_optical_enabled=True)
    assert _post_heart(_hearts(3))["inserted"] == 3
    assert _post_heart(_hearts(3))["duplicates"] == 3
    assert _post_heart(_hearts(3, start=3))["inserted"] == 3


def test_a_failed_write_gives_its_rows_back(store, monkeypatch):
    tighten(monkeypatch, main._INGEST_ROW_LIMITER, limit=5)
    _consent(store, headband_optical_enabled=True)
    real = main.supabase

    class _Down:
        def table(self, name):
            if name == "heart_signals":
                raise RuntimeError("write failed")
            return real.table(name)

        def rpc(self, name, params):
            return real.rpc(name, params)

    monkeypatch.setattr(main, "supabase", _Down())
    with pytest.raises(RuntimeError):
        _post_heart(_hearts(5))
    monkeypatch.setattr(main, "supabase", real)
    assert _post_heart(_hearts(5))["inserted"] == 5


def test_the_default_ceiling_is_the_one_the_sidecar_warns_against():
    """One pushed row per tick; the sidecar compares its EEG_SAMPLE_HZ to this default.

    Read from source, since a local backend .env may set the variable.
    """
    default = int(re.search(r'env_number\("INGEST_MAX_ROWS_PER_MINUTE", (\d+)',
                            Path(main.__file__).read_text(encoding="utf-8")).group(1))
    config = Path(__file__).resolve().parents[4] / "EEGResearch" / "src" / "app" / "config.py"
    assert f"BACKEND_ROWS_PER_MINUTE = {default}\n" in config.read_text(encoding="utf-8")
    assert default >= 2 * 60 * 4, "twice the sidecar's default 4 Hz"
    assert main._INGEST_ROWS_PER_MINUTE >= main._INGEST_MAX_BATCH


def test_a_cost_is_taken_whole_or_not_at_all():
    limiter = main._SlidingWindowLimiter("t", 5, 60.0)
    assert limiter.check("k", cost=4) is None
    assert limiter.check("k", cost=2) is not None
    assert limiter.check("k", cost=1) is None, "the refused cost of 2 was partly taken"


def test_a_batch_is_one_entry_and_can_be_given_back_in_part():
    """Stored per call, not per row: a 500-row batch must not be 500 entries rescanned under the lock."""
    limiter = main._SlidingWindowLimiter("t", 1200, 60.0)
    assert limiter.check("k", cost=500) is None and limiter.check("k", cost=300) is None
    assert len(limiter.hits["k"]) == 2
    limiter.release("k", 350)          # all of the newest call and 50 of the one before
    assert [n for _t, n in limiter.hits["k"]] == [450]
    assert limiter.check("k", cost=750) is None and limiter.check("k", cost=1) is not None


def test_the_clock_is_read_under_the_lock(monkeypatch):
    """Read before it, two callers can append out of time order, and pruning assumes order."""
    limiter = main._SlidingWindowLimiter("t", 5, 60.0)
    real, held = time.monotonic, []
    monkeypatch.setattr(main.time, "monotonic", lambda: (held.append(limiter._lock.locked()), real())[1])
    limiter.check("k")
    assert held and all(held)
