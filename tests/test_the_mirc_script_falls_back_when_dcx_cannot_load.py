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

    def test_a_loader_that_is_not_the_shipped_one_stops_it_but_a_missing_one_does_not(self):
        self.assertIn("if ($isfile($dccore.dcx.wv2file)) && ($sha256($dccore.dcx.wv2file,2) != "
                      "$dccore.dcx.wv2sha256)", self.body)
        self.assertIn("$scriptdir $+ dcx\\WebView2Loader.dll", alias("dccore.dcx.wv2file"))
        self.assertLess(self.position("$sha256($dccore.dcx.file,2)"), self.position("$sha256($dccore.dcx.wv2file,2)"))
        self.assertLess(self.position("$sha256($dccore.dcx.wv2file,2)"), self.position("$dccore.dcx.version == $null"))

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


class TheTree(unittest.TestCase):
    """Phase 2: the built-in window, marked, with a DCX tree for the pages."""

    def test_it_is_built_before_the_first_page_is_shown(self):
        lines = statements(alias("dccore.sw.init"))
        self.assertLess(lines.index("dccore.sw.dcx"), lines.index("dccore.sw.tab 1"))

    def test_nothing_happens_when_dcx_cannot_be_used(self):
        lines = statements(alias("dccore.sw.dcx"))
        self.assertEqual(lines[0], "if ($dccore.dcx.why != $null) { return }")
        self.assertEqual(lines[1], "dccore.dcx Mark dccore.set dccore.sw.dcxcb")
        self.assertEqual(lines[2], "hadd dccore.sws dcx 1")

    def test_the_strip_and_the_buttons_are_hidden_for_it(self):
        body = alias("dccore.sw.dcx")
        self.assertIn("did -h dccore.set $calc(1000 + %g)", body)
        self.assertIn("did -h dccore.set $calc(1019 + %i)", body)
        # and dccore.sw.tab does not show the buttons again
        self.assertIn("if (!$dccore.sw.s(dcx)) { did -v dccore.set $calc(1019 + %i) }", alias("dccore.sw.tab"))
        self.assertIn("if (!$dccore.sw.s(dcx)) { did -f dccore.set $calc(1000 + %g) }", alias("dccore.sw.goto"))

    def test_it_is_placed_by_the_generated_geometry_and_the_reported_scale(self):
        body = alias("dccore.sw.dcx")
        self.assertIn("$dialog(dccore.set).cw / $gettok($dccore.sw.m(size),1,32)", body)
        self.assertIn("$dialog(dccore.set).ch / $gettok($dccore.sw.m(size),2,32)", body)
        self.assertIn("-c %id treeview %x %y %w %h", body)
        found = re.search(r"(?m)^  hadd dccore\.swm dcxtree (\d+) (\d+) (\d+) (\d+) (\d+)$", raw())
        size = re.search(r"(?m)^  hadd dccore\.swm size (\d+) (\d+)$", raw())
        self.assertTrue(found and size)
        tree_id, x, y, w, h = (int(n) for n in found.groups())
        import build_settings_window as builder
        self.assertEqual((int(size.group(1)), int(size.group(2))), (builder.DIALOG_W, builder.DIALOG_H))
        self.assertLessEqual(x + w, builder.CONTENT_X - 2)
        self.assertLessEqual(y + h, builder.CONNECTED[1] - 2)
        table = block("dialog dccore.set {", raw())
        self.assertNotIn(", %d, " % tree_id, table)

    def test_groups_and_pages_come_from_the_window_data(self):
        body = alias("dccore.sw.dcx")
        self.assertIn("$+(-1,$chr(9),+beE 0 0 0 0 0 0 0 0 $dccore.sw.m(g. $+ %g))", body)
        self.assertIn("$+(%g -1,$chr(9),+E 0 0 0 0 0 0 0 0 $dccore.sw.untext($dccore.sw.m(p. $+ "
                      "$gettok(%pages,%i,32))))", body)

    def test_the_page_shown_is_selected_quietly(self):
        self.assertIn("if ($dccore.sw.s(dcx)) { dccore.sw.dcxshow $1 }", alias("dccore.sw.page"))
        lines = statements(alias("dccore.sw.dcxshow"))
        quiet = lines.index("hadd dccore.sws dcxquiet 1")
        select = [i for i, line in enumerate(lines) if " -c %g %at" in line][0]
        self.assertLess(quiet, select)
        self.assertLess(select, lines.index("hdel dccore.sws dcxquiet"))

    def test_a_click_drives_the_same_switch_as_the_buttons(self):
        body = alias("dccore.sw.dcxcb")
        self.assertIn("(!$istok(sclick selchange,$2,32)) || ($dccore.sw.s(dcxquiet)) { return }", body)
        self.assertIn("var %p = $gettok($dccore.sw.m(g. $+ %g $+ .pages),%at,32)", body)
        self.assertIn("hadd dccore.sws last. $+ %g %p", body)
        self.assertTrue(statements(body)[-1] == "dccore.sw.tab %g")


if __name__ == "__main__":
    unittest.main()
