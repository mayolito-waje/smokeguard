"""Route tests for the internet/NTP status and refresh endpoint."""

import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.mqtt_reader import MqttStatus
from app.routes import api


class FakeNtp:
    """Minimal stand-in for NtpMonitor."""

    def __init__(self, snapshot: dict) -> None:
        self._snapshot = dict(snapshot)
        self.calls = 0

    def snapshot(self) -> dict:
        return dict(self._snapshot)

    async def refresh(self) -> dict:
        self.calls += 1
        self._snapshot = {
            "state": "online",
            "server": "pool.ntp.org",
            "offset_s": 0.002,
            "rtt_s": 0.02,
            "last_checked_at": time.time(),
            "error": None,
        }
        return dict(self._snapshot)


class FakeWs:
    def __init__(self, clients: int = 0) -> None:
        self.client_count = clients
        self.messages: list[dict] = []

    async def broadcast_sync(self, payload: dict) -> None:
        self.messages.append(payload)


def _make_app(ntp_monitor, ws_manager=None) -> FastAPI:
    app = FastAPI()
    app.include_router(api.router)

    mqtt_status = MqttStatus()
    mqtt_status.update(
        connected=True,
        packets=10,
        dropped_lines=0,
        timestamps_corrected=3,
        last_timestamp_correction_at=time.time(),
    )
    app.state.mqtt_status = mqtt_status
    app.state.ntp = ntp_monitor
    app.state.influx = SimpleNamespace(connected=True)
    app.state.ws_manager = ws_manager or FakeWs()
    return app


def test_status_includes_ntp_and_correction_fields():
    ntp_monitor = FakeNtp(
        {
            "state": "offline",
            "server": None,
            "offset_s": None,
            "rtt_s": None,
            "last_checked_at": 123.0,
            "error": "unreachable",
        }
    )
    app = _make_app(ntp_monitor)

    with TestClient(app) as client:
        response = client.get("/api/status")

    assert response.status_code == 200
    body = response.json()
    assert body["internet_ntp_state"] == "offline"
    assert body["internet_ntp_error"] == "unreachable"
    assert body["internet_ntp_last_checked_at"] == 123.0
    assert body["timestamps_corrected"] == 3
    # The firmware flag stays distinct from the backend probe.
    assert body["ntp_synced"] is None


def test_ntp_check_requires_auth():
    app = _make_app(
        FakeNtp(
            {
                "state": "unknown",
                "server": None,
                "offset_s": None,
                "rtt_s": None,
                "last_checked_at": None,
                "error": None,
            }
        )
    )

    with TestClient(app) as client:
        response = client.post("/api/ntp/check")

    assert response.status_code == 401


def test_ntp_check_refreshes_and_broadcasts():
    ntp_monitor = FakeNtp(
        {
            "state": "unknown",
            "server": None,
            "offset_s": None,
            "rtt_s": None,
            "last_checked_at": None,
            "error": None,
        }
    )
    ws_manager = FakeWs(clients=1)
    app = _make_app(ntp_monitor, ws_manager)
    app.dependency_overrides[get_current_user] = lambda: "tester"

    with TestClient(app) as client:
        response = client.post("/api/ntp/check")

    assert response.status_code == 200
    assert ntp_monitor.calls == 1
    body = response.json()
    assert body["internet_ntp_state"] == "online"
    assert body["internet_ntp_server"] == "pool.ntp.org"

    assert ws_manager.messages
    status = ws_manager.messages[0]
    assert status["type"] == "status"
    assert status["internet_ntp_state"] == "online"
    assert status["timestamps_corrected"] == 3
