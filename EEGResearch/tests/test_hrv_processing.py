"""RMSSD derivation.

Behaviour, not accuracy. These pin the gates firing on the right windows and the
two-stage improvement doing what it claims; the accuracy of the values is settled
against ECG references in `test_hrv_against_dense_ecg.py` (30s) and
`test_optics_rmssd.py` (the 25s production path). Where a number is asserted
here, the tolerance is wide and the docstring says what it is standing in for.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.app.services.hrv_processing import (
    MAX_BEAT_COVERAGE,
    MIN_BEAT_COVERAGE,
    MIN_RATE_CONFIDENCE,
    _channels_needed,
    consensus_beats,
    detect_beats,
    estimate_hrv,
    rmssd_from_beats,
)
from src.app.services.ppg_processing import BANDPASS_ORDER, estimate_window
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
    # 30 beats, 29 intervals, 28 successive differences -- and the count returned
    # is the last of those, because it is the number of terms the mean of squares
    # is actually taken over and the number MIN_INTERVALS is meant to bound.
    assert n == 28


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


def test_a_missed_beat_is_removed_rather_than_averaged_in():
    """A missed beat merges two intervals, and RMSSD squares the difference, so
    one miss in a window dominates it. Unfiltered, this case measured 136ms
    against a true zero.

    The relative filter removes the merged interval outright, which is the only
    safe treatment: it cannot be repaired, because the beat time that would
    split it was never observed. The window loses an interval instead of
    reporting a fabricated one, and coverage is what notices the loss."""
    beats = [i * 0.5 for i in range(30)]
    clean, _ = rmssd_from_beats(beats)
    missing, n = rmssd_from_beats(beats[:15] + beats[16:])
    assert clean == pytest.approx(0.0, abs=1e-9)
    assert missing == pytest.approx(0.0, abs=1e-9)
    assert n < 28, "the merged interval should have been dropped"


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
    assert combined < single, f"consensus {combined:.1f} did not beat {single:.1f}"
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


def test_rmssd_is_stable_across_a_still_recording():
    """The test that would have caught the artefact filter being too loose.

    Every earlier claim about RMSSD here rested on the resting fixture, which is
    long enough for exactly ONE reportable window -- so a single lucky draw read
    as a validated result. The paired recording gives 42 windows over a still
    subject at a flat 69-72 bpm, where the true value cannot be swinging.

    Unfiltered, those 42 windows ranged 29-240ms. A spread that wide is not a
    physiological signal, and no gate in this module noticed: coverage sat at
    0.96-1.08 and rate confidence at 1.00 throughout.

    Asserted as a spread rather than a value, because the value is what needs an
    ECG to check and the spread is what makes any value meaningful."""
    data, fs = _load("optics_ecg_paired.jsonl.gz")
    window_s, values = 30, []
    for start in range(0, int(len(data) / fs) - window_s, 5):
        window = data[int(start * fs):int((start + window_s) * fs)]
        out = estimate_hrv(window, fs, *_rate(window, fs))
        if out.rmssd_ms is not None:
            values.append(out.rmssd_ms)

    assert len(values) > 30, f"expected many reportable windows, got {len(values)}"
    assert max(values) / min(values) < 3.0, (
        f"RMSSD swung {min(values):.0f}-{max(values):.0f}ms on a still subject; "
        "the true value is not moving, so this is the estimator"
    )
    assert all(v < 100.0 for v in values), (
        "a resting adult does not have an RMSSD near 100ms"
    )


# ── consensus needs channels to be a consensus ───────────────────────────────

def test_the_agreement_requirement_scales_with_the_channel_count():
    """A flat count would be 3-of-16 on the wide optics presets.

    The table is the contract: never one, never unanimity where a channel can be
    spared, and 3-of-4 preserved because that is the rung it was tuned on.
    """
    assert [_channels_needed(n) for n in (2, 3, 4, 8, 16)] == [2, 2, 3, 6, 12]


def test_one_live_channel_is_refused_rather_than_believed():
    """The gate that stops this module quietly becoming the thing it replaces.

    With a single populated channel, consensus is the identity on that channel's
    detections and the cross-channel averaging does nothing -- which is the
    29-246ms per-channel detector the module docstring exists to describe. Run
    against the six ECG-referenced windows one channel at a time it reported
    every one, never refusing, at up to +75% error.

    It cannot be caught downstream: `estimate_window`'s agreement term is 1.00
    by construction against a single waveform, so rate confidence can be 1.00 on
    exactly the window that least deserves it -- the same inapplicable-confidence
    trap CLAUDE.md records for camera rPPG.
    """
    window, fs = _window("optics_ecg_dense.jsonl.gz", 52, 77)
    bpm, confidence = _rate(window, fs)

    single = estimate_hrv(window[:, :1], fs, bpm, confidence)
    assert single.rmssd_ms is None
    assert single.beat_count == 0

    # And it is the channel count doing it, not the window: the same 25s of
    # signal with all four channels reports.
    assert estimate_hrv(window, fs, bpm, confidence).rmssd_ms is not None


def test_a_dead_channel_does_not_make_a_quorum():
    """`populated` counts channels with detections, not columns in the array, so
    padding a single live channel with silent ones must not buy agreement."""
    window, fs = _window("optics_ecg_dense.jsonl.gz", 52, 77)
    padded = np.hstack([window[:, :1], np.zeros((len(window), 3))])
    assert consensus_beats(padded, fs) == []


# ── coverage is bounded both ways ────────────────────────────────────────────

def test_more_beats_than_the_rate_accounts_for_is_refused():
    """The lower bound catches missed beats; nothing else catches invented ones.

    A double-detected dicrotic notch or an octave-low rate both land well above
    1.0 and every count-based statistic beneath them looks healthy, so without
    this they are indistinguishable from a clean window. Simulated by halving
    the rate the coverage is judged against, which is what an octave error does.
    """
    window, fs = _window("optics_ecg_dense.jsonl.gz", 52, 77)
    bpm, confidence = _rate(window, fs)

    out = estimate_hrv(window, fs, bpm / 2.0, confidence)
    assert out.rmssd_ms is None
    assert out.rejected_by == "excess_beats"
    assert out.coverage > MAX_BEAT_COVERAGE


def test_a_window_that_was_never_examined_has_no_coverage():
    """None, not 0.0. The rate-confidence gate returns before any beat is
    counted, so a 0.0 would reach `heart_signals.raw` as "0% of beats detected"
    for a window nobody looked at -- and `_raw` drops nulls but keeps zeros, so
    the distinction survives here or not at all."""
    window, fs = _window("optics_ecg_dense.jsonl.gz", 52, 77)

    for out in (estimate_hrv(window, fs, None, 1.0),
                estimate_hrv(window, fs, 70.0, MIN_RATE_CONFIDENCE - 0.01)):
        assert out.coverage is None
        assert out.beat_count == 0

    # Where beats *were* counted, a real number.
    assert estimate_hrv(window, fs, *_rate(window, fs)).coverage is not None


# ── the counts mean what they say ────────────────────────────────────────────

def test_intervals_stranded_between_artefacts_are_not_counted():
    """`interval_count` gates MIN_INTERVALS, so it has to be the number of terms
    the mean of squares is taken over. Isolated intervals contribute no
    successive difference; counting them would let six stranded pairs report
    twelve and clear a gate of ten on six real terms."""
    # Alternating good pair / artefact: every run is length 2, so each yields
    # exactly one difference.
    beats, t = [0.0], 0.0
    for _ in range(6):
        for step in (0.80, 0.81, 2.00):
            t += step
            beats.append(t)

    rmssd, n = rmssd_from_beats(beats)
    assert rmssd is not None
    assert n == 6, "expected one difference per surviving pair"


def test_a_window_too_short_for_the_filter_is_empty_not_an_exception():
    """`filtfilt` requires strictly more than its padlen, and the guard used to
    admit exactly that length. `ppg_processing` carries a comment saying this
    bound was already gotten wrong once."""
    padlen = 3 * (2 * BANDPASS_ORDER + 1)
    assert detect_beats(np.random.default_rng(0).normal(size=padlen), 64.0).size == 0
    # One more sample and it runs rather than raising.
    detect_beats(np.random.default_rng(0).normal(size=padlen + 1), 64.0)
