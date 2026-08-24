"""Regression tests for irc.py trigger matching and connection scoping.

Two independent things are pinned down here.

1. ``irc.get_bot_aliases()`` - the fix for the 433 nick-collision blackout. The
   master list is written by ``update_list.py`` as a subprocess, so every pasted
   request line is stamped with the config.py default nick. While the bot ran as
   the alternate nick the dispatcher matched only the LIVE nick, so every
   "!DCCore Song.flac" was dropped silently.

2. The trigger expressions themselves. Rather than retyping them - which would
   make the tests agree with themselves and not with the daemon - the conditions
   are READ OUT OF irc.py and evaluated. If somebody edits the dispatch chain the
   tests here evaluate the edited condition, so a regression shows up as a
   failure instead of as a test that quietly stops describing the code.
"""

import os
import unittest

from tests.support import (DCCoreTestCase, reset_config, install_fake_oserve,
                           silence_debug, no_disk_writes, queue_row,
                           CapturedDispatch, TempTree, RecordingSocket, DeadSocket)

import config
import irc


IRC_SOURCE_PATH = os.path.abspath(irc.__file__)
if IRC_SOURCE_PATH.endswith(".pyc"):  # pragma: no cover - defensive
    IRC_SOURCE_PATH = IRC_SOURCE_PATH[:-1]

with open(IRC_SOURCE_PATH, "r", encoding="utf-8") as _handle:
    IRC_LINES = _handle.read().splitlines()


# The two conditions this module evaluates, identified by a fragment unique to
# each. The live-nick fragment keeps its closing quote attached so it does NOT
# match the "-que"/"-remove" variants further down the chain.
ALIAS_FRAGMENT = 'startswith(f"!{alias} ")'
LIVE_NICK_FRAGMENT = 'msg_lower == f"@{config.NICKNAME.lower()}"'


def _elif_conditions(fragment=None):
    """Return (line_index, condition_source) for every ``elif`` line in irc.py.

    Only single-line ``elif ...:`` headers are considered; the dispatch chain is
    written that way throughout.
    """
    found = []
    for index, raw in enumerate(IRC_LINES):
        stripped = raw.strip()
        if not stripped.startswith("elif ") or not stripped.endswith(":"):
            continue
        if fragment is not None and fragment not in stripped:
            continue
        found.append((index, stripped[len("elif "):-1].strip()))
    return found


def _one_elif_condition(fragment):
    """The single dispatch-chain ``elif`` carrying ``fragment``."""
    matches = _elif_conditions(fragment)
    if len(matches) != 1:
        raise AssertionError(
            "expected exactly one 'elif' in irc.py containing %r, found %d"
            % (fragment, len(matches)))
    return matches[0]


def _flood_gate_source():
    """The ``is_bot_command = (...)`` expression, read out of irc.py."""
    start = None
    for index, raw in enumerate(IRC_LINES):
        if raw.strip() == "is_bot_command = (":
            start = index
            break
    if start is None:
        raise AssertionError("could not find the 'is_bot_command = (' gate in irc.py")
    body = []
    for raw in IRC_LINES[start + 1:]:
        stripped = raw.strip()
        if stripped == ")":
            return "(\n" + "\n".join(body) + "\n)"
        body.append(stripped)
    raise AssertionError("unterminated 'is_bot_command' expression in irc.py")


ALIAS_LINE, ALIAS_CONDITION = _one_elif_condition(ALIAS_FRAGMENT)
LIVE_NICK_LINE, LIVE_NICK_CONDITION = _one_elif_condition(LIVE_NICK_FRAGMENT)
FLOOD_GATE_SOURCE = _flood_gate_source()


def _evaluate(source, msg):
    """Evaluate a condition lifted from irc.py against one PRIVMSG payload.

    ``msg``/``msg_lower``/``bot_aliases`` are bound exactly as irc.py binds them
    at the point the condition runs, so the surrounding config state decides the
    outcome just as it does in the daemon. irc.py strips the payload before
    dispatching, so the corpus here is pre-stripped too.
    """
    namespace = {
        "msg": msg,
        "msg_lower": msg.lower(),
        "bot_aliases": irc.get_bot_aliases(),
        "config": config,
    }
    return bool(eval(source, namespace))  # source comes from irc.py itself


