"""RMSSD as it actually reaches `heart_signals`, through `build_heart_record`.

`test_hrv_against_dense_ecg.py` validates the derivation against six simultaneous
watch ECGs over **30s** windows. This file covers the two things that file cannot:
that the production path derives over the **25s** the rate estimator was
validated on and is still worth recording there, and that RMSSD is an enrichment
-- it may be absent from a perfectly good reading and may never take one away.

The 25s numbers are measured here rather than carried across, because they are
not the same numbers. Same six windows, same references:

    window   ECG      30s       25s
    t=52     41.4ms   35.2ms    37.1ms
    t=100    48.9ms   43.0ms    42.5ms
    t=150    34.2ms   31.9ms    33.8ms
    t=199    41.5ms   46.0ms    32.8ms
    t=250    42.6ms   rejected  39.9ms
    t=299    29.2ms   32.6ms    33.4ms

    30s: n=5  r=0.75  bias -1.3ms  rms 4.7ms  worst 15%
    25s: n=6  r=0.78  bias -3.1ms  rms 5.2ms  worst 21%

Correlation holds and one more window reports; bias and the worst case both grow.
That is the trade for sharing the rate's window, and sharing it is not optional --
`estimate_hrv` takes the rate rather than re-deriving it, so that the two cannot
disagree about whether a window is usable.
"""

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
    """`samples` as a healthy link delivers them: nothing lost, no long gap.

    **By keyword, and every field named.** This file was written against a
    five-field `OpticsWindow` and merged against a seven-field one -- #83 added
    `received_rate_hz` and `completeness` as fields 3 and 4 while this branch was
    open, so the five positional arguments here silently became `channels`,
    `fs`, `received_rate_hz`, `completeness`, `span_seconds`. It failed loudly
    only because the two fields left over had no defaults; had they, this file
    would have gone green describing a 25Hz link at 2% completeness.
    `unusable_reason` *does* have a default, so the next field inserted into the
    middle of this record gets exactly that outcome. Naming the fields is what
    turns that from a wrong number into an error.
    """
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
    """One block, built the way a tick builds it: the production window length,
    the production step, and the tracker the rate derivation needs for
    continuity."""
    samples = data[int(offset_s * fs):int((offset_s + RATE_WINDOW_SECONDS) * fs)]
    return build_heart_record(_window(samples, fs), tracker or HeartRateTracker(),
                              EMIT_EVERY_SECONDS)


@pytest.fixture(scope="module")
def dense():
    return _load("optics_ecg_dense.jsonl.gz")


@pytest.fixture(scope="module")
def paired(dense):
    """The six ECG-referenced windows, as (reference RMSSD, built record).

    One tracker across all six, in offset order, because that is what production
    does: `HeartRateTracker` carries the previous window's bpm and its continuity
    rule can refuse a window that would pass in isolation. Estimating each with a
    fresh tracker would test a path the sidecar never takes.
    """
    data, fs = dense
    tracker = HeartRateTracker()
    return [(rmssd_from_beats(_ecg_beats(_load_ecg(name)))[0],
             _record(data, fs, offset, tracker))
            for name, offset in sorted(PAIRS, key=lambda p: p[1])]


def test_the_production_window_still_tracks_the_reference(paired):
    """Correlation, not tolerance. A blanket tolerance alone would pass an
    estimator that always returned 39ms, which is the middle of the range and
    within 25% of four of these six."""
    refs = [ref for ref, rec in paired if rec["rmssd_ms"] is not None]
    got = [rec["rmssd_ms"] for _, rec in paired if rec["rmssd_ms"] is not None]
    assert len(got) >= 5, f"expected most windows to report, got {len(got)}"
    r = float(np.corrcoef(refs, got)[0, 1])
    assert r > 0.5, f"correlation {r:.2f} -- not tracking the reference"


