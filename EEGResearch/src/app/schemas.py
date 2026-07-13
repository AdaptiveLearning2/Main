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
    timestamp: str
    channels: ChannelData
    features: FeatureData
    state: StateData
    question_policy: QuestionPolicyData
    bands: BandData | None = None
    ingestion: dict[str, Any] | None = None


class Envelope(BaseModel):
    status: Literal["ok", "idle"]
    data: InterpretedEegData | None
    message: str
