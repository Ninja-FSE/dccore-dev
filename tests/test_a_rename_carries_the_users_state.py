"""A user who changes nick keeps their queue, their freezer slot and their
presence.

`irc.py`'s NICK handler moved `send_queue` and nothing else. The server sends
that message once, naming the old nick and the new one, so every store still
keyed on the old name keeps it until something unrelated rebuilds it - and two
of those stores matter immediately:

  * `channel_users` is what `dcc.user_is_present_in_ram()` reads, and dcc.py
    treats it as proof somebody is there before dispatching to them or thawing
    a frozen queue. Left stale, a renamed user reads as absent under the name
    they now use and present under one nobody has, so their transfers stop
    while they are sitting in the channel.
  * `dcc_queue` holds their files. Left stale, `!que` under the new nick shows
    nothing and the bot has nobody to send them to.

Found while checking #376's premise that `channel_users` can serve as a
presence history: it has a hole at exactly the event that issue is trying to
reason about.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dcc  # noqa: E402
import defaults as config  # noqa: E402
import irc  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


class ARenameCarriesTheUsersState(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        config.channel_users["#one"] = {"someuser", "somebot"}
        config.dcc_queue["someuser"] = ["Track.flac"]
        config.send_queue["someuser"] = ["a message"]
        config.frozen_queues["someuser"] = 1234.0

    def test_presence_follows_them(self):
        """The one that silently stops transfers."""
        irc.note_nick_change("someuser", "someuser_")

        self.assertTrue(dcc.user_is_present_in_ram("someuser_"),
                        "the name they now use reads as absent")
        self.assertFalse(dcc.user_is_present_in_ram("someuser"),
                         "a name nobody has reads as present")

    def test_their_queued_files_follow_them(self):
        irc.note_nick_change("someuser", "someuser_")

        self.assertEqual(config.dcc_queue.get("someuser_"), ["Track.flac"])
        self.assertNotIn("someuser", config.dcc_queue)

    def test_their_freezer_slot_follows_them(self):
        irc.note_nick_change("someuser", "someuser_")

        self.assertEqual(config.frozen_queues.get("someuser_"), 1234.0)
        self.assertNotIn("someuser", config.frozen_queues)

    def test_the_outbound_queue_still_follows_them(self):
        """The one thing the handler already did. Kept, and now in the same
        place as the rest, so a rename is one operation rather than three
        that can drift apart."""
        irc.note_nick_change("someuser", "someuser_")

        self.assertEqual(config.send_queue.get("someuser_"), ["a message"])
        self.assertNotIn("someuser", config.send_queue)

    def test_it_reports_what_it_moved(self):
        moved = irc.note_nick_change("someuser", "someuser_")

        self.assertEqual(sorted(moved),
                         ["dcc_queue", "frozen_queues", "presence",
                          "send_queue"])

    def test_every_channel_we_share_with_them_not_just_one(self):
        """A NICK is not per-channel and the server sends it once. Updating
        only the channel it happened to arrive through would leave the others
        stale, and presence is answered across all of them."""
        config.channel_users["#two"] = {"someuser"}
        config.channel_users["#three"] = {"somebody-else"}

        irc.note_nick_change("someuser", "someuser_")

        self.assertEqual(config.channel_users["#one"], {"someuser_", "somebot"})
        self.assertEqual(config.channel_users["#two"], {"someuser_"})
        self.assertEqual(config.channel_users["#three"], {"somebody-else"},
                         "a channel they were never in was rewritten")

    def test_a_user_we_have_nothing_for_is_a_no_op(self):
        moved = irc.note_nick_change("astranger", "astranger_")

        self.assertEqual(moved, [])
        self.assertIn("someuser", config.dcc_queue)


class TheThingsItRefusesToDo(DCCoreTestCase):

    def test_it_never_inherits_another_users_queue(self):
        """Nicks are reused. If somebody else already holds the new name and
        has files queued under it, those are not this user's to take - and
        overwriting would hand one person another's queue."""
        config.dcc_queue["someuser"] = ["Mine.flac"]
        config.dcc_queue["someuser_"] = ["Theirs.flac"]

        irc.note_nick_change("someuser", "someuser_")

        self.assertEqual(config.dcc_queue["someuser_"], ["Theirs.flac"],
                         "the existing holder's queue was overwritten")
        self.assertEqual(config.dcc_queue["someuser"], ["Mine.flac"],
                         "and the renaming user's was dropped")

    def test_a_mute_does_not_follow_them(self):
        """Deliberate, and the reason is in the docstring: `muted_until` is
        nick-keyed, and carrying it would quietly turn a soft sanction into a
        hard one. DCCore already answers nick-hopping with hard bans, which
        match a HOSTMASK pattern. Changing that is an operator's decision, not
        a side effect of this fix."""
        config.muted_until["someuser"] = 9999999999.0

        irc.note_nick_change("someuser", "someuser_")

        self.assertIn("someuser", config.muted_until)
        self.assertNotIn("someuser_", config.muted_until)

    def test_a_ban_does_not_follow_them_either(self):
        config.banned_users["someuser"] = 9999999999.0

        irc.note_nick_change("someuser", "someuser_")

        self.assertNotIn("someuser_", config.banned_users)

    def test_it_does_not_invent_an_online_status(self):
        """`whois_status` caches what a WHO reply said. Asserting the new nick
        is online because the old one was is answering for the server."""
        config.whois_status["someuser"] = True

        irc.note_nick_change("someuser", "someuser_")

        self.assertNotIn("someuser_", config.whois_status)


