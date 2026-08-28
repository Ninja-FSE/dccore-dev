"""webserver.py's build_*_payload() functions.

Deliberately exercises only the pure, Flask-free half of webserver.py:
build_queue_payload(), build_search_payload() and build_filelists_payload()
never import flask, which is what lets this file run - like the rest of the
suite - with nothing but the standard library. create_app()/start() are the
Flask-gated half and are only smoke-tested here for the "Flask is missing"
and "disabled via config" paths, which must never raise regardless of whether
Flask happens to be installed in the environment running this file.
"""

import io
import os
import sys
import time
import unittest
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if os.path.join(REPO_ROOT, "tests") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import config  # noqa: E402
import list as list_mod  # noqa: E402
import webserver  # noqa: E402

from tests.support import DCCoreTestCase, queue_row  # noqa: E402


def write_master_list(lists_dir, base_name, folders):
    """A minimal master list in update_list.py's exact on-disk shape.

    `folders` is [(folder_path_or_None, [(filename, size_str), ...]), ...],
    mirroring the "====...====" / folder / "====...====" header block
    update_list.py writes before each folder's "!..." lines
    (update_list.py:190-195, :216).
    """
    path = os.path.join(lists_dir, f"{base_name}-2026-08-25.txt")
    rule = "=" * 53
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("List of 2 Files (1.0MB) generated on Aug 25th\n")
        f.write(f"To request a file, copy/paste to the channel... !{base_name} FILENAME\n\n\n")
        for folder, files in folders:
            if folder:
                f.write(f"\n{rule}\n{folder}\n{rule}\n")
            for filename, size in files:
                f.write(f"!{base_name} {filename}  ::INFO:: {size}\n")
    return path


class QueuePayloadTests(DCCoreTestCase):

    def test_summary_rows_reflect_sending_frozen_and_queued(self):
        config.dcc_queue = {
            "alice": [queue_row(user="alice", filename="A.flac")],
            "bob":   [queue_row(user="bob", filename="B.flac"), queue_row(user="bob", filename="C.flac")],
            "carol": [queue_row(user="carol", filename="D.flac")],
        }
        config.frozen_queues = {"bob": 12345.0}
        config.active_transfers = [{"user": "carol", "file": "D.flac"}]

        rows = {row["user"]: row for row in webserver.build_queue_payload()}

        self.assertEqual(rows["alice"]["status"], "queued")
        self.assertEqual(rows["alice"]["preview"], "A.flac")
        self.assertEqual(rows["alice"]["count"], 1)

        self.assertEqual(rows["bob"]["status"], "frozen")
        self.assertEqual(rows["bob"]["count"], 2)

        self.assertEqual(rows["carol"]["status"], "sending")

    def test_sending_status_wins_over_frozen(self):
        """A user can be frozen and still be the one currently sending (the
        freeze applies to future dispatch, not an in-flight transfer)."""
        config.dcc_queue = {"dave": [queue_row(user="dave")]}
        config.frozen_queues = {"dave": 1.0}
        config.active_transfers = [{"user": "dave", "file": "Song.flac"}]

        rows = webserver.build_queue_payload()
        self.assertEqual(rows[0]["status"], "sending")

    def test_empty_queue_is_an_empty_list(self):
        self.assertEqual(webserver.build_queue_payload(), [])

    def test_user_param_returns_that_users_full_file_list(self):
        config.dcc_queue = {
            "dave": [queue_row(user="dave", filename="One.flac"),
                     queue_row(user="dave", filename="Two.flac")],
        }
        result = webserver.build_queue_payload(user="Dave")
        self.assertEqual(result["user"], "dave")
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["files"], ["One.flac", "Two.flac"])

    def test_user_param_for_an_unknown_user_is_empty_not_an_error(self):
        result = webserver.build_queue_payload(user="nobody")
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["files"], [])

    def test_user_param_is_case_and_whitespace_insensitive(self):
        config.dcc_queue = {"dave": [queue_row(user="dave", filename="One.flac")]}
        result = webserver.build_queue_payload(user="  DAVE  ")
        self.assertEqual(result["user"], "dave")
        self.assertEqual(result["count"], 1)


def payload_rows(payload):
    """Flat rows out of a folder-grouped file-list payload.

    Both file-list endpoints page by FOLDER now and return
    {"folders": [{"folder", "count", "entries"}, ...]}. Most tests here are
    asserting things about rows - what a row contains, whether a file is
    present - and do not care how the page was cut, so they read through this
    rather than each learning the grouped shape.

    Tests that are about the PAGING ITSELF use payload["folders"] directly:
    for those, the grouping is the thing under test.
    """
    return [row for group in payload["folders"] for row in group["entries"]]


class SearchAndFilelistsPayloadTests(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()
        config.LOCAL_LIST_DIR = self.tree.lists
        config.LIST_BASE_NAME = "DCCore"
        config.NICKNAME = "DCCore"
        config.CHANNEL = "#mp3passion, #mp3servers"
        write_master_list(self.tree.lists, "DCCore", [
            ("D:\\MUSIC\\Metallica\\Black Album (1991)\\", [
                ("01 - Enter Sandman.flac", "42.31MB"),
                ("02 - Sad But True.flac", "39.02MB"),
                ("00 - Intro.flac", "0.50MB"),
            ]),
            ("D:\\MUSIC\\Metallica\\Reload (1997)\\", [
                ("01 - Fuel.flac", "38.50MB"),
                # Same name+size as one already listed under a different
                # folder - the filelists dedup is expected to collapse this.
                ("00 - Intro.flac", "0.50MB"),
            ]),
        ])

    def test_search_matches_carry_folder_and_size(self):
        rows = webserver.build_search_payload("sandman")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "01 - Enter Sandman.flac")
        self.assertEqual(rows[0]["path"], "D:\\MUSIC\\Metallica\\Black Album (1991)\\")
        self.assertEqual(rows[0]["size"], "42.31MB")

    def test_search_channel_is_the_full_joined_channel_list(self):
        rows = webserver.build_search_payload("fuel")
        self.assertEqual(rows[0]["channel"], "#mp3passion, #mp3servers")

    def test_search_is_case_insensitive_and_matches_all_words(self):
        self.assertEqual(len(webserver.build_search_payload("SANDMAN")), 1)
        self.assertEqual(len(webserver.build_search_payload("enter sandman")), 1)
        self.assertEqual(len(webserver.build_search_payload("enter fuel")), 0)

    def test_blank_query_returns_no_results(self):
        self.assertEqual(webserver.build_search_payload(""), [])
        self.assertEqual(webserver.build_search_payload("   "), [])
        self.assertEqual(webserver.build_search_payload("---"), [])

    def test_search_respects_its_own_higher_limit_than_irc(self):
        """WEBUI_MAX_SEARCH_RESULTS (50) is not config.MAX_SEARCH_RESULTS (5,
        sized for a channel) - a browser search for a common word must not be
        clipped to five results the way @find deliberately is."""
        write_master_list(self.tree.lists, "DCCore", [
            (None, [(f"Track {i:02d}.flac", "1.00MB") for i in range(10)]),
        ])
        rows = webserver.build_search_payload("track")
        self.assertEqual(len(rows), 10)

    def test_filelists_covers_every_file_and_dedupes_same_name_and_size(self):
        payload = webserver.build_filelists_payload()
        titles = sorted(r["title"] for r in payload_rows(payload))
        # "00 - Intro.flac" is listed under BOTH albums in the fixture, and
        # appears twice now. It used to collapse to one row, because the dedup
        # key was (filename, size) with no folder in it - invisible while rows
        # were a flat list, wrong once they are grouped: the second album
        # would show as missing a track it actually has.
        self.assertEqual(titles, [
            "00 - Intro.flac", "00 - Intro.flac", "01 - Enter Sandman.flac",
            "01 - Fuel.flac", "02 - Sad But True.flac",
        ])
        # `total` counts FOLDERS, the unit of a page. total_files counts rows.
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["total_files"], 5)

    def test_filelists_rows_have_format_and_source(self):
        rows = {r["title"]: r for r in payload_rows(webserver.build_filelists_payload())}
        fuel = rows["01 - Fuel.flac"]
        self.assertEqual(fuel["format"], "FLAC")
        self.assertEqual(fuel["source"], "DCCore")
        self.assertEqual(fuel["size"], "38.50MB")

    def test_execute_search_irc_behaviour_is_unchanged_by_the_refactor(self):
        """The refactor moved execute_search()'s matching loop into
        find_matching_entries() - this proves the IRC-facing result (what gets
        queued to the user, and the total_matches the header reports) did not
        change shape or content."""
        import announce
        headers = []
        real_header = announce.send_search_result_header

        def fake_header(user, term, total, channel):
            headers.append(total)

        announce.send_search_result_header = fake_header
        self.addCleanup(lambda: setattr(announce, "send_search_result_header", real_header))

        list_mod.execute_search(None, "someuser", "sandman", "#mp3passion")

        self.assertEqual(headers, [1])
        self.assertEqual(len(self.oserve.queued), 1)
        sent_user, sent_msg, _is_vip = self.oserve.queued[0]
        self.assertEqual(sent_user, "someuser")
        self.assertIn("01 - Enter Sandman.flac", sent_msg)
        self.assertIn("::INFO:: 42.31MB", sent_msg)

    def test_execute_search_still_matches_nothing_on_an_all_punctuation_term(self):
        """Historical execute_search() edge case, preserved on purpose: a term
        that strips down to zero search words (e.g. "---") has always matched
        nothing, even though find_matching_entries([]) itself now means
        "match everything" for build_filelists_payload()'s benefit."""
        list_mod.execute_search(None, "someuser", "---", "#mp3passion")
        self.assertEqual(self.oserve.queued, [])


