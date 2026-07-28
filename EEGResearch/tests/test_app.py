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
    # Regression test: FeatureData.signal_quality is a strict Pydantic Literal.
    # A "no_signal" payload must actually serialize through /api/v1/state (not
    # just be correct as an in-memory dict) or every real disconnect/stop
    # would 500 instead of reporting zeroed scores.
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
            assert data["contract_version"] == "1.2.0"
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


class _ReaderThreadStub:
    """Stand-in for TcpMuseBridgeAdapter's reader thread.

    read_sample() calls is_alive() on the timeout path (to detect a bridge that
    dropped) and disconnect() joins the thread, so a bare object() is not
    enough to stand in for one.
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
    # Reader alive but no data arriving: should time out, not block forever,
    # and should leave the connection in place for the next poll.
    adapter._reader_thread = _ReaderThreadStub(alive=True)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="No EEG sample received from bridge"):
        adapter.read_sample()
    assert time.monotonic() - started < 2.0
    assert adapter._reader_thread is not None


def test_tcp_bridge_read_sample_resets_connection_when_reader_died():
    # Reader thread has exited (bridge disconnected): read_sample() should
    # still raise a recoverable error, but also tear the connection down so the
    # next call reconnects instead of waiting on a queue nothing is feeding.
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


def test_simulator_bands_stay_within_processor_ratio_bounds():
    """The simulator is a *producer* of band powers, so it has to emit them on
    the same scale the processor reads them (Bels), across its whole hidden
    state domain.

    Emitting linear magnitudes instead is silently catastrophic rather than
    loud: the processor exponentiates them to ~10**40, both log-ratios land
    around +-31 against bounds spanning ~1.6, and every spectral term clamps.
    The scores still come back in 0..100 -- only the 25% amplitude term is
    still moving -- so nothing raises and nothing looks obviously wrong. This
    asserts on the raw ratios rather than the scores for exactly that reason.
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
            # Bels, in the range live Muse S captures actually produce.
            for band in ("delta", "theta", "alpha", "beta", "gamma"):
                assert -2.0 <= meta[band] <= 2.0, f"{band}={meta[band]} is not a plausible Bel value"


def test_simulator_focus_and_calm_are_not_mirror_images():
    """Under linear band values, alpha/beta reach ~10**40 while theta/gamma sit
    at ~10**4, so theta and gamma become numerically negligible: focus collapses
    to ln(beta/alpha) and calm to ln(alpha/beta), exact negations. The two
    scores then carry identical information and AdaptationEngine cannot
    separate states. Holding focus fixed while calm_state moves must move calm
    and leave focus alone."""
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
    """10.0**x overflows past ~308. That means an upstream producer isn't
    emitting Bels -- the same class of problem as a malformed value -- so it
    has to be handled here. Letting OverflowError escape sends it to
    stream_manager's broad except, which drops the tick, and a dropped tick
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
    # Older bridge with no HSI/IS_GOOD: the legacy calm/confidence heuristic
    # applies, and must be labelled as such so callers don't mistake its
    # verdict for a statement about the electrodes.
    explicit_none = _quality_for({**_ENGAGED_BANDS, "hsi": None, "is_good": None})
    absent = _quality_for(_ENGAGED_BANDS)
    assert explicit_none["signal_quality"] == absent["signal_quality"]
    assert explicit_none["quality_basis"] == "heuristic"
    assert absent["quality_basis"] == "heuristic"
    # And specifically: an engaged learner trips the heuristic's calm gate, so
    # it reports "poor" for a perfectly good signal. This is exactly why
    # quality_basis exists -- see the persistence guard in eeg_poller.
    assert absent["signal_quality"] == "poor"


def test_heuristic_poor_is_distinguishable_from_contact_poor():
    """Regression guard for the old-bridge path.

    Both report "poor", but only one means the electrodes are bad. Consumers
    gate data collection on this distinction; collapsing it would make an
    outdated bridge silently record nothing for an entire session.
    """
    heuristic = _quality_for(_ENGAGED_BANDS)
    contact = _quality_for({**_ENGAGED_BANDS, "hsi": [4, 4, 4, 4], "is_good": [0, 0, 0, 0]})
    assert heuristic["signal_quality"] == contact["signal_quality"] == "poor"
    assert heuristic["quality_basis"] == "heuristic"
    assert contact["quality_basis"] == "contact"


def test_signal_quality_ignores_malformed_contact_values():
    # Garbage in the contact fields must not be read as a contact verdict:
    # falling through to the heuristic is correct, silently treating it as
    # "contact says poor" would gate persistence on nonsense.
    features = _quality_for({**_ENGAGED_BANDS, "hsi": ["x", None], "is_good": "nope"})
    assert features["quality_basis"] == "heuristic"
    assert features["signal_quality"] == _quality_for(_ENGAGED_BANDS)["signal_quality"]


def test_a_single_hsi_blip_does_not_drop_quality_to_poor():
    """HSI is smoothed on the same basis as IS_GOOD.

    Previously fit_score was instantaneous while good_channels was smoothed,
    and quality took the worse of the two -- so one bad HSI frame bypassed the
    smoothing entirely and dropped straight to poor.
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


