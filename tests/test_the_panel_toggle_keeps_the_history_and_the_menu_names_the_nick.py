"""#582 and #584: two bugs in dccore.mrc's window, found by the 2026-09-20 audit.

CI has no mIRC, so this reads the script (as test_the_bots_window_in_mirc.py
does) and pins the shape of each fix.

#582 - toggling the side panel (Options, /dccore panel, right-click) rebuilt
the window by closing it and THEN reading its lines, which do not exist any
more: the history was gone, the first empty /echo halted the alias, and the
"rebuilding" flag - which makes the CLOSE handler ignore the close - stayed set
for good, so @DCCore could not be closed. The lines are now copied out first,
an empty one is kept as a non-breaking space, and the flag is a start time that
stops counting after a few seconds.

#584 - the right-click "Queue of" / "Clear the queue of" read the nick back
from the panel text, where it is cut or padded to nine characters: a long nick
was truncated (the bot cleared the queue of a nick that does not exist), a
short one came back with its padding. The number that starts a queue row names
the row in the status the panel was drawn from, and a sending row's nine
characters are matched against the slots; the whole nick comes from there.
"""

import io
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

SCRIPT = os.path.join(REPO_ROOT, "scripts", "mirc", "dccore.mrc")


def script():
    with io.open(SCRIPT, encoding="ascii", newline="") as handle:
        return handle.read().replace("\r\n", "\n")


def alias_body(name):
    text = script()
    start = text.index("alias " + name + " {")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise AssertionError(name)


class TheRebuild(unittest.TestCase):

    def test_the_lines_are_copied_out_before_the_window_is_closed(self):
        body = alias_body("dccore.rebuild")
        self.assertLess(body.index("hadd dccore.rb %i %t"), body.index("window -c $dccore.win"))

    def test_the_lines_are_written_back_from_the_copy_not_from_the_new_window(self):
        body = alias_body("dccore.rebuild")
        after_close = body[body.index("window -c $dccore.win"):]
        self.assertIn("$hget(dccore.rb,%i)", after_close)
        self.assertNotIn("$line($dccore.win,%i)", after_close, "reading the empty new window is the bug")

    def test_an_empty_line_is_kept_as_a_non_breaking_space(self):
        """/hadd and /echo both refuse an empty text; either would halt the alias."""
        body = alias_body("dccore.rebuild")
        self.assertIn("if (%t == $null) { var %t = $dccore.nbsp }", body)
        self.assertLess(body.index("if (%t == $null)"), body.index("hadd dccore.rb"))

    def test_the_table_is_made_fresh_and_freed(self):
        body = alias_body("dccore.rebuild")
        self.assertIn("if ($hget(dccore.rb)) { hfree dccore.rb }", body)
        self.assertLess(body.index("hmake dccore.rb"), body.index("hadd dccore.rb"))
        self.assertLess(body.index("echo -i2"), body.rindex("hfree dccore.rb"))

    def test_the_flag_is_a_start_time_and_is_cleared_at_the_end(self):
        body = alias_body("dccore.rebuild")
        self.assertIn("hadd dccore.live rebuilding $ticks", body)
        self.assertTrue(body.rstrip().endswith("hadd dccore.live rebuilding 0\n}"))

    def test_close_ignores_only_a_rebuild_that_is_recent(self):
        text = script()
        handler = text[text.index("on *:CLOSE:@DCCore: {"):]
        handler = handler[:handler.index("\n}") + 2]
        self.assertIn("if ($dccore.st(rebuilding)) && ($calc($ticks - $dccore.st(rebuilding)) < 5000) { return }", handler)

    def test_nothing_else_still_sets_the_flag_to_one(self):
        self.assertNotIn("rebuilding 1\n", script())


class TheRightClickNick(unittest.TestCase):

    def test_a_queue_row_is_resolved_through_its_number_not_its_text(self):
        body = alias_body("dccore.selq")
        self.assertIn("(\\d+)", body)
        self.assertIn("$dccore.st(queue. $+ $regml(dccoreq,1))", body)
        self.assertNotIn("(\\S+)", body, "reading the nick off the padded text is the bug")

    def test_a_sending_row_is_matched_against_the_slots_by_its_nine_characters(self):
        body = alias_body("dccore.sels")
        self.assertIn("(.{9})", body)
        self.assertIn("$dccore.fit(%n,9) == %f", body)
        self.assertIn("$dccore.st(slot. $+ %i)", body)
        self.assertNotIn("(\\S+)", body)

    def test_the_lookups_use_the_same_keys_the_panel_is_drawn_from(self):
        text = script()
        self.assertIn("$dccore.st(queue. $+ %i)", text[text.index("alias dccore.panel"):text.index("alias dccore.selq")])
        self.assertIn("$dccore.st(slot. $+ %i)", text[text.index("alias dccore.panel"):text.index("alias dccore.selq")])

    def test_a_queue_row_starts_with_its_number_as_the_lookup_expects(self):
        panel = script()
        panel = panel[panel.index("alias dccore.panel"):panel.index("alias dccore.selq")]
        self.assertGreaterEqual(panel.count("$dccore.rfit(%i,2) $dccore.fit($gettok(%l,1,32),9)"), 2,
                                "both the frozen and the ordinary queue row")

    def test_the_menu_still_offers_both(self):
        text = script()
        self.assertIn("Queue of $dccore.selq", text)
        self.assertIn("Clear the queue of $dccore.selq", text)
        self.assertIn("Queue of $dccore.sels", text)

    def test_the_regexes_do_what_the_script_relies_on(self):
        """The two patterns, run on rows built exactly as the panel builds them."""
        nbsp = "\xa0"

        def fit(nick, width):
            return (nick + nbsp * width)[:width]

        def rfit(text, width):
            return (nbsp * width + text)[-width:]

        queue = rfit("3", 2) + " " + fit("longnickname1", 9) + " 4 files"
        short = rfit("12", 2) + " " + fit("helen", 9) + " 1 file"
        sending = "> " + fit("longnickname1", 9) + " 1.2G"

        number = re.compile("^[ \xa0]*(\\d+)[ \xa0]+\\S")
        self.assertEqual(number.match(queue).group(1), "3")
        self.assertEqual(number.match(short).group(1), "12")
        # headings and blank panel rows are not queue rows
        for other in ("Queue 3 (12 files)", "\xa0sent 5 / 1.2G", "\xa0\xa0... 4 more", "Sending 1/3", "\xa0"):
            self.assertIsNone(number.match(other), other)

        nine = re.compile("^>[ \xa0]+(.{9})").match(sending).group(1)
        self.assertEqual(nine, fit("longnickname1", 9))
        self.assertEqual(nine, "longnickn")


if __name__ == "__main__":
    unittest.main()
