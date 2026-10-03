"""Recording an advert does not scan every channel member once per old entry.

_prune_known_bots() asked _bot_confirmed_absent() about each registry entry
older than a day, and that walked every nick in every channel - so one advert
cost (old entries) x (everybody present). Who is present is now worked out
once per pass, and only if an entry is old enough to need it. Counted, not
timed.
"""

import unittest

from tests.support import DCCoreTestCase

import defaults as config  # noqa: E402
import irc  # noqa: E402
import runtime  # noqa: E402

T0 = 1_000_000.0
DAY = 24 * 60 * 60


class CountingSet(set):
    """A channel's members; counts every one walked."""

    walked = 0

    def __iter__(self):
        for member in super().__iter__():
            CountingSet.walked += 1
            yield member


class OnePassOneLook(DCCoreTestCase):
    def setUp(self):
        super().setUp()
        runtime.known_bots.clear()
        self.addCleanup(runtime.known_bots.clear)
        CountingSet.walked = 0

    def test_many_old_entries_share_one_look_at_who_is_present(self):
        members = 2000
        config.channel_users["#big"] = CountingSet(f"member{n}" for n in range(members))
        for n in range(500):
            runtime.known_bots[f"gone{n}"] = {"nick": f"Gone{n}", "last_seen": T0 - DAY - 1}

        irc._prune_known_bots(T0)

        self.assertEqual(runtime.known_bots, {})
        self.assertLessEqual(CountingSet.walked, members, "one look per old entry is 500 times this")

    def test_nobody_is_looked_at_when_no_entry_is_old_enough_to_ask(self):
        config.channel_users["#big"] = CountingSet(f"member{n}" for n in range(100))
        for n in range(50):
            runtime.known_bots[f"fresh{n}"] = {"nick": f"Fresh{n}", "last_seen": T0 - 60}

        irc._prune_known_bots(T0)

        self.assertEqual(len(runtime.known_bots), 50)
        self.assertEqual(CountingSet.walked, 0)

    def test_the_answer_is_the_same_for_each_entry(self):
        config.channel_users["#chan"] = {"Herebot", "other"}
        runtime.known_bots["herebot"] = {"nick": "Herebot", "last_seen": T0 - DAY - 1}
        runtime.known_bots["gonebot"] = {"nick": "Gonebot", "last_seen": T0 - DAY - 1}

        irc._prune_known_bots(T0)

        self.assertEqual(list(runtime.known_bots), ["herebot"])


if __name__ == "__main__":
    unittest.main()
