"""#133's filter bar: one term against every list you hold, live as you type.

WHY THERE IS AN INDEX AT ALL

`list_fetch` re-parses a list fresh on every call - deliberately, since #76
removed unbounded retention - so searching every held list by re-reading them
is arithmetically out of reach rather than merely slow. #133 measured one
719k-file list at 2.0s and ten held lists at about 11s. No amount of
debouncing turns eleven seconds into typing.

WHY FTS5 AND NOT AN ORDINARY INDEXED TABLE

#133 proposed the latter and called it "milliseconds across millions of rows".
That is true of one of the two queries this feature needs and false of the
other, and the false half is the headline behaviour. Measured against
4,000,000 rows in ten lists - the sizes #133's own channel capture recorded:

    plain table, LIKE '%term%'   page  1-41ms     which-bots  1150-1400ms
    FTS5, per-bot LIMIT 1        page   1- 4ms    which-bots     2-  4ms

Paging is fast either way because LIMIT stops the scan early. "Which bots have
nothing", the question that greys the sidebar, must prove a negative for every
bot and has no such escape - unless it is asked per bot, where each stops at
its first hit.

WHAT IT COSTS

FTS5 tokenises where find_matching_entries() does substring, so a mid-word
fragment no longer matches. That is a real behavioural difference and it is
what buys the feature: substring matching over four million rows is the 1.4s
query above. @find over our own list is untouched.
"""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if os.path.join(REPO_ROOT, "tests") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import defaults as config  # noqa: E402
import list_index  # noqa: E402
import webserver  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


class IndexCase(DCCoreTestCase):
    """A throwaway index per test, and never the operator's own."""

    def setUp(self):
        super().setUp()
        self.index_dir = tempfile.mkdtemp(prefix="dccore-list-index-")
        self.set_config(LIST_INDEX_FILE=os.path.join(self.index_dir, "idx.db"))
        list_index.reset_for_tests()
        self.addCleanup(list_index.reset_for_tests)

    def index(self, bot, *filenames, folder="D:\\MUSIC\\Some Folder\\"):
        return list_index.index_bot_list(
            bot, [{"filename": name, "folder": folder, "size": "4.00MB"}
                  for name in filenames])

    def hold(self, *bots):
        self.set_config(fetched_bot_lists={
            bot.lower(): {"bot": bot, "fetched_at": 1, "entry_count": 1}
            for bot in bots})


