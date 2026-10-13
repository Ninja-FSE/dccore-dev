"""A rebuild's progress report is its own, and there from its first step (#1361).

Three ways the "rebuilding" state could go wrong, found by checking every way
a rebuild can end:

1. A run killed for stalling, or ended by a crash or a reboot, never cleared
   its progress file. The next rebuild's watch read that old report as its
   own child's, "silent for hours", and killed it at the first tick - before
   it had written anything - and the one after, every time.
2. A rebuild that blocked before its first report (statting its folders on a
   mount that stopped answering) was never ended, since with no report the
   watch cannot tell a stall from a slow start; and with no phase to read,
   every search and file request was refused meanwhile, until a restart.
3. Between raising the flags and starting the rebuild's thread nothing took
   them down again on an exception.

Every nick, path and token here is invented.
"""

import contextlib
import io
import json
import os
import sys
import time
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import commands  # noqa: E402
import defaults as config  # noqa: E402
import list as list_mod  # noqa: E402
import runtime  # noqa: E402
import subprocess  # noqa: E402
import update_list  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402
from tests.test_a_working_rebuild_is_not_hung import FakeChild  # noqa: E402

TOKEN = "a1b2c3d4e5f6a1b2c3d4e5f6"
OTHER = "ffeeddccbbaaffeeddccbbaa"


class ProgressCase(DCCoreTestCase):
    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()
        self.progress = os.path.join(self.tree.root, "progress.json")
        self.set_config(LIST_PROGRESS_FILE=self.progress)

    def reported(self, seconds_ago, token, phase="scanning"):
        with io.open(self.progress, "w", encoding="utf-8") as handle:
            json.dump({"phase": phase, "at": time.time() - seconds_ago, "token": token}, handle)


class WhoseReportItIs(ProgressCase):

    def test_another_runs_report_is_not_this_childs(self):
        self.reported(1200, OTHER)
        self.assertIsNone(commands.last_progress_at(token=TOKEN))

    def test_this_childs_report_is_read(self):
        self.reported(5, TOKEN)
        self.assertAlmostEqual(commands.last_progress_at(token=TOKEN), time.time() - 5, delta=2)

    def test_a_report_with_no_token_is_read_as_before(self):
        """A terminal's run, or a build from before #1182."""
        self.reported(5, "")
        self.assertIsNotNone(commands.last_progress_at(token=TOKEN))

    def test_without_a_token_to_compare_every_report_is_read(self):
        self.reported(5, OTHER)
        self.assertIsNotNone(commands.last_progress_at())


class TheWatchAndALeftoverReport(ProgressCase):
    """run_watching_for_a_stall() with the token the bot gives the child."""

    def setUp(self):
        super().setUp()
        real_popen = subprocess.Popen
        self.addCleanup(setattr, subprocess, "Popen", real_popen)

    def install(self, child):
        subprocess.Popen = lambda *a, **k: child
        return child

    def test_a_leftover_report_does_not_kill_the_next_rebuild(self):
        """The old run's report is twenty minutes old; this child has not
        written yet. It is left alone and finishes."""
        child = self.install(FakeChild(ticks=3, returncode=0))
        self.reported(1200, OTHER)
        result = commands.run_watching_for_a_stall(
            ["x"], stall=900, tick=0.01, env={update_list.RUN_TOKEN_ENV: TOKEN})
        self.assertFalse(child.killed)
        self.assertEqual(result.returncode, 0)

    def test_its_own_silence_is_still_a_stall(self):
        child = self.install(FakeChild(ticks=None))
        self.reported(1200, TOKEN)
        with self.assertRaises(commands.ListUpdateStalled):
            commands.run_watching_for_a_stall(
                ["x"], stall=900, tick=0.01, env={update_list.RUN_TOKEN_ENV: TOKEN})
        self.assertTrue(child.killed)

    def test_a_stalled_child_leaves_no_report_behind(self):
        self.install(FakeChild(ticks=None))
        self.reported(1200, TOKEN)
        with self.assertRaises(commands.ListUpdateStalled):
            commands.run_watching_for_a_stall(
                ["x"], stall=900, tick=0.01, env={update_list.RUN_TOKEN_ENV: TOKEN})
        self.assertFalse(os.path.exists(self.progress))

    def test_a_child_past_the_ceiling_leaves_no_report_behind(self):
        self.install(FakeChild(ticks=None))
        self.reported(0, TOKEN)
        with self.assertRaises(subprocess.TimeoutExpired):
            commands.run_watching_for_a_stall(["x"], ceiling=0.001, stall=900, tick=0.01)
        self.assertFalse(os.path.exists(self.progress))


