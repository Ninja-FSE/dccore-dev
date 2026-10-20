"""`unpause <old nick>` lifts the pause on the nick it renamed to (#1477).

A nick paused by DAILY_SEND_FAILS_LIMIT keeps its pause across a rename
(#1437): irc.note_nick_change() carries the count to the new nick. The
console's `unpause` looked only under the nick it was given, so an operator
who typed the old nick was told it had "no failed sends counted today;
nothing to lift" while the pause stayed on the new one.

dcc.carry_send_fails() now records where it carried a count today
(runtime.send_fails_renamed, {old_key: (day, new_nick)}), and
dcc.send_fails_renamed_to() follows those records - through a chain, never
round a loop, never from an earlier day, and not at all when the named nick
holds a count of its own. The console lifts the pause where it sits now and
says so: "<old> is now <new>: ...".

Driven through the real console command (adminchat.handle_command()), the
real rename (irc.note_nick_change() and the NICK line), and
send_fails_renamed_to() on its own.

Every nick here is invented.
"""

import contextlib
import datetime
import importlib
import io
import os
import sys
import threading
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import adminchat  # noqa: E402
import commands  # noqa: E402
import dcc  # noqa: E402
import defaults as config  # noqa: E402
import irc  # noqa: E402
import runtime  # noqa: E402

from tests import test_a_nick_whose_sends_keep_failing_is_paused as paused  # noqa: E402

TODAY = paused.TODAY
YESTERDAY = TODAY - datetime.timedelta(days=1)


def lifted(old, new, fails):
    return (f"{old} is now {new}: {new}'s {fails} failed send(s) today are forgotten; "
            f"its requests are taken again.")


def own(nick, fails):
    return f"{nick}'s {fails} failed send(s) today are forgotten; its requests are taken again."


def nothing(nick):
    return f"{nick} has no failed sends counted today; nothing to lift."


