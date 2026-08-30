"""webserver.py's build_*_payload() functions.

Deliberately exercises only the pure, Flask-free half of webserver.py:
build_queue_payload(), build_search_payload() and build_filelists_payload()
never import flask, which is what lets this file run - like the rest of the
suite - with nothing but the standard library. create_app()/start() are the
Flask-gated half and are only smoke-tested here for the "Flask is missing"
and "disabled via config" paths, which must never raise regardless of whether
Flask happens to be installed in the environment running this file.
"""

import ast
import importlib
import io
import os
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if os.path.join(REPO_ROOT, "tests") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import adminchat  # noqa: E402
import commands  # noqa: E402
import config  # noqa: E402
import list as list_mod  # noqa: E402
import settings_file  # noqa: E402
import webserver  # noqa: E402

from tests.support import DCCoreTestCase, queue_row  # noqa: E402

WEBUI_TEST_PASSWORD = "test-password"


def log_in_test_client(client, password=WEBUI_TEST_PASSWORD):
    """POST the login form on a Flask test client, the same way a browser
    would - so a protected route's test reaches the code under test instead
    of the 401/redirect require_login() now puts in front of every route.
    Low iteration count purely for test speed; see test_adminchat.py for the
    same pattern.

    Clears webserver._web_bad_ips first: every Flask test client presents the
    same synthetic remote_addr, so a block left over from an earlier test in
    the same process (module-level state, not reset between tests otherwise)
    would fail a login this call has no reason to expect to fail."""
    webserver._web_bad_ips.clear()
    resp = client.post("/login", data={"password": password})
    assert resp.status_code == 302, "test login failed - fixture's own password/hash disagree"
    return resp


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


def _quiet_reload_config():
    """importlib.reload(config), with its own "[CONFIG] Applied N setting(s)"
    print() suppressed - the same pattern tests/test_runtime_state.py uses
    for the same call. Used below as a safe, config.py-only stand-in for what
    a real rehash would do to pick a freshly-saved setting.conf value back up
    (real commands.handle_rehash_request() additionally reloads dcc/announce/
    security/db/stats_mgr, none of which any test in this suite reloads for
    real - this suite's only precedent, test_runtime_state.py, reloads
    config.py alone too, for the same reason), and again in cleanup to put
    every setting back at its tracked default once the temp settings.conf
    that changed it is gone (DCCoreTestCase.tearDown() deletes it before any
    addCleanup callback runs, so a reload at that point sees no override file
    at all)."""
    with redirect_stdout(io.StringIO()):
        importlib.reload(config)


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


