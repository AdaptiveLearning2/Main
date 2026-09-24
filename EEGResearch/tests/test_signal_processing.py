"""SignalProcessor rules derived from the reference captures (tests/fixtures/EEG_REFERENCE.md)."""

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
    """Feeds samples at a fixed rate on a clock shared with the processor."""

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
    """Raw spread depends on the strap fitting (EEG_REFERENCE.md), not the brain."""
    bands = {**RELAXED, **CONTACT_GOOD}
    tight = Ticker().run(bands, 30, level=800.0, spread=20.0)
    loose = Ticker().run(bands, 30, level=950.0, spread=150.0)
    assert tight["focus_score"] == pytest.approx(loose["focus_score"])
    assert tight["calm_score"] == pytest.approx(loose["calm_score"])


def test_without_bands_the_amplitude_fallback_still_scores():
    """An older bridge reports no band powers."""
    tight = Ticker().run(None, 30, spread=20.0)
    loose = Ticker().run(None, 30, spread=150.0)
    assert tight["calm_score"] > loose["calm_score"]
    assert 0.0 <= tight["focus_score"] <= 100.0


# -- 1.2 confidence is signal quality, and calm is not in it ------------------

def test_a_stressed_spectrum_on_good_contact_is_not_low_confidence():
    """Same contact, same stability: same confidence, whatever the calm."""
    relaxed = Ticker().run({**RELAXED, **CONTACT_GOOD}, 30)
    aroused = Ticker().run({**ENGAGED, "alpha": -0.6, "gamma": 0.7, **CONTACT_GOOD}, 30)
    assert aroused["calm_score"] < relaxed["calm_score"] - 20
    assert aroused["confidence"] == pytest.approx(relaxed["confidence"], abs=1.0)


def test_poor_contact_takes_a_steady_signal_below_the_gate_and_degraded_does_not():
    """The 0.45 gate in adaptation.py and signal_fusion.py must mean something about the electrodes."""
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
    """One smoothed contact per tick feeds both, so they cannot disagree."""
    f = Ticker().run({**RELAXED, **CONTACT_POOR}, 30)
    assert f["signal_quality"] == "poor" and f["quality_basis"] == "contact"
    assert f["contact_ratio"] is not None and f["contact_ratio"] < 0.4
    g = Ticker().run(RELAXED, 30)  # no contact data at all
    assert g["contact_ratio"] is None and g["quality_basis"] == "heuristic"


# -- 1.4 the artifact gate holds, never writes ---------------------------------

def _warm(t: Ticker, ticks: int = 20):
    return t.run({**RELAXED, **CONTACT_GOOD}, ticks)


def test_a_delta_spike_holds_the_previous_scores_and_is_counted_not_rejected():
    """A blink doubles delta; the tick holds the last admitted scores and says so."""
    t = Ticker()
    before = _warm(t)
    blink = t.tick({**ENGAGED, "delta": RELAXED["delta"] + 0.6, **CONTACT_GOOD})
    assert blink["artifact_reason"] == "delta_jump"
    assert blink["samples_artifact"] == 1
    assert blink["samples_rejected"] == before["samples_rejected"]
    assert blink["focus_score"] == pytest.approx(before["focus_score"])
    assert blink["calm_score"] == pytest.approx(before["calm_score"])
    # Contact is still judged on the held tick.
    assert blink["signal_quality"] == "good" and blink["quality_basis"] == "contact"


def test_a_jaw_clench_is_held_on_gamma_exceeding_beta():
    t = Ticker()
    before = _warm(t)
    clench = t.tick({**RELAXED, "beta": 0.3, "gamma": 1.0, **CONTACT_GOOD})
    assert clench["artifact_reason"] == "emg_gamma"
    assert clench["calm_score"] == pytest.approx(before["calm_score"])


