"""Which public daemon functions does the test suite never enter?

THE FINDING THIS EXISTS FOR

The pre-publication audit's first critical: twenty-one daemon functions had no
behavioural coverage at all. Not weak coverage - none. Replacing the body of
handle_rehash_request(), handle_queue_check() or send_file_list() with a raise
left the whole suite green. Among them were the most dangerous operation in the
daemon (!rehash), the two most common user commands, the advert worker, the IRC
read loop and both admin-console handlers.

A suite that reports 1900 passing tests while never calling a fifth of the
daemon is making a promise it does not keep, and the first outside contributor
will believe it. Three separate one-line fixes during the follow-up work turned
out to be unprovable for exactly this reason, and one of them reproduced the bug
it was meant to disprove when it was finally made testable.

WHAT THIS DOES

Runs the suite with a profiler attached, records every function actually
entered, and compares that against the public module-level functions the daemon
defines. Anything never entered is reported.

WHY A SCRIPT AND NOT A TEST

It has to run the whole suite to know the answer, so it cannot be a member of
that suite. preflight.py calls it.

THE ALLOWLIST IS THE POINT

tests/uncovered_functions.txt lists what is known-uncovered today. The gate
fails on anything NOT in it - so no new uncovered function can appear - and
also fails when a listed function HAS become covered, which forces the list to
shrink as work lands rather than rotting into a list of excuses.

WHAT IT DOES NOT MEASURE

Entry, not behaviour. A function called once by a test that asserts nothing
counts as entered. This is a floor, deliberately: the ceiling is what the tests
themselves are for, and a floor that is checkable beats a ceiling that is not.
"""

import ast
import io
import os
import sys
import threading
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST = os.path.join(REPO_ROOT, "tests", "uncovered_functions.txt")

# admin_config.py is gitignored and machine-specific; scripts/ are entry points
# run as subprocesses by their own tests, so a profiler in THIS process never
# sees them.
SKIP_MODULES = {"admin_config.py"}


def public_functions():
    """{(module, name): lineno} for every public module-level function."""
    found = {}
    for name in sorted(os.listdir(REPO_ROOT)):
        if not name.endswith(".py") or name in SKIP_MODULES:
            continue
        with io.open(os.path.join(REPO_ROOT, name), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    found[(name, node.name)] = node.lineno
    return found


class Recorder:
    """Every (file, name, firstlineno) entered, via sys.setprofile.

    setprofile rather than settrace: it fires on call and return only, not on
    every line, which is the difference between a suite that takes minutes and
    one that takes an afternoon. Installed on threading too - the daemon does
    most of its work on threads, and a profiler set on the main thread alone
    would report the advert worker and every dispatch handler as never entered.
    """

    def __init__(self):
        self.seen = set()

    def __call__(self, frame, event, arg):
        if event == "call":
            code = frame.f_code
            self.seen.add((code.co_filename, code.co_name, code.co_firstlineno))

    def start(self):
        threading.setprofile(self)
        sys.setprofile(self)

    def stop(self):
        sys.setprofile(None)
        threading.setprofile(None)


def read_allowlist():
    if not os.path.exists(ALLOWLIST):
        return set()
    entries = set()
    with io.open(ALLOWLIST, encoding="utf-8") as handle:
        for line in handle:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            module, _, name = line.partition("::")
            entries.add((module.strip(), name.strip()))
    return entries


def run_suite():
    """The whole suite, quietly, with the profiler attached."""
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=os.path.join(REPO_ROOT, "tests"),
                            top_level_dir=REPO_ROOT)
    recorder = Recorder()
    stream = io.StringIO()
    recorder.start()
    try:
        runner = unittest.TextTestRunner(stream=stream, verbosity=0)
        result = runner.run(suite)
    finally:
        recorder.stop()
    return recorder.seen, result


def main():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    os.chdir(REPO_ROOT)

    declared = public_functions()
    seen, result = run_suite()

    entered = {(os.path.basename(path), name, line) for path, name, line in seen}
    uncovered = sorted(
        key for key, lineno in declared.items()
        if (key[0], key[1], lineno) not in entered)

    allowed = read_allowlist()
    new = [k for k in uncovered if k not in allowed]
    now_covered = sorted(allowed - set(uncovered))

    print()
    print("=" * 70)
    print(f"  Public daemon functions: {len(declared)}")
    print(f"  Never entered by any test: {len(uncovered)}")
    print(f"  Known and allowed: {len(allowed)}")
    print("=" * 70)

    if not result.wasSuccessful():
        print("  The suite itself failed; coverage numbers above are unreliable.")
        return 1

    if new:
        print()
        print(f"  {len(new)} function(s) with no behavioural coverage, not on the list:")
        for module, name in new:
            print(f"    {module}::{name}  (line {declared[(module, name)]})")
        print()
        print("  Add a test that calls it. If it genuinely cannot be covered, add")
        print(f"  it to {os.path.relpath(ALLOWLIST, REPO_ROOT)} with the reason.")
        return 1

    if now_covered:
        print()
        print(f"  {len(now_covered)} allowlisted function(s) are now covered:")
        for module, name in now_covered:
            print(f"    {module}::{name}")
        print()
        print(f"  Remove them from {os.path.relpath(ALLOWLIST, REPO_ROOT)} - the list")
        print("  has to shrink as the work lands, or it becomes a list of excuses.")
        return 1

    print("  No public daemon function is uncovered outside the allowlist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
