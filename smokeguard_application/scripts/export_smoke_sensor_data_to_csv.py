#!/usr/bin/env python3
"""Export smoke-sensor readings from InfluxDB to the sensor's CSV format.

The output matches the IoT node's 16-column CSV layout (PMS5003 + BME680
block, same order the device publishes to MQTT), so it can be studied
directly or fed back line-by-line via mosquitto_pub:

    timestamp,pm1_0,pm2_5,pm10,cnt0_3,cnt0_5,cnt1_0,cnt2_5,cnt5_0,cnt10,
    temp_c,pressure_hpa,humidity_pct,gas_kohm,altitude_m,rssi

Legacy rows (written by 11-field firmware, no BME680) export with empty
BME680 cells.

Usage:
    uv run python scripts/export_smoke_sensor_data_to_csv.py                 # all data
    uv run python scripts/export_smoke_sensor_data_to_csv.py --limit 500     # latest 500
    uv run python scripts/export_smoke_sensor_data_to_csv.py \
        --start 2026-08-21T00:00:00Z --stop 2026-08-21T01:00:00Z
"""

import argparse
import csv
import os
import sys
from datetime import timezone

from dotenv import load_dotenv
from influxdb_client import InfluxDBClient

# Column order from the IoT node's smoke CSV payload (16 fields)
COLUMNS = [
    "timestamp", "pm1_0", "pm2_5", "pm10", "cnt0_3", "cnt0_5", "cnt1_0",
    "cnt2_5", "cnt5_0", "cnt10", "temp_c", "pressure_hpa", "humidity_pct",
    "gas_kohm", "altitude_m", "rssi",
]


def fmt_int(value) -> str:
    """Format an int field; empty string when absent (legacy rows)."""
    return "" if value is None else str(int(value))


def fmt_float(value) -> str:
    """Format a float field with up to 3 decimals, trailing zeros trimmed."""
    if value is None:
        return ""
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="sample_csv_output/smoke_sensor_data.csv",
                        help="Output CSV path (default: sample_csv_output/smoke_sensor_data.csv)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max number of rows to export (0 = all)")
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

    # Pivot all fields wide so each record is one complete smoke row.
    flux = f"""
    from(bucket: "{bucket}")
      |> range(start: {args.start}{', stop: ' + args.stop if args.stop else ''})
      |> filter(fn: (r) => r._measurement == "smoke_reading")
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"], desc: false)
    """

    print(f"Querying bucket '{bucket}' (range: {args.start}..{args.stop or 'now'})...")
    # Raise the client's default 10 s read timeout — large pivots exceed it.
    with InfluxDBClient(url=url, token=token, org=org,
                        timeout=args.timeout * 1000) as client:
        query_api = client.query_api()
        tables = query_api.query(flux)

    rows = 0
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)

        for table in tables:
            # Flux sorts ascending; --limit takes the latest N rows.
            records = table.records[-args.limit:] if args.limit else table.records
            for record in records:
                v = record.values

                ts = record.get_time()
                if ts is None:
                    continue
                ts = ts.astimezone(timezone.utc)

                writer.writerow([
                    f"{ts.timestamp():.6f}",          # timestamp (epoch seconds)
                    fmt_int(v.get("pm1_0")),
                    fmt_int(v.get("pm2_5")),
                    fmt_int(v.get("pm10")),
                    fmt_int(v.get("cnt0_3")),
                    fmt_int(v.get("cnt0_5")),
                    fmt_int(v.get("cnt1_0")),
                    fmt_int(v.get("cnt2_5")),
                    fmt_int(v.get("cnt5_0")),
                    fmt_int(v.get("cnt10")),
                    fmt_float(v.get("temp_c")),
                    fmt_float(v.get("pressure_hpa")),
                    fmt_float(v.get("humidity_pct")),
                    fmt_float(v.get("gas_kohm")),
                    fmt_float(v.get("altitude_m")),
                    fmt_int(v.get("rssi")),
                ])
                rows += 1

    print(f"Exported {rows} rows to {args.out}")
    print('Feed a line back via: mosquitto_pub -t home/smoke_sensor/data '
          f'-m "$(tail -n 1 {args.out})"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
