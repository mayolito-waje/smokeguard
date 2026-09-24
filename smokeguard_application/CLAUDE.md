# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SmokeGuard is a Wi-Fi CSI (Channel State Information) based smoke detection research system. The workspace root (`smokeguard_application/`) is a FastAPI backend that ingests CSI data, stores it in a local InfluxDB, and streams to a React frontend via WebSocket. It is part of a larger multi-component project under `csi_research/csi/`:

- `csi_send/` — ESP32-S3 transmitter firmware (ESP-IDF C). Sends ESP-NOW packets at 100 Hz on Wi-Fi channel 11 (HT40).
- `csi_recv/` — ESP32-S3 receiver firmware (ESP-IDF C). Captures CSI data, applies gain compensation, streams CSV over serial (921600 baud). Pristine reference — do not edit.
- `csi_send_mqtt/`, `csi_recv_mqtt/` — MQTT variants of the above. The receiver joins the hotspot, NTP-syncs its wall clock, stamps the trailing `timestamp_real` CSV column, and publishes each CSI line to `home/csi/data` (plus JSON status to `home/csi/status`) on the Mosquitto broker. **These are the active firmware projects** — edits go here, not in the pristine `csi_send`/`csi_recv` projects.
- `esp_mac_identifier/` — Simple ESP32-S3 firmware to read and log MAC addresses of each device.
- `prototype/csi_parser/` — Python prototype for serial capture (`read_csi.py`) and real-time visualization (`csi_visualize.py`) using PyQt5 + pyqtgraph.
- `smokeguard_application/` — (this directory) FastAPI backend + InfluxDB + WebSocket streaming.

## How to Run

```bash
# Install dependencies
uv sync

# Start InfluxDB (run in a separate terminal)
bash scripts/start_influxdb.sh
bash scripts/setup_influxdb.sh   # first time only — creates org, bucket, token → .env

# Run with real hardware (Mosquitto broker + csi_recv_mqtt publishing)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# Run in replay mode (no hardware — replays sample CSV)
REPLAY_CSV=./sample_csv_output/sample.csv uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

InfluxDB v2.7.10 binary is at `influxdb_bin/influxd`. Data is stored in `./influxdb_data/` (gitignored). The bucket is `csi_data` in org `smokeguard` with 30-day retention.

## Backend Architecture

### Data flow

```
ESP32 Rx (csi_recv_mqtt) --MQTT--> Mosquitto broker (this laptop)
ESP32 PMS5003 sensor   --MQTT-->   home/csi/data (CSV lines, QoS 0)
        │                          home/csi/status (JSON, QoS 1 retained)
        │                          home/smoke_sensor/data (CSV, QoS 0)
        ▼                          (or CSV replay — CSI only)
┌──────────────────┐
│  CsiMqttReader   │  paho network thread
│  (mqtt_reader)   │  parses CSI CSV → CSIRecord, smoke CSV → SmokeRecord
└──────┬───────────┘
       │ call_soon_threadsafe
       ▼
┌─────────────────────────────┐
│  asyncio.Queues             │  CSI maxsize=2000, smoke maxsize=512
│  (overflow drops oldest)    │
└──────┬──────────────────────┘
       │ two consumer tasks
       ├──────────────────────────────┐
       ▼                              ▼
