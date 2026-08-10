"""The `heart` block built from a headband optical window.

Two things are pinned: that every refusal is named rather than reported as a
null reading, and that a real recording gets through the gates and produces the
rate the fixture is known to contain.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from src.app.services.eeg_ingestion import OpticsWindow, TcpMuseBridgeAdapter
from src.app.services.optics_processing import (
    EMIT_EVERY_SECONDS,
    RATE_WINDOW_SECONDS,
    build_heart_record,
)
from src.app.services.ppg_processing import HeartRateTracker

# Ground truth for optics_rest_60s: 67.9 bpm by independent spectral analysis,
# and the wearer's watch agreed. Same slice test_ppg_processing validates on.
RESTING_BPM = 68.0


def _window(samples=1600, fs=64.0, received=64.0, span=None, gap=None,
            channels=4, unusable=None):
    span = RATE_WINDOW_SECONDS if span is None else span
    data = np.zeros((samples, channels)) if samples else np.empty((0, channels))
    completeness = (received / fs) if (fs and received is not None) else None
    return OpticsWindow(data, fs, received, completeness, span, gap, channels, unusable)


def _build(window, tracker=None, since=EMIT_EVERY_SECONDS):
    return build_heart_record(window, tracker or HeartRateTracker(), since)


def test_a_refusal_is_never_a_reading():
    """The property the ingestion paths depend on.

    `source` is set unconditionally, so a caller enqueuing on the block rather
    than on the reading writes a null row every tick -- ~14k an hour at 4Hz,
    each one counted as a sample by the aggregates.
    """
    record = _build(_window(samples=0, fs=None, received=None, span=0.0))
    assert record["source"] == "muse_optics"
    assert record["bpm"] is None
    assert record["trusted"] is False
    assert record["rejected_by"] == "no_samples"


def test_the_first_seconds_of_a_session_are_warming_up_not_a_failure():
    record = _build(_window(samples=320, span=5.0))
    assert record["rejected_by"] == "warming_up"
    assert record["window_coverage"] == pytest.approx(0.2)


def test_an_unmeasured_rate_is_refused_rather_than_assumed():
    """Falling back to the preset's nominal 64Hz is the assumption the
    measurement exists to remove: a link running slow would then report every
    rate high by exactly the shortfall, with full confidence."""
    record = _build(_window(fs=None, received=None))
    assert record["rejected_by"] == "unmeasured_sample_rate"
    assert record["sample_rate_hz"] is None


def test_a_window_that_is_mostly_interpolation_is_refused():
    """The gap this opened, measured rather than reasoned about.

    `fs` is read off `seq`, which counts what the headband *sent*, so it is 64Hz
    whether or not the samples arrived; `window_coverage` is elapsed span, which
    the surviving samples still bracket. Both stay healthy while the grid fills
    with `np.interp` output -- and interpolation manufactures the smooth
    periodicity autocorrelation rewards, so the result was a *confident* wrong
    rate, not a noisy one.
    """
    record = _build(_window(fs=64.0, received=2.0))
    assert record["rejected_by"] == "effective_rate_too_low"
    # The link looks fine and the window looks full. Only the third number says
    # otherwise, which is why it had to be carried separately.
    assert record["sample_rate_hz"] == 64.0
    assert record["window_coverage"] >= 1.0
    assert record["completeness"] == pytest.approx(0.031, abs=0.001)


def test_a_corrupt_sample_index_says_so_rather_than_no_samples():
    """Empty channels alone reach the caller as `no_samples`, which is what a
    headband that never emitted reports -- the opposite situation."""
    record = _build(_window(samples=0, unusable="corrupt_sample_index"))
    assert record["rejected_by"] == "corrupt_sample_index"


def test_a_collapsed_link_is_refused_and_says_so():
    record = _build(_window(fs=6.0))
    assert record["rejected_by"] == "sample_rate_too_low"
    # Reported even though it is the reason for the rejection: it is the
    # diagnosis, and a row that hid it could not be read back.
    assert record["sample_rate_hz"] == 6.0


def test_a_long_gap_is_refused_because_interpolation_would_invent_it():
    record = _build(_window(gap=2.5))
    assert record["rejected_by"] == "sampling_gap"
    assert record["largest_gap_s"] == 2.5


def test_flat_channels_produce_no_rate():
    record = _build(_window())
    assert record["bpm"] is None
    assert record["rejected_by"] is not None


def _window_from(name: str, through_seconds: float | None = None):
    """A window built the way the sidecar builds one: bridge lines in, buffer,
    then the same `optics_window` call the tick makes."""
    with gzip.open(Path(__file__).parent / "fixtures" / name, "rt", encoding="utf-8") as fh:
        frames = [json.loads(line) for line in fh if line.strip()]
    if through_seconds is not None:
        start = frames[0]["mono_ts_ms"]
        frames = [f for f in frames if f["mono_ts_ms"] - start <= through_seconds * 1000]
    adapter = TcpMuseBridgeAdapter("127.0.0.1", 8765, 1)
    for frame in frames:
        adapter._store_optics(frame)
    return adapter.optics_window(RATE_WINDOW_SECONDS)


@pytest.fixture(scope="module")
def recorded_window():
    # 20-45s of the resting recording: the same slice test_ppg_processing uses,
    # past the noisy opening, against a watch-confirmed 67.9 bpm.
    return _window_from("optics_rest_60s.jsonl.gz", through_seconds=45.0)


def test_a_real_resting_recording_produces_its_known_rate(recorded_window):
    """The whole path, from bridge lines to the block that gets recorded.

    Seated and at rest, which is the only condition this is cleared for: the
    same estimator reports step cadence at confidence 1.00 through gait, and
    nothing derived from these channels tells the two oscillators apart.
    """
    record = _build(recorded_window)
    assert record["rejected_by"] is None
    assert record["bpm"] == pytest.approx(RESTING_BPM, abs=3.0)
    assert record["ts"]
    assert record["trusted"] is True
    assert record["confidence"] > 0.55
    assert record["channel_count"] == 4
    assert record["sample_rate_hz"] == pytest.approx(64.2, abs=0.5)


@pytest.mark.parametrize("keep_one_in,was", [(32, 55.8), (64, 44.0)])
def test_heavy_sample_loss_is_refused_on_a_real_recording(keep_one_in, was):
    """The regression, on the fixture that made it visible.

    Decimating a window that reads 69.4 bpm intact used to produce these rates
    at **confidence 1.00** with `rejected_by: None` and `trusted: True` -- a
    25 bpm error at one-in-64, shipped as a measurement. Parametrised with the
    numbers it actually reported so this cannot be quietly re-broken into
    something merely different.
    """
    with gzip.open(Path(__file__).parent / "fixtures" / "optics_rest_60s.jsonl.gz",
                   "rt", encoding="utf-8") as fh:
        frames = [json.loads(line) for line in fh if line.strip()]
    start = frames[0]["mono_ts_ms"]
    frames = [f for f in frames if f["mono_ts_ms"] - start <= 45_000]

    adapter = TcpMuseBridgeAdapter("127.0.0.1", 8765, 1)
    for i, frame in enumerate(frames):
        if i % keep_one_in == 0:
            adapter._store_optics(frame)
    record = _build(adapter.optics_window(RATE_WINDOW_SECONDS))

    assert record["bpm"] is None, f"still reporting a rate (was {was} bpm)"
    assert record["rejected_by"] == "effective_rate_too_low"
    assert record["trusted"] is False


def test_isolated_gaps_do_not_distort_the_rate():
    """Why MAX_GAP_SECONDS is not the defence against sample loss.

    A second is longer than a cardiac cycle above 60 bpm, so a 0.92s hole
    swallows a whole beat -- which sounds like it should matter. Six of them,
    each swallowing a beat, move the reported rate by 0.6 bpm. A 25s window
    holds ~28 beats. The damage comes from the proportion of the window that is
    reconstructed, not from the length of any one hole, which is what
    `received_rate_hz` measures and this does not.
    """
    with gzip.open(Path(__file__).parent / "fixtures" / "optics_rest_60s.jsonl.gz",
                   "rt", encoding="utf-8") as fh:
        frames = [json.loads(line) for line in fh if line.strip()]
    start = frames[0]["mono_ts_ms"]
    frames = [f for f in frames if f["mono_ts_ms"] - start <= 45_000]

    blocked = set()
    for hole in range(6):
        at = int(len(frames) * (hole + 1) / 7)
        blocked.update(range(at, at + 58))  # ~0.92s at 64Hz

    adapter = TcpMuseBridgeAdapter("127.0.0.1", 8765, 1)
    for i, frame in enumerate(frames):
        if i not in blocked:
            adapter._store_optics(frame)
    window = adapter.optics_window(RATE_WINDOW_SECONDS)
    record = _build(window)

    assert window.largest_gap_seconds == pytest.approx(0.92, abs=0.05)
    assert record["rejected_by"] is None
    assert record["bpm"] == pytest.approx(RESTING_BPM, abs=3.0)


def test_light_sample_loss_still_reads_correctly():
    """The gate has to cost windows that are genuinely usable, or it is just a
    stricter version of not working. One in four lost leaves 16Hz, comfortably
    above the pulse band, and the rate is unchanged."""
    with gzip.open(Path(__file__).parent / "fixtures" / "optics_rest_60s.jsonl.gz",
                   "rt", encoding="utf-8") as fh:
        frames = [json.loads(line) for line in fh if line.strip()]
    start = frames[0]["mono_ts_ms"]
    frames = [f for f in frames if f["mono_ts_ms"] - start <= 45_000]

    adapter = TcpMuseBridgeAdapter("127.0.0.1", 8765, 1)
    for i, frame in enumerate(frames):
        if i % 4 == 0:
            adapter._store_optics(frame)
    record = _build(adapter.optics_window(RATE_WINDOW_SECONDS))

    assert record["rejected_by"] is None
    assert record["bpm"] == pytest.approx(RESTING_BPM, abs=3.0)


def test_every_record_carries_a_parseable_stamp_of_its_own():
    """Both writers key the row on this rather than on the tick's timestamp.

    The block is held on the payload between recomputes, so it arrives on ~40
    consecutive ticks; without a stamp of its own, one measurement would be
    forty rows on the push path and mostly missed readings on the pull one.
    Present on rejections too, so the field has one meaning in both cases.
    """
    for window in (_window(), _window(samples=0, fs=None, span=0.0)):
        stamp = _build(window)["ts"]
        parsed = datetime.fromisoformat(stamp)
        # Timezone-aware: `heart_signals.ts` is timestamptz, and a naive stamp
        # would be read as whatever the database's zone happens to be.
        assert parsed.tzinfo is not None
