# Operations Runbook

Audience: ops / on-call. For app architecture see [CLAUDE.md](../CLAUDE.md); for setup see [README.md](../README.md).

## At a glance

- **Process:** single Python process, `python website_app.py`. Default bind: `0.0.0.0:5000`.
- **State on disk:** JSON files in the repo root (`user_roles.json`, `pending_registrations.json`, `renter_profiles.json`, `renter_contracts.json`, `tickets.json`) plus `application.log`, `site_changes.log`, `property_appointments.txt`, and uploaded images under `static/uploads/`. Back these up together — they are the entire app state.
- **Upstream:** AWS API at `PROPERTIES_API_BASE_URL` (default: `https://7pdnexz05a.execute-api.us-east-1.amazonaws.com/test`). All property data lives there.
- **Outbound email:** Gmail SMTP (`smtp.gmail.com:587`) using `EMAIL_APP_PASSWORD`. Sender: `anthony.j.ekberg@gmail.com`. Default recipient: `anthony@ekbergproperties.com`.
- **Health page:** `/admin/status` (high_admin only) — shows cached property count, file presence, and which subsystems are configured.

## Crash handling — what visitors see and what you get

A handler in `somewheria_app/__init__.py` catches any unhandled exception escaping a route:

1. Visitor receives an **empty body with HTTP 503**. No template, no DB, no AWS call — nothing that could fail again.
2. A stack trace is emailed asynchronously to the admin recipient.
3. The exception is logged via `app.logger.exception` to `application.log`.

**Email rate limit:** one email per 10 minutes per `(ExceptionType, request_path)` fingerprint. A sustained outage will produce one email per unique error/path combination per 10 min, not one per request.

`HTTPException` subclasses (401/403/404/502/503/504) bypass this and use the per-status template handlers in `__init__.py` — they do **not** trigger crash emails.

This handler only catches in-process failures. A dead Python process or unreachable host requires an external uptime monitor (UptimeRobot, BetterStack, etc.) — not provided by the app.

## When the AWS properties API is down

Symptoms: `/for-rent` and `/for-rent.json` slow or empty; `application.log` shows refresh errors from the `properties` logger.

Behavior: `PropertyService.refresh_cache()` is called synchronously on every `/for-rent` and `/for-rent.json` hit. If the upstream call fails, the view falls back to the last successful cache contents — visitors see whatever was cached before the outage rather than an error. The **first request after a process restart** with a dead upstream will see an empty list because the in-memory cache starts empty.

Actions:

1. Check `/admin/status` to confirm the cache count and that "Property API Base" reports configured.
2. Check `application.log` (filter for `[properties]`) for the upstream error.
3. If the AWS endpoint genuinely changed, override via `PROPERTIES_API_BASE_URL` in `.env` and restart.
4. Avoid restarting during an upstream outage if the in-memory cache is non-empty — restart will lose the warm cache and serve nothing until upstream recovers.

There is **no periodic background refresh**. The cache only repopulates on visitor traffic to `/for-rent`/`/for-rent.json` or after admin mutations. This is intentional to keep API Gateway / Lambda costs proportional to traffic.

## Restart procedure

Sessions are signed with `SECRET_KEY`. If `SECRET_KEY` is unset, a fresh random key is generated each process start, which **invalidates all logged-in sessions on restart**. Always set `SECRET_KEY` in `.env` for production.

Steps:

1. Confirm `SECRET_KEY` is set in `.env`.
2. Stop the existing process (whatever supervisor is in use — systemd, supervisord, screen/tmux, etc.).
3. Start: `python website_app.py`. In a non-TTY context the startup prompts are skipped; defaults apply (`HOST=0.0.0.0`, `PORT=5000`, `LOG_LEVEL=INFO`, cache warm-up runs).
4. Hit `/admin/status` and verify "Properties cached" is non-zero.

To run with the cache warm-up disabled (faster start, first visitor pays the cost): pipe an answer in or run with stdin not a TTY and set the env appropriately. There is no flag — the prompt is interactive only.

## Rotating secrets

### `GOOGLE_CLIENT_SECRET`

1. In Google Cloud Console for the OAuth client (see [GOOGLE_OAUTH_SETUP.md](../GOOGLE_OAUTH_SETUP.md)), create a new client secret.
2. Update `GOOGLE_CLIENT_SECRET` in `.env`.
3. Restart the app.
4. Verify `/google/login` → `/google/callback` round-trips successfully with a test sign-in.
5. Delete the old secret in Google Cloud Console only after the new one is verified working.

`GOOGLE_CLIENT_ID` typically does not rotate. If it does, update `GOOGLE_CLIENT_ID` and any registered redirect URIs (default `http://localhost:5000/google/callback`; production must match `GOOGLE_REDIRECT_URI`).

### `EMAIL_APP_PASSWORD` (Gmail app password)

