"""A fixed sleep before an assertion, and a misattributed failure note
when the hostile pass is skipped (audit L42, #706).

tests/test_webserver.py's window test slept 0.4 s for a 0.15 s window and
asserted the raw flag the closer thread sets - a bet on the scheduler,
which a loaded runner loses. It waits for the condition now, with a
deadline.

scripts/preflight.py's "only the hidden-tooling pass failed" note fired
whenever the LAST result was False and all earlier ones True. With the
hostile pass SKIPPED (rar reachable regardless), the last result is the
test-count floor - so an operator whose count had dropped was told to fix
a hidden-tooling dependency that does not exist. The condition is a named
function now, and it knows whether the hidden pass ran and which of its
two results is the run.
"""

import io
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import preflight  # noqa: E402

T, F = True, False


class TheNote(unittest.TestCase):

    def test_the_audits_case_a_skipped_hostile_pass_and_a_failed_count_floor(self):
        """[T,T,T,T,T,F] with the hostile pass skipped: the F is the count
        floor, not the hidden pass."""
        self.assertFalse(preflight.only_the_hidden_pass_failed([T, T, T, T, T, F], hostile_ran=False))

    def test_the_hidden_run_alone_failing_is_the_note(self):
        self.assertTrue(preflight.only_the_hidden_pass_failed([T, T, T, T, T, T, F, T], hostile_ran=True))

    def test_an_earlier_failure_is_not_the_note(self):
        self.assertFalse(preflight.only_the_hidden_pass_failed([T, T, F, T, T, T, T, T], hostile_ran=True))
        self.assertFalse(preflight.only_the_hidden_pass_failed([T, T, T, T, T, T, F, T], hostile_ran=False))

    def test_the_hidden_passes_own_state_check_failing_is_not_the_note_either(self):
        """A test that wrote into the tree under the hidden pass is a
        different message (report_state_writes prints it)."""
        self.assertFalse(preflight.only_the_hidden_pass_failed([T, T, T, T, T, T, T, F], hostile_ran=True))

    def test_the_tail_calls_it_with_the_flag(self):
        with io.open(os.path.join(REPO_ROOT, "scripts", "preflight.py"), encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn("if only_the_hidden_pass_failed(results, hostile_ran):", source)
        self.assertNotIn("if results[-1] is False and all(results[:-1]):", source)
        self.assertIn("hostile_ran = False", source)
        self.assertIn("hostile_ran = True", source)


class TheWindowTestWaits(unittest.TestCase):

    def test_no_fixed_sleep_before_the_flag_assertion(self):
        with io.open(os.path.join(REPO_ROOT, "tests", "test_webserver.py"), encoding="utf-8") as handle:
            source = handle.read()
        start = source.index("def test_the_window_closes_itself_after_the_duration")
        body = source[start:source.index("def test_", start + 1)]

        self.assertNotIn("time.sleep(", body)
        self.assertIn("wait_for(lambda: not config.broadcast_search_inprogress", body)


if __name__ == "__main__":
    unittest.main()
