#!/usr/bin/env python3
"""Offline validation of the SmokeGuard ML inference path (no backend/MQTT).

Runs the exact notebook preprocessing + model code from ``app/inference.py``
against recorded data so the pipeline can be verified without sensors.

Modes (choose one):

  CSI — real smoking window + same-day static background:
    uv run python scripts/verify_inference.py \
        --csi-parquet raw_sensor_data/csi/csi_data_10-15-2026.parquet \
        --boris /home/mayo/Documents/thesis_data/observed_events/TimeVideo_20261015_101723.csv
    # optional: --session-start <epoch> --background-start <epoch> --csi-seconds 4.2
    # add --scan to evaluate the 4.0-7.0 s candidate grid (runtime behaviour)

  Sensor — real window + causal 600 s baseline from the smoke parquet:
    uv run python scripts/verify_inference.py \
        --smoke-parquet raw_sensor_data/smoke_sensor/smoke_sensor_data-10-27-2026.parquet \
        --event-start 1793058045.1

  Synthetic — the mockup publisher's clean/event profile:
    uv run python scripts/verify_inference.py --synthetic-smoke

  List BORIS smoking sessions in a file:
    uv run python scripts/verify_inference.py --boris path.csv --list-sessions

Exit code 1 on load/shape/NaN failures.
"""

import argparse
import csv
import glob
import sys
from pathlib import Path

import numpy as np

# Make ``app`` importable when the script is run from scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import inference  # noqa: E402
from app.models import SmokeRecord  # noqa: E402

DEFAULT_BORIS = (
    "/home/mayo/Documents/thesis_data/observed_events/TimeVideo_20261015_101723.csv"
)
DEFAULT_CSI_PARQUET = "raw_sensor_data/csi/csi_data_10-15-2026.parquet"
DEFAULT_SMOKE_PARQUET = (
    "raw_sensor_data/smoke_sensor/smoke_sensor_data-10-27-2026.parquet"
)
DEFAULT_CSI_MODEL = "models/csi_resnet_lstm_7class.keras"
DEFAULT_SMOKE_MODEL = "models/smoke_svm_pipeline.joblib"


# ---------------------------------------------------------------------------
# BORIS helpers
# ---------------------------------------------------------------------------

def load_boris_intervals(path: str, behavior: str) -> list[tuple[float, float]]:
    """START/STOP intervals for one behavior label in a BORIS export."""
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


def nearest_static_interval(
    boris_dir: str, target: float
) -> tuple[float, float] | None:
    """Nearest same-day static interval across all BORIS files in a directory.

    Mirrors the notebook's nearest-static-background selection (same local
    date, closest in time).
    """
    best: tuple[float, float] | None = None
    best_gap = float("inf")
    for path in glob.glob(str(Path(boris_dir) / "*.csv")):
        for start, stop in load_boris_intervals(path, "static"):
            gap = abs((start + stop) / 2.0 - target)
            if gap < best_gap:
                best_gap, best = gap, (start, stop)
    return best


# ---------------------------------------------------------------------------
# Parquet helpers
# ---------------------------------------------------------------------------

def load_csi_window(
    parquet_path: str, t0: float, seconds: float
) -> tuple[np.ndarray, np.ndarray]:
    """(amp (N, 52), t (N,)) for 64-subcarrier rows within [t0, t0+seconds]."""
    import pyarrow.parquet as pq

    table = pq.read_table(parquet_path, columns=["time", "i", "q", "subcarrier_count"])
    times = np.array(
        [t.timestamp() for t in table.column("time").to_pylist()], dtype=np.float64
    )
    i_col = table.column("i").to_pylist()
    q_col = table.column("q").to_pylist()
    sc_col = table.column("subcarrier_count").to_pylist()
    idx = [
        j for j in np.where((times >= t0) & (times <= t0 + seconds))[0]
        if sc_col[j] == 64 and i_col[j] and len(i_col[j]) == 64
    ]
    if not idx:
        raise SystemExit(f"No 64-subcarrier CSI rows in [{t0}, {t0 + seconds}]")
    amp = np.stack([
        np.sqrt(
            np.asarray(i_col[j], dtype=np.float64) ** 2
            + np.asarray(q_col[j], dtype=np.float64) ** 2
        )[inference.ACTIVE_INDICES_64]
        for j in idx
    ])
    return amp, times[idx]


