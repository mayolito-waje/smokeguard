"""Background serial reader for ESP32 CSI data.

Reads CSI CSV lines from the ESP32 receiver over serial (or replays a CSV
file for development), parses each line, and pushes CSIRecord instances onto
an asyncio.Queue for consumption by the main event loop.

Key features:
- Detects format variant (ESP32-S3 vs C5/C6) by field count
- Computes real UNIX timestamps from the ESP32's microsecond counter
- Handles 32-bit microsecond counter wraparound (~71.6 minutes)
- Graceful reconnection on serial errors (exponential backoff)
- Replay mode: reads from a .csv file when serial_port ends with .csv
"""

import asyncio
import csv
import json
import logging
import os
import queue
import time
from io import StringIO
from threading import Event, Lock, Thread
import serial

from app.config import Settings
from app.models import CSIRecord, DATA_COLUMNS_C5C6, DATA_COLUMNS_S3

logger = logging.getLogger(__name__)

# Maximum time (seconds) between reconnection attempts
MAX_RECONNECT_DELAY = 10.0

# CSV file replay throttle (seconds between lines; 0 = no throttle)
# Set to 0.01 for ~100 Hz (realistic hardware rate), 0 for max speed
REPLAY_THROTTLE = 0.005  # 200 Hz — fast but allows consumer to keep up


class SerialStatus:
    """Thread-safe snapshot of the serial reader state."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.connected: bool = False
        self.packets: int = 0
        self.dropped_lines: int = 0
        self.reconnect_attempts: int = 0
        self.last_record_at: float | None = None
        self.error: str | None = None

    def snapshot(self) -> dict:
        """Return a dict copy of the current status (thread-safe)."""
        with self._lock:
            return {
                "connected": self.connected,
                "packets": self.packets,
                "dropped_lines": self.dropped_lines,
                "reconnect_attempts": self.reconnect_attempts,
                "last_record_at": self.last_record_at,
                "error": self.error,
            }

    def update(self, **kwargs: object) -> None:
        """Atomically update one or more status fields."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)


