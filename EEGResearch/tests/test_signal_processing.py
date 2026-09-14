"""SignalProcessor after the Phase 0 captures (tests/fixtures/EEG_REFERENCE.md).

Each test here pins one Phase 1 change and was checked by reverting that
change: the test must fail against the code it replaces. The older processor
tests in test_app.py still run; where a change moved their premise they were
updated there.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.app.models import EegSample
from src.app.services.signal_processing import SignalProcessor

RELAXED = {"delta": 0.4, "theta": 0.1, "alpha": 0.5, "beta": 0.1, "gamma": 0.05}
ENGAGED = {"delta": 0.4, "theta": 0.0, "alpha": -0.2, "beta": 0.6, "gamma": 0.1}
CONTACT_GOOD = {"hsi": [1.0, 1.0, 1.0, 1.0], "is_good": [1.0, 1.0, 1.0, 1.0]}
CONTACT_POOR = {"hsi": [4.0, 4.0, 4.0, 4.0], "is_good": [1.0, 0.0, 0.0, 0.0]}


class Ticker:
    """Feeds samples at a fixed tick rate on a clock the processor shares, so
    every time-based window in the processor sees real elapsed seconds."""

    def __init__(self, window_size: int = 20, hz: float = 4.0):
        self.now = 0.0
        self.t0 = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
        self.dt = 1.0 / hz
        self.processor = SignalProcessor(window_size=window_size, clock=lambda: self.now)

    def sample(self, level: float = 800.0, spread: float = 10.0) -> EegSample:
        return EegSample(
            timestamp=self.t0 + timedelta(seconds=self.now),
            channel_tp9=level, channel_af7=level + spread,
            channel_af8=level + spread / 2, channel_tp10=level + spread / 4,
        )

    def tick(self, bands: dict | None, *, level: float = 800.0, spread: float = 10.0):
        features = self.processor.update(self.sample(level, spread), bands)
        self.now += self.dt
        return features

    def run(self, bands: dict | None, ticks: int, **kw):
        features = None
        for _ in range(ticks):
            features = self.tick(bands, **kw)
        return features


# -- 1.1 the amplitude terms are out of the scores -----------------------------

def test_raw_level_and_spread_do_not_move_focus_or_calm_when_bands_are_present():
    """Rest spread was 147 uV on one strap fitting and 23 uV on another for
    the same person; a score that read that as calm was reading the strap."""
    bands = {**RELAXED, **CONTACT_GOOD}
    tight = Ticker().run(bands, 30, level=800.0, spread=20.0)
    loose = Ticker().run(bands, 30, level=950.0, spread=150.0)
    assert tight["focus_score"] == pytest.approx(loose["focus_score"])
    assert tight["calm_score"] == pytest.approx(loose["calm_score"])


def test_without_bands_the_amplitude_fallback_still_scores():
    """An older bridge reports no band powers; the fallback is what it gets."""
    tight = Ticker().run(None, 30, spread=20.0)
    loose = Ticker().run(None, 30, spread=150.0)
    assert tight["calm_score"] > loose["calm_score"]
    assert 0.0 <= tight["focus_score"] <= 100.0


# -- 1.2 confidence is signal quality, and calm is not in it ------------------

def test_a_stressed_spectrum_on_good_contact_is_not_low_confidence():
    """Calm was 32% of confidence, so the stressed student -- low calm -- was
    the one most likely to be discarded as insufficient_signal and treated
    by fusion as no opinion. Same contact, same stability: same confidence."""
    relaxed = Ticker().run({**RELAXED, **CONTACT_GOOD}, 30)
    aroused = Ticker().run({**ENGAGED, "alpha": -0.6, "gamma": 0.7, **CONTACT_GOOD}, 30)
    assert aroused["calm_score"] < relaxed["calm_score"] - 20
    assert aroused["confidence"] == pytest.approx(relaxed["confidence"], abs=1.0)


def test_poor_contact_takes_a_steady_signal_below_the_gate_and_degraded_does_not():
    """The gate is 0.45 in adaptation.py and signal_fusion.py. On hardware
    the old confidence crossed it on 2 ticks in ~5000, poor contact
    included; it has to mean something about the electrodes."""
    good = Ticker().run({**RELAXED, **CONTACT_GOOD}, 30)
    degraded = Ticker().run({**RELAXED, "hsi": [1.0, 1.0, 4.0, 4.0],
                             "is_good": [1.0, 1.0, 0.0, 0.0]}, 30)
    poor = Ticker().run({**RELAXED, **CONTACT_POOR}, 30)
    assert good["confidence"] > degraded["confidence"] > poor["confidence"]
    assert degraded["confidence"] >= 45.0
    assert poor["confidence"] < 45.0
    assert good["contact_ratio"] == pytest.approx(1.0)
    assert poor["contact_ratio"] < SignalProcessor.CONTACT_DEGRADED


def test_a_jumping_spectrum_lowers_confidence_on_the_same_contact():
    steady = Ticker().run({**RELAXED, **CONTACT_GOOD}, 30)
    t = Ticker()
    for i in range(30):
        t.tick({**(RELAXED if i % 2 else ENGAGED), **CONTACT_GOOD})
    jumpy = t.tick({**RELAXED, **CONTACT_GOOD})
    assert jumpy["confidence"] < steady["confidence"] - 10


def test_quality_and_confidence_read_the_same_contact():
    """One smoothed contact per tick feeds both, so the verdict and the score
    cannot disagree about the same electrodes."""
    f = Ticker().run({**RELAXED, **CONTACT_POOR}, 30)
    assert f["signal_quality"] == "poor" and f["quality_basis"] == "contact"
    assert f["contact_ratio"] is not None and f["contact_ratio"] < 0.4
    g = Ticker().run(RELAXED, 30)  # no contact data at all
    assert g["contact_ratio"] is None and g["quality_basis"] == "heuristic"


# -- 1.4 the artifact gate holds, never writes ---------------------------------

def _warm(t: Ticker, ticks: int = 20):
    return t.run({**RELAXED, **CONTACT_GOOD}, ticks)


def test_a_delta_spike_holds_the_previous_scores_and_is_counted_not_rejected():
    """Delta doubles on a blink. A blink is not a change in focus, so the
    tick holds the last admitted scores -- not zero, not a fresh score off
    an eye movement -- and says so."""
    t = Ticker()
    before = _warm(t)
    blink = t.tick({**ENGAGED, "delta": RELAXED["delta"] + 0.6, **CONTACT_GOOD})
    assert blink["artifact_reason"] == "delta_jump"
    assert blink["samples_artifact"] == 1
    assert blink["samples_rejected"] == before["samples_rejected"]
    assert blink["focus_score"] == pytest.approx(before["focus_score"])
    assert blink["calm_score"] == pytest.approx(before["calm_score"])
    # Contact is still judged on the held tick: quality is its own fact.
    assert blink["signal_quality"] == "good" and blink["quality_basis"] == "contact"


def test_a_jaw_clench_is_held_on_gamma_exceeding_beta():
    t = Ticker()
    before = _warm(t)
    clench = t.tick({**RELAXED, "beta": 0.3, "gamma": 1.0, **CONTACT_GOOD})
    assert clench["artifact_reason"] == "emg_gamma"
    assert clench["calm_score"] == pytest.approx(before["calm_score"])


def test_a_spread_jump_is_relative_to_the_sessions_own_spread():
    """Rest spread was 147 uV on one fitting and 23 on another, so an
    absolute bound would reject one wearer's every tick and the other's
    none."""
    tight = Ticker()
    tight.run({**RELAXED, **CONTACT_GOOD}, 20, spread=20.0)
    assert tight.tick({**RELAXED, **CONTACT_GOOD}, spread=80.0)["artifact_reason"] == "spread_jump"
    loose = Ticker()
    loose.run({**RELAXED, **CONTACT_GOOD}, 20, spread=150.0)
    assert loose.tick({**RELAXED, **CONTACT_GOOD}, spread=200.0)["artifact_reason"] is None


def test_held_ticks_enter_neither_the_baseline_nor_the_window():
    # A window wider than the warm-up, so its length can still grow: at
    # maxlen a deque's length is the same whether or not a tick was added.
    t = Ticker(window_size=40)
    _warm(t)
    n_base = len(t.processor._baseline_focus)
    n_win = len(t.processor.window)
    assert n_win < 40
    t.tick({**ENGAGED, "delta": 1.5, **CONTACT_GOOD}, level=900.0)
    assert len(t.processor._baseline_focus) == n_base
    assert len(t.processor.window) == n_win
    assert all(v[0] != 900.0 for v in t.processor.window)


def test_no_verdict_before_the_history_exists():
    t = Ticker()
    first = t.tick({**RELAXED, "delta": 3.0, **CONTACT_GOOD})
    assert first["artifact_reason"] is None and first["samples_artifact"] == 0


def test_three_states_are_distinguishable_on_the_payload():
    t = Ticker()
    _warm(t)
    held = t.tick({**RELAXED, "delta": 1.5, **CONTACT_GOOD})
    rejected = t.tick({**RELAXED, "hsi": [4.0] * 4, "is_good": [0.0] * 4})
    low = t.tick({**ENGAGED, "alpha": -0.6, "gamma": 0.2, **CONTACT_GOOD})
    assert held["artifact_reason"] is not None
    assert rejected["artifact_reason"] is None and rejected["samples_rejected"] == 1
    assert low["artifact_reason"] is None and low["samples_rejected"] == 1
    assert low["calm_score"] < held["calm_score"]


# -- 1.5 the ratios are smoothed before they are scaled ------------------------

def test_a_one_tick_excursion_moves_the_score_less_than_the_ratio_moved():
    t = Ticker()
    steady = _warm(t, 40)
    # A large but non-artifact swing for one tick, then back.
    spike = t.tick({**ENGAGED, **CONTACT_GOOD})
    back = t.tick({**RELAXED, **CONTACT_GOOD})
    raw_move = abs(spike["focus_log_ratio"] - steady["focus_log_ratio"])
    smooth_move = abs(spike["focus_log_ratio_smoothed"] - steady["focus_log_ratio_smoothed"])
    assert smooth_move < 0.15 * raw_move
    assert abs(spike["focus_score"] - steady["focus_score"]) < 8.0
    assert abs(back["focus_score"] - steady["focus_score"]) < 8.0


def test_a_sustained_change_converges_and_within_the_deciders_cadence():
    """The topic decider reads the label about every 10 s. 4 s of smoothing
    reaches 92% of a step in 10 s; a relaxed-to-aroused step must cross the
    stressed line (calm < 35) inside 40 ticks at 4 Hz."""
    t = Ticker()
    _warm(t, 40)
    aroused = {**ENGAGED, "alpha": -0.6, "gamma": 0.7, **CONTACT_GOOD}
    crossed_at = None
    for i in range(40):
        f = t.tick(aroused)
        if f["calm_score"] < 35.0 and crossed_at is None:
            crossed_at = i
    assert crossed_at is not None and crossed_at < 40
    settled = t.run(aroused, 80)
    assert settled["calm_log_ratio_smoothed"] == pytest.approx(settled["calm_log_ratio"], abs=0.02)


def test_smoothing_is_time_based_not_tick_based():
    """The same ticks at 1 Hz and 4 Hz cover different spans, so the value
    after N ticks differs; a count-based smoother could not tell them
    apart."""
    fast, slow = Ticker(hz=4.0), Ticker(hz=1.0)
    for tk in (fast, slow):
        _warm(tk, 40)
        for _ in range(4):
            f = tk.tick({**ENGAGED, **CONTACT_GOOD})
        tk.after = f
    assert slow.after["focus_log_ratio_smoothed"] > fast.after["focus_log_ratio_smoothed"]


def test_held_and_rejected_ticks_do_not_advance_the_smoothed_ratio():
    t = Ticker()
    steady = _warm(t, 40)
    held = t.tick({**ENGAGED, "delta": 1.5, **CONTACT_GOOD})
    rejected = t.tick({**ENGAGED, "hsi": [4.0] * 4, "is_good": [0.0] * 4})
    for f in (held, rejected):
        assert f["focus_log_ratio_smoothed"] == pytest.approx(steady["focus_log_ratio_smoothed"])
        assert f["focus_score"] == pytest.approx(steady["focus_score"])


# -- 1.6 the baseline is time-based, contact-gated and does not step ----------

def _run_until_latched(t: Ticker, bands: dict, limit: int = 400) -> int:
    for i in range(limit):
        if t.processor._baseline_ready:
            return i
        t.tick(bands)
    raise AssertionError("baseline never latched")


def test_the_baseline_ignores_poor_contact_ticks_and_its_clock_starts_on_the_first_good_one():
    """Both captures latched the old baseline inside the loose-strap settling
    period. On poor contact nothing is collected and the 45 s have not
    begun."""
    t = Ticker()
    t.run({**RELAXED, **CONTACT_POOR}, 400)  # 100 s of poor contact
    assert not t.processor._baseline_ready
    assert t.processor._baseline_started is None
    n = _run_until_latched(t, {**RELAXED, "hsi": [1.0, 1.0, 4.0, 4.0],
                              "is_good": [1.0, 1.0, 0.0, 0.0]})  # degraded: 2 of 4
    assert 4 * SignalProcessor.BASELINE_SECONDS <= n <= 4 * SignalProcessor.BASELINE_SECONDS + 30


def test_the_latch_needs_elapsed_time_not_a_tick_count():
    slow, fast = Ticker(hz=1.0), Ticker(hz=16.0)
    bands = {**RELAXED, **CONTACT_GOOD}
    fast.run(bands, 200)  # 200 ticks, 12.5 s
    assert not fast.processor._baseline_ready
    slow.run(bands, 50)  # 50 ticks, 50 s
    assert slow.processor._baseline_ready


def test_a_steady_signal_crosses_the_latch_without_a_step():
    """The centre ramps from the population midpoint to the session mean
    over BASELINE_RAMP_SECONDS, on one scale, so the latch is not visible as
    a jump in the scores."""
    t = Ticker()
    bands = {**ENGAGED, **CONTACT_GOOD}  # far from the population midpoint
    scores = []
    for _ in range(4 * int(SignalProcessor.BASELINE_SECONDS + SignalProcessor.BASELINE_RAMP_SECONDS) + 40):
        scores.append(t.tick(bands)["focus_score"])
    assert t.processor._baseline_ready
    steps = [abs(b - a) for a, b in zip(scores, scores[1:])]
    assert max(steps) < 3.0
    # And it did move: from the population reading to the session's own 50.
    assert abs(scores[0] - scores[-1]) > 10.0
    assert scores[-1] == pytest.approx(50.0, abs=1.0)


def test_the_gain_is_the_same_on_both_sides_of_the_latch():
    """A raw excursion of the same size scores the same distance whether or
    not the baseline has latched -- the old path doubled the gain at latch."""
    # Before: a fresh processor on the population path.
    fresh = Ticker()
    ts0 = fresh.sample().timestamp
    p0 = fresh.processor
    before = (p0._score_against_baseline(0.3, "focus", ts0)
              - p0._score_against_baseline(0.0, "focus", ts0))
    # After: a baseline the collector produced, well past its ramp -- the
    # timestamp matters, since omitting it skips the ramp entirely.
    t = Ticker()
    _run_until_latched(t, {**ENGAGED, **CONTACT_GOOD})
    p = t.processor
    mean = p._baseline_focus_mean
    ts = p._baseline_latched + timedelta(seconds=60)
    after = (p._score_against_baseline(mean + 0.3, "focus", ts)
             - p._score_against_baseline(mean, "focus", ts))
    assert before == pytest.approx(after)
    assert before == pytest.approx(0.3 / (SignalProcessor.FOCUS_LOG_RATIO_MAX - SignalProcessor.FOCUS_LOG_RATIO_MIN))


# -- 1.7b a label needs persistence before the cooldown protects it ------------

def _engine():
    from src.app.services.adaptation import AdaptationEngine
    clock = [0.0]
    eng = AdaptationEngine(clock=lambda: clock[0])
    return eng, clock


def _feat(label: str) -> dict:
    return {
        "focused": {"focus_score": 80.0, "calm_score": 60.0, "confidence": 90.0},
        "stressed": {"focus_score": 40.0, "calm_score": 20.0, "confidence": 90.0},
        "neutral": {"focus_score": 50.0, "calm_score": 60.0, "confidence": 90.0},
    }[label]


def test_a_single_tick_excursion_never_changes_the_label():
    """On the captures 90 of 133 focused readings were the cooldown holding
    one spurious tick. One tick must not become the label at all."""
    eng, clock = _engine()
    for _ in range(8):
        assert eng.infer_state(_feat("neutral")).label == "neutral"
        clock[0] += 0.25
    spike = eng.infer_state(_feat("focused"))
    assert spike.label == "neutral" and "persistence" in spike.reason
    clock[0] += 0.25
    for _ in range(12):
        assert eng.infer_state(_feat("neutral")).label == "neutral"
        clock[0] += 0.25


def test_four_consecutive_ticks_commit_the_label_and_the_cooldown_then_holds_it():
    eng, clock = _engine()
    # Pinned, not read: driven from the attribute this went vacuous at the
    # pre-change value of 1.
    assert eng.persist_ticks == 4
    clock[0] = 10.0
    eng.infer_state(_feat("neutral"))
    labels = []
    for _ in range(4):
        labels.append(eng.infer_state(_feat("focused")).label)
        clock[0] += 0.25
    assert labels == ["neutral", "neutral", "neutral", "focused"]
    # Now a run of neutral ticks inside the cooldown: persistence is met
    # after four, but the cooldown still holds focused until it lapses.
    held = [eng.infer_state(_feat("neutral")) for _ in range(eng.persist_ticks + 2)]
    assert all(h.label == "focused" for h in held)
    assert "Cooldown" in held[-1].reason
    clock[0] += eng.cooldown_seconds
    assert eng.infer_state(_feat("neutral")).label == "neutral"


def test_after_signal_loss_a_label_still_needs_persistence_but_not_the_cooldown():
    """The stream manager resets on every no-sample tick, so flapping
    contact would otherwise commit whatever single tick follows each gap."""
    eng, clock = _engine()
    eng.cooldown_seconds = 1000.0
    eng.infer_state(_feat("neutral"))
    eng.reset_for_signal_loss()
    first_three = [eng.infer_state(_feat("stressed")) for _ in range(3)]
    assert [s.label for s in first_three] == ["no_signal"] * 3
    assert all("persistence" in s.reason for s in first_three)
    # The fourth commits, with no cooldown standing in the way.
    assert eng.infer_state(_feat("stressed")).label == "stressed"


def test_losing_signal_quality_applies_at_once_and_regaining_it_needs_persistence():
    """insufficient_signal is a statement about the signal, not the
    student. Made to compete for persistence, a confidence oscillating
    across the gate froze the last content label for the session."""
    eng, clock = _engine()
    eng.infer_state(_feat("neutral"))
    weak = {**_feat("neutral"), "confidence": 30.0}
    assert eng.infer_state(weak).label == "insufficient_signal"
    for _ in range(10):
        assert eng.infer_state(_feat("neutral")).label == "insufficient_signal"
        assert eng.infer_state(weak).label == "insufficient_signal"
    for _ in range(3):
        assert eng.infer_state(_feat("neutral")).label == "insufficient_signal"
    assert eng.infer_state(_feat("neutral")).label == "neutral"


def test_a_change_of_mind_mid_run_restarts_the_count():
    eng, clock = _engine()
    eng.infer_state(_feat("neutral"))
    for _ in range(3):
        eng.infer_state(_feat("focused"))
    for _ in range(3):
        eng.infer_state(_feat("stressed"))
    assert eng.infer_state(_feat("focused")).label == "neutral"


# -- the baseline restarts when recording is armed ------------------------------

def test_restart_baseline_gathers_a_fresh_reference_from_the_next_ticks():
    """The stream is up from Connect; arming recording on the first question
    restarts the baseline so the reference is not the strap being adjusted."""
    t = Ticker()
    settling = {**ENGAGED, **CONTACT_GOOD}  # muscle-high opening stretch
    _run_until_latched(t, settling)
    first_mean = t.processor._baseline_focus_mean
    t.processor.restart_baseline()
    assert t.processor._baseline_ready, "the old centre stays in use until the new one latches"
    resting = {**RELAXED, **CONTACT_GOOD}
    n = 0
    while t.processor._baseline_focus_mean == first_mean:
        t.tick(resting)
        n += 1
        assert n < 400
    assert t.processor._baseline_focus_mean != first_mean
    settled = t.run(resting, 4 * int(SignalProcessor.BASELINE_RAMP_SECONDS) + 8)
    assert settled["focus_score"] == pytest.approx(50.0, abs=1.0)


def test_a_restart_ramps_from_the_centre_in_use_without_a_step():
    t = Ticker()
    _run_until_latched(t, {**ENGAGED, **CONTACT_GOOD})
    t.processor.restart_baseline()
    scores = []
    for _ in range(4 * int(SignalProcessor.BASELINE_SECONDS + SignalProcessor.BASELINE_RAMP_SECONDS) + 20):
        scores.append(t.tick({**RELAXED, **CONTACT_GOOD})["focus_score"])
    # The first ten seconds are the smoother tracking the change of spectrum,
    # which is a real move; what must not step is the latch, ~45 s in.
    settled = scores[40:]
    steps = [abs(b - a) for a, b in zip(settled, settled[1:])]
    # The two spectra here sit further apart than the whole scale, so the
    # ramp moves the centre by ~130 points over its 40 ticks: ~3.3 a tick,
    # against the 130 a single step would be.
    assert max(steps) < 4.0
    assert scores[-1] == pytest.approx(50.0, abs=1.0)
    assert scores[40] < 10.0, "before the new latch the old centre still applies"


def test_a_tick_with_no_delta_still_scores_and_does_not_feed_the_gate():
    """delta is not among the bands the ratios read, so band features can be
    usable with delta absent or null. That must cost the delta gate its
    reference for that tick, not the tick."""
    t = Ticker()
    _warm(t, 12)
    for bands in ({**RELAXED, "delta": None, **CONTACT_GOOD},
                  {k: v for k, v in {**RELAXED, **CONTACT_GOOD}.items() if k != "delta"},
                  {**RELAXED, "delta": "n/a", **CONTACT_GOOD}):
        f = t.tick(bands)
        assert 0.0 <= f["focus_score"] <= 100.0
        assert f["artifact_reason"] is None
    assert len(t.processor._delta_history) == 12


def test_a_stream_with_no_delta_still_holds_a_clench_and_a_spread_jump():
    """Each gate waits for its own history. Keyed on delta's, a stream that
    never reports delta disabled all three and reported zero artifacts."""
    no_delta = {k: v for k, v in RELAXED.items() if k != "delta"}
    t = Ticker()
    t.run({**no_delta, **CONTACT_GOOD}, 20, spread=20.0)
    clench = t.tick({**no_delta, "beta": 0.3, "gamma": 1.0, **CONTACT_GOOD}, spread=20.0)
    assert clench["artifact_reason"] == "emg_gamma"
    jump = t.tick({**no_delta, **CONTACT_GOOD}, spread=90.0)
    assert jump["artifact_reason"] == "spread_jump"


def test_a_signal_gap_does_not_rebaseline_from_the_recovery_stretch():
    """reset() is what the stream manager calls on a no-sample tick. After
    a latch, a gap followed by a muscle-heavy re-fitting stretch must leave
    the reference where the session put it."""
    t = Ticker()
    _run_until_latched(t, {**RELAXED, **CONTACT_GOOD})
    before = t.processor._baseline_focus_mean
    t.processor.reset()
    t.run({**ENGAGED, **CONTACT_GOOD}, 4 * int(SignalProcessor.BASELINE_SECONDS) + 20)
    assert t.processor._baseline_focus_mean == before


# -- third review: gaps, coverage, and the degraded regime ---------------------

def test_contact_flapping_every_other_tick_still_commits_a_label():
    """A gap tick is not a reading. Clearing the pending run on every
    signal-loss reset meant gap, reading, gap, reading never reached four
    and a focused student read no_signal for as long as it lasted."""
    eng, clock = _engine()
    labels = []
    for _ in range(6):
        eng.reset_for_signal_loss()
        clock[0] += 0.25
        labels.append(eng.infer_state(_feat("focused")).label)
        clock[0] += 0.25
    assert labels[:3] == ["no_signal"] * 3
    assert labels[3] == "focused"


def test_a_pending_run_does_not_survive_a_long_gap():
    """Three readings, a minute of nothing, one reading: that one is a
    first reading, not a fourth."""
    eng, clock = _engine()
    eng.infer_state(_feat("neutral"))
    for _ in range(3):
        clock[0] += 0.25
        assert eng.infer_state(_feat("focused")).label == "neutral"
    eng.reset_for_signal_loss()
    clock[0] += 60.0
    assert eng.infer_state(_feat("focused")).label == "no_signal"


def test_the_baseline_counts_covered_seconds_so_a_gap_is_worth_one():
    """21 ticks, a ten-minute gap, one tick: elapsed time said 45 s had
    passed and latched on 22 samples. Covered time says 6 s have."""
    t = Ticker()
    bands = {**RELAXED, "hsi": [1.0, 1.0, 4.0, 4.0], "is_good": [1.0, 1.0, 0.0, 0.0]}
    t.run(bands, 21)
    t.now += 600.0
    t.tick(bands)
    assert not t.processor._baseline_ready
    assert t.processor._baseline_coverage == pytest.approx(5.0 + SignalProcessor.BASELINE_TICK_CAP_SECONDS)
    n = _run_until_latched(t, bands)
    assert n >= 4 * (SignalProcessor.BASELINE_SECONDS - 6.0) - 2


def test_reset_keeps_the_contact_history_so_one_blip_after_a_gap_is_not_good_contact():
    """The histories are time-windowed and prune themselves. Cleared on
    reset, the first frame after a gap was judged on itself: one blip with
    every electrode good read contact 1.0 and entered the baseline."""
    t = Ticker()
    t.run({**RELAXED, **CONTACT_POOR}, 20)
    assert t.processor._baseline_focus == []
    t.processor.reset()
    blip = t.tick({**RELAXED, **CONTACT_GOOD})
    assert blip["contact_ratio"] < SignalProcessor.CONTACT_DEGRADED
    assert blip["signal_quality"] == "poor"
    assert t.processor._baseline_focus == []


def test_degraded_contact_clears_the_gate_whatever_the_spectrum_does():
    """Two of four electrodes is the ordinary state. On a ramp from zero
    at the degraded line it sat at exactly 50 on a constant spectrum and
    fell under 45 on any jitter -- the operating regime gated on luck."""
    degraded = {"hsi": [1.0, 1.0, 4.0, 4.0], "is_good": [1.0, 1.0, 0.0, 0.0]}
    t = Ticker()
    for i in range(30):
        f = t.tick({**(RELAXED if i % 2 else ENGAGED), **degraded})
    assert f["contact_ratio"] == pytest.approx(0.5)
    assert f["signal_quality"] == "degraded"
    assert f["confidence"] >= 45.0
    poor = Ticker()
    for i in range(30):
        p = poor.tick({**(RELAXED if i % 2 else ENGAGED), **CONTACT_POOR})
    assert p["confidence"] < 45.0


def test_good_is_reachable_on_a_bridge_that_reports_no_contact():
    """With no contact data confidence tops out at 0.70, and the heuristic
    asked for 0.75, so good was unreachable on that path."""
    f = Ticker().run(RELAXED, 30)  # no hsi / is_good at all, before the latch
    assert f["quality_basis"] == "heuristic"
    assert f["signal_quality"] == "good"


# -- fifth review: what a gap keeps, and what a session end forgets ------------

def test_a_gap_does_not_disarm_the_artifact_gate():
    """The delta and spread gates need ARTIFACT_MIN_HISTORY usable ticks.
    Cleared on every no-sample tick, flapping contact kept them from ever
    reaching it: 0 of 20 blinks held with a reset every fifth tick."""
    t = Ticker()
    held = 0
    for i in range(100):
        if i % 5 == 4:
            t.processor.reset()
            continue
        if i % 5 == 3 and i > 20:
            f = t.tick({**RELAXED, "delta": RELAXED["delta"] + 0.6, **CONTACT_GOOD})
            held += f["artifact_reason"] == "delta_jump"
        else:
            t.tick({**RELAXED, **CONTACT_GOOD})
    assert held >= 12


def test_the_held_and_rejected_counts_are_session_totals_across_gaps():
    t = Ticker()
    _warm(t)
    t.tick({**RELAXED, "delta": 1.5, **CONTACT_GOOD})
    t.tick({**RELAXED, "hsi": [4.0] * 4, "is_good": [0.0] * 4})
    t.processor.reset()
    f = t.tick({**RELAXED, **CONTACT_GOOD})
    assert f["samples_artifact"] == 1 and f["samples_rejected"] == 1
    t.processor.clear_session()
    f = t.tick({**RELAXED, **CONTACT_GOOD})
    assert f["samples_artifact"] == 0 and f["samples_rejected"] == 0


def test_clear_session_forgets_the_baseline_where_reset_keeps_it():
    """stop() is a real end of session; on a shared station the next
    student must not be scored against the last one's resting spectrum."""
    t = Ticker()
    _run_until_latched(t, {**ENGAGED, **CONTACT_GOOD})
    t.processor.reset()
    assert t.processor._baseline_ready
    t.processor.clear_session()
    assert not t.processor._baseline_ready
    assert t.processor._baseline_focus_mean is None
    assert t.processor._centre_from == {"focus": None, "calm": None}
    assert len(t.processor._is_good_history) == 0
    fresh = t.tick({**ENGAGED, **CONTACT_GOOD})
    assert fresh["focus_score"] > 60.0, "scored against the population midpoint, not the old mean"