┌──────────────────┐     ┌──────────────────┐
│  InfluxClient    │     │ ConnectionMgr    │
│  (batched write) │     │ (per-client      │
│  csi_reading +   │     │  bounded queues) │
│  smoke_reading   │     │                  │
└──────────────────┘     └──────────────────┘
```

Key design decisions:
- The paho network thread never touches I/O outside of MQTT — it pushes parsed `CSIRecord`/`SmokeRecord` objects onto the async queues via `call_soon_threadsafe`.
- Reconnects are paho's job: `reconnect_delay_set(1, 30)` gives exponential backoff, and subscriptions are re-established in `on_connect`.
- Two consumer tasks drain the two queues independently — a 100 Hz CSI flood can never evict or delay 1 Hz smoke samples.
- WebSocket broadcast uses pre-serialized JSON (one `json.dumps` per message, not per client).
- Per-client bounded queues drop oldest messages when full — slow clients never stall the 100 Hz pipeline.
- InfluxDB writes use the official client's background batching thread (`batch_size=500`, `flush_interval=1000ms`).
- A periodic cleanup task (`cleanup_old_readings` in `main.py`) deletes readings older than `csi_retention_days` (default 7 days) via InfluxDB's delete API — first run 60 s after startup, then every `cleanup_interval_hours` (default 0.5 h = 30 min). Export CSVs (`scripts/export_influx_to_csv.py`) before the window expires; deleted points are unrecoverable.

### CSI parsing (`app/mqtt_reader.py`)

Two input modes:
- **MQTT mode** (default): subscribes to `home/csi/data` (QoS 0), `home/csi/status` (QoS 1), and `home/smoke_sensor/data` (QoS 0, PMS5003 air-quality sensor). Each data payload is one CSV line; retained data messages are skipped. Status JSON updates `receiver_online` / `ntp_synced` (the retained LWT `{"state":"offline"}` marks the receiver dead).
- **Replay mode**: when `REPLAY_CSV` is set to a `.csv` path, reads the CSV file directly. Pre-parsed rows are passed to `_process_row()` to avoid re-splitting the quoted JSON `data` column. Replays CSI only — smoke data requires live MQTT.

Smoke CSV format (11 fields, no header): `timestamp,pm1_0,pm2_5,pm10,cnt0_3,cnt0_5,cnt1_0,cnt2_5,cnt5_0,cnt10,rssi` — `_process_smoke_line()` parses it into a `SmokeRecord`. `timestamp` is UNIX epoch seconds; 0/negative (NTP unsynced) falls back to server receive time, mirroring the CSI fallback. Malformed lines bump `dropped_lines`.

Two format variants detected by field count:
- **ESP32-S3** (25/26 columns): `local_timestamp` at index 18, `data` at index 24 (or -2 with `timestamp_real` appended).
- **ESP32-C5/C6** (14/15 columns): `local_timestamp` at index 9, `data` at index 13 (or -2).

Timestamp handling: the firmware NTP-stamps the trailing `timestamp_real` column (UNIX epoch seconds with microseconds). Until its first NTP sync it sends `0.000000`, so the backend falls back to `UNIX_START_TIME + (local_timestamp - OFFSET_TIME) / 1_000_000.0`, where the baseline is captured on the first valid row of each connection. The ESP32's 32-bit microsecond counter wraps every ~71.6 minutes — `_compute_timestamp` detects wraparound and accumulates overflow.

Subcarrier layout for 128 I/Q values (64 subcarriers): guard band (pairs 0–5), lower data (6–31), DC null (32), upper data (33–58), guard band (59–63).

For unknown lengths (LLTF mode with ~50–57 subcarriers, or other custom modes), metadata is computed dynamically via `_build_lltf_metadata()` in `app/models.py`. This estimates guard bands (~5% each edge) and provides the `subcarrier_index_offset` so the frontend can map raw array indices to **signed subcarrier numbers** (e.g. array[28] − 28 = sc 0 = DC null for 57-subcarrier LLTF). The `CSIRecord.subcarrier_metadata` property always returns a valid metadata dict (never `None` for valid data).

### InfluxDB schema (`app/influxdb_client.py`)

- **Measurement**: `csi_reading`
- **Tags** (low cardinality): `mac`, `channel`, `bandwidth`, `ant`, `secondary_channel`
- **Fields**: scalar metadata (`seq`, `rssi`, `rate`, `mcs`, `noise_floor`, `sig_mode`, `smoothing`, `aggregation`, `stbc`, `fec_coding`, `sgi`, `ampdu_cnt`, `sig_len`, `rx_state`, `first_word`, `csi_len`, `subcarrier_count`) + per-subcarrier I/Q pairs (`i_0`..`i_63`, `q_0`..`q_63`)
- **Timestamp**: `timestamp_real` in nanosecond precision (`WritePrecision.NS`) — preserves the actual CSI capture time, not server receive time.
- **Queries**: Flux with `pivot(rowKey: ["_time"], columnKey: ["_field"])` to reconstruct wide rows, then `_execute_history_query` rebuilds `i`/`q` arrays from column names.

Second measurement written by `write_smoke()`:
- **Measurement**: `smoke_reading` (PMS5003 air-quality)
- **Tags**: none (single fixed sensor)
- **Fields**: `pm1_0`, `pm2_5`, `pm10`, `cnt0_3`, `cnt0_5`, `cnt1_0`, `cnt2_5`, `cnt5_0`, `cnt10`, `rssi`
- **Timestamp**: sensor `timestamp_real` (receive-time fallback) in nanosecond precision.
- Excluded from the 7-day CSI cleanup (the delete predicate is `_measurement="csi_reading"` only); the bucket's 30-day retention governs it.

### WebSocket messages (`app/websocket_manager.py`, `app/routes/ws.py`)

Server → client at `GET /ws`:
```json
{"type": "welcome", "source": "smokeguard-backend", "num_subcarriers": 64, "started_at": ...}
{"type": "csi", "t": 1785103746.01, "seq": 47835, "mac": "...", "rssi": -9,
 "channel": 11, "bandwidth": 1, "mcs": 0, "noise_floor": -98, "ant": 0,
 "length": 128, "subcarrier_count": 64,
 "i": [0,0,10,9,...], "q": [0,0,3,3,...],
 "metadata": {"data_start_idx": 6, "data_end_idx": 31, "dc_null_idx": 32, ...,
              "subcarrier_index_offset": 32}}
{"type": "smoke", "t": 1727090000.0, "pm1_0": 7, "pm2_5": 15, "pm10": 22,
 "cnt0_3": 1800, "cnt0_5": 600, "cnt1_0": 200, "cnt2_5": 90, "cnt5_0": 30,
 "cnt10": 4, "rssi": -52}
{"type": "status", "mqtt": "connected", "packets": 5000, "dropped_lines": 0,
 "receiver_online": true, "ntp_synced": true, ...}
{"type": "pong"}
```

The `metadata` field is always present (including `subcarrier_index_offset` for signed-subcarrier axis mapping). For LLTF / unknown subcarrier counts, metadata is computed dynamically — it is never `null` for valid data.

Periodic status messages broadcast every 5 seconds. Clients may send `{"type": "ping"}` — server responds `{"type": "pong"}`.

### REST API (`app/routes/api.py`)

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | `{status, mqtt_connected, packets_received, dropped_lines, influxdb_connected}` |
| `GET /api/status` | Detailed status with uptime, reconnect attempts, receiver online / NTP-sync state, WS client count |
| `GET /api/readings/latest?limit=N` | N most recent readings (default 10, max 500) |
| `GET /api/readings?start=<ISO>&stop=<ISO>&limit=N` | Time-range query (start required, max 10000) |
| `GET /api/cleanup` | Manually trigger retention cleanup (deletes readings older than `csi_retention_days`, default 7) |

## ESP-IDF Build System (sibling components)

ESP-IDF v5.5.3 at `~/.espressif/v5.5.3/esp-idf`. All C components target ESP32-S3.

```bash
. ~/.espressif/v5.5.3/esp-idf/export.sh
cd ../csi_recv_mqtt && idf.py build    # or: csi_send_mqtt, esp_mac_identifier
idf.py -p /dev/ttyUSB0 flash monitor
```

## Hardware Architecture

```
[ESP32-S3 "Tx"] --ESP-NOW (ch 11, HT40, MCS0, 100 Hz)--> [ESP32-S3 "Rx"]
  14:C1:9F:28:C1:A0                                        14:C1:9F:28:99:DC
                                                                  |
                                                   Wi-Fi (hotspot) → Mosquitto
                                                       home/csi/data|status
                                                                  |
                                                                  v
                                                         This application
