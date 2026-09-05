"""#164 step 4: the served-folder list is editable from the dashboard.

Steps 1-3 shipped in v1.11.0 and made the daemon serve from a list of folders -
library.py as the single accessor, the list built from every folder in the
operator's order, !rar and search resolving a request back to the right one by
its label. Until now the only way to WRITE that list was to create
data/library_folders.json by hand, so the feature existed and no operator could
reach it. The Settings page offered one "Music directory" box, which is the
single-folder FALLBACK, and nothing said so.

WHY ITS OWN ENDPOINT AND NOT A SETTING

Every other field on that page is one scalar an operator types into one box,
and settings_file.save() writes exactly that. A folder list is ordered,
validated as a SET rather than a value at a time, and stored as JSON. Bending
it into the settings shape would mean either one comma-joined string (which
cannot carry labels, and breaks the moment a path contains a comma) or one
setting per folder (which cannot be reordered).

WHAT VALIDATION HAS TO CATCH THAT PER-FIELD VALIDATION CANNOT

Two folders can each be perfectly good and still be an invalid pair: one
nested inside the other lists every file under it twice, and two sharing a
label make the label useless for telling them apart in the list. That is why
library.problems() takes the whole set and returns every fault at once.
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

import library  # noqa: E402
import webserver  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


class FoldersTestCase(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.root = self.make_tree().root
        self.flac = os.path.join(self.root, "Flac")
        self.mp3 = os.path.join(self.root, "Mp3")
        self.nested = os.path.join(self.flac, "Sub")
        for directory in (self.flac, self.mp3, self.nested):
            os.makedirs(directory, exist_ok=True)
        self.folders_file = os.path.join(self.root, "library_folders.json")
        self.set_config(FILE_DIRECTORY=self.flac,
                        LIBRARY_FOLDERS_FILE=self.folders_file)

    def save(self, rows):
        return webserver.apply_folder_changes({"folders": rows})


class ReportingWhatIsServed(FoldersTestCase):

    def test_with_no_file_it_reports_the_single_directory(self):
        payload = webserver.build_folders_payload()

        self.assertEqual(payload["source"], "file_directory")
        self.assertEqual(len(payload["folders"]), 1)
        self.assertEqual(payload["folders"][0]["path"], self.flac)

    def test_with_nothing_configured_at_all(self):
        """A fresh install. "none" and "one folder" must not look the same to
        an operator wondering why nothing is served."""
        self.set_config(FILE_DIRECTORY=None)

        payload = webserver.build_folders_payload()

        self.assertEqual(payload["source"], "none")
        self.assertEqual(payload["folders"], [])

    def test_with_a_file_it_reports_the_list(self):
        self.save([{"name": "Flac", "path": self.flac},
                   {"name": "Mp3", "path": self.mp3}])

        payload = webserver.build_folders_payload()

        self.assertEqual(payload["source"], "file")
        self.assertEqual([f["name"] for f in payload["folders"]], ["Flac", "Mp3"])

    def test_the_order_is_the_operators(self):
        """It decides list order and which folder wins when the same relative
        path exists in two, so it is not incidental."""
        self.save([{"name": "Mp3", "path": self.mp3},
                   {"name": "Flac", "path": self.flac}])

        payload = webserver.build_folders_payload()

        self.assertEqual([f["name"] for f in payload["folders"]], ["Mp3", "Flac"])


class SavingASet(FoldersTestCase):

    def test_two_folders_are_written_and_read_back(self):
        status, result = self.save([{"name": "Flac", "path": self.flac},
                                    {"name": "Mp3", "path": self.mp3}])

        self.assertEqual(status, 200)
        self.assertEqual(result["written"], 2)
        self.assertEqual([f.name for f in library.folders()], ["Flac", "Mp3"])

    def test_a_missing_label_is_filled_in_from_the_path(self):
        """So an operator who only wants to add a path can."""
        status, _result = self.save([{"path": self.mp3}])

        self.assertEqual(status, 200)
        self.assertEqual(library.folders()[0].name, "Mp3")

    def test_the_save_says_the_list_needs_rebuilding(self):
        """Nothing needs a rehash - library.folders() re-reads the file on
        every call - but a folder that is not in the published list is not
        really being served yet, and the operator has no other way to know."""
        _status, result = self.save([{"name": "Flac", "path": self.flac}])

        self.assertTrue(result["rebuild_required"])


class RefusingASetThatCannotBeServed(FoldersTestCase):

    def assert_refused(self, rows, fragment):
        """Refused AS A LIST of problems, not as one error string.

        library.save_folders() validates again and raises ValueError, so a
        refusal happens either way - which is why dropping the
        library.problems() call here survived a mutation run. What that call
        buys is the STRUCTURE: the page renders one bullet per problem, and a
        single joined string cannot be rendered that way. So that is what this
        asserts.
        """
        status, result = self.save(rows)

        self.assertEqual(status, 400)
        self.assertIsInstance(result.get("problems"), list,
                              "a refusal must carry a list of problems for the "
                              "page to render one line each")
        self.assertTrue(result["problems"])
        self.assertIn(fragment, " ".join(result["problems"]))

    def test_a_folder_nested_in_another(self):
        self.assert_refused(
            [{"name": "Flac", "path": self.flac},
             {"name": "Sub", "path": self.nested}], "sits inside")

    def test_two_folders_sharing_a_label(self):
        """Case-insensitively: the label is matched that way when a request
        comes back in."""
        self.assert_refused(
            [{"name": "Same", "path": self.flac},
             {"name": "same", "path": self.mp3}], "already used by")

    def test_the_same_folder_twice(self):
        self.assert_refused(
            [{"name": "One", "path": self.flac},
             {"name": "Two", "path": self.flac}], "already listed")

    def test_a_path_that_is_not_a_folder(self):
        self.assert_refused(
            [{"name": "Ghost", "path": os.path.join(self.root, "nope")}],
            "not a folder on this machine")

    def test_a_label_with_a_separator_in_it(self):
        """It becomes the first component of every path a user copies back."""
        self.assert_refused([{"name": "a/b", "path": self.flac}],
                            "must be a single folder name")

    def test_every_problem_is_reported_at_once(self):
        """An operator fixing three things should be told about three things -
        the reason library.problems() exists in that shape."""
        status, result = self.save([
            {"name": "Dup", "path": self.flac},
            {"name": "dup", "path": self.mp3},
            {"name": "Ghost", "path": os.path.join(self.root, "nope")},
        ])

        self.assertEqual(status, 400)
        self.assertGreaterEqual(len(result["problems"]), 2)

    def test_a_refused_save_leaves_the_stored_list_alone(self):
        """The operator's rows are still on their screen; what must not happen
        is the file changing under a save that reported failure."""
        self.save([{"name": "Flac", "path": self.flac}])

        self.save([{"name": "Bad", "path": os.path.join(self.root, "nope")}])

        self.assertEqual([f.name for f in library.folders()], ["Flac"])

    def test_a_body_that_is_not_a_folder_list(self):
        for body in ({}, {"folders": "nope"}, {"folders": [1, 2]},
                     {"folders": ["x"]}, "string", None, []):
            with self.subTest(body=body):
                status, _result = webserver.apply_folder_changes(body)

                self.assertEqual(status, 400)

    def test_more_folders_than_the_cap(self):
        rows = [{"name": f"n{i}", "path": self.flac}
                for i in range(webserver.MAX_SERVED_FOLDERS + 1)]

        status, result = self.save(rows)

        self.assertEqual(status, 400)
        self.assertIn("At most", result["error"])


class ClearingTheListGoesBackToTheSingleDirectory(FoldersTestCase):

    def test_an_empty_list_removes_the_file(self):
        """Rather than writing []. library.load_folders() already returns None
        for an empty list and falls back, so a written [] would be a file on
        disk that does nothing and the next reader would have to work that
        out."""
        self.save([{"name": "Flac", "path": self.flac}])
        self.assertTrue(os.path.exists(self.folders_file))

        status, result = self.save([])

        self.assertEqual(status, 200)
        self.assertFalse(os.path.exists(self.folders_file))
        self.assertEqual(result["source"], "file_directory")

    def test_clearing_when_there_was_no_file(self):
        """Must not raise on a file that was never there."""
        status, _result = self.save([])

        self.assertEqual(status, 200)

    def test_rows_with_no_path_are_ignored_not_refused(self):
        """The page adds an empty row when "Add folder" is pressed, and an
        operator who changes their mind should not have to remove it before
        anything else can be saved."""
        status, result = self.save([{"name": "Flac", "path": self.flac},
                                    {"name": "", "path": "   "}])

        self.assertEqual(status, 400,
                         "a blank path is still a problem the server names")
        self.assertIn("no path given", " ".join(result["problems"]))


class ThePageBuildsRowsWithoutInterpolatingValues(unittest.TestCase):
    """The rule this codebase already carries four long comments about:
    escapeHtml() is textContent -> innerHTML, which leaves a double quote
    alone. A path is operator input and can contain one, so concatenating it
    into value="…" would close the attribute and everything after it would be
    parsed as markup.

    Nothing here executes JavaScript - see tests/test_web_assets.py - so this
    reads the source for the shape, the same way the other guards in this
    project do.
    """

    def source(self):
        with io.open(os.path.join(REPO_ROOT, "web", "app.js"),
                     encoding="utf-8") as handle:
            return handle.read()

    def test_the_row_markup_carries_no_value_attribute(self):
        js = self.source()
        block = js.split("function foldersSectionHtml(", 1)[1]
        block = block.split("function attachFolderRows(", 1)[0]

        self.assertNotIn('value="', block,
                         "a folder row is interpolating a value into markup - "
                         "escapeHtml() does not encode a double quote, so a "
                         "path containing one breaks out of the attribute")

    def test_values_are_assigned_as_properties(self):
        js = self.source()
        block = js.split("function attachFolderRows(", 1)[1]
        block = block.split("function loadFolders(", 1)[0]

        self.assertIn("nameInput.value = ", block)
        self.assertIn("pathInput.value = ", block)


class TheRoutesAreRegisteredAndGuarded(unittest.TestCase):

    def test_both_routes_exist_and_need_a_session(self):
        try:
            import flask  # noqa: F401
        except ImportError:
            self.skipTest("flask is not installed in this environment")

        import adminchat
        import defaults as config

        config.ADMIN_PASSWORD_HASH = adminchat.make_password_hash("pw")
        app = webserver.create_app()
        anon = app.test_client()

        self.assertNotEqual(anon.get("/api/folders").status_code, 200)
        self.assertNotEqual(
            anon.post("/api/folders", data=json.dumps({"folders": []}),
                      content_type="application/json").status_code, 200)


if __name__ == "__main__":
    unittest.main()
