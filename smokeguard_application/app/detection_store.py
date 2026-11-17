"""Thread-safe SQLite store for smoking-detection events and settings.

A single connection with ``check_same_thread=False`` plus a lock is enough
here: every operation is a tiny insert or indexed select (microseconds),
and the one heavier step — serializing a ~300 KB snapshot to JSON — happens
only when a detection triggers, which is a rare event.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime

logger = logging.getLogger("smokeguard")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS detection_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at REAL NOT NULL,          -- epoch seconds (server time)
    csi_frames INTEGER NOT NULL,        -- denormalized for cheap summaries
    smoke_samples INTEGER NOT NULL,
    csi_json TEXT NOT NULL,             -- snapshot: frames + active_indices
    smoke_json TEXT NOT NULL,           -- snapshot: list of sample dicts
    created_at REAL NOT NULL,
    label TEXT NOT NULL DEFAULT 'fusion',  -- 'fusion' | 'smoke_only' | 'csi_only'
    p_csi REAL,                         -- calibrated P(smoke) from the CSI model
    p_sensor REAL,                      -- SVM P(smoke)
    fused_score REAL                    -- weighted CSI+sensor score; NULL for csi_only
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Created after the migration so indexes on migrated columns never run
# against a pre-migration table.
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_det_events_time ON detection_events(detected_at);
CREATE INDEX IF NOT EXISTS idx_det_events_label ON detection_events(label);
"""

# Columns added after the initial schema — migrated in place for old DBs.
_MIGRATION_COLUMNS = {
    "label": "TEXT NOT NULL DEFAULT 'fusion'",
    "p_csi": "REAL",
    "p_sensor": "REAL",
    "fused_score": "REAL",
    # Model gate flags (NULL on events recorded before they were persisted;
    # scripts/backfill_detection_hits.py fills them from stored snapshots).
    "csi_hit": "INTEGER",
    "sensor_hit": "INTEGER",
}

_DETECTION_ENABLED_KEY = "detection_enabled"
_CSI_ONLY_ENABLED_KEY = "csi_only_enabled"