class FetchStatusPayloadConcurrencyTests(DCCoreTestCase):
    """build_fetch_status_payload() used to read config.fetch_queue with no
    lock at all, while dcc_fetch.check_fetch_queue() has always promoted a
    row to 'offered' under dcc_fetch._fetch_lock() as TWO separate
    statements - `row["state"] = "offered"` then `row["offered_at"] = now`
    (dcc_fetch.py, promoting a pending row). Every existing reader of the
    status payload treats state=="offered" and offered_at being set as a
    package - the dashboard shows "waiting on <bot>" only once an offer
    time exists - so a read landing between those two statements used to be
    able to observe "offered" with offered_at still None, a torn pair no
    valid row can actually be in.

    This races a writer performing exactly that two-step promotion, over
    and over, against build_fetch_status_payload(), asserting the pair is
    never torn - then proves the identical writer reliably produces a torn
    read when the read is left unlocked (the pre-fix shape), confirming the
    passing test depends on the fix rather than being compatible with it by
    chance.
    """

    ROUNDS = 5000

    def setUp(self):
        super().setUp()
        import dcc_fetch
        self.rid = dcc_fetch.enqueue_fetch("racebot", "Song.flac")

    def _promote_and_revert(self, stop):
        """The exact two statements dcc_fetch.check_fetch_queue() uses to
        promote a pending row, under the real lock, followed by a revert so
        the row can be promoted again on the next round."""
        import dcc_fetch
        row = config.fetch_queue[self.rid]
        i = 0
        while not stop.is_set() and i < self.ROUNDS:
            with dcc_fetch._fetch_lock():
                row["state"] = "offered"
                row["offered_at"] = time.time()
            with dcc_fetch._fetch_lock():
                row["state"] = "pending"
                row["offered_at"] = None
            i += 1
        stop.set()

    def test_concurrent_promotion_and_status_payload_reads_never_see_a_torn_pair(self):
        stop = threading.Event()
        torn = []

        def reader():
            while not stop.is_set():
                for row in webserver.build_fetch_status_payload():
                    if row["state"] == "offered" and row["offered_at"] is None:
                        torn.append(dict(row))

        writer_thread = threading.Thread(target=self._promote_and_revert, args=(stop,), daemon=True)
        reader_thread = threading.Thread(target=reader, daemon=True)
        writer_thread.start()
        reader_thread.start()
        writer_thread.join(timeout=30)
        stop.set()
        reader_thread.join(timeout=10)

        self.assertFalse(writer_thread.is_alive(), "writer thread never finished - possible deadlock")
        self.assertFalse(reader_thread.is_alive(), "reader thread never finished - possible deadlock")
        self.assertEqual(torn, [],
                         f"observed {len(torn)} torn read(s) of state=='offered' with "
                         f"offered_at still None, despite the shared lock: {torn[:5]}")

    def test_without_the_lock_the_same_workload_produces_a_torn_pair(self):
        """Control: the identical promote/revert workload, with the read
        left unlocked (the pre-fix shape of build_fetch_status_payload()),
        reliably observes the torn pair - proving the test above is not
        vacuously passing.

        sys.setswitchinterval() is turned down for this test only, forcing
        far more frequent thread switches - the default 5ms interval
        otherwise lets the whole promote-then-revert workload finish inside
        one timeslice often enough that this control can pass by luck even
        though nothing here is actually synchronised.
        """
        old_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        self.addCleanup(sys.setswitchinterval, old_interval)

        stop = threading.Event()
        torn = []

        def unlocked_reader():
            while not stop.is_set():
                queue = dict(getattr(config, "fetch_queue", {}) or {})
                for row in queue.values():
                    row_out = dict(row)
                    if row_out["state"] == "offered" and row_out["offered_at"] is None:
                        torn.append(row_out)

        writer_thread = threading.Thread(target=self._promote_and_revert, args=(stop,), daemon=True)
        reader_thread = threading.Thread(target=unlocked_reader, daemon=True)
        writer_thread.start()
        reader_thread.start()
        writer_thread.join(timeout=30)
        stop.set()
        reader_thread.join(timeout=10)

        self.assertTrue(len(torn) > 0,
                        "the unlocked control workload never observed a torn pair - "
                        "this test would no longer prove the fix prevents anything; "
                        "it needs a heavier workload")


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
        self.set_config(ADMIN_PASSWORD_HASH=adminchat.make_password_hash(
            WEBUI_TEST_PASSWORD, iterations=1000))
        self.app = webserver.create_app()
        self.client = self.app.test_client()
        log_in_test_client(self.client)

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
class LoginGateTests(DCCoreTestCase):
    """require_login() and the /login, /logout routes it exempts itself from.

    Every route in create_app() sits behind this gate now - these tests are
    the ones that actually exercise logging in and out, rather than assuming
    the fixtures elsewhere (which all log in during setUp) prove it works."""

    def setUp(self):
        super().setUp()
        self.set_config(ADMIN_PASSWORD_HASH=adminchat.make_password_hash(
            WEBUI_TEST_PASSWORD, iterations=1000))
        self.app = webserver.create_app()
        self.client = self.app.test_client()
        # Module-level state, not reset between tests otherwise, and every
        # Flask test client in this process presents the same synthetic
        # remote_addr - so a block one test method leaves behind would reach
        # the next one regardless of run order.
        webserver._web_bad_ips.clear()

    def test_an_unauthenticated_page_request_redirects_to_login(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/login"))

    def test_an_unauthenticated_api_request_gets_json_401_not_a_redirect(self):
        """An API caller (fetch(), not a browser navigation) wants a status
        code and a body it can branch on, not a 302 to an HTML page."""
        resp = self.client.get("/api/queue")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("error", resp.get_json())

    def test_the_login_page_itself_is_reachable_unauthenticated(self):
        """Defect guard: require_login() must exempt exactly this route, or
        an unauthenticated visitor could never reach the form that logs them
        in - the site would refuse everyone, permanently, including its own
        operator."""
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Admin password", resp.data)

    def test_the_right_password_logs_in_and_reaches_protected_routes(self):
        log_in_test_client(self.client, WEBUI_TEST_PASSWORD)

        resp = self.client.get("/api/queue")
        self.assertEqual(resp.status_code, 200)

    def test_the_wrong_password_is_refused_and_leaves_routes_locked(self):
        resp = self.client.post("/login", data={"password": "not-it"})
        self.assertEqual(resp.status_code, 401)
        self.assertIn(b"Incorrect password", resp.data)

        still_locked = self.client.get("/api/queue")
        self.assertEqual(still_locked.status_code, 401)

    def test_repeated_failures_block_the_address_even_with_the_right_password(self):
        """The same password guards the DCC CHAT console (see adminchat.py's
        own _bad_ips), so this form must not be a fourth, unthrottled way to
        guess it. adminchat.MAX_PASSWORD_ATTEMPTS wrong attempts trip the
        block; the very next request is refused even when it finally supplies
        the correct password."""
        for _ in range(adminchat.MAX_PASSWORD_ATTEMPTS):
            self.client.post("/login", data={"password": "not-it"})

        resp = self.client.post("/login", data={"password": WEBUI_TEST_PASSWORD})
        self.assertEqual(resp.status_code, 401)
        self.assertIn(b"Too many failed attempts", resp.data)

    def test_the_web_block_pool_is_separate_from_admin_consoles(self):
        """Defect guard: sharing one counter with adminchat.py's DCC console
        would let a web-side attacker spend down the operator's own block
        budget there too - a denial of service against the console, using
        nothing but repeated wrong guesses through this HTTP form."""
        for _ in range(adminchat.MAX_PASSWORD_ATTEMPTS):
            self.client.post("/login", data={"password": "not-it"})

        self.assertFalse(adminchat.is_bad_ip("127.0.0.1"),
                         "the DCC console's own block pool must be untouched")

    def test_the_session_cookie_is_samesite_lax(self):
        """One of the mutating routes behind this login sends a real @find
        into a channel. SameSite=Lax is what stops a plain cross-site POST
        from riding in on it - left to the app rather than a browser default."""
        resp = self.client.post("/login", data={"password": WEBUI_TEST_PASSWORD})
        cookie_headers = resp.headers.getlist("Set-Cookie")
        self.assertTrue(any("SameSite=Lax" in h for h in cookie_headers),
                        cookie_headers)

    def test_logout_clears_the_session_and_relocks_every_route(self):
        log_in_test_client(self.client, WEBUI_TEST_PASSWORD)
        self.assertEqual(self.client.get("/api/queue").status_code, 200)

        logout_resp = self.client.post("/logout")
        self.assertEqual(logout_resp.status_code, 302)
        self.assertTrue(logout_resp.headers["Location"].endswith("/login"))

        self.assertEqual(self.client.get("/api/queue").status_code, 401)

    def test_a_static_asset_is_also_behind_the_gate(self):
        """The docstring's claim - "including static assets" - would be false
        if web/style.css slipped past require_login() by being served through
        Flask's separate "static" endpoint rather than a named @app.route."""
        resp = self.client.get("/style.css")
        self.assertEqual(resp.status_code, 302)

        log_in_test_client(self.client, WEBUI_TEST_PASSWORD)
        resp = self.client.get("/style.css")
        self.assertEqual(resp.status_code, 200)


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
        self.set_config(ADMIN_PASSWORD_HASH=adminchat.make_password_hash(
            WEBUI_TEST_PASSWORD, iterations=1000))
        self.app = webserver.create_app()
        self.client = self.app.test_client()
        log_in_test_client(self.client)

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
        # Restores absence as absence. Defaulting to True here would have the
        # cleanup CREATE the switch, set to the value this PR exists to stop
        # anything assuming.
        missing = object()
        real_enabled = getattr(config, "WEBUI_ENABLED", missing)
        config.WEBUI_ENABLED = False

        def restore_enabled():
            if real_enabled is missing:
                if hasattr(config, "WEBUI_ENABLED"):
                    del config.WEBUI_ENABLED
            else:
                config.WEBUI_ENABLED = real_enabled

        self.addCleanup(restore_enabled)

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




class FetchRoutesRefuseWhenTheFeatureIsOff(DCCoreTestCase):
    """The two routes that CREATE fetch rows have to honour the same flag
    dcc_fetch.check_fetch_queue() honours.

    oserve.startup() sets config.fetch_feature_disabled when
    FETCHED_FILES_DIR could not be created, and the dispatcher then refuses to
    promote any row past `pending`. Before this, the HTTP routes accepted the
    request anyway: the dashboard reported the fetch queued, and it sat there
    forever with nothing said about why. One [FETCH] line on the console at
    startup was the only evidence.
    """

    def _unset_the_flag(self):
        """Remove the attribute and put it back afterwards. Deleting module
        state in a test that does not restore it leaves the next test to
        discover it, which is the kind of order-dependence that only shows up
        when the suite is run in a different order."""
        had = hasattr(config, "fetch_feature_disabled")
        previous = getattr(config, "fetch_feature_disabled", None)
        if had:
            del config.fetch_feature_disabled

        def restore():
            if had:
                config.fetch_feature_disabled = previous
            elif hasattr(config, "fetch_feature_disabled"):
                del config.fetch_feature_disabled

        self.addCleanup(restore)

    def test_a_file_fetch_is_refused_rather_than_queued(self):
        config.fetch_feature_disabled = True

        status, result = webserver.build_fetch_enqueue_result(
            {"bot": "goodbot", "filename": "Song.flac"})

        self.assertEqual(status, 503)
        self.assertIn("FETCHED_FILES_DIR", result["error"])
        self.assertEqual(config.fetch_queue, {},
                         "the row must not be created - it could never be promoted")

    def test_a_list_fetch_is_refused_rather_than_queued(self):
        config.fetch_feature_disabled = True

        status, result = webserver.build_list_fetch_enqueue_result("goodbot")

        self.assertEqual(status, 503)
        self.assertIn("FETCHED_FILES_DIR", result["error"])
        self.assertEqual(config.fetch_queue, {})

    def test_a_multi_item_request_creates_none_of_them(self):
        """The bulk shape has its own path through the validator, so it gets
        its own check: partial acceptance would be worse than refusal."""
        config.fetch_feature_disabled = True

        status, _result = webserver.build_fetch_enqueue_result([
            {"bot": "a", "filename": "One.flac"},
            {"bot": "b", "filename": "Two.flac"},
        ])

        self.assertEqual(status, 503)
        self.assertEqual(config.fetch_queue, {})

    def test_the_error_says_what_to_do_about_it(self):
        """An operator reading this in the dashboard has to be able to act on
        it - the cause is a directory that could not be created, and the fix
        needs a restart once that is sorted."""
        config.fetch_feature_disabled = True

        message = webserver.fetch_feature_error()

        self.assertIn("permissions", message.lower())
        self.assertIn("restart", message.lower())

    def test_both_routes_still_work_when_the_feature_is_on(self):
        """Control. reset_config() leaves the flag False, which is the normal
        running state - these must not have been broken by the gate."""
        config.fetch_feature_disabled = False

        file_status, _f = webserver.build_fetch_enqueue_result(
            {"bot": "goodbot", "filename": "Song.flac"})
        list_status, _l = webserver.build_list_fetch_enqueue_result("goodbot")

        self.assertEqual((file_status, list_status), (200, 200))
        self.assertEqual(len(config.fetch_queue), 2)

    def test_the_flag_being_absent_counts_as_off(self):
        """The reason the read is `getattr(..., True)` rather than the
        `getattr(..., False)` it used to be.

        The attribute exists only once oserve.startup() has set it, so reading
        it missing means asking before that point. Accepting a fetch with
        nowhere to put the file is the worse of the two guesses - and #100
        would start the dashboard earlier in the boot sequence than the line
        that sets this, which turns "before that point" from unreachable into
        an ordinary startup window.
        """
        self._unset_the_flag()

        self.assertIsNotNone(webserver.fetch_feature_error(),
                             "a missing flag must read as disabled, not enabled")

        status, _result = webserver.build_fetch_enqueue_result(
            {"bot": "goodbot", "filename": "Song.flac"})

        self.assertEqual(status, 503)
        self.assertEqual(config.fetch_queue, {})

    def test_the_dispatcher_agrees_with_the_routes(self):
        """Both sides of the same flag: the routes refuse to create the row,
        and the dispatcher refuses to promote one that already exists. They
        have to read the missing attribute the same way, or a row created
        under one reading gets stranded by the other."""
        import dcc_fetch

        # Create the row through the real path, with the feature on, so it has
        # exactly the shape the dispatcher expects.
        config.fetch_feature_disabled = False
        request_id = dcc_fetch.enqueue_fetch("somebot", "Song.flac")

        self._unset_the_flag()
        dcc_fetch.check_fetch_queue()

        self.assertEqual(config.fetch_queue[request_id]["state"], "pending",
                         "the dispatcher promoted a row while the feature "
                         "reads as disabled")




class TheDashboardSwitchFailsClosed(unittest.TestCase):
    """config.py ships `WEBUI_ENABLED = False` and `WEBUI_HOST = "127.0.0.1"`,
    and states why in as many words: a network-facing surface that "should
    never be on just because someone pulled and restarted" (WEBUI_ENABLED),
    put only on loopback unless the operator explicitly widens it
    (WEBUI_HOST) - now on top of the login gate in webserver.py, not instead
    of it.

    Both readers used to fall back to `True` and `"0.0.0.0"` when the name was
    absent. So a missing switch did not just fail to honour the shipped
    default - it put the dashboard on every interface, which is the strongest
    possible inversion of what config.py promises.

    Absent is reachable rather than theoretical: a bare annotation binds no
    name at all, and #100's mandatory-settings work is precisely about
    removing shipped values from settings.
    """

    TEST_PASSWORD_HASH = "pbkdf2_sha256$1000$00$00"  # never actually verified in this class

    def setUp(self):
        self._real_flask = webserver.HAVE_FLASK
        webserver.HAVE_FLASK = True
        self.addCleanup(lambda: setattr(webserver, "HAVE_FLASK", self._real_flask))
        # Replaced for EVERY test in this class, not only the ones that inspect
        # the bind. These drive start() directly, so if the gate under test
        # regresses the real Flask app binds a socket and blocks the whole
        # suite - which is exactly what happened while mutation-testing this
        # change. A test must not be able to start a live listener because the
        # code it is testing broke.
        self.recorded = self._record_run()

    def _unset(self, name):
        """Remove a config attribute for one test and put it back exactly as it
        was - including putting back its absence, if it was absent."""
        missing = object()
        previous = getattr(config, name, missing)
        if previous is not missing:
            delattr(config, name)

        def restore():
            if previous is missing:
                if hasattr(config, name):
                    delattr(config, name)
            else:
                setattr(config, name, previous)

        self.addCleanup(restore)

    def _set(self, name, value):
        self._unset(name)
        setattr(config, name, value)

    def _record_run(self):
        """Stand in for create_app() so start() can be driven all the way to
        the bind without a socket ever being opened."""
        recorded = {}

        class FakeApp:
            def run(self, **kwargs):
                recorded.update(kwargs)

        missing = object()
        previous = getattr(webserver, "create_app", missing)
        webserver.create_app = lambda: FakeApp()

        def restore():
            if previous is missing:
                del webserver.create_app
            else:
                webserver.create_app = previous

        self.addCleanup(restore)
        return recorded

    def test_an_absent_switch_reads_as_disabled(self):
        self._unset("WEBUI_ENABLED")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            webserver.start()

        self.assertIn("Disabled via config.WEBUI_ENABLED", buffer.getvalue(),
                      "a missing switch started an unauthenticated listener")

    def test_an_absent_host_binds_loopback_not_every_interface(self):
        """0.0.0.0 would put the dashboard on the LAN. Loopback is what
        config.py ships, so loopback is what a missing value means."""
        self._set("WEBUI_ENABLED", True)
        self._set("ADMIN_PASSWORD_HASH", self.TEST_PASSWORD_HASH)
        self._unset("WEBUI_HOST")

        with redirect_stdout(io.StringIO()):
            webserver.start()

        self.assertEqual(self.recorded.get("host"), "127.0.0.1")

    def test_an_explicit_host_is_still_honoured(self):
        """Control: failing closed is about the ABSENT case. An operator who
        deliberately binds every interface must still get that."""
        self._set("WEBUI_ENABLED", True)
        self._set("ADMIN_PASSWORD_HASH", self.TEST_PASSWORD_HASH)
        self._set("WEBUI_HOST", "0.0.0.0")

        with redirect_stdout(io.StringIO()):
            webserver.start()

        self.assertEqual(self.recorded.get("host"), "0.0.0.0")

    def test_the_switch_being_on_still_starts_it(self):
        """Control: the gate must not have been welded shut."""
        self._set("WEBUI_ENABLED", True)
        self._set("ADMIN_PASSWORD_HASH", self.TEST_PASSWORD_HASH)

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            webserver.start()

        self.assertNotIn("Disabled via", buffer.getvalue())
        self.assertIn("port", self.recorded)

    def test_an_unset_password_hash_refuses_to_start_even_when_enabled(self):
        """The dashboard must never be reachable with no way to log in.
        WEBUI_ENABLED alone is not enough of a gate any more - a password has
        to actually be configured, or start() refuses regardless."""
        self._set("WEBUI_ENABLED", True)
        self._set("ADMIN_PASSWORD_HASH", "")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            webserver.start()

        self.assertIn("ADMIN_PASSWORD_HASH is not set", buffer.getvalue())
        self.assertEqual(self.recorded, {}, "app.run() must not have been reached")


class WebuiFallbacksMatchWhatConfigShips(unittest.TestCase):
    """Every `getattr(config, "WEBUI_*", <default>)` must use the value
    config.py actually ships.

    Derived from the source rather than listed by hand, so a WEBUI_* setting
    added later is covered without anyone remembering to add it here.

    A fallback that disagrees with the shipped default is a second, invisible
    default that applies only when something has already gone wrong - which is
    exactly the moment the permissive answer is the one you least want. These
    four gate a network listener with no authentication on it.
    """

    SOURCES = ("oserve.py", "webserver.py")

    def shipped_defaults(self):
        path = os.path.join(REPO_ROOT, "config.py")
        with io.open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        shipped = {}
        for node in tree.body:
            target = value = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target, value = node.target.id, node.value
            elif (isinstance(node, ast.Assign) and len(node.targets) == 1
                  and isinstance(node.targets[0], ast.Name)):
                target, value = node.targets[0].id, node.value
            if target and value is not None:
                try:
                    shipped[target] = ast.literal_eval(value)
                except (ValueError, TypeError, SyntaxError):
                    pass
        return shipped

    def fallbacks(self):
        """[(file, line, name, fallback), ...] for every WEBUI_* getattr."""
        found = []
        for filename in self.SOURCES:
            path = os.path.join(REPO_ROOT, filename)
            with io.open(path, encoding="utf-8") as handle:
                source = handle.read()
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call):
                    continue
                if not (isinstance(node.func, ast.Name) and node.func.id == "getattr"):
                    continue
                if len(node.args) != 3:
                    continue
                target, name, default = node.args
                if not (isinstance(target, ast.Name) and target.id == "config"):
                    continue
                if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
                    continue
                if not name.value.startswith("WEBUI_"):
                    continue
                try:
                    found.append((filename, node.lineno, name.value,
                                  ast.literal_eval(default)))
                except (ValueError, TypeError, SyntaxError):
                    continue
        return found

    def test_every_webui_fallback_equals_the_shipped_default(self):
        shipped = self.shipped_defaults()

        wrong = []
        for filename, lineno, name, fallback in self.fallbacks():
            if name not in shipped:
                continue
            if shipped[name] != fallback or type(shipped[name]) is not type(fallback):
                wrong.append(f"{filename}:{lineno} {name} ships {shipped[name]!r} "
                             f"but falls back to {fallback!r}")

        self.assertEqual(
            wrong, [],
            "these fallbacks contradict config.py, so a missing setting takes "
            "a different path from the one shipped: " + "; ".join(wrong))

    def test_the_scan_finds_the_call_sites_it_is_meant_to_check(self):
        """Fixture invariant. If the scan stops matching - because the code
        adopts a shape it does not recognise - the test above would pass while
        checking nothing, which is the failure it exists to prevent."""
        found = self.fallbacks()

        self.assertGreaterEqual(
            len(found), 3,
            f"only {len(found)} WEBUI_* fallback(s) found across "
            f"{', '.join(self.SOURCES)}; the scan has probably stopped "
            f"recognising the shape rather than the code having stopped using it")
        self.assertIn("WEBUI_ENABLED", [name for _f, _l, name, _d in found])


