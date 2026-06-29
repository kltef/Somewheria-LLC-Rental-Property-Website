# Production Deployment

The production stack is the standard Flask trio:

```
Internet ──HTTPS──> nginx ──HTTP──> gunicorn ──WSGI──> Flask (website_app:app)
                    (TLS,            (workers,          (the app)
                     static,          timeouts,
                     edge)            crash recovery)
```

- **nginx** terminates TLS, serves `/static/` from disk, buffers slow clients, and is the hardened public edge.
- **gunicorn** runs the app with worker/timeout management and restarts crashed workers. Config: [`gunicorn.conf.py`](../gunicorn.conf.py).
- **Flask** (`website_app:app`) is the application itself.

`main` is production (tagged releases); `dev` is where features land first — see `CLAUDE.md`.

---

## 1. Server prerequisites

```bash
sudo apt update
sudo apt install -y python3-venv nginx
# TLS certificates (Let's Encrypt):
sudo apt install -y certbot python3-certbot-nginx
```

Create an unprivileged service account and clone the repo:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin somewheria
sudo mkdir -p /opt/somewheria
sudo chown somewheria:somewheria /opt/somewheria
sudo -u somewheria git clone <repo-url> \
    /opt/somewheria/Somewheria-LLC-Rental-Property-Website
```

## 2. Configure `.env`

The app reads config from `.env` via python-dotenv. At minimum set a real
`SECRET_KEY`, the role lists, and (if used) Google OAuth credentials:

```bash
cd /opt/somewheria/Somewheria-LLC-Rental-Property-Website
sudo -u somewheria tee .env >/dev/null <<'ENV'
SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">
AUTHORIZED_USERS=renter1@example.com,renter2@example.com
ADMIN_USERS=admin@example.com
HIGH_ADMIN_USERS=owner@example.com
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=https://example.com/google/callback
ENV
```

> **Do NOT set `FLASK_ENV`** in production. Leaving it unset keeps
> `SESSION_COOKIE_SECURE` on and OAuth on real HTTPS. Setting it to
> `development` would downgrade cookie security and allow insecure OAuth.

## 3. Install dependencies (venv)

```bash
sudo -u somewheria ./start.sh   # creates venv, installs requirements, then runs
# Ctrl-C once it boots — systemd will own the process from here on.
```

`start.sh` builds `./venv` and installs `requirements.txt` (which now includes
gunicorn). Use `SKIP_INSTALL=1` on later runs to reuse the venv.

## 4. gunicorn under systemd

```bash
sudo cp deploy/somewheria.service /etc/systemd/system/somewheria.service
# Edit User/Group/paths in the unit if your layout differs.
sudo systemctl daemon-reload
sudo systemctl enable --now somewheria
sudo systemctl status somewheria
journalctl -u somewheria -f
```

The unit sets `TRUSTED_PROXY_COUNT=1` (so ProxyFix trusts the single nginx
hop) and `BIND=127.0.0.1:8000`. Tune workers/threads/timeout via the env vars
documented in `gunicorn.conf.py` — the default is **1 worker × 8 threads**,
which keeps the in-memory property cache shared (see the caveat below).

## 5. nginx

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/somewheria
sudo ln -s /etc/nginx/sites-available/somewheria /etc/nginx/sites-enabled/
# Edit server_name + $app_root in the file, then:
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d example.com -d www.example.com   # provisions TLS
```

## 6. Verify

```bash
curl -I https://example.com/                 # 200/302 from the app
curl -I https://example.com/static/css/...   # served by nginx (check headers)
sudo systemctl status somewheria nginx
```

---

## Important app-specific notes

- **The property cache is per-process and in-memory.** This is why the default
  is **one** gunicorn worker. Multiple workers each keep a *separate* cache, so
  an admin mutation that refreshes one worker's cache won't be seen by the
  others, and you multiply upstream AWS calls. To scale, prefer more **threads**
  (the workload is I/O-bound on AWS) before adding workers; if you truly need
  multiple worker processes, move the cache to a shared store first.
- **Never expose `private/contracts/` via nginx.** Signed contract PDFs live
  outside `static/` on purpose and must only be reachable through the
  authenticated `/contracts/<id>/download` route. The nginx `location /static/`
  block is scoped to `static/` only — keep it that way.
- **`client_max_body_size` (16m) must match Flask's `MAX_CONTENT_LENGTH`.** If
  you change one, change the other, or uploads fail confusingly.
- **`proxy_read_timeout` ≥ gunicorn `--timeout`.** A cold property cache triggers
  a synchronous AWS fan-out on `/for-rent`; too-low an nginx timeout 504s a
  request gunicorn would have finished.
- **This still uses Flask's dev server in dev mode** (`./start.sh` without
  `MODE=prod`). Only `MODE=prod` / the systemd unit uses gunicorn.
