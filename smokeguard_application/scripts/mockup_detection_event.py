#!/usr/bin/env python3
"""Publish a mock smoking event to MQTT to exercise the detection pipeline.

Publishes, against the local Mosquitto broker (credentials/topics come from
the backend ``.env``):

1. A backdated ~600 s clean-air sensor baseline (so the SVM's causal
   baseline window is available immediately).
2. At ``event_start``: a real recorded CSI smoking window, replayed as
   ESP32-S3-format CSV lines with ``timestamp_real`` shifted so the CSI
   window starts exactly at the smoke event start (the collection protocol:
   CSI = the first 4-7 s of the 14 s smoke window; the backend scans
   candidate durations and picks the most confident).
3. ~14 s of sensor samples describing the smoking event (synthetic ramp by
   default, or real samples replayed from the 2026-10-27 parquet).

The backend evaluates every 0.2 s; with a captured static background the
event should alert as ``fusion`` (CSI + sensor).  ``--mode smoke-only``
skips the CSI replay, producing a ``smoke_only`` ("possible") alert.

Usage:
    # Fusion event (needs a captured static background for CSI to run)
    uv run python scripts/mockup_detection_event.py --mode fusion

    # Sensor-only "possible" event
    uv run python scripts/mockup_detection_event.py --mode smoke-only

    # Experimental CSI-only event (clean sensor, smoking-like CSI gesture;
    # requires the CSI-only toggle to be ON)
    uv run python scripts/mockup_detection_event.py --mode csi-only

    # Dry run: print what would be published, no broker connection
    uv run python scripts/mockup_detection_event.py --dry-run

    # List the smoking sessions in the default BORIS annotation
    uv run python scripts/mockup_detection_event.py --list-sessions

Run mockup scenarios at least ``DETECTION_COOLDOWN_S`` (default 30 s) apart.
"""

import argparse
import csv
import json
import sys
import threading
import time
from io import StringIO
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

DEFAULT_BORIS = (
    "/home/mayo/Documents/thesis_data/observed_events/TimeVideo_20261015_101723.csv"
)
DEFAULT_CSI_PARQUET = "raw_sensor_data/csi/csi_data_10-15-2026.parquet"
DEFAULT_SMOKE_PARQUET = (
    "raw_sensor_data/smoke_sensor/smoke_sensor_data-10-27-2026.parquet"
)
DEFAULT_SMOKE_EVENT_START = 1793058045.1  # verified smoking window (p ~= 0.99)

# Synthetic profile informed by the 2026-10-27 recording; the SVM scores this
# window at ~0.88.  Keep in sync with scripts/verify_inference.py.
DEFAULT_CLEAN = {
    "pm1_0": 10, "pm2_5": 12, "pm10": 14,
    "temp_c": 34.6, "pressure_hpa": 1002.5, "humidity_pct": 65.0,
    "gas_kohm": 118.0,
}
DEFAULT_EVENT = {
    "pm1_0": 1200, "pm2_5": 2000, "pm10": 2150,
    "temp_c": 34.7, "pressure_hpa": 1002.5, "humidity_pct": 64.4,
    "gas_kohm": 105.0,
}


# ---------------------------------------------------------------------------
# BORIS helpers
# ---------------------------------------------------------------------------

def load_boris_intervals(path: str, behavior: str) -> list[tuple[float, float]]:
    rows = list(csv.DictReader(open(path, newline="", encoding="utf-8")))
    starts = [
        float(r["Time"]) for r in rows
        if r["Behavior"] == behavior and r["Behavior type"] == "START"
    ]
    stops = [
        float(r["Time"]) for r in rows
        if r["Behavior"] == behavior and r["Behavior type"] == "STOP"
    ]
    intervals = []
    for s in starts:
        later = [t for t in stops if t > s]
        if later:
            intervals.append((s, min(later)))
    return intervals


