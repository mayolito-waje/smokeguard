# SmokeGuard — Wi-Fi CSI Smoking-Activity Detection

SmokeGuard recognizes smoking activity from Wi-Fi Channel State Information (CSI)
and air-quality readings. Two ESP32-S3 boards exchange ESP-NOW packets; the receiver
publishes CSI over MQTT to the laptop, where the backend fuses a CSI gesture model
with an air-quality SVM and streams a live dashboard, detection history, and monthly
PDF reports.

```
[ESP32-S3 Tx] --ESP-NOW 100 Hz--> [ESP32-S3 Rx] --Wi-Fi/MQTT--> [Mosquitto :1883]
                                                                      |
                                        home/csi/data, home/csi/status |
                                                                      v
[PMS5003 + BME680 publisher] --MQTT--> home/smoke_sensor/data --> [SmokeGuard backend]
                                                                      |
                                                            InfluxDB + React dashboard
                                                                http://localhost:8080
```

## Repository layout

| Path | Purpose |
|---|---|
| `csi_send_mqtt/` | Transmitter firmware: ESP32-S3, ESP-NOW, 100 Hz (ESP-IDF) |
| `csi_recv_mqtt/` | Receiver firmware: captures CSI, publishes CSV over MQTT (ESP-IDF) |
| `esp_mac_identifier/` | Small utility firmware that prints the board's Wi-Fi/BT MACs |
| `smokeguard_application/` | FastAPI backend, React dashboard, Docker Compose stack |

`csi_send_mqtt` and `csi_recv_mqtt` are the active firmware projects. The older
pristine `csi_send`/`csi_recv` reference projects and the early Python prototype
were removed.

## Hardware requirements

| Item | Notes |
|---|---|
| 2× ESP32-S3 development boards | One transmitter, one receiver |
| 2× USB data cables | Flashing/monitoring; some cables are power-only |
| 2.4 GHz Wi-Fi hotspot | Phone hotspot or router. **5 GHz-only hotspots do not work** |
| Laptop | Runs the Docker stack (x86_64) and the MQTT broker |
| PMS5003 + BME680 sensor + ESP32 publisher | Air-quality stream for detection — **publisher firmware is not in this checkout** (see below) |

### Smoke sensor data

The app subscribes to `home/smoke_sensor/data` and expects one CSV line per sample
(about 1 Hz, no header, QoS 0):

```
timestamp,pm1_0,pm2_5,pm10,cnt0_3,cnt0_5,cnt1_0,cnt2_5,cnt5_0,cnt10,temp_c,pressure_hpa,humidity_pct,gas_kohm,altitude_m,rssi
```

Older 11-field publishers (no BME680 block) are still accepted:
`timestamp,pm1_0,pm2_5,pm10,cnt0_3,cnt0_5,cnt1_0,cnt2_5,cnt5_0,cnt10,rssi`.
`timestamp` is UNIX epoch seconds; `0`/negative (before NTP sync) falls back to the
server receive time.

