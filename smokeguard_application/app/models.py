"""Pydantic data models for CSI readings and API request/response schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Column name constants (from the ESP32 firmware CSV output)
# ---------------------------------------------------------------------------

# ESP32-S3 full-format columns (25 fields, with timestamp_real appended = 26)
DATA_COLUMNS_S3 = [
    "type", "id", "mac", "rssi", "rate", "sig_mode", "mcs", "bandwidth",
    "smoothing", "not_sounding", "aggregation", "stbc", "fec_coding",
    "sgi", "noise_floor", "ampdu_cnt", "channel", "secondary_channel",
    "local_timestamp", "ant", "sig_len", "rx_state", "len", "first_word",
    "data",
]

# ESP32-C5/C6 compact-format columns (14 fields, no timestamp_real appended = 15)
DATA_COLUMNS_C5C6 = [
    "type", "id", "mac", "rssi", "rate", "noise_floor", "fft_gain",
    "agc_gain", "channel", "local_timestamp", "sig_len", "rx_state",
    "len", "first_word", "data",
]

# ---------------------------------------------------------------------------
# Subcarrier layout metadata
# ---------------------------------------------------------------------------
#
# Array indices are 0-based positions in the (de-interleaved) subcarrier-pair
# list.  ESP-IDF reports CSI in FFT-bin order: index 0 is the DC null,
# subcarrier numbers ascend to the Nyquist bin, then the sequence wraps to
# negatives — [0 … 31, -32 … -1] for 64 pairs.  (Verified on a 5924-row
# capture: pair indices [0, 27..37] are always exactly zero.)
#
# Signed subcarrier numbers: sc = idx if idx < subcarrier_wrap_idx
#                                else idx - total_pairs
# (e.g. array[38] - 64 = sc -26 for 64 subcarriers; DC is always index 0).
#
# The two data-run key pairs (data_start_* / data_upper_*) denote array order,
# not sign — the first run is the positive-sc half.
#
# For LLTF mode the ESP32 reports fewer subcarriers (~50-57), so we compute
# metadata dynamically when the exact len is not in the known-sizes table.
#
# Known layouts (indexed by len = number of I/Q integers):
SUBCARRIER_METADATA: dict[int, dict[str, int]] = {
    128: {  # len = 128 → 64 subcarriers (HT40)
        "data_start_idx": 1,             # sc +1 … +26
        "data_end_idx": 26,
        "dc_null_idx": 0,                # sc 0
        "data_upper_start": 38,          # sc -26 … -1
        "data_upper_end": 63,
        "total_pairs": 64,
        "subcarrier_wrap_idx": 32,       # sc = idx < 32 ? idx : idx - 64
    },
    256: {  # len = 256 → 128 subcarriers (HT40 on newer chips)
        # Assumed to follow the same FFT-bin order; no len=256 sample to verify.
        "data_start_idx": 1,             # sc +1 … +58
        "data_end_idx": 58,
        "dc_null_idx": 0,                # sc 0
        "data_upper_start": 70,          # sc -58 … -1
        "data_upper_end": 127,
        "total_pairs": 128,
        "subcarrier_wrap_idx": 64,       # sc = idx < 64 ? idx : idx - 128
    },
}

# ---------------------------------------------------------------------------
# Dynamic metadata for unknown lengths (LLTF / HT20 / custom modes)
# ---------------------------------------------------------------------------

def _build_lltf_metadata(subcarrier_count: int) -> dict[str, int]:
    """Build subcarrier layout metadata for an arbitrary subcarrier count.

    ESP-IDF stores CSI in FFT-bin order: index 0 is the DC null, positive
    subcarrier numbers ascend to the Nyquist bin, then the sequence wraps to
    negatives (indices >= wrap map to idx - count).  Without per-mode
    guard-band knowledge we conservatively treat the outer ~5 % (min 2 tones)
    of each band edge as guard tones.  Very small counts can yield empty or
    inverted runs — consumers clamp.
    """
    wrap_idx = (subcarrier_count + 1) // 2   # first negative subcarrier
    # Reasonable guard estimate for LLTF: ~2-3 tones per band edge
    guard_width = max(2, subcarrier_count // 20)

    sc_max = wrap_idx - 1                        # e.g. +31 for 64 pairs
    sc_min = -(subcarrier_count - wrap_idx)      # e.g. -32 for 64 pairs

    def to_index(sc: int) -> int:
        """Map a signed subcarrier number to its array index."""
        return sc if sc >= 0 else sc + subcarrier_count

    return {
        "data_start_idx": 1,
        "data_end_idx": sc_max - guard_width,
        "dc_null_idx": 0,
        "data_upper_start": to_index(sc_min + guard_width),
        "data_upper_end": subcarrier_count - 1,
        "total_pairs": subcarrier_count,
        "subcarrier_wrap_idx": wrap_idx,
    }


# ---------------------------------------------------------------------------
# CSI Reading model
# ---------------------------------------------------------------------------

class CSIRecord(BaseModel):
    """A single parsed CSI reading from the ESP32 receiver."""

    id: int = Field(description="Packet sequence number from sender")
    mac: str = Field(description="Sender MAC address")
    rssi: int
    rate: int
    sig_mode: int
    mcs: int
    bandwidth: int
    smoothing: int
    not_sounding: int
    aggregation: int
    stbc: int
    fec_coding: int
    sgi: int
    noise_floor: int
    ampdu_cnt: int
    channel: int
    secondary_channel: int
    local_timestamp: int = Field(description="ESP32 microsecond counter")
    ant: int
    sig_len: int
    rx_state: int
    len: int = Field(description="Number of I/Q integers in the data array")
    first_word: int
    data: list[int] = Field(description="Interleaved I/Q samples [I0,Q0,I1,Q1,...]")
    timestamp_real: float = Field(description="Derived UNIX epoch seconds")

    @property
    def subcarrier_count(self) -> int:
        """Number of complex subcarrier pairs."""
        return self.len // 2

    @property
    def i_samples(self) -> list[int]:
        """In-phase (I) samples for each subcarrier."""
        return self.data[0::2]

    @property
    def q_samples(self) -> list[int]:
        """Quadrature (Q) samples for each subcarrier."""
        return self.data[1::2]

    @property
    def subcarrier_metadata(self) -> dict | None:
        """Return subcarrier layout metadata.

        Uses a known fixed layout for common lengths (128, 256), and computes
        a dynamic layout for LLTF / unknown lengths so the frontend always
        receives DC-null position and signed-subcarrier mapping info.
        """
        known = SUBCARRIER_METADATA.get(self.len)
        if known:
            return known
        # LLTF or other unknown length — compute layout from subcarrier count
        sc = self.subcarrier_count
        if sc > 0:
            return _build_lltf_metadata(sc)
        return None


# ---------------------------------------------------------------------------
# Smoke (air-quality) records
# ---------------------------------------------------------------------------

class SmokeRecord(BaseModel):
    """A single PMS5003 + BME680 air-quality reading parsed from the smoke MQTT topic.

    CSV (16 fields): timestamp,pm1_0,pm2_5,pm10,cnt0_3,cnt0_5,cnt1_0,cnt2_5,
    cnt5_0,cnt10,temp_c,pressure_hpa,humidity_pct,gas_kohm,altitude_m,rssi
    Legacy firmware sends 11 fields (no BME680) — those fields are None.
    """

    timestamp_real: float = Field(
        description="UNIX epoch seconds; receive time when the sensor clock was unsynced (0)"
    )
    pm1_0: int
    pm2_5: int
    pm10: int
    cnt0_3: int
    cnt0_5: int
    cnt1_0: int
    cnt2_5: int
    cnt5_0: int
    cnt10: int
    temp_c: float | None = None
    pressure_hpa: float | None = None
    humidity_pct: float | None = None
    gas_kohm: float | None = None
    altitude_m: float | None = None
    rssi: int


# ---------------------------------------------------------------------------
# WebSocket message envelope
# ---------------------------------------------------------------------------

class WSMessage(BaseModel):
    """Envelope for WebSocket messages sent to the React frontend."""

    type: Literal["welcome", "csi", "smoke", "status", "pong", "alert"]


class WSWelcome(WSMessage):
    """Sent on initial WebSocket connection."""

    type: Literal["welcome"] = "welcome"  # type: ignore[assignment]
    source: str = "smokeguard-backend"
    num_subcarriers: int
    started_at: float


class WSCsiData(WSMessage):
    """CSI reading broadcast to all connected clients."""

    type: Literal["csi"] = "csi"  # type: ignore[assignment]
    t: float = Field(alias="timestamp_real")
    seq: int = Field(alias="id")
    mac: str
    rssi: int
    channel: int
    bandwidth: int
    mcs: int
    noise_floor: int
    ant: int
    length: int = Field(alias="len")
    subcarrier_count: int
    i: list[int]
    q: list[int]
    metadata: dict | None = None


class WSSmokeData(WSMessage):
    """Air-quality reading broadcast to all connected clients.

    BME680 fields are omitted from the JSON when absent (legacy firmware);
    see the exclude_none=True dump in main.py.
    """

    type: Literal["smoke"] = "smoke"  # type: ignore[assignment]
    t: float
    pm1_0: int
    pm2_5: int
    pm10: int
    cnt0_3: int
    cnt0_5: int
    cnt1_0: int
    cnt2_5: int
    cnt5_0: int
    cnt10: int
    temp_c: float | None = None
    pressure_hpa: float | None = None
    humidity_pct: float | None = None
    gas_kohm: float | None = None
    altitude_m: float | None = None
    rssi: int


class WSStatus(WSMessage):
    """Periodic or event-driven status update."""

    type: Literal["status"] = "status"  # type: ignore[assignment]
    mqtt: Literal["connected", "disconnected", "reconnecting"]
    packets: int
    dropped_lines: int
    last_record_at: float | None = None
    error: str | None = None
    receiver_online: bool | None = None
    ntp_synced: bool | None = None


class WSAlert(WSMessage):
    """Broadcast when a smoking activity is detected (toast trigger)."""

    type: Literal["alert"] = "alert"  # type: ignore[assignment]
    id: int = Field(description="Detection event id in the SQLite store")
    detected_at: float = Field(description="Detection UNIX epoch seconds")


# ---------------------------------------------------------------------------
# API response models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """GET /api/health response."""

    status: str = "ok"
    mqtt_connected: bool
    packets_received: int
    dropped_lines: int
    influxdb_connected: bool


class StatusResponse(BaseModel):
    """GET /api/status response — detailed live status."""

    mqtt_connected: bool
    packets_received: int
    dropped_lines: int
    last_record_at: float | None = None
    reconnect_attempts: int = 0
    receiver_online: bool | None = None
    ntp_synced: bool | None = None
    influxdb_connected: bool
    websocket_clients: int
    uptime_seconds: float


class HistoryPoint(BaseModel):
    """A single CSI reading returned by historical queries."""

    time: datetime
    seq: int
    rssi: int
    noise_floor: int
    mac: str
    channel: int
    bandwidth: int
    mcs: int
    i: list[int]
    q: list[int]


class HistoryResponse(BaseModel):
    """Envelope for historical CSI query responses."""

    count: int
    points: list[HistoryPoint]


class CleanupResponse(BaseModel):
    """GET /api/cleanup response."""

    status: str = "ok"
    retention_days: int


class DetectionConfigResponse(BaseModel):
    """GET /api/detection/config response."""

    enabled: bool


class DetectionConfigUpdate(BaseModel):
    """POST /api/detection/config request body."""

    enabled: bool


class DetectionEventSummary(BaseModel):
    """One row in the month/year history listing (no snapshot blobs)."""

    id: int
    detected_at: float
    csi_frames: int
    smoke_samples: int


class DetectionEventsResponse(BaseModel):
    """GET /api/detection/events response."""

    year: int
    month: int
    count: int
    events: list[DetectionEventSummary]


class DetectionEventDetail(BaseModel):
    """GET /api/detection/events/{id} response — full snapshot payloads."""

    id: int
    detected_at: float
    csi_frames: int
    smoke_samples: int
    csi: dict
    smoke: dict


class DetectionEventDeleteResponse(BaseModel):
    """DELETE /api/detection/events/{id} response."""

    status: str = "deleted"


# ---------------------------------------------------------------------------
# Auth request/response schemas
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    """POST /api/auth/login request body."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    """POST /api/auth/login response — JWT plus its expiry for the client."""

    token: str
    expires_at: float = Field(description="JWT expiry, UNIX epoch seconds")


class PasswordResetRequest(BaseModel):
    """POST /api/auth/reset-password request body (also needs a bearer token)."""

    username: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=1)
    secret: str = Field(min_length=1)

    @field_validator("new_password")
    @classmethod
    def _bcrypt_byte_limit(cls, v: str) -> str:
        # bcrypt hashes at most 72 BYTES and bcrypt 5.x rejects longer input;
        # a char-based max_length would be wrong for non-ASCII passwords.
        if len(v.encode("utf-8")) > 72:
            raise ValueError("new_password must be at most 72 bytes")
        return v


class PasswordResetResponse(BaseModel):
    """POST /api/auth/reset-password response."""

    status: str = "ok"


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str
