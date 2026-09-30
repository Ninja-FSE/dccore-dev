"""A fetch request goes out once, and is timed from when it goes out (#1028).

The dispatcher logged "Requested" and started the offer timer when it handed
the line to the send queue, where it waited its turn behind every other user's
replies. On a busy bot the timer ran out before the line was sent, each expiry
queued the request again behind the first, and a file was asked for four or
five times - and sent twice by the other bot.
"""

import os
import shutil
import sys
import time
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import announce  # noqa: E402
import dcc_fetch  # noqa: E402
import defaults as config  # noqa: E402

from tests.support import DCCoreTestCase, silence_debug  # noqa: E402

FILE = "Artist - Song.mp3"


class Case(DCCoreTestCase):
    def setUp(self):
        super().setUp()
        self.set_config(fetch_queue={}, MAX_FETCH_SLOTS=10, fetch_feature_disabled=False,
                        CHANNEL="#chan", FETCH_MAX_PER_BOT=0, bot_joined_channel=True,
                        FETCH_OFFER_TIMEOUT=60, vip_queue=[])
        config.channel_users["#chan"] = {"serverone"}
        silence_debug(announce)
        self.ordinary = []

        def queue_message(user, message, is_vip=False):
            (config.vip_queue if is_vip else self.ordinary).append(message)

        stand_in = types.ModuleType("oserve")
        stand_in.queue_message = queue_message
        real = sys.modules.get("oserve")
        sys.modules["oserve"] = stand_in
        self.addCleanup(lambda: sys.modules.__setitem__("oserve", real) if real
                        else sys.modules.pop("oserve", None))
        real_usage = shutil.disk_usage
        shutil.disk_usage = lambda path: shutil._ntuple_diskusage(10 ** 13, 0, 10 ** 12)
        self.addCleanup(setattr, shutil, "disk_usage", real_usage)

    def ask(self, name=FILE):
        rid = dcc_fetch.enqueue_fetch("ServerOne", name)
        self.assertIsNotNone(rid)
        dcc_fetch.check_fetch_queue()
        return rid

    def age(self, rid, seconds):
        config.fetch_queue[rid]["offered_at"] -= seconds


class TheLane(Case):
    def test_the_request_goes_in_the_express_lane(self):
        self.ask()
        self.assertEqual(len(config.vip_queue), 1)
        self.assertIn(f"!ServerOne {FILE}", config.vip_queue[0])
        self.assertEqual(self.ordinary, [])


class TheTimer(Case):
    def test_it_does_not_run_while_the_line_is_still_waiting(self):
        rid = self.ask()
        self.age(rid, 500)
        dcc_fetch.check_fetch_queue()
        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "offered")
        self.assertEqual(len(config.vip_queue), 1, "nothing was asked a second time")
        self.assertLess(time.time() - row["offered_at"], 5)

    def test_it_runs_once_the_line_has_gone(self):
        rid = self.ask()
        config.vip_queue.clear()
        self.age(rid, 500)
        dcc_fetch.check_fetch_queue()
        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "pending")
        self.assertEqual(row["silent_asks"], 2)
        dcc_fetch.check_fetch_queue()
        self.assertEqual(row["state"], "offered")
        self.assertEqual(len(config.vip_queue), 1)


class NoSecondLine(Case):
    def test_asking_again_replaces_a_line_still_waiting(self):
        rid = self.ask()
        config.fetch_queue[rid].update(state="pending", offered_at=None)
        dcc_fetch.check_fetch_queue()
        self.assertEqual(len(config.vip_queue), 1)

    def test_a_finished_row_takes_its_waiting_line_back(self):
        rid = self.ask()
        config.fetch_queue[rid]["state"] = "complete"
        dcc_fetch.check_fetch_queue()
        self.assertEqual(config.vip_queue, [])
        self.assertNotIn("request_line", config.fetch_queue[rid])

    def test_a_deleted_row_takes_its_waiting_line_back(self):
        import webserver
        rid = self.ask()
        status, _ = webserver.build_fetch_delete_result(rid)
        self.assertEqual(status, 200)
        self.assertEqual(config.vip_queue, [])

    def test_another_rows_line_is_left_alone(self):
        first = self.ask()
        self.ask("Other - Song.mp3")
        config.fetch_queue[first]["state"] = "failed"
        dcc_fetch.check_fetch_queue()
        self.assertEqual(len(config.vip_queue), 1)
        self.assertIn("Other - Song.mp3", config.vip_queue[0])


if __name__ == "__main__":
    unittest.main()
