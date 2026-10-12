"""A list rebuild stops with the bot that started it (#1356).

update_list.py is a process of its own. Stopped from Tools > Stop while a
scheduled rebuild was scanning, the bot ended and the rebuild did not: half an
hour later it was at folder 10 of 20, holding the list lock, and the restarted
bot's Update list was refused with "Another list rebuild is already running".

Now the rebuild asks, while it scans and once more before it publishes,
whether the bot is stopping (the stop file the bot writes) or gone (the bot's
pid, for a bot that ended without its shutdown). Stopped, it publishes nothing
and the previous list stays in use. From the swap on it is never stopped: the
new list is the one in use by then.

Rebuilds of a real throwaway library, in this process; the stop arrives
through the same file and the same question the running child asks.
"""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import commands  # noqa: E402
import defaults as config  # noqa: E402
import list as list_mod  # noqa: E402
import update_list  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


def hold_the_list_lock(case):
    """As rebuild_main() does: only a run holding the lock is stopped."""
    case.assertIsNone(update_list.take_list_lock("rebuild"))
    case.addCleanup(update_list.release_list_lock)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class TheQuestion(DCCoreTestCase):
    """rebuild_stop_reason(): what it answers, and how often it looks."""

    def setUp(self):
        super().setUp()
        tree = self.make_tree()
        self.set_config(LIST_PROGRESS_FILE=os.path.join(tree.root, "progress.json"))
        update_list.reset_rebuild_stop()
        self.addCleanup(update_list.reset_rebuild_stop)
        self.addCleanup(update_list.clear_stop_request)
        hold_the_list_lock(self)
        self.clock = Clock()

    def ask(self, force=False, gone=False):
        return update_list.rebuild_stop_reason(clock=self.clock, force=force,
                                               should_stop=lambda: gone)

    def test_nothing_asked_and_the_bot_alive_is_no_stop(self):
        self.assertIsNone(self.ask(force=True))

    def test_a_build_that_does_not_hold_the_lock_is_never_stopped(self):
        """Another caller's build; a stop file there is somebody else's."""
        update_list.release_list_lock()
        update_list.request_stop()
        self.assertIsNone(self.ask(force=True, gone=True))
        self.assertIsNone(update_list.take_list_lock("rebuild"))

    def test_taking_the_lock_forgets_an_earlier_answer(self):
        update_list.request_stop()
        self.ask(force=True)
        update_list.release_list_lock()
        hold_the_list_lock(self)
        self.assertIsNone(self.ask(force=True))

    def test_the_stop_file_is_the_bot_stopping(self):
        update_list.request_stop()
        self.assertEqual(self.ask(force=True), "the bot is stopping and asked it to stop")

    def test_a_gone_bot_is_a_stop(self):
        self.assertEqual(self.ask(force=True, gone=True), "the bot that started it is no longer running")

    def test_the_answer_is_kept(self):
        """So a multi-list run does not start the next list once the file is
        cleared."""
        update_list.request_stop()
        self.ask(force=True)
        update_list.clear_stop_request()
        self.assertIsNotNone(self.ask(force=True))
        update_list.reset_rebuild_stop()
        self.assertIsNone(self.ask(force=True))

    def test_it_looks_at_most_once_a_second_unless_forced(self):
        self.assertIsNone(self.ask())
        update_list.request_stop()
        self.clock.now += 0.5
        self.assertIsNone(self.ask(), "looked again within the second")
        self.assertIsNotNone(self.ask(force=True))

    def test_a_second_later_it_looks_again(self):
        self.assertIsNone(self.ask())
        update_list.request_stop()
        self.clock.now += update_list.REBUILD_STOP_CHECK_SECONDS
        self.assertIsNotNone(self.ask())


