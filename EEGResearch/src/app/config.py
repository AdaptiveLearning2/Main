from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = Field(default="EEG Learning Platform", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    secret_key: str = Field(default="change-me", alias="SECRET_KEY")
    api_token: str = Field(default="local-pilot-token", alias="API_TOKEN")
    admin_token: str = Field(default="local-pilot-admin-token", alias="ADMIN_TOKEN")
    allowed_origins: str = Field(default="http://localhost:3000", alias="ALLOWED_ORIGINS")
    data_retention_days: int = Field(default=30, alias="DATA_RETENTION_DAYS")
    ws_heartbeat_seconds: int = Field(default=5, alias="WS_HEARTBEAT_SECONDS")
    eeg_sample_hz: int = Field(default=4, alias="EEG_SAMPLE_HZ")
    eeg_source: str = Field(default="sim", alias="EEG_SOURCE")
    muse_bridge_host: str = Field(default="127.0.0.1", alias="MUSE_BRIDGE_HOST")
    muse_bridge_port: int = Field(default=8765, alias="MUSE_BRIDGE_PORT")
    muse_bridge_timeout_seconds: int = Field(default=5, alias="MUSE_BRIDGE_TIMEOUT_SECONDS")
    secure_cookies: bool = Field(default=False, alias="SECURE_COOKIES")
    # Optional: path to a built SPA (e.g. `dist/`) served on the same PORT as the API.
    static_site_dir: str | None = Field(default=None, alias="STATIC_SITE_DIR")


@lru_cache
def get_settings() -> Settings:
    return Settings()
