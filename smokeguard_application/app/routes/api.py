"""REST API endpoints for health checks and historical CSI data queries."""

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import get_current_user
from app.config import get_settings
from app.models import (
    CleanupResponse,
    ErrorResponse,
    HealthResponse,
    HistoryPoint,
    HistoryResponse,
    NtpStatusResponse,
    StatusResponse,
)
from app.mqtt_reader import MqttStatus
from app.status import build_ws_status

if TYPE_CHECKING:
    from app.influxdb_client import InfluxClient
    from app.websocket_manager import ConnectionManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Track when the server started
SERVER_START_TIME = time.time()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_influx(request: Request) -> "InfluxClient":
    return request.app.state.influx  # type: ignore[attr-defined]


def _get_ws_manager(request: Request) -> "ConnectionManager":
    return request.app.state.ws_manager  # type: ignore[attr-defined]


def _get_mqtt_status(request: Request) -> MqttStatus:
    return request.app.state.mqtt_status  # type: ignore[attr-defined]


def _get_ntp(request: Request):
    """Return the application-level NTP monitor, if startup created one."""
    return getattr(request.app.state, "ntp", None)


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    """Liveness check: reports MQTT and InfluxDB connectivity."""
    mqtt_status = _get_mqtt_status(request).snapshot()
    influx_client = _get_influx(request)
    return HealthResponse(
        status="ok",
        mqtt_connected=mqtt_status["connected"],
        packets_received=mqtt_status["packets"],
        dropped_lines=mqtt_status["dropped_lines"],
        influxdb_connected=influx_client.connected,
    )


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------

@router.get("/status", response_model=StatusResponse)
async def get_status(request: Request) -> StatusResponse:
    """Detailed live status of the CSI data pipeline."""
    mqtt_status = _get_mqtt_status(request).snapshot()
    ntp_monitor = _get_ntp(request)
    ntp_status = ntp_monitor.snapshot() if ntp_monitor else {}
    influx_client = _get_influx(request)
    ws_manager = _get_ws_manager(request)
    return StatusResponse(
        mqtt_connected=mqtt_status["connected"],
        packets_received=mqtt_status["packets"],
        dropped_lines=mqtt_status["dropped_lines"],
        last_record_at=mqtt_status["last_record_at"],
        reconnect_attempts=mqtt_status["reconnect_attempts"],
        receiver_online=mqtt_status["receiver_online"],
        ntp_synced=mqtt_status["ntp_synced"],
        internet_ntp_state=ntp_status.get("state", "unknown"),
        internet_ntp_server=ntp_status.get("server"),
        internet_ntp_offset_s=ntp_status.get("offset_s"),
        internet_ntp_rtt_s=ntp_status.get("rtt_s"),
        internet_ntp_last_checked_at=ntp_status.get("last_checked_at"),
        internet_ntp_error=ntp_status.get("error"),
        timestamps_corrected=mqtt_status["timestamps_corrected"],
        last_timestamp_correction_at=mqtt_status["last_timestamp_correction_at"],
        influxdb_connected=influx_client.connected,
        websocket_clients=ws_manager.client_count,
        uptime_seconds=time.time() - SERVER_START_TIME,
    )


@router.post(
    "/ntp/check",
    response_model=NtpStatusResponse,
    responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def check_internet_ntp(
    request: Request, _user: str = Depends(get_current_user)
) -> NtpStatusResponse:
    """Re-probe internet/NTP connectivity now (dashboard refresh button).

    Requires a bearer token, matching the other action routes.  The probe never
    raises; a failed check returns the offline state with an error message.
    """
    ntp_monitor = _get_ntp(request)
    if ntp_monitor is None:
        raise HTTPException(status_code=503, detail="NTP monitor not available")

    status_snapshot = await ntp_monitor.refresh()

    # Push the fresh state to every connected dashboard immediately; the 5 s
    # periodic broadcast keeps it current afterwards.
    ws_manager = _get_ws_manager(request)
    if ws_manager.client_count > 0:
        await ws_manager.broadcast_sync(
            build_ws_status(
                _get_mqtt_status(request), status_snapshot
            ).model_dump()
        )

    logger.info(
        "Internet NTP re-checked by %s: %s",
        _user, status_snapshot.get("state"),
    )
    return NtpStatusResponse(
        internet_ntp_state=status_snapshot.get("state", "unknown"),
        internet_ntp_server=status_snapshot.get("server"),
        internet_ntp_offset_s=status_snapshot.get("offset_s"),
        internet_ntp_rtt_s=status_snapshot.get("rtt_s"),
        internet_ntp_last_checked_at=status_snapshot.get("last_checked_at"),
        internet_ntp_error=status_snapshot.get("error"),
    )


# ------------------------------------------------------------------
# Latest readings
# ------------------------------------------------------------------

@router.get(
    "/readings/latest",
    response_model=HistoryResponse,
    responses={503: {"model": ErrorResponse}},
)
async def get_latest_readings(
    request: Request,
    limit: int = Query(default=10, ge=1, le=500),
) -> HistoryResponse:
    """Return the N most recent CSI readings from InfluxDB."""
    influx_client = _get_influx(request)
    if not influx_client.connected:
        raise HTTPException(status_code=503, detail="InfluxDB not available")

    points = influx_client.query_latest(limit=limit)
    return HistoryResponse(count=len(points), points=points)


# ------------------------------------------------------------------
# Time-range query
# ------------------------------------------------------------------

@router.get(
    "/readings",
    response_model=HistoryResponse,
    responses={503: {"model": ErrorResponse}},
)
async def get_readings_range(
    request: Request,
    start: str = Query(description="ISO 8601 start time (required)"),
    stop: str | None = Query(default=None, description="ISO 8601 end time (default: now)"),
    limit: int = Query(default=1000, ge=1, le=10000),
) -> HistoryResponse:
    """Query CSI readings within a time range."""
    influx_client = _get_influx(request)
    if not influx_client.connected:
        raise HTTPException(status_code=503, detail="InfluxDB not available")

    try:
        start_dt = datetime.fromisoformat(start)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid start time format")

    stop_dt: datetime | None = None
    if stop:
        try:
            stop_dt = datetime.fromisoformat(stop)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid stop time format")

    points = influx_client.query_range(start=start_dt, stop=stop_dt, limit=limit)
    return HistoryResponse(count=len(points), points=points)


# ------------------------------------------------------------------
# Manual retention cleanup
# ------------------------------------------------------------------

@router.get(
    "/cleanup",
    response_model=CleanupResponse,
    responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def cleanup_now(
    request: Request, _user: str = Depends(get_current_user)
) -> CleanupResponse:
    """Delete readings older than the retention window now (manual trigger).

    Requires a bearer token (it deletes data).  The periodic task runs the
    same cleanup on its own schedule; use this endpoint to trigger it
    immediately, e.g. right after a CSV export.
    """
    influx_client = _get_influx(request)
    if not influx_client.connected:
        raise HTTPException(status_code=503, detail="InfluxDB not available")

    days = get_settings().csi_retention_days
    if not influx_client.delete_older_than(days):
        raise HTTPException(status_code=503, detail="InfluxDB cleanup failed")
    logger.info("Manual retention cleanup triggered by %s", _user)
    return CleanupResponse(retention_days=days)
