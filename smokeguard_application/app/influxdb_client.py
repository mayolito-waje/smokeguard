"""InfluxDB v2 client wrapper for writing and querying CSI readings.

Uses the official influxdb-client Python library with batched writes.
"""

import logging
from datetime import datetime, timedelta, timezone

from influxdb_client import InfluxDBClient as _InfluxDBClient
from influxdb_client.client.write_api import WriteOptions
from influxdb_client.client.write.point import Point
from influxdb_client.rest import ApiException

from app.config import Settings
from app.models import CSIRecord, HistoryPoint

logger = logging.getLogger(__name__)


class InfluxDBError(Exception):
    """Raised when InfluxDB operations fail."""


class InfluxClient:
    """Wraps the InfluxDB v2 Python client for CSI data storage and retrieval."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: _InfluxDBClient | None = None
        self._write_api = None
        self._query_api = None
        self._connected = False

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Connect to InfluxDB and ensure the bucket exists."""
        self._client = _InfluxDBClient(
            url=self.settings.influxdb_url,
            token=self.settings.influxdb_token,
            org=self.settings.influxdb_org,
        )

        # Verify connectivity
        try:
            ready = self._client.ping()
            if not ready:
                raise InfluxDBError("InfluxDB ping failed")
        except Exception as exc:
            logger.warning("InfluxDB not reachable at %s: %s",
                           self.settings.influxdb_url, exc)
            self._connected = False
            return

        # Set up write API with batching
        write_options = WriteOptions(
            batch_size=500,
            flush_interval=1_000,  # ms
            retry_interval=1_000,
            max_retries=3,
            max_retry_delay=3_000,
        )
        self._write_api = self._client.write_api(
            write_options=write_options
        )
        self._query_api = self._client.query_api()

        # Ensure bucket exists
        self._ensure_bucket()
        self._connected = True
        logger.info("InfluxDB connected (bucket: %s)", self.settings.influxdb_bucket)

    def close(self) -> None:
        """Flush pending writes and close the client."""
        if self._client:
            try:
                self._write_api.close()  # type: ignore[union-attr]
            except Exception:
                pass
            self._client.close()
            self._connected = False

    def flush(self) -> None:
        """Force-flush any buffered writes."""
        if self._write_api:
            try:
                self._write_api.flush()
            except Exception as exc:
                logger.warning("InfluxDB flush error: %s", exc)

    @property
    def connected(self) -> bool:
        """Whether InfluxDB is currently reachable."""
        if not self._client or not self._connected:
            return False
        try:
            return self._client.ping()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write_reading(self, record: CSIRecord) -> None:
        """Convert a CSI record to an InfluxDB Point and enqueue for writing.

        Does not block — the WriteApi batches points in a background thread.
        """
        if not self._connected or not self._write_api:
            return

        try:
            point = self._to_point(record)
            self._write_api.write(
                bucket=self.settings.influxdb_bucket,
                org=self.settings.influxdb_org,
                record=point,
            )
        except Exception as exc:
            logger.error("InfluxDB write error: %s (packet %d)", exc, record.id)

    def _to_point(self, record: CSIRecord) -> Point:
        """Build an InfluxDB Point from a CSI record.

        Tags (low cardinality): mac, channel, bandwidth, ant
        Fields (numeric): all scalar metadata + per-subcarrier i_N / q_N
        Timestamp: timestamp_real in nanosecond precision
        """
        point = Point("csi_reading")

        # Tags
        point.tag("mac", record.mac)
        point.tag("channel", str(record.channel))
        point.tag("bandwidth", str(record.bandwidth))
        point.tag("ant", str(record.ant))
        point.tag("secondary_channel", str(record.secondary_channel))

        # Scalar metadata fields
        point.field("seq", record.id)
        point.field("rssi", record.rssi)
        point.field("rate", record.rate)
        point.field("sig_mode", record.sig_mode)
        point.field("mcs", record.mcs)
        point.field("smoothing", record.smoothing)
        point.field("not_sounding", record.not_sounding)
        point.field("aggregation", record.aggregation)
        point.field("stbc", record.stbc)
        point.field("fec_coding", record.fec_coding)
        point.field("sgi", record.sgi)
        point.field("noise_floor", record.noise_floor)
        point.field("ampdu_cnt", record.ampdu_cnt)
        point.field("secondary_channel_val", record.secondary_channel)
        point.field("sig_len", record.sig_len)
        point.field("rx_state", record.rx_state)
        point.field("first_word", record.first_word)
        point.field("csi_len", record.len)
        point.field("subcarrier_count", record.subcarrier_count)

        # Per-subcarrier I/Q fields
        i_samples = record.i_samples
        q_samples = record.q_samples
        for n in range(len(i_samples)):
            point.field(f"i_{n}", i_samples[n])
            point.field(f"q_{n}", q_samples[n])

        # Timestamp: use the CSI capture time in nanosecond precision
        ts_ns = int(record.timestamp_real * 1_000_000_000)
        point.time(ts_ns)

        return point

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def delete_older_than(self, days: int) -> bool:
        """Permanently delete readings older than `days` days from the bucket.

        Only CSI readings are removed (predicate on `_measurement`), leaving
        any other measurement in the bucket untouched. Export CSVs before the
        retention window expires — deleted points cannot be recovered.
        Returns True on success.
        """
        if not self._connected or not self._client:
            return False

        stop = datetime.now(timezone.utc) - timedelta(days=days)
        start = datetime(1970, 1, 1, tzinfo=timezone.utc)
        try:
            self._client.delete_api().delete(
                start=start,
                stop=stop,
                predicate='_measurement="csi_reading"',
                bucket=self.settings.influxdb_bucket,
                org=self.settings.influxdb_org,
            )
        except Exception as exc:
            logger.error("InfluxDB cleanup failed (delete < %s): %s", stop, exc)
            return False

        logger.info("Deleted CSI readings older than %s (%d-day retention)",
                    stop.isoformat(), days)
        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def query_latest(self, limit: int = 10) -> list[HistoryPoint]:
        """Return the N most recent CSI readings."""
        if not self._connected or not self._query_api:
            return []

        flux = f"""
        from(bucket: "{self.settings.influxdb_bucket}")
          |> range(start: -30d)
          |> filter(fn: (r) => r._measurement == "csi_reading")
          |> filter(fn: (r) => r._field =~ /^(seq|rssi|noise_floor|channel|bandwidth|mcs|i_[0-9]+|q_[0-9]+)$/)
          |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["_time"], desc: true)
          |> limit(n: {limit})
        """
        return self._execute_history_query(flux)

    def query_range(
        self,
        start: datetime,
        stop: datetime | None = None,
        limit: int = 1000,
    ) -> list[HistoryPoint]:
        """Return CSI readings within a time range."""
        if not self._connected or not self._query_api:
            return []

        start_iso = start.isoformat()
        stop_str = f', stop: {stop.isoformat()}' if stop else ''

        flux = f"""
        from(bucket: "{self.settings.influxdb_bucket}")
          |> range(start: {start_iso}{stop_str})
          |> filter(fn: (r) => r._measurement == "csi_reading")
          |> filter(fn: (r) => r._field =~ /^(seq|rssi|noise_floor|channel|bandwidth|mcs|i_[0-9]+|q_[0-9]+)$/)
          |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["_time"], desc: false)
          |> limit(n: {limit})
        """
        return self._execute_history_query(flux)

    def _execute_history_query(self, flux: str) -> list[HistoryPoint]:
        """Execute a Flux query and parse the results into HistoryPoints."""
        try:
            tables = self._query_api.query(flux)  # type: ignore[union-attr]
        except ApiException as exc:
            logger.error("InfluxDB query error: %s", exc)
            return []
        except Exception as exc:
            logger.error("InfluxDB query failed: %s", exc)
            return []

        points: list[HistoryPoint] = []
        for table in tables:
            for record in table.records:
                values = record.values
                # Reconstruct I/Q arrays from i_N / q_N fields
                i_list: list[int] = []
                q_list: list[int] = []
                n = 0
                while f"i_{n}" in values:
                    i_list.append(int(values.get(f"i_{n}", 0)))
                    q_list.append(int(values.get(f"q_{n}", 0)))
                    n += 1

                if not i_list:
                    continue

                ts = record.get_time()
                if ts is None:
                    continue

                points.append(HistoryPoint(
                    time=ts,
                    seq=int(values.get("seq", 0)),
                    rssi=int(values.get("rssi", 0)),
                    noise_floor=int(values.get("noise_floor", 0)),
                    mac=str(values.get("mac", "")),
                    channel=int(values.get("channel", 0)),
                    bandwidth=int(values.get("bandwidth", 0)),
                    mcs=int(values.get("mcs", 0)),
                    i=i_list,
                    q=q_list,
                ))

        return points

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _ensure_bucket(self) -> None:
        """Create the CSI bucket if it does not already exist."""
        if not self._client:
            return

        try:
            buckets_api = self._client.buckets_api()
            bucket = buckets_api.find_bucket_by_name(self.settings.influxdb_bucket)
            if bucket is None:
                logger.info(
                    "Creating InfluxDB bucket '%s' (org: %s, retention: 24h)",
                    self.settings.influxdb_bucket, self.settings.influxdb_org,
                )
                from influxdb_client import BucketRetentionRules
                buckets_api.create_bucket(
                    bucket_name=self.settings.influxdb_bucket,
                    org=self.settings.influxdb_org,
                    retention_rules=BucketRetentionRules(type="expire", every_seconds=2592000),  # 30 days
                )
        except Exception as exc:
            logger.warning("Could not ensure bucket exists: %s", exc)
