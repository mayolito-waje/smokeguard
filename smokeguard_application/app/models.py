"""Pydantic data models for CSI readings and API request/response schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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
# Array indices are 0-based positions in the I/Q samples list.
# Signed subcarrier numbers: sc_num = array_idx - subcarrier_index_offset
# (e.g. array[32] - 32 = sc 0 = DC null for 64-subcarrier HT40).
#
# For LLTF mode the ESP32 reports fewer subcarriers (~50-57), so we compute
# metadata dynamically when the exact len is not in the known-sizes table.
#
# Known layouts (indexed by len = number of I/Q integers):
SUBCARRIER_METADATA: dict[int, dict[str, int]] = {
    128: {  # len = 128 → 64 subcarriers (HT40, HTLTF)
        "data_start_idx": 6,
        "data_end_idx": 31,
        "dc_null_idx": 32,
        "data_upper_start": 33,
        "data_upper_end": 58,
        "total_pairs": 64,
        "subcarrier_index_offset": 32,   # sc = array_idx - 32  →  [-32 … +31]
    },
    256: {  # len = 256 → 128 subcarriers (HT40 on newer chips)
        "data_start_idx": 6,
        "data_end_idx": 63,
        "dc_null_idx": 64,
        "data_upper_start": 65,
        "data_upper_end": 122,
        "total_pairs": 128,
        "subcarrier_index_offset": 64,   # sc = array_idx - 64  →  [-64 … +63]
    },
}

# ---------------------------------------------------------------------------
# Dynamic metadata for unknown lengths (LLTF / HT20 / custom modes)
# ---------------------------------------------------------------------------

def _build_lltf_metadata(subcarrier_count: int) -> dict[str, int]:
    """Build subcarrier layout metadata for an arbitrary subcarrier count.

    The ESP32 CSI data array is ordered from lowest to highest subcarrier
    index.  The DC null subcarrier (index 0) sits at the centre of the array.
    Without per-mode guard-band knowledge we conservatively treat the outer
    ~8 % on each side as guard / null tones and the rest as data subcarriers.
    """
    dc_center = subcarrier_count // 2
    # Reasonable guard estimate for LLTF: ~2-3 tones per edge
    guard_width = max(2, subcarrier_count // 20)

    return {
        "data_start_idx": guard_width,
        "data_end_idx": dc_center - 1,
        "dc_null_idx": dc_center,
        "data_upper_start": dc_center + 1,
        "data_upper_end": subcarrier_count - 1 - guard_width,
        "total_pairs": subcarrier_count,
        "subcarrier_index_offset": dc_center,
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

    type: Literal["welcome", "csi", "smoke", "status", "pong"]


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


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str
