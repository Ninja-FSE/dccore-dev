"""A pasted list of requests is taken one by one, not punished (#888).

Every line to the bot counted toward the flood gate - ten per five seconds -
file requests included. A user pasting fifteen rows of one album had lines
1-10 queued, line 11 muted them, and line 12 escalated the mute into a
one-hour ban, whenever their client sent faster than two lines a second.
And the mute notice said "queue cleared" when only their pending replies
were dropped - their files stayed queued - so a user who believed it asked
again, which during a mute is exactly what earns the ban.

The operator's decision: take requests one by one as fast as the IRC server
lets them through; the queue cap is the limit. Driven through the real
irc_loop() against a scripted server, so the gate is executed, not read.
"""

import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import announce  # noqa: E402
import dcc  # noqa: E402
import defaults as config  # noqa: E402
import irc  # noqa: E402
import list as list_mod  # noqa: E402
import security  # noqa: E402

from tests import test_the_bots_own_nick_follows_the_server as own  # noqa: E402
from tests.support import DCCoreTestCase  # noqa: E402
from tests.test_what_a_user_typed_reaches_the_log_printable import _Records  # noqa: E402

CHANNEL = "#somechannel"
BOT = "SomeBot"


class APasteInTheChannel(own.DrivesPastRegistration):

    def setUp(self):
        super().setUp()
        self.set_config(MAX_REQUESTS=10, REQUEST_WINDOW=5, MUTE_TIME=30,
                        FLOOD_BAN_SECONDS=3600)
        config.muted_until.clear()
        config.banned_users.clear()
        config.user_requests.clear()
        self.addCleanup(config.muted_until.clear)
        self.addCleanup(config.banned_users.clear)
        self.addCleanup(config.user_requests.clear)
        _Records.started = []
        irc.threading.Thread = _Records

    def line(self, nick, text):
        return ":%s!~u@%s.example PRIVMSG %s :%s" % (nick, nick.lower(), CHANNEL, text)

    def paste(self, nick, rows):
        self.oserve.queued[:] = []
        self.run_registration(own.NOTICE_AUTH, own.welcome(BOT),
                              *[self.line(nick, row) for row in rows])

    def requests_started(self, nick):
        return [args for target, args in _Records.started
                if target is dcc.handle_download_request and args[1] == nick]

    def warnings_to(self, nick):
        return [m for _u, m, *_ in self.oserve.queued
                if m.startswith("NOTICE %s :" % nick) and "moving too fast" in m]

    def test_thirty_request_rows_are_all_taken_and_nobody_is_punished(self):
        rows = ["!%s Some Band - Track %02d.flac" % (BOT, n) for n in range(1, 31)]

        self.paste("mara", rows)

        self.assertEqual(len(self.requests_started("mara")), 30,
                         "a pasted row was dropped by the flood gate")
        self.assertNotIn("mara", config.muted_until)
        self.assertNotIn("mara", config.banned_users)
        self.assertEqual(self.warnings_to("mara"), [])

    def test_folder_requests_are_taken_the_same_way(self):
        rows = ["!%s !rar Some Band\\Album %02d\\" % (BOT, n) for n in range(1, 16)]

        self.paste("mara", rows)

        self.assertEqual(len(self.requests_started("mara")), 15)
        self.assertNotIn("mara", config.banned_users)

    def test_searches_are_still_metered(self):
        """The gate is untouched for everything that is not a file request."""
        rows = ["@find some band %d" % n for n in range(12)]

        self.paste("tobi", rows)

        self.assertIn("tobi", config.banned_users,
                      "an 11th search mutes, a 12th during the mute bans - as before")

    def test_requests_during_a_mute_are_taken_and_never_escalate_it(self):
        """Muted for flooding searches, then pasting rows: the rows are
        served and the mute does not become a ban because of them."""
        rows = ["@find x %d" % n for n in range(11)]
        rows += ["!%s Some Band - Track %02d.flac" % (BOT, n) for n in range(1, 6)]

        self.paste("lin", rows)

        self.assertIn("lin", config.muted_until, "the 11th search muted them")
        self.assertNotIn("lin", config.banned_users, "a request line escalated the mute")
        self.assertEqual(len(self.requests_started("lin")), 5)

    def test_a_ban_still_refuses_requests(self):
        import time
        config.banned_users["ivo"] = time.time() + 3600

        self.paste("ivo", ["!%s Some Band - Track 01.flac" % BOT])

        self.assertEqual(self.requests_started("ivo"), [])


