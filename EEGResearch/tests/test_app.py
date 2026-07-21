import asyncio
import math
import pytest
from fastapi.testclient import TestClient
import time
from datetime import datetime, timezone

from src.app.config import get_settings
from src.app.main import app, stream_manager
from src.app.models import EegSample
from src.app.services.adaptation import AdaptationEngine
from src.app.services.eeg_ingestion import (
    _apply_bridge_ingestion_fields,
    SimulatedMuseIngestionAdapter,
    TcpMuseBridgeAdapter,
    build_ingestion_adapter,
    enrich_ingestion_dict,
    parse_bridge_message,
)
from src.app.services.signal_processing import SignalProcessor


def test_healthz():
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_state_requires_auth():
    client = TestClient(app)
    response = client.get("/api/v1/state")
    assert response.status_code == 401


def test_muse_status_requires_auth():
    client = TestClient(app)
    assert client.get("/api/v1/muse/status").status_code == 401


def test_muse_status_returns_ingestion_shape():
    client = TestClient(app)
    settings = get_settings()
    learner_headers = {"Authorization": f"Bearer {settings.api_token}"}
    r = client.get("/api/v1/muse/status", headers=learner_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    data = body["data"]
    assert "running" in data
    assert "brain_signals" in data
    assert "brain_bands" in data
    ing = data["ingestion"]
    assert ing["eeg_source"] in {"sim", "muse"}
    assert "muse_devices" in ing
    assert "connection_state_name" in ing


def test_state_endpoint_serializes_no_signal_payload():
    # Regression test: FeatureData.signal_quality is a strict Pydantic Literal.
    # A "no_signal" payload must actually serialize through /api/v1/state (not
    # just be correct as an in-memory dict) or every real disconnect/stop
    # would 500 instead of reporting zeroed scores.
    client = TestClient(app)
    settings = get_settings()
    learner_headers = {"Authorization": f"Bearer {settings.api_token}"}
    previous_payload = stream_manager.latest_payload
    stream_manager.latest_payload = stream_manager._no_signal_payload()
    try:
        r = client.get("/api/v1/state", headers=learner_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["data"]["features"]["signal_quality"] == "no_signal"
        assert body["data"]["features"]["focus_score"] == 0.0
        assert body["data"]["state"]["label"] == "no_signal"
    finally:
        stream_manager.latest_payload = previous_payload


def test_muse_refresh_returns_ok_false_when_not_tcp_muse():
    client = TestClient(app)
    settings = get_settings()
    admin_headers = {"Authorization": f"Bearer {settings.admin_token}"}
    r = client.post("/api/v1/muse/refresh", headers=admin_headers)
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "ok"
    assert payload["data"]["ok"] is False


def test_session_lifecycle_and_state():
    client = TestClient(app)
    settings = get_settings()
    admin_headers = {"Authorization": f"Bearer {settings.admin_token}"}
    learner_headers = {"Authorization": f"Bearer {settings.api_token}"}
    status_response = client.get("/api/v1/muse/status", headers=learner_headers)
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] == "ok"
    ingestion = status_payload["data"]["ingestion"]
    connection_state_name = ingestion.get("connection_state_name")
    muse_connected = bool(ingestion.get("muse_connected", False))

    if muse_connected or connection_state_name == "connected":
        start_response = client.post("/api/v1/session/start", headers=admin_headers)
        assert start_response.status_code == 200
        try:
            data = None
            for _ in range(60):
                state_response = client.get("/api/v1/state", headers=learner_headers)
                assert state_response.status_code == 200
                payload = state_response.json()
                assert payload["status"] in {"ok", "idle"}
                assert "message" in payload
                data = payload.get("data")
                if data:
                    break
                time.sleep(0.05)
            assert data is not None
            assert data["contract_version"] == "1.1.0"
            assert "state" in data
            assert "question_policy" in data
            assert "bands" in data
            assert "ingestion" in data
            assert data["ingestion"]["eeg_source"] == "muse"
            assert data["features"]["signal_quality"] in {"good", "degraded", "poor"}
        finally:
            client.post("/api/v1/session/stop", headers=admin_headers)
    else:
        assert connection_state_name not in {"connecting", "connected"}
        assert muse_connected is False

def test_adaptation_cooldown_holds_previous_state():
    engine = AdaptationEngine()
    engine.cooldown_seconds = 1000.0
    first = engine.infer_state({"focus_score": 0.9, "calm_score": 0.8, "confidence": 0.9})
    second = engine.infer_state({"focus_score": 0.1, "calm_score": 0.2, "confidence": 0.9})
    assert first.label == "focused"
    assert second.label == "focused"
    assert "Cooldown" in second.reason


def test_adaptation_accepts_percentage_confidence_scale():
    engine = AdaptationEngine()
    engine.cooldown_seconds = 0.0
    low = engine.infer_state({"focus_score": 0.6, "calm_score": 0.6, "confidence": 30.0})
    assert low.label == "insufficient_signal"
    high = engine.infer_state({"focus_score": 0.8, "calm_score": 0.7, "confidence": 90.0})
    assert high.label == "focused"


def test_adaptation_accepts_percentage_focus_and_calm_scales():
    engine = AdaptationEngine()
    engine.cooldown_seconds = 0.0
    focused = engine.infer_state({"focus_score": 80.0, "calm_score": 70.0, "confidence": 90.0})
    assert focused.label == "focused"
    stressed = engine.infer_state({"focus_score": 80.0, "calm_score": 20.0, "confidence": 90.0})
    assert stressed.label == "stressed"



def test_parse_bridge_message_from_monotonic_timestamp():
    sample = parse_bridge_message(
        {
            "mono_ts_ms": 1700000000123,
            "tp9": 1.0,
            "af7": 2.0,
            "af8": 3.0,
            "tp10": 4.0,
        }
    )
    assert sample.channel_tp9 == 1.0
    assert sample.channel_af7 == 2.0
    assert sample.channel_af8 == 3.0
    assert sample.channel_tp10 == 4.0


def test_parse_bridge_message_ignores_device_fields():
    sample = parse_bridge_message(
        {
            "kind": "eeg",
            "bridge_mode": "libmuse",
            "muse_connected": True,
            "muse_discovered": True,
            "connection_state": 1,
            "mono_ts_ms": 1700000000123,
            "tp9": 1.0,
            "af7": 2.0,
            "af8": 3.0,
            "tp10": 4.0,
        }
    )
    assert sample.channel_tp9 == 1.0


def test_enrich_ingestion_dict_sets_connection_state_name():
    settings = get_settings()
    ing = enrich_ingestion_dict(settings, {"bridge_mode": "synthetic", "connection_state": 2})
    assert ing["connection_state_name"] == "connecting"


def test_apply_bridge_ingestion_fields_ignores_malformed_numeric_values():
    target = {"delta": 1.23, "connection_state": 1}
    payload = {"delta": "invalid", "theta": "2.5", "connection_state": "bad-int"}
    _apply_bridge_ingestion_fields(target, payload)
    assert target["delta"] == 1.23
    assert target["theta"] == 2.5
    assert target["connection_state"] == 1


def test_enrich_ingestion_dict_excludes_band_fields():
    settings = get_settings()
    ing = enrich_ingestion_dict(
        settings,
        {"connection_state": 1, "delta": 0.5, "theta": 0.4, "alpha": 0.3, "beta": 0.2, "gamma": 0.1},
    )
    for key in ("delta", "theta", "alpha", "beta", "gamma"):
        assert key not in ing
    assert ing["connection_state_name"] == "connected"


def test_tcp_bridge_read_sample_times_out_instead_of_blocking_forever():
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=8765, timeout_seconds=1)
    adapter._reader_thread = object()  # mark adapter as "connected" for this unit test
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="No EEG sample received from bridge"):
        adapter.read_sample()
    assert time.monotonic() - started < 2.0