class FilelistsPaginationTests(SearchAndFilelistsPayloadTests):
    """Issue #76, option 3: build_filelists_payload()'s offset/limit
    behaviour. Subclasses SearchAndFilelistsPayloadTests to reuse its setUp()
    (the 4-row master list fixture) rather than rebuilding it."""

    def test_default_offset_and_limit_when_omitted(self):
        payload = webserver.build_filelists_payload()
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(payload["limit"], webserver.FILELISTS_DEFAULT_PAGE_SIZE)
        self.assertEqual(payload["total"], 2, "total counts folders, not rows")
        self.assertEqual(payload["returned"], 2)
        self.assertEqual(len(payload["folders"]), 2)  # fixture is smaller than a page

    def test_an_explicit_offset_and_limit_slice_returns_exactly_that_slice(self):
        all_folders = webserver.build_filelists_payload(0, 100)["folders"]
        names_in_order = [g["folder"] for g in all_folders]

        # offset/limit count FOLDERS: page two of one folder each.
        payload = webserver.build_filelists_payload(1, 1)
        self.assertEqual(payload["offset"], 1)
        self.assertEqual(payload["limit"], 1)
        self.assertEqual(payload["total"], 2)
        self.assertEqual([g["folder"] for g in payload["folders"]], names_in_order[1:2])
        # A folder arrives whole or not at all - never split across pages.
        self.assertEqual(len(payload["folders"][0]["entries"]),
                         payload["folders"][0]["count"])

    def test_an_offset_past_the_end_is_an_empty_page_with_the_correct_total(self):
        payload = webserver.build_filelists_payload(999, 50)
        self.assertEqual(payload["folders"], [])
        self.assertEqual(payload["returned"], 0)
        self.assertEqual(payload["total"], 2)

    def test_limit_is_clamped_at_the_documented_ceiling(self):
        payload = webserver.build_filelists_payload(0, webserver.FILELISTS_MAX_PAGE_SIZE + 5000)
        # build_filelists_payload() itself does not clamp - that is
        # parse_pagination_params()'s job, exercised below - but confirms the
        # slice still behaves sanely (returns everything there is) when
        # handed a limit larger than the whole dataset.
        self.assertEqual(len(payload["folders"]), 2)
        self.assertEqual(len(payload_rows(payload)), 5)


class PaginationParamParsingTests(unittest.TestCase):
    """webserver.parse_pagination_params() - the pure (offset, limit)
    validation shared by both GET /api/filelists and
    GET /api/filelists/bot/<nick>. No DCCoreTestCase config isolation needed:
    this touches no config state at all."""

    def test_missing_values_fall_back_to_defaults(self):
        offset, limit = webserver.parse_pagination_params(None, None)
        self.assertEqual(offset, 0)
        self.assertEqual(limit, webserver.FILELISTS_DEFAULT_PAGE_SIZE)

    def test_non_numeric_values_fall_back_to_defaults_not_an_error(self):
        offset, limit = webserver.parse_pagination_params("banana", "banana")
        self.assertEqual(offset, 0)
        self.assertEqual(limit, webserver.FILELISTS_DEFAULT_PAGE_SIZE)

    def test_negative_offset_falls_back_to_zero(self):
        offset, _limit = webserver.parse_pagination_params("-5", "50")
        self.assertEqual(offset, 0)

    def test_negative_or_zero_limit_falls_back_to_the_default(self):
        for bad in ("-5", "0"):
            with self.subTest(limit=bad):
                _offset, limit = webserver.parse_pagination_params("0", bad)
                self.assertEqual(limit, webserver.FILELISTS_DEFAULT_PAGE_SIZE)

    def test_a_valid_explicit_offset_and_limit_pass_through_unchanged(self):
        offset, limit = webserver.parse_pagination_params("40", "75")
        self.assertEqual(offset, 40)
        self.assertEqual(limit, 75)

    def test_limit_is_clamped_at_the_ceiling(self):
        offset, limit = webserver.parse_pagination_params("0", "999999999")
        self.assertEqual(offset, 0)
        self.assertEqual(limit, webserver.FILELISTS_MAX_PAGE_SIZE)

    def test_a_limit_exactly_at_the_ceiling_is_not_reduced(self):
        _offset, limit = webserver.parse_pagination_params("0", str(webserver.FILELISTS_MAX_PAGE_SIZE))
        self.assertEqual(limit, webserver.FILELISTS_MAX_PAGE_SIZE)


