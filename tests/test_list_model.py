"""library.py's list model - stage 1 of #26.

NOTHING THE DAEMON DOES CHANGES HERE. With no lists.json on disk, which is
every install, lists() returns ONE list built from exactly the folders
folders() already resolved, and every one of the thirteen existing callers
sees what it saw before. That equivalence is the property most worth pinning,
because the whole argument for putting the accessor in first is that the
folder set can move inside a list without touching those callers again.

The rest is the rules an operator meets the first time they define two lists,
and the answers this module has to give for a request to be routable at all:
which list has no channel, which list a private message means, and what
happens to a channel bound to nothing.
"""

import io
import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if os.path.join(REPO_ROOT, "tests") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import defaults as config  # noqa: E402
import library  # noqa: E402
import list as list_mod  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


class ListCase(DCCoreTestCase):
    """A scratch tree, and a lists file that does not exist yet."""

    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()
        self.store = os.path.join(self.tree.root, "lists.json")
        self.folder_store = os.path.join(self.tree.root, "library_folders.json")
        self.set_config(LISTS_FILE=self.store,
                        LIBRARY_FOLDERS_FILE=self.folder_store,
                        FILE_DIRECTORY=self.tree.music)

    def write(self, entries, path=None):
        with io.open(path or self.store, "w", encoding="utf-8") as handle:
            json.dump(entries, handle)

    def one(self, **over):
        entry = {"name": "Main", "primary": True, "channels": [],
                 "folders": [{"name": "Music", "path": self.tree.music}]}
        entry.update(over)
        return entry


class WithNoFileNothingChanges(ListCase):
    """The equivalence this whole stage rests on."""

    def test_there_is_exactly_one_list(self):
        every = library.lists()

        self.assertEqual(len(every), 1)
        self.assertTrue(every[0].primary)
        self.assertEqual(every[0].name, library.DEFAULT_LIST_NAME)

    def test_it_holds_exactly_the_folders_folders_already_resolved(self):
        """Not a copy of the resolution - the same one. Two answers to "which
        folders" is how they drift, and the folder file, FILE_DIRECTORY and
        the "neither" case all have to keep behaving identically."""
        self.assertEqual(library.lists()[0].folders, library.folders())

    def test_the_folder_file_still_wins_over_file_directory(self):
        other = os.path.join(self.tree.root, "other")
        os.makedirs(other, exist_ok=True)
        self.write([{"name": "Other", "path": other}], path=self.folder_store)

        self.assertEqual([f.path for f in library.folders()], [other])

    def test_an_install_that_has_chosen_nothing_still_has_a_list(self):
        """[] folders, not [] lists. "Which list is this request for" has to
        stay answerable on a fresh install, where FILE_DIRECTORY is
        deliberately allowed to be blank until the dashboard sets it."""
        self.set_config(FILE_DIRECTORY="")

        every = library.lists()

        self.assertEqual(len(every), 1)
        self.assertEqual(every[0].folders, [])


class WhichListARequestMeans(ListCase):

    def test_a_private_message_means_the_primary(self):
        """A PM carries no channel, which is the whole reason a primary
        exists."""
        self.write([self.one(name="Films", primary=False),
                    self.one(name="Music", primary=True)])

        self.assertEqual(library.primary_list().name, "Music")

    def test_a_channel_bound_to_a_list_means_that_list(self):
        self.write([self.one(name="Music", primary=True, channels=["#music"]),
                    self.one(name="Films", primary=False, channels=["#films"])])

        self.assertEqual(library.list_for_channel("#films").name, "Films")

    def test_a_channel_is_matched_however_it_is_capitalised(self):
        """IRC channel names are case-insensitive, and this one arrives off
        the wire."""
        self.write([self.one(name="Music", channels=["#Music"])])

        self.assertEqual(library.list_for_channel("#MUSIC").name, "Music")

    def test_a_channel_bound_to_nothing_gets_nothing(self):
        """None is a real answer, not a gap to paper over. #26: a channel with
        no list bound gets no advert and no requests answered - quietly
        serving the primary instead would be the opposite of that."""
        self.write([self.one(name="Music", channels=["#music"])])

        self.assertIsNone(library.list_for_channel("#somewhere-else"))

    def test_a_list_can_be_found_by_name(self):
        self.write([self.one(name="Films")])

        self.assertEqual(library.list_by_name("films").name, "Films")
        self.assertIsNone(library.list_by_name("nothing"))


