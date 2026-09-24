"""Every payload key survives `/api/v1/state`: the response model silently drops undeclared keys."""

import re

import pytest

from src.app.schemas import Envelope, InterpretedEegData
from src.app.services import stream_manager as sm

EEG_SNAPSHOT = {
    "contract_version": "1.3.0",
    "device_id": "default",
    "timestamp": "2026-08-15T22:00:00+00:00",
    "channels": {"tp9": 819.37, "af7": 828.87, "af8": 840.91, "tp10": 817.55},
    "features": {
        "focus_score": 88.1, "calm_score": 42.2, "confidence": 73.8,
        "signal_quality": "degraded", "quality_basis": "contact",
        "samples_rejected": 61, "band_channels_used": 2, "batch_size": 76,
    },
    "state": {
        "label": "focused", "reason": "Cooldown: hold prior state",
        "confidence": 73.8, "focus_score": 88.1, "calm_score": 42.2,
    },
    "bands": {"delta": 1.0, "theta": 2.0, "alpha": 3.0, "beta": 4.0, "gamma": 5.0},
    "ingestion": {"eeg_source": "muse", "optics_packets": 2697},
}

# `build_heart_record` for an accepted window; the poller dedupes on `ts` and `source`.
HEART_BLOCK = {
    "source": "muse_optics",
    "ts": "2026-08-15T22:00:00+00:00",
    "bpm": 68.4,
    "confidence": 0.82,
    "trusted": True,
    "rejected_by": None,
    "rmssd_ms": 41.2,
    "rmssd_rejected_by": None,
    "channel_count": 4,
    "sample_rate_hz": 64.0,
}


def _through_envelope(snapshot):
    """Exactly what the endpoint does with a snapshot."""
    return Envelope(status="ok", data=snapshot, message="x").model_dump()["data"]


def test_the_headband_heart_block_survives_the_state_endpoint():
    out = _through_envelope({**EEG_SNAPSHOT, "heart": HEART_BLOCK})

    assert out.get("heart") is not None, (
        "the heart block was dropped by the response model -- the pull poller "
        "reads this endpoint, so the headband records no heart rate at all"
    )
    assert out["heart"]["bpm"] == pytest.approx(68.4)
    assert out["heart"]["source"] == "muse_optics"
    # Without it, dedup falls back to the tick clock.
    assert out["heart"]["ts"] == HEART_BLOCK["ts"]


def test_a_refused_window_keeps_its_reason_rather_than_becoming_an_absent_block():
    """`bpm: None` with a reason is a refused measurement; an absent block is no optical channel."""
    refused = {**HEART_BLOCK, "bpm": None, "trusted": False,
               "rejected_by": "unconfirmed_anchor"}

    out = _through_envelope({**EEG_SNAPSHOT, "heart": refused})

    assert out["heart"]["bpm"] is None
    assert out["heart"]["rejected_by"] == "unconfirmed_anchor"


def test_an_eeg_payload_with_no_optics_still_validates():
    """A sim device, or a headband on PRESET_21, sets no heart key at all."""
    out = _through_envelope(EEG_SNAPSHOT)

    assert out["heart"] is None
    assert out["features"]["focus_score"] == pytest.approx(88.1)


def test_every_key_the_eeg_payload_carries_is_declared_on_the_model():
    """Key list is derived from `stream_manager`'s source, not hand-kept."""
    import inspect

    source = inspect.getsource(sm)

    keys = set()
    # Top-level keys of the latest_payload literal only; `channels` nests its own dict.
    literal = re.search(r"self\.latest_payload = \{(.*?)\n                \}",
                        source, re.DOTALL)
    if literal:
        found = re.findall(r'^([ \t]*)"(\w+)":', literal.group(1), re.MULTILINE)
        if found:
            top = min(len(indent) for indent, _ in found)
            keys |= {name for indent, name in found if len(indent) == top}
    # Keys attached afterwards, plus the ones snapshot() adds on the way out.
    keys |= set(re.findall(r'self\.latest_payload\["(\w+)"\]\s*=', source))
    keys |= set(re.findall(r'out\["(\w+)"\]\s*=', source))
    keys |= set(re.findall(r'out\.setdefault\("(\w+)"', source))

    assert "heart" in keys, "the payload no longer carries heart; this test is stale"
    assert "channels" in keys, "the literal did not parse; this test is looking at nothing"

    declared = set(InterpretedEegData.model_fields)
    missing = keys - declared
    assert not missing, (
        f"{sorted(missing)} reach latest_payload but are not declared on "
        "InterpretedEegData, so pydantic will drop them from /api/v1/state "
        "without raising -- the pull poller reads that endpoint"
    )


def test_every_feature_key_the_processor_returns_is_declared_on_the_model():
    """`features` is its own nested model; keys come from calling the processor, so conditional ones count."""
    from datetime import datetime, timezone

    from src.app.models import EegSample
    from src.app.schemas import FeatureData
    from src.app.services.signal_processing import SignalProcessor

    processor = SignalProcessor(window_size=4)
    sample = EegSample(
        timestamp=datetime.now(timezone.utc),
        channel_tp9=590.0, channel_af7=600.0, channel_af8=605.0, channel_tp10=595.0,
    )
    bands = {"delta": 0.4, "theta": 0.3, "alpha": 0.5, "beta": 0.4, "gamma": 0.2,
             "hsi": [1.0, 1.0, 1.0, 1.0], "is_good": [1.0, 1.0, 1.0, 1.0],
             "band_channels_used": 4}
    keys = set(processor.update(sample, bands)) | set(processor.update(sample, None))
    # stream_manager attaches this one after the call.
    keys.add("batch_size")

    assert "focus_log_ratio" in keys, "the processor no longer reports it; this test is stale"

    missing = keys - set(FeatureData.model_fields)
    assert not missing, (
        f"{sorted(missing)} are returned by SignalProcessor.update but not "
        "declared on FeatureData, so /api/v1/state drops them silently"
    )
