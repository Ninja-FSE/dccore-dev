"""Regression tests for scripts/linux/check-setup.py's numeric sanity checks.

check-setup.py is a standalone script (not a set of importable functions -
`import config` at module scope, print()s a report, sys.exit(1)s on a real
problem), so it is run as a real subprocess here rather than imported, the
same way tests/test_console_encoding.py drives platform_compat's console
guard as a child process.

Before this fix, two misconfigurations passed the check silently and only
surfaced once the daemon was actually running:

* MAX_DCC_SLOTS <= 0 - the bot boots, joins its channels, and then never
  dispatches a single queued transfer, because dcc.py's own slot-count gate
  (`len(config.active_transfers) < config.MAX_DCC_SLOTS`) can never be true.
  Every request just sits in the queue forever, with nothing in the startup
  output saying why.
* DCC_PORT_START > DCC_PORT_END - range(start, end + 1) is empty, so the
  ports loop found 0 free ports and reported "no port in X-Y could be
  bound - something else is using them", which sends an operator looking
  for a phantom port conflict instead of the swapped values that are
  actually the problem.

local_config.py is gitignored and gets `from local_config import *`-applied
by config.py if present (see config.py's own comment on that mechanism) -
these tests inject one through PYTHONPATH rather than writing into the real
repository's local_config.py, which a machine actually running this daemon
could have real, meaningful content in.
"""

import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK_SETUP = os.path.join(REPO_ROOT, "scripts", "linux", "check-setup.py")


def _run_with_local_config(overrides_source, extra_env=None):
    """Run check-setup.py as a real subprocess with a throwaway local_config.py
    placed earlier on sys.path than the real repository - config.py's own
    `from local_config import *` then picks up ours, not (a nonexistent, in
    this checkout) real one, and the real repository is never touched.
    """
    tmp_dir = tempfile.mkdtemp(prefix="dccore-checksetup-")
    try:
        with open(os.path.join(tmp_dir, "local_config.py"), "w", encoding="utf-8") as handle:
            handle.write(overrides_source)

        env = dict(os.environ)
        env["PYTHONPATH"] = tmp_dir + os.pathsep + env.get("PYTHONPATH", "")
        if extra_env:
            env.update(extra_env)

        return subprocess.run(
            [sys.executable, CHECK_SETUP],
            cwd=REPO_ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30, text=True,
        )
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


