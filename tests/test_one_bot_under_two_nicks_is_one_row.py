"""#376, part 1: one bot seen under two nicks is one List Browser row.

Two ways, both decided on #376 and both display only - fetched_bot_lists,
known_bots and the counters stay keyed per nick:

- A NICK message from a known bot is proof: its rows merge under the new nick.
- Option B, by ident: same ident, same advertised file count, the old nick's
  departure OBSERVED (not just absent), and never advertising at the same
  time. The ident is held in memory only - never written, never logged -
  and no host or IP is kept at all.
"""

import io
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import defaults as config  # noqa: E402
import irc  # noqa: E402
import runtime  # noqa: E402
import webserver  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402

T0 = 1_000_000.0


class Case(DCCoreTestCase):
    def setUp(self):
        super().setUp()
        config.channel_users["#chan"] = set()
        original = dict(runtime.known_bots)
        runtime.known_bots.clear()
        self.addCleanup(lambda: (runtime.known_bots.clear(), runtime.known_bots.update(original)))

    def advertise(self, nick, at, files=5000, ident="packbot", online=True):
        """A bot's advert arriving at `at`, from nick!ident@host."""
        key = nick.lower()
        entry = dict(runtime.known_bots.get(key) or {})
        entry.update({"nick": nick, "files": files, "last_seen": at})
        runtime.known_bots[key] = entry
        irc._capture_bot_ident(nick, f"{ident}@host-{nick}.example.net", now=at)
        if online:
            config.channel_users["#chan"].add(key)

    def leave(self, nick, at):
        """An observed QUIT/PART - what the handlers do."""
        config.channel_users["#chan"].discard(nick.lower())
        irc.note_observed_departure(nick.lower(), "#chan", now=at)

    def merges(self):
        return webserver._ident_merges(dict(runtime.known_bots), webserver.present_nicks())


class ByIdent(Case):
    def the_usual_reconnect(self):
        """PackBot advertises, its connection dies; it comes back as PackBot_
        while the ghost still sits in the channel, then the ghost times out."""
        self.advertise("PackBot", T0)
        self.advertise("PackBot_", T0 + 300)
        self.leave("PackBot", T0 + 400)

    def test_the_usual_reconnect_merges(self):
        self.the_usual_reconnect()
        self.assertEqual(self.merges(), {"packbot": "PackBot_"})

    def test_a_different_ident_does_not(self):
        self.advertise("PackBot", T0)
        self.advertise("PackBot_", T0 + 300, ident="someoneelse")
        self.leave("PackBot", T0 + 400)
        self.assertEqual(self.merges(), {})

    def test_a_different_file_count_does_not(self):
        self.advertise("PackBot", T0, files=5000)
        self.advertise("PackBot_", T0 + 300, files=5001)
        self.leave("PackBot", T0 + 400)
        self.assertEqual(self.merges(), {})

    def test_absence_alone_is_not_a_departure(self):
        """Gone from the channel without a QUIT/PART we saw: it may never
        have been in a channel we share."""
        self.advertise("PackBot", T0)
        self.advertise("PackBot_", T0 + 300)
        config.channel_users["#chan"].discard("packbot")
        self.assertEqual(self.merges(), {})

    def test_back_again_is_not_merged(self):
        self.the_usual_reconnect()
        config.channel_users["#chan"].add("packbot")
        self.assertEqual(self.merges(), {})

    def test_two_bots_advertising_at_the_same_time_are_two_bots(self):
        """Same ident, same count, but both were alive at once: the old
        nick's last advert came after the new nick's first."""
        self.advertise("PackBot", T0)
        self.advertise("PackBot_", T0 + 300)
        self.advertise("PackBot", T0 + 350)
        self.leave("PackBot", T0 + 400)
        self.assertEqual(self.merges(), {})

    def test_two_candidates_are_not_an_answer(self):
        self.advertise("PackBot", T0)
        self.advertise("PackBot_", T0 + 300)
        self.advertise("PackBot__", T0 + 310)
        self.leave("PackBot", T0 + 400)
        self.assertEqual(self.merges(), {})

    def test_only_a_known_bot_ident_is_kept(self):
        irc._capture_bot_ident("SomeUser", "someuser@host.example.net", now=T0)
        self.assertNotIn("someuser", runtime.bot_idents)

    def test_a_changed_ident_is_a_new_connection(self):
        self.advertise("PackBot", T0)
        self.advertise("PackBot", T0 + 60, ident="other")
        self.assertEqual(runtime.bot_idents["packbot"], {"ident": "other", "first_seen": T0 + 60})

    def test_a_departure_is_recorded_only_for_a_bot_with_an_ident(self):
        irc.note_observed_departure("someuser", "#chan", now=T0)
        self.assertNotIn("someuser", runtime.bot_departures)

    def test_the_sidebar_shows_one_row_under_the_current_nick(self):
        self.the_usual_reconnect()
        self.set_config(fetched_bot_lists={"packbot": {
            "bot": "PackBot", "fetched_at": T0, "entry_count": 10,
            "advert_when_fetched": {"files": 5000}}})
        rows = webserver.build_fetched_bot_list_summaries()
        mine = [r for r in rows if str(r.get("bot", "")).lower().startswith("packbot")]
        self.assertEqual(sorted(r["bot"] for r in mine), ["PackBot", "PackBot_"],
                         "display only: each row keeps its real nick")
        self.assertEqual({r["nick"] for r in mine}, {"PackBot_"})


