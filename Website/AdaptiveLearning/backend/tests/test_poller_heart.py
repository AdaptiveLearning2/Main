"""The poller's heart write; under pull, `_may_record_heart` is the only consent enforcement."""
import pytest

import eeg_poller


class _Table:
    def __init__(self, sink, name):
        self.sink, self.name = sink, name

    def upsert(self, row, **kwargs):
        self.sink.append((self.name, row, kwargs))
        return self

    def insert(self, row):
        self.sink.append((self.name, row, {}))
        return self

    def execute(self):
        class _R:
            data = []
        return _R()


class _FakeSupabase:
    def __init__(self):
        self.writes = []

    def table(self, name):
        return _Table(self.writes, name)


def _payload(bpm=68.2, ts="2026-08-10T10:00:00+00:00", source="muse_optics"):
    return {
        "timestamp": "2026-08-10T10:00:07Z",
        "device_id": "station1",
        "heart": {"source": source, "bpm": bpm, "ts": ts, "confidence": 0.8,
                  "trusted": bpm is not None, "sample_rate_hz": 64.2},
    }


@pytest.fixture
def poller():
    """A poller with no running thread; tests drive `_record_heart` directly."""
    db = _FakeSupabase()
    p = eeg_poller._Poller(db, "student-1", "session-1", "station1")
    return p, db


@pytest.fixture(autouse=True)
def _restore_hooks():
    """Both hooks are module globals wired at import."""
    heart, eeg = eeg_poller._heart_consent_check, eeg_poller._consent_check
    yield
    eeg_poller.set_heart_consent_check(heart)
    eeg_poller.set_consent_check(eeg)


def _allow(*sources):
    eeg_poller.set_heart_consent_check(lambda user_id, source: source in sources)


def test_a_consented_reading_is_written(poller):
    p, db = poller
    _allow("muse_optics")
    p._record_heart(_payload(), loops=1)

    assert [name for name, _row, _kw in db.writes] == ["heart_signals"]
    _name, row, kwargs = db.writes[0]
    assert row["heart_rate_bpm"] == 68.2
    assert row["source"] == "muse_optics"
    assert row["user_id"] == "student-1"
    # Keyed on the reading's own stamp, not the tick's.
    assert row["ts"] == "2026-08-10T10:00:00+00:00"
    # Same dedupe key as /api/signals/heart, so a sidecar that also pushes doesn't double-count.
    assert kwargs["on_conflict"] == "session_id,source,ts"
    assert kwargs["ignore_duplicates"] is True
    assert p.heart_samples == 1


def test_a_refused_channel_records_nothing(poller):
    p, db = poller
    _allow()  # nothing consented
    p._record_heart(_payload(), loops=1)

    assert db.writes == []
    assert p.heart_samples == 0


def test_an_unwired_check_records_nothing(poller):
    """Fails closed: unwired must not look like a student who said yes."""
    p, db = poller
    eeg_poller.set_heart_consent_check(None)
    p._record_heart(_payload(), loops=1)

    assert db.writes == []


def test_a_failed_consent_read_records_nothing(poller):
    p, db = poller

    def _boom(_user_id, _source):
        raise RuntimeError("consent table unreachable")

    eeg_poller.set_heart_consent_check(_boom)
    p._record_heart(_payload(), loops=1)

    assert db.writes == []


def test_consent_is_checked_per_sensor(poller):
    """Allowing the headband and refusing the camera is not consent to rPPG."""
    p, db = poller
    _allow("muse_optics")
    p._record_heart(_payload(source="rppg", ts="a"), loops=1)
    assert db.writes == []

    p._record_heart(_payload(source="muse_optics", ts="b"), loops=1)
    assert len(db.writes) == 1


def test_a_rejected_window_is_not_a_row(poller):
    """A refused window reports `bpm: None`; gating on the block's presence writes a null row."""
    p, db = poller
    _allow("muse_optics")
    p._record_heart(_payload(bpm=None), loops=1)

    assert db.writes == []


def test_a_held_reading_is_written_once(poller):
    """The sidecar holds the block between recomputes, so the poller sees it every tick."""
    p, db = poller
    _allow("muse_optics")
    for _ in range(5):
        p._record_heart(_payload(), loops=1)

    assert len(db.writes) == 1

    p._record_heart(_payload(ts="2026-08-10T10:00:10+00:00"), loops=1)
    assert len(db.writes) == 2


def test_a_withdrawal_mid_session_stops_the_writes(poller, monkeypatch):
    p, db = poller
    monkeypatch.setattr(eeg_poller, "CONSENT_RECHECK_SECONDS", 0.0)
    _allow("muse_optics")
    p._record_heart(_payload(ts="a"), loops=1)
    assert len(db.writes) == 1

    _allow()  # student withdraws
    p._record_heart(_payload(ts="b"), loops=1)
    assert len(db.writes) == 1


def test_consent_is_not_read_once_per_reading(poller):
    """Consent is cached on a cadence, off the recording hot path."""
    p, db = poller
    calls = []

    def _counting(user_id, source):
        calls.append((user_id, source))
        return True

    eeg_poller.set_heart_consent_check(_counting)
    for i in range(4):
        p._record_heart(_payload(ts=f"stamp-{i}"), loops=1)

    assert len(db.writes) == 4
    assert len(calls) == 1


def test_a_failed_write_is_retried_on_the_next_tick(poller):
    """The block stays on the payload for ~40 more ticks, so a retry is free."""
    p, db = poller
    _allow("muse_optics")

    class _Failing:
        def upsert(self, *_a, **_k):
            return self

        def execute(self):
            raise RuntimeError("transient")

    p.supabase = type("S", (), {"table": lambda _s, _n: _Failing()})()
    p._record_heart(_payload(), loops=1)
    assert p.heart_samples == 0
    assert p.last_heart_ts is None, "a failed write must not consume the reading"

    p.supabase = db
    p._record_heart(_payload(), loops=1)
    assert len(db.writes) == 1
    assert p.heart_samples == 1


def test_a_refusal_consumes_the_reading(poller):
    """A refusal is final for this reading; re-deciding it would re-log it every tick."""
    p, _db = poller
    _allow()
    p._record_heart(_payload(), loops=1)

    assert p.last_heart_ts == "2026-08-10T10:00:00+00:00"


def test_withdrawing_eeg_consent_stops_the_heart_channel_too(monkeypatch):
    """Deliberate: the EEG gate stopping the poller ends headband heart too (errs safe).

    If this breaks because someone decoupled them, update CLAUDE.md too.
    """
    import eeg_client

    monkeypatch.setattr(eeg_client, "start_session", lambda device_id=None: {"ok": True})
    monkeypatch.setattr(eeg_client, "stop_session", lambda device_id=None: {"ok": True})
    monkeypatch.setattr(eeg_client, "get_state", lambda device_id=None, timeout=2.0: None)
    monkeypatch.setattr(eeg_poller, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(eeg_poller, "CONSENT_RECHECK_SECONDS", 0.0)
    # EEG withdrawn, headband optical still allowed.
    eeg_poller.set_consent_check(lambda _user_id: False)
    _allow("muse_optics")

    db = _FakeSupabase()
    p = eeg_poller._Poller(db, "student-1", "session-1", "station1")
    p.start()
    p.join(timeout=2.0)

    assert not p.is_alive(), "the poller should have stopped on the withdrawal"
    assert db.writes == [], "and recorded nothing on either channel"


def test_status_counts_heart_separately(poller):
    """EEG fine while heart is refused or unmeasurable is normal; one number would hide it."""
    p, _db = poller
    assert p.heart_samples == 0
    assert p.last_heart_ts is None
