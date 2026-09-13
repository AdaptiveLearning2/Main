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
