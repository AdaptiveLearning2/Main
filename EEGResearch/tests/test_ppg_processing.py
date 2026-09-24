"""Heart-rate derivation on synthetic signals and real recordings (see tests/fixtures/README.md); two tests pin known-wrong values."""

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

# Synthetic-signal rate only. Fixture rates come from _load(), per recording.
FS = 64.3
FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> tuple[np.ndarray, float]:
    """Samples and the recording's own rate, as frames / span (~9% of frames share a timestamp)."""
    ch, ts = [], []
    with gzip.open(FIXTURES / name, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            ch.append(row["ch"])
            ts.append(row["mono_ts_ms"])
    span_s = (ts[-1] - ts[0]) / 1000.0
    return np.array(ch, dtype=float), (len(ch) - 1) / span_s


def _pulse(bpm: float, seconds: float = 25.0, fs: float = FS,
           harmonic: float = 0.0, noise: float = 0.08, drift: float = 0.0,
           seed: int = 0) -> np.ndarray:
    """A synthetic pulse with the real signal's nuisances; noise is on by default."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * fs)) / fs
    f = bpm / 60.0
    x = np.sin(2 * np.pi * f * t)
    if harmonic:
        # The second harmonic is what makes a spectral argmax report double the rate.
        x += harmonic * np.sin(2 * np.pi * 2 * f * t)
    if drift:
        x += drift * np.sin(2 * np.pi * 0.2 * t)   # baseline wander
    if noise:
        x += noise * rng.standard_normal(len(t))
    return x


# ── synthetic signals ─────────────────────────────────────────────────────

@pytest.mark.parametrize("bpm", [48.0, 60.0, 75.0, 100.0, 140.0])
def test_recovers_a_known_rate(bpm):
    est = estimate_channel(_pulse(bpm), FS)
    assert est.bpm == pytest.approx(bpm, abs=1.0)


def test_recovers_the_fundamental_not_the_harmonic():
    """A spectral argmax reports 2x the rate; the first-peak rule does not."""
    est = estimate_channel(_pulse(70.0, harmonic=1.4), FS)
    assert est.bpm == pytest.approx(70.0, abs=2.0)


def test_survives_baseline_drift():
    """Real 0.2 Hz drift exceeds the pulse; unfiltered, an argmax returns the band edge."""
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
    """No fixture reaches these rates; a failure reads downstream as "no pulse detected"."""
    channels = np.column_stack([_pulse(bpm, harmonic=0.4, seed=i) for i in range(4)])
    out = estimate_window(channels, FS)
    assert out.bpm is not None, (
        f"{bpm} bpm rejected: {out.reason}"
    )
    assert out.bpm == pytest.approx(bpm, abs=2.0)


@pytest.mark.parametrize("bpm", [72.0, 170.0])
def test_a_clean_signal_is_reportable(bpm):
    channels = np.column_stack([_pulse(bpm, harmonic=0.4, noise=0.0)] * 4)
    out = estimate_window(channels, FS)
    assert out.bpm == pytest.approx(bpm, abs=2.0)


def test_one_bad_channel_does_not_move_the_answer():
    good = [_pulse(72.0, seed=i) for i in range(3)]
    bad = _pulse(120.0, seed=9)
    out = estimate_window(np.column_stack(good + [bad]), FS)
    assert out.bpm == pytest.approx(72.0, abs=2.0)


def test_continuity_rejects_an_impossible_jump():
    window, fs = _clean_rest_window()
    out = estimate_window(window, fs, previous_bpm=150.0, seconds_since_previous=10.0)
    assert out.bpm is None
    assert out.rejected_by == "continuity"


def test_continuity_allows_a_plausible_change():
    window, fs = _clean_rest_window()
    out = estimate_window(window, fs, previous_bpm=75.0, seconds_since_previous=10.0)
    assert out.bpm == pytest.approx(69.0, abs=3.0)


def test_continuity_does_not_widen_without_bound():
    """A long gap expires the anchor; uncapped, 60 s would allow 180 bpm of movement."""
    window, fs = _clean_rest_window()
    out = estimate_window(window, fs, previous_bpm=150.0, seconds_since_previous=60.0)
    assert out.bpm is None, "a 60s gap should not licence an 80 bpm jump"


def test_the_known_interferer_is_recognised_but_not_banned():
    """44.5 bpm is a real rate for some people, so it's flagged, not excluded."""
    assert near_known_interferer(44.5)
    assert not near_known_interferer(72.0)
    est = estimate_channel(_pulse(44.5), FS)
    assert est.bpm == pytest.approx(44.5, abs=1.5)


# ── real recordings: physiology ──────────────────────────────────────────

def _clean_rest_window() -> tuple[np.ndarray, float]:
    """A 25s window from the resting fixture, past its noisy opening."""
    data, fs = _load("optics_rest_60s.jsonl.gz")
    return data[int(20 * fs):int(45 * fs)], fs


def _track(loaded, window_s: float = 25.0, step_s: float = 10.0):
    data, fs = loaded
    tracker = HeartRateTracker()
    w, step = int(window_s * fs), int(step_s * fs)
    return [tracker.update(data[s:s + w], fs, step_s).bpm
            for s in range(0, len(data) - w, step)]


def test_resting_recording_reads_the_resting_rate():
    """Ground truth 67.9 bpm (tests/fixtures/README.md)."""
    reported = [b for b in _track(_load("optics_rest_60s.jsonl.gz")) if b]
    assert reported, "expected at least one reportable window at rest"
    assert np.median(reported) == pytest.approx(68.0, abs=3.0)


def test_the_rate_is_higher_after_exertion():
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
    """Channels split between two nearly tied periods: a low margin."""
    data, fs = _load("optics_recovery_150s.jsonl.gz")
    w = int(25 * fs)
    for start_s in (10, 20):
        out = estimate_window(data[int(start_s * fs):int(start_s * fs) + w], fs)
        assert out.bpm is None, (
            f"t={start_s}s should be rejected, got {out.bpm:.1f} bpm"
        )
        assert out.rejected_by == "confidence"


def test_the_first_window_after_motion_is_wrong_and_is_never_published():
    """Nothing in one window separates this from a real 127 bpm; the tracker waits for a second to agree."""
    data, fs = _load("optics_recovery_150s.jsonl.gz")
    w, step = int(25 * fs), int(10 * fs)

    first = estimate_window(data[:w], fs)
    assert first.bpm == pytest.approx(127.0, abs=4.0), (
        "if this window now reads correctly the estimator has changed, and the "
        "tracker rule below is being tested against the wrong input -- but "
        "check 120-180 bpm still reports first"
    )

    tracker = HeartRateTracker()
    seen = [tracker.update(data[s:s + w], fs, 10.0).bpm
            for s in range(0, 5 * step, step)]

    assert seen[0] is None, f"127 bpm was published as a reading: {seen}"
    assert all(b is None for b in seen[:4]), (
        f"nothing should be published until a window is corroborated: {seen}"
    )
    assert seen[4] == pytest.approx(83.0, abs=6.0), (
        f"should publish the first corroborated rate, got {seen}"
    )


def test_a_held_first_reading_says_why():
    """`unconfirmed_anchor`, not `no_signal`: a withheld rate, not a sensor that saw nothing."""
    data, fs = _load("optics_recovery_150s.jsonl.gz")
    held = HeartRateTracker().update(data[:int(25 * fs)], fs, 10.0)

    assert held.bpm is None
    assert held.rejected_by == "unconfirmed_anchor"
    assert "127" in held.reason


def test_a_steady_rate_costs_exactly_one_window_of_latency():
    """Synthetic: the rest fixture's first window is rejected on its own (confidence 0.28)."""
    channels = np.column_stack([_pulse(150.0, harmonic=0.4, seed=i) for i in range(4)])

    tracker = HeartRateTracker()
    seen = [tracker.update(channels, FS, 10.0).bpm for _ in range(3)]

    assert seen[0] is None, "the first window must not be published unconfirmed"
    assert seen[1] == pytest.approx(150.0, abs=2.0), (
        f"a steady rate should publish at the second usable window: {seen}"
    )
    assert seen[2] == pytest.approx(150.0, abs=2.0)


def test_re_acquisition_is_corroborated_too():
    slow = np.column_stack([_pulse(70.0, seed=i) for i in range(4)])
    fast = np.column_stack([_pulse(140.0, harmonic=0.4, seed=i) for i in range(4)])

    tracker = HeartRateTracker()
    tracker.update(slow, FS, 10.0)                      # candidate
    assert tracker.update(slow, FS, 10.0).bpm == pytest.approx(70.0, abs=2.0)

    first = tracker.update(fast, FS, 10.0)              # discontinuous
    assert first.bpm is None and first.rejected_by == "continuity"

    reacquire = tracker.update(fast, FS, 10.0)          # drops the lock
    assert reacquire.bpm is None, (
        "a re-acquired rate was published without a second window agreeing"
    )
    assert reacquire.rejected_by == "unconfirmed_anchor"

    # The rule delays, it doesn't block.
    assert tracker.update(fast, FS, 10.0).bpm == pytest.approx(140.0, abs=3.0)


def test_two_consecutive_windows_must_agree_to_corroborate():
    """Two windows further apart than a heart can move are two artefacts, not corroboration."""
    fast = np.column_stack([_pulse(150.0, harmonic=0.4, seed=i) for i in range(4)])
    slow = np.column_stack([_pulse(60.0, seed=i) for i in range(4)])

    tracker = HeartRateTracker()
    assert tracker.update(fast, FS, 10.0).bpm is None      # candidate
    assert tracker.update(slow, FS, 10.0).bpm is None, (
        "150 -> 60 bpm in one step is not a heart rate changing"
    )
    # The disagreeing window becomes the new candidate.
    assert tracker.update(slow, FS, 10.0).bpm == pytest.approx(60.0, abs=2.0)


def test_a_gap_discards_a_candidate_rather_than_bridging_it():
    """Windows either side of an unusable one aren't consecutive."""
    good = np.column_stack([_pulse(72.0, seed=i) for i in range(4)])
    flat = np.zeros((int(25 * FS), 4))

    tracker = HeartRateTracker()
    assert tracker.update(good, FS, 10.0).bpm is None      # candidate
    assert tracker.update(flat, FS, 10.0).bpm is None      # unusable: discards it
    assert tracker.update(good, FS, 10.0).bpm is None, (
        "a candidate must not be corroborated across an unusable window"
    )
    assert tracker.update(good, FS, 10.0).bpm == pytest.approx(72.0, abs=2.0)


def test_settled_recovery_matches_the_watch():
    """Watch 75 bpm; a fix that just desensitises the estimator fails here."""
    data, fs = _load("optics_through_exercise.jsonl.gz")
    out = estimate_window(data[int(270 * fs):int(295 * fs)], fs)
    assert out.bpm == pytest.approx(75.0, abs=5.0)


def test_motion_is_reported_confidently_and_wrongly():
    """Known defect, pinned: step cadence read as the rate; fixing it needs the accelerometer.

    If it fails with bpm=None, check 100-170 bpm still reports before assuming motion was fixed.
    """
    data, fs = _load("optics_through_exercise.jsonl.gz")
    out = estimate_window(data[int(90 * fs):int(115 * fs)], fs)
    assert out.bpm == pytest.approx(167.0, abs=6.0)
    assert out.confidence > 0.9, (
        "motion does not degrade into low confidence -- any consumer relying on "
        "the confidence score to filter movement is relying on nothing"
    )


def test_a_bad_first_window_does_not_poison_the_rest():
    """Repeated rejections count as evidence against a wrong anchor."""
    reported = [b for b in _track(_load("optics_recovery_150s.jsonl.gz")) if b]
    assert len(reported) >= 8, f"expected most windows to report, got {len(reported)}"