class TheFirstReport(ProgressCase):
    """rebuild_main() reports "starting" before it looks at any folder."""

    def setUp(self):
        super().setUp()
        self.missing = os.path.join(self.tree.root, "unplugged")
        self.set_config(FILE_DIRECTORY=self.missing)

    def run_main(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return update_list.rebuild_main(log=print)

    def test_it_is_written_before_the_folders_are_looked_at(self):
        seen = []
        real = update_list.platform_compat.long_path

        def long_path(path):
            if path == self.missing:
                seen.append(update_list.read_phase())
            return real(path)

        with mock.patch.object(update_list.platform_compat, "long_path", long_path):
            self.assertEqual(self.run_main(), 1)
        self.assertEqual(seen, [update_list.STARTING_PHASE])

    def test_a_run_that_stops_at_the_checks_leaves_no_report(self):
        self.assertEqual(self.run_main(), 1)
        self.assertFalse(os.path.exists(self.progress))

    def test_no_folder_configured_leaves_no_report_either(self):
        self.set_config(FILE_DIRECTORY="")
        with mock.patch("library.lists", return_value=[]):
            self.assertEqual(self.run_main(), 1)
        self.assertFalse(os.path.exists(self.progress))

    def test_it_never_writes_over_a_running_rebuilds_report(self):
        """Refused for the lock first: the report there is the other run's."""
        self.reported(3, OTHER, phase="publishing")
        with open(self.progress, encoding="utf-8") as handle:
            before = handle.read()
        with mock.patch.object(update_list, "take_list_lock", return_value=(4321, "rebuild")):
            self.assertEqual(self.run_main(), 1)
        with open(self.progress, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), before)

    def test_starting_pauses_nothing(self):
        """Searches and file requests are answered from the current list
        while a rebuild starts, as they are while it scans."""
        self.set_config(update_inprogress=True, PAUSE_ON_UPDATE=True, PAUSE_FOR_WHOLE_UPDATE=False)
        self.reported(0, TOKEN, phase=update_list.STARTING_PHASE)
        self.assertFalse(list_mod.rebuild_pauses_requests())
        self.reported(0, TOKEN, phase="publishing")
        self.assertTrue(list_mod.rebuild_pauses_requests())


class NothingLeavesTheFlagsUp(DCCoreTestCase):
    """An exception between raising the flags and starting the thread."""

    def setUp(self):
        super().setUp()
        self.set_config(update_inprogress=False, search_inprogress=False,
                        PAUSE_ON_UPDATE=True, PAUSE_FOR_WHOLE_UPDATE=True)

    def request(self):
        with contextlib.redirect_stdout(io.StringIO()):
            commands.handle_list_update_request("someadmin", "#somechannel", authorised=True)

    def test_a_thread_that_cannot_start(self):
        with mock.patch("threading.Thread.start", side_effect=RuntimeError("can't start new thread")):
            with self.assertRaises(RuntimeError):
                self.request()
        self.assertFalse(config.update_inprogress)
        self.assertFalse(config.search_inprogress)

    def test_the_count_before_it_failing(self):
        with mock.patch.object(commands, "count_by_list", side_effect=OSError("mount gone")):
            with self.assertRaises(OSError):
                self.request()
        self.assertFalse(config.update_inprogress)
        self.assertFalse(config.search_inprogress)

    def test_a_reading_whose_watch_cannot_start_is_not_left_running(self):
        def cannot(*_a, **_k):
            raise RuntimeError("can't start new thread")
        with self.assertRaises(RuntimeError):
            commands.start_audio_watch(object(), "rebuild", start=cannot)
        self.assertIsNone(runtime.audio_reading)


if __name__ == "__main__":
    unittest.main()