def test_signal_processor_muse_range_produces_non_saturated_features():
    processor = SignalProcessor(window_size=4)
    now = datetime.now(timezone.utc)
    low_spread = processor.update(
        EegSample(
            timestamp=now,
            channel_tp9=590.0,
            channel_af7=600.0,
            channel_af8=605.0,
            channel_tp10=595.0,
        )
    )
    high_spread = processor.update(
        EegSample(
            timestamp=now,
            channel_tp9=820.0,
            channel_af7=560.0,
            channel_af8=805.0,
            channel_tp10=575.0,
        )
    )
    assert 0.0 < low_spread["focus_score"] < 100.0
    assert 0.0 < high_spread["focus_score"] < 100.0
    assert 0.0 < low_spread["calm_score"] < 100.0
    assert 0.0 <= high_spread["calm_score"] < 100.0
    assert low_spread["calm_score"] > high_spread["calm_score"]
    assert 20.0 <= low_spread["confidence"] <= 100.0
    assert 20.0 <= high_spread["confidence"] <= 100.0
    assert low_spread["confidence"] > high_spread["confidence"]
    assert low_spread["signal_quality"] in {"good", "degraded", "poor"}


def test_signal_processor_uses_band_features_when_available():
    processor = SignalProcessor(window_size=4)
    now = datetime.now(timezone.utc)
    sample = EegSample(
        timestamp=now,
        channel_tp9=700.0,
        channel_af7=705.0,
        channel_af8=695.0,
        channel_tp10=702.0,
    )
    low_focus = processor.update(sample, {"alpha": 1.5, "beta": 0.2, "theta": 1.0, "gamma": 0.3})
    high_focus = processor.update(sample, {"alpha": 0.5, "beta": 1.6, "theta": 0.4, "gamma": 0.2})
    assert high_focus["focus_score"] > low_focus["focus_score"]

    low_calm = processor.update(sample, {"alpha": 0.2, "beta": 1.2, "theta": 0.3, "gamma": 0.9})
    high_calm = processor.update(sample, {"alpha": 1.6, "beta": 0.3, "theta": 0.5, "gamma": 0.2})
    assert high_calm["calm_score"] > low_calm["calm_score"]


