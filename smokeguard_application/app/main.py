"""SmokeGuard FastAPI application entry point.

Reads CSI data from an ESP32 receiver over serial (or replays a CSV file),
stores readings in InfluxDB, and streams live data to React clients via WebSocket.

Usage:
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

Replay mode (no hardware needed):
    SERIAL_PORT=./sample_csv_output/sample.csv uv run uvicorn app.main:app
"""

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.influxdb_client import InfluxClient
from app.models import CSIRecord, WSCsiData, WSStatus
from app.routes import api, ws
from app.serial_reader import CSISerialReader
from app.websocket_manager import ConnectionManager

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("smokeguard")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    logger.info("Starting SmokeGuard backend (serial: %s)", settings.serial_port)

    # Subsystems (initialized in lifespan)
    influx: InfluxClient | None = None
    ws_manager: ConnectionManager | None = None
    serial_reader: CSISerialReader | None = None

    app = FastAPI(
        title="SmokeGuard CSI Backend",
        description="Wi-Fi CSI smoke detection — data ingestion and streaming API",
        version="0.1.0",
    )

    # ------------------------------------------------------------------
    # CORS — allow React dev server and production builds
    # ------------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Tighten for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Lifespan — startup / shutdown
    # ------------------------------------------------------------------
    @app.on_event("startup")
    async def on_startup() -> None:
        nonlocal influx, ws_manager, serial_reader

        # Connect to InfluxDB
        influx = InfluxClient(settings)
        influx.connect()

        # WebSocket manager
        ws_manager = ConnectionManager(max_queue_size=settings.ws_queue_maxsize)

        # Shared queue: serial thread → async consumer
        queue: asyncio.Queue[CSIRecord] = asyncio.Queue(maxsize=2000)

        # Serial reader (background thread)
        loop = asyncio.get_running_loop()
        serial_reader = CSISerialReader(settings, queue, loop)
        app.state.serial_status = serial_reader.status
        serial_reader.start()

        # Consumer task: fans out from queue → InfluxDB + WebSocket
        async def consume_queue() -> None:
            while True:
                record = await queue.get()
                if record is None:  # Shutdown sentinel
                    break

                # Write to InfluxDB
                if influx and influx.connected:
                    influx.write_reading(record)

                # Broadcast to WebSocket clients
                if ws_manager and ws_manager.client_count > 0:
                    ws_msg = WSCsiData(
                        timestamp_real=record.timestamp_real,
                        id=record.id,
                        mac=record.mac,
                        rssi=record.rssi,
                        channel=record.channel,
                        bandwidth=record.bandwidth,
                        mcs=record.mcs,
                        noise_floor=record.noise_floor,
                        ant=record.ant,
                        len=record.len,
                        subcarrier_count=record.subcarrier_count,
                        i=record.i_samples,
                        q=record.q_samples,
                        metadata=record.subcarrier_metadata,
                    )
                    await ws_manager.broadcast_sync(ws_msg.model_dump(by_alias=False))

        consumer_task = asyncio.create_task(consume_queue())

        # Periodic status broadcast task
        async def broadcast_status() -> None:
            while True:
                await asyncio.sleep(5.0)
                if ws_manager and ws_manager.client_count > 0:
                    snap = serial_reader.status.snapshot() if serial_reader else {}
                    await ws_manager.broadcast_sync(
                        WSStatus(
                            serial=(
                                "connected" if snap.get("connected")
                                else "disconnected" if snap.get("error") is None
                                else "reconnecting"
                            ),
                            packets=snap.get("packets", 0),
                            dropped_lines=snap.get("dropped_lines", 0),
                            last_record_at=snap.get("last_record_at"),
                            error=snap.get("error"),
                        ).model_dump()
                    )

        status_task = asyncio.create_task(broadcast_status())

        # Store references for shutdown and routes
        app.state.influx = influx
        app.state.ws_manager = ws_manager
        app.state.serial_reader = serial_reader
        app.state.num_subcarriers = settings.num_subcarriers
        app.state._consumer_task = consumer_task
        app.state._status_task = status_task
        app.state._queue = queue
        app.state._settings = settings

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        # Signal consumer to stop
        queue = getattr(app.state, "_queue", None)
        if queue:
            await queue.put(None)

        # Cancel tasks
        for attr in ("_consumer_task", "_status_task"):
            task = getattr(app.state, attr, None)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Stop serial reader
        if serial_reader:
            serial_reader.stop()

        # Flush and close InfluxDB
        if influx:
            influx.flush()
            influx.close()

        logger.info("SmokeGuard backend shut down")

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------
    app.include_router(ws.router)
    app.include_router(api.router)

    return app


# ---------------------------------------------------------------------------
# Module-level app instance (for uvicorn)
# ---------------------------------------------------------------------------

app = create_app()
