"""Tests for RMSSD derivation.

These check behaviour, not accuracy: that the gates fire on the right windows
and the two-stage improvement works. Accuracy against ECG references is
checked in `test_hrv_against_dense_ecg.py` (30s window) and
`test_optics_rmssd.py` (25s production window).
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
    # 30 beats give 29 intervals and 28 successive differences; n is that count.
    assert n == 28


def test_rmssd_is_successive_differences_not_spread():
    """Two sequences with the same interval distribution but different
    orderings must give different RMSSD. If not, this is computing SDNN
    instead, a different metric that can look the same on random intervals."""
    alternating, drifting = [0.0], [0.0]
    for i in range(30):
        alternating.append(alternating[-1] + (0.7 if i % 2 else 0.9))
        drifting.append(drifting[-1] + (0.7 if i < 15 else 0.9))
    assert rmssd_from_beats(alternating)[0] > 5 * rmssd_from_beats(drifting)[0]


def test_a_missed_beat_is_removed_rather_than_averaged_in():
    """A missed beat merges two intervals, and since RMSSD squares the
    difference, one miss dominates the window. Unfiltered this measured 136ms
    against a true zero. The filter drops the merged interval instead of
    trying to repair it, since the missing beat time can't be recovered."""
    beats = [i * 0.5 for i in range(30)]
    clean, _ = rmssd_from_beats(beats)
    missing, n = rmssd_from_beats(beats[:15] + beats[16:])
    assert clean == pytest.approx(0.0, abs=1e-9)
    assert missing == pytest.approx(0.0, abs=1e-9)
    assert n < 28, "the merged interval should have been dropped"


def test_impossible_intervals_are_dropped_not_clamped():
    """A clamped interval would be a fabricated measurement, counted twice in
    the successive difference."""
    beats = [i * 0.8 for i in range(20)]
    beats.insert(10, beats[9] + 0.05)      # 50ms -- far below any real beat
    rmssd, n = rmssd_from_beats(beats)
    # The 50ms interval is dropped; its 750ms remainder partner is fine and
    # stays, so a small residual is expected. Must not see the 500ms+ figure
    # clamping would produce.
    assert rmssd < 30.0
    assert n < 20, "the impossible interval should not have been counted"


def test_successive_differences_are_not_taken_across_a_dropped_interval():
    """Filtering the interval list and then diffing it would pair intervals
    that were never adjacent. The two 800ms intervals around a rejected one
    must not be compared across the gap."""
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
    """Consensus can only remove detections, never add one. A result larger
    than the busiest channel would mean the matching invented a beat."""
    window, fs = _window("optics_rest_60s.jsonl.gz", 20, 50)
    busiest = max(len(detect_beats(window[:, c], fs)) for c in range(4))
    agreed = consensus_beats(window, fs)
    assert 0 < len(agreed) <= busiest