def test_baseline_ignores_the_frames_the_window_rejects():
    """The baseline must be gated on the same usability verdict as the window.

    Baselining a frame the window rejected is worse than the window pollution
    it mirrors: the baseline latches after BASELINE_SAMPLES and is never
    revisited, so artifacts during warm-up shift every score for the rest of
    the session rather than for one window length.
    """
    good = {**_ENGAGED_BANDS, "is_good": [1, 1, 1, 1], "hsi": [1, 1, 1, 1]}
    bad = {**_ENGAGED_BANDS, "is_good": [0, 0, 0, 0], "hsi": [4, 4, 4, 4]}

    clean = SignalProcessor(window_size=8)
    while not clean._baseline_ready:
        clean.update(_sample(), good)

    polluted = SignalProcessor(window_size=8)
    i = 0
    while not polluted._baseline_ready:
        # Every third frame is a fully-invalid artifact at a wildly different
        # amplitude -- the same thing the window already refuses.
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
    """_sample_is_usable only rejects when *every* electrode is bad, so the
    motivating scenario -- ear contacts failing while the frontals read
    cleanly -- reaches the amplitude maths. mean_spread is max-min across
    channels, so one railing electrode dominates it, and the amplitude term is
    25% of the blended scores and 32% of the confidence weight."""
    def ears(tp9, tp10):
        # Identical clean frontals; only the two flagged ear electrodes differ.
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

    # Two different sets of readings on the flagged ear electrodes. Kept inside
    # CALM_MIN_SPREAD..CALM_MAX_SPREAD rather than fully railing, so the
    # unflagged control below lands on distinct scores instead of both clamping
    # to 0 -- a railing electrode saturates the calm term outright, which is a
    # larger error than this measures, not a smaller one.
    proc_a, a = run(ears(730.0, 700.0), flagged_meta)
    _, b = run(ears(800.0, 650.0), flagged_meta)

    # The frames were admitted (not all-bad), but only the two vouched-for
    # electrodes were stored, so what the ears read cannot matter at all.
    assert len(proc_a.window) == 8
    assert len(proc_a.window[0]) == 2
    for key in ("focus_score", "calm_score", "confidence"):
        assert a[key] == pytest.approx(b[key], abs=0.01), f"{key} moved with a flagged electrode"

    # Guard against the assertion above passing vacuously: with the same two
    # readings *unflagged*, the scores must diverge -- which is the old
    # behaviour, and the size of the error the mask prevents.
    _, a_raw = run(ears(730.0, 700.0), unflagged_meta)
    _, b_raw = run(ears(800.0, 650.0), unflagged_meta)
    assert abs(a_raw["calm_score"] - b_raw["calm_score"]) > 5.0


def test_mean_level_weights_frames_equally_regardless_of_channel_count():
    """Window entries vary in width now that bad electrodes are dropped.
    Pooling every channel value would weight a 4-channel frame twice as
    heavily as a 2-channel one, so during a contact transition the level would
    drift toward whichever regime contributed more channels."""
    now = datetime.now(timezone.utc)
    all_good = {"is_good": [1, 1, 1, 1], "hsi": [1, 1, 1, 1]}
    ears_bad = {"is_good": [0, 1, 1, 0], "hsi": [4, 1, 1, 4]}
    # Frontals sit at 800; the ears sit at 600, so the two regimes have
    # genuinely different means and any weighting error is visible.
    sample = EegSample(timestamp=now, channel_tp9=600.0, channel_af7=800.0,
                       channel_af8=800.0, channel_tp10=600.0)

    processor = SignalProcessor(window_size=4)
    processor.update(sample, all_good)              # 4 channels -> frame mean 700
    features = processor.update(sample, ears_bad)   # 2 channels (both 800)
    assert [len(v) for v in processor.window] == [4, 2]

    def to_score(level):
        return SignalProcessor._clamp01(
            (level - SignalProcessor.FOCUS_MIN_LEVEL)
            / (SignalProcessor.FOCUS_MAX_LEVEL - SignalProcessor.FOCUS_MIN_LEVEL)
        ) * 100.0

    # Equal weight per frame: (700 + 800) / 2 = 750.
    per_frame = fmean([700.0, 800.0])
    # Pooling all six channel values instead: 4400 / 6 = 733.3, dragged toward
    # the 4-channel frame purely because it contributed more values.
    pooled = fmean([600.0, 800.0, 800.0, 600.0, 800.0, 800.0])
    assert per_frame == pytest.approx(750.0, abs=0.01)
    assert pooled == pytest.approx(733.33, abs=0.01)

    # focus_score is 100% amplitude here: no band data, so no spectral blend.
    assert features["focus_score"] == pytest.approx(to_score(per_frame), abs=0.01)
    assert features["focus_score"] != pytest.approx(to_score(pooled), abs=0.01)


