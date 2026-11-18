"""Shared composition of the WebSocket status payload.

The same status message is sent on WS connect, broadcast every 5 s, and
re-sent after a manual NTP refresh.  Keeping one builder here prevents the
three call sites from drifting apart.
"""

from __future__ import annotations

from app.models import WSStatus
from app.mqtt_reader import MqttStatus


def build_ws_status(
    mqtt_status: MqttStatus,
    ntp_snapshot: dict | None = None,
) -> WSStatus:
    """Build the current status message from the MQTT and NTP state."""
    snap = mqtt_status.snapshot()
    ntp = ntp_snapshot or {}
    return WSStatus(
        mqtt=mqtt_status.transport_state(),
        packets=snap.get("packets", 0),
        dropped_lines=snap.get("dropped_lines", 0),
        last_record_at=snap.get("last_record_at"),
        error=snap.get("error"),
        receiver_online=snap.get("receiver_online"),
        ntp_synced=snap.get("ntp_synced"),
        internet_ntp_state=ntp.get("state", "unknown"),
        internet_ntp_server=ntp.get("server"),
        internet_ntp_offset_s=ntp.get("offset_s"),
        internet_ntp_rtt_s=ntp.get("rtt_s"),
        internet_ntp_last_checked_at=ntp.get("last_checked_at"),
        internet_ntp_error=ntp.get("error"),
        timestamps_corrected=snap.get("timestamps_corrected", 0),
        last_timestamp_correction_at=snap.get("last_timestamp_correction_at"),
    )
