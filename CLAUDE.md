# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

```bash
# Install
pip install -r requirements.txt
npm install                       # only needed if rebuilding Tailwind CSS

# Run the dev server (interactive prompts on first start)
python website_app.py

# Lint
pip install -r requirements-dev.txt
ruff check .
ruff format .

# Tests
python -m unittest discover                       # full suite
python -m unittest test_routes_expanded           # one file
python -m unittest test_services.TestPropertyService.test_refresh_cache   # one test

# Tailwind (only if editing static/css/input.css)
npm run build:css     # one-off minified build
npm run watch:css     # rebuild on change

# Manage the AUTHORIZED_USERS list in .env
python manage_users.py
```

`website_app.py` prompts for log level / request logging / cache warm-up / host / port at startup. When stdin is not a TTY (CI, supervisord, Docker), it skips prompts and reads `LOG_LEVEL`, `HOST`, `PORT` from env, defaulting to `0.0.0.0:5000`. Set `DISABLE_BACKGROUND_THREADS=1` in tests to keep async work out of the test process.

`FLASK_ENV=development` enables `OAUTHLIB_INSECURE_TRANSPORT=1` and disables `SESSION_COOKIE_SECURE`, so OAuth works over plain `http://localhost`. Production must leave `FLASK_ENV` unset (or set to `production`).

## Architecture

**App factory + service registry.** `somewheria_app/__init__.py:create_app()` is the single entry point. It builds an `AppConfig` (all env-driven, see `config.py`), instantiates every service, bundles them into a `Services` dataclass, and stashes it on `app.extensions["somewheria_services"]`. Routes pull dependencies via `get_services()` from `services/registry.py` rather than importing service modules directly — this is the seam tests use to swap services. Don't reach across services by import; go through the registry.

**Routes are registered, not blueprinted.** Each `routes/*.py` module exports a `register_*_routes(app)` function called from `create_app()`. There are no Flask blueprints. Role gating uses decorators from `services/auth.py` (`@admin_required`, `@high_admin_required`, etc.) — apply them rather than re-checking `session` inline.

**Property data lives upstream.** The site does not own a property DB. `PropertyService` (`services/properties.py`) fans out to the AWS API at `PROPERTIES_API_BASE_URL` with an 8-worker pool: list UUIDs → per-property `details` + `photos` (base64-encoded) + `thumbnail` → in-memory `properties_cache` behind a lock. The cache is refreshed **synchronously on every `/for-rent` and `/for-rent.json` hit** and after any admin mutation (`trigger_background_refresh`); there is no periodic refresher. Concurrent hits within 2 seconds of the last refresh piggyback on the warm cache to avoid redundant upstream calls. If the upstream call fails, views fall back to whatever's in cache rather than erroring — preserve that behavior when editing the cache path.

**Crash safety.** `create_app()` registers a global `Exception` handler that returns an empty `503` body (no template, no DB, no AWS — nothing that can fail again) and emails a stack trace asynchronously, rate-limited to one email per 10 min per `(ExceptionType, path)` fingerprint. `HTTPException` subclasses bypass this and use the per-status template handlers. Don't add work into the crash path that itself depends on AWS, the cache, or rendering.

**Persistence is JSON files, not a DB.** `FileStorageService` reads/writes `pending_registrations.json`, `user_roles.json`, `renter_profiles.json`, `renter_contracts.json`, `tickets.json`, `pending_lead_captures.json`, plus the `property_appointments.txt` log — all paths defined in `AppConfig.__post_init__`. Treat these as the source of truth for user/registration/ticket state. All writes use tempfile + `os.replace()` (atomic, crash-safe). Binary attachments (signed-contract PDFs at `static/uploads/contracts/<uuid>.pdf`, ticket photos at `static/uploads/tickets/<ticket_id>/`) also go through `FileStorageService` (`save_binary_file` / `load_binary_file` / `delete_file`) — don't bypass it with raw `open()`. A single `file_lock` serializes all I/O; this is fine for single-worker but would need a distributed lock for multi-worker deployments.

**Roles** are a four-level hierarchy: `guest < renter < admin < high_admin`. `AUTHORIZED_USERS` → renter, `ADMIN_USERS` → admin, `HIGH_ADMIN_USERS` → full panel (CSV env vars, loaded once at startup). Role lookup checks `user_roles.json` first, then falls back to env vars. Deleted users are tombstoned in `user_roles.json` as `"revoked"` to prevent env-var re-grant after removal. Admins can only act on lower-ranked users; only `high_admin` can promote.