class ARebuildThatIsStopped(DCCoreTestCase):
    """generate_all_lists() over a real library, stopped at each place it asks."""

    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()
        self.set_config(LIST_BASE_NAME="alfa", NICKNAME="alfa",
                        LIST_PROGRESS_FILE=os.path.join(self.tree.root, "progress.json"))
        self.addCleanup(update_list.clear_stop_request)
        self.addCleanup(update_list.reset_rebuild_stop)
        # Not started by a bot unless a test says so.
        env = mock.patch.dict(os.environ, {update_list.DAEMON_PID_ENV: ""})
        env.start()
        self.addCleanup(env.stop)
        # A first list, published, that a stopped rebuild must leave alone.
        self.assertTrue(self.rebuild())
        self.latest = list_mod.find_latest_list()
        with io.open(self.latest, encoding="utf-8") as handle:
            self.published = handle.read()
        self.files_before = sorted(os.listdir(self.tree.lists))
        # And something new in the library, so a rebuild that went through
        # would have written a different list.
        with open(os.path.join(self.tree.album, "03 - Holier Than Thou.flac"), "wb") as handle:
            handle.write(b"\x00" * 4096)

    def rebuild(self):
        """As rebuild_main() runs it: holding the list lock."""
        self.out = io.StringIO()
        self.assertIsNone(update_list.take_list_lock("rebuild"))
        try:
            with contextlib.redirect_stdout(self.out):
                result = update_list.generate_all_lists(log=print)
        finally:
            update_list.release_list_lock()
        self.output = self.out.getvalue()
        return result

    def assert_the_previous_list_is_in_use(self):
        self.assertEqual(sorted(os.listdir(self.tree.lists)), self.files_before)
        self.assertEqual(list_mod.find_latest_list(), self.latest)
        with io.open(self.latest, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), self.published)
        self.assertFalse(os.path.exists(config.LIST_PROGRESS_FILE), "the progress file was left behind")

    def test_a_stop_during_the_scan_publishes_nothing_and_ends_the_walk(self):
        """Looked at every directory here (the once-a-second limit set to
        0), so the walk ends after the directory the stop arrived in; and
        the walk is closed before the run says it stopped."""
        real = update_list.walk_with_sizes
        yielded, closed_after_saying_so = [], []

        def walk(top, onerror=None, workers=None):
            inner = real(top, onerror=onerror, workers=workers)
            try:
                for index, item in enumerate(inner):
                    if index == 0:
                        update_list.request_stop()     # the bot stops
                    yielded.append(item[0])
                    yield item
            finally:
                closed_after_saying_so.append("Stopped before publishing" in self.out.getvalue())
                inner.close()

        with mock.patch.object(update_list, "walk_with_sizes", walk),                 mock.patch.object(update_list, "REBUILD_STOP_CHECK_SECONDS", 0):
            self.assertFalse(self.rebuild())
        self.assertIn("Stopped before publishing: the bot is stopping", self.output)
        self.assertEqual(len(yielded), 1, "the walk went on after the stop")
        self.assertEqual(closed_after_saying_so, [False])
        self.assert_the_previous_list_is_in_use()

        # The next run, with the stop gone, is not stopped by this one's answer.
        update_list.clear_stop_request()
        self.assertTrue(self.rebuild())

    def test_a_stop_after_the_scan_is_honoured_before_the_swap(self):
        real_progress = update_list.write_progress
        swapped = []

        def progress(phase, *args, **kwargs):
            if phase == "writing":
                update_list.request_stop()
            return real_progress(phase, *args, **kwargs)

        with mock.patch.object(update_list, "write_progress", progress), \
                mock.patch.object(update_list, "_publish_artifacts", lambda swaps: swapped.append(swaps)):
            self.assertFalse(self.rebuild())
        self.assertEqual(swapped, [])
        self.assertIn("stopped before publishing", self.output)
        self.assert_the_previous_list_is_in_use()
        self.assertEqual([n for n in os.listdir(self.tree.lists) if n.endswith(".new")], [],
                         "a staged list was left behind")

    def test_a_bot_that_is_gone_stops_it(self):
        with mock.patch.dict(os.environ, {update_list.DAEMON_PID_ENV: "4242"}), \
                mock.patch.object(update_list.platform_compat, "pid_alive", lambda pid: False), \
                mock.patch.object(update_list, "reading_should_stop",
                                  lambda: not update_list.platform_compat.pid_alive(
                                      os.environ[update_list.DAEMON_PID_ENV])):
            self.assertFalse(self.rebuild())
        self.assertIn("the bot that started it is no longer running", self.output)
        self.assert_the_previous_list_is_in_use()

    def test_a_rebuild_run_by_hand_is_not_stopped(self):
        """No bot pid, no stop file: a terminal's rebuild goes through."""
        self.assertTrue(self.rebuild())
        with io.open(list_mod.find_latest_list(), encoding="utf-8") as handle:
            self.assertIn("Holier Than Thou", handle.read())

    def test_a_bot_that_is_alive_does_not_stop_it(self):
        with mock.patch.object(update_list, "reading_should_stop", lambda: False):
            self.assertTrue(self.rebuild())


