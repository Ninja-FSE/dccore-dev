"""A rehash must never show a running daemon a configuration it does not have.

WHAT WENT WRONG

Two defects, one report, both in the rehash path.

1. THE RELOAD WINDOW. `importlib.reload(defaults)` re-executes defaults.py from
   the top. That file is a long list of literal assignments - NICKNAME = None,
   CHANNEL = None, ADMIN_NICK = None - and `settings_file.apply_to(globals())`
   runs only at the very end of it. So for the whole of a reload, every setting
   the operator configured is transiently back to its shipped default.

   Measured against a real install's settings.conf: a thread looping on
   config.NICKNAME during the reload saw it blank for 52% of the window.

   An operator saved DEBUG_CHANNEL from the dashboard. The browser re-fetched
   /api/settings the moment the response said "Rehash started", landed inside
   the window, and the Settings page came back with Nickname, Admin nick(s) and
   Channels EMPTY - on a daemon whose settings.conf had all three, intact, the
   entire time. Only those three looked wrong because every other setting on
   that page has a shipped default that happens to match what most operators
   run, so it renders identically either way.

2. THE DEBUG CHANNEL WAS NEVER JOINED. The rehash's channel sync built its
   list from CHANNEL alone; DEBUG_CHANNEL appeared in that block only in the
   test that stops it being PARTed. irc.py joins it at connect and nothing
   joins it after that. So setting a debug channel on a running bot reported
   success and did nothing until the next restart.

Both share the signature that keeps costing this project real installs:
NOTHING FAILED. No exception, no error line, no failing test - just a page
telling the truth about a state that should never have been observable, and a
JOIN that was never sent.
"""

import io
import contextlib
import os
import sys
import threading
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if os.path.join(REPO_ROOT, "tests") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import commands  # noqa: E402
import dcc  # noqa: E402
import defaults as config  # noqa: E402
import runtime  # noqa: E402
import settings_file  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


class TheDebugChannelIsSomewhereTheBotShouldBe(DCCoreTestCase):
    """_channels_to_sync() is both sides of the JOIN/PART comparison. The bug
    was that the debug channel was in neither."""

    def test_the_debug_channel_is_included(self):
        self.set_config(CHANNEL="#one,#two", DEBUG_CHANNEL="#bot-debug")

        self.assertIn("#bot-debug", commands._channels_to_sync(self.config))

    def test_the_reported_case(self):
        """Verbatim from the install that found it: six channels configured,
        a debug channel added afterwards from the dashboard."""
        self.set_config(
            CHANNEL="#Music,#servers,#downloads,#best-of,"
                    "#country,#albums",
            DEBUG_CHANNEL="#bot-debug")

        chans = commands._channels_to_sync(self.config)

        self.assertEqual(len(chans), 7)
        self.assertIn("#bot-debug", chans)

    def test_a_blank_debug_channel_adds_nothing(self):
        """Blank is the shipped default and means "no debug channel", not a
        channel named "". Joining "" would be a malformed JOIN."""
        for blank in ("", "   ", None):
            with self.subTest(blank=blank):
                self.set_config(CHANNEL="#one", DEBUG_CHANNEL=blank)

                self.assertEqual(commands._channels_to_sync(self.config), ["#one"])

    def test_it_is_not_added_twice_when_it_is_already_a_main_channel(self):
        """An operator may well point DEBUG_CHANNEL at a channel already in
        CHANNEL. A duplicate would be JOINed and NAMEd twice."""
        self.set_config(CHANNEL="#one,#two", DEBUG_CHANNEL="#TWO")

        self.assertEqual(commands._channels_to_sync(self.config), ["#one", "#two"])

    def test_an_unchanged_debug_channel_produces_no_join(self):
        """THE REASON IT IS ON BOTH SIDES. old_chans and new_chans both come
        from this helper, so a debug channel that has not changed appears in
        both and the sync has nothing to do. Putting it only in new_chans would
        have fixed the reported bug and re-JOINed the channel, with a "due to
        new configuration layout!" debug line, on every rehash for ever."""
        self.set_config(CHANNEL="#one", DEBUG_CHANNEL="#bot-debug")
        old_chans = commands._channels_to_sync(self.config)

        new_chans = commands._channels_to_sync(self.config)

        self.assertEqual([c for c in new_chans if c not in old_chans], [])

    def test_a_newly_set_debug_channel_produces_exactly_one_join(self):
        """The reported sequence: no debug channel, then one."""
        self.set_config(CHANNEL="#one", DEBUG_CHANNEL="")
        old_chans = commands._channels_to_sync(self.config)

        self.set_config(CHANNEL="#one", DEBUG_CHANNEL="#bot-debug")
        new_chans = commands._channels_to_sync(self.config)

        self.assertEqual([c for c in new_chans if c not in old_chans], ["#bot-debug"])

    def test_whitespace_and_case_are_normalised(self):
        """The comparison is by name, and IRC channel names are
        case-insensitive. " #Bot-Debug " and "#bot-debug" are one channel."""
        self.set_config(CHANNEL=" #One , #two ", DEBUG_CHANNEL="  #Bot-Debug  ")

        self.assertEqual(commands._channels_to_sync(self.config),
                         ["#one", "#two", "#bot-debug"])

    def test_a_blank_channel_setting_does_not_raise(self):
        """CHANNEL is in settings_file.REQUIRED, but this runs on a config
        object anything can assign to - and it used to be a bare
        config.CHANNEL.split(","), which is an AttributeError on None. That
        exception would land on the rehash thread, after the reload and before
        the channel sync finished."""
        for blank in ("", None):
            with self.subTest(blank=blank):
                self.set_config(CHANNEL=blank, DEBUG_CHANNEL="#bot-debug")

                self.assertEqual(commands._channels_to_sync(self.config),
                                 ["#bot-debug"])


