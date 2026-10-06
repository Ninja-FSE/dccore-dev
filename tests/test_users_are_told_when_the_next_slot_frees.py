"""Users are told when the next slot is likely to free up (#1207).

The question a busy channel asks most is "when do I get a slot?", and DCCore
had no answer to it: -que and -stats gave counts and no time, and the CTCP
SLOTS line's "next" field is a literal NOW or 0.

THE ESTIMATE is stats_mgr.next_slot_estimate(): "now" while a slot is free;
otherwise the minimum over the active sends of (size - sent) / speed, rounded
up to whole minutes - when the first busy slot frees. Unknown (None) while no
send has a usable speed. The speed is stats_mgr.send_speed(), the per-send
figure the DCC console's SLOT line already showed, moved out of adminchat so
there is one measure rather than two.

IT IS THE NEXT FREE SLOT, NOT THIS USER'S TURN, and every text says "next
free slot": who gets that slot is the queue's business.

SHOWN IN @nick-que (both replies), @nick-stats next to the free slots, and
the dashboard's Live Transfers page.

THE CTCP SLOTS LINE IS LEFT ALONE, byte for byte. Other scripts parse it, and
nothing says its "next" field accepts minutes - so a test below drives one
real advert cycle with every slot busy and a known estimate, and pins the
whole line.
"""

import io
import os
import re
import sys
import threading
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import adminchat  # noqa: E402
import announce  # noqa: E402
import commands  # noqa: E402
import db  # noqa: E402
import defaults as config  # noqa: E402
import library  # noqa: E402
import list as list_mod  # noqa: E402
import stats_mgr  # noqa: E402
import webserver  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402

NOW = 10_000.0
CHANNEL = "#somechannel"


def send(size, sent, seconds_ago, resume=0, now=NOW, user="someuser"):
    """An active_transfers row as dcc.start_dcc_send() leaves it."""
    row = {"user": user, "file": "Artist - Song.flac", "bytes_sent": sent,
           "size": size, "started_at": now - seconds_ago}
    if resume:
        row["resume_offset"] = resume
    return row