class SettingsPayloadTests(DCCoreTestCase):
    """webserver.build_settings_payload()/apply_settings_changes()/
    build_password_change_result() - the pure logic behind
    GET/POST /api/settings and POST /api/settings/password. No Flask
    required; see the module docstring."""

    def setUp(self):
        super().setUp()
        self.settings_path = os.path.join(self.make_tree().root, "settings.conf")
        os.environ["DCCORE_SETTINGS_FILE"] = self.settings_path
        self.addCleanup(os.environ.pop, "DCCORE_SETTINGS_FILE", None)
        # Every successful apply_settings_changes()/build_password_change_result()
        # call below dispatches commands.handle_rehash_request() on its own
        # thread (see apply_settings_changes()'s docstring) - unmocked, that
        # is the REAL production rehash: it reloads config/dcc/announce/
        # security/db/stats_mgr for real, on a thread this test does not wait
        # for, which then races the NEXT test in this process. Defaulting it
        # to a no-op here keeps every test in this class isolated; the two
        # tests that actually need to observe rehash behaviour install their
        # own function on top of this one, and this cleanup (registered
        # first, so it runs LAST) always puts the true original back.
        real_rehash = commands.handle_rehash_request
        commands.handle_rehash_request = lambda *a, **kw: None
        self.addCleanup(setattr, commands, "handle_rehash_request", real_rehash)

    def test_admin_password_hash_never_appears_as_a_field(self):
        self.set_config(ADMIN_PASSWORD_HASH=adminchat.make_password_hash("x", iterations=1000))
        payload = webserver.build_settings_payload()
        names = {f["name"] for c in payload["categories"] for f in c["fields"]}
        self.assertNotIn("ADMIN_PASSWORD_HASH", names)

    def test_admin_password_set_reflects_an_empty_hash(self):
        self.set_config(ADMIN_PASSWORD_HASH="")
        payload = webserver.build_settings_payload()
        self.assertFalse(payload["admin_password_set"])

    def test_admin_password_set_reflects_a_real_hash(self):
        self.set_config(ADMIN_PASSWORD_HASH=adminchat.make_password_hash("x", iterations=1000))
        payload = webserver.build_settings_payload()
        self.assertTrue(payload["admin_password_set"])

    def test_every_declared_overridable_setting_appears_in_exactly_one_category(self):
        """Completeness guard against SETTINGS_CATEGORIES going stale: every
        setting config.py declares and settings_file.is_overridable() accepts
        - other than ADMIN_PASSWORD_HASH, which deliberately never appears as
        a field - must be slotted into exactly one category. A setting that
        fell through would either be silently missing from the page, or land
        twice if a name were copy-pasted into two categories by mistake."""
        types = settings_file.declared_types(vars(config))
        expected = {name for name in types
                    if name != "ADMIN_PASSWORD_HASH"
                    and settings_file.is_overridable(name, getattr(config, name, None))}

        payload = webserver.build_settings_payload()
        seen = [f["name"] for c in payload["categories"] for f in c["fields"]]

        self.assertEqual(sorted(seen), sorted(set(seen)), "a setting appears in more than one category")
        self.assertEqual(set(seen), expected)
        self.assertNotIn("other", [c["id"] for c in payload["categories"]],
                          "a real setting fell through to the catch-all category - "
                          "add it to webserver.SETTINGS_CATEGORIES")

    def test_a_runtime_only_name_never_appears(self):
        self.set_config(ORIGINAL_NICK="X")
        payload = webserver.build_settings_payload()
        names = {f["name"] for c in payload["categories"] for f in c["fields"]}
        self.assertNotIn("ORIGINAL_NICK", names)

    def test_apply_settings_changes_writes_and_returns_200(self):
        status, result = webserver.apply_settings_changes({"MAX_DCC_SLOTS": "9"})
        self.assertEqual(status, 200)
        self.assertEqual(result["rehash"], "started")
        self.assertIn("MAX_DCC_SLOTS", result["written"])
        with io.open(self.settings_path, encoding="utf-8") as handle:
            entries = settings_file.parse(handle.read())
        self.assertEqual(entries["MAX_DCC_SLOTS"], "9")

    def test_apply_settings_changes_does_not_wait_for_the_rehash_thread(self):
        """Same shape as BroadcastSearchTests.test_it_never_blocks_the_caller_
        for_the_window_duration: commands.handle_rehash_request() must run on
        its own thread, not the request thread, or a save would freeze the
        dashboard for as long as the real rehash (socket I/O, several
        importlib.reload()s) takes."""
        real_rehash = commands.handle_rehash_request
        finished = threading.Event()

        def slow_rehash(*_args, **_kwargs):
            time.sleep(0.3)
            finished.set()

        commands.handle_rehash_request = slow_rehash
        self.addCleanup(setattr, commands, "handle_rehash_request", real_rehash)

        started = time.time()
        status, _result = webserver.apply_settings_changes({"MAX_DCC_SLOTS": "9"})
        elapsed = time.time() - started

        self.assertEqual(status, 200)
        self.assertLess(elapsed, 0.1)
        self.assertFalse(finished.is_set(), "the calling thread must return before the rehash finishes")
        self.assertTrue(finished.wait(timeout=2), "the dispatched rehash thread never actually ran")

    def test_apply_settings_changes_rejects_the_password_hash_directly(self):
        status, result = webserver.apply_settings_changes({"ADMIN_PASSWORD_HASH": "x"})
        self.assertEqual(status, 400)
        self.assertIn("error", result)
        self.assertFalse(os.path.exists(self.settings_path))

    def test_apply_settings_changes_rejects_an_unknown_setting(self):
        status, result = webserver.apply_settings_changes({"NOT_A_REAL_SETTING": "x"})
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_webui_port_is_flagged_as_restart_required(self):
        status, result = webserver.apply_settings_changes({"WEBUI_PORT": "9000"})
        self.assertEqual(status, 200)
        self.assertIn("WEBUI_PORT", result["restart_required"])

    def test_nickname_is_not_flagged_as_restart_required(self):
        status, result = webserver.apply_settings_changes({"NICKNAME": "X"})
        self.assertEqual(status, 200)
        self.assertNotIn("NICKNAME", result["restart_required"])

    def test_password_change_rejects_an_empty_password(self):
        status, result = webserver.build_password_change_result("", "")
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_password_change_rejects_a_mismatched_confirmation(self):
        status, result = webserver.build_password_change_result("newpass1", "newpass2")
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_password_change_applies_after_the_dispatched_rehash_runs(self):
        """settings_file.save() only writes settings.conf - it never touches
        config's own attributes (see its docstring's "THE FILE IS THE
        OPERATOR'S, NOT OURS" section) - so ADMIN_PASSWORD_HASH only actually
        changes once a rehash re-reads the file. See _quiet_reload_config()'s
        docstring for why this patches in a config-only reload rather than
        letting the real commands.handle_rehash_request() run."""
        real_rehash = commands.handle_rehash_request
        done = threading.Event()

        def reload_config_only(*_args, **_kwargs):
            _quiet_reload_config()
            done.set()

        commands.handle_rehash_request = reload_config_only
        self.addCleanup(setattr, commands, "handle_rehash_request", real_rehash)
        self.addCleanup(_quiet_reload_config)

        status, result = webserver.build_password_change_result("newpass1", "newpass1")
        self.assertEqual(status, 200)
        self.assertTrue(done.wait(timeout=2), "the dispatched rehash never ran")
        self.assertTrue(adminchat.verify_password(config.ADMIN_PASSWORD_HASH, "newpass1"))


