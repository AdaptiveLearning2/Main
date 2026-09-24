"""The local calm's two open decisions as settings: poison length, and the calm centre on arm."""

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
    """4 s waits for the whole buffer to turn over; 2 s only for the Welch window."""
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
        # Part way up the ramp from the midpoint to 0.4: what "keep" carries.
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
    """8 s: 4 s good, one clench second, 3 s good."""
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
    """Under one sample makes poison() a no-op; nan and inf would raise at import."""
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
    """Through Settings and DeviceSession, the path that runs at import."""
    for value in ("nan", "inf", "0", "abc", ""):
        s = Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a", EEG_SPECTRUM_POISON_SECONDS=value)
        session = DeviceSession("station1", s, DeviceConfig(device_id="station1", kind="sim",
                                                              host="127.0.0.1", port=8765))
        assert session.spectrum.poison_seconds == EPOCH_SECONDS


def test_midpoint_on_arm_is_inert_on_the_sdk_source():
    """On sdk calm latches beside focus, so the arm ramps as focus does."""
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
    """Case and whitespace are forgiven; a misspelling warns and means keep."""
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


def test_a_misspelt_spectrum_source_warns_rather_than_silently_meaning_sdk(caplog):
    """This setting decides the unit of every stored calm value."""
    import logging
    with caplog.at_level(logging.WARNING, logger="src.app.config"):
        s = Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a", EEG_SPECTRUM_SOURCE="locl")
    assert s.eeg_spectrum_source == "sdk"
    assert any("EEG_SPECTRUM_SOURCE" in r.message for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="src.app.config"):
        for raw, want in ((" LOCAL ", "local"), ("", "sdk"), ("Sdk", "sdk")):
            assert Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a",
                            EEG_SPECTRUM_SOURCE=raw).eeg_spectrum_source == want
    assert not caplog.records
    s = Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a", EEG_SPECTRUM_SOURCE=" LOCAL ")
    session = DeviceSession("station1", s, DeviceConfig(device_id="station1", kind="sim",
                                                          host="127.0.0.1", port=8765))
    assert session.processor.calm_source == "local"
