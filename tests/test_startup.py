"""oserve.py's boot sequence - the one path CI could never execute.

Every module was imported and every unit tested, but the boot itself only ran
when somebody started the real bot, which connects to Undernet and joins live
channels. It is also the first thing a Windows port meets, so these run on both
platforms in CI.

Nothing here touches the network: run_forever() is the only thing that calls
irc.irc_loop(), and it is always given a stub.
"""

import importlib
import io
import os
import sys
import threading
import unittest
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if os.path.join(REPO_ROOT, "tests") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import config  # noqa: E402
import db  # noqa: E402
import irc  # noqa: E402
import queue_mgr  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


def real_oserve():
    """The actual module, not tests.support's stub.

    support.install_fake_oserve() replaces sys.modules["oserve"] so the rest of
    the suite can run single-threaded. These tests need the real thing, and test
    order is not guaranteed, so it is re-imported rather than assumed.
    """
    sys.modules.pop("oserve", None)
    return importlib.import_module("oserve")


class BootCase(DCCoreTestCase):
    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()
        config.FILE_DIRECTORY = self.tree.music
        config.LOCAL_LIST_DIR = self.tree.lists
        config.BANS_FILE = os.path.join(self.tree.root, "bans.txt")
        config.DCC_QUEUE_FILE = os.path.join(self.tree.root, "dcc_queue.txt")

        self.oserve = real_oserve()

        # startup() spawns the flood-queue worker. Stubbed so the suite does not
        # accumulate a live worker thread per test.
        self.workers = []
        self._real_worker = queue_mgr.queue_worker
        queue_mgr.queue_worker = lambda: self.workers.append(1)
        self.addCleanup(lambda: setattr(queue_mgr, "queue_worker", self._real_worker))

    def boot(self):
        """Run startup(), capturing its console output."""
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.oserve.startup()
        return buffer.getvalue()


class StartupRunsOnThisPlatform(BootCase):
    """The smoke test: the whole boot, on whichever OS is running it."""

    def test_a_clean_boot_succeeds(self):
        output = self.boot()
        self.assertIn(config.SCRIPT_VERSION, output)

    def test_it_starts_exactly_one_queue_worker(self):
        """The comment in oserve calls this out: one [QUEUE] line at boot."""
        self.boot()
        # The thread is real even though the target is stubbed; give it a moment.
        for _ in range(200):
            if self.workers:
                break
            threading.Event().wait(0.01)
        self.assertEqual(len(self.workers), 1)

    def test_a_missing_music_directory_stops_the_daemon(self):
        """sys.exit(1) - unchanged from before the split."""
        config.FILE_DIRECTORY = os.path.join(self.tree.root, "not-there")
        with self.assertRaises(SystemExit) as caught:
            self.boot()
        self.assertEqual(caught.exception.code, 1)

    def test_it_warns_but_continues_with_no_master_list(self):
        """A fresh install has no list yet; that must not stop the boot."""
        output = self.boot()
        self.assertIn("No file list found", output)

    def test_it_reports_the_list_when_there_is_one(self):
        name = f"{config.LIST_BASE_NAME}-2026-08-26.txt"
        with open(os.path.join(self.tree.lists, name), "w", encoding="utf-8") as handle:
            handle.write("List of 0 Files\n")
        output = self.boot()
        self.assertIn(name, output)

    def test_it_loads_saved_bans(self):
        config.banned_users = {"dave": 9999999999.0}
        db.save_bans_to_file()
        config.banned_users = {}
        self.boot()
        self.assertIn("dave", config.banned_users)

    def test_it_loads_the_saved_queue(self):
        loaded = []
        real = db.load_dcc_queue
        db.load_dcc_queue = lambda: loaded.append(1)
        self.addCleanup(lambda: setattr(db, "load_dcc_queue", real))
        self.boot()
        self.assertEqual(len(loaded), 1, "a queue saved before a restart must come back")

    def test_no_bans_file_is_not_an_error(self):
        self.assertFalse(os.path.exists(config.BANS_FILE))
        self.boot()


class ReconnectLoopResetsTheGlobals(BootCase):
    """The trap this refactor had to avoid.

    irc_connection and bot_joined_channel used to be assigned at MODULE level
    inside __main__, so they rebound oserve.irc_connection and
    oserve.bot_joined_channel - which irc.py and dcc.py reach through
    sys.modules to find the live socket. Moved into a function without a global
    declaration they become locals, the reconnect cleanup silently stops
    happening, and nothing reports it.
    """

    class BreakOut(Exception):
        """Ends the otherwise infinite loop after one pass."""

    def run_one_pass(self, loop_raises):
        self.oserve.irc_connection = "a stale socket"
        self.oserve.bot_joined_channel = True

        real_loop, real_sleep = irc.irc_loop, self.oserve.time.sleep

        def stub_loop():
            raise loop_raises

        def stub_sleep(_seconds):
            raise self.BreakOut()

        irc.irc_loop = stub_loop
        self.oserve.time.sleep = stub_sleep
        self.addCleanup(lambda: setattr(irc, "irc_loop", real_loop))
        self.addCleanup(lambda: setattr(self.oserve.time, "sleep", real_sleep))

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            try:
                self.oserve.run_forever()
            except self.BreakOut:
                pass
        return buffer.getvalue()

    def test_a_dropped_connection_clears_the_socket_reference(self):
        self.run_one_pass(RuntimeError("network died"))
        self.assertIsNone(self.oserve.irc_connection,
                          "a stale socket left here is handed to the next connection")

    def test_a_dropped_connection_clears_the_joined_flag(self):
        self.run_one_pass(RuntimeError("network died"))
        self.assertFalse(self.oserve.bot_joined_channel)

    def test_the_advert_is_told_the_connection_is_gone(self):
        import announce
        announce.is_ready = True
        self.run_one_pass(RuntimeError("network died"))
        self.assertFalse(announce.is_ready)

    def test_a_crash_in_the_loop_is_reported_not_swallowed(self):
        output = self.run_one_pass(RuntimeError("network died"))
        self.assertIn("CRITICAL MAIN ERROR", output)
        self.assertIn("network died", output)

    def test_ctrl_c_exits_cleanly(self):
        real_loop = irc.irc_loop

        def stub_loop():
            raise KeyboardInterrupt()

        irc.irc_loop = stub_loop
        self.addCleanup(lambda: setattr(irc, "irc_loop", real_loop))

        with self.assertRaises(SystemExit) as caught:
            with redirect_stdout(io.StringIO()):
                self.oserve.run_forever()
        self.assertEqual(caught.exception.code, 0)


class TheEntryPointStillWiresItUp(unittest.TestCase):
    """Splitting the block is only safe if __main__ still calls both halves."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "oserve.py"), encoding="utf-8") as handle:
            self.source = handle.read()

    def test_main_calls_startup_then_run_forever(self):
        tail = self.source.split('if __name__ == "__main__":', 1)[1]
        self.assertIn("startup()", tail)
        self.assertIn("run_forever()", tail)
        self.assertLess(tail.index("startup()"), tail.index("run_forever()"),
                        "the boot must happen before the network loop")

    def test_the_reconnect_loop_declares_its_globals(self):
        """Belt and braces alongside the behavioural tests above."""
        body = self.source.split("def run_forever", 1)[1]
        self.assertIn("global irc_connection, bot_joined_channel", body)


if __name__ == "__main__":
    unittest.main()
