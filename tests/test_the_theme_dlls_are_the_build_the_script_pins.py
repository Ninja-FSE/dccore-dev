"""The theme's two DLLs are the build the script pins (#1368).

dccore.mrc darkens its dialogs and puts icons beside its menu items through
two DLLs of DCCore's own, scripts/mirc/dll/dccore_ui.dll and
dccore_icons.dll. As with dcx.dll (tests/test_the_bundled_dcx_dll_is_the_
pinned_release.py), a shipped binary is pinned in three places that are
checked against each other: the file itself, the README beside it, and the
script, which refuses a dccore_ui.dll whose SHA-256 differs. Change one
without the others and this fails.

It also holds the files to what the README promises: 32-bit, importing
nothing but Windows' own libraries, the icons' names in the script in the
order the README gives them and as many of them as the file holds, and the
sources beside the binaries.
"""

import hashlib
import io
import os
import re
import struct
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.test_the_bundled_dcx_dll_is_the_pinned_release import PE, WINDOWS_DLLS  # noqa: E402

DLL_DIR = os.path.join(REPO_ROOT, "scripts", "mirc", "dll")
UI = os.path.join(DLL_DIR, "dccore_ui.dll")
ICONS = os.path.join(DLL_DIR, "dccore_icons.dll")
README = os.path.join(DLL_DIR, "README.md")
UI_SOURCE = os.path.join(DLL_DIR, "src", "dccore_ui.c")
ICON_SOURCE = os.path.join(DLL_DIR, "src", "make_icons.py")
SCRIPT = os.path.join(REPO_ROOT, "scripts", "mirc", "dccore.mrc")

# Two beyond the DCX test's list, both on every Windows: the dark title bar
# is DWM's (dwmapi.dll), and a mingw build's C runtime is msvcrt.dll.
WINDOWS_DLLS_TOO = WINDOWS_DLLS | {"dwmapi.dll", "msvcrt.dll"}
RT_GROUP_ICON = 14


def read(path, mode="r"):
    if "b" in mode:
        with open(path, "rb") as handle:
            return handle.read()
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def sha256(path):
    return hashlib.sha256(read(path, "rb")).hexdigest()


def resource_ids(pe, resource_type):
    """The ids under one type in the dll's resource tree: a directory is
    sixteen bytes of header with the counts of named and numbered entries,
    then eight bytes per entry; an entry's offset is from the tree's root,
    and its high bit says it is a directory itself."""
    root = pe.offset(pe.directory(2))

    def entries(at):
        named, numbered = struct.unpack_from("<HH", pe.data, at + 12)
        for index in range(named + numbered):
            yield struct.unpack_from("<II", pe.data, at + 16 + 8 * index)

    for name, offset in entries(root):
        if name == resource_type and offset & 0x80000000:
            return [entry_name for entry_name, _ in entries(root + (offset & 0x7FFFFFFF))]
    return []


def icon_names_in(text, pattern):
    found = re.search(pattern, text)
    assert found, pattern
    return found.group(1).split()


class TheFilesAreTheOnesTheReadmeNames(unittest.TestCase):

    def test_the_readme_names_the_files_hashes(self):
        named = re.findall(r"`([0-9a-f]{64})`", read(README))
        self.assertEqual(named, [sha256(UI), sha256(ICONS)],
                         "scripts/mirc/dll/README.md must name the SHA-256 of dccore_ui.dll, then of dccore_icons.dll")

    def test_the_script_refuses_any_other_ui_dll(self):
        pinned = re.search(r"^alias dccore\.theme\.uisha256 \{ return ([0-9a-f]{64}) \}$", read(SCRIPT), re.M)
        self.assertIsNotNone(pinned, "dccore.mrc has no dccore.theme.uisha256")
        self.assertEqual(pinned.group(1), sha256(UI), "dccore.theme.uisha256 is not the shipped file's SHA-256")
        body = read(SCRIPT)
        self.assertIn("if ($dccore.st(ui.sha) != $dccore.theme.uisha256) { return dll\\dccore_ui.dll is not the build "
                      "this script was shipped with }", body)

    def test_the_readme_names_the_version_the_dll_answers(self):
        version = re.search(r"`Version` - `(dccore_ui \d+\.\d+)`", read(README))
        self.assertIsNotNone(version, "the README does not say what Version answers")
        self.assertIn(version.group(1).encode("ascii"), read(UI, "rb"))

    def test_the_folder_holds_the_two_and_nothing_else_binary(self):
        self.assertEqual({n.lower() for n in os.listdir(DLL_DIR) if n.lower().endswith(".dll")},
                         {"dccore_ui.dll", "dccore_icons.dll"})

    def test_the_build_is_the_same_file_every_time(self):
        # --no-insert-timestamp: the hash above can be checked against a
        # build of one's own, which is the point of shipping the source
        text = read(README)
        self.assertEqual(text.count("-Wl,--no-insert-timestamp"), 2, "both build lines need --no-insert-timestamp")
        self.assertIn("i686-w64-mingw32-gcc", text)