def test_a_spread_jump_is_relative_to_the_sessions_own_spread():
    """Rest spread varies ~6x between fittings, so an absolute bound fits nobody."""
    tight = Ticker()
    tight.run({**RELAXED, **CONTACT_GOOD}, 20, spread=20.0)
    assert tight.tick({**RELAXED, **CONTACT_GOOD}, spread=80.0)["artifact_reason"] == "spread_jump"
    loose = Ticker()
    loose.run({**RELAXED, **CONTACT_GOOD}, 20, spread=150.0)
    assert loose.tick({**RELAXED, **CONTACT_GOOD}, spread=200.0)["artifact_reason"] is None


def test_held_ticks_enter_neither_the_baseline_nor_the_window():
    # Wider than the warm-up: at maxlen a deque's length can't show an added tick.
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
    # A large non-artifact swing for one tick, then back.
    spike = t.tick({**ENGAGED, **CONTACT_GOOD})
    back = t.tick({**RELAXED, **CONTACT_GOOD})
    raw_move = abs(spike["focus_log_ratio"] - steady["focus_log_ratio"])
    smooth_move = abs(spike["focus_log_ratio_smoothed"] - steady["focus_log_ratio_smoothed"])
    assert smooth_move < 0.15 * raw_move
    assert abs(spike["focus_score"] - steady["focus_score"]) < 8.0
    assert abs(back["focus_score"] - steady["focus_score"]) < 8.0


def test_a_sustained_change_converges_and_within_the_deciders_cadence():
    """The topic decider reads every ~10 s: a step must cross calm < 35 inside 40 ticks at 4 Hz."""
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
    """On poor contact nothing is collected and the 45 s have not begun."""
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
    """The centre ramps to the session mean over BASELINE_RAMP_SECONDS, so the latch never jumps."""
    t = Ticker()
    bands = {**ENGAGED, **CONTACT_GOOD}  # far from the population midpoint
    scores = []
    for _ in range(4 * int(SignalProcessor.BASELINE_SECONDS + SignalProcessor.BASELINE_RAMP_SECONDS) + 40):
        scores.append(t.tick(bands)["focus_score"])
    assert t.processor._baseline_ready
    steps = [abs(b - a) for a, b in zip(scores, scores[1:])]
    assert max(steps) < 3.0
    # It did move: from the population reading to the session's own 50.
    assert abs(scores[0] - scores[-1]) > 10.0
    assert scores[-1] == pytest.approx(50.0, abs=1.0)


def test_the_gain_is_the_same_on_both_sides_of_the_latch():
    # Before: a fresh processor on the population path.
    fresh = Ticker()
    ts0 = fresh.sample().timestamp
    p0 = fresh.processor
    before = (p0._score_against_baseline(0.3, "focus", ts0)
              - p0._score_against_baseline(0.0, "focus", ts0))
    # After: a collected baseline past its ramp (omitting the timestamp skips the ramp).
    t = Ticker()
    _run_until_latched(t, {**ENGAGED, **CONTACT_GOOD})
    # Ramp progress needs admitted ticks, not just a later timestamp; mid-ramp one score would clamp.
    t.run({**ENGAGED, **CONTACT_GOOD}, 4 * int(SignalProcessor.BASELINE_RAMP_SECONDS) + 8)
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
    """The cooldown must not hold one spurious tick as the label (EEG_REFERENCE.md)."""
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
    # Pinned, not read from the attribute, which would go vacuous at 1.
    assert eng.persist_ticks == 4
    clock[0] = 10.0
    eng.infer_state(_feat("neutral"))
    labels = []
    for _ in range(4):
        labels.append(eng.infer_state(_feat("focused")).label)
        clock[0] += 0.25
    assert labels == ["neutral", "neutral", "neutral", "focused"]
    # Persistence is met after four neutral ticks, but the cooldown holds focused until it lapses.
    held = [eng.infer_state(_feat("neutral")) for _ in range(eng.persist_ticks + 2)]
    assert all(h.label == "focused" for h in held)
    assert "Cooldown" in held[-1].reason
    clock[0] += eng.cooldown_seconds
    assert eng.infer_state(_feat("neutral")).label == "neutral"


