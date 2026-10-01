"""A !rar pack turned away at [DCC-BLOCK] is woken when a slot frees (#1034).

Only check_queue_and_send's own-user branch starts a folder pack, and it is only
ever handed the nick whose send just finished. A pack turned away because the
slots were full was therefore revisited only when a pack finished - a plain
file finishing woke nobody, and the sweep skipped pack heads.
"""

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


def pack_row(user):
    return queue_row(user=user, filename="Album.zip", is_unpacked_rar_folder=True, is_temporary_zip=True)


class Case(feed.ServesARealRequest):
    def setUp(self):
        super().setUp()
        self.set_config(MAX_DCC_SLOTS=1, transfers_paused=False, rar_inprogress=False)
        config.channel_users[OTHER] = {"dave", "erin", "frank"}
        ticks = itertools.count(1000)
        patch = mock.patch.object(dcc.time, "time", lambda: float(next(ticks)))
        patch.start()
        self.addCleanup(patch.stop)

    def sweep(self):
        feed.InlineThread.dispatched = []
        self.quietly(dcc.check_queue_and_send, "system_next_trigger_fallback")

    def waited(self, *nicks):
        for stamp, nick in enumerate(nicks, start=500):
            runtime.queue_waiting_since[nick] = float(stamp)

    def woken(self):
        return [args[1] for name, args in feed.InlineThread.dispatched if name == "check_queue_and_send"]

    def started(self):
        return [args[1] for name, args in feed.InlineThread.dispatched if name == "start_dcc_send"]


class AFreeSlotWakesTheWaitingPack(Case):
    def test_the_sweep_wakes_a_pack_when_the_slot_is_free(self):
        config.dcc_queue["dave"] = [pack_row("dave")]
        self.sweep()
        self.assertEqual(self.woken(), ["dave"])

    def test_a_full_slot_wakes_nobody(self):
        config.dcc_queue["dave"] = [pack_row("dave")]
        config.active_transfers.append({"user": "erin", "file": "X.flac", "bytes_sent": 0})
        self.sweep()
        self.assertEqual(self.woken(), [])

    def test_a_pack_in_progress_is_not_woken_over(self):
        config.dcc_queue["dave"] = [pack_row("dave")]
        config.rar_inprogress = True
        self.sweep()
        self.assertEqual(self.woken(), [])

    def test_a_pack_in_progress_does_not_hold_back_a_plain_file(self):
        config.dcc_queue["dave"] = [pack_row("dave")]
        config.dcc_queue["erin"] = [queue_row(user="erin", filename="A.flac")]
        config.rar_inprogress = True
        self.sweep()
        self.assertEqual(self.started(), ["erin"])

    def test_an_absent_nick_is_not_woken(self):
        config.dcc_queue["dave"] = [pack_row("dave")]
        config.channel_users[OTHER].discard("dave")
        self.sweep()
        self.assertEqual(self.woken(), [])

    def test_a_frozen_queue_is_not_woken(self):
        config.dcc_queue["dave"] = [pack_row("dave")]
        config.frozen_queues["dave"] = 0
        config.channel_users[OTHER].discard("dave")
        self.sweep()
        self.assertEqual(self.woken(), [])

    def test_a_nick_already_being_served_is_not_woken(self):
        config.dcc_queue["dave"] = [pack_row("dave")]
        config.user_processing_lock.add("dave")
        self.sweep()
        self.assertEqual(self.woken(), [])


    def test_with_a_slot_to_spare_the_pack_is_woken_and_the_plain_file_starts(self):
        self.set_config(MAX_DCC_SLOTS=2)
        config.dcc_queue["dave"] = [pack_row("dave")]
        config.dcc_queue["erin"] = [queue_row(user="erin", filename="A.flac")]
        self.waited("dave", "erin")
        self.sweep()
        self.assertEqual(self.woken(), ["dave"])
        self.assertEqual(self.started(), ["erin"])

    def test_one_slot_is_set_aside_for_the_pack_not_two(self):
        self.set_config(MAX_DCC_SLOTS=2)
        config.dcc_queue["dave"] = [pack_row("dave")]
        config.dcc_queue["erin"] = [pack_row("erin")]
        config.dcc_queue["frank"] = [queue_row(user="frank", filename="A.flac")]
        self.waited("dave", "erin", "frank")
        self.sweep()
        self.assertEqual(self.woken(), ["dave"])
        self.assertEqual(self.started(), ["frank"])



class InWaitOrder(Case):
    def test_the_nick_that_waited_longest_goes_first_when_it_is_the_pack(self):
        config.dcc_queue["dave"] = [pack_row("dave")]
        config.dcc_queue["erin"] = [queue_row(user="erin", filename="A.flac")]
        self.waited("dave", "erin")
        self.sweep()
        self.assertEqual(self.woken(), ["dave"])
        self.assertEqual(self.started(), [])

    def test_the_nick_that_waited_longest_goes_first_when_it_is_the_plain_file(self):
        config.dcc_queue["dave"] = [pack_row("dave")]
        config.dcc_queue["erin"] = [queue_row(user="erin", filename="A.flac")]
        self.waited("erin", "dave")
        self.sweep()
        self.assertEqual(self.started(), ["erin"])
        self.assertEqual(self.woken(), [])

    def test_the_pack_gets_its_turn_once_the_plain_send_has_gone(self):
        config.dcc_queue["dave"] = [pack_row("dave")]
        config.dcc_queue["erin"] = [queue_row(user="erin", filename="A.flac")]
        self.waited("erin", "dave")
        self.sweep()
        config.active_transfers.clear()
        config.user_processing_lock.discard("erin")
        config.dcc_queue.pop("erin", None)
        self.sweep()
        self.assertEqual(self.woken(), ["dave"])


if __name__ == "__main__":
    unittest.main()