def test_band_powers_are_treated_as_logarithmic_not_linear():
    """libMuse ABSOLUTE band powers are Bels (logarithms), so they must be
    converted with 10**x before being combined. Feeding them straight into
    log() -- a logarithm of a logarithm -- and summing in log space made the
    ratios meaningless, and could take log() of a negative sum since theta is
    routinely negative."""
    processor = SignalProcessor(window_size=2)
    # Real capture from a Muse S. theta is negative, which is normal on a log
    # scale, and alpha + theta = 0.148 under the old (broken) arithmetic.
    live = {"delta": 0.360908, "theta": -0.0567422, "alpha": 0.205242,
            "beta": 0.212698, "gamma": 0.00128006}
    focus_lr, calm_lr = processor._extract_band_log_ratios(live)

    # Correct values, computed on linear power (10**bels):
    #   focus = ln(10**0.2127) - ln(10**0.2052 + 10**-0.0567) ~= -0.419
    #   calm  = ln(10**0.2052) - ln(10**0.2127 + 10**0.00128) ~= -0.497
    assert focus_lr == pytest.approx(-0.419, abs=0.01)
    assert calm_lr == pytest.approx(-0.497, abs=0.01)


def test_negative_band_values_do_not_break_ratio_extraction():
    # Strongly negative theta/gamma used to be able to drive the denominator
    # negative under the old log-space summing.
    processor = SignalProcessor(window_size=2)
    focus_lr, calm_lr = processor._extract_band_log_ratios(
        {"theta": -1.5, "alpha": -0.8, "beta": -0.3, "gamma": -2.0}
    )
    assert focus_lr is not None and calm_lr is not None
    assert math.isfinite(focus_lr) and math.isfinite(calm_lr)


def test_all_zero_bands_still_ignored_but_negative_bands_are_kept():
    processor = SignalProcessor(window_size=2)
    assert processor._extract_band_log_ratios(
        {"theta": 0.0, "alpha": 0.0, "beta": 0.0, "gamma": 0.0}
    ) == (None, None)
    # A frame where one band is negative is valid data, not an empty frame.
    focus_lr, _ = processor._extract_band_log_ratios(
        {"theta": -0.2, "alpha": 0.1, "beta": 0.3, "gamma": 0.0}
    )
    assert focus_lr is not None


