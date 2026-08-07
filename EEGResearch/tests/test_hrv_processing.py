"""RMSSD derivation.

Every physiological assertion here is provisional. There is no reference
measurement yet, so these tests pin *behaviour that was measured* -- the gates
firing on the right windows, the two-stage improvement doing what it claims --
and not correctness of the RMSSD values themselves. Where a number is asserted,
the tolerance is wide and the docstring says what it is standing in for.

When an ECG reference is recorded, the values below become checkable and the
tolerances should tighten. Until then a passing suite here means "unchanged",
not "right".
"""

from __future__ import annotations

import numpy as np
import pytest

from src.app.services.hrv_processing import (
    MIN_BEAT_COVERAGE,
    consensus_beats,
    detect_beats,
    estimate_hrv,
    rmssd_from_beats,
)
from src.app.services.ppg_processing import estimate_window
from test_ppg_processing import _load

PHYSIOLOGICAL_MS = (10.0, 120.0)


def _window(name: str, start_s: float, end_s: float):
    data, fs = _load(name)
    return data[int(start_s * fs):int(end_s * fs)], fs


def _rate(window, fs):
    est = estimate_window(window, fs)
    return est.bpm, est.confidence


# ── the arithmetic ───────────────────────────────────────────────────────────

def test_rmssd_of_a_perfectly_regular_beat_is_zero():
    beats = [i * 0.8 for i in range(30)]
    rmssd, n = rmssd_from_beats(beats)
    assert rmssd == pytest.approx(0.0, abs=1e-9)
    assert n == 29


def test_rmssd_is_successive_differences_not_spread():
    """Two sequences with identical interval distributions and different
    orderings must give different RMSSD -- otherwise the implementation is
    computing SDNN, which is a different metric that looks similar in tests
    built from random intervals."""
    alternating, drifting = [0.0], [0.0]
    for i in range(30):
        alternating.append(alternating[-1] + (0.7 if i % 2 else 0.9))
        drifting.append(drifting[-1] + (0.7 if i < 15 else 0.9))
    assert rmssd_from_beats(alternating)[0] > 5 * rmssd_from_beats(drifting)[0]


def test_a_missed_beat_inflates_rmssd():
    """The reason this module exists. Dropping one beat merges two intervals,
    and RMSSD squares the difference, so a single miss dominates a window.

    At 120 bpm, because that is where the merged interval stays physiologically
    possible. Below about 84 bpm a single miss produces an interval longer than
    MAX_IBI_MS and gets dropped as an artefact instead -- which is lucky rather
    than designed, and is exactly why coverage is gated separately: the range
    filter is not a substitute for detecting the beats in the first place."""
    beats = [i * 0.5 for i in range(30)]
    clean, _ = rmssd_from_beats(beats)
    missing, _ = rmssd_from_beats(beats[:15] + beats[16:])
    assert clean == pytest.approx(0.0, abs=1e-9)
    # sqrt of the mean over all 28 differences, so the single +500/-500 pair is
    # diluted rather than dominant -- still an order of magnitude above zero.
    assert missing > 100.0


def test_impossible_intervals_are_dropped_not_clamped():
    """A clamped interval is a fabricated measurement, and it enters the
    successive difference twice."""
    beats = [i * 0.8 for i in range(20)]
    beats.insert(10, beats[9] + 0.05)      # 50ms -- far below any real beat
    rmssd, n = rmssd_from_beats(beats)
    # The 50ms interval is dropped. Its partner -- the 750ms remainder of the
    # split beat -- is physiologically fine and stays, so a small residual is
    # expected and correct. What must NOT happen is the 500ms+ figure that
    # clamping would produce, or a difference taken across the dropped one.
    assert rmssd < 30.0
    assert n < 20, "the impossible interval should not have been counted"


def test_successive_differences_are_not_taken_across_a_dropped_interval():
    """Filtering the interval list and then diffing it pairs intervals that
    were never adjacent, fabricating a difference from the fact that something
    between them was removed. Here the two 800ms intervals surrounding a
    rejected one must not be compared to anything across the gap."""
    beats = [0.0, 0.8, 1.6, 1.65, 2.45, 3.25]     # 800, 800, 50, 800, 800
    rmssd, _ = rmssd_from_beats(beats)
    assert rmssd == pytest.approx(0.0, abs=1e-9)


# ── beat detection on real recordings ────────────────────────────────────────