def test_consensus_and_averaging_beat_a_single_channel():
    """A single channel gave 134-158ms on this window against a physiological
    20-50ms. Consensus plus averaging brings it into range. Checked as an
    improvement rather than an exact figure, since the figure isn't validated
    against a reference yet."""
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
    """This window's rate is right (66.2 bpm) but its beats are about 9%
    short, giving 134ms. Rate quality and beat quality are independent, so
    RMSSD needs its own gate instead of inheriting the rate's verdict."""
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
    """A known defect, pinned deliberately so it can't ship silently.

    25s after exercise the rate derivation reports a confident but wrong 127
    bpm. The beats are perfectly consistent with that wrong rate, so coverage
    is 0.98 and RMSSD comes out at ~26ms -- the healthiest-looking output in
    the fixture set, computed against motion, not the heart.

    Nothing in this module catches it; the beats really are that regular. It
    needs accelerometer gating upstream, which doesn't exist yet, so RMSSD
    must not be recorded here, just like the rate.

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
    """Catches the artefact filter being too loose.

    The resting fixture alone is only long enough for one reportable window,
    so a single lucky draw could look like a validated result. This paired
    recording gives 42 windows over a still subject at a flat 69-72 bpm, where
    the true value can't be swinging.

    Unfiltered, those 42 windows ranged 29-240ms -- too wide to be
    physiological, and no gate in this module caught it: coverage sat at
    0.96-1.08 and rate confidence at 1.00 throughout.

    Checked as a spread rather than an exact value, since the value needs an
    ECG reference and the spread is what makes any value meaningful."""
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
    """A flat count would mean 3-of-16 on the wide optics presets. The table
    never requires just one channel or full unanimity, and keeps 3-of-4 for
    the rung it was tuned on."""
    assert [_channels_needed(n) for n in (2, 3, 4, 8, 16)] == [2, 2, 3, 6, 12]


def test_one_live_channel_is_refused_rather_than_believed():
    """With a single populated channel, consensus is just that channel's own
    detections, and cross-channel averaging does nothing -- back to a raw
    29-246ms per-channel detector. Run against the six ECG-referenced windows
    one channel at a time, it reported every one, never refusing, at up to
    +75% error.

    This can't be caught downstream: `estimate_window`'s agreement term is
    1.00 by construction against a single waveform, so rate confidence reads
    1.00 on exactly the window that least deserves it.
    """
    window, fs = _window("optics_ecg_dense.jsonl.gz", 52, 77)
    bpm, confidence = _rate(window, fs)

    single = estimate_hrv(window[:, :1], fs, bpm, confidence)
    assert single.rmssd_ms is None
    assert single.beat_count == 0

    # Confirms it's the channel count, not the window: same 25s of signal
    # with all four channels reports fine.
    assert estimate_hrv(window, fs, bpm, confidence).rmssd_ms is not None


def test_a_dead_channel_does_not_make_a_quorum():
    """`populated` counts channels with detections, not array columns, so
    padding a live channel with silent ones must not buy agreement."""
    window, fs = _window("optics_ecg_dense.jsonl.gz", 52, 77)
    padded = np.hstack([window[:, :1], np.zeros((len(window), 3))])
    assert consensus_beats(padded, fs) == []


# ── coverage is bounded both ways ────────────────────────────────────────────

def test_more_beats_than_the_rate_accounts_for_is_refused():
    """The lower coverage bound catches missed beats; nothing else catches
    invented ones. A double-detected dicrotic notch or an octave-low rate both
    land well above 1.0 and look healthy on every other statistic. Simulated
    here by halving the rate coverage is judged against, which is what an
    octave error does.
    """
    window, fs = _window("optics_ecg_dense.jsonl.gz", 52, 77)
    bpm, confidence = _rate(window, fs)

    out = estimate_hrv(window, fs, bpm / 2.0, confidence)
    assert out.rmssd_ms is None
    assert out.rejected_by == "excess_beats"
    assert out.coverage > MAX_BEAT_COVERAGE


def test_a_window_that_was_never_examined_has_no_coverage():
    """Coverage should be None, not 0.0, when the rate-confidence gate returns
    before any beat is counted. A 0.0 would read as "0% of beats detected" for
    a window nobody actually looked at, and `_raw` drops nulls but keeps
    zeros, so this is where the distinction has to hold."""
    window, fs = _window("optics_ecg_dense.jsonl.gz", 52, 77)

    for out in (estimate_hrv(window, fs, None, 1.0),
                estimate_hrv(window, fs, 70.0, MIN_RATE_CONFIDENCE - 0.01)):
        assert out.coverage is None
        assert out.beat_count == 0

    # Where beats were counted, coverage is a real number.
    assert estimate_hrv(window, fs, *_rate(window, fs)).coverage is not None


# ── the counts mean what they say ────────────────────────────────────────────

def test_intervals_stranded_between_artefacts_are_not_counted():
    """`interval_count` gates MIN_INTERVALS, so it must count the terms the
    mean of squares is actually taken over. An isolated interval contributes
    no successive difference, so counting it would let six stranded pairs
    report twelve and pass a gate of ten on only six real terms."""
    # Alternating good pair / artefact: each run is length 2, so each yields
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
    """`filtfilt` requires strictly more than its padlen; the guard must
    reject exactly that length rather than let it through."""
    padlen = 3 * (2 * BANDPASS_ORDER + 1)
    assert detect_beats(np.random.default_rng(0).normal(size=padlen), 64.0).size == 0
    # One more sample and it runs instead of raising.
    detect_beats(np.random.default_rng(0).normal(size=padlen + 1), 64.0)
