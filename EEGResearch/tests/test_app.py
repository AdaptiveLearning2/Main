import asyncio
import math
import pytest
from statistics import fmean
from fastapi.testclient import TestClient
import time
from datetime import datetime, timezone

from src.app.config import DeviceConfig, get_settings, parse_eeg_devices
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
from src.app.services.stream_manager import DeviceSession


def _make_session(device_id: str = "test-device") -> DeviceSession:
    settings = get_settings()
    return DeviceSession(
        device_id,
        settings,
        DeviceConfig(device_id=device_id, kind=settings.eeg_source, host=settings.muse_bridge_host, port=settings.muse_bridge_port),
    )


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
    # signal_quality is a strict Pydantic Literal, so a "no_signal" payload
    # must actually serialize through /api/v1/state, not just be valid as an
    # in-memory dict, or a real disconnect would 500 instead of zeroing scores.
    client = TestClient(app)
    settings = get_settings()
    learner_headers = {"Authorization": f"Bearer {settings.api_token}"}
    default_session = stream_manager.session()
    previous_payload = default_session.latest_payload
    default_session.latest_payload = default_session._no_signal_payload()
    try:
        r = client.get("/api/v1/state", headers=learner_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["data"]["features"]["signal_quality"] == "no_signal"
        assert body["data"]["features"]["focus_score"] == 0.0
        assert body["data"]["state"]["label"] == "no_signal"
    finally:
        default_session.latest_payload = previous_payload


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
            assert data["contract_version"] == "1.3.0"
            assert "state" in data
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


def test_apply_bridge_ingestion_fields_passes_optical_fields_through():
    """A field the bridge emits but the whitelist omits is dropped silently
    and looks from outside like a bridge that never sent it. That's why every
    field the bridge sends has to be named here."""
    target: dict = {}
    _apply_bridge_ingestion_fields(target, {
        "muse_model": "MS-03",
        "requested_preset": "PRESET_1035",
        "active_preset": "PRESET_1035",
        "eeg_channel_count": 4,
        "optical_supported": True,
        "optics_packets": 3789,
        "ppg_packets": 0,
        "optics_values": 4,
        "last_optics": [0.94, 0.57, 5.95, 4.92],
        "is_ppg_good": True,
        "optics_age_ms": 16,
    })
    assert target["muse_model"] == "MS-03"
    assert target["active_preset"] == "PRESET_1035"
    assert target["eeg_channel_count"] == 4
    assert target["optical_supported"] is True
    assert target["optics_packets"] == 3789
    assert target["optics_values"] == 4
    assert target["last_optics"] == [0.94, 0.57, 5.95, 4.92]
    assert target["is_ppg_good"] is True
    assert target["optics_age_ms"] == 16


def test_apply_bridge_ingestion_fields_keeps_unknown_distinct_from_false_and_zero():
    """null means the headband hasn't reported yet; False means it reported a
    bad signal. Only False justifies falling back to another source, so the
    two must stay distinct. Same reasoning for eeg_channel_count and
    optics_age_ms, where 0 is a real reading, not a sentinel."""
    target: dict = {}
    _apply_bridge_ingestion_fields(target, {
        "is_ppg_good": None,
        "is_heart_good": None,
        "eeg_channel_count": None,
        "optics_age_ms": None,
    })
    assert target["is_ppg_good"] is None
    assert target["is_heart_good"] is None
    assert target["eeg_channel_count"] is None
    assert target["optics_age_ms"] is None

    reported_bad: dict = {}
    _apply_bridge_ingestion_fields(reported_bad, {"is_ppg_good": False, "optics_age_ms": 0})
    assert reported_bad["is_ppg_good"] is False
    assert reported_bad["optics_age_ms"] == 0


def test_apply_bridge_ingestion_fields_carries_battery_and_keeps_zero_from_unknown():
    """0% is a real charge and the reading the badge exists to show. The
    bridge sends null until a BATTERY packet arrives, which can take most of
    the first minute -- collapsing null and 0 would draw an empty battery for
    a headband that just hasn't reported yet."""
    measured: dict = {}
    _apply_bridge_ingestion_fields(measured, {"battery_percent": 82.0})
    assert measured["battery_percent"] == 82.0

    flat: dict = {}
    _apply_bridge_ingestion_fields(flat, {"battery_percent": 0})
    assert flat["battery_percent"] == 0.0

    unreported: dict = {}
    _apply_bridge_ingestion_fields(unreported, {"battery_percent": None})
    assert unreported["battery_percent"] is None

    # An old bridge predating the field leaves the key absent entirely -- a
    # third state, distinct from both null and 0.
    absent: dict = {}
    _apply_bridge_ingestion_fields(absent, {"muse_model": "MS-03"})
    assert "battery_percent" not in absent


def test_apply_bridge_ingestion_fields_ignores_malformed_optical_values():
    target = {"optics_packets": 12, "optics_age_ms": 5}
    _apply_bridge_ingestion_fields(target, {
        "optics_packets": "not-an-int",
        "optics_age_ms": "also-not",
        "last_optics": "not-a-list",
    })
    assert target["optics_packets"] == 12
    assert target["optics_age_ms"] == 5
    assert "last_optics" not in target


def test_enrich_ingestion_dict_excludes_band_fields():
    settings = get_settings()
    ing = enrich_ingestion_dict(
        settings,
        {"connection_state": 1, "delta": 0.5, "theta": 0.4, "alpha": 0.3, "beta": 0.2, "gamma": 0.1},
    )
    for key in ("delta", "theta", "alpha", "beta", "gamma"):
        assert key not in ing
    assert ing["connection_state_name"] == "connected"


class _ReaderThreadStub:
    """Stand-in for TcpMuseBridgeAdapter's reader thread.

    read_sample() calls is_alive() on the timeout path and disconnect() joins
    the thread, so a bare object() won't work as a stand-in.
    """

    def __init__(self, alive=True):
        self._alive = alive
        self.joined = False

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self.joined = True


def test_tcp_bridge_read_sample_times_out_instead_of_blocking_forever():
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=8765, timeout_seconds=1)
    # Reader alive but no data arriving: should time out rather than block
    # forever, and leave the connection in place for the next poll.
    adapter._reader_thread = _ReaderThreadStub(alive=True)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="No EEG sample received from bridge"):
        adapter.read_sample()
    assert time.monotonic() - started < 2.0
    assert adapter._reader_thread is not None


