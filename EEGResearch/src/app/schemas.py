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
    # Raw, pre-baseline log ratios (linear power, ln beta/(alpha+theta) and
    # ln alpha/(beta+gamma)) -- diagnostics for the accuracy capture
    # (HANDOFF.md Phase 0). None on a frame with no usable bands.
    focus_log_ratio: float | None = None
    calm_log_ratio: float | None = None
    # Smoothed 0..1 electrode contact behind signal_quality and confidence;
    # None when the bridge reports no contact data.
    contact_ratio: float | None = None
    # Artifact gate: ticks held this session (delta jump, EMG gamma, spread
    # jump), and why this tick was held -- None when it was scored.
    samples_artifact: int | None = None
    # Usable ticks whose delta could not be read, so the blink gate had no
    # reference for them. Distinct from an artifact count of zero.
    samples_no_delta: int | None = None
    # Its sibling for the spread gate: usable multi-electrode ticks whose
    # spread could not be taken (a non-finite channel).
    samples_no_spread: int | None = None
    # A plain str, not a Literal of the three reasons: the processor writes
    # them as unshared string literals, and a fourth would have made every
    # /api/v1/state call 500 -- a harder failure than the silent key drop
    # this model exists to guard against.
    artifact_reason: str | None = None
    # The exponentially smoothed ratios the scores were scaled from.
    focus_log_ratio_smoothed: float | None = None
    calm_log_ratio_smoothed: float | None = None
    # Which spectrum calm was scored from ("sdk" | "local"), the local
    # spectrum's 1/f-relative temporal alpha residual (carried on both
    # sources for comparison), and whether its 4 s buffer was full.
    calm_source: str | None = None
    calm_alpha_residual: float | None = None
    spectrum_ready: bool | None = None


class StateData(BaseModel):
    label: str
    reason: str
    confidence: float
    focus_score: float
    calm_score: float


class BandData(BaseModel):
    # None for a band the bridge reported as NaN or infinite: the renderer
    # refuses non-finite floats, so a malformed band made /api/v1/state 500
    # on exactly the tick the processor had correctly held -- under pull the
    # poller then recorded nothing, indistinguishable from a sidecar down.
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
