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


class CameraData(BaseModel):
    """Interpreted camera snapshot.

    A separate model rather than making the EEG fields optional, which was the
    other option and the wrong one: `channels`, `features` and `state` are
    genuinely required of an EEG payload, and relaxing them to satisfy a camera
    would let a malformed *EEG* record validate silently.
    Two device kinds produce two shapes; the union says so.

    `heart` and `face` are each absent -- not null -- when that channel is
    switched off, so a consumer can tell a respected refusal from a sensor that
    failed. Same reason the EEG fields are absent here rather than nulled.
    """

    # An explicit discriminator, not a field count. Without it an EEG payload
    # validates happily as a CameraData -- pydantic ignores extra keys, so
    # `channels`, `features` and `state` would be silently dropped from the
    # response rather than raising. A consumer also gets to branch on the kind
    # instead of probing for which fields happen to be present.
    # Required, with no default. A default would make it useless as a
    # discriminator: an EEG payload carries no `kind`, so it would take the
    # default and validate as a camera anyway -- which it did, silently dropping
    # channels, features and state from the response.
    kind: Literal["camera"]
    contract_version: str
    device_id: str
    timestamp: str
    ingestion: dict[str, Any] | None = None
    heart: dict[str, Any] | None = None
    face: dict[str, Any] | None = None


class Envelope(BaseModel):
    status: Literal["ok", "idle"]
    # EEG first: it carries no `kind`, and CameraData requires one, so the two
    # cannot be confused in either direction.
    data: InterpretedEegData | CameraData | None
    message: str