class _BoundedTrail(dict):
    """runtime.send_fails_renamed, failing loudly after far more reads than
    any trail here needs: a follow that went round a loop of renames for
    ever would hang the run instead of failing it."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reads_left = 1000

    def get(self, *args):
        self.reads_left -= 1
        if self.reads_left < 0:
            raise AssertionError("the rename trail was followed round a loop")
        return dict.get(self, *args)


class UnpauseCase(paused.PauseCase):
    """The limit at 3, today fixed (self.today), the renames recorded in a
    bounded dict, and a console session to type into."""

    LIMIT = 3

    def setUp(self):
        super().setUp()
        tree = self.make_tree()
        self.set_config(HARD_BANS_FILE=os.path.join(tree.root, "hard_bans.txt"))
        self.trail = _BoundedTrail()
        patch = mock.patch.object(runtime, "send_fails_renamed", self.trail)
        patch.start()
        self.addCleanup(patch.stop)

    def fail_sends(self, nick, times=3):
        return self.count_failures(nick, times)

    def rename(self, old, new):
        with contextlib.redirect_stdout(io.StringIO()):
            return irc.note_nick_change(old, new)

    def unpause(self, nick):
        session = paused._Session()
        with contextlib.redirect_stdout(io.StringIO()):
            adminchat.handle_command(session, "unpause " + nick)
        return session.lines


# ---------------------------------------------------------------------------
# The console follows the rename.
# ---------------------------------------------------------------------------

class TheConsoleFollowsTheRename(UnpauseCase):

    def test_unpause_by_the_old_nick_lifts_the_pause_on_the_new_one(self):
        self.fail_sends("Alfa")
        self.rename("Alfa", "Bravo")
        self.assertEqual(dcc.send_fails_paused("Bravo"), 3, "the fixture pauses the new nick")

        self.assertEqual(self.unpause("Alfa"), [lifted("Alfa", "Bravo", 3)])

        self.assertEqual(dcc.send_fails_paused("Bravo"), 0, "the pause stayed on the new nick")
        self.assertEqual(runtime.failed_transfers, {})

    def test_a_second_unpause_has_nothing_to_lift(self):
        self.fail_sends("Alfa")
        self.rename("Alfa", "Bravo")
        self.unpause("Alfa")

        self.assertEqual(self.unpause("Alfa"), [nothing("Alfa")])
        self.assertEqual(self.unpause("Bravo"), [nothing("Bravo")])

    def test_unpause_by_the_new_nick_answers_as_before(self):
        self.fail_sends("Alfa")
        self.rename("Alfa", "Bravo")

        self.assertEqual(self.unpause("Bravo"), [own("Bravo", 3)])
        self.assertEqual(self.unpause("Alfa"), [nothing("Alfa")],
                         "the old nick still leads to a count that is gone")

    def test_a_nick_never_renamed_has_nothing_to_lift(self):
        self.assertEqual(self.unpause("Alfa"), [nothing("Alfa")])

    def test_a_chain_of_renames_is_followed_to_the_last_nick(self):
        self.fail_sends("alfa")
        self.rename("alfa", "bravo")
        self.rename("bravo", "charlie")

        self.assertEqual(dcc.send_fails_renamed_to("alfa"), "charlie")
        self.assertEqual(dcc.send_fails_renamed_to("bravo"), "charlie")
        self.assertEqual(self.unpause("alfa"), [lifted("alfa", "charlie", 3)])
        self.assertEqual(dcc.send_fails_paused("charlie"), 0)
        self.assertEqual(runtime.failed_transfers, {})

    def test_a_loop_of_renames_back_to_the_first_nick(self):
        """alfa -> bravo -> alfa: the count is back on alfa, which is
        unpaused under its own name; after that neither name leads anywhere,
        and following the records must stop rather than go round."""
        self.fail_sends("alfa")
        self.rename("alfa", "bravo")
        self.rename("bravo", "alfa")
        self.assertEqual(dcc.send_fails_paused("alfa"), 3)
        self.assertIsNone(dcc.send_fails_renamed_to("alfa"), "alfa holds the count itself")
        self.assertEqual(dcc.send_fails_renamed_to("bravo"), "alfa")

        self.assertEqual(self.unpause("alfa"), [own("alfa", 3)])

        self.assertEqual(self.unpause("alfa"), [nothing("alfa")])
        self.assertEqual(self.unpause("bravo"), [nothing("bravo")])
        self.assertIsNone(dcc.send_fails_renamed_to("alfa"))
        self.assertIsNone(dcc.send_fails_renamed_to("bravo"))

    def test_the_old_nick_after_a_loop_leads_to_where_the_count_sits(self):
        """bravo -> alfa was the last rename, so `unpause bravo` lifts alfa's."""
        self.fail_sends("alfa")
        self.rename("alfa", "bravo")
        self.rename("bravo", "alfa")

        self.assertEqual(self.unpause("bravo"), [lifted("bravo", "alfa", 3)])
        self.assertEqual(dcc.send_fails_paused("alfa"), 0)

    def test_a_rename_from_yesterday_is_not_followed(self):
        """Yesterday alfa renamed to bravo with a count; today bravo has been
        paused afresh. `unpause alfa` is about nobody today."""
        self.today = YESTERDAY
        self.fail_sends("alfa")
        self.rename("alfa", "bravo")
        self.today = TODAY
        self.fail_sends("bravo")
        self.assertEqual(dcc.send_fails_paused("bravo"), 3)

        self.assertIsNone(dcc.send_fails_renamed_to("alfa"))
        self.assertEqual(self.unpause("alfa"), [nothing("alfa")])
        self.assertEqual(dcc.send_fails_paused("bravo"), 3, "yesterday's rename lifted today's pause")

    def test_a_nick_with_a_count_of_its_own_is_not_followed(self):
        """alfa renamed to bravo, paused; someone else took the name alfa and
        failed once. `unpause alfa` is about that one, not bravo."""
        self.fail_sends("alfa")
        self.rename("alfa", "bravo")
        self.fail_sends("alfa", 1)

        self.assertIsNone(dcc.send_fails_renamed_to("alfa"))
        self.assertEqual(self.unpause("alfa"), [own("alfa", 1)])
        self.assertEqual(dcc.send_fails_paused("bravo"), 3, "bravo's pause was lifted instead")

    def test_a_count_of_its_own_from_yesterday_does_not_stop_the_follow(self):
        self.fail_sends("alfa")
        self.rename("alfa", "bravo")
        runtime.failed_transfers["alfa"] = {"day": YESTERDAY, "fails": 5, "paused": True}

        self.assertEqual(dcc.send_fails_renamed_to("alfa"), "bravo")

    def test_the_names_are_matched_whatever_their_case(self):
        self.fail_sends("alfa")
        self.rename("alfa", "Bravo")

        self.assertEqual(self.unpause("ALFA"), [lifted("ALFA", "Bravo", 3)])


# ---------------------------------------------------------------------------
# What a rename records.
# ---------------------------------------------------------------------------