def nearest_static_start(boris_path: str, target: float | None) -> float | None:
    """Nearest static-labelled interval start across the BORIS directory."""
    import glob

    target = target if target is not None else 0.0
    best: float | None = None
    best_gap = float("inf")
    for path in glob.glob(str(Path(boris_path).parent / "*.csv")):
        for start, stop in load_boris_intervals(path, "static"):
            gap = abs((start + stop) / 2.0 - target)
            if gap < best_gap:
                best_gap, best = gap, start
    return best


# ---------------------------------------------------------------------------
# CSI parquet → ESP32-S3 CSV lines
# ---------------------------------------------------------------------------

def load_csi_rows(parquet_path: str, t0: float, seconds: float) -> list[dict]:
    """Rows within [t0, t0+seconds] with 64 subcarriers, sorted by time."""
    import pyarrow.parquet as pq

    table = pq.read_table(
        parquet_path,
        columns=[
            "time", "seq", "mac", "rssi", "rate", "sig_mode", "mcs",
            "bandwidth", "smoothing", "not_sounding", "aggregation", "stbc",
            "fec_coding", "sgi", "noise_floor", "ampdu_cnt", "channel",
            "secondary_channel", "ant", "sig_len", "rx_state", "first_word",
            "subcarrier_count", "i", "q",
        ],
    )
    cols = {c: table.column(c).to_pylist() for c in table.column_names}
    times = np.array([t.timestamp() for t in cols["time"]], dtype=np.float64)
    idx = [
        j for j in np.where((times >= t0) & (times <= t0 + seconds))[0]
        if cols["subcarrier_count"][j] == 64
        and cols["i"][j] and len(cols["i"][j]) == 64
        and cols["q"][j] and len(cols["q"][j]) == 64
    ]
    rows = []
    for j in idx:
        row = {c: cols[c][j] for c in cols if c not in ("time", "i", "q")}
        row["t"] = float(times[j])
        row["i"] = cols["i"][j]
        row["q"] = cols["q"][j]
        rows.append(row)
    rows.sort(key=lambda r: r["t"])
    return rows


def build_csi_line(row: dict, timestamp_real: float) -> str:
    """ESP32-S3 26-field CSV line (DATA_COLUMNS_S3 + timestamp_real)."""
    interleaved = [v for pair in zip(row["i"], row["q"]) for v in pair]
    fields = [
        "CSI_DATA",
        str(row["seq"]),
        row["mac"],
        str(row["rssi"]),
        str(row["rate"]),
        str(row["sig_mode"]),
        str(row["mcs"]),
        str(row["bandwidth"]),
        str(row["smoothing"]),
        str(row["not_sounding"]),
        str(row["aggregation"]),
        str(row["stbc"]),
        str(row["fec_coding"]),
        str(row["sgi"]),
        str(row["noise_floor"]),
        str(row["ampdu_cnt"]),
        str(row["channel"]),
        str(row["secondary_channel"]),
        "0",  # local_timestamp (unused when timestamp_real > 0)
        str(row["ant"]),
        str(row["sig_len"]),
        str(row["rx_state"]),
        str(len(interleaved)),  # len = number of I/Q integers
        str(row["first_word"]),
        json.dumps(interleaved, separators=(",", ":")),
        f"{timestamp_real:.6f}",
    ]
    # csv.writer quotes the JSON array (it contains commas), matching the
    # firmware's quoted data column.
    buffer = StringIO()
    csv.writer(buffer, lineterminator="").writerow(fields)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Smoke CSV lines (16 fields: PMS5003 + BME680)
# ---------------------------------------------------------------------------

def build_smoke_line(ts: float, values: dict, rssi: int = -42) -> str:
    pm = values["pm2_5"]
    return ",".join([
        f"{ts:.6f}",
        str(int(values["pm1_0"])),
        str(int(pm)),
        str(int(values["pm10"])),
        str(int(pm * 18)), str(int(pm * 6)), str(int(pm * 2)),
        str(int(pm)), str(int(pm * 0.3)), "0",
        f"{values['temp_c']:.2f}",
        f"{values['pressure_hpa']:.2f}",
        f"{values['humidity_pct']:.2f}",
        f"{values['gas_kohm']:.2f}",
        "45.00",
        str(rssi),
    ])


