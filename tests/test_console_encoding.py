"""The daemon must survive a console code page that cannot encode its own logs.

WHY THIS FILE FORCES THE ENCODING INSTEAD OF TRUSTING THE MACHINE

This project's log strings contain Swedish characters. print() encodes with
whatever code page the attached stream uses, and the answer differs by region:

    cp1252  (Western European)  contains a-ring/a-umlaut/o-umlaut   no symptom
    cp1253  (Greek)             does not                            crash
    cp1251  (Cyrillic)          does not                            crash
    cp932   (Japanese)          does not                            crash
    ascii   (POSIX/C locale)    does not                            crash

GitHub's windows-latest runner is Western European, so CI is structurally
incapable of catching this: it stays green on cp1252 forever while the daemon
dies on an operator's Greek box. A test that only used the ambient encoding
would therefore prove nothing on the machine that runs it.

So every test here pins the encoding explicitly - through a constructed stream,
or through PYTHONIOENCODING in a subprocess - and behaves identically on every
machine. The control test below deliberately reproduces the CRASH, so that if
someone deletes the guard, these tests fail instead of quietly passing.
"""
import io
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import platform_compat  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Written as escapes so this file stays pure ASCII and cannot itself be
# mangled by an editor or a code page on the way in.
SWEDISH = "S\u00f6kningen f\u00f6r anv\u00e4ndaren avslutades"

# The code pages a real operator actually runs, that cannot encode the above.
HOSTILE_CODE_PAGES = ("cp1253", "cp1251", "cp932", "ascii")

# A literal backslash byte, for asserting output was NOT escape-mangled.
BACKSLASH = chr(92).encode()


def _stream_using(encoding, errors="strict"):
    """A text stream over a real byte buffer, pinned to `encoding`."""
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors=errors)


def _clean_env():
    """A child environment with the UTF-8 escape hatches removed.

    PYTHONUTF8 / PYTHONLEGACYWINDOWSSTDIO would each mask the very condition
    these tests exist to create, and one of them may well be set in the shell
    that runs the suite.
    """
    env = dict(os.environ)
    for masking in ("PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO", "PYTHONIOENCODING"):
        env.pop(masking, None)
    return env


