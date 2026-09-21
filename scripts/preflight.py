#!/usr/bin/env python3
"""Run everything CI runs, before pushing - including the parts a developer machine hides.

    python scripts/preflight.py

Passing this locally is meant to mean CI will pass. A plain test run does not,
because a developer machine carries things the runners do not.

That is not hypothetical. PR #39 was green locally and red on all four CI jobs,
because a test asserted on a call that only happens when a rar binary exists -
and this machine has WinRAR installed while the runners have nothing. The test
was measuring the host, not the code, and a normal `python -m unittest` could
never have caught it.

So this runs the suite twice: once as-is, and once in a deliberately hostile
environment with host-provided tools hidden. A test that passes in the first
pass and fails in the second is depending on something incidental to the machine
it runs on.
"""

import re
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Environment variables that let the code discover optional host tooling. Blanking
# them simulates a bare runner. Add to this list whenever a new optional
# dependency is introduced.
HOST_TOOLING_VARS = ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")


def run(label, argv, env=None):
    print(f"\n=== {label} ===", flush=True)
    result = subprocess.run(argv, cwd=REPO_ROOT, env=env)
    ok = result.returncode == 0
    print(f"--- {label}: {'PASS' if ok else 'FAIL'}", flush=True)
    return ok


def capture(argv, env=None):
    """Run argv with its output captured, decoded as UTF-8.

    The children write UTF-8: the test child reconfigures its streams the
    moment a test imports oserve.py (install_console_encoding_guard), and
    hostile_env() sets PYTHONUTF8=1 for the probe. `text=True` on its own
    decodes with the locale code page instead - cp1253 on a Greek Windows,
    strict - and a byte that code page leaves undefined (0x81, 0x9f, ...:
    any emoji, an A-acute) does not raise out of subprocess.run on Windows.
    It kills the reader thread, a UnicodeDecodeError traceback lands in
    preflight's own output where it reads as a test failure, and the captured
    stream comes back as None. On a green run that was a misleading traceback;
    on a red run whose failure text carried such a character, the count was
    parsed from None and reported as "only 0 collected".
    """
    return subprocess.run(argv, cwd=REPO_ROOT, env=env, capture_output=True,
                          encoding="utf-8", errors="replace")


def hostile_env():
    """A copy of the environment with host-installed tooling made undiscoverable.

    Removal is CASE-INSENSITIVE on purpose. os.environ upper-cases its keys on
    Windows, so env.pop("ProgramFiles") silently matches nothing and the variable
    survives - which made the first version of this script pass while hiding
    absolutely nothing. Verified by asserting the result below.
    """
    env = dict(os.environ)
    targets = {name.lower() for name in HOST_TOOLING_VARS}
    for key in [k for k in env if k.lower() in targets]:
        del env[key]
    # Keep only the interpreter's own directory on PATH, so anything the code
    # locates via shutil.which has to be something CI would also have.
    #
    # Plus System32 on Windows (#642). cmd.exe is the operating system, not
    # host tooling - a bare Windows runner has it, and the .bat launcher tests
    # are gated on finding it - so hiding it made every one of them skip in
    # this pass and "PASS" say nothing about the launchers. Git Bash, rar and
    # the rest live under Program Files and stay hidden. (A WSL bash.exe in
    # System32 would be found by the POSIX classes here; the skip report
    # below is where that shows.)
    path = [os.path.dirname(sys.executable)]
    if os.name == "nt":
        system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        if os.path.isdir(system32):
            path.append(system32)
    env["PATH"] = os.pathsep.join(path)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


# Files a test must never write for real. All three of these have actually
# been written by the suite: settings.conf (twice), data/on_connect.json with
# a plaintext X password in it, and data/lists.json - the last one pointing at
# a temp directory that had already been deleted, which cost 147 failures in
# tests that never mentioned lists.
#
# Every one was invisible in `git status`: settings.conf and data/ are both
# gitignored. Three times is enough to stop relying on noticing.
WRITABLE_STATE = ("settings.conf", "data")


