"""preflight's state-write guard looked once, before two of its four suite
runs, and could not see a directory (audit M41, #643).

state_snapshot() was compared right after the first checks; the count pass
and the hostile pass ran afterwards and were never followed by a
comparison, so a write that only happens with ProgramFiles stripped passed
preflight. And the snapshot walked files only: an empty directory created
under data/ - data/fetched, which oserve.startup() makedirs for every test
that boots the daemon without redirecting FETCHED_FILES_DIR - was
invisible, so the guard could not name the test that had just written
into the operator's tree.

The snapshot is taken once and compared after every pass, records
directories, and DCCoreTestCase redirects FETCHED_FILES_DIR like the nine
files before it.
"""

import importlib.util
import io
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import defaults as config  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


def load_preflight():
    spec = importlib.util.spec_from_file_location(
        "preflight_under_test", os.path.join(REPO_ROOT, "scripts", "preflight.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TheSnapshotSeesDirectories(unittest.TestCase):

    def setUp(self):
        self.preflight = load_preflight()
        self.tmp = tempfile.mkdtemp(prefix="dccore-state-guard-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        # Point the guard at a tree of our own, not the repository's.
        self.preflight.REPO_ROOT = self.tmp
        self.preflight.WRITABLE_STATE = ("settings.conf", "data")
        os.makedirs(os.path.join(self.tmp, "data"))

    def test_an_empty_directory_is_a_write(self):
        before = self.preflight.state_snapshot()
        os.makedirs(os.path.join(self.tmp, "data", "fetched"))
        after = self.preflight.state_snapshot()

        self.assertNotEqual(before, after)
        self.assertIn("data" + os.sep + "fetched" + os.sep, set(after) - set(before))

    def test_and_is_reported_with_its_separator(self):
        before = self.preflight.state_snapshot()
        os.makedirs(os.path.join(self.tmp, "data", "fetched"))

        out = io.StringIO()
        real_stdout, sys.stdout = sys.stdout, out
        try:
            ok = self.preflight.report_state_writes(before, self.preflight.state_snapshot())
        finally:
            sys.stdout = real_stdout

        self.assertFalse(ok)
        self.assertIn("created  data" + os.sep + "fetched" + os.sep, out.getvalue())

    def test_a_file_is_still_a_write(self):
        before = self.preflight.state_snapshot()
        with io.open(os.path.join(self.tmp, "settings.conf"), "w", encoding="utf-8") as handle:
            handle.write("NICKNAME = x\n")

        self.assertIn("settings.conf", set(self.preflight.state_snapshot()) - set(before))

    def test_nothing_written_is_nothing_reported(self):
        before = self.preflight.state_snapshot()

        self.assertTrue(self.preflight.report_state_writes(before, self.preflight.state_snapshot()))


class TheGuardRunsAfterEveryPass(unittest.TestCase):
    """main() is the reconnect loop of this script: it runs the suite four
    times and cannot be driven here, so this reads it. One snapshot, and a
    comparison after each pass that runs the suite - including the last."""

    def source(self):
        with io.open(os.path.join(REPO_ROOT, "scripts", "preflight.py"), encoding="utf-8") as handle:
            return handle.read().split("def main():", 1)[1]

    def test_one_snapshot_three_comparisons(self):
        main = self.source()

        self.assertEqual(main.count("state_before = state_snapshot()"), 1)
        self.assertEqual(main.count("report_state_writes(state_before, state_snapshot())"), 3)

    def test_the_last_comparison_follows_the_hostile_pass(self):
        main = self.source()
        hostile = main.index("full suite with host tooling hidden")
        last_guard = main.rindex("report_state_writes(state_before, state_snapshot())")

        self.assertGreater(last_guard, hostile, "the hostile pass is not followed by the guard")

    def test_the_count_pass_is_guarded_too(self):
        main = self.source()
        count = main.index("counted = capture(")
        after_count = main.index("report_state_writes(state_before, state_snapshot())", count)
        next_pass = main.index("full suite with host tooling hidden")

        self.assertLess(after_count, next_pass, "the count pass is not followed by the guard")


class TheHarnessRedirectsTheFetchedDirectory(DCCoreTestCase):

    def test_it_is_under_the_tests_own_temp_dir(self):
        fetched = getattr(config, "FETCHED_FILES_DIR")

        self.assertNotEqual(os.path.normpath(fetched), os.path.normpath("./data/fetched"))
        self.assertTrue(os.path.isabs(fetched))
        self.assertIn(os.sep + "dccore-fetch-history-", fetched)

    def test_and_does_not_exist_until_the_code_under_test_makes_it(self):
        self.assertFalse(os.path.exists(config.FETCHED_FILES_DIR))


if __name__ == "__main__":
    unittest.main()
