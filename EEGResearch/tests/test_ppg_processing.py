"""Heart-rate derivation: synthetic cases, then real recordings.

Synthetic signals check the arithmetic against a known rate. The recordings
check behavior against real physiology, especially motion: octave errors just
after movement, and a lock onto step cadence during it.

Two tests here deliberately assert **wrong** values, because those are known
defects that must not silently regress. Each explains what to check before
assuming it has been fixed.
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

# Synthetic-signal rate only. Fixture rates come from _load(), per recording.
FS = 64.3
FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> tuple[np.ndarray, float]:
    """Samples and the recording's own measured rate.

    The rate is derived from the fixture rather than assumed: a wrong rate
    scales every bpm by the same factor, which looks like a plausible heart
    rate rather than an error.

    Computed as frames / span, not median inter-frame gap: about 9% of frames
    share a timestamp with their predecessor, which breaks a median.
    """
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
    """A synthetic pulse with the nuisances the real signal carries.

    Noise is on by default because nothing the headband produces is clean, and
    an estimator tuned on a perfect sine won't see anything like it in
    practice. `test_a_clean_signal_is_reportable` covers the noiseless case
    separately, since that's where the high-rate defect showed up sharpest."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * fs)) / fs
    f = bpm / 60.0
    x = np.sin(2 * np.pi * f * t)
    if harmonic:
        # A real pulse isn't a sine; the second harmonic is what makes a
        # spectral argmax report double the rate.
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
    """With a strong second harmonic, a spectral argmax reports 2x the rate.
    The first-peak rule returns the fundamental instead."""
    est = estimate_channel(_pulse(70.0, harmonic=1.4), FS)
    assert est.bpm == pytest.approx(70.0, abs=2.0)


def test_survives_baseline_drift():
    """0.2Hz drift is larger than the pulse in real recordings; without the
    bandpass an argmax returns the band edge instead of the rate."""
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
    """No fixture reaches these rates, so a regression here would be silent.

    Fixtures top out near 92 bpm. A defect above that returns bpm=None with
    rejected_by="confidence", indistinguishable downstream from "no pulse
    detected" -- exactly the band a child during/after exertion falls in.

    Regression guarded against: an exclusion band narrower than the ACF peak
    let the peak's own shoulder count as a rival, collapsing confidence above
    about 100 bpm even though estimate_channel itself was correct. Tested at
    the whole-pipeline level (estimate_window) rather than per-channel because
    that's where the bug was."""
    channels = np.column_stack([_pulse(bpm, harmonic=0.4, seed=i) for i in range(4)])
    out = estimate_window(channels, FS)
    assert out.bpm is not None, (
        f"{bpm} bpm rejected: {out.reason}"
    )
    assert out.bpm == pytest.approx(bpm, abs=2.0)


@pytest.mark.parametrize("bpm", [72.0, 170.0])
def test_a_clean_signal_is_reportable(bpm):
    """Covers the noiseless case, where the high-rate defect above showed up
    sharpest -- noise is on by default elsewhere, so nothing else exercises
    this."""
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
    """Tested against a real recording, not a synthetic pulse, because
    continuity has to hold against messy real input."""
    window, fs = _clean_rest_window()
    out = estimate_window(window, fs, previous_bpm=150.0, seconds_since_previous=10.0)
    assert out.bpm is None
    assert out.rejected_by == "continuity"


def test_continuity_allows_a_plausible_change():
    window, fs = _clean_rest_window()
    out = estimate_window(window, fs, previous_bpm=75.0, seconds_since_previous=10.0)
    assert out.bpm == pytest.approx(69.0, abs=3.0)


def test_continuity_does_not_widen_without_bound():
    """A long gap must expire the anchor rather than widen its tolerance
    forever. Uncapped, a 60s dropout would allow 180 bpm of movement, letting
    every octave error through while the stale anchor stays authoritative."""
    window, fs = _clean_rest_window()
    out = estimate_window(window, fs, previous_bpm=150.0, seconds_since_previous=60.0)
    assert out.bpm is None, "a 60s gap should not licence an 80 bpm jump"


def test_the_known_interferer_is_recognised_but_not_banned():
    """44.5 bpm is a real rate for some people, so it's flagged, not excluded
    from the search range."""
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
    """Ground truth: 67.9 bpm by independent spectral analysis of the same
    session, agreeing with the wearer's watch."""
    reported = [b for b in _track(_load("optics_rest_60s.jsonl.gz")) if b]
    assert reported, "expected at least one reportable window at rest"
    assert np.median(reported) == pytest.approx(68.0, abs=3.0)


def test_the_rate_is_higher_after_exertion():
    """End-to-end check: same headband, minutes apart, exercise in between.
    The wearer's watch recorded a peak of 97 bpm."""
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
    """10-35s after exercise, channels split 113/58/113/58 against a true rate
    near 90 -- two genuinely different periods nearly tied, which is a low
    margin."""
    data, fs = _load("optics_recovery_150s.jsonl.gz")
    w = int(25 * fs)
    for start_s in (10, 20):
        out = estimate_window(data[int(start_s * fs):int(start_s * fs) + w], fs)
        assert out.bpm is None, (
            f"t={start_s}s should be rejected, got {out.bpm:.1f} bpm"
        )
        assert out.rejected_by == "confidence"