def test_engaged_student_is_not_scored_as_stressed():
    """Regression for the calibration bug: alpha is suppressed during focused
    mental effort, so an engaged learner's calm score must still clear the
    AdaptationEngine "stressed" threshold (calm_ratio < 0.35) -- otherwise
    concentrating on a problem gets misread as distress and eases difficulty."""
    processor = SignalProcessor(window_size=4)
    engaged = {"theta": -0.10, "alpha": 0.10, "beta": 0.45, "gamma": 0.05}
    features = None
    for _ in range(4):
        features = processor.update(
            EegSample(
                timestamp=datetime.now(timezone.utc),
                channel_tp9=740.0, channel_af7=760.0,
                channel_af8=755.0, channel_tp10=745.0,
            ),
            engaged,
        )
    assert features["calm_score"] > 35.0
    # ...while a genuinely aroused/stressed profile still drops below it.
    stressed_processor = SignalProcessor(window_size=4)
    stressed = {"theta": -0.05, "alpha": -0.20, "beta": 0.60, "gamma": 0.35}
    stressed_features = None
    for _ in range(4):
        stressed_features = stressed_processor.update(
            EegSample(
                timestamp=datetime.now(timezone.utc),
                channel_tp9=740.0, channel_af7=760.0,
                channel_af8=755.0, channel_tp10=745.0,
            ),
            stressed,
        )
    assert stressed_features["calm_score"] < 35.0
    assert stressed_features["calm_score"] < features["calm_score"]


def _quality_for(meta, window_size=4):
    """Run a steady, well-formed sample through the processor and return the
    resulting signal_quality for the given ingestion metadata."""
    processor = SignalProcessor(window_size=window_size)
    features = None
    for _ in range(window_size):
        features = processor.update(
            EegSample(
                timestamp=datetime.now(timezone.utc),
                channel_tp9=700.0,
                channel_af7=705.0,
                channel_af8=702.0,
                channel_tp10=698.0,
            ),
            meta,
        )
    return features


# An engaged, eyes-open student: beta dominant, alpha suppressed. This is the
# normal state while working through problems, and it drives calm_ratio below
# the legacy "degraded" gate -- so signal_quality must come from electrode
# contact, not calmness, or a perfectly-fitted headband reports "poor".
_ENGAGED_BANDS = {"alpha": 20.0, "beta": 40.0, "theta": 5.0, "gamma": 5.0}


def test_signal_quality_uses_electrode_contact_not_calmness():
    good = _quality_for({**_ENGAGED_BANDS, "hsi": [1, 1, 1, 1], "is_good": [1, 1, 1, 1]})
    # Regression guard: calm is genuinely low here (alpha suppressed), which is
    # exactly the case the old calm-based rule mis-reported as "poor".
    assert good["calm_score"] < 30.0
    assert good["signal_quality"] == "good"

    poor = _quality_for({**_ENGAGED_BANDS, "hsi": [4, 4, 4, 4], "is_good": [0, 0, 0, 0]})
    assert poor["signal_quality"] == "poor"


def test_signal_quality_degrades_as_electrode_fit_worsens():
    one_mediocre = _quality_for({**_ENGAGED_BANDS, "hsi": [1, 1, 1, 2]})
    two_mediocre = _quality_for({**_ENGAGED_BANDS, "hsi": [1, 1, 2, 2]})
    assert one_mediocre["signal_quality"] == "good"
    assert two_mediocre["signal_quality"] == "degraded"


def test_signal_quality_takes_worse_of_fit_and_validity():
    # Electrodes seated well (hsi all good) but most channels reporting bad
    # data -- the noisy signal must win over the optimistic fit reading.
    features = _quality_for({**_ENGAGED_BANDS, "hsi": [1, 1, 1, 1], "is_good": [1, 0, 0, 0]})
    assert features["signal_quality"] == "poor"


def test_signal_quality_is_not_flipped_by_a_single_blink():
    """IS_GOOD dips on eye blinks and muscle movement (libMuse documents this),
    which briefly zeroes the frontal channels. A well-seated headband must not
    drop out of "good" every time the student blinks."""
    processor = SignalProcessor(window_size=8)
    seated = {**_ENGAGED_BANDS, "hsi": [1, 1, 1, 1]}
    sample = EegSample(
        timestamp=datetime.now(timezone.utc),
        channel_tp9=740.0, channel_af7=760.0,
        channel_af8=755.0, channel_tp10=745.0,
    )
    # Steady clean data, then one blink frame with both frontal channels bad.
    for _ in range(6):
        processor.update(sample, {**seated, "is_good": [1, 1, 1, 1]})
    blink = processor.update(sample, {**seated, "is_good": [1, 0, 0, 1]})
    assert blink["signal_quality"] == "good"


