"""Regression tests for security.check_user_status - ban enforcement.

Ban enforcement used to be a complete no-op: the ``return False`` inside the
hard-ban branch was commented out, so every nick - banned or not - came back
True and the daemon happily served people who had been banned for months.
The fixes since then also had to deal with a second problem: the debug notice
that announces a block calls announce.send_debug, which sleeps 0.5s under a
lock on the IRC read thread. One notice per BLOCKED MESSAGE froze the network
loop, so notices are now suppressed to one per nick per run.

Every test here names the defect it guards against in its docstring.
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
import unittest

from tests.support import DCCoreTestCase, silence_debug, no_disk_writes

import announce
import db
import security


class BanEnforcementTests(DCCoreTestCase):
    """check_user_status must actually deny banned nicks."""

    def setUp(self):
        super().setUp()
        # security._ban_notified is a module global that survives between tests,
        # exactly as it survives between messages in the daemon. Start clean and
        # leave clean, or the "notify once" cases contaminate each other.
        security._ban_notified.clear()
        self.addCleanup(security._ban_notified.clear)

        self.ban_dir = tempfile.mkdtemp(prefix="dccore-bans-")
        self.addCleanup(shutil.rmtree, self.ban_dir, True)
        self.hard_bans = os.path.join(self.ban_dir, "hard_bans.txt")
        self.config.HARD_BANS_FILE = self.hard_bans
        self.config.BANS_FILE = os.path.join(self.ban_dir, "bans.txt")

        # Never write to the real data/ directory when an expired ban is pruned.
        no_disk_writes(db)
        # send_debug paces itself with a 0.5s sleep; capture it instead.
        self._real_send_debug = announce.send_debug
        self.addCleanup(setattr, announce, "send_debug", self._real_send_debug)
        self.notices = silence_debug(announce)

    # -- helpers ---------------------------------------------------------

    def write_hard_bans(self, *lines):
        """(Re)write the hard-ban file with one pattern per line."""
        with open(self.hard_bans, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def check(self, nick):
        """Call check_user_status with its [SECURITY BLOCK] prints swallowed."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = security.check_user_status(nick)
        self.last_stdout = buffer.getvalue()
        return result

    # -- hard bans -------------------------------------------------------

    def test_hard_ban_pattern_denies(self):
        """Defect: the hard-ban branch's `return False` was commented out, so a
        nick matching a wildcard in hard_bans.txt was still served."""
        self.write_hard_bans("lidx_*")
        self.assertFalse(self.check("lidx_abc"))

    def test_hard_ban_match_is_case_insensitive(self):
        """Defect: a banned nick reconnecting in a different case walked straight
        through. The pattern and the nick are both lowercased before matching."""
        self.write_hard_bans("LiDx_*")
        self.assertFalse(self.check("LIDX_ABC"))
        self.assertFalse(self.check("lidx_abc"))

    def test_exact_pattern_without_wildcard_denies_only_that_nick(self):
        """A pattern with no '*' is anchored: it matches that nick and nothing
        that merely starts with it."""
        self.write_hard_bans("leecher")
        self.assertFalse(self.check("leecher"))
        self.assertTrue(self.check("leecherbot"))

    def test_comment_lines_are_not_patterns(self):
        """Defect: '#'-prefixed documentation lines were compiled as patterns, so
        a commented-out ban still banned."""
        self.write_hard_bans("# lidx_*", "#leecher", "", "   ")
        self.assertTrue(self.check("lidx_abc"))
        self.assertTrue(self.check("leecher"))
        self.assertEqual(self.notices, [])

    def test_star_only_pattern_is_refused(self):
        """Defect: a single '*' line in hard_bans.txt locked out every nick in all
        six channels. Star-only patterns are skipped, and the rest of the file is
        still applied."""
        self.write_hard_bans("*", "***", "lidx_*")
        # The over-broad lines must not catch an unrelated user...
        self.assertTrue(self.check("dave"))
        # ...and the scan must carry on to the legitimate pattern below them.
        self.assertFalse(self.check("lidx_abc"))
        self.assertIn("SECURITY WARNING", self.last_stdout)

    def test_clean_user_is_allowed(self):
        """A nick matching nothing returns True - enforcement must not fail closed."""
        self.write_hard_bans("lidx_*", "# a comment")
        self.assertTrue(self.check("dave"))
        self.assertEqual(self.notices, [])

    def test_missing_hard_ban_file_allows(self):
        """No hard_bans.txt at all is not an error: the scan fails open."""
        self.assertFalse(os.path.exists(self.hard_bans))
        self.assertTrue(self.check("dave"))

    # -- timed bans ------------------------------------------------------

    def test_timed_ban_with_future_expiry_denies(self):
        """Defect: timed bans in config.banned_users were regex-matched like
        wildcard lines instead of having their expiry compared, so a live day-ban
        never actually denied."""
        self.config.banned_users["lidx_abc"] = time.time() + 3600
        self.assertFalse(self.check("LIDX_ABC"))
        self.assertEqual([c for c, _ in self.notices], ["TBAN"])
        # Still live, so it must not be pruned.
        self.assertIn("lidx_abc", self.config.banned_users)

    def test_expired_timed_ban_allows_and_is_pruned(self):
        """Defect: an expired day-ban was never removed, so the nick stayed
        blocked past midnight. A past expiry allows the user AND drops the row."""
        self.config.banned_users["lidx_abc"] = time.time() - 10
        self.assertTrue(self.check("lidx_abc"))
        self.assertNotIn("lidx_abc", self.config.banned_users)
        self.assertEqual(self.notices, [])

    def test_corrupt_timed_ban_expiry_is_treated_as_expired(self):
        """A non-numeric expiry (a hand-edited bans.txt) must not raise on the IRC
        read thread; it is treated as long expired and pruned."""
        self.config.banned_users["lidx_abc"] = "not-a-timestamp"
        self.assertTrue(self.check("lidx_abc"))
        self.assertNotIn("lidx_abc", self.config.banned_users)

    # -- the debug-notice rate limit -------------------------------------

    def test_debug_notice_fires_once_per_nick(self):
        """Defect: every blocked message sent its own debug notice, and
        send_debug sleeps 0.5s under a lock on the IRC read thread - a flooding
        banned nick stalled the network loop into a ping timeout."""
        self.write_hard_bans("lidx_*")
        for _ in range(6):
            self.assertFalse(self.check("lidx_abc"))
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(self.notices[0][0], "BAN")

    def test_debug_notice_is_per_nick_not_global(self):
        """The one-notice rule is keyed on the nick: a second banned nick still
        gets its own notice."""
        self.write_hard_bans("lidx_*", "leecher")
        self.assertFalse(self.check("lidx_abc"))
        self.assertFalse(self.check("leecher"))
        self.assertFalse(self.check("lidx_abc"))
        self.assertEqual(len(self.notices), 2)

    def test_notify_mark_cleared_when_user_seen_clean(self):
        """Defect: the 'already notified' mark was only cleared on the timed-ban
        expiry path, so after an !unban and a later re-ban the debug channel
        stayed silent and the admin never saw the pattern take effect."""
        self.write_hard_bans("lidx_*")
        self.assertFalse(self.check("lidx_abc"))
        self.assertEqual(len(self.notices), 1)

        # !unban rewrites the file without the pattern; the nick is seen clean.
        self.write_hard_bans("# unbanned")
        self.assertTrue(self.check("lidx_abc"))
        self.assertNotIn("lidx_abc", security._ban_notified)

        # A later re-ban must notify once more.
        self.write_hard_bans("lidx_*")
        self.assertFalse(self.check("lidx_abc"))
        self.assertEqual(len(self.notices), 2)

    def test_notify_mark_survives_an_unreadable_ban_file(self):
        """Defect: clearing the mark on the fail-open path re-armed the notice.
        A missing file - or a read landing inside the truncate window of an
        !unban rewrite - is 'unknown', not 'clean', so the mark must survive and
        the next block on the same nick must stay silent."""
        self.write_hard_bans("lidx_*")
        self.assertFalse(self.check("lidx_abc"))
        self.assertEqual(len(self.notices), 1)

        # The file vanishes mid-run: the scan fails open, but nothing was proven
        # clean, so the mark must NOT be dropped.
        os.remove(self.hard_bans)
        self.assertTrue(self.check("lidx_abc"))
        self.assertIn("lidx_abc", security._ban_notified)

        # The file comes back with the pattern intact - still no second notice.
        self.write_hard_bans("lidx_*")
        self.assertFalse(self.check("lidx_abc"))
        self.assertEqual(len(self.notices), 1)


if __name__ == "__main__":
    unittest.main()
