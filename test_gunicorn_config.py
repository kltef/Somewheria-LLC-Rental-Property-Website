"""Tests for the gunicorn production config.

The config file is loaded by gunicorn with ``exec`` at startup, so any
exception raised while reading env vars prevents the service from booting.
``int(os.getenv(...))`` on a mistyped or blank ``GUNICORN_WORKERS`` /
``GUNICORN_THREADS`` / ``GUNICORN_TIMEOUT`` would otherwise crash the
process with a cryptic ValueError traceback instead of using the
documented default — same contract ``_int_env`` applies in
``somewheria_app/config.py`` for the app's own numeric env vars.
"""

from __future__ import annotations

import os
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch


GUNICORN_CONF_PATH = Path(__file__).parent / "gunicorn.conf.py"
# Env vars the config consults; scrub them all so a stray value in the
# host environment can't leak into the "defaults" test.
TOUCHED_KEYS = (
    "BIND",
    "GUNICORN_WORKERS",
    "GUNICORN_THREADS",
    "GUNICORN_TIMEOUT",
    "GUNICORN_LOGLEVEL",
)


def _load_config(env: dict) -> dict:
    """Exec gunicorn.conf.py with ``env`` as the ONLY values for the keys
    the config consults, and return the resulting module namespace. Uses
    ``runpy`` rather than ``importlib`` because ``gunicorn.conf`` is a
    name that resolves to the installed gunicorn package's own submodule
    (``site-packages/gunicorn/conf.py``) — reloading that would not run
    our file at all.
    """
    base = {k: v for k, v in os.environ.items() if k not in TOUCHED_KEYS}
    base.update(env)
    with patch.dict("os.environ", base, clear=True):
        return runpy.run_path(str(GUNICORN_CONF_PATH))


class GunicornConfigEnvTestCase(unittest.TestCase):
    def test_defaults_when_env_unset(self):
        conf = _load_config({})
        self.assertEqual(conf["bind"], "127.0.0.1:8000")
        self.assertEqual(conf["workers"], 1)
        self.assertEqual(conf["threads"], 8)
        self.assertEqual(conf["timeout"], 120)
        self.assertEqual(conf["loglevel"], "info")

    def test_env_overrides_integers(self):
        conf = _load_config(
            {
                "GUNICORN_WORKERS": "4",
                "GUNICORN_THREADS": "16",
                "GUNICORN_TIMEOUT": "60",
            }
        )
        self.assertEqual(conf["workers"], 4)
        self.assertEqual(conf["threads"], 16)
        self.assertEqual(conf["timeout"], 60)

    def test_non_numeric_env_falls_back_to_default(self):
        # The prior ``int(os.getenv(...))`` would ValueError here and
        # prevent gunicorn from starting — the whole point of _int_env.
        conf = _load_config(
            {
                "GUNICORN_WORKERS": "auto",
                "GUNICORN_THREADS": "many",
                "GUNICORN_TIMEOUT": "forever",
            }
        )
        self.assertEqual(conf["workers"], 1)
        self.assertEqual(conf["threads"], 8)
        self.assertEqual(conf["timeout"], 120)

    def test_blank_env_falls_back_to_default(self):
        # A trailing ``GUNICORN_WORKERS=`` line in the service unit leaves
        # the var set to the empty string; ``int("")`` crashes the same way
        # a non-numeric value does.
        conf = _load_config(
            {
                "GUNICORN_WORKERS": "",
                "GUNICORN_THREADS": "   ",
                "GUNICORN_TIMEOUT": "",
            }
        )
        self.assertEqual(conf["workers"], 1)
        self.assertEqual(conf["threads"], 8)
        self.assertEqual(conf["timeout"], 120)

    def test_below_minimum_falls_back_to_default(self):
        # workers=0 and threads=0 are invalid; gunicorn would reject the
        # config at validation time. Reject below-floor values here so the
        # fallback lands on a defensible default rather than a broken
        # service.
        conf = _load_config(
            {
                "GUNICORN_WORKERS": "0",
                "GUNICORN_THREADS": "-3",
            }
        )
        self.assertEqual(conf["workers"], 1)
        self.assertEqual(conf["threads"], 8)

    def test_timeout_zero_is_allowed(self):
        # timeout=0 disables gunicorn's per-worker timeout; it is a valid
        # (if unusual) setting, so the minimum floor there is 0, not 1.
        conf = _load_config({"GUNICORN_TIMEOUT": "0"})
        self.assertEqual(conf["timeout"], 0)

    def test_negative_timeout_falls_back(self):
        conf = _load_config({"GUNICORN_TIMEOUT": "-1"})
        self.assertEqual(conf["timeout"], 120)

    def test_bind_and_loglevel_env_overrides(self):
        conf = _load_config(
            {
                "BIND": "0.0.0.0:9000",
                "GUNICORN_LOGLEVEL": "warning",
            }
        )
        self.assertEqual(conf["bind"], "0.0.0.0:9000")
        self.assertEqual(conf["loglevel"], "warning")


if __name__ == "__main__":
    unittest.main()
