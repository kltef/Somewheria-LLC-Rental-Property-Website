"""Gunicorn configuration for the Somewheria web app.

Used in production behind an AWS ALB:
    ALB (TLS via ACM) -> gunicorn -> Flask (website_app:app)

The ALB terminates HTTPS and forwards plain HTTP to gunicorn on the EC2
instance. The systemd unit sets BIND=0.0.0.0:8000 so the ALB can reach it;
the instance security group must restrict that port to the ALB only.

Defaults are tuned for this app's workload, which is I/O-bound: most routes
spend their time waiting on the upstream AWS properties API, and the property
cache lives *in memory inside a single process*. Running ONE worker with
several threads keeps that cache shared (a multi-worker setup would give each
worker its own private cache and multiply AWS calls). Override via env vars
when CPU — not AWS latency — becomes the bottleneck.

Env overrides:
  BIND              listen address           (default 127.0.0.1:8000)
  GUNICORN_WORKERS  worker processes         (default 1 — keeps cache shared)
  GUNICORN_THREADS  threads per worker       (default 8 — matches the app's
                                              upstream fan-out pool)
  GUNICORN_TIMEOUT  worker timeout, seconds  (default 120 — the AWS fan-out on
                                              a cold cache can be slow; keep
                                              nginx proxy_read_timeout >= this)
  GUNICORN_LOGLEVEL gunicorn log level       (default info)
"""

import os


def _int_env(name: str, default: int, *, minimum: int) -> int:
    # ``int()`` alone would raise ValueError and refuse to boot on a mistyped
    # env value (``GUNICORN_WORKERS=auto``, or a stray ``GUNICORN_WORKERS=``
    # line that leaves the var set to the empty string). Gunicorn reads this
    # config file with ``exec`` at startup — an uncaught exception here means
    # the production service fails to come up with a cryptic traceback instead
    # of using the documented default. Falling back to the default on blank /
    # non-numeric / below-minimum matches the ``_int_env`` contract the app's
    # own config.py already applies for ``CACHE_REFRESH_INTERVAL`` /
    # ``TRUSTED_PROXY_COUNT`` so an operator typo can never take the service
    # offline. ``minimum`` enforces the per-setting floor (workers / threads
    # must be ≥ 1 or gunicorn rejects the config at validation time).
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value >= minimum else default


bind = os.getenv("BIND", "127.0.0.1:8000")
workers = _int_env("GUNICORN_WORKERS", 1, minimum=1)
threads = _int_env("GUNICORN_THREADS", 8, minimum=1)
timeout = _int_env("GUNICORN_TIMEOUT", 120, minimum=0)
graceful_timeout = 30
# Recycle workers periodically to bound any slow memory growth.
max_requests = 1000
max_requests_jitter = 100
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")
# Log to stdout/stderr so systemd's journal captures everything.
accesslog = "-"
errorlog = "-"