def test_tcp_bridge_read_sample_resets_connection_when_reader_died():
    # Reader thread exited (bridge disconnected): read_sample() should raise a
    # recoverable error and tear the connection down so the next call
    # reconnects instead of waiting on a queue nothing feeds.
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=8765, timeout_seconds=1)
    stub = _ReaderThreadStub(alive=False)
    adapter._reader_thread = stub
    with pytest.raises(RuntimeError, match="No EEG sample received from bridge"):
        adapter.read_sample()
    assert stub.joined
    assert adapter._reader_thread is None


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
    # scale.
    live = {"delta": 0.360908, "theta": -0.0567422, "alpha": 0.205242,
            "beta": 0.212698, "gamma": 0.00128006}
    focus_lr, calm_lr = processor._extract_band_log_ratios(live)

    # Expected values, computed on linear power (10**bels):
    #   focus = ln(10**0.2127) - ln(10**0.2052 + 10**-0.0567) ~= -0.419
    #   calm  = ln(10**0.2052) - ln(10**0.2127 + 10**0.00128) ~= -0.497
    assert focus_lr == pytest.approx(-0.419, abs=0.01)
    assert calm_lr == pytest.approx(-0.497, abs=0.01)


def test_negative_band_values_do_not_break_ratio_extraction():
    # Strongly negative theta/gamma should not drive the denominator negative.
    processor = SignalProcessor(window_size=2)
    focus_lr, calm_lr = processor._extract_band_log_ratios(
        {"theta": -1.5, "alpha": -0.8, "beta": -0.3, "gamma": -2.0}
    )
    assert focus_lr is not None and calm_lr is not None
    assert math.isfinite(focus_lr) and math.isfinite(calm_lr)


def test_simulator_bands_stay_within_processor_ratio_bounds():
    """The simulator produces band powers, so it must emit them on the same
    scale the processor reads (Bels), across its whole state range.

    Emitting linear magnitudes instead fails silently: the processor
    exponentiates them to ~10**40, the log-ratios blow past their bounds, and
    every spectral term clamps -- but scores still land in 0..100 with
    nothing raising an error. Asserting on the raw ratios (not the scores)
    is what actually catches this.
    """
    processor = SignalProcessor(window_size=4)
    adapter = SimulatedMuseIngestionAdapter()
    adapter.connect()

    for focus_state in (0.0, 0.5, 1.0):
        for calm_state in (0.0, 0.5, 1.0):
            adapter._focus_state = focus_state
            adapter._calm_state = calm_state
            meta = adapter.get_ingestion_meta()
            focus_lr, calm_lr = processor._extract_band_log_ratios(meta)
            assert focus_lr is not None and calm_lr is not None
            assert SignalProcessor.FOCUS_LOG_RATIO_MIN <= focus_lr <= SignalProcessor.FOCUS_LOG_RATIO_MAX, (
                f"focus log-ratio {focus_lr} outside calibrated bounds at "
                f"focus_state={focus_state} calm_state={calm_state}"
            )
            assert SignalProcessor.CALM_LOG_RATIO_MIN <= calm_lr <= SignalProcessor.CALM_LOG_RATIO_MAX, (
                f"calm log-ratio {calm_lr} outside calibrated bounds at "
                f"focus_state={focus_state} calm_state={calm_state}"
            )
            # Bels, in the range a live Muse S actually produces.
            for band in ("delta", "theta", "alpha", "beta", "gamma"):
                assert -2.0 <= meta[band] <= 2.0, f"{band}={meta[band]} is not a plausible Bel value"


