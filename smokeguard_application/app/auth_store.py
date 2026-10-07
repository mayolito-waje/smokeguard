"""Thread-safe SQLite store for admin credentials (single admin user).

Mirrors ``DetectionStore``: a single connection with
``check_same_thread=False`` plus a lock is enough — every operation is a
tiny indexed select or upsert (microseconds).
"""

import logging
import sqlite3
import threading
from datetime import datetime

logger = logging.getLogger("smokeguard")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,        -- bcrypt hash (salt embedded)
    updated_at REAL NOT NULL            -- epoch seconds (server time)
);
"""


class AuthStore:
    """SQLite persistence for admin credentials."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        logger.info("Auth store ready at %s", db_path)

    def close(self) -> None:
        """Close the connection (called on backend shutdown)."""
        with self._lock:
            self._conn.close()

    def get_password_hash(self, username: str) -> str | None:
        """Stored bcrypt hash for a user, or None when the user is absent."""
        with self._lock:
            row = self._conn.execute(
                "SELECT password_hash FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        return row[0] if row else None

    def upsert_user(self, username: str, password_hash: str) -> None:
        """Create the user or replace its password hash."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO users (username, password_hash, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET "
                "password_hash = excluded.password_hash, "
                "updated_at = excluded.updated_at",
                (username, password_hash, datetime.now().timestamp()),
            )
            self._conn.commit()
