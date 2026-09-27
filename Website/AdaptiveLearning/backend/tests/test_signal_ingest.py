"""Ingestion trust boundary: may this be recorded (consent), and how much of it (bounds)."""

import os
import time

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
        return _Query(self.store, name)


@pytest.fixture
def store(monkeypatch):
    st = {"signal_consent": [], "heart_signals": [], "face_signals": []}
    monkeypatch.setattr(main, "supabase", _FakeSupabase(st))
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    # Recorded, so a test can assert the rate limit kept it from being reached.
    st["_owner_checks"] = []
    monkeypatch.setattr(main, "_verify_session_owner",
                        lambda *a: st["_owner_checks"].append(a))
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
    asked = []
    monkeypatch.setattr(main, "_verify_session_owner",
                        lambda sid, uid, columns="user_id": asked.append(columns) or _BOUNDED)
    out = _post_heart([_heart(ts="2026-08-09T10:30:00Z"), _heart(ts="2026-08-08T10:30:00Z")])
    assert (out["inserted"], out["out_of_window"]) == (1, 1)
    assert [r["ts"] for r in store["heart_signals"]] == ["2026-08-09T10:30:00Z"]
    assert asked == [main._INGEST_SESSION_COLUMNS]


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


def test_consent_failing_to_read_records_nothing(store, monkeypatch):
    monkeypatch.setattr(main, "_consent",
                        lambda _uid: {**main._CONSENT_DENIED, "retrieved": False})
    out = _post_heart([_heart()])
    assert out["inserted"] == 0


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
    assert _post_heart([_heart()])["ok"]


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


def test_an_unreadable_consent_row_is_not_reported_as_a_refusal(store, monkeypatch):
    """Both record nothing; only the unreadable one is a fault."""
    monkeypatch.setattr(main, "_consent",
                        lambda _uid: {**main._CONSENT_DENIED, "retrieved": False})
    assert _post_heart([_heart()])["reason"] == "consent unavailable"

    out = main.ingest_face(
        main.FaceBatch(session_id=SESSION, samples=[{"emotion": "happy"}]),
        request=None,
    )
    assert out["reason"] == "consent unavailable"


def test_a_genuine_refusal_says_so(store):
    """Otherwise the test above passes for the wrong reason."""
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
    main._INGEST_LIMITER.hits.update({f"gone-{i}": [stale] for i in range(10)})
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

    main._INGEST_LIMITER.hits["busy"] = [time.monotonic()]   # a hit just now
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


def test_a_non_finite_heart_value_is_refused_by_the_model(store):
    """NaN/Infinity pass `float | None`, then fail the insert and take the batch down."""
    from pydantic import ValidationError

    for field in ("heart_rate_bpm", "rmssd_ms", "beat_coverage",
                  "sqi", "stress_score"):
        with pytest.raises(ValidationError):
            main.HeartBatch(session_id=SESSION,
                            samples=[_heart(**{field: float("nan")})])


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