def test_after_signal_loss_a_label_still_needs_persistence_but_not_the_cooldown():
    """Otherwise flapping contact commits whatever single tick follows each gap."""
    eng, clock = _engine()
    eng.cooldown_seconds = 1000.0
    eng.infer_state(_feat("neutral"))
    eng.reset_for_signal_loss()
    first_three = [eng.infer_state(_feat("stressed")) for _ in range(3)]
    assert [s.label for s in first_three] == ["no_signal"] * 3
    assert all("persistence" in s.reason for s in first_three)
    # The fourth commits, with no cooldown.
    assert eng.infer_state(_feat("stressed")).label == "stressed"


def test_losing_signal_quality_applies_at_once_and_regaining_it_needs_persistence():
    """insufficient_signal is about the signal, not the student, so it doesn't compete for persistence."""
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
    """Arming on the first question keeps strap adjustment out of the reference."""
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
    # Skip the smoother's first 10 s (a real move); the latch ~45 s in must not step.
    settled = scores[40:]
    steps = [abs(b - a) for a, b in zip(settled, settled[1:])]
    # The ramp moves the centre ~130 points over 40 ticks: ~3.3 a tick, not one 130 step.
    assert max(steps) < 4.0
    assert scores[-1] == pytest.approx(50.0, abs=1.0)
    assert scores[40] < 10.0, "before the new latch the old centre still applies"


def test_a_tick_with_no_delta_still_scores_and_does_not_feed_the_gate():
    """The ratios don't read delta, so a missing delta costs only the delta gate's reference."""
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
    """Each gate waits for its own history, not delta's."""
    no_delta = {k: v for k, v in RELAXED.items() if k != "delta"}
    t = Ticker()
    t.run({**no_delta, **CONTACT_GOOD}, 20, spread=20.0)
    clench = t.tick({**no_delta, "beta": 0.3, "gamma": 1.0, **CONTACT_GOOD}, spread=20.0)
    assert clench["artifact_reason"] == "emg_gamma"
    jump = t.tick({**no_delta, **CONTACT_GOOD}, spread=90.0)
    assert jump["artifact_reason"] == "spread_jump"


def test_a_signal_gap_does_not_rebaseline_from_the_recovery_stretch():
    """reset() runs on each no-sample tick; a re-fitting stretch after it must not move the reference."""
    t = Ticker()
    _run_until_latched(t, {**RELAXED, **CONTACT_GOOD})
    before = t.processor._baseline_focus_mean
    t.processor.reset()
    t.run({**ENGAGED, **CONTACT_GOOD}, 4 * int(SignalProcessor.BASELINE_SECONDS) + 20)
    assert t.processor._baseline_focus_mean == before


# -- gaps, coverage, and the degraded regime -----------------------------------

def test_contact_flapping_every_other_tick_still_commits_a_label():
    """A gap tick is not a reading, so it must not clear the pending run."""
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
    eng, clock = _engine()
    eng.infer_state(_feat("neutral"))
    for _ in range(3):
        clock[0] += 0.25
        assert eng.infer_state(_feat("focused")).label == "neutral"
    eng.reset_for_signal_loss()
    clock[0] += 60.0
    assert eng.infer_state(_feat("focused")).label == "no_signal"


def test_the_baseline_counts_covered_seconds_so_a_gap_is_worth_one():
    """21 ticks, a ten-minute gap, one tick: covered time is 6 s, not 45."""
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
    """The histories are time-windowed and prune themselves, so reset keeps them."""
    t = Ticker()
    t.run({**RELAXED, **CONTACT_POOR}, 20)
    assert len(t.processor._baseline_focus) == 0
    t.processor.reset()
    blip = t.tick({**RELAXED, **CONTACT_GOOD})
    assert blip["contact_ratio"] < SignalProcessor.CONTACT_DEGRADED
    assert blip["signal_quality"] == "poor"
    assert len(t.processor._baseline_focus) == 0