def test_detects_about_the_right_number_of_beats():
    window, fs = _window("optics_rest_60s.jsonl.gz", 20, 50)
    bpm, _ = _rate(window, fs)
    expected = bpm / 60.0 * 30.0
    per_channel = [len(detect_beats(window[:, c], fs)) for c in range(4)]
    assert all(abs(n - expected) < 0.25 * expected for n in per_channel), (
        f"per-channel counts {per_channel} against ~{expected:.0f} expected"
    )


def test_consensus_beats_are_fewer_and_agreed():
    """Consensus removes detections; it cannot add one. A result larger than
    the busiest channel would mean the matching invented a beat."""
    window, fs = _window("optics_rest_60s.jsonl.gz", 20, 50)
    busiest = max(len(detect_beats(window[:, c], fs)) for c in range(4))
    agreed = consensus_beats(window, fs)
    assert 0 < len(agreed) <= busiest


def test_consensus_and_averaging_beat_a_single_channel():
    """The measured claim in the module docstring, pinned.

    A single channel gave 134-158ms on this window against a physiological
    20-50ms. Both steps together bring it into range. Asserted as an
    improvement rather than an exact figure, because the figure is not yet
    validated against anything."""
    window, fs = _window("optics_rest_60s.jsonl.gz", 20, 50)
    single, _ = rmssd_from_beats(list(detect_beats(window[:, 0], fs)))
    combined, _ = rmssd_from_beats(consensus_beats(window, fs))
    assert single > 100.0, "single-channel RMSSD was not the known-bad case"
    assert combined < single / 2
    assert PHYSIOLOGICAL_MS[0] < combined < PHYSIOLOGICAL_MS[1]


# ── the gates ────────────────────────────────────────────────────────────────

def test_reports_on_a_clean_resting_window():
    window, fs = _window("optics_rest_60s.jsonl.gz", 20, 50)
    out = estimate_hrv(window, fs, *_rate(window, fs))
    assert out.rejected_by is None
    assert PHYSIOLOGICAL_MS[0] < out.rmssd_ms < PHYSIOLOGICAL_MS[1]
    assert out.coverage >= MIN_BEAT_COVERAGE


def test_a_correct_rate_with_missing_beats_is_still_rejected():
    """The gate that is not redundant with the rate derivation.

    This window's rate is right (66.2 bpm) and its beats are not -- about 9%
    short, which yields 134ms. Rate quality and beat quality are independent,
    so RMSSD needs its own gate rather than inheriting the rate's verdict."""
    window, fs = _window("optics_recovery_150s.jsonl.gz", 100, 125)
    bpm, conf = _rate(window, fs)
    assert bpm == pytest.approx(66.0, abs=4.0), "the rate here should be fine"
    assert estimate_hrv(window, fs, bpm, conf).rmssd_ms is None


def test_motion_with_missing_beats_is_rejected_on_coverage():
    window, fs = _window("optics_through_exercise.jsonl.gz", 90, 115)
    out = estimate_hrv(window, fs, *_rate(window, fs))
    assert out.rejected_by == "coverage"


def test_no_rate_means_no_rmssd():
    window, fs = _window("optics_rest_60s.jsonl.gz", 20, 50)
    out = estimate_hrv(window, fs, None, 0.0)
    assert out.rmssd_ms is None and out.rejected_by == "no_rate"


def test_the_motion_window_produces_a_healthy_looking_wrong_answer():
    """A known defect, asserted deliberately so it cannot become a product.

    25s after exercise the rate derivation reports a confident, wrong 127 bpm.
    The beats are perfectly consistent with that wrong rate, so coverage is 0.98
    and RMSSD comes out at ~26ms -- the healthiest-looking output in the fixture
    set, computed against an oscillation that was not the heart.

    Nothing in this module can catch it; the beats really are that regular. It
    needs accelerometer gating upstream, which the bridge does not yet have.
    Until then RMSSD must not be recorded, exactly as the rate must not be.

    If this starts failing, check what changed before assuming it improved."""
    window, fs = _window("optics_recovery_150s.jsonl.gz", 0, 25)
    bpm, conf = _rate(window, fs)
    assert bpm == pytest.approx(127.0, abs=5.0), "the known-wrong rate moved"
    out = estimate_hrv(window, fs, bpm, conf)
    assert out.rejected_by is None, "no gate here catches motion -- by design"
    assert PHYSIOLOGICAL_MS[0] < out.rmssd_ms < PHYSIOLOGICAL_MS[1], (
        "the wrong answer looks entirely healthy, which is the point"
    )
