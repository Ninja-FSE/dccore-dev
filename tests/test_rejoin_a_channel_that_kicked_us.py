"""Being thrown out is a thing that happens, and nothing noticed.

Reported from a live channel:

    "a slight bug there, dccore doesn't appear to rejoin a chan if kicked or
    banned, maybe add an option that it can try to rejoin when the advert
    timer triggers"

IT IS WORSE THAN NOT REJOINING. `KICK` was not parsed anywhere, so DCCore did
not know it had left. It went on advertising into a channel it was not in -
the server answers those with 404 and nothing reads it - and never asked to
come back. `474 ERR_BANNEDFROMCHAN` was not parsed either, so a refused join
was equally invisible.

THE RETRY RIDES ON THE ADVERT TIMER, as suggested, and that is the right
cadence for a reason beyond convenience: an instant rejoin reads as a fight
with whoever kicked us, and is how a kick becomes a ban. The advert interval
is already the bot's rhythm, and it is the moment it was about to speak there
anyway.

GIVING UP IS THE POINT, not retrying. A channel answering "you are banned"
will answer that way for as long as the ban stands, and a bot that keeps
asking earns a longer one. After REJOIN_ATTEMPTS refusals DCCore stops and
says so.

WHY THE RULE LIVES IN irc.py AND THE SEND IN announce.py. The read thread must
not block on a socket write, and the advert worker must not hold a lock or
know what a kick is. So irc.py counts and decides; the worker asks it what to
send.
"""

import os
import sys
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import irc  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402

OURS = "#somechannel"
ALSO_OURS = "#otherchannel"
NOT_OURS = "#somewhere-else"


class ParsingAKick(unittest.TestCase):

    def test_a_well_formed_kick(self):
        parsed = irc.parse_kick(
            ":someop!user@host KICK #somechannel SomeBot :go away")

        self.assertEqual(parsed, ("someop", "#somechannel", "SomeBot"))

    def test_a_channel_with_an_ampersand_in_its_name(self):
        """Legal per RFC 2812, and the thing six parsers here used to drop."""
        parsed = irc.parse_kick(
            ":someop!user@host KICK #rock&metal SomeBot :bye")

        self.assertEqual(parsed[1], "#rock&metal")

    def test_a_kick_typed_into_a_channel_is_not_a_kick(self):
        """Anchored on the server prefix. Unanchored, anyone could say this in
        a channel and have the bot act on it - the same forgery the PRIVMSG
        and user-event parsers were fixed for."""
        forged = (":someone!user@host PRIVMSG #somechannel :"
                  ":x!y@z KICK #somechannel SomeBot :got you")

        self.assertIsNone(irc.parse_kick(forged))

    def test_nonsense_is_not_a_kick(self):
        for line in ("", "KICK", ":only a prefix", ":a!b@c KICK #chan"):
            with self.subTest(line=line):
                self.assertIsNone(irc.parse_kick(line))


class ParsingARefusal(unittest.TestCase):

    def test_banned_from_channel(self):
        parsed = irc.parse_join_refusal(
            ":irc.example.net 474 SomeBot #somechannel :Cannot join channel (+b)")

        self.assertEqual(parsed, ("#somechannel", "474"))

    def test_the_other_three_a_join_can_be_refused_with(self):
        for numeric in ("471", "473", "475"):
            with self.subTest(numeric=numeric):
                line = ":irc.example.net %s SomeBot #somechannel :no" % numeric
                self.assertEqual(irc.parse_join_refusal(line)[1], numeric)

    def test_a_numeric_that_is_not_a_refusal_is_ignored(self):
        """366 is a join that WORKED. Counting it as a refusal would give up
        on every channel the bot successfully joined."""
        self.assertIsNone(irc.parse_join_refusal(
            ":irc.example.net 366 SomeBot #somechannel :End of /NAMES list."))

    def test_the_set_is_what_it_claims(self):
        """Guard on the guard: an empty set would make every assertion above
        about parsing pass and the counting never happen."""
        self.assertEqual(irc.JOIN_REFUSED_NUMERICS, {"471", "473", "474", "475"})


