"""The `heart` block from a headband optical window: every refusal named, a real recording read correctly."""

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

# optics_rest_60s ground truth: 67.9 bpm spectrally, matching the wearer's watch.
RESTING_BPM = 68.0


def _window(samples=1600, fs=64.0, received=64.0, span=None, gap=None,
            channels=4, unusable=None):
    span = RATE_WINDOW_SECONDS if span is None else span
    data = np.zeros((samples, channels)) if samples else np.empty((0, channels))
    completeness = (received / fs) if (fs and received is not None) else None
    return OpticsWindow(data, fs, received, completeness, span, gap, channels, unusable)


def _build(window, tracker=None, since=EMIT_EVERY_SECONDS):
    if tracker is not None:
        return build_heart_record(window, tracker, since)
    # A fresh tracker holds its first reading; warm it so these test the gates, not anchor policy.
    warm = HeartRateTracker()
    build_heart_record(window, warm, since)
    return build_heart_record(window, warm, since)


def test_a_refusal_is_never_a_reading():
    """`source` is always set, so a caller enqueuing on the block would write a null row every tick."""
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
    """A nominal 64 Hz fallback would skew every rate on a slow link, at full confidence."""
    record = _build(_window(fs=None, received=None))
    assert record["rejected_by"] == "unmeasured_sample_rate"
    assert record["sample_rate_hz"] is None


def test_a_window_that_is_mostly_interpolation_is_refused():
    """`fs` counts what was sent; interpolated fill yields a confident wrong rate."""
    record = _build(_window(fs=64.0, received=2.0))
    assert record["rejected_by"] == "effective_rate_too_low"
    # Link and span look fine; only completeness says otherwise.
    assert record["sample_rate_hz"] == 64.0
    assert record["window_coverage"] >= 1.0
    assert record["completeness"] == pytest.approx(0.031, abs=0.001)


def test_a_corrupt_sample_index_says_so_rather_than_no_samples():
    """`no_samples` is what a headband that never emitted reports."""
    record = _build(_window(samples=0, unusable="corrupt_sample_index"))
    assert record["rejected_by"] == "corrupt_sample_index"


def test_a_collapsed_link_is_refused_and_says_so():
    record = _build(_window(fs=6.0))
    assert record["rejected_by"] == "sample_rate_too_low"
    # Reported even though it caused the rejection: it is the diagnosis.
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
    """Builds a window the way the sidecar does: bridge lines, buffer, `optics_window`."""
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
    # 20-45 s, past the noisy opening: the slice test_ppg_processing uses.
    return _window_from("optics_rest_60s.jsonl.gz", through_seconds=45.0)


def test_a_real_resting_recording_produces_its_known_rate(recorded_window):
    """Cleared for seated rest only: through gait the estimator reports step cadence at confidence 1.00."""
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
    """`was` is the wrong rate this decimation reported, trusted, at confidence 1.00 before the gate."""
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
    """Damage scales with the proportion reconstructed, not any one hole's length."""
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


@pytest.mark.parametrize("keep_one_in,effective_hz,accepted", [
    (4, 16.1, True),
    (5, 12.9, True),   # 20% completeness
    (6, 10.7, True),   # last rung above the bar
    (7, 9.2, False),   # first rung below it
])
def test_the_gate_sits_where_the_estimate_actually_breaks(keep_one_in, effective_hz, accepted):
    """Gates the received rate, not completeness: 17% of a fast link still clears Nyquist."""
    with gzip.open(Path(__file__).parent / "fixtures" / "optics_rest_60s.jsonl.gz",
                   "rt", encoding="utf-8") as fh:
        frames = [json.loads(line) for line in fh if line.strip()]
    start = frames[0]["mono_ts_ms"]
    frames = [f for f in frames if f["mono_ts_ms"] - start <= 45_000]

    adapter = TcpMuseBridgeAdapter("127.0.0.1", 8765, 1)
    for i, frame in enumerate(frames):
        if i % keep_one_in == 0:
            adapter._store_optics(frame)
    window = adapter.optics_window(RATE_WINDOW_SECONDS)
    record = _build(window)

    assert window.received_rate_hz == pytest.approx(effective_hz, abs=0.3)
    if accepted:
        assert record["rejected_by"] is None
        assert record["bpm"] == pytest.approx(RESTING_BPM, abs=3.0)
    else:
        assert record["rejected_by"] == "effective_rate_too_low"


def test_light_sample_loss_still_reads_correctly():
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
    """Writers key the row on this stamp; the block is held across ~40 ticks between recomputes."""
    for window in (_window(), _window(samples=0, fs=None, span=0.0)):
        stamp = _build(window)["ts"]
        parsed = datetime.fromisoformat(stamp)
        # `heart_signals.ts` is timestamptz; a naive stamp takes the database's zone.
        assert parsed.tzinfo is not None
