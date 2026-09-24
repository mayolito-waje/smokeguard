"""Background MQTT reader for ESP32 CSI and air-quality data.

Subscribes to the CSI data topic published by the csi_recv_mqtt firmware
and the PMS5003 air-quality topic (or replays a CSI CSV file for
development), parses each payload, and pushes records onto asyncio.Queues
for consumption by the main event loop.

Key features:
- paho-mqtt network thread replaces the old serial thread; on_message runs
  off the event loop and pushes records via call_soon_threadsafe
- The firmware NTP-stamps the trailing timestamp_real column; before its
  first sync it sends 0.000000, so the backend falls back to receive-time
  estimation from the ESP32's microsecond counter (incl. 32-bit wraparound)
- Also subscribes to the status topic to track receiver online / NTP-sync
  state (single-receiver setup assumed — later heartbeats overwrite)
- The smoke topic carries PMS5003 CSV lines (timestamp,pm1_0,...,rssi);
  a 0/negative timestamp falls back to receive time, same as CSI
- Auto-reconnect via paho's built-in exponential backoff
- Replay mode: reads from a .csv file when replay_csv is set (CSI only)
"""

import asyncio
import csv
import json
import logging
import os
import time
from io import StringIO
from threading import Event, Lock, Thread

import paho.mqtt.client as mqtt

from app.config import Settings
from app.models import CSIRecord, DATA_COLUMNS_C5C6, DATA_COLUMNS_S3, SmokeRecord

logger = logging.getLogger(__name__)

# CSV file replay throttle (seconds between lines; 0 = no throttle)
# Set to 0.01 for ~100 Hz (realistic hardware rate), 0 for max speed
REPLAY_THROTTLE = 0.005  # 200 Hz — fast but allows consumer to keep up


class MqttStatus:
    """Thread-safe snapshot of the MQTT reader state."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.connected: bool = False
        self.packets: int = 0
        self.dropped_lines: int = 0
        self.reconnect_attempts: int = 0
        self.last_record_at: float | None = None
        self.error: str | None = None
        # Receiver state learned from the status topic (may stay None until
        # the first status message arrives)
        self.receiver_online: bool | None = None
        self.ntp_synced: bool | None = None
        self.receiver_client: str | None = None
        self.last_status_at: float | None = None
        # Smoke (air-quality) counters — debug/status use only
        self.smoke_packets: int = 0
        self.smoke_last_at: float | None = None

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
                "receiver_online": self.receiver_online,
                "ntp_synced": self.ntp_synced,
                "receiver_client": self.receiver_client,
                "last_status_at": self.last_status_at,
                "smoke_packets": self.smoke_packets,
                "smoke_last_at": self.smoke_last_at,
            }

    def update(self, **kwargs: object) -> None:
        """Atomically update one or more status fields."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    def transport_state(self) -> str:
        """Map the connection state to the WSStatus/API 3-state enum."""
        with self._lock:
            if self.connected:
                return "connected"
            if self.error is not None:
                return "reconnecting"
            return "disconnected"