@unittest.skipUnless(webserver.HAVE_FLASK, "Flask not installed - see the module docstring: "
                                            "CI never installs it, this class only runs when it "
                                            "happens to be available locally")
class SettingsHttpRouteTests(DCCoreTestCase):
    """GET/POST /api/settings and POST /api/settings/password, end to end
    through the real Flask app - same setUp shape as
    FilelistsHttpPaginationTests."""

    def setUp(self):
        super().setUp()
        self.settings_path = os.path.join(self.make_tree().root, "settings.conf")
        os.environ["DCCORE_SETTINGS_FILE"] = self.settings_path
        self.addCleanup(os.environ.pop, "DCCORE_SETTINGS_FILE", None)
        # See SettingsPayloadTests.setUp()'s comment: unmocked, a successful
        # save dispatches the REAL commands.handle_rehash_request() on its
        # own thread, which this test does not wait for and would otherwise
        # race every test that runs after it in this process.
        real_rehash = commands.handle_rehash_request
        commands.handle_rehash_request = lambda *a, **kw: None
        self.addCleanup(setattr, commands, "handle_rehash_request", real_rehash)
        self.set_config(ADMIN_PASSWORD_HASH=adminchat.make_password_hash(
            WEBUI_TEST_PASSWORD, iterations=1000))
        self.app = webserver.create_app()
        self.client = self.app.test_client()
        log_in_test_client(self.client)

    def test_get_settings_returns_the_expected_shape(self):
        resp = self.client.get("/api/settings")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn("categories", body)
        self.assertTrue(body["admin_password_set"])
        names = {f["name"] for c in body["categories"] for f in c["fields"]}
        self.assertIn("MAX_DCC_SLOTS", names)
        self.assertNotIn("ADMIN_PASSWORD_HASH", names)

    def test_post_settings_writes_the_real_file_end_to_end(self):
        resp = self.client.post("/api/settings", json={"MAX_DCC_SLOTS": "7"})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["rehash"], "started")
        self.assertIn("MAX_DCC_SLOTS", body["written"])
        with io.open(self.settings_path, encoding="utf-8") as handle:
            entries = settings_file.parse(handle.read())
        self.assertEqual(entries["MAX_DCC_SLOTS"], "7")

    def test_post_settings_password_then_logs_in_with_the_new_password(self):
        real_rehash = commands.handle_rehash_request
        done = threading.Event()

        def reload_config_only(*_args, **_kwargs):
            _quiet_reload_config()
            done.set()

        commands.handle_rehash_request = reload_config_only
        self.addCleanup(setattr, commands, "handle_rehash_request", real_rehash)
        self.addCleanup(_quiet_reload_config)

        resp = self.client.post("/api/settings/password", json={
            "new_password": "newpass1", "confirm_password": "newpass1",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(done.wait(timeout=2), "the dispatched rehash never ran")

        webserver._web_bad_ips.clear()
        fresh_client = self.app.test_client()
        login_resp = fresh_client.post("/login", data={"password": "newpass1"})
        self.assertEqual(login_resp.status_code, 302)


if __name__ == "__main__":
    unittest.main()
