"""ADMIN-CONSOLE.md said configure.py "does steps 2 and 3 together" (audit
M36, #638).

Step 3 is ADMIN_HOSTMASKS. configure.py prompts for the password only - its
own intro says hostmasks are configured afterwards - and a file with no
usable hostmask pattern makes adminchat ignore every DCC CHAT in silence, by
design. So a novice who took the guide at its word skipped step 3, got no
reply at all, and was sent by "When it does not work" to check +x and typos
rather than the step they had been told was done.

The guide now says configure.py does step 2 only and that step 3 is still
theirs, and the troubleshooting entry names that case. The audit found this
by reading the guide against configure.py, so this reads both: the claim
must keep matching what the setup actually asks.
"""

import io
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def read(*parts):
    with io.open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


class TheSetupReallyDoesNotAskForHostmasks(unittest.TestCase):
    """The premise. If configure.py ever grows a hostmask prompt, the guide's
    wording below is wrong the other way round and this says so first."""

    def test_no_prompt_mentions_hostmasks(self):
        prompts = re.findall(r'input\((?:\s*")([^"]*)"', read("configure.py"))

        self.assertGreater(len(prompts), 3, "the prompt sweep found nothing")
        self.assertEqual([p for p in prompts if "hostmask" in p.lower()], [])


class TheGuideSaysSo(unittest.TestCase):

    def step_two(self):
        guide = read("docs", "ADMIN-CONSOLE.md")
        return guide.split("### 2. Generate a password hash", 1)[1].split("### 3.", 1)[0]

    def test_configure_is_no_longer_credited_with_step_three(self):
        self.assertNotIn("steps 2 and 3", self.step_two())

    def test_it_says_step_three_is_still_the_operators(self):
        passage = self.step_two()

        self.assertIn("does not do step 3", passage)
        self.assertIn("`ADMIN_HOSTMASKS`", passage)

    def test_and_names_both_places_to_put_it(self):
        passage = self.step_two()

        self.assertIn("`admin_config.py` by hand", passage)
        self.assertIn("Settings → Admin console", passage)

    def test_the_troubleshooting_entry_names_the_missing_step(self):
        guide = read("docs", "ADMIN-CONSOLE.md")
        entry = guide.split("**The bot ignores you completely", 1)[1].split("\n**", 1)[0]

        self.assertIn("was never set at all", entry)
        self.assertIn("configure.py", entry)
        self.assertIn("step 3", entry)


if __name__ == "__main__":
    unittest.main()