def test_sustained_bad_data_still_degrades_quality():
    # Smoothing must not hide a genuinely bad channel: enough consecutive bad
    # frames should still pull the reported quality down.
    processor = SignalProcessor(window_size=8)
    seated = {**_ENGAGED_BANDS, "hsi": [1, 1, 1, 1]}
    sample = EegSample(
        timestamp=datetime.now(timezone.utc),
        channel_tp9=740.0, channel_af7=760.0,
        channel_af8=755.0, channel_tp10=745.0,
    )
    features = None
    for _ in range(10):
        features = processor.update(sample, {**seated, "is_good": [1, 0, 0, 0]})
    assert features["signal_quality"] != "good"


def test_signal_quality_falls_back_when_contact_data_absent():
    # No headband contact data (older bridge, or simulator without it): the
    # legacy calm/confidence heuristic still applies rather than crashing.
    explicit_none = _quality_for({**_ENGAGED_BANDS, "hsi": None, "is_good": None})
    absent = _quality_for(_ENGAGED_BANDS)
    assert explicit_none["signal_quality"] == absent["signal_quality"]
    assert absent["signal_quality"] in {"good", "degraded", "poor"}


def test_signal_quality_ignores_malformed_contact_values():
    features = _quality_for({**_ENGAGED_BANDS, "hsi": ["x", None], "is_good": "nope"})
    assert features["signal_quality"] in {"good", "degraded", "poor"}


def _sample(level=740.0):
    return EegSample(
        timestamp=datetime.now(timezone.utc),
        channel_tp9=level, channel_af7=level + 20,
        channel_af8=level + 15, channel_tp10=level + 5,
    )


def test_unusable_samples_are_kept_out_of_the_rolling_window():
    """A frame where every electrode reports bad data would otherwise skew the
    window's mean/spread/stability for its whole length afterwards."""
    processor = SignalProcessor(window_size=8)
    processor.update(_sample(), {**_ENGAGED_BANDS, "is_good": [1, 1, 1, 1]})
    assert len(processor.window) == 1

    # Wildly different amplitude, flagged entirely invalid -- must not be stored.
    processor.update(_sample(5000.0), {**_ENGAGED_BANDS, "is_good": [0, 0, 0, 0]})
    assert len(processor.window) == 1

    # A partially-good frame is still real data and should be kept.
    processor.update(_sample(), {**_ENGAGED_BANDS, "is_good": [0, 1, 1, 0]})
    assert len(processor.window) == 2


def test_window_filtering_never_empties_the_window():
    # The feature maths needs at least one sample; a run of bad frames must
    # hold the last known-good reading rather than leaving nothing to compute.
    processor = SignalProcessor(window_size=4)
    for _ in range(6):
        features = processor.update(_sample(), {**_ENGAGED_BANDS, "is_good": [0, 0, 0, 0]})
    assert len(processor.window) >= 1
    assert features["focus_score"] is not None


def test_samples_without_contact_data_are_never_discarded():
    processor = SignalProcessor(window_size=8)
    for _ in range(3):
        processor.update(_sample(), _ENGAGED_BANDS)  # no is_good key at all
    assert len(processor.window) == 3


def test_scores_center_on_baseline_once_it_is_established():
    """After the baseline period, holding steady at the learner's own resting
    level should read mid-scale rather than wherever fixed population bounds
    happen to place that individual."""
    processor = SignalProcessor(window_size=8)
    steady = {**_ENGAGED_BANDS, "is_good": [1, 1, 1, 1]}
    features = None
    for _ in range(SignalProcessor.BASELINE_SAMPLES + 5):
        features = processor.update(_sample(), steady)
    assert processor._baseline_ready is True
    # Band term is centred at 0.5; the blend with the amplitude term keeps the
    # final score near mid-scale rather than pinned to an extreme.
    assert 25.0 < features["focus_score"] < 75.0
    assert 25.0 < features["calm_score"] < 75.0