def test_the_production_window_is_within_a_quarter_of_the_reference(paired):
    """Looser than the 30s file's 20%, and deliberately not silently so.

    The worst window is 21% at 25s against 15% at 30s -- the shorter window has
    fewer beats to average, and this is the cost of it. If this starts failing,
    check for a real regression before loosening it again."""
    errors = [abs(rec["rmssd_ms"] - ref) / ref
              for ref, rec in paired if rec["rmssd_ms"] is not None]
    assert max(errors) < 0.25, f"errors {[f'{e:.0%}' for e in errors]}"


def test_the_bias_stays_small_relative_to_the_scatter(paired):
    """-3.1ms against an RMS of 5.2ms. Still scatter rather than a scale factor,
    so there is no calibration constant to remove -- but it is twice the 30s
    bias, so anything proposing one should be checked at both lengths."""
    diffs = [rec["rmssd_ms"] - ref
             for ref, rec in paired if rec["rmssd_ms"] is not None]
    bias = float(np.mean(diffs))
    rms = float(np.sqrt(np.mean(np.square(diffs))))
    assert abs(bias) < 0.8 * rms, f"bias {bias:.1f}ms vs rms {rms:.1f}ms"


def test_a_refused_rmssd_never_costs_the_heart_rate(dense):
    """The enrichment property, over a whole real recording.

    `stress_score` is defined on heart rate alone and RMSSD is added when
    available. Roughly one window in five is gated out here even seated and at
    rest, so a refusal that also dropped the rate -- or that named itself in
    `rejected_by` -- would take the primary measurement down with the optional
    one, exactly the coupling the definition exists to prevent.
    """
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
        # ...and the two refusals are never confused for one another.
        assert record["rmssd_rejected_by"]

    assert rated > 20, f"expected a usable recording, got {rated} rated windows"
    assert enriched, "no window produced RMSSD at all"
    assert rated > enriched, "no window exercised the refusal path"
    # Named causes, so a null column can be explained rather than guessed at.
    assert refusals <= {"rate_confidence", "coverage", "excess_beats",
                        "too_few_intervals", "no_rate", "no_rmssd"}, refusals


def test_a_refused_rate_is_not_enriched(dense):
    """RMSSD is attempted only after the rate is accepted.

    Not an optimisation. On the window after exercise the beats are perfectly
    consistent with a confidently wrong 127 bpm and yield a healthy-looking
    25.6ms, so a block that carried RMSSD without a rate would be the most
    plausible-looking output in the fixture set and would mean nothing.
    """
    _, fs = dense
    flat = np.zeros((int(RATE_WINDOW_SECONDS * fs), 4))
    record = build_heart_record(
        _window(flat, fs), HeartRateTracker(), EMIT_EVERY_SECONDS)
    assert record["bpm"] is None
    assert record["rejected_by"] is not None
    assert record["rmssd_ms"] is None
    # Not even the diagnostics: nothing was counted, so there is nothing to say
    # beyond the rate's own refusal.
    assert record["rmssd_rejected_by"] is None
    assert record["beat_coverage"] is None


def test_a_window_no_beat_was_counted_in_has_no_beat_coverage(dense):
    """Null, never 0.0, and this is the write path so it is the one that counts.

    RMSSD's confidence gate is 0.9 against the rate's 0.55, so confidence in
    between is routine -- 2 of 46 windows on this recording. `_raw` drops nulls
    and keeps zeros, so a 0.0 here reaches the database indistinguishable from a
    window where every beat really was missed, which is the same
    can't-tell-no-data-from-zero failure the mappers exist to prevent.
    """
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
    """One shape, so a consumer never reads an absent field as a third state --
    the same rule `trusted` and `rejected_by` already follow."""
    data, fs = dense
    # Not `_window`: nothing arrived, so every measurement of the time base is
    # None rather than a number. Same shape `eeg_ingestion` builds for an empty
    # buffer, and by keyword for the reason given above.
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
