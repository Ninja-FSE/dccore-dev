"""Preflight never looked at the temp folder (#1149).

Its state guard watches settings.conf, conf/ and data/, so the 236 entries
each run left in the operator's temp folder went unnoticed until 232,000 of
them had built up. Every pass now runs with TEMP, TMP and TMPDIR pointed at a
fresh folder of preflight's own; what a pass leaves there is named, fails the
pass, and is cleared so the next pass is judged on its own; the folder goes
when preflight ends.

Dev-only, like the script it covers: stripped with it at release (see
docs/PUBLIC-REPO-WORKFLOW.md).
"""

import io
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.support import temp_dir  # noqa: E402
from tests.test_preflight_checks_every_pass_for_state_writes import load_preflight  # noqa: E402


class TheLeftoversAreNamed(unittest.TestCase):

    def setUp(self):
        self.preflight = load_preflight()
        self.folder = temp_dir(self, prefix="dccore-preflight-test-")

    def report(self):
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            ok = self.preflight.report_temp_leftovers(self.folder)
        return ok, out.getvalue()

    def test_an_empty_folder_passes_quietly(self):
        self.assertEqual(self.report(), (True, ""))

    def test_what_a_pass_left_is_named_by_family_and_fails_it(self):
        for name in ("dccore-list-index-ab12", "dccore-list-index-cd34", "tmpxyz987"):
            os.makedirs(os.path.join(self.folder, name))
        with io.open(os.path.join(self.folder, "tmpfile.txt"), "w", encoding="utf-8") as handle:
            handle.write("x")

        ok, said = self.report()

        self.assertFalse(ok)
        self.assertIn("LEFT 4 ENTRIES", said)
        self.assertIn("   2  dccore-list-index-*   e.g. dccore-list-index-ab12", said)
        self.assertIn("   2  tmp*", said)

    def test_the_folder_is_emptied_for_the_next_pass(self):
        os.makedirs(os.path.join(self.folder, "dccore-test-ab12", "inner"))
        with io.open(os.path.join(self.folder, "loose.txt"), "w", encoding="utf-8") as handle:
            handle.write("x")

        self.report()

        self.assertEqual(os.listdir(self.folder), [])
        self.assertEqual(self.report(), (True, ""))

    def test_the_sink_for_a_late_thread_write_is_expected(self):
        os.makedirs(os.path.join(self.folder, "dccore-orphaned-test-write"))

        self.assertEqual(self.report(), (True, ""))


class EveryPassRunsInAFolderOfItsOwn(unittest.TestCase):

    def test_the_children_inherit_it_and_it_is_new(self):
        preflight = load_preflight()
        with mock.patch.dict(os.environ), mock.patch.object(preflight.atexit, "register") as register:
            folder = preflight.private_temp()
            self.addCleanup(os.rmdir, folder)
            for name in ("TEMP", "TMP", "TMPDIR"):
                self.assertEqual(os.environ[name], folder)
        self.assertEqual(os.listdir(folder), [])
        self.assertEqual(os.path.dirname(folder), tempfile.gettempdir())
        register.assert_called_once()
        self.assertEqual(register.call_args[0][1:], (folder, True), "the folder is not removed at exit")

    def source(self):
        with io.open(os.path.join(REPO_ROOT, "scripts", "preflight.py"), encoding="utf-8") as handle:
            return handle.read().split("def main():", 1)[1]

    def test_it_is_made_before_the_first_pass(self):
        main = self.source()

        self.assertLess(main.index("    suite_temp = private_temp()\n"),
                        main.index("    results = [run(label, argv) for label, argv in checks]\n"))

    def test_every_state_check_also_checks_the_temp_folder(self):
        main = self.source()
        joined = "results.append(report_state_writes(state_before, state_snapshot()) and left_nothing)"

        self.assertEqual(main.count("left_nothing = report_temp_leftovers(suite_temp)\n"), 3)
        self.assertEqual(main.count(joined), 3)
        self.assertEqual(main.count("report_state_writes(state_before, state_snapshot())"), 3)


if __name__ == "__main__":
    unittest.main()
