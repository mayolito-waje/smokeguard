"""REST endpoints for smoking-activity detection (config, simulate, history)."""

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, Request

from app.models import (
    DetectionConfigResponse,
    DetectionConfigUpdate,
    DetectionEventDetail,
    DetectionEventSummary,
    DetectionEventsResponse,
    ErrorResponse,
)

if TYPE_CHECKING:
    from app.detection import DetectionManager
    from app.detection_store import DetectionStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/detection")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_detection(request: Request) -> "DetectionManager":
    return request.app.state.detection  # type: ignore[attr-defined]


def _get_store(request: Request) -> "DetectionStore":
    return request.app.state.detection_store  # type: ignore[attr-defined]


# ------------------------------------------------------------------
# Config (enable / disable)
# ------------------------------------------------------------------

@router.get("/config", response_model=DetectionConfigResponse)
async def get_detection_config(request: Request) -> DetectionConfigResponse:
    """Current enable/disable state (persisted in SQLite, default off)."""
    return DetectionConfigResponse(enabled=_get_store(request).get_enabled())


@router.post(
    "/config",
    response_model=DetectionConfigResponse,
    responses={400: {"model": ErrorResponse}},
)
async def set_detection_config(
    request: Request, body: DetectionConfigUpdate
) -> DetectionConfigResponse:
    """Enable or disable the automatic detection mechanism."""
    _get_store(request).set_enabled(body.enabled)
    logger.info("Smoking detection %s", "enabled" if body.enabled else "disabled")
    return DetectionConfigResponse(enabled=body.enabled)


# ------------------------------------------------------------------
# Simulate (manual trigger — works even while disabled)
# ------------------------------------------------------------------

@router.post(
    "/simulate",
    response_model=DetectionEventSummary,
    responses={409: {"model": ErrorResponse}},
)
async def simulate_detection(request: Request) -> DetectionEventSummary:
    """Manually fire the detector once (test override, bypasses the toggle)."""
    detection = _get_detection(request)
    if not detection.has_buffered_csi:
        raise HTTPException(
            status_code=409,
            detail="No CSI data buffered yet — start the data source and retry",
        )
    result = await detection.trigger()
    if result is None:
        raise HTTPException(status_code=409, detail="Detector did not fire")
    return DetectionEventSummary(**result)


# ------------------------------------------------------------------
# History (month / year listing + full event detail)
# ------------------------------------------------------------------

@router.get("/events", response_model=DetectionEventsResponse)
async def list_detection_events(
    request: Request,
    year: int = Query(..., ge=1970, le=2100, description="Year, e.g. 2026"),
    month: int = Query(..., ge=1, le=12, description="Month 1-12"),
) -> DetectionEventsResponse:
    """Detection events recorded in the given month, newest first."""
    events = _get_store(request).list_events(year, month)
    return DetectionEventsResponse(
        year=year,
        month=month,
        count=len(events),
        events=[DetectionEventSummary(**e) for e in events],
    )


@router.get(
    "/events/{event_id}",
    response_model=DetectionEventDetail,
    responses={404: {"model": ErrorResponse}},
)
async def get_detection_event(
    request: Request, event_id: int
) -> DetectionEventDetail:
    """Full detail for one event, including the CSI/smoke snapshots."""
    event = _get_store(request).get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return DetectionEventDetail(**event)
