"""Application settings loaded from environment variables and .env file."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """SmokeGuard backend configuration.

    All values can be set via environment variables or a .env file.
    """

    # MQTT broker configuration (Mosquitto on this machine; the ESP32
    # receiver publishes CSI lines to the data topic)
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_topic_data: str = "home/csi/data"
    mqtt_topic_status: str = "home/csi/status"
    mqtt_topic_smoke: str = "home/smoke_sensor/data"
    mqtt_client_id: str = "smokeguard-backend"

    # CSV replay mode (development without hardware): set to a .csv path to
    # replay recorded CSI data instead of subscribing to MQTT
    replay_csv: str = ""

    # Optional MAC filter (leave empty to accept all senders)
    sender_mac: str = ""

    # InfluxDB connection
    influxdb_url: str = "http://127.0.0.1:8086"
    influxdb_token: str = ""
    influxdb_org: str = "smokeguard"
    influxdb_bucket: str = "csi_data"

    # InfluxDB local data directory (for the subprocess)
    influxdb_data_dir: str = "./influxdb_data"

    # Periodic cleanup — CSI data grows fast; readings older than this many
    # days are deleted from InfluxDB (export first: scripts/export_csi_data_to_parquet.py)
    csi_retention_days: int = 7
    cleanup_interval_hours: float = 0.5  # 30 minutes

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    # Number of subcarriers (64 for ESP32-S3 HT40, 128 for HT40 on newer chips)
    num_subcarriers: int = 64

    # WebSocket per-client queue size (oldest dropped when full)
    ws_queue_maxsize: int = 256

    # Smoking-activity detection (real ML pipeline — see app/inference.py)
    detection_db_path: str = "./detection.db"
    # Window lengths.  The smoke window is the last 14.905 s of sensor
    # samples; the CSI window always starts at the same instant (t0) and its
    # duration is scanned over candidates between min and max (gestures last
    # 4-7 s).  The most confident candidate is used.
    detection_csi_min_seconds: float = 4.0
    detection_csi_max_seconds: float = 7.0
    detection_csi_step_seconds: float = 0.5
    detection_smoke_seconds: float = 14.905367895113585
    detection_sensor_baseline_seconds: float = 600.0  # causal baseline before window
    detection_interval_s: float = 0.2      # evaluation cadence
    detection_cooldown_s: float = 30.0     # alert dedupe (record-timestamp domain)
    detection_csi_min_packets: int = 200   # min raw CSI packets to run the model
    detection_smoke_min_window: int = 8    # notebook requirement
    detection_smoke_min_baseline: int = 30  # notebook requirement
    detection_baseline_min_packets: int = 300  # static-background capture minimum

    # Experimental CSI-only class (sensor below gate, CSI sees a smoking
    # gesture). Enabled at runtime via the SQLite toggle, not here.
    detection_csi_only_min_prob: float = 0.0  # extra confidence gate (0 = off)
    detection_csi_only_interval_s: float = 1.0  # CSI-only scan rate cap

    # Model artifacts (paths relative to the backend working directory).
    # The CSI checkpoint is the seven-class ResNet-LSTM; its Platt calibrator
    # JSON must list the same classes in the same order.
    csi_model_path: str = "models/csi_resnet_lstm_7class.keras"
    csi_platt_path: str = "models/csi_resnet_lstm_7class.platt.json"
    smoke_model_path: str = "models/smoke_svm_pipeline.joblib"

    # Decision thresholds / fusion weights (from the fusion notebook).
    # The sensor gate is the SVM's F1-max OOF threshold from the sensor
    # notebook (theta = 0.4049989698494056 for the 2026-11-08 checkpoint).
    sensor_prob_threshold: float = 0.4049989698494056
    fusion_score_threshold: float = 0.70
    # Balanced CSI/sensor weights.  The notebook's 0.3/0.7 favoured the
    # sensor so heavily that a high p_sensor alone could carry a weak CSI
    # hit over the fused threshold; equal weights require real CSI evidence.
    fusion_csi_weight: float = 0.50        # sensor weight = 1 - this

    # Admin auth (PoC).  The two secrets are independent and generated once
    # into the gitignored .env (scripts/setup_auth_secrets.sh) — required, so
    # a misconfigured deployment fails fast at import instead of at login.
    jwt_secret: str                        # HMAC key for bearer tokens
    password_reset_secret: str             # second factor for the reset API
    jwt_expire_days: int = 7
    auth_db_path: str = "./auth.db"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # BaseSettings defaults to extra='forbid', and unknown keys in a
        # dotenv FILE are injected into validation — a stale key from an old
        # .env would crash startup.  Filter dotenv keys to declared fields;
        # unknown environment variables were already ignored by pydantic.
        dotenv_filtering="only_existing",
    )

    @model_validator(mode="after")
    def _validate_csi_window(self) -> "Settings":
        """Keep the CSI scan range sane and inside the smoke window."""
        if self.detection_csi_min_seconds <= 0:
            raise ValueError("detection_csi_min_seconds must be > 0")
        if self.detection_csi_step_seconds <= 0:
            raise ValueError("detection_csi_step_seconds must be > 0")
        if self.detection_csi_max_seconds < self.detection_csi_min_seconds:
            raise ValueError(
                "detection_csi_max_seconds must be >= detection_csi_min_seconds"
            )
        if self.detection_csi_max_seconds > self.detection_smoke_seconds:
            raise ValueError(
                "detection_csi_max_seconds must not exceed "
                "detection_smoke_seconds (the CSI window must end at or "
                "before the smoke anchor)"
            )
        if not 0.0 <= self.detection_csi_only_min_prob <= 1.0:
            raise ValueError(
                "detection_csi_only_min_prob must be between 0 and 1"
            )
        if self.detection_csi_only_interval_s <= 0:
            raise ValueError("detection_csi_only_interval_s must be > 0")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton)."""
    return Settings()
