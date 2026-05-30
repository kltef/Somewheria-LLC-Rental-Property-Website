"""SQLite-backed storage service.

Mirrors the public API of ``FileStorageService`` so it can be swapped in via
the ``USE_SQLITE_STORAGE`` feature flag without touching any callers. The
flag is OFF by default and runtime behavior is unchanged unless explicitly
opted in.

Only the methods actually used elsewhere in the app are reimplemented here.
``load_json_file`` / ``save_json_file`` are also provided so that
``TicketService`` (which calls them directly with ``config.tickets_file``)
keeps working when handed a ``SqlStorageService``. The path argument is used
as a routing key — ``config.tickets_file`` routes to the ``tickets`` table.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .console import get_console_logger
from .database import Database, dumps, loads


class SqlStorageService:
    def __init__(self, config) -> None:
        self.config = config
        self.logger = get_console_logger("storage-sql")
        self.db = Database(config.sqlite_file)

    # ---------------------------------------------------------------- helpers

    def _path_key(self, path: Path) -> str:
        path = Path(path)
        if path == self.config.registration_file:
            return "pending_registrations"
        if path == self.config.user_roles_file:
            return "user_roles"
        if path == self.config.renter_profile_file:
            return "renter_profiles"
        if path == self.config.contracts_file:
            return "renter_contracts"
        if path == self.config.tickets_file:
            return "tickets"
        if path == self.config.lead_capture_file:
            return "lead_captures"
        return ""

    # ---------------- Generic JSON shim used by TicketService et al --------

    def load_json_file(self, path, default, *, expected_type=None):
        key = self._path_key(path)
        if not key:
            self.logger.warning("Unknown storage path %s; returning default", path)
            return default
        try:
            if key == "tickets":
                return self._load_tickets()
            if key == "user_roles":
                return self.get_user_roles()
            if key == "pending_registrations":
                return self.get_pending_registrations()
            if key == "renter_profiles":
                return self.get_renter_profiles()
            if key == "renter_contracts":
                return self.get_renter_contracts()
            if key == "lead_captures":
                return self.get_pending_lead_captures()
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error("Failed to load %s from sqlite: %s", path, exc)
        return default

    def save_json_file(self, path, data) -> None:
        key = self._path_key(path)
        if not key:
            self.logger.warning("Unknown storage path %s; ignoring write", path)
            return
        try:
            if key == "tickets":
                self._save_tickets(data)
            elif key == "user_roles":
                self._replace_user_roles(data)
            elif key == "pending_registrations":
                self._replace_pending_registrations(data)
            elif key == "renter_profiles":
                self.save_renter_profiles(data)
            elif key == "renter_contracts":
                self.save_renter_contracts(data)
            elif key == "lead_captures":
                self._replace_pending_lead_captures(data)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.error("Failed to save %s to sqlite: %s", path, exc)

    # ----------------------------------------------------- pending_registrations

    def get_pending_registrations(self) -> list[dict]:
        with self.db.read() as conn:
            rows = conn.execute("SELECT payload FROM pending_registrations").fetchall()
        return [loads(row["payload"]) for row in rows]

    def add_pending_registration(self, registration: dict) -> None:
        email = (registration.get("email") or "").strip().lower()
        if not email:
            return
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pending_registrations(email, payload) VALUES (?, ?)",
                (email, dumps(registration)),
            )

    def remove_pending_registration(self, email: str) -> None:
        email = (email or "").strip().lower()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM pending_registrations WHERE email = ?", (email,))

    def _replace_pending_registrations(self, data: list[dict]) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM pending_registrations")
            for item in data or []:
                email = (item.get("email") or "").strip().lower()
                if not email:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO pending_registrations(email, payload) VALUES (?, ?)",
                    (email, dumps(item)),
                )

    # ------------------------------------------------------------- user_roles

    def get_user_roles(self) -> dict:
        with self.db.read() as conn:
            rows = conn.execute("SELECT email, role FROM user_roles").fetchall()
        return {row["email"]: row["role"] for row in rows}

    def set_user_role(self, email: str, role: str) -> None:
        email = (email or "").lower()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_roles(email, role) VALUES (?, ?)",
                (email, role),
            )

    def delete_user_role(self, email: str) -> bool:
        email = (email or "").lower()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT role FROM user_roles WHERE email = ?", (email,)).fetchone()
            previous = row["role"] if row else None
            conn.execute(
                "INSERT OR REPLACE INTO user_roles(email, role) VALUES (?, ?)",
                (email, "revoked"),
            )
        return previous is not None and previous != "revoked"

    def _replace_user_roles(self, data: dict) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM user_roles")
            for email, role in (data or {}).items():
                conn.execute(
                    "INSERT OR REPLACE INTO user_roles(email, role) VALUES (?, ?)",
                    (str(email).lower(), str(role)),
                )

    # -------------------------------------------------------- renter_profiles

    def get_renter_profiles(self) -> dict:
        with self.db.read() as conn:
            rows = conn.execute("SELECT email, profile FROM renter_profiles").fetchall()
        return {row["email"]: loads(row["profile"]) for row in rows}

    def save_renter_profiles(self, profiles: dict) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM renter_profiles")
            for email, profile in (profiles or {}).items():
                conn.execute(
                    "INSERT OR REPLACE INTO renter_profiles(email, profile) VALUES (?, ?)",
                    (str(email).lower(), dumps(profile)),
                )

    # ------------------------------------------------------- renter_contracts

    def get_renter_contracts(self) -> dict:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT email, idx, contract FROM renter_contracts ORDER BY email, idx"
            ).fetchall()
        out: dict[str, list] = {}
        for row in rows:
            out.setdefault(row["email"], []).append(loads(row["contract"]))
        return out

    def save_renter_contracts(self, contracts: dict) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM renter_contracts")
            for email, items in (contracts or {}).items():
                email_lc = str(email).lower()
                for idx, contract in enumerate(items or []):
                    conn.execute(
                        "INSERT INTO renter_contracts(email, idx, contract) VALUES (?, ?, ?)",
                        (email_lc, idx, dumps(contract)),
                    )

    # ---------------------------------------------------------------- tickets

    def _load_tickets(self) -> list[dict]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT payload FROM tickets ORDER BY COALESCE(updated_at, created_at) DESC"
            ).fetchall()
        return [loads(row["payload"]) for row in rows]

    def _save_tickets(self, tickets: list[dict]) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM tickets")
            for ticket in tickets or []:
                tid = ticket.get("id")
                if not tid:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO tickets(id, payload, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        str(tid),
                        json.dumps(ticket),
                        ticket.get("created_at"),
                        ticket.get("updated_at"),
                    ),
                )

    # ----------------------------------------------------------- lead_captures

    def get_pending_lead_captures(self) -> list[dict]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT payload FROM lead_captures ORDER BY email"
            ).fetchall()
        return [loads(row["payload"]) for row in rows]

    def add_pending_lead_capture(self, lead: dict) -> bool:
        # Mirror FileStorageService: de-duplicate by email so a repeated
        # submission doesn't bloat the table or flood the admin UI. Returns
        # True when a row was inserted, False when the email was already
        # pending (or when no email was supplied). The route uses the return
        # value to skip the admin notification on duplicates.
        target_email = (lead.get("email") or "").strip().lower()
        if not target_email:
            return False
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT 1 FROM lead_captures WHERE email = ?", (target_email,)
            ).fetchone()
            if existing is not None:
                return False
            conn.execute(
                "INSERT INTO lead_captures(email, payload) VALUES (?, ?)",
                (target_email, dumps(lead)),
            )
        return True

    def remove_pending_lead_capture(self, email: str) -> None:
        email_lc = (email or "").strip().lower()
        if not email_lc:
            return
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM lead_captures WHERE email = ?", (email_lc,))

    def _replace_pending_lead_captures(self, data: list[dict]) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM lead_captures")
            for item in data or []:
                email = (item.get("email") or "").strip().lower()
                if not email:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO lead_captures(email, payload) VALUES (?, ?)",
                    (email, dumps(item)),
                )

    # --------------------------------------------------------------- binaries
    #
    # Binary attachments (signed-contract PDFs at
    # ``static/uploads/contracts/<uuid>.pdf`` and ticket photos at
    # ``static/uploads/tickets/<ticket_id>/``) are URL-addressed from inside
    # ticket / contract JSON payloads, so they continue to live on disk even
    # when ``USE_SQLITE_STORAGE=1``. These mirror the implementation in
    # ``FileStorageService`` (atomic temp-file + os.replace + fsync) so the
    # request handlers don't need to know which backend is wired up.

    def save_binary_file(self, path, data: bytes) -> bool:
        """Persist ``data`` to ``path`` atomically. Returns True on success."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            return True
        except Exception as exc:
            self.logger.error("Failed to save binary file %s: %s", path, exc)
            return False

    def load_binary_file(self, path) -> bytes | None:
        try:
            if not path.exists():
                return None
            with path.open("rb") as handle:
                return handle.read()
        except Exception as exc:
            self.logger.error("Failed to load binary file %s: %s", path, exc)
            return None

    def delete_file(self, path) -> bool:
        try:
            if path.exists():
                os.unlink(path)
                return True
        except Exception as exc:
            self.logger.error("Failed to delete file %s: %s", path, exc)
        return False