def test_simulator_focus_and_calm_are_not_mirror_images():
    """Under linear (not log) band values, alpha/beta would dominate theta/gamma
    so heavily that focus and calm collapse to exact negations of each other,
    and AdaptationEngine couldn't tell states apart. Moving calm_state must
    move calm and leave focus alone."""
    processor = SignalProcessor(window_size=4)
    adapter = SimulatedMuseIngestionAdapter()
    adapter.connect()
    adapter._focus_state = 0.5

    adapter._calm_state = 0.0
    focus_lo, calm_lo = processor._extract_band_log_ratios(adapter.get_ingestion_meta())
    adapter._calm_state = 1.0
    focus_hi, calm_hi = processor._extract_band_log_ratios(adapter.get_ingestion_meta())

    assert calm_hi > calm_lo + 0.1, "calm_state must move the calm ratio"
    assert focus_hi == pytest.approx(focus_lo, abs=0.01), "calm_state must not move the focus ratio"
    # The mirroring failure mode: focus == -calm for every input.
    assert abs(focus_lo + calm_lo) > 0.1


def test_absurd_band_values_fall_back_instead_of_raising():
    """10.0**x overflows past ~308, meaning an upstream producer isn't
    emitting Bels. This must be handled here rather than raising, or the
    error escapes to stream_manager's broad except, which drops the tick and
    freezes latest_payload at a stale value."""
    processor = SignalProcessor(window_size=2)
    assert processor._extract_band_log_ratios(
        {"alpha": 400.0, "beta": 1.0, "theta": 0.5, "gamma": 0.2}
    ) == (None, None)
    assert processor._extract_band_log_ratios(
        {"alpha": 1e9, "beta": 1e9, "theta": 1e9, "gamma": 1e9}
    ) == (None, None)


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
    """Alpha is suppressed during focused mental effort, so an engaged
    learner's calm score must still clear the AdaptationEngine "stressed"
    threshold (calm_ratio < 0.35) -- otherwise concentrating on a problem gets
    misread as distress and eases difficulty."""
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


# An engaged, eyes-open student: beta dominant, alpha suppressed. This is
# normal while working through problems, but it drives calm_ratio below the
# legacy "degraded" gate -- so signal_quality must come from electrode
# contact, not calmness, or a well-fitted headband reports "poor".
_ENGAGED_BANDS = {"alpha": 20.0, "beta": 40.0, "theta": 5.0, "gamma": 5.0}


def test_signal_quality_uses_electrode_contact_not_calmness():
    good = _quality_for({**_ENGAGED_BANDS, "hsi": [1, 1, 1, 1], "is_good": [1, 1, 1, 1]})
    # Calm is genuinely low here (alpha suppressed) -- a calm-based rule would
    # wrongly report this as "poor".
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
    # Electrodes seated well (hsi good) but most channels report bad data --
    # the noisy signal must win over the optimistic fit reading.
    features = _quality_for({**_ENGAGED_BANDS, "hsi": [1, 1, 1, 1], "is_good": [1, 0, 0, 0]})
    assert features["signal_quality"] == "poor"


def test_signal_quality_is_not_flipped_by_a_single_blink():
    """IS_GOOD dips on eye blinks and muscle movement, briefly zeroing the
    frontal channels. A well-seated headband must not drop out of "good"
    every time the student blinks."""
    processor = SignalProcessor(window_size=8)
    seated = {**_ENGAGED_BANDS, "hsi": [1, 1, 1, 1]}
    sample = EegSample(
        timestamp=datetime.now(timezone.utc),
        channel_tp9=740.0, channel_af7=760.0,
        channel_af8=755.0, channel_tp10=745.0,
    )
    # Steady clean data, then one blink frame with both frontals bad.
    for _ in range(6):
        processor.update(sample, {**seated, "is_good": [1, 1, 1, 1]})
    blink = processor.update(sample, {**seated, "is_good": [1, 0, 0, 1]})
    assert blink["signal_quality"] == "good"


