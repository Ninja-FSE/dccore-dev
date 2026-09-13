"""#440 - the one outbound path that both scaled with the channel count and
ignored the pacer.

A rehash compares the channel list before the reload against the one after it
and JOINs what is new, PARTs what is gone and NAMESes the rest. All three
loops wrote straight to the socket: no pacer slot, no gap, one line per
channel per verb. The dashboard fires a rehash on EVERY settings save - a
theme change, a password change - so an operator with fifteen channels sent
fifteen back-to-back lines for changing a colour.

Two things fix it. The lines are comma-batched, the way irc.py's connect path
has always batched its JOIN, and they go through oserve.queue_message() like
every other line the bot says.
"""

import io
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import announce  # noqa: E402
import commands  # noqa: E402
import defaults as config  # noqa: E402

from tests.support import (DCCoreTestCase, RecordingSocket,  # noqa: E402
                           install_fake_oserve, silence_debug)

# One IRC line is 512 bytes including the trailing CRLF.
IRC_LINE_BYTES = 512


class ChannelSyncCase(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.sock = RecordingSocket()
        self.oserve = install_fake_oserve(irc_connection=self.sock)
        self.debug = silence_debug(announce)

    def sync(self, old_chans, new_chans):
        lines = commands.sync_channels(self.oserve, old_chans, new_chans)
        self.assertEqual([line for _user, line, _vip in self.oserve.queued], lines,
                         "a line was queued that sync_channels did not report, "
                         "or the other way round")
        return lines

    def verbs(self, lines):
        return [line.split(" ", 1)[0] for line in lines]


class TheBurstIsGone(ChannelSyncCase):

    def test_fifteen_channels_cost_two_lines_not_fifteen(self):
        """The realistic case the issue measured: fourteen channels plus the
        debug channel, nothing removed. It was 15 NAMES lines and 15 JOINs."""
        channels = ["#chan%02d" % n for n in range(15)]

        lines = self.sync([], channels)

        self.assertEqual(self.verbs(lines), ["JOIN", "NAMES"])

    def test_a_full_swap_costs_three_lines_not_forty_three(self):
        """The worst case in the issue - the operator replaces the whole
        channel list in one save - measured there at 43 lines and 894 bytes."""
        old = ["#old%02d" % n for n in range(14)]
        new = ["#new%02d" % n for n in range(14)]

        lines = self.sync(old, new)

        self.assertEqual(self.verbs(lines), ["JOIN", "PART", "NAMES"])

    def test_nothing_is_written_to_the_socket(self):
        """The whole point. A line on the socket has taken no pacer slot and
        is racing whatever else is being sent."""
        self.sync([], ["#one", "#two"])

        self.assertEqual(self.sock.sent, [],
                         "the sync still writes to the raw socket, which is "
                         "the burst this fixes")

    def test_the_rehash_itself_no_longer_writes_to_the_socket(self):
        """Read out of commands.py: reaching this block for real means
        reloading every module in the daemon. Asserted as the STATEMENT - the
        word irc_sock appears in the comment above it either way."""
        with io.open(os.path.join(REPO_ROOT, "commands.py"), encoding="utf-8") as handle:
            code = handle.read()
        body = code.split("def _handle_rehash_request(", 1)[1]
        body = re.sub(chr(35) + "[^" + chr(10) + "]*", "", body)

        self.assertIn("sync_channels(", body)
        # No paren: since #504 a direct write is spelled sendall(), and the
        # old anchor would pass against one.
        self.assertNotIn("irc_sock.send", body)

    def test_a_failing_sync_does_not_report_the_reload_as_failed(self):
        """Everything the rehash exists to do has already happened by the time
        the sync runs. Raising from there reached the handler at the bottom,
        which says "The files could not be reloaded live" - so the operator is
        told their rehash failed when it succeeded.

        Found by the end-to-end fixture, whose oserve stand-in carried only
        irc_connection: the first AttributeError took the whole rehash down."""
        with io.open(os.path.join(REPO_ROOT, "commands.py"), encoding="utf-8") as handle:
            code = handle.read()
        body = code.split("def _handle_rehash_request(", 1)[1]
        body = re.sub(chr(35) + "[^" + chr(10) + "]*", "", body)
        block = body.split("sync_channels(", 1)[0][-400:]

        self.assertIn("try:", block,
                      "the sync can still take the whole reload down with it")


class WhatGoesInEachLine(ChannelSyncCase):

    def test_every_line_is_a_whole_irc_line(self):
        self.sync([], ["#one", "#two"])

        for line in [text for _user, text, _vip in self.oserve.queued]:
            with self.subTest(line=line[:40]):
                self.assertTrue(line.endswith("\r\n"), repr(line))

    def test_no_batch_is_longer_than_an_irc_line(self):
        """Comma-batching without a limit would build one line longer than a
        server will read, which is a worse failure than the burst: the line
        is truncated and the tail is read as a command of its own."""
        channels = ["#" + ("c%03d" % n) * 8 for n in range(200)]

        lines = self.sync([], channels)

        self.assertGreater(len(lines), 2, "200 channels must not fit in two lines")
        for line in lines:
            with self.subTest(line=line[:40]):
                self.assertLessEqual(len(line.encode("utf-8")), IRC_LINE_BYTES)

    def test_splitting_into_batches_loses_nothing(self):
        """The other half of the batching: every channel appears once across
        the NAMES lines, in order."""
        channels = ["#" + ("c%03d" % n) * 8 for n in range(200)]

        lines = self.sync([], channels)
        named = []
        for line in lines:
            if line.startswith("NAMES "):
                named.extend(line[len("NAMES "):].strip().split(","))

        self.assertEqual(named, channels)

    def test_the_part_says_why(self):
        """It is what everyone left in the channel sees."""
        lines = self.sync(["#gone"], [])

        part = [line for line in lines if line.startswith("PART ")]
        self.assertEqual(len(part), 1, lines)
        self.assertIn(" :Removed from DCCore", part[0])

    def test_a_single_channel_is_not_batched_into_something_odd(self):
        lines = self.sync([], ["#only"])

        self.assertEqual(lines, ["JOIN #only\r\n", "NAMES #only\r\n"])


class WhatTheSyncDecides(ChannelSyncCase):

    def test_the_debug_channel_is_never_parted(self):
        """It is not in CHANNEL, so it is in old_chans and not in new_chans on
        every single rehash - the one channel that must not be parted for
        looking removed."""
        self.set_config(DEBUG_CHANNEL="#somedebug")

        lines = self.sync(["#kept", "#somedebug"], ["#kept"])

        self.assertEqual([line for line in lines if line.startswith("PART")], [])

    def test_a_channel_genuinely_removed_is_parted(self):
        """The other half, so the guard above cannot be a blanket refusal."""
        self.set_config(DEBUG_CHANNEL="#somedebug")

        lines = self.sync(["#kept", "#gone"], ["#kept"])

        self.assertEqual([line for line in lines if line.startswith("PART")],
                         ["PART #gone :Removed from DCCore\r\n"])

    def test_the_membership_map_moves_with_the_decision_not_the_send(self):
        """dcc.py treats channel_users as proof a user is present. The lines
        are queued now rather than written, so waiting for them to go out
        would leave that map stale for as long as the queue takes to drain."""
        config.channel_users.clear()
        config.channel_users["#gone"] = {"someone"}

        self.sync(["#gone"], ["#fresh"])

        self.assertIn("#fresh", config.channel_users)
        self.assertEqual(config.channel_users["#fresh"], set())
        self.assertNotIn("#gone", config.channel_users)

    def test_a_channel_that_stays_keeps_the_names_it_had(self):
        """Nothing about an unchanged channel is disturbed - the NAMES reply
        refreshes it, and emptying it first would make every user in it
        invisible until that reply arrived."""
        config.channel_users.clear()
        config.channel_users["#kept"] = {"someone"}

        self.sync(["#kept"], ["#kept"])

        self.assertEqual(config.channel_users["#kept"], {"someone"})

    def test_the_debug_channel_says_it_once_not_once_per_channel(self):
        """send_debug() goes to a CHANNEL through the VIP lane, so a line per
        channel was a second burst sitting behind the first."""
        self.sync([], ["#one", "#two", "#three"])

        joins = [text for category, text in self.debug if category == "JOIN"]
        self.assertEqual(len(joins), 1, self.debug)
        for chan in ("#one", "#two", "#three"):
            self.assertIn(chan, joins[0])


class TheBatcherItself(unittest.TestCase):
    """comma_batched() on its own - the arithmetic, without the sync around
    it."""

    def test_an_empty_list_sends_nothing(self):
        """A rehash that changes nothing must not send a bare JOIN."""
        self.assertEqual(commands.comma_batched("JOIN", []), [])

    def test_the_tail_is_counted_against_the_limit(self):
        """PART carries a reason, and a batch sized as if it did not would be
        over the limit by exactly the length of that reason."""
        channels = ["#" + ("c%03d" % n) * 8 for n in range(200)]

        lines = commands.comma_batched("PART", channels, tail=" :" + "x" * 200)

        for line in lines:
            with self.subTest(line=line[:40]):
                self.assertLessEqual(len(line.encode("utf-8")), IRC_LINE_BYTES)

    def test_a_channel_too_long_to_batch_still_goes_out(self):
        """It cannot happen with anything a server accepts - RFC 2812 caps a
        channel name at 50 characters - but dropping it silently would be the
        wrong answer to something impossible."""
        monster = "#" + "x" * 900

        lines = commands.comma_batched("JOIN", [monster])

        self.assertEqual(lines, ["JOIN %s\r\n" % monster])