def test_baseline_falls_back_to_population_bounds_before_it_is_ready():
    processor = SignalProcessor(window_size=8)
    features = processor.update(_sample(), {**_ENGAGED_BANDS, "is_good": [1, 1, 1, 1]})
    assert processor._baseline_ready is False
    # Still produces a usable score from the very first sample.
    assert 0.0 <= features["focus_score"] <= 100.0


def test_reset_clears_the_session_baseline():
    processor = SignalProcessor(window_size=8)
    steady = {**_ENGAGED_BANDS, "is_good": [1, 1, 1, 1]}
    for _ in range(SignalProcessor.BASELINE_SAMPLES + 1):
        processor.update(_sample(), steady)
    assert processor._baseline_ready is True
    processor.reset()
    assert processor._baseline_ready is False
    assert processor._baseline_focus_mean is None


def test_signal_processor_reset_clears_window():
    processor = SignalProcessor(window_size=4)
    processor.update(
        EegSample(
            timestamp=datetime.now(timezone.utc),
            channel_tp9=700.0,
            channel_af7=700.0,
            channel_af8=700.0,
            channel_tp10=700.0,
        )
    )
    assert len(processor.window) == 1
    processor.reset()
    assert len(processor.window) == 0


def test_adaptation_reset_for_signal_loss_bypasses_cooldown():
    engine = AdaptationEngine()
    engine.cooldown_seconds = 1000.0
    focused = engine.infer_state({"focus_score": 90.0, "calm_score": 80.0, "confidence": 90.0})
    assert focused.label == "focused"

    engine.reset_for_signal_loss()
    assert engine.last_label == "no_signal"

    # Without the reset, the 1000s cooldown would hold the stale "focused" label.
    next_state = engine.infer_state({"focus_score": 10.0, "calm_score": 90.0, "confidence": 90.0})
    assert "Cooldown" not in next_state.reason


def test_stream_manager_no_signal_payload_zeroes_scores():
    manager = stream_manager.__class__()
    payload = manager._no_signal_payload()
    assert payload["features"]["focus_score"] == 0.0
    assert payload["features"]["calm_score"] == 0.0
    assert payload["features"]["confidence"] == 0.0
    assert payload["features"]["signal_quality"] == "no_signal"
    assert payload["state"]["label"] == "no_signal"
    assert payload["question_policy"]["action"] == "fallback_default"


def test_stream_manager_loop_zeroes_scores_and_resets_state_on_read_failure():
    class FailingAdapter:
        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def read_sample(self):
            raise RuntimeError("no data")

    async def run_case():
        manager = stream_manager.__class__()
        manager.adapter = FailingAdapter()
        manager.processor.update(
            EegSample(
                timestamp=datetime.now(timezone.utc),
                channel_tp9=700.0,
                channel_af7=700.0,
                channel_af8=700.0,
                channel_tp10=700.0,
            )
        )
        manager.adaptation.last_label = "focused"
        manager.adaptation.last_change_ts = time.monotonic()
        manager.running = True
        loop_task = asyncio.create_task(manager._loop())
        await asyncio.sleep(0.08)
        manager.running = False
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        return manager

    manager = asyncio.run(run_case())
    assert manager.latest_payload["features"]["focus_score"] == 0.0
    assert manager.latest_payload["features"]["calm_score"] == 0.0
    assert manager.latest_payload["state"]["label"] == "no_signal"
    assert len(manager.processor.window) == 0
    assert manager.adaptation.last_label == "no_signal"


def test_stream_manager_stop_zeroes_stale_scores_instead_of_freezing_them():
    async def run_case():
        manager = stream_manager.__class__()
        manager.adapter = SimulatedMuseIngestionAdapter()
        await manager.start()
        # Let a couple of real samples land so latest_payload has non-zero scores.
        for _ in range(5):
            await asyncio.sleep(0.05)
        assert manager.latest_payload["features"]["focus_score"] != 0.0
        await manager.stop()
        return manager

    manager = asyncio.run(run_case())
    assert manager.latest_payload["features"]["focus_score"] == 0.0
    assert manager.latest_payload["features"]["calm_score"] == 0.0
    assert manager.latest_payload["features"]["signal_quality"] == "no_signal"
    assert manager.latest_payload["state"]["label"] == "no_signal"


