"""The two open decisions on the local calm, exposed as settings.

How long an artifact poisons the spectrum buffer, and what calm is centred
on between the arm and its new latch. Defaults are the shipped behaviour;
the second wearer's capture is replayed under both alternatives in one run
(`replay_raw_capture.py --matrix`), and a session can run under either
without a code change.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from src.app.config import Settings
from src.app.services.eeg_spectrum import CHANNELS, EPOCH_SECONDS, SAMPLE_RATE_HZ, SpectrumEstimator
from src.app.services.signal_processing import SignalProcessor
from src.app.services.stream_manager import DeviceConfig, DeviceSession
from tests.test_eeg_spectrum import GOOD, T0, pink, samples_from
from tests.test_local_calm_review import BANDS, _local, _tick
from tests.test_poison_behaviour import CLENCH, GOOD as GOOD_BANDS
from tests.test_signal_processing import Ticker, _spectrum


def _n():
    return int(EPOCH_SECONDS * SAMPLE_RATE_HZ)


def test_a_shorter_poison_readmits_the_buffer_sooner():
    """4 s waits for every sample the blink landed among to leave; 2 s
    readmits after the Welch window it landed in."""
    n = _n()
    samples = samples_from({c: pink(2 * n, i) for i, c in enumerate(CHANNELS)})
    full, short = SpectrumEstimator(), SpectrumEstimator(poison_seconds=2.0)
    for est in (full, short):
        assert est.push(samples[:n], GOOD)["ready"] is True
        est.poison()
    half = n // 2
    assert full.push(samples[n:n + half], GOOD)["reason"] == "artifact"
    assert short.push(samples[n:n + half], GOOD)["ready"] is True
    assert full.push(samples[n + half:], GOOD)["ready"] is True
    assert SpectrumEstimator().poison_seconds == EPOCH_SECONDS, "the default is the whole buffer"


def test_midpoint_on_arm_drops_the_pre_arm_calm_centre_and_keep_carries_it():
    lo, hi = SignalProcessor.CALM_ALPHA_RESIDUAL_MIN, SignalProcessor.CALM_ALPHA_RESIDUAL_MAX
    midpoint = (lo + hi) / 2
    for mode in ("keep", "midpoint"):
        t = _local()
        t.processor = SignalProcessor(clock=lambda: t.now, calm_source="local", calm_centre_on_arm=mode)
        for _ in range(4 * int(SignalProcessor.BASELINE_SECONDS) + 8):
            f = _tick(t, _spectrum(0.4))
        assert f["calm_centred"] is True and t.processor._baseline_calm_mean == pytest.approx(0.4)
        # The centre in effect just before the arm -- part way up the ramp
        # from the midpoint to 0.4, which is exactly what "keep" carries.
        before = t.processor._centre("calm", t.processor._calm_last_ts)
        assert midpoint < before <= 0.4
        t.processor.restart_baseline()
        f = _tick(t, _spectrum(0.4))
        centre = t.processor._centre("calm", t.processor._calm_last_ts)
        if mode == "keep":
            assert f["calm_centred"] is True and centre >= before
        else:
            assert f["calm_centred"] is False and centre == pytest.approx(midpoint)
    with pytest.raises(ValueError):
        SignalProcessor(calm_centre_on_arm="sideways")


def test_focus_keeps_its_centre_on_arm_under_either_mode():
    """Calm only: the no-step design for focus stands."""
    t = Ticker()
    t.processor = SignalProcessor(clock=lambda: t.now, calm_centre_on_arm="midpoint")
    t.run(BANDS, 4 * int(SignalProcessor.BASELINE_SECONDS) + 8)
    assert t.processor._baseline_ready
    t.processor.restart_baseline()
    assert t.processor._baseline_ready and t.processor._baseline_focus_mean is not None


def test_the_settings_reach_the_objects_the_stream_manager_builds(monkeypatch):
    monkeypatch.setenv("EEG_SPECTRUM_POISON_SECONDS", "2")
    monkeypatch.setenv("EEG_CALM_CENTRE_ON_ARM", "midpoint")
    s = Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a")
    session = DeviceSession("station1", s, DeviceConfig(device_id="station1", kind="sim",
                                                          host="127.0.0.1", port=8765))
    assert session.spectrum.poison_seconds == 2.0
    assert session.processor.calm_centre_on_arm == "midpoint"
    d = Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a",
                 EEG_SPECTRUM_POISON_SECONDS="4", EEG_CALM_CENTRE_ON_ARM="keep")
    assert d.eeg_spectrum_poison_seconds == 4.0 and d.eeg_calm_centre_on_arm == "keep"


def _capture(tmp_path):
    """8 s: 4 s good, one clench second, 3 s good -- enough for the poison
    length to show in the poisoned count."""
    n = _n()
    total = 2 * n
    chans = {c: pink(total, i) for i, c in enumerate(CHANNELS)}
    path = tmp_path / "raw.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for i in range(total):
            sec = i / SAMPLE_RATE_HZ
            bands = CLENCH if 4 <= sec < 5 else GOOD_BANDS
            row = {"kind": "eeg", "t": (T0 + timedelta(seconds=sec)).isoformat(), "segment": "a",
                   **{c: float(chans[c][i]) for c in CHANNELS}, **bands}
            fh.write(json.dumps(row) + "\n")
    return path


def test_the_replay_scores_both_poison_lengths(tmp_path):
    import sys
    sys.path.insert(0, str(tmp_path.parents[0]))
    from scripts.replay_raw_capture import replay
    path = _capture(tmp_path)
    full = replay(str(path), hz=4.0)["a"]
    short = replay(str(path), hz=4.0, poison_seconds=2.0)["a"]
    assert full["artifact"] == short["artifact"] > 0
    assert short["poisoned"] < full["poisoned"], "a shorter poison withholds fewer ticks"


@pytest.mark.parametrize("bad", [0, -1, 0.001, float("nan"), float("inf")])
def test_an_unusable_poison_length_falls_back_to_the_buffer_with_a_warning(bad, caplog):
    """0 and anything under a sample made poison() a no-op, so a blink
    contaminated four seconds of estimates with nothing saying so; nan and
    inf raised inside StreamManager() at import and took the sidecar down."""
    import logging
    with caplog.at_level(logging.WARNING, logger="src.app.services.eeg_spectrum"):
        est = SpectrumEstimator(poison_seconds=bad)
    assert est.poison_seconds == EPOCH_SECONDS and est.poison_samples == _n()
    assert any("EEG_SPECTRUM_POISON_SECONDS" in r.message for r in caplog.records)
    n = _n()
    samples = samples_from({c: pink(2 * n, i) for i, c in enumerate(CHANNELS)})
    assert est.push(samples[:n], GOOD)["ready"] is True
    est.poison()
    assert est.push(samples[n:n + 64], GOOD)["reason"] == "artifact", "the poison is not a no-op"
    with caplog.at_level(logging.WARNING, logger="src.app.services.eeg_spectrum"):
        caplog.clear()
        assert SpectrumEstimator(poison_seconds=1 / SAMPLE_RATE_HZ).poison_samples == 1
    assert not caplog.records, "one sample is the floor and is usable"


def test_the_settings_survive_an_unusable_poison_length_end_to_end():
    """The value reaches the estimator through Settings and DeviceSession
    without raising -- the path that ran at import and took the sidecar down."""
    for value in ("nan", "inf", "0", "abc", ""):
        s = Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a", EEG_SPECTRUM_POISON_SECONDS=value)
        session = DeviceSession("station1", s, DeviceConfig(device_id="station1", kind="sim",
                                                              host="127.0.0.1", port=8765))
        assert session.spectrum.poison_seconds == EPOCH_SECONDS


def test_midpoint_on_arm_is_inert_on_the_sdk_source():
    """On sdk, calm latches beside focus in 45 s, so the arm keeps its
    centre and ramps to the new one exactly as focus does; the setting was
    written for the local latch the poison starves."""
    t = Ticker()
    t.processor = SignalProcessor(clock=lambda: t.now, calm_source="sdk", calm_centre_on_arm="midpoint")
    t.run(BANDS, 4 * int(SignalProcessor.BASELINE_SECONDS) + 8)
    assert t.processor._calm_ready and t.processor._baseline_calm_mean is not None
    before = t.processor._baseline_calm_mean
    t.processor.restart_baseline()
    assert t.processor._calm_ready and t.processor._baseline_calm_mean == pytest.approx(before)
    f = t.run(BANDS, 1)
    assert f["calm_centred"] is True


def test_a_misspelt_centre_setting_boots_the_sidecar_on_keep_with_a_warning(caplog):
    """EEG_CALM_CENTRE_ON_ARM=midpont raised ValueError inside StreamManager()
    at import: no server, over a knob whose fallback is the shipped default.
    Case and whitespace are forgiven; a misspelling warns and means keep."""
    import logging
    with caplog.at_level(logging.WARNING, logger="src.app.config"):
        s = Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a", EEG_CALM_CENTRE_ON_ARM="midpont")
    assert s.eeg_calm_centre_on_arm == "keep"
    assert any("EEG_CALM_CENTRE_ON_ARM" in r.message for r in caplog.records)
    session = DeviceSession("station1", s, DeviceConfig(device_id="station1", kind="sim",
                                                          host="127.0.0.1", port=8765))
    assert session.processor.calm_centre_on_arm == "keep"
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="src.app.config"):
        for raw, want in ((" MIDPOINT ", "midpoint"), ("", "keep"), ("Keep", "keep")):
            assert Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a",
                            EEG_CALM_CENTRE_ON_ARM=raw).eeg_calm_centre_on_arm == want
    assert not caplog.records
    with pytest.raises(ValueError):
        SignalProcessor(calm_centre_on_arm="midpont"), "a direct caller is code, and code is wrong"


def test_a_non_numeric_poison_length_warns_at_settings_not_at_import(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="src.app.config"):
        s = Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a", EEG_SPECTRUM_POISON_SECONDS="abc")
    assert s.eeg_spectrum_poison_seconds == 4.0
    assert any("EEG_SPECTRUM_POISON_SECONDS" in r.message for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="src.app.config"):
        assert Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a",
                        EEG_SPECTRUM_POISON_SECONDS="2").eeg_spectrum_poison_seconds == 2.0
    assert not caplog.records
