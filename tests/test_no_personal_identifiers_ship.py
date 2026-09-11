"""Nothing that ships names a real person, bot, channel or address.

`.gitattributes` export-ignores exactly two files - `docs/UPDATES.md` and
`docs/PUBLIC-REPO-WORKFLOW.md`. EVERYTHING else in the repository reaches the
public tree, `tests/` very much included, and this project has shipped
identifiers three times already: a serving bot's nick in two test files, a
real channel in a changelog, and an operator's own paths in fixtures.

A FOURTH was caught in review rather than by this file, which is the reason
the list below grew. The gap is structural and worth naming: a denylist only
knows what it has been taught, so it cannot catch the first appearance of a
name nobody has thought about yet. It catches the SECOND. That is still worth
having - every one of these got in because somebody read a live console and
carried the names across into a fix, and the same names come back every time
- but it means review is the first line and this file is the net under it.

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

import hashlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# HASHED, not written out. This file SHIPS - it is in tests/, like everything
# else here - so a denylist spelling the names would publish the very strings
# it exists to remove. The first version did exactly that, and had to skip
# itself to pass: the one file guaranteed to contain every forbidden string
# was the one file never checked.
#
# docs/PUBLIC-REPO-WORKFLOW.md already records the principle, learnt from the
# licence check: "Assert what should be true, not a list of what shouldn't."
# No positive property distinguishes a person's handle from any other word,
# so the next best thing is a denylist nobody can read.
#
# SHA-256 of the lowercased word, first 16 hex characters. Add one with:
#     python -c "import hashlib;print(hashlib.sha256(b'thename').hexdigest()[:16])"
FORBIDDEN = {
    "73ef176d9f12809e": "a co-maintainer's handle, the same one used on IRC "
                        "and the issue tracker. Attribute an observation to "
                        "'an operator' instead",
    "5dade860d3d5eadd": "a real serving bot on a real network; it shipped in "
                        "two test files before an audit found it",
    "f0756a8e416936e7": "the maintainer's own account name",
    "13ea59307fc3f4ec": "the private development repository. Naming it in the "
                        "public tree points strangers at a repo they cannot "
                        "read, whose issue numbers resolve to nothing",
    # THE FOURTH TIME, and the first the guard was already in place for.
    #
    # A live report names the channels and the bots it happened to involve,
    # and the honest instinct is to carry those names into the fix so the test
    # describes the real case. Three channels and a bot's nick reached a
    # branch that way - in comments, in docstrings, in a set_config(CHANNEL=)
    # and in the assertions themselves - and the suite passed, because this
    # denylist only knows what it has already been taught.
    #
    # Nothing needs them. "a bot only in the second configured channel" is the
    # same sentence, and #one/#two/SomeBot are what the rest of the suite
    # already uses. The live detail belongs in the pull request, which does
    # not ship.
    "33870ebe3595990b": "a channel this bot serves",
    "0f3fcff0f5c1e22d": "a channel this bot serves",
    "6205a0d9a6086904": "a channel this bot serves",
    "fcfd075cbe367c15": "a real bot on a real network, seen in a live console",
}

# Compared lowercased, so one hash covers every capitalisation.
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def word_hash(word):
    return hashlib.sha256(word.lower().encode("utf-8")).hexdigest()[:16]

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
        # NO SELF-EXEMPTION any more. The first version spelled the names out
        # and had to skip itself to pass, which meant the one file guaranteed
        # to contain every forbidden string was the one file never checked -
        # and it shipped them. Hashing removed the exemption and the leak in
        # the same change.
        for name in self.shipped:
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
            for match in WORD.finditer(text):
                why = FORBIDDEN.get(word_hash(match.group(0)))
                if why is None:
                    continue
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

    def test_the_tests_are_covered_by_this_guard(self):
        """The premise the whole file rests on: nothing export-ignores
        `tests/`, so a fixture is as public as the daemon and has to be
        scanned like one.

        What ships, and what must not, is NOT re-asserted here -
        tests/test_internal_files_do_not_ship.py owns that question and had it
        first. Two guards for one property drift apart, and the one nobody
        looks at is the one that quietly stops meaning anything.
        """
        scanned = [p for p in self.files() if p.startswith("tests/")]

        self.assertGreater(len(scanned), 100,
                           "tests/ is missing from the scan - if it ever "
                           "stopped shipping, this file's premise changed")


if __name__ == "__main__":
    unittest.main()