def test_sustained_bad_data_still_degrades_quality():
    # Smoothing must not hide a genuinely bad channel: enough consecutive bad
    # frames should still pull quality down.
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
    # Older bridge with no HSI/IS_GOOD: the legacy calm/confidence heuristic
    # applies and must be labelled as such, so callers don't mistake it for a
    # statement about the electrodes.
    explicit_none = _quality_for({**_ENGAGED_BANDS, "hsi": None, "is_good": None})
    absent = _quality_for(_ENGAGED_BANDS)
    assert explicit_none["signal_quality"] == absent["signal_quality"]
    assert explicit_none["quality_basis"] == "heuristic"
    assert absent["quality_basis"] == "heuristic"
    # An engaged learner trips the heuristic's calm gate, so it reports "poor"
    # for a perfectly good signal -- why quality_basis exists.
    assert absent["signal_quality"] == "poor"


def test_heuristic_poor_is_distinguishable_from_contact_poor():
    """Both report "poor", but only one means the electrodes are bad.
    Consumers gate data collection on this distinction; collapsing it would
    make an outdated bridge silently record nothing for a whole session.
    """
    heuristic = _quality_for(_ENGAGED_BANDS)
    contact = _quality_for({**_ENGAGED_BANDS, "hsi": [4, 4, 4, 4], "is_good": [0, 0, 0, 0]})
    assert heuristic["signal_quality"] == contact["signal_quality"] == "poor"
    assert heuristic["quality_basis"] == "heuristic"
    assert contact["quality_basis"] == "contact"


def test_signal_quality_ignores_malformed_contact_values():
    # Garbage in the contact fields must not be read as a contact verdict --
    # it should fall through to the heuristic, not be treated as "contact
    # says poor".
    features = _quality_for({**_ENGAGED_BANDS, "hsi": ["x", None], "is_good": "nope"})
    assert features["quality_basis"] == "heuristic"
    assert features["signal_quality"] == _quality_for(_ENGAGED_BANDS)["signal_quality"]


def test_a_single_hsi_blip_does_not_drop_quality_to_poor():
    """HSI must be smoothed the same way IS_GOOD is, or one bad HSI frame
    bypasses the smoothing entirely and drops quality straight to poor.
    """
    processor = SignalProcessor(window_size=8)
    seated = {**_ENGAGED_BANDS, "is_good": [1, 1, 1, 1]}
    sample = EegSample(
        timestamp=datetime.now(timezone.utc),
        channel_tp9=740.0, channel_af7=760.0,
        channel_af8=755.0, channel_tp10=745.0,
    )
    for _ in range(6):
        processor.update(sample, {**seated, "hsi": [1, 1, 1, 1]})
    blip = processor.update(sample, {**seated, "hsi": [4, 4, 4, 4]})
    assert blip["signal_quality"] == "good"


def _sample(level=740.0):
    return EegSample(
        timestamp=datetime.now(timezone.utc),
        channel_tp9=level, channel_af7=level + 20,
        channel_af8=level + 15, channel_tp10=level + 5,
    )


def test_unusable_samples_are_kept_out_of_the_rolling_window():
    """A frame where every electrode reports bad data must not enter the
    window, or it skews the mean/spread/stability for the window's whole
    length."""
    processor = SignalProcessor(window_size=8)
    processor.update(_sample(), {**_ENGAGED_BANDS, "is_good": [1, 1, 1, 1]})
    assert len(processor.window) == 1

    # Wildly different amplitude, flagged entirely invalid -- must not be kept.
    processor.update(_sample(5000.0), {**_ENGAGED_BANDS, "is_good": [0, 0, 0, 0]})
    assert len(processor.window) == 1

    # A partially-good frame is still real data and should be kept.
    processor.update(_sample(), {**_ENGAGED_BANDS, "is_good": [0, 1, 1, 0]})
    assert len(processor.window) == 2


def test_window_filtering_never_empties_the_window():
    # The feature math needs at least one sample; a run of bad frames should
    # hold the last known-good reading rather than leave nothing to compute.
    processor = SignalProcessor(window_size=4)
    for _ in range(6):
        features = processor.update(_sample(), {**_ENGAGED_BANDS, "is_good": [0, 0, 0, 0]})
    assert len(processor.window) >= 1
    assert features["focus_score"] is not None


def test_samples_without_contact_data_are_never_discarded():
    processor = SignalProcessor(window_size=8)
    for _ in range(3):
        processor.update(_sample(), _ENGAGED_BANDS)  # no is_good key
    assert len(processor.window) == 3


