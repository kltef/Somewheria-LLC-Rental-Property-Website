# Production Deployment (AWS)

The app runs on an **EC2 instance** behind an **Application Load Balancer**.
The property data it serves comes from a separate **Lambda function behind API
Gateway** (`PROPERTIES_API_BASE_URL`).

```
Internet ──HTTPS──> ALB ──HTTP──> gunicorn ──WSGI──> Flask (website_app:app)
                  (TLS via ACM,   (workers,          │
                   idle timeout,   timeouts,          └─ fans out to ──HTTPS──>
                   health check)   crash recovery)       API Gateway -> Lambda
```

- **ALB** terminates TLS (ACM cert), load-balances, health-checks the target,
  and is the hardened public edge.
- **gunicorn** runs the app with worker/timeout management and restarts crashed
  workers. Config: [`gunicorn.conf.py`](../gunicorn.conf.py). Managed by systemd.
- **Flask** (`website_app:app`) is the application; it also serves `/static`
  directly (modest CSS/JS/PWA assets — no separate web server on the box).
- **Upstream** property data is a Lambda behind API Gateway; the app caches
  responses in memory (see the cost/cache note below).

`main` is production (tagged releases); `dev` is where features land first —
see `CLAUDE.md`.

---

## 1. Instance prerequisites

```bash
sudo apt update
sudo apt install -y python3-venv
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
`SECRET_KEY`, the role lists, the upstream API URL, and (if used) Google OAuth:

```bash
cd /opt/somewheria/Somewheria-LLC-Rental-Property-Website
sudo -u somewheria tee .env >/dev/null <<'ENV'
SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">
PROPERTIES_API_BASE_URL=https://<api-id>.execute-api.<region>.amazonaws.com/<stage>
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
# Ctrl-C once it boots — systemd owns the process from here on.
```

`start.sh` builds `./venv` and installs `requirements.txt` (which includes
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

The unit sets:
- `TRUSTED_PROXY_COUNT=1` — one proxy hop (the ALB), so ProxyFix trusts the
  ALB's `X-Forwarded-Proto`/`X-Forwarded-For` and the secure-cookie / OAuth
  https logic works.
- `BIND=0.0.0.0:8000` — so the ALB can reach gunicorn over the network.

Tune workers/threads/timeout via the env vars documented in
`gunicorn.conf.py`. Default is **1 worker × 8 threads** (see the cost/cache
note below).

## 5. AWS load balancer wiring

Configure these in the AWS console / IaC:

- **Target group:** protocol HTTP, port **8000**, target = the instance.
  - **Health check path:** `/` (returns 200 and is cheap — it does *not*
    trigger the property-cache refresh; only `/for-rent` does).
- **ALB listener:** HTTPS :443 with an **ACM certificate**, forwarding to the
  target group. Add a :80 listener that redirects to :443.
- **ALB idle timeout:** raise from the default **60s to ≥ 120s**. A cold
  property cache triggers a synchronous fan-out to API Gateway/Lambda on
  `/for-rent` that can exceed 60s; otherwise the ALB 504s a request gunicorn
  would have completed. Keep it ≥ gunicorn's `--timeout` (120s).
- **Security groups:**
  - ALB SG: allow 443 (and 80) from the internet.
  - Instance SG: allow port **8000 only from the ALB's security group** —
    never from `0.0.0.0/0`.
- **Egress:** the instance needs outbound HTTPS to the API Gateway endpoint
  (via internet/NAT gateway, or a VPC endpoint for API Gateway).

## 6. Verify

```bash
# On the instance — gunicorn is healthy locally:
curl -I http://127.0.0.1:8000/                 # 200
# Through the ALB:
curl -I https://example.com/                   # 200/302, X-Forwarded handled
sudo systemctl status somewheria
# Target group should show the instance "healthy" in the AWS console.
```

---

## Important app-specific notes

- **The property cache is per-process, in-memory — and every refresh costs
  API Gateway + Lambda invocations.** This is why the default is **one**
  gunicorn worker: multiple workers each keep a *separate* cache, so an admin
  mutation that refreshes one worker's cache isn't seen by the others, **and**
  you multiply upstream AWS calls (and the bill — the periodic refresher was
  removed for exactly this cost reason). To scale, prefer more **threads**
  (the workload is I/O-bound on the upstream API) before adding workers; if you
  truly need multiple worker processes, move the cache to a shared store first.
- **Cold starts.** The first `/for-rent` after idle hits cold Lambdas, so that
  refresh is slow. The upstream client already times out per call (20s list,
  10s per-property) under API Gateway's 29s hard limit, and gunicorn's 120s
  timeout (and the ALB idle timeout) cover the aggregate 8-way fan-out. If
  cold-start latency hurts UX, consider Lambda provisioned concurrency.
- **Contract PDFs stay auth-gated.** Signed contracts live outside `static/`
  and are only reachable via the authenticated `/contracts/<id>/download`
  route. Flask serves `/static` but never that directory — there is no web
  server config to misconfigure now that nginx is gone, but keep it that way.
- **This still uses Flask's dev server in dev mode** (`./start.sh` without
  `MODE=prod`). Only `MODE=prod` / the systemd unit uses gunicorn.
