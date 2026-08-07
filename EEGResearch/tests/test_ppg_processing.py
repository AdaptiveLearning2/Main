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

    Noise is on by default, and that is not incidental. A pure sine has equal
    autocorrelation peaks at every multiple of its period, so no period is ever
    decisive and the margin check -- which is what rejects octave errors on real
    data -- scores it near zero. Real optical traces have decaying peaks. A
    noiseless sine is not a simpler version of the signal, it is a different
    one, and testing against it would have meant tuning the estimator to
    something it will never see."""
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
    """Exercised against a real window, not a synthetic one.

    A sustained synthetic sinusoid cannot reach the confidence threshold, and
    that is correct rather than a gap: its autocorrelation has near-equal peaks
    at every multiple of the period, so no period is ever decisive. Real traces
    decorrelate at longer lags because consecutive beats differ in length, which
    is what gives a genuine pulse its margin. Adding noise does not reproduce
    that -- beat-to-beat variability does, and only partly.

    So continuity is tested by feeding a real, clean window an anchor it cannot
    plausibly have come from."""
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


def test_windows_contaminated_by_motion_are_rejected():
    """The hardest case in the fixtures.

    In the ~30s after exercise the signal genuinely looks periodic at about
    twice the true rate, and all four channels agree on it -- so agreement
    cannot catch it. The peak margin can: near 1.0 there against 2.0-2.2 on
    clean windows."""
    data = _load("optics_recovery_150s.jsonl.gz")
    w = int(25 * FS)
    for start_s in (0, 10, 20):
        out = estimate_window(data[int(start_s * FS):int(start_s * FS) + w], FS)
        assert out.bpm is None, (
            f"t={start_s}s should be rejected, got {out.bpm:.1f} bpm"
        )
        assert out.rejected_by == "confidence"


def test_a_rejected_window_does_not_become_the_anchor():
    data = _load("optics_recovery_150s.jsonl.gz")
    tracker = HeartRateTracker()
    tracker.update(data[:int(25 * FS)], FS, 0.0)
    assert tracker.bpm is None


def test_a_bad_first_window_does_not_poison_the_rest():
    """A tracker anchored to a wrong rate rejects every later correct one, so
    one bad window would otherwise cost the following minute. Repeated
    rejections are treated as evidence against the anchor."""
    reported = [b for b in _track(_load("optics_recovery_150s.jsonl.gz")) if b]
    assert len(reported) >= 8, f"expected most windows to report, got {len(reported)}"