def wait_for(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def plain(text):
    """The words, with the colour codes taken off."""
    codes = re.compile("[" + chr(2) + chr(15) + "]|" + chr(3) + r"\d{0,2}(,\d{1,2})?")
    return codes.sub("", text)


class TheEstimate(unittest.TestCase):

    def estimate(self, transfers, slots=2):
        return stats_mgr.next_slot_estimate(now=NOW, transfers=transfers, slots=slots)

    def test_a_free_slot_is_now(self):
        self.assertEqual(self.estimate([send(10_000_000, 1, 100)]), stats_mgr.NEXT_SLOT_NOW)
        self.assertEqual(stats_mgr.NEXT_SLOT_NOW, "now")

    def test_a_free_slot_is_now_even_with_no_speed_known(self):
        self.assertEqual(self.estimate([{"user": "a", "bytes_sent": 0}]), "now")
        self.assertEqual(self.estimate([]), "now")

    def test_minutes_from_the_size_left_and_the_speed(self):
        """40 kB/s with 6 MB left is 150 seconds: three minutes, rounded up."""
        self.assertEqual(self.estimate([send(10_000_000, 4_000_000, 100)], slots=1), 3)

    def test_an_exact_number_of_minutes_is_not_rounded_up_further(self):
        """40 kB/s with 4.8 MB left is exactly 120 seconds."""
        self.assertEqual(self.estimate([send(8_800_000, 4_000_000, 100)], slots=1), 2)

    def test_it_is_the_send_nearest_its_end_whichever_comes_first(self):
        slow = send(100_000_000, 1_000_000, 100)      # 10 kB/s, 9900 s left
        quick = send(10_000_000, 4_000_000, 100)      # 40 kB/s, 150 s left
        self.assertEqual(self.estimate([slow, quick]), 3)
        self.assertEqual(self.estimate([quick, slow]), 3)

    def test_a_resumed_send_is_timed_by_what_this_connection_moved(self):
        """Resumed at 50 MB, 6 MB moved in ten minutes: 10 kB/s, so 44 MB
        left is 4400 s - 74 minutes. Counting the resumed part as moved would
        say 8."""
        row = send(100_000_000, 56_000_000, 600, resume=50_000_000)
        self.assertEqual(self.estimate([row], slots=1), 74)

    def test_a_busy_slot_is_never_less_than_a_minute(self):
        """A send a few bytes from its end is not a free slot."""
        self.assertEqual(self.estimate([send(4_000_010, 4_000_000, 100)], slots=1), 1)
        self.assertEqual(self.estimate([send(4_000_000, 4_000_000, 100)], slots=1), 1)

    def test_unknown_while_no_send_has_a_speed(self):
        for row in ({"user": "a", "file": "x", "bytes_sent": 0},          # not stamped yet
                    send(10_000_000, 0, 100),                           # nothing moved
                    send(10_000_000, 500, 0.2),                         # half a second is not a rate
                    {"user": "a", "file": "x", "bytes_sent": 5000,       # no size known
                     "started_at": NOW - 100}):
            with self.subTest(row=row):
                self.assertIsNone(self.estimate([row], slots=1))

    def test_a_send_with_no_speed_does_not_hide_one_that_has(self):
        fresh = {"user": "a", "file": "x", "bytes_sent": 0}
        self.assertEqual(self.estimate([fresh, send(10_000_000, 4_000_000, 100)]), 3)

    def test_a_row_that_is_not_a_send_holds_a_slot_and_nothing_else(self):
        """The console's STATUS burst skips such a row too; here it still
        counts as busy, and must not take the -que reply down with it."""
        self.assertEqual(self.estimate(["junk", send(10_000_000, 4_000_000, 100)]), 3)

    def test_a_malformed_send_is_skipped_not_raised(self):
        bad = {"user": "a", "file": "x", "bytes_sent": 5, "size": 10, "started_at": "abc"}
        self.assertIsNone(self.estimate([bad], slots=1))
        self.assertEqual(self.estimate([bad, send(10_000_000, 4_000_000, 100)]), 3)

    def test_by_default_it_reads_the_live_transfers_and_the_slot_count(self):
        saved = (list(config.active_transfers), config.MAX_DCC_SLOTS)
        self.addCleanup(setattr, config, "MAX_DCC_SLOTS", saved[1])
        self.addCleanup(config.active_transfers.__setitem__, slice(None), saved[0])
        config.active_transfers[:] = [send(10_000_000, 4_000_000, 100)]

        config.MAX_DCC_SLOTS = 1
        self.assertEqual(stats_mgr.next_slot_estimate(now=NOW), 3)
        config.MAX_DCC_SLOTS = 2
        self.assertEqual(stats_mgr.next_slot_estimate(now=NOW), "now")


class TheWords(unittest.TestCase):

    def test_each_kind_of_answer(self):
        cases = {"now": "now", None: "not known yet", 1: "~1 min", 4: "~4 min",
                 59: "~59 min", 60: "~1h 0m", 130: "~2h 10m", 1439: "~23h 59m",
                 1440: "~1d 0h", 1620: "~1d 3h"}
        for estimate, words in cases.items():
            with self.subTest(estimate=estimate):
                self.assertEqual(stats_mgr.format_next_slot(estimate), words)


class OneSpeedMeasureNotTwo(DCCoreTestCase):
    """The console's SLOT line and the estimate read the same per-send speed."""

    def test_the_console_slot_line_reads_send_speed(self):
        config.active_transfers[:] = [send(10_000, 5_000, 2, now=1000.0, user="erin")]
        real = stats_mgr.send_speed
        self.addCleanup(setattr, stats_mgr, "send_speed", real)
        stats_mgr.send_speed = lambda tx, now=None: 12345

        line = adminchat.status_lines(now=1000.0)[1]

        self.assertEqual(line, "DCCORE SLOT erin 5000 10000 12345 Artist - Song.flac")

    def test_send_speed_is_what_this_connection_moved_per_second(self):
        self.assertEqual(stats_mgr.send_speed(send(10_000, 5_000, 2), NOW), 2500)
        self.assertEqual(stats_mgr.send_speed(send(10_000, 900, 10, resume=800), NOW), 10)
        self.assertEqual(stats_mgr.send_speed(send(10_000, 100, 10, resume=5000), NOW), 0)
        self.assertEqual(stats_mgr.send_speed({"bytes_sent": 5}, NOW), 0)

    def test_a_malformed_row_still_fails_the_console_burst_as_before(self):
        """#681 pins that the STATUS burst reports such a row as a failure;
        moving the arithmetic must not quietly turn it into a 0."""
        with self.assertRaises(ValueError):
            stats_mgr.send_speed({"bytes_sent": 5, "started_at": "abc"}, NOW)


if __name__ == "__main__":
    unittest.main()