class TheIndexAnswersTheQuestionsTheFilterAsks(IndexCase):

    def test_a_term_finds_a_file_in_a_held_list(self):
        self.index("BigTruck", "01 - Enter Sandman.flac")

        found = list_index.search(["sandman"])

        self.assertEqual([row["filename"] for row in found],
                         ["01 - Enter Sandman.flac"])

    def test_a_partial_word_finds_it_too(self):
        """The point of a filter bar rather than a search button: results
        appear before the word is finished."""
        self.index("BigTruck", "01 - Enter Sandman.flac")

        self.assertEqual(len(list_index.search(["sandm"])), 1)

    def test_every_word_must_match(self):
        """The same AND-across-words rule find_matching_entries() applies, so
        narrowing a term narrows the result rather than widening it."""
        self.index("BigTruck", "Enter Sandman.flac", "Sad But True.flac")

        self.assertEqual(len(list_index.search(["enter", "sandman"])), 1)
        self.assertEqual(len(list_index.search(["enter", "nirvana"])), 0)

    def test_a_mid_word_fragment_does_not_match(self):
        """Stated as a test because it is the one thing the index gives up.
        FTS5 tokenises; "andma" is not a token or a prefix of one. Substring
        matching over four million rows is the 1.4-second query this whole
        module exists to avoid, and @find over our own list still does it."""
        self.index("BigTruck", "01 - Enter Sandman.flac")

        self.assertEqual(list_index.search(["andma"]), [])

    def test_matching_ignores_case(self):
        self.index("BigTruck", "01 - Enter SANDMAN.flac")

        self.assertEqual(len(list_index.search(["sandman"])), 1)

    def test_an_empty_term_finds_nothing_rather_than_everything(self):
        """A cleared filter box must not page four million rows through the
        route on its way to showing the browse view again."""
        self.index("BigTruck", "01 - Enter Sandman.flac")

        self.assertEqual(list_index.search([]), [])
        self.assertEqual(list_index.search(["   "]), [])

    def test_a_query_full_of_fts_syntax_is_treated_as_text(self):
        """A term is whatever somebody typed into a box, and FTS5's expression
        language has operators in it. Unquoted, `NEAR` or a bare `-` is read
        as syntax and at best answers a different question; at worst it raises
        and the filter goes dead.

        Asserting the ANSWER, not the absence of an exception: search() is
        best-effort and swallows its own failures, which is right for
        production and means "did not raise" passes whether the term was
        quoted or not. A mutation removing the quoting proved exactly that."""
        self.index("BigTruck", "AC-DC Back In Black.flac", "Nirvana.flac")

        self.assertEqual([row["filename"] for row in list_index.search(["ac-dc"])],
                         ["AC-DC Back In Black.flac"])

        # None of these appear in either filename, so each must find nothing -
        # rather than being read as an operator joining what came before and
        # after it.
        for typed in ("NEAR", "OR", "AND", "NOT", '"', "^x"):
            with self.subTest(typed=typed):
                self.assertEqual(list_index.search([typed]), [],
                                 f"{typed!r} was read as FTS5 syntax")

    def test_an_empty_term_builds_no_query_at_all(self):
        """The property behind test_an_empty_term_finds_nothing: no query is
        constructed, so nothing downstream can be asked to match everything.
        Asserted here rather than through search(), which returns [] for a
        malformed query too and so cannot tell the two apart."""
        for nothing in ([], None, ["  "], ["", "\t"]):
            with self.subTest(terms=nothing):
                self.assertIsNone(list_index.build_match_query(nothing))

    def test_a_refetch_replaces_a_list_rather_than_doubling_it(self):
        self.index("BigTruck", "Old Track.flac")
        self.index("BigTruck", "New Track.flac")

        found = [row["filename"] for row in list_index.search(["track"])]

        self.assertEqual(found, ["New Track.flac"])

    def test_dropping_a_bot_forgets_its_list(self):
        self.index("BigTruck", "Only Track.flac")

        list_index.drop_bot("BigTruck")

        self.assertEqual(list_index.search(["track"]), [])
        self.assertEqual(list_index.indexed_bots(), set())

    def test_the_page_is_capped(self):
        self.index("BigTruck", *[f"Track {i}.flac" for i in range(50)])

        self.assertEqual(len(list_index.search(["track"], limit=10)), 10)

    def test_an_absurd_limit_is_clamped_rather_than_honoured(self):
        """A query parameter decides how many rows this materialises, so the
        ceiling has to be the server's rather than the caller's.

        Indexed past MAX_SEARCH_LIMIT deliberately: with five rows in the
        table every limit returns five, so the clamp is invisible and a
        mutation removing it passes."""
        over = list_index.MAX_SEARCH_LIMIT + 10
        self.index("BigTruck", *[f"Track {i}.flac" for i in range(over)])

        self.assertEqual(len(list_index.search(["track"], limit=10 ** 9)),
                         list_index.MAX_SEARCH_LIMIT)
        self.assertEqual(len(list_index.search(["track"], limit=-1)),
                         list_index.DEFAULT_SEARCH_LIMIT)
        self.assertEqual(len(list_index.search(["track"], limit="nonsense")),
                         list_index.DEFAULT_SEARCH_LIMIT)


