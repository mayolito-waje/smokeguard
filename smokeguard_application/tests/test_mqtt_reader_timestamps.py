"""Unit tests for host-clock timestamp correction in the MQTT reader."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from app.mqtt_reader import CsiMqttReader

MAC = "14:C1:9F:28:XX:XX"


class ImmediateLoop:
    """Run call_soon_threadsafe callbacks inline (no event loop needed)."""

    def call_soon_threadsafe(self, callback, *args):
        callback(*args)


def _settings(**overrides) -> SimpleNamespace:
    base = dict(
        replay_csv="",
        sender_mac="",
        timestamp_max_skew_s=300.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _reader(settings: SimpleNamespace | None = None):
    settings = settings or _settings()
    queue: asyncio.Queue = asyncio.Queue()
    smoke_queue: asyncio.Queue = asyncio.Queue()
    reader = CsiMqttReader(settings, queue, smoke_queue, ImmediateLoop())
    return reader, queue, smoke_queue


def _csi_row(local_timestamp: int, timestamp_real: float | str) -> list[str]:
    return [
        "CSI_DATA", "1", MAC, "-40", "11", "1", "0", "1",
        "0", "0", "0", "0", "0", "0", "-90", "0", "11", "1",
        str(local_timestamp), "0", "100", "0", "2", "0", "[1,2]",
        str(timestamp_real),
    ]


def _smoke_line(timestamp: float | str) -> str:
    # 11-field legacy format: timestamp + 9 PM/count fields + rssi
    return f"{timestamp},10,12,15,1800,600,200,90,30,4,-42"


# ---------------------------------------------------------------------------
# CSI
# ---------------------------------------------------------------------------

def test_boot_relative_csi_timestamp_is_corrected():
    reader, queue, _ = _reader()
    reader._process_row(_csi_row(1_000_000, 12.345678), has_ts_real=True)

    record = queue.get_nowait()
    assert abs(record.timestamp_real - time.time()) < 2.0
    assert reader.status.timestamps_corrected == 1
    assert (
        reader.status.last_timestamp_correction_at == record.timestamp_real
    )


def test_valid_csi_timestamp_is_preserved():
    now = time.time()
    reader, queue, _ = _reader()
    reader._process_row(_csi_row(1_000_000, now), has_ts_real=True)

    record = queue.get_nowait()
    assert record.timestamp_real == now
    assert reader.status.timestamps_corrected == 0


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf"])
def test_non_finite_csi_timestamp_is_corrected(raw):
    reader, queue, _ = _reader()
    reader._process_row(_csi_row(1_000_000, raw), has_ts_real=True)

    record = queue.get_nowait()
    assert abs(record.timestamp_real - time.time()) < 2.0
    assert reader.status.timestamps_corrected == 1


def test_counter_spacing_is_preserved_after_correction():
    reader, queue, _ = _reader()
    reader._process_row(_csi_row(1_000_000, 5.0), has_ts_real=True)
    reader._process_row(_csi_row(1_010_000, 5.0), has_ts_real=True)

    first = queue.get_nowait()
    second = queue.get_nowait()
    assert second.timestamp_real - first.timestamp_real == pytest.approx(
        0.01, abs=1e-6
    )


def test_replay_keeps_original_timestamp():
    reader, queue, _ = _reader(_settings(replay_csv="capture.csv"))
    reader._process_row(
        _csi_row(1_000_000, 1_600_000_000.0), has_ts_real=True
    )

    record = queue.get_nowait()
    assert record.timestamp_real == 1_600_000_000.0
    assert reader.status.timestamps_corrected == 0


def test_counter_wrap_continues_forward():
    reader, _, _ = _reader()
    first = reader._compute_timestamp(
        {"local_timestamp": 2**32 - 10_000}
    )
    second = reader._compute_timestamp({"local_timestamp": 10_000})
    assert second - first == pytest.approx(0.02, abs=1e-6)


def test_counter_reset_reanchors():
    reader, _, _ = _reader()
    reader._compute_timestamp({"local_timestamp": 100_000_000})
    timestamp = reader._compute_timestamp({"local_timestamp": 1_000_000})
    assert abs(timestamp - time.time()) < 1.0


def test_uptime_drop_resets_timestamp_state():
    reader, _, _ = _reader()
    reader._compute_timestamp({"local_timestamp": 50_000_000})
    assert reader._offset_time is not None

    reader._handle_status_payload(
        json.dumps(
            {"state": "online", "ntp_synced": True, "uptime_s": 100}
        ).encode()
    )
    reader._handle_status_payload(
        json.dumps(
            {"state": "online", "ntp_synced": True, "uptime_s": 1}
        ).encode()
    )
    assert reader._offset_time is None


def test_lwt_recovery_resets_timestamp_state():
    reader, _, _ = _reader()
    reader._compute_timestamp({"local_timestamp": 50_000_000})

    reader._handle_status_payload(b'{"state":"offline"}')
    reader._handle_status_payload(
        b'{"state":"online","ntp_synced":true,"uptime_s":50}'
    )
    assert reader._offset_time is None


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------

def test_smoke_zero_timestamp_falls_back_to_now():
    reader, _, smoke_queue = _reader()
    reader._process_smoke_line(_smoke_line(0))

    record = smoke_queue.get_nowait()
    assert abs(record.timestamp_real - time.time()) < 2.0
    assert reader.status.timestamps_corrected == 1


def test_smoke_boot_relative_timestamp_is_corrected():
    reader, _, smoke_queue = _reader()
    reader._process_smoke_line(_smoke_line(12.5))

    record = smoke_queue.get_nowait()
    assert abs(record.timestamp_real - time.time()) < 2.0


def test_smoke_recent_timestamp_is_preserved():
    timestamp = time.time() - 240
    reader, _, smoke_queue = _reader()
    reader._process_smoke_line(_smoke_line(timestamp))

    record = smoke_queue.get_nowait()
    assert record.timestamp_real == timestamp
    assert reader.status.timestamps_corrected == 0


def test_smoke_old_epoch_is_corrected():
    reader, _, smoke_queue = _reader()
    reader._process_smoke_line(_smoke_line(time.time() - 600))

    record = smoke_queue.get_nowait()
    assert abs(record.timestamp_real - time.time()) < 2.0
    assert reader.status.timestamps_corrected == 1


# ---------------------------------------------------------------------------
# Status snapshot
# ---------------------------------------------------------------------------

def test_snapshot_exposes_correction_counters():
    reader, _, _ = _reader()
    reader._process_row(_csi_row(1_000_000, 12.3), has_ts_real=True)

    snap = reader.status.snapshot()
    assert snap["timestamps_corrected"] == 1
    assert snap["last_timestamp_correction_at"] is not None
