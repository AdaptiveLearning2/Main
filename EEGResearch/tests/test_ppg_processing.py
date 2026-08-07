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
           harmonic: float = 0.0, noise: float = 0.0, drift: float = 0.0,
           seed: int = 0) -> np.ndarray:
    """A synthetic pulse with the nuisances the real signal carries."""
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


def test_one_bad_channel_does_not_move_the_answer():
    """Median across channels, so a single mis-seated emitter is outvoted."""
    good = [_pulse(72.0, seed=i) for i in range(3)]
    bad = _pulse(120.0, seed=9)
    out = estimate_window(np.column_stack(good + [bad]), FS)
    assert out.bpm == pytest.approx(72.0, abs=2.0)


def test_continuity_rejects_an_impossible_jump():
    out = estimate_window(np.column_stack([_pulse(150.0)] * 4), FS,
                          previous_bpm=70.0, seconds_since_previous=10.0)
    assert out.bpm is None
    assert "moved more than" in out.reason


def test_continuity_allows_a_plausible_change():
    out = estimate_window(np.column_stack([_pulse(85.0)] * 4), FS,
                          previous_bpm=70.0, seconds_since_previous=10.0)
    assert out.bpm == pytest.approx(85.0, abs=2.0)


def test_the_known_interferer_is_recognised_but_not_banned():
    """44.5 bpm is a real rate for some people, so it is flagged rather than
    excluded from the search range."""
    assert near_known_interferer(44.5)
    assert not near_known_interferer(72.0)
    est = estimate_channel(_pulse(44.5), FS)
    assert est.bpm == pytest.approx(44.5, abs=1.5)


# ── real recordings: the physiology ──────────────────────────────────────────

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


def test_the_window_right_after_motion_is_wrong_and_known_to_be():
    """Pins a limitation, not a success.

    The 25s after exercise yields ~127 bpm against a true rate near 90 --
    unanimous across channels, with a decisive enough peak to look real. The
    estimator does not catch it, and two candidate discriminators were tried and
    rejected (see the module docstring).

    Asserted so the limitation is visible and so that an implementation which
    *does* fix it fails here loudly rather than passing quietly."""
    data = _load("optics_recovery_150s.jsonl.gz")
    first = estimate_window(data[:int(25 * FS)], FS)
    assert first.bpm is not None and first.bpm > 110, (
        f"expected the known octave error, got {first.bpm} -- if this now reads "
        f"~90 the estimator improved and this test should be inverted"
    )


def test_a_wrong_post_motion_window_does_not_become_the_anchor():
    """What actually limits the damage.

    The estimator cannot reject that window, so the tracker must not build on
    it: an anchor is only adopted from a window clearing a higher bar than mere
    reportability."""
    data = _load("optics_recovery_150s.jsonl.gz")
    tracker = HeartRateTracker()
    w = int(25 * FS)
    first = tracker.update(data[:w], FS, 0.0)
    assert first.bpm is not None and first.bpm > 110
    assert tracker.bpm is None, "a low-confidence window must not become the anchor"


def test_a_bad_first_window_does_not_poison_the_rest():
    """A tracker anchored to a wrong rate rejects every later correct one, so
    one bad window would otherwise cost the following minute. Repeated
    rejections are treated as evidence against the anchor."""
    reported = [b for b in _track(_load("optics_recovery_150s.jsonl.gz")) if b]
    assert len(reported) >= 8, f"expected most windows to report, got {len(reported)}"