def test_baseline_ignores_the_frames_the_window_rejects():
    """The baseline must reject the same frames the window rejects. It
    latches after BASELINE_SAMPLES and is never revisited, so an artifact
    during warm-up would shift every score for the rest of the session, not
    just one window's worth.
    """
    good = {**_ENGAGED_BANDS, "is_good": [1, 1, 1, 1], "hsi": [1, 1, 1, 1]}
    bad = {**_ENGAGED_BANDS, "is_good": [0, 0, 0, 0], "hsi": [4, 4, 4, 4]}

    clean = SignalProcessor(window_size=8)
    while not clean._baseline_ready:
        clean.update(_sample(), good)

    polluted = SignalProcessor(window_size=8)
    i = 0
    while not polluted._baseline_ready:
        # Every third frame is a fully invalid artifact at a wildly different
        # amplitude -- the same kind the window already refuses.
        polluted.update(_sample(5000.0) if i % 3 == 2 else _sample(),
                        bad if i % 3 == 2 else good)
        i += 1

    assert polluted._samples_rejected > 0, "test must actually exercise rejection"
    assert polluted._baseline_focus_mean == pytest.approx(clean._baseline_focus_mean, abs=1e-9)
    assert polluted._baseline_calm_mean == pytest.approx(clean._baseline_calm_mean, abs=1e-9)

    # The observable consequence: an identical good frame must score the same.
    c = clean.update(_sample(), good)
    p = polluted.update(_sample(), good)
    assert p["focus_score"] == pytest.approx(c["focus_score"], abs=0.01)
    assert p["calm_score"] == pytest.approx(c["calm_score"], abs=0.01)


def test_amplitude_path_excludes_electrodes_the_headband_flagged():
    """_sample_is_usable only rejects a frame when *every* electrode is bad,
    so ear contacts failing while the frontals read cleanly still reach the
    amplitude math. mean_spread is max-min across channels, so one railing
    electrode would dominate it -- the amplitude term is 25% of the blended
    scores and 32% of the confidence weight."""
    def ears(tp9, tp10):
        # Identical clean frontals; only the flagged ear electrodes differ.
        return EegSample(
            timestamp=datetime.now(timezone.utc),
            channel_tp9=tp9, channel_af7=720.0, channel_af8=715.0, channel_tp10=tp10,
        )

    flagged_meta = {**_ENGAGED_BANDS, "is_good": [0, 1, 1, 0], "hsi": [4, 1, 1, 4]}
    unflagged_meta = {**_ENGAGED_BANDS, "is_good": [1, 1, 1, 1], "hsi": [1, 1, 1, 1]}

    def run(sample, meta):
        processor = SignalProcessor(window_size=8)
        for _ in range(8):
            features = processor.update(sample, meta)
        return processor, features

    # Two different readings on the flagged ear electrodes, kept inside
    # CALM_MIN_SPREAD..CALM_MAX_SPREAD rather than fully railing, so the
    # unflagged control below lands on distinct scores instead of both
    # clamping to 0.
    proc_a, a = run(ears(730.0, 700.0), flagged_meta)
    _, b = run(ears(800.0, 650.0), flagged_meta)

    # The frames were admitted (not all-bad), but only the vouched-for
    # electrodes were stored, so what the ears read cannot matter.
    assert len(proc_a.window) == 8
    assert len(proc_a.window[0]) == 2
    for key in ("focus_score", "calm_score", "confidence"):
        assert a[key] == pytest.approx(b[key], abs=0.01), f"{key} moved with a flagged electrode"

    # With the same two readings *unflagged*, the scores must diverge -- this
    # confirms the assertion above isn't passing vacuously.
    _, a_raw = run(ears(730.0, 700.0), unflagged_meta)
    _, b_raw = run(ears(800.0, 650.0), unflagged_meta)
    assert abs(a_raw["calm_score"] - b_raw["calm_score"]) > 5.0