class DetectionStore:
    """SQLite persistence for detection events and the enabled toggle."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.executescript(_INDEXES)
            self._conn.commit()
        logger.info("Detection store ready at %s", db_path)

    def _migrate(self) -> None:
        """Add label/probability/gate columns to DBs created before them."""
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(detection_events)")
        }
        for column, decl in _MIGRATION_COLUMNS.items():
            if column not in existing:
                self._conn.execute(
                    f"ALTER TABLE detection_events ADD COLUMN {column} {decl}"
                )
                logger.info("Migrated detection_events: added column %s", column)

    def close(self) -> None:
        """Close the connection (called on backend shutdown)."""
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Settings (the enabled toggle)
    # ------------------------------------------------------------------

    def get_enabled(self) -> bool:
        """Return whether automatic detection is enabled (default False)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (_DETECTION_ENABLED_KEY,),
            ).fetchone()
        return row is not None and row[0] == "1"

    def set_enabled(self, enabled: bool) -> None:
        """Persist the enabled toggle (survives backend restarts)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_DETECTION_ENABLED_KEY, "1" if enabled else "0"),
            )
            self._conn.commit()

    def get_csi_only_enabled(self) -> bool:
        """Return whether the experimental CSI-only class is enabled."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (_CSI_ONLY_ENABLED_KEY,),
            ).fetchone()
        return row is not None and row[0] == "1"

    def set_csi_only_enabled(self, enabled: bool) -> None:
        """Persist the experimental CSI-only toggle (default off)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_CSI_ONLY_ENABLED_KEY, "1" if enabled else "0"),
            )
            self._conn.commit()

    def get_setting(self, key: str) -> str | None:
        """Read an arbitrary persisted setting (None when absent)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row is not None else None

    def set_setting(self, key: str, value: str) -> None:
        """Upsert an arbitrary persisted setting."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Detection events
    # ------------------------------------------------------------------

    def insert_event(
        self,
        detected_at: float,
        csi_frames: int,
        smoke_samples: int,
        csi_json: str,
        smoke_json: str,
        label: str,
        p_csi: float | None,
        p_sensor: float | None,
        fused_score: float | None,
        csi_hit: bool | None = None,
        sensor_hit: bool | None = None,
    ) -> int:
        """Insert a detection event (csi_json/smoke_json pre-serialized)."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO detection_events "
                "(detected_at, csi_frames, smoke_samples, csi_json, smoke_json, "
                "created_at, label, p_csi, p_sensor, fused_score, csi_hit, "
                "sensor_hit) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    detected_at, csi_frames, smoke_samples, csi_json, smoke_json,
                    datetime.now().timestamp(), label, p_csi, p_sensor, fused_score,
                    None if csi_hit is None else int(csi_hit),
                    None if sensor_hit is None else int(sensor_hit),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    @staticmethod
    def _month_bounds(year: int, month: int) -> tuple[float, float]:
        """Epoch bounds for a calendar month in server-local time."""
        start = datetime(year, month, 1).timestamp()
        if month == 12:
            end = datetime(year + 1, 1, 1).timestamp()
        else:
            end = datetime(year, month + 1, 1).timestamp()
        return start, end

    def list_events(
        self, year: int, month: int, label: str | None = None
    ) -> list[dict]:
        """Summaries (no JSON blobs) for one month, newest first."""
        start, end = self._month_bounds(year, month)
        query = (
            "SELECT id, detected_at, csi_frames, smoke_samples, "
            "label, p_csi, p_sensor, fused_score, csi_hit, sensor_hit "
            "FROM detection_events "
            "WHERE detected_at >= ? AND detected_at < ?"
        )
        params: list = [start, end]
        if label is not None:
            query += " AND label = ?"
            params.append(label)
        query += " ORDER BY detected_at DESC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "id": row[0],
                "detected_at": row[1],
                "csi_frames": row[2],
                "smoke_samples": row[3],
                "label": row[4],
                "p_csi": row[5],
                "p_sensor": row[6],
                "fused_score": row[7],
                "csi_hit": None if row[8] is None else bool(row[8]),
                "sensor_hit": None if row[9] is None else bool(row[9]),
            }
            for row in rows
        ]

    def list_events_for_report(self, year: int, month: int) -> list[dict]:
        """Monthly rows for the PDF report, oldest first, with avg PM2.5.

        Reads the smoke snapshot blob to average PM2.5 (small: one window per
        event); the much larger CSI blob is left untouched.
        """
        start, end = self._month_bounds(year, month)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, detected_at, label, p_csi, p_sensor, fused_score, "
                "csi_hit, sensor_hit, smoke_json "
                "FROM detection_events "
                "WHERE detected_at >= ? AND detected_at < ? "
                "ORDER BY detected_at ASC",
                (start, end),
            ).fetchall()
        return [
            {
                "id": row[0],
                "detected_at": row[1],
                "label": row[2],
                "p_csi": row[3],
                "p_sensor": row[4],
                "fused_score": row[5],
                "csi_hit": None if row[6] is None else bool(row[6]),
                "sensor_hit": None if row[7] is None else bool(row[7]),
                "avg_pm2_5": _mean_pm2_5(row[8]),
            }
            for row in rows
        ]

    def get_event(self, event_id: int) -> dict | None:
        """Full event including parsed snapshot JSON, or None if missing."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, detected_at, csi_frames, smoke_samples, csi_json, "
                "smoke_json, label, p_csi, p_sensor, fused_score, csi_hit, "
                "sensor_hit "
                "FROM detection_events WHERE id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "detected_at": row[1],
            "csi_frames": row[2],
            "smoke_samples": row[3],
            "csi": json.loads(row[4]),
            "smoke": json.loads(row[5]),
            "label": row[6],
            "p_csi": row[7],
            "p_sensor": row[8],
            "fused_score": row[9],
            "csi_hit": None if row[10] is None else bool(row[10]),
            "sensor_hit": None if row[11] is None else bool(row[11]),
        }

    def delete_event(self, event_id: int) -> bool:
        """Delete one event; returns True when a row was removed."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM detection_events WHERE id = ?", (event_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Gate-flag backfill (legacy events)
    # ------------------------------------------------------------------

    def list_events_missing_hits(self) -> list[dict]:
        """Rows whose csi_hit / sensor_hit flags are not filled in yet."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, detected_at, label, p_csi, p_sensor, csi_json "
                "FROM detection_events "
                "WHERE csi_hit IS NULL OR sensor_hit IS NULL "
                "ORDER BY detected_at ASC"
            ).fetchall()
        return [
            {
                "id": row[0],
                "detected_at": row[1],
                "label": row[2],
                "p_csi": row[3],
                "p_sensor": row[4],
                "csi_json": row[5],
            }
            for row in rows
        ]

    def update_event_hits(
        self,
        event_id: int,
        *,
        csi_hit: bool | None = None,
        sensor_hit: bool | None = None,
    ) -> bool:
        """Set gate flags for one row; None leaves that column unchanged."""
        assignments: list[str] = []
        params: list = []
        if csi_hit is not None:
            assignments.append("csi_hit = ?")
            params.append(int(csi_hit))
        if sensor_hit is not None:
            assignments.append("sensor_hit = ?")
            params.append(int(sensor_hit))
        if not assignments:
            return False
        params.append(event_id)
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE detection_events SET {', '.join(assignments)} "
                "WHERE id = ?",
                params,
            )
            self._conn.commit()
            return cur.rowcount > 0


def _mean_pm2_5(smoke_json: str | None) -> float | None:
    """Mean PM2.5 over a stored smoke-window snapshot (None when unavailable)."""
    if not smoke_json:
        return None
    try:
        samples = json.loads(smoke_json).get("samples") or []
    except (AttributeError, TypeError, ValueError):
        return None
    values: list[float] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        value = sample.get("pm2_5")
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return sum(values) / len(values) if values else None
