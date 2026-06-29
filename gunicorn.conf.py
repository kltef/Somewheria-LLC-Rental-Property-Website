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

bind = os.getenv("BIND", "127.0.0.1:8000")
workers = int(os.getenv("GUNICORN_WORKERS", "1"))
threads = int(os.getenv("GUNICORN_THREADS", "8"))
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
# Recycle workers periodically to bound any slow memory growth.
max_requests = 1000
max_requests_jitter = 100
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")
# Log to stdout/stderr so systemd's journal captures everything.
accesslog = "-"
errorlog = "-"
