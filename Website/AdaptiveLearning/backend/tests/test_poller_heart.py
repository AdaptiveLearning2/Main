"""The poller's heart write, and the consent gate in front of it.

The pull path used to write nothing to `heart_signals`, so a co-located
deployment recorded no heart rate while an identical push deployment recorded
it fully.

The consent tests matter most. The poller writes with the service-role
client, so neither RLS nor `/api/signals/heart`'s per-sample check reaches
anything it inserts -- `_may_record_heart` is the only enforcement under
`INGEST_MODE=pull`. Every test here checks what actually reached the table,
not what the poller decided.
"""
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
    """A poller with no running thread. `_record_heart` is what the loop
    calls, and driving a real thread would make these timing-dependent."""
    db = _FakeSupabase()
    p = eeg_poller._Poller(db, "student-1", "session-1", "station1")
    return p, db


@pytest.fixture(autouse=True)
def _restore_hooks():
    """Restores both hooks. They're module globals wired once at import, so a
    test that left one swapped would silently affect the next test."""
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
    # Upserts on the dedupe key, matching /api/signals/heart: a deployment on
    # `pull` whose sidecar also pushes must not double-count this channel.
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
    """Fails closed. A deployment where nobody wired up the check would
    otherwise be indistinguishable from one where the student said yes."""
    p, db = poller
    eeg_poller.set_heart_consent_check(None)
    p._record_heart(_payload(), loops=1)

    assert db.writes == []


def test_a_failed_consent_read_records_nothing(poller):
    """Like `_consent` itself, and unlike the reporting helpers: a read error
    must never be the reason a refusal stops being enforced."""
    p, db = poller

    def _boom(_user_id, _source):
        raise RuntimeError("consent table unreachable")

    eeg_poller.set_heart_consent_check(_boom)
    p._record_heart(_payload(), loops=1)

    assert db.writes == []


def test_consent_is_checked_per_sensor(poller):
    """One channel, two sensors, two permissions: a student who allowed the
    headband and refused the camera has not consented to rPPG."""
    p, db = poller
    _allow("muse_optics")
    p._record_heart(_payload(source="rppg", ts="a"), loops=1)
    assert db.writes == []

    p._record_heart(_payload(source="muse_optics", ts="b"), loops=1)
    assert len(db.writes) == 1


def test_a_rejected_window_is_not_a_row(poller):
    """A refused window reports `bpm: None` with a `rejected_by`. Gating on
    the block's presence instead would write a null row every tick."""
    p, db = poller
    _allow("muse_optics")
    p._record_heart(_payload(bpm=None), loops=1)

    assert db.writes == []


def test_a_held_reading_is_written_once(poller):
    """The sidecar holds the block between recomputes so a 1Hz poller sees
    every reading -- without dedup the poller would rewrite it every tick."""
    p, db = poller
    _allow("muse_optics")
    for _ in range(5):
        p._record_heart(_payload(), loops=1)

    assert len(db.writes) == 1

    p._record_heart(_payload(ts="2026-08-10T10:00:10+00:00"), loops=1)
    assert len(db.writes) == 2


def test_a_withdrawal_mid_session_stops_the_writes(poller, monkeypatch):
    """A lesson can outlive a change of mind, and under pull the poller is
    the only thing enforcing that withdrawal."""
    p, db = poller
    monkeypatch.setattr(eeg_poller, "CONSENT_RECHECK_SECONDS", 0.0)
    _allow("muse_optics")
    p._record_heart(_payload(ts="a"), loops=1)
    assert len(db.writes) == 1

    _allow()  # student withdraws
    p._record_heart(_payload(ts="b"), loops=1)
    assert len(db.writes) == 1


def test_consent_is_not_read_once_per_reading(poller):
    """Consent is cached on a cadence -- a Supabase round trip per reading
    would put the consent table on the recording hot path."""
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
    """The block stays on the payload for ~40 more ticks, so a retry is free.
    The timestamp used to be marked as consumed before the write succeeded,
    which turned one transient insert error into a permanently lost reading.
    """
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
    """The opposite case: a refusal is final for this reading, so re-deciding
    it on every later tick would just re-log the same refusal repeatedly."""
    p, _db = poller
    _allow()
    p._record_heart(_payload(), loops=1)

    assert p.last_heart_ts == "2026-08-10T10:00:00+00:00"


def test_withdrawing_eeg_consent_stops_the_heart_channel_too(monkeypatch):
    """Deliberate behaviour, pinned here because it's not obvious.
    `_record_heart` runs inside the poller loop, so the EEG consent gate
    killing the poller also ends headband heart recording -- even though
    `headband_optical` is consented separately. Accepted because it errs
    safe (the pull path records less than consent allows, never more). If
    this test breaks because someone decoupled them, update CLAUDE.md too.
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
    """A session recording EEG fine while its heart channel is refused or
    unmeasurable is normal -- one combined number would hide that."""
    p, _db = poller
    assert p.heart_samples == 0
    assert p.last_heart_ts is None
