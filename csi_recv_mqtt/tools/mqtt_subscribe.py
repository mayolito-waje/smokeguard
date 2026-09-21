#!/usr/bin/env python3
"""Subscribe to the CSI receiver's topics and show what is arriving.

Reads the same .env the firmware is built from, so broker address, credentials
and topics only ever have to be right in one place.

    python3 tools/mqtt_subscribe.py              # one summary line per second
    python3 tools/mqtt_subscribe.py --raw        # dump every CSV line
    python3 tools/mqtt_subscribe.py --limit 500  # stop after 500 packets

Requires paho-mqtt 2.x:  pip install -r tools/requirements.txt
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("paho-mqtt is not installed. Try:  pip install -r tools/requirements.txt")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    # Reuse the firmware's .env parser so the two cannot disagree.
    from gen_config import ConfigError, parse_env
except ImportError:
    sys.exit("could not import gen_config.py; keep it next to this script")


def die(message: str) -> None:
    sys.exit(f"error: {message}")


class Stats:
    """Rolling counters, reported once a second."""

    def __init__(self) -> None:
        self.packets = 0
        self.total = 0
        self.bytes = 0
        self.window_start = time.monotonic()
        self.last: dict[str, object] = {}

    def add(self, meta: dict[str, str], data: list[int], size: int) -> None:
        self.packets += 1
        self.total += 1
        self.bytes += size
        self.last = {"meta": meta, "subcarriers": len(data)}

    def maybe_report(self) -> None:
        now = time.monotonic()
        elapsed = now - self.window_start
        if elapsed < 1.0:
            return
        rate = self.packets / elapsed
        kbps = (self.bytes * 8 / 1000) / elapsed
        meta = self.last.get("meta") or {}
        detail = ""
        if meta:
            detail = (f" | rssi={meta.get('rssi', '?')}dBm"
                      f" ch={meta.get('channel', '?')}"
                      f" subcarriers={self.last.get('subcarriers')}"
                      f" len={meta.get('len', '?')}"
                      f" ts={meta.get('timestamp_real', '?')}")
        print(f"[data]   {rate:6.1f} pkt/s  {kbps:7.1f} kbit/s  "
              f"total={self.total}{detail}")
        self.packets = 0
        self.bytes = 0
        self.window_start = now


def parse_csv_line(line: str) -> tuple[list[str], list[int], str | None] | None:
    """Split a CSI_DATA line into metadata fields, subcarrier array and the
    optional trailing timestamp_real column.

    The array is the only quoted element, so the first ',"' marks where the
    flat metadata ends -- that keeps the commas inside the array from being
    mistaken for field separators. The firmware appends ",<epoch>.<usec>"
    after the array's closing quote, which this returns as the third element.
    """
    marker = line.find(',"')
    if marker < 0:
        return None
    meta = line[:marker].split(",")
    tail = line[marker + 2:].rstrip()
    close = tail.find('"')
    if close < 0:
        return None
    array = tail[:close].strip("[]")
    trailing = tail[close + 1:].lstrip(",").strip() or None
    data = [int(v) for v in array.split(",")] if array else []
    return meta, data, trailing


def describe_status(payload: str) -> str:
    try:
        doc = json.loads(payload)
    except json.JSONDecodeError:
        return payload
    state = doc.pop("state", "?")
    body = "  ".join(f"{k}={v}" for k, v in doc.items())
    return f"{state:<8} {body}"


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=os.path.join(here, ".env"),
                        help="path to the .env file (default: %(default)s)")
    parser.add_argument("--raw", action="store_true",
                        help="print every CSI line instead of a per-second summary")
    parser.add_argument("--no-status", action="store_true",
                        help="ignore the status topic")
    parser.add_argument("--limit", type=int, default=0,
                        help="exit after N CSI packets (0 = run until interrupted)")
    args = parser.parse_args()

    try:
        env = parse_env(args.env)
    except FileNotFoundError:
        die(f"{args.env} not found; run this from the project or pass --env")
    except ConfigError as err:
        die(str(err))

    host = env.get("MQTT_BROKER_HOST", "")
    if not host:
        die("MQTT_BROKER_HOST is empty in .env")
    port = int(env.get("MQTT_BROKER_PORT") or 1883)
    data_topic = env.get("MQTT_TOPIC_DATA") or "/home/csi/data"
    status_topic = env.get("MQTT_TOPIC_STATUS") or "/home/csi/status"

    # Resolve up front: a ".local" name needs Avahi/nss-mdns here, and the
    # resulting error is far clearer than a generic connection failure later.
    try:
        socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as err:
        hint = ""
        if host.endswith(".local"):
            hint = ("\n       \".local\" names need mDNS on this machine.\n"
                    "       Install/enable Avahi, or check that the broker host\n"
                    "       is really named that (`hostname` on the broker).")
        die(f"cannot resolve \"{host}\": {err}{hint}")

    print(f"broker   mqtt://{host}:{port}")
    print(f"topics   {data_topic}")
    if not args.no_status:
        print(f"         {status_topic}")
    print(f"client   csi-subscribe-{os.getpid()}")
    print()

    # Field layout comes from the firmware's status message, so this works on
    # any chip without hardcoded column indices.
    header_fields: list[str] = []
    stats = Stats()
    state = {"connected": False, "stopped": False}

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            print(f"[mqtt]   connection refused: {reason_code}", file=sys.stderr)
            state["stopped"] = True
            client.disconnect()
            return
        state["connected"] = True
        print(f"[mqtt]   connected (reason_code={reason_code})")
        client.subscribe(data_topic, qos=0)
        if not args.no_status:
            client.subscribe(status_topic, qos=1)

    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
        state["connected"] = False
        print(f"[mqtt]   disconnected ({reason_code})", file=sys.stderr)

    def on_message(client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="replace")

        if msg.topic == status_topic:
            nonlocal header_fields
            try:
                doc = json.loads(payload)
                if isinstance(doc.get("csv_header"), str):
                    header_fields = doc["csv_header"].split(",")
            except json.JSONDecodeError:
                pass
            if not args.raw:
                print(f"[status] {describe_status(payload)}")
            return

        parsed = parse_csv_line(payload)
        if parsed is None:
            print(f"[warn]   unparseable payload on {msg.topic}: {payload[:120]!r}",
                  file=sys.stderr)
            return
        meta_list, data, trailing = parsed

        meta: dict[str, str] = {}
        # The header describes every column, including the trailing "data"
        # array, so it has exactly one more field than the flat metadata has.
        # timestamp_real sits after the array and arrives separately.
        if header_fields and len(header_fields) == len(meta_list) + 1:
            meta = dict(zip(header_fields, meta_list))
            if trailing is not None and header_fields[-1] == "timestamp_real":
                meta["timestamp_real"] = trailing
        else:
            # Fall back to the columns common to every chip variant.
            meta = {k: v for k, v in zip(("type", "id", "mac", "rssi", "rate"), meta_list)}

        if args.raw:
            print(payload)
        else:
            stats.add(meta, data, len(msg.payload))
            stats.maybe_report()

        if args.limit and stats.total >= args.limit:
            state["stopped"] = True
            client.disconnect()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"csi-subscribe-{os.getpid()}")
    if env.get("MQTT_USERNAME"):
        client.username_pw_set(env["MQTT_USERNAME"], env.get("MQTT_PASSWORD", ""))
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    try:
        client.connect(host, port, keepalive=30)
    except OSError as err:
        die(f"cannot reach {host}:{port}: {err}")

    client.loop_start()
    print("listening -- Ctrl-C to stop\n")
    try:
        while not state["stopped"]:
            time.sleep(0.2)
            if not args.raw:
                stats.maybe_report()
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()

    if stats.total:
        print(f"\nreceived {stats.total} CSI packets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
