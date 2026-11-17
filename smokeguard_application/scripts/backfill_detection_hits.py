"""Backfill csi_hit / sensor_hit for detection events recorded before the
monthly-report gate columns existed.

Exact, no model needed:
  * sensor_hit: fusion / smoke_only -> 1, csi_only -> 0
  * csi_hit: fusion / csi_only -> 1; smoke_only with p_csi NULL -> 0
    (the CSI path did not run for that event)

Best-effort, requires the CSI model and the saved static background:
  * legacy smoke_only rows recorded AFTER the saved background was captured
    are re-scored from their stored v2 amplitude snapshot. The snapshot
    rounds amplitudes to 2 dp, so this is a reconstruction; rows that predate
    the background (the CSI path could not have used it) or cannot be
    re-scored are left NULL (the report shows them without a highlight).

Idempotent: only NULL flags are touched. Run with --dry-run first.

Usage:
    uv run python scripts/backfill_detection_hits.py [--dry-run] [--skip-csi]
                                                     [--db PATH]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import inference
from app.config import get_settings
from app.detection import _BASELINE_SETTING_KEY
from app.detection_store import DetectionStore

logger = logging.getLogger("smokeguard.backfill")


def _load_background(
    store: DetectionStore,
) -> tuple[np.ndarray, float] | None:
    """Read the persisted static background and when it was captured."""
    raw = store.get_setting(_BASELINE_SETTING_KEY)
    if not raw:
        logger.warning(
            "No saved static background; legacy CSI gates cannot be re-scored"
        )
        return None
    try:
        data = json.loads(raw)
        vector = np.asarray(data["vector"], dtype=np.float64)
        captured_at = float(data["captured_at"])
    except (KeyError, TypeError, ValueError):
        logger.warning("Saved static background is malformed")
        return None
    expected = len(inference.ACTIVE_INDICES_64)
    if vector.shape != (expected,):
        logger.warning(
            "Saved static background has shape %s; expected (%d,)",
            vector.shape, expected,
        )
        return None
    return vector, captured_at


def _label_hits(
    label: str, p_csi: float | None
) -> tuple[bool | None, bool | None]:
    """(csi_hit, sensor_hit) implied by the deployed gating for this label."""
    if label == "fusion":
        return True, True
    if label == "smoke_only":
        # The CSI hit is ambiguous (miss, or hit but fused below 0.70) unless
        # the CSI path never ran (p_csi is None).
        return (False if p_csi is None else None), True
    if label == "csi_only":
        return True, False
    return None, None


def backfill(
    store: DetectionStore,
    *,
    dry_run: bool,
    skip_csi: bool,
) -> dict[str, int]:
    """Fill missing gate flags; returns counters for the summary line."""
    rows = store.list_events_missing_hits()
    stats = {
        "scanned": len(rows),
        "sensor_filled": 0,
        "csi_filled": 0,
        "csi_rescored": 0,
        "csi_unresolved": 0,
        "skipped": 0,
    }
    if not rows:
        return stats

    needs_csi = any(
        row["label"] == "smoke_only" and row["p_csi"] is not None
        for row in rows
    )
    background = None
    background_captured_at = 0.0
    if needs_csi and not skip_csi:
        settings = get_settings()
        inference.load_models(
            settings.csi_model_path,
            settings.smoke_model_path,
            settings.csi_platt_path,
        )
        if inference.csi_model_ready():
            loaded = _load_background(store)
            if loaded is not None:
                background, background_captured_at = loaded
        else:
            logger.warning(
                "CSI model unavailable; legacy smoke_only CSI gates stay NULL"
            )

    for row in rows:
        label = row["label"]
        csi_hit, sensor_hit = _label_hits(label, row["p_csi"])

        if (
            csi_hit is None
            and label == "smoke_only"
            and row["p_csi"] is not None
        ):
            # Only events recorded after the saved background was captured
            # can have used it; earlier rows were scored by the dummy detector
            # (synthetic probabilities) or with a background that is gone.
            if (
                background is not None
                and row["detected_at"] >= background_captured_at
            ):
                try:
                    payload = json.loads(row["csi_json"])
                except (TypeError, ValueError):
                    payload = None
                result = (
                    inference.predict_csi_from_snapshot(payload, background)
                    if payload is not None
                    else None
                )
                if result is not None:
                    csi_hit = bool(result.hit)
                    stats["csi_rescored"] += 1
            if csi_hit is None:
                stats["csi_unresolved"] += 1

        if csi_hit is None and sensor_hit is None:
            stats["skipped"] += 1
            continue

        if dry_run:
            print(
                f"#{row['id']} {label}: csi_hit={csi_hit} "
                f"sensor_hit={sensor_hit}"
            )
        else:
            store.update_event_hits(
                row["id"], csi_hit=csi_hit, sensor_hit=sensor_hit
            )
        if sensor_hit is not None:
            stats["sensor_filled"] += 1
        if csi_hit is not None:
            stats["csi_filled"] += 1

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill detection gate flags for the monthly PDF report."
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Detection SQLite path (default: settings.detection_db_path)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned updates without writing them",
    )
    parser.add_argument(
        "--skip-csi",
        action="store_true",
        help="Do not load the CSI model / re-score stored snapshots",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()
    store = DetectionStore(args.db or settings.detection_db_path)
    try:
        stats = backfill(
            store, dry_run=args.dry_run, skip_csi=args.skip_csi
        )
        remaining = len(store.list_events_missing_hits())
    finally:
        store.close()

    prefix = "Would update" if args.dry_run else "Updated"
    print(
        f"{prefix}: scanned={stats['scanned']} "
        f"sensor_hit={stats['sensor_filled']} csi_hit={stats['csi_filled']} "
        f"(re-scored={stats['csi_rescored']}, "
        f"unresolved={stats['csi_unresolved']}) skipped={stats['skipped']} "
        f"remaining_unresolved={remaining}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
