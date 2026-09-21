# csi_send_mqtt

CSI sender that **joins the hotspot** instead of pinning a channel and width.

`csi_send` has to hardcode both, because it never associates with anything: its
radio can only sit on the channel it was told at build time. That makes the pair
fragile — when the hotspot picks a different channel, the sender has to be
rebuilt and reflashed, and until it is the receiver is simply deaf and the only
symptom is silence.

This variant associates to the same hotspot the receiver is on. An access point
gives every station on it the same channel and width, so both ends agree by
construction: nothing to keep in sync, nothing to reflash when the hotspot
moves. The ESP-NOW peer is added with channel 0 ("whatever channel the interface
is on") and the rate's phymode is derived from what the AP actually negotiated.

It is **not** an MQTT client. The name only reflects that it belongs to the same
Wi-Fi network — and the same `.env` — as `csi_recv_mqtt`.

---

## The `.env` is shared with `csi_recv_mqtt`

This project has no `.env` of its own. `main/CMakeLists.txt` runs
`csi_recv_mqtt/tools/gen_config.py` against `csi_recv_mqtt/.env` and generates
`main/wifi_config.h`, so:

- the hotspot SSID and password live in **one** place;
- `CSI_SENDER_MAC` and the MAC this sender actually transmits from are the same
  single edit.

Every key in `.env` ends up in the generated header, but nothing here references
the broker ones, so they do not reach the binary — the MQTT password is not
compiled into this sender.

---

## Build and flash

```bash
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

---

## The MAC has to match, and it is checked silently

The receiver discards every CSI frame whose source MAC is not `CSI_SENDER_MAC`
(`main.c`, `csi_rx_cb`) — with no log line, so a mismatch and a dead sender look
identical. This sender prints its own MAC at boot:

```
I (1234) csi_send_mqtt: send_frequency: 100, mac: 14:C1:9F:28:C1:A0
```

If that differs from `CSI_SENDER_MAC` in `csi_recv_mqtt/.env`, edit `.env` and
rebuild **both** projects — this one because its header is generated, the
receiver because the filter is compiled in.

---

## What this changes for the receiver's channel check

The receiver's `CSI_CHANNEL` warning compares the AP's channel against a
constant, and it was written when the sender had a fixed channel. With both ends
following the AP it can no longer detect a real problem: if the hotspot moves to
channel 6, the check still compares against `CSI_CHANNEL` and announces that no
CSI will be captured, which is no longer true.

While this sender is in use, set `CSI_CHANNEL=0` (the default) in
`csi_recv_mqtt/.env`, which switches the check off — the honest setting when
there is no second channel to compare against. A channel number there is only
meaningful with the original fixed-channel `csi_send`.

---

## Differences from `csi_send`

- **It associates**, so it needs the hotspot credentials and reaches the network
  on its own address. The original never joins anything.
- **It does not set the channel or the width.** Those calls were the thing that
  had to be kept in sync by hand; the AP dictates both here.
- **The phymode is derived at runtime** from `esp_wifi_get_channel()` and the
  rate config is non-fatal, so a refused rate warns instead of rebooting.
- **The sender waits for an address before the first ESP-NOW send**, and gives
  up waiting after 30 s rather than refusing to start — the peer follows the
  channel whenever the association completes, so a late hotspot needs no reboot.
- **Send errors are logged once per failure run** instead of once per packet.
  At 100 packets/s a dropped link would otherwise bury the monitor.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `Wi-Fi disconnected (reason 15)` repeating | Wrong `WIFI_SSID`/`WIFI_PASSWORD` in `csi_recv_mqtt/.env`, or the hotspot is 5 GHz only |
| `no address after 30s; starting anyway` | The hotspot is out of range or down. Harmless — it will associate when it returns |
| Receiver shows status heartbeats, zero CSI | The MAC filter. Compare the `mac:` line above against `CSI_SENDER_MAC` |
| Receiver warns about a channel mismatch | Set `CSI_CHANNEL=0` in `csi_recv_mqtt/.env` — the check only applies to a fixed-channel sender |
| `ESP-NOW rate config rejected` | The rate could not be applied; CSI still works, only the explicit MCS choice is lost |
| `CMake Error: The source ... does not match the source ... used to generate cache` | A `build/` directory copied from another project. `rm -rf build` and rebuild |
