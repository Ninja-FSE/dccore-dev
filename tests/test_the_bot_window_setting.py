"""BOT_WINDOW: the bot in its window, minimised, or with no window (#1065).

start-dccore.bat reads it through scripts/windows/window-mode.py (an exit code,
so no output has to be parsed), checks with `oserve.py --running` that no bot
holds the folder already, and starts the bot minimised (`start /min`) or under
pythonw. With no window sys.stdout and sys.stderr are None; oserve.py puts a
null file in their place so every write works, and the log file - kept even
when it is turned off - is where everything goes. The first run always has
its window.
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from tests import support  # noqa: F401  (path setup)

REPO_ROOT = support.REPO_ROOT
WINDOW_MODE = os.path.join(REPO_ROOT, "scripts", "windows", "window-mode.py")


def read(path):
    with io.open(os.path.join(REPO_ROOT, path), encoding="utf-8") as handle:
        return handle.read()


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dccore-window-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.settings = os.path.join(self.dir, "settings.conf")

    def write_settings(self, **values):
        with io.open(self.settings, "w", encoding="utf-8") as handle:
            for name, value in values.items():
                handle.write(f"{name} = {value}\n")

    def env(self):
        return dict(os.environ, DCCORE_SETTINGS_FILE=self.settings, PYTHONIOENCODING="utf-8")


class TheLaunchersHelper(Case):
    def code(self, **values):
        self.write_settings(**values)
        return subprocess.run([sys.executable, WINDOW_MODE], cwd=REPO_ROOT, env=self.env(),
                              capture_output=True, timeout=120).returncode

    def test_each_mode_is_its_exit_code(self):
        self.assertEqual(self.code(BOT_WINDOW="hidden"), 21)
        self.assertEqual(self.code(BOT_WINDOW="minimised"), 20)
        self.assertEqual(self.code(BOT_WINDOW="normal"), 0)

    def test_not_set_or_not_a_choice_is_normal(self):
        self.assertEqual(self.code(), 0)
        self.assertEqual(self.code(BOT_WINDOW="invisible"), 0)


class IsOneRunning(Case):
    def running(self):
        self.write_settings(DCC_QUEUE_FILE=os.path.join(self.dir, "dcc_queue.txt").replace("\\", "/"))
        return subprocess.run([sys.executable, "oserve.py", "--running"], cwd=REPO_ROOT, env=self.env(),
                              capture_output=True, timeout=120).returncode

    def test_no_bot_is_one(self):
        self.assertEqual(self.running(), 1)

    def test_a_bot_holding_the_folder_is_zero(self):
        child = subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent(f"""
                import sys, time
                sys.path.insert(0, {os.path.join(REPO_ROOT, 'src')!r})
                import platform_compat
                platform_compat.take_instance_lock({os.path.join(self.dir, 'dccore.lock')!r})
                print("holding", flush=True)
                time.sleep(60)
            """)], stdout=subprocess.PIPE, text=True)

        def end():
            child.kill()
            child.wait(timeout=10)
            child.stdout.close()
        self.addCleanup(end)
        self.assertEqual(child.stdout.readline().strip(), "holding")
        self.assertEqual(self.running(), 0)


class WithNoWindow(Case):
    """What pythonw gives the bot - no streams at all - simulated anywhere:
    oserve.py run as the program with sys.stdout and sys.stderr set to None,
    up to its entry point (the way test_importing_oserve_touches_nothing
    drives it)."""

    def test_writes_work_and_go_to_the_log_even_with_the_log_turned_off(self):
        src = read("oserve.py")
        entry_start = src.index("\n    startup()\n", src.index("def run_forever(")) + 1
        entry_end = src.index("    run_forever()", entry_start) + len("    run_forever()")
        # The entry point's body - startup(), the watcher, run_forever() -
        # replaced by three writes, one of each kind.
        body = chr(10).join("    " + line for line in (
            'print("reached the entry point")',
            'sys.stdout.write("a direct write, the way Flask writes" + chr(10))',
            'sys.stderr.write("and one to stderr" + chr(10))',
        ))
        stub = src[:entry_start] + body + src[entry_end:]
        handle_fd, stub_path = tempfile.mkstemp(suffix=".py", prefix="_oserve_windowless_", dir=REPO_ROOT)
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(stub)
        self.addCleanup(os.remove, stub_path)
        runner_fd, runner = tempfile.mkstemp(suffix=".py", dir=self.dir)
        with os.fdopen(runner_fd, "w", encoding="utf-8") as handle:
            handle.write(textwrap.dedent(f"""
                import runpy, sys
                sys.path.insert(0, {REPO_ROOT!r})   # where oserve.py is, as when it is run
                sys.stdout = None
                sys.stderr = None
                runpy.run_path({stub_path!r}, run_name="__main__")
            """))
        self.write_settings(CONSOLE_LOG_FILE="")
        done = subprocess.run([sys.executable, runner], cwd=self.dir, env=self.env(),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        log = os.path.join(self.dir, "data", "logs", "dccore.log")
        self.assertTrue(os.path.isfile(log), "a windowless bot keeps the default log")
        with io.open(log, encoding="utf-8") as handle:
            text = handle.read()
        for line in ("reached the entry point", "a direct write, the way Flask writes", "and one to stderr"):
            self.assertIn(line, text)


class TheLauncher(unittest.TestCase):
    def setUp(self):
        self.bat = read("scripts/windows/start-dccore.bat").replace("\r\n", "\n")
        self.go = self.bat.split("\n:go\n", 1)[1]

    def test_the_first_run_keeps_its_window(self):
        self.assertIn('if not "%BROWSER_SETUP%"=="1" (\n    %PY% scripts\\windows\\window-mode.py', self.go)

    def test_the_codes_are_the_helpers(self):
        helper = read("scripts/windows/window-mode.py")
        self.assertIn("NORMAL, MINIMISED, HIDDEN = 0, 20, 21", helper)
        self.assertIn('if "%WINDOW_MODE%"=="21" goto :go_hidden', self.go)
        self.assertIn('if "%WINDOW_MODE%"=="20" goto :go_minimised', self.go)

    def test_both_ask_whether_one_is_running_before_starting(self):
        for label in (":go_minimised", ":go_hidden"):
            body = self.bat.split("\n" + label + "\n", 1)[1]
            self.assertTrue(body.startswith("call :already_running && exit /b 4\n"), label)

    def test_hidden_is_pythonw_and_minimised_is_start_min(self):
        hidden = self.bat.split("\n:go_hidden\n", 1)[1].split("\n:started_elsewhere\n", 1)[0]
        self.assertIn('set "PYW=%PY:python.exe=pythonw.exe%"', hidden)
        self.assertIn('if /i "%PY%"=="py -3" set "PYW=pyw -3"', hidden)
        self.assertIn('start "DCCore" %PYW% oserve.py', hidden)
        minimised = self.bat.split("\n:go_minimised\n", 1)[1].split("\n:go_hidden\n", 1)[0]
        self.assertIn('start "DCCore" /min %PY% oserve.py', minimised)

    def test_nothing_waits_for_a_key_after_it_started_elsewhere(self):
        """The logon task has nobody at the keyboard."""
        tail = self.bat.split("\n:started_elsewhere\n", 1)[1].split("\n:already_running\n", 1)[0]
        self.assertNotIn("pause", tail)
        self.assertIn("ping -n 8 127.0.0.1 >nul", tail)


if __name__ == "__main__":
    unittest.main()