def test_contradictory_contact_flags_keep_all_four_channels():
    """is_good says every channel is usable, hsi says nothing is seated. The
    two disagree; _sample_is_usable admits the frame and _good_channel_values
    would exclude everything, so it falls back to all four rather than
    discarding data on the more pessimistic of two conflicting signals."""
    now = datetime.now(timezone.utc)
    sample = EegSample(timestamp=now, channel_tp9=700.0, channel_af7=705.0,
                       channel_af8=695.0, channel_tp10=702.0)
    processor = SignalProcessor(window_size=4)
    processor.update(sample, {"is_good": [1, 1, 1, 1], "hsi": [4, 4, 4, 4]})
    assert len(processor.window) == 1
    assert len(processor.window[0]) == 4


def test_single_good_electrode_does_not_fabricate_perfect_calm():
    """With one usable channel, max-min is 0, which the calm amplitude term
    would read as a perfectly steady signal -- inventing "maximally calm" from
    an almost-dead headband. Absence of a spread reading is not evidence."""
    one_good = {"is_good": [0, 1, 0, 0], "hsi": [4, 1, 4, 4]}
    processor = SignalProcessor(window_size=4)
    for _ in range(4):
        features = processor.update(_sample(), one_good)  # no band data
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
    manager = _make_session()
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
    # Regression test: snapshot() used to pull "bands" live from
    # adapter.get_ingestion_meta() unconditionally, bypassing the zeroed
    # no-signal payload entirely. Adapters (real and simulated) cache their
    # last-known band values and don't reset them on disconnect, so the EEG
    # Bands display kept showing stale non-zero values after a disconnect
    # even though focus/calm/confidence correctly zeroed out.
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
            # Simulate lock/contention delay inside adapter metadata retrieval.
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
    """Regression: stream_manager._loop used to call processor.update() once
    per drained sample instead of once per tick. SignalProcessor.window
    (window_size) and the per-session baseline (BASELINE_SAMPLES) are
    calibrated in *ticks*, not raw samples -- draining dozens of samples in a
    single tick (all sharing the one raw_meta fetched for that tick, since
    band metadata is fetched once per tick, not once per drained sample)
    collapsed the ~15s baseline warmup and ~5s window into a single tick,
    latching the baseline on one band reading instead of a real resting-state
    average. Feeding only the freshest drained sample per tick keeps
    processor.update() at its designed one-call-per-tick cadence regardless
    of how many samples the adapter buffered.
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
            # Same values for the whole tick, as the real adapters do --
            # metadata is fetched once per tick, not once per drained sample.
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
    # window/baseline, not `batch_size` of them.
    assert len(manager.processor.window) < batch_size
    assert len(manager.processor._baseline_focus) < batch_size
    assert not manager.processor._baseline_ready
    # But the full batch is still visible for diagnostics.
    assert manager.latest_payload["features"]["batch_size"] == batch_size


# ─── device registry (multi-headband) ───────────────────────────────────


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
    # same default MUSE_BRIDGE_HOST:MUSE_BRIDGE_PORT and contend for one
    # single-client bridge process.
    with pytest.raises(ValueError, match="already used by another muse device"):
        parse_eeg_devices(get_settings().model_copy(update={"eeg_devices": "station1:muse,station2:muse"}))
    # Same failure spelled out with explicit matching ports.
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


def test_two_sim_devices_run_independently():
    async def run_case():
        manager_a = _make_session("dev-a")
        manager_b = _make_session("dev-b")
        manager_a.adapter = SimulatedMuseIngestionAdapter()
        manager_b.adapter = SimulatedMuseIngestionAdapter()
        await manager_a.start()
        await manager_b.start()
        for _ in range(5):
            await asyncio.sleep(0.05)
        b_before = manager_b.samples_processed
        await manager_a.stop()
        for _ in range(5):
            await asyncio.sleep(0.05)
        # Snapshot B's state here, before stopping it too, since stop() itself
        # zeroes latest_payload -- capturing after both stops would prove
        # nothing about B being independent of A's stop.
        b_after = manager_b.samples_processed
        b_signal_quality = manager_b.latest_payload["features"]["signal_quality"]
        await manager_b.stop()
        return manager_a, manager_b, b_before, b_after, b_signal_quality

    manager_a, manager_b, b_before, b_after, b_signal_quality = asyncio.run(run_case())
    assert manager_a.samples_processed > 0
    assert b_before > 0
    # Independent counters -- stopping device A doesn't freeze or reset
    # device B's tally, which keeps advancing on its own loop.
    assert b_after > b_before
    assert manager_a.latest_payload["features"]["signal_quality"] == "no_signal"
    assert b_signal_quality != "no_signal"
    assert manager_a.device_id == "dev-a"
    assert manager_b.device_id == "dev-b"
