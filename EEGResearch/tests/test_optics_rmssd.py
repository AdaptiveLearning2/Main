"""RMSSD through `build_heart_record` on the 25 s production window: still worth recording, and only ever an enrichment."""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pytest

from src.app.services.eeg_ingestion import OpticsWindow
from src.app.services.optics_processing import (
    EMIT_EVERY_SECONDS,
    RATE_WINDOW_SECONDS,
    build_heart_record,
)
from src.app.services.ppg_processing import HeartRateTracker
from test_hrv_against_dense_ecg import PAIRS, _ecg_beats, _load_ecg
from src.app.services.hrv_processing import rmssd_from_beats
from test_ppg_processing import _load

FIXTURES = Path(__file__).parent / "fixtures"


def _window(samples, fs, span_seconds=RATE_WINDOW_SECONDS):
    """`samples` as a healthy link delivers them; by keyword, so an inserted field errors rather than shifts."""
    return OpticsWindow(
        channels=samples,
        fs=fs,
        received_rate_hz=fs,
        completeness=1.0,
        span_seconds=span_seconds,
        largest_gap_seconds=0.02,
        channel_count=samples.shape[1],
    )


def _record(data, fs, offset_s, tracker=None):
    """Builds one block the way a tick does."""
    samples = data[int(offset_s * fs):int((offset_s + RATE_WINDOW_SECONDS) * fs)]
    return build_heart_record(_window(samples, fs), tracker or HeartRateTracker(),
                              EMIT_EVERY_SECONDS)


@pytest.fixture(scope="module")
def dense():
    return _load("optics_ecg_dense.jsonl.gz")


@pytest.fixture(scope="module")
def paired(dense):
    """(reference RMSSD, record) for the six windows, through one tracker in offset order as in production."""
    data, fs = dense
    tracker = HeartRateTracker()
    return [(rmssd_from_beats(_ecg_beats(_load_ecg(name)))[0],
             _record(data, fs, offset, tracker))
            for name, offset in sorted(PAIRS, key=lambda p: p[1])]


def test_the_production_window_still_tracks_the_reference(paired):
    """A tolerance alone would pass a constant 39 ms."""
    refs = [ref for ref, rec in paired if rec["rmssd_ms"] is not None]
    got = [rec["rmssd_ms"] for _, rec in paired if rec["rmssd_ms"] is not None]
    assert len(got) >= 5, f"expected most windows to report, got {len(got)}"
    r = float(np.corrcoef(refs, got)[0, 1])
    assert r > 0.5, f"correlation {r:.2f} -- not tracking the reference"


def test_the_production_window_is_within_a_quarter_of_the_reference(paired):
    """Looser than the 30 s file's 20%: fewer beats to average. Check for a regression before loosening."""
    errors = [abs(rec["rmssd_ms"] - ref) / ref
              for ref, rec in paired if rec["rmssd_ms"] is not None]
    assert max(errors) < 0.25, f"errors {[f'{e:.0%}' for e in errors]}"


def test_the_bias_stays_small_relative_to_the_scatter(paired):
    """Scatter, not a scale factor; any calibration must be checked at both 25 s and 30 s."""
    diffs = [rec["rmssd_ms"] - ref
             for ref, rec in paired if rec["rmssd_ms"] is not None]
    bias = float(np.mean(diffs))
    rms = float(np.sqrt(np.mean(np.square(diffs))))
    assert abs(bias) < 0.8 * rms, f"bias {bias:.1f}ms vs rms {rms:.1f}ms"


def test_a_refused_rmssd_never_costs_the_heart_rate(dense):
    """`stress_score` is defined on heart rate alone; RMSSD is optional."""
    data, fs = dense
    tracker = HeartRateTracker()
    rated, enriched, refusals = 0, 0, set()
    for start in range(0, int(len(data) / fs - RATE_WINDOW_SECONDS),
                       int(EMIT_EVERY_SECONDS)):
        record = _record(data, fs, start, tracker)
        if record["bpm"] is None:
            continue
        rated += 1
        if record["rmssd_ms"] is not None:
            enriched += 1
            continue
        refusals.add(record["rmssd_rejected_by"])
        # The rate survives its own gates untouched...
        assert record["trusted"] is True
        assert record["rejected_by"] is None
        # ...and the two refusal fields are never confused.
        assert record["rmssd_rejected_by"]

    assert rated > 20, f"expected a usable recording, got {rated} rated windows"
    assert enriched, "no window produced RMSSD at all"
    assert rated > enriched, "no window exercised the refusal path"
    # Named causes so a null column can be explained, not guessed at.
    assert refusals <= {"rate_confidence", "coverage", "excess_beats",
                        "too_few_intervals", "no_rate", "no_rmssd"}, refusals


def test_a_refused_rate_is_not_enriched(dense):
    """Beats consistent with a wrong rate yield a healthy-looking RMSSD that means nothing."""
    _, fs = dense
    flat = np.zeros((int(RATE_WINDOW_SECONDS * fs), 4))
    record = build_heart_record(
        _window(flat, fs), HeartRateTracker(), EMIT_EVERY_SECONDS)
    assert record["bpm"] is None
    assert record["rejected_by"] is not None
    assert record["rmssd_ms"] is None
    # Nothing was counted, so no RMSSD diagnostics either.
    assert record["rmssd_rejected_by"] is None
    assert record["beat_coverage"] is None


def test_a_window_no_beat_was_counted_in_has_no_beat_coverage(dense):
    """Null, never 0.0, on the write path: `_raw` drops nulls but keeps zeros."""
    data, fs = dense
    tracker = HeartRateTracker()
    seen = 0
    for start in range(0, int(len(data) / fs - RATE_WINDOW_SECONDS),
                       int(EMIT_EVERY_SECONDS)):
        record = _record(data, fs, start, tracker)
        if record["rmssd_rejected_by"] != "rate_confidence":
            continue
        seen += 1
        assert record["beat_coverage"] is None

    assert seen, "no window exercised the rate-confidence path"


def test_every_block_carries_the_rmssd_fields(dense):
    """One shape, so an absent field is never read as a third state."""
    data, fs = dense
    # Not `_window`: an empty buffer has None time-base measurements, as `eeg_ingestion` builds it.
    empty = OpticsWindow(
        channels=np.empty((0, 4)),
        fs=None,
        received_rate_hz=None,
        completeness=None,
        span_seconds=0.0,
        largest_gap_seconds=None,
        channel_count=4,
    )
    for record in (_record(data, fs, 52),
                   build_heart_record(empty, HeartRateTracker(), EMIT_EVERY_SECONDS)):
        assert set(record) >= {"rmssd_ms", "beat_coverage", "rmssd_rejected_by"}
