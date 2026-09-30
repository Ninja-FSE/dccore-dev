"""A freed slot goes to the nick that has waited longest (#1032).

The nick that had just finished was handed its own next file straight away,
so a nick with a long queue took every slot in turn and the nicks behind it
waited for that queue to run dry. Now the finishing nick goes to the back of
the line, and a new request does not jump ahead of nicks already waiting.
"""

import io
import itertools
import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dcc  # noqa: E402
import defaults as config  # noqa: E402
import runtime  # noqa: E402

from tests import test_the_feed_says_which_channel as feed  # noqa: E402
from tests.support import queue_row  # noqa: E402

OTHER = feed.OTHER
NAMES = ["A.flac", "B.flac", "C.flac", "D.flac", "E.flac"]


class Case(feed.ServesARealRequest):
    def setUp(self):
        super().setUp()
        for name in NAMES:
            with io.open(os.path.join(self.tree.music, name), "wb") as handle:
                handle.write(b"\x00" * 4096)
        self.set_config(MAX_DCC_SLOTS=1, transfers_paused=False)
        config.channel_users[OTHER] = {"dave", "erin", "frank"}
        # A clock that only moves forward, so two events never share a stamp
        # on a platform with a coarse one.
        ticks = itertools.count(1000)
        patch = mock.patch.object(dcc.time, "time", lambda: float(next(ticks)))
        patch.start()
        self.addCleanup(patch.stop)

    def running(self):
        return [tx["user"] for tx in config.active_transfers]

    def queued(self, user):
        return [row["file"] for row in config.dcc_queue.get(user, [])]

    def finish(self, user):
        config.active_transfers[:] = [tx for tx in config.active_transfers if tx["user"] != user]
        config.user_processing_lock.discard(user)
        dcc.go_to_the_back(user)

    def pick_up(self, user):
        self.quietly(dcc.check_queue_and_send, user)

    def started(self):
        return [(args[1], args[3]) for name, args in feed.InlineThread.dispatched if name == "start_dcc_send"]


class TheFinishedNickGoesToTheBack(Case):
    def test_the_slot_goes_to_the_nick_that_waited_not_the_one_that_finished(self):
        self.request("A.flac", user="dave")
        self.request("B.flac", user="dave")
        self.request("C.flac", user="erin")
        self.request("D.flac", user="frank")
        self.assertEqual(self.running(), ["dave"])
        self.finish("dave")
        self.pick_up("dave")
        self.assertEqual(self.running(), ["erin"])

    def test_and_so_on_round_the_line(self):
        self.request("A.flac", user="dave")
        self.request("B.flac", user="dave")
        self.request("C.flac", user="erin")
        self.request("D.flac", user="frank")
        order = []
        for finished in ("dave", "erin", "frank"):
            self.finish(finished)
            self.pick_up(finished)
            order.extend(self.running())
        self.assertEqual(order, ["erin", "frank", "dave"])

    def test_with_nobody_else_waiting_the_same_nick_carries_on(self):
        self.request("A.flac", user="dave")
        self.request("B.flac", user="dave")
        self.finish("dave")
        self.pick_up("dave")
        self.assertEqual(self.running(), ["dave"])
        self.assertEqual(self.started()[-1], ("dave", "B.flac"))

    def test_a_trigger_for_a_nick_that_waited_less_still_gives_the_slot_to_the_longest(self):
        self.request("A.flac", user="dave")
        self.request("C.flac", user="erin")
        self.request("D.flac", user="frank")
        self.finish("dave")
        self.pick_up("frank")
        self.assertEqual(self.running(), ["erin"])


class ANewRequestDoesNotJumpTheLine(Case):
    def test_it_queues_behind_a_nick_that_is_waiting(self):
        self.request("A.flac", user="dave")
        self.request("C.flac", user="erin")
        self.finish("dave")
        self.request("D.flac", user="frank")
        self.assertEqual(self.running(), [])
        self.assertEqual(self.queued("frank"), ["D.flac"])
        self.pick_up("dave")
        self.assertEqual(self.running(), ["erin"])

    def test_it_sends_at_once_when_nobody_is_waiting(self):
        self.request("A.flac", user="frank")
        self.assertEqual(self.running(), ["frank"])

    def test_a_nick_that_is_absent_is_not_waited_for(self):
        config.channel_users[OTHER] = {"dave", "frank"}
        config.dcc_queue["erin"] = [queue_row(user="erin", filename="C.flac")]
        self.request("A.flac", user="frank")
        self.assertEqual(self.running(), ["frank"])

    def test_a_frozen_nick_is_not_waited_for(self):
        config.dcc_queue["erin"] = [queue_row(user="erin", filename="C.flac")]
        config.frozen_queues["erin"] = 1
        self.request("A.flac", user="frank")
        self.assertEqual(self.running(), ["frank"])


class TheSweepKeepsTheSameOrder(Case):
    def test_it_takes_the_longest_waiting_whatever_the_insertion_order(self):
        config.dcc_queue["dave"] = [queue_row(user="dave", filename="A.flac")]
        config.dcc_queue["erin"] = [queue_row(user="erin", filename="C.flac")]
        config.dcc_queue["frank"] = [queue_row(user="frank", filename="D.flac")]
        runtime.queue_waiting_since.update({"dave": 30.0, "erin": 10.0, "frank": 20.0})
        self.pick_up("system_next_trigger_fallback")
        self.assertEqual(self.running(), ["erin"])

    def test_a_folder_pack_waiting_does_not_hold_back_a_plain_file(self):
        config.dcc_queue["erin"] = [queue_row(user="erin", filename="Album.rar", is_unpacked_rar_folder=True,
                                              is_temporary_zip=True)]
        config.dcc_queue["dave"] = [queue_row(user="dave", filename="A.flac")]
        runtime.queue_waiting_since.update({"erin": 1.0, "dave": 2.0})
        self.pick_up("dave")
        self.assertEqual(self.running(), ["dave"])


class TheWaitIsStamped(Case):
    def test_the_first_queued_file_starts_the_wait_and_the_next_does_not_restart_it(self):
        self.request("A.flac", user="dave")
        self.request("B.flac", user="erin")
        first = runtime.queue_waiting_since["erin"]
        self.request("C.flac", user="erin")
        self.assertEqual(runtime.queue_waiting_since["erin"], first)

    def test_a_nick_that_empties_its_queue_starts_a_new_wait_next_time(self):
        self.request("A.flac", user="dave")
        self.request("B.flac", user="erin")
        first = runtime.queue_waiting_since["erin"]
        config.dcc_queue.pop("erin")
        self.request("C.flac", user="erin")
        self.assertGreater(runtime.queue_waiting_since["erin"], first)

    def test_a_finished_send_puts_the_nick_at_the_back(self):
        self.request("A.flac", user="dave")
        self.request("B.flac", user="erin")
        before = runtime.queue_waiting_since["erin"]
        self.finish("dave")
        self.assertGreater(runtime.queue_waiting_since["dave"], before)

    def test_a_nick_nothing_has_stamped_counts_as_waiting_longest(self):
        self.assertEqual(dcc.queue_waiting_since("unknown"), 0.0)


if __name__ == "__main__":
    unittest.main()
