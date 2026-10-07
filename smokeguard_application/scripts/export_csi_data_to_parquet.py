#!/usr/bin/env python3
"""Export raw CSI readings from InfluxDB to Parquet (columnar + compressed).

Replaces the old CSV export — at ~100 Hz a 7-day window is tens of millions
of rows, so the pivot is streamed and written row-group by row-group
(``BATCH_ROWS``) instead of being materialised in memory.

Columns (one row per CSI reading):
    time                timestamp[ns, UTC] — InfluxDB _time (== timestamp_real)
    seq                 int64
    mac                 string (dictionary-encoded)
    rssi … first_word   int32 scalars
    subcarrier_count    int32
    i, q                list<int32> — de-interleaved I/Q per subcarrier

Absent values are stored as Parquet nulls (never coerced to 0).  The old
replay-format artifacts are not stored: ``type`` was the constant "CSI_DATA",
``local_timestamp`` was always 0, and the interleaved ``data`` JSON string is
rebuilt as ``data = [v for pair in zip(i, q) for v in pair]`` if a replay
CSV is ever needed.

Usage:
    uv run python scripts/export_csi_data_to_parquet.py                 # all data
    uv run python scripts/export_csi_data_to_parquet.py --limit 500     # latest 500
    uv run python scripts/export_csi_data_to_parquet.py \
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
    pa.field("seq", pa.int64()),
    pa.field("mac", pa.string()),
    pa.field("rssi", pa.int32()),
    pa.field("rate", pa.int32()),
    pa.field("sig_mode", pa.int32()),
    pa.field("mcs", pa.int32()),
    pa.field("bandwidth", pa.int32()),
    pa.field("smoothing", pa.int32()),
    pa.field("not_sounding", pa.int32()),
    pa.field("aggregation", pa.int32()),
    pa.field("stbc", pa.int32()),
    pa.field("fec_coding", pa.int32()),
    pa.field("sgi", pa.int32()),
    pa.field("noise_floor", pa.int32()),
    pa.field("ampdu_cnt", pa.int32()),
    pa.field("channel", pa.int32()),
    pa.field("secondary_channel", pa.int32()),
    pa.field("ant", pa.int32()),
    pa.field("sig_len", pa.int32()),
    pa.field("rx_state", pa.int32()),
    pa.field("csi_len", pa.int32()),
    pa.field("first_word", pa.int32()),
    pa.field("subcarrier_count", pa.int32()),
    pa.field("i", pa.list_(pa.int32())),
    pa.field("q", pa.list_(pa.int32())),
])

# InfluxDB value keys used verbatim as column names (tags included).
_SCALAR_NAMES = [f.name for f in SCHEMA if f.name not in ("time", "mac", "i", "q")]


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
    parser.add_argument("--out", default="sample_csv_output/csi_data.parquet",
                        help="Output Parquet path (default: sample_csv_output/csi_data.parquet)")
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

    # Pivot all fields wide so each record is one complete CSI row, then merge
    # the per-tag-set tables and sort globally: a single time-ordered stream
    # (the old scripts materialised every record in memory just to sort).
    flux = f"""
    from(bucket: "{bucket}")
      |> range(start: {args.start}{', stop: ' + args.stop if args.stop else ''})
      |> filter(fn: (r) => r._measurement == "csi_reading")
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> group()
      |> sort(columns: ["_time"])
    """
    if args.limit:
        flux += f"  |> tail(n: {args.limit})\n"

    print(f"Querying bucket '{bucket}' (range: {args.start}..{args.stop or 'now'})...")
    # The pivot over ~140 fields is slow: raise the client's default 10 s timeout.
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

                # Reconstruct de-interleaved I/Q from the i_N / q_N fields.
                i_vals: list[int | None] = []
                q_vals: list[int | None] = []
                n = 0
                while f"i_{n}" in v:
                    i_vals.append(as_int(v.get(f"i_{n}")))
                    q_vals.append(as_int(v.get(f"q_{n}")))
                    n += 1
                if not i_vals:
                    continue

                batch["time"].append(ts.astimezone(timezone.utc))
                batch["i"].append(i_vals)
                batch["q"].append(q_vals)
                mac = v.get("mac")
                batch["mac"].append(str(mac) if mac is not None else "")
                for name in _SCALAR_NAMES:
                    batch[name].append(as_int(v.get(name)))

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