class MemoryOnly(Case):
    def test_no_host_is_kept_in_any_form(self):
        self.advertise("PackBot", T0)
        self.assertEqual(runtime.bot_idents["packbot"], {"ident": "packbot", "first_seen": T0})
        self.assertNotIn("ident", runtime.known_bots["packbot"], "the registry is saved to disk")

    def test_nothing_that_writes_to_disk_reads_it(self):
        for name in ("db.py", "irc.py", "announce.py"):
            with io.open(os.path.join(REPO_ROOT, name), encoding="utf-8") as handle:
                code = handle.read()
            for line in code.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if name != "irc.py":
                    self.assertNotIn("bot_idents", stripped, f"{name}: {stripped}")
                else:
                    # irc.py writes it; it must never print it.
                    if "bot_idents" in stripped:
                        self.assertNotIn("print(", stripped)


class ByNickMessage(Case):
    def test_a_known_bot_changing_nick_is_one_row_under_the_new_one(self):
        self.advertise("PackBot_", T0)
        self.assertTrue(irc.note_bot_renamed("PackBot_", "PackBot"))
        self.assertEqual(runtime.resolve_display_nick("PackBot_"), "PackBot")
        self.assertEqual(runtime.resolve_display_nick("PackBot"), "PackBot")

    def test_a_user_who_is_not_a_bot_is_left_alone(self):
        self.assertFalse(irc.note_bot_renamed("SomeUser", "SomeUser_away"))
        self.assertEqual(runtime.nick_aliases, {})

    def test_back_and_forth_never_aliases_a_nick_to_itself(self):
        self.advertise("PackBot", T0)
        irc.note_bot_renamed("PackBot", "PackBot_")
        self.advertise("PackBot_", T0 + 1)
        irc.note_bot_renamed("PackBot_", "PackBot")
        self.assertEqual(runtime.resolve_display_nick("PackBot"), "PackBot")
        self.assertEqual(runtime.resolve_display_nick("PackBot_"), "PackBot")

    def test_an_older_alias_follows_the_rename(self):
        self.advertise("PackBot", T0)
        runtime.nick_aliases["packbot__"] = "PackBot"
        irc.note_bot_renamed("PackBot", "PackBot_")
        self.assertEqual(runtime.resolve_display_nick("PackBot__"), "PackBot_")

    def test_the_new_nick_stops_being_an_alias_of_another_bot(self):
        """PackBot_ was once aliased to some other bot; now PackBot is
        PackBot_, and its row must not be filed under that other bot."""
        self.advertise("PackBot", T0)
        runtime.nick_aliases["packbot_"] = "OtherBot"
        irc.note_bot_renamed("PackBot", "PackBot_")
        self.assertEqual(runtime.resolve_display_nick("PackBot_"), "PackBot_")
        self.assertEqual(runtime.resolve_display_nick("PackBot"), "PackBot_")

    def test_a_held_list_counts_as_known(self):
        self.set_config(fetched_bot_lists={"quietbot": {"bot": "QuietBot"}})
        self.assertTrue(irc.note_bot_renamed("QuietBot", "QuietBot_"))


class TheWiring(unittest.TestCase):
    def read(self, *parts):
        with io.open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as handle:
            return handle.read()

    def test_every_advert_line_passes_its_ident(self):
        code = self.read("irc.py")
        at = code.index("_capture_channel_advert(user, target_chan, msg)\n")
        self.assertIn("_capture_bot_ident(user, user_host)", code[at:at + 200])

    def test_the_nick_handler_merges(self):
        code = self.read("irc.py")
        at = code.index("note_nick_change(nick_match.group(1),")
        self.assertIn("note_bot_renamed(nick_match.group(1),", code[at:at + 400])

    def test_the_summary_applies_it(self):
        code = self.read("webserver.py")
        self.assertIn("merges = _ident_merges(known, present)", code)
        self.assertEqual(code.count('"nick": _display_nick(bot, present, merges),'), 2)

    def test_the_page_opens_the_held_list_and_shows_the_bot_here(self):
        js = self.read("web", "app.js")
        start = js.index("function primaryEntry(group)")
        body = js[start:js.index("function otherNicks", start)]
        self.assertLess(body.index("group.entries[h].held"), body.index("return group.entries[0];"))
        self.assertIn("var online = groupOnline(group, primary);", js)
        self.assertIn('t("filelists.alsoSeenAs")', js)


if __name__ == "__main__":
    unittest.main()
