#!/usr/bin/env bash
# SmokeGuard — Start InfluxDB v2 OSS with data directory inside this project.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

DATA_DIR="${PROJECT_DIR}/influxdb_data"
INFLUXD_BIN="${PROJECT_DIR}/influxdb_bin/influxd"

if [ ! -x "$INFLUXD_BIN" ]; then
    echo "ERROR: influxd not found at ${INFLUXD_BIN}"
    echo "Run: bash scripts/setup_influxdb.sh"
    exit 1
fi

mkdir -p "$DATA_DIR"

exec "$INFLUXD_BIN" \
    --bolt-path "${DATA_DIR}/influxd.bolt" \
    --engine-path "${DATA_DIR}/engine" \
    --http-bind-address :8086 \
    --reporting-disabled