```

Bridge MAC: `14:C1:9F:28:88:E8`. The receiver filters CSI packets by sender MAC (`CFG_CSI_SENDER_MAC` in `csi_recv_mqtt/main/.env`).

## CSI Data Format

ESP32-S3 CSV columns:
`type, id, mac, rssi, rate, sig_mode, mcs, bandwidth, smoothing, not_sounding, aggregation, stbc, fec_coding, sgi, noise_floor, ampdu_cnt, channel, secondary_channel, local_timestamp, ant, sig_len, rx_state, len, first_word, data, timestamp_real`

- `data`: JSON array of interleaved I/Q integers — `[I1, Q1, I2, Q2, ...]`. Length matches the `len` field (128 = 64 subcarriers for HT40 on S3).
- `local_timestamp`: ESP32 monotonic microsecond counter (u32, wraps every ~71.6 min).
- `timestamp_real`: UNIX epoch (float seconds with microseconds), stamped by the ESP32 via NTP — `0.000000` until its first sync, at which point the backend falls back to receive-time estimation.
- The receiver applies AGC/FFT gain compensation (baseline sampled over first 100 packets).

## InfluxDB Troubleshooting

If the backend logs `(401) Unauthorized` errors and history queries return 503, the InfluxDB token is likely missing or invalid.

**Check:** `INFLUXDB_TOKEN=` is empty in `.env`, or InfluxDB isn't running.

**Fix — Option 1 (quick, loses stored data):**
```bash
pkill -f influxd
rm -rf influxdb_data/*
bash scripts/start_influxdb.sh
bash scripts/setup_influxdb.sh   # generates new token → .env
```

**Fix — Option 2 (keep existing data, needs influx CLI):**
```bash
# Download influx CLI v2.7.5
curl -fsSL "https://dl.influxdata.com/influxdb/releases/influxdb2-client-2.7.5-linux-amd64.tar.gz" | tar xz -C /tmp
# Then use /tmp/influx to create a new operator token against the running instance
```

**Note:** The WebSocket streaming works independently of InfluxDB — the 401 errors only affect data persistence and historical queries. Real-time visualization at 100 Hz continues uninterrupted.

## React Frontend

The frontend lives in `frontend/` — a Vite + React 18 + TypeScript SPA that visualizes the live CSI amplitude stream as a **pulse-monitor style strip chart** (scrolling time-series, one polyline per subcarrier), with a bottom **air-quality panel** showing the PMS5003 PM1.0/PM2.5/PM10 strip chart plus a stat rail (PM2.5/PM10 headline tiles, particle counts, RSSI). Includes a dashboard layout with sidebar metrics, rotating smoking trivia (sourced from Wikipedia), light/dark theme toggle, and a placeholder for the future detection model.

### Quick Start

```bash
# Terminal 1: Backend (replay mode — no hardware needed)
REPLAY_CSV=./sample_csv_output/sample.csv uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# Terminal 2: Frontend dev server
cd frontend && npm run dev
# → http://localhost:5173  (Vite proxies /ws and /api to :8000)
```

### Build for Production

```bash
cd frontend && npm run build   # → dist/
```

### Architecture (14 source files)

- **100 Hz data path**: CSI frames arrive via WebSocket → parsed → `csiStore.pushFrame()` computes amplitude (`sqrt(I²+Q²)`) and pushes into a module-level ring buffer (max 600 frames, ~6 s at 100 Hz). No React re-renders per frame.
- **Canvas at 60 fps**: `StripChart` (exported from `WaterfallChart.tsx`) runs its own `requestAnimationFrame` loop, pulling the latest frame from the store, maintaining a 600-frame ring buffer, and calling the pure render function `drawStripChart()`.
- **React at ~5 Hz**: Sidebar metrics tiles (RSSI, noise floor, packet count, pkts/s) and WS connection badge use React state, updated via a throttled `setInterval`.
- **Strip chart** (`src/renderers/drawStripChart.ts`): X-axis = elapsed time (newest at right), Y-axis = amplitude (0 at bottom, stable nice-number ticks with hysteresis). Each subcarrier is a coloured polyline — spectral gradient (blue → red), data subcarriers vibrant, guard bands muted, DC null as a gray dashed line. Theme-aware (light/dark palettes). Non-data subcarriers are visually distinguished.
- **Air-quality panel** (bottom of `main`): 1 Hz smoke samples arrive via WebSocket → `smokeStore.pushSmokeSample()` (600-sample ring, ~10 min; per-series peak-hold auto-scale; QoS-0 dedupe via monotonic `t` guard; staleness from `performance.now()` — `STALE_AFTER_MS` = 15 s). `SmokePanel` runs its own rAF loop calling `drawSmokeChart()`; stat tiles update via the store's rAF-coalesced subscription. Series palette (validated CVD-safe hexes, see `drawSmokeChart.ts` header comment) + HTML legend in the toolbar; stat rail doubles as the table view. Sensor offline → tiles dim + `SENSOR OFFLINE` badge.
- **Dashboard layout** (`src/App.tsx`): Header (branding, WS badge, theme toggle), left sidebar (live metrics card, trivia card, model placeholder), main chart area (CSI chart on top, air-quality panel below), footer. Theme persisted to `localStorage`, respects `prefers-color-scheme` on first visit.
- **Trivia** (`src/hooks/useSmokingFacts.ts`): Fetches intro extracts from 15 Wikipedia smoking-related articles on first load, parses into sentences, combines with 30 hand-picked fallback facts, and caches in `localStorage` for 24 hours. Rotates every 8 seconds in the sidebar.
- **WebSocket**: `useWebSocket` hook — exponential backoff reconnect (1s→15s cap), 25s heartbeat pings, dispatches `csi` messages to `csiStore` and `smoke` messages to `smokeStore`.
- **No charting libraries, no state management libraries** — pure Canvas 2D API rendering.

### Key Files

| File | Purpose |
|------|---------|
| `src/store/csiStore.ts` | Module-level singleton — 600-frame ring buffer, amplitude precomputation, EMA auto-scale, frame/metrics subscribers |
| `src/store/smokeStore.ts` | Module-level singleton for the 1 Hz smoke path — 600-sample ring, peak-hold auto-scale, dedupe, staleness, rAF-coalesced subscribers |
| `src/hooks/useWebSocket.ts` | WS lifecycle, reconnect with backoff, heartbeat, message→store dispatch |
| `src/hooks/useSmokingFacts.ts` | Fetches smoking facts from Wikipedia API, caches in localStorage for 24h, falls back to 30 hardcoded facts |
| `src/renderers/drawStripChart.ts` | Strip-chart renderer — per-subcarrier polylines over time, theme-aware light/dark palettes, stable Y-axis ticks with hysteresis, non-data subcarrier suppression |
| `src/renderers/drawSmokeChart.ts` | Air-quality renderer — 3 PM series (one µg/m³ Y axis), validated CVD-safe series colors, direct end-labels, offline badge; exports `SERIES_COLORS` for the HTML legend |
| `src/renderers/drawWaterfall.ts` | (Orphaned) Original 3-D perspective waterfall renderer — kept for reference, not imported by any component |
| `src/components/WaterfallChart.tsx` | Canvas component (exports `StripChart`) — rAF loop, 600-frame buffer, pause toggle, DPR-aware canvas sizing, accepts `theme` prop |
| `src/components/SmokePanel.tsx` | Bottom air-quality panel — rAF canvas (PM1.0/PM2.5/PM10), stat rail (PM2.5/PM10 hero tiles, particle counts, RSSI), staleness dimming, pause toggle |
| `src/App.tsx` | Dashboard shell — header (branding, WS badge, theme toggle), sidebar (metrics, trivia, model placeholder), main chart + air-quality panel, footer |
| `src/types.ts` | `CsiFrame`, `SmokeSample`, `WsMessage`, `SubcarrierMetadata`, `MetricsSnapshot` |

### Theme System

CSS custom properties on `:root` (light) and `[data-theme="dark"]` (dark). The `App` component manages theme state and applies the attribute to `<html>`. The chart renderer receives a `Theme` prop (`'light' | 'dark'`) and selects an appropriate palette. Light mode uses white/green; dark mode uses black/green.

All visual elements (cards, borders, text, chart background, grid lines) respond to the theme. Theme choice is persisted to `localStorage` key `smokeguard-theme`.

### Backend ↔ Frontend Field Contract

The backend `WSCsiData` model uses Pydantic field aliases (`Field(alias=...)`). The broadcast in `main.py` **must** use `by_alias=False` (the default) so the JSON keys match the frontend `CsiFrame` interface:

| Python field | Alias | Frontend expects | Correct JSON key |
|---|---|---|---|
| `t` | `timestamp_real` | `t` | `"t"` |
| `seq` | `id` | `seq` | `"seq"` |
| `length` | `len` | `length` | `"length"` |

Using `by_alias=True` will cause the frontend chart to freeze at one frame (all `seq` values become `undefined`, breaking the dedupe check).

### Dependencies

- **react**, **react-dom** — UI framework
- **No other runtime deps** — real-time rendering uses native Canvas 2D API; no charting libraries, no state management libraries.

### Dev Proxy

Vite dev server proxies `/ws` (WebSocket) and `/api` (REST) to `localhost:8000`. Configured in `vite.config.ts`. Proxy socket errors (`EPIPE`, `ECONNRESET`) from backend restarts are silently suppressed (checks both `.code` and `.message` properties).

| Parameter | Value | Where |
|-----------|-------|-------|
| Wi-Fi channel | 11 | `CONFIG_LESS_INTERFERENCE_CHANNEL` in firmware |
| Bandwidth | HT40 (40 MHz) | `CONFIG_WIFI_BANDWIDTH` |
| ESP-NOW rate | MCS0, long GI | `CONFIG_ESP_NOW_RATE` |
| Tx frequency | 100 Hz | `CONFIG_SEND_FREQUENCY` in csi_send |
| MQTT broker | 127.0.0.1:1883 | `mqtt_host`/`mqtt_port` in Settings |
| MQTT topics | `home/csi/data` (QoS 0), `home/csi/status` (QoS 1), `home/smoke_sensor/data` (QoS 0) | `mqtt_topic_data`/`mqtt_topic_status`/`mqtt_topic_smoke` in Settings |
| Gain control | enabled (ESP32-S3) | `CONFIG_GAIN_CONTROL` |
| InfluxDB retention | 30 days | `influxdb_client.py` → `every_seconds=2592000` |
| CSI cleanup retention | 7 days | `csi_retention_days` in Settings |
| Cleanup interval | 30 min | `cleanup_interval_hours` in Settings |
| InfluxDB write batch | 500 points / 1000ms | `WriteOptions` in `influxdb_client.py` |
| Async queue size | 2000 | `main.py` consumer |
| WS per-client queue | 256 | `ws_queue_maxsize` in Settings |
| Replay throttle | 5 ms (~200 Hz) | `REPLAY_THROTTLE` in `mqtt_reader.py` |
