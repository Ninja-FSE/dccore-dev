"""The dashboard's JavaScript is not left syntactically broken.

There is no JS engine in this suite, and the whole dashboard is one script:
a single unterminated string anywhere in web/app.js takes down every tab at
once, silently, with a blank page and one line in a console nobody has open.
That is the same shape as the failure that reached the RC1 beta - the Settings
page rendering headings and no fields, with nothing in the log.

This is not a parser and does not pretend to be one. It checks the invariants
that a hand-edit actually breaks, and it caught the one that put it here: a
"\\n" written into a string through a shell heredoc arrived as a REAL newline,
so the string ran off the end of its line. The file still looked right in a
diff.

WHY A SCANNER AND NOT A REGEX. A quote inside a comment, an apostrophe inside
a double-quoted string, and an escaped quote inside its own kind are all
ordinary here and all defeat counting. So this walks the file once, in the
order a lexer would: block comment, line comment, string, code.

Template literals would legitimately span lines. app.js has no backtick in
CODE (all 26 are prose inside comments), and the file targets the same
ES5-era style as the rest of web/, so one appearing is itself worth knowing
about rather than a case to handle.
"""

import io
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

WEB_DIR = os.path.join(REPO_ROOT, "web")


def scan(source):
    """Walk `source` once and return (unterminated_strings, depth_by_kind).

    unterminated_strings: [(line_number, quote_character), ...] - a string
    opened and still open when its line ended.
    depth_by_kind: the final nesting depth of (), {} and [] counted outside
    strings and comments; every one should be zero.
    """
    unterminated = []
    depth = {"(": 0, "{": 0, "[": 0}
    closers = {")": "(", "}": "{", "]": "["}
    line = 1
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char == "\n":
            line += 1
            index += 1
            continue
        if source.startswith("//", index):
            index = source.find("\n", index)
            if index == -1:
                break
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end == -1:
                index = length
                break
            line += source.count("\n", index, end)
            index = end + 2
            continue
        if char in "\"'":
            quote = char
            index += 1
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == quote:
                    index += 1
                    break
                if source[index] == "\n":
                    unterminated.append((line, quote))
                    break
                index += 1
            continue
        if char in depth:
            depth[char] += 1
        elif char in closers:
            depth[closers[char]] -= 1
        index += 1
    return unterminated, depth


class TheScannerItself(unittest.TestCase):
    """Guard on the guard. A scanner that never reports anything would pass
    every file below and mean nothing."""

    def test_a_string_running_off_its_line_is_reported(self):
        broken, _ = scan('var a = "hello\nvar b = 1;\n')

        self.assertEqual(broken, [(1, '"')])

    def test_an_escaped_newline_sequence_is_not_a_newline(self):
        """The exact case that put this file here: the two characters
        backslash and n are fine; a real newline is not."""
        clean, _ = scan('var a = "hello\\nworld";\n')

        self.assertEqual(clean, [])

    def test_a_quote_inside_a_comment_is_not_a_string(self):
        clean, _ = scan('// it\'s fine\nvar a = 1;\n')

        self.assertEqual(clean, [])

    def test_an_apostrophe_inside_double_quotes_is_not_an_opener(self):
        clean, _ = scan('var a = "it\'s fine";\n')

        self.assertEqual(clean, [])

    def test_an_escaped_quote_does_not_close_its_string(self):
        clean, _ = scan('var a = "say \\"hi\\" now";\n')

        self.assertEqual(clean, [])

    def test_a_brace_inside_a_string_is_not_counted(self):
        _, depth = scan('var a = "{{{";\n')

        self.assertEqual(depth["{"], 0)

    def test_an_unclosed_brace_is_counted(self):
        _, depth = scan('function f() {\n')

        self.assertEqual(depth["{"], 1)

    def test_a_block_comment_hides_everything_in_it(self):
        clean, depth = scan('/* " { ( */ var a = 1;\n')

        self.assertEqual(clean, [])
        self.assertEqual(depth, {"(": 0, "{": 0, "[": 0})

    def test_the_line_number_survives_a_multi_line_block_comment(self):
        broken, _ = scan('/* one\ntwo\nthree */\nvar a = "x\n')

        self.assertEqual(broken, [(4, '"')])


class EveryScriptInWebIsIntact(unittest.TestCase):

    def scripts(self):
        return sorted(name for name in os.listdir(WEB_DIR)
                      if name.endswith(".js"))

    def read(self, name):
        with io.open(os.path.join(WEB_DIR, name), encoding="utf-8") as handle:
            return handle.read()

    def test_there_is_something_to_check(self):
        """Guard on the guard: a loop over an empty directory passes."""
        self.assertIn("app.js", self.scripts())

    def test_no_string_runs_off_the_end_of_its_line(self):
        for name in self.scripts():
            broken, _ = scan(self.read(name))

            self.assertEqual(
                broken, [],
                f"{name}: a string literal is left open at "
                f"line(s) {[line for line, _ in broken]} - the whole "
                f"dashboard fails to parse, every tab at once")

    def test_brackets_balance(self):
        for name in self.scripts():
            _, depth = scan(self.read(name))

            self.assertEqual(
                {kind: value for kind, value in depth.items() if value},
                {},
                f"{name}: unbalanced brackets")

    def test_no_template_literal_has_appeared_in_code(self):
        """All of app.js's backticks are prose inside comments. One reaching
        code would mean a string CAN legitimately span lines, and the check
        above would need to know about it."""
        for name in self.scripts():
            source = self.read(name)
            without_comments = []
            index = 0
            while index < len(source):
                if source.startswith("//", index):
                    end = source.find("\n", index)
                    index = len(source) if end == -1 else end
                elif source.startswith("/*", index):
                    end = source.find("*/", index + 2)
                    index = len(source) if end == -1 else end + 2
                else:
                    without_comments.append(source[index])
                    index += 1

            self.assertNotIn("`", "".join(without_comments),
                             f"{name}: a template literal in code - see this "
                             f"test's docstring")


if __name__ == "__main__":
    unittest.main()
