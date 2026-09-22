"""Re-running configure.py keeps every admin host that is configured (#891).

#811 added a services-host question. On a re-run it took the FIRST
configured entry as its default and, on a blank answer, wrote [that one]
back - so an operator with two hosts (home and phone) who pressed Enter
lost the second without a word, and the phone could no longer run admin
commands. configure._current()'s own promise is that re-running never
silently changes what is configured. It also indexed the value directly,
so a comma-separated string setting offered its first character, and a
wildcard first entry was put through the validator as if just typed.

Driven through collect_answers() with canned input, as #811's own tests are.
"""

import contextlib
import io
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import adminchat  # noqa: E402
import configure  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402

HOME = "me.users.undernet.org"
PHONE = "my-phone.example.net"


class ARerun(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()
        real = adminchat._read_password
        self.addCleanup(setattr, adminchat, "_read_password", real)

    def run_with_host_answers(self, *host_answers):
        """The whole question flow; `host_answers` are what is typed at the
        services-host prompt (more than one when the first is refused)."""
        answers = iter(["MyBot", "", "#my-channel", "MyAdmin", *host_answers,
                        self.tree.music, "n"])
        passwords = iter(["secret123", "secret123"])
        adminchat._read_password = lambda prompt: next(passwords)
        import builtins
        real_input = builtins.input
        builtins.input = lambda prompt="": next(answers)
        said = io.StringIO()
        try:
            with contextlib.redirect_stdout(said):
                changes, _hash = configure.collect_answers()
        finally:
            builtins.input = real_input
        return changes, said.getvalue()

    def test_two_hosts_and_a_blank_answer_writes_nothing(self):
        """The reported case."""
        self.set_config(ADMIN_HOSTMASKS=["*!*@" + HOME, "*!*@" + PHONE])

        changes, said = self.run_with_host_answers("")

        self.assertNotIn("ADMIN_HOSTMASKS", changes,
                         "a blank answer rewrote the hosts - the second one would be lost")
        self.assertIn("Configured now: %s, %s" % (HOME, PHONE), said)

    def test_one_host_and_a_blank_answer_writes_nothing_either(self):
        self.set_config(ADMIN_HOSTMASKS=["*!*@" + HOME])

        changes, _said = self.run_with_host_answers("")

        self.assertNotIn("ADMIN_HOSTMASKS", changes)

    def test_typing_a_host_that_is_already_there_changes_nothing(self):
        self.set_config(ADMIN_HOSTMASKS=["*!*@" + HOME, "*!*@" + PHONE])

        changes, _said = self.run_with_host_answers(PHONE.upper())

        self.assertNotIn("ADMIN_HOSTMASKS", changes)

    def test_a_new_host_beside_several_is_added_and_they_are_kept(self):
        self.set_config(ADMIN_HOSTMASKS=["*!*@" + HOME, "*!*@" + PHONE])

        changes, said = self.run_with_host_answers("laptop.users.undernet.org")

        self.assertEqual(changes["ADMIN_HOSTMASKS"],
                         ["*!*@" + HOME, "*!*@" + PHONE, "*!*@laptop.users.undernet.org"])
        self.assertIn("Added beside the 2 already configured", said)

    def test_a_new_host_replacing_the_only_one_is_written_as_before(self):
        self.set_config(ADMIN_HOSTMASKS=["*!*@" + HOME])

        changes, _said = self.run_with_host_answers("new.users.undernet.org")

        self.assertEqual(changes["ADMIN_HOSTMASKS"], ["*!*@new.users.undernet.org"])

    def test_a_wildcard_entry_is_not_put_through_the_validator_on_a_blank(self):
        """It used to be the prompt's default, so Enter printed "That will
        not do: '*.home.net' has a '*' in it" about a value nobody typed."""
        self.set_config(ADMIN_HOSTMASKS=["*!*@*.home.net"])

        changes, said = self.run_with_host_answers("")

        self.assertNotIn("That will not do", said)
        self.assertNotIn("ADMIN_HOSTMASKS", changes)

    def test_a_comma_separated_setting_is_read_as_hosts_not_characters(self):
        self.set_config(ADMIN_HOSTMASKS="*!*@%s,*!*@%s" % (HOME, PHONE))

        changes, said = self.run_with_host_answers("")

        self.assertIn("Configured now: %s, %s" % (HOME, PHONE), said)
        self.assertNotIn("ADMIN_HOSTMASKS", changes)

    def test_nothing_configured_still_behaves_as_811_wrote_it(self):
        self.set_config(ADMIN_HOSTMASKS=[])

        blank, _said = self.run_with_host_answers("")
        typed, _said = self.run_with_host_answers("myaccount.users.undernet.org")

        self.assertNotIn("ADMIN_HOSTMASKS", blank)
        self.assertEqual(typed["ADMIN_HOSTMASKS"], ["*!*@myaccount.users.undernet.org"])


if __name__ == "__main__":
    unittest.main()