def state_snapshot():
    """Every real state file AND directory, with its modification time.

    Compared before and after every pass. A test that redirects its writes
    correctly leaves this identical; one that does not shows up as an added
    or touched path, named.

    Directories too (#643): this used to walk files only, so an empty
    directory a test created under data/ - data/fetched, made by
    oserve.startup()'s makedirs for a test that never redirected
    FETCHED_FILES_DIR - was invisible, and the guard could not name the
    test that had just written into the operator's tree. A directory's own
    mtime is left out on purpose: it changes whenever an entry is added or
    removed, which the entries themselves already report.
    """
    seen = {}
    for target in WRITABLE_STATE:
        path = os.path.join(REPO_ROOT, target)
        if os.path.isfile(path):
            seen[target] = os.path.getmtime(path)
        elif os.path.isdir(path):
            seen[target + os.sep] = None
            for root, dirs, names in os.walk(path):
                for name in dirs:
                    seen[os.path.relpath(os.path.join(root, name), REPO_ROOT) + os.sep] = None
                for name in names:
                    full = os.path.join(root, name)
                    try:
                        seen[os.path.relpath(full, REPO_ROOT)] = os.path.getmtime(full)
                    except OSError:
                        pass
    return seen


def report_state_writes(before, after):
    """True if the suite left the developer's own state alone."""
    added = sorted(set(after) - set(before))
    touched = sorted(p for p in set(after) & set(before) if after[p] != before[p])
    if not added and not touched:
        return True
    print()
    print("  THE SUITE WROTE REAL STATE FILES (a trailing separator marks a directory):")
    for path in added:
        print(f"    created  {path}")
    for path in touched:
        print(f"    modified {path}")
    print("  A test wrote outside its temp directory. Redirect it in "
          "tests/support.py setUp() - see LISTS_FILE there for the pattern.")
    return False


# How many skips the normal pass may carry before preflight refuses to call
# it a pass. Skips are legitimate - a Windows box cannot exercise permission
# bits, a POSIX box has no cmd.exe - and the ceiling is set well above what
# any one platform skips for those reasons (about a dozen here, a few dozen
# on a shell with no bash), so that only a whole family of tests going dark
# trips it. The REASONS are always printed, whatever the count: that is the
# part that turns "skipped=35" into "run this from Git Bash".
MAX_SKIPPED = 60

SKIP_REASON = re.compile(r"\.\.\. skipped ['\"](.*)['\"]\s*$")


def skip_report(output):
    """(skipped, {reason: count}) from a verbose unittest run.

    The count comes from the summary line ("OK (skipped=14)"), the reasons
    from the per-test lines. The two can disagree - a class whose
    setUpClass raises SkipTest is one line for the whole class - and the
    summary is the authority; the reasons are the explanation.
    """
    match = re.search(r"skipped=(\d+)", output)
    skipped = int(match.group(1)) if match else 0
    reasons = {}
    for line in output.splitlines():
        found = SKIP_REASON.search(line)
        if found:
            reasons[found.group(1)] = reasons.get(found.group(1), 0) + 1
    return skipped, reasons