class WhatWeStartTrackingAndWhy(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.set_config(CHANNEL="%s, %s" % (OURS, ALSO_OURS),
                        REJOIN_ATTEMPTS=3)

    def test_a_kick_from_one_of_ours_is_recorded(self):
        self.assertTrue(irc.note_kicked_from(OURS, "someop"))
        self.assertIn(OURS, irc.channels_to_rejoin())

    def test_a_kick_from_somewhere_we_never_asked_to_be_is_not(self):
        """Somebody invited the bot, or the channel has been taken out of
        CHANNEL. Rejoining would be the bot deciding where it belongs."""
        self.assertFalse(irc.note_kicked_from(NOT_OURS, "someop"))
        self.assertEqual(irc.channels_to_rejoin(), [])

    def test_the_case_of_the_channel_does_not_matter(self):
        """IRC channel names are case-insensitive, and an operator types
        CHANNEL by hand."""
        self.assertTrue(irc.note_kicked_from(OURS.upper(), "someop"))
        self.assertIn(OURS, irc.channels_to_rejoin())

    def test_who_did_it_is_kept(self):
        irc.note_kicked_from(OURS, "someop")

        self.assertEqual(self.config.kicked_channels[OURS]["by"], "someop")


class TheCountIsWhatStopsIt(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.set_config(CHANNEL=OURS, REJOIN_ATTEMPTS=3)
        irc.note_kicked_from(OURS, "someop")

    def test_it_is_tried_while_it_has_attempts_left(self):
        for expected in (1, 2):
            self.assertEqual(irc.note_join_refused(OURS), expected)
            self.assertIn(OURS, irc.channels_to_rejoin(),
                          "gave up with attempts remaining")

    def test_and_left_alone_once_they_are_used(self):
        for _ in range(3):
            irc.note_join_refused(OURS)

        self.assertEqual(irc.channels_to_rejoin(), [])
        self.assertEqual(irc.gave_up_on(), [OURS])

    def test_a_refusal_for_a_channel_we_are_not_chasing_counts_nothing(self):
        """Otherwise an ordinary failed JOIN - somewhere the bot was never in
        - would start it knocking on a door nobody asked about."""
        self.assertEqual(irc.note_join_refused(NOT_OURS), 0)
        self.assertNotIn(NOT_OURS, irc.gave_up_on())

    def test_a_join_that_works_clears_the_record_entirely(self):
        irc.note_join_refused(OURS)

        self.assertIsNotNone(irc.note_joined(OURS))
        self.assertEqual(irc.channels_to_rejoin(), [])
        self.assertEqual(irc.gave_up_on(), [])

    def test_and_a_later_kick_starts_from_zero_again(self):
        """The count is CONSECUTIVE refusals. A channel that let us back in
        and threw us out again months later is not two-thirds of the way to
        being abandoned."""
        for _ in range(2):
            irc.note_join_refused(OURS)
        irc.note_joined(OURS)

        irc.note_kicked_from(OURS, "someop")

        self.assertEqual(self.config.kicked_channels[OURS]["refusals"], 0)
        self.assertIn(OURS, irc.channels_to_rejoin())

    def test_zero_attempts_means_never(self):
        """An operator who does not want the bot chasing channels at all."""
        self.set_config(REJOIN_ATTEMPTS=0)

        self.assertEqual(irc.channels_to_rejoin(), [])

    def test_and_zero_attempts_is_not_the_same_as_having_given_up(self):
        """The asymmetry a mutation run exposed. `refusals >= 0` is true for
        every entry, so without its own guard gave_up_on() would report a
        channel the bot had never tried - and the operator would be told
        DCCore had abandoned somewhere it was told not to chase."""
        self.set_config(REJOIN_ATTEMPTS=0)

        self.assertEqual(irc.gave_up_on(), [],
                         "reported giving up on a channel it never tried")

    def test_a_channel_removed_from_CHANNEL_stops_being_chased(self):
        """The operator's answer to a ban is often to stop serving there. The
        retry must not outlive the configuration that wanted it."""
        self.set_config(CHANNEL=ALSO_OURS)

        self.assertEqual(irc.channels_to_rejoin(), [])


class TheOrderOfResponsibilities(unittest.TestCase):
    """The read thread must not block on a socket write, and the advert worker
    must not hold a lock or know what a kick is."""

    def source(self, name):
        with open(os.path.join(REPO_ROOT, name), encoding="utf-8") as handle:
            return handle.read()

    def test_the_read_loop_only_records(self):
        body = self.source("irc.py")
        watch = body.split("kick_parsed = parse_kick(line)", 1)[1][:900]

        self.assertNotIn("JOIN ", watch,
                         "the read thread is sending a JOIN itself")

    def test_the_worker_sends_what_irc_py_decides(self):
        worker = self.source("announce.py")

        self.assertIn("irc_mod.channels_to_rejoin()", worker)
        self.assertIn('f"JOIN {waiting}', worker)

    def test_the_worker_holds_no_rule_of_its_own(self):
        """No count, no limit, no comparison - if the rule were duplicated
        here the two could disagree about when to stop."""
        worker = self.source("announce.py")
        block = worker.split("for waiting in irc_mod.channels_to_rejoin():", 1)[1][:400]

        self.assertNotIn("REJOIN_ATTEMPTS", block)
        self.assertNotIn("refusals", block)

    def test_a_failure_in_the_rejoin_cannot_stop_the_advert(self):
        """It runs inside the advert loop. An exception there would take the
        thing the bot exists to do with it."""
        worker = self.source("announce.py")
        block = worker.split("for waiting in irc_mod.channels_to_rejoin():", 1)[0]

        self.assertIn("try:", block.rsplit("import irc as irc_mod", 1)[0][-200:])


class TheStateIsLiveStateAndFollowsThoseRules(unittest.TestCase):
    """runtime.py containers have four separate contracts and this is the
    fourth thing that has had to learn them."""

    def test_it_lives_in_runtime(self):
        import runtime

        self.assertIsInstance(runtime.kicked_channels, dict)

    def test_it_survives_a_rehash(self):
        """Losing it restarts the count at zero on every rehash, so a channel
        that has refused three times gets three more tries each time the
        operator saves a setting."""
        import commands

        self.assertIn("kicked_channels", commands.PRESERVE_RUNTIME)

    def test_and_the_harness_resets_it_between_tests(self):
        from tests import support

        self.assertIn("kicked_channels", support.RUNTIME_CONTAINERS)


if __name__ == "__main__":
    unittest.main()
