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
        rows = webserver.build_filelists_payload()
        titles = sorted(r["title"] for r in rows)
        self.assertEqual(titles, [
            "00 - Intro.flac", "01 - Enter Sandman.flac",
            "01 - Fuel.flac", "02 - Sad But True.flac",
        ])

    def test_filelists_rows_have_format_and_source(self):
        rows = {r["title"]: r for r in webserver.build_filelists_payload()}
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

    def test_a_clean_broadcast_still_works_via_http(self):
        """The CRLF/type checks must not false-positive on ordinary input."""
        resp = self.client.post("/api/search/broadcast", json={"term": "sandman"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(self.oserve.queued), 1)

    def test_a_clean_fetch_enqueue_still_works_via_http(self):
        resp = self.client.post("/api/fetch/enqueue", json={"bot": "goodbot", "filename": "Song.flac"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.get_json()["created"]), 1)


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


if __name__ == "__main__":
    unittest.main()
