"""WebSocket connection manager with per-client bounded queues.

Slow clients drop oldest messages rather than blocking the 100 Hz pipeline.
"""

import asyncio
import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages connected WebSocket clients and broadcasts CSI readings.

    Each client gets its own asyncio.Queue and sender task. When a queue is
    full, the oldest message is dropped to keep the pipeline flowing at 100 Hz.
    """

    def __init__(self, max_queue_size: int = 256) -> None:
        self.max_queue_size = max_queue_size
        self._connections: dict[WebSocket, asyncio.Queue[str]] = {}
        self._sender_tasks: dict[WebSocket, asyncio.Task[None]] = {}

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection and start its sender task."""
        await websocket.accept()
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self.max_queue_size)
        self._connections[websocket] = queue

        task = asyncio.create_task(self._sender(websocket, queue))
        self._sender_tasks[websocket] = task
        logger.info(
            "WebSocket client connected (total: %d)", len(self._connections)
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket client and cancel its sender task."""
        self._connections.pop(websocket, None)
        task = self._sender_tasks.pop(websocket, None)
        if task and not task.done():
            task.cancel()
        logger.info(
            "WebSocket client disconnected (total: %d)", len(self._connections)
        )

    async def broadcast(self, message: str) -> None:
        """Enqueue a pre-serialized JSON message for all connected clients.

        Called from the async consumer task. If a client queue is full, the
        oldest message is dropped first.
        """
        dead: list[WebSocket] = []
        for ws, queue in list(self._connections.items()):
            try:
                if queue.full():
                    # Drop oldest to make room — keep the latest CSI frame
                    queue.get_nowait()
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass  # Should not happen after the drop above
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

    async def broadcast_sync(self, message: dict) -> None:
        """Serialize and broadcast a message dict to all connected clients.

        Convenience wrapper around broadcast() that handles JSON encoding.
        """
        payload = json.dumps(message)
        await self.broadcast(payload)

    @property
    def client_count(self) -> int:
        """Number of currently connected WebSocket clients."""
        return len(self._connections)

    async def _sender(
        self, websocket: WebSocket, queue: asyncio.Queue[str]
    ) -> None:
        """Per-client sender task: drains the queue and sends to the socket."""
        try:
            while True:
                message = await queue.get()
                try:
                    await websocket.send_text(message)
                except Exception:
                    # Client disconnected or send failed
                    break
        except asyncio.CancelledError:
            pass
        finally:
            self.disconnect(websocket)
