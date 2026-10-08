"""#1240: AUTO_DISCOVER_CHANNEL_LISTS watches bots we already hold a list
from for a SECOND, genuinely different list bound to another of our
channels, and fetches it automatically - but only once the difference has
held steady for MULTI_CHANNEL_LIST_STABLE_SECONDS on both sides, never off a
single advert (a bot mid-scan in one channel must not be mistaken for a
second list).
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import defaults as config  # noqa: E402
import list_grab  # noqa: E402
import runtime  # noqa: E402
import webserver  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402

NOW = 1_000_000.0
STABLE = 3600


def channels(chans):
    """{channel: {"files": N, "since": T}, ...} - the shape known_bots[...]
    ["channels"] holds (irc._record_channel_signature()'s own output), keys
    lowercased the way that function stores them."""
    return {str(chan).lower(): dict(info) for chan, info in chans.items()}


class Case(DCCoreTestCase):
    def setUp(self):
        super().setUp()
        self.set_config(AUTO_DISCOVER_CHANNEL_LISTS=True,
                        MULTI_CHANNEL_LIST_STABLE_SECONDS=STABLE,
                        fetched_bot_lists={})
        runtime.secondary_channel_tries = {}
        runtime.secondary_channel_last = None
        self.asked = []
        self.answer = (200, {"created": ["rid"]})

        def fake_enqueue(bot, channel):
            self.asked.append((bot, channel))
            return self.answer

        real = webserver.build_list_fetch_enqueue_result
        webserver.build_list_fetch_enqueue_result = fake_enqueue
        self.addCleanup(setattr, webserver, "build_list_fetch_enqueue_result", real)

    def hold(self, bot, channel, **lists_extra):
        key = bot.lower()
        config.fetched_bot_lists[key] = {
            "bot": bot, "channel": channel,
            "lists": {"": {"list_path": "x", "entry_count": 1, "channel": channel}, **lists_extra},
        }

    def register(self, bot, chans):
        runtime.known_bots[bot.lower()] = {"nick": bot, "channels": channels(chans)}


class Candidates(Case):

    def test_nothing_without_a_held_list(self):
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        self.assertEqual(list_grab._secondary_channel_candidates(NOW), [])

    def test_a_genuine_stable_difference_is_found(self):
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        found = list_grab._secondary_channel_candidates(NOW)
        self.assertEqual(found, [("somebot", "SomeBot", "#chan_b")])

    def test_the_same_file_count_everywhere_is_not_a_candidate(self):
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 100, "since": NOW - STABLE * 2}})
        self.assertEqual(list_grab._secondary_channel_candidates(NOW), [])

    def test_a_difference_not_yet_stable_on_the_new_channel_is_not_a_candidate(self):
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 200, "since": NOW - 10}})
        self.assertEqual(list_grab._secondary_channel_candidates(NOW), [])

    def test_a_held_channel_itself_mid_change_is_not_compared_against(self):
        """The channel we already hold a list from is ALSO mid-scan (its own
        signature just changed) - comparing anything against a moving target
        is as wrong as comparing a moving target against us."""
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 150, "since": NOW - 10}, "#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        self.assertEqual(list_grab._secondary_channel_candidates(NOW), [])

    def test_a_channel_already_held_as_a_marker_is_not_offered_again(self):
        self.hold("SomeBot", "#chan_a",
                 video={"list_path": "y", "entry_count": 1, "channel": "#chan_b"})
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        self.assertEqual(list_grab._secondary_channel_candidates(NOW), [])

    def test_only_one_channel_is_never_a_candidate(self):
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}})
        self.assertEqual(list_grab._secondary_channel_candidates(NOW), [])

    def test_with_no_signature_for_the_held_channel_a_real_looking_count_still_qualifies(self):
        """The bot has never advertised in the channel we actually hold its
        list from (fetched by hand, say) - nothing to compare against, so a
        stable, concrete file count elsewhere is still worth a look."""
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        found = list_grab._secondary_channel_candidates(NOW)
        self.assertEqual(found, [("somebot", "SomeBot", "#chan_b")])

    def test_a_cooled_down_pair_is_skipped(self):
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        runtime.secondary_channel_tries = {"somebot:#chan_b": {"tries": 1, "last": NOW - 60}}
        self.assertEqual(list_grab._secondary_channel_candidates(NOW), [])

    def test_a_pair_that_gave_up_is_skipped(self):
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        runtime.secondary_channel_tries = {
            "somebot:#chan_b": {"tries": list_grab.SECONDARY_CHANNEL_TRIES, "last": NOW - 10 ** 6}}
        self.assertEqual(list_grab._secondary_channel_candidates(NOW), [])


class RefreshingAnAlreadyDiscoveredChannel(Case):
    """Not just discovery once - an already-held secondary marker is offered
    again when ITS OWN channel has genuinely moved on since it was fetched,
    the ongoing upkeep #1240 promises (no manual re-fetch, ever)."""

    def hold_with_video(self, signature_when_fetched):
        self.hold("SomeBot", "#chan_a",
                 video={"list_path": "y", "entry_count": 1, "channel": "#chan_b",
                        "advert_signature": dict(signature_when_fetched)})

    def test_unchanged_since_the_marker_was_fetched_is_not_offered_again(self):
        sig = {"files": 200, "since": NOW - STABLE * 3}
        self.hold_with_video(sig)
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2},
                                  "#chan_b": dict(sig)})
        self.assertEqual(list_grab._secondary_channel_candidates(NOW), [])

    def test_a_stable_change_since_the_marker_was_fetched_is_offered_again(self):
        self.hold_with_video({"files": 200, "since": NOW - STABLE * 10})
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2},
                                  "#chan_b": {"files": 350, "since": NOW - STABLE * 2}})
        found = list_grab._secondary_channel_candidates(NOW)
        self.assertEqual(found, [("somebot", "SomeBot", "#chan_b")])

    def test_a_change_not_yet_stable_is_not_offered_again_yet(self):
        self.hold_with_video({"files": 200, "since": NOW - STABLE * 10})
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2},
                                  "#chan_b": {"files": 350, "since": NOW - 10}})
        self.assertEqual(list_grab._secondary_channel_candidates(NOW), [])

    def test_a_successful_refetch_resets_its_own_tries(self):
        list_grab.note_secondary_channel_list_arrived("SomeBot", "#chan_b")  # no-op, nothing to reset
        runtime.secondary_channel_tries = {"somebot:#chan_b": {"tries": 3, "last": NOW}}
        list_grab.note_secondary_channel_list_arrived("SomeBot", "#chan_b")
        self.assertNotIn("somebot:#chan_b", runtime.secondary_channel_tries)