class WhichBotsHaveNothing(IndexCase):
    """What greys the sidebar out.

    Asked per bot with LIMIT 1 rather than as one DISTINCT over every match -
    the difference between 2ms and 1.4s at four million rows, and therefore
    the difference between a filter bar and a search button.
    """

    def test_a_bot_with_a_match_is_matched(self):
        self.index("BigTruck", "Enter Sandman.flac")

        matched, empty = list_index.bots_with_a_match(["sandman"], ["BigTruck"])

        self.assertEqual(matched, {"bigtruck"})
        self.assertEqual(empty, set())

    def test_a_bot_with_no_match_is_empty(self):
        self.index("BigTruck", "Enter Sandman.flac")
        self.index("Vibessono", "Nevermind.flac")

        matched, empty = list_index.bots_with_a_match(
            ["sandman"], ["BigTruck", "Vibessono"])

        self.assertEqual(matched, {"bigtruck"})
        self.assertEqual(empty, {"vibessono"})

    def test_a_held_bot_that_was_never_indexed_is_empty_not_missing(self):
        """A list held from before the index existed, or one whose indexing
        failed. It has no matches to show, which is what the sidebar says -
        rather than the row disappearing from the answer entirely."""
        matched, empty = list_index.bots_with_a_match(["anything"], ["NewBot"])

        self.assertEqual(matched, set())
        self.assertEqual(empty, {"newbot"})

    def test_the_existence_question_stops_at_the_first_hit(self):
        """A performance property, and the one the whole feature rests on, so
        it is asserted even though nothing about the ANSWER changes without
        it - fetchone() takes one row either way. Read out of the source
        because that is the only place the difference is visible.

        Without LIMIT 1 this enumerates every match for every bot on every
        keystroke: measured at 1150-1400ms across four million rows, against
        2-4ms with it. That is the difference between a filter bar and a
        search button."""
        with open(os.path.join(REPO_ROOT, "list_index.py"),
                  encoding="utf-8") as handle:
            code = "\n".join(line.split("#", 1)[0]
                              for line in handle.read().splitlines())
        block = code.split("def bots_with_a_match(", 1)[1].split("\ndef ", 1)[0]

        # The STATEMENT, not the words. Stripping "#" comments leaves
        # docstrings behind, and that function's own docstring says "asked
        # once per bot with LIMIT 1" - so a bare search for "LIMIT 1" matched
        # the explanation of the code rather than the code, and passed with
        # the clause deleted.
        self.assertIn("MATCH ? LIMIT 1", block,
                      "the existence query enumerates every match instead of "
                      "stopping at the first")

    def test_no_term_greys_nobody(self):
        self.index("BigTruck", "Enter Sandman.flac")

        matched, empty = list_index.bots_with_a_match([], ["BigTruck"])

        self.assertEqual((matched, empty), (set(), set()))


class TheIndexIsNotTheRecordOfWhatIsHeld(IndexCase):
    """`fetched_bot_lists` is. The two can drift.

    A list file removed by hand, a reset store, an entry that went away while
    its rows stayed - and a row for a list we no longer hold offers a file
    that cannot be requested, which is worse than not finding it.
    """

    def test_a_search_is_scoped_to_the_lists_held(self):
        self.index("BigTruck", "Enter Sandman.flac")
        self.index("GoneBot", "Enter Sandman.flac")

        found = list_index.search(["sandman"], bots=["BigTruck"])

        self.assertEqual([row["bot"] for row in found], ["BigTruck"])

    def test_holding_nothing_finds_nothing_however_full_the_index(self):
        self.index("GoneBot", "Enter Sandman.flac")

        self.assertEqual(list_index.search(["sandman"], bots=[]), [])

    def test_the_route_only_ever_offers_held_lists(self):
        self.index("BigTruck", "Enter Sandman.flac")
        self.index("GoneBot", "Enter Sandman.flac")
        self.hold("BigTruck")

        payload = webserver.build_crosslist_search_payload("sandman")

        self.assertEqual([group["bot"] for group in payload["folders"]],
                         ["BigTruck"])