1. Generate a new app password in the sender Google account (`anthony.j.ekberg@gmail.com`).
2. Update `EMAIL_APP_PASSWORD` in `.env`.
3. Restart the app.
4. Trigger a test email — easiest path is `/admin/registrations` approve/reject (sends a confirmation email) or wait for the next legitimate notification. `application.log` will show "Sent email '...'" or the SMTP error.
5. Revoke the old app password in Google account settings.

If `EMAIL_APP_PASSWORD` is unset or wrong, the app **does not crash** — `NotificationService.send_email` logs a warning and returns `False`. Crash alerts, registration approvals, and admin notifications will silently not deliver. `/admin/status` will show "Email Notifications" as not ready.

### `SECRET_KEY`

Rotating `SECRET_KEY` invalidates all active sessions (everyone has to sign back in). Update `.env`, restart, and notify users they may need to re-authenticate.

## Logs

| File | What's in it | How to read |
|---|---|---|
| `application.log` | All app logging (request logs if enabled, properties refresh, email send results, exceptions, errors). Format: `timestamp \| LEVEL \| component \| message`. | Tail it. The admin UI reads the last 500 entries via `NotificationService.read_logs()`. |
| `site_changes.log` | Audit log of admin mutations (user added/deleted, role updated, etc.). One JSON object per line. | `tail` and `jq .` |
| `property_appointments.txt` | Append-only log of viewing requests submitted via the public form. Plain text. | `cat` / `tail`. Each visitor request appends a block. |

The application log is unrotated — it grows forever. If the file gets large, rotate it externally (logrotate, or move-and-restart). The in-app log reader uses a bounded deque (last 500 lines) so a multi-MB file won't blow process memory, but disk fills are your problem.

## Reading `property_appointments.txt`

Each entry is a free-text block written by `AppointmentService` containing the requesting visitor's name, contact info, the property they want to view, and their preferred time. There is no UI surface — the admin recipient also gets an email when a request is submitted. The file is the durable record; emails can be lost or filtered.

## Roles and access

Roles are evaluated per request from `user_roles.json` first, then the env-var fallbacks `HIGH_ADMIN_USERS`, `ADMIN_USERS`, `AUTHORIZED_USERS`. A user explicitly deleted via the admin UI is stored as `"revoked"` in `user_roles.json` — this is a deliberate tombstone so the env-var fallback can't silently re-grant access on the next login.

Role hierarchy: `guest < renter < admin < high_admin`. `high_admin` is required for `/admin/dashboard`, `/admin/status`, `/admin/analytics`. `admin` is required for the listing/contract/registration/ticket admin pages. `renter` is required for `/renter-dashboard` and `/renter/profile`.

To grant emergency admin access without using the UI: add the email to `ADMIN_USERS` or `HIGH_ADMIN_USERS` in `.env` and restart. To revoke: remove from env **and** ensure the user is set to `revoked` in `user_roles.json` (otherwise an env-var entry will keep granting access).

## Disk and capacity

- Uploaded images go to `static/uploads/`. Max upload: 16 MB per file (`MAX_CONTENT_LENGTH`); decoded raster capped at 24 MP and 6000px per side to prevent decompression-bomb DoS. Images are letterboxed at upload time.
- JSON state files are small (KB range for a small property portfolio). They are written via `tempfile + os.replace` for atomicity.
- The properties cache is in-memory only. Each restart re-fetches from AWS (8-worker pool, one full property = details + photos + thumbnail).

## Security headers and CSP