def test_degraded_contact_clears_the_gate_whatever_the_spectrum_does():
    """Two of four electrodes is the ordinary state and must clear the 45 gate under jitter."""
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
    """With no contact data, confidence tops out at 0.70."""
    f = Ticker().run(RELAXED, 30)  # no hsi / is_good at all, before the latch
    assert f["quality_basis"] == "heuristic"
    assert f["signal_quality"] == "good"


# -- what a gap keeps, and what a session end forgets --------------------------

def test_a_gap_does_not_disarm_the_artifact_gate():
    """The gates need ARTIFACT_MIN_HISTORY ticks, which flapping contact must not keep clearing."""
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
    """On a shared station the next student must not be scored against the last one's baseline."""
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
    """A device clock that rebases on reconnect must not hold the ramp at its start."""
    t = Ticker()
    bands = {**ENGAGED, **CONTACT_GOOD}
    _run_until_latched(t, bands)
    # Two seconds into the ramp, the clock rebases ten minutes back.
    scores = [t.tick(bands)["focus_score"] for _ in range(8)]
    t.now -= 600.0
    scores += [t.tick(bands)["focus_score"] for _ in range(8)]
    steps = [abs(b - a) for a, b in zip(scores, scores[1:])]
    # Neither frozen nor jumped past the ramp's ~3.3-point per-tick move.
    assert max(steps) < 4.0
    assert scores[-1] != pytest.approx(scores[7], abs=0.5)
    ramped = t.run(bands, 4 * int(SignalProcessor.BASELINE_RAMP_SECONDS))
    assert ramped["focus_score"] == pytest.approx(50.0, abs=1.0)


def test_arming_restarts_the_label_engine_too():
    """A lesson must not open on a label formed while the strap was being fitted."""
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


# -- session ends on push, and what the medians remember -----------------------

def test_end_session_forgets_the_pending_run_where_a_gap_keeps_it():
    """The last student's run must not count toward the next one's first label."""
    eng, clock = _engine()
    eng.infer_state(_feat("neutral"))
    for _ in range(3):
        clock[0] += 0.25
        eng.infer_state(_feat("focused"))
    eng.end_session()
    assert eng.last_label == "no_signal" and eng._pending_label is None
    clock[0] += 0.25
    assert eng.infer_state(_feat("focused")).label == "no_signal", "a first reading, not a fourth"


def test_stop_and_the_push_session_end_both_reach_clear_session_and_end_session():
    """push/stop is push's only session end; the stream stays up."""
    import inspect
    from src.app import main as sidecar_main
    from src.app.services.stream_manager import DeviceSession, StreamManager
    stop_src = inspect.getsource(DeviceSession.stop)
    assert "processor.clear_session()" in stop_src and "adaptation.end_session()" in stop_src
    end_src = inspect.getsource(StreamManager.end_session)
    assert "processor.clear_session()" in end_src and "adaptation.end_session()" in end_src
    assert "stream_manager.end_session()" in inspect.getsource(sidecar_main.push_stop)


def test_the_artifact_medians_expire_by_wall_clock_not_by_count():
    """After a long gap, a refit must not be judged against the strap as it was."""
    t = Ticker()
    t.run({**RELAXED, **CONTACT_GOOD}, 40, spread=20.0)
    t.processor.reset()
    t.now += 600.0
    # 4x the old spread: every tick would jump against the old median.
    held = sum(t.tick({**RELAXED, **CONTACT_GOOD}, spread=80.0)["artifact_reason"] == "spread_jump"
               for _ in range(8))
    assert held == 0


# -- bounds, NaN, a stalled clock, and a faster stream -------------------------

