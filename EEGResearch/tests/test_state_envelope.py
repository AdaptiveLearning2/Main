"""What `DeviceSession` puts on the payload has to survive `/api/v1/state`.

`Envelope.data` is typed `InterpretedEegData | CameraData | None`, so a
snapshot serialized through it silently drops any key the model doesn't
declare. `heart` was missing from `InterpretedEegData`, so a headband's
optical block was deleted from the one endpoint the pull poller reads
(`eeg_client.get_state`) -- a headband on an optics preset never recorded a
heart rate, with nothing raised anywhere.

Every heart test asserts on `session.latest_payload` (the dict before the
response model), and the push path posts `snapshot()` directly, bypassing the
envelope -- so both existing test paths missed the layer that was eating it.

The exhaustiveness test below derives its key list from `stream_manager`'s own
source rather than a hand-kept list, so a field added later can't go missing
here silently.
"""

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

# What `build_heart_record` returns for an accepted window, trimmed to the
# fields a consumer uses. `map_heart_to_heart_signal` reads `ts` and `source`;
# the poller dedupes on them.
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
    """Without `heart` declared on the model, this returns None."""
    out = _through_envelope({**EEG_SNAPSHOT, "heart": HEART_BLOCK})

    assert out.get("heart") is not None, (
        "the heart block was dropped by the response model -- the pull poller "
        "reads this endpoint, so the headband records no heart rate at all"
    )
    assert out["heart"]["bpm"] == pytest.approx(68.4)
    assert out["heart"]["source"] == "muse_optics"
    # The timestamp lets both writers dedupe a reading; without it, dedup
    # would fall back to the tick clock instead.
    assert out["heart"]["ts"] == HEART_BLOCK["ts"]


def test_a_refused_window_keeps_its_reason_rather_than_becoming_an_absent_block():
    """`bpm: None` with a reason means a refused measurement; an absent block
    means no optical channel. Collapsing the two would hide a failing headband
    behind one that was never asked to measure."""
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
    """Any key assigned into `latest_payload` (or added by `snapshot`) that the
    model doesn't declare is dropped silently at the HTTP boundary -- invisible
    to the session tests (which read `latest_payload` directly) and to push
    (which never crosses the envelope). Key list is derived from
    `stream_manager`'s source, not hand-kept.
    """
    import inspect

    source = inspect.getsource(sm)

    keys = set()
    # The dict literal assigned to self.latest_payload on the EEG path. Only
    # keys at the literal's own indentation -- `channels` nests its own dict,
    # and pulling tp9/af7 to the top level would compare the wrong names.
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
