"""Where the `heart` block meets the sampling loop.

The block is recomputed on a cadence and held in between. Both halves matter to
the ingestion paths: recomputing per tick would run an autocorrelation sixteen
times a second for the same answer and compare each window against a
predecessor 99% itself, while *not* holding the result would leave a 1Hz poller
polling straight past most readings.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from src.app.config import DeviceConfig, get_settings
from src.app.services.eeg_ingestion import OpticsWindow
from src.app.services.stream_manager import DeviceSession


class _StubAdapter:
    """Just enough of the TCP adapter: a window, and a count of the reads."""

    def __init__(self, window):
        self.window = window
        self.reads = 0

    def optics_window(self, _seconds):
        self.reads += 1
        return self.window

    def disconnect(self):
        pass


def _session(window=None) -> DeviceSession:
    session = DeviceSession("station1", get_settings(),
                            DeviceConfig(device_id="station1", kind="sim",
                                         host="127.0.0.1", port=8765))
    if window is not None:
        session.adapter = _StubAdapter(window)
    return session


def _flat_window(samples=1600):
    return OpticsWindow(np.zeros((samples, 4)), 64.0, 25.0, 0.02, 4)


def test_a_device_without_optics_produces_no_block():
    """The simulator included, deliberately: it does not model an optical
    channel, and a simulated pulse would be a number on a parent's chart with
    nothing behind it."""
    assert _session().adapter.__class__.__name__ != "TcpMuseBridgeAdapter"
    assert _session()._optical_heart_block() is None


def test_the_block_is_computed_once_and_then_held():
    session = _session(_flat_window())
    blocks = [session._optical_heart_block() for _ in range(20)]

    assert session.adapter.reads == 1
    assert all(b is blocks[0] for b in blocks)
    assert blocks[0]["source"] == "muse_optics"


def test_losing_the_signal_drops_the_held_block():
    """Otherwise the last rate keeps being published -- and, under fresh
    timestamps, keeps being recorded -- for a headband that is off."""
    session = _session(_flat_window())
    assert session._optical_heart_block() is not None

    session._reset_heart()
    assert session._heart_block is None
    assert session._heart_tracker is None

    # And the next tick recomputes rather than waiting out the cadence.
    session._optical_heart_block()
    assert session.adapter.reads == 2


def test_the_cadence_is_the_step_the_estimator_was_validated_on():
    """`MAX_BPM_CHANGE_PER_S` -- the continuity rule that rejects octave errors
    -- is calibrated against a 10s step between 25s windows."""
    from src.app.services import optics_processing

    assert optics_processing.EMIT_EVERY_SECONDS == 10.0
    assert optics_processing.RATE_WINDOW_SECONDS == 25.0


def test_a_recomputed_block_replaces_the_held_one(monkeypatch):
    session = _session(_flat_window())
    first = session._optical_heart_block()
    monkeypatch.setattr("src.app.services.stream_manager.EMIT_EVERY_SECONDS", 0.0)
    second = session._optical_heart_block()

    assert second is not first
    assert session.adapter.reads == 2


def test_the_tracker_survives_between_windows(monkeypatch):
    """Continuity needs the previous window to compare against, which is state
    a single call cannot hold -- so it lives on the session, per device."""
    session = _session(_flat_window())
    session._optical_heart_block()
    tracker = session._heart_tracker
    monkeypatch.setattr("src.app.services.stream_manager.EMIT_EVERY_SECONDS", 0.0)
    session._optical_heart_block()

    assert session._heart_tracker is tracker


@pytest.mark.anyio
async def test_a_stopped_stream_publishes_no_rate():
    """Stopping is itself a no-data condition. Without this, `snapshot()` keeps
    serving the last rate from before `stop()`, indistinguishable from a live
    session -- and under pull it keeps being written."""
    session = _session(_flat_window())
    session._optical_heart_block()
    session.running = True
    # `stop()` gates its resets on there having been a stream to stop, so a
    # session that never started still reports idle rather than a fabricated
    # zero reading. This is the started case.
    session._task = asyncio.create_task(asyncio.sleep(60))
    await session.stop()

    assert session._heart_block is None
    assert "heart" not in session.latest_payload
