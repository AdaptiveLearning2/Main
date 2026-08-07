"""Heart-rate derivation: synthetic cases, then the real recordings.

Synthetic signals pin the arithmetic against a rate that is known exactly. The
fixtures pin behaviour against physiology, which no synthetic signal can — most
importantly the octave errors after motion, which are the failure this whole
design is arranged around.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from src.app.services.ppg_processing import (
    HeartRateTracker,
    estimate_channel,
    estimate_window,
    near_known_interferer,
)

FS = 64.3
FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> np.ndarray:
    with gzip.open(FIXTURES / name, "rt", encoding="utf-8") as fh:
        return np.array([json.loads(line)["ch"] for line in fh if line.strip()], dtype=float)


def _pulse(bpm: float, seconds: float = 25.0, fs: float = FS,
           harmonic: float = 0.0, noise: float = 0.08, drift: float = 0.0,
           seed: int = 0) -> np.ndarray:
    """A synthetic pulse with the nuisances the real signal carries.

    Noise is on by default because a noiseless sine is not a simpler version of
    this signal, it is a different one -- nothing the headband produces is
    clean, and an estimator tuned on a perfect sine is tuned on something it
    will never see. It is not required, though: test_a_clean_signal_is_
    reportable keeps the noiseless case covered, since that is where the
    high-rate defect showed up most sharply."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * fs)) / fs
    f = bpm / 60.0
    x = np.sin(2 * np.pi * f * t)
    if harmonic:
        # A real pulse waveform is not a sine; the second harmonic is what makes
        # a spectral argmax report double the rate.
        x += harmonic * np.sin(2 * np.pi * 2 * f * t)
    if drift:
        x += drift * np.sin(2 * np.pi * 0.2 * t)   # baseline wander
    if noise:
        x += noise * rng.standard_normal(len(t))
    return x


# ── synthetic: the arithmetic ────────────────────────────────────────────────

@pytest.mark.parametrize("bpm", [48.0, 60.0, 75.0, 100.0, 140.0])
def test_recovers_a_known_rate(bpm):
    est = estimate_channel(_pulse(bpm), FS)
    assert est.bpm == pytest.approx(bpm, abs=1.0)


def test_recovers_the_fundamental_not_the_harmonic():
    """The failure autocorrelation is chosen to avoid.

    With a strong second harmonic a spectral argmax reports 2x the rate. The
    first-peak rule returns the fundamental."""
    est = estimate_channel(_pulse(70.0, harmonic=1.4), FS)
    assert est.bpm == pytest.approx(70.0, abs=2.0)


def test_survives_baseline_drift():
    """Drift at 0.2Hz is larger than the pulse in the real recordings; without
    the bandpass an argmax returns the band edge instead of the rate."""
    est = estimate_channel(_pulse(72.0, drift=6.0), FS)
    assert est.bpm == pytest.approx(72.0, abs=2.0)


def test_flat_input_yields_nothing():
    est = estimate_channel(np.zeros(int(25 * FS)), FS)
    assert est.bpm is None and est.snr == 0.0


def test_noise_is_not_reported_as_a_rate():
    rng = np.random.default_rng(1)
    out = estimate_window(rng.standard_normal((int(25 * FS), 4)), FS)
    assert out.bpm is None
    assert "confidence" in out.reason


@pytest.mark.parametrize("bpm", [100.0, 115.0, 130.0, 150.0, 170.0])
def test_high_rates_are_reportable(bpm):
    """No fixture reaches these rates, and a regression here is silent.

    The fixtures top out near 92 bpm. A defect above that returns bpm=None with
    rejected_by="confidence", which downstream is indistinguishable from "no
    pulse detected" -- so the band this platform exists to observe, a child
    during and after exertion, would go quiet with nothing raising an error.

    This happened: an exclusion band narrower than the ACF peak it excluded let
    the peak's own shoulder count as an unrelated rival, collapsing confidence
    for correct estimates from about 100 bpm upward. estimate_channel was right
    throughout; only the fusion refused to report it. Hence a whole-pipeline
    assertion rather than a per-channel one."""
    channels = np.column_stack([_pulse(bpm, harmonic=0.4, seed=i) for i in range(4)])
    out = estimate_window(channels, FS)
    assert out.bpm is not None, (
        f"{bpm} bpm rejected: {out.reason}"
    )
    assert out.bpm == pytest.approx(bpm, abs=2.0)


@pytest.mark.parametrize("bpm", [72.0, 170.0])
def test_a_clean_signal_is_reportable(bpm):
    """Noise is on by default elsewhere, so without this the suite would never
    exercise the noiseless case -- which is where the high-rate defect above was
    sharpest."""
    channels = np.column_stack([_pulse(bpm, harmonic=0.4, noise=0.0)] * 4)
    out = estimate_window(channels, FS)
    assert out.bpm == pytest.approx(bpm, abs=2.0)


def test_one_bad_channel_does_not_move_the_answer():
    """Median across channels, so a single mis-seated emitter is outvoted."""
    good = [_pulse(72.0, seed=i) for i in range(3)]
    bad = _pulse(120.0, seed=9)
    out = estimate_window(np.column_stack(good + [bad]), FS)
    assert out.bpm == pytest.approx(72.0, abs=2.0)


def test_continuity_rejects_an_impossible_jump():
    """Exercised against a real window given an anchor it cannot have come from.

    A synthetic pulse would work too, but continuity is the rule that has to
    hold against the messy input, so it is tested against a recording rather
    than against a signal built to be estimable."""
    window = _clean_rest_window()
    out = estimate_window(window, FS, previous_bpm=150.0, seconds_since_previous=10.0)
    assert out.bpm is None
    assert out.rejected_by == "continuity"


