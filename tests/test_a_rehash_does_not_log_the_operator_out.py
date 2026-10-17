"""A request that lands while a rehash reloads the settings keeps its session (#1433).

A rehash re-executes defaults.py, which puts ADMIN_PASSWORD_HASH back to ""
until settings.conf is applied again, about a millisecond later (see
runtime.config_reload_lock). The dashboard compared each session's password
stamp against that value without the lock, so a request in the window looked
like one from before a password change: 401, the cookie cleared, the browser
sent to /login. The likeliest such request is the Settings page's own
re-fetch right after a save. A login checked in the window was refused and
counted as a failed attempt.

The window is reproduced here as the rehash makes it: a thread holds the lock
while the hash is "", then puts it back.
"""

import os
import sys
import threading
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import defaults as config  # noqa: E402
import runtime  # noqa: E402
import webserver  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402
from tests.test_every_route_is_behind_the_login import PASSWORD, RouteCase  # noqa: E402

# Long enough that a request started inside the window is still inside it
# when it reads the hash, unless it waits for the lock.
WINDOW_SECONDS = 0.3


class ReloadWindow:
    """What a rehash does to the hash, on its own thread: under the lock, the
    hash is "" for WINDOW_SECONDS, then the configured one again."""

    def __init__(self):
        self.opened = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        with runtime.config_reload_lock:
            configured = config.ADMIN_PASSWORD_HASH
            config.ADMIN_PASSWORD_HASH = ""
            self.opened.set()
            threading.Event().wait(WINDOW_SECONDS)
            config.ADMIN_PASSWORD_HASH = configured

    def __enter__(self):
        self.thread.start()
        if not self.opened.wait(5):
            raise AssertionError("the reload window never opened")
        return self

    def __exit__(self, *exc):
        self.thread.join(5)


class TheHashIsReadUnderTheLock(DCCoreTestCase):
    """_admin_password_hash() itself - no Flask."""

    def test_it_waits_for_the_reload_and_reads_the_configured_hash(self):
        self.set_config(ADMIN_PASSWORD_HASH="pbkdf2$configured")

        with ReloadWindow():
            self.assertEqual(config.ADMIN_PASSWORD_HASH, "",
                             "control: the window is really open")
            self.assertEqual(webserver._admin_password_hash(), "pbkdf2$configured")


@unittest.skipUnless(webserver.HAVE_FLASK, "Flask not installed; CI installs requirements-web.txt")
class ARequestDuringTheReload(RouteCase):

    def test_an_open_session_stays_open(self):
        self.log_in()

        with ReloadWindow():
            resp = self.client.get("/api/settings")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.get("/api/settings").status_code, 200,
                         "the session was dropped by the request in the window")

    def test_a_correct_password_logs_in_and_is_not_counted_as_a_failure(self):
        with ReloadWindow():
            resp = self.client.post("/login", data={"password": PASSWORD})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.client.get("/api/settings").status_code, 200)
        self.assertEqual(dict(webserver._web_bad_ips), {})


if __name__ == "__main__":
    unittest.main()