class BroadcastSearchTests(DCCoreTestCase):
    """start_broadcast_search()/build_broadcast_status_payload() - the pure
    logic behind POST /api/search/broadcast and its /status counterpart.
    NO AUTHENTICATION on either route (see webserver.py's module docstring);
    these tests are about the mutation/validation logic itself, not auth."""

    def setUp(self):
        super().setUp()
        self.oserve.irc_connection = "fake-connected-socket"
        config.CHANNEL = "#mp3passion,#mp3servers"
        config.BROADCAST_SEARCH_CHANNEL = "#mp3passion"

    def test_a_short_term_is_rejected(self):
        status, result = webserver.start_broadcast_search("ab")
        self.assertEqual(status, 400)
        self.assertIn("error", result)
        self.assertFalse(config.broadcast_search_inprogress)

    def test_a_valid_term_starts_listening_and_queues_the_find(self):
        status, result = webserver.start_broadcast_search("sandman")
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "listening")
        self.assertTrue(config.broadcast_search_inprogress)
        self.assertEqual(config.broadcast_search_term, "sandman")
        self.assertEqual(config.broadcast_search_results, [])

        self.assertEqual(len(self.oserve.queued), 1)
        _key, sent_msg, _is_vip = self.oserve.queued[0]
        self.assertEqual(sent_msg, "PRIVMSG #mp3passion :@find sandman\r\n")

    def test_it_never_blocks_the_caller_for_the_window_duration(self):
        real_window = webserver.BROADCAST_SEARCH_WINDOW
        webserver.BROADCAST_SEARCH_WINDOW = 0.2
        self.addCleanup(lambda: setattr(webserver, "BROADCAST_SEARCH_WINDOW", real_window))

        started = time.time()
        webserver.start_broadcast_search("sandman")
        self.assertLess(time.time() - started, 0.1)

    def test_the_window_closes_itself_after_the_duration(self):
        real_window = webserver.BROADCAST_SEARCH_WINDOW
        webserver.BROADCAST_SEARCH_WINDOW = 0.15
        self.addCleanup(lambda: setattr(webserver, "BROADCAST_SEARCH_WINDOW", real_window))

        webserver.start_broadcast_search("sandman")
        self.assertTrue(config.broadcast_search_inprogress)
        time.sleep(0.4)
        self.assertFalse(config.broadcast_search_inprogress)

    def test_a_second_broadcast_while_one_is_open_is_rejected(self):
        webserver.start_broadcast_search("sandman")
        status, result = webserver.start_broadcast_search("another")
        self.assertEqual(status, 409)
        self.assertIn("already in progress", result["error"])

    def test_cooldown_rejects_a_new_broadcast_started_too_soon(self):
        config.last_broadcast_search_at = time.time()
        config.broadcast_search_inprogress = False  # window already closed
        self.set_config(BROADCAST_SEARCH_COOLDOWN=30)
        status, result = webserver.start_broadcast_search("sandman")
        self.assertEqual(status, 429)
        self.assertIn("wait", result["error"])
        self.assertEqual(self.oserve.queued, [])

    def test_cooldown_elapsed_allows_a_new_broadcast(self):
        config.last_broadcast_search_at = time.time() - 999
        config.broadcast_search_inprogress = False
        self.set_config(BROADCAST_SEARCH_COOLDOWN=30)
        status, _result = webserver.start_broadcast_search("sandman")
        self.assertEqual(status, 200)

    def test_no_irc_connection_is_rejected(self):
        self.oserve.irc_connection = None
        status, result = webserver.start_broadcast_search("sandman")
        self.assertEqual(status, 503)
        self.assertIn("error", result)

    def test_a_term_with_embedded_crlf_is_rejected_not_queued(self):
        """BUG 1 regression: raw IRC command injection via CRLF. A `term`
        containing \\r\\n must never reach oserve.queue_message() - the
        outbound line it would build ("PRIVMSG <channel> :@find <term>\\r\\n")
        gets sent byte-for-byte to the live socket by queue_mgr.py, so an
        embedded CRLF would let the caller smuggle arbitrary extra IRC lines
        (QUIT, JOIN, PRIVMSG as this bot, etc.) past the intended one."""
        status, result = webserver.start_broadcast_search(
            "flac\r\nQUIT :pwned-via-broadcast\r\nPRIVMSG #admin :hi")
        self.assertEqual(status, 400)
        self.assertIn("error", result)
        self.assertFalse(config.broadcast_search_inprogress)
        self.assertEqual(self.oserve.queued, [])

    def test_a_term_with_bare_lf_is_also_rejected(self):
        status, result = webserver.start_broadcast_search("flac\nJOIN #secretadmin")
        self.assertEqual(status, 400)
        self.assertEqual(self.oserve.queued, [])

    def test_a_non_string_term_is_rejected_not_silently_coerced(self):
        status, result = webserver.start_broadcast_search({"nested": "dict"})
        self.assertEqual(status, 400)
        self.assertIn("error", result)
        self.assertEqual(self.oserve.queued, [])

    def test_status_payload_reflects_an_open_window(self):
        webserver.start_broadcast_search("sandman")
        config.broadcast_search_results.append({"from": "otherbot", "text": "line one", "received_at": time.time()})
        payload = webserver.build_broadcast_status_payload()
        self.assertTrue(payload["listening"])
        self.assertEqual(payload["term"], "sandman")
        self.assertEqual(len(payload["results"]), 1)

    def test_status_payload_reflects_a_closed_window(self):
        payload = webserver.build_broadcast_status_payload()
        self.assertFalse(payload["listening"])
        self.assertEqual(payload["results"], [])

    def test_expired_deadline_reads_as_not_listening_even_if_flag_is_stale(self):
        config.broadcast_search_inprogress = True
        config.broadcast_search_deadline = time.time() - 5
        payload = webserver.build_broadcast_status_payload()
        self.assertFalse(payload["listening"])


