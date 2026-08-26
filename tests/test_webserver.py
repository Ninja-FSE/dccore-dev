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
