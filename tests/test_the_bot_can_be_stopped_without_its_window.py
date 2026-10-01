"""The bot can be stopped without its window (#1065).

Closing the window or Ctrl-C in it were the only ways. Now `start-dccore stop`
(oserve.py --stop), the admin console's `shutdown now` and the dashboard's
Stop the bot all end in the same KeyboardInterrupt Ctrl-C raises. The command
asks through a file, data/dccore.stop, and the instance lock says when the bot
has really gone - no kill, which on Windows would skip the shutdown.
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import types
import unittest
from unittest import mock

from tests import support  # noqa: F401  (path setup)

import adminchat  # noqa: E402
import platform_compat  # noqa: E402
import stopping  # noqa: E402

REPO_ROOT = support.REPO_ROOT


class Case(support.DCCoreTestCase):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="dccore-stop-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.set_config(DCC_QUEUE_FILE=os.path.join(self.dir, "dcc_queue.txt"))


class TheStopItself(Case):
    def test_it_says_why_sends_quit_and_interrupts_like_ctrl_c(self):
        sent, interrupted = [], []
        sock = types.SimpleNamespace(sendall=lambda data: sent.append(data))
        old = sys.modules.get("oserve")
        sys.modules["oserve"] = types.SimpleNamespace(irc_connection=sock)
        self.addCleanup(lambda: sys.modules.__setitem__("oserve", old) if old
                        else sys.modules.pop("oserve", None))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            stopping.request_stop("a test asked", interrupt=lambda: interrupted.append(True))
        self.assertEqual(sent, [b"QUIT :DCCore is stopping\r\n"])
        self.assertEqual(interrupted, [True])
        self.assertIn("[STOP] Stopping the bot: a test asked.", out.getvalue())

    def test_with_no_connection_it_still_stops(self):
        interrupted = []
        old = sys.modules.get("oserve")
        sys.modules["oserve"] = types.SimpleNamespace(irc_connection=None)
        self.addCleanup(lambda: sys.modules.__setitem__("oserve", old) if old
                        else sys.modules.pop("oserve", None))
        with mock.patch("sys.stdout", io.StringIO()):
            stopping.request_stop("x", interrupt=lambda: interrupted.append(True))
        self.assertEqual(interrupted, [True])


class TheStopFile(Case):
    def test_no_file_no_stop(self):
        stopped = []
        self.assertFalse(stopping.check_stop_file(stop=stopped.append))
        self.assertEqual(stopped, [])

    def test_a_file_stops_it_once_and_goes(self):
        with open(stopping.stop_file(), "w") as handle:
            handle.write("stop\n")
        stopped = []
        self.assertTrue(stopping.check_stop_file(stop=stopped.append))
        self.assertEqual(stopped, ["asked to by the stop command (start-dccore stop)"])
        self.assertFalse(os.path.exists(stopping.stop_file()))

    def test_one_left_from_before_is_cleared_at_startup(self):
        with open(stopping.stop_file(), "w") as handle:
            handle.write("stop\n")
        stopping.clear_stale_stop_file()
        self.assertFalse(os.path.exists(stopping.stop_file()))

    def test_the_watcher_starts_once(self):
        import runtime
        self.addCleanup(setattr, runtime, "stop_watcher_thread", runtime.stop_watcher_thread)
        runtime.stop_watcher_thread = None
        gate = __import__("threading").Event()
        self.addCleanup(gate.set)
        with mock.patch.object(stopping, "_watch", gate.wait):
            stopping.ensure_watcher()
            first = runtime.stop_watcher_thread
            stopping.ensure_watcher()
        self.assertIs(runtime.stop_watcher_thread, first)
        self.assertTrue(first.is_alive())


# A stand-in for the bot: holds the instance lock the way oserve.startup()
# does, and lets it go when the stop file appears - or, told to, never does.
CHILD = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, sys.argv[1])
    import platform_compat
    lock, stop_file, mode = sys.argv[2], sys.argv[3], sys.argv[4]
    platform_compat.take_instance_lock(lock)
    print("holding", flush=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        if mode == "obey" and os.path.exists(stop_file):
            os.remove(stop_file)
            break
        time.sleep(0.1)
""")