class ThePayloadTheBrowserRenders(IndexCase):
    """The same shape GET /api/filelists returns.

    Deliberately: the browser's folder rendering, its checkboxes and its
    "Download selected" already work on that shape, and each row's "source" is
    its bot - so selecting across several lists at once needed no new
    plumbing at all.
    """

    def test_it_returns_folder_groups_like_the_browse_view(self):
        self.index("BigTruck", "Enter Sandman.flac")
        self.hold("BigTruck")

        payload = webserver.build_crosslist_search_payload("sandman")

        self.assertEqual(payload["total"], 1)
        group = payload["folders"][0]
        self.assertEqual(group["count"], 1)
        self.assertEqual(group["entries"][0]["title"], "Enter Sandman.flac")

    def test_every_row_carries_the_bot_it_came_from(self):
        """What makes cross-list selection work: the checkbox reads its bot
        from here, so a page of results from four bots queues correctly."""
        self.index("BigTruck", "Enter Sandman.flac")
        self.index("Vibessono", "Sandman Live.flac")
        self.hold("BigTruck", "Vibessono")

        payload = webserver.build_crosslist_search_payload("sandman")
        sources = {entry["source"]
                   for group in payload["folders"] for entry in group["entries"]}

        self.assertEqual(sources, {"BigTruck", "Vibessono"})

    def test_two_bots_sharing_a_folder_path_stay_apart(self):
        """list.group_rows_by_folder() keys on the folder string, which is
        right for one list and wrong across several: two bots can both have
        "D:\\MUSIC\\Metallica\\". Merged, one bot's files would sit under the
        other's heading and the folder's !rar button would point at whichever
        came first."""
        shared = "D:\\MUSIC\\Metallica\\"
        self.index("BigTruck", "Enter Sandman.flac", folder=shared)
        self.index("Vibessono", "Sandman Live.flac", folder=shared)
        self.hold("BigTruck", "Vibessono")

        payload = webserver.build_crosslist_search_payload("sandman")

        self.assertEqual(len(payload["folders"]), 2)
        self.assertEqual({group["bot"] for group in payload["folders"]},
                         {"BigTruck", "Vibessono"})

    def test_the_bots_with_nothing_are_named_for_the_sidebar(self):
        self.index("BigTruck", "Enter Sandman.flac")
        self.hold("BigTruck", "Vibessono", "MetalHead")

        payload = webserver.build_crosslist_search_payload("sandman")

        self.assertEqual(payload["matched"], ["bigtruck"])
        self.assertEqual(payload["empty"], ["metalhead", "vibessono"])

    def test_a_bot_is_not_called_empty_just_because_the_page_filled_up(self):
        """The reason matched/empty is asked separately rather than derived
        from the rows. The page is capped, so a bot whose matches all fall
        past the cap would look empty when it is not - and the sidebar would
        cross out a list that has exactly what the operator is looking for."""
        self.index("BigTruck", *[f"Track {i}.flac" for i in range(30)])
        self.index("Vibessono", "Track Rare.flac")
        self.hold("BigTruck", "Vibessono")

        payload = webserver.build_crosslist_search_payload("track", limit=5)

        self.assertEqual(payload["empty"], [])
        self.assertEqual(sorted(payload["matched"]), ["bigtruck", "vibessono"])

    def test_it_says_when_it_had_to_stop_early(self):
        self.index("BigTruck", *[f"Track {i}.flac" for i in range(30)])
        self.hold("BigTruck")

        self.assertTrue(
            webserver.build_crosslist_search_payload("track", limit=5)["truncated"])
        self.assertFalse(
            webserver.build_crosslist_search_payload("track", limit=100)["truncated"])

    def test_an_empty_term_greys_nobody_and_returns_nothing(self):
        """A cleared box goes back to browsing; it must not cross out every
        bot on the way."""
        self.index("BigTruck", "Enter Sandman.flac")
        self.hold("BigTruck", "Vibessono")

        payload = webserver.build_crosslist_search_payload("")

        self.assertEqual(payload["folders"], [])
        self.assertEqual(payload["matched"], [])
        self.assertEqual(payload["empty"], ["bigtruck", "vibessono"])