class TheMuteSaysWhatIsTrue(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.set_config(MAX_REQUESTS=2, REQUEST_WINDOW=5, MUTE_TIME=30)
        config.muted_until.clear()
        config.user_requests.clear()
        self.addCleanup(config.muted_until.clear)
        self.addCleanup(config.user_requests.clear)
        self.sent = []
        self.debug = []
        for target, value in (
                ((self.oserve, "queue_message"), lambda user, text, *a, **k: self.sent.append(text)),
                ((announce, "send_debug"), lambda text, category="INFO", **k: self.debug.append(text))):
            patch = mock.patch.object(*target, value)
            patch.start()
            self.addCleanup(patch.stop)

    def trip(self):
        with mock.patch("builtins.print"):
            for _ in range(3):
                security.is_flooding("dave")

    def test_the_user_is_told_their_queued_files_are_kept(self):
        self.trip()

        warning = [m for m in self.sent if "moving too fast" in m]
        self.assertEqual(len(warning), 1, self.sent)
        self.assertIn("Your queued files are kept", warning[0])
        self.assertIn("Searches and other commands are ignored for 30 seconds", warning[0])
        self.assertNotIn("queue cleared", warning[0].lower())

    def test_the_operator_is_told_the_same(self):
        self.trip()

        mute = [m for m in self.debug if "moving too fast" in m]
        self.assertEqual(len(mute), 1, self.debug)
        self.assertIn("queued files kept", mute[0])
        self.assertNotIn("queue cleared", mute[0].lower())

    def test_it_is_true_the_file_queue_is_untouched(self):
        """The claim the notice now makes, checked."""
        row = {"file": "Track.flac", "path": "/music/Track.flac"}
        config.dcc_queue["dave"] = [row]
        self.addCleanup(config.dcc_queue.pop, "dave", None)

        self.trip()

        self.assertEqual(config.dcc_queue.get("dave"), [row])


class QueueFullIsSaidOnce(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        announce._queue_full_told.clear()
        self.addCleanup(announce._queue_full_told.clear)
        self.sent = []
        patch = mock.patch.object(self.oserve, "queue_message",
                                  lambda user, text, *a, **k: self.sent.append((user, text)))
        patch.start()
        self.addCleanup(patch.stop)

    def test_fifty_lines_past_the_cap_get_one_refusal(self):
        for _ in range(50):
            announce.send_dcc_error("mara", "user_full")

        self.assertEqual(len(self.sent), 1)
        self.assertIn("Requests past it were not queued", self.sent[0][1])

    def test_each_user_is_told_once_of_their_own(self):
        announce.send_dcc_error("mara", "user_full")
        announce.send_dcc_error("tobi", "user_full")
        announce.send_dcc_error("mara", "global_full")

        self.assertEqual(len(self.sent), 3)

    def test_told_again_once_the_window_has_passed(self):
        announce.send_dcc_error("mara", "user_full")
        for key in list(announce._queue_full_told):
            announce._queue_full_told[key] -= announce.QUEUE_FULL_REPEAT_SECONDS + 1
        announce.send_dcc_error("mara", "user_full")

        self.assertEqual(len(self.sent), 2)

    def test_other_errors_are_not_deduplicated(self):
        for _ in range(3):
            announce.send_dcc_error("mara", "file_not_found")

        self.assertEqual(len(self.sent), 3)


if __name__ == "__main__":
    unittest.main()
