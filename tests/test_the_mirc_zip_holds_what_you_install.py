"""#1412: dccore-mirc.zip, the mIRC script as one download.

scripts/build_mirc_zip.py packs dccore.mrc with the dcx and dll folders it
loads, for each GitHub release. Checked here on a zip built from the real
scripts/mirc/ into a temp folder:

  * it holds what mIRC loads - the script, both folders, their licences -
    and none of what builds them (the generator, the layout, the dll
    sources, the icon preview), each member byte for byte the file it
    came from;
  * no top folder, so it extracts where dccore.mrc already is, and no
    dccore.ini, so an update keeps the operator's settings and token;
  * its README says the version dccore.mrc says, and the steps;
  * two builds give the same bytes;
  * a folder dropped from scripts/mirc/ is a failed build, not a smaller zip.
"""

import hashlib
import io
import os
import re
import shutil
import sys
import unittest
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
try:
    import build_mirc_zip  # noqa: E402
finally:
    sys.path.remove(os.path.join(REPO_ROOT, "scripts"))

from tests.support import temp_dir  # noqa: E402

MIRC = os.path.join(REPO_ROOT, "scripts", "mirc")


def built(test, source=MIRC):
    out = os.path.join(temp_dir(test, prefix="dccore-mirczip-"), "dccore-mirc.zip")
    digest, count = build_mirc_zip.build(out, source)
    return out, digest, count


def names(path):
    with zipfile.ZipFile(path) as archive:
        return archive.namelist()


class WhatIsInIt(unittest.TestCase):

    def setUp(self):
        self.path, self.digest, self.count = built(self)
        self.names = names(self.path)

    def test_the_script_and_what_it_loads(self):
        for name in ("dccore.mrc", "dcx/dcx.dll", "dcx/WebView2Loader.dll", "dcx/LICENSE-DCX.txt",
                     "dcx/LICENSE-WebView2.txt", "dll/dccore_ui.dll", "dll/dccore_icons.dll",
                     "dccore-mirc-README.txt"):
            with self.subTest(name=name):
                self.assertIn(name, self.names)

    def test_nothing_that_builds_them(self):
        for name in self.names:
            with self.subTest(name=name):
                self.assertFalse(name.endswith((".py", ".c", ".h", ".png", ".rc", ".def")), name)
                self.assertFalse(name.startswith("dll/src/"), name)

    def test_every_file_of_the_two_folders_but_the_skipped_ones(self):
        """The rule, read back from the tree: a file added to dcx/ or dll/
        ships without anyone remembering to list it."""
        expected = set()
        for folder in ("dcx", "dll"):
            for root, dirs, files in os.walk(os.path.join(MIRC, folder)):
                dirs[:] = [d for d in dirs if d not in ("src", "__pycache__")]
                for name in files:
                    inside = os.path.relpath(os.path.join(root, name), MIRC).replace(os.sep, "/")
                    if inside != "dll/icons-preview.png":
                        expected.add(inside)
        shipped = {n for n in self.names if n.startswith(("dcx/", "dll/"))}
        self.assertEqual(shipped, expected)

    def test_each_member_is_the_file_it_came_from(self):
        with zipfile.ZipFile(self.path) as archive:
            for name in self.names:
                if name == "dccore-mirc-README.txt":
                    continue
                with self.subTest(name=name):
                    with open(os.path.join(MIRC, *name.split("/")), "rb") as handle:
                        self.assertEqual(archive.read(name), handle.read())

    def test_no_top_folder_and_no_settings_file(self):
        """Extracted where dccore.mrc is, an update overwrites the script and
        its folders and leaves dccore.ini - the token and settings - alone."""
        self.assertIn("dccore.mrc", self.names)
        self.assertFalse(any(n.lower().endswith(".ini") for n in self.names))
        tops = {n.split("/", 1)[0] for n in self.names if "/" in n}
        self.assertEqual(tops, {"dcx", "dll"})

    def test_the_count_and_digest_it_reports_are_the_files(self):
        self.assertEqual(self.count, len(self.names))
        with open(self.path, "rb") as handle:
            self.assertEqual(self.digest, hashlib.sha256(handle.read()).hexdigest())


class TheReadme(unittest.TestCase):

    def setUp(self):
        path, _digest, _count = built(self)
        with zipfile.ZipFile(path) as archive:
            self.text = archive.read("dccore-mirc-README.txt").decode("utf-8")

    def test_it_names_the_version_the_script_says(self):
        with io.open(os.path.join(MIRC, "dccore.mrc"), encoding="latin-1") as handle:
            said = re.search(r"alias dccore\.ver \{ return (\S+) \}", handle.read()).group(1)
        self.assertIn(f"dccore.mrc {said}", self.text.splitlines()[0])

    def test_it_says_how_to_load_pair_and_update(self):
        for step in ("/load -rs dccore.mrc", "/dccore pair", "/reload -rs dccore.mrc", "dccore.ini"):
            with self.subTest(step=step):
                self.assertIn(step, self.text)

    def test_its_lines_end_as_notepad_expects(self):
        self.assertNotIn("\n", self.text.replace("\r\n", ""))
        self.assertTrue(self.text.endswith("\r\n"))


class TheSameBytesEveryTime(unittest.TestCase):

    def test_two_builds_are_identical(self):
        first, digest_one, _ = built(self)
        second, digest_two, _ = built(self)
        self.assertEqual(digest_one, digest_two)
        with open(first, "rb") as a, open(second, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_no_member_carries_the_checkout_time(self):
        path, _digest, _count = built(self)
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                with self.subTest(name=info.filename):
                    self.assertEqual(info.date_time, build_mirc_zip.FIXED_TIME)

    def test_the_members_are_in_order(self):
        path, _digest, _count = built(self)
        listed = names(path)
        self.assertEqual(listed, sorted(listed))


class AMissingFolderStopsIt(unittest.TestCase):

    def test_without_dcx_there_is_no_zip(self):
        copy = os.path.join(temp_dir(self, prefix="dccore-mirczip-src-"), "mirc")
        shutil.copytree(MIRC, copy, ignore=shutil.ignore_patterns("dcx"))
        out = os.path.join(temp_dir(self, prefix="dccore-mirczip-out-"), "dccore-mirc.zip")
        with self.assertRaises(SystemExit):
            build_mirc_zip.build(out, copy)
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
