"""The `heart` block is recomputed on a cadence and held between recomputes so a 1 Hz poller sees it."""

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
    return OpticsWindow(np.zeros((samples, 4)), 64.0, 64.0, 1.0, 25.0, 0.02, 4)


def test_a_device_without_optics_produces_no_block():
    """No `optics_window` (the camera's shape) yields no block, not a `no_samples` one."""
    class _NoOptics:
        def disconnect(self):
            pass

    session = _session()
    session.adapter = _NoOptics()
    assert session._optical_heart_block() is None
    # The unpaired simulator answers a no_samples block, a different fact from no block.
    fresh = _session()
    assert fresh.adapter.__class__.__name__ == "SimulatedMuseIngestionAdapter"
    assert fresh._optical_heart_block()["rejected_by"] == "no_samples"


def test_the_block_is_computed_once_and_then_held():
    session = _session(_flat_window())
    blocks = [session._optical_heart_block() for _ in range(20)]

    assert session.adapter.reads == 1
    assert all(b is blocks[0] for b in blocks)
    assert blocks[0]["source"] == "muse_optics"


def test_losing_the_signal_drops_the_held_block():
    """Otherwise the last rate is recorded under fresh timestamps for a headband that's off."""
    session = _session(_flat_window())
    assert session._optical_heart_block() is not None

    session._drop_held_heart_block()
    assert session._heart_block is None
    assert session._optical_heart_block() is None


def test_eeg_flapping_does_not_restart_the_heart_cadence():
    """Resetting the emit clock on each no-data tick would mint a new `ts` for the same window."""
    session = _session(_flat_window())
    first = session._optical_heart_block()

    for _ in range(10):
        session._drop_held_heart_block()
        session._optical_heart_block()

    assert session.adapter.reads == 1
    assert session._heart_emitted_at is not None

    # The anchor (which catches octave errors) survives too: EEG dropout says nothing about optics.
    assert session._heart_tracker is not None
    assert first is not None


def test_stopping_forgets_everything():
    session = _session(_flat_window())
    session._optical_heart_block()

    session._reset_heart()
    assert session._heart_block is None
    assert session._heart_tracker is None
    assert session._heart_emitted_at is None

    # Next tick recomputes rather than waiting out the cadence.
    session._optical_heart_block()
    assert session.adapter.reads == 2


def test_the_continuity_anchor_ages_from_the_last_accepted_rate(monkeypatch):
    """Otherwise a run of refusals judges the next good window against one step's allowed movement."""
    seen = []

    def _spy(_window, _tracker, since):
        seen.append(since)
        return {"source": "muse_optics", "bpm": None, "rejected_by": "warming_up"}

    session = _session(_flat_window())
    monkeypatch.setattr("src.app.services.stream_manager.build_heart_record", _spy)
    monkeypatch.setattr("src.app.services.stream_manager.EMIT_EVERY_SECONDS", 0.0)
    for _ in range(3):
        session._optical_heart_block()

    # Never accepted, so every window reports "nothing measured yet".
    assert seen == [0.0, 0.0, 0.0]
    assert session._heart_accepted_at is None


def test_the_cadence_is_the_step_the_estimator_was_validated_on():
    """`MAX_BPM_CHANGE_PER_S` is calibrated against a 10 s step between 25 s windows."""
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
    session = _session(_flat_window())
    session._optical_heart_block()
    tracker = session._heart_tracker
    monkeypatch.setattr("src.app.services.stream_manager.EMIT_EVERY_SECONDS", 0.0)
    session._optical_heart_block()

    assert session._heart_tracker is tracker


@pytest.mark.anyio
async def test_a_stopped_stream_publishes_no_rate():
    """Otherwise `snapshot()` serves the pre-stop rate, which pull keeps writing."""
    session = _session(_flat_window())
    session._optical_heart_block()
    session.running = True
    # `stop()` only resets if a stream was running.
    session._task = asyncio.create_task(asyncio.sleep(60))
    await session.stop()

    assert session._heart_block is None
    assert "heart" not in session.latest_payload
