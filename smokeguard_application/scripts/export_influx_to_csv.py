#!/usr/bin/env python3
"""Export raw CSI readings from InfluxDB to the original CSI_DATA CSV format.

The output matches the ESP32 receiver's CSV layout (26 columns, same order as
sample_csv_output/sample.csv), so it can be studied directly or fed back into
the backend in replay mode (REPLAY_CSV=./export.csv).

Not stored in InfluxDB (reconstructed as constants):
  - type           -> "CSI_DATA"
  - local_timestamp -> 0 (only timestamp_real is persisted)

Usage:
    uv run python scripts/export_influx_to_csv.py                 # all data
    uv run python scripts/export_influx_to_csv.py --limit 500     # latest 500
    uv run python scripts/export_influx_to_csv.py \
        --start 2026-08-21T00:00:00Z --stop 2026-08-21T01:00:00Z
"""

import argparse
import csv
import json
import os
import sys
from datetime import timezone

from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

# Column order from the ESP32 firmware CSV output (models.DATA_COLUMNS_S3 + timestamp_real)
COLUMNS = [
    "type", "id", "mac", "rssi", "rate", "sig_mode", "mcs", "bandwidth",
    "smoothing", "not_sounding", "aggregation", "stbc", "fec_coding",
    "sgi", "noise_floor", "ampdu_cnt", "channel", "secondary_channel",
    "local_timestamp", "ant", "sig_len", "rx_state", "len", "first_word",
    "data", "timestamp_real",
]

# Scalar metadata fields as stored in InfluxDB (excluding per-subcarrier i_N / q_N)
SCALAR_FIELDS = [
    "seq", "rssi", "rate", "sig_mode", "mcs", "smoothing", "not_sounding",
    "aggregation", "stbc", "fec_coding", "sgi", "noise_floor", "ampdu_cnt",
    "secondary_channel_val", "sig_len", "rx_state", "first_word", "csi_len",
    "subcarrier_count",
]


def as_int(value) -> int:
    """Coerce a possibly-None / float Flux value to int."""
    return int(value) if value is not None else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="sample_csv_output/influx_export.csv",
                        help="Output CSV path (default: sample_csv_output/influx_export.csv)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max number of rows to export (0 = all)")
    parser.add_argument("--start", default="-30d",
                        help="Flux range start (ISO 8601 or relative, default: -30d)")
    parser.add_argument("--stop", default="",
                        help="Flux range stop (ISO 8601, default: now)")
    args = parser.parse_args()

    load_dotenv()
    url = os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086")
    token = os.environ.get("INFLUXDB_TOKEN", "")
    org = os.environ.get("INFLUXDB_ORG", "smokeguard")
    bucket = os.environ.get("INFLUXDB_BUCKET", "csi_data")

    if not token:
        print("INFLUXDB_TOKEN is empty in .env — run scripts/setup_influxdb.sh", file=sys.stderr)
        return 1

    # Pivot all fields wide so each record is one complete CSI row.
    flux = f"""
    from(bucket: "{bucket}")
      |> range(start: {args.start}{', stop: ' + args.stop if args.stop else ''})
      |> filter(fn: (r) => r._measurement == "csi_reading")
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"], desc: false)
    """

    print(f"Querying bucket '{bucket}' (range: {args.start}..{args.stop or 'now'})...")
    with InfluxDBClient(url=url, token=token, org=org) as client:
        query_api = client.query_api()
        tables = query_api.query(flux)

    rows = 0
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)

        for table in tables:
            for record in table.records:
                if args.limit and rows >= args.limit:
                    break
                v = record.values

                # Reconstruct interleaved I/Q array from i_N / q_N fields.
                n = 0
                data: list[int] = []
                while f"i_{n}" in v:
                    data.append(as_int(v.get(f"i_{n}")))
                    data.append(as_int(v.get(f"q_{n}")))
                    n += 1
                if not data:
                    continue

                ts = record.get_time()
                if ts is None:
                    continue
                ts = ts.astimezone(timezone.utc)

                writer.writerow([
                    "CSI_DATA",                          # type (constant)
                    as_int(v.get("seq")),                # id
                    str(v.get("mac", "")),               # mac
                    as_int(v.get("rssi")),               # rssi
                    as_int(v.get("rate")),               # rate
                    as_int(v.get("sig_mode")),           # sig_mode
                    as_int(v.get("mcs")),                # mcs
                    as_int(v.get("bandwidth")),          # bandwidth
                    as_int(v.get("smoothing")),          # smoothing
                    as_int(v.get("not_sounding")),       # not_sounding
                    as_int(v.get("aggregation")),        # aggregation
                    as_int(v.get("stbc")),               # stbc
                    as_int(v.get("fec_coding")),         # fec_coding
                    as_int(v.get("sgi")),                # sgi
                    as_int(v.get("noise_floor")),        # noise_floor
                    as_int(v.get("ampdu_cnt")),          # ampdu_cnt
                    as_int(v.get("channel")),            # channel
                    as_int(v.get("secondary_channel")),  # secondary_channel
                    0,                                   # local_timestamp (not stored)
                    as_int(v.get("ant")),                # ant
                    as_int(v.get("sig_len")),            # sig_len
                    as_int(v.get("rx_state")),           # rx_state
                    as_int(v.get("csi_len")),            # len
                    as_int(v.get("first_word")),         # first_word
                    json.dumps(data),                    # data (interleaved I/Q)
                    f"{ts.timestamp():.6f}",             # timestamp_real
                ])
                rows += 1
            if args.limit and rows >= args.limit:
                break

    print(f"Exported {rows} rows to {args.out}")
    print("Replay it with: REPLAY_CSV=./{} uv run uvicorn app.main:app --port 8000".format(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
