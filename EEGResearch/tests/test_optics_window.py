"""Tests for the optical window buffer in the TCP bridge adapter.

These check the time base. The bridge's `mono_ts_ms` records BLE delivery,
not sample time (see test_optics_fixture.py), so the window is placed on
`seq`, and the stamps are used only to measure an average rate. Every test
below covers a way this could go wrong quietly -- a rate that's plausible
and wrong rather than an outright error.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from src.app.services.eeg_ingestion import TcpMuseBridgeAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "optics_rest_64hz.jsonl.gz"


def _adapter() -> TcpMuseBridgeAdapter:
    # Never connected: `_store_optics` is what the reader thread calls, and the
    # buffer is deliberately reachable without a socket.
    return TcpMuseBridgeAdapter("127.0.0.1", 8765, 1)


def _feed(adapter, samples, *, start_seq=0, start_ms=0.0, interval_ms=1000 / 64):
    for i, values in enumerate(samples):
        adapter._store_optics({
            "kind": "optics",
            "seq": start_seq + i,
            "mono_ts_ms": start_ms + i * interval_ms,
            "n": len(values),
            "ch": list(values),
        })


def test_window_is_empty_before_anything_arrives():
    w = _adapter().optics_window(25.0)
    assert len(w.channels) == 0
    assert w.fs is None
    assert w.largest_gap_seconds is None


def test_rate_is_measured_from_the_stamps_not_assumed():
    a = _adapter()
    # 32Hz, not the preset's nominal 64. A window that assumed the nominal rate
    # would report every bpm at double the truth, confidently.
    _feed(a, [(1.0, 2.0)] * 640, interval_ms=1000 / 32)
    w = a.optics_window(25.0)
    assert w.fs == pytest.approx(32.0, abs=0.2)
    assert w.channel_count == 2


def test_rate_counts_dropped_samples_rather_than_rows():
    """With a tenth of the samples missing, `(len(rows) - 1) / span` would
    report 57.6Hz for a link still running at 64, silently making every
    derived rate 10% low. `seq` counts what was sent, so the measured rate
    stays put and the loss shows up as a gap instead.
    """
    a = _adapter()
    for i in range(640):
        if i % 10 == 3:
            continue
        a._store_optics({"seq": i, "mono_ts_ms": i * (1000 / 64), "n": 1, "ch": [1.0]})
    w = a.optics_window(25.0)
    assert w.fs == pytest.approx(64.0, abs=0.5)
    # Two intervals where one sample went missing, at ~64Hz.
    assert w.largest_gap_seconds == pytest.approx(2 / 64, abs=0.002)


def test_gaps_are_interpolated_onto_the_sample_grid():
    a = _adapter()
    a._store_optics({"seq": 0, "mono_ts_ms": 0.0, "n": 1, "ch": [0.0]})
    a._store_optics({"seq": 4, "mono_ts_ms": 4 * (1000 / 64), "n": 1, "ch": [4.0]})
    w = a.optics_window(float("inf"))
    # Five grid points for a span of four sample indices, filled linearly --
    # the samples are placed where they were taken, not packed together.
    assert [round(float(v), 6) for v in w.channels[:, 0]] == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_a_seq_reset_drops_the_earlier_recording():
    """seq is a per-bridge-run counter, so it going backwards means a restart.

    Keeping both halves would splice two recordings onto one reconstructed
    clock, inventing a gap the size of however long the bridge was down.
    """
    a = _adapter()
    _feed(a, [(1.0,)] * 100, start_seq=5000, start_ms=0.0)
    _feed(a, [(2.0,)] * 100, start_seq=0, start_ms=60_000.0)
    w = a.optics_window(float("inf"))
    assert len(w.channels) == 100
    assert all(v == 2.0 for v in w.channels[:, 0])


def test_a_channel_count_change_ends_the_window():
    """A preset change makes the samples either side different measurements."""
    a = _adapter()
    _feed(a, [(1.0, 1.0, 1.0, 1.0)] * 50, start_seq=0, start_ms=0.0)
    _feed(a, [(2.0, 2.0)] * 30, start_seq=50, start_ms=50 * (1000 / 64))
    w = a.optics_window(float("inf"))
    assert w.channel_count == 2
    assert len(w.channels) == 30


def test_samples_older_than_the_window_are_left_out():
    a = _adapter()
    _feed(a, [(1.0,)] * 640, start_seq=0, start_ms=0.0)         # 10s
    _feed(a, [(2.0,)] * 384, start_seq=640, start_ms=10_000.0)  # 6s more
    w = a.optics_window(5.0)
    assert w.span_seconds == pytest.approx(5.0, abs=0.1)
    assert all(v == 2.0 for v in w.channels[:, 0])


def test_a_sample_with_a_null_channel_is_dropped_whole():
    """The bridge writes JSON null for a non-finite reading.

    Half a sample cannot be placed on the grid, and keeping the readable half
    beside a zero would store a measurement nothing measured. The sample goes,
    and the grid position it left is filled from its neighbours like any other
    loss -- so the trace stays continuous rather than acquiring a spike.
    """
    a = _adapter()
    _feed(a, [(1.0, 1.0)] * 10, start_seq=0, start_ms=0.0)
    a._store_optics({"seq": 10, "mono_ts_ms": 10 * (1000 / 64), "n": 2,
                     "ch": [99.0, None]})
    _feed(a, [(1.0, 1.0)] * 10, start_seq=11, start_ms=11 * (1000 / 64))
    w = a.optics_window(float("inf"))
    assert len(w.channels) == 21
    assert float(w.channels[10, 0]) == pytest.approx(1.0)


def test_a_disconnect_clears_the_buffer():
    a = _adapter()
    _feed(a, [(1.0,)] * 100)
    a.disconnect()
    assert len(a.optics_window(float("inf")).channels) == 0


def test_the_buffer_holds_more_than_a_window_and_no_more():
    a = _adapter()
    _feed(a, [(1.0,)] * (TcpMuseBridgeAdapter.OPTICS_BUFFER_MAXLEN + 500))
    w = a.optics_window(float("inf"))
    assert len(w.channels) == TcpMuseBridgeAdapter.OPTICS_BUFFER_MAXLEN
    # Comfortably more than the 25s a rate is derived over, at ~64Hz.
    assert w.span_seconds > 30.0


@pytest.fixture(scope="module")
def recorded_frames() -> list[dict]:
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_a_real_recording_measures_its_own_rate(recorded_frames):
    """End to end against the hardware capture, not synthesised timing.

    The stamps in this file are batched -- ~9% of samples share one with their
    predecessor -- which is exactly the case a median-of-intervals rate reports
    as 0 and this span-based one gets right.
    """
    a = _adapter()
    for frame in recorded_frames:
        a._store_optics(frame)
    w = a.optics_window(25.0)
    assert w.fs == pytest.approx(64.234, abs=0.5)
    assert w.channel_count == 4
    assert w.span_seconds == pytest.approx(25.0, abs=0.2)
    assert len(w.channels) == pytest.approx(25 * 64, abs=20)