def _run_child(code, encoding):
    """Run `code` in a child whose stdout is pinned to `encoding`, redirected.

    Redirected on purpose: PEP 528 makes Python talk UTF-16 to a real console
    window whatever the code page, so the bug only appears when output goes to
    a pipe, a log file, or a service host - which is how the daemon runs.
    """
    env = _clean_env()
    env["PYTHONIOENCODING"] = encoding
    return subprocess.run([sys.executable, "-c", code],
                          cwd=REPO_ROOT, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class TheHazardIsReal(unittest.TestCase):
    """Control tests. If these ever stop failing/crashing, the premise of this
    file has changed and the guard's tests below would be proving nothing."""

    def test_an_unguarded_stream_raises_on_the_log_strings(self):
        for code_page in HOSTILE_CODE_PAGES:
            with self.subTest(code_page=code_page):
                stream = _stream_using(code_page)
                with self.assertRaises(UnicodeEncodeError):
                    stream.write(SWEDISH)
                    stream.flush()

    def test_western_european_hides_it(self):
        # Documents precisely why CI cannot catch this.
        stream = _stream_using("cp1252")
        stream.write(SWEDISH)
        stream.flush()

    def test_an_unguarded_child_process_dies(self):
        result = _run_child(f"print({SWEDISH!r})", "cp1253")
        self.assertNotEqual(result.returncode, 0,
                            "expected an unguarded print to fail on cp1253")
        self.assertIn(b"UnicodeEncodeError", result.stderr)


class TheGuardPreventsTheCrash(unittest.TestCase):

    def test_every_hostile_code_page_survives_after_the_guard(self):
        for code_page in HOSTILE_CODE_PAGES:
            with self.subTest(code_page=code_page):
                stream = _stream_using(code_page)
                changed = platform_compat.install_console_encoding_guard(
                    [("test", stream)])
                self.assertEqual(changed, ["test"])
                stream.write(SWEDISH)   # must not raise
                stream.flush()

    def test_the_characters_survive_intact_not_just_the_process(self):
        stream = _stream_using("cp1253")
        platform_compat.install_console_encoding_guard([("test", stream)])
        stream.write(SWEDISH)
        stream.flush()
        written = stream.buffer.getvalue()
        self.assertEqual(written.decode("utf-8"), SWEDISH)

    def test_a_stream_already_utf8_is_left_lossless(self):
        stream = _stream_using("utf-8")
        changed = platform_compat.install_console_encoding_guard([("test", stream)])
        self.assertEqual(changed, ["test"])
        stream.write(SWEDISH)
        stream.flush()
        self.assertEqual(stream.buffer.getvalue().decode("utf-8"), SWEDISH)

    def test_the_error_handler_is_pinned_even_when_encoding_was_already_utf8(self):
        # A stream that is utf-8 but strict still gets errors="replace", so a
        # surrogate or other unencodable value cannot raise later.
        stream = _stream_using("utf-8")
        platform_compat.install_console_encoding_guard([("test", stream)])
        stream.write("lone surrogate: \ud800")   # unencodable even in utf-8
        stream.flush()

    def test_errors_replace_is_load_bearing_on_a_reencoded_stream_too(self):
        """Switching a cp1253 stream to utf-8 is not enough on its own.

        A lone surrogate cannot be encoded by utf-8 either, so without
        errors="replace" the guard would MOVE the crash rather than remove
        it. Mutation-checked: dropping errors="replace" from that
        reconfigure() call makes this test - and only this test - fail.
        """
        stream = _stream_using("cp1253")
        platform_compat.install_console_encoding_guard([("test", stream)])
        stream.write("lone surrogate: \ud800")   # must not raise
        stream.flush()


class TheGuardIsSafeToCall(unittest.TestCase):

    def test_a_none_stream_is_skipped(self):
        # pythonw.exe on Windows gives None for stdout AND stderr.
        self.assertEqual(
            platform_compat.install_console_encoding_guard(
                [("stdout", None), ("stderr", None)]),
            [])

    def test_a_stream_without_reconfigure_is_skipped(self):
        self.assertEqual(
            platform_compat.install_console_encoding_guard(
                [("captured", io.StringIO())]),
            [])

    def test_a_closed_stream_does_not_raise(self):
        stream = _stream_using("cp1253")
        stream.close()
        platform_compat.install_console_encoding_guard([("closed", stream)])

    def test_calling_it_twice_is_harmless(self):
        stream = _stream_using("cp1253")
        platform_compat.install_console_encoding_guard([("test", stream)])
        platform_compat.install_console_encoding_guard([("test", stream)])
        stream.write(SWEDISH)
        stream.flush()


class TheDaemonInstallsItAtStartup(unittest.TestCase):
    """End to end, in a real child process, under a real hostile code page.

    This is the test that fails if someone removes the call from oserve.py -
    the unit tests above would all still pass, because they call the guard
    themselves.
    """

    def test_importing_oserve_makes_the_log_strings_safe(self):
        result = _run_child(
            f"import oserve; print({SWEDISH!r}); print('SURVIVED')", "cp1253")
        self.assertEqual(result.returncode, 0,
                         f"oserve did not guard stdout:\n"
                         f"{result.stderr.decode('utf-8', 'replace')}")
        self.assertIn(b"SURVIVED", result.stdout)

    def test_the_real_search_log_line_survives(self):
        # list.py prints this on every completed search - the specific line
        # that killed the search thread on a Greek-locale box.
        result = _run_child(
            "import oserve, list as list_mod;"
            "print('[SEARCH-FINISHED] S\\u00f6kningen f\\u00f6r x avslutades')",
            "cp1253")
        self.assertEqual(result.returncode, 0,
                         result.stderr.decode("utf-8", "replace"))

    def test_stderr_keeps_the_characters_rather_than_escaping_them(self):
        """stderr never crashed in the first place: it defaults to
        errors="backslashreplace", so asserting "did not crash" here would
        pass with the guard REMOVED and prove nothing - which is exactly what
        the first version of this test did. What the guard actually changes on
        stderr is fidelity: unguarded it writes the mangled ASCII literal
        S\\xf6kningen, guarded it writes the real characters.
        """
        result = _run_child(
            f"import oserve, sys; sys.stderr.write({SWEDISH!r})", "cp1253")
        self.assertEqual(result.returncode, 0,
                         result.stderr.decode("utf-8", "replace"))
        self.assertEqual(result.stderr.decode("utf-8"), SWEDISH)
        self.assertNotIn(BACKSLASH, result.stderr)


if __name__ == "__main__":
    unittest.main()