class CsiMqttReader:
    """Subscribes to CSI and smoke-sensor data over MQTT (or replays a CSV file).

    Parsed CSIRecord / SmokeRecord objects are pushed onto their respective
    asyncio.Queues via call_soon_threadsafe, keeping the paho network thread
    free of async concerns — the same pattern the old serial thread used.
    """

    def __init__(
        self,
        settings: Settings,
        queue: asyncio.Queue[CSIRecord],
        smoke_queue: asyncio.Queue[SmokeRecord],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.settings = settings
        self._queue = queue
        self._smoke_queue = smoke_queue
        self._loop = loop
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._client: mqtt.Client | None = None
        self._stopping = False
        self.status = MqttStatus()

        # Timestamp tracking (reset on each reconnect, used only as a
        # fallback when the firmware's NTP-stamped timestamp is invalid)
        self._unix_start_time: float = 0.0
        self._offset_time: int | None = None
        self._prev_local_ts: int | None = None
        self._ts_overflow: int = 0

        # Replay mode is selected by the replay_csv setting (empty = live MQTT)
        self._replay_mode = bool(settings.replay_csv)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the replay thread, or start the paho network thread."""
        if self._replay_mode:
            if self._thread and self._thread.is_alive():
                logger.warning("Replay thread already running")
                return

            self._stop_event.clear()
            self._thread = Thread(target=self._run_replay, daemon=True, name="csi-replay")
            self._thread.start()
            logger.info("Replay started (csv: %s)", self.settings.replay_csv)
            return

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.settings.mqtt_client_id,
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        if self.settings.mqtt_username:
            # Only set creds when a username is configured — an empty string
            # would be sent to the broker as a literal username
            client.username_pw_set(
                self.settings.mqtt_username, self.settings.mqtt_password
            )
        # paho reconnects on its own with exponential backoff (1s → 30s)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=30)
        client.loop_start()
        self._client = client
        logger.info(
            "MQTT reader started (broker %s:%d, topics: %s, %s, %s)",
            self.settings.mqtt_host, self.settings.mqtt_port,
            self.settings.mqtt_topic_data, self.settings.mqtt_topic_status,
            self.settings.mqtt_topic_smoke,
        )

    def stop(self) -> None:
        """Stop the reader thread and disconnect from the broker."""
        self._stop_event.set()

        if self._replay_mode:
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=3.0)
                if self._thread.is_alive():
                    logger.warning("Replay thread did not stop within timeout")
            return

        client = self._client
        if client is None:
            return

        # Mark the stop so _on_disconnect doesn't record the clean drop as
        # an error (paho fires it with "Normal disconnection" here)
        self._stopping = True
        self.status.update(connected=False)
        try:
            client.disconnect()  # Returns MQTT_ERR_NO_CONN if not connected
        except Exception:
            logger.debug("MQTT disconnect raised", exc_info=True)
        try:
            client.loop_stop()  # Joins the network thread
        except Exception:
            logger.debug("MQTT loop_stop raised", exc_info=True)
        logger.info("MQTT reader stopped")

    # ------------------------------------------------------------------
    # paho callbacks (run on the paho network thread)
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        """Subscribe to the data topics once connected to the broker."""
        if reason_code.is_failure:
            self.status.update(
                connected=False,
                error=f"MQTT connect failed: {reason_code}",
                reconnect_attempts=self.status.reconnect_attempts + 1,
            )
            logger.warning("MQTT connect failed: %s", reason_code)
            return

        client.subscribe([
            (self.settings.mqtt_topic_data, 0),    # CSI is loss-tolerant
            (self.settings.mqtt_topic_status, 1),  # status is retained/low-rate
            (self.settings.mqtt_topic_smoke, 0),   # PMS5003, live-only
        ])
        self._reset_timestamps()
        self.status.update(connected=True, error=None, reconnect_attempts=0)
        logger.info("Connected to MQTT broker, subscribed to data topics")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        """Record the drop; paho's network thread keeps retrying on its own."""
        if self._stopping or reason_code is None:
            # Client-initiated disconnect (stop()) — nothing to report
            self.status.update(connected=False)
            return
        self.status.update(
            connected=False,
            error=f"MQTT disconnected: {reason_code}",
            reconnect_attempts=self.status.reconnect_attempts + 1,
        )
        logger.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        """Dispatch a message by topic: CSI data, smoke data, or receiver status.

        The dispatch is wrapped so a malformed payload can never raise out
        of this callback — an uncaught exception here would kill paho's
        network thread and leave the reader permanently deaf.
        """
        try:
            if msg.topic == self.settings.mqtt_topic_data:
                # The data topic is QoS 0; skip retained messages as a cheap
                # guard against stale-line replays
                if msg.retain:
                    return
                line = msg.payload.decode("utf-8", errors="replace").strip()
                if line:
                    self._process_line(line)
            elif msg.topic == self.settings.mqtt_topic_smoke:
                # Live-only QoS 0 topic; skip retained messages as a
                # stale-line guard
                if msg.retain:
                    return
                line = msg.payload.decode("utf-8", errors="replace").strip()
                if line:
                    self._process_smoke_line(line)
            elif msg.topic == self.settings.mqtt_topic_status:
                self._handle_status_payload(msg.payload)
        except Exception:
            logger.exception("Error handling MQTT message on topic %s", msg.topic)
            self.status.update(dropped_lines=self.status.dropped_lines + 1)

    def _handle_status_payload(self, payload) -> None:
        """Parse a receiver status JSON message and update the status."""
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        if not isinstance(data, dict) or "state" not in data:
            return

        if data["state"] == "online":
            self.status.update(
                receiver_online=True,
                ntp_synced=bool(data.get("ntp_synced")),
                receiver_client=data.get("client"),
                last_status_at=time.time(),
            )
        elif data["state"] == "offline":
            # LWT message: the receiver died. Leave the other fields alone —
            # an offline LWT carries no ntp_synced/channel/etc.
            self.status.update(
                receiver_online=False,
                last_status_at=time.time(),
            )

    # ------------------------------------------------------------------
    # CSV replay mode
    # ------------------------------------------------------------------

    def _run_replay(self) -> None:
        """Replay CSI data from a CSV file (for development without hardware)."""
        csv_path = self.settings.replay_csv
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

        # MQTT payloads always carry the firmware-stamped timestamp_real
        # column; the fallback inside covers the pre-NTP-sync window
        self._process_row(row, has_ts_real=True)

    def _process_row(
        self, row: list[str], has_ts_real: bool = False
    ) -> None:
        """Parse a pre-split CSV row into a CSIRecord and push to the queue.

        Args:
            row: List of CSV field strings (already split by csv.reader).
            has_ts_real: If True, the row has timestamp_real appended as the
                last column (stamped by the firmware via NTP).
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
        if not isinstance(raw_data, list):
            logger.debug("Data field is not a JSON array, skipping")
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
            # Use the firmware's NTP-stamped timestamp_real
            try:
                timestamp_real = float(row[-1])
            except (ValueError, IndexError):
                timestamp_real = self._compute_timestamp(fields)
            if timestamp_real <= 0.0:
                # The ESP32 sends 0.000000 until its first NTP sync —
                # fall back to receive-time estimation
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
    # Smoke line parsing
    # ------------------------------------------------------------------

    def _process_smoke_line(self, line: str) -> None:
        """Parse one PMS5003 CSV line into a SmokeRecord and push to the queue.

        CSV: timestamp,pm1_0,pm2_5,pm10,cnt0_3,cnt0_5,cnt1_0,cnt2_5,cnt5_0,cnt10,rssi
        (no header). timestamp is UNIX epoch seconds; 0 (NTP unsynced) falls
        back to receive time, mirroring the CSI timestamp_real fallback.
        """
        try:
            reader = csv.reader(StringIO(line))
            row = next(reader)
        except (csv.Error, StopIteration):
            self.status.update(dropped_lines=self.status.dropped_lines + 1)
            return

        if len(row) != 11:
            logger.debug("Smoke line field count %d (expected 11), skipping",
                         len(row))
            self.status.update(dropped_lines=self.status.dropped_lines + 1)
            return

        try:
            ts = float(row[0])
            values = [int(v) for v in row[1:]]
        except ValueError:
            logger.debug("Smoke line has non-numeric fields, skipping: %r",
                         line[:80])
            self.status.update(dropped_lines=self.status.dropped_lines + 1)
            return

        # Sensor clock not NTP-synced yet (or missing) — fall back to
        # receive time, same as the CSI timestamp_real fallback
        timestamp_real = ts if ts > 0.0 else time.time()

        record = SmokeRecord(
            timestamp_real=timestamp_real,
            pm1_0=values[0],
            pm2_5=values[1],
            pm10=values[2],
            cnt0_3=values[3],
            cnt0_5=values[4],
            cnt1_0=values[5],
            cnt2_5=values[6],
            cnt5_0=values[7],
            cnt10=values[8],
            rssi=values[9],
        )

        self._loop.call_soon_threadsafe(self._safe_smoke_enqueue, record)
        self.status.update(
            smoke_packets=self.status.smoke_packets + 1,
            smoke_last_at=timestamp_real,
        )

    def _safe_smoke_enqueue(self, record: SmokeRecord) -> None:
        """Enqueue a smoke record, dropping oldest if the queue is full.

        This is called via call_soon_threadsafe on the event loop thread.
        """
        try:
            self._smoke_queue.put_nowait(record)
        except asyncio.QueueFull:
            try:
                self._smoke_queue.get_nowait()  # Drop oldest
                self._smoke_queue.put_nowait(record)
            except (asyncio.QueueFull, asyncio.QueueEmpty):
                pass
        except RuntimeError:
            # Event loop already closed during shutdown — drop the record
            pass

    # ------------------------------------------------------------------
    # Timestamp computation (fallback only)
    # ------------------------------------------------------------------

    def _compute_timestamp(self, fields: dict) -> float:
        """Estimate a UNIX timestamp from the ESP32's microsecond counter.

        Used only when the firmware-stamped timestamp_real is missing or
        invalid (pre-NTP-sync). On the first packet of a session (or after
        reconnect), the current wall-clock time is recorded as
        UNIX_START_TIME and the ESP32's microsecond counter value is stored
        as OFFSET_TIME. Subsequent timestamps are computed relative to
        these baselines.

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
        except RuntimeError:
            # Event loop already closed during shutdown — drop the record
            pass

    def _reset_timestamps(self) -> None:
        """Reset timestamp tracking state (called on each new connection)."""
        self._unix_start_time = 0.0
        self._offset_time = None
        self._prev_local_ts = None
        self._ts_overflow = 0