class TheReloadWindowIsNotObservable(unittest.TestCase):
    """The property: no thread holding the lock ever sees a REQUIRED setting
    blank, however long the reload takes or however often it runs."""

    def test_a_reader_holding_the_lock_never_sees_a_blank_required_setting(self):
        import defaults as config

        blanks = []
        reads = [0]
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                with runtime.config_reload_lock:
                    for name in settings_file.REQUIRED:
                        if not getattr(config, name, None):
                            blanks.append(name)
                reads[0] += 1

        # Only meaningful if the settings actually resolve to something in this
        # environment - otherwise "never blank" is vacuously true.
        if any(not getattr(config, name, None) for name in settings_file.REQUIRED):
            self.skipTest("no REQUIRED settings are configured in this checkout")

        worker = threading.Thread(target=reader, daemon=True)
        worker.start()
        try:
            for _ in range(8):
                commands.reload_modules_in_order(modules=('defaults',),
                                                 reload_self=False)
        finally:
            stop.set()
            worker.join(5)

        self.assertGreater(reads[0], 0, "the reader never ran")
        self.assertEqual(blanks, [],
                         f"a lock-holding reader saw {sorted(set(blanks))} blank "
                         f"during a reload - the window is observable again")

    def test_the_reload_actually_holds_the_lock(self):
        """The guard above passes trivially if the reload never takes the lock -
        a reader would then simply never contend. Assert the reload really is
        holding it, by watching from another thread."""
        held = []
        ready = threading.Event()
        done = threading.Event()

        def watcher():
            ready.set()
            # acquire(False) fails only while another thread holds it.
            while not done.is_set():
                if not runtime.config_reload_lock.acquire(blocking=False):
                    held.append(True)
                    return
                runtime.config_reload_lock.release()

        worker = threading.Thread(target=watcher, daemon=True)
        worker.start()
        ready.wait(2)
        try:
            for _ in range(30):
                commands.reload_modules_in_order(modules=('defaults',),
                                                 reload_self=False)
                if held:
                    break
        finally:
            done.set()
            worker.join(5)

        self.assertTrue(held, "no other thread was ever blocked by the reload - "
                              "reload_modules_in_order() is not holding "
                              "runtime.config_reload_lock")


