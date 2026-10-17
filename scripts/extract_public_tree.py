#!/usr/bin/env python3
"""Extract the public dccore tree from a revision of this repository (#1418).

docs/PUBLIC-REPO-WORKFLOW.md's "The extraction", steps 1-3, as one command:

    python3 scripts/extract_public_tree.py <rev> <out> [--test]

  1. `git archive <rev>` - export-ignore keeps the internal files out;
  2. the dev-only tooling with no shipped consumer goes (DEV_ONLY), and the
     one .gitattributes line that is wrong in the public repo
     (INTERNAL_CHANGELOG_RULE) - and nothing else;
  3. docs/UPDATES-PUBLIC.md becomes docs/UPDATES.md, and the two links to it
     are rewritten (LINKS).

--test then runs the suite in the extracted tree (step 4 asks for that run:
it is the one that caught #1409's fourteen failures before anything reached
dccore).

ALL OR NOTHING. Everything happens in a scratch folder beside <out>, which
becomes <out> only when every step found exactly what it expected: a
DEV_ONLY file that is not there, a .gitattributes line or a link that is not
where the step says, refuses - naming it - and leaves no <out> at all. A list
that has gone stale is the failure this exists to make loud; done by hand,
the steps followed a checklist that had already drifted twice.

The strip list lives here and only here (CONVENTIONS rule 2): the workflow
document points to it. tests/test_the_public_tree_is_extracted_by_a_script.py
fails when a dev-only file exists that it does not name.
"""

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Dev-only tooling with no shipped consumer, and the tests that exist only to
# cover it (docs/PUBLIC-REPO-WORKFLOW.md, step 2, says why each kind goes).
# This script and its own test are dev-only too.
DEV_ONLY = (
    "scripts/preflight.py",
    "scripts/capture_adverts.py",
    "scripts/function_coverage.py",
    "scripts/extract_public_tree.py",
    "tests/uncovered_functions.txt",
    "tests/test_preflight_checks_every_pass_for_state_writes.py",
    "tests/test_preflight_counts_what_was_skipped.py",
    "tests/test_preflight_hides_the_tooling_for_real.py",
    "tests/test_preflight_names_what_a_pass_leaves_in_temp.py",
    "tests/test_preflight_reads_its_children_in_utf8.py",
    "tests/test_preflight_runs_the_suite_in_parallel.py",
    "tests/test_preflights_note_names_the_pass_that_failed.py",
    "tests/test_the_public_tree_is_extracted_by_a_script.py",
)

# Correct here, wrong there: step 3 makes docs/UPDATES.md the public changelog,
# and this line would drop it from any `git archive` run in the public repo.
# The docs/PUBLIC-REPO-WORKFLOW.md line stays - tests/exported_tree.py tells
# the two trees apart by it.
INTERNAL_CHANGELOG_RULE = "docs/UPDATES.md    export-ignore"

PUBLIC_CHANGELOG = "docs/UPDATES-PUBLIC.md"
SHIPPED_CHANGELOG = "docs/UPDATES.md"

# (file, the text as it is here, the text it becomes there). Each must be
# found exactly once.
LINKS = (
    ("README.md",
     "| [UPDATES-PUBLIC.md](docs/UPDATES-PUBLIC.md) | the changelog |",
     "| [UPDATES.md](docs/UPDATES.md) | the changelog |"),
    ("docs/INSTALL.md",
     "**5. Read the changelog.** [UPDATES-PUBLIC.md](UPDATES-PUBLIC.md)",
     "**5. Read the changelog.** [UPDATES.md](UPDATES.md)"),
)


class Refused(Exception):
    """A step did not find what it expected; nothing was written."""


def _archive(rev, into, repo):
    try:
        result = subprocess.run(["git", "archive", "--format=tar", rev], cwd=repo,
                                capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise Refused(f"git archive {rev} could not run: {err}")
    if result.returncode != 0:
        raise Refused(f"git archive {rev} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        if hasattr(tarfile, "data_filter"):
            archive.extractall(into, filter="data")
        else:  # Python before 3.12 and its backports: the archive is our own repository's
            archive.extractall(into)


def _read(path):
    with io.open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _write(path, text):
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _strip(tree):
    missing = [name for name in DEV_ONLY if not os.path.isfile(os.path.join(tree, name))]
    if missing:
        raise Refused("not in the archive, so DEV_ONLY has gone stale: " + ", ".join(missing))
    for name in DEV_ONLY:
        os.remove(os.path.join(tree, name))
    path = os.path.join(tree, ".gitattributes")
    text = _read(path)
    lines = text.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == INTERNAL_CHANGELOG_RULE]
    if len(hits) != 1:
        raise Refused(f".gitattributes has {len(hits)} lines {INTERNAL_CHANGELOG_RULE!r}, not one")
    del lines[hits[0]]
    _write(path, "".join(lines))


def _swap_the_changelog(tree):
    source = os.path.join(tree, PUBLIC_CHANGELOG)
    target = os.path.join(tree, SHIPPED_CHANGELOG)
    if not os.path.isfile(source):
        raise Refused(f"{PUBLIC_CHANGELOG} is not in the archive")
    if os.path.exists(target):
        raise Refused(f"{SHIPPED_CHANGELOG} is in the archive: its export-ignore rule is gone")
    os.replace(source, target)
    for name, here, there in LINKS:
        path = os.path.join(tree, name)
        text = _read(path)
        if text.count(here) != 1:
            raise Refused(f"{name}: the link {here!r} is there {text.count(here)} times, not once")
        text = text.replace(here, there)
        if "UPDATES-PUBLIC" in text:
            raise Refused(f"{name} still names UPDATES-PUBLIC after the rewrite")
        _write(path, text)


def extract(rev, out, repo=REPO_ROOT):
    """Steps 1-3 into `out`, which must not exist yet. Returns `out`.
    Raises Refused, with no `out` left behind, when a step does not find
    what it expects."""
    out = os.path.abspath(out)
    if os.path.exists(out):
        raise Refused(f"{out} is already there; the extraction makes it")
    scratch = out + ".partial"
    if os.path.exists(scratch):
        shutil.rmtree(scratch)
    os.makedirs(scratch)
    try:
        _archive(rev, scratch, repo)
        _strip(scratch)
        _swap_the_changelog(scratch)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    os.replace(scratch, out)
    return out


def run_the_suite(tree, processes=2):
    """Step 4's run: the suite in the extracted tree, as its own CI runs it."""
    return subprocess.run([sys.executable, os.path.join("scripts", "run_tests_in_parallel.py"),
                           "-j", str(processes)], cwd=tree).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description="Extract the public dccore tree (docs/PUBLIC-REPO-WORKFLOW.md, steps 1-3).")
    parser.add_argument("rev", help="the revision to extract, e.g. origin/main")
    parser.add_argument("out", help="the folder to create")
    parser.add_argument("--test", action="store_true", help="then run the suite in it (step 4)")
    parser.add_argument("-j", type=int, default=2, help="test processes with --test (default 2)")
    args = parser.parse_args(argv)
    try:
        out = extract(args.rev, args.out)
    except Refused as err:
        print(f"extract_public_tree: refused - {err}", file=sys.stderr)
        return 2
    print(f"extract_public_tree: {args.rev} extracted into {out}")
    if args.test:
        return run_the_suite(out, args.j)
    return 0


if __name__ == "__main__":
    sys.exit(main())