def test_a_nan_band_is_a_tick_with_no_bands_not_a_dead_headband():
    """NaN and inf pass float(); raising later would read as no data and reset every tick."""
    t = Ticker()
    _warm(t)
    for bad in (float("nan"), float("inf"), -float("inf")):
        f = t.tick({**RELAXED, "alpha": bad, **CONTACT_GOOD})
        assert f["focus_log_ratio"] is None
        assert 0.0 <= f["focus_score"] <= 100.0


def test_the_population_bounds_bracket_the_reference_capture():
    """The bounds are the population scale either side of the latch; values are per-segment capture ratios."""
    lo, hi = SignalProcessor.FOCUS_LOG_RATIO_MIN, SignalProcessor.FOCUS_LOG_RATIO_MAX
    for segment_ratio in (-1.53, -1.37, -0.91, -0.83, -0.21):
        assert lo < segment_ratio < hi
    clo, chi = SignalProcessor.CALM_LOG_RATIO_MIN, SignalProcessor.CALM_LOG_RATIO_MAX
    for segment_ratio in (-0.87, -0.18, 0.29, 0.47):
        assert clo < segment_ratio < chi
    # Pre-latch eyes-closed scores low, not at the floor.
    eyes_closed = {"delta": 0.5, "theta": 0.2, "alpha": 0.15, "beta": -0.2, "gamma": -0.4}
    f = Ticker().run({**eyes_closed, **CONTACT_GOOD}, 30)
    assert 0.0 < f["focus_score"] < 40.0


def test_the_push_session_end_resets_the_heart_channel_too():
    import inspect
    from src.app.services.stream_manager import StreamManager
    assert "_reset_heart()" in inspect.getsource(StreamManager.end_session)


def test_a_stalled_sample_clock_still_latches_and_the_lists_stay_bounded():
    t = Ticker()
    bands = {**RELAXED, **CONTACT_GOOD}
    sample = t.sample()
    for _ in range(3000):
        t.processor.update(sample, bands)  # the same timestamp every tick
        t.now += 0.25
    assert t.processor._baseline_ready
    assert len(t.processor._baseline_focus) <= SignalProcessor.BASELINE_MAX_SAMPLES


def test_the_artifact_window_is_twenty_seconds_at_sixteen_hertz_too():
    t = Ticker(hz=16.0)
    t.run({**RELAXED, **CONTACT_GOOD}, 16 * 15)  # 15 s
    assert len(t.processor._delta_history) == 16 * 15
    t.run({**RELAXED, **CONTACT_GOOD}, 16 * 10)  # 25 s in: pruned to 20 s
    t.tick({**RELAXED, **CONTACT_GOOD})
    assert 16 * 19 <= len(t.processor._delta_history) <= 16 * 20 + 1


# -- what push/stop may end, malformed bands, a frozen clock -------------------

def test_push_stop_ends_a_session_only_if_one_was_pushing():
    """The page fires it from pagehide; under pull it must not wipe an armed baseline."""
    import inspect
    from src.app import main as sidecar_main
    src = inspect.getsource(sidecar_main.push_stop)
    assert "push_client.session_id is not None" in src
    assert src.index("was_pushing = ") < src.index("await push_client.stop()")
    assert "if was_pushing:" in src and "stream_manager.end_session()" in src


def test_a_malformed_band_is_a_held_tick_below_the_gate_not_a_no_bands_tick():
    """A malformed tick must not look like a genuine no-bands tick above the fusion gate."""
    t = Ticker()
    before = _warm(t)
    nan = t.tick({**RELAXED, "alpha": float("nan"), **CONTACT_GOOD})
    assert nan["artifact_reason"] == "malformed_bands"
    assert nan["focus_score"] == pytest.approx(before["focus_score"])
    assert nan["confidence"] < 45.0
    honest = Ticker().run(CONTACT_GOOD, 21)  # a bridge with no bands at all
    assert honest["artifact_reason"] is None and honest["confidence"] >= 45.0


