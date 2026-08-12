"""Heart-rate derivation: synthetic cases, then the real recordings.

Synthetic signals pin the arithmetic against a rate that is known exactly. The
fixtures pin behaviour against physiology, which no synthetic signal can — most
importantly what motion does, which is the failure this whole design is arranged
around and is not one failure but two: octave errors in the seconds after
movement, and a lock onto step cadence during it that bears no harmonic relation
to the heart at all.

Two tests here deliberately assert **wrong** values, because those are known
defects that must not drift into a product unnoticed. Both carry a message
saying what to check before concluding they have been fixed.
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

    The rate is derived from the fixture rather than assumed, because the
    derivation reconstructs its time base from sample index and a wrong rate
    scales every bpm by the same factor -- which looks like a plausible heart
    rate, not like an error. An early analysis of these recordings was 30% high
    for exactly that reason.

    frames / span, not the median inter-frame gap: ~9% of frames share a
    timestamp with their predecessor, which is precisely what breaks a median.
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
    window, fs = _clean_rest_window()
    out = estimate_window(window, fs, previous_bpm=150.0, seconds_since_previous=10.0)
    assert out.bpm is None
    assert out.rejected_by == "continuity"


def test_continuity_allows_a_plausible_change():
    window, fs = _clean_rest_window()
    out = estimate_window(window, fs, previous_bpm=75.0, seconds_since_previous=10.0)
    assert out.bpm == pytest.approx(69.0, abs=3.0)


def test_continuity_does_not_widen_without_bound():
    """A long gap must expire the anchor, not make it infinitely permissive.

    Uncapped, a 60s dropout would allow 180 bpm of movement -- every octave
    error passes while the stale anchor is still treated as authoritative."""
    window, fs = _clean_rest_window()
    out = estimate_window(window, fs, previous_bpm=150.0, seconds_since_previous=60.0)
    assert out.bpm is None, "a 60s gap should not licence an 80 bpm jump"


def test_the_known_interferer_is_recognised_but_not_banned():
    """44.5 bpm is a real rate for some people, so it is flagged rather than
    excluded from the search range."""
    assert near_known_interferer(44.5)
    assert not near_known_interferer(72.0)
    est = estimate_channel(_pulse(44.5), FS)
    assert est.bpm == pytest.approx(44.5, abs=1.5)


# ── real recordings: the physiology ──────────────────────────────────────────

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
    data, fs = _load("optics_recovery_150s.jsonl.gz")
    w = int(25 * fs)
    for start_s in (10, 20):
        out = estimate_window(data[int(start_s * fs):int(start_s * fs) + w], fs)
        assert out.bpm is None, (
            f"t={start_s}s should be rejected, got {out.bpm:.1f} bpm"
        )
        assert out.rejected_by == "confidence"


def test_the_first_window_after_motion_is_wrong_and_is_never_published():
    """The limitation, and what now contains it (#105).

    The window starting the moment the wearer sat down reads ~127 bpm against a
    true ~90, on all four channels and with a healthy margin. That is still
    true of `estimate_window` and is expected to stay true: nothing *in* one
    window separates it from a real 127, and a rule that did would also have to
    leave a genuine 127 reportable -- the trap a previous revision fell into.

    What changed is that the tracker no longer publishes it. An unanchored
    candidate is held until a second window agrees, so a periodicity that is
    gone a step later never becomes a reading. Motion settling is gone a step
    later; a heartbeat is not.

    The old behaviour -- 127 adopted as the anchor, the next two windows
    rejected as discontinuous, re-acquire within ~30s -- contained the error
    from the *anchor* but still put 127 bpm on the chart for one window.
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
    """`unconfirmed_anchor`, not `no_signal`. The window produced a rate and it
    was withheld; reporting that as no signal would tell a caller the sensor
    saw nothing, which is the can't-tell-no-data-from-a-refusal failure this
    codebase keeps a separate vocabulary for."""
    data, fs = _load("optics_recovery_150s.jsonl.gz")
    held = HeartRateTracker().update(data[:int(25 * fs)], fs, 10.0)

    assert held.bpm is None
    assert held.rejected_by == "unconfirmed_anchor"
    assert "127" in held.reason


def test_a_steady_rate_costs_exactly_one_window_of_latency():
    """The price of the rule, pinned so it cannot grow.

    Synthetic rather than a fixture, deliberately: the rest recording's first
    window is rejected on its own merits (confidence 0.28), so measuring the
    latency there would confuse "held for corroboration" with "unusable", and
    would quietly pass if the hold ever became two windows.

    One *usable* window, and then a reading. Not two, and never a rejection --
    a genuinely fast rate is published a step late, not refused.
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
    """The path that needs the rule most, and the one it originally missed.

    A lock is dropped because two windows disagreed with it -- which is what a
    mid-lesson motion event looks like, a student shifting or adjusting the
    headband. So the window doing the re-acquiring is drawn from exactly the
    population this rule exists to distrust, and adopting it directly put the
    weakest test on the most exposed path: the pre-#105 bug relocated rather
    than fixed.

    Found in review of #109 by reproduction rather than by reading, and every
    other test in this file passed against the broken version.
    """
    slow = np.column_stack([_pulse(70.0, seed=i) for i in range(4)])
    fast = np.column_stack([_pulse(140.0, harmonic=0.4, seed=i) for i in range(4)])

    tracker = HeartRateTracker()
    tracker.update(slow, FS, 10.0)                      # candidate
    assert tracker.update(slow, FS, 10.0).bpm == pytest.approx(70.0, abs=2.0)

    first = tracker.update(fast, FS, 10.0)              # discontinuous #1
    assert first.bpm is None and first.rejected_by == "continuity"

    reacquire = tracker.update(fast, FS, 10.0)          # drops the lock
    assert reacquire.bpm is None, (
        "a re-acquired rate was published without a second window agreeing"
    )
    assert reacquire.rejected_by == "unconfirmed_anchor"

    # And it does re-acquire -- the rule delays, it does not lock the tracker
    # out of ever following a real change.
    assert tracker.update(fast, FS, 10.0).bpm == pytest.approx(140.0, abs=3.0)


def test_two_consecutive_windows_must_agree_to_corroborate():
    """Consecutive is necessary, not sufficient.

    Two readable windows in a row that disagree by more than a heart can move
    are two artefacts, or an artefact and a pulse -- not corroboration. Without
    the distance check the rule degrades into "publish the second window
    whatever it says", which would have republished the motion anchor in any
    recording where the window after it happened to be readable.
    """
    fast = np.column_stack([_pulse(150.0, harmonic=0.4, seed=i) for i in range(4)])
    slow = np.column_stack([_pulse(60.0, seed=i) for i in range(4)])

    tracker = HeartRateTracker()
    assert tracker.update(fast, FS, 10.0).bpm is None      # candidate
    assert tracker.update(slow, FS, 10.0).bpm is None, (
        "150 -> 60 bpm in one step is not a heart rate changing"
    )
    # The disagreeing window becomes the new candidate, so agreement with *it*
    # publishes -- the rule delays a reading, it does not lock the tracker out.
    assert tracker.update(slow, FS, 10.0).bpm == pytest.approx(60.0, abs=2.0)


def test_a_gap_discards_a_candidate_rather_than_bridging_it():
    """Two windows either side of an unusable one are not two consecutive
    windows. A candidate is evidence about the present only while the step
    between them is a step; across a gap it is just an old number, and
    accepting it would reintroduce exactly the anchor this rule exists to
    stop -- 127 bpm was followed by two unusable windows."""
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
    """The one point in the through-exercise recording with a timed reading.

    Watch said 75 bpm at the end. This pins the accurate case in the same
    fixture whose motion windows are wildly wrong, so a change that "fixes"
    motion by desensitising the estimator fails here."""
    data, fs = _load("optics_through_exercise.jsonl.gz")
    out = estimate_window(data[int(270 * fs):int(295 * fs)], fs)
    assert out.bpm == pytest.approx(75.0, abs=5.0)


def test_motion_is_reported_confidently_and_wrongly():
    """A known defect, pinned so it cannot regress silently into a product.

    During exercise the derivation reports the wearer's step cadence, ~166 bpm,
    at confidence 1.00 -- against a watch reading of 104 the moment they
    stopped. 166/104 = 1.60, no harmonic relation, so nothing in this module can
    separate them; it needs the accelerometer.

    Asserting the wrong value on purpose. If this starts failing because the
    number moved toward 104, something real changed and this test should be
    replaced by one asserting correctness. If it fails because bpm is None,
    check 100-170 bpm still reports before believing motion was fixed -- that
    exact false positive has happened here twice."""
    data, fs = _load("optics_through_exercise.jsonl.gz")
    out = estimate_window(data[int(90 * fs):int(115 * fs)], fs)
    assert out.bpm == pytest.approx(167.0, abs=6.0)
    assert out.confidence > 0.9, (
        "motion does not degrade into low confidence -- any consumer relying on "
        "the confidence score to filter movement is relying on nothing"
    )


def test_a_bad_first_window_does_not_poison_the_rest():
    """A tracker anchored to a wrong rate rejects every later correct one, so
    one bad window would otherwise cost the following minute. Repeated
    rejections are treated as evidence against the anchor."""
    reported = [b for b in _track(_load("optics_recovery_150s.jsonl.gz")) if b]
    assert len(reported) >= 8, f"expected most windows to report, got {len(reported)}"