class MaxDccSlotsSanityTests(unittest.TestCase):

    def test_zero_slots_is_reported_as_a_failure(self):
        result = _run_with_local_config("MAX_DCC_SLOTS = 0\n")
        self.assertIn("MAX_DCC_SLOTS is 0", result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_negative_slots_is_reported_as_a_failure(self):
        result = _run_with_local_config("MAX_DCC_SLOTS = -2\n")
        self.assertIn("MAX_DCC_SLOTS is -2", result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_a_positive_slot_count_is_not_flagged(self):
        result = _run_with_local_config("MAX_DCC_SLOTS = 5\n")
        self.assertNotIn("MAX_DCC_SLOTS is", result.stdout)
        self.assertIn("max DCC slots 5", result.stdout)


class DccPortRangeSanityTests(unittest.TestCase):

    def test_a_swapped_range_is_reported_as_a_failure_not_a_phantom_conflict(self):
        result = _run_with_local_config(
            "DCC_PORT_START = 55010\nDCC_PORT_END = 55000\n")
        self.assertIn(
            "DCC_PORT_START (55010) is greater than DCC_PORT_END (55000)",
            result.stdout)
        # The old, misleading message for this exact case must not appear -
        # this range was never actually contended, it is just empty.
        self.assertNotIn("something else is using them", result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_an_ordinary_range_is_not_flagged(self):
        result = _run_with_local_config(
            "DCC_PORT_START = 55000\nDCC_PORT_END = 55010\n")
        self.assertNotIn("is greater than DCC_PORT_END", result.stdout)


class AdminNickSanityTests(unittest.TestCase):
    """#162 finding #20: NICKNAME still being upstream is a hard FAIL with a
    careful rationale; ADMIN_NICK used to be printed with a plain "ok" and
    compared against nothing at all - a config the check declared "Ready to
    start" could still hand the upstream operator's nick full control of
    !ban/!rehash/!update/!clearqueue on the new operator's bot."""

    def test_the_bare_upstream_admin_nick_is_reported_as_a_failure(self):
        result = _run_with_local_config("ADMIN_NICK = 'FLAC,Samoth'\n")
        self.assertIn("ADMIN_NICK is still", result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_one_upstream_name_among_several_still_fails(self):
        """ADMIN_NICK is comma-separated - a new operator who ADDS their own
        nick without removing the upstream one is still exposed."""
        result = _run_with_local_config("ADMIN_NICK = 'Samoth,MyOwnNick'\n")
        self.assertIn("ADMIN_NICK is still", result.stdout)
        self.assertIn("samoth", result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_case_and_whitespace_do_not_evade_the_check(self):
        result = _run_with_local_config("ADMIN_NICK = ' Flac , samoth '\n")
        self.assertIn("ADMIN_NICK is still", result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_a_genuinely_different_admin_nick_is_not_flagged(self):
        result = _run_with_local_config("ADMIN_NICK = 'MyOwnOperatorNick'\n")
        self.assertNotIn("ADMIN_NICK is still", result.stdout)
        self.assertIn("ok     admin nick MyOwnOperatorNick", result.stdout)


class CleanConfigurationPassesEndToEnd(unittest.TestCase):
    """Control: a configuration with nothing wrong except (an artifact of
    how this test injects overrides, not a real misconfiguration - see
    _run_with_local_config's docstring) the literal <repo>/local_config.py
    and <repo>/settings.conf existence check (#162 finding #19: neither
    exists in a fresh checkout, and check-setup.py now only fails when
    BOTH are absent - see scripts/setup_check.py's own comment) reports
    only THAT one problem, proving the two new checks above do not
    false-positive on ordinary, valid values."""

    def test_only_the_unrelated_local_config_path_check_fires(self):
        tmp_dir = tempfile.mkdtemp(prefix="dccore-checksetup-music-")
        try:
            result = _run_with_local_config(
                "NICKNAME = 'TotallyNotUpstream'\n"
                "ADMIN_NICK = 'TotallyNotUpstreamEither'\n"
                f"FILE_DIRECTORY = {tmp_dir!r}\n"
                "MAX_DCC_SLOTS = 3\n"
                "DCC_PORT_START = 55000\n"
                "DCC_PORT_END = 55010\n"
            )
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)

        self.assertNotIn("MAX_DCC_SLOTS is", result.stdout)
        self.assertNotIn("is greater than DCC_PORT_END", result.stdout)
        self.assertIn("ok     max DCC slots 3", result.stdout)
        # Not "all N ports free": some of 55000-55010 may genuinely be busy on
        # a machine already running other tests or another instance of the
        # daemon - that outcome (and "no port ... could be bound" if every one
        # of them happens to be busy) is pre-existing, unrelated behaviour of
        # the bind-probing loop this PR does not touch, so it is not asserted
        # on either way here.
        self.assertIn("no local_config.py and no settings.conf", result.stdout)


class SettingsConfSatisfiesTheConfigurationRequirement(unittest.TestCase):
    """#162 finding #19's core claim, exercised for real: settings.conf ALONE,
    with no local_config.py at all, must satisfy check-setup.py's "you
    configured something" requirement. It used to hard-fail unconditionally
    whenever local_config.py was absent, even though the daemon starts fine
    from settings.conf alone (config.py's settings_file.apply_to() applies it
    the same way regardless of which file provided it) - contradicting the
    check's own "Applied N setting(s)" line on the very same run."""

    def test_settings_conf_alone_satisfies_the_check(self):
        tmp_dir = tempfile.mkdtemp(prefix="dccore-checksetup-settingsconf-")
        music_dir = tempfile.mkdtemp(prefix="dccore-checksetup-music-")
        try:
            settings_conf = os.path.join(tmp_dir, "settings.conf")
            with open(settings_conf, "w", encoding="utf-8") as handle:
                handle.write(
                    "NICKNAME = TotallyNotUpstream\n"
                    f"FILE_DIRECTORY = {music_dir}\n"
                )
            env = dict(os.environ)
            env["DCCORE_SETTINGS_FILE"] = settings_conf
            result = subprocess.run(
                [sys.executable, CHECK_SETUP],
                cwd=REPO_ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=30, text=True,
            )
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
            shutil.rmtree(music_dir, ignore_errors=True)

        self.assertNotIn("no local_config.py and no settings.conf", result.stdout)
        self.assertIn("configured via settings.conf (no local_config.py)", result.stdout)
        self.assertIn("Applied", result.stdout)


if __name__ == "__main__":
    unittest.main()