class Tick(Case):

    def test_off_does_nothing(self):
        self.set_config(AUTO_DISCOVER_CHANNEL_LISTS=False)
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        self.assertEqual(list_grab.secondary_channel_tick(NOW), "off")
        self.assertEqual(self.asked, [])

    def test_a_confirmed_candidate_is_asked_for(self):
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        self.assertEqual(list_grab.secondary_channel_tick(NOW), "asked")
        self.assertEqual(self.asked, [("SomeBot", "#chan_b")])

    def test_a_refusal_is_not_asked_again_the_same_tick(self):
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        self.answer = (409, {"error": "busy"})
        self.assertEqual(list_grab.secondary_channel_tick(NOW), "refused")

    def test_nothing_to_find_says_so(self):
        self.assertEqual(list_grab.secondary_channel_tick(NOW), "nothing")

    def test_a_second_tick_too_soon_waits(self):
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        list_grab.secondary_channel_tick(NOW)
        self.assertEqual(list_grab.secondary_channel_tick(NOW + 1), "waiting")

    def test_three_tries_then_it_gives_up(self):
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        self.answer = (409, {"error": "busy"})
        t = NOW
        for _ in range(list_grab.SECONDARY_CHANNEL_TRIES):
            self.assertEqual(list_grab.secondary_channel_tick(t), "refused")
            t += list_grab.SECONDARY_CHANNEL_COOLDOWN_SECONDS + 1
        self.assertEqual(list_grab.secondary_channel_tick(t), "nothing")

    def test_tries_survive_a_restart(self):
        self.hold("SomeBot", "#chan_a")
        self.register("SomeBot", {"#chan_a": {"files": 100, "since": NOW - STABLE * 2}, "#chan_b": {"files": 200, "since": NOW - STABLE * 2}})
        list_grab.secondary_channel_tick(NOW)
        runtime.secondary_channel_tries = None  # simulate a fresh process
        self.assertEqual(list_grab._secondary_channel_state()["somebot:#chan_b"]["tries"], 1)


class TheWiring(unittest.TestCase):

    def setUp(self):
        self.real_flag = config.AUTO_DISCOVER_CHANNEL_LISTS
        self.real_started = runtime.secondary_channel_started
        self.addCleanup(setattr, config, "AUTO_DISCOVER_CHANNEL_LISTS", self.real_flag)
        self.addCleanup(setattr, runtime, "secondary_channel_started", self.real_started)

    def test_ensure_worker_is_off_by_default(self):
        config.AUTO_DISCOVER_CHANNEL_LISTS = False
        runtime.secondary_channel_started = False
        self.assertFalse(list_grab.ensure_secondary_channel_worker())

    def test_ensure_worker_starts_once(self):
        config.AUTO_DISCOVER_CHANNEL_LISTS = True
        runtime.secondary_channel_started = False
        started = []
        self.assertTrue(list_grab.ensure_secondary_channel_worker(start=lambda: started.append(1)))
        self.assertFalse(list_grab.ensure_secondary_channel_worker(start=lambda: started.append(1)))
        self.assertEqual(started, [1])

    def test_the_loop_survives_a_tick_that_raises(self):
        real = list_grab.secondary_channel_tick

        def explode(*_a, **_k):
            raise RuntimeError("boom")

        list_grab.secondary_channel_tick = explode
        self.addCleanup(setattr, list_grab, "secondary_channel_tick", real)

        class Enough(Exception):
            pass

        def stop(_seconds):
            raise Enough()

        with self.assertRaises(Enough):
            list_grab.secondary_channel_worker(sleep=stop)

    def test_boot_and_rehash_start_it(self):
        with open(os.path.join(REPO_ROOT, "oserve.py"), encoding="utf-8") as handle:
            self.assertIn("ensure_secondary_channel_worker()", handle.read())
        with open(os.path.join(REPO_ROOT, "src", "commands.py"), encoding="utf-8") as handle:
            self.assertIn("if _list_grab_secondary.ensure_secondary_channel_worker():", handle.read())


if __name__ == "__main__":
    unittest.main()