def test_mean_level_weights_frames_equally_regardless_of_channel_count():
    """Window entries vary in width since bad electrodes are dropped. Pooling
    every channel value would weight a 4-channel frame twice as heavily as a
    2-channel one, drifting the level toward whichever regime contributed
    more channels."""
    now = datetime.now(timezone.utc)
    all_good = {"is_good": [1, 1, 1, 1], "hsi": [1, 1, 1, 1]}
    ears_bad = {"is_good": [0, 1, 1, 0], "hsi": [4, 1, 1, 4]}
    # Frontals sit at 800, ears at 600, so the two regimes have genuinely
    # different means and any weighting error is visible.
    sample = EegSample(timestamp=now, channel_tp9=600.0, channel_af7=800.0,
                       channel_af8=800.0, channel_tp10=600.0)

    processor = SignalProcessor(window_size=4)
    processor.update(sample, all_good)              # 4 channels, frame mean 700
    features = processor.update(sample, ears_bad)   # 2 channels, both 800
    assert [len(v) for v in processor.window] == [4, 2]

    def to_score(level):
        return SignalProcessor._clamp01(
            (level - SignalProcessor.FOCUS_MIN_LEVEL)
            / (SignalProcessor.FOCUS_MAX_LEVEL - SignalProcessor.FOCUS_MIN_LEVEL)
        ) * 100.0

    # Equal weight per frame: (700 + 800) / 2 = 750.
    per_frame = fmean([700.0, 800.0])
    # Pooling all six channel values instead gives 4400 / 6 = 733.3, dragged
    # toward the 4-channel frame purely because it has more values.
    pooled = fmean([600.0, 800.0, 800.0, 600.0, 800.0, 800.0])
    assert per_frame == pytest.approx(750.0, abs=0.01)
    assert pooled == pytest.approx(733.33, abs=0.01)

    # No band data here, so focus_score is 100% amplitude.
    assert features["focus_score"] == pytest.approx(to_score(per_frame), abs=0.01)
    assert features["focus_score"] != pytest.approx(to_score(pooled), abs=0.01)


def test_contradictory_contact_flags_keep_all_four_channels():
    """is_good says every channel is usable, hsi says nothing is seated.
    _good_channel_values would exclude everything on that disagreement, so it
    falls back to all four rather than discarding data on the more
    pessimistic of two conflicting signals."""
    now = datetime.now(timezone.utc)
    sample = EegSample(timestamp=now, channel_tp9=700.0, channel_af7=705.0,
                       channel_af8=695.0, channel_tp10=702.0)
    processor = SignalProcessor(window_size=4)
    processor.update(sample, {"is_good": [1, 1, 1, 1], "hsi": [4, 4, 4, 4]})
    assert len(processor.window) == 1
    assert len(processor.window[0]) == 4


def test_single_good_electrode_does_not_fabricate_perfect_calm():
    """With one usable channel, max-min is 0, which the calm amplitude term
    would read as a perfectly steady signal -- inventing "maximally calm"
    from an almost-dead headband."""
    one_good = {"is_good": [0, 1, 0, 0], "hsi": [4, 1, 4, 4]}
    processor = SignalProcessor(window_size=4)
    for _ in range(4):
        features = processor.update(_sample(), one_good)
    assert len(processor.window[0]) == 1
    assert features["calm_score"] == pytest.approx(50.0, abs=0.01)


def test_diagnostics_are_surfaced_in_the_feature_payload():
    processor = SignalProcessor(window_size=4)
    processor.update(_sample(), {**_ENGAGED_BANDS, "is_good": [1, 1, 1, 1]})
    features = processor.update(
        _sample(5000.0),
        {**_ENGAGED_BANDS, "is_good": [0, 0, 0, 0], "band_channels_used": 2},
    )
    assert features["samples_rejected"] == 1
    assert features["band_channels_used"] == 2


def test_scores_center_on_baseline_once_it_is_established():
    """After the baseline period, holding steady at the learner's own resting
    level should read mid-scale, not wherever fixed population bounds happen
    to place that individual."""
    processor = SignalProcessor(window_size=8)
    steady = {**_ENGAGED_BANDS, "is_good": [1, 1, 1, 1]}
    features = None
    for _ in range(SignalProcessor.BASELINE_SAMPLES + 5):
        features = processor.update(_sample(), steady)
    assert processor._baseline_ready is True
    # Band term is centered at 0.5; blended with the amplitude term, the
    # final score stays near mid-scale rather than pinned to an extreme.
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
    manager = _make_session()
    payload = manager._no_signal_payload()
    assert payload["features"]["focus_score"] == 0.0
    assert payload["features"]["calm_score"] == 0.0
    assert payload["features"]["confidence"] == 0.0
    assert payload["features"]["signal_quality"] == "no_signal"
    assert payload["state"]["label"] == "no_signal"
    # No question_policy anywhere. Difficulty is decided in the backend from
    # correctness, topic history and grade -- the sidecar can't see any of
    # that, so it must not compute its own policy.
    assert "question_policy" not in payload


