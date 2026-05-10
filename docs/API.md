# HTTP API Reference

Complete reference for every HTTP endpoint exposed by the app. Generated from the route registrations in `somewheria_app/routes/`. For architectural context see [DEVELOPING.md](DEVELOPING.md).

## Conventions

- **Auth** column shows the minimum role required:
  - `public` — no sign-in required.
  - `any` — any signed-in user (`@login_required`).
  - `renter+` — renter, admin, or high_admin (`@renter_required`).
  - `admin+` — admin or high_admin (`@admin_required`).
  - `high_admin` — high_admin only (`@high_admin_required`).
- **CSRF**: every unsafe-method request (POST/PUT/PATCH/DELETE) requires a valid `_csrf_token` from the session, sent either as the form field `_csrf_token`, the JSON field `_csrf_token`, or the `X-CSRF-Token` header. The only exemptions are `/google/callback` and `/static/...`. See [DEVELOPING.md § CSRF](DEVELOPING.md#csrf).
- **Rate limits** are noted per-endpoint and enforced per `(endpoint, client_ip)` in-process. They do not coordinate across multiple workers.
- **Content type**: `text/html` unless noted. Endpoints whose path ends in `.json` or that explicitly note JSON in the response column return `application/json`.

## Public

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/` | public | Home page. |
| GET | `/about` | public | About page. |
| GET | `/contact` | public | Contact page. |
| GET | `/for-rent` | public | Listings page. **Side effect:** synchronously refreshes the property cache from the upstream AWS API on every request. Falls back to cache contents on upstream failure. |
| GET | `/for-rent.json` | public | JSON list of all cached properties (after the same synchronous refresh as `/for-rent`). |
| GET | `/property/<uuid>` | public | Property detail page. 404 if `uuid` is not in the cache. |
| POST | `/property/<uuid>/schedule` | public | Submit a viewing-appointment request for property `uuid`. JSON body, see below. Rate-limited 5 / 10 min per IP. |
| GET | `/report-issue` | public | Issue-report form. |
| POST | `/report-issue` | public | Submit a site-issue report. Form fields: `name` (≤120 char), `description` (≤4000 char). Rate-limited 3 / 10 min per IP. Sends an email to the admin recipient. |
| GET | `/report-issue-complete` | public | Confirmation page after submitting an issue. |
| GET | `/register` | public | Account-request form (renter access request). |
| POST | `/register` | public | Submit a registration request. Form fields: `name` (≤120 char), `email` (≤254 char, must contain `@`), `reason` (≤2000 char). Duplicate emails do not re-notify (SMTP abuse protection). Rate-limited 3 / 10 min per IP. |

### `POST /property/<uuid>/schedule` — request body

```json
{
  "name":           "string, required, ≤120 chars",
  "date":           "YYYY-MM-DD, required, must be today or later",
  "contact_method": "one of: email | phone | text | sms | call (optional)",
  "contact_info":   "string, required, ≤200 chars",
  "_csrf_token":    "session CSRF token"
}
```

Responses:

- `200 {"success": true}` — request recorded; admin notified by email.
- `400 {"success": false, "error": "..."}` — missing/invalid fields, invalid date, past date.
- `404 {"success": false, "error": "Property not found."}` — `uuid` not in upstream property store.

## Authentication

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/login` | public | Login page. Redirects to `/manage-listings` if already signed in. |
| POST | `/login` | public | No-op (always redirects to `/manage-listings`). Real auth happens via Google. |
| GET | `/google/login` | public | Begins the OAuth flow. Redirects to Google's auth URL. Returns a 503 page if `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` are unconfigured. |
| GET | `/google/callback` | public | Google OAuth redirect target. **CSRF-exempt** (validates the OAuth `state` parameter instead). On success, sets the session and redirects to `/manage-listings`. |
| GET | `/logout` | public | Clears the user from the session and redirects to `/`. |
| GET | `/auth/status` | public | Returns JSON `{authenticated: bool, user: {email, name, role} \| null}`. |

### Google OAuth gates

The callback enforces three gates in order:

1. **Domain gate**: email must end in `@ekbergproperties.com` or `@somewheria.com`. Other domains receive a 401 login error.
2. **Whitelist gate** (only if `AUTHORIZED_USERS` is configured): if the email is not on the whitelist *and* has no role from any source, access is denied and an alert email is sent.
3. **Role gate**: if the resolved role is `guest` (no role anywhere), access is denied with "Please request an account."

On success the session is cleared (defense against session fixation) and re-populated with the user record. Login is recorded by `analytics`.

## Renter / signed-in

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/manage-listings` | any | Listings management page. The page itself uses cached property data. Note: the route only requires sign-in; admin-only buttons inside the template are gated by role checks in the template/JS. |
| GET | `/renter-dashboard` | renter+ | Renter's dashboard with their contracts and recent tickets. |
| GET | `/renter/profile` | renter+ | View renter profile. |
| POST | `/renter/profile` | renter+ | Update renter profile. Form: `name` (≤120 char), `contact` (≤200 char), `email_status_updates` (checkbox). |
| GET | `/for-rent-refresh.json` | public¹ | Force-refresh the property cache and return the JSON list. Rate-limited 6 / minute per IP for both GET and POST. |

¹ The route is registered without an auth decorator but the rate limit deters abuse. It triggers an upstream refresh on every call within the limit.

## Admin

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/add-listing` | admin+ | Form to create a new listing. |
| GET | `/edit-listing/<property_id>` | admin+ | Form to edit an existing listing. 404 if the id isn't in the cache. |
| POST | `/save-edit/<id>` | admin+ | Save a new (`id == "new"`) or existing listing. Form fields match the `BLANK_PROPERTY` schema in `services/properties.py`. Redirects to `/manage-listings` on success; returns plain-text error with status code on failure. |
| POST | `/upload-image/<uuid>` | admin+ | Upload a single image for property `uuid`. Multipart/form-data with field name `file`. Allowed extensions: jpg, jpeg, png, gif, webp. Max 16 MB; max 24 MP decoded; max 6000px per side. Returns `{success: bool, new_image_url?: str, message?: str}`. |
| POST | `/image-edit-notify` | admin+ | Notify admin recipient that an image edit occurred. Empty body. Does **not** accept client-supplied URLs (intentional — prevents exfiltration / spam amplification). |
| POST | `/delete-listing/<id>` | admin+ | Delete the listing `id` from the upstream property store. Redirects to `/manage-listings`. |
| POST | `/toggle-sale/<id>` | admin+ | Toggle the for-sale flag on listing `id`. Redirects to `/manage-listings`. |
| GET | `/admin/registrations` | admin+ | List of pending registration requests. |
| POST | `/admin/registrations` | admin+ | Approve or reject a request. Form: `action` (`approve` \| `reject`), `email`. Approve grants the `renter` role and emails the requester; reject removes the entry and emails a polite rejection. |
| GET | `/admin/users` | admin+ | User-management page. |
| POST | `/admin/users` | admin+ | Add, update, or delete a user. Form: `email`, `role` (`renter` \| `admin` \| `high_admin`), `action` (`delete` or omitted). Constraint: cannot modify your own account; cannot assign or modify a role at or above your own. Delete writes a `"revoked"` tombstone (see [RUNBOOK.md § Roles and access](RUNBOOK.md#roles-and-access)). |
| GET | `/admin/contracts` | admin+ | List all renter contracts. |
| POST | `/admin/contracts` | admin+ | Add or delete a contract. Form (`action=add`): `renter_email`, `property_name`, `start_date`, `end_date`, `status` (default `Active`). Form (`action=delete`): `renter_email`, `contract_index` (integer). |
| GET | `/admin/contracts/export.csv` | admin+ | Streamed CSV of every contract. Columns: `renter_email`, `property_name`, `start_date`, `end_date`, `status`, `created_at`. `Content-Type: text/csv`, `Content-Disposition: attachment; filename="contracts-YYYY-MM-DD.csv"`. |

## High admin

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/admin/dashboard` | high_admin | Combined dashboard: metrics + chart data + ticket summary + inline user-management. |
| POST | `/admin/dashboard` | high_admin | Same user-management actions as `/admin/users` (`add`, `update`, `delete`). Also writes to `site_changes.log`. |
| GET | `/admin/analytics` | high_admin | Request-timing and visitor metrics over the last 7 days. |
| GET | `/admin/status` | high_admin | System status page: cached property count, configured-service indicators, file presence. |
| GET | `/logs` | high_admin | View the last 500 entries of `application.log` rendered as a table. |

## Repair tickets

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/tickets/new` | public | Submit-ticket form. Pre-fills `property_id` from `?property_id=...`. |
| POST | `/tickets/new` | public | Create a ticket. Form fields: `title`, `description`, `category` (one of `ALLOWED_CATEGORIES`), `priority` (one of `ALLOWED_PRIORITIES`), `submitter_name`, `contact`, `property_id` (optional), `email_updates` (checkbox). Returns 400 with the form on validation error. Rate-limited 5 / 10 min per IP. |
| GET | `/tickets` | any | The signed-in user's own ticket list. |
| GET | `/tickets/<ticket_id>` | any² | Ticket detail page. Renters see only their own; admin+ see any. |
| POST | `/tickets/<ticket_id>/notes` | any² | Add a note to the ticket. Form: `note`. Empty notes are silently dropped. Rate-limited 20 / 5 min per IP. |
| POST | `/tickets/<ticket_id>/email-updates` | any² | Toggle the per-ticket email-updates preference. Form: `email_updates` (checkbox). |
| GET | `/admin/tickets` | admin+ | All tickets. Query params: `status` (one of `ALLOWED_STATUSES`), `priority` (one of `ALLOWED_PRIORITIES`), `q` (text search across title/description/submitter/property name). |
| GET | `/admin/tickets/export.csv` | admin+ | Streamed CSV of every ticket (no filtering). Columns: `id`, `title`, `status`, `priority`, `category`, `submitter`, `property_name`, `created_at`, `last_updated`. `Content-Type: text/csv`, `Content-Disposition: attachment; filename="tickets-YYYY-MM-DD.csv"`. |
| POST | `/admin/tickets/<ticket_id>` | admin+ | Update ticket. Form: any of `status`, `priority`, `assigned_to` (free-text email). Only fields present in the form are applied. |

² Per-ticket access: signed in **and** either an admin/high_admin **or** the ticket's submitter.

`ALLOWED_CATEGORIES`, `ALLOWED_PRIORITIES`, `ALLOWED_STATUSES`, and `OPEN_STATUSES` are defined in `somewheria_app/services/tickets.py`.

## PWA

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/manifest.webmanifest` | public | Web app manifest. Content-Type: `application/manifest+json`. |
| GET | `/manifest.json` | public | Same file as `/manifest.webmanifest` (alias). |
| GET | `/service-worker.js` | public | Service worker. `Cache-Control: no-cache`. |
| GET | `/offline` | public | Offline fallback page rendered by the service worker. |

## Static

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/static/<path>` | public | Flask's built-in static handler. CSRF-exempt. Serves files from `static/`. |

## Error pages

| Status | Trigger | Response |
|---|---|---|
| 401 | `abort(401)` | Renders `401.html`. |
| 403 | `abort(403)` (e.g. role gate failure) | Renders `403.html`. |
| 404 | Unknown route or `abort(404)` | Renders `404.html`. |
| 502 | `abort(502)` | Renders `502.html`. |
| 503 | `abort(503)` | Renders `503.html`. |
| 503 (bare) | Any unhandled `Exception` escaping a route | **Empty body**, `Content-Type: text/plain`, `Content-Length: 0`. Stack trace emailed to admin (rate-limited 1 / 10 min per `(ExceptionType, path)`). See [RUNBOOK.md § Crash handling](RUNBOOK.md#crash-handling-—-what-visitors-see-and-what-you-get). |
| 504 | `abort(504)` | Renders `504.html`. |
| 400 (CSRF) | Unsafe method without valid `_csrf_token` | Plain-text "CSRF token missing or invalid." |
| 429 | Rate limit hit | JSON `{error: "..."}` if the client accepts JSON, else plain text. |

## Headers set on every response

`services/security.py` adds these via `setdefault` (a fronting proxy can override):

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), microphone=(), camera=()
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'
                         https://cdn.tailwindcss.com https://accounts.google.com
                         https://apis.google.com; style-src 'self' 'unsafe-inline'
                         https://fonts.googleapis.com https://cdn.tailwindcss.com;
                         font-src 'self' https://fonts.gstatic.com data:;
                         img-src 'self' data: https:;
                         connect-src 'self' https://accounts.google.com;
                         frame-src https://accounts.google.com;
                         frame-ancestors 'none'; base-uri 'self';
                         form-action 'self' https://accounts.google.com
Strict-Transport-Security: max-age=31536000; includeSubDomains   (only when request.is_secure)
```

Session cookies: `HttpOnly`, `SameSite=Lax`, `Secure` (unless `FLASK_ENV=development`), 8-hour lifetime, max body size 16 MB.

## How to discover routes at runtime

```bash
FLASK_APP=website_app.py flask routes
```

Lists every registered URL rule with endpoint and methods. Useful when adding a new route to confirm it's wired up.