class TheStopCommand(Case):
    def child(self, mode):
        handle_fd, script = tempfile.mkstemp(suffix=".py", dir=self.dir)
        with os.fdopen(handle_fd, "w") as handle:
            handle.write(CHILD)
        proc = subprocess.Popen(
            [sys.executable, script, os.path.join(REPO_ROOT, "src"), stopping.lock_file(),
             stopping.stop_file(), mode], stdout=subprocess.PIPE, text=True)
        def end():
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            proc.stdout.close()
        self.addCleanup(end)
        self.assertEqual(proc.stdout.readline().strip(), "holding")
        return proc

    def test_not_running_says_so_and_asks_nothing(self):
        said = []
        self.assertEqual(stopping.stop_from_outside(log=said.append), 0)
        self.assertEqual(said, ["DCCore is not running from this folder - nothing to stop."])
        self.assertFalse(os.path.exists(stopping.stop_file()))
        # Let go again: a stop command that kept the lock would itself be the
        # "running bot" the next start is refused for.
        self.assertIsNone(platform_compat._instance_lock["handle"])

    def test_a_running_bot_is_asked_and_waited_for(self):
        proc = self.child("obey")
        said = []
        self.assertEqual(stopping.stop_from_outside(wait=30, log=said.append), 0)
        self.assertEqual(said[-1], "DCCore has stopped.")
        self.assertTrue(said[0].startswith(f"Asked DCCore (pid {proc.pid}) to stop."), said)
        proc.wait(timeout=10)

    def test_one_that_does_not_stop_is_named_with_the_way_to_end_it(self):
        proc = self.child("ignore")
        said = []
        self.assertEqual(stopping.stop_from_outside(wait=2, log=said.append), 1)
        self.assertIn(f"has not stopped after 2 seconds", said[-1])
        self.assertIn(str(proc.pid), said[-1])


class TheConsoleCommand(unittest.TestCase):
    def session(self):
        lines = []
        return types.SimpleNamespace(nick="SomeAdmin", send=lines.append, lines=lines)

    def test_it_asks_for_now_first(self):
        session = self.session()
        with mock.patch.object(stopping, "request_stop_soon") as soon:
            adminchat.handle_command(session, "shutdown")
        soon.assert_not_called()
        self.assertIn("Type 'shutdown now'", session.lines[0])

    def test_shutdown_now_stops_and_says_who(self):
        session = self.session()
        with mock.patch.object(stopping, "request_stop_soon") as soon:
            adminchat.handle_command(session, "shutdown now")
        soon.assert_called_once_with("asked by SomeAdmin in the admin console")
        self.assertIn("Stopping the bot", session.lines[0])


def read(path):
    with io.open(os.path.join(REPO_ROOT, path), encoding="utf-8") as handle:
        return handle.read()


class TheWiring(unittest.TestCase):
    def test_oserve_stop_runs_before_anything_starts(self):
        code = read("oserve.py")
        self.assertLess(code.index('if "--stop" in sys.argv[1:]:'),
                        code.index("platform_compat.install_console_log(_console_log_settings)"))
        self.assertIn("        sys.exit(stopping.stop_from_outside())", code)

    def test_startup_clears_an_old_file_after_taking_the_lock(self):
        # startup() alone: the shutdown in run_forever() clears it too.
        body = read("oserve.py").split("def startup(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(body.index("platform_compat.take_instance_lock(lock_path)"),
                        body.index("stopping.clear_stale_stop_file()"))

    def test_the_watcher_starts_at_the_entry_point_only(self):
        """Not inside run_forever(): tests drive that in-process, and a watcher
        started there once interrupted the whole test runner."""
        code = read("oserve.py")
        self.assertNotIn("ensure_watcher", code.split("def run_forever(", 1)[1].split('\nif __name__ == "__main__":', 1)[0])
        entry = code.rsplit('\nif __name__ == "__main__":', 1)[1]
        self.assertLess(entry.index("    startup()"), entry.index("    stopping.ensure_watcher()"))
        self.assertLess(entry.index("    stopping.ensure_watcher()"), entry.index("    run_forever()"))

    def test_both_launchers_have_stop(self):
        bat = read("scripts/windows/start-dccore.bat")
        self.assertIn('if /i "%~1"=="stop" goto :run_stop', bat)
        self.assertIn(":run_stop\n%PY% oserve.py --stop", bat.replace("\r\n", "\n"))
        sh = read("scripts/linux/start-dccore.sh")
        self.assertIn('if [ "$1" = "stop" ]; then\n    "$PY" oserve.py --stop\n    exit $?', sh)

    def test_the_dashboard_button_confirms_then_posts(self):
        js = read("web/app.js")
        handler = js.split('el.stopBotRunBtn.addEventListener("click", function () {', 1)[1][:600]
        self.assertLess(handler.index('window.confirm(t("tools.stopBotConfirm"))'),
                        handler.index('postJson("/api/tools/stop", {})'))


@unittest.skipUnless(getattr(__import__("webserver"), "HAVE_FLASK", False), "Flask not installed")
class TheRoute(support.DCCoreTestCase):
    def test_it_answers_then_stops(self):
        import adminchat as _admin
        import webserver
        from tests.test_webserver import WEBUI_TEST_PASSWORD, log_in_test_client
        self.set_config(ADMIN_PASSWORD_HASH=_admin.make_password_hash(WEBUI_TEST_PASSWORD, iterations=1000))
        client = webserver.create_app().test_client()
        log_in_test_client(client)
        with mock.patch.object(stopping, "request_stop_soon") as soon:
            resp = client.post("/api/tools/stop", json={})
        self.assertEqual(resp.status_code, 200)
        soon.assert_called_once_with("asked from the dashboard")


if __name__ == "__main__":
    unittest.main()
