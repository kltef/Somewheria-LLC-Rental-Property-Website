"""Migrate JSON storage files into the SQLite database.

Reads the JSON files referenced by ``AppConfig`` and writes them to the SQLite
database at ``config.sqlite_file``. Idempotent: re-running it replaces the
contents of each table from the current JSON snapshot. The JSON files
themselves are NOT touched — they remain the live source of truth until
``USE_SQLITE_STORAGE=1`` is set.

Usage:

    python scripts/migrate_from_json.py            # use default base dir
    python scripts/migrate_from_json.py --dry-run  # report counts only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# Allow running as a plain script: ``python scripts/migrate_from_json.py``.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from somewheria_app.config import AppConfig  # noqa: E402
from somewheria_app.services.sql_storage import SqlStorageService  # noqa: E402


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        print(f"WARN: could not read {path}: {exc}")
        return default


def migrate(config: AppConfig, *, dry_run: bool = False) -> dict:
    roles = _read_json(config.user_roles_file, {})
    pending = _read_json(config.registration_file, [])
    profiles = _read_json(config.renter_profile_file, {})
    contracts = _read_json(config.contracts_file, {})
    tickets = _read_json(config.tickets_file, [])

    counts = {
        "user_roles": len(roles) if isinstance(roles, dict) else 0,
        "pending_registrations": len(pending) if isinstance(pending, list) else 0,
        "renter_profiles": len(profiles) if isinstance(profiles, dict) else 0,
        "renter_contracts": sum(
            len(v) for v in (contracts.values() if isinstance(contracts, dict) else [])
        ),
        "tickets": len(tickets) if isinstance(tickets, list) else 0,
    }

    if dry_run:
        return counts

    storage = SqlStorageService(config)
    storage._replace_user_roles(roles if isinstance(roles, dict) else {})
    storage._replace_pending_registrations(pending if isinstance(pending, list) else [])
    storage.save_renter_profiles(profiles if isinstance(profiles, dict) else {})
    storage.save_renter_contracts(contracts if isinstance(contracts, dict) else {})
    storage._save_tickets(tickets if isinstance(tickets, list) else [])
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read the JSON files and print row counts without writing to SQLite.",
    )
    args = parser.parse_args(argv)

    config = AppConfig()
    print(f"SQLite target: {config.sqlite_file}")
    counts = migrate(config, dry_run=args.dry_run)
    label = "would migrate" if args.dry_run else "migrated"
    for table, n in counts.items():
        print(f"  {label} {n} rows -> {table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
