"""WebSocket endpoint for live CSI data streaming to the React frontend."""

import asyncio
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models import WSCsiData, WSStatus, WSWelcome
from app.websocket_manager import ConnectionManager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
) -> None:
    """WebSocket endpoint for live CSI data streaming.

    On connect, sends a welcome message with subcarrier metadata.
    Then streams each new CSI reading as it arrives from the serial reader,
    plus periodic status updates.
    """
    manager: ConnectionManager = websocket.app.state.ws_manager  # type: ignore[attr-defined]
    num_subcarriers: int = websocket.app.state.num_subcarriers  # type: ignore[attr-defined]
    serial_status = websocket.app.state.serial_status  # type: ignore[attr-defined]
    started_at = time.time()

    await manager.connect(websocket)

    # Send welcome message
    await websocket.send_text(json.dumps(
        WSWelcome(
            num_subcarriers=num_subcarriers,
            started_at=started_at,
        ).model_dump()
    ))

    # Send current serial status
    status_snapshot = serial_status.snapshot()
    await websocket.send_text(json.dumps(
        WSStatus(
            serial="connected" if status_snapshot["connected"] else "disconnected",
            packets=status_snapshot["packets"],
            dropped_lines=status_snapshot["dropped_lines"],
            last_record_at=status_snapshot["last_record_at"],
            error=status_snapshot["error"],
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
