#!/usr/bin/env bash
# SmokeGuard — InfluxDB v2 OSS one-time setup
# Downloads the InfluxDB binary, creates the org/bucket, and writes the
# API token to .env.  InfluxDB must already be running (start_influxdb.sh).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

INFLUXD_BIN="${PROJECT_DIR}/influxdb_bin/influxd"
INFLUX_URL="${INFLUXDB_URL:-http://127.0.0.1:8086}"
INFLUX_ORG="${INFLUXDB_ORG:-smokeguard}"
INFLUX_BUCKET="${INFLUXDB_BUCKET:-csi_data}"
INFLUX_USER="${INFLUXDB_USER:-smokeguard}"
INFLUX_PASS="${INFLUXDB_PASS:-smokeguard-dev}"

# ------------------------------------------------------------------
# 1. Download InfluxDB v2 OSS if not already present
# ------------------------------------------------------------------
INFLUX_VERSION="2.7.10"
if [ ! -x "$INFLUXD_BIN" ]; then
    echo "=== Downloading InfluxDB v${INFLUX_VERSION} ==="
    mkdir -p influxdb_bin
    curl -fsSL "https://dl.influxdata.com/influxdb/releases/influxdb2-${INFLUX_VERSION}_linux_amd64.tar.gz" \
        -o /tmp/influxdb2.tar.gz
    tar xzf /tmp/influxdb2.tar.gz -C /tmp
    cp "/tmp/influxdb2-${INFLUX_VERSION}/usr/bin/influxd" "$INFLUXD_BIN"
    chmod +x "$INFLUXD_BIN"
    rm -rf /tmp/influxdb2.tar.gz "/tmp/influxdb2-${INFLUX_VERSION}"
    echo "InfluxDB v${INFLUX_VERSION} installed to influxdb_bin/"
fi

# ------------------------------------------------------------------
# 2. Wait for InfluxDB to be ready
# ------------------------------------------------------------------
echo "=== Waiting for InfluxDB at ${INFLUX_URL} ==="
for i in $(seq 1 30); do
    if curl -s "${INFLUX_URL}/health" > /dev/null 2>&1; then
        echo "InfluxDB is ready."
        break
    fi
    echo "  waiting... (${i}/30)"
    sleep 1
done

# ------------------------------------------------------------------
# 3. Onboard (idempotent — only if setup is allowed)
# ------------------------------------------------------------------
SETUP_ALLOWED=$(curl -fsS "${INFLUX_URL}/api/v2/setup" 2>/dev/null | python3 -c "import sys,json; print(str(json.load(sys.stdin).get('allowed', False)).lower())" 2>/dev/null || echo "false")

if [ "$SETUP_ALLOWED" = "true" ]; then
    echo "=== Running InfluxDB initial setup ==="
    # Generate a random token
    TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(16))")
    curl -fsS -X POST "${INFLUX_URL}/api/v2/setup" \
        -H 'Content-Type: application/json' \
        -d "{
            \"username\": \"${INFLUX_USER}\",
            \"password\": \"${INFLUX_PASS}\",
            \"org\": \"${INFLUX_ORG}\",
            \"bucket\": \"${INFLUX_BUCKET}\",
            \"token\": \"${TOKEN}\",
            \"retentionPeriodSeconds\": 2592000
        }"
    echo ""

    # Write token to .env
    if grep -q '^INFLUXDB_TOKEN=' .env 2>/dev/null; then
        sed -i "s/^INFLUXDB_TOKEN=.*/INFLUXDB_TOKEN=${TOKEN}/" .env
    else
        echo "INFLUXDB_TOKEN=${TOKEN}" >> .env
    fi
    echo "Token written to .env"
else
    echo "InfluxDB already set up — skipping onboarding."
fi

echo "=== Setup complete ==="
echo "Start InfluxDB:  bash scripts/start_influxdb.sh"
echo "Start backend:   uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"
