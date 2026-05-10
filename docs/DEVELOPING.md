# Developing

Audience: new contributors. For project overview see [README.md](../README.md); for architecture digest see [CLAUDE.md](../CLAUDE.md). This doc covers the patterns you need to follow to keep the codebase coherent.

## Local setup beyond the README

Beyond `pip install -r requirements.txt`, a useful `.env` for development:

```
FLASK_ENV=development
SECRET_KEY=dev-only-not-for-prod
GOOGLE_CLIENT_ID=...           # from your own OAuth client; see GOOGLE_OAUTH_SETUP.md
GOOGLE_CLIENT_SECRET=...
EMAIL_APP_PASSWORD=            # leave empty in dev — emails will log a warning, not crash
AUTHORIZED_USERS=you@example.com
ADMIN_USERS=you@example.com
HIGH_ADMIN_USERS=you@example.com
DISABLE_BACKGROUND_THREADS=1   # optional; off by default
CONSOLE_LOG_LEVEL=DEBUG
```

`FLASK_ENV=development` is **required for local OAuth over plain HTTP** — it sets `OAUTHLIB_INSECURE_TRANSPORT=1` and disables `SESSION_COOKIE_SECURE`. Never set this in production.

You typically want your own email in all three role env vars so you can sign in as renter/admin/high_admin without first registering and approving yourself.

## The service-registry pattern

Routes do **not** import service modules directly. They go through a registry:

```python
from ..services.registry import get_services

def my_view():
    services = get_services()
    services.properties.refresh_cache()
```

`create_app()` (in `somewheria_app/__init__.py`) instantiates every service once, bundles them into a `Services` dataclass (`services/registry.py`), and stores it on `app.extensions["somewheria_services"]`. `get_services()` retrieves it via `current_app`.

**Why this matters:**

- Tests can substitute a service by overwriting `app.extensions["somewheria_services"]` (or one field of it) without monkey-patching imports.
- Cross-service dependencies are explicit at construction time in `create_app()`, not implicit through module-level imports.
- Adding a new dependency to a service is one line in `__init__.py`, not a refactor across every caller.

Don't reach across services by import. Always go through the registry.

## Adding a new route

Routes are not blueprints. Each `routes/*.py` module defines view functions and a `register_*_routes(app)` function called from `create_app()`.

To add a new route:

1. Pick or create the right module under `somewheria_app/routes/`. Group by audience: public, auth, admin, ticket, pwa.
2. Define the view function. Apply the right auth decorator from `services.auth`:
   - `@login_required` — any signed-in user
   - `@renter_required` — renter or above
   - `@admin_required` — admin or high_admin
   - `@high_admin_required` — high_admin only
3. Pull dependencies via `get_services()`, not module imports.
4. If the route accepts user input on POST and is public-facing, decorate with `@rate_limit(limit=N, window_seconds=S)` from `services.security`.
5. Register the URL in the module's `register_*_routes(app)`:
   ```python
   app.add_url_rule(
       "/my-route/<id>",
       endpoint="my_route",
       view_func=my_view,
       methods=["GET", "POST"],
   )
   ```
6. If `register_*_routes` is in a new module, import and call it from `create_app()` in `__init__.py`.

Conventions:

- Endpoint name matches the function name. Templates and `url_for()` calls reference the endpoint, so don't rename one without the other.
- Cap and trim user-supplied form input at the route boundary: `request.form.get("email", "").strip().lower()[:254]`. Don't trust input length.
- For mutating routes, log via `services.notifications.log_site_change(actor_email, action, extra_dict)` so the change ends up in `site_changes.log`.

## CSRF

`services/security.py` registers a global `before_request` hook that rejects any unsafe-method request (POST/PUT/PATCH/DELETE) without a valid CSRF token. Two exemptions:

- `google_callback` — Google redirects via GET so CSRF doesn't apply, and the callback validates the OAuth state parameter instead.
- `static` — Flask's static handler.

The token is injected into all templates via the `csrf_token` context processor. In a form:

```html
<input type="hidden" name="_csrf_token" value="{{ csrf_token() }}">
```

For JSON requests, send the token in the `X-CSRF-Token` header or include `_csrf_token` in the JSON body.

If you add a new POST endpoint and forget the token, the request will 400 with "CSRF token missing or invalid." This is intentional and cannot be silently bypassed except in tests (see below).

## Adding a new service

1. Create `somewheria_app/services/my_service.py` with a class. Take dependencies in `__init__` rather than reaching for globals — easier to test.
2. Add a field to `Services` in `services/registry.py`.
3. Instantiate it in `create_app()` and pass it into the `Services(...)` constructor.
4. Use it from routes via `services = get_services(); services.my_service.do_thing()`.

Do not import other services at module-import time — get them through the registry inside the method that needs them. This avoids initialization-order coupling.

## Persistence

There is no database. State lives in JSON files in the repo root (paths defined in `AppConfig.__post_init__`):

- `user_roles.json` — `{email: role}`. `"revoked"` is a tombstone, not a deletion.
- `pending_registrations.json` — list of pending registration requests.
- `renter_profiles.json` — `{email: profile_dict}`.
- `renter_contracts.json` — `{email: [contract_dict, ...]}`.
- `tickets.json` — list of ticket dicts. Managed by `TicketService`.
- `property_appointments.txt` — append-only plain text, viewing requests.
- `application.log`, `site_changes.log` — log files.