def test_snapshot_zeroes_bands_on_no_signal_instead_of_stale_adapter_meta():
    # Regression test: snapshot() used to pull "bands" live from
    # adapter.get_ingestion_meta() unconditionally, bypassing the zeroed
    # no-signal payload entirely. Adapters (real and simulated) cache their
    # last-known band values and don't reset them on disconnect, so the EEG
    # Bands display kept showing stale non-zero values after a disconnect
    # even though focus/calm/confidence correctly zeroed out.
    async def run_case():
        manager = stream_manager.__class__()
        manager.adapter = SimulatedMuseIngestionAdapter()
        await manager.start()
        for _ in range(5):
            await asyncio.sleep(0.05)
        running_bands = manager.snapshot()["bands"]
        await manager.stop()
        return manager, running_bands

    manager, running_bands = asyncio.run(run_case())
    assert any(v != 0.0 for v in running_bands.values())
    stopped_bands = manager.snapshot()["bands"]
    assert stopped_bands == {"delta": 0.0, "theta": 0.0, "alpha": 0.0, "beta": 0.0, "gamma": 0.0}


def test_ingestion_adapter_supports_live_muse_source():
    settings = get_settings().model_copy(update={"eeg_source": "muse"})
    adapter = build_ingestion_adapter(settings)
    assert isinstance(adapter, TcpMuseBridgeAdapter)

    unsupported_settings = get_settings().model_copy(update={"eeg_source": "invalid-source"})
    with pytest.raises(ValueError, match="Unsupported EEG_SOURCE"):
        build_ingestion_adapter(unsupported_settings)


def test_stream_manager_start_does_not_block_event_loop_on_connect():
    class BlockingConnectAdapter:
        def connect(self) -> None:
            time.sleep(0.2)

        def disconnect(self) -> None:
            pass

        def read_sample(self):
            raise RuntimeError("no data")

    async def run_case() -> int:
        manager = stream_manager.__class__()
        manager.adapter = BlockingConnectAdapter()
        ticks = 0
        keep_ticking = True

        async def ticker() -> None:
            nonlocal ticks
            while keep_ticking:
                await asyncio.sleep(0.02)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        start_task = asyncio.create_task(manager.start())
        await asyncio.sleep(0.08)
        await start_task
        keep_ticking = False
        await manager.stop()
        await ticker_task
        return ticks

    ticks = asyncio.run(run_case())
    assert ticks > 0


def test_stream_manager_stop_does_not_block_event_loop_on_disconnect():
    class BlockingDisconnectAdapter:
        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            time.sleep(0.2)

        def read_sample(self):
            raise RuntimeError("no data")

    async def run_case() -> int:
        manager = stream_manager.__class__()
        manager.adapter = BlockingDisconnectAdapter()
        manager.running = True
        ticks = 0
        keep_ticking = True

        async def ticker() -> None:
            nonlocal ticks
            while keep_ticking:
                await asyncio.sleep(0.02)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        stop_task = asyncio.create_task(manager.stop())
        await asyncio.sleep(0.08)
        await stop_task
        keep_ticking = False
        await ticker_task
        return ticks

    ticks = asyncio.run(run_case())
    assert ticks > 0


def test_stream_manager_loop_does_not_block_event_loop_on_get_ingestion_meta():
    class BlockingMetaAdapter:
        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def read_sample(self):
            return EegSample(
                timestamp=datetime.now(timezone.utc),
                channel_tp9=700.0,
                channel_af7=705.0,
                channel_af8=695.0,
                channel_tp10=702.0,
            )

        def get_ingestion_meta(self):
            # Simulate lock/contention delay inside adapter metadata retrieval.
            time.sleep(0.2)
            return {"alpha": 1.0, "beta": 1.0, "theta": 1.0, "gamma": 1.0}

    async def run_case() -> int:
        manager = stream_manager.__class__()
        manager.adapter = BlockingMetaAdapter()
        manager.running = True
        ticks = 0
        keep_ticking = True

        async def ticker() -> None:
            nonlocal ticks
            while keep_ticking:
                await asyncio.sleep(0.02)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        loop_task = asyncio.create_task(manager._loop())
        await asyncio.sleep(0.08)
        keep_ticking = False
        manager.running = False
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        await ticker_task
        return ticks

    ticks = asyncio.run(run_case())
    assert ticks > 0
