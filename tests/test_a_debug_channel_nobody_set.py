"""A fresh install sent every runtime report to nowhere, out loud.

`DEBUG_TO_CHANNEL` defaults to True. `DEBUG_CHANNEL` defaults to `""`. Those
two combine, out of the box, into

    PRIVMSG  :<debug text>

- a PRIVMSG with no target, put on the wire for every runtime report the
daemon makes, by a bot that has just connected and has not been configured
yet. That is the state EVERY fresh install starts in, so it is the state every
new operator's first run is in (#424).

The fix is not to refuse to log. The line falls through to the console, which
is where somebody running a bot for the first time is watching anyway, and
startup says once that this is what is happening - silence is otherwise
indistinguishable from working.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import announce  # noqa: E402
import defaults as config  # noqa: E402

from tests.support import DCCoreTestCase, install_fake_oserve  # noqa: E402


class DebugTestCase(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        install_fake_oserve()
        announce._debug_queue.clear()
        config.DEBUG_TO_CHANNEL = True
        config.DEBUG_TO_CONSOLE = False

        # THE DRAIN THREAD IS NEVER STOPPED ONCE STARTED, and these tests are
        # about what gets ENQUEUED, not about delivery. Letting the real drain
        # start here leaves it running for the rest of the suite, where it
        # empties another test's queue before that test can look at it - which
        # is exactly what it did to test_announce_output's delivery test.
        # test_debug_routing.py closes the same gate for the same reason.
        self._drain = announce._debug_drain_started
        announce._debug_drain_started = True
        self.addCleanup(lambda: setattr(announce, "_debug_drain_started",
                                        self._drain))

    def tearDown(self):
        announce._debug_queue.clear()
        super().tearDown()

    def queued(self):
        return list(announce._debug_queue)


class WithNoChannelConfigured(DebugTestCase):

    def setUp(self):
        super().setUp()
        config.DEBUG_CHANNEL = ""

    def test_nothing_is_queued_for_a_channel_that_does_not_exist(self):
        announce.send_debug("a runtime report", category="INFO")

        self.assertEqual(self.queued(), [])

    def test_no_line_with_an_empty_target_is_ever_built_for_the_wire(self):
        """The actual defect, stated as the property rather than as a count:
        whatever ends up queued, none of it may address nobody."""
        for category in ("INFO", "SENT", "PART", "MUTE"):
            announce.send_debug("a runtime report", category=category)

        for line in self.queued():
            self.assertFalse(line.startswith("PRIVMSG  :"), line)

    def test_the_operator_still_sees_the_line(self):
        """Dropped from the wire, not dropped. A fresh install is exactly when
        the operator most needs to see what the daemon is doing."""
        seen = []
        config.DEBUG_TO_CONSOLE = True

        def sink(text, category):
            seen.append((category, text))

        announce.add_debug_sink(sink)
        try:
            announce.send_debug("a runtime report", category="INFO")
        finally:
            announce.remove_debug_sink(sink)

        self.assertEqual(len(seen), 1)

    def test_a_channel_of_only_spaces_counts_as_unset(self):
        """settings.conf is a text file edited by hand, and `DEBUG_CHANNEL = `
        with a trailing space reads back as a string that is true but is not
        a channel."""
        config.DEBUG_CHANNEL = "   "

        announce.send_debug("a runtime report", category="INFO")

        self.assertEqual(self.queued(), [])


class WithAChannelConfigured(DebugTestCase):

    def setUp(self):
        super().setUp()
        config.DEBUG_CHANNEL = "#somechannel"

    def test_the_line_goes_to_that_channel(self):
        announce.send_debug("a runtime report", category="INFO")

        self.assertEqual(len(self.queued()), 1)
        self.assertTrue(self.queued()[0].startswith("PRIVMSG #somechannel :"),
                        self.queued()[0])

    def test_turning_channel_output_off_still_stops_it(self):
        """The existing switch keeps working; the new check is an additional
        condition, not a replacement for it."""
        config.DEBUG_TO_CHANNEL = False

        announce.send_debug("a runtime report", category="INFO")

        self.assertEqual(self.queued(), [])


class TheStartupNoticeSaysSo(unittest.TestCase):
    """oserve.py. Read from the source: reaching it means booting a daemon,
    and the property is that the condition is stated at all."""

    @staticmethod
    def startup():
        import io
        with io.open(os.path.join(REPO_ROOT, "oserve.py"), encoding="utf-8") as f:
            return f.read()

    def test_startup_mentions_both_halves_of_the_condition(self):
        """Either one alone is unremarkable. It is the combination - channel
        output on, no channel named - that sends reports nowhere."""
        source = self.startup()
        block = source.split("DEBUG_TO_CHANNEL is on but DEBUG_CHANNEL", 1)
        self.assertEqual(len(block), 2,
                         "startup no longer tells the operator that runtime "
                         "reports are going to the console only")

    def test_the_check_requires_both_conditions_and_not_either(self):
        source = self.startup()
        head = source.split("DEBUG_TO_CHANNEL is on but DEBUG_CHANNEL", 1)[0]
        condition = head.rsplit("if (getattr(config, \"DEBUG_TO_CHANNEL\"", 1)[-1]

        self.assertIn("and not str(getattr(config, \"DEBUG_CHANNEL\"", condition)
