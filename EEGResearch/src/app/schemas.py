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
    bands: BandData | None = None
    ingestion: dict[str, Any] | None = None
    # The headband's optical heart block, exactly as `CameraData` carries the camera's.
    # This field must stay declared: pydantic silently drops undeclared keys, so
    # without it `/api/v1/state` deleted the heart block before the poller (pull mode,
    # the default) ever saw it -- confirmed on hardware, 227 consecutive polls with no
    # `heart` key despite 2697 optics packets/s reaching the adapter. Push was
    # unaffected since `push_client` posts `snapshot()` directly, bypassing this model.
    heart: dict[str, Any] | None = None


class CameraData(BaseModel):
    """Interpreted camera snapshot.

    A separate model rather than making the EEG fields optional: `channels`,
    `features` and `state` are genuinely required of an EEG payload, and relaxing
    them for the camera's sake would let a malformed EEG record validate silently.

    `heart` and `face` are absent -- not null -- when that channel is switched off,
    so a consumer can tell a respected refusal from a sensor that failed.
    """

    # Required, with no default, so it works as a discriminator: without it an EEG
    # payload (which carries no `kind`) would validate as a camera and silently
    # drop `channels`/`features`/`state` instead of raising.
    kind: Literal["camera"]
    contract_version: str
    device_id: str
    timestamp: str
    ingestion: dict[str, Any] | None = None
    heart: dict[str, Any] | None = None
    face: dict[str, Any] | None = None


class Envelope(BaseModel):
    status: Literal["ok", "idle"]
    # EEG first: it carries no `kind`, CameraData requires one, so the two
    # can't be confused either way.
    data: InterpretedEegData | CameraData | None
    message: str