class BotAliasTests(DCCoreTestCase):
    """irc.get_bot_aliases() - the nick-collision blackout fix."""

    def test_single_nick_collapses_to_one_alias(self):
        """Defect guard: widening the trigger must not change normal operation.

        With NICKNAME == ORIGINAL_NICK the alias set is one entry, so the trigger
        built from it is byte-identical to the old hardcoded "!dccore " one.
        """
        reset_config(NICKNAME="DCCore", ORIGINAL_NICK="DCCore")
        self.assertEqual(irc.get_bot_aliases(), ["dccore"])

    def test_fallback_returns_both_lowercased_current_first(self):
        """Defect: after a 433 fallback the bot ignored every "!DCCore <file>".

        The master list is stamped by the update_list.py subprocess with the
        config.py default nick, so ORIGINAL_NICK has to stay answerable.
        """
        reset_config(NICKNAME="DCCore_", ORIGINAL_NICK="DCCore")
        self.assertEqual(irc.get_bot_aliases(), ["dccore_", "dccore"])

    def test_list_base_name_is_not_an_alias(self):
        """Defect guard: LIST_BASE_NAME is a filename constant, not a nick.

        Letting it become a live public trigger would let the list filename
        dictate what the bot answers to on IRC.
        """
        reset_config(NICKNAME="DCCore", ORIGINAL_NICK="DCCore",
                     LIST_BASE_NAME="MusicPack")
        aliases = irc.get_bot_aliases()
        self.assertEqual(aliases, ["dccore"])
        self.assertNotIn("musicpack", aliases)

    def test_previous_nick_is_honoured_when_present(self):
        """Defect guard: !rehash renames the bot; PREVIOUS_NICK keeps the request
        lines of the already-distributed list working until it is regenerated."""
        reset_config(NICKNAME="DCCore2", ORIGINAL_NICK="DCCore2",
                     PREVIOUS_NICK="DCCore")
        self.assertEqual(irc.get_bot_aliases(), ["dccore2", "dccore"])

    def test_previous_nick_absent_is_not_an_error(self):
        """Defect guard: PREVIOUS_NICK only exists after a rehash rename, so the
        attribute is normally missing entirely and must be tolerated."""
        reset_config()
        self.assertFalse(hasattr(config, "PREVIOUS_NICK"))
        self.assertEqual(irc.get_bot_aliases(), ["dccore"])

    def test_blank_values_are_skipped(self):
        """Defect guard: an empty or whitespace nick must not become the alias
        "" - "!<empty> " would degrade into a bare "! " prefix trigger."""
        reset_config(NICKNAME="DCCore", ORIGINAL_NICK="", PREVIOUS_NICK="   ")
        self.assertEqual(irc.get_bot_aliases(), ["dccore"])
        self.assertNotIn("", irc.get_bot_aliases())

    def test_none_values_are_skipped(self):
        """Defect guard: a None nick must be dropped, not stringified to "none",
        which would make "!none <file>" a live public trigger."""
        reset_config(NICKNAME="DCCore", ORIGINAL_NICK=None, PREVIOUS_NICK=None)
        self.assertEqual(irc.get_bot_aliases(), ["dccore"])
        self.assertNotIn("none", irc.get_bot_aliases())

    def test_aliases_are_deduplicated(self):
        """Defect guard: duplicates would make the "!<alias> " generator match the
        same message repeatedly, and would double-dispatch a request the moment
        the chain stopped short-circuiting."""
        reset_config(NICKNAME="DCCore", ORIGINAL_NICK="dccore",
                     PREVIOUS_NICK="DCCORE")
        self.assertEqual(irc.get_bot_aliases(), ["dccore"])

    def test_case_and_whitespace_are_normalised(self):
        """Defect guard: aliases are compared against a lowercased message, so an
        unlowered or padded nick would never match anything at all."""
        reset_config(NICKNAME="  DcCoRe_  ", ORIGINAL_NICK="DCCore")
        self.assertEqual(irc.get_bot_aliases(), ["dccore_", "dccore"])


