"""A pasted album is taken through the real irc_loop, not only on paper (#888).

#894 exempted file requests from the flood gate and tested the pieces: the
exemption expression lifted out of irc.py, the mute notice's wording, the
file queue left untouched, and the queue-full notice said once. What nothing
executed was the gate's own `if` - `is_bot_command and not is_file_request
and security.is_flooding(user)`. With `not is_file_request` taken back out of
that line the whole suite stayed green (6414 OK, checked on main), so the
behaviour the operator asked for - "bot should just add them to queue one by
one as he requests" - had no test that would notice it going.

These drive irc_loop() against a scripted server, the #789 harness: a paste
of rows arrives as channel lines, the loop's threads are recorded, and the
mute and ban tables are read afterwards.
"""

import os
import sys
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dcc  # noqa: E402
import defaults as config  # noqa: E402
import irc  # noqa: E402

from tests import test_the_bots_own_nick_follows_the_server as own  # noqa: E402
from tests.test_what_a_user_typed_reaches_the_log_printable import _Records  # noqa: E402

CHANNEL = "#somechannel"
BOT = "SomeBot"


class APasteInTheChannel(own.DrivesPastRegistration):

    def setUp(self):
        super().setUp()
        self.set_config(MAX_REQUESTS=10, REQUEST_WINDOW=5, MUTE_TIME=30,
                        FLOOD_BAN_SECONDS=3600)
        for table in (config.muted_until, config.banned_users, config.user_requests):
            table.clear()
            self.addCleanup(table.clear)
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
        """Everything that is not a file request meets the gate as before."""
        self.paste("tobi", ["@find some band %d" % n for n in range(12)])

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
        config.banned_users["ivo"] = time.time() + 3600

        self.paste("ivo", ["!%s Some Band - Track 01.flac" % BOT])

        self.assertEqual(self.requests_started("ivo"), [])


if __name__ == "__main__":
    unittest.main()
