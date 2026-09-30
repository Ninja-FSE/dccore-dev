"""One nick can have several sends at once, when MAX_SENDS_PER_USER allows it (#1030).

A receiver that asked for ten files while two of three slots were free saw all
ten wait in the queue: a nick could only ever have one transfer running. The
default of 1 keeps that; above 1 a nick's own running sends are counted against
the setting instead, and MAX_DCC_SLOTS still bounds the total.
"""

import io
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dcc  # noqa: E402
import defaults as config  # noqa: E402

from tests import test_the_feed_says_which_channel as feed  # noqa: E402
from tests.support import queue_row  # noqa: E402

OTHER = feed.OTHER
NAMES = ["A.flac", "B.flac", "C.flac"]


class Case(feed.ServesARealRequest):
    def setUp(self):
        super().setUp()
        for name in NAMES:
            with io.open(os.path.join(self.tree.music, name), "wb") as handle:
                handle.write(b"\x00" * 4096)
        self.set_config(MAX_DCC_SLOTS=3, MAX_SENDS_PER_USER=1, LEND_SPARE_SLOTS=False, transfers_paused=False)

    def running(self, user="dave"):
        return sorted(tx["file"] for tx in config.active_transfers if tx["user"] == user)

    def queued(self, user="dave"):
        return [r["file"] for r in config.dcc_queue.get(user, [])]

    def sweep(self):
        self.quietly(dcc.check_queue_and_send, "system_next_trigger_fallback")

    def pick_up(self, user="dave"):
        self.quietly(dcc.check_queue_and_send, user)


class TheDefaultIsUnchanged(Case):
    def test_the_second_request_waits(self):
        self.request("A.flac")
        self.request("B.flac")
        self.assertEqual(self.running(), ["A.flac"])
        self.assertEqual(self.queued(), ["B.flac"])

    def test_a_queued_row_is_not_started_beside_a_running_one(self):
        config.dcc_queue["dave"] = [queue_row(filename="A.flac"), queue_row(filename="B.flac")]
        config.active_transfers.append({"user": "dave", "file": "A.flac", "bytes_sent": 0})
        self.pick_up()
        self.sweep()
        self.assertEqual(self.running(), ["A.flac"])


class WithAHigherCap(Case):
    def setUp(self):
        super().setUp()
        self.set_config(MAX_SENDS_PER_USER=2)

    def test_two_requests_both_send_at_once(self):
        self.request("A.flac")
        self.request("B.flac")
        self.assertEqual(self.running(), ["A.flac", "B.flac"])
        self.assertEqual(self.queued(), [])

    def test_the_third_waits(self):
        for name in NAMES:
            self.request(name)
        self.assertEqual(self.running(), ["A.flac", "B.flac"])
        self.assertEqual(self.queued(), ["C.flac"])

    def test_the_same_file_is_not_sent_twice_at_once(self):
        self.request("A.flac")
        self.request("A.flac")
        self.assertEqual(self.running(), ["A.flac"])
        self.assertEqual(self.queued(), ["A.flac"])

    def test_the_total_is_still_bounded_by_the_slots(self):
        self.set_config(MAX_DCC_SLOTS=1)
        self.request("A.flac")
        self.request("B.flac")
        self.assertEqual(self.running(), ["A.flac"])
        self.assertEqual(self.queued(), ["B.flac"])

    def test_a_queued_row_starts_beside_a_running_one(self):
        first, second = queue_row(filename="A.flac"), queue_row(filename="B.flac")
        config.dcc_queue["dave"] = [first, second]
        config.active_transfers.append({"user": "dave", "file": "A.flac", "bytes_sent": 0})
        self.pick_up()
        self.assertEqual(self.running(), ["A.flac", "B.flac"])

    def test_the_sweep_skips_the_row_already_in_flight(self):
        first, second = queue_row(filename="A.flac"), queue_row(filename="B.flac")
        config.dcc_queue["dave"] = [first, second]
        config.active_transfers.append({"user": "dave", "file": "A.flac", "bytes_sent": 0})
        self.sweep()
        self.assertEqual(self.running(), ["A.flac", "B.flac"])
        started = [args[3] for name, args in feed.InlineThread.dispatched if name == "start_dcc_send"]
        self.assertEqual(started, ["B.flac"])

    def test_at_the_cap_nothing_more_starts(self):
        config.dcc_queue["dave"] = [queue_row(filename=n) for n in NAMES]
        config.active_transfers.extend({"user": "dave", "file": n, "bytes_sent": 0} for n in NAMES[:2])
        self.pick_up()
        self.sweep()
        self.assertEqual(self.running(), ["A.flac", "B.flac"])

    def test_another_user_is_not_held_back_by_the_first(self):
        self.set_config(MAX_DCC_SLOTS=2)
        config.channel_users[OTHER].add("erin")
        self.request("A.flac", user="dave")
        self.request("B.flac", user="dave")
        self.request("C.flac", user="erin")
        self.assertEqual(self.running("erin"), [])
        self.assertEqual(self.queued("erin"), ["C.flac"])
        config.active_transfers.pop(0)
        self.sweep()
        self.assertEqual(self.running("erin"), ["C.flac"])