class TheNamesItIsGiven(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        config.channel_users["#one"] = {"someuser"}
        config.dcc_queue["someuser"] = ["Track.flac"]

    def test_the_match_is_case_insensitive(self):
        """IRC nicks are, and the stores are keyed lower-cased. The server
        sends whatever casing the user typed."""
        irc.note_nick_change("SomeUser", "SomeUser_")

        self.assertIn("someuser_", config.dcc_queue)
        self.assertTrue(dcc.user_is_present_in_ram("SOMEUSER_"))

    def test_a_rename_to_the_same_name_changes_nothing(self):
        """Only the casing differs, which is the same nick on IRC. Moving a
        key onto itself is a no-op at best and a lost entry at worst."""
        moved = irc.note_nick_change("someuser", "SomeUser")

        self.assertEqual(moved, [])
        self.assertEqual(config.dcc_queue.get("someuser"), ["Track.flac"])

    def test_an_empty_name_is_ignored(self):
        """A malformed line must not empty anybody's queue into a "" key."""
        for old, new in (("", "someuser_"), ("someuser", ""), (None, None)):
            with self.subTest(old=old, new=new):
                self.assertEqual(irc.note_nick_change(old, new), [])

        self.assertEqual(config.dcc_queue.get("someuser"), ["Track.flac"])


class TheHandlerActuallyCallsIt(unittest.TestCase):
    """Structural: the read loop is not run here, and the whole point of
    moving this out of it was that it could not be reached otherwise."""

    @staticmethod
    def handler():
        import io

        with io.open(os.path.join(REPO_ROOT, "irc.py"), encoding="utf-8") as f:
            body = f.read()
        block = body.split('if is_user_event(line, "NICK"):', 1)[1]
        return block.split("# Anchored", 1)[0]

    def test_the_nick_branch_calls_the_helper(self):
        self.assertIn("note_nick_change(", self.handler())

    def test_it_no_longer_moves_only_the_send_queue_inline(self):
        """The shape of the original bug: one store handled at the call site,
        the others forgotten. If that line comes back, so does the defect."""
        self.assertNotIn("send_queue[new_nick.lower()]", self.handler())


if __name__ == "__main__":
    unittest.main()