def test_continuity_allows_a_plausible_change():
    window = _clean_rest_window()
    out = estimate_window(window, FS, previous_bpm=75.0, seconds_since_previous=10.0)
    assert out.bpm == pytest.approx(69.0, abs=3.0)


def test_continuity_does_not_widen_without_bound():
    """A long gap must expire the anchor, not make it infinitely permissive.

    Uncapped, a 60s dropout would allow 180 bpm of movement -- every octave
    error passes while the stale anchor is still treated as authoritative."""
    window = _clean_rest_window()
    out = estimate_window(window, FS, previous_bpm=150.0, seconds_since_previous=60.0)
    assert out.bpm is None, "a 60s gap should not licence an 80 bpm jump"


def test_the_known_interferer_is_recognised_but_not_banned():
    """44.5 bpm is a real rate for some people, so it is flagged rather than
    excluded from the search range."""
    assert near_known_interferer(44.5)
    assert not near_known_interferer(72.0)
    est = estimate_channel(_pulse(44.5), FS)
    assert est.bpm == pytest.approx(44.5, abs=1.5)


# ── real recordings: the physiology ──────────────────────────────────────────

def _clean_rest_window() -> np.ndarray:
    """A 25s window from the resting fixture, past its noisy opening."""
    data = _load("optics_rest_60s.jsonl.gz")
    return data[int(20 * FS):int(45 * FS)]


def _track(data: np.ndarray, window_s: float = 25.0, step_s: float = 10.0):
    tracker = HeartRateTracker()
    w, step = int(window_s * FS), int(step_s * FS)
    return [tracker.update(data[s:s + w], FS, step_s).bpm
            for s in range(0, len(data) - w, step)]


def test_resting_recording_reads_the_resting_rate():
    """Ground truth from the same session: 67.9 bpm by independent spectral
    analysis, and the wearer's watch agreed."""
    reported = [b for b in _track(_load("optics_rest_60s.jsonl.gz")) if b]
    assert reported, "expected at least one reportable window at rest"
    assert np.median(reported) == pytest.approx(68.0, abs=3.0)


def test_the_rate_is_higher_after_exertion():
    """The end-to-end check no synthetic signal can provide.

    Same headband, minutes apart, exercise in between. The wearer's watch
    recorded a peak of 97 bpm."""
    rest = [b for b in _track(_load("optics_rest_60s.jsonl.gz")) if b]
    recovery = [b for b in _track(_load("optics_recovery_150s.jsonl.gz")) if b]
    assert max(recovery) > max(rest) + 10, (
        f"exertion should raise the peak: rest max {max(rest):.1f}, "
        f"recovery max {max(recovery):.1f}"
    )


def test_recovery_decays_toward_the_resting_rate():
    reported = [b for b in _track(_load("optics_recovery_150s.jsonl.gz")) if b]
    assert reported[0] > reported[-1] + 15, f"expected a decay, got {reported}"
    assert reported[-1] == pytest.approx(68.0, abs=5.0)


def test_ambiguous_windows_after_motion_are_rejected():
    """The two windows the peak margin does catch.

    10-35s after exercise, the channels split 113/58/113/58 against a true rate
    near 90 -- two genuinely different periods nearly tied, which is what a low
    margin means."""
    data = _load("optics_recovery_150s.jsonl.gz")
    w = int(25 * FS)
    for start_s in (10, 20):
        out = estimate_window(data[int(start_s * FS):int(start_s * FS) + w], FS)
        assert out.bpm is None, (
            f"t={start_s}s should be rejected, got {out.bpm:.1f} bpm"
        )
        assert out.rejected_by == "confidence"


def test_the_first_window_after_motion_is_wrong_and_the_tracker_clears_it():
    """A documented limitation, pinned so it cannot be lost silently.

    The window starting the moment the wearer sat down reads ~127 bpm against a
    true ~90, on all four channels and with a healthy margin. Nothing in
    estimate_window separates it from a real 127 -- and a rule that did would
    also have to leave a genuine 127 reportable, which is the trap a previous
    revision fell into.

    The tracker is what contains it: 127 becomes the anchor, the next two
    windows are rejected as discontinuous, and it re-acquires."""
    data = _load("optics_recovery_150s.jsonl.gz")
    w, step = int(25 * FS), int(10 * FS)

    first = estimate_window(data[:w], FS)
    assert first.bpm == pytest.approx(127.0, abs=4.0), (
        "if this window now reads correctly, the limitation is fixed and this "
        "test should be replaced -- but check 120-180 bpm still reports first"
    )

    tracker = HeartRateTracker()
    seen = [tracker.update(data[s:s + w], FS, 10.0).bpm
            for s in range(0, 5 * step, step)]
    assert seen[0] == pytest.approx(127.0, abs=4.0)
    assert seen[1] is None and seen[2] is None, "should reject, not follow"
    assert seen[-1] == pytest.approx(83.0, abs=6.0), (
        f"should have re-acquired a plausible rate, got {seen}"
    )


def test_a_bad_first_window_does_not_poison_the_rest():
    """A tracker anchored to a wrong rate rejects every later correct one, so
    one bad window would otherwise cost the following minute. Repeated
    rejections are treated as evidence against the anchor."""
    reported = [b for b in _track(_load("optics_recovery_150s.jsonl.gz")) if b]
    assert len(reported) >= 8, f"expected most windows to report, got {len(reported)}"
