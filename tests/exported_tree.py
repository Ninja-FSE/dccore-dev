"""Telling this repository apart from an extracted public tree.

`docs/UPDATES.md` is export-ignored in `.gitattributes`. It is the internal
changelog: it exists in the development repository and, by design, never
reaches the public one, which gets `docs/UPDATES-PUBLIC.md` instead.

That leaves any test which READS the internal changelog with two different
absences to tell apart, and getting it wrong is expensive in both directions:

  * Fail whenever the file is missing, and the public repository's very first
    CI run is red - on the branch its own protection rules require to be
    green - for a file that was deliberately excluded.
  * Skip whenever the file is missing, and the day it genuinely disappears
    from THIS repository, every test that guards its contents quietly stops
    running and nobody is told.

So the absence has to be EXPLAINED rather than assumed. `.gitattributes`
ships, so an extracted tree still carries the statement that the file was
export-ignored. If that statement is there, the file is missing for the one
reason that is allowed. If it is not, something else happened and the test
should say so loudly.

Checking for `.git` instead would not work: the public repository is a git
repository too, so its presence says nothing about which tree this is.
"""

import io
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INTERNAL_CHANGELOG = os.path.join("docs", "UPDATES.md")


def _is_export_ignored(relative_path):
    """True if .gitattributes says this path does not ship."""
    attributes = os.path.join(REPO_ROOT, ".gitattributes")
    if not os.path.exists(attributes):
        return False
    wanted = relative_path.replace(os.sep, "/")
    with io.open(attributes, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.split("#", 1)[0]
            fields = line.split()
            if len(fields) >= 2 and fields[0] == wanted:
                if "export-ignore" in fields[1:]:
                    return True
    return False


def internal_changelog_or_skip(test_case):
    """The absolute path of docs/UPDATES.md, or skip if this is an export.

    Fails rather than skips when the file is missing AND nothing explains
    why - see this module's own docstring for why those are not the same.
    """
    full = os.path.join(REPO_ROOT, INTERNAL_CHANGELOG)
    if os.path.exists(full):
        return full
    if not _is_export_ignored(INTERNAL_CHANGELOG):
        test_case.fail(
            f"{INTERNAL_CHANGELOG} is missing, and .gitattributes does not "
            f"export-ignore it. In an extracted public tree its absence is "
            f"expected and this test skips; here it means the changelog has "
            f"gone missing for some other reason.")
    test_case.skipTest(
        f"{INTERNAL_CHANGELOG} is export-ignored and absent, so this is an "
        f"extracted public tree rather than the development repository.")