def test_a_frozen_clock_latches_a_baseline_that_is_applied():
    """A frozen clock must advance the ramp as well as the coverage."""
    t = Ticker()
    bands = {**ENGAGED, **CONTACT_GOOD}
    sample = t.sample()
    f = None
    for _ in range(600):
        f = t.processor.update(sample, bands)
        t.now += 0.25
    assert t.processor._baseline_ready
    assert f["focus_score"] == pytest.approx(50.0, abs=1.0)


def test_the_calm_midpoint_did_not_move_so_strap_settling_is_not_stressed():
    """The capture's strap-settling segment (calm log-ratio -0.87) must stay above the stressed line."""
    lo, hi = SignalProcessor.CALM_LOG_RATIO_MIN, SignalProcessor.CALM_LOG_RATIO_MAX
    assert (lo + hi) / 2.0 == pytest.approx(-0.57, abs=0.01)
    p = SignalProcessor()
    assert p._score_against_baseline(-0.87, "calm") >= 0.377


def test_the_label_lines_are_the_same_bels_as_before_and_match_the_backend():
    """0.322 Bels above centre for focused, 0.312 below for stressed; signal_fusion carries the same literals."""
    import inspect
    from src.app.services.adaptation import AdaptationEngine
    src = inspect.getsource(AdaptationEngine.infer_state)
    from src.app.services.adaptation import STRESSED_CALM_MAX
    assert "focus_ratio >= 0.624" in src and "calm_ratio < stressed_line" in src
    assert STRESSED_CALM_MAX["sdk"] == 0.377
    f_span = SignalProcessor.FOCUS_LOG_RATIO_MAX - SignalProcessor.FOCUS_LOG_RATIO_MIN
    c_span = SignalProcessor.CALM_LOG_RATIO_MAX - SignalProcessor.CALM_LOG_RATIO_MIN
    assert (0.624 - 0.5) * f_span == pytest.approx(0.322, abs=0.002)
    assert (0.5 - 0.377) * c_span == pytest.approx(0.312, abs=0.002)


def test_the_push_session_end_clears_the_optical_buffer_without_disconnecting():
    import inspect
    from src.app.services.eeg_ingestion import TcpMuseBridgeAdapter
    from src.app.services.stream_manager import StreamManager
    assert "clear_optics" in inspect.getsource(StreamManager.end_session)
    assert hasattr(TcpMuseBridgeAdapter, "clear_optics")
    assert "disconnect" not in inspect.getsource(StreamManager.end_session)


# -- what a malformed tick may reach, and what counts as one --------------------

def test_a_nan_band_serialises_out_of_the_state_endpoint():
    """The JSON renderer refuses non-finite floats, so they must be nulled first."""
    from src.app.schemas import BandData
    from src.app.services.stream_manager import _finite_or_none
    assert _finite_or_none(float("nan")) is None
    assert _finite_or_none(float("inf")) is None
    assert _finite_or_none("x") is None
    assert _finite_or_none(0.25) == 0.25
    bands = BandData(delta=None, theta=0.1, alpha=0.5, beta=0.1, gamma=0.05)
    assert bands.model_dump()["delta"] is None


def test_a_nan_in_delta_costs_the_blink_gate_its_reference_and_nothing_else():
    """The ratio bands are fine, so the tick scores; NaN has no ordering, so it skips the median."""
    t = Ticker()
    before = _warm(t)
    n = len(t.processor._delta_history)
    f = t.tick({**ENGAGED, "delta": float("nan"), **CONTACT_GOOD})
    assert f["artifact_reason"] is None
    assert f["focus_score"] != pytest.approx(before["focus_score"]), "scored, not held"
    assert len(t.processor._delta_history) == n
    assert all(v == v for _, v in t.processor._delta_history)
    t.run({**ENGAGED, "delta": float("nan"), **CONTACT_GOOD}, 400)
    assert t.processor._baseline_ready


