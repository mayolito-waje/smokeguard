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
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_det_events_time ON detection_events(detected_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_DETECTION_ENABLED_KEY = "detection_enabled"


class DetectionStore:
    """SQLite persistence for detection events and the enabled toggle."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        logger.info("Detection store ready at %s", db_path)

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
    ) -> int:
        """Insert a detection event (csi_json/smoke_json pre-serialized)."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO detection_events "
                "(detected_at, csi_frames, smoke_samples, csi_json, smoke_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (detected_at, csi_frames, smoke_samples, csi_json, smoke_json, datetime.now().timestamp()),
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

    def list_events(self, year: int, month: int) -> list[dict]:
        """Summaries (no JSON blobs) for one month, newest first."""
        start, end = self._month_bounds(year, month)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, detected_at, csi_frames, smoke_samples "
                "FROM detection_events "
                "WHERE detected_at >= ? AND detected_at < ? "
                "ORDER BY detected_at DESC",
                (start, end),
            ).fetchall()
        return [
            {
                "id": row[0],
                "detected_at": row[1],
                "csi_frames": row[2],
                "smoke_samples": row[3],
            }
            for row in rows
        ]

    def get_event(self, event_id: int) -> dict | None:
        """Full event including parsed snapshot JSON, or None if missing."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, detected_at, csi_frames, smoke_samples, csi_json, smoke_json "
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
        }

    def delete_event(self, event_id: int) -> bool:
        """Delete one event; returns True when a row was removed."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM detection_events WHERE id = ?", (event_id,)
            )
            self._conn.commit()
            return cur.rowcount > 0
