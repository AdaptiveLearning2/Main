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
    # "contact" (electrode data) or the calm-based "heuristic" fallback.
    quality_basis: Literal["contact", "heuristic"] | None = None
    # Session contact-filter drops; electrodes averaged into bands (4 = all); samples drained this tick.
    samples_rejected: int | None = None
    band_channels_used: int | None = None
    batch_size: int | None = None
    # Raw pre-baseline ln ratios (linear power); None on a frame with no usable bands.
    focus_log_ratio: float | None = None
    calm_log_ratio: float | None = None
    # Smoothed 0..1 electrode contact; None when the bridge reports none.
    contact_ratio: float | None = None
    # Artifact-held ticks this session; None reason means this tick was scored.
    samples_artifact: int | None = None
    # Ticks the blink / spread gates could not check -- distinct from zero artifacts.
    samples_no_delta: int | None = None
    samples_no_spread: int | None = None
    # Plain str, not a Literal: an unlisted reason would 500 every /api/v1/state call.
    artifact_reason: str | None = None
    focus_log_ratio_smoothed: float | None = None
    calm_log_ratio_smoothed: float | None = None
    # calm_source is "sdk" | "local"; the residual is carried on both for comparison.
    calm_source: str | None = None
    calm_alpha_residual: float | None = None
    spectrum_ready: bool | None = None
    # spectrum_slope is carried for comparison, not scored.
    spectrum_reason: str | None = None
    spectrum_slope: float | None = None
    # False for the midpoint placeholder before any estimate.
    calm_measured: bool | None = None
    calm_held_seconds: float | None = None
    # Centred on the session baseline yet, or still on the population midpoint.
    focus_centred: bool | None = None
    calm_centred: bool | None = None


class StateData(BaseModel):
    label: str
    reason: str
    confidence: float
    focus_score: float
    calm_score: float


class BandData(BaseModel):
    # None for a non-finite band: the JSON renderer refuses NaN/inf and would 500.
    delta: float | None
    theta: float | None
    alpha: float | None
    beta: float | None
    gamma: float | None


class InterpretedEegData(BaseModel):
    """Interpreted EEG snapshot; optional device/ingestion metadata for pilot visibility."""

    contract_version: str
    device_id: str
    timestamp: str
    channels: ChannelData
    features: FeatureData
    state: StateData
    bands: BandData | None = None
    ingestion: dict[str, Any] | None = None
    # Headband optical heart block. Must stay declared: pydantic drops undeclared keys,
    # so pull mode would never see it.
    heart: dict[str, Any] | None = None


class CameraData(BaseModel):
    """Interpreted camera snapshot; separate so the EEG fields stay required.

    `heart` / `face` are absent, not null, when switched off: a refusal, not a failure.
    """

    # Required, no default: the discriminator that stops an EEG payload validating as camera.
    kind: Literal["camera"]
    contract_version: str
    device_id: str
    timestamp: str
    ingestion: dict[str, Any] | None = None
    heart: dict[str, Any] | None = None
    face: dict[str, Any] | None = None


class Envelope(BaseModel):
    status: Literal["ok", "idle"]
    data: InterpretedEegData | CameraData | None
    message: str