def test_a_partial_band_dict_is_malformed_not_zero_bels():
    t = Ticker()
    before = _warm(t)
    partial = {k: v for k, v in RELAXED.items() if k != "alpha"}
    f = t.tick({**partial, **CONTACT_GOOD})
    assert f["artifact_reason"] == "malformed_bands"
    assert f["focus_score"] == pytest.approx(before["focus_score"])
    # No ratio bands at all is the fallback, not malformed.
    assert Ticker().run(CONTACT_GOOD, 5)["artifact_reason"] is None


def test_malformed_ticks_do_not_starve_the_spread_gate():
    """The spread comes from the raw channels, so a malformed band tick still feeds it."""
    t = Ticker()
    t.run({**RELAXED, **CONTACT_GOOD}, 8, spread=20.0)
    partial = {k: v for k, v in RELAXED.items() if k != "alpha"}
    t.run({**partial, **CONTACT_GOOD}, 100, spread=20.0)  # 25 s malformed
    assert t.tick({**RELAXED, **CONTACT_GOOD}, spread=90.0)["artifact_reason"] == "spread_jump"


def test_an_absent_band_is_published_as_null_not_zero():
    """A missing band is not a measurement of 0 Bels."""
    from src.app.services.stream_manager import _finite_or_none
    assert _finite_or_none(None) is None
    import inspect
    from src.app.services.stream_manager import DeviceSession
    src = inspect.getsource(DeviceSession.snapshot)
    assert 'raw_meta.get(name)' in src and 'raw_meta.get(name, 0.0)' not in src


def test_the_push_client_accounts_for_samples_the_backend_could_not_read():
    """Otherwise the backend's `malformed` count turns a loud 422 into a silent 200."""
    import inspect
    from src.app.services.push_client import PushClient
    src = inspect.getsource(PushClient)
    assert 'body.get("malformed", 0)' in src
    assert '"malformed": dict(self._malformed)' in src


def test_a_nan_channel_spread_never_reaches_the_spread_median():
    t = Ticker()
    t.run({**RELAXED, **CONTACT_GOOD}, 20, spread=20.0)
    n = len(t.processor._spread_history)
    t.tick({**RELAXED, **CONTACT_GOOD}, spread=float("nan"))
    assert len(t.processor._spread_history) == n
    assert t.tick({**RELAXED, **CONTACT_GOOD}, spread=900.0)["artifact_reason"] == "spread_jump"


def test_ticks_the_blink_gate_had_no_delta_for_are_counted():
    """Uncounted, a never-armed blink gate reads as a flawless recording."""
    t = Ticker()
    _warm(t)
    for bands in ({**RELAXED, "delta": float("nan")}, {k: v for k, v in RELAXED.items() if k != "delta"}):
        f = t.tick({**bands, **CONTACT_GOOD})
    assert f["samples_no_delta"] == 2 and f["samples_artifact"] == 0
    t.processor.clear_session()
    assert t.tick({**RELAXED, **CONTACT_GOOD})["samples_no_delta"] == 0


def test_ticks_the_spread_gate_had_no_spread_for_are_counted():
    """A non-finite channel drops the spread; a single-electrode frame is a contact fact, not counted."""
    t = Ticker()
    _warm(t)
    f = t.tick({**RELAXED, **CONTACT_GOOD}, spread=float("nan"))
    assert f["samples_no_spread"] == 1 and f["artifact_reason"] is None
    one_electrode = t.tick({**RELAXED, "hsi": [1.0, 4.0, 4.0, 4.0], "is_good": [1.0, 0.0, 0.0, 0.0]})
    assert one_electrode["samples_no_spread"] == 1
    t.processor.clear_session()
    assert t.tick({**RELAXED, **CONTACT_GOOD})["samples_no_spread"] == 0


