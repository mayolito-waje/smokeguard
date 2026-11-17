"""REST endpoints for smoking-activity detection (config, baseline, history)."""

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app import inference
from app.auth import get_current_user
from app.detection import BaselineCaptureError

from app.models import (
    BaselineCaptureResponse,
    DetectionConfigResponse,
    DetectionConfigUpdate,
    DetectionEventDeleteResponse,
    DetectionEventDetail,
    DetectionEventSummary,
    DetectionEventsResponse,
    DetectionLabel,
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

def _config_response(request: Request) -> DetectionConfigResponse:
    detection = _get_detection(request)
    store = _get_store(request)
    return DetectionConfigResponse(
        enabled=store.get_enabled(),
        csi_only_enabled=store.get_csi_only_enabled(),
        baseline_status=detection.background_status(),
        models_ready=inference.models_ready(),
    )


@router.get("/config", response_model=DetectionConfigResponse)
async def get_detection_config(request: Request) -> DetectionConfigResponse:
    """Current enable/disable state (persisted in SQLite, default off)."""
    return _config_response(request)


@router.post(
    "/config",
    response_model=DetectionConfigResponse,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
    },
)
async def set_detection_config(
    request: Request,
    body: DetectionConfigUpdate,
    _user: str = Depends(get_current_user),
) -> DetectionConfigResponse:
    """Update the detection toggles (bearer required; partial update)."""
    if body.enabled is None and body.csi_only_enabled is None:
        raise HTTPException(
            status_code=400,
            detail="Provide 'enabled' and/or 'csi_only_enabled'",
        )
    store = _get_store(request)
    if body.enabled is not None:
        store.set_enabled(body.enabled)
        logger.info(
            "Smoking detection %s (by %s)",
            "enabled" if body.enabled else "disabled",
            _user,
        )
    if body.csi_only_enabled is not None:
        store.set_csi_only_enabled(body.csi_only_enabled)
        logger.info(
            "Experimental CSI-only detection %s (by %s)",
            "enabled" if body.csi_only_enabled else "disabled",
            _user,
        )
    return _config_response(request)


# ------------------------------------------------------------------
# Static background capture (CSI model baseline)
# ------------------------------------------------------------------

@router.post(
    "/baseline",
    response_model=BaselineCaptureResponse,
    responses={401: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def capture_static_background(
    request: Request, _user: str = Depends(get_current_user)
) -> BaselineCaptureResponse:
    """Capture the static CSI background from buffered packets (bearer required).

    The capture must be made while the environment is quiet (no smoking /
    movement); the stored (52,) vector is subtracted from every CSI model
    window from then on.
    """
    detection = _get_detection(request)
    try:
        info = await detection.capture_background()
    except BaselineCaptureError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info(
        "Static background captured by %s (%d packets)",
        _user, info["packet_count"],
    )
    return BaselineCaptureResponse(**info)


# ------------------------------------------------------------------
# History (month / year listing + full event detail)
# ------------------------------------------------------------------

@router.get("/events", response_model=DetectionEventsResponse)
async def list_detection_events(
    request: Request,
    year: int = Query(..., ge=1970, le=2100, description="Year, e.g. 2026"),
    month: int = Query(..., ge=1, le=12, description="Month 1-12"),
    label: DetectionLabel | None = Query(
        None,
        description="Filter by detection class (omit for all)",
    ),
) -> DetectionEventsResponse:
    """Detection events recorded in the given month, newest first."""
    events = _get_store(request).list_events(year, month, label=label)
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


@router.delete(
    "/events/{event_id}",
    response_model=DetectionEventDeleteResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def delete_detection_event(
    request: Request,
    event_id: int,
    _user: str = Depends(get_current_user),
) -> DetectionEventDeleteResponse:
    """Delete one event from the SQLite store (bearer required; irreversible)."""
    if not _get_store(request).delete_event(event_id):
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    logger.info("Detection event #%d deleted (by %s)", event_id, _user)
    return DetectionEventDeleteResponse()
