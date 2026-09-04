"""JOIN is stricter than the setting looks.

WHAT WENT WRONG

RFC 2812 is `JOIN <channel>{,<channel>} [<key>{,<key>}]` - SPACE-separated
parameters. So a space anywhere in the channel list ends it. Sending

    JOIN #one, #two, #three

joins "#one" and hands the server "#two," as a channel KEY; the rest is
discarded. Nothing about that is an error, so nothing reports one.

config.CHANNEL was passed to JOIN verbatim. An operator who wrote
"#a, #b, #c" - which reads naturally, and is how configure.py echoes the value
back at them - joined their first channel and no others. Reported from a real
install: six channels configured, one joined, and the log said "Activating the
advert despite 5 unconfirmed channel(s)" without ever connecting the two.

The advert loop was fine, because announce.py strips each entry as it goes.
Only the JOIN was not, which is why the bot looked half-working rather than
broken.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if os.path.join(REPO_ROOT, "tests") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import irc  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


class TheJoinLineNeverContainsASpace(DCCoreTestCase):
    """The property, stated as the protocol states it. Everything else in this
    file is a way of arriving at this one."""

    def test_spaces_after_commas_are_tolerated(self):
        self.set_config(CHANNEL="#Mp3Passion, #mp3servers, #mp3download")

        self.assertEqual(irc.join_target_list(),
                         "#Mp3Passion,#mp3servers,#mp3download")
        self.assertNotIn(" ", irc.join_target_list())

    def test_the_real_reported_configuration(self):
        """Six channels, one joined. Verbatim from the install that found it."""
        self.set_config(CHANNEL="#Mp3Passion, #mp3servers, #mp3download, "
                                "#mp3-best-of, #mp3country, #mp3Albums4u")

        self.assertEqual(len(irc.configured_channels()), 6)
        self.assertNotIn(" ", irc.join_target_list())

    def test_whitespace_of_every_kind_is_removed(self):
        self.set_config(CHANNEL="  #one ,\t#two,\n#three  ")

        self.assertEqual(irc.join_target_list(), "#one,#two,#three")

    def test_empty_entries_are_dropped(self):
        """A trailing comma is the commonest edit-by-hand mistake, and an empty
        channel in a JOIN is a malformed line rather than a no-op."""
        self.set_config(CHANNEL="#one,,#two,")

        self.assertEqual(irc.join_target_list(), "#one,#two")

    def test_order_is_preserved(self):
        """The first channel is not arbitrary: defaults.py derives
        BROADCAST_SEARCH_CHANNEL from it."""
        self.set_config(CHANNEL="#zebra, #alpha, #middle")

        self.assertEqual(irc.configured_channels(),
                         ["#zebra", "#alpha", "#middle"])

    def test_case_is_left_alone(self):
        """Channel names are case-insensitive on the wire, but the operator
        typed what they typed and it appears in the advert."""
        self.set_config(CHANNEL="#Mp3Passion, #mp3servers")

        self.assertEqual(irc.configured_channels()[0], "#Mp3Passion")


class TheOrdinaryCasesStillWork(DCCoreTestCase):

    def test_a_list_with_no_spaces_is_unchanged(self):
        """Control. The overwhelmingly common configuration must pass through
        untouched, or this fix would be a change of behaviour rather than a
        repair."""
        self.set_config(CHANNEL="#one,#two,#three")

        self.assertEqual(irc.join_target_list(), "#one,#two,#three")

    def test_a_single_channel(self):
        self.set_config(CHANNEL="#only")

        self.assertEqual(irc.join_target_list(), "#only")
        self.assertEqual(irc.configured_channels(), ["#only"])

    def test_a_blank_setting_does_not_raise(self):
        """CHANNEL is in settings_file.REQUIRED so the daemon refuses to boot
        without it, but this runs on a config object anything can assign to."""
        for value in ("", "   ", ",", None):
            with self.subTest(value=value):
                self.set_config(CHANNEL=value)

                self.assertEqual(irc.configured_channels(), [])
                self.assertEqual(irc.join_target_list(), "")


class TheJoinUsesIt(unittest.TestCase):
    """The fix is only a fix if the JOIN actually calls it. Read out of the
    source rather than retyped: a helper that exists and is not used would
    satisfy every test above."""

    def test_the_join_thread_is_handed_the_normalised_list(self):
        import io

        with io.open(os.path.join(REPO_ROOT, "irc.py"), encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn("args=(s, join_target_list())", source.replace("\n", " ")
                      .replace("  ", " "),
                      "the JOIN thread is not being handed join_target_list() - "
                      "if it takes config.CHANNEL directly, the space bug is back")
        self.assertNotIn("args=(s, config.CHANNEL)", source,
                         "the JOIN is passing the raw setting again")


if __name__ == "__main__":
    unittest.main()
