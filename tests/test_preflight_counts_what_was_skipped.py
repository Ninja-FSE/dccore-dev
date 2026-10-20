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
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

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


# The guard's own path for its denylist, not a copy of it: preflight prints
# what the guard answers, and these tests compare with the same.
from tests.test_no_personal_identifiers_ship import DENYLIST  # noqa: E402

GUARD_FILE = os.path.join(REPO_ROOT, "tests", "test_no_personal_identifiers_ship.py")

# Planted in a fake, and in a real, stderr of the probe. Preflight must never
# print it: a real import error there can quote a line of the local denylist.
STDERR_MARKER = "zzplantedstderrmarkerzz"


def answer(returncode, stdout, stderr=""):
    """What capture() returns, for a child that exited with `returncode`."""
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TheNoteAboutAnAbsentDenylist(unittest.TestCase):
    """The denylist is not tracked, and without it the identifier sweep skips.
    identifier_sweep_note() is what preflight prints about that, read from
    what the guard answered in a child process (SWEEP_PROBE): READY, ABSENT
    and its path, or anything else - given here as fake answers. The
    child's stderr is never part of the note."""

    def setUp(self):
        self.preflight = load_preflight()

    def note(self, given):
        return " ".join(self.preflight.identifier_sweep_note(given))

    def test_ready_means_no_note(self):
        self.assertEqual(self.preflight.identifier_sweep_note(
            answer(0, f"READY {DENYLIST}\n", STDERR_MARKER)), [])

    def test_absent_says_the_sweep_did_not_run(self):
        note = self.note(answer(0, f"ABSENT {DENYLIST}\n"))

        self.assertIn(f"{DENYLIST} is absent", note)
        self.assertRegex(note, r"did not\s+run")

    def test_the_path_printed_is_the_one_the_guard_gave(self):
        """Not a copy kept in preflight: a planted path comes back as given."""
        note = self.note(answer(0, "ABSENT planted/elsewhere/placeholder_list.py\n"))

        self.assertIn("planted/elsewhere/placeholder_list.py is absent", note)
        self.assertNotIn(DENYLIST, note)

    def test_any_other_answer_says_the_sweep_will_skip(self):
        cases = {
            "a non-zero exit": answer(1, ""),
            "a non-zero exit after READY": answer(1, f"READY {DENYLIST}\n"),
            "no output": answer(0, ""),
            "no stdout at all": answer(0, None),
            "READY alone": answer(0, "READY\n"),
            "an unknown word": answer(0, f"MAYBE {DENYLIST}\n"),
            "a third field": answer(0, f"READY {DENYLIST} extra\n"),
        }
        for what, given in cases.items():
            with self.subTest(what):
                lines = self.preflight.identifier_sweep_note(given)
                self.assertNotEqual(lines, [], "an answer that was not READY read as READY")
                note = " ".join(lines)
                self.assertIn("could not be imported", note)
                self.assertRegex(note, r"will\s+skip")

    def test_a_probe_that_cannot_start_says_the_sweep_will_skip(self):
        def refusing_capture(argv, env=None):
            raise OSError("planted: the interpreter could not be started")

        with mock.patch.object(self.preflight, "capture", refusing_capture):
            note = " ".join(self.preflight.identifier_sweep_note())

        self.assertIn("could not be imported", note)
        self.assertNotIn("planted:", note)

    def test_with_no_answer_given_it_runs_the_probe(self):
        calls = []

        def recording_capture(argv, env=None):
            calls.append(list(argv))
            return answer(0, f"ABSENT {DENYLIST}\n")

        with mock.patch.object(self.preflight, "capture", recording_capture):
            note = " ".join(self.preflight.identifier_sweep_note())

        self.assertEqual(calls, [[sys.executable, "-c", self.preflight.SWEEP_PROBE]])
        self.assertIn(f"{DENYLIST} is absent", note)

    def test_stderr_never_reaches_the_note(self):
        for given in (answer(1, "", STDERR_MARKER + "\n"),
                      answer(1, "", f"Traceback\n  FORBIDDEN = {{'{STDERR_MARKER}': }}\n"),
                      answer(0, f"ABSENT {DENYLIST}\n", STDERR_MARKER),
                      answer(0, "", STDERR_MARKER),
                      answer(2, f"READY {DENYLIST}\n", STDERR_MARKER)):
            with self.subTest(returncode=given.returncode, stdout=given.stdout):
                self.assertNotIn(STDERR_MARKER, self.note(given))

    def printed_by_main(self, probe_answer):
        """main()'s whole output with every pass stubbed out and the sweep
        probe answering `probe_answer` (raised, if it is an exception). The
        real identifier_sweep_note() runs: only capture() is faked."""
        preflight = self.preflight
        temp = tempfile.mkdtemp(prefix="dccore-preflight-main-")
        self.addCleanup(shutil.rmtree, temp, ignore_errors=True)

        def capture(argv, env=None):
            if preflight.SWEEP_PROBE in argv:
                if isinstance(probe_answer, BaseException):
                    raise probe_answer
                return probe_answer
            if "-c" in argv:  # the tooling probe: say the tooling is reachable
                return answer(0, "placeholder-rar\n")
            return answer(0, "", "Ran 500 tests in 1.0s\n\nOK\n")

        out = io.StringIO()
        with mock.patch.multiple(preflight, run=lambda *a, **k: True, capture=capture,
                                 state_snapshot=lambda: {}, report_state_writes=lambda *a: True,
                                 private_temp=lambda: temp, report_temp_leftovers=lambda folder: True,
                                 hostile_env=lambda: {}), \
                mock.patch.object(sys, "stdout", out):
            code = preflight.main()
        self.assertEqual(code, 0, out.getvalue())
        return out.getvalue()

    @staticmethod
    def skip_section(printed):
        return printed[printed.index("=== skipped:"):printed.index("--- skipped:")]

    def test_main_prints_the_note_under_the_skip_report(self):
        absent = answer(0, f"ABSENT {DENYLIST}\n")
        section = self.skip_section(self.printed_by_main(absent))

        lines = self.preflight.identifier_sweep_note(absent)
        self.assertTrue(lines, "control: there is a note to print")
        for line in lines:
            self.assertIn(line + "\n", section)

    def test_main_prints_nothing_about_it_when_the_guard_is_ready(self):
        printed = self.printed_by_main(answer(0, f"READY {DENYLIST}\n"))

        self.assertNotIn(DENYLIST, printed)
        self.assertNotIn("identifier sweep", printed)

    def test_main_never_prints_the_probes_stderr(self):
        failed = answer(1, "", "Traceback\n  " + STDERR_MARKER + "\nSyntaxError: invalid syntax\n")
        printed = self.printed_by_main(failed)

        self.assertIn("could not be imported", self.skip_section(printed))
        self.assertNotIn(STDERR_MARKER, printed)
        self.assertNotIn("SyntaxError", printed)

    def test_main_passes_when_the_probe_cannot_start(self):
        printed = self.printed_by_main(OSError("planted: cannot start"))

        self.assertIn("could not be imported", self.skip_section(printed))
        self.assertNotIn("planted:", printed)


