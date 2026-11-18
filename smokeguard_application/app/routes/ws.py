"""WebSocket endpoint for live CSI data streaming to the React frontend."""

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models import WSWelcome
from app.status import build_ws_status
from app.websocket_manager import ConnectionManager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
) -> None:
    """WebSocket endpoint for live CSI data streaming.

    On connect, sends a welcome message with subcarrier metadata.
    Then streams each new CSI reading as it arrives from the MQTT reader,
    plus periodic status updates.
    """
    manager: ConnectionManager = websocket.app.state.ws_manager  # type: ignore[attr-defined]
    num_subcarriers: int = websocket.app.state.num_subcarriers  # type: ignore[attr-defined]
    mqtt_status = websocket.app.state.mqtt_status  # type: ignore[attr-defined]
    started_at = time.time()

    await manager.connect(websocket)

    # Send welcome message
    await websocket.send_text(json.dumps(
        WSWelcome(
            num_subcarriers=num_subcarriers,
            started_at=started_at,
        ).model_dump()
    ))

    # Send current MQTT + internet/NTP status
    ntp_monitor = getattr(websocket.app.state, "ntp", None)
    await websocket.send_text(json.dumps(
        build_ws_status(
            mqtt_status,
            ntp_monitor.snapshot() if ntp_monitor is not None else None,
        ).model_dump()
    ))

    try:
        while True:
            # Keep connection alive; clients may send ping frames
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=30.0
                )
                # Respond to ping messages
                if data and "ping" in data:
                    await websocket.send_text('{"type":"pong"}')
            except asyncio.TimeoutError:
                # Send a keep-alive ping (no timeout disconnect)
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket error")
    finally:
        manager.disconnect(websocket)
