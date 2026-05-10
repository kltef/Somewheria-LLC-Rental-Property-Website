---
description: Generate or update documentation for this Flask app (runbook, admin guide, or developer docs)
argument-hint: [runbook|admin-guide|dev|all]
---

You are generating documentation for the Somewheria LLC Rental Property Website. The argument `$1` selects which doc to produce. If no argument is given, ask the user which one before doing anything.

Valid values for `$1`:

- `runbook` → write/update `docs/RUNBOOK.md`. Audience: ops / on-call. Cover: the bare-503 crash handler and the rate-limited alert email (see `somewheria_app/__init__.py`), what to do when the AWS properties API at `PROPERTIES_API_BASE_URL` is down (cache fallback behavior in `services/properties.py`), how to rotate `GOOGLE_CLIENT_SECRET` and `EMAIL_APP_PASSWORD`, where logs live (`application.log`, `site_changes.log`), how to read `property_appointments.txt`, and how to restart safely (sessions survive only if `SECRET_KEY` is set).

- `admin-guide` → write/update `docs/ADMIN_GUIDE.md`. Audience: site admins / high-admins (non-technical). Task-oriented: how to add a listing, edit a listing, upload photos, toggle for-sale, approve a pending registration, manage user roles, view analytics, manage contracts, respond to tickets. Derive the actual flows from `routes/admin_routes.py` and `routes/ticket_routes.py` and the matching templates. Use plain language, no code. Note where screenshots would help but do not invent them.

- `dev` → write/update `docs/DEVELOPING.md`. Audience: new contributors. Cover: local setup beyond what README has, the service-registry pattern and why routes use `get_services()` instead of imports, how to add a new route + register it, how to add a new service to the `Services` dataclass, the CSRF requirement for mutating endpoints, the JSON-file persistence model, and how to write a test that swaps services. Cross-reference `CLAUDE.md` rather than duplicating it.

- `all` → produce all three.

## Process

1. Read `README.md`, `CLAUDE.md`, and the source files relevant to the selected doc before writing. Do not write from memory.
2. Create the `docs/` folder if it doesn't exist.
3. If the target file already exists, read it first and update in place rather than overwriting wholesale. Preserve any human-added sections.
4. After writing, list the file(s) you created or modified and call out anything you couldn't document because it requires information not in the codebase (e.g., production hostnames, on-call rotation, who holds the OAuth client).

## Style

- Markdown. No emojis unless already present in the existing file.
- Concrete file paths and route names (`/manage-listings`, `services/properties.py:refresh_cache`) over hand-waving.
- Don't restate what's already in `README.md` or `CLAUDE.md` — link to them.
- For the admin guide specifically: write for someone who has never seen the codebase. Steps, not concepts.
