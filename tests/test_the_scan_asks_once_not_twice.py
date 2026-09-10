"""The size comes back with the name, instead of being asked for again.

os.walk is built on os.scandir, which gets each entry's size from the
directory enumeration - and then throws it away, because os.walk's contract is
names only. The caller then asked os.path.getsize() for a number the
filesystem had just finished telling us: one redundant syscall per file, and
on a network share one redundant ROUND TRIP per file.

Measured on 20,000 files, local SSD, warm cache, both producing the same
answer:

    os.walk + getsize    0.356s   (17.8 us/file)
    os.scandir + cached  0.095s   ( 4.7 us/file)   3.8x

That is the WALK, and quoting it alone would overstate the change. A whole
rebuild over 30,000 files, producing byte-identical lists, goes 2.51s -> 1.56s
- 1.61x - because writing and packing are the rest of the job. The walk's
share is what grows on a network drive, where the second ask is a round trip.

Local disk is the BEST case for the old shape. The library this was written
for is 799,438 files on a mapped network drive, where a rebuild takes fifteen
and a half minutes.

THE RISK IS NOT THE SPEED. This is the loop that decides what the bot hands
out, so the tests that matter most below are the ones asserting the new walk
answers *identically* to the old one - same files, same sizes - on a tree with
the awkward cases in it, rather than asserting that it is fast.

WHAT DOES NOT MATTER: traversal order. all_files_data is sorted by (folder,
filename) before anything is written, so the order directories come back in
cannot reach the published list.
"""

import io
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import update_list  # noqa: E402


def the_old_way(top):
    """os.walk + os.path.getsize, exactly as the scan did it."""
    found = {}
    for root, _dirs, files in os.walk(top):
        for name in files:
            full = os.path.join(root, name)
            try:
                found[os.path.normcase(full)] = os.path.getsize(full)
            except OSError:
                found[os.path.normcase(full)] = None
    return found


def the_new_way(top):
    found = {}
    for root, files in update_list.walk_with_sizes(top):
        for name, size in files:
            found[os.path.normcase(os.path.join(root, name))] = size
    return found


class ATreeCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dccore-walk-")
        self.addCleanup(shutil.rmtree, self.root, True)

    def make(self, relative, size=1024):
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
        return path

    def make_dir(self, relative):
        path = os.path.join(self.root, relative)
        os.makedirs(path, exist_ok=True)
        return path


class ItAnswersExactlyWhatTheOldWalkAnswered(ATreeCase):
    """The assertion the change rests on."""

    def test_on_an_ordinary_library(self):
        self.make("Artist/Album/01.flac", 2048)
        self.make("Artist/Album/02.flac", 4096)
        self.make("Artist/Other Album/01.mp3", 512)
        self.make("Loose.flac", 7)

        self.assertEqual(the_new_way(self.root), the_old_way(self.root))

    def test_including_an_empty_directory(self):
        """It yields nothing, and must not be skipped or crash."""
        self.make("Artist/Album/01.flac")
        self.make_dir("Artist/Empty")

        self.assertEqual(the_new_way(self.root), the_old_way(self.root))

    def test_and_a_deep_tree(self):
        self.make("a/b/c/d/e/deep.flac", 33)

        self.assertEqual(the_new_way(self.root), the_old_way(self.root))

    def test_and_a_zero_byte_file(self):
        """0 is a real size and must not read as "unknown" - the two mean
        opposite things to the caller."""
        self.make("Artist/Album/silence.flac", 0)

        answer = the_new_way(self.root)
        self.assertEqual(answer, the_old_way(self.root))
        self.assertIn(0, answer.values())

    def test_and_a_name_with_awkward_characters(self):
        self.make("Artist/Album [1991]/01 - Track (feat. Someone) & more.flac")

        self.assertEqual(the_new_way(self.root), the_old_way(self.root))

    def test_the_comparison_is_not_vacuous(self):
        """Guard on the guard: two empty dicts are equal, and every assertion
        above would pass on a walk that found nothing at all."""
        self.make("Artist/Album/01.flac")

        self.assertTrue(the_new_way(self.root))


class WhatItDoesWithAnUnreadableEntry(ATreeCase):

    def test_a_size_that_cannot_be_read_is_None_not_zero(self):
        """0 would publish the file as a real 0-byte track - #228's bug. The
        caller excludes None and logs it; a helper that guessed 0 would put
        the bug back below the caller's feet."""
        self.make("Artist/Album/01.flac")

        real_scandir = os.scandir

        class Blind:
            def __init__(self, entry):
                self.name, self.path = entry.name, entry.path
                self._entry = entry

            def is_dir(self, follow_symlinks=True):
                return self._entry.is_dir(follow_symlinks=follow_symlinks)

            def stat(self, follow_symlinks=True):
                raise OSError(13, "Permission denied", self.path)

        class Scandir:
            def __init__(self, path="."):
                with real_scandir(path) as scanning:
                    self._entries = [
                        Blind(e) if e.name.endswith(".flac") else e
                        for e in scanning]
                self._next = iter(self._entries)

            def __iter__(self):
                return self

            def __next__(self):
                return next(self._next)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        os.scandir = Scandir
        self.addCleanup(setattr, os, "scandir", real_scandir)

        sizes = list(the_new_way(self.root).values())

        self.assertEqual(sizes, [None])
        self.assertNotIn(0, sizes)