class ARequiredSettingThatComesBackBlankIsKept(DCCoreTestCase):
    """The backstop, for when the window does not close cleanly.

    settings_file.apply_to() is deliberately forgiving about an unreadable
    settings.conf - right at startup, where oserve.startup()'s REQUIRED gate
    then refuses to boot and says why; wrong at rehash, where there is no gate
    and the daemon simply carries on with nothing configured.
    """

    def test_a_value_that_survives_is_left_alone(self):
        """Control: the ordinary rehash must not be touched by the backstop."""
        import defaults as config

        before = {name: getattr(config, name, None)
                  for name in settings_file.REQUIRED}

        commands.reload_modules_in_order(modules=('defaults',), reload_self=False)

        for name, value in before.items():
            self.assertEqual(getattr(config, name, None), value)

    def test_a_blank_after_reload_is_restored_from_the_running_value(self):
        """The failure this exists for: settings.conf readable a moment ago and
        not now - antivirus holding it open, a network share blinking, an
        editor mid-save. apply_to() logs that and keeps the built-in defaults,
        which at this point in the daemon's life means no nickname, no channels
        and no admin on a bot that is already connected and serving.

        Driven through the real function with importlib.reload replaced by one
        that blanks the setting, because that blanking IS the observable effect
        of the read failure - reproducing the antivirus is not the point.
        """
        import importlib

        import defaults as config

        self.set_config(NICKNAME="StillRunningAs")

        def blanking_reload(module):
            config.NICKNAME = ""
            return module

        real_reload = importlib.reload
        importlib.reload = blanking_reload
        try:
            commands.reload_modules_in_order(modules=('runtime',),
                                             reload_self=False)
        finally:
            importlib.reload = real_reload

        self.assertEqual(config.NICKNAME, "StillRunningAs",
                         "a REQUIRED setting that came back blank was not "
                         "restored - a rehash can de-configure a running bot")

    def test_only_required_settings_are_backstopped(self):
        """The narrowness is the safety. If this covered every setting it would
        be a ratchet: no rehash could ever unset anything, because the previous
        value would always be put back. DEBUG_CHANNEL - the very setting whose
        save started all this - must NOT be protected, or clearing it on the
        dashboard would silently do nothing."""
        import importlib

        import defaults as config

        self.assertNotIn("DEBUG_CHANNEL", settings_file.REQUIRED)
        self.set_config(DEBUG_CHANNEL="#bot-debug")

        def blanking_reload(module):
            config.DEBUG_CHANNEL = ""
            return module

        real_reload = importlib.reload
        importlib.reload = blanking_reload
        try:
            commands.reload_modules_in_order(modules=('runtime',),
                                             reload_self=False)
        finally:
            importlib.reload = real_reload

        self.assertEqual(config.DEBUG_CHANNEL, "",
                         "a non-REQUIRED setting was restored by the backstop - "
                         "no rehash could then ever unset one")

    def test_a_value_that_was_already_blank_is_not_invented(self):
        """A fresh install has no ADMIN_NICK yet and is about to be told so by
        the REQUIRED gate. The backstop must not paper over that."""
        import importlib

        import defaults as config

        self.set_config(NICKNAME="")

        real_reload = importlib.reload
        importlib.reload = lambda module: module
        try:
            commands.reload_modules_in_order(modules=('runtime',),
                                             reload_self=False)
        finally:
            importlib.reload = real_reload

        self.assertEqual(config.NICKNAME, "")


class TheDashboardReadsUnderTheLock(unittest.TestCase):
    """Read out of the source rather than by timing: the race is real but
    winning it on demand in a test is not reliable, and a payload builder that
    stopped taking the lock would pass every timing-based check on a fast
    machine."""

    def test_build_settings_payload_takes_the_reload_lock(self):
        with io.open(os.path.join(REPO_ROOT, "webserver.py"),
                     encoding="utf-8") as handle:
            source = handle.read()

        body = source.split("def build_settings_payload(", 1)[1]
        body = body.split("\ndef ", 1)[0]
        # The docstring names the lock too, and an assertion that matches prose
        # passes on a function that has stopped taking it - which is exactly
        # what the mutation run found. Look for the statement, not the noun.
        body = body.split('"""', 2)[-1]

        self.assertIn("with runtime.config_reload_lock:", body,
                      "build_settings_payload() no longer reads under "
                      "runtime.config_reload_lock - the Settings page can show "
                      "a half-reloaded configuration again")


