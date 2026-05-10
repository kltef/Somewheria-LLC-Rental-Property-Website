# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

```bash
# Install
pip install -r requirements.txt
npm install                       # only needed if rebuilding Tailwind CSS

# Run the dev server (interactive prompts on first start)
python website_app.py

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

**Routes are registered, not blueprinted.** Each `routes/*.py` module exports a `register_*_routes(app)` function called from `create_app()`. There are no Flask blueprints. Role gating uses decorators from `services/auth.py` (`@admin_required`, etc.) — apply them rather than re-checking `session` inline.

**Property data lives upstream.** The site does not own a property DB. `PropertyService` (`services/properties.py`) fans out to the AWS API at `PROPERTIES_API_BASE_URL` with an 8-worker pool: list UUIDs → per-property `details` + `photos` (base64-encoded) + `thumbnail` → in-memory `properties_cache` behind a lock. The cache is refreshed **synchronously on every `/for-rent` and `/for-rent.json` hit** and after any admin mutation (`trigger_background_refresh`); there is no periodic refresher. If the upstream call fails, views fall back to whatever's in cache rather than erroring — preserve that behavior when editing the cache path.

**Crash safety.** `create_app()` registers a global `Exception` handler that returns an empty `503` body (no template, no DB, no AWS — nothing that can fail again) and emails a stack trace asynchronously, rate-limited to one email per 10 min per `(ExceptionType, path)` fingerprint. `HTTPException` subclasses bypass this and use the per-status template handlers. Don't add work into the crash path that itself depends on AWS, the cache, or rendering.

**Persistence is JSON files, not a DB.** `FileStorageService` reads/writes `pending_registrations.json`, `user_roles.json`, `renter_profiles.json`, `renter_contracts.json`, `tickets.json`, plus the `property_appointments.txt` log — all paths defined in `AppConfig.__post_init__`. Treat these as the source of truth for user/registration/ticket state.

**Roles** are CSV env vars: `AUTHORIZED_USERS` → renter, `ADMIN_USERS` → admin, `HIGH_ADMIN_USERS` → full panel. They're loaded once at config construction; restart the app after editing `.env`.

**Security middleware** (`services/security.py`) registers CSRF and security headers in `create_app()`. Mutating routes need a CSRF token from the session helper — don't add new POST/PUT/DELETE handlers without it.

**Browser support.** Supported matrix and the human QA checklist live in [`docs/BROWSER_SUPPORT.md`](docs/BROWSER_SUPPORT.md). We target latest + N-1 of Safari (macOS/iOS), Chrome, Edge, Samsung Internet, and Firefox; Internet Explorer is explicitly unsupported. Any change to `templates/base.html`, inline JS, the service worker, or Tailwind input warrants re-running the checklist.

## Branches

`main` is production (tagged releases). `dev` is where features land first and get verified before promotion to `main`.