class WhatItDoesWithAnUnreadableDirectory(ATreeCase):

    def test_onerror_is_called_and_the_walk_continues(self):
        """os.walk's own contract, and the scan depends on both halves: the
        callback is what records a systemic failure, and continuing is what
        stops one bad subtree ending the run."""
        self.make("Good/01.flac")

        real_scandir = os.scandir
        bad = os.path.join(self.root, "Bad")
        os.makedirs(bad, exist_ok=True)

        def picky(path="."):
            if os.path.normcase(os.path.abspath(path)) == os.path.normcase(bad):
                raise OSError(5, "Input/output error", path)
            return real_scandir(path)

        os.scandir = picky
        self.addCleanup(setattr, os, "scandir", real_scandir)

        problems = []
        found = {}
        for root, files in update_list.walk_with_sizes(self.root,
                                                       onerror=problems.append):
            for name, size in files:
                found[name] = size

        self.assertEqual(len(problems), 1)
        self.assertIsInstance(problems[0], OSError)
        self.assertIn("01.flac", found,
                      "one unreadable subtree ended the whole walk")

    def test_a_second_unreadable_subtree_is_also_reported(self):
        """The half the test above cannot prove on its own.

        Directories are taken off a stack, so whether the good one is visited
        before or after the bad one is not fixed - and if the bad one happens
        to come last, a walk that ABANDONED the rest on an error would still
        have found everything and passed. A mutation run replacing `continue`
        with `return` survived exactly that way.

        Two failing directories and a count is order-independent: abandoning
        after the first can never report the second, whichever order they come
        in."""
        self.make("Good/01.flac")
        real_scandir = os.scandir
        bad = {os.path.normcase(self.make_dir("BadOne")),
               os.path.normcase(self.make_dir("BadTwo"))}

        def picky(path="."):
            if os.path.normcase(os.path.abspath(path)) in bad:
                raise OSError(5, "Input/output error", path)
            return real_scandir(path)

        os.scandir = picky
        self.addCleanup(setattr, os, "scandir", real_scandir)

        problems = []
        found = {}
        for root, files in update_list.walk_with_sizes(self.root,
                                                       onerror=problems.append):
            for name, size in files:
                found[name] = size

        self.assertEqual(len(problems), 2,
                         "the walk stopped at the first unreadable subtree")
        self.assertIn("01.flac", found)

    def test_no_onerror_is_not_an_error(self):
        """The parameter is optional on os.walk and has to be here too."""
        self.make("Good/01.flac")

        list(update_list.walk_with_sizes(self.root))


class TheTwoSymlinkDecisionsAreStated(unittest.TestCase):
    """Asserted on the source, because the behavioural tests for these need a
    symlink and Windows refuses to make one without Developer Mode or
    elevation - so on the machine most likely to run this, they skip, and a
    change to either decision goes unnoticed. A mutation run confirmed that:
    flipping both survived locally.

    Two decisions, opposite answers, and the asymmetry is the point:

      * is_dir(follow_symlinks=False) - do NOT follow, which is os.walk's own
        default. A library with a link back up its own tree would otherwise
        walk forever.
      * stat() - DO follow, which is what os.path.getsize() did, so a
        symlinked track still reports the size of what it points at rather
        than the size of the link.
    """

    def source(self):
        """The CODE of walk_with_sizes(), with its docstring taken off.

        The docstring quotes both of these decisions verbatim, to explain
        them - so a search over the whole function matches the prose whether
        or not the code still agrees with it, and a mutation flipping
        follow_symlinks survived exactly that way. This project has been
        caught by that shape before; see tests/test_webserver.py's own notes
        on asserting the statement rather than the identifier.
        """
        with io.open(os.path.join(REPO_ROOT, "update_list.py"),
                     encoding="utf-8") as handle:
            text = handle.read()
        body = text.split("def walk_with_sizes(", 1)[1].split("\ndef ", 1)[0]
        # Everything after the docstring's closing quotes.
        return body.split('"""', 2)[-1]

    def test_directories_are_not_followed(self):
        self.assertIn("entry.is_dir(follow_symlinks=False)", self.source())

    def test_but_sizes_are(self):
        body = self.source()

        self.assertIn("entry.stat().st_size", body)
        self.assertNotIn("stat(follow_symlinks=False)", body)


class SymlinkedDirectoriesAreNotFollowed(ATreeCase):
    """os.walk's default is followlinks=False, and a library with a link back
    up its own tree would otherwise walk forever."""

    def test_a_loop_does_not_run_forever(self):
        self.make("Artist/Album/01.flac")
        link = os.path.join(self.root, "Artist", "loop")
        try:
            os.symlink(self.root, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError) as err:
            # Windows needs Developer Mode or elevation for this. Probe and
            # skip rather than assert - the same rule the loopback, code-page
            # and MAX_PATH checks in this suite already follow.
            self.skipTest("cannot create a directory symlink here: %s" % err)

        found = the_new_way(self.root)

        self.assertTrue(found)
        self.assertEqual(len(found), 1,
                         "the walk descended into a symlinked directory")


if __name__ == "__main__":
    unittest.main()
