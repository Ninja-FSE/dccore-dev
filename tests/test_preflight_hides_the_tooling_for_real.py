"""Preflight's hidden-tooling pass runs, and a broken probe fails it (#1178).

Before the pass that runs the suite with the host's tooling stripped away,
preflight asks a child process whether rar can still be found. That child ran
`import platform_compat` from the repository root, and since the modules moved
into src/ (#959) the import failed on every machine. The probe printed
nothing, preflight read "not NONE" as "host tooling is reachable regardless",
and skipped the pass - everywhere, blaming the machine.

Dev-only, like the other preflight tests: on the public strip list.
"""

import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import preflight  # noqa: E402


class TheProbe(unittest.TestCase):

    def test_it_answers_from_the_repository_root_under_the_stripped_environment(self):
        probe = subprocess.run([sys.executable, "-c", preflight.TOOLING_PROBE], cwd=REPO_ROOT,
                               env=preflight.hostile_env(), capture_output=True,
                               encoding="utf-8", errors="replace")
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertTrue(probe.stdout.strip(), "the probe printed nothing: it did not run")

    def test_a_probe_that_cannot_run_fails_preflight_instead_of_skipping(self):
        with open(preflight.__file__, encoding="utf-8") as handle:
            source = handle.read()
        failed_branch = source.split("if probe.returncode != 0 or not found:", 1)
        self.assertEqual(len(failed_branch), 2, "the probe's own failure is not checked")
        branch = failed_branch[1].split("elif found != \"NONE\":", 1)[0]
        self.assertIn("results.append(False)", branch)


if __name__ == "__main__":
    unittest.main()