**The firmware that reads the PMS5003/BME680 and publishes this topic is not included
in this checkout.** You need that separate publisher for air-quality scores, fusion,
and therefore detection. Without it the dashboard still shows live CSI, but the
detection loop does not evaluate (it needs a smoke-sample window and a 600 s
baseline). See [Troubleshooting](#troubleshooting).

## Software prerequisites

- **ESP-IDF 5.5.3** for both firmware projects.
- **Docker Engine + Compose v2** for the application.
- `git`; Python/`uv` and Node only if you run the app outside Docker.
- Optional: `mosquitto-clients` on the laptop for MQTT debugging.
- Optional for `.local` broker names: Avahi (Linux) or Bonjour (macOS/Windows).

## Network topology

- Both ESP32 boards join the **same 2.4 GHz hotspot**. The AP determines the channel
  and HT20/HT40 width; the firmware follows it (`CSI_CHANNEL=0` disables the channel
  check).
- The sender talks to the receiver over ESP-NOW; the receiver reaches the laptop over
  Wi-Fi.
- The laptop must be reachable from the hotspot on the MQTT port (default 1883).
  Open the firewall, e.g. on firewalld:
  `sudo firewall-cmd --add-port=1883/tcp --permanent && sudo firewall-cmd --reload`.
- Phone hotspots often block client-to-client traffic or LAN hostnames. Turn off
  client/AP isolation and prefer the laptop's numeric LAN IP over `.local`.
- The receiver needs NTP access (internet or a LAN NTP server) to stamp
  `timestamp_real`.

## Step 1 — Identify the transmitter MAC

Flash the MAC utility to the board you will use as the **transmitter** and note its
Wi-Fi Station MAC:

```bash
cd esp_mac_identifier
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

The serial monitor runs at 115200 baud. Use the **Wi-Fi Station MAC** as
`CSI_SENDER_MAC`. In documentation and commits use a placeholder such as
`14:C1:9F:28:XX:XX`; keep real MACs only in the gitignored `.env` files.

## Step 2 — Configure the firmware

`csi_recv_mqtt` requires a `.env`. `csi_send_mqtt` **reads the same file**
(`../csi_recv_mqtt/.env`), so keep the two project directories as siblings.

```bash
cd csi_recv_mqtt
cp .env.example .env
$EDITOR .env
```

Set at least:

| Key | Value |
|---|---|
| `WIFI_SSID` / `WIFI_PASSWORD` | The 2.4 GHz hotspot |
| `MQTT_BROKER_HOST` | Laptop LAN IP (recommended), or `<laptop>.local` |
| `MQTT_BROKER_PORT` | `1883` (or the Compose `MQTT_PORT`) |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | Must match the Docker stack (`esp32`/`mosquitto` by default) |
| `MQTT_TOPIC_DATA` | `home/csi/data` |
| `MQTT_TOPIC_STATUS` | `home/csi/status` |
| `NTP_SERVER` | `pool.ntp.org` (or a LAN NTP server) |
| `CSI_SENDER_MAC` | The transmitter's Wi-Fi Station MAC from Step 1 |
| `CSI_CHANNEL` | `0` (follow the AP) |

**Topic strings must match exactly.** The app defaults to topics without a leading
slash (`home/csi/data`); the sample `.env.example` shows `/home/csi/data`. MQTT topic
matching is literal, so use the no-leading-slash form in both places.

## Step 3 — Build and flash the firmware

```bash
source <path-to-esp-idf>/export.sh
idf.py --version                          # expect 5.5.3

cd csi_recv_mqtt
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

Then repeat for the transmitter (disconnect the receiver first):

```bash
cd ../csi_send_mqtt
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

The sender prints its STA MAC at boot — confirm it equals `CSI_SENDER_MAC`. After
changing the MAC or any `.env` value, rebuild **both** projects. If a `build/`
directory was copied or the ESP-IDF installation changed, delete `build/` and
re-run `idf.py set-target esp32s3`. Use the correct serial device
(`/dev/ttyUSB0`, `/dev/ttyACM0`, or `COMx`).

## Step 4 — Start the application

```bash
cd smokeguard_application
docker compose up --build -d
```

The first build downloads TensorFlow and takes several minutes. Open
**http://localhost:8080** and log in with **admin / admin** on a fresh install.

If Mosquitto is already running on the laptop, stop it first so the bundled broker
can own port 1883:

```bash
sudo systemctl stop mosquitto
```

Stop any local uvicorn/InfluxDB on 8000/8086 as well, or change the Compose port
variables. The firmware must use the published MQTT port.

## Step 5 — Verify end to end

1. `docker compose logs -f backend` — look for `Connected to MQTT broker` and the
   CSI/smoke subscriptions.
2. On the laptop, with `mosquitto-clients`:
   ```bash
   mosquitto_sub -h localhost -p 1883 -u esp32 -P mosquitto -t 'home/csi/status' -v
   mosquitto_sub -h localhost -p 1883 -u esp32 -P mosquitto -t 'home/csi/data' -v
   ```
   The status topic is retained JSON; the data topic should show CSV lines.
3. The dashboard's MQTT badge should read connected, `packets/s` should move, and the
   receiver should show online.
4. Open **Detection** in the sidebar, enable it, and click **Capture static
   background** while the room is quiet. Models are loaded in the background at
   startup.
5. With the smoke-sensor stream running, detections appear in History and trigger
   toasts; the monthly PDF report can be downloaded there.

## MQTT topic contract

| Topic | Payload | QoS | Retained |
|---|---|---|---|
| `home/csi/data` | One CSI CSV line | 0 | No |
| `home/csi/status` | Receiver JSON (`state`, counters, `ntp_synced`, ...) | 1 | Yes (LWT publishes `{"state":"offline"}`) |
| `home/smoke_sensor/data` | One air-quality CSV line | 0 | No |

The backend topic names are configurable in
`smokeguard_application/docker-compose.yml`; the firmware names are in
`csi_recv_mqtt/.env`. They must match exactly, including any leading slash.

## Troubleshooting

**Receiver heartbeats but the dashboard shows zero CSI** — `CSI_SENDER_MAC` does not
match the transmitter's Wi-Fi Station MAC. The sender prints its MAC at boot; fix
`.env` and rebuild both projects. This failure is silent by design (other
transmitters are filtered out).

**Backend MQTT disconnected** — check `MQTT_BROKER_HOST`, port, and credentials; make
sure the laptop firewall allows the port and the hotspot has client isolation off.
`.local` names need Avahi/Bonjour; a numeric IP is more reliable on phone hotspots.

**Topics never arrive** — a leading slash mismatch (`/home/csi/data` vs
`home/csi/data`). MQTT does not normalize this.

**`timestamp_real` is 0 or `ntp_synced` is false** — the receiver cannot reach NTP.
Give it internet access or set `NTP_SERVER` to a reachable LAN server. Until sync,
the backend falls back to receive-time estimation.

**No detections** — confirm the detection toggle is on, the dashboard reports
`models_ready`, a static background was captured, and `home/smoke_sensor/data` is
streaming. Detection needs both a full smoke window and the sensor baseline; CSI
alone cannot fire.

**Firmware will not build** — `csi_send_mqtt` fails without the sibling
`csi_recv_mqtt/.env`; copy it from `.env.example`. Also verify ESP-IDF 5.5.3 and
remove a stale `build/` tree.

**Docker issues** — see the
[application README](smokeguard_application/README.md#troubleshooting): port
conflicts, x86_64-only TensorFlow, InfluxDB token/volume resets, and broker
credential changes.

## Security and repository hygiene

The Docker defaults (JWT/reset secrets, InfluxDB token/admin password, MQTT
credentials) are for development. Change them before a real deployment. MQTT is
plaintext and has no TLS here; keep the broker on a trusted LAN. Auth is a proof of
concept and tokens are not revoked on logout.

Do not commit `.env` files, SQLite databases, or MAC dumps. `mac_addresses.txt` is
gitignored; use placeholders like `14:C1:9F:28:XX:XX` in documentation.

## Documentation

- [Application README](smokeguard_application/README.md) — Docker stack, configuration, troubleshooting.
- [Receiver firmware](csi_recv_mqtt/README.md) — `.env`, MQTT, channel details.
- [Transmitter firmware](csi_send_mqtt/README.md) — shared `.env`, MAC matching.
- [CLAUDE.md](smokeguard_application/CLAUDE.md) — internal backend/frontend architecture.
