"""list_fetch.py - safe extraction and parsing of another bot's fetched
master-list zip.

Covers, per the brief: a zip containing a path-traversal entry is rejected
without writing anything outside the intended extraction directory; a zip
whose declared uncompressed size exceeds the sane cap is rejected before
extracting anything; a zip with no recognisable list file inside is handled
gracefully (no crash, a clear (False, reason) result); an ambiguous zip (more
than one candidate .txt) picks the largest and does not crash; and the happy
path parses real entries via list.py's existing pipeline.

Deliberately stdlib-only, like the rest of the suite.
"""

import io
import os
import sys
import unittest
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import config  # noqa: E402
import list_fetch  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


def _zip_bytes(members):
    """members: [(arcname, data_bytes_or_str), ...]. Returns raw zip bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in members:
            if isinstance(data, str):
                data = data.encode("utf-8")
            zf.writestr(arcname, data)
    return buf.getvalue()


def _write_zip(path, members):
    with open(path, "wb") as f:
        f.write(_zip_bytes(members))


def _list_txt(base_name="OtherBot", files=(("Track One.flac", "10.0MB"),)):
    lines = [
        "List of 1 Files (10.0MB) generated on Jan 1st\n",
        f"To request a file, copy/paste to the channel... !{base_name} FILENAME\n\n\n",
        "\n" + "=" * 53 + "\n",
        "D:\\MUSIC\\SomeAlbum\\\n",
        "=" * 53 + "\n",
    ]
    for filename, size in files:
        lines.append(f"!{base_name} {filename}  ::INFO:: {size}\n")
    return "".join(lines)


class SafeExtractionTests(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-listfetch-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp
        self.zip_path = os.path.join(self.tmp, "incoming.zip")

    def test_happy_path_extracts_and_parses(self):
        _write_zip(self.zip_path, [("OtherBot-2026-08-27.txt", _list_txt())])

        ok, reason = list_fetch.process_fetched_list_zip("otherbot", self.zip_path)

        self.assertTrue(ok, reason)
        self.assertIsNone(reason)
        entry = config.fetched_bot_lists["otherbot"]
        self.assertEqual(entry["bot"], "otherbot")
        titles = [row["title"] for row in entry["entries"]]
        self.assertEqual(titles, ["Track One.flac"])
        self.assertEqual(entry["entries"][0]["size"], "10.0MB")
        self.assertEqual(entry["entries"][0]["source"], "otherbot")

    def test_a_second_fetch_for_the_same_bot_replaces_the_first(self):
        _write_zip(self.zip_path, [("OtherBot-2026-08-27.txt", _list_txt(files=(("First.flac", "1.0MB"),)))])
        list_fetch.process_fetched_list_zip("otherbot", self.zip_path)

        _write_zip(self.zip_path, [("OtherBot-2026-08-28.txt", _list_txt(files=(("Second.flac", "2.0MB"),)))])
        list_fetch.process_fetched_list_zip("otherbot", self.zip_path)

        self.assertEqual(len(config.fetched_bot_lists), 1)
        titles = [row["title"] for row in config.fetched_bot_lists["otherbot"]["entries"]]
        self.assertEqual(titles, ["Second.flac"])

    def test_bot_nick_is_case_insensitively_keyed_but_preserves_display_case(self):
        _write_zip(self.zip_path, [("OtherBot-2026-08-27.txt", _list_txt())])
        list_fetch.process_fetched_list_zip("OtherBot", self.zip_path)

        self.assertIn("otherbot", config.fetched_bot_lists)
        self.assertEqual(config.fetched_bot_lists["otherbot"]["bot"], "OtherBot")

    # ---------------------------------------------------------- Path traversal

    def test_a_relative_path_traversal_entry_is_rejected_and_nothing_is_written_outside(self):
        _write_zip(self.zip_path, [("../../evil.txt", "pwned")])

        ok, reason = list_fetch.process_fetched_list_zip("evilbot", self.zip_path)

        self.assertFalse(ok)
        self.assertIn("traversal", reason.lower())
        self.assertNotIn("evilbot", config.fetched_bot_lists)
        # Nothing was left anywhere under FETCHED_FILES_DIR, including
        # outside the per-bot extraction directory - the whole archive is
        # rejected before any member is ever written, not just the
        # offending one.
        for root, _dirs, files in os.walk(self.tmp):
            self.assertNotIn("evil.txt", files)

    def test_an_absolute_path_entry_is_rejected(self):
        _write_zip(self.zip_path, [("/etc/cron.d/evil", "pwned")])

        ok, reason = list_fetch.process_fetched_list_zip("evilbot", self.zip_path)

        self.assertFalse(ok)
        self.assertIn("absolute", reason.lower())
        self.assertFalse(os.path.exists("/etc/cron.d/evil"))

    def test_a_windows_drive_letter_absolute_path_entry_is_rejected(self):
        _write_zip(self.zip_path, [("C:/Windows/evil.txt", "pwned")])

        ok, reason = list_fetch.process_fetched_list_zip("evilbot", self.zip_path)

        self.assertFalse(ok)
        self.assertIn("absolute", reason.lower())

    def test_extraction_directory_is_left_clean_after_a_rejection(self):
        extract_dir = list_fetch.list_extract_dir("evilbot")
        _write_zip(self.zip_path, [("../../evil.txt", "pwned")])

        list_fetch.process_fetched_list_zip("evilbot", self.zip_path)

        self.assertFalse(os.path.exists(extract_dir))

    # -------------------------------------------------------------- Zip bombs

    def test_a_declared_total_size_over_the_cap_is_rejected_before_extracting(self):
        self.set_config(MAX_FETCH_FILE_SIZE=100)
        big_txt = _list_txt() + ("!OtherBot Filler.flac  ::INFO:: 1.0MB\n" * 50)
        self.assertGreater(len(big_txt.encode("utf-8")), 100)
        _write_zip(self.zip_path, [("OtherBot-2026-08-27.txt", big_txt)])

        ok, reason = list_fetch.process_fetched_list_zip("bigbot", self.zip_path)

        self.assertFalse(ok)
        self.assertIn("exceeds", reason)
        self.assertNotIn("bigbot", config.fetched_bot_lists)
        self.assertFalse(os.path.exists(list_fetch.list_extract_dir("bigbot")))

    def test_too_many_entries_is_rejected(self):
        members = [(f"file{i}.txt", "x") for i in range(list_fetch.MAX_LIST_ZIP_ENTRIES + 1)]
        _write_zip(self.zip_path, members)

        ok, reason = list_fetch.process_fetched_list_zip("floodbot", self.zip_path)

        self.assertFalse(ok)
        self.assertIn("entries", reason)
        self.assertNotIn("floodbot", config.fetched_bot_lists)

    # ------------------------------------------------------ No recognizable list

    def test_a_zip_with_no_txt_file_at_all_fails_gracefully(self):
        _write_zip(self.zip_path, [("cover.jpg", b"\xff\xd8\xff\xe0notarealjpeg")])

        ok, reason = list_fetch.process_fetched_list_zip("nolistbot", self.zip_path)

        self.assertFalse(ok)
        self.assertIn("no recognizable", reason.lower())
        self.assertNotIn("nolistbot", config.fetched_bot_lists)

    def test_an_empty_zip_fails_gracefully(self):
        _write_zip(self.zip_path, [])

        ok, reason = list_fetch.process_fetched_list_zip("emptybot", self.zip_path)

        self.assertFalse(ok)
        self.assertIsNotNone(reason)

    def test_not_a_zip_file_at_all_fails_gracefully_not_a_crash(self):
        with open(self.zip_path, "wb") as f:
            f.write(b"this is definitely not a zip file")

        ok, reason = list_fetch.process_fetched_list_zip("badzipbot", self.zip_path)

        self.assertFalse(ok)
        self.assertIsNotNone(reason)

    def test_ambiguous_multiple_txt_files_picks_the_largest_without_crashing(self):
        small = "just a readme, not a list\n"
        big = _list_txt(files=(("Real Track.flac", "5.0MB"),)) * 5  # make it clearly bigger
        _write_zip(self.zip_path, [
            ("README.txt", small),
            ("OtherBot-2026-08-27.txt", big),
        ])

        ok, reason = list_fetch.process_fetched_list_zip("ambiguousbot", self.zip_path)

        self.assertTrue(ok, reason)
        titles = [row["title"] for row in config.fetched_bot_lists["ambiguousbot"]["entries"]]
        self.assertIn("Real Track.flac", titles)

    def test_the_rar_side_list_is_excluded_when_a_real_master_list_is_also_present(self):
        rar_txt = "List of Entire Album Folders (!rar)\n" + "=" * 90 + "\n"
        real_txt = _list_txt(files=(("Genuine.flac", "3.0MB"),))
        _write_zip(self.zip_path, [
            ("OtherBot-RAR-2026-08-27.txt", rar_txt),
            ("OtherBot-2026-08-27.txt", real_txt),
        ])

        ok, reason = list_fetch.process_fetched_list_zip("rarbot", self.zip_path)

        self.assertTrue(ok, reason)
        titles = [row["title"] for row in config.fetched_bot_lists["rarbot"]["entries"]]
        self.assertEqual(titles, ["Genuine.flac"])


class ListExtractDirTests(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-listfetch-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp

    def test_extract_dir_is_a_per_bot_subdirectory_under_lists(self):
        path = list_fetch.list_extract_dir("GoodBot")
        expected_root = os.path.join(os.path.abspath(self.tmp), "lists")
        self.assertTrue(path.startswith(expected_root + os.sep))
        self.assertIn("goodbot", path.lower())

    def test_path_separators_in_a_bot_nick_cannot_escape_the_lists_root(self):
        path = list_fetch.list_extract_dir("../../evil")
        expected_root = os.path.join(os.path.abspath(self.tmp), "lists")
        self.assertTrue(path == expected_root or path.startswith(expected_root + os.sep))

    def test_never_extracted_alongside_raw_fetched_files(self):
        """Distinct from FETCHED_FILES_DIR's top level - a fetched LIST
        archive must never be mistaken for a plain fetched file."""
        path = list_fetch.list_extract_dir("goodbot")
        self.assertNotEqual(os.path.dirname(path), os.path.abspath(self.tmp))


if __name__ == "__main__":
    unittest.main()
