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
    env["PATH"] = os.path.dirname(sys.executable)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def main():
    py = sys.executable
    checks = [
        # Mirrors .github/workflows/tests.yml, in the same order.
        ("every module imports", [py, "-c",
         "import config, platform_compat, db, stats_mgr, queue_mgr, security, "
         "list, announce, dcc, irc, commands, update_list, webserver; print('ok')"]),
        ("compile every source file", [py, "-m", "compileall", "-q", "."]),
        ("full suite", [py, "-m", "unittest", "discover", "-s", "tests", "-t", "."]),
    ]

    results = [run(label, argv) for label, argv in checks]

    # A test file that silently becomes empty - a bad edit, a broken import - lets
    # the suite report success while testing less. Pin a floor so shrinkage is loud.
    MIN_TESTS = 165
    counted = subprocess.run(
        [py, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    match = re.search(r"Ran (\d+) tests", (counted.stderr or "") + (counted.stdout or ""))
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

    # The pass CI effectively performs and a developer machine never does.
    env = hostile_env()

    # Prove the environment really is stripped before trusting anything it reports.
    # A hostile pass that is not actually hostile is worse than no check at all: it
    # reports safety it never tested. This exact assertion caught the first version
    # of this script, which hid nothing.
    probe = subprocess.run(
        [py, "-c", "import platform_compat; print(platform_compat.rar_command() or 'NONE')"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )
    found = probe.stdout.strip()
    print("")
    print("=== verifying the hostile environment ===")
    print(f"    rar_command() under stripped env: {found}")
    if found != "NONE":
        print("--- hostile environment is NOT hostile: host tooling is still reachable.")
        print("    Add whatever exposed it to HOST_TOOLING_VARS, or the check is theatre.")
        results.append(False)
    else:
        print("--- hostile environment verified: host tooling is hidden")
        results.append(run(
            "full suite with host tooling hidden (simulates a bare runner)",
            [py, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
            env=env,
        ))

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