def test_the_first_window_after_motion_is_wrong_and_is_never_published():
    """The window starting the moment the wearer sat down reads ~127 bpm
    against a true ~90, on all four channels with a healthy margin.
    `estimate_window` is expected to keep reading this wrong: nothing inside
    one window can tell it apart from a real 127 bpm.

    So the tracker holds an unanchored candidate until a second window agrees,
    instead of publishing it immediately. A periodicity that's gone a step
    later never becomes a reading -- motion settling fades, a heartbeat
    doesn't.
    """
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
    """`unconfirmed_anchor`, not `no_signal`: the window produced a rate that
    was withheld, not a sensor that saw nothing. Those are different facts and
    need different labels."""
    data, fs = _load("optics_recovery_150s.jsonl.gz")
    held = HeartRateTracker().update(data[:int(25 * fs)], fs, 10.0)

    assert held.bpm is None
    assert held.rejected_by == "unconfirmed_anchor"
    assert "127" in held.reason


def test_a_steady_rate_costs_exactly_one_window_of_latency():
    """Pins the corroboration cost at exactly one usable window, then a
    reading -- never two, never a rejection.

    Synthetic rather than a fixture: the rest recording's first window is
    already rejected on its own merits (confidence 0.28), which would confuse
    "held for corroboration" with "unusable" and hide a regression to two
    windows.
    """
    channels = np.column_stack([_pulse(150.0, harmonic=0.4, seed=i) for i in range(4)])

    tracker = HeartRateTracker()
    seen = [tracker.update(channels, FS, 10.0).bpm for _ in range(3)]

    assert seen[0] is None, "the first window must not be published unconfirmed"
    assert seen[1] == pytest.approx(150.0, abs=2.0), (
        f"a steady rate should publish at the second usable window: {seen}"
    )
    assert seen[2] == pytest.approx(150.0, abs=2.0)


def test_re_acquisition_is_corroborated_too():
    """A lock is dropped because two windows disagreed with it -- the same
    shape as a mid-lesson motion event. Re-acquisition must go through the
    same corroboration rule rather than adopting the new window directly.
    """
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

    # It does eventually re-acquire; the rule only delays, it doesn't block.
    assert tracker.update(fast, FS, 10.0).bpm == pytest.approx(140.0, abs=3.0)


def test_two_consecutive_windows_must_agree_to_corroborate():
    """Consecutive readings are necessary but not sufficient: two readable
    windows disagreeing by more than a heart can move are two artefacts, not
    corroboration. Without the distance check, the rule degrades to
    "publish the second window whatever it says."
    """
    fast = np.column_stack([_pulse(150.0, harmonic=0.4, seed=i) for i in range(4)])
    slow = np.column_stack([_pulse(60.0, seed=i) for i in range(4)])

    tracker = HeartRateTracker()
    assert tracker.update(fast, FS, 10.0).bpm is None      # candidate
    assert tracker.update(slow, FS, 10.0).bpm is None, (
        "150 -> 60 bpm in one step is not a heart rate changing"
    )
    # The disagreeing window becomes the new candidate, so agreement with it
    # publishes -- the rule delays, it doesn't block.
    assert tracker.update(slow, FS, 10.0).bpm == pytest.approx(60.0, abs=2.0)


def test_a_gap_discards_a_candidate_rather_than_bridging_it():
    """Two windows either side of an unusable one aren't consecutive. Bridging
    the gap would reintroduce the exact bug this rule exists to stop: 127 bpm
    followed by two unusable windows."""
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
    """Watch said 75 bpm at the end of this recording. Pins the accurate case
    in the same fixture whose motion windows are wildly wrong, so a fix that
    just desensitizes the estimator would fail here."""
    data, fs = _load("optics_through_exercise.jsonl.gz")
    out = estimate_window(data[int(270 * fs):int(295 * fs)], fs)
    assert out.bpm == pytest.approx(75.0, abs=5.0)


def test_motion_is_reported_confidently_and_wrongly():
    """A known defect, pinned so it can't regress silently.

    During exercise the derivation reports the wearer's step cadence, ~166
    bpm, at confidence 1.00, against a watch reading of 104 the moment they
    stopped. 166/104 = 1.60, no harmonic relation, so nothing in this module
    can tell them apart -- fixing it needs the accelerometer.

    Asserts the wrong value on purpose. If it starts moving toward 104,
    something real changed and this should become a correctness test. If it
    fails with bpm=None, check that 100-170 bpm still reports before assuming
    motion was fixed."""
    data, fs = _load("optics_through_exercise.jsonl.gz")
    out = estimate_window(data[int(90 * fs):int(115 * fs)], fs)
    assert out.bpm == pytest.approx(167.0, abs=6.0)
    assert out.confidence > 0.9, (
        "motion does not degrade into low confidence -- any consumer relying on "
        "the confidence score to filter movement is relying on nothing"
    )


def test_a_bad_first_window_does_not_poison_the_rest():
    """A tracker anchored to a wrong rate rejects every later correct window,
    so one bad window shouldn't cost the following minute. Repeated
    rejections count as evidence against the anchor."""
    reported = [b for b in _track(_load("optics_recovery_150s.jsonl.gz")) if b]
    assert len(reported) >= 8, f"expected most windows to report, got {len(reported)}"
