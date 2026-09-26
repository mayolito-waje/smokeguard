"""SmokeGuard FastAPI application entry point.

Subscribes to CSI data published by an ESP32 receiver over MQTT (or replays
a CSV file), stores readings in InfluxDB, and streams live data to React
clients via WebSocket.

Usage:
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

Replay mode (no hardware needed):
    REPLAY_CSV=./sample_csv_output/sample.csv uv run uvicorn app.main:app
"""

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.influxdb_client import InfluxClient
from app.models import CSIRecord, SmokeRecord, WSCsiData, WSSmokeData, WSStatus
from app.mqtt_reader import CsiMqttReader
from app.routes import api, ws
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
    logger.info("Starting SmokeGuard backend (mqtt: %s:%d)",
                settings.mqtt_host, settings.mqtt_port)

    # Subsystems (initialized in lifespan)
    influx: InfluxClient | None = None
    ws_manager: ConnectionManager | None = None
    mqtt_reader: CsiMqttReader | None = None

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
        nonlocal influx, ws_manager, mqtt_reader

        # Connect to InfluxDB
        influx = InfluxClient(settings)
        influx.connect()

        # WebSocket manager
        ws_manager = ConnectionManager(max_queue_size=settings.ws_queue_maxsize)

        # Shared queue: MQTT network thread → async consumer
        queue: asyncio.Queue[CSIRecord] = asyncio.Queue(maxsize=2000)

        # Smoke queue: PMS5003 readings at ~1 Hz (separate from the 100 Hz
        # CSI queue so a CSI flood can never evict smoke samples)
        smoke_queue: asyncio.Queue[SmokeRecord] = asyncio.Queue(maxsize=512)

        # MQTT reader (paho network thread, or CSV replay thread)
        loop = asyncio.get_running_loop()
        mqtt_reader = CsiMqttReader(settings, queue, smoke_queue, loop)
        app.state.mqtt_status = mqtt_reader.status
        mqtt_reader.start()

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

        # Smoke consumer task: fans out from smoke_queue → InfluxDB + WebSocket
        async def consume_smoke_queue() -> None:
            while True:
                record = await smoke_queue.get()
                if record is None:  # Shutdown sentinel
                    break

                # Write to InfluxDB
                if influx and influx.connected:
                    influx.write_smoke(record)

                # Broadcast to WebSocket clients. exclude_none keeps the JSON
                # keys stable for the frontend while omitting BME680 fields
                # that legacy firmware doesn't send.
                if ws_manager and ws_manager.client_count > 0:
                    ws_msg = WSSmokeData(
                        t=record.timestamp_real,
                        pm1_0=record.pm1_0,
                        pm2_5=record.pm2_5,
                        pm10=record.pm10,
                        cnt0_3=record.cnt0_3,
                        cnt0_5=record.cnt0_5,
                        cnt1_0=record.cnt1_0,
                        cnt2_5=record.cnt2_5,
                        cnt5_0=record.cnt5_0,
                        cnt10=record.cnt10,
                        temp_c=record.temp_c,
                        pressure_hpa=record.pressure_hpa,
                        humidity_pct=record.humidity_pct,
                        gas_kohm=record.gas_kohm,
                        altitude_m=record.altitude_m,
                        rssi=record.rssi,
                    )
                    await ws_manager.broadcast_sync(ws_msg.model_dump(exclude_none=True))

        smoke_consumer_task = asyncio.create_task(consume_smoke_queue())

        # Periodic status broadcast task
        async def broadcast_status() -> None:
            while True:
                await asyncio.sleep(5.0)
                if ws_manager and ws_manager.client_count > 0:
                    snap = mqtt_reader.status.snapshot() if mqtt_reader else {}
                    await ws_manager.broadcast_sync(
                        WSStatus(
                            mqtt=mqtt_reader.status.transport_state(),
                            packets=snap.get("packets", 0),
                            dropped_lines=snap.get("dropped_lines", 0),
                            last_record_at=snap.get("last_record_at"),
                            error=snap.get("error"),
                            receiver_online=snap.get("receiver_online"),
                            ntp_synced=snap.get("ntp_synced"),
                        ).model_dump()
                    )

        status_task = asyncio.create_task(broadcast_status())

        # Periodic cleanup task: purge readings older than the retention
        # window (export CSVs first — deleted points cannot be recovered)
        async def cleanup_old_readings() -> None:
            await asyncio.sleep(60.0)  # let startup settle first
            while True:
                if influx and influx.connected:
                    influx.delete_older_than(settings.csi_retention_days)
                await asyncio.sleep(settings.cleanup_interval_hours * 3600)

        cleanup_task = asyncio.create_task(cleanup_old_readings())

        # Store references for shutdown and routes
        app.state.influx = influx
        app.state.ws_manager = ws_manager
        app.state.mqtt_reader = mqtt_reader
        app.state.num_subcarriers = settings.num_subcarriers
        app.state._consumer_task = consumer_task
        app.state._smoke_consumer_task = smoke_consumer_task
        app.state._status_task = status_task
        app.state._cleanup_task = cleanup_task
        app.state._queue = queue
        app.state._smoke_queue = smoke_queue
        app.state._settings = settings

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        # Signal consumers to stop
        queue = getattr(app.state, "_queue", None)
        if queue:
            await queue.put(None)
        smoke_queue = getattr(app.state, "_smoke_queue", None)
        if smoke_queue:
            await smoke_queue.put(None)

        # Cancel tasks
        for attr in ("_consumer_task", "_smoke_consumer_task", "_status_task", "_cleanup_task"):
            task = getattr(app.state, attr, None)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Stop MQTT reader
        if mqtt_reader:
            mqtt_reader.stop()

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
