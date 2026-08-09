"""Application settings loaded from environment variables and .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """SmokeGuard backend configuration.

    All values can be set via environment variables or a .env file.
    """

    # Serial port configuration
    serial_port: str = "/dev/ttyUSB0"
    serial_baudrate: int = 921600

    # Optional MAC filter (leave empty to accept all senders)
    sender_mac: str = ""

    # InfluxDB connection
    influxdb_url: str = "http://127.0.0.1:8086"
    influxdb_token: str = ""
    influxdb_org: str = "smokeguard"
    influxdb_bucket: str = "csi_data"

    # InfluxDB local data directory (for the subprocess)
    influxdb_data_dir: str = "./influxdb_data"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Number of subcarriers (64 for ESP32-S3 HT40, 128 for HT40 on newer chips)
    num_subcarriers: int = 64

    # WebSocket per-client queue size (oldest dropped when full)
    ws_queue_maxsize: int = 256

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton)."""
    return Settings()
