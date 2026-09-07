"""Nothing that ships names a real person, bot, channel or address.

`.gitattributes` export-ignores exactly two files - `docs/UPDATES.md` and
`docs/PUBLIC-REPO-WORKFLOW.md`. EVERYTHING else in the repository reaches the
public tree, `tests/` very much included, and this project has shipped
identifiers three times already: a serving bot's nick in two test files, a
real channel in a changelog, and an operator's own paths in fixtures.

Greps done by hand keep missing them, because they are written to find the
thing already known about. This asks the export itself.

WHY THE CO-MAINTAINER'S HANDLE COUNTS

It was used in good faith - dozens of comments attributed an observation to
the person who made it, which is ordinary practice. But this is a public
repository read by strangers, the handle is the same one on the issue tracker
and on IRC, and none of the comments need it: "an operator reported" carries
the same weight and dates better. The attributions were rewritten rather than
deleted, so the reasoning survives without the name.
"""

import io
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every pattern here has actually shipped, or was one edit away from it.
FORBIDDEN = {
    r"\bNeo\b": "the co-maintainer's handle - the same one on IRC and the "
                "issue tracker. Attribute to 'an operator' instead",
    r"\bFlacMe": "a real serving bot on a real network; it shipped in two "
                 "test files before an audit found it",
    r"\bchchatzop\b": "the maintainer's account name",
    r"\bdccore-dev\b": "the private development repository. Naming it in the "
                       "public tree points strangers at a repo they cannot "
                       "read and whose issue numbers resolve to nothing",
}

# Documentation and private ranges are the ONLY literal addresses that may
# ship. Everything else is somebody's real machine.
ALLOWED_IP = re.compile(
    r"^(127\.|0\.0\.0\.0|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.|"
    r"192\.0\.2\.|198\.51\.100\.|203\.0\.113\.|1\.2\.3\.4|8\.8\.8\.8|1\.1\.1\.1|"
    # 224-255: multicast, reserved and broadcast. Not anybody's machine, and
    # the address tests use them deliberately to check they are refused.
    r"2(2[4-9]|[3-4]\d|5[0-5])\.)")
IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

TEXT_SUFFIXES = (".py", ".js", ".md", ".css", ".html", ".txt", ".sample",
                 ".conf", ".bat", ".sh", ".yml", ".yaml", ".json")


def shipped_paths():
    """Every tracked file that would ship, or (None, reason).

    THE WORKING TREE, not `git archive HEAD`. The archive reads the last
    COMMIT, so a guard built on it cannot see the change being made - it
    would have passed the scrub that introduced it and failed on everything
    before, which is precisely backwards for something meant to stop an
    identifier being committed.

    export-ignore is still the authority on what ships: `git check-attr`
    answers the same question `git archive` does, against the files as they
    are now.
    """
    try:
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT,
                                check=True, capture_output=True, timeout=180)
        names = [n for n in listed.stdout.decode("utf-8", "replace").split("\0") if n]
        attrs = subprocess.run(["git", "check-attr", "--stdin", "-z", "export-ignore"],
                               cwd=REPO_ROOT, check=True, timeout=180,
                               input="\0".join(names).encode("utf-8"),
                               capture_output=True)
        fields = attrs.stdout.decode("utf-8", "replace").split("\0")
        ignored = set()
        # check-attr -z emits path, attribute, value as three NUL-separated
        # fields per line.
        for index in range(0, len(fields) - 2, 3):
            if fields[index + 2] == "set":
                ignored.add(fields[index])
    except Exception as err:  # noqa: BLE001 - git may not be available
        return None, f"could not list the shipped files ({err})"

    return [name for name in names if name not in ignored], None


class NothingIdentifyingShips(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.shipped, cls.reason = shipped_paths()

    def setUp(self):
        if self.reason:
            self.skipTest(self.reason)

    def files(self):
        # This file is skipped: it necessarily CONTAINS every pattern it
        # searches for, so a guard that scanned itself could never pass. The
        # patterns live nowhere else, which is what keeps the exemption from
        # being a hole somebody could hide behind.
        mine = os.path.basename(__file__)
        for name in self.shipped:
            if os.path.basename(name) == mine:
                continue
            if name.endswith(TEXT_SUFFIXES):
                yield name

    def read(self, path):
        with io.open(os.path.join(REPO_ROOT, path), encoding="utf-8",
                     errors="replace") as handle:
            return handle.read()

    def test_no_forbidden_identifier_is_in_the_export(self):
        found = {}
        for path in self.files():
            text = self.read(path)
            for pattern, why in FORBIDDEN.items():
                for match in re.finditer(pattern, text):
                    line = text.count("\n", 0, match.start()) + 1
                    found.setdefault(why, []).append(f"{path}:{line}")

        self.assertEqual(
            found, {},
            "these ship to the public repository:\n  "
            + "\n  ".join(f"{why}\n    {sorted(set(where))[:6]}"
                          for why, where in found.items()))

    def test_no_real_ip_address_is_in_the_export(self):
        found = []
        for path in self.files():
            text = self.read(path)
            for match in IP.finditer(text):
                if ALLOWED_IP.match(match.group(0)):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{path}:{line} {match.group(0)}")

        self.assertEqual(found, [],
                         "only loopback, private and documentation ranges may "
                         "ship; these are somebody's real machine")

    def test_the_two_internal_documents_do_not_ship(self):
        """The whole rule rests on these being export-ignored. If either ever
        appears in the archive, the internal changelog - which names real
        channels, this repository, and its own issue numbers - is public."""
        for internal in ("docs/UPDATES.md", "docs/PUBLIC-REPO-WORKFLOW.md"):
            with self.subTest(document=internal):
                self.assertNotIn(internal, self.shipped,
                                 f"{internal} is export-ignored and must not "
                                 f"ship")

    def test_the_tests_do_ship(self):
        """The reason this guard has to cover them. Nothing export-ignores
        tests/, so a fixture is as public as the daemon."""
        shipped = [p for p in self.files() if p.startswith("tests/")]

        self.assertGreater(len(shipped), 100,
                           "tests/ is missing from the export - if that "
                           "became deliberate, this file's premise changed")

    def test_the_public_changelog_ships_and_the_internal_one_does_not(self):
        """They are a pair, and swapping them would publish the internal
        history rather than the operator-facing one."""
        self.assertIn("docs/UPDATES-PUBLIC.md", self.shipped)


if __name__ == "__main__":
    unittest.main()
