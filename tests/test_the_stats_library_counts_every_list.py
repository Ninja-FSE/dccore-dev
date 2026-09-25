"""The Stats page's Library block counts every served list, not the primary alone (#952).

`build_stats_payload()` read the library without saying which list: no name to
`get_file_count_date_size_and_raw_bytes()`, so the primary's files and side
files, and `count_rar_album_folders()` looked only in LOCAL_LIST_DIR. On a bot
serving a music list and a film list the page showed the music list's files,
size and album folders as if they were the library - and the channel adverts,
which do ask per list, disagreed with it.

Now the four Library figures are totals across every list, `library.lists`
holds one row per list, and a single-list install reads exactly as before.

Two halves. The payload tests build a real two-list install on disk. The page
tests read app.js, and run the real `renderLibraryLists()` under node against a
stub DOM - a guard that reads text cannot tell that the table is hidden for one
list and shown for two - skipped where node is not installed.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import defaults as config  # noqa: E402
import list as list_mod  # noqa: E402
import webserver  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402

GIB = 1024 ** 3


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class TwoLists(DCCoreTestCase):
    """A primary list of 3 files and a second one of 2, each with its own side
    files - the layout update_list.py writes: the primary in LOCAL_LIST_DIR
    itself, every other list in a directory of its own."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="dccore-statslib-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.lists_dir = os.path.join(self.tmp, "lists")
        os.makedirs(self.lists_dir)
        self.set_config(LOCAL_LIST_DIR=self.lists_dir, NICKNAME="SomeBot",
                        LIST_BASE_NAME="SomeBot")
        self.write_lists_file([
            {"name": "music", "primary": True, "channels": [], "folders": []},
            {"name": "video", "primary": False, "channels": ["#somechannel"], "folders": []},
        ])
        self.video_dir = list_mod.list_dir("video")
        self.assertNotEqual(self.video_dir, self.lists_dir, "the second list has its own directory")

        self.build("music", self.lists_dir, files=3, raw=GIB + GIB // 2, size="1.50GB")
        self.build("video", self.video_dir, files=2, raw=GIB // 2, size="512.00MB")

    def write_lists_file(self, entries):
        path = os.path.join(self.tmp, "lists.json")
        write(path, json.dumps(entries))
        self.set_config(LISTS_FILE=path)

    def build(self, name, directory, files, raw, size, rar=None, when=None):
        """What one rebuild leaves in a list's directory."""
        master = os.path.join(directory, "SomeBot-2026-09-07.txt")
        write(master, "List of %d Files\n" % files
              + "".join("!SomeBot Track %s %d.mp3  ::INFO:: 1.0MB\n" % (name, n) for n in range(files)))
        write(os.path.join(directory, config.LIST_SIZE_FILE), size)
        write(os.path.join(directory, config.LIST_RAWBYTES_FILE), str(raw))
        if rar is not None:
            write(os.path.join(directory, "SomeBot-RAR-2026-09-07.txt"),
                  "List of Entire Album Folders\nx\n" + "=" * 20 + "\n\n"
                  + "".join("!SomeBot !rar D:\\%s\\Album %d\\\n" % (name, n) for n in range(rar)))
        if when is not None:
            os.utime(master, (when, when))
        return master


class TheTotalsCoverEveryList(TwoLists):

    def test_the_files_are_summed(self):
        self.assertEqual(webserver.build_library_payload()["files"], 5)

    def test_the_bytes_are_summed_and_the_size_is_written_from_the_sum(self):
        library = webserver.build_library_payload()

        self.assertEqual(library["raw_bytes"], 2 * GIB)
        self.assertEqual(library["size"], "2.00GB",
                         "the total, in the two-decimal style the lists' own size files use")

    def test_the_album_folders_are_summed_over_the_lists_that_have_a_rar_list(self):
        self.build("music", self.lists_dir, files=3, raw=GIB, size="1.00GB", rar=7)
        self.build("video", self.video_dir, files=2, raw=GIB, size="1.00GB", rar=4)

        self.assertEqual(webserver.build_library_payload()["rar_folders"], 11)

    def test_a_list_with_no_rar_list_adds_nothing_but_does_not_make_the_total_unknown(self):
        self.build("music", self.lists_dir, files=3, raw=GIB, size="1.00GB", rar=7)

        library = webserver.build_library_payload()

        self.assertEqual(library["rar_folders"], 7)
        video = [row for row in library["lists"] if row["name"] == "video"][0]
        self.assertIsNone(video["rar_folders"], "unknown for that list, not zero")

    def test_no_rar_list_anywhere_is_unknown_not_zero(self):
        self.assertIsNone(webserver.build_library_payload()["rar_folders"])

    def test_the_list_built_date_is_the_newest_build(self):
        older, newer = 1_600_000_000, 1_800_000_000
        self.build("music", self.lists_dir, files=3, raw=GIB, size="1.00GB", when=older)
        self.build("video", self.video_dir, files=2, raw=GIB, size="1.00GB", when=newer)

        library = webserver.build_library_payload()

        by_name = {row["name"]: row for row in library["lists"]}
        self.assertNotEqual(by_name["music"]["list_date"], by_name["video"]["list_date"])
        self.assertEqual(library["list_date"], by_name["video"]["list_date"])


class EachListHasItsOwnRow(TwoLists):

    def test_one_row_per_list_in_the_operators_order_with_its_own_figures(self):
        rows = webserver.build_library_payload()["lists"]

        self.assertEqual([row["name"] for row in rows], ["music", "video"])
        self.assertEqual([row["primary"] for row in rows], [True, False])
        self.assertEqual([row["files"] for row in rows], [3, 2])
        self.assertEqual([row["size"] for row in rows], ["1.50GB", "512.00MB"])
        self.assertEqual([row["raw_bytes"] for row in rows], [GIB + GIB // 2, GIB // 2])

    def test_the_primary_is_asked_for_with_no_name_and_the_other_by_name(self):
        """The primary keeps the path the page has always taken; only a list
        that is not the primary is named."""
        asked = []
        real = list_mod.get_file_count_date_size_and_raw_bytes

        def spy(name=None):
            asked.append(name)
            return real(name)

        list_mod.get_file_count_date_size_and_raw_bytes = spy
        self.addCleanup(setattr, list_mod, "get_file_count_date_size_and_raw_bytes", real)

        webserver.build_library_payload()

        self.assertEqual(asked, [None, "video"])

    def test_the_rar_count_reads_the_named_lists_own_directory(self):
        self.build("video", self.video_dir, files=2, raw=GIB, size="1.00GB", rar=4)

        self.assertEqual(webserver.count_rar_album_folders("video"), 4)
        self.assertIsNone(webserver.count_rar_album_folders(),
                          "and the primary's directory is still where no name looks")


class OneBrokenListCostsItsOwnRowOnly(TwoLists):

    def test_a_list_that_cannot_be_read_leaves_the_other_in_the_totals(self):
        real = list_mod.get_file_count_date_size_and_raw_bytes

        def broken_for_video(name=None):
            if name == "video":
                raise OSError("an AV scanner is holding the list")
            return real(name)

        list_mod.get_file_count_date_size_and_raw_bytes = broken_for_video
        self.addCleanup(setattr, list_mod, "get_file_count_date_size_and_raw_bytes", real)

        library = webserver.build_library_payload()

        self.assertEqual(library["files"], 3)
        self.assertEqual(library["raw_bytes"], GIB + GIB // 2)
        self.assertEqual([row["files"] for row in library["lists"]], [3, 0])

    def test_a_lists_file_that_cannot_be_read_falls_back_to_the_one_primary(self):
        import library

        original = library.lists

        def boom():
            raise ValueError("lists.json is unreadable")

        library.lists = boom
        self.addCleanup(setattr, library, "lists", original)

        result = webserver.build_library_payload()

        self.assertEqual(result["files"], 3, "the primary, as the page always showed it")
        self.assertEqual(len(result["lists"]), 1)


class ASingleListInstallReadsAsBefore(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="dccore-statslib1-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.lists_dir = os.path.join(self.tmp, "lists")
        os.makedirs(self.lists_dir)
        self.set_config(LOCAL_LIST_DIR=self.lists_dir, NICKNAME="SomeBot",
                        LIST_BASE_NAME="SomeBot",
                        LISTS_FILE=os.path.join(self.tmp, "no-such-lists.json"))
        write(os.path.join(self.lists_dir, "SomeBot-2026-09-07.txt"),
              "List of 2 Files\n!SomeBot A.mp3  ::INFO:: 1.0MB\n!SomeBot B.mp3  ::INFO:: 1.0MB\n")
        # A stored size that is NOT what the bytes would format to: proves the
        # single-list path shows the list's own string, untouched.
        write(os.path.join(self.lists_dir, config.LIST_SIZE_FILE), "1.85TB")
        write(os.path.join(self.lists_dir, config.LIST_RAWBYTES_FILE), "4096")

    def test_the_totals_are_the_one_lists_own_figures(self):
        library = webserver.build_library_payload()

        self.assertEqual(library["files"], 2)
        self.assertEqual(library["size"], "1.85TB")
        self.assertEqual(library["raw_bytes"], 4096)
        self.assertEqual(len(library["lists"]), 1)

    def test_it_goes_through_the_stats_payload_the_same_way(self):
        self.assertEqual(webserver.build_stats_payload()["library"]["files"], 2)

    def test_a_bot_with_nothing_built_yet_says_so_rather_than_zero_albums(self):
        os.remove(os.path.join(self.lists_dir, "SomeBot-2026-09-07.txt"))

        library = webserver.build_library_payload()

        self.assertEqual(library["files"], 0)
        self.assertIsNone(library["rar_folders"])


def read_app_js():
    with io.open(os.path.join(REPO_ROOT, "web", "app.js"), encoding="utf-8") as handle:
        return handle.read()


class ThePageShowsTheBreakdownOnlyForSeveralLists(unittest.TestCase):

    def test_the_render_is_called_from_the_stats_render(self):
        code = read_app_js()
        at = code.index('setStat(el.stBuilt, lib.list_date || "—");')
        self.assertIn("renderLibraryLists(lib.lists);", code[at:at + 200])

    def test_list_names_are_written_as_text_not_markup(self):
        """A list's name is whatever the operator typed on the Library page."""
        code = read_app_js()
        start = code.index("function renderLibraryLists(lists)")
        body = code[start:code.index("\n  }\n", start)]
        self.assertIn("td.textContent = String(cell[0]);", body)
        self.assertNotIn("innerHTML =", body.replace('el.stListsBody.innerHTML = "";', ""))

    def test_the_block_starts_hidden_and_the_labels_exist_in_every_language(self):
        with io.open(os.path.join(REPO_ROOT, "web", "index.html"), encoding="utf-8") as handle:
            html = handle.read()
        self.assertRegex(html, r'<div id="st-lists" hidden>')
        for code in ("en", "es", "fr"):
            with io.open(os.path.join(REPO_ROOT, "web", "lang", code + ".json"),
                         encoding="utf-8") as handle:
                strings = json.load(handle)
            for key in ("stats.byList", "stats.listName"):
                with self.subTest(language=code, key=key):
                    self.assertTrue(strings.get(key), "%s.json has no %s" % (code, key))


HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const start = src.indexOf("function renderLibraryLists(lists)");
if (start < 0) { throw new Error("renderLibraryLists is missing"); }
let depth = 0, i = src.indexOf("{", start);
for (; i < src.length; i++) {
  if (src[i] === "{") { depth++; }
  else if (src[i] === "}") { depth--; if (depth === 0) { break; } }
}
const fn = src.slice(start, i + 1);

function element() {
  return { children: [], className: "", title: "", textContent: "",
    appendChild(c) { this.children.push(c); if (this.children.length === 1) { this.firstChild = c; } return c; },
    set innerHTML(v) { this.children = []; this.firstChild = undefined; } };
}
const document = { createElement: element };
function run(lists) {
  const el = { stLists: { hidden: true }, stListsBody: element() };
  new Function("el", "document", fn + "\nrenderLibraryLists(" + JSON.stringify(lists) + ");")(el, document);
  return el;
}
const out = [];
let el = run([{ name: "only", files: 3, size: "1GB", rar_folders: 1, list_date: "Sep 1st" }]);
out.push("oneHidden=" + el.stLists.hidden);
out.push("oneRows=" + el.stListsBody.children.length);
el = run([]);
out.push("noneHidden=" + el.stLists.hidden);
el = run(null);
out.push("nullHidden=" + el.stLists.hidden);
el = run([
  { name: "music", files: 64136, size: "1.85TB", rar_folders: 11322, list_date: "Sep 25th" },
  { name: "<b>video</b>", files: 2, size: "512.00MB", rar_folders: null, list_date: null }
]);
out.push("twoHidden=" + el.stLists.hidden);
out.push("twoRows=" + el.stListsBody.children.length);
const cells = (r) => el.stListsBody.children[r].children.map(c => c.textContent);
out.push("row0=" + cells(0).join("|"));
out.push("row1=" + cells(1).join("|"));
out.push("row1Title=" + el.stListsBody.children[1].firstChild.title);
out.push("row0NumClasses=" + el.stListsBody.children[0].children.map(c => c.className).join(","));
console.log(out.join("\n"));
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed; CI's runners have it")
class TheRealRenderHidesAndShows(unittest.TestCase):

    def seen(self):
        handle, path = tempfile.mkstemp(suffix=".js")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(HARNESS)
            done = subprocess.run(["node", path, os.path.join(REPO_ROOT, "web", "app.js")],
                                  capture_output=True, timeout=60)
        finally:
            os.unlink(path)
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        return dict(line.split("=", 1) for line in done.stdout.decode("utf-8").splitlines())

    def test_one_list_or_none_shows_nothing_extra(self):
        seen = self.seen()

        self.assertEqual(seen["oneHidden"], "true")
        self.assertEqual(seen["oneRows"], "0")
        self.assertEqual(seen["noneHidden"], "true")
        self.assertEqual(seen["nullHidden"], "true")

    def test_several_lists_get_a_row_each_with_their_own_figures(self):
        seen = self.seen()

        self.assertEqual(seen["twoHidden"], "false")
        self.assertEqual(seen["twoRows"], "2")
        self.assertTrue(seen["row0"].startswith("music|"))
        self.assertIn("|1.85TB|", seen["row0"])
        self.assertTrue(seen["row0"].endswith("|Sep 25th"))

    def test_an_unknown_album_count_and_date_show_a_dash_not_zero(self):
        row = self.seen()["row1"].split("|")

        self.assertEqual(row[3], "\u2014")
        self.assertEqual(row[4], "\u2014")

    def test_a_name_with_markup_is_text_and_the_full_name_is_on_hover(self):
        seen = self.seen()

        self.assertTrue(seen["row1"].startswith("<b>video</b>|"))
        self.assertEqual(seen["row1Title"], "<b>video</b>")

    def test_the_figure_cells_are_the_numeric_column(self):
        self.assertEqual(self.seen()["row0NumClasses"], ",col-num,col-num,col-num,col-num")


if __name__ == "__main__":
    unittest.main()