def load_smoke_records(
    parquet_path: str, t0: float, t1: float
) -> list[SmokeRecord]:
    """SmokeRecord list for rows within [t0, t1]."""
    import pyarrow.parquet as pq

    table = pq.read_table(parquet_path)
    cols = {c: table.column(c).to_pylist() for c in table.column_names}
    times = [t.timestamp() for t in cols["time"]]
    records = []
    for i, ts in enumerate(times):
        if t0 <= ts <= t1:
            records.append(SmokeRecord(
                timestamp_real=ts,
                pm1_0=cols["pm1_0"][i], pm2_5=cols["pm2_5"][i],
                pm10=cols["pm10"][i],
                cnt0_3=cols["cnt0_3"][i], cnt0_5=cols["cnt0_5"][i],
                cnt1_0=cols["cnt1_0"][i], cnt2_5=cols["cnt2_5"][i],
                cnt5_0=cols["cnt5_0"][i], cnt10=cols["cnt10"][i],
                temp_c=cols["temp_c"][i], pressure_hpa=cols["pressure_hpa"][i],
                humidity_pct=cols["humidity_pct"][i],
                gas_kohm=cols["gas_kohm"][i], altitude_m=cols["altitude_m"][i],
                rssi=cols["rssi"][i],
            ))
    return records


# ---------------------------------------------------------------------------
# Synthetic sensor profile (matches scripts/mockup_detection_event.py)
# ---------------------------------------------------------------------------

def synthetic_samples(
    start: float, seconds: float, cadence: float, clean: dict, event: dict,
    ramp_fraction: float = 0.25,
) -> list[SmokeRecord]:
    """Clean baseline or ramping smoking window as SmokeRecord samples.

    Event windows are spaced evenly so the last sample lands exactly at
    ``start + seconds`` (matches the mockup publisher's alignment).
    """
    if event is None:
        n = max(1, int(round(seconds / cadence)))
        spacing = cadence
    else:
        n = max(2, int(np.ceil(seconds / cadence)) + 1)
        spacing = seconds / (n - 1)
    records = []
    for k in range(n):
        t_rel = k * spacing
        frac = 1.0 if event is None else min(1.0, t_rel / (ramp_fraction * seconds))
        src = clean if event is None else {
            key: clean[key] + frac * (event[key] - clean[key])
            for key in clean
        }
        records.append(SmokeRecord(
            timestamp_real=start + t_rel,
            pm1_0=int(src["pm1_0"]), pm2_5=int(src["pm2_5"]), pm10=int(src["pm10"]),
            cnt0_3=int(src["pm2_5"] * 18), cnt0_5=int(src["pm2_5"] * 6),
            cnt1_0=int(src["pm2_5"] * 2), cnt2_5=int(src["pm2_5"]),
            cnt5_0=int(src["pm2_5"] * 0.3), cnt10=0,
            temp_c=src["temp_c"], pressure_hpa=src["pressure_hpa"],
            humidity_pct=src["humidity_pct"], gas_kohm=src["gas_kohm"],
            altitude_m=45.0, rssi=-42,
        ))
    return records


# Values informed by the 2026-10-27 recording (clean baseline vs the
# recorded smoking window that the SVM scores at ~0.99).  Keep in sync with
# scripts/mockup_detection_event.py.
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
# Modes
# ---------------------------------------------------------------------------

