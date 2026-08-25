"""Tests for scripts/migrate_from_json.py.

The migration script copies every JSON-backed storage bucket into the
matching SQLite table so an operator can flip ``USE_SQLITE_STORAGE=1``
without losing state. These tests pin that parity — a bucket added to
the storage layer but missed here silently republishes / drops state
on cutover.

The hidden-listings bucket in particular regressed once: it was added
to ``SqlStorageService`` but never listed in the migration script, so
flipping the flag republished every listing an admin had deactivated.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import migrate_from_json  # noqa: E402
from somewheria_app.services.sql_storage import SqlStorageService  # noqa: E402


def _make_config(base: Path) -> SimpleNamespace:
    return SimpleNamespace(
        registration_file=base / "pending_registrations.json",
        user_roles_file=base / "user_roles.json",
        renter_profile_file=base / "renter_profiles.json",
        contracts_file=base / "renter_contracts.json",
        tickets_file=base / "tickets.json",
        lead_capture_file=base / "pending_lead_captures.json",
        hidden_listings_file=base / "hidden_listings.json",
        sqlite_file=base / "test.sqlite3",
    )


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class MigrateFromJsonTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config = _make_config(self.base)

    def _seed(self):
        _write(self.config.user_roles_file, {
            "admin@example.com": "admin",
            "renter@example.com": "renter",
        })
        _write(self.config.registration_file, [
            {"email": "pending@example.com", "name": "Pending"},
        ])
        _write(self.config.renter_profile_file, {
            "renter@example.com": {"name": "R", "contact": "555"},
        })
        _write(self.config.contracts_file, {
            "renter@example.com": [
                {"id": "c1", "property_name": "House A"},
                {"id": "c2", "property_name": "House B"},
            ],
        })
        _write(self.config.tickets_file, [
            {"id": "t1", "title": "Leak"},
        ])
        _write(self.config.lead_capture_file, [
            {"email": "lead@example.com"},
        ])
        _write(self.config.hidden_listings_file, ["prop-hidden-1", "prop-hidden-2"])

    def test_dry_run_reports_row_counts_without_writing(self):
        self._seed()
        counts = migrate_from_json.migrate(self.config, dry_run=True)
        self.assertEqual(counts, {
            "user_roles": 2,
            "pending_registrations": 1,
            "renter_profiles": 1,
            "renter_contracts": 2,
            "tickets": 1,
            "lead_captures": 1,
            "hidden_listings": 2,
        })
        # Dry-run must not create the sqlite file.
        self.assertFalse(self.config.sqlite_file.exists())

    def test_migrate_copies_every_bucket_into_sqlite(self):
        self._seed()
        migrate_from_json.migrate(self.config)

        storage = SqlStorageService(self.config)
        self.assertEqual(storage.get_user_roles(), {
            "admin@example.com": "admin",
            "renter@example.com": "renter",
        })
        pending = storage.get_pending_registrations()
        self.assertEqual([row["email"] for row in pending], ["pending@example.com"])
        self.assertEqual(storage.get_renter_profiles(), {
            "renter@example.com": {"name": "R", "contact": "555"},
        })
        contracts = storage.get_renter_contracts()
        self.assertEqual(list(contracts), ["renter@example.com"])
        self.assertEqual([c["id"] for c in contracts["renter@example.com"]], ["c1", "c2"])
        tickets = storage._load_tickets()
        self.assertEqual([t["id"] for t in tickets], ["t1"])
        leads = storage.get_pending_lead_captures()
        self.assertEqual([lead["email"] for lead in leads], ["lead@example.com"])
        # Regression: hidden listings were dropped by the migrate script
        # before the fix, so flipping USE_SQLITE_STORAGE=1 republished every
        # listing an admin had deactivated.
        self.assertEqual(
            storage.get_hidden_listing_ids(), ["prop-hidden-1", "prop-hidden-2"]
        )

    def test_missing_files_migrate_to_empty_tables(self):
        # A fresh deployment has no JSON files at all — migration must not
        # crash and every bucket must land as an empty collection.
        counts = migrate_from_json.migrate(self.config)
        self.assertEqual(counts, {
            "user_roles": 0,
            "pending_registrations": 0,
            "renter_profiles": 0,
            "renter_contracts": 0,
            "tickets": 0,
            "lead_captures": 0,
            "hidden_listings": 0,
        })
        storage = SqlStorageService(self.config)
        self.assertEqual(storage.get_user_roles(), {})
        self.assertEqual(storage.get_pending_registrations(), [])
        self.assertEqual(storage.get_hidden_listing_ids(), [])

    def test_migrate_is_idempotent(self):
        # Re-running the migration replaces the contents of each table from
        # the current JSON snapshot; hidden listings must not accumulate
        # duplicates across runs.
        self._seed()
        migrate_from_json.migrate(self.config)
        migrate_from_json.migrate(self.config)
        storage = SqlStorageService(self.config)
        self.assertEqual(
            storage.get_hidden_listing_ids(), ["prop-hidden-1", "prop-hidden-2"]
        )
        pending = storage.get_pending_registrations()
        self.assertEqual([row["email"] for row in pending], ["pending@example.com"])

    def test_malformed_shapes_treated_as_empty(self):
        # A JSON file with the wrong top-level shape (list where a dict is
        # expected, etc.) is a hand-editing mistake, not a migration halt —
        # the script coerces to an empty collection so the flip doesn't
        # crash mid-cutover.
        _write(self.config.user_roles_file, ["not", "a", "dict"])
        _write(self.config.hidden_listings_file, {"not": "a list"})
        counts = migrate_from_json.migrate(self.config)
        self.assertEqual(counts["user_roles"], 0)
        self.assertEqual(counts["hidden_listings"], 0)
        storage = SqlStorageService(self.config)
        self.assertEqual(storage.get_user_roles(), {})
        self.assertEqual(storage.get_hidden_listing_ids(), [])


if __name__ == "__main__":
    unittest.main()