def synthetic_values(t_rel: float, window_s: float, ramp_fraction: float) -> dict:
    frac = min(1.0, t_rel / (ramp_fraction * window_s))
    return {
        key: DEFAULT_CLEAN[key] + frac * (DEFAULT_EVENT[key] - DEFAULT_CLEAN[key])
        for key in DEFAULT_CLEAN
    }


def load_smoke_rows(parquet_path: str, t0: float, t1: float) -> list[tuple[float, dict, int]]:
    """(timestamp, channel values, rssi) rows within [t0, t1]."""
    import pyarrow.parquet as pq

    table = pq.read_table(parquet_path)
    cols = {c: table.column(c).to_pylist() for c in table.column_names}
    times = [t.timestamp() for t in cols["time"]]
    rows = []
    for i, ts in enumerate(times):
        if t0 <= ts <= t1:
            values = {c: cols[c][i] for c in DEFAULT_CLEAN}
            rows.append((ts, values, cols["rssi"][i]))
    return rows


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------

def _sleep_until(target: float) -> None:
    while True:
        remaining = target - time.time()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.05))


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    broker = args.broker or settings.mqtt_host
    port = args.port or settings.mqtt_port
    client = None
    if not args.dry_run:
        import paho.mqtt.client as mqtt

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"smokeguard-mockup-{int(time.time())}",
        )
        if settings.mqtt_username:
            client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        client.connect(broker, port, keepalive=30)
        client.loop_start()
        deadline = time.time() + 5
        while not client.is_connected() and time.time() < deadline:
            time.sleep(0.05)
        if not client.is_connected():
            print(f"ERROR: could not connect to {broker}:{port}", file=sys.stderr)
            return 1
        print(f"Connected to {broker}:{port}")

    def publish(topic: str, payload: str) -> None:
        if args.dry_run:
            if args.verbose:
                print(f"  [{topic}] {payload[:100]}{'…' if len(payload) > 100 else ''}")
        else:
            client.publish(topic, payload, qos=0)

    def publish_smoke(ts: float, values: dict, rssi: int = -42) -> None:
        publish(args.smoke_topic, build_smoke_line(ts, values, rssi))

    # ---- Static baseline mode (for capturing the CSI background) -------
    if args.mode == "static":
        if args.static_start is not None:
            static_start = args.static_start
        else:
            # Anchor the search to the smoking session (same recording day)
            smoking = load_boris_intervals(args.boris, "smoking")
            anchor = args.session_start or (smoking[0][0] if smoking else None)
            static_start = nearest_static_start(args.boris, anchor)
            if static_start is None:
                print("ERROR: no static-labelled interval found — pass "
                      "--static-start", file=sys.stderr)
                return 1
        rows = load_csi_rows(args.csi_parquet, static_start, args.csi_seconds)
        if not rows:
            print("ERROR: no CSI rows for the static window", file=sys.stderr)
            return 1
        event_start = time.time() + args.lead_in
        print(f"Publishing a static CSI window ({len(rows)} packets from "
              f"{static_start:.1f}) starting at {event_start:.1f}.")
        t_first = rows[0]["t"]
        for row in rows:
            offset = row["t"] - t_first
            _sleep_until(event_start + offset)
            publish(args.csi_topic, build_csi_line(row, event_start + offset))
        time.sleep(0.5)
        if client is not None:
            client.disconnect()
            client.loop_stop()
        print("Static CSI published — click 'Capture static background' in "
              "the sidebar (or POST /api/detection/baseline) now.")
        return 0

    # ---- Resolve the smoking session -----------------------------------
    intervals = load_boris_intervals(args.boris, "smoking")
    if not intervals:
        print(f"ERROR: no smoking intervals in {args.boris}", file=sys.stderr)
        return 1
    session_start = args.session_start or intervals[0][0]
    print(f"Smoking session: start={session_start:.3f} "
          f"(from {Path(args.boris).name})")

    event_start = args.event_start or (time.time() + args.lead_in)
    print(f"Mock event start: {event_start:.3f} (epoch)")

    # ---- Baseline (backdated) ------------------------------------------
    if args.smoke_source == "parquet":
        if args.smoke_event_start is None:
            print("ERROR: --smoke-event-start is required for "
                  "--smoke-source parquet", file=sys.stderr)
            return 1
        rows = load_smoke_rows(
            args.smoke_parquet,
            args.smoke_event_start - args.baseline_seconds,
            args.smoke_event_start,
        )
        baseline = [
            (event_start - (args.smoke_event_start - ts), values, rssi)
            for ts, values, rssi in rows
        ]
    else:
        # Even spacing ending exactly at event_start so the backend anchor
        # matches event_start (exact CSI window alignment).
        n = max(2, int(np.ceil(args.baseline_seconds / args.cadence)) + 1)
        spacing = args.baseline_seconds / (n - 1)
        baseline = [
            (event_start - args.baseline_seconds + k * spacing,
             DEFAULT_CLEAN, -42)
            for k in range(n)
        ]
    anchor = baseline[-1][0]
    csi_t0 = anchor - args.smoke_window

    def publish_baseline(samples: list[tuple[float, dict, int]]) -> None:
        for i, (ts, values, rssi) in enumerate(samples):
            publish_smoke(ts, values, rssi)
            if not args.dry_run and i % 200 == 199:
                time.sleep(0.02)

    # ---- CSI-only mode: burst of CSI at the window start, no sensor event
    if args.mode == "csi-only":
        csi_rows = load_csi_rows(args.csi_parquet, session_start, args.csi_seconds)
        if not csi_rows:
            print("ERROR: no CSI rows for the session window", file=sys.stderr)
            return 1
        tail_cut = anchor - (args.smoke_window + 1.0)
        head = [b for b in baseline if b[0] < tail_cut]
        tail = [b for b in baseline if b[0] >= tail_cut]
        print(f"CSI-only event: {len(csi_rows)} CSI packets at "
              f"{csi_t0:.1f} (no smoking sensor samples)")
        print(f"Publishing baseline head ({len(head)}), then the CSI burst, "
              f"then the last {len(tail)} baseline samples")
        publish_baseline(head)
        t_first = csi_rows[0]["t"]
        for row in csi_rows:
            publish(
                args.csi_topic,
                build_csi_line(row, csi_t0 + (row["t"] - t_first)),
            )
        time.sleep(0.2)
        # Completing the window only after the burst guarantees the first
        # evaluable tick already sees the whole CSI window.
        publish_baseline(tail)

        time.sleep(0.5)
        if client is not None:
            client.disconnect()
            client.loop_stop()
        print("Mock CSI-only event published.")
        print("Expected: a 'csi_only' alert within ~1-2 s (requires the "
              "static background and CSI-only detection enabled).")
        print("After another class's alert, wait ~45 s (previous anchor "
              "+ 14.905 s window + 30 s cooldown).")
        return 0

    print(f"Publishing {len(baseline)} baseline samples "
          f"({baseline[0][0]:.1f} … {baseline[-1][0]:.1f})")
    publish_baseline(baseline)

    # ---- CSI window (replayed, start-aligned) --------------------------
    csi_rows: list[dict] = []
    if args.mode == "fusion":
        csi_rows = load_csi_rows(args.csi_parquet, session_start, args.csi_seconds)
        if not csi_rows:
            print("ERROR: no CSI rows for the session window", file=sys.stderr)
            return 1
        print(f"CSI window: {len(csi_rows)} packets over "
              f"{csi_rows[-1]['t'] - csi_rows[0]['t']:.2f} s")

        def publish_csi() -> None:
            t_first = csi_rows[0]["t"]
            for row in csi_rows:
                offset = row["t"] - t_first
                _sleep_until(event_start + offset)
                publish(
                    args.csi_topic,
                    build_csi_line(row, event_start + offset),
                )

        threading.Thread(target=publish_csi, daemon=True,
                         name="mockup-csi").start()

    # ---- Sensor event window ------------------------------------------
    if args.smoke_source == "parquet":
        rows = load_smoke_rows(
            args.smoke_parquet,
            args.smoke_event_start,
            args.smoke_event_start + args.smoke_window,
        )
        print(f"Replaying {len(rows)} real smoking samples")
        for ts, values, rssi in rows:
            _sleep_until(event_start + (ts - args.smoke_event_start))
            publish_smoke(event_start + (ts - args.smoke_event_start),
                          values, rssi)
    else:
        # Space the samples evenly so the LAST one lands exactly at the end
        # of the smoke window — the backend anchors the window at the
        # latest sample, so this start-aligns the CSI window precisely.
        n = max(2, int(np.ceil(args.smoke_window / args.cadence)) + 1)
        spacing = args.smoke_window / (n - 1)
        print(f"Publishing {n} synthetic smoking samples over "
              f"{args.smoke_window:.3f} s (spacing {spacing:.3f} s)")
        for k in range(n):
            t_rel = k * spacing
            _sleep_until(event_start + t_rel)
            publish_smoke(
                event_start + t_rel,
                synthetic_values(t_rel, args.smoke_window, args.ramp_fraction),
            )

    # Let in-flight messages drain before disconnecting
    time.sleep(0.5)
    if client is not None:
        client.disconnect()
        client.loop_stop()

    print("Mock event published.")
    if args.mode == "fusion":
        print("Expected: a 'fusion' alert ~14 s after the event start "
              "(requires a captured static background).")
    else:
        print("Expected: a 'smoke_only' (possible activity) alert ~5-15 s "
              "after the event start.")
    print(f"Keep scenarios at least the detection cooldown apart "
          f"({settings.detection_cooldown_s:.0f} s).")
    return 0