class ARehashDoesNotSpendTheQueuesRetries(DCCoreTestCase):
    """Neo: "if the bot had some queues from a user, and admin made a rehash,
    it cancels the queue."

    Traced to the wake. Every rehash ends by calling check_queue_and_send() to
    let queued users into the free slots - and if that attempt fails, the
    failure is charged to the user's retry budget. MAX_SEND_FAILS is 3, so
    three rehashes deleted the row.

    The cause of the failure is what makes it wrong. "No usable public
    address" is the BOT's configuration: it affects every queued user
    identically and is fixed by the operator setting MY_IP_OR_DOCK, not by the
    user waiting. A file that is missing or empty is a different thing - that
    row really is dead, and charging it is what stops it being re-selected
    every three seconds for ever.
    """

    def setUp(self):
        super().setUp()
        self.set_config(dcc_queue={}, frozen_queues={}, channel_users={},
                        MY_IP_OR_DOCK="", MAX_SEND_FAILS=3)
        config.dcc_queue["someuser"] = [{"file": "T.flac", "path": "/m/T.flac"}]

    def row(self):
        return config.dcc_queue.get("someuser", [None])[0]

    def attempt(self):
        """One dispatch attempt for the queued user, as the wake makes."""
        row = config.dcc_queue["someuser"][0]
        with contextlib.redirect_stdout(io.StringIO()):
            dcc.start_dcc_send(None, "someuser", row["path"], row["file"],
                               "#chan", row)

    def test_no_public_address_does_not_charge_the_user(self):
        for _ in range(5):
            self.attempt()

        self.assertIn("someuser", config.dcc_queue)
        self.assertNotIn("send_fails", self.row() or {},
                         "an unset MY_IP_OR_DOCK is being charged to the user")

    def test_the_queue_survives_repeated_rehash_wakes(self):
        """The shape of the report: it took three, and the third one was
        silent about what it had just thrown away."""
        for _ in range(4):
            self.attempt()

        self.assertEqual(len(config.dcc_queue.get("someuser", [])), 1)

    def test_the_log_says_the_address_is_what_is_missing(self):
        """It said "file missing, empty, or public IP unknown" - three
        different problems in one sentence, which is how an operator ends up
        looking for a file that is perfectly present."""
        row = config.dcc_queue["someuser"][0]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            dcc.start_dcc_send(None, "someuser", row["path"], row["file"],
                               "#chan", row)

        self.assertIn("the address, not the file", buffer.getvalue())

    def test_a_genuinely_missing_file_is_still_charged(self):
        """The hot loop this budget exists to bound: without it the same
        unreadable entry was re-selected every three seconds for ever, with no
        counter and no way out."""
        # A PUBLIC-LOOKING address, because is_offerable_to_strangers()
        # correctly refuses the documentation ranges - 203.0.113.x is reserved
        # and unreachable from a real network, which is the other half of this
        # same branch. Never dialled: the send aborts on the missing file
        # first. The precondition is asserted rather than assumed, so this
        # cannot quietly start testing the address branch again.
        self.set_config(MY_IP_OR_DOCK="8.8.8.8")
        self.assertTrue(dcc.is_offerable_to_strangers("8.8.8.8"),
                        "fixture invariant: this test needs an address the "
                        "send path accepts, or it tests the wrong branch")

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            row0 = config.dcc_queue["someuser"][0]
            dcc.start_dcc_send(None, "someuser", row0["path"], row0["file"],
                               "#chan", row0)

        row = self.row()
        self.assertTrue(row is None or row.get("send_fails"),
                        "a dead row is no longer charged, so nothing bounds "
                        "its re-selection")
        # And it says WHICH of the two things went wrong. The old message
        # named three at once - "file missing, empty, or public IP unknown" -
        # which is how an operator ends up hunting for a file that is present.
        self.assertIn("file missing or empty", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
