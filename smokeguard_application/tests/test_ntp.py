"""Unit tests for the stdlib SNTP probe, monitor, and settings."""

import asyncio
import struct
import time

import pytest
from pydantic import ValidationError

from app import ntp
from app.config import Settings


def _response(
    epoch: float,
    fraction: int = 0,
    *,
    mode: int = 4,
    version: int = 4,
    stratum: int = 2,
) -> bytes:
    data = bytearray(48)
    data[0] = (version << 3) | mode
    data[1] = stratum
    seconds = int(epoch + ntp.NTP_EPOCH_DELTA)
    struct.pack_into("!II", data, 40, seconds, fraction)
    return bytes(data)


# ---------------------------------------------------------------------------
# Wire protocol
# ---------------------------------------------------------------------------

def test_build_request_shape():
    request = ntp.build_request()
    assert len(request) == ntp.NTP_PACKET_SIZE
    assert request[0] == 0x23
    assert request[1:] == bytes(47)


def test_parse_transmit_time_includes_fraction():
    data = _response(1_800_000_000, fraction=0x80000000)
    assert ntp.parse_transmit_time(data) == pytest.approx(
        1_800_000_000.5, abs=1e-6
    )


def test_parse_rejects_unusable_responses():
    empty_transmit = bytearray(_response(1_800_000_000.0))
    empty_transmit[40:48] = bytes(8)
    cases = [
        _response(1_800_000_000.0)[:40],
        _response(1_800_000_000.0, mode=3),
        _response(1_800_000_000.0, mode=5),
        _response(1_800_000_000.0, version=2),
        _response(1_800_000_000.0, stratum=0),
        _response(1_800_000_000.0, stratum=16),
        bytes(empty_transmit),
        _response(1_500_000_000.0),  # before the 2020 floor
    ]
    for data in cases:
        with pytest.raises(ntp.NtpError):
            ntp.parse_transmit_time(data)


def test_compute_offset_rtt():
    offset, rtt = ntp.compute_offset_rtt(server_time=100.0, t1=90.0, t2=98.0)
    assert offset == pytest.approx(6.0)
    assert rtt == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

def _settings(**overrides) -> Settings:
    return Settings(
        jwt_secret="test-jwt",
        password_reset_secret="test-reset",
        _env_file=None,
        **overrides,
    )


def test_settings_ntp_server_list_is_trimmed():
    settings = _settings(ntp_servers=" a , b,, ")
    assert settings.ntp_server_list == ["a", "b"]
    assert _settings(ntp_servers="").ntp_server_list == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("ntp_timeout_s", 0),
        ("timestamp_max_skew_s", 0),
        ("ntp_recheck_interval_s", -1),
    ],
)
def test_settings_reject_invalid_ntp_values(key, value):
    with pytest.raises(ValidationError):
        _settings(**{key: value})


def test_monitor_success_then_failure(monkeypatch):
    monitor = ntp.NtpMonitor(["a", "b"], 0.1)
    monkeypatch.setattr(
        ntp,
        "probe_server",
        lambda server, timeout: ntp.NtpProbe(server, 0.012, 0.034),
    )

    snap = asyncio.run(monitor.refresh())
    assert snap["state"] == "online"
    assert snap["server"] == "a"
    assert snap["offset_s"] == 0.012
    assert snap["rtt_s"] == 0.034
    assert snap["last_checked_at"] is not None
    assert snap["error"] is None

    def fail(server, timeout):
        raise ntp.NtpError("blocked")

    monkeypatch.setattr(ntp, "probe_server", fail)
    snap = asyncio.run(monitor.refresh())
    assert snap["state"] == "offline"
    assert snap["server"] is None
    assert "blocked" in snap["error"]
    assert snap["last_checked_at"] is not None


def test_refresh_single_flight(monkeypatch):
    monitor = ntp.NtpMonitor(["a"], 0.1)
    calls = 0

    def slow(server, timeout):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return ntp.NtpProbe(server, 0.0, 0.01)

    monkeypatch.setattr(ntp, "probe_server", slow)

    async def scenario():
        return await asyncio.gather(monitor.refresh(), monitor.refresh())

    results = asyncio.run(scenario())
    assert calls == 1
    assert results[0]["state"] == "online"
    assert results[1]["state"] == "online"


def test_run_periodic_zero_interval_checks_once(monkeypatch):
    monitor = ntp.NtpMonitor(["a"], 0.1, recheck_interval_s=0)
    calls = 0

    def fake(server, timeout):
        nonlocal calls
        calls += 1
        return ntp.NtpProbe(server, 0.0, 0.01)

    monkeypatch.setattr(ntp, "probe_server", fake)
    asyncio.run(asyncio.wait_for(monitor.run_periodic(), timeout=1.0))
    assert calls == 1
    assert monitor.snapshot()["state"] == "online"


def test_monitor_without_servers_is_offline():
    monitor = ntp.NtpMonitor([], 0.1)
    snap = asyncio.run(monitor.refresh())
    assert snap["state"] == "offline"
    assert "no NTP servers" in snap["error"]
