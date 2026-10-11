"""dccore.mrc uses DCX only when it can, and says why when it cannot (#1288).

mIRC cannot be run here, so these read the script: every reason the built-in
window is used must be in $dccore.dcx.why, in an order where a cheap check
answers before the dll is touched, and a dll that will not load must not stop
the script.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_the_mirc_settings_window import alias, block, code, raw, statements  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts", "mirc"))

import settings_window_layout as layout  # noqa: E402


class WhyNot(unittest.TestCase):

    def setUp(self):
        self.why = statements(alias("dccore.dcx.why"))
        self.body = "\n".join(self.why)

    def position(self, needle):
        self.assertIn(needle, self.body)
        return self.body.index(needle)

    def test_every_reason_is_there(self):
        for needle in ("$dccore.opt(nodcx)", "$version < 7.45", "$bits == 64",
                       "!$isfile($dccore.dcx.file)", "$sha256($dccore.dcx.file,2) != $dccore.dcx.sha256",
                       "$dccore.dcx.version == $null"):
            self.position(needle)

    def test_the_dll_is_touched_last(self):
        # The option, the version, the bitness, the file and its hash all
        # answer without loading anything; only then is the dll asked.
        order = [self.position(n) for n in ("$dccore.opt(nodcx)", "$version < 7.45", "$bits == 64",
                                            "!$isfile(", "$sha256(", "$dccore.dcx.version == $null")]
        self.assertEqual(order, sorted(order))

    def test_a_dll_another_script_loaded_is_not_hash_checked(self):
        guard = block("if (!$dccore.dcx.loaded) {", "\n".join(self.why))
        self.assertIn("$isfile(", guard)
        self.assertIn("$sha256(", guard)
        self.assertNotIn("$dccore.dcx.version", guard)

    def test_success_says_nothing(self):
        # An empty answer is what "use DCX" means to every caller.
        self.assertFalse([s for s in self.why if s.startswith("return") and len(s.split()) == 1])
        self.assertTrue(all("{ return " in s for s in self.why if s.startswith("if (") and "{" in s
                            and not s.endswith("{")))


class TheLoadCannotStopTheScript(unittest.TestCase):

    def test_the_version_call_traps_its_error(self):
        lines = statements(alias("dccore.dcx.version"))
        self.assertTrue(lines[0].startswith("return $dll($dccore.dcx.path,Version,"))
        self.assertIn(":error", lines)
        self.assertIn("reseterror", lines)
        self.assertLess(lines.index(":error"), lines.index("reseterror"))

    def test_the_dll_is_always_called_by_its_full_path(self):
        body = alias("dccore.dcx")
        self.assertIn("$dll($dccore.dcx.path,$1,$2-)", body)
        self.assertIn("dll $qt($dccore.dcx.path) $1 $2-", body)
        # Never by bare name, which would pick up whatever dcx.dll mIRC finds.
        self.assertNotRegex(code(), r"(?m)^\s*dll\s+\"?dcx\.dll")

    def test_no_users_dcx_aliases_are_relied_on(self):
        # xdid/xdialog/dcx are what DCX's own helper script defines; a user
        # may have them, or other versions of them. Ours goes through dccore.dcx.
        for line in code().split("\n"):
            first = line.strip().split(" ", 1)[0]
            self.assertNotIn(first, ("xdid", "xdialog", "dcx"), line)

    def test_the_path_is_beside_the_script(self):
        self.assertIn("$scriptdir $+ dcx\\dcx.dll", alias("dccore.dcx.file"))
        self.assertIn("$dll(dcx.dll)", alias("dccore.dcx.loaded"))


class TheOption(unittest.TestCase):

    def test_it_defaults_to_dcx(self):
        self.assertIn("dccore.default nodcx 0", alias("dccore.init"))

    def test_it_is_on_the_this_mirc_window_page(self):
        pages = {name: sections for _group, ps in layout.GROUPS for name, sections in ps}
        items = [item for _title, section in pages["This mIRC window"] for item in section]
        self.assertIn({"local": "nodcx", "label": "Use the built-in settings window, not DCX"}, items)

    def test_the_generated_window_carries_it(self):
        self.assertRegex(raw(), r"(?m)^  hadd dccore\.swm lo\.nodcx \d+$")


class TheCommand(unittest.TestCase):

    def test_dccore_dcx_and_check(self):
        branch = block("if (%cmd == dcx) {", alias("dccore"))
        self.assertIn("if ($2 == check) { dccore.dcx.check }", branch)
        self.assertIn("else { dccore.dcx.status }", branch)

    def test_the_help_lists_it(self):
        self.assertIn("/dccore dcx [check]", alias("dccore"))

    def test_the_status_says_why_not(self):
        body = alias("dccore.dcx.status")
        self.assertIn("var %why = $dccore.dcx.why", body)
        self.assertIn("DCX is not used: %why", body)

    def test_the_check_is_refused_when_dcx_cannot_be_used(self):
        lines = statements(alias("dccore.dcx.check"))
        self.assertEqual(lines[0], "var %why = $dccore.dcx.why")
        self.assertTrue(lines[1].startswith("if (%why != $null) {") and "return" in lines[1])


class TheCheckWindow(unittest.TestCase):

    def test_it_holds_a_native_button_and_the_box_the_tree_should_fill(self):
        table = block("dialog dccore.dcxcheck {")
        self.assertIn("option dbu", table)
        self.assertRegex(table, r'button "[^"]+", 1, ')
        self.assertRegex(table, r'box "[^"]+", 2, 110 4 126 90')

    def test_mark_comes_before_any_dcx_control(self):
        lines = statements(alias("dccore.dcx.checkinit"))
        self.assertEqual(lines[0], "dccore.dcx Mark dccore.dcxcheck dccore.dcx.checkcb")
        dcx = [i for i, line in enumerate(lines) if line.startswith("dccore.dcx x")]
        self.assertTrue(dcx and min(dcx) > 0)

    def test_the_tree_is_placed_inside_the_box_by_the_reported_scale(self):
        body = alias("dccore.dcx.checkinit")
        self.assertIn("$dialog(dccore.dcxcheck).cw", body)
        self.assertIn("$calc(%cw / 240)", body)
        self.assertIn("-c 10 treeview %x %y %w %h", body)
        # 114,14 118x76 sits inside the box at 110,4 126x90, clear of its label.
        found = re.search(r"\$calc\((\d+) \* %sx\).*\$calc\((\d+) \* %sy\).*\$calc\((\d+) \* %sx\)"
                          r".*\$calc\((\d+) \* %sy\)", body)
        x, y, w, h = (int(n) for n in found.groups())
        self.assertTrue(110 < x and x + w < 110 + 126 and 4 + 8 <= y and y + h < 4 + 90)

    def test_both_events_reach_atdccore(self):
        self.assertIn("on *:dialog:dccore.dcxcheck:sclick:1:", code())
        self.assertIn("if ($2 == sclick) && ($3 == 10)", alias("dccore.dcx.checkcb"))


if __name__ == "__main__":
    unittest.main()
