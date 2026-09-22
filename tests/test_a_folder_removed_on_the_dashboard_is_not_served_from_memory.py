"""A folder removed on the dashboard does not leave "invalid path" behind (#901).

#886's lookup memories remember where a name was found and which folders
lookups landed in; #889 drops them on !rehash. But the dashboard's Folders
and Lists pages change the library WITHOUT a rehash - they save the file and
return, and library.folders() reads it on every call - so a path remembered
under the folder just removed was still on disk, passed the memory's own
existence check, and was then refused by is_safe_path() against the new
roots: "Error: Invalid path." for a file the new configuration serves,
for up to LOOKUP_HIT_TTL_SECONDS.

A remembered path is now trusted only under a root that is configured at
the moment of the request. Driven through the real request path, changing
library.folders the way the page's save does - no rehash, no forget.
"""

import os
import shutil
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dcc  # noqa: E402
import defaults as config  # noqa: E402
import library  # noqa: E402

from tests.test_an_unknown_filename_does_not_scan_the_library_unbounded import LookupBase  # noqa: E402
from tests.test_path_security import InlineThread  # noqa: E402


class TheDashboardMovesTheLibrary(LookupBase):

    def setUp(self):
        super().setUp()
        # What an operator moving to another disk has: the same album,
        # complete, under a new root - and the old one still on disk.
        self.new_root = os.path.join(self.tree.root, "new-disk")
        shutil.copytree(self.tree.music, self.new_root)

    def a_track(self, index=0):
        return os.path.basename(self.tree.tracks[index])

    def only_the_new_root(self):
        """What the Folders page's save leaves behind: the new folder set,
        read live by the next request. Nothing calls forget_library_lookups()."""
        return mock.patch.object(library, "folders",
                                 lambda name=None: [library.Folder("Music", self.new_root)])

    def sent_paths(self):
        return [args[2] for name, args in InlineThread.dispatched if name == "start_dcc_send"]

    def the_first_send_is_done(self):
        """The first file went out and finished, before the operator moved the
        library - its transfer and its per-user send lock released, as a
        completed send releases them. Without this the next request queues
        rather than sends, and the path read would be the FIRST one's, from
        before the move."""
        config.active_transfers[:] = []
        config.dcc_queue.clear()
        config.user_processing_lock.clear()
        InlineThread.dispatched[:] = []

    def test_a_remembered_file_is_served_from_the_new_root(self):
        name = self.a_track()
        self.ask(name)
        self.assertEqual(self.errors(), [], "the first request should have been served")
        self.the_first_send_is_done()

        with self.only_the_new_root():
            self.ask(name)

        self.assertEqual(self.errors(), [], "a path remembered under the removed folder was used")
        self.assertEqual(len(self.sent_paths()), 1, InlineThread.dispatched)
        self.assertTrue(self.sent_paths()[0].startswith(self.new_root),
                        "sent from %s, not from the new root" % self.sent_paths()[0])

    def test_a_remembered_folder_outside_the_new_roots_is_not_used_either(self):
        """The folder memory, not the name memory: a sibling of a file found
        before the change, asked for after it."""
        self.ask(self.a_track(0))
        dcc._lookup_hits.clear()          # only the folder memory is left
        self.the_first_send_is_done()

        with self.only_the_new_root():
            self.ask(self.a_track(1))

        self.assertEqual(self.errors(), [])
        self.assertEqual(len(self.sent_paths()), 1, InlineThread.dispatched)
        self.assertTrue(self.sent_paths()[0].startswith(self.new_root), self.sent_paths()[0])

    def test_with_the_folders_unchanged_the_memory_still_saves_the_scan(self):
        """The control: #886's point is kept. A path under a root that is
        still configured is used as it was, with no second scan."""
        name = self.a_track()
        self.ask(name)
        scans = len(self.walks)
        self.assertEqual(scans, 1, "the first request should have scanned")

        self.ask(name)

        self.assertEqual(len(self.walks), scans, "a remembered path under a live root was not used")
        self.assertEqual(self.errors(), [])


if __name__ == "__main__":
    unittest.main()