class ExactlyOnePrimary(ListCase):
    """Decided once, on load, rather than at every call site. A PM has no
    channel, so the primary is the only answer to "which list" - and "none of
    them" and "two of them" are both answers somebody would have to handle."""

    def test_a_file_with_no_primary_promotes_the_first(self):
        self.write([self.one(name="A", primary=False),
                    self.one(name="B", primary=False)])

        every = library.lists()

        self.assertEqual([e.primary for e in every], [True, False])
        self.assertEqual(library.primary_list().name, "A")

    def test_a_file_with_several_keeps_the_first(self):
        self.write([self.one(name="A", primary=True),
                    self.one(name="B", primary=True),
                    self.one(name="C", primary=True)])

        self.assertEqual([e.primary for e in library.lists()],
                         [True, False, False])

    def test_the_order_is_the_operators(self):
        self.write([self.one(name="C"), self.one(name="A"), self.one(name="B")])

        self.assertEqual([e.name for e in library.lists()], ["C", "A", "B"])


class WhichFoldersAListIsBuiltFrom(ListCase):

    def test_no_name_means_the_primary(self):
        """What every existing caller does, unchanged."""
        first = os.path.join(self.tree.root, "one")
        second = os.path.join(self.tree.root, "two")
        for path in (first, second):
            os.makedirs(path, exist_ok=True)
        self.write([
            {"name": "Music", "primary": True,
             "folders": [{"name": "M", "path": first}]},
            {"name": "Films", "folders": [{"name": "F", "path": second}]}])

        self.assertEqual([f.path for f in library.folders()], [first])

    def test_a_name_picks_that_list(self):
        first = os.path.join(self.tree.root, "one")
        second = os.path.join(self.tree.root, "two")
        for path in (first, second):
            os.makedirs(path, exist_ok=True)
        self.write([
            {"name": "Music", "primary": True,
             "folders": [{"name": "M", "path": first}]},
            {"name": "Films", "folders": [{"name": "F", "path": second}]}])

        self.assertEqual([f.path for f in library.folders("Films")], [second])

    def test_an_unknown_name_serves_nothing_rather_than_somebody_elses(self):
        """A caller asking for a list that is not configured has a bug.
        Falling back to the primary would hide it behind output that looks
        perfectly plausible."""
        self.write([self.one(name="Music")])

        self.assertEqual(library.folders("Films"), [])


class AFileItCannotRead(ListCase):
    """None rather than [], for the reason load_folders() gives: [] reads as
    "the operator configured no lists" and serves nothing, when the truth is
    "we could not read the file"."""

    def test_broken_json_falls_back_to_the_single_list(self):
        with io.open(self.store, "w", encoding="utf-8") as handle:
            handle.write("{not json at all")

        self.assertIsNone(library.load_lists())
        self.assertEqual(len(library.lists()), 1)

    def test_a_file_that_is_not_a_list_says_so(self):
        self.write({"name": "Music"})

        self.assertIsNone(library.load_lists())

    def test_an_entry_with_no_name_is_skipped(self):
        """A list with no name cannot be bound to a channel, picked in the
        dashboard, or named in a log line."""
        self.write([{"folders": [{"path": self.tree.music}]},
                    self.one(name="Real")])

        self.assertEqual([e.name for e in library.lists()], ["Real"])

    def test_a_file_of_nothing_usable_falls_back(self):
        self.write([{"folders": []}, "not a dict", 5])

        self.assertIsNone(library.load_lists())
        self.assertEqual(library.lists()[0].name, library.DEFAULT_LIST_NAME)

    def test_a_list_with_no_folders_is_still_a_list(self):
        """An operator who has defined a list and not yet pointed it anywhere
        has a list. It serves nothing, which is exactly what they configured."""
        self.write([{"name": "Empty", "folders": []}])

        every = library.lists()

        self.assertEqual([e.name for e in every], ["Empty"])
        self.assertEqual(every[0].folders, [])


class TheFileLocationIsResolvedPerCall(ListCase):

    def test_rehash_moving_it_takes_effect(self):
        """Same reasoning as folders_file(): !rehash reloads config, and a
        path baked in at import would keep pointing at the old location for
        the life of the process."""
        moved = os.path.join(self.tree.root, "moved.json")
        self.write([self.one(name="Moved")], path=moved)
        self.set_config(LISTS_FILE=moved)

        self.assertEqual(library.lists()[0].name, "Moved")


