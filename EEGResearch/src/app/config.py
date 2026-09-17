from dataclasses import dataclass
from functools import lru_cache
import logging
from math import isfinite

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = Field(default="EEG Learning Platform", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8001, alias="PORT")
    api_token: str = Field(alias="API_TOKEN")
    admin_token: str = Field(alias="ADMIN_TOKEN")
    # Under push ingestion the student's browser calls this sidecar directly, so the
    # frontend origin must be listed here, not just the backend's. A hosted deployment
    # that forgets to add its origin fails CORS while the sidecar looks healthy.
    allowed_origins: str = Field(
        default="http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000",
        alias="ALLOWED_ORIGINS",
    )
    eeg_sample_hz: int = Field(default=4, alias="EEG_SAMPLE_HZ")
    eeg_source: str = Field(default="sim", alias="EEG_SOURCE")
    # Where `calm` reads its spectrum: "sdk" (the bridge's band powers) or
    # "local" (our own Welch spectrum on the raw stream, 1/f-relative alpha
    # at the temporal pair -- services/eeg_spectrum.py). Ships dark on
    # purpose: one adult, three runs is not a validation set, and flipping
    # it changes what every stored calm value means. See EEG_REFERENCE.md.
    eeg_spectrum_source: str = Field(default="sdk", alias="EEG_SPECTRUM_SOURCE")
    # The simulator's synthesised pulse. Off by default, like MUSE_ENABLE_OPTICS
    # on hardware: a plain run must not store a made-up heart rate. Read only
    # under EEG_SOURCE=sim; the classroom simulation sets it.
    eeg_sim_optics: bool = Field(default=False, alias="EEG_SIM_OPTICS")
    # The two open decisions on the local calm (HANDOFF.md), exposed so the
    # second wearer's capture can be replayed and a session run under either
    # alternative without a code change. Defaults are the shipped behaviour.
    # How long an artifact tick withholds spectrum estimates, in seconds:
    # 4.0 is the whole buffer, 2.0 the Welch window it landed in. A value
    # that is not a finite number falls back to 4.0 with a warning (below);
    # SpectrumEstimator floors a numeric one at a sample, since it knows the
    # rate. Both settings are read at import, inside StreamManager(), so a
    # typo in either must not refuse the sidecar boot over a tuning knob --
    # the MUSE_OPTICS_PRESET precedent.
    eeg_spectrum_poison_seconds: float = Field(default=4.0, alias="EEG_SPECTRUM_POISON_SECONDS")
    # What the local calm is centred on between the arm and its new latch:
    # "keep" the centre in use, or the population "midpoint". Inert on the
    # sdk source, whose calm latches beside focus. Case and whitespace are
    # forgiven; a misspelling falls back to "keep" with a warning.
    eeg_calm_centre_on_arm: str = Field(default="keep", alias="EEG_CALM_CENTRE_ON_ARM")

    @field_validator("eeg_spectrum_source", mode="before")
    @classmethod
    def _spectrum_source_is_known(cls, value):
        # This one decides what unit every stored calm value is in, so a
        # typo silently meaning sdk (locl -> sdk, scale 2, the 0.377 line)
        # would spoil exactly the local-calm capture it was set for.
        text = str(value or "sdk").lower().strip() or "sdk"
        if text not in ("sdk", "local"):
            _log.warning("EEG_SPECTRUM_SOURCE=%r is not 'sdk' or 'local'; using 'sdk'", value)
            return "sdk"
        return text

    @field_validator("eeg_spectrum_poison_seconds", mode="before")
    @classmethod
    def _poison_seconds_is_a_finite_number(cls, value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = float("nan")
        if not isfinite(number):
            _log.warning("EEG_SPECTRUM_POISON_SECONDS=%r is not a finite number; using 4.0", value)
            return 4.0
        return number

    @field_validator("eeg_calm_centre_on_arm", mode="before")
    @classmethod
    def _calm_centre_on_arm_is_known(cls, value):
        text = str(value or "keep").lower().strip() or "keep"
        if text not in ("keep", "midpoint"):
            _log.warning("EEG_CALM_CENTRE_ON_ARM=%r is not 'keep' or 'midpoint'; using 'keep'", value)
            return "keep"
        return text

    @field_validator("eeg_sim_optics", mode="before")
    @classmethod
    def _sim_optics_is_a_bool(cls, value):
        # Read at import inside StreamManager() like the three above, so a
        # typo (`ture`) must warn and mean off, not refuse the boot -- and
        # off is the safe side: nothing synthesised gets stored by mistake.
        if isinstance(value, bool):
            return value
        text = str(value if value is not None else "false").lower().strip()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("", "0", "false", "no", "off"):
            return False
        _log.warning("EEG_SIM_OPTICS=%r is not a boolean; using false", value)
        return False
    muse_bridge_host: str = Field(default="127.0.0.1", alias="MUSE_BRIDGE_HOST")
    muse_bridge_port: int = Field(default=8765, alias="MUSE_BRIDGE_PORT")
    muse_bridge_timeout_seconds: int = Field(default=5, alias="MUSE_BRIDGE_TIMEOUT_SECONDS")
    # Multi-headband registry: "device_id:kind[@[host:]port],...", e.g.
    # "station1:muse@8765,station2:muse@8766" or "station1:sim,station2:sim".
    # Empty/unset means single-device mode -- see parse_eeg_devices below.
    eeg_devices: str = Field(default="", alias="EEG_DEVICES")

    # Camera. Off by default and gated separately from EEG_SOURCE, since a headband-only
    # deployment must boot even with the `face` extra uninstalled.
    face_enabled: bool = Field(default=False, alias="FACE_ENABLED")
    face_camera_index: int = Field(default=0, ge=0, alias="FACE_CAMERA_INDEX")
    # A camera reporting 0 fps would make the POS window zero-length and the sample
    # interval infinite, so this is floored above 1.
    face_fps: float = Field(default=30.0, gt=1.0, le=240.0, alias="FACE_FPS")
    # Two camera channels, switchable independently by the website backend (which enforces
    # consent). Emotion defaults on; heart defaults off since it's only a failover for when
    # the headband isn't providing a heart rate.
    face_emotion_enabled: bool = Field(default=True, alias="FACE_EMOTION_ENABLED")
    face_heart_enabled: bool = Field(default=False, alias="FACE_HEART_ENABLED")
    # Off by default: gaze needs a second per-frame detector and a model file not included
    # in the MediaPipe wheel, so an unprovisioned deployment must not fail on it.
    face_gaze_enabled: bool = Field(default=False, alias="FACE_GAZE_ENABLED")
    face_landmark_model_path: str = Field(
        default="models/face_landmarker.task", alias="FACE_LANDMARK_MODEL_PATH"
    )
    face_emotion_model_path: str = Field(
        default="models/emotion-ferplus-8.onnx", alias="FACE_EMOTION_MODEL_PATH"
    )

    # Push ingestion. Off by default so a co-located dev stack keeps polling via
    # `eeg_poller` in the website backend, with nothing double-posting the same samples.
    # On, this sidecar POSTs to BACKEND_URL/api/signals/* with the student's bearer
    # token (handed over from the browser at session start, never stored) -- see
    # services/push_client.py.
    push_enabled: bool = Field(default=False, alias="PUSH_ENABLED")
    backend_url: str = Field(default="http://127.0.0.1:8000", alias="BACKEND_URL")


@lru_cache
def get_settings() -> Settings:
    return Settings()


DEFAULT_DEVICE_ID = "default"


@dataclass(frozen=True)
class DeviceConfig:
    device_id: str
    kind: str  # "sim", "muse" or "face"
    host: str
    port: int
    # Only meaningful for kind == "face". A camera is addressed by index, not host:port,
    # so it needs its own field rather than reusing `port`.
    camera_index: int | None = None


def parse_eeg_devices(settings: Settings) -> dict[str, DeviceConfig]:
    """Parse EEG_DEVICES into a device_id -> DeviceConfig registry.

    Unset/empty EEG_DEVICES synthesizes a single "default" device from the
    existing EEG_SOURCE / MUSE_BRIDGE_HOST / MUSE_BRIDGE_PORT settings, so
    current .env files (single headband, no EEG_DEVICES) keep working
    untouched.
    """
    raw = settings.eeg_devices.strip()
    if not raw:
        return {
            DEFAULT_DEVICE_ID: DeviceConfig(
                device_id=DEFAULT_DEVICE_ID,
                kind=settings.eeg_source.lower().strip(),
                host=settings.muse_bridge_host,
                port=settings.muse_bridge_port,
                camera_index=settings.face_camera_index,
            )
        }

    devices: dict[str, DeviceConfig] = {}
    # Each "muse" device owns a TCP connection to one muse_native_bridge.exe process,
    # which accepts exactly one client, so two devices can't share a (host, port).
    # "sim" devices have no such process behind them, so they're exempt.
    seen_muse_endpoints: set[tuple[str, int]] = set()
    # Same reasoning for cameras: one OpenCV capture per device index, or two devices
    # would fight over one webcam and interleave frames from different sessions.
    seen_camera_indices: set[int] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        device_id, sep, spec = entry.partition(":")
        device_id = device_id.strip()
        if not sep or not device_id:
            raise ValueError(f"Invalid EEG_DEVICES entry: {entry!r} (expected device_id:kind[@[host:]port])")
        kind_part, _, addr_part = spec.partition("@")
        kind = kind_part.strip().lower()
        if kind not in {"sim", "muse", "face"}:
            raise ValueError(
                f"Invalid EEG_DEVICES entry: {entry!r} "
                "(kind must be 'sim', 'muse' or 'face')"
            )
        host = settings.muse_bridge_host
        port = settings.muse_bridge_port
        camera_index: int | None = None
        addr_part = addr_part.strip()

        if kind == "face":
            # "station1:face@2" means camera index 2, not port 2.
            try:
                camera_index = int(addr_part) if addr_part else settings.face_camera_index
            except ValueError as exc:
                raise ValueError(
                    f"Invalid EEG_DEVICES entry: {entry!r} "
                    "(face takes a camera index, e.g. 'station1:face@0')"
                ) from exc
            if camera_index < 0:
                raise ValueError(
                    f"Invalid EEG_DEVICES entry: {entry!r} (camera index must be >= 0)"
                )
            if camera_index in seen_camera_indices:
                raise ValueError(
                    f"Invalid EEG_DEVICES entry: {entry!r} (camera index {camera_index} "
                    "is already used by another face device -- one capture per camera)"
                )
            seen_camera_indices.add(camera_index)
            if device_id in devices:
                raise ValueError(f"Duplicate device_id in EEG_DEVICES: {device_id!r}")
            devices[device_id] = DeviceConfig(
                device_id=device_id, kind=kind, host=host, port=port,
                camera_index=camera_index,
            )
            continue

        if addr_part:
            host_part, sep2, port_part = addr_part.rpartition(":")
            if sep2 and not host_part.strip():
                raise ValueError(f"Invalid EEG_DEVICES entry: {entry!r} (empty host before ':')")
            try:
                if sep2:
                    host = host_part.strip()
                    port = int(port_part.strip())
                else:
                    port = int(addr_part)
            except ValueError as exc:
                raise ValueError(f"Invalid EEG_DEVICES entry: {entry!r} (bad port)") from exc
        if device_id in devices:
            raise ValueError(f"Duplicate device_id in EEG_DEVICES: {device_id!r}")
        if kind == "muse":
            endpoint = (host, port)
            if endpoint in seen_muse_endpoints:
                raise ValueError(
                    f"Invalid EEG_DEVICES entry: {entry!r} ({host}:{port} is already used by "
                    "another muse device -- each muse device needs its own bridge host:port)"
                )
            seen_muse_endpoints.add(endpoint)
        devices[device_id] = DeviceConfig(
            device_id=device_id, kind=kind, host=host, port=port,
            camera_index=camera_index,
        )

    if not devices:
        raise ValueError("EEG_DEVICES is set but no valid entries were parsed")
    return devices
