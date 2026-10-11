"""The dcx.dll that ships with dccore.mrc is the release it says it is (#1288).

A binary nobody here built is shipped, so what it is has to be pinned in three
places that are checked against each other: the file itself, the README beside
it, and the script, which refuses to load a file whose SHA-256 differs. Change
one without the others and this fails.

It also holds the dll to what the README promises: 32-bit (the reason a 64-bit
mIRC falls back), importing nothing but Windows' own libraries (the reason
WebView2Loader.dll is not shipped), with DCX's BSD-3-Clause notice beside it.
"""

import hashlib
import io
import os
import re
import struct
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DCX_DIR = os.path.join(REPO_ROOT, "scripts", "mirc", "dcx")
DLL = os.path.join(DCX_DIR, "dcx.dll")
README = os.path.join(DCX_DIR, "README.md")
LICENSE = os.path.join(DCX_DIR, "LICENSE-DCX.txt")
SCRIPT = os.path.join(REPO_ROOT, "scripts", "mirc", "dccore.mrc")

# What a stock Windows install has. A dll outside this list would have to be
# shipped too, or the load fails on a machine without it.
WINDOWS_DLLS = {
    "advapi32.dll", "comctl32.dll", "comdlg32.dll", "crypt32.dll", "gdi32.dll", "gdiplus.dll",
    "kernel32.dll", "msimg32.dll", "ole32.dll", "oleaut32.dll", "shell32.dll", "shlwapi.dll",
    "urlmon.dll", "user32.dll", "uxtheme.dll", "version.dll", "winmm.dll",
}


def read(path, mode="r"):
    if "b" in mode:
        with open(path, "rb") as handle:
            return handle.read()
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


class PE(object):
    """Just enough of a PE32 file to read its machine and its import lists."""

    def __init__(self, data):
        self.data = data
        self.pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[self.pe:self.pe + 4] != b"PE\0\0":
            raise AssertionError("not a PE file")
        self.machine = struct.unpack_from("<H", data, self.pe + 4)[0]
        sections = struct.unpack_from("<H", data, self.pe + 6)[0]
        optional_size = struct.unpack_from("<H", data, self.pe + 20)[0]
        self.optional = self.pe + 24
        self.magic = struct.unpack_from("<H", data, self.optional)[0]
        self.sections = []
        at = self.optional + optional_size
        for _ in range(sections):
            virtual_size, rva, raw_size, raw = struct.unpack_from("<IIII", data, at + 8)
            self.sections.append((rva, max(virtual_size, raw_size), raw))
            at += 40

    def offset(self, rva):
        for start, size, raw in self.sections:
            if start <= rva < start + size:
                return rva - start + raw
        raise AssertionError("rva %#x is in no section" % rva)

    def text(self, rva):
        at = self.offset(rva)
        return self.data[at:self.data.index(b"\0", at)].decode("ascii")

    def directory(self, index):
        return struct.unpack_from("<I", self.data, self.optional + 96 + 8 * index)[0]

    def imports(self):
        names, at = [], self.offset(self.directory(1))
        while True:
            entry = struct.unpack_from("<5I", self.data, at)
            if entry[3] == 0:
                return names
            names.append(self.text(entry[3]))
            at += 20

    def delay_imports(self):
        rva = self.directory(13)
        if not rva:
            return []
        names, at = [], self.offset(rva)
        while True:
            entry = struct.unpack_from("<8I", self.data, at)
            if entry[1] == 0:
                return names
            names.append(self.text(entry[1]))
            at += 32


class TheFileIsTheOneItSaysItIs(unittest.TestCase):

    def test_the_readme_names_the_files_hash(self):
        actual = hashlib.sha256(read(DLL, "rb")).hexdigest()
        named = re.findall(r"`([0-9a-f]{64})`", read(README))
        self.assertEqual(named, [actual],
                         "scripts/mirc/dcx/README.md must name exactly the SHA-256 of the dcx.dll beside it")

    def test_the_script_refuses_anything_else(self):
        actual = hashlib.sha256(read(DLL, "rb")).hexdigest()
        pinned = re.search(r"^alias dccore\.dcx\.sha256 \{ return ([0-9a-f]{64}) \}$", read(SCRIPT), re.M)
        self.assertIsNotNone(pinned, "dccore.mrc has no dccore.dcx.sha256")
        self.assertEqual(pinned.group(1), actual, "dccore.dcx.sha256 is not the shipped dcx.dll's SHA-256")

    def test_the_readme_names_the_project_and_the_release(self):
        text = read(README)
        self.assertIn("https://github.com/twig/dcxdll", text)
        self.assertRegex(text, r"releases/tag/3\.1-git\d+")
        self.assertIn("BSD-3-Clause", text)


class TheFileIsWhatTheFallbackAssumes(unittest.TestCase):

    def setUp(self):
        self.pe = PE(read(DLL, "rb"))

    def test_it_is_32_bit(self):
        # A 64-bit mIRC cannot load it: dccore.dcx.why says so by $bits
        # rather than letting the load fail.
        self.assertEqual(self.pe.machine, 0x14C, "dcx.dll is no longer x86 - revisit the $bits fallback")
        self.assertEqual(self.pe.magic, 0x10B)

    def test_it_needs_nothing_but_windows(self):
        wanted = {name.lower() for name in self.pe.imports() + self.pe.delay_imports()}
        self.assertTrue(wanted, "found no imports - the reader above is wrong")
        self.assertEqual(wanted - WINDOWS_DLLS, set(),
                         "dcx.dll imports a dll that is not Windows' own; ship it too or pick another build")

    def test_webview2loader_is_not_shipped(self):
        # Only DCX's web control loads it, at run time, and the window has none.
        self.assertNotIn("webview2loader.dll", {n.lower() for n in os.listdir(DCX_DIR)})


class TheLicenceTravelsWithIt(unittest.TestCase):

    def test_the_bsd_notice_is_beside_the_binary(self):
        text = read(LICENSE)
        self.assertIn("Copyright (c) 2013, twig", text)
        self.assertIn("Redistributions in binary form must reproduce the above copyright notice", text)
        self.assertIn("THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS \"AS IS\"", text)

    def test_the_dll_is_kept_byte_for_byte(self):
        attributes = read(os.path.join(REPO_ROOT, ".gitattributes"))
        self.assertRegex(attributes, r"(?m)^\*\.dll\s+binary$")


if __name__ == "__main__":
    unittest.main()