def test_stream_manager_loop_zeroes_scores_and_resets_state_on_read_failure():
    class FailingAdapter:
        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def read_sample(self):
            raise RuntimeError("no data")

    async def run_case():
        manager = _make_session()
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
        manager = _make_session()
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
    # Adapters (real and simulated) cache their last-known band values and
    # don't reset them on disconnect. snapshot() must zero the bands on
    # no-signal rather than pulling them live from the adapter, or the EEG
    # Bands display would show stale non-zero values after a disconnect.
    async def run_case():
        manager = _make_session()
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
        manager = _make_session()
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
        manager = _make_session()
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
            # Simulate a lock/contention delay inside metadata retrieval.
            time.sleep(0.2)
            return {"alpha": 1.0, "beta": 1.0, "theta": 1.0, "gamma": 1.0}

    async def run_case() -> int:
        manager = _make_session()
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


def test_stream_manager_loop_batch_drain_does_not_recalibrate_baseline_or_window():
    """SignalProcessor.window and the per-session baseline are calibrated in
    *ticks*, not raw samples. If stream_manager._loop called processor.update()
    once per drained sample instead of once per tick, draining dozens of
    samples in one tick would collapse the ~15s baseline warmup and ~5s window
    into a single tick, latching the baseline on one band reading instead of a
    real resting-state average. Feeding only the freshest drained sample per
    tick keeps update() at one call per tick regardless of batch size.
    """
    batch_size = 64

    class BatchAdapter:
        def __init__(self) -> None:
            self._n = 0

        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def drain_samples(self, max_batch: int):
            samples = []
            for _ in range(batch_size):
                self._n += 1
                samples.append(
                    EegSample(
                        timestamp=datetime.now(timezone.utc),
                        channel_tp9=700.0 + self._n,
                        channel_af7=700.0 + self._n,
                        channel_af8=700.0 + self._n,
                        channel_tp10=700.0 + self._n,
                    )
                )
            return samples

        def get_ingestion_meta(self):
            # Same values for the whole tick, as real adapters do -- metadata
            # is fetched once per tick, not once per drained sample.
            return {"alpha": 0.30, "beta": 0.20, "theta": 0.10, "gamma": 0.05}

    async def run_case():
        manager = _make_session()
        manager.adapter = BatchAdapter()
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
    # A tick's worth of drained samples must count as ONE tick toward the
    # window/baseline, not `batch_size` ticks.
    assert len(manager.processor.window) < batch_size
    assert len(manager.processor._baseline_focus) < batch_size
    assert not manager.processor._baseline_ready
    # But the full batch is still visible for diagnostics.
    assert manager.latest_payload["features"]["batch_size"] == batch_size


# --- device registry (multi-headband) ---


def test_parse_eeg_devices_defaults_to_single_default_device_when_unset():
    settings = get_settings().model_copy(update={"eeg_devices": ""})
    devices = parse_eeg_devices(settings)
    assert set(devices) == {"default"}
    assert devices["default"].kind == settings.eeg_source.lower().strip()
    assert devices["default"].host == settings.muse_bridge_host
    assert devices["default"].port == settings.muse_bridge_port


def test_parse_eeg_devices_parses_multi_entry_with_and_without_port_override():
    settings = get_settings().model_copy(
        update={"eeg_devices": "station1:muse@8766,station2:muse@192.168.1.5:8767,station3:sim"}
    )
    devices = parse_eeg_devices(settings)
    assert set(devices) == {"station1", "station2", "station3"}
    assert devices["station1"].kind == "muse"
    assert devices["station1"].host == settings.muse_bridge_host
    assert devices["station1"].port == 8766
    assert devices["station2"].host == "192.168.1.5"
    assert devices["station2"].port == 8767
    assert devices["station3"].kind == "sim"


def test_parse_eeg_devices_rejects_malformed_entries():
    with pytest.raises(ValueError):
        parse_eeg_devices(get_settings().model_copy(update={"eeg_devices": "station1"}))
    with pytest.raises(ValueError):
        parse_eeg_devices(get_settings().model_copy(update={"eeg_devices": "station1:not-a-kind"}))
    with pytest.raises(ValueError):
        parse_eeg_devices(get_settings().model_copy(update={"eeg_devices": "station1:sim,station1:sim"}))
    with pytest.raises(ValueError, match="empty host"):
        parse_eeg_devices(get_settings().model_copy(update={"eeg_devices": "station1:muse@:8766"}))


def test_parse_eeg_devices_rejects_two_muse_devices_on_the_same_bridge():
    # No explicit port on either entry -- both would silently resolve to the
    # same default host:port and contend for one single-client bridge process.
    with pytest.raises(ValueError, match="already used by another muse device"):
        parse_eeg_devices(get_settings().model_copy(update={"eeg_devices": "station1:muse,station2:muse"}))
    # Same failure with explicit matching ports.
    with pytest.raises(ValueError, match="already used by another muse device"):
        parse_eeg_devices(
            get_settings().model_copy(update={"eeg_devices": "station1:muse@8766,station2:muse@8766"})
        )
    # sim devices share no underlying process, so no such constraint applies.
    devices = parse_eeg_devices(get_settings().model_copy(update={"eeg_devices": "station1:sim,station2:sim"}))
    assert set(devices) == {"station1", "station2"}


