"""Which copy the daemon serves when a filename is not unique.

dcc.handle_download_request() resolves a bare filename in three steps:

  1. FILE_DIRECTORY/<name> - misses for any library that keeps files in folders
  2. the master list: find the name, take the folder heading above it
  3. os.walk(FILE_DIRECTORY), first hit wins

Step 2 is the only one that knows anything. The list is where the requester
read the name from, so the folder the list puts it under is the copy they
asked for. Step 3 is a last-resort scan in whatever order the filesystem
hands back - an order the operator cannot see, control, or predict from the
listing they published.

The two only disagree when the same filename exists in more than one folder.
Step 2 had a bug that made it never match at all, so step 3 answered every
request - including the ones step 2 would have answered differently.
"""

import io
import os
import unittest

from tests import support  # noqa: F401  (path setup)

import config  # noqa: E402
import dcc  # noqa: E402

from tests.test_path_security import InlineThread, PathSecurityBase, quiet  # noqa: E402

NAME = "Track 01.flac"


class TwoFoldersOneFilename(PathSecurityBase):
    """The same track name under two albums, where the list and the
    filesystem would choose differently.

    "Alpha Album" sorts first, so os.walk() reaches it first. The list names
    "Zebra Album" first. Every assertion below turns on which of those wins,
    which is exactly the question a duplicate filename asks.
    """

    def setUp(self):
        super().setUp()
        self.alpha = self._album("Alpha Album", "ALPHA")
        self.zebra = self._album("Zebra Album", "ZEBRA")
        self._write_list(["Zebra Album", "Alpha Album"])

    def _album(self, folder, payload):
        directory = os.path.join(self.tree.music, folder)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, NAME)
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return path

    def _write_list(self, folders):
        """A master list in the shape update_list.py writes: a folder heading
        wrapped in rule lines, then one "!<nick> <file>  ::INFO:: <size>" row
        per file. The "D:\\MUSIC\\" prefix is the fixed heading update_list.py
        emits whatever the library's real path is, not a real directory."""
        rule = "=" * 53
        lines = ["List of files generated on Jan 1st\n", "\n"]
        for folder in folders:
            lines += ["\n", rule + "\n",
                      "D:\\MUSIC\\%s\\\n" % folder,
                      rule + "\n",
                      "!%s %s  ::INFO:: 1.0MB\n" % (config.NICKNAME, NAME)]
        path = os.path.join(self.tree.lists,
                            "%s-2026-01-01.txt" % config.LIST_BASE_NAME)
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
        return path

    def _request(self, name):
        self.notices.clear()
        InlineThread.dispatched = []
        config.dcc_queue.clear()
        with quiet():
            dcc.handle_download_request(self.sock, "dave", name, "#mp3passion")
        return [kind for kind, _args in self.notices]

    def _served_path(self):
        """The path the daemon settled on, whether it dispatched at once or
        queued the row - both carry the same resolved path."""
        for name, args in InlineThread.dispatched:
            if name == "start_dcc_send":
                return args[2]
        for rows in config.dcc_queue.values():
            for row in rows:
                if not row.get("is_temporary_zip"):
                    return row.get("path")
        return None

    # -- the behaviour under test -----------------------------------------

    def test_the_copy_the_list_names_first_is_the_one_served(self):
        """The requester copied the name out of the list, so the list's first
        answer is the copy they meant."""
        self._request(NAME)

        self.assertEqual(self._served_path(), self.zebra,
                         "served the copy the filesystem happened to reach "
                         "first, not the one the published list names first")

    def test_the_walk_would_have_picked_the_other_one(self):
        """Fixture invariant, and the discriminator for the test above.

        If os.walk ever stopped reaching Alpha first, the test above would
        pass without proving anything - it would be asserting a coincidence.
        """
        found = None
        for root, _dirs, files in os.walk(self.tree.music):
            if NAME in files:
                found = os.path.join(root, NAME)
                break

        self.assertEqual(found, self.alpha,
                         "fixture invariant: the walk must reach the copy the "
                         "list does NOT name first, or these tests prove nothing")

    def test_reordering_the_list_changes_which_copy_is_served(self):
        """The list is genuinely being read, rather than the right answer
        coming out for some other reason."""
        self._write_list(["Alpha Album", "Zebra Album"])

        self._request(NAME)

        self.assertEqual(self._served_path(), self.alpha)

    def test_the_served_copy_is_the_one_whose_contents_go_out(self):
        """The path is not just cosmetic - it is what gets read off disk."""
        self._request(NAME)

        with io.open(self._served_path(), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "ZEBRA")

    def test_no_error_is_reported_for_a_duplicate(self):
        """Documents the behaviour rather than asking for it: a duplicate is
        resolved silently, not refused. Worth pinning so that a future change
        to refuse instead is a deliberate decision and not a surprise."""
        kinds = self._request(NAME)

        self.assertNotIn("error", kinds)

    # -- controls ----------------------------------------------------------

    def test_a_unique_filename_in_a_folder_still_resolves(self):
        """The ordinary case, which is every request on a clean library."""
        only = self._album("Solo Album", "SOLO")
        self._write_list(["Zebra Album", "Alpha Album", "Solo Album"])
        with io.open(os.path.join(self.tree.lists,
                                  "%s-2026-01-01.txt" % config.LIST_BASE_NAME),
                     "a", encoding="utf-8") as handle:
            handle.write("!%s Only Here.flac  ::INFO:: 1.0MB\n" % config.NICKNAME)
        os.rename(only, os.path.join(os.path.dirname(only), "Only Here.flac"))

        kinds = self._request("Only Here.flac")

        self.assertNotIn("error", kinds)
        self.assertEqual(os.path.basename(self._served_path() or ""), "Only Here.flac")

    def test_a_missing_file_is_still_refused(self):
        """Without this, "no error" above could pass for a build that never
        refuses anything."""
        self.assertIn("error", self._request("No Such Track At All.flac"))


if __name__ == "__main__":
    unittest.main()
