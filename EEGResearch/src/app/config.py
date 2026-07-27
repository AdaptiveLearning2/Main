from dataclasses import dataclass
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = Field(default="EEG Learning Platform", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8001, alias="PORT")
    api_token: str = Field(alias="API_TOKEN")
    admin_token: str = Field(alias="ADMIN_TOKEN")
    allowed_origins: str = Field(default="http://localhost:3000,http://localhost:8000", alias="ALLOWED_ORIGINS")
    eeg_sample_hz: int = Field(default=4, alias="EEG_SAMPLE_HZ")
    eeg_source: str = Field(default="sim", alias="EEG_SOURCE")
    muse_bridge_host: str = Field(default="127.0.0.1", alias="MUSE_BRIDGE_HOST")
    muse_bridge_port: int = Field(default=8765, alias="MUSE_BRIDGE_PORT")
    muse_bridge_timeout_seconds: int = Field(default=5, alias="MUSE_BRIDGE_TIMEOUT_SECONDS")
    # Multi-headband registry: "device_id:kind[@[host:]port],...", e.g.
    # "seat1:muse@8765,seat2:muse@8766" or "seat1:sim,seat2:sim". Empty/unset
    # means single-device mode -- see parse_eeg_devices below.
    eeg_devices: str = Field(default="", alias="EEG_DEVICES")


@lru_cache
def get_settings() -> Settings:
    return Settings()


DEFAULT_DEVICE_ID = "default"


@dataclass(frozen=True)
class DeviceConfig:
    device_id: str
    kind: str  # "sim" or "muse"
    host: str
    port: int


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
            )
        }

    devices: dict[str, DeviceConfig] = {}
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
        if kind not in {"sim", "muse"}:
            raise ValueError(f"Invalid EEG_DEVICES entry: {entry!r} (kind must be 'sim' or 'muse')")
        host = settings.muse_bridge_host
        port = settings.muse_bridge_port
        addr_part = addr_part.strip()
        if addr_part:
            host_part, sep2, port_part = addr_part.rpartition(":")
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
        devices[device_id] = DeviceConfig(device_id=device_id, kind=kind, host=host, port=port)

    if not devices:
        raise ValueError("EEG_DEVICES is set but no valid entries were parsed")
    return devices