def test_list_devices_endpoint_shape():
    client = TestClient(app)
    settings = get_settings()
    learner_headers = {"Authorization": f"Bearer {settings.api_token}"}
    r = client.get("/api/v1/devices", headers=learner_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert isinstance(body["data"], list)
    assert any(d["device_id"] == "default" for d in body["data"])
    entry = next(d for d in body["data"] if d["device_id"] == "default")
    assert "running" in entry
    assert "kind" in entry
    assert "connection_state_name" in entry


def test_list_devices_requires_auth():
    client = TestClient(app)
    assert client.get("/api/v1/devices").status_code == 401


def test_unknown_device_id_returns_404_across_endpoints():
    client = TestClient(app)
    settings = get_settings()
    admin_headers = {"Authorization": f"Bearer {settings.admin_token}"}
    learner_headers = {"Authorization": f"Bearer {settings.api_token}"}
    assert client.get("/api/v1/state", params={"device_id": "nope"}, headers=learner_headers).status_code == 404
    assert client.get("/api/v1/muse/status", params={"device_id": "nope"}, headers=learner_headers).status_code == 404
    assert client.post("/api/v1/session/start", params={"device_id": "nope"}, headers=admin_headers).status_code == 404
    assert client.post("/api/v1/muse/refresh", params={"device_id": "nope"}, headers=admin_headers).status_code == 404


async def _counter_advances(manager, baseline: int, timeout: float = 5.0) -> bool:
    """Whether `manager.samples_processed` gets past `baseline` within timeout.

    Waits for the counter to move rather than sleeping a fixed window. The
    stream loop ticks once per 1 / settings.eeg_sample_hz seconds (0.25s at
    the default 4 Hz), so a fixed window close to one tick can miss a tick on
    timing jitter alone and fail flakily.

    The timeout is generous on purpose: the callers below are testing that a
    loop keeps running at all, so only a genuinely frozen one should reach the
    deadline. A slow runner still passes.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while manager.samples_processed <= baseline:
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.01)
    return True


def test_counter_advance_helper_detects_a_stopped_loop():
    """Confirms _counter_advances can actually fail, so the progress
    assertions elsewhere in this file aren't vacuous. A stopped manager's
    counter never moves, and the timeout is short since it's expected to
    expire.
    """
    async def run_case():
        manager = _make_session("dev-stopped")
        manager.adapter = SimulatedMuseIngestionAdapter()
        await manager.start()
        assert await _counter_advances(manager, 0), "loop never started"
        await manager.stop()
        frozen_at = manager.samples_processed
        return await _counter_advances(manager, frozen_at, timeout=0.5)

    assert asyncio.run(run_case()) is False


def test_two_sim_devices_run_independently():
    async def run_case():
        manager_a = _make_session("dev-a")
        manager_b = _make_session("dev-b")
        manager_a.adapter = SimulatedMuseIngestionAdapter()
        manager_b.adapter = SimulatedMuseIngestionAdapter()
        await manager_a.start()
        await manager_b.start()
        a_started = await _counter_advances(manager_a, 0)
        b_started = await _counter_advances(manager_b, 0)
        b_before = manager_b.samples_processed
        await manager_a.stop()
        # Stopping A must not freeze or reset B's counter.
        b_kept_going = await _counter_advances(manager_b, b_before)
        # Snapshot B's state before stopping it too -- stop() zeroes
        # latest_payload, so capturing after both stops would prove nothing.
        b_signal_quality = manager_b.latest_payload["features"]["signal_quality"]
        await manager_b.stop()
        return (manager_a, manager_b, a_started, b_started,
                b_kept_going, b_signal_quality)

    (manager_a, manager_b, a_started, b_started,
     b_kept_going, b_signal_quality) = asyncio.run(run_case())
    assert a_started, "device A never processed a sample"
    assert b_started, "device B never processed a sample"
    # Independent counters -- stopping device A doesn't freeze or reset
    # device B's tally.
    assert b_kept_going, "device B's counter stopped advancing when A was stopped"
    assert manager_a.latest_payload["features"]["signal_quality"] == "no_signal"
    assert b_signal_quality != "no_signal"
    assert manager_a.device_id == "dev-a"
    assert manager_b.device_id == "dev-b"
