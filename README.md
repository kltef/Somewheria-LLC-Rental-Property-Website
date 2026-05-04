# Somewheria LLC Rental Property Website

A Flask web application that lists rental properties owned by Somewheria LLC. Visitors can browse listings and request viewings; renters and admins have authenticated dashboards behind Google sign-in. Property data lives in an AWS-backed API; the site fetches and renders it on demand.

## Tech stack

- **Python 3.10+** with **Flask 2.3**
- **Google OAuth** (`google-auth`, `google-auth-oauthlib`) for sign-in
- **Pillow** for image processing on uploads
- **AWS API Gateway + Lambda** as the upstream property store
- **Gmail SMTP** for transactional and crash-alert emails
- **unittest** for the test suite

## Project layout

```
somewheria_app/
  __init__.py            create_app() factory + global error handlers
  config.py              env-driven AppConfig dataclass
  routes/
    auth_routes.py       Google OAuth flow, login/logout
    public_routes.py     /, /for-rent, /property/<id>, /about, /contact, …
    admin_routes.py      add/edit/delete listings, dashboard, users, contracts
    ticket_routes.py     maintenance/issue tickets
    pwa_routes.py        manifest, service worker, offline page
  services/
    properties.py        property cache, AWS fan-out, image upload
    auth.py              session, role decorators
    notifications.py     email + log writers
    appointments.py      viewing requests
    analytics.py         per-request timing & visitor metrics
    storage.py           JSON file persistence helpers
    security.py          CSRF, rate limiting, security headers
    tickets.py           ticket service
    registry.py, console.py
templates/               Jinja2 templates (one per page)
static/                  CSS, JS, images, uploaded photos
website_app.py           entry point with interactive startup prompts
test_*.py                unittest suites
```

## Quick start

```bash
git clone https://github.com/kltef2013/Somewheria-LLC-Rental-Property-Website.git
cd Somewheria-LLC-Rental-Property-Website
pip install -r requirements.txt
# Create a .env file in the repo root with the variables described below.
python website_app.py
```

The first run asks a few startup questions (log level, request logging, cache warm-up, host, port). Defaults work for local dev. The server then listens on `http://localhost:5000`.

## Environment variables

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | recommended | random per-process | Flask session signing. Set this in production so sessions survive restarts. |
| `GOOGLE_CLIENT_ID` | required for login | — | Google OAuth client. See `GOOGLE_OAUTH_SETUP.md`. |
| `GOOGLE_CLIENT_SECRET` | required for login | — | Google OAuth secret. |
| `GOOGLE_REDIRECT_URI` | optional | `http://localhost:5000/google/callback` | OAuth callback URL. |
| `EMAIL_APP_PASSWORD` | required for email | — | Gmail app password used to send transactional and crash-alert emails. |
| `PROPERTIES_API_BASE_URL` | optional | AWS test endpoint | Override the upstream property API. |
| `AUTHORIZED_USERS` | optional | empty | Comma-separated emails granted the `renter` role. |
| `ADMIN_USERS` | optional | empty | Comma-separated emails granted `admin`. |
| `HIGH_ADMIN_USERS` | optional | empty | Comma-separated emails granted `high_admin` (full panel). |
| `FLASK_ENV` | optional | `production` | Set to `development` to relax cookie security and allow plaintext OAuth locally. |
| `DISABLE_BACKGROUND_THREADS` | optional | `0` | Set to `1` to disable any opt-in background workers (useful for tests). |
| `CONSOLE_LOG_LEVEL` | optional | `INFO` | Initial log verbosity. |

`load_dotenv()` is called at startup, so a `.env` file in the repo root is picked up automatically.

## Routes (high level)

- **Public:** `/`, `/for-rent`, `/for-rent.json`, `/property/<id>`, `/about`, `/contact`, `/privacy`, `/terms`, `/report-issue`, `/register`, `/login`, `/logout`
- **Auth (Google):** `/google/login`, `/google/callback`, `/auth/status`
- **Renter (signed in, role ≥ renter):** `/renter-dashboard`, `/renter/profile`, `/for-rent-refresh.json`, `/tickets/...`
- **Admin (`@admin_required`):** `/manage-listings`, `/add-listing`, `/edit-listing/<id>`, `/save-edit/<id>`, `/upload-image/<id>`, `/delete-listing/<id>`, `/toggle-sale/<id>`, `/admin/dashboard`, `/admin/analytics`, `/admin/users`, `/admin/registrations`, `/admin/contracts`
- **PWA:** `/manifest.webmanifest`, `/manifest.json`, `/service-worker.js`, `/offline`

Run `flask routes` (with `FLASK_APP=website_app.py`) for the full list.

## How property data flows

The app does not own the property database; it reads from the AWS API at `PROPERTIES_API_BASE_URL`.

```
visitor → /for-rent
        → PropertyService.refresh_cache()       # synchronous on every load
            ├─ GET /propertiesforrent           (list of UUIDs)
            └─ for each UUID, in a 8-worker pool:
               ├─ GET /properties/<uuid>/details
               ├─ GET /properties/<uuid>/photos      (then base64-encode each)
               └─ GET /properties/<uuid>/thumbnail
        → properties_cache (in-memory, locked)
        → render for_rent.html
```

If the upstream call fails, the view falls back to whatever is in cache and serves that — visitors don't see an error during transient AWS hiccups.

There is no longer a periodic background refresh; the cache is repopulated only when a visitor hits `/for-rent` or `/for-rent.json`, or when an admin mutation (`/add-listing`, `/save-edit/<id>`, etc.) fires `trigger_background_refresh`. This keeps AWS API Gateway / Lambda costs proportional to real traffic.

## Safe mode (crash handling)

When an unhandled exception escapes a route, the global handler in `somewheria_app/__init__.py`:

1. Returns an empty body with HTTP 503 (no template, no cache, no AWS — nothing that could fail again).
2. Emails the admin a stack trace asynchronously, rate-limited to once per 10 minutes per `(ExceptionType, path)` fingerprint so a sustained outage doesn't mailbomb the inbox.
3. Logs the exception via `app.logger.exception`.

HTTPException subclasses (401, 403, 404, 502, 503, 504) keep their dedicated template handlers and do not trigger crash emails.

This catches in-process failures only. A dead Python process or unreachable host needs an external uptime monitor (UptimeRobot, BetterStack, etc.).

## Tests

```bash
python -m unittest discover
```

Or hit individual files:

```bash
python -m unittest test_coverage_full
python -m unittest test_routes_expanded
python -m unittest test_services
```

`test_oauth.py` exercises the Google OAuth flow against a mocked client; `test_site.py` is a smoke test that spins up a client and walks the public pages.

## Branches

- **`main`** — production. Tagged releases land here.
- **`dev`** — active development. Features and fixes start here and are merged to `main` once verified.

## License

Proprietary — © Somewheria, LLC. All rights reserved.