# -- calm from the local spectrum, behind EEG_SPECTRUM_SOURCE -------------------

def _spectrum(residual, ready=True):
    return {"ready": ready, "reason": None if ready else "filling",
            "alpha_residual_temporal": residual, "slope_temporal": -2.0,
            "channels_used": 2 if ready else 0}


def test_on_the_sdk_source_the_spectrum_is_carried_and_not_scored():
    """The default: the local figure rides along for comparison; calm is the SDK ratio."""
    plain = Ticker().run({**RELAXED, **CONTACT_GOOD}, 30)
    t = Ticker()
    for _ in range(30):
        f = t.processor.update(t.sample(), {**RELAXED, **CONTACT_GOOD}, spectrum=_spectrum(-0.5))
        t.now += t.dt
    assert f["calm_source"] == "sdk" and f["spectrum_ready"] is True
    assert f["calm_alpha_residual"] == -0.5
    assert f["calm_score"] == pytest.approx(plain["calm_score"])
    assert f["calm_log_ratio"] == pytest.approx(plain["calm_log_ratio"])


def test_on_the_local_source_calm_is_the_alpha_residual_and_focus_stays_the_ratio():
    """Capture residuals: eyes closed 0.32, open -0.16. Focus is untouched: no spectral marker of effort exists."""
    def run(residual):
        t = Ticker()
        t.processor = SignalProcessor(clock=lambda: t.now, calm_source="local")
        for _ in range(30):
            f = t.processor.update(t.sample(), {**RELAXED, **CONTACT_GOOD}, spectrum=_spectrum(residual))
            t.now += t.dt
        return f
    closed, open_ = run(0.32), run(-0.16)
    sdk = Ticker().run({**RELAXED, **CONTACT_GOOD}, 30)
    assert closed["calm_source"] == "local"
    assert closed["calm_score"] > 60.0 > 50.0 > open_["calm_score"]
    assert closed["focus_score"] == pytest.approx(sdk["focus_score"])
    assert closed["calm_log_ratio"] == pytest.approx(sdk["calm_log_ratio"]), "the SDK ratio is still reported"


def test_on_the_local_source_calm_is_held_until_the_buffer_fills_and_the_baseline_takes_only_ticks_that_had_one():
    """Never substitutes the SDK ratio: a different scale, and one baseline cannot hold both."""
    t = Ticker()
    t.processor = SignalProcessor(clock=lambda: t.now, calm_source="local")
    warming = None
    for _ in range(16):  # 4 s of buffer filling
        warming = t.processor.update(t.sample(), {**RELAXED, **CONTACT_GOOD}, spectrum=_spectrum(None, ready=False))
        t.now += t.dt
    assert warming["calm_score"] == pytest.approx(50.0) and warming["spectrum_ready"] is False
    assert 0.0 <= warming["focus_score"] <= 100.0
    assert len(t.processor._baseline_calm) == 0 and len(t.processor._baseline_focus) == 16
    for _ in range(4):
        f = t.processor.update(t.sample(), {**RELAXED, **CONTACT_GOOD}, spectrum=_spectrum(0.32))
        t.now += t.dt
    assert f["calm_score"] > 50.0
    assert len(t.processor._baseline_calm) == 4
    held = t.processor.update(t.sample(), {**RELAXED, **CONTACT_GOOD}, spectrum=_spectrum(None, ready=False))
    assert held["calm_score"] == pytest.approx(f["calm_score"]), "held, not the SDK ratio"


def test_the_local_calm_scale_brackets_the_capture():
    lo, hi = SignalProcessor.CALM_ALPHA_RESIDUAL_MIN, SignalProcessor.CALM_ALPHA_RESIDUAL_MAX
    for v in (-0.33, -0.16, 0.32, 0.54):  # 10th..90th percentile, 4 s epochs
        assert lo < v < hi
    assert (lo + hi) / 2.0 == pytest.approx(0.0)