def main():
    py = sys.executable
    checks = [
        # Mirrors .github/workflows/tests.yml, in the same order.
        # Same script CI runs, deriving the module list from the filesystem.
        # This used to be a second hand-written copy of CI's list, and the two
        # had drifted apart from each other and from the project.
        ("every module imports",
         [py, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "check_imports.py")]),
        ("compile every source file", [py, "-m", "compileall", "-q", "."]),
        ("full suite", [py, "-m", "unittest", "discover", "-s", "tests", "-t", "."]),
        # The audit's first critical: a public daemon function nothing calls has
        # no regression protection at all, and the suite reports it as covered
        # anyway. This runs the suite a second time under a profiler, which
        # costs about fifteen seconds on top, and fails on any public function
        # nothing enters that is not listed in tests/uncovered_functions.txt.
        ("no uncovered public function",
         [py, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "function_coverage.py")]),
    ]

    # Taken once, compared after EVERY pass that runs the suite (#643). The
    # comparison used to happen here, right after these checks - so the
    # count pass and the hostile pass below could write settings.conf or
    # data/ and preflight said PASS. A path that derives differently with
    # ProgramFiles stripped is exactly the kind of write only the hostile
    # pass would make, and it was the one pass never checked.
    state_before = state_snapshot()
    results = [run(label, argv) for label, argv in checks]
    results.append(report_state_writes(state_before, state_snapshot()))

    # A test file that silently becomes empty - a bad edit, a broken import - lets
    # the suite report success while testing less. Pin a floor so shrinkage is loud.
    # Verbose, so the same run also says which tests were skipped and why (#642):
    # a skipped test is one that ran nothing, and until this nothing parsed
    # "skipped=N" - a pass that skipped a hundred tests printed PASS.
    MIN_TESTS = 165
    counted = capture([py, "-m", "unittest", "discover", "-v", "-s", "tests", "-t", "."])
    output = (counted.stderr or "") + (counted.stdout or "")
    match = re.search(r"Ran (\d+) tests", output)
    total = int(match.group(1)) if match else 0
    print("")
    print(f"=== test count: {total} (floor {MIN_TESTS}) ===")
    if total < MIN_TESTS:
        print(f"--- test count FAILED: only {total} collected. A module is not being")
        print("    discovered, or a file was emptied. Fewer tests is not a pass.")
        results.append(False)
    else:
        print("--- test count: PASS")
        results.append(True)

    results.append(report_state_writes(state_before, state_snapshot()))

    skipped, reasons = skip_report(output)
    print("")
    print(f"=== skipped: {skipped} of {total} (ceiling {MAX_SKIPPED}) ===")
    for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0])):
        print(f"    {count:3d}  {reason}")
    # The launcher classes' own wording - not "needs a POSIX shell", which
    # one Windows-hosted test says about itself with bash right there.
    if any(reason.startswith("no POSIX shell on PATH") for reason in reasons):
        print("    (no bash/sh on PATH: the POSIX launcher tests did not run. From")
        print("     Windows, run preflight from Git Bash to include them.)")
    if skipped > MAX_SKIPPED:
        print(f"--- skipped FAILED: {skipped} tests ran nothing. A whole family of tests")
        print("    is dark on this machine, and PASS would not cover it.")
        results.append(False)
    else:
        print("--- skipped: PASS")
        results.append(True)

    # The pass CI effectively performs and a developer machine never does.
    env = hostile_env()

    # Prove the environment really is stripped before trusting anything it reports.
    # A hostile pass that is not actually hostile is worse than no check at all: it
    # reports safety it never tested. This exact assertion caught the first version
    # of this script, which hid nothing.
    probe = capture(
        [py, "-c", "import platform_compat; print(platform_compat.rar_command() or 'NONE')"],
        env=env,
    )
    found = probe.stdout.strip()
    print("")
    print("=== verifying the hostile environment ===")
    print(f"    rar_command() under stripped env: {found}")
    if found != "NONE":
        # SKIPPED, not failed. This step exists to prove the suite passes on a
        # bare runner with no host tooling, and it fakes that by stripping PATH
        # and the tooling variables. On a machine where rar lives somewhere that
        # survives all of it - /usr/bin/rar on a Linux box, say - the environment
        # simply cannot be made hostile from in here, and nothing about that says
        # the code is wrong.
        #
        # Failing on it made preflight cry wolf on exactly the machines where it
        # was working correctly, which teaches people to ignore the output. The
        # real suite has already run and passed above; this is a bonus pass.
        print(f"--- hostile environment SKIPPED: host tooling is reachable "
              f"regardless ({found}).")
        print("    That is this machine, not the code - the full suite above "
              "already passed.")
        print("    If you meant to hide it, add whatever exposed it to "
              "HOST_TOOLING_VARS.")
    else:
        print("--- hostile environment verified: host tooling is hidden")
        results.append(run(
            "full suite with host tooling hidden (simulates a bare runner)",
            [py, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
            env=env,
        ))
        results.append(report_state_writes(state_before, state_snapshot()))

    print()
    if all(results):
        print("PREFLIGHT PASSED - safe to push")
        return 0

    print("PREFLIGHT FAILED - do not push")
    if results[-1] is False and all(results[:-1]):
        print()
        print("Note: only the hidden-tooling pass failed. That means a test depends on")
        print("something installed on this machine that CI does not have. Fix the test,")
        print("not the environment - CI will fail the same way.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
