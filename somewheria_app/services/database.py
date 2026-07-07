"""SQLite database scaffolding.

This module is the seam for the planned JSON-files -> SQLite migration. It is
NOT wired into the running app by default. ``create_app()`` only constructs a
``SqlStorageService`` when ``USE_SQLITE_STORAGE=1``. With the flag off (the
default), nothing in this file is imported during request handling and the
existing ``FileStorageService`` remains the source of truth.

Schema overview
---------------

- ``user_roles(email TEXT PRIMARY KEY, role TEXT NOT NULL)``
- ``pending_registrations(email TEXT PRIMARY KEY, payload TEXT NOT NULL)``
  payload is the original registration dict serialized as JSON.
- ``renter_profiles(email TEXT PRIMARY KEY, profile TEXT NOT NULL)``
- ``renter_contracts(email TEXT NOT NULL, idx INTEGER NOT NULL,
   contract TEXT NOT NULL, PRIMARY KEY (email, idx))``
- ``tickets(id TEXT PRIMARY KEY, payload TEXT NOT NULL,
   updated_at TEXT, created_at TEXT)``
- ``lead_captures(email TEXT PRIMARY KEY, payload TEXT NOT NULL)``
- ``hidden_listings(property_id TEXT PRIMARY KEY)`` — listings deactivated by
  an admin (hidden from the public site, kept upstream).

Tickets and registrations keep their full dict in a JSON ``payload`` column to
match the existing flexible shape; promotion to typed columns can come later
once the JSON path is fully retired.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS user_roles (
        email TEXT PRIMARY KEY,
        role  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pending_registrations (
        email   TEXT PRIMARY KEY,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS renter_profiles (
        email   TEXT PRIMARY KEY,
        profile TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS renter_contracts (
        email    TEXT NOT NULL,
        idx      INTEGER NOT NULL,
        contract TEXT NOT NULL,
        PRIMARY KEY (email, idx)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tickets (
        id         TEXT PRIMARY KEY,
        payload    TEXT NOT NULL,
        created_at TEXT,
        updated_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lead_captures (
        email   TEXT PRIMARY KEY,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hidden_listings (
        property_id TEXT PRIMARY KEY
    )
    """,
)


class Database:
    """Thin wrapper around ``sqlite3`` with a process-wide lock.

    The lock mirrors ``FileStorageService.file_lock`` — single-process model.
    Across multiple workers SQLite's own file locking takes over.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            for stmt in SCHEMA_STATEMENTS:
                conn.execute(stmt)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        # Reads do not require the process lock for correctness (SQLite
        # handles concurrent readers), but we still serialize via the lock
        # to keep parity with FileStorageService until full cutover.
        with self._lock:
            conn = self._connect()
            try:
                yield conn
            finally:
                conn.close()


# ---------------------------------------------------------------------------
# JSON helpers — small wrappers so callers don't need to import json everywhere.

def dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=False)


def loads(text: str):
    return json.loads(text)