class WhenTheIndexIsNotThere(IndexCase):
    """Best-effort throughout. A missing or unusable index costs the filter
    bar and nothing else: the lists are on disk, the browser still pages them,
    @find still works, and the next fetch rebuilds it."""

    def test_searching_without_one_returns_nothing_rather_than_raising(self):
        self.set_config(LIST_INDEX_FILE=os.path.join(
            self.index_dir, "no-such-dir", "nested", "idx.db"))
        list_index.reset_for_tests()

        self.assertEqual(list_index.search(["anything"]), [])

    def test_a_corrupt_database_costs_the_filter_and_nothing_else(self):
        path = os.path.join(self.index_dir, "corrupt.db")
        with open(path, "wb") as handle:
            handle.write(b"this is not a database, it is a text file")
        self.set_config(LIST_INDEX_FILE=path)
        list_index.reset_for_tests()

        self.assertEqual(list_index.search(["anything"]), [])
        self.assertEqual(list_index.index_bot_list("BigTruck", []), 0)

    def test_no_index_does_not_grey_out_every_bot(self):
        """"We cannot tell" is not "nobody has it". Crossing out the whole
        sidebar would read as a definite answer, and the wrong one."""
        path = os.path.join(self.index_dir, "corrupt2.db")
        with open(path, "wb") as handle:
            handle.write(b"nope")
        self.set_config(LIST_INDEX_FILE=path)
        list_index.reset_for_tests()

        matched, empty = list_index.bots_with_a_match(["x"], ["BigTruck"])

        self.assertEqual((matched, empty), (set(), set()))