All JSON reads/writes go through `FileStorageService`, which:

- Uses a single `threading.Lock` to serialize file access (single-process model, in-memory lock — won't help across multiple workers).
- Writes via `tempfile.mkstemp` + `os.replace` for atomicity (no half-written files).
- Validates the loaded shape against `expected_type` and falls back to `default` if a hand-edited file is the wrong shape.

**Don't add a new persistence path that bypasses `FileStorageService`.** If you need a new file, add a load/save method on `FileStorageService` and a path field on `AppConfig`.

If you ever need to scale beyond a single process, this whole layer needs to move to a real database — the in-process locks won't coordinate across workers.

## Properties cache

`PropertyService` (`services/properties.py`) holds the property cache in memory. Read it via `get_cached_properties()` (returns a deep copy) or `get_property(id)`. Refresh it via `refresh_cache()`.

The cache is repopulated on:

- Every `/for-rent` and `/for-rent.json` request (synchronous, blocking).
- Admin mutations (`create_property`, `update_property`, `delete_property`, `toggle_sale`, `upload_image`) call `trigger_background_refresh` after success.

There is **no periodic background refresh**. Don't reintroduce one without a discussion — the on-demand model is deliberate to keep AWS API Gateway / Lambda costs bounded.

If your route reads property data, prefer `get_cached_properties()` / `get_property()` over forcing a refresh. Only call `refresh_cache()` after a write, and prefer `trigger_background_refresh` if it's available.

## Crash safety contract

The global `Exception` handler in `create_app()` returns a bare 503 and emails an alert. To preserve that contract:

- Don't add work into the crash path that itself depends on AWS, the cache, templates, or anything that could fail again. The handler must be allocation-light and dependency-free.
- Don't catch and re-raise as a generic `Exception` in routes if you can return a proper 4xx/5xx via `abort()` instead. `HTTPException` subclasses bypass the crash handler and use templated error pages.
- The 10-minute per-fingerprint email rate-limit is intentional. If you change the cooldown, update the runbook.

## Tests

```bash
python -m unittest discover                          # all
python -m unittest test_services                     # one module
python -m unittest test_services.TestPropertyService.test_refresh_cache  # one test
```

Patterns used in the existing suite:

- **Swap a service**: build the app with `create_app()`, then `app.extensions["somewheria_services"].properties = FakePropertyService(...)`. The route code goes through `get_services()` so it picks up the swap automatically.
- **Bypass CSRF**: set `app.config["TESTING"] = True`. The CSRF before-request hook short-circuits when `TESTING` is set, mirroring Flask-WTF's behavior.
- **Bypass rate limits**: same — `TESTING=True` makes `@rate_limit` a no-op.
- **Disable background work**: set `DISABLE_BACKGROUND_THREADS=1` in the environment before constructing the app, or set `app.config["DISABLE_BACKGROUND_THREADS"] = True` after.
- **Email side-effects**: don't set `EMAIL_APP_PASSWORD` in the test environment. `NotificationService.send_email` will log a warning and return `False` instead of trying SMTP.

The existing test files are good examples:

- `test_routes_expanded.py` — route-level coverage with the test client.
- `test_services.py` — service unit tests.
- `test_oauth.py` — OAuth flow with a mocked Google client.
- `test_site.py` — smoke test walking the public pages.
- `test_coverage_full.py` — broader coverage.
- `test_startup.py` — startup-prompt and bootstrap behavior.
- `test_generated_matrices.py` — parametric / table-driven tests.

## Frontend / Tailwind

CSS is built from `static/css/input.css` to `static/css/tailwind.css` via Tailwind. Two scripts:

```bash
npm run build:css     # one-off minified build
npm run watch:css     # rebuild on change
```

The minified `tailwind.css` is committed; rebuild and commit it when you change `input.css` or any class string in templates that Tailwind needs to detect. The deployed app does not run `npm` at boot.

Templates are Jinja2 under `templates/`. Each route renders one template; there is no template inheritance scheme beyond a base layout. When adding a page, copy a similar existing template and adjust.

## Sequencing for a typical change

A change that adds, say, a new admin page typically touches:

1. `somewheria_app/routes/admin_routes.py` — view function + decorator + `add_url_rule` in `register_admin_routes`.
2. `templates/your_new_page.html` — Jinja template.
3. Possibly `somewheria_app/services/<some_service>.py` if you need new business logic — keep it in a service, not in the route.
4. `test_routes_expanded.py` and/or `test_services.py` — coverage.
5. CSS / template tweaks may require `npm run build:css`.

If your change adds a new persisted file, also touch:

6. `AppConfig.__post_init__` for the path.
7. `FileStorageService` for load/save methods.
8. The runbook (state-on-disk section).

## Things to avoid

- Importing services directly into routes (use `get_services()`).
- Bypassing `FileStorageService` for new persistence.
- Introducing a periodic background thread for cache refresh (cost decision).
- Using `print()` for logging — use `get_console_logger("component-name")`.
- Trusting `request.form` length without `[:N]` slicing at the boundary.
- Adding work into the crash handler that can itself fail.
- Skipping CSRF on a new POST endpoint.
