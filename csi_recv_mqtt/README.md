# csi_recv_mqtt

CSI receiver that publishes each CSI packet to an MQTT broker instead of
dumping it to the serial port.

This is the `csi_recv` example from [esp-csi](https://github.com/espressif/esp-csi)
with the transport swapped: the receiver joins your mobile hotspot as a normal
Wi-Fi station and publishes the exact same `CSI_DATA,...` CSV line it used to
print over UART. Everything downstream of the serial port (`csi_parser`,
`smokeguard_application`) can consume the MQTT payload unchanged.

The broker is addressed **by hostname** so that a hotspot handing out a new IP
on every reconnect does not leave a stale address behind. A plain **IPv4
address** works too and is used verbatim — handy while setting up, at the cost
of needing an edit whenever the hotspot reassigns. See
[How the broker hostname is resolved](#how-the-broker-hostname-is-resolved).

---

## Quick start

```bash
# 1. Create your local config and fill in the blank values
cp .env.example .env
$EDITOR .env

# 2. Build and flash
idf.py set-target esp32s3        # regenerates sdkconfig from sdkconfig.defaults
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor

# 3. Watch the data arrive (on the broker machine)
pip install -r tools/requirements.txt
python3 tools/mqtt_subscribe.py
```

The transmitter is either `csi_send` (upstream, with its channel pinned at build
time) or [`csi_send_mqtt`](../csi_send_mqtt), which joins the same hotspot so the
two ends cannot drift apart — see [the channel
section](#️-the-one-thing-that-will-bite-you-the-channel). After this the
receiver only needs USB for flashing and log output.

> **`sdkconfig.defaults` only applies when `sdkconfig` is generated.** If a
> `sdkconfig` already exists it is left alone, so it will *not* pick up changes
> made here. `sdkconfig` is gitignored, so after pulling a change to
> `sdkconfig.defaults`, delete `sdkconfig` and re-run `idf.py set-target
> esp32s3`. This matters: `CONFIG_ESP_WIFI_CSI_ENABLED` is one of those
> settings, and Wi-Fi CSI is compiled in or not at build time.

---

## ⚠️ The one thing that will bite you: the channel

A Wi-Fi station **cannot choose its own channel or width** — the access point
dictates both. This receiver follows the hotspot, so CSI is only captured when
the sender agrees with it. Which sender you flash decides how much of that you
have to manage by hand:

| Sender | Channel and width | When the hotspot moves |
| --- | --- | --- |
| [`csi_send`](../csi_send) | Pinned at build time — `CONFIG_LESS_INTERFERENCE_CHANNEL` (11 upstream) at HT40 | Rebuild and reflash it, or this receiver goes deaf |
| [`csi_send_mqtt`](../csi_send_mqtt) | Follows the AP, exactly as this receiver does | Nothing to do |

**`csi_send_mqtt` is the one to pair with this receiver** — it joins the same
hotspot, so channel and width are settled by the AP instead of by hand.

`csi_send` is the upstream example: it never associates, so its radio can only
sit where it was told at build time. Use it and you want the hotspot on channel
11, wide enough for the HT40 rate it asks for. When the two disagree the
receiver is simply deaf and the only symptom is silence, so the firmware logs
the channel it actually landed on and shouts if it does not match `CSI_CHANNEL`:

```
W (12345) csi_recv_mqtt: Channel mismatch: hotspot put us on 6, but the CSI
W (12345) csi_recv_mqtt: sender transmits on 11. No CSI will be captured.
```

Two ways to fix that, whichever is easier for your phone:

1. Set the hotspot to the sender's channel (many Android hotspots allow this
   under *Hotspot → Advanced → AP band/channel*). **Preferred** — no reflash.
2. Leave the hotspot alone, change `CONFIG_LESS_INTERFERENCE_CHANNEL` in
   `csi_send/main/app_main.c` to match the hotspot's channel, set the same
   value as `CSI_CHANNEL` in `.env` so the warning stays accurate, and reflash
   the sender.

One caveat on the warning itself: it compares the AP's channel against
`CSI_CHANNEL`, a constant. With `csi_send_mqtt` both ends follow the AP, so the
check cannot detect a real problem — if the hotspot moves to channel 6 it would
still announce that no CSI is being captured, which is not true. Set
`CSI_CHANNEL=0` in that setup to switch the check off; it is the default, and
the check is only meaningful with the fixed-channel `csi_send`.

Width fixes the payload size: 64 subcarrier values per packet at HT20, 128 at
HT40. The parsers size themselves from the `len` field either way.

---

## The `.env` file

`.env` is gitignored; only `.env.example` is committed. `tools/gen_config.py`
converts it into `main/mqtt_config.h` at CMake configure time, so `idf.py build`
always compiles against whatever is in the file — just rebuild after editing.

| Key | Default | Meaning |
| --- | --- | --- |
| `WIFI_SSID` | *required* | Hotspot name (2.4 GHz) |
| `WIFI_PASSWORD` | *required* | Hotspot password; may be empty for an open network |
| `MQTT_BROKER_HOST` | *required* | Broker hostname (`mayo-laptop.local`) **or** a plain IPv4 address, which is used verbatim with no lookup |
| `MQTT_BROKER_PORT` | `1883` | Broker TCP port |
| `MQTT_USERNAME` | empty | Broker username; empty connects anonymously |
| `MQTT_PASSWORD` | empty | Broker password |
| `MQTT_TOPIC_DATA` | `/home/csi/data` | CSI packets |
| `MQTT_TOPIC_STATUS` | `/home/csi/status` | Online/offline/heartbeat |
| `MQTT_CLIENT_ID` | derived | Empty → `csi-recv-<mac>`. Must be unique on the broker |
| `MQTT_KEEPALIVE_S` | `60` | Keepalive period |
| `NTP_SERVER` | `pool.ntp.org` | Server used to set the wall clock behind `timestamp_real` |
| `MDNS_TIMEOUT_MS` | `3000` | Timeout for `.local` lookups |
| `MDNS_HOSTNAME` | `csi-recv` | Name this ESP32 answers to over mDNS |
| `CSI_SENDER_MAC` | `14:C1:9F:28:C1:A0` | Sender MAC; frames from others are ignored |
| `CSI_CHANNEL` | `0` | Expected channel of a **fixed-channel** sender. `0` disables the check, which is correct for `csi_send_mqtt` |
| `CSI_QUEUE_DEPTH` | `32` | Packets buffered while the broker is slow |
| `STATUS_INTERVAL_S` | `30` | Heartbeat period; `0` disables |
| `LOG_LEVEL` | `INFO` | `NONE`/`ERROR`/`WARN`/`INFO`/`DEBUG`/`VERBOSE` |

A missing or malformed value fails the **build** with a message naming the key,
rather than producing a device that silently fails to connect. `#` only starts
a comment at the beginning of a line, so passwords containing `#` are safe.

---

## Broker setup

Any MQTT broker works. For a quick local Mosquitto with authentication:

```bash
sudo mosquitto_passwd -c /etc/mosquitto/passwd csi   # prompts for a password
```

```conf
# /etc/mosquitto/conf.d/csi.conf
listener 1883 0.0.0.0
allow_anonymous false
password_file /etc/mosquitto/passwd
```

```bash
sudo systemctl restart mosquitto
sudo ufw allow 1883/tcp        # if the broker machine runs a firewall
```

Put those same credentials in `MQTT_USERNAME` / `MQTT_PASSWORD`.

**Then confirm it is actually listening before blaming the firmware.** A broker
that failed to start looks exactly like a DNS or hostname problem from the
ESP32's side — the firmware cannot distinguish "no such name" from "nothing
there", and neither can a subscriber.

```bash
systemctl status mosquitto                  # active, not failed
ss -lntp | grep 1883                        # something bound to the port
mosquitto_sub -h localhost -t '/home/csi/#' -v   # end-to-end, from the broker
```

A `mosquitto.service` that exits within milliseconds is a config problem, and
`sudo journalctl -u mosquitto -n 30` says which. `sudo mosquitto -c
/etc/mosquitto/mosquitto.conf -v` in the foreground prints the same thing
without the journal.

---

## How the broker hostname is resolved

The firmware resolves `MQTT_BROKER_HOST` itself and hands esp-mqtt a literal
address:

1. An IPv4 literal is used as-is (handy for testing on a fixed network).
2. A name ending in **`.local`** is resolved over **mDNS**.
3. Anything else goes through the normal DNS resolver.

The name is **re-resolved on every disconnect**, so when the hotspot hands the
broker a new address the ESP32 picks it up on the next reconnect and logs it:

```
I (15234) csi_recv_mqtt: broker "mayo-laptop.local" moved: 192.168.43.7 -> 192.168.43.19
```

Why mDNS: a phone hotspot does not run a DNS server that knows the names of the
machines behind it, so `my-laptop` will not resolve — but `my-laptop.local`
will, over multicast, with no router support. This is why `.local` is strongly
recommended for this setup.

On the broker machine:

```bash
hostname                       # e.g. "mayo-laptop" -> use "mayo-laptop.local"
avahi-resolve -n mayo-laptop.local   # verify it answers with an IP
```

Linux needs Avahi (usually preinstalled), macOS and Windows need Bonjour.
If mDNS is not an option, fall back to a static IP or DHCP reservation and put
that address in `MQTT_BROKER_HOST` — the code path is identical.

---

## Topics and payloads

**`/home/csi/data`** — one message per CSI packet, QoS 0, not retained. The
payload is the CSV line the serial example printed, plus a trailing
`timestamp_real` column:

```
CSI_DATA,1234,14:C1:9F:28:C1:A0,-42,11,0,0,1,0,0,0,0,0,-95,0,11,0,148234,1,256,0,128,0,"[-3,5,-12,8,...]",1785103746.011223
```

`timestamp_real` is the wall clock at reception — UNIX epoch seconds with
microseconds — set by NTP once the Wi-Fi comes up (`NTP_SERVER` in `.env`).
Until the first successful sync it reads `0.000000`; the status topic's
`ntp_synced` flag tells the two apart.

QoS 0 is deliberate: packets arrive ~100×/s and are individually disposable, so
retransmitting stale ones would only add latency.

**`/home/csi/status`** — QoS 1, retained. Three kinds of message:

```jsonc
// on connect (retained, so a late subscriber immediately sees the device)
{"state":"online","client":"csi-recv-a1b2c3","ip":"192.168.43.42",
 "broker":"mqtt://192.168.43.7:1883","channel":11,"rssi":-42,
 "ntp_synced":true,"uptime_s":12,
 "csv_header":"type,id,mac,rssi,rate,...,first_word,data,timestamp_real"}

// periodically (retained) -- counters to diagnose a slow or unreachable broker
{"state":"online","client":"csi-recv-a1b2c3","ntp_synced":true,
 "uptime_s":312,"seen":30512,"published":30498,"publish_failed":0,
 "dropped":0,"queue_overrun":0,"queue_peak":3,"free_heap":141728,"rssi":-42}

// broker-published last will, if the ESP32 dies or loses power (retained)
{"state":"offline","client":"csi-recv-a1b2c3"}
```

The `csv_header` field lets a subscriber map CSV columns to names without
hardcoding chip-specific column order — `tools/mqtt_subscribe.py` uses it.

---

## Testing

```bash
python3 tools/mqtt_subscribe.py            # per-second rate + latest packet
python3 tools/mqtt_subscribe.py --raw      # dump every CSV line
python3 tools/mqtt_subscribe.py --limit 500
```

Or with the Mosquitto CLI clients, which also confirms the broker config:

```bash
mosquitto_sub -h mayo-laptop.local -u csi -P 'secret' -t '/home/csi/#' -v
```

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| No data, no Wi-Fi log | `WIFI_SSID`/`WIFI_PASSWORD` wrong, or the hotspot is 5 GHz only |
| Wi-Fi connects, zero CSI, warning about channel | Hotspot and sender are on different channels — see the section above |
| Wi-Fi connects, zero CSI, **no** warning, sender is definitely transmitting | `CSI_SENDER_MAC` is not the MAC the sender transmits from. The receiver discards every frame from anyone else, silently. The sender prints its own MAC at boot as `mac: XX:XX:XX:XX:XX:XX` — put that in `.env` and rebuild |
| `could not resolve "..."` | Broker hostname wrong, or Avahi/Bonjour not running on the broker |
| `broker refused connection` | Username/password mismatch — check `MQTT_USERNAME`/`MQTT_PASSWORD` |
| `delayed connect error: Connection reset by peer`, `sock errno 104`, `Failed to open a new connection: 32772` — often *after* a session that worked | The broker is not running. Nothing is bound to the port, so the host's kernel answers the connect with a TCP RST. Check it on the broker machine: `ss -lntp \| grep 1883`. The `esp-tls` lines are expected even for plain `mqtt://` — esp-mqtt routes every transport through esp-tls and plain TCP is one of its modes, so their presence does not mean TLS was attempted |
| Connects, then drops every few seconds | Another client is using the same `MQTT_CLIENT_ID` |
| `queue_overrun` climbing in heartbeats | Broker slower than the CSI rate; raise `CSI_QUEUE_DEPTH` or lower `CONFIG_SEND_FREQUENCY` on the sender |
| Reboot loop, `invalid chanel info, need change second channel to 40` | The ESP-NOW rate config asked for a wider channel than the hotspot gave us. Fixed — the phymode is now derived from the negotiated width. If it returns, the firmware warns instead of aborting |
| Associates fine, then reboots in `wifi_csi_init` with `ESP_FAIL` from `esp_wifi_set_csi_config` | `CONFIG_ESP_WIFI_CSI_ENABLED` is off. Wi-Fi CSI is a compile-time feature and its Kconfig default is `n`; see the note in Quick start about `sdkconfig.defaults` not applying to an existing `sdkconfig` |
| Nothing on the status topic | You are subscribed but broker persistence is off and the retained message was never published — check the monitor log |
| `CMake Error: The source ... does not match the source ... used to generate cache` | You switched between two ESP-IDF installations. The build directory caches the IDF path, and it cannot be mixed. `rm -rf build` and rebuild |

Set `LOG_LEVEL=DEBUG` in `.env` for gain-compensation details. That log is a
per-packet line at ~100 Hz, so it is deliberately not at `INFO`.

---

## Differences from the serial `csi_recv`

Beyond the transport swap, three deliberate changes:

- **The forced MAC was removed.** `csi_recv` called
  `esp_wifi_set_mac(WIFI_IF_STA, CONFIG_CSI_SEND_MAC)`, setting the receiver's
  own MAC equal to the transmitter's. That is a duplicate MAC on the hotspot
  (and would collide with the sender if it ever joined the same network), and
  it makes the MQTT client id ambiguous. The filter on incoming frames is
  unaffected — it still keys on `CSI_SENDER_MAC`.
- **The per-packet channel override was removed.** A station follows its AP;
  forcing a channel while associated only breaks the association. The channel
  is now read back and validated instead.
- **The forced HT40 phymode was removed.** `csi_recv` pins the ESP-NOW rate
  config to `WIFI_PHY_MODE_HT40`. That only works there because it also forces
  the radio wide with its channel override; asking for a 40 MHz rate on a
  20 MHz interface is refused by the driver, and with `ESP_ERROR_CHECK` that
  refusal is a reboot loop. The width now follows the AP, so the firmware asks
  for what was actually negotiated — and if the driver still refuses, it warns
  and carries on, because the rate config is only an optimisation.
- **The gain-compensation log moved from `INFO` to `DEBUG`.** One line per
  packet at 100 packets/s would swamp the UART that now shares bandwidth with
  the Wi-Fi stack.

## Files

```
.env.example            committed template -- copy to .env
.env                    your credentials (gitignored)
main/main.c             firmware
main/mqtt_config.h      generated from .env at build time (gitignored)
main/CMakeLists.txt     runs the generator, declares component deps
sdkconfig.defaults      build-time config -- CSI_ENABLED and RX buffers
tools/gen_config.py     .env -> mqtt_config.h, with validation
tools/mqtt_subscribe.py test subscriber
```
