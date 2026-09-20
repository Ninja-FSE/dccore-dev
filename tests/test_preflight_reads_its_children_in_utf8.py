"""preflight decodes what its children print as UTF-8, whatever the code page.

#620, reported from the operator's Greek Windows (cp1253). preflight captured
the test suite for its count and ran the hostile-environment probe with
`capture_output=True, text=True` and no encoding, so the parent decoded with
the locale code page, strict. The children write UTF-8: oserve.py installs
install_console_encoding_guard() the moment a test imports it, and
hostile_env() sets PYTHONUTF8=1 for the probe. The first byte cp1253 leaves
undefined - 0x9f, in any emoji a test prints - did not raise out of
subprocess.run. On Windows it killed the reader thread: a UnicodeDecodeError
traceback landed in preflight's own output where it read as a test failure,
and the captured stream came back as None. On a green run that was only a
misleading traceback; on a red run whose failure text carried such a
character, the count was parsed from None and preflight said "only 0
collected" - the wrong diagnosis for the real failure.

Now scripts/preflight.capture() decodes utf-8 with errors="replace", the way
commands.py, dcc.py and update_list.py already did (see
test_a_filename_your_code_page_cannot_spell.py, whose sweep over parents
that capture output now includes preflight).

The tests here run capture() for real, in a Python that has been forced onto
a narrow locale: on POSIX that is the C locale with Python's coercion and its
UTF-8 mode both switched off (ASCII); on Windows it is the machine's ANSI code
page with UTF-8 mode switched off. Whether that made the locale hostile is
PROBED, not assumed - a Windows box with "Use Unicode UTF-8 for worldwide
language support" ticked cannot be made hostile from in here, and asserting
the hazard universally has gone wrong before. The fix itself is asserted on
every machine: capture() returns the child's text regardless of the locale.
"""

import ast
import os
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFLIGHT = os.path.join(REPO_ROOT, "scripts", "preflight.py")

# An emoji (UTF-8 f0 9f 98 80: 0x9f is undefined in cp1253) and an A-acute
# (c3 81: 0x81 is undefined in cp1250, cp1252, cp1253, cp1254 and cp1257), so
# every common single-byte Windows code page trips on one of them.
TEXT = "count \U0001f600 \u00c1"

# The parent under test. It prints only ASCII (an ascii()) so that its OWN
# stdout, which is on the narrow locale too, cannot fail the test.
PARENT = r'''
import importlib.util, subprocess, sys
mode, path, text = sys.argv[1], sys.argv[2], sys.argv[3].encode("ascii").decode("unicode_escape")
child = [sys.executable, "-c",
         "import sys; sys.stdout.reconfigure(encoding='utf-8'); print(%s)" % ascii(text)]
if mode == "bare":
    # The old form. On Windows the reader thread dies and stdout is None; on
    # POSIX communicate() decodes at the end and raises. Both lose the stream.
    try:
        seen = subprocess.run(child, capture_output=True, text=True).stdout
    except UnicodeDecodeError:
        seen = None
else:
    spec = importlib.util.spec_from_file_location("preflight_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seen = module.capture(child).stdout
print("STDOUT=" + ascii(seen))
'''


def narrow_locale_env():
    env = dict(os.environ)
    env["PYTHONUTF8"] = "0"
    env.pop("PYTHONIOENCODING", None)
    if os.name != "nt":
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        env["PYTHONCOERCECLOCALE"] = "0"
    return env


def run_parent(mode):
    """The captured stdout the parent saw: a str, or None when the reader
    thread died on it."""
    done = subprocess.run(
        [sys.executable, "-c", PARENT, mode, PREFLIGHT,
         TEXT.encode("unicode_escape").decode("ascii")],
        cwd=REPO_ROOT, env=narrow_locale_env(), capture_output=True,
        encoding="utf-8", errors="replace", timeout=60)
    assert done.returncode == 0, done.stderr
    for line in done.stdout.splitlines():
        if line.startswith("STDOUT="):
            return ast.literal_eval(line[len("STDOUT="):])
    raise AssertionError("the parent never reported: " + done.stdout + done.stderr)


class PreflightReadsItsChildrenInUtf8(unittest.TestCase):

    def test_the_locale_really_is_hostile_here(self):
        """The bug, reproduced: text=True alone loses the whole stream on a
        narrow locale. Skipped, not failed, where the locale cannot be
        narrowed - that is the machine, not the code, and the assertion
        that matters is the next one."""
        seen = run_parent("bare")
        if seen is not None and TEXT in seen:
            self.skipTest("this machine's locale decodes UTF-8 on its own; "
                          "the hazard cannot be forced here")
        self.assertNotIn(TEXT, seen or "",
                         "text=True read the child correctly after all - "
                         "this reproduction has gone stale")

    def test_capture_returns_the_text_on_a_narrow_locale(self):
        seen = run_parent("fixed")

        self.assertIsNotNone(seen, "the reader thread died: capture() decoded "
                                   "with the locale code page")
        self.assertIn(TEXT, seen)


if __name__ == "__main__":
    unittest.main()