class WhatACarryRecords(UnpauseCase):

    def test_a_carry_records_the_new_nick_as_given(self):
        self.fail_sends("alfa")

        self.assertTrue(dcc.carry_send_fails("alfa", "bravo", "BrAvO"))

        self.assertEqual(dict(self.trail), {"alfa": (TODAY, "BrAvO")})

    def test_without_the_nick_it_records_the_key(self):
        self.fail_sends("alfa")

        self.assertTrue(dcc.carry_send_fails("alfa", "bravo"))

        self.assertEqual(dict(self.trail), {"alfa": (TODAY, "bravo")})

    def test_a_carry_of_nothing_records_nothing(self):
        self.assertFalse(dcc.carry_send_fails("alfa", "bravo", "Bravo"))
        self.assertEqual(dict(self.trail), {})

    def test_a_carry_of_yesterdays_count_records_nothing(self):
        runtime.failed_transfers["alfa"] = {"day": YESTERDAY, "fails": 3, "paused": True}

        self.assertFalse(dcc.carry_send_fails("alfa", "bravo", "Bravo"))

        self.assertEqual(dict(self.trail), {})
        self.assertIsNone(dcc.send_fails_renamed_to("alfa"))

    def test_records_from_an_earlier_day_are_dropped_as_one_is_written(self):
        self.today = YESTERDAY
        self.fail_sends("alfa")
        self.rename("alfa", "bravo")
        self.assertEqual(set(self.trail), {"alfa"})
        self.today = TODAY
        self.fail_sends("charlie")

        self.rename("charlie", "delta")

        self.assertEqual(dict(self.trail), {"charlie": (TODAY, "delta")})

    def test_the_rename_passes_the_new_nick_as_the_server_wrote_it(self):
        self.fail_sends("alfa")

        self.rename("Alfa", "Bravo_Away")

        self.assertEqual(dict(self.trail), {"alfa": (TODAY, "Bravo_Away")})
        self.assertEqual(dcc.send_fails_renamed_to("alfa"), "Bravo_Away")
        self.assertEqual(self.unpause("Alfa"), [lifted("Alfa", "Bravo_Away", 3)])

    def test_the_nick_line_records_it_too(self):
        """The real NICK line, as irc_loop() hands it over."""
        self.fail_sends("alfa")

        with contextlib.redirect_stdout(io.StringIO()):
            irc.answer_nick_change(":Alfa!someuser@host.example NICK :BrAvO")

        self.assertEqual(dict(self.trail), {"alfa": (TODAY, "BrAvO")})
        self.assertEqual(self.unpause("Alfa"), [lifted("Alfa", "BrAvO", 3)])


class UnderTheCountsLock(UnpauseCase):
    """The records are written and read under failed_transfers_lock, as the
    counts are (runtime.py)."""

    def test_written_and_read_holding_the_lock(self):
        real = threading.Lock()
        holder = []

        class Recording:
            def __enter__(self):
                real.acquire()
                holder.append(threading.get_ident())
                return self

            def __exit__(self, *exc_info):
                holder.pop()
                real.release()
                return False

        seen = []

        class Watched(_BoundedTrail):
            def get(self, *args):
                seen.append(("read", bool(holder)))
                return _BoundedTrail.get(self, *args)

            def __setitem__(self, key, value):
                seen.append(("write", bool(holder)))
                dict.__setitem__(self, key, value)

        self.fail_sends("alfa")
        for name, value in (("failed_transfers_lock", Recording()), ("send_fails_renamed", Watched())):
            patch = mock.patch.object(runtime, name, value)
            patch.start()
            self.addCleanup(patch.stop)

        self.rename("alfa", "bravo")
        self.assertEqual(dcc.send_fails_renamed_to("alfa"), "bravo")

        self.assertIn(("write", True), seen)
        self.assertIn(("read", True), seen)
        self.assertEqual({held for _what, held in seen}, {True}, seen)


# ---------------------------------------------------------------------------
# A !rehash keeps the records.
# ---------------------------------------------------------------------------

class SurvivesARehash(paused.PauseCase):
    """The preserve, reload, restore that commands.handle_rehash_request()
    does, short of the reload of the other modules."""

    LIMIT = 3

    def setUp(self):
        super().setUp()
        tree = self.make_tree()
        self.set_config(HARD_BANS_FILE=os.path.join(tree.root, "hard_bans.txt"))

    def _restore_config(self):
        with contextlib.redirect_stdout(io.StringIO()):
            importlib.reload(config)

    def test_unpause_by_the_old_nick_still_follows_after_a_rehash(self):
        self.assertIn("send_fails_renamed", commands.PRESERVE_RUNTIME)
        self.assertIs(config.send_fails_renamed, runtime.send_fails_renamed)
        self.count_failures("alfa", 3)
        with contextlib.redirect_stdout(io.StringIO()):
            irc.note_nick_change("alfa", "Bravo")

        preserved = {name: getattr(config, name) for name in commands.PRESERVE_RUNTIME
                     if hasattr(config, name)}
        with contextlib.redirect_stdout(io.StringIO()):
            importlib.reload(config)
        self.addCleanup(self._restore_config)
        commands.restore_preserved_runtime(config, preserved)

        self.assertIs(config.send_fails_renamed, runtime.send_fails_renamed)
        self.assertEqual(runtime.send_fails_renamed, {"alfa": (TODAY, "Bravo")})
        session = paused._Session()
        with contextlib.redirect_stdout(io.StringIO()):
            adminchat.handle_command(session, "unpause alfa")
        self.assertEqual(session.lines, [lifted("alfa", "Bravo", 3)])


if __name__ == "__main__":
    unittest.main()