class AStopBetweenFolders(DCCoreTestCase):
    """Two served folders: a stop that arrives as the first one's walk ends
    is seen before the second is started, not a second later inside it."""

    def test_the_next_folder_is_not_walked(self):
        import library
        tree = self.make_tree()
        second = os.path.join(tree.root, "more")
        os.makedirs(os.path.join(second, "Some Band", "Some Album (2001)"))
        with open(os.path.join(second, "Some Band", "Some Album (2001)", "01 - Intro.flac"), "wb") as handle:
            handle.write(b"\x00" * 4096)
        self.set_config(LIST_BASE_NAME="alfa", NICKNAME="alfa",
                        LIST_PROGRESS_FILE=os.path.join(tree.root, "progress.json"),
                        LIBRARY_FOLDERS_FILE=os.path.join(tree.root, "library_folders.json"))
        library.save_folders([library.Folder("Music", tree.music), library.Folder("More", second)])
        self.addCleanup(update_list.clear_stop_request)
        self.addCleanup(update_list.reset_rebuild_stop)
        hold_the_list_lock(self)

        real = update_list.walk_with_sizes
        walked = []

        def walk(top, onerror=None, workers=None):
            walked.append(top)
            inner = real(top, onerror=onerror, workers=workers)
            try:
                yield from inner
            finally:
                update_list.request_stop()      # the bot stops as this folder ends
                inner.close()

        with mock.patch.dict(os.environ, {update_list.DAEMON_PID_ENV: ""}),                 mock.patch.object(update_list, "walk_with_sizes", walk),                 contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(update_list.generate_all_lists(log=print))
        self.assertEqual(len(walked), 1, walked)


class AStoppedMultiListRun(DCCoreTestCase):

    def test_no_further_list_is_built(self):
        built = []

        def build(name=None, **kwargs):
            built.append(name)
            update_list.request_stop()
            update_list.rebuild_stop_reason(force=True)
            return False

        lists = [mock.Mock(name="a", folders=[]), mock.Mock(name="b", folders=[])]
        lists[0].name, lists[1].name = "Main", "Films"
        self.addCleanup(update_list.clear_stop_request)
        self.addCleanup(update_list.reset_rebuild_stop)
        tree = self.make_tree()
        self.set_config(LIST_PROGRESS_FILE=os.path.join(tree.root, "progress.json"))
        hold_the_list_lock(self)
        with mock.patch.object(update_list, "generate_master_list", build), \
                mock.patch("library.lists", return_value=lists), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertFalse(update_list.generate_all_lists(log=print))
        self.assertEqual(built, ["Main"])
        self.assertIn("Not building 'Films'", out.getvalue())


class TheBotStopsItsRebuild(DCCoreTestCase):
    """commands.stop_rebuild(), which the shutdown calls."""

    def setUp(self):
        super().setUp()
        tree = self.make_tree()
        self.set_config(LIST_PROGRESS_FILE=os.path.join(tree.root, "progress.json"),
                        update_inprogress=True)
        self.addCleanup(update_list.clear_stop_request)
        self.clock = Clock()

    def stop(self, holders, wait=1.0):
        answers = list(holders)

        def holder():
            return answers.pop(0) if len(answers) > 1 else answers[0]

        def sleep(seconds):
            self.clock.now += seconds

        with mock.patch.object(update_list, "list_lock_holder", holder), \
                contextlib.redirect_stdout(io.StringIO()):
            return commands.stop_rebuild(wait=wait, poll=0.25, sleep=sleep, clock=self.clock)

    def test_it_asks_and_clears_the_request_once_the_lock_is_free(self):
        seen = []
        real = update_list.request_stop
        with mock.patch.object(update_list, "request_stop", lambda: seen.append(1) or real()):
            self.assertTrue(self.stop([(77, "rebuild"), (77, "rebuild"), None]))
        self.assertEqual(seen, [1])
        self.assertFalse(update_list.stop_requested())

    def test_a_rebuild_still_holding_on_keeps_the_request(self):
        """It may be in the middle of a directory; the file is what it will see."""
        self.assertTrue(self.stop([(77, "rebuild")], wait=1.0))
        self.assertTrue(update_list.stop_requested())

    def test_no_rebuild_of_its_own_is_left_alone(self):
        self.set_config(update_inprogress=False)
        self.assertFalse(self.stop([(77, "rebuild")]))
        self.assertFalse(update_list.stop_requested())

    def test_a_reading_is_for_stop_audio_reading(self):
        self.assertFalse(self.stop([(77, "reading")]))
        self.assertFalse(update_list.stop_requested())

    def test_the_shutdown_stops_the_rebuild_before_the_reading(self):
        with io.open(os.path.join(REPO_ROOT, "oserve.py"), encoding="utf-8") as handle:
            code = handle.read()
        rebuild = code.find("_commands_stop.stop_rebuild(wait=10.0)")
        reading = code.find("_commands_stop.stop_audio_reading(wait=10.0)")
        self.assertGreater(rebuild, 0)
        self.assertGreater(reading, rebuild)


if __name__ == "__main__":
    unittest.main()