`services/security.py` sets HSTS (only when request is HTTPS), `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, and a Content Security Policy that allows Tailwind CDN, Google Accounts, and inline styles/scripts. If you front the app with a reverse proxy that also sets these headers, the app's `setdefault` calls won't override them.

CSRF: required on all unsafe methods except `/google/callback` and static. Tokens live in the session; templates inject via the `csrf_token` context processor. Tests bypass CSRF when `app.config["TESTING"]` is true.

## Rate limits (in-process)

- `/register` (POST): 3 per 10 minutes per IP.
- `/tickets/new` (POST): 5 per 10 minutes per IP.
- `/tickets/<id>/notes` (POST): 20 per 5 minutes per IP.

These are enforced by `_RateLimiter` in `services/security.py`, in-memory per process. Behind multiple workers / instances each has its own bucket — the limits effectively multiply.

`X-Forwarded-For` is trusted for the client IP if present. If you front with a proxy that doesn't sanitize this header, attackers can spoof IPs to evade the per-IP limits. Configure your proxy to overwrite `X-Forwarded-For`.

## SQLite storage migration (opt-in)

The app ships persisting state in JSON files via `FileStorageService`. A
parallel SQLite-backed `SqlStorageService` is included for the planned
migration off the JSON files, gated behind the `USE_SQLITE_STORAGE` env var
(default `0`). With the flag off there is no behavior change.

### Migrating

1. Stop the app (or run while quiesced — the migration replaces table
   contents with the current JSON snapshot, so any writes to JSON during
   migration would be lost).
2. Dry-run to inspect counts:
   ```bash
   python scripts/migrate_from_json.py --dry-run
   ```
3. Run the migration:
   ```bash
   python scripts/migrate_from_json.py
   ```
   This creates `somewheria.sqlite3` next to the JSON files (path is
   `AppConfig.sqlite_file`, i.e. `<base_dir>/somewheria.sqlite3`). The
   script is idempotent — re-running replaces table contents from the JSON
   snapshot. The JSON files are not modified.
4. Set `USE_SQLITE_STORAGE=1` in `.env` and start the app. `create_app()`
   constructs `SqlStorageService` instead of `FileStorageService`. Because
   the SqlStorageService mirrors the same public API used by routes and
   `TicketService`, no other code changes.
5. Verify reads via `/admin/registrations`, `/admin/users`, and the ticket
   pages. Verify a write (e.g. add a pending registration) round-trips.
6. To roll back: unset `USE_SQLITE_STORAGE` (or set to `0`) and restart.
   The JSON files are untouched, so the app reverts to them.

The JSON files remain on disk after migration as a fallback. Once the SQLite
path has run cleanly for a while, the JSON files can be archived. Do not
delete them until you have a working backup of `somewheria.sqlite3`.

## Daily SQLite backup to S3

`scripts/backup_database.py` compresses the SQLite DB with gzip and uploads
the artifact to S3. Run it daily from cron once SQLite storage is live.

### Required env vars

| Variable | Purpose |
|---|---|
| `BACKUP_S3_BUCKET` | Target bucket. If unset, upload is skipped and the local artifact is left in the staging dir. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | AWS credentials with `s3:PutObject` on the bucket. If either is unset, upload is skipped. |
| `BACKUP_S3_PREFIX` | Optional. Key prefix inside the bucket (default `somewheria/`). |
| `BACKUP_RETENTION_DAYS` | Optional. Local artifacts older than N days are pruned (default `7`). S3 lifecycle is managed in S3, not here. |
| `BACKUP_STAGING_DIR` | Optional. Directory for the local artifact (default `./backups`). |

### Cron example

```cron
# Daily at 03:15 UTC; logs to /var/log/somewheria-backup.log.
15 3 * * *  cd /opt/somewheria && \
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... BACKUP_S3_BUCKET=somewheria-backups \
  /opt/somewheria/.venv/bin/python scripts/backup_database.py \
  >> /var/log/somewheria-backup.log 2>&1
```

The script exits non-zero on failure so cron's `MAILTO` will surface errors.
S3 lifecycle rules (e.g. transition to Glacier after 30 days, expire after a
year) should be configured on the bucket itself.

### S3 setup

`boto3` is not in `requirements.txt` — install it on the host that runs the
backup (`pip install boto3`). The script imports lazily so it can also be
exercised without S3 credentials (writes the gz artifact to the staging dir
and prints "Skipping S3 upload").

## External uptime monitoring

The in-process crash handler only catches exceptions in a running Python
process. A dead process, OOM kill, network outage, or hung worker will not
trigger it. Wire up an external monitor.

Recommended: **UptimeRobot** (free tier covers 5-minute checks) or
**BetterStack / Better Uptime**.

Configuration:

1. Create an HTTP(S) check against the production root URL (e.g.
   `https://somewheria.example.com/`). Interval: **5 minutes**. Expected
   status: `200`.
2. Set the alert recipient to the same admin email used by
   `NotificationService` (currently `anthony@ekbergproperties.com`). Add SMS
   as a secondary if available.
3. Optionally add a content keyword check (a string from the home page) to
   catch the case where the process is up but serving the bare 503 from the
   crash handler — the crash handler returns `503`, so a status check alone
   would already catch it, but a content check protects against any future
   "200 with broken page" regression.
4. Test the monitor by stopping the process and confirming the alert fires
   within the configured window.

This is independent of the in-process crash handler. Both are needed: the
crash handler emails on caught exceptions; the external monitor catches the
cases where no Python is alive to send mail.

## Continuous integration

`.github/workflows/ci.yml` runs on push and PR to `main` and `dev`:

- `test` job: Python 3.11, `pip install -r requirements.txt`, then
  `python -m unittest discover`. Fails the workflow on any test failure.
- `lint` job: installs `requirements-dev.txt` (which adds `ruff`) and runs
  `ruff check .`.

Branch protection (require checks to pass before merge) is configured in the
GitHub UI under Settings -> Branches, not in this repo.

## What this document does not cover

- **Production hostname / TLS termination / reverse proxy config:** not in the codebase. Document separately for your deployment.
- **On-call rotation and escalation paths:** not in the codebase.
- **Backup schedule and restore procedure for the JSON state files:** not implemented; whatever cron/snapshot you run is out of scope here.
- **Who holds the Google OAuth client and Gmail account credentials:** not in the codebase.