class WhereAListsFilesLive(ListCase):
    """#26 stage 2. A subdirectory rather than a longer filename, and the
    primary keeps the root it already has."""

    def setUp(self):
        super().setUp()
        self.lists_dir = os.path.join(self.tree.root, "lists")
        os.makedirs(self.lists_dir, exist_ok=True)
        self.set_config(LOCAL_LIST_DIR=self.lists_dir)

    def test_with_no_name_it_is_the_directory_every_install_uses(self):
        self.assertEqual(list_mod.list_dir(), self.lists_dir)

    def test_the_primary_list_uses_that_same_directory(self):
        """Nothing moves. A single-list install cannot tell this function
        exists, and no upgrade migrates anything."""
        self.write([self.one(name="Music", primary=True)])

        self.assertEqual(list_mod.list_dir("Music"), self.lists_dir)

    def test_another_list_gets_its_own_subdirectory(self):
        self.write([self.one(name="Music", primary=True),
                    self.one(name="Films", primary=False)])

        self.assertEqual(list_mod.list_dir("Films"),
                         os.path.join(self.lists_dir, "Films"))

    def test_an_unknown_name_does_not_invent_a_directory(self):
        """Callers looking there find nothing, which is the truthful outcome.
        A build never writes to a name it did not get from lists()."""
        self.write([self.one(name="Music")])

        self.assertEqual(list_mod.list_dir("Nope"), self.lists_dir)

    def test_the_side_files_follow_the_list(self):
        self.write([self.one(name="Music", primary=True),
                    self.one(name="Films", primary=False)])

        self.assertTrue(list_mod.size_file_path("Films").startswith(
            os.path.join(self.lists_dir, "Films")))
        self.assertTrue(list_mod.rawbytes_file_path("Films").startswith(
            os.path.join(self.lists_dir, "Films")))
        self.assertEqual(list_mod.size_file_path(),
                         list_mod.size_file_path("Music"))

    def test_the_finders_look_in_that_lists_directory(self):
        """The behaviour, not the string. A list's own index is found under
        its own directory and the primary's is not mistaken for it."""
        self.write([self.one(name="Music", primary=True),
                    self.one(name="Films", primary=False)])
        films = os.path.join(self.lists_dir, "Films")
        os.makedirs(films, exist_ok=True)
        self.set_config(LIST_BASE_NAME="DCCore")
        primary_index = os.path.join(self.lists_dir, "DCCore-2026-09-01.txt")
        films_index = os.path.join(films, "DCCore-2026-09-02.txt")
        for path in (primary_index, films_index):
            with io.open(path, "w", encoding="utf-8") as handle:
                handle.write("!DCCore Song.flac\n")

        self.assertEqual(list_mod.find_latest_list(), primary_index)
        self.assertEqual(list_mod.find_latest_list("Films"), films_index)

    def test_all_list_paths_threads_the_name_through(self):
        self.write([self.one(name="Music", primary=True),
                    self.one(name="Films", primary=False)])
        films = os.path.join(self.lists_dir, "Films")
        os.makedirs(films, exist_ok=True)
        self.set_config(LIST_BASE_NAME="DCCore")
        index = os.path.join(films, "DCCore-2026-09-02.txt")
        with io.open(index, "w", encoding="utf-8") as handle:
            handle.write("!DCCore Song.flac\n")

        self.assertEqual(list_mod.all_list_paths("Films"), [index])
        self.assertEqual(list_mod.all_list_paths(), [])


class ALessThanObviousName(ListCase):
    """A list name is operator-facing and never typed in a channel, so it can
    hold anything - including what a filesystem refuses."""

    def test_a_plain_name_is_its_own_directory(self):
        """The common case stays readable: "Films" is the Films directory."""
        self.assertEqual(list_mod.list_slug("Films"), "Films")

    def test_nothing_that_could_traverse_survives(self):
        for name in ("..", "../../etc", "a/b", "a\\b", "."):
            with self.subTest(name=name):
                slug = list_mod.list_slug(name)
                self.assertNotIn("..", slug)
                self.assertNotIn("/", slug)
                self.assertNotIn(chr(92), slug)

    def test_two_names_that_flatten_the_same_stay_apart(self):
        """Replacing characters is not injective: "A/B" and "A B" both flatten
        to "A_B", and two lists in one directory would overwrite each other's
        index, archive and side files with no error anywhere."""
        self.assertNotEqual(list_mod.list_slug("A/B"), list_mod.list_slug("A B"))
        self.assertNotEqual(list_mod.list_slug("A:B"), list_mod.list_slug("A/B"))

    def test_it_is_the_same_answer_every_time(self):
        """Stable across restarts and across reordering the lists - it depends
        on that one name and nothing else. hash() is randomised per process
        and would rename every directory on each start."""
        first = list_mod.list_slug("My Films / 2024")

        self.assertEqual(first, list_mod.list_slug("My Films / 2024"))
        self.assertNotIn(str(id(self)), first)

    def test_an_empty_name_still_produces_something_usable(self):
        self.assertTrue(list_mod.list_slug(""))
        self.assertTrue(list_mod.list_slug("   "))


if __name__ == "__main__":
    unittest.main()