def run_csi(args: argparse.Namespace) -> int:
    inference.load_models(args.csi_model, args.smoke_model, args.csi_platt)
    if not inference.csi_model_ready():
        raise SystemExit("CSI model failed to load")
    if not inference.models_ready():
        print("WARNING: sensor model missing — CSI-only validation")

    intervals = load_boris_intervals(args.boris, "smoking")
    if not intervals:
        raise SystemExit(f"No smoking intervals in {args.boris}")
    session_start = args.session_start or intervals[0][0]

    if args.background_start is not None:
        bg_start = args.background_start
    else:
        static = nearest_static_interval(str(Path(args.boris).parent), session_start)
        if static is None:
            raise SystemExit(
                "No static-labelled interval found — pass --background-start"
            )
        bg_start = static[0] + 0.3

    amp_bg, t_bg = load_csi_window(args.csi_parquet, bg_start, args.csi_seconds)
    background = inference.capture_background(t_bg, amp_bg)
    if not np.isfinite(background).all():
        raise SystemExit("Background contains non-finite values")
    print(f"Background: {len(t_bg)} packets from {bg_start:.1f} "
          f"(mean {background.mean():.2f})")

    if args.scan:
        return _scan_report(args, session_start, background)

    amp, t = load_csi_window(args.csi_parquet, session_start, args.csi_seconds)
    features = inference.preprocess_csi_window(t, amp, background)
    if features.shape != (inference.N_CSI_POINTS, len(inference.ACTIVE_INDICES_64)):
        raise SystemExit(f"Unexpected feature shape {features.shape}")
    if not np.isfinite(features).all():
        raise SystemExit("Features contain non-finite values")

    result = inference.predict_csi(features)
    print(f"Smoking window @ {session_start:.1f}: {len(t)} packets, "
          f"{t[-1] - t[0]:.2f} s")
    print(f"  logits:      {np.round(result.logits, 3)}")
    print(f"  calibrated:  {dict((k, round(v, 3)) for k, v in result.calibrated_probs.items())}")
    print(f"  p_csi_smoke: {result.p_smoke:.4f}   argmax: {result.pred_class}   "
          f"gate: {'PASS' if result.hit else 'FAIL'}")
    return 0


def _scan_report(
    args: argparse.Namespace, session_start: float, background: np.ndarray
) -> int:
    """Evaluate every candidate window duration and print the winner."""
    candidates = inference.csi_window_candidates(
        args.csi_min_seconds, args.csi_max_seconds, args.csi_step_seconds
    )
    if not candidates:
        raise SystemExit("Invalid duration range/step")
    amp, t = load_csi_window(
        args.csi_parquet, session_start, args.csi_max_seconds
    )
    print(f"Duration scan @ {session_start:.1f} ({len(t)} packets over "
          f"{t[-1] - t[0]:.2f} s)")
    results: list[tuple[float, inference.CsiResult]] = []
    for duration in candidates:
        cut = int(np.searchsorted(t, session_start + duration + 1e-6, side="right"))
        if cut < args.min_packets:
            print(f"  {duration:4.1f} s  SKIP ({cut} packets < {args.min_packets})")
            continue
        try:
            features = inference.preprocess_csi_window(
                t[:cut], amp[:cut], background
            )
            if not np.isfinite(features).all():
                print(f"  {duration:4.1f} s  SKIP (non-finite features)")
                continue
            result = inference.predict_csi(features)
        except Exception as exc:  # noqa: BLE001 - verification report
            print(f"  {duration:4.1f} s  SKIP ({exc!r})")
            continue
        if not (np.isfinite(result.logits).all() and np.isfinite(result.p_smoke)):
            print(f"  {duration:4.1f} s  SKIP (non-finite model output)")
            continue
        results.append((duration, result))
        span = t[cut - 1] - t[0]
        print(f"  {duration:4.1f} s  packets={cut:4d} span={span:4.2f}s  "
              f"p={result.p_smoke:.3f}  argmax={result.pred_class:6s} "
              f"{'HIT' if result.hit else '   '}")
    best = inference.select_best_csi_candidate(results)
    if best is None:
        raise SystemExit("No usable candidate window")
    duration, result = best
    print(f"Winner: {duration:.1f} s window — p={result.p_smoke:.3f}, "
          f"argmax={result.pred_class}, gate={'PASS' if result.hit else 'FAIL'}")
    return 0