class SwitchingOneListOffWhileFiltering(unittest.TestCase):
    """#133: "clicking a bot toggles its results in or out; select-all and
    select-none for the sidebar".

    Read out of app.js - nothing in this project executes JavaScript. The
    property worth pinning is that toggling is done over the answer already
    held rather than by asking again: #133 calls it trivial precisely because
    the rows are already in the browser, and a round trip per click would be
    slower than the search that fetched them.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO_ROOT, "web", "app.js"),
                  encoding="utf-8") as handle:
            cls.js = handle.read()

    def block(self, name):
        start = self.js.index("function " + name + "(")
        depth = 0
        for i in range(self.js.index("{", start), len(self.js)):
            if self.js[i] == "{":
                depth += 1
            elif self.js[i] == "}":
                depth -= 1
                if depth == 0:
                    return self.js[start:i + 1]
        raise AssertionError("unbalanced braces in " + name)

    def test_toggling_re_renders_rather_than_re_fetching(self):
        body = self.block("rerenderFromFilterPayload")

        self.assertIn("state.filelistsFilterPayload", body)
        self.assertNotIn("fetchJson", body)
        self.assertNotIn("loadFilelists", body)

    def test_a_switched_off_bot_is_dropped_from_what_is_shown(self):
        body = self.block("visibleFilterGroups")

        self.assertIn("state.filelistsExcluded", body)

    def test_nothing_is_hidden_when_no_term_is_set(self):
        """Browsing one list, the sidebar picks the source and a switched-off
        bot has no meaning. The exclusions must not leak into that view."""
        body = self.block("visibleFilterGroups")

        self.assertIn("if (!(state.filelistsFilter || \"\").trim()) { return groups; }",
                      body)

    def test_a_new_term_shows_every_list_again(self):
        """A new term is a new question. A bot switched off while looking for
        one thing must not stay switched off, invisibly, for the next."""
        body = self.block("runFilelistsFilter")

        self.assertIn("state.filelistsExcluded = {}", body)

    def test_show_none_switches_off_only_the_lists_that_matched(self):
        """Not every bot in the sidebar: one with no matches is already
        saying so, and marking it switched off as well would claim the
        operator made a choice they did not make."""
        body = self.block("setEveryListShown")

        self.assertIn("payload.matched", body)

    def test_all_of_them_switched_off_says_so(self):
        """Distinct from "nothing matches". One is an answer about the
        library, the other is a thing the operator did and can undo."""
        body = self.block("renderFilelistGroups")

        self.assertIn("Every list with a match is switched off.", body)
        self.assertIn("Nothing in any list you hold matches that.", body)


class MarkingWhatYouAlreadyAskedFor(IndexCase):
    """#133: "mark what you already requested", and it wants two states.

    requested  still in flight. dcc_fetch groups exactly these as
               _UNRESOLVED_FETCH_STATES, and reusing that tuple is what stops
               this drifting the day a state is added there.
    received   complete.
    nothing    FAILED, deliberately. A failure is not a thing you have, and
               marking it would discourage the one useful action left.
    """

    def queue(self, **rows):
        self.set_config(fetch_queue={
            key: dict(row) for key, row in rows.items()})

    def marks_for(self, term="flac"):
        payload = webserver.build_crosslist_search_payload(term)
        return {entry["title"]: entry["mark"]
                for group in payload["folders"] for entry in group["entries"]}

    def setUp(self):
        super().setUp()
        self.index("BigTruck", "Asked.flac", "Have.flac", "Failed.flac",
                   "Untouched.flac")
        self.hold("BigTruck")

    def test_an_in_flight_request_reads_as_asked(self):
        for state in ("pending", "offered", "listening", "receiving"):
            with self.subTest(state=state):
                self.queue(a={"bot": "BigTruck", "requested_filename": "Asked.flac",
                              "request_type": "file", "state": state})
                self.assertEqual(self.marks_for()["Asked.flac"], "requested")

    def test_every_unresolved_state_dcc_fetch_knows_is_covered(self):
        """Derived from dcc_fetch's own tuple rather than listed again here:
        a state added there must not silently stop being marked."""
        import dcc_fetch

        for state in dcc_fetch._UNRESOLVED_FETCH_STATES:
            with self.subTest(state=state):
                self.queue(a={"bot": "BigTruck", "requested_filename": "Asked.flac",
                              "request_type": "file", "state": state})
                self.assertEqual(self.marks_for()["Asked.flac"], "requested")

    def test_a_completed_one_reads_as_received(self):
        self.queue(a={"bot": "BigTruck", "requested_filename": "Have.flac",
                      "request_type": "file", "state": "complete"})

        self.assertEqual(self.marks_for()["Have.flac"], "received")

    def test_a_failed_one_is_not_marked_at_all(self):
        """The useful action is to ask again, and a mark would discourage
        it."""
        self.queue(a={"bot": "BigTruck", "requested_filename": "Failed.flac",
                      "request_type": "file", "state": "failed"})

        self.assertEqual(self.marks_for()["Failed.flac"], "")

    def test_a_file_never_asked_for_is_not_marked(self):
        self.queue()

        self.assertEqual(self.marks_for()["Untouched.flac"], "")

    def test_having_it_wins_over_having_asked(self):
        """Asked twice, arrived once. "You have this" is the more useful of
        the two answers."""
        # The COMPLETED one first, so that "last row wins" would give the
        # wrong answer. Ordered the other way this passed with the guard
        # deleted - the completed row happened to be seen last and won by
        # accident rather than by rule.
        self.queue(
            a={"bot": "BigTruck", "requested_filename": "Have.flac",
               "request_type": "file", "state": "complete"},
            b={"bot": "BigTruck", "requested_filename": "Have.flac",
               "request_type": "file", "state": "receiving"})

        self.assertEqual(self.marks_for()["Have.flac"], "received")

    def test_a_mark_belongs_to_one_bot_only(self):
        """Two bots can hold a file of the same name. Asking one for it says
        nothing about the other, and marking both would claim a request that
        was never made."""
        self.index("Vibessono", "Asked.flac")
        self.hold("BigTruck", "Vibessono")
        self.queue(a={"bot": "BigTruck", "requested_filename": "Asked.flac",
                      "request_type": "file", "state": "receiving"})

        payload = webserver.build_crosslist_search_payload("asked")
        marks = {(group["bot"], entry["title"]): entry["mark"]
                 for group in payload["folders"] for entry in group["entries"]}

        self.assertEqual(marks[("BigTruck", "Asked.flac")], "requested")
        self.assertEqual(marks[("Vibessono", "Asked.flac")], "")

    def test_a_whole_list_request_does_not_mark_a_file(self):
        """A "list" row asks for the bot's list, not for any row in the
        table. Marking a filename from one would claim something that never
        happened - and the filename field of such a row is empty anyway."""
        self.queue(a={"bot": "BigTruck", "requested_filename": "Asked.flac",
                      "request_type": "list", "state": "receiving"})

        self.assertEqual(self.marks_for()["Asked.flac"], "")

    def test_a_folder_request_does_not_mark_a_file_either(self):
        self.queue(a={"bot": "BigTruck", "requested_filename": "Asked.flac",
                      "request_type": "folder", "state": "receiving"})

        self.assertEqual(self.marks_for()["Asked.flac"], "")

    def test_the_match_ignores_case(self):
        """The list writes whatever that bot wrote; the request carries
        whatever was clicked. They agree in practice and must not depend on
        it."""
        self.queue(a={"bot": "bigtruck", "requested_filename": "ASKED.FLAC",
                      "request_type": "file", "state": "receiving"})

        self.assertEqual(self.marks_for()["Asked.flac"], "requested")


class TheRowShapeIsOne(unittest.TestCase):
    """Our own list and a fetched one go through list.entries_to_filelist_rows()
    precisely so the frontend sees one row shape. A key present in one and
    absent in the other is how that stops being true."""

    def test_mark_is_part_of_the_shape_rather_than_added_by_a_payload(self):
        import list as list_mod

        rows = list_mod.entries_to_filelist_rows(
            [{"filename": "A.flac", "size": "1MB", "folder": "F"}], "Someone")

        self.assertIn("mark", rows[0])
        self.assertEqual(rows[0]["mark"], "",
                         "our own list is never something we requested")


class TheIndexIsWrittenByTheFetch(unittest.TestCase):
    """Read out of the source: driving a real fetch needs a socket, a zip and
    a peer, and the one thing that matters is that it happens in the parse
    that was already walking the file."""

    def source(self):
        with open(os.path.join(REPO_ROOT, "list_fetch.py"),
                  encoding="utf-8") as handle:
            return handle.read()

    def test_the_fetch_indexes_the_rows_it_already_parsed(self):
        code = "\n".join(line.split("#", 1)[0]
                         for line in self.source().splitlines())

        self.assertIn("list_index.index_bot_list(", code,
                      "a fetched list never reaches the search index, so the "
                      "cross-list filter can never see it")

    def test_it_reuses_the_parse_rather_than_walking_the_file_again(self):
        """The rows were parsed only to be counted and thrown away. Indexing
        them costs a pass that was already happening; a second walk of a
        719k-line file would not be."""
        code = self.source()
        block = code.split("rows = list_mod.entries_to_filelist_rows(", 1)[1]
        block = block.split("store = _ensure_fetched_bot_lists()", 1)[0]

        self.assertIn("list_index.index_bot_list(", block)
        self.assertNotIn("find_matching_entries(", block,
                         "the index is built from a second parse of its own")


if __name__ == "__main__":
    unittest.main()
