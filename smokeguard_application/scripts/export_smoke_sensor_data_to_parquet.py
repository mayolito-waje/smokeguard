#!/usr/bin/env python3
"""Export smoke-sensor readings from InfluxDB to Parquet (columnar + compressed).

Replaces the old CSV export; the query result is streamed and written
row-group by row-group (``BATCH_ROWS``).

Columns (one row per PMS5003 + BME680 reading):
    time            timestamp[ns, UTC] — InfluxDB _time (== timestamp_real)
    pm1_0 … cnt10   int32 particle fields
    rssi            int32
    temp_c, pressure_hpa, humidity_pct, gas_kohm, altitude_m
                    float64 — null for legacy (11-field) firmware rows

Usage:
    uv run python scripts/export_smoke_sensor_data_to_parquet.py                 # all data
    uv run python scripts/export_smoke_sensor_data_to_parquet.py --limit 500     # latest 500
    uv run python scripts/export_smoke_sensor_data_to_parquet.py \
        --start 2026-08-21T00:00:00Z --stop 2026-08-21T01:00:00Z
"""

import argparse
import os
import sys
from datetime import timezone

import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

# Rows buffered before a Parquet row group is flushed — bounds peak memory
# during the streaming export.
BATCH_ROWS = 20_000

SCHEMA = pa.schema([
    pa.field("time", pa.timestamp("ns", tz="UTC")),
    pa.field("pm1_0", pa.int32()),
    pa.field("pm2_5", pa.int32()),
    pa.field("pm10", pa.int32()),
    pa.field("cnt0_3", pa.int32()),
    pa.field("cnt0_5", pa.int32()),
    pa.field("cnt1_0", pa.int32()),
    pa.field("cnt2_5", pa.int32()),
    pa.field("cnt5_0", pa.int32()),
    pa.field("cnt10", pa.int32()),
    pa.field("rssi", pa.int32()),
    pa.field("temp_c", pa.float64()),
    pa.field("pressure_hpa", pa.float64()),
    pa.field("humidity_pct", pa.float64()),
    pa.field("gas_kohm", pa.float64()),
    pa.field("altitude_m", pa.float64()),
])

_INT_NAMES = [
    "pm1_0", "pm2_5", "pm10", "cnt0_3", "cnt0_5", "cnt1_0", "cnt2_5",
    "cnt5_0", "cnt10", "rssi",
]
_FLOAT_NAMES = ["temp_c", "pressure_hpa", "humidity_pct", "gas_kohm", "altitude_m"]


def as_int(value) -> int | None:
    """Coerce a Flux value to int; missing values stay null."""
    return int(value) if value is not None else None


def flush(writer: pq.ParquetWriter, batch: dict[str, list]) -> None:
    """Write the buffered rows as one row group and reset the buffers."""
    if not batch["time"]:
        return
    table = pa.Table.from_arrays(
        [pa.array(batch[f.name], type=f.type) for f in SCHEMA], schema=SCHEMA
    )
    writer.write_table(table)
    for values in batch.values():
        values.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="sample_csv_output/smoke_sensor_data.parquet",
                        help="Output Parquet path (default: sample_csv_output/smoke_sensor_data.parquet)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max number of rows to export, latest first (0 = all)")
    parser.add_argument("--start", default="-30d",
                        help="Flux range start (ISO 8601 or relative, default: -30d)")
    parser.add_argument("--stop", default="",
                        help="Flux range stop (ISO 8601, default: now)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="HTTP read timeout in seconds (default: 300)")
    args = parser.parse_args()

    load_dotenv()
    url = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
    token = os.environ.get("INFLUXDB_TOKEN", "")
    org = os.environ.get("INFLUXDB_ORG", "smokeguard")
    bucket = os.environ.get("INFLUXDB_BUCKET", "csi_data")

    if not token:
        print("INFLUXDB_TOKEN is empty in .env — run scripts/setup_influxdb.sh", file=sys.stderr)
        return 1

    # Pivot all fields wide so each record is one complete smoke row, then
    # merge the per-tag-set tables and sort globally (single stream; latest-N
    # via tail).
    flux = f"""
    from(bucket: "{bucket}")
      |> range(start: {args.start}{', stop: ' + args.stop if args.stop else ''})
      |> filter(fn: (r) => r._measurement == "smoke_reading")
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> group()
      |> sort(columns: ["_time"])
    """
    if args.limit:
        flux += f"  |> tail(n: {args.limit})\n"

    print(f"Querying bucket '{bucket}' (range: {args.start}..{args.stop or 'now'})...")
    # Raise the client's default 10 s read timeout — large pivots exceed it.
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    rows = 0
    writer = pq.ParquetWriter(args.out, SCHEMA, compression="zstd")
    try:
        with InfluxDBClient(url=url, token=token, org=org,
                            timeout=args.timeout * 1000) as client:
            batch: dict[str, list] = {name: [] for name in SCHEMA.names}
            for record in client.query_api().query_stream(flux):
                v = record.values
                ts = record.get_time()
                if ts is None:
                    continue

                batch["time"].append(ts.astimezone(timezone.utc))
                for name in _INT_NAMES:
                    batch[name].append(as_int(v.get(name)))
                for name in _FLOAT_NAMES:
                    value = v.get(name)
                    batch[name].append(float(value) if value is not None else None)

                rows += 1
                if rows % BATCH_ROWS == 0:
                    flush(writer, batch)
                if rows % 100_000 == 0:
                    print(f"  ... {rows:,} rows", flush=True)

            flush(writer, batch)
    finally:
        writer.close()

    size_mb = os.path.getsize(args.out) / 1e6
    print(f"Exported {rows:,} rows to {args.out} ({size_mb:.1f} MB, zstd)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