class CSISerialReader:
    """Reads CSI CSV lines from a serial port (or CSV file) in a daemon thread.

    Parsed CSIRecord objects are pushed onto an asyncio.Queue via
    call_soon_threadsafe, keeping the serial I/O thread free of async concerns.
    """

    def __init__(
        self,
        settings: Settings,
        queue: asyncio.Queue[CSIRecord],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.settings = settings
        self._queue = queue
        self._loop = loop
        self._stop_event = Event()
        self._thread: Thread | None = None
        self.status = SerialStatus()

        # Timestamp tracking (reset on each reconnect)
        self._unix_start_time: float = 0.0
        self._offset_time: int | None = None
        self._prev_local_ts: int | None = None
        self._ts_overflow: int = 0

        # Determine if we're in replay mode (CSV file instead of serial)
        self._replay_mode = settings.serial_port.endswith(".csv")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the background reader thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Serial reader thread already running")
            return

        self._stop_event.clear()
        self._thread = Thread(target=self._run, daemon=True, name="csi-serial")
        self._thread.start()
        mode = "replay" if self._replay_mode else "serial"
        logger.info("Serial reader started (mode: %s)", mode)

    def stop(self) -> None:
        """Signal the reader thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                logger.warning("Serial reader thread did not stop within timeout")

    # ------------------------------------------------------------------
    # Thread main loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Main thread loop: connect → stream → reconnect."""
        if self._replay_mode:
            self._run_replay()
            return

        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                self._stream_serial()
                backoff = 1.0  # Reset backoff on clean exit
            except (serial.SerialException, OSError) as exc:
                self.status.update(
                    connected=False,
                    error=str(exc),
                    reconnect_attempts=self.status.reconnect_attempts + 1,
                )
                logger.warning(
                    "Serial error: %s — reconnecting in %.1fs",
                    exc, backoff,
                )
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, MAX_RECONNECT_DELAY)
            except Exception:
                logger.exception("Unexpected error in serial reader")
                self._stop_event.wait(5.0)

    # ------------------------------------------------------------------
    # Serial streaming
    # ------------------------------------------------------------------

    def _stream_serial(self) -> None:
        """Open serial port and process lines until error or stop."""
        ser = serial.Serial(
            port=self.settings.serial_port,
            baudrate=self.settings.serial_baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1.0,
        )
        logger.info("Serial port %s opened at %d baud",
                     self.settings.serial_port, self.settings.serial_baudrate)

        # Flush any stale data in the buffer
        ser.reset_input_buffer()

        # Reset timestamp state for this connection
        self._reset_timestamps()

        self.status.update(connected=True, error=None, reconnect_attempts=0)

        try:
            while not self._stop_event.is_set():
                raw = ser.readline()
                if not raw:
                    continue

                try:
                    line = raw.decode("utf-8", errors="replace").strip()
                except UnicodeDecodeError:
                    self.status.update(dropped_lines=self.status.dropped_lines + 1)
                    continue

                if not line:
                    continue

                self._process_line(line)
        finally:
            ser.close()
            self.status.update(connected=False)
            logger.info("Serial port closed")

    # ------------------------------------------------------------------
    # CSV replay mode
    # ------------------------------------------------------------------

    def _run_replay(self) -> None:
        """Replay CSI data from a CSV file (for development without hardware)."""
        csv_path = self.settings.serial_port
        if not os.path.isfile(csv_path):
            logger.error("Replay CSV not found: %s", csv_path)
            self.status.update(error=f"CSV not found: {csv_path}")
            return

        logger.info("Replay mode: reading from %s", csv_path)
        self._reset_timestamps()
        self.status.update(connected=True, error=None)

        with open(csv_path, "r") as f:
            reader = csv.reader(f)
            header = next(reader, None)  # Skip header row

            # Determine if the CSV already has timestamp_real appended
            has_ts_real = header and len(header) == len(DATA_COLUMNS_S3) + 1

            for row in reader:
                if self._stop_event.is_set():
                    break

                if not row or row[0] != "CSI_DATA":
                    continue

                # Pass the pre-parsed row directly to avoid re-splitting
                # the quoted JSON data field
                self._process_row(row, has_ts_real)

                if REPLAY_THROTTLE > 0:
                    self._stop_event.wait(REPLAY_THROTTLE)

        self.status.update(connected=False)
        logger.info("Replay complete: %d packets", self.status.packets)

    # ------------------------------------------------------------------
    # Line parsing
    # ------------------------------------------------------------------

    def _process_line(self, line: str) -> None:
        """Parse a single CSI_DATA CSV line and push to the queue."""
        if "CSI_DATA" not in line:
            return

        try:
            reader = csv.reader(StringIO(line))
            row = next(reader)
        except (csv.Error, StopIteration):
            self.status.update(dropped_lines=self.status.dropped_lines + 1)
            return

        self._process_row(row, has_ts_real=False)

    def _process_row(
        self, row: list[str], has_ts_real: bool = False
    ) -> None:
        """Parse a pre-split CSV row into a CSIRecord and push to the queue.

        Args:
            row: List of CSV field strings (already split by csv.reader).
            has_ts_real: If True, the row has timestamp_real appended as the
                last column (from CSV replay with pre-computed timestamps).
        """
        # Detect format by field count
        num_fields = len(row)
        s3_base = len(DATA_COLUMNS_S3)
        c5_base = len(DATA_COLUMNS_C5C6)

        if num_fields == s3_base or num_fields == s3_base + 1:
            columns = DATA_COLUMNS_S3
        elif num_fields == c5_base or num_fields == c5_base + 1:
            columns = DATA_COLUMNS_C5C6
        else:
            logger.debug("Unknown field count: %d (expected %d or %d), skipping",
                         num_fields, s3_base, c5_base)
            self.status.update(dropped_lines=self.status.dropped_lines + 1)
            return

        # Build field dict
        fields = dict(zip(columns, row))

        # Parse the JSON data array — it's at the index matching 'data' in columns
        data_idx = columns.index("data")
        try:
            raw_data = json.loads(row[data_idx])
        except (json.JSONDecodeError, IndexError):
            self.status.update(dropped_lines=self.status.dropped_lines + 1)
            return

        csi_len = int(fields["len"])
        if len(raw_data) != csi_len:
            logger.debug("Data length mismatch: expected %d, got %d",
                         csi_len, len(raw_data))
            self.status.update(dropped_lines=self.status.dropped_lines + 1)
            return

        # Compute timestamp_real
        if has_ts_real and num_fields == len(columns) + 1:
            # Use the pre-computed timestamp_real from the CSV
            try:
                timestamp_real = float(row[-1])
            except (ValueError, IndexError):
                timestamp_real = self._compute_timestamp(fields)
        else:
            timestamp_real = self._compute_timestamp(fields)

        # MAC filter (if configured)
        mac = fields["mac"]
        if self.settings.sender_mac and mac.upper() != self.settings.sender_mac.upper():
            return

        # Build the CSIRecord
        try:
            record = CSIRecord(
                id=int(fields["id"]),
                mac=mac,
                rssi=int(fields["rssi"]),
                rate=int(fields["rate"]),
                sig_mode=int(fields.get("sig_mode", 0)),
                mcs=int(fields.get("mcs", 0)),
                bandwidth=int(fields.get("bandwidth", 0)),
                smoothing=int(fields.get("smoothing", 0)),
                not_sounding=int(fields.get("not_sounding", 0)),
                aggregation=int(fields.get("aggregation", 0)),
                stbc=int(fields.get("stbc", 0)),
                fec_coding=int(fields.get("fec_coding", 0)),
                sgi=int(fields.get("sgi", 0)),
                noise_floor=int(fields["noise_floor"]),
                ampdu_cnt=int(fields.get("ampdu_cnt", 0)),
                channel=int(fields.get("channel", 0)),
                secondary_channel=int(fields.get("secondary_channel", 0)),
                local_timestamp=int(fields["local_timestamp"]),
                ant=int(fields.get("ant", 0)),
                sig_len=int(fields.get("sig_len", 0)),
                rx_state=int(fields["rx_state"]),
                len=csi_len,
                first_word=int(fields["first_word"]),
                data=raw_data,
                timestamp_real=timestamp_real,
            )
        except (ValueError, KeyError) as exc:
            logger.debug("Failed to build CSIRecord: %s", exc)
            self.status.update(dropped_lines=self.status.dropped_lines + 1)
            return

        # Push to async queue (thread-safe via call_soon_threadsafe)
        self._loop.call_soon_threadsafe(self._safe_enqueue, record)

        # Update status
        self.status.update(
            packets=self.status.packets + 1,
            last_record_at=timestamp_real,
            dropped_lines=self.status.dropped_lines,
        )

    # ------------------------------------------------------------------
    # Timestamp computation
    # ------------------------------------------------------------------

    def _compute_timestamp(self, fields: dict) -> float:
        """Compute a real UNIX timestamp from the ESP32's microsecond counter.

        On the first packet of a session (or after reconnect), the current
        wall-clock time is recorded as UNIX_START_TIME and the ESP32's
        microsecond counter value is stored as OFFSET_TIME. Subsequent
        timestamps are computed relative to these baselines.

        The ESP32's 32-bit microsecond counter wraps every ~71.6 minutes.
        We detect wraparound (new value < previous by more than 2^31) and
        accumulate overflow.
        """
        local_ts = int(fields["local_timestamp"])

        # Detect 32-bit wraparound
        if self._prev_local_ts is not None:
            if local_ts < self._prev_local_ts - (2 ** 31):
                self._ts_overflow += 2 ** 32
                logger.debug(
                    "local_timestamp wrap detected (prev=%d, cur=%d, overflow=%d)",
                    self._prev_local_ts, local_ts, self._ts_overflow,
                )
        self._prev_local_ts = local_ts

        adjusted = local_ts + self._ts_overflow

        # Establish baseline on first valid packet of this session
        if self._offset_time is None:
            self._offset_time = adjusted
            self._unix_start_time = time.time()

        return self._unix_start_time + (adjusted - self._offset_time) / 1_000_000.0

    def _safe_enqueue(self, record: CSIRecord) -> None:
        """Enqueue a record, dropping oldest if the queue is full.

        This is called via call_soon_threadsafe on the event loop thread.
        """
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()  # Drop oldest
                self._queue.put_nowait(record)
            except (asyncio.QueueFull, asyncio.QueueEmpty):
                pass

    def _reset_timestamps(self) -> None:
        """Reset timestamp tracking state (called on each new connection)."""
        self._unix_start_time = 0.0
        self._offset_time = None
        self._prev_local_ts = None
        self._ts_overflow = 0