def run_sensor(args: argparse.Namespace) -> int:
    inference.load_models(args.csi_model, args.smoke_model, args.csi_platt)
    if not inference.sensor_model_ready():
        raise SystemExit("Smoke SVM failed to load")

    if args.event_start is None:
        raise SystemExit("--event-start is required with --smoke-parquet")
    event_start = args.event_start
    window = load_smoke_records(
        args.smoke_parquet, event_start, event_start + args.smoke_window
    )
    baseline = load_smoke_records(
        args.smoke_parquet, event_start - args.baseline_seconds, event_start
    )
    print(f"Window: {len(window)} samples, baseline: {len(baseline)} samples")
    features = inference.extract_sensor_features(window, baseline)
    if features is None:
        raise SystemExit("Feature extraction returned None (missing BME680 fields)")
    if not np.isfinite(features).all():
        raise SystemExit("Sensor features contain non-finite values")
    p = inference.predict_sensor(features)
    print(f"  p_sensor: {p:.4f}   gate (>= {inference.SENSOR_THRESHOLD:.4f}): "
          f"{'PASS' if p >= inference.SENSOR_THRESHOLD else 'FAIL'}")
    return 0


def run_synthetic(args: argparse.Namespace) -> int:
    inference.load_models(args.csi_model, args.smoke_model, args.csi_platt)
    if not inference.sensor_model_ready():
        raise SystemExit("Smoke SVM failed to load")

    t_event = 1_800_000_000.0
    baseline = synthetic_samples(
        t_event - args.baseline_seconds, args.baseline_seconds,
        args.cadence, DEFAULT_CLEAN, None,
    )
    window = synthetic_samples(
        t_event, args.smoke_window, args.cadence, DEFAULT_CLEAN, DEFAULT_EVENT,
    )
    print(f"Synthetic window: {len(window)} samples, "
          f"baseline: {len(baseline)} samples")
    features = inference.extract_sensor_features(window, baseline)
    if features is None or not np.isfinite(features).all():
        raise SystemExit("Synthetic features invalid")
    p = inference.predict_sensor(features)
    print(f"  p_sensor: {p:.4f}   gate (>= {inference.SENSOR_THRESHOLD:.4f}): "
          f"{'PASS' if p >= inference.SENSOR_THRESHOLD else 'FAIL'}")
    return 0


def list_sessions(args: argparse.Namespace) -> int:
    intervals = load_boris_intervals(args.boris, "smoking")
    print(f"Smoking sessions in {args.boris}: {len(intervals)}")
    for start, stop in intervals:
        print(f"  start={start:.3f}  stop={stop:.3f}  dur={stop - start:.1f}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csi-parquet", default=DEFAULT_CSI_PARQUET)
    parser.add_argument("--boris", default=DEFAULT_BORIS)
    parser.add_argument("--session-start", type=float, default=None)
    parser.add_argument("--background-start", type=float, default=None)
    parser.add_argument("--csi-seconds", type=float, default=4.2)
    parser.add_argument("--scan", action="store_true",
                        help="CSI mode: evaluate every candidate duration")
    parser.add_argument("--csi-min-seconds", type=float, default=4.0)
    parser.add_argument("--csi-max-seconds", type=float, default=7.0)
    parser.add_argument("--csi-step-seconds", type=float, default=0.5)
    parser.add_argument("--min-packets", type=int, default=200)
    parser.add_argument("--smoke-parquet", default=None)
    parser.add_argument("--event-start", type=float, default=None)
    parser.add_argument("--baseline-seconds", type=float,
                        default=inference.SENSOR_BASELINE_SECONDS)
    parser.add_argument("--smoke-window", type=float,
                        default=inference.SENSOR_WINDOW_SECONDS)
    parser.add_argument("--cadence", type=float, default=0.58)
    parser.add_argument("--synthetic-smoke", action="store_true")
    parser.add_argument("--list-sessions", action="store_true")
    parser.add_argument("--csi-model", default=DEFAULT_CSI_MODEL)
    parser.add_argument(
        "--csi-platt", default=None,
        help="Platt calibrator JSON (default: alongside --csi-model)",
    )
    parser.add_argument("--smoke-model", default=DEFAULT_SMOKE_MODEL)
    args = parser.parse_args()

    if args.scan and (args.synthetic_smoke or args.smoke_parquet is not None):
        parser.error("--scan applies to the CSI mode only")

    try:
        if args.list_sessions:
            return list_sessions(args)
        if args.synthetic_smoke:
            return run_synthetic(args)
        if args.smoke_parquet is not None:
            return run_sensor(args)
        return run_csi(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - verification script
        print(f"FAILED: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