class TriggerExpressionTests(DCCoreTestCase):
    """The dispatch conditions, evaluated as they are actually written."""

    def test_conditions_were_lifted_from_the_source(self):
        """Guard on the test method itself: if the dispatch chain is rewritten so
        these fragments no longer appear, this module must fail loudly rather
        than silently testing nothing."""
        self.assertIn("bot_aliases", ALIAS_CONDITION)
        self.assertIn("config.NICKNAME", LIVE_NICK_CONDITION)
        self.assertIn("bot_aliases", FLOOD_GATE_SOURCE)
        # The alias dispatch sits in the chain, below the list-request branch.
        self.assertGreater(ALIAS_LINE, LIVE_NICK_LINE)

    # --- normal operation: identical to the old hardcoded trigger -----------

    def test_main_nick_alias_trigger_matches_as_before(self):
        """Defect guard: on the main nick the alias trigger must behave exactly
        like the old hardcoded 'msg_lower.startswith("!dccore ")'."""
        reset_config(NICKNAME="DCCore", ORIGINAL_NICK="DCCore")
        for msg in ("!DCCore Song.flac",
                    "!dccore Artist/Album/Song.flac",
                    "!DcCoRe !rar Artist/Album",
                    "!DCCore  spaced.flac",
                    "!DCCoreX Song.flac",
                    "!DCCore",
                    "hello !DCCore Song.flac",
                    "!other Song.flac",
                    "@DCCore"):
            with self.subTest(msg=msg):
                self.assertEqual(_evaluate(ALIAS_CONDITION, msg),
                                 msg.lower().startswith("!dccore "))

    def test_main_nick_list_request_matches(self):
        """Defect guard: "@<nick>" is the master-list request and stays an exact
        match, so "@DCCore-que" is not swallowed by it."""
        reset_config(NICKNAME="DCCore", ORIGINAL_NICK="DCCore")
        self.assertTrue(_evaluate(LIVE_NICK_CONDITION, "@DCCore"))
        self.assertTrue(_evaluate(LIVE_NICK_CONDITION, "@dccore"))
        for msg in ("@DCCore-que", "@DCCore-remove", "@DCCore please",
                    "@DCCoreX", "DCCore"):
            with self.subTest(msg=msg):
                self.assertFalse(_evaluate(LIVE_NICK_CONDITION, msg))

    # --- during a 433 fallback ---------------------------------------------

    def test_fallback_alias_request_dispatches(self):
        """Defect: running as "DCCore_", every "!DCCore Song.flac" pasted from the
        master list was dropped with no reply and no log line."""
        reset_config(NICKNAME="DCCore_", ORIGINAL_NICK="DCCore")
        self.assertTrue(_evaluate(ALIAS_CONDITION, "!DCCore Song.flac"))
        self.assertTrue(_evaluate(ALIAS_CONDITION, "!DCCore_ Song.flac"))
        # The pre-fix, live-nick-only trigger would have rejected the first one,
        # which is exactly the blackout this fix removed.
        self.assertFalse("!DCCore Song.flac".lower().startswith(
            "!" + config.NICKNAME.lower() + " "))

    def test_fallback_list_request_stays_live_nick_only(self):
        """Defect guard, deliberate asymmetry: "@<nick>" is typed live and the
        advert publishes the LIVE nick, so during a fallback "@DCCore" addresses
        whichever client now holds the main nick - the bot must not answer it."""
        reset_config(NICKNAME="DCCore_", ORIGINAL_NICK="DCCore")
        self.assertFalse(_evaluate(LIVE_NICK_CONDITION, "@DCCore"))
        self.assertFalse(_evaluate(LIVE_NICK_CONDITION, "@dccore"))
        self.assertTrue(_evaluate(LIVE_NICK_CONDITION, "@DCCore_"))

    def test_fallback_rejects_near_miss_nicks(self):
        """Defect guard: widening to an alias set must not turn the trigger into
        a loose prefix match - "!DCCoreX" belongs to a different bot."""
        reset_config(NICKNAME="DCCore_", ORIGINAL_NICK="DCCore")
        for msg in ("!DCCoreX Song.flac", "!DCCore_X Song.flac",
                    "!DCCore-que Song.flac", "!DCCore", "!DCCore_",
                    "!DCC Song.flac"):
            with self.subTest(msg=msg):
                self.assertFalse(_evaluate(ALIAS_CONDITION, msg))

    def test_previous_nick_request_dispatches_after_rehash(self):
        """Defect guard: after a !rehash rename the lists already in users' hands
        still carry the old nick and must keep working."""
        reset_config(NICKNAME="DCCore2", ORIGINAL_NICK="DCCore2",
                     PREVIOUS_NICK="DCCore")
        self.assertTrue(_evaluate(ALIAS_CONDITION, "!DCCore Song.flac"))
        self.assertTrue(_evaluate(ALIAS_CONDITION, "!DCCore2 Song.flac"))

    def test_alias_branch_is_actually_reachable(self):
        """Defect guard: the alias dispatch is the LAST elif in the chain, so an
        earlier branch matching "!DCCore <file>" would shadow it completely."""
        reset_config(NICKNAME="DCCore_", ORIGINAL_NICK="DCCore")
        msg = "!DCCore Song.flac"
        shadowing = []
        for index, condition in _elif_conditions():
            if not (LIVE_NICK_LINE <= index < ALIAS_LINE):
                continue
            try:
                matched = _evaluate(condition, msg)
            except NameError:
                # Branch guarded by names bound elsewhere in the read loop
                # (ctcp_cmd, line, ...); not a plain message predicate.
                continue
            if matched:
                shadowing.append(condition)
        self.assertEqual(shadowing, [],
                         "an earlier elif shadows the alias download trigger")


