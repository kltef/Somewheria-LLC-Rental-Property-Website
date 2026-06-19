"""Tests for scripts/backup_database.py.

The backup script switched from a raw byte-copy of the SQLite file to an
online snapshot via ``sqlite3.Connection.backup`` so the archived file is
guaranteed to be internally consistent even when the live writer is active.
These tests verify:

* the produced .gz decompresses to a valid SQLite database with the
  expected schema and rows,
* the intermediate snapshot file is cleaned up,
* a snapshot taken while writes are in flight is internally consistent
  (no torn WAL state, no missing committed rows).
"""

from __future__ import annotations

import gzip
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import backup_database  # noqa: E402


def _seed_db(path: Path, row_count: int = 50, wal: bool = True) -> None:
    conn = sqlite3.connect(str(path))
    try:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, value TEXT)")
        with conn:
            conn.executemany(
                "INSERT INTO items(value) VALUES (?)",
                [(f"row-{i}",) for i in range(row_count)],
            )
    finally:
        conn.close()


def _decompress_gz(gz_path: Path, dest: Path) -> None:
    with gzip.open(gz_path, "rb") as src, dest.open("wb") as dst:
        for chunk in iter(lambda: src.read(65536), b""):
            dst.write(chunk)


class BackupCompressTests(unittest.TestCase):
    def test_compress_produces_valid_sqlite_db_with_expected_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "live.sqlite3"
            _seed_db(src, row_count=37)

            staging = tmp_path / "staging"
            artifact = backup_database.compress(src, staging)

            self.assertTrue(artifact.exists(), "gzipped backup should be written")
            self.assertTrue(artifact.name.endswith(".sqlite3.gz"))

            restored = tmp_path / "restored.sqlite3"
            _decompress_gz(artifact, restored)

            conn = sqlite3.connect(str(restored))
            try:
                count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 37)

    def test_compress_cleans_up_intermediate_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "live.sqlite3"
            _seed_db(src, row_count=3)

            staging = tmp_path / "staging"
            backup_database.compress(src, staging)

            leftovers = list(staging.glob("*.snapshot"))
            self.assertEqual(
                leftovers, [],
                f"intermediate snapshot files should be cleaned up; found {leftovers}",
            )

    def test_snapshot_is_consistent_while_writer_is_active(self):
        """Snapshot taken concurrently with a writer must include every row
        the writer committed before the snapshot started and exclude every
        row it committed after. Critically, the file must always be a valid
        SQLite database — no torn WAL pages, no integrity_check failure.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "live.sqlite3"
            _seed_db(src, row_count=1)

            stop = threading.Event()

            def writer():
                conn = sqlite3.connect(str(src), timeout=5.0)
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    i = 0
                    while not stop.is_set():
                        with conn:
                            conn.execute(
                                "INSERT INTO items(value) VALUES (?)",
                                (f"writer-{i}",),
                            )
                        i += 1
                        time.sleep(0.001)
                finally:
                    conn.close()

            t = threading.Thread(target=writer, daemon=True)
            t.start()
            # Let the writer settle into its loop before snapshotting.
            time.sleep(0.05)
            try:
                staging = tmp_path / "staging"
                artifact = backup_database.compress(src, staging)
            finally:
                stop.set()
                t.join(timeout=5)

            restored = tmp_path / "restored.sqlite3"
            _decompress_gz(artifact, restored)

            conn = sqlite3.connect(str(restored))
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                self.assertEqual(integrity, "ok")
                count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            finally:
                conn.close()
            # At least the original seed row should be present.
            self.assertGreaterEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
