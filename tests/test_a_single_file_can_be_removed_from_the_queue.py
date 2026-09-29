"""`@<nick>-remove <file>` takes one file out of the user's queue; the bare
`@<nick>-remove` still clears all of it (#1021)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import announce  # noqa: E402
import commands  # noqa: E402
import db  # noqa: E402
import defaults as config  # noqa: E402
import irc  # noqa: E402
from tests.support import DCCoreTestCase, RecordingSocket, no_disk_writes, silence_debug  # noqa: E402
from tests.test_irc_dispatch import FLOOD_GATE_SOURCE, _evaluate  # noqa: E402


def row(name, path=None, **extra):
    return dict({"file": name, "path": path or "/music/" + name, "channel": "#c",
                 "user_raw": "dave", "is_temporary_zip": False}, **extra)


class OneFile(DCCoreTestCase):
    def setUp(self):
        super().setUp()
        self.sock = RecordingSocket()
        silence_debug(announce)
        no_disk_writes(db)
        self.notices = []
        import types
        self._old = sys.modules.get("oserve")
        sys.modules["oserve"] = types.SimpleNamespace(
            queue_message=lambda to, msg, *a, **k: self.notices.append(msg))
        self.addCleanup(lambda: sys.modules.__setitem__("oserve", self._old) if self._old
                        else sys.modules.pop("oserve", None))
        config.frozen_queues = {}
        config.active_transfers = []

    def test_only_the_named_file_goes(self):
        config.dcc_queue = {"dave": [row("One Song.mp3"), row("Two Song.mp3")]}
        commands.handle_queue_remove_file(self.sock, "dave", "#c", "One Song.mp3")
        self.assertEqual([r["file"] for r in config.dcc_queue["dave"]], ["Two Song.mp3"])
        self.assertIn("Removed", self.notices[0])

    def test_underscores_and_case_do_not_matter(self):
        config.dcc_queue = {"dave": [row("Some Artist - 11 - Some Song.mp3")]}
        commands.handle_queue_remove_file(
            self.sock, "dave", "#c", "some_artist_-_11_-_some_song.MP3")
        self.assertNotIn("dave", config.dcc_queue)

    def test_a_name_that_is_not_queued_changes_nothing_and_says_so(self):
        config.dcc_queue = {"dave": [row("One Song.mp3")]}
        commands.handle_queue_remove_file(self.sock, "dave", "#c", "Other.mp3")
        self.assertEqual(len(config.dcc_queue["dave"]), 1)
        self.assertIn("is not in your queue", self.notices[0])

    def test_another_users_queue_is_untouched(self):
        config.dcc_queue = {"dave": [row("A.mp3")], "erin": [row("A.mp3")]}
        commands.handle_queue_remove_file(self.sock, "dave", "#c", "A.mp3")
        self.assertIn("erin", config.dcc_queue)

    def test_a_frozen_queue_loses_the_file_too(self):
        config.dcc_queue = {}
        config.frozen_queues = {"dave": [row("A.mp3"), row("B.mp3")]}
        commands.handle_queue_remove_file(self.sock, "dave", "#c", "A.mp3")
        self.assertEqual([r["file"] for r in config.frozen_queues["dave"]], ["B.mp3"])

    def test_the_bare_form_still_clears_the_whole_queue(self):
        config.dcc_queue = {"dave": [row("A.mp3"), row("B.mp3")]}
        commands.handle_queue_remove(self.sock, "dave", "#c")
        self.assertNotIn("dave", config.dcc_queue)

    def test_the_temp_archive_of_the_removed_file_goes_but_a_shared_one_stays(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        mine = os.path.join(d, "Mine.rar")
        shared = os.path.join(d, "Shared.rar")
        for p in (mine, shared):
            with open(p, "wb") as fh:
                fh.write(b"x")
        config.dcc_queue = {
            "dave": [row("Mine.rar", mine, is_temporary_zip=True),
                     row("Shared.rar", shared, is_temporary_zip=True),
                     row("Also Shared.rar", shared, is_temporary_zip=True)]}
        commands.handle_queue_remove_file(self.sock, "dave", "#c", "Mine.rar")
        commands.handle_queue_remove_file(self.sock, "dave", "#c", "Shared.rar")
        self.assertFalse(os.path.exists(mine))
        self.assertTrue(os.path.exists(shared), "another row of dave's still names it")

    def test_control_characters_are_not_echoed_back(self):
        config.dcc_queue = {"dave": [row("A.mp3")]}
        commands.handle_queue_remove_file(self.sock, "dave", "#c", "x\x02\x034,5y")
        self.assertNotIn("\x02", self.notices[0])
        self.assertNotIn("\x03", self.notices[0])


class TheFloodGateMetersIt(unittest.TestCase):
    def test_the_file_form_counts_as_a_bot_command(self):
        nick = config.NICKNAME
        self.assertTrue(_evaluate(FLOOD_GATE_SOURCE, f"@{nick}-remove Song.mp3"))
        self.assertTrue(_evaluate(FLOOD_GATE_SOURCE, f"@{nick}-remove"))
        self.assertTrue(_evaluate(FLOOD_GATE_SOURCE, "\x01REMOVE Song.mp3\x01"))
        self.assertFalse(_evaluate(FLOOD_GATE_SOURCE, f"@{nick}-removed"))


if __name__ == "__main__":
    unittest.main()