**Security middleware** (`services/security.py`) registers CSRF and security headers in `create_app()`. Mutating routes need a CSRF token from the session helper — don't add new POST/PUT/DELETE handlers without it. CSRF is exempt for `google_callback`, `static`, and `jira_webhook`. The `Content-Security-Policy` is tight; if adding an external resource (iframe, script, font), you must add it to the `CSP` in `security.py`. Rate limiting uses `@rate_limit(limit=N, window_seconds=S)` from `security.py` — apply to any new public-facing form endpoints.

**Browser support.** Supported matrix and the human QA checklist live in [`docs/BROWSER_SUPPORT.md`](docs/BROWSER_SUPPORT.md). We target latest + N-1 of Safari (macOS/iOS), Chrome, Edge, Samsung Internet, and Firefox; Internet Explorer is explicitly unsupported. Any change to `templates/base.html`, inline JS, the service worker, or Tailwind input warrants re-running the checklist.

## Services overview

| Service | File | Purpose |
|---|---|---|
| `PropertyService` | `services/properties.py` | AWS fan-out, in-memory cache, image upload/resize |
| `FileStorageService` | `services/storage.py` | Atomic JSON + binary file I/O |
| `AuthService` | `services/auth.py` | Session, role lookup, decorators |
| `TicketService` | `services/tickets.py` | Repair ticket CRUD + photo attachments |
| `NotificationService` | `services/notifications.py` | Gmail SMTP, audit log (`site_changes.log`) |
| `SecurityService` | `services/security.py` | CSRF, rate limiting, security headers |
| `AnalyticsTracker` | `services/analytics.py` | Per-request timing, daily visit/error counters |
| `AppointmentService` | `services/appointments.py` | Booked-dates file I/O for the date picker |
| `JiraClient` | `services/jira.py` | **No-op stub** — no-ops until `JIRA_*` env vars are set |
| `ZillowPublisher` | `services/zillow.py` | **No-op stub** — no-ops until Zillow credentials are set |
| `SqlStorageService` | `services/sql_storage.py` | SQLite alternative, enabled via `USE_SQLITE_STORAGE=1` |

`JiraClient` and `ZillowPublisher` silently log a warning at boot when unconfigured and never raise — admin operations must never block on their availability.

## Key conventions

**Input sanitization at route boundary.** Strip, lowercase, and cap lengths on all user-supplied strings before passing to services:
```python
email = request.form.get("email", "").strip().lower()[:254]
```

**Audit logging for mutations.** Every admin action that changes persistent state must call:
```python
services.notifications.log_site_change(actor_email, action, extra_dict)
```
This appends a JSON line to `site_changes.log`, which feeds the analytics dashboard.

**Property ID validation.** IDs are validated with `^[A-Za-z0-9_-]{1,64}$` before any upstream call. Tour URLs are whitelisted to `http`/`https` only. Image uploads are defended against decompression bombs (Pillow `MAX_IMAGE_PIXELS` tightened) and capped at 16 MB / 24 MP.

**Tests.** All 8 test files use `unittest` (no pytest). All set `DISABLE_BACKGROUND_THREADS=1`. Tests swap services by replacing `app.extensions["somewheria_services"]` before each request — don't tighten access to that key.

## Key env vars

| Var | Required | Notes |
|---|---|---|
| `SECRET_KEY` | Production | Random per-process if unset — **must be set in production** to survive restarts |
| `FLASK_ENV` | Dev only | Set to `development` for plaintext OAuth; unset in production |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | Yes | OAuth |
| `PROPERTIES_API_BASE_URL` | Yes | Upstream AWS endpoint |
| `AUTHORIZED_USERS` / `ADMIN_USERS` / `HIGH_ADMIN_USERS` | Yes | CSV role lists |
| `EMAIL_APP_PASSWORD` | Optional | Gmail app password; emails silently fail if unset |
| `USE_SQLITE_STORAGE` | Optional | `1` to enable SQLite backend |
| `DISABLE_BACKGROUND_THREADS` | Test only | Keeps async workers out of test process |
| `JIRA_BASE_URL` / `JIRA_API_TOKEN` / `JIRA_WEBHOOK_SECRET` | Optional | JIRA integration |

## Branches

`main` is production (tagged releases). `dev` is where features land first and get verified before promotion to `main`.
