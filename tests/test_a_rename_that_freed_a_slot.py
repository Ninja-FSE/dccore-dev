"""Three /nick commands took every slot the bot has.

`dcc.handle_download_request()`'s admission gate is built from exactly three
things, and all three are keyed on the CURRENT nick:

    user_already_transferring   - a row in config.active_transfers
    user_is_processing          - membership of config.user_processing_lock
    user_has_queue              - a row in config.dcc_queue

`irc.note_nick_change()` carried the third and left the other two behind, so
a rename read as a different person arriving with nothing in flight and the
same human was handed another immediate slot (#431).

It needs three renames and three requests. The flood gate allows ten requests
in five seconds, so nothing about it looks like abuse from the outside, and
there is no crash and no data loss - just every other user of the bot waiting
behind one person who typed /nick twice.
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

from tests.support import DCCoreTestCase, install_fake_oserve  # noqa: E402


class WhatARenameCarries(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        install_fake_oserve()
        config.channel_users["#chan"] = {"someuser"}
        config.user_processing_lock = {"someuser"}
        config.active_transfers = [{"user": "SomeUser", "file": "a.bin",
                                    "bytes_sent": 0}]

    def test_the_processing_lock_follows_the_person(self):
        irc.note_nick_change("SomeUser", "SomeUser2")

        self.assertIn("someuser2", config.user_processing_lock)
        self.assertNotIn("someuser", config.user_processing_lock)

    def test_the_transfer_row_follows_the_person(self):
        irc.note_nick_change("SomeUser", "SomeUser2")

        self.assertEqual([row["user"] for row in config.active_transfers],
                         ["SomeUser2"])

    def test_the_row_keeps_the_case_the_server_gave(self):
        """Every store here keys on lower() and displays what the server
        actually said - active_transfers is read by the dashboard and by
        !que, so a lowercased nick would be visible to the operator."""
        irc.note_nick_change("SomeUser", "SomeUser2")

        self.assertEqual(config.active_transfers[0]["user"], "SomeUser2")

    def test_it_says_what_it_carried(self):
        moved = irc.note_nick_change("SomeUser", "SomeUser2")

        self.assertIn("processing_lock", moved)
        self.assertIn("active_transfers", moved)

    def test_somebody_elses_transfer_is_left_alone(self):
        config.active_transfers.append({"user": "Another", "file": "b.bin",
                                        "bytes_sent": 0})

        irc.note_nick_change("SomeUser", "SomeUser2")

        self.assertEqual([row["user"] for row in config.active_transfers],
                         ["SomeUser2", "Another"])

    def test_a_rename_with_nothing_in_flight_carries_nothing(self):
        config.user_processing_lock = set()
        config.active_transfers = []

        moved = irc.note_nick_change("SomeUser", "SomeUser2")

        self.assertNotIn("processing_lock", moved)
        self.assertNotIn("active_transfers", moved)

    def test_a_transfers_list_that_is_not_a_list_is_survived(self):
        """irc.py shadows the `list` builtin with its own `import list`, so
        the obvious isinstance(x, list) raises TypeError here - the same trap
        #418 hit. Whatever this is, the rename must not take the read loop
        down with it."""
        config.active_transfers = None

        irc.note_nick_change("SomeUser", "SomeUser2")

        self.assertIn("someuser2", config.user_processing_lock)


class ThreeNicksAndThreeSlots(DCCoreTestCase):
    """The attack itself, through the real request path rather than a model
    of it: one person, three renames, and every slot the bot has."""

    def setUp(self):
        super().setUp()
        install_fake_oserve()
        tree = self.make_tree()
        self.file_name = "SomeTrack.mp3"
        with open(os.path.join(tree.music, self.file_name), "wb") as handle:
            handle.write(b"x" * 2048)

        config.MAX_DCC_SLOTS = 3
        # The channel the harness says this bot is in. A request arriving
        # through any other one is refused before the gate is reached.
        self.channel = config.CHANNEL
        config.channel_users[self.channel] = {"someuser"}

        # The send itself is not what is being tested, and starting it would
        # want a socket and a peer. What matters is whether the gate ADMITS
        # the request - so record who got through.
        self.admitted = []
        real = dcc.start_dcc_send
        self.addCleanup(setattr, dcc, "start_dcc_send", real)
        dcc.start_dcc_send = lambda sock, user, *a, **k: self.admitted.append(user)

    def request_as(self, nick):
        config.channel_users[self.channel].add(nick.lower())
        dcc.handle_download_request(None, nick, self.file_name, self.channel)

    def test_one_person_renaming_cannot_take_every_slot(self):
        """Before the fix this admitted all three: the gate saw a stranger
        each time, because the two interlocks still named who they used to
        be."""
        self.request_as("SomeUser")
        irc.note_nick_change("SomeUser", "SomeUser2")
        self.request_as("SomeUser2")
        irc.note_nick_change("SomeUser2", "SomeUser3")
        self.request_as("SomeUser3")

        self.assertEqual(len(self.admitted), 1,
                         "a rename still buys another slot: admitted %r"
                         % (self.admitted,))

    def test_the_slots_are_still_there_for_everybody_else(self):
        self.request_as("SomeUser")
        irc.note_nick_change("SomeUser", "SomeUser2")
        self.request_as("SomeUser2")

        self.assertLess(len(config.active_transfers), config.MAX_DCC_SLOTS)

    def test_a_different_person_is_still_served(self):
        """The fix must not turn into "one transfer at a time for the whole
        bot" - the interlock is per user, and two people are two users."""
        self.request_as("SomeUser")
        self.request_as("Another")

        self.assertEqual(sorted(self.admitted), ["Another", "SomeUser"])


class TheGateReadsWhatTheRenameWrites(unittest.TestCase):
    """The three keys are one set of facts read in two places. Asserted over
    the source so it holds for the next store somebody adds to either side."""

    @staticmethod
    def source(name):
        import io
        with io.open(os.path.join(REPO_ROOT, name), encoding="utf-8") as f:
            return f.read()

    def test_the_gate_is_built_from_the_three_the_rename_carries(self):
        gate = self.source("dcc.py").split(
            "user_already_transferring = any(", 1)[1].split("if not user_already_transferring", 1)[0]
        rename = self.source("irc.py").split("def note_nick_change(", 1)[1]
        rename = rename.split("def ", 1)[0]

        self.assertIn("user_processing_lock", gate)
        self.assertIn("user_processing_lock", rename)
        self.assertIn("active_transfers", rename)
        self.assertIn("dcc_queue", rename)