class FloodGateCoverageTests(DCCoreTestCase):
    """The is_bot_command gate must meter every request trigger it dispatches."""

    def _gate(self, msg):
        return _evaluate(FLOOD_GATE_SOURCE, msg)

    def test_gate_covers_every_public_request_trigger(self):
        """Defect guard: a gate narrower than the dispatch is an unmetered
        command path - which is what pre-fix fallback traffic was, neither
        dispatched nor even seen by the flood counter."""
        for nickname, original in (("DCCore", "DCCore"), ("DCCore_", "DCCore")):
            reset_config(NICKNAME=nickname, ORIGINAL_NICK=original)
            for msg in ("@" + nickname, "@find metallica", "@locator metallica",
                        "!" + nickname + " Song.flac", "!DCCore Song.flac"):
                with self.subTest(nick=nickname, msg=msg):
                    self.assertTrue(self._gate(msg))

    def test_gate_is_never_narrower_than_the_dispatch(self):
        """Defect guard: every message the alias or list-request branch would
        dispatch must first have passed through the flood meter."""
        corpus = ["!DCCore Song.flac", "!DCCore_ Song.flac", "!dccore x",
                  "!DCCore !rar Artist/Album", "!DCCoreX Song.flac",
                  "!DCCore2 Song.flac", "@DCCore", "@DCCore_", "@DCCore2",
                  "@find x", "@locator x", "hello world", "!ping"]
        for nickname, original, previous in (("DCCore", "DCCore", None),
                                             ("DCCore_", "DCCore", None),
                                             ("DCCore2", "DCCore2", "DCCore")):
            kwargs = {"NICKNAME": nickname, "ORIGINAL_NICK": original}
            if previous:
                kwargs["PREVIOUS_NICK"] = previous
            reset_config(**kwargs)
            for msg in corpus:
                with self.subTest(nick=nickname, msg=msg):
                    if _evaluate(ALIAS_CONDITION, msg):
                        self.assertTrue(self._gate(msg))
                    if _evaluate(LIVE_NICK_CONDITION, msg):
                        self.assertTrue(self._gate(msg))

    def test_gate_is_not_wider_than_the_dispatch(self):
        """Defect guard: a gate wider than the dispatch charges users flood points
        for messages the bot ignores - during a fallback "@DCCore" is not
        answered, so it must not be metered either."""
        reset_config(NICKNAME="DCCore_", ORIGINAL_NICK="DCCore")
        self.assertFalse(_evaluate(LIVE_NICK_CONDITION, "@DCCore"))
        self.assertFalse(self._gate("@DCCore"))
        self.assertFalse(self._gate("just chatting about DCCore"))
        self.assertFalse(self._gate("!DCCoreX Song.flac"))

    def test_gate_uses_the_same_alias_set_as_the_dispatch(self):
        """Defect guard: the gate and the dispatch must be built from the same
        get_bot_aliases() call, not from two hardcoded lists that can drift."""
        self.assertIn("bot_aliases", FLOOD_GATE_SOURCE)
        self.assertIn("bot_aliases", ALIAS_CONDITION)
        reset_config(NICKNAME="DCCore2", ORIGINAL_NICK="DCCore2",
                     PREVIOUS_NICK="DCCore")
        self.assertTrue(_evaluate(ALIAS_CONDITION, "!DCCore Song.flac"))
        self.assertTrue(self._gate("!DCCore Song.flac"))


class ConnectionScopeTests(DCCoreTestCase):
    """irc._release_socket() - the stale shared-socket reference."""

    def test_release_socket_clears_the_shared_reference(self):
        """Defect: queue_mgr and announce.send_debug both guard on
        "if current_sock:", so a reference left pointing at a CLOSED socket made
        those guards useless for the whole reconnect."""
        self.oserve.irc_connection = RecordingSocket()
        irc._release_socket()
        self.assertIsNone(self.oserve.irc_connection)

    def test_release_socket_survives_a_dead_socket(self):
        """Defect guard: the reference is dropped without touching the socket, so
        a peer that vanished in a netsplit cannot raise out of the cleanup."""
        self.oserve.irc_connection = DeadSocket()
        irc._release_socket()
        self.assertIsNone(self.oserve.irc_connection)

    def test_release_socket_without_oserve_is_a_no_op(self):
        """Defect guard: _release_socket also runs during teardown, where the
        oserve module may already be gone; it must not raise."""
        import sys
        saved = sys.modules.pop("oserve", None)
        try:
            irc._release_socket()
        finally:
            if saved is not None:
                sys.modules["oserve"] = saved


if __name__ == "__main__":
    unittest.main()