class FetchRoutesTests(DCCoreTestCase):
    """build_fetch_enqueue_result()/build_fetch_status_payload() - the pure
    logic behind POST /api/fetch/enqueue and GET /api/fetch/status."""

    def test_a_single_object_is_accepted(self):
        status, result = webserver.build_fetch_enqueue_result({"bot": "goodbot", "filename": "Song.flac"})
        self.assertEqual(status, 200)
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["errors"], [])
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["bot"], "goodbot")
        self.assertEqual(config.fetch_queue[rid]["state"], "pending")

    def test_a_list_multi_select_is_accepted(self):
        status, result = webserver.build_fetch_enqueue_result([
            {"bot": "bot1", "filename": "A.flac"},
            {"bot": "bot2", "filename": "B.flac"},
        ])
        self.assertEqual(status, 200)
        self.assertEqual(len(result["created"]), 2)
        self.assertEqual(len(config.fetch_queue), 2)

    def test_missing_fields_are_reported_as_errors_not_silently_dropped(self):
        status, result = webserver.build_fetch_enqueue_result([
            {"bot": "goodbot", "filename": "A.flac"},
            {"bot": "", "filename": "B.flac"},
            {"filename": "C.flac"},
        ])
        self.assertEqual(status, 200)
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(len(result["errors"]), 2)

    def test_an_empty_or_malformed_payload_shape_is_rejected(self):
        """None/[]/a bare string/a number: the payload itself is the wrong
        SHAPE (not a dict, not a list) - rejected at the top with a single
        "error" key, before any per-item validation runs."""
        for bad in (None, [], "not a dict or list", 42):
            status, result = webserver.build_fetch_enqueue_result(bad)
            self.assertEqual(status, 400, f"payload={bad!r}")
            self.assertIn("error", result)

    def test_an_empty_object_is_shape_valid_but_fails_field_validation(self):
        """{} is a validly-shaped single item - it is rejected the OTHER way,
        as a per-item entry in "errors" (missing bot/filename), not as a
        payload-shape error."""
        status, result = webserver.build_fetch_enqueue_result({})
        self.assertEqual(status, 400)
        self.assertEqual(result["created"], [])
        self.assertEqual(len(result["errors"]), 1)

    def test_bot_with_embedded_crlf_is_rejected_not_queued(self):
        """BUG 1 regression: dcc_fetch.check_fetch_queue()'s dispatcher later
        interpolates `bot`/`filename` verbatim into an outbound
        "PRIVMSG <bot> :!<bot> <filename>\\r\\n" line - a CRLF embedded in
        either field would let the caller smuggle extra raw IRC lines
        (QUIT, JOIN #secretadmin, etc.) past the intended one. Reproduces the
        adversarial repro: bot="victimbot\\r\\nQUIT :pwned-by-webhook\\r\\nJOIN #secretadmin"."""
        status, result = webserver.build_fetch_enqueue_result({
            "bot": "victimbot\r\nQUIT :pwned-by-webhook\r\nJOIN #secretadmin",
            "filename": "x.mp3",
        })
        self.assertEqual(status, 400)
        self.assertEqual(result["created"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(config.fetch_queue, {})

    def test_filename_with_embedded_crlf_is_rejected_not_queued(self):
        status, result = webserver.build_fetch_enqueue_result({
            "bot": "goodbot",
            "filename": "song.flac\r\nQUIT :pwned",
        })
        self.assertEqual(status, 400)
        self.assertEqual(result["created"], [])
        self.assertEqual(config.fetch_queue, {})

    def test_bot_with_a_ctcp_delimiter_is_rejected_not_queued(self):
        """BUG 1 regression (recurred a THIRD time): \\x01 closes the CTCP
        wrapper dcc_fetch._serve_passive_offer() later builds early, letting
        the receiving peer parse trailing content as a second CTCP or as
        plain text - reject_if_unsafe_for_irc_line() used to reject only
        \\r/\\n, not \\x01, even though dcc_fetch's own equivalent check
        already did. Now both delegate to the same
        dcc_fetch.contains_unsafe_ctcp_bytes()."""
        status, result = webserver.build_fetch_enqueue_result({
            "bot": "evilbot\x01ACTION pwned\x01",
            "filename": "Song.flac",
        })
        self.assertEqual(status, 400)
        self.assertEqual(result["created"], [])
        self.assertEqual(config.fetch_queue, {})

    def test_filename_with_a_ctcp_delimiter_is_rejected_not_queued(self):
        status, result = webserver.build_fetch_enqueue_result({
            "bot": "goodbot",
            "filename": "Song\x01.mp3",
        })
        self.assertEqual(status, 400)
        self.assertEqual(result["created"], [])
        self.assertEqual(config.fetch_queue, {})

    def test_non_string_bot_and_filename_are_rejected_not_silently_coerced(self):
        """BUG 3 regression: {"bot": {"nested": "dict"}, "filename": 12345}
        used to return 200 and silently str()-coerce into a real queue row.
        Must now be rejected with 400, per-item, and never create a row."""
        status, result = webserver.build_fetch_enqueue_result({
            "bot": {"nested": "dict"},
            "filename": 12345,
        })
        self.assertEqual(status, 400)
        self.assertEqual(result["created"], [])
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(config.fetch_queue, {})

    def test_a_bad_item_in_a_multiselect_list_is_rejected_others_still_created(self):
        status, result = webserver.build_fetch_enqueue_result([
            {"bot": "goodbot", "filename": "Good.flac"},
            {"bot": "evilbot\r\nQUIT :pwned", "filename": "Evil.flac"},
            {"bot": 12345, "filename": "Bad.flac"},
        ])
        self.assertEqual(status, 200)
        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(len(result["errors"]), 2)
        self.assertEqual(len(config.fetch_queue), 1)

    def test_status_payload_is_ordered_oldest_first_and_carries_the_id(self):
        rid1 = None
        import dcc_fetch
        rid1 = dcc_fetch.enqueue_fetch("bot1", "First.flac")
        config.fetch_queue[rid1]["requested_at"] = 1.0
        rid2 = dcc_fetch.enqueue_fetch("bot2", "Second.flac")
        config.fetch_queue[rid2]["requested_at"] = 2.0

        rows = webserver.build_fetch_status_payload()
        self.assertEqual([r["id"] for r in rows], [rid1, rid2])
        self.assertEqual(rows[0]["bot"], "bot1")

    def test_status_payload_is_empty_list_when_queue_is_empty(self):
        self.assertEqual(webserver.build_fetch_status_payload(), [])


class ListFetchRoutesTests(DCCoreTestCase):
    """build_list_fetch_enqueue_result()/build_fetched_bot_list_summaries()/
    build_fetched_bot_list_payload() - the pure logic behind
    POST /api/filelists/fetch, GET /api/filelists/bots and
    GET /api/filelists/bot/<nick>."""

    def test_a_clean_bot_nick_is_accepted_and_creates_a_pending_list_row(self):
        status, result = webserver.build_list_fetch_enqueue_result("goodbot")
        self.assertEqual(status, 200)
        self.assertEqual(len(result["created"]), 1)
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["bot"], "goodbot")
        self.assertEqual(config.fetch_queue[rid]["request_type"], "list")
        self.assertEqual(config.fetch_queue[rid]["state"], "pending")
        self.assertEqual(config.fetch_queue[rid]["filename"], "")

    def test_an_empty_or_blank_bot_nick_is_rejected(self):
        for bad in ("", "   "):
            status, result = webserver.build_list_fetch_enqueue_result(bad)
            self.assertEqual(status, 400, f"bot={bad!r}")
            self.assertIn("error", result)
        self.assertEqual(config.fetch_queue, {})

    def test_a_non_string_bot_is_rejected_not_silently_coerced(self):
        status, result = webserver.build_list_fetch_enqueue_result({"nested": "dict"})
        self.assertEqual(status, 400)
        self.assertEqual(config.fetch_queue, {})

    def test_bot_with_embedded_crlf_is_rejected_not_queued(self):
        """Same BUG 1 injection class as POST /api/fetch/enqueue's bot/filename
        (see FetchRoutesTests above) - the bot nick typed into the File Lists
        fetch box reaches the exact same outbound-IRC-line boundary
        (check_fetch_queue()'s "PRIVMSG <channel> :@<bot>\\r\\n") and must be
        rejected the same way."""
        status, result = webserver.build_list_fetch_enqueue_result(
            "victimbot\r\nQUIT :pwned-by-webhook\r\nJOIN #secretadmin")
        self.assertEqual(status, 400)
        self.assertEqual(config.fetch_queue, {})

    def test_bot_with_a_bare_lf_is_also_rejected(self):
        status, result = webserver.build_list_fetch_enqueue_result("evilbot\nPRIVMSG #x :hi")
        self.assertEqual(status, 400)
        self.assertEqual(config.fetch_queue, {})

    def test_bot_with_a_ctcp_delimiter_is_rejected(self):
        """BUG 1 regression, list-fetch route: check_fetch_queue() builds
        this bot's outbound trigger as a bare "PRIVMSG <channel> :@<bot>",
        but a CTCP-wrapped reply is exactly what an offering bot answers
        with, and a \\x01 in `bot` is just as much an injection vector here
        as it is for /api/fetch/enqueue's bot/filename - see
        webserver.reject_if_unsafe_for_irc_line()."""
        status, result = webserver.build_list_fetch_enqueue_result(
            "evilbot\x01ACTION pwned\x01")
        self.assertEqual(status, 400)
        self.assertEqual(config.fetch_queue, {})

    def test_summaries_are_empty_when_nothing_has_been_fetched(self):
        self.assertEqual(webserver.build_fetched_bot_list_summaries(), [])

    def test_summaries_reflect_fetched_bots_sorted_by_nick(self):
        # Issue #76, option 2: summaries read the precomputed "entry_count"
        # field, not a stored "entries" list (which no longer exists) - no
        # real on-disk list_path is needed for this one, since
        # build_fetched_bot_list_summaries() never reads the file itself.
        config.fetched_bot_lists = {
            "zbot": {"bot": "zbot", "fetched_at": 111.0, "entry_count": 2},
            "abot": {"bot": "ABot", "fetched_at": 222.0, "entry_count": 0},
        }
        rows = webserver.build_fetched_bot_list_summaries()
        self.assertEqual([r["bot"] for r in rows], ["ABot", "zbot"])
        by_bot = {r["bot"]: r for r in rows}
        self.assertEqual(by_bot["zbot"]["count"], 2)
        self.assertEqual(by_bot["ABot"]["count"], 0)

    def test_bot_payload_returns_404_for_an_unknown_nick(self):
        status, result = webserver.build_fetched_bot_list_payload("nosuchbot")
        self.assertEqual(status, 404)
        self.assertIn("error", result)

    def _make_fetched_entry(self, bot, files, folders=None):
        """A real on-disk list file plus the {"bot","fetched_at","list_path",
        "entry_count","source_zip"} dict process_fetched_list_zip() now
        stores - build_fetched_bot_list_payload() re-parses `list_path` from
        disk on every call (issue #76, option 2), so a fake in-memory
        "entries" list is no longer enough to exercise it."""
        # `folders` when the test is about paging, which counts folders -
        # a flat `files` list is one group and would page as one item.
        path = write_master_list(self.tree.lists, bot,
                                 folders or [(None, files)])
        return {
            "bot": bot,
            "fetched_at": 111.0,
            "list_path": path,
            "entry_count": (len(files) if files is not None
                            else sum(len(f) for _folder, f in folders)),
            "source_zip": "incoming.zip",
        }

    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()

    def test_bot_payload_is_case_insensitive_and_matches_own_list_row_shape(self):
        config.fetched_bot_lists = {
            "goodbot": self._make_fetched_entry("GoodBot", [("A.flac", "1.0MB")]),
        }
        status, result = webserver.build_fetched_bot_list_payload("GOODBOT")
        self.assertEqual(status, 200)
        self.assertEqual(result["bot"], "GoodBot")
        rows = [r for g in result["folders"] for r in g["entries"]]
        self.assertEqual(rows[0]["title"], "A.flac")
        # Exactly the row shape build_filelists_payload() uses for our own
        # list - same five keys, nothing more, nothing less. "folder" joined
        # them when the views became folder-grouped.
        self.assertEqual(set(rows[0].keys()),
                         {"title", "size", "format", "source", "folder"})
        self.assertEqual(result["total"], 1, "one folder in this fixture")
        self.assertEqual(result["total_files"], 1)
        self.assertEqual(result["offset"], 0)
        self.assertEqual(result["limit"], webserver.FILELISTS_DEFAULT_PAGE_SIZE)

    def test_bot_payload_paginates_and_reports_the_correct_total(self):
        folders = [(f"D:/MUSIC/Album {i:02d}/", [(f"Track {i:02d}.flac", "1.0MB")])
                   for i in range(10)]
        config.fetched_bot_lists = {
            "pagebot": self._make_fetched_entry("PageBot", None, folders=folders)}

        status, result = webserver.build_fetched_bot_list_payload("pagebot", offset=3, limit=4)
        self.assertEqual(status, 200)
        self.assertEqual(result["total"], 10, "offset/limit count folders")
        self.assertEqual(result["total_files"], 10)
        self.assertEqual(result["offset"], 3)
        self.assertEqual(result["limit"], 4)
        self.assertEqual([g["folder"].rstrip("/") for g in result["folders"]],
                         ["D:/MUSIC/Album 03", "D:/MUSIC/Album 04",
                          "D:/MUSIC/Album 05", "D:/MUSIC/Album 06"])

    def test_bot_payload_offset_past_the_end_is_empty_with_correct_total(self):
        config.fetched_bot_lists = {"onebot": self._make_fetched_entry("OneBot", [("A.flac", "1.0MB")])}

        status, result = webserver.build_fetched_bot_list_payload("onebot", offset=500, limit=50)
        self.assertEqual(status, 200)
        self.assertEqual(result["folders"], [])
        self.assertEqual(result["returned"], 0)
        self.assertEqual(result["total"], 1, "one folder in this fixture")

    def test_bot_payload_reports_a_clear_error_when_the_file_has_gone_missing(self):
        """The extracted file can disappear between the fetch and the view -
        an operator manually clearing data/fetched/, or an unrelated bug.
        This must come back as a clean error response, never a 500 from an
        unhandled exception."""
        entry = self._make_fetched_entry("GoneBot", [("A.flac", "1.0MB")])
        os.remove(entry["list_path"])
        config.fetched_bot_lists = {"gonebot": entry}

        status, result = webserver.build_fetched_bot_list_payload("gonebot")
        self.assertNotEqual(status, 200)
        self.assertIn("error", result)


@unittest.skipUnless(webserver.HAVE_FLASK, "Flask not installed - see the module docstring: "
                                            "CI never installs it, this class only runs when it "
                                            "happens to be available locally")
class CrlfInjectionHttpRouteTests(DCCoreTestCase):
    """BUG 1 regression, exercised through the real Flask app/test client
    (not just the pure functions above) - end to end, a raw HTTP POST with an
    embedded CRLF in `term`/`bot`/`filename` must come back 400 and must
    never reach oserve.queue_message(). Skipped entirely when Flask is not
    installed, same as the rest of this module's Flask-gated behaviour."""

    def setUp(self):
        super().setUp()
        self.oserve.irc_connection = "fake-connected-socket"
        config.CHANNEL = "#mp3passion,#mp3servers"
        config.BROADCAST_SEARCH_CHANNEL = "#mp3passion"
        self.app = webserver.create_app()
        self.client = self.app.test_client()

    def test_broadcast_term_with_crlf_is_rejected_via_http(self):
        resp = self.client.post("/api/search/broadcast", json={
            "term": "flac\r\nQUIT :pwned via broadcast\r\nPRIVMSG #admin :hi",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())
        self.assertEqual(self.oserve.queued, [])
        self.assertFalse(config.broadcast_search_inprogress)

    def test_fetch_enqueue_bot_with_crlf_is_rejected_via_http(self):
        resp = self.client.post("/api/fetch/enqueue", json={
            "bot": "victimbot\r\nQUIT :pwned-by-webhook\r\nJOIN #secretadmin",
            "filename": "x.mp3",
        })
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertEqual(body["created"], [])
        self.assertEqual(config.fetch_queue, {})

    def test_fetch_enqueue_filename_with_crlf_is_rejected_via_http(self):
        resp = self.client.post("/api/fetch/enqueue", json={
            "bot": "goodbot",
            "filename": "x.mp3\r\nQUIT :pwned",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(config.fetch_queue, {})

    def test_fetch_enqueue_non_string_fields_rejected_via_http(self):
        resp = self.client.post("/api/fetch/enqueue", json={
            "bot": {"nested": "dict"},
            "filename": 12345,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(config.fetch_queue, {})

    def test_fetch_enqueue_bot_with_ctcp_delimiter_is_rejected_via_http(self):
        """BUG 1 regression, end to end through the real Flask app: a bare
        \\x01 in `bot` used to sail past reject_if_unsafe_for_irc_line()
        (which only checked \\r/\\n) all the way to a 200 response."""
        resp = self.client.post("/api/fetch/enqueue", json={
            "bot": "evilbot2\x01INJECTED\x01",
            "filename": "Song\x01.mp3",
        })
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertEqual(body["created"], [])
        self.assertEqual(config.fetch_queue, {})

    def test_a_clean_broadcast_still_works_via_http(self):
        """The CRLF/type checks must not false-positive on ordinary input."""
        resp = self.client.post("/api/search/broadcast", json={"term": "sandman"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.oserve.queued), 1)

    def test_a_clean_fetch_enqueue_still_works_via_http(self):
        resp = self.client.post("/api/fetch/enqueue", json={"bot": "goodbot", "filename": "Song.flac"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.get_json()["created"]), 1)

    def test_filelists_fetch_bot_with_crlf_is_rejected_via_http(self):
        resp = self.client.post("/api/filelists/fetch", json={
            "bot": "victimbot\r\nQUIT :pwned-by-webhook\r\nJOIN #secretadmin",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(config.fetch_queue, {})

    def test_filelists_fetch_bot_with_ctcp_delimiter_is_rejected_via_http(self):
        resp = self.client.post("/api/filelists/fetch", json={
            "bot": "evilbot\x01ACTION pwned\x01",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(config.fetch_queue, {})

    def test_a_clean_filelists_fetch_still_works_via_http(self):
        resp = self.client.post("/api/filelists/fetch", json={"bot": "goodbot"})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(len(body["created"]), 1)
        self.assertEqual(config.fetch_queue[body["created"][0]]["request_type"], "list")

    def test_filelists_bots_route_returns_an_empty_list_with_nothing_fetched(self):
        resp = self.client.get("/api/filelists/bots")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_filelists_bot_route_returns_404_for_an_unknown_nick(self):
        resp = self.client.get("/api/filelists/bot/nosuchbot")
        self.assertEqual(resp.status_code, 404)


@unittest.skipUnless(webserver.HAVE_FLASK, "Flask not installed - see the module docstring: "
                                            "CI never installs it, this class only runs when it "
                                            "happens to be available locally")
class FilelistsHttpPaginationTests(DCCoreTestCase):
    """GET /api/filelists and GET /api/filelists/bot/<nick>, end to end
    through the real Flask app - both now return a page object
    ({"entries","total","offset","limit"}), not a bare array, and both honour
    `?offset=&limit=` (issue #76, option 3)."""

    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()
        config.LOCAL_LIST_DIR = self.tree.lists
        config.LIST_BASE_NAME = "DCCore"
        config.NICKNAME = "DCCore"
        # Ten folders of one track each. The old fixture put all ten files
        # under no folder at all, which pages as a single group now and would
        # exercise nothing.
        write_master_list(self.tree.lists, "DCCore", [
            (f"D:\MUSIC\Album {i:02d}\\", [(f"Track {i:02d}.flac", "1.0MB")])
            for i in range(10)
        ])
        self.app = webserver.create_app()
        self.client = self.app.test_client()

    def test_own_list_defaults_to_the_documented_page_size(self):
        resp = self.client.get("/api/filelists")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["total"], 10, "ten folders")
        self.assertEqual(body["total_files"], 10)
        self.assertEqual(body["offset"], 0)
        self.assertEqual(body["limit"], webserver.FILELISTS_DEFAULT_PAGE_SIZE)
        self.assertEqual(len(body["folders"]), 10)

    def test_own_list_honours_explicit_offset_and_limit(self):
        resp = self.client.get("/api/filelists?offset=2&limit=3")
        body = resp.get_json()
        self.assertEqual(body["offset"], 2)
        self.assertEqual(body["limit"], 3)
        self.assertEqual(len(body["folders"]), 3, "three FOLDERS, not three rows")

    def test_own_list_invalid_query_values_fall_back_to_defaults_via_http(self):
        resp = self.client.get("/api/filelists?offset=notanumber&limit=-5")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["offset"], 0)
        self.assertEqual(body["limit"], webserver.FILELISTS_DEFAULT_PAGE_SIZE)

    def test_own_list_limit_is_clamped_via_http(self):
        resp = self.client.get("/api/filelists?limit=999999999")
        body = resp.get_json()
        self.assertEqual(body["limit"], webserver.FILELISTS_MAX_PAGE_SIZE)

    def test_fetched_bot_list_is_paginated_via_http(self):
        path = write_master_list(self.tree.lists, "OtherBot", [
            (f"D:/MUSIC/Other {i:02d}/", [(f"Other {i:02d}.flac", "2.0MB")])
            for i in range(7)
        ])
        config.fetched_bot_lists = {
            "otherbot": {"bot": "OtherBot", "fetched_at": 111.0,
                         "list_path": path, "entry_count": 7,
                         "source_zip": "incoming.zip"},
        }
        resp = self.client.get("/api/filelists/bot/otherbot?offset=5&limit=10")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["total"], 7, "seven folders")
        self.assertEqual(body["offset"], 5)
        self.assertEqual(len(body["folders"]), 2, "the tail: folders 5 and 6")


class BroadcastRenderingXssRegressionTests(unittest.TestCase):
    """BUG 2 regression: web/app.js's escapeHtml() (div.textContent = v;
    return div.innerHTML) never encodes `"`. renderBroadcastResults() used to
    string-concatenate it straight into data-bot="..."/data-filename="..."
    HTML attribute values built from entry.bot/entry.filename - which come
    from irc.py's best-effort "!<bot> <filename>" extraction against
    arbitrary text ANY IRC user can PM/NOTICE to the bot during an open
    broadcast window (attacker-controlled, unauthenticated). A filename like
    `innocent.mp3" autofocus onfocus="alert(document.cookie)" x="` broke out
    of the attribute and injected a live event handler.

    The project has no browser/DOM test harness, and none of the rest of the
    stdlib-only suite needs one, so this is a source-level regression check
    rather than a full DOM assertion: it confirms renderBroadcastResults() no
    longer builds those two attributes via string-concatenated innerHTML, and
    uses .dataset assignment (which the browser encodes correctly, with no
    HTML-parsing step for untrusted text to escape through) instead.
    """

    @classmethod
    def setUpClass(cls):
        app_js_path = os.path.join(REPO_ROOT, "web", "app.js")
        with open(app_js_path, "r", encoding="utf-8") as f:
            cls.source = f.read()

    def _extract_function(self, name):
        start = self.source.index(f"function {name}(")
        # A generously-sized window after the signature - good enough to
        # contain this (short) function's whole body without a real JS parser.
        return self.source[start:start + 2500]

    def test_render_broadcast_results_does_not_build_attributes_via_string_concat(self):
        body = self._extract_function("renderBroadcastResults")
        self.assertNotIn('data-bot="', body)
        self.assertNotIn('data-filename="', body)

    def test_render_broadcast_results_uses_dataset_assignment_instead(self):
        body = self._extract_function("renderBroadcastResults")
        self.assertIn(".dataset.bot = entry.bot", body)
        self.assertIn(".dataset.filename = entry.filename", body)

    def test_no_render_function_anywhere_builds_these_attributes_via_string_concat(self):
        """Confirms the audit findings: this exact unsafe pattern - an HTML
        attribute value string-concatenated from a value that can originate
        from IRC (a filename, nick, or search term) - never existed anywhere
        else in this file. Search/Queue/File Lists rendering only ever place
        escapeHtml() output inside text-node content (between <td>...</td>),
        never inside an attribute, so they were never exposed to this bug
        class in the first place."""
        self.assertNotIn('data-bot="', self.source)
        self.assertNotIn('data-filename="', self.source)


class DownloadTabAndFilelistsSwitcherRegressionTests(unittest.TestCase):
    """New user-supplied text surfaces added alongside the Download tab
    (bot/filename pairs parsed out of the bulk-paste textarea) and the File
    Lists bot-switcher (bot nicks typed into the fetch box, and bot nicks/
    counts returned by GET /api/filelists/bots): same bug class as
    BroadcastRenderingXssRegressionTests above - confirms none of this new
    rendering code builds an HTML attribute via string-concatenated
    innerHTML from any of it, and reuses .textContent/DOM APIs instead.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO_ROOT, "web", "app.js"), "r", encoding="utf-8") as f:
            cls.source = f.read()

    def _extract_function(self, name):
        start = self.source.index(f"function {name}(")
        return self.source[start:start + 2500]

    def test_bulk_fetch_messages_are_rendered_via_textcontent(self):
        body = self._extract_function("renderBulkFetchMessages")
        self.assertIn(".textContent = msg.text", body)
        self.assertNotIn("innerHTML +=", body)
        self.assertNotIn("innerHTML +", body)

    def test_filelists_switcher_options_are_built_via_dom_apis(self):
        body = self._extract_function("renderFilelistsSwitcher")
        self.assertIn(".textContent = row.bot", body)
        self.assertNotIn("innerHTML +=", body)
        self.assertNotIn("innerHTML +", body)

    def test_no_new_attribute_built_via_string_concatenated_innerhtml(self):
        """Bot nicks and filenames from the Download tab / File Lists fetch
        box never end up inside an HTML attribute value built by string
        concatenation anywhere in the file - the exact bug class already
        fixed once (BUG 2, see BroadcastRenderingXssRegressionTests above)."""
        self.assertNotIn('data-bot="', self.source)
        self.assertNotIn('data-filename="', self.source)

    def test_bulk_fetch_form_is_wired_up(self):
        self.assertIn('el.bulkFetchForm.addEventListener("submit"', self.source)

    def test_filelists_fetch_form_is_wired_up(self):
        self.assertIn('el.filelistsFetchForm.addEventListener("submit"', self.source)

    def test_bulk_fetch_line_regex_matches_the_shared_bang_bot_filename_pattern(self):
        """Per the brief: reuse the same tolerant "!(\\S+)\\s+(.+)$" pattern
        already used elsewhere, don't reinvent it."""
        self.assertIn("BULK_FETCH_LINE_RE = /^!(\\S+)\\s+(.+)$/", self.source)


class FilelistsPaginationJsRegressionTests(unittest.TestCase):
    """Issue #76, option 3's frontend half: the File Lists table's Prev/Next
    pager. Same "no unsafe innerHTML string-concat with untrusted text"
    discipline as the other rendering regression tests above - the pager's
    own text (a row count/offset, never IRC-originated) is not itself a risk,
    but the page-size constant and wiring are pinned so a future edit can't
    silently drift the frontend page size away from the backend default, or
    drop the reset-to-first-page behaviour."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO_ROOT, "web", "app.js"), "r", encoding="utf-8") as f:
            cls.source = f.read()

    def _extract_function(self, name):
        """Unlike the fixed-window extraction the other regression classes in
        this file use (good enough for their short functions), renderFilelistsPager()
        sits directly above the much longer loadFilelists() with no blank-line
        gap, so a fixed window bleeds into the next function's body and its
        (legitimate, elsewhere) innerHTML usage. Balances braces instead, to
        get exactly this one function's body regardless of what follows it."""
        start = self.source.index(f"function {name}(")
        brace_start = self.source.index("{", start)
        depth = 0
        for i in range(brace_start, len(self.source)):
            if self.source[i] == "{":
                depth += 1
            elif self.source[i] == "}":
                depth -= 1
                if depth == 0:
                    return self.source[start:i + 1]
        raise AssertionError(f"unbalanced braces looking for function {name}()")

    def test_page_size_constant_matches_the_documented_backend_default(self):
        """webserver.FILELISTS_DEFAULT_PAGE_SIZE is the source of truth; this
        just pins that the frontend constant was set to the same number."""
        import webserver
        self.assertIn(f"var FILELISTS_PAGE_SIZE = {webserver.FILELISTS_DEFAULT_PAGE_SIZE};", self.source)

    def test_pager_info_is_rendered_via_textcontent_not_innerhtml(self):
        body = self._extract_function("renderFilelistsPager")
        self.assertIn(".textContent =", body)
        self.assertNotIn("innerHTML", body)

    def test_prev_and_next_buttons_are_wired_up(self):
        self.assertIn('el.filelistsPrevBtn.addEventListener("click"', self.source)
        self.assertIn('el.filelistsNextBtn.addEventListener("click"', self.source)

    def test_switching_bot_source_resets_to_the_first_page(self):
        start = self.source.index('el.filelistsSourceSelect.addEventListener("change"')
        body = self.source[start:start + 300]
        self.assertIn("state.filelistsOffset = 0", body)

    def test_load_filelists_requests_offset_and_limit_query_params(self):
        body = self._extract_function("loadFilelists")
        self.assertIn('"?offset=" + offset + "&limit=" + FILELISTS_PAGE_SIZE', body)


class OptionalFlaskDependencyTests(unittest.TestCase):
    """The load-bearing design decision: importing this module, and calling
    start(), must never require Flask to be installed."""

    def test_have_flask_matches_whether_flask_actually_imports(self):
        try:
            import flask  # noqa: F401
            expected = True
        except ImportError:
            expected = False
        self.assertEqual(webserver.HAVE_FLASK, expected)

    def test_start_logs_and_returns_when_flask_is_missing(self):
        real_have_flask = webserver.HAVE_FLASK
        webserver.HAVE_FLASK = False
        self.addCleanup(lambda: setattr(webserver, "HAVE_FLASK", real_have_flask))

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            webserver.start()  # must not raise
        self.assertIn("Flask not installed", buffer.getvalue())

    def test_start_logs_and_returns_when_disabled_via_config(self):
        real_have_flask = webserver.HAVE_FLASK
        webserver.HAVE_FLASK = True
        self.addCleanup(lambda: setattr(webserver, "HAVE_FLASK", real_have_flask))
        real_enabled = getattr(config, "WEBUI_ENABLED", True)
        config.WEBUI_ENABLED = False
        self.addCleanup(lambda: setattr(config, "WEBUI_ENABLED", real_enabled))

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            webserver.start()  # must return before ever touching Flask/create_app
        self.assertIn("Disabled via config.WEBUI_ENABLED", buffer.getvalue())


class ListeningStateRenderingTests(unittest.TestCase):
    """Regression: dcc_fetch.py writes a 'listening' state to a fetch-queue
    row (the interim state a passive/reverse DCC SEND offer sits in while we
    wait for the offering bot to connect back), but web/app.js's
    DOWNLOAD_STATE_LABELS and style.css's .status-pill rules were never
    extended for it - the dashboard fell back to the raw string "listening"
    with no matching color for however long that state lasted (up to
    PASSIVE_LISTEN_TIMEOUT, 60s).
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO_ROOT, "web", "app.js"), "r", encoding="utf-8") as f:
            cls.app_js = f.read()
        with open(os.path.join(REPO_ROOT, "web", "style.css"), "r", encoding="utf-8") as f:
            cls.style_css = f.read()

    def test_download_state_labels_has_an_entry_for_listening(self):
        start = self.app_js.index("DOWNLOAD_STATE_LABELS = {")
        body = self.app_js[start:start + 300]
        self.assertIn("listening:", body)

    def test_style_css_has_a_rule_for_status_listening(self):
        self.assertIn(".status-pill.status-listening", self.style_css)


class JsonBodyMustBeAnObject(DCCoreTestCase):
    """POST bodies that parse to something other than a JSON object.

    The two routes used `request.get_json(silent=True) or {}`, which only
    substitutes for a FALSY result. A truthy non-dict - `["a"]`, `"text"`, `7`
    - passed straight through to `.get()` on the next line and raised
    AttributeError, which Flask turns into a 500 with a traceback. Every other
    bad input to these routes gets a 400.

    An empty dict is the right substitute rather than an error of its own: it
    is exactly what a missing body already produces, and the validators
    downstream turn that into their normal 400.
    """

    def test_an_object_passes_through_unchanged(self):
        body = {"term": "sandman"}
        self.assertIs(webserver.json_object(body), body)

    def test_truthy_non_objects_become_an_empty_object(self):
        """The bug. Each of these is truthy, so `or {}` left it intact."""
        for bad in (["a", "b"], "text", 7, 1.5, True):
            with self.subTest(body=bad):
                self.assertEqual(webserver.json_object(bad), {})

    def test_falsy_bodies_still_become_an_empty_object(self):
        """Control. `or {}` already handled these - the replacement must too,
        or a request with no body at all starts failing differently."""
        for empty in (None, {}, [], "", 0):
            with self.subTest(body=empty):
                self.assertEqual(webserver.json_object(empty), {})

    def test_the_result_is_always_safe_to_call_get_on(self):
        """The defect stated directly: whatever the peer sent, the next line
        of every route is a .get(), and it must not raise."""
        for bad in (["a"], "text", 7, None, True):
            with self.subTest(body=bad):
                self.assertEqual(webserver.json_object(bad).get("term", ""), "")

    def test_a_non_object_body_reaches_the_normal_400(self):
        """End to end through the pure route logic: a bad body should land on
        the same 400 an empty body gets, not a 500."""
        status, _payload = webserver.start_broadcast_search(
            webserver.json_object(["not", "an", "object"]).get("term", ""))
        self.assertEqual(status, 400)

        status, _payload = webserver.build_list_fetch_enqueue_result(
            webserver.json_object("not an object").get("bot", ""))
        self.assertEqual(status, 400)

    def test_no_post_route_still_coerces_with_or(self):
        """Guards the wiring, not just the helper.

        A route that goes back to `request.get_json(silent=True) or {}`
        reintroduces the 500, and a unit test of json_object() on its own
        would never notice - the helper would still be correct, just unused.
        """
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "webserver.py")
        with io.open(path, encoding="utf-8") as handle:
            source = handle.read()

        # assertFalse, not assertNotIn: the haystack is the whole module, and
        # assertNotIn prints it in full on failure.
        self.assertFalse(
            "get_json(silent=True) or {}" in source,
            "a POST route coerces its body with `or {}` again, which lets a "
            "truthy non-dict through to .get() and returns a 500")


class RejectedListArchiveRenderingTests(DCCoreTestCase):
    """A list archive whose bytes arrived intact but whose contents the
    extraction guard refused.

    dcc_fetch.py deliberately leaves state == "complete" - the transfer
    really did succeed - and records why the archive was refused in
    row["list_processing_error"], explicitly "on the row for the dashboard".
    /api/fetch/status serves it.

    web/app.js never read it. renderDownloads() branched only on state, and
    read row.reason only in the "failed" arm, so a rejected archive - a
    zip-slip attempt by a foreign bot included - rendered as "Complete" with
    a working Download button, indistinguishable from a list that fetched
    perfectly. The field was served and discarded, and the only record of the
    attempt was a single stdout line.
    """

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO_ROOT, "web", "app.js"), "r", encoding="utf-8") as f:
            cls.app_js = f.read()
        with open(os.path.join(REPO_ROOT, "web", "style.css"), "r", encoding="utf-8") as f:
            cls.style_css = f.read()

    def _render_downloads_source(self):
        """Just renderDownloads()'s body.

        Anchored on a two-space-indented `function` for the end, not a bare
        one: renderDownloads contains an anonymous `function (row)` in its
        rows.map() call, and stopping at that returns six useless lines.
        """
        start = self.app_js.index("  function renderDownloads(")
        end = self.app_js.index(chr(10) + "  function ", start + 10)
        return self.app_js[start:end]

    def test_the_dashboard_reads_the_field_at_all(self):
        """The defect in one line: written three times server side, read zero
        times client side."""
        # assertTrue, not assertIn: the haystack is a whole source file and
        # assertIn prints it in full on failure.
        self.assertTrue(
            "list_processing_error" in self._render_downloads_source(),
            "renderDownloads() still never reads the field the server sets "
            "for it, so a refused archive still renders as a success")

    def test_download_state_labels_has_an_entry_for_rejected(self):
        start = self.app_js.index("DOWNLOAD_STATE_LABELS = {")
        self.assertTrue("rejected:" in self.app_js[start:start + 500],
                        "no display label for a rejected archive")

    def test_style_css_has_a_rule_for_status_rejected(self):
        self.assertTrue(".status-pill.status-rejected" in self.style_css,
                        "the rejected pill has no colour rule, so it renders "
                        "with the default and reads as an ordinary state")

    def test_the_rejected_check_runs_before_the_complete_arm(self):
        """Order matters. A rejected row is ALSO state === "complete", so if
        the complete arm is tested first it gets the Download button anyway
        and the fix does nothing."""
        render = self._render_downloads_source()
        self.assertLess(
            render.index("if (rejected)"),
            # The BRANCH, not a mention of it - the comment above that branch
            # names the same condition and would otherwise match first.
            render.index('else if (state === "complete")'),
            "the complete branch is tested before the rejected check, so a "
            "refused archive still gets a Download button")

    def test_the_server_still_serves_the_field(self):
        """The other half of the contract. A display fix is worthless if the
        field ever stops being sent, and that would break silently."""
        config.fetch_queue = {
            "req1": {"bot": "evilbot", "state": "complete", "requested_at": 1.0,
                     "list_processing_error": "zip entry would extract outside"},
        }
        rows = webserver.build_fetch_status_payload()
        self.assertEqual(rows[0]["list_processing_error"],
                         "zip entry would extract outside")


if __name__ == "__main__":
    unittest.main()