class TheProbeAsksARealGuard(unittest.TestCase):
    """SWEEP_PROBE run for real, by identifier_sweep_note() itself, in a temp
    tree holding a copy of the guard and - or not - a denylist. Preflight's
    REPO_ROOT, the probe's working directory, points at that tree, so this
    machine's own denylist plays no part."""

    def setUp(self):
        self.preflight = load_preflight()
        self.root = tempfile.mkdtemp(prefix="dccore-sweep-probe-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "tests"))
        open(os.path.join(self.root, "tests", "__init__.py"), "w").close()
        shutil.copyfile(GUARD_FILE, os.path.join(self.root, "tests", os.path.basename(GUARD_FILE)))
        self.denylist = os.path.join(self.root, *DENYLIST.split("/"))
        root_patch = mock.patch.object(self.preflight, "REPO_ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        # The probe imports from its working directory. A host PYTHONPATH or
        # PYTHONSAFEPATH must not change where it looks.
        env_patch = mock.patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "1"})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        for name in ("PYTHONPATH", "PYTHONSAFEPATH"):
            os.environ.pop(name, None)

    def plant(self, content):
        with open(self.denylist, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)

    def note(self):
        return " ".join(self.preflight.identifier_sweep_note())

    def test_without_the_file_the_note_says_it_is_absent(self):
        self.assertIn(f"{DENYLIST} is absent", self.note())

    def test_a_file_without_forbidden_is_absent_too(self):
        """The case a path check of preflight's own would miss."""
        self.plant("SOMETHING_ELSE = {}\n")

        self.assertIn(f"{DENYLIST} is absent", self.note())

    def test_a_folder_of_that_name_is_not_the_file(self):
        os.makedirs(self.denylist)

        self.assertIn(f"{DENYLIST} is absent", self.note())

    def test_a_valid_file_means_no_note(self):
        self.plant('FORBIDDEN = {"0123456789abcdef": "a planted label"}\n')

        self.assertEqual(self.preflight.identifier_sweep_note(), [])

    def test_a_file_that_does_not_parse_is_never_quoted(self):
        self.plant('# planted\nFORBIDDEN = {"' + STDERR_MARKER + '": }\n')
        # Control: the child's stderr really does quote the offending line,
        # so the note leaving it out is the note's doing.
        child = self.preflight.capture([sys.executable, "-c", self.preflight.SWEEP_PROBE])
        self.assertNotEqual(child.returncode, 0)
        self.assertIn(STDERR_MARKER, child.stderr, "control: the import error quotes the line")

        note = self.note()

        self.assertIn("could not be imported", note)
        self.assertRegex(note, r"will\s+skip")
        self.assertNotIn(STDERR_MARKER, note)

    def test_nothing_of_it_reaches_preflights_own_streams(self):
        """The note above leaves the line out; this checks that the probe's
        stderr does not go past preflight to the terminal either. Preflight
        runs as a child here, with both of its own streams captured."""
        self.plant('# planted\nFORBIDDEN = {"' + STDERR_MARKER + '": }\n')
        child_code = (
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('preflight_child', sys.argv[1])\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "module.REPO_ROOT = sys.argv[2]\n"
            "print('\\n'.join(module.identifier_sweep_note()))\n")
        done = subprocess.run(
            [sys.executable, "-c", child_code,
             os.path.join(REPO_ROOT, "scripts", "preflight.py"), self.root],
            cwd=self.root, capture_output=True, encoding="utf-8", errors="replace", timeout=120)

        self.assertEqual(done.returncode, 0, "the child preflight did not finish")
        self.assertIn("could not be imported", done.stdout)
        self.assertNotIn(STDERR_MARKER, done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
