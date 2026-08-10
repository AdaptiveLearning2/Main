"""The poller's heart write, and the consent gate in front of it.

Nothing wrote `heart_signals` from the pull path at all, so a co-located
deployment recorded no heart rate while an identical push one recorded it fully
-- the two-deployments-diverging failure the shared mapper exists to prevent,
one layer above it.

The consent tests are the important half. The poller writes with the
**service-role** client, so neither RLS nor `/api/signals/heart`'s per-sample
check reaches anything it inserts: `_may_record_heart` is the only enforcement
under `INGEST_MODE=pull`, and every one of these asserts on what reached the
table rather than on what the poller decided.
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
    """A poller object without a running thread -- `_record_heart` is what the
    loop calls, and driving the thread would make these timing tests."""
    db = _FakeSupabase()
    p = eeg_poller._Poller(db, "student-1", "session-1", "station1")
    return p, db


@pytest.fixture(autouse=True)
def _restore_hook():
    original = eeg_poller._heart_consent_check
    yield
    eeg_poller.set_heart_consent_check(original)


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
    # Upsert on the dedupe key, matching /api/signals/heart: a deployment left
    # on `pull` whose sidecar also pushes must not double-count this channel.
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
    """Fails closed. A deployment nobody wired the check into is otherwise
    indistinguishable from one whose student said yes, and only one may record."""
    p, db = poller
    eeg_poller.set_heart_consent_check(None)
    p._record_heart(_payload(), loops=1)

    assert db.writes == []


def test_a_failed_consent_read_records_nothing(poller):
    """Like `_consent` itself and unlike the reporting helpers: a read error must
    never be the reason a refusal stops being enforced."""
    p, db = poller

    def _boom(_user_id, _source):
        raise RuntimeError("consent table unreachable")

    eeg_poller.set_heart_consent_check(_boom)
    p._record_heart(_payload(), loops=1)

    assert db.writes == []


def test_consent_is_checked_per_sensor(poller):
    """One channel, two sensors, two permissions. A student who allowed the
    headband and refused the camera has not consented to rPPG."""
    p, db = poller
    _allow("muse_optics")
    p._record_heart(_payload(source="rppg", ts="a"), loops=1)
    assert db.writes == []

    p._record_heart(_payload(source="muse_optics", ts="b"), loops=1)
    assert len(db.writes) == 1


def test_a_rejected_window_is_not_a_row(poller):
    """The block always carries a `source`, and reports refusal as `bpm: None`
    with a `rejected_by`. Gating on the block would write a null row per tick."""
    p, db = poller
    _allow("muse_optics")
    p._record_heart(_payload(bpm=None), loops=1)

    assert db.writes == []


def test_a_held_reading_is_written_once(poller):
    """The sidecar holds the block between recomputes so a 1Hz poller sees every
    reading; without this the poller would rewrite each one on every tick."""
    p, db = poller
    _allow("muse_optics")
    for _ in range(5):
        p._record_heart(_payload(), loops=1)

    assert len(db.writes) == 1

    p._record_heart(_payload(ts="2026-08-10T10:00:10+00:00"), loops=1)
    assert len(db.writes) == 2


def test_a_withdrawal_mid_session_stops_the_writes(poller, monkeypatch):
    """A lesson outlives a change of mind, and the poller is the only thing
    enforcing one under pull."""
    p, db = poller
    monkeypatch.setattr(eeg_poller, "HEART_CONSENT_RECHECK_SECONDS", 0.0)
    _allow("muse_optics")
    p._record_heart(_payload(ts="a"), loops=1)
    assert len(db.writes) == 1

    _allow()  # student withdraws
    p._record_heart(_payload(ts="b"), loops=1)
    assert len(db.writes) == 1


def test_consent_is_not_read_once_per_reading(poller):
    """Cached on a cadence: a Supabase round trip per reading would put the
    consent table on the recording path."""
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


def test_status_counts_heart_separately(poller):
    """A session recording EEG happily while its heart channel is refused or
    unmeasurable is normal, and one combined number would call that healthy."""
    p, _db = poller
    assert p.heart_samples == 0
    assert p.last_heart_ts is None
