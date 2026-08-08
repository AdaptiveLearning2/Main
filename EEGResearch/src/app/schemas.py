from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ChannelData(BaseModel):
    tp9: float
    af7: float
    af8: float
    tp10: float


class FeatureData(BaseModel):
    focus_score: float
    calm_score: float
    confidence: float
    signal_quality: Literal["good", "degraded", "poor", "no_signal"]
    # Whether signal_quality came from the headband's electrode data
    # ("contact") or the legacy calm-based fallback ("heuristic").
    quality_basis: Literal["contact", "heuristic"] | None = None
    # Diagnostics: frames dropped by the contact filter this session, how many
    # electrodes the bridge averaged into the band values (4 = all), and how
    # many samples were drained from the bridge queue this tick (0 on a
    # no-signal tick).
    samples_rejected: int | None = None
    band_channels_used: int | None = None
    batch_size: int | None = None


class StateData(BaseModel):
    label: str
    reason: str
    confidence: float
    focus_score: float
    calm_score: float


class QuestionPolicyData(BaseModel):
    action: str
    difficulty: int


class BandData(BaseModel):
    delta: float
    theta: float
    alpha: float
    beta: float
    gamma: float


class InterpretedEegData(BaseModel):
    """Interpreted EEG snapshot; optional device/ingestion metadata for pilot visibility."""

    contract_version: str
    device_id: str
    timestamp: str
    channels: ChannelData
    features: FeatureData
    state: StateData
    question_policy: QuestionPolicyData
    bands: BandData | None = None
    ingestion: dict[str, Any] | None = None
    # Camera-derived blocks. Absent -- not null -- when the channel is switched
    # off or the device has no camera, because a channel that was disabled is
    # not a channel that failed. `heart` always carries `source`, since a rate
    # from a headband and one from a webcam are not the same measurement and a
    # consumer that cannot tell them apart cannot weight them.
    heart: dict[str, Any] | None = None
    face: dict[str, Any] | None = None


class Envelope(BaseModel):
    status: Literal["ok", "idle"]
    data: InterpretedEegData | None
    message: str
