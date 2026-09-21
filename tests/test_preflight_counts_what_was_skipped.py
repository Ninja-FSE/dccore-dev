"""preflight said PASS over a suite that had skipped a hundred tests (audit
M40, #642).

Only "Ran N" was ever parsed. A skipped test is one that ran nothing, and on
Windows a good share of the launcher and OS-script tests skip: the hostile
pass strips PATH to the interpreter's directory, so cmd.exe - the operating
system, not host tooling - was unfindable and every class gated on it
skipped; from PowerShell there is no bash either, so the POSIX launcher
classes skip in the normal pass too. A launcher regression could pass local
preflight with nothing having run it, and the operator read PASS.

Now the count pass runs verbose, preflight prints how many were skipped and
every reason with its count, refuses to pass above a ceiling, and the
hostile PATH keeps System32 on Windows so the .bat tests run there.
"""

import importlib.util
import os
import shutil
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def load_preflight():
    spec = importlib.util.spec_from_file_location(
        "preflight_under_test", os.path.join(REPO_ROOT, "scripts", "preflight.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERBOSE_RUN = """test_a (tests.test_x.Alpha.test_a) ... skipped 'needs a POSIX shell'
test_b (tests.test_x.Alpha.test_b) ... ok
test_c (tests.test_y.Beta.test_c)
The docstring's first line. ... skipped "this machine does not enforce MAX_PATH"
test_d (tests.test_x.Alpha.test_d) ... skipped 'needs a POSIX shell'
setUpClass (tests.test_z.Gamma) ... skipped 'no cmd.exe'

----------------------------------------------------------------------
Ran 4 tests in 0.123s

OK (skipped=3)
"""


class TheSkipReport(unittest.TestCase):

    def setUp(self):
        self.preflight = load_preflight()

    def test_the_count_comes_from_the_summary_line(self):
        skipped, _reasons = self.preflight.skip_report(VERBOSE_RUN)

        self.assertEqual(skipped, 3)

    def test_the_reasons_are_counted_whatever_the_quote_style(self):
        _skipped, reasons = self.preflight.skip_report(VERBOSE_RUN)

        self.assertEqual(reasons, {
            "needs a POSIX shell": 2,
            "this machine does not enforce MAX_PATH": 1,
            "no cmd.exe": 1,
        })

    def test_a_run_with_no_skips_reports_none(self):
        self.assertEqual(self.preflight.skip_report("Ran 5 tests in 0.1s\n\nOK\n"), (0, {}))

    def test_the_ceiling_is_above_what_a_platform_legitimately_skips(self):
        """A dozen on a Windows box run from Git Bash, a few dozen from a
        shell with no bash, a few dozen on Linux for the Windows-only
        classes: the ceiling must not cry wolf on any of them, and must
        still catch a whole family going dark."""
        self.assertGreaterEqual(self.preflight.MAX_SKIPPED, 40)
        self.assertLessEqual(self.preflight.MAX_SKIPPED, 100)


class TheHostilePath(unittest.TestCase):

    def setUp(self):
        self.env = load_preflight().hostile_env()

    def test_the_interpreter_is_still_first(self):
        self.assertEqual(self.env["PATH"].split(os.pathsep)[0], os.path.dirname(sys.executable))

    @unittest.skipUnless(os.name == "nt", "System32 is a Windows directory")
    def test_cmd_exe_is_reachable_on_windows(self):
        """The operating system's shell is not host tooling: a bare Windows
        runner has it, and the .bat launcher tests are gated on it."""
        self.assertIsNotNone(shutil.which("cmd.exe", path=self.env["PATH"]))

    @unittest.skipUnless(os.name == "nt", "the hidden tooling is Windows-installed")
    def test_git_bash_and_git_stay_hidden_on_windows(self):
        """Program Files is still off the path - the whole point of the pass."""
        for tool in ("git", "rar", "unrar"):
            with self.subTest(tool=tool):
                self.assertIsNone(shutil.which(tool, path=self.env["PATH"]))

    @unittest.skipIf(os.name == "nt", "the POSIX shape")
    def test_only_the_interpreter_directory_elsewhere(self):
        self.assertEqual(self.env["PATH"], os.path.dirname(sys.executable))


class TheCountPassIsVerbose(unittest.TestCase):
    """skip_report() reads per-test lines; the pass it reads must produce
    them. The source is the only place that says which flags the count pass
    runs with."""

    def test_the_counted_run_passes_v(self):
        with open(os.path.join(REPO_ROOT, "scripts", "preflight.py"), encoding="utf-8") as handle:
            source = handle.read()
        counted = source.split("counted = capture(", 1)[1].split(")", 1)[0]

        self.assertIn('"-v"', counted)

    def test_and_its_output_reaches_the_report(self):
        with open(os.path.join(REPO_ROOT, "scripts", "preflight.py"), encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn("skipped, reasons = skip_report(output)", source)
        self.assertIn("if skipped > MAX_SKIPPED:", source)


if __name__ == "__main__":
    unittest.main()