class SpareSlotsAreLent(Case):
    """One person, one slot while somebody else waits; idle slots are not left idle."""

    def setUp(self):
        super().setUp()
        self.set_config(LEND_SPARE_SLOTS=True)
        config.channel_users[OTHER].add("erin")

    def test_a_lone_user_fills_every_free_slot(self):
        for name in NAMES:
            self.request(name)
        self.assertEqual(self.running(), NAMES)
        self.assertEqual(self.queued(), [])

    def test_a_fourth_request_queues_until_a_slot_is_free(self):
        self.set_config(MAX_DCC_SLOTS=3)
        for name in NAMES:
            self.request(name)
        self.request("Song.flac", user="erin")
        self.assertEqual(self.running("erin"), [])
        self.assertEqual(self.queued("erin"), ["Song.flac"])

    def test_a_freed_slot_goes_to_the_waiting_user_not_the_one_holding_three(self):
        for name in NAMES:
            self.request(name)
        config.dcc_queue["dave"] = [queue_row(filename="D.flac")]
        self.request("Song.flac", user="erin")
        config.active_transfers.pop(0)
        self.pick_up("dave")
        self.assertEqual(self.running("dave"), ["B.flac", "C.flac"])
        self.assertEqual(self.running("erin"), [])
        self.sweep()
        self.assertEqual(self.running("erin"), ["Song.flac"])

    def test_with_someone_waiting_a_user_is_back_to_the_cap(self):
        self.set_config(MAX_DCC_SLOTS=3)
        config.dcc_queue["erin"] = [queue_row(user="erin", filename="Song.flac")]
        self.request("A.flac")
        self.request("B.flac")
        self.assertEqual(self.running(), ["A.flac"])
        self.assertEqual(self.queued(), ["B.flac"])

    def test_off_means_strictly_the_cap(self):
        self.set_config(LEND_SPARE_SLOTS=False)
        self.request("A.flac")
        self.request("B.flac")
        self.assertEqual(self.running(), ["A.flac"])
        self.assertEqual(self.queued(), ["B.flac"])

    def test_a_frozen_waiting_user_does_not_hold_the_slots_back(self):
        config.dcc_queue["erin"] = [queue_row(user="erin", filename="Song.flac")]
        config.frozen_queues["erin"] = 0
        self.request("A.flac")
        self.request("B.flac")
        self.assertEqual(self.running(), ["A.flac", "B.flac"])


class TheSetting(unittest.TestCase):
    def test_the_default_is_one(self):
        self.assertEqual(config.MAX_SENDS_PER_USER, 1)

    def test_spare_slots_are_lent_by_default(self):
        self.assertIs(config.LEND_SPARE_SLOTS, True)

    def test_nonsense_counts_as_one(self):
        real = getattr(config, "MAX_SENDS_PER_USER", 1)
        self.addCleanup(setattr, config, "MAX_SENDS_PER_USER", real)
        for bad in (0, -3, None, "many"):
            config.MAX_SENDS_PER_USER = bad
            self.assertEqual(dcc.sends_per_user_cap(), 1, bad)

    def test_it_has_a_help_text(self):
        import settings_help
        with open(settings_help.__file__, encoding="utf-8") as handle:
            self.assertIn("'MAX_SENDS_PER_USER':", handle.read())


if __name__ == "__main__":
    unittest.main()
