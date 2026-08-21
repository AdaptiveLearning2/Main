"""Where the `heart` block meets the sampling loop.

The block is recomputed on a cadence and held between recomputes. Recomputing
every tick would waste cycles re-running autocorrelation on nearly identical
windows; not holding the result would leave a 1Hz poller missing most
readings.
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
    return OpticsWindow(np.zeros((samples, 4)), 64.0, 64.0, 1.0, 25.0, 0.02, 4)


def test_a_device_without_optics_produces_no_block():
    """Includes the simulator deliberately: it models no optical channel, and
    a simulated pulse would be a number on a parent's chart with nothing
    behind it."""
    assert _session().adapter.__class__.__name__ != "TcpMuseBridgeAdapter"
    assert _session()._optical_heart_block() is None


def test_the_block_is_computed_once_and_then_held():
    session = _session(_flat_window())
    blocks = [session._optical_heart_block() for _ in range(20)]

    assert session.adapter.reads == 1
    assert all(b is blocks[0] for b in blocks)
    assert blocks[0]["source"] == "muse_optics"


def test_losing_the_signal_drops_the_held_block():
    """Otherwise the last rate keeps being published, and recorded under fresh
    timestamps, for a headband that's off."""
    session = _session(_flat_window())
    assert session._optical_heart_block() is not None

    session._drop_held_heart_block()
    assert session._heart_block is None
    assert session._optical_heart_block() is None


def test_eeg_flapping_does_not_restart_the_heart_cadence():
    """`drain_samples` raises whenever no EEG sample arrives in its timeout, so
    flapping contact takes the no-data path repeatedly. Clearing the emit
    clock there would make the next good tick recompute immediately, minting
    a new `ts` for materially the same 25s window -- and since both writers
    dedupe on `ts`, that would write up to four near-identical rows a second.
    """
    session = _session(_flat_window())
    first = session._optical_heart_block()

    for _ in range(10):
        session._drop_held_heart_block()
        session._optical_heart_block()

    # One window read, so one stamp: the cadence survived the flap.
    assert session.adapter.reads == 1
    assert session._heart_emitted_at is not None

    # The tracker's anchor survives too -- EEG dropping out says nothing about
    # the optical emitters, and the anchor is what catches octave errors.
    assert session._heart_tracker is not None
    assert first is not None


def test_stopping_forgets_everything():
    """Unlike a flap, a real stop clears everything."""
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
    """The tracker sizes allowed movement from the last *accepted* rate, not
    the last recompute -- otherwise a run of refusals still reports one step's
    worth, so the first good window after a minute of silence would be judged
    against only 30 bpm of allowed movement.
    """
    seen = []

    def _spy(_window, _tracker, since):
        seen.append(since)
        return {"source": "muse_optics", "bpm": None, "rejected_by": "warming_up"}

    session = _session(_flat_window())
    monkeypatch.setattr("src.app.services.stream_manager.build_heart_record", _spy)
    monkeypatch.setattr("src.app.services.stream_manager.EMIT_EVERY_SECONDS", 0.0)
    for _ in range(3):
        session._optical_heart_block()

    # Never accepted, so every window reports "nothing measured yet" rather
    # than a step that shrinks back to one interval.
    assert seen == [0.0, 0.0, 0.0]
    assert session._heart_accepted_at is None


def test_the_cadence_is_the_step_the_estimator_was_validated_on():
    """`MAX_BPM_CHANGE_PER_S`, the continuity rule that rejects octave errors,
    is calibrated against a 10s step between 25s windows."""
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
    """Continuity needs the previous window to compare against, so the tracker
    lives on the session, per device, not per call."""
    session = _session(_flat_window())
    session._optical_heart_block()
    tracker = session._heart_tracker
    monkeypatch.setattr("src.app.services.stream_manager.EMIT_EVERY_SECONDS", 0.0)
    session._optical_heart_block()

    assert session._heart_tracker is tracker


@pytest.mark.anyio
async def test_a_stopped_stream_publishes_no_rate():
    """Stopping is itself a no-data condition. Otherwise `snapshot()` keeps
    serving the last rate from before `stop()`, indistinguishable from a live
    session, and it keeps being written under pull."""
    session = _session(_flat_window())
    session._optical_heart_block()
    session.running = True
    # `stop()` only resets if a stream was running; this is the started case.
    session._task = asyncio.create_task(asyncio.sleep(60))
    await session.stop()

    assert session._heart_block is None
    assert "heart" not in session.latest_payload
