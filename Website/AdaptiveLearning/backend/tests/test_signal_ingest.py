"""Ingestion is the trust boundary, so these are the enforcement.

The sidecar runs on the student's own machine and POSTs with the student's own
bearer token, which means the client is not trusted. `_verify_session_owner`
answers "whose session"; everything here answers the two questions it does not:
**may this be recorded**, and **how much of it**.

The consent check matters most, and it is deliberately redundant with the
sidecar's own. A stale sidecar that kept sending after a student withdrew would
otherwise keep recording, and the withdrawal would look respected from every
surface that reads. Getting this wrong does not leak data to the wrong reader --
it records a child's body against their refusal.
"""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

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
            hit = [r for r in rows
                   if all(r.get(c) == v for c, v in self._filters.items())]
            return _Result(hit[0] if "single" in self._filters else hit)
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
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    # Each test gets its own rate-limit budget; the limiter has its own tests.
    monkeypatch.setattr(main, "_ingest_hits", {})
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


# ── consent decides what is written ──────────────────────────────────────────

def test_a_heart_sample_from_a_declined_sensor_is_not_stored(store):
    """Per *sample*, against the sensor named in `source`. One channel, two
    sensors, two separate permissions -- which is why the table carries
    `source` at all."""
    _consent(store, headband_optical_enabled=True)      # camera declined

    out = _post_heart([_heart(source="rppg")])

    assert out["inserted"] == 0
    assert out["dropped"] == 1
    assert store["heart_signals"] == []


def test_a_mixed_batch_keeps_the_consented_samples(store):
    """Dropping the batch would take the permitted samples with it, and a mixed
    batch is a legitimate thing for a client to send."""
    _consent(store, headband_optical_enabled=True)

    out = _post_heart([
        _heart(source="muse_optics", ts="2026-08-09T10:00:00Z"),
        _heart(source="rppg",        ts="2026-08-09T10:00:01Z"),
        _heart(source="muse_ppg",    ts="2026-08-09T10:00:02Z"),
    ])

    assert (out["inserted"], out["dropped"]) == (2, 1)
    assert {r["source"] for r in store["heart_signals"]} == {"muse_optics", "muse_ppg"}


def test_no_consent_row_records_nothing(store):
    """Absent means the same as all-false. No backfill, no default-on."""
    out = _post_heart([_heart()])
    assert (out["inserted"], out["dropped"]) == (0, 1)


def test_camera_consent_alone_permits_only_the_camera_source(store):
    _consent(store, camera_enabled=True)

    out = _post_heart([_heart(source="rppg"), _heart(source="muse_optics",
                                                     ts="2026-08-09T10:00:05Z")])
    assert (out["inserted"], out["dropped"]) == (1, 1)
    assert store["heart_signals"][0]["source"] == "rppg"


def test_face_ingestion_stops_when_the_camera_is_declined(store):
    """The stale-sidecar case: it kept sending, and the withdrawal must still
    hold at the boundary."""
    _consent(store, eeg_enabled=True)                    # camera declined

    out = main.ingest_face(
        main.FaceBatch(session_id=SESSION,
                       samples=[{"emotion": "happy", "emotion_confidence": 0.9}]),
        request=None,
    )
    assert out["inserted"] == 0
    assert store["face_signals"] == []


def test_face_ingestion_stores_both_emotion_fields(store):
    """`emotion_confidence` and `emotion_trusted` are what the fusion gate
    reads; dropping them on the way in would make the gate unreachable."""
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
    assert row["identity_confidence"] == 0.4, "the two confidences must not be conflated"


def test_consent_failing_to_read_records_nothing(store, monkeypatch):
    """`_consent` fails closed. A database problem must not become an
    unrecorded-permission grant."""
    monkeypatch.setattr(main, "_consent",
                        lambda _uid: {**main._CONSENT_DENIED, "retrieved": False})
    out = _post_heart([_heart()])
    assert out["inserted"] == 0


# ── the retry that would otherwise double every average ──────────────────────

def test_replaying_a_batch_inserts_nothing_the_second_time(store):
    """ON CONFLICT DO NOTHING against (session_id, source, ts).

    A retried batch is the normal consequence of a flaky connection, and the
    failure it used to cause is silent: nothing about a doubled row is visible
    in a chart except that the average is wrong."""
    _consent(store, headband_optical_enabled=True)
    batch = [_heart(ts="2026-08-09T10:00:00Z"), _heart(ts="2026-08-09T10:00:01Z")]

    first = _post_heart(batch)
    second = _post_heart(batch)

    assert first["inserted"] == 2
    assert len(store["heart_signals"]) == 2, "the replay doubled the rows"
    assert second["ok"]


def test_two_sources_may_report_the_same_instant(store):
    """Which is why `source` is in the key. A key without it would discard the
    second reading as a duplicate of the first."""
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
    """`_verify_session_owner` answers whose session and consent answers whether
    to record. Neither bounds volume, and volume is its own denial of service."""
    from fastapi import HTTPException

    _consent(store, headband_optical_enabled=True)
    monkeypatch.setattr(main, "_INGEST_RATE_LIMIT", 3)

    for _ in range(3):
        _post_heart([_heart()])

    with pytest.raises(HTTPException) as exc:
        _post_heart([_heart()])
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_the_limit_is_per_caller(store, monkeypatch):
    """One student exhausting their allowance must not lock out another."""
    _consent(store, headband_optical_enabled=True)
    monkeypatch.setattr(main, "_INGEST_RATE_LIMIT", 2)

    for _ in range(2):
        _post_heart([_heart()])

    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "student-2"})
    store["signal_consent"].append({"user_id": "student-2",
                                    "headband_optical_enabled": True,
                                    "camera_enabled": False, "eeg_enabled": False})
    assert _post_heart([_heart()])["ok"]