def test_the_smoothed_ratios_are_null_on_a_tick_with_no_bands():
    t = Ticker()
    _warm(t)
    f = t.tick(CONTACT_GOOD)  # contact only, no bands
    assert f["focus_log_ratio"] is None
    assert f["focus_log_ratio_smoothed"] is None and f["calm_log_ratio_smoothed"] is None


def test_a_sample_clock_that_goes_backwards_does_not_freeze_the_ramp():
    """A device clock that rebases on reconnect. The clamp read a negative
    age as fraction 0 and held every later score at the ramp's start."""
    t = Ticker()
    _run_until_latched(t, {**ENGAGED, **CONTACT_GOOD})
    p = t.processor
    settled = p._centre("focus", p._baseline_latched + timedelta(seconds=60))
    assert settled == pytest.approx(p._baseline_focus_mean)
    assert p._centre("focus", p._baseline_latched - timedelta(seconds=600)) == pytest.approx(settled)


def test_arming_restarts_the_label_engine_too():
    """The label, its cooldown and its pending run were carried in from
    pairing, so a lesson opened on a label formed while the strap was
    being fitted, beside scores that had been re-centred."""
    import inspect
    from src.app.services.stream_manager import StreamManager
    eng, clock = _engine()
    eng.infer_state(_feat("neutral"))
    clock[0] = 10.0
    for _ in range(4):
        eng.infer_state(_feat("focused"))
    assert eng.last_label == "focused"
    eng.restart()
    assert eng.last_label == "neutral" and eng.last_change_ts == float("-inf")
    assert eng._pending_label is None and eng._pending_ts is None
    assert "adaptation.restart()" in inspect.getsource(StreamManager.arm_baseline)


def test_a_fourth_artifact_reason_survives_the_envelope():
    from src.app.schemas import FeatureData
    f = FeatureData(focus_score=50.0, calm_score=50.0, confidence=70.0,
                    signal_quality="degraded", artifact_reason="something_new")
    assert f.artifact_reason == "something_new"
