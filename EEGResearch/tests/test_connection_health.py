"""Device liveness as the status endpoints report it, and the bridge's own
recovery of a dropped link as the sidecar passes it through.

Two layers, both new. The native bridge now retries a BLE link that dropped
on its own and says so on every status line; `_apply_bridge_ingestion_fields`
has to carry those fields or a consumer sees a headband vanish and come back
with nothing in between. And `DeviceSession` now tracks when it last produced
a real reading, so a consumer can tell "went quiet ten seconds ago" from
"never had a sensor" without keeping its own clock.
"""

from __future__ import annotations

import asyncio
import socket
import time

import pytest

from src.app.config import DeviceConfig, get_settings
from src.app.models import EegSample
from src.app.services.eeg_ingestion import (
    TcpMuseBridgeAdapter,
    _apply_bridge_ingestion_fields,
)
from src.app.services.stream_manager import DeviceSession
from datetime import datetime, timezone


# ── the bridge's reconnect fields survive the pass-through ──────────────────

def test_reconnect_fields_are_carried_with_their_types():
    target: dict = {}
    _apply_bridge_ingestion_fields(target, {
        "auto_reconnect": True, "reconnecting": 1, "reconnect_attempt": "2",
        "reconnect_max_attempts": 5, "reconnect_exhausted": False,
        "eeg_age_ms": 137,
    })
    assert target["auto_reconnect"] is True
    assert target["reconnecting"] is True
    assert target["reconnect_attempt"] == 2
    assert target["reconnect_max_attempts"] == 5
    assert target["reconnect_exhausted"] is False
    assert target["eeg_age_ms"] == 137


def test_eeg_age_null_is_kept_apart_from_zero_and_from_absent():
    """null: connected, nothing arrived yet. 0: arrived this instant. Absent:
    an older bridge that does not report it. Three states, and collapsing any
    two would make a stalled link look like a fresh one."""
    fresh: dict = {}
    _apply_bridge_ingestion_fields(fresh, {"eeg_age_ms": None})
    assert fresh["eeg_age_ms"] is None

    now: dict = {}
    _apply_bridge_ingestion_fields(now, {"eeg_age_ms": 0})
    assert now["eeg_age_ms"] == 0

    old_bridge: dict = {}
    _apply_bridge_ingestion_fields(old_bridge, {"muse_connected": True})
    assert "eeg_age_ms" not in old_bridge
    assert "reconnecting" not in old_bridge


def test_malformed_reconnect_fields_keep_the_prior_value():
    target = {"reconnect_attempt": 1, "eeg_age_ms": 40}
    _apply_bridge_ingestion_fields(target, {"reconnect_attempt": "x", "eeg_age_ms": "y"})
    assert target["reconnect_attempt"] == 1
    assert target["eeg_age_ms"] == 40


# ── TCP reconnect backoff ───────────────────────────────────────────────────

def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_a_refused_connection_is_not_retried_until_the_backoff_elapses():
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=_closed_port(), timeout_seconds=1)
    assert adapter.connect_wait_remaining() == 0.0

    assert adapter._try_connect() is False
    assert adapter.connect_failures == 1
    first_wait = adapter.connect_wait_remaining()
    assert 0.0 < first_wait <= TcpMuseBridgeAdapter.CONNECT_BACKOFF_MIN_S

    # Inside the window: no socket is opened, the failure count stays put.
    assert adapter._try_connect() is False
    assert adapter.connect_failures == 1

    # drain_samples says how long, so the log line reads as "waiting", not
    # as a fresh failure every 250ms.
    with pytest.raises(RuntimeError, match=r"next attempt in \d+\.\ds"):
        adapter.drain_samples(1)


def test_the_backoff_doubles_to_a_cap_and_never_past_it():
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=_closed_port(), timeout_seconds=1)
    waits = []
    for _ in range(6):
        adapter._next_connect_at = 0.0  # let the next attempt through
        assert adapter._try_connect() is False
        waits.append(adapter.connect_wait_remaining())
    # Each attempt's wait is at least the previous one's up to the cap: the
    # sequence is 0.5, 1, 2, 4, 5, 5 minus however long the assertions took.
    for earlier, later in zip(waits, waits[1:]):
        assert later >= earlier - 0.05
    assert waits[-1] <= TcpMuseBridgeAdapter.CONNECT_BACKOFF_MAX_S
    assert waits[-1] > TcpMuseBridgeAdapter.CONNECT_BACKOFF_MAX_S * 0.9
    assert adapter.connect_failures == 6


def test_a_successful_connection_resets_the_backoff():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=port, timeout_seconds=1)
    try:
        # Pretend a run of failures happened first.
        adapter._connect_backoff_s = TcpMuseBridgeAdapter.CONNECT_BACKOFF_MAX_S
        adapter._next_connect_at = 0.0
        assert adapter._try_connect() is True
        assert adapter._connect_backoff_s == TcpMuseBridgeAdapter.CONNECT_BACKOFF_MIN_S
        assert adapter.connect_wait_remaining() == 0.0
    finally:
        adapter.disconnect()
        listener.close()


