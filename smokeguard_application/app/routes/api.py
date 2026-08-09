"""REST API endpoints for health checks and historical CSI data queries."""

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request

from app.models import (
    ErrorResponse,
    HealthResponse,
    HistoryPoint,
    HistoryResponse,
    StatusResponse,
    WSCsiData,
    WSStatus,
)
from app.serial_reader import SerialStatus

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


def _get_serial_status(request: Request) -> SerialStatus:
    return request.app.state.serial_status  # type: ignore[attr-defined]


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    """Liveness check: reports serial and InfluxDB connectivity."""
    serial = _get_serial_status(request).snapshot()
    influx_client = _get_influx(request)
    return HealthResponse(
        status="ok",
        serial_connected=serial["connected"],
        packets_received=serial["packets"],
        dropped_lines=serial["dropped_lines"],
        influxdb_connected=influx_client.connected,
    )


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------

@router.get("/status", response_model=StatusResponse)
async def get_status(request: Request) -> StatusResponse:
    """Detailed live status of the CSI data pipeline."""
    serial = _get_serial_status(request).snapshot()
    influx_client = _get_influx(request)
    ws_manager = _get_ws_manager(request)
    return StatusResponse(
        serial_connected=serial["connected"],
        packets_received=serial["packets"],
        dropped_lines=serial["dropped_lines"],
        last_record_at=serial["last_record_at"],
        reconnect_attempts=serial["reconnect_attempts"],
        influxdb_connected=influx_client.connected,
        websocket_clients=ws_manager.client_count,
        uptime_seconds=time.time() - SERVER_START_TIME,
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
