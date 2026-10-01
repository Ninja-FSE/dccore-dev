"""Everything the window shows is also written to a log file (#1065).

What the bot said was gone when its window was closed, and #1065 goes on to
let it run with no window at all. So every console line also goes to
CONSOLE_LOG_FILE, with the date on it, through the one wrapper every console
line already passes - not a second proxy on sys.stdout - and the file is
started afresh at CONSOLE_LOG_MAX_MB, keeping CONSOLE_LOG_KEEP old ones.
Nothing about the log can take a line away from the window.
"""

import io
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from tests import support  # noqa: F401  (path setup)

import platform_compat  # noqa: E402

REPO_ROOT = support.REPO_ROOT
DATED = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] ")
WINDOW = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] ")


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dccore-console-log-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "logs", "dccore.log")
        self.max_bytes, self.keep = 0, 5
        platform_compat.install_console_log(lambda: (self.path, self.max_bytes, self.keep))
        self.addCleanup(platform_compat.install_console_log, None)
        self.addCleanup(platform_compat._console_log.close)
        self.window = io.StringIO()
        self.stream = platform_compat._TimestampedStream(self.window, lambda: "%H:%M:%S")

    def logged(self, path=None):
        platform_compat._console_log.close()
        with io.open(path or self.path, encoding="utf-8") as handle:
            return handle.read()


class WhatIsWritten(Case):
    def test_the_window_is_unchanged_and_the_file_has_the_date(self):
        self.stream.write("first line\nsecond ")
        self.stream.write("half\n")
        window = self.window.getvalue().splitlines()
        self.assertEqual([WINDOW.sub("", l) for l in window], ["first line", "second half"])
        self.assertTrue(all(WINDOW.match(l) for l in window), window)
        logged = self.logged().splitlines()
        self.assertEqual([DATED.sub("", l) for l in logged], ["first line", "second half"])
        self.assertTrue(all(DATED.match(l) for l in logged), logged)

    def test_with_no_window_stamp_the_file_still_has_one(self):
        stream = platform_compat._TimestampedStream(self.window, lambda: "")
        stream.write("plain\n")
        self.assertEqual(self.window.getvalue(), "plain\n")
        self.assertRegex(self.logged(), DATED.pattern + "plain\n")

    def test_its_folder_is_made(self):
        self.stream.write("x\n")
        self.assertTrue(os.path.isfile(self.path))

    def test_an_empty_path_writes_no_file(self):
        self.path = ""
        self.stream.write("x\n")
        self.assertEqual(os.listdir(self.dir), [])
        self.assertTrue(WINDOW.match(self.window.getvalue()))
        self.assertTrue(platform_compat._console_log.active(),
                        "an empty path is 'none for now', not a failure that stays off")

    def test_a_changed_path_takes_effect_at_once(self):
        self.stream.write("one\n")
        first = self.path
        self.path = os.path.join(self.dir, "elsewhere.log")
        self.stream.write("two\n")
        self.assertIn("one", self.logged(first))
        self.assertNotIn("two", self.logged(first))
        self.assertIn("two", self.logged(self.path))

    def test_bytes_are_still_refused_as_a_real_stream_does(self):
        """click probes with write(b"") - see _TimestampedStream.write()."""
        with self.assertRaises(TypeError):
            self.stream.write(b"")


class Rotation(Case):
    def test_a_full_file_moves_to_dot_1_and_the_oldest_beyond_keep_goes(self):
        self.max_bytes, self.keep = 200, 2
        for number in range(20):
            self.stream.write(f"line {number:02d} " + "x" * 40 + "\n")
        platform_compat._console_log.close()
        names = sorted(os.listdir(os.path.dirname(self.path)))
        self.assertEqual(names, ["dccore.log", "dccore.log.1", "dccore.log.2"])
        self.assertIn("line 19", self.logged())
        self.assertTrue(os.path.getsize(self.path + ".1") >= 200)

    def test_a_file_that_cannot_be_renamed_is_written_on(self):
        """A viewer holding the file open on Windows: carry on, try later."""
        self.max_bytes = 50
        with mock.patch.object(platform_compat.os, "replace", side_effect=PermissionError("in use")):
            for number in range(5):
                self.stream.write(f"line {number} " + "y" * 30 + "\n")
        self.assertEqual(self.logged().count("line "), 5)
        self.assertTrue(platform_compat._console_log.active())


class WhenTheFileCannotBeWritten(Case):
    def test_the_window_keeps_every_line_and_the_log_says_so_once(self):
        os.makedirs(self.path)   # a folder where the file should be
        told = io.StringIO()
        with mock.patch.object(platform_compat.sys, "__stdout__", told):
            self.stream.write("one\n")
            self.stream.write("two\n")
        self.assertEqual([WINDOW.sub("", l) for l in self.window.getvalue().splitlines()],
                         ["one", "two"])
        self.assertEqual(told.getvalue().count("[LOG] Could not write the log file"), 1)
        self.assertFalse(platform_compat._console_log.active())


class TheDaemonInstallsIt(unittest.TestCase):
    def test_after_the_window_format_and_only_as_the_program(self):
        with io.open(os.path.join(REPO_ROOT, "oserve.py"), encoding="utf-8") as handle:
            code = handle.read()
        lines = code.splitlines()
        start = lines.index('if __name__ == "__main__":', lines.index("import defaults as config"))
        block = []
        for line in lines[start + 1:]:
            if line and not line.startswith(" "):
                break   # back at module level: the block has ended
            block.append(line)
        block = "\n".join(block)
        self.assertIn("platform_compat.set_console_timestamp_format(", block)
        self.assertIn("    platform_compat.install_console_log(_console_log_settings)", block,
                      "the log is installed outside the program-only block, so an import of "
                      "oserve (every test process) would start writing it")


if __name__ == "__main__":
    unittest.main()