def list_sessions(args: argparse.Namespace) -> int:
    intervals = load_boris_intervals(args.boris, "smoking")
    print(f"Smoking sessions in {args.boris}: {len(intervals)}")
    for start, stop in intervals:
        print(f"  start={start:.3f}  stop={stop:.3f}  dur={stop - start:.1f}s")
    return 0


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mode",
                        choices=["fusion", "smoke-only", "csi-only", "static"],
                        default="fusion",
                        help="csi-only = CSI gesture with a clean sensor "
                             "(experimental class); static = replay a quiet "
                             "CSI window, then capture the background")
    parser.add_argument("--broker", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--csi-topic", default=settings.mqtt_topic_data)
    parser.add_argument("--smoke-topic", default=settings.mqtt_topic_smoke)
    parser.add_argument("--csi-parquet", default=DEFAULT_CSI_PARQUET)
    parser.add_argument("--boris", default=DEFAULT_BORIS)
    parser.add_argument("--session-start", type=float, default=None)
    parser.add_argument("--csi-seconds", type=float, default=7.0,
                        help="CSI span to replay (backend scans 4.0-7.0 s)")
    parser.add_argument("--smoke-window", type=float,
                        default=settings.detection_smoke_seconds)
    parser.add_argument("--baseline-seconds", type=float, default=600.0)
    parser.add_argument("--cadence", type=float, default=0.58)
    parser.add_argument("--ramp-fraction", type=float, default=0.25)
    parser.add_argument("--lead-in", type=float, default=3.0,
                        help="Seconds between script start and event start")
    parser.add_argument("--event-start", type=float, default=None,
                        help="Override the mock event start epoch")
    parser.add_argument("--smoke-source", choices=["synthetic", "parquet"],
                        default="synthetic")
    parser.add_argument("--smoke-parquet", default=DEFAULT_SMOKE_PARQUET)
    parser.add_argument("--smoke-event-start", type=float,
                        default=DEFAULT_SMOKE_EVENT_START)
    parser.add_argument("--static-start", type=float, default=None,
                        help="Epoch start of a static CSI window (mode static)")
    parser.add_argument("--list-sessions", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.list_sessions:
        return list_sessions(args)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
