"""Preflight ran the whole suite four times, each in one process (#1146).

The plain pass and the counting pass now go through
scripts/run_tests_in_parallel.py, as CI does. The coverage pass cannot: the
profiler has to see every call in one process. The hidden-tooling pass stays
serial too, so one pass still runs the suite in the order a plain
`python -m unittest discover` gives it.

Dev-only, like the script it covers: stripped with it at release (see
docs/PUBLIC-REPO-WORKFLOW.md).
"""

import io
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.test_preflight_checks_every_pass_for_state_writes import load_preflight  # noqa: E402

SERIAL = '[py, "-m", "unittest", "discover", "-s", "tests", "-t", "."]'


def main_source():
    with io.open(os.path.join(REPO_ROOT, "scripts", "preflight.py"), encoding="utf-8") as handle:
        return handle.read().split("def main():", 1)[1]


class WhichPassesRunInParallel(unittest.TestCase):

    def test_the_runner_it_names_is_the_one_that_ships(self):
        self.assertEqual(load_preflight().PARALLEL_RUNNER,
                         os.path.join(REPO_ROOT, "scripts", "run_tests_in_parallel.py"))
        self.assertTrue(os.path.isfile(load_preflight().PARALLEL_RUNNER))

    def test_the_plain_pass_is_parallel(self):
        self.assertIn('        ("full suite", [py, PARALLEL_RUNNER]),\n', main_source())

    def test_the_counting_pass_is_parallel_and_verbose(self):
        self.assertIn("    counted = capture([py, PARALLEL_RUNNER, \"-v\"])\n", main_source())

    def test_the_hidden_tooling_pass_stays_serial(self):
        main = main_source()
        hidden = main.split('"full suite with host tooling hidden (simulates a bare runner)",', 1)[1]

        self.assertTrue(hidden.lstrip().startswith(SERIAL), hidden[:200])
        self.assertEqual(main.count(SERIAL), 1, "only the hidden-tooling pass is serial")

    def test_the_coverage_pass_is_untouched(self):
        self.assertIn('"function_coverage.py")]),', main_source())


if __name__ == "__main__":
    unittest.main()