class TheFilesAreWhatTheReadmePromises(unittest.TestCase):

    def setUp(self):
        self.ui = PE(read(UI, "rb"))
        self.icons = PE(read(ICONS, "rb"))

    def test_both_are_32_bit(self):
        # A 64-bit mIRC cannot load them, as it cannot load dcx.dll
        for pe in (self.ui, self.icons):
            self.assertEqual(pe.machine, 0x14C)
            self.assertEqual(pe.magic, 0x10B)

    def test_they_need_nothing_but_windows(self):
        for name, pe in (("dccore_ui.dll", self.ui), ("dccore_icons.dll", self.icons)):
            wanted = {n.lower() for n in pe.imports() + pe.delay_imports()}
            self.assertTrue(wanted, "found no imports in " + name)
            self.assertEqual(wanted - WINDOWS_DLLS_TOO, set(),
                             name + " imports a dll that is not Windows' own; ship it too or build without it")

    def test_the_icon_file_holds_pictures_only(self):
        # Nothing of it is ever run: no exports beyond DllMain's file, a
        # resource tree with the icons in it
        self.assertEqual({n.lower() for n in self.icons.imports()}, {"kernel32.dll", "msvcrt.dll"})
        self.assertNotEqual(self.icons.directory(2), 0, "dccore_icons.dll has no resources")
        self.assertNotIn(b"ApplyTheme", read(ICONS, "rb"))

    def test_the_icons_are_the_ones_the_script_names_in_that_order(self):
        in_script = icon_names_in(read(SCRIPT), r"(?m)^alias dccore\.ico\.names \{ return ([a-z ]+) \}$")
        in_readme = icon_names_in(read(README), r"must list them in the same order: `([a-z ]+)`")
        self.assertEqual(in_script, in_readme)
        self.assertEqual(len(in_script), len(set(in_script)), "an icon is named twice")
        groups = resource_ids(self.icons, RT_GROUP_ICON)
        self.assertEqual(len(groups), len(in_script),
                         "dccore_icons.dll holds %d icons, the script names %d" % (len(groups), len(in_script)))
        self.assertEqual(sorted(groups), list(range(1, len(in_script) + 1)), "the icons are not numbered 1 up")
        self.assertIn("%d icons" % len(in_script), read(README))

    def test_the_drawing_script_draws_them_in_the_same_order(self):
        names = icon_names_in(read(SCRIPT), r"(?m)^alias dccore\.ico\.names \{ return ([a-z ]+) \}$")
        drawn = re.findall(r'\("([a-z]+)", [a-z_]+\)', read(ICON_SOURCE))
        self.assertEqual(drawn, names, "src/make_icons.py draws the icons in another order than the script names them")


class TheSourceIsBesideTheBinary(unittest.TestCase):

    def test_the_ui_source_is_what_the_script_relies_on(self):
        source = read(UI_SOURCE)
        # mIRC's calling shape, exported: $dll(file,ApplyTheme,...) and the rest
        self.assertIn("#define MIRC_PROC(name) __declspec(dllexport) int __stdcall name(HWND mWnd, HWND aWnd, "
                      "char *data, char *parms, BOOL show, BOOL nopause)", source)
        for export in ("ApplyTheme", "MenuIcons", "Version"):
            self.assertIn("MIRC_PROC(%s)" % export, source)
        for export in ("LoadDll", "UnloadDll"):
            self.assertRegex(source, r"__declspec\(dllexport\)[^\n]*\b%s\(" % export, export)
        # mKeep: mIRC keeps the dll loaded instead of dropping it after ten
        # idle minutes, which is what lets the script ask whether it is
        # loaded before turning the menu hook off (dccore.theme.uiloaded)
        self.assertIn("li->mKeep = TRUE", source)
        # the hook and the subclasses come off again when the dll goes
        self.assertIn("UnloadDll", source)

    def test_the_sources_and_the_readme_are_plain_ascii(self):
        for path in (UI_SOURCE, ICON_SOURCE, README, os.path.join(DLL_DIR, "src", "dll.c")):
            read(path, "rb").decode("ascii")

    def test_the_readme_says_what_a_missing_file_costs(self):
        text = read(README)
        self.assertIn("a missing `dccore_ui.dll` leaves the dialogs light", text)
        self.assertIn("a missing `dccore_icons.dll` leaves the menus", text)


if __name__ == "__main__":
    unittest.main()