# ── DeviceSession health ────────────────────────────────────────────────────

class _Adapter:
    """Reads succeed or fail on command; carries the preset fields a bridge would."""

    def __init__(self):
        self.fail = False
        self.meta = {"requested_preset": "", "active_preset": ""}

    def connect(self):
        pass

    def disconnect(self):
        pass

    def read_sample(self):
        if self.fail:
            raise RuntimeError("no bridge")
        return EegSample(datetime.now(timezone.utc), 700.0, 700.0, 700.0, 700.0)

    def get_ingestion_meta(self):
        return dict(self.meta)


def _session() -> DeviceSession:
    s = DeviceSession("station1", get_settings(),
                      DeviceConfig(device_id="station1", kind="sim",
                                   host="127.0.0.1", port=8765))
    s.adapter = _Adapter()
    return s


async def _tick(session: DeviceSession, n: int = 1) -> None:
    """Run the loop for n iterations and stop."""
    period = 1 / max(1, session.settings.eeg_sample_hz)
    session.running = True
    task = asyncio.create_task(session._loop())
    await asyncio.sleep(period * n + period * 0.5)
    session.running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_before_any_reading_the_device_reports_never_not_old():
    fields = _session().health_fields()
    assert fields["last_good_ts"] is None
    assert fields["last_good_age_s"] is None
    assert fields["consecutive_errors"] == 0
    assert fields["preset_mismatch"] is False


def test_a_good_tick_stamps_the_reading_and_clears_the_error_run():
    session = _session()
    session.consecutive_errors = 4
    asyncio.run(_tick(session))
    fields = session.health_fields()
    assert fields["last_good_ts"] == session.latest_payload["timestamp"]
    assert fields["last_good_age_s"] is not None and fields["last_good_age_s"] >= 0.0
    assert fields["consecutive_errors"] == 0


def test_failed_reads_count_up_and_the_last_good_stamp_stands():
    """The stamp is the point: it keeps saying when the last real reading was
    while the errors climb, which is what lets a consumer say "quiet for 8s"
    rather than reporting each failed tick as the newest fact."""
    session = _session()
    asyncio.run(_tick(session))
    stamp = session.last_good_ts
    session.adapter.fail = True
    asyncio.run(_tick(session, 3))
    fields = session.health_fields()
    assert fields["consecutive_errors"] >= 2
    assert fields["errors_seen"] >= 2
    assert fields["last_good_ts"] == stamp


def test_the_age_grows_between_reads_rather_than_being_frozen():
    session = _session()
    session._note_good_tick("2026-09-02T10:00:00+00:00")
    session.last_good_at -= 7.0
    assert session.health_fields()["last_good_age_s"] == pytest.approx(7.0, abs=0.2)


def test_a_preset_mismatch_is_reported_only_after_it_has_settled():
    """A preset switch after CONNECTED interrupts streaming and the
    configuration is re-read live, so the two disagree for a moment on every
    good connection. Only a disagreement that outlasts the settle window is a
    request the headband ignored."""
    session = _session()
    session._note_preset({"requested_preset": "PRESET_1035", "active_preset": "PRESET_21"})
    assert session.health_fields()["preset_mismatch"] is False
    session._preset_mismatch_since -= DeviceSession.PRESET_SETTLE_SECONDS + 1
    assert session.health_fields()["preset_mismatch"] is True

    # Agreement clears it outright, and so does either side going unknown --
    # no headband is not a headband on the wrong preset.
    session._note_preset({"requested_preset": "PRESET_1035", "active_preset": "PRESET_1035"})
    assert session._preset_mismatch_since is None
    session._note_preset({"requested_preset": "PRESET_1035", "active_preset": ""})
    assert session._preset_mismatch_since is None


def test_both_status_shapes_carry_the_same_health_fields():
    """/api/v1/muse/status reads muse_ingestion_snapshot(); /api/v1/state and
    the push client read snapshot()['ingestion']. One source, so the two
    cannot answer differently about whether a device is alive."""
    session = _session()
    asyncio.run(_tick(session))
    a = session.snapshot()["ingestion"]
    b = session.muse_ingestion_snapshot()
    for key in ("last_good_ts", "last_good_age_s", "errors_seen",
                "consecutive_errors", "preset_mismatch"):
        assert key in a and key in b
    assert a["last_good_ts"] == b["last_good_ts"]


def test_stop_forgets_the_last_reading_with_the_rest_of_the_session():
    session = _session()
    asyncio.run(_tick(session))
    assert session.last_good_ts is not None

    async def run():
        session.running = True
        session._task = asyncio.create_task(asyncio.sleep(60))
        await session.stop()
    asyncio.run(run())
    assert session.last_good_ts is None
    assert session.last_good_at is None
    assert session.consecutive_errors == 0
