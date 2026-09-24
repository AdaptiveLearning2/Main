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
    # Must include the frontend origin: under push the browser calls this sidecar directly.
    allowed_origins: str = Field(
        default="http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000",
        alias="ALLOWED_ORIGINS",
    )
    eeg_sample_hz: int = Field(default=4, alias="EEG_SAMPLE_HZ")
    eeg_source: str = Field(default="sim", alias="EEG_SOURCE")
    # "sdk" (bridge band powers) or "local" (services/eeg_spectrum.py). Changes the unit of
    # every stored calm value; see docs/signals.md.
    eeg_spectrum_source: str = Field(default="sdk", alias="EEG_SPECTRUM_SOURCE")
    # Simulator pulse; off so a plain run never stores a made-up heart rate. Sim only.
    eeg_sim_optics: bool = Field(default=False, alias="EEG_SIM_OPTICS")
    # Seconds an artifact tick withholds spectrum estimates. Read at import, so a bad
    # value warns and falls back rather than refusing the boot (as do the validators below).
    eeg_spectrum_poison_seconds: float = Field(default=4.0, alias="EEG_SPECTRUM_POISON_SECONDS")
    # Local-calm centre between arm and relatch: "keep" or population "midpoint". Inert on sdk.
    eeg_calm_centre_on_arm: str = Field(default="keep", alias="EEG_CALM_CENTRE_ON_ARM")

    @field_validator("eeg_spectrum_source", mode="before")
    @classmethod
    def _spectrum_source_is_known(cls, value):
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
        # A typo warns and means off (the safe side), never refuses the boot.
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
    # "device_id:kind[@[host:]port],..." e.g. "station1:muse@8765"; empty = single device.
    eeg_devices: str = Field(default="", alias="EEG_DEVICES")

    # Off by default so a headband-only deployment boots without the `face` extra.
    face_enabled: bool = Field(default=False, alias="FACE_ENABLED")
    face_camera_index: int = Field(default=0, ge=0, alias="FACE_CAMERA_INDEX")
    # Floored above 1: 0 fps makes the POS window zero-length.
    face_fps: float = Field(default=30.0, gt=1.0, le=240.0, alias="FACE_FPS")
    # Heart is off by default: it is only a failover when the headband gives no heart rate.
    face_emotion_enabled: bool = Field(default=True, alias="FACE_EMOTION_ENABLED")
    face_heart_enabled: bool = Field(default=False, alias="FACE_HEART_ENABLED")
    # Off by default: needs a model file the MediaPipe wheel does not ship.
    face_gaze_enabled: bool = Field(default=False, alias="FACE_GAZE_ENABLED")
    face_landmark_model_path: str = Field(
        default="models/face_landmarker.task", alias="FACE_LANDMARK_MODEL_PATH"
    )
    face_emotion_model_path: str = Field(
        default="models/emotion-ferplus-8.onnx", alias="FACE_EMOTION_MODEL_PATH"
    )

    # On: POST to BACKEND_URL/api/signals/* with the student's token (never stored).
    # Off: the backend's eeg_poller pulls instead. See services/push_client.py.
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
    # kind == "face" only; a camera is addressed by index, not host:port.
    camera_index: int | None = None


def parse_eeg_devices(settings: Settings) -> dict[str, DeviceConfig]:
    """Parse EEG_DEVICES into a device_id -> DeviceConfig registry.

    Unset/empty synthesizes one "default" device from EEG_SOURCE / MUSE_BRIDGE_*.
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
    # A bridge accepts exactly one client, so muse devices can't share a host:port.
    seen_muse_endpoints: set[tuple[str, int]] = set()
    # One capture per camera index, or two sessions interleave frames.
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
