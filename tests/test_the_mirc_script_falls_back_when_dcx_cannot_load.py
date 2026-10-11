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

from test_the_mirc_settings_window import (alias, block, code, dialog, generated_data, handwritten, raw,  # noqa: E402
                                           statements, untext)

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

    def test_it_is_on_the_script_tabs_window_page(self):
        pages = {name: sections for _group, ps in layout.GROUPS for name, sections in ps}
        items = [item for _title, section in pages["Window"] for item in section]
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


class ThePageList(unittest.TestCase):
    """Phase 2 and 3: the built-in window, marked, with the mockup's
    navigation - the tab strip for the groups, and a DCX list of the chosen
    tab's pages where the page buttons are."""

    def test_it_is_built_before_the_first_page_is_shown(self):
        lines = statements(alias("dccore.sw.init"))
        self.assertLess(lines.index("dccore.sw.dcx"), lines.index("dccore.sw.tab $iif($hget(dccore.live,sw.open) == script,"
                                                           "$dccore.sw.group(Script),1)"))

    def test_nothing_happens_when_dcx_cannot_be_used(self):
        lines = statements(alias("dccore.sw.dcx"))
        self.assertEqual(lines[0], "if ($dccore.dcx.why != $null) { return }")
        self.assertEqual(lines[1], "dccore.dcx Mark dccore.set dccore.sw.dcxcb")
        self.assertEqual(lines[2], "hadd dccore.sws dcx 1")

    def test_the_buttons_are_hidden_for_it_and_the_tab_strip_stays(self):
        body = alias("dccore.sw.dcx")
        self.assertIn("did -h dccore.set $calc(1019 + %i)", body)
        self.assertNotIn("$calc(1000 + %g)", body)
        # dccore.sw.tab does not show the buttons again, and a jump to a
        # page selects its tab in either form
        self.assertIn("if (!$dccore.sw.s(dcx)) { did -v dccore.set $calc(1019 + %i) }", alias("dccore.sw.tab"))
        self.assertIn("did -f dccore.set $calc(1000 + %g)", alias("dccore.sw.goto"))
        self.assertNotIn("if (!$dccore.sw.s(dcx)) { did -f", alias("dccore.sw.goto"))

    def test_it_is_placed_by_the_generated_geometry_and_the_reported_scale(self):
        body = alias("dccore.sw.dcx")
        rect = alias("dccore.sw.dcxrect")
        self.assertIn("$dialog(dccore.set).cw / $gettok($dccore.sw.m(size),1,32)", rect)
        self.assertIn("$dialog(dccore.set).ch / $gettok($dccore.sw.m(size),2,32)", rect)
        self.assertIn("var %t = $dccore.sw.m(dcxpages), %id = $gettok(%t,1,32), %rect = "
                      "$dccore.sw.dcxrect($gettok(%t,2-5,32))", body)
        self.assertIn("-c %id listview %rect report noheader singlesel fullrow showsel", body)
        # the four numbers reach the identifier as ONE parameter (mIRC splits
        # an identifier's parameters on commas): read as $1 to $4 they made
        # every DCX control zero-sized, so they are taken apart from $1-
        self.assertIn("var %r = $1-,", rect)
        for n in (1, 2, 3, 4):
            self.assertIn("$gettok(%%r,%d,32)" % n, rect)
        self.assertNotRegex(rect, r"\$[1-4] \*")
        found = re.search(r"(?m)^  hadd dccore\.swm dcxpages (\d+) (\d+) (\d+) (\d+) (\d+)$", raw())
        size = re.search(r"(?m)^  hadd dccore\.swm size (\d+) (\d+)$", raw())
        self.assertTrue(found and size)
        list_id, x, y, w, h = (int(n) for n in found.groups())
        import build_settings_window as builder
        self.assertEqual((int(size.group(1)), int(size.group(2))), (builder.DIALOG_W, builder.DIALOG_H))
        self.assertLessEqual(x + w, builder.CONTENT_X - 2)
        # inside the tab control's frame, under the tabs themselves
        self.assertGreaterEqual(y, builder.TABS[1] + builder.TAB_HEADER_H)
        self.assertLessEqual(y + h, builder.TABS[1] + builder.TABS[3] - 2)
        self.assertGreater(x, builder.TABS[0])
        table = block("dialog dccore.set {", raw())
        self.assertNotIn(", %d, " % list_id, table)

    def test_the_chosen_tabs_pages_fill_it_from_the_window_data(self):
        self.assertIn("if ($dccore.sw.s(dcx)) { dccore.sw.dcxpages %pages }", alias("dccore.sw.tab"))
        lines = statements(alias("dccore.sw.dcxpages"))
        self.assertIn("dccore.dcx xdid dccore.set %id -r", lines)
        self.assertIn("dccore.dcx xdid dccore.set %id -a 0 0 + 0 0 0 0 0 0 $dccore.sw.untext($dccore.sw.m(p. $+ "
                      "$gettok($1-,%i,32)))", lines)
        # refilled quietly: emptying and filling the list is not a click
        self.assertLess(lines.index("hadd dccore.sws dcxquiet 1"), lines.index("dccore.dcx xdid dccore.set %id -r"))
        self.assertEqual(lines[-1], "hdel dccore.sws dcxquiet")

    def test_the_page_shown_is_selected_quietly(self):
        self.assertIn("if ($dccore.sw.s(dcx)) { dccore.sw.dcxshow $1 }", alias("dccore.sw.page"))
        lines = statements(alias("dccore.sw.dcxshow"))
        quiet = lines.index("hadd dccore.sws dcxquiet 1")
        select = [i for i, line in enumerate(lines) if " -c %at" in line][0]
        self.assertLess(quiet, select)
        self.assertLess(select, lines.index("hdel dccore.sws dcxquiet"))

    def test_a_click_on_a_page_drives_the_same_switch_as_its_button(self):
        lines = statements(alias("dccore.sw.dcxcb"))
        self.assertEqual(lines[0], "if ($dccore.sw.s(dcxquiet)) { return }")
        self.assertIn("if ($3 != $gettok($dccore.sw.m(dcxpages),1,32)) || (!$istok(sclick selected,$2,32)) || "
                      "($4 !isnum) { return }", lines)
        self.assertIn("var %p = $gettok($dccore.sw.m(g. $+ $dccore.sw.s(tab) $+ .pages),$4,32)", lines)
        self.assertIn("if (%p == $null) || (%p == $dccore.sw.s(page)) { return }", lines)
        # through dccore.sw.go, which asks first when something is not saved
        self.assertEqual(lines[-1], "dccore.sw.go page %p")


TABLES = (1520, 1540, 1580, 1595)


class TheMockupLayout(unittest.TestCase):
    """Phase 3: the pages as the mockup draws them - each section a titled
    frame, every label above its field, the page's name over the page - in
    one window that both DCX and the built-in controls draw."""

    @classmethod
    def setUpClass(cls):
        import build_settings_window as builder
        cls.builder = builder
        cls.controls = dialog("dccore.set")
        cls.where = {c[2]: c for c in cls.controls}
        cls.order = {c[2]: n for n, c in enumerate(cls.controls)}
        cls.data = generated_data()
        cls.pages = {}
        for page in range(1, int(cls.data["pages"]) + 1):
            ids = []
            for part in range(1, int(cls.data["p.%d.n" % page]) + 1):
                ids.extend(int(i) for i in cls.data["p.%d.%d" % (page, part)].split(",") if i)
            cls.pages[page] = ids

    def test_every_titled_section_is_a_frame_listed_before_what_it_holds(self):
        frames = [c for c in self.controls if c[0] == "box"]
        self.assertGreater(len(frames), 40)
        for _kind, title, fid, x, y, w, h, _style in frames:
            self.assertTrue(title, fid)
            held = [c for c in self.controls if c[2] != fid and c[0] != "box"
                    and x <= c[3] and c[3] + c[5] <= x + w and y <= c[4] and c[4] + c[6] <= y + h
                    and self.page_of(c[2]) == self.page_of(fid)]
            self.assertTrue(held, "the frame %d (%r) holds nothing" % (fid, title))
            for c in held:
                # mIRC draws a control over the ones listed before it: a frame
                # listed after its controls would cover them
                self.assertLess(self.order[fid], self.order[c[2]], "%d is listed before its frame %d" % (c[2], fid))

    def page_of(self, cid):
        return next((page for page, ids in self.pages.items() if cid in ids), None)

    def test_labels_sit_above_their_fields_except_on_the_compact_page(self):
        layouts = {page: self.data["p.%d.layout" % page] for page in self.pages}
        compact = [untext(self.data["p.%d" % page]) for page, form in layouts.items() if form.startswith("compact")]
        self.assertEqual(compact, ["File locations"])
        self.assertIn("roomy 2", layouts.values())
        above = beside = 0
        for name, value in self.data.items():
            if not name.startswith("k.") or name == "k.CHANNEL":
                continue
            cid, kind, _factor, label_id = value.split()
            if kind == "bool":
                continue
            label, control = self.where[int(label_id)], self.where[int(cid)]
            if layouts[self.page_of(int(cid))].startswith("roomy"):
                self.assertEqual(label[3], control[3], name)
                self.assertLessEqual(label[4] + label[6], control[4], name)
                if kind not in ("choice", "tri", "colour"):
                    self.assertGreaterEqual(control[5], label[5] - self.builder.BROWSE_W - 2, name)
                above += 1
            else:
                self.assertLess(label[3] + label[5], control[3], name)
                beside += 1
        self.assertGreater(above, 90)
        self.assertEqual(beside, 23)

    def test_a_check_that_fits_no_column_sends_the_page_to_the_next_form_not_an_error(self):
        # Fetch tuning is two roomy columns only because its re-fetch check
        # was shortened to fit one; the long one made the page compact
        page = next(p for p in self.pages if untext(self.data["p.%d" % p]) == "Fetch tuning")
        self.assertEqual(self.data["p.%d.layout" % page], "roomy 2")
        import settings_window_layout as layout
        fetch = dict(dict(layout.GROUPS)["Downloads"])["Fetch tuning"]
        labels = [item["label"] for _h, items in fetch for item in items if isinstance(item, dict) and "label" in item]
        self.assertIn("Re-fetch when a bot has a new list", labels)

    def test_the_title_sits_between_the_tabs_and_the_page(self):
        tid, x, y, w, h = (int(n) for n in self.data["dcxtitle"].split())
        self.assertEqual(tid, self.builder.DCX_TITLE_ID)
        self.assertGreaterEqual(y, self.builder.TABS[1] + self.builder.TAB_HEADER_H)
        self.assertLessEqual(y + h, self.builder.CONTENT_Y)
        self.assertGreaterEqual(x, self.builder.CONTENT_X)
        body = alias("dccore.sw.dcx")
        self.assertIn("-c $gettok(%t,1,32) text $dccore.sw.dcxrect($gettok(%t,2-5,32)) nowrap noprefix noformat", body)
        self.assertIn("-f +b default 9 Tahoma", body)
        show = alias("dccore.sw.showpage")
        self.assertIn("if ($2 == -v) { dccore.dcx xdid dccore.set $gettok($dccore.sw.m(dcxtitle),1,32) -t "
                      "$dccore.sw.untext($dccore.sw.m(p. $+ $1)) }", show)

    def test_the_first_tabs_left_edge_is_drawn_as_a_line_of_its_own(self):
        """Windows 10 draws an unselected tab with its top and right edges
        only, so the first tab has no left edge: a DCX panel a pixel wide,
        in the tab border's colour, runs down it into the frame's edge."""
        eid, x, y, h, colour = (int(n) for n in self.data["dcxtabedge"].split())
        self.assertEqual(eid, self.builder.DCX_TABEDGE_ID)
        self.assertTrue(self.builder.DCX_TITLE_ID < eid < 910)
        self.assertEqual((x, y, h), self.builder.DCX_TABEDGE)
        self.assertEqual(x, self.builder.TABS[0])
        self.assertLess(self.builder.TABS[1], y)
        self.assertLess(y, self.builder.TABS[1] + self.builder.TAB_HEADER_H)
        self.assertLess(self.builder.TABS[1] + self.builder.TAB_HEADER_H, y + h)
        red, green, blue = self.builder.DCX_TABEDGE_RGB
        self.assertEqual(colour, red + green * 256 + blue * 65536)
        body = alias("dccore.sw.dcx")
        self.assertIn("%t = $dccore.sw.m(dcxtabedge)", body)
        # the height scaled with the window, the width a pixel whatever the scale
        self.assertIn("var %e = $dccore.sw.dcxrect($gettok(%t,2,32) $gettok(%t,3,32) 1 $gettok(%t,4,32))", body)
        self.assertIn("-c $gettok(%t,1,32) panel $gettok(%e,1,32) $gettok(%e,2,32) 1 $gettok(%e,4,32)", body)
        self.assertIn("xdid dccore.set $gettok(%t,1,32) -C +b $gettok(%t,5,32)", body)
        # nothing of it in the native form: that keeps Windows' own look
        self.assertNotIn("dcxtabedge", block("dialog dccore.set {", raw()))

    def test_every_table_has_its_columns_and_a_listview_over_its_place(self):
        lists = [c for c in self.controls if c[0] == "list"]
        self.assertEqual(sorted(c[2] for c in lists), sorted(TABLES))
        self.assertEqual([int(i) for i in self.data["lvs"].split()], [c[2] for c in lists])
        dcx_ids = []
        for _kind, _label, cid, x, y, w, h, _style in lists:
            dcx_id, lx, ly, lw, lh = (int(n) for n in self.data["lv.%d" % cid].split())
            self.assertEqual((lx, ly, lw, lh), (x, y, w, h), cid)
            self.assertTrue(self.builder.DCX_TITLE_ID < dcx_id < 1001, dcx_id)
            dcx_ids.append(dcx_id)
            columns = [col.split(" ", 1) for col in self.data["lvc.%d" % cid].split(",")]
            self.assertTrue(all(title for _w, title in columns), cid)
            self.assertEqual([w for w, _t in columns].count("0"), 1)
            self.assertEqual(columns[-1][0], "0")
            self.assertIn(str(cid), self.data["p.%d.lv" % self.page_of(cid)].split())
        self.assertEqual(len(set(dcx_ids)), len(dcx_ids))
        self.assertEqual(self.data["lvx.1520"], "noheader")
        self.assertEqual(self.data["lvc.1540"], "120 Name,44 Primary,0 Folders")

    def test_a_listview_is_made_hidden_over_its_native_list_with_its_columns(self):
        lines = statements(alias("dccore.sw.lv.make"))
        self.assertEqual(lines[1], "did -h dccore.set $1")
        self.assertIn("dccore.dcx xdialog dccore.set -c %id listview %rect report singlesel fullrow showsel "
                      "$dccore.sw.m(lvx. $+ $1)", lines)
        self.assertIn("dccore.dcx xdid dccore.set %id -t %out", lines)
        self.assertEqual(lines[-1], "dccore.dcx xdid dccore.set %id -h")
        self.assertLess(lines.index("dccore.dcx xdid dccore.set %id -t %out"), len(lines) - 1)
        # the last column takes what the others leave
        self.assertIn("if (%w == 0) { %w = %left }", lines)

    def test_the_tables_are_only_touched_through_the_helpers(self):
        text = handwritten()
        ids = "|".join(str(i) for i in TABLES)
        self.assertNotRegex(text, r"did -[a-z]+ dccore\.set (?:%s)\b" % ids)
        self.assertNotRegex(text, r"\$did\(dccore\.set,(?:%s)[,)]" % ids)
        for name in ("clear", "add", "sel", "count", "text", "del"):
            body = alias("dccore.sw.lv." + name)
            self.assertIn("$dccore.sw.s(dcx)", body, name)
            self.assertIn("$dccore.sw.lv.id($1)", body, name)
        self.assertEqual(statements(alias("dccore.sw.lv.id")),
                         ["return $iif($dccore.sw.s(dcx),$gettok($dccore.sw.m(lv. $+ $1),1,32),$1)"])

    def test_a_row_and_a_selection_say_the_same_to_either(self):
        add = alias("dccore.sw.lv.add")
        self.assertIn("dccore.dcx xdid dccore.set $dccore.sw.lv.id($1) -a 0 0 + 0 0 0 0 0 0 %row", add)
        self.assertIn("else { did -a dccore.set $1 %row }", add)
        self.assertIn("var %cell = $dccore.sw.untok($gettok($2-,%i,32))", add)
        sel = alias("dccore.sw.lv.sel")
        self.assertIn("$dccore.dcx(_xdid,dccore.set $dccore.sw.lv.id($1) sel)", sel)
        self.assertIn("return $did(dccore.set,$1).sel", sel)
        self.assertIn("return $iif(%s isnum 1-,%s)", sel)

    def test_the_listviews_follow_their_page_and_the_enable_state(self):
        show = alias("dccore.sw.showpage")
        self.assertIn("var %lvs = $dccore.sw.m(p. $+ $1 $+ .lv), %i = 1", show)
        self.assertIn("did -h dccore.set $gettok(%lvs,%i,32)", show)
        self.assertIn("dccore.dcx xdid dccore.set $dccore.sw.lv.id($gettok(%lvs,%i,32)) $iif($2 == -v,-s,-h)", show)
        self.assertIn("dccore.dcx xdid dccore.set $dccore.sw.lv.id($gettok(%lvs,%i,32)) $iif($1,-e,-b)",
                      alias("dccore.sw.enable"))
        # made after every page was hidden, before the first is shown
        init = statements(alias("dccore.sw.init"))
        self.assertLess(init.index("dccore.sw.enable 0"), init.index("dccore.sw.dcx"))

    def test_the_lists_tables_selection_is_read_when_a_button_needs_it(self):
        # no pick on a click: Edit and Delete read the selected row then
        self.assertEqual(statements(alias("dccore.sw.srv.selected")),
                         ["return $dccore.sw.s(srv.rowl. $+ $dccore.sw.lv.sel(1540))"])
        self.assertNotIn("pick", alias("dccore.sw.dcxcb"))
        click = alias("dccore.sw.click")
        self.assertIn("if (%id == 1554) { if ($dccore.sw.srv.selected != $null) { dccore.sw.srv.open $v1 }", click)
        self.assertIn("var %n = $dccore.sw.srv.selected", alias("dccore.sw.srv.delete"))

    def test_lists_and_bindings_are_the_mockups_two_pages(self):
        import settings_window_layout as layout
        sharing = dict(dict(layout.GROUPS)["Sharing"])
        widgets_of = lambda page: [(h, [i["widget"] for i in items if isinstance(i, dict) and "widget" in i])
                                   for h, items in page]
        self.assertEqual(widgets_of(sharing["Lists"]), [("", []), ("Lists", ["srvlists"])])
        self.assertEqual(widgets_of(sharing["Channel bindings"]),
                         [("", []), ("Channel bindings", ["srvchans"]), ("", ["srvsaveb"])])
        # the lists table: a row per list, Edit and Add list opening the list window
        draw = statements(alias("dccore.sw.srv.draw"))
        self.assertEqual(draw[0], "dccore.sw.lv.clear 1540")
        self.assertIn("hadd dccore.sws srv.rowl. $+ %i %id", draw)
        self.assertEqual(draw[-1], "dccore.sw.srv.drawrows")
        buttons = {c[2]: c[1] for c in self.controls if c[0] == "button"}
        self.assertEqual([buttons[i] for i in (1551, 1554, 1555, 1557)], ["Add list", "Edit", "Delete", "Refresh"])
        click = alias("dccore.sw.click")
        self.assertIn("if (%id == 1551) { dccore.sw.srv.open new | return }", click)
        self.assertIn("if (%id == 1555) { dccore.sw.srv.delete | return }", click)
        self.assertIn("if (%id == 1557) || (%id == 1567) { dccore.sw.reask served | return }", click)
        # no Save button of its own: Apply saves the lists with everything else
        self.assertFalse([c for c in self.controls if c[0] == "button" and c[1].startswith("Save")])
        self.assertNotIn("dccore.sw.srv.save", click)
        for name in ("pickl", "pickc", "sellist", "newfolder", "changel", "changec", "removel", "removec", "addlist"):
            self.assertNotIn("alias dccore.sw.srv.%s " % name, code(), name)
        # the list window: its own dialog, its folders typed there
        table = block("dialog dccore.setl {")
        for cid, text in ((2, "edit"), (3, "check"), (5, "list"), (7, "edit"), (9, "edit"), (15, "OK"), (16, "Cancel")):
            self.assertRegex(table, r"(?m)^  \w+ (?:\"[^\"]*\", )?%d, " % cid, text)
        self.assertIn("on *:dialog:dccore.setl:sclick:*: { dccore.sw.srv.lclick $did }", code())
        lok = alias("dccore.sw.srv.lok")
        self.assertIn("hadd dccore.sws srv.l. $+ %id $did(dccore.setl,3).state $dccore.sw.enc($did(dccore.setl,2).text)", lok)
        self.assertIn("dccore.sw.srv.addfolder %id $hget(dccore.swl,f. $+ $gettok(%w,%i,32))", lok)
        self.assertEqual(statements(lok)[-1], "dccore.sw.srv.edited")
        # and nothing reaches the bot but through Apply
        self.assertNotIn("dccore.send", alias("dccore.sw.srv.lok"))
        self.assertNotIn("dccore.send", alias("dccore.sw.srv.rowclick"))

    def test_the_bindings_rows_are_the_mockups(self):
        import build_settings_window as builder
        page = next(p for p in self.pages if untext(self.data["p.%d" % p]) == "Channel bindings")
        ids = set(self.pages[page])
        for r in range(builder.BIND_ROWS):
            base = builder.BIND_FIRST_ID + 4 * r
            kinds = [self.where[base + k][0] for k in range(4)]
            self.assertEqual(kinds, ["text", "combo", "combo", "button"], r)
            self.assertEqual(self.where[base + 3][1], "Remove")
            self.assertTrue({base, base + 1, base + 2, base + 3} <= ids, r)
            if r:
                self.assertEqual(self.where[base][4] - self.where[base - 4][4], builder.BIND_PITCH)
        add = builder.BIND_ADD_ID
        self.assertEqual([self.where[add + k][0] for k in range(4)], ["edit", "combo", "combo", "button"])
        self.assertEqual(self.where[add + 3][1], "Add")
        self.assertEqual(self.where[1567][1], "Refresh")
        self.assertNotIn(1566, self.where)
        self.assertEqual(statements(alias("dccore.sw.srv.rowbase")), ["return $calc(1700 + 4 * ($1 - 1))"])
        self.assertEqual(builder.BIND_FIRST_ID, 1700)
        # a scrollbar beside the rows, not pages: every channel is reached by
        # scrolling, and the rows are filled from its position
        scroll = self.where[builder.BIND_SCROLL_ID]
        self.assertEqual(scroll[0], "scroll")
        self.assertIn(builder.BIND_SCROLL_ID, ids)
        self.assertGreater(scroll[3], self.where[builder.BIND_FIRST_ID + 3][3] + self.where[builder.BIND_FIRST_ID + 3][5])
        self.assertIn("vertical", scroll[7])
        self.assertIn("on *:dialog:dccore.set:scroll:*: { dccore.sw.scrolled $did }", code())
        self.assertEqual(statements(alias("dccore.sw.scrolled")),
                         ["if ($1 != 1774) { return }", "hadd dccore.sws bind.off $did(dccore.set,1774).sel",
                          "dccore.sw.srv.drawrows"])
        drawrows = alias("dccore.sw.srv.drawrows")
        self.assertIn("did -z dccore.set 1774 range 0 $iif(%n > 15,$calc(%n - 15),0)", drawrows)
        self.assertIn("did -c dccore.set 1774 %off", drawrows)
        self.assertIn("if (%off > $calc(%n - 15)) { %off = $calc(%n - 15) }", drawrows)
        self.assertNotIn("Previous", code())
        self.assertNotIn("pagenext", code())
        # a click in a row: its menus change the binding at once, its Remove takes it
        click = alias("dccore.sw.click")
        self.assertIn("if (%id isnum 1700-1759) { dccore.sw.srv.rowclick %id | return }", click)
        self.assertIn("if (%id == 1773) { dccore.sw.srv.newchan | return }", click)
        rowclick = alias("dccore.sw.srv.rowclick")
        self.assertIn("if (%what == 3) {", rowclick)
        self.assertIn("hadd dccore.sws srv.c. $+ %n $dccore.sw.srv.chosen($calc(%base + 1)) $gettok(%row,2,32) "
                      "$dccore.sw.srv.mode($calc(%base + 2))", rowclick)
        self.assertIn("hadd dccore.sws srv.dirty 1", rowclick)
        # the spare rows are hidden, and hidden again each time the page is shown
        rowsvis = statements(alias("dccore.sw.srv.rowsvis"))
        self.assertIn("did $iif($calc(%off + %r) <= %n,-v,-h) dccore.set", "\n".join(rowsvis))
        # and no row is shown while another page is up: the lists arrive from
        # the bot after they were asked for, and were drawn over whatever
        # page was shown by then
        self.assertEqual(rowsvis[0], "if (!$istok($dccore.sw.m(p. $+ $dccore.sw.s(page) $+ .w),srvchans,32)) { return }")
        shows = [line for line in handwritten().split("\n") if "did -v dccore.set" in line or "-v,-h" in line]
        for line in shows:
            self.assertTrue("dccore.sw.showpage" in line or "$calc(1019 + %i)" in line
                            or "-v,-h" in line, line)
        self.assertIn("if ($istok($dccore.sw.m(p. $+ $1 $+ .w),srvchans,32)) { dccore.sw.srv.rowsvis }",
                      alias("dccore.sw.page"))
        self.assertIn("srvchans", self.data["p.%d.w" % page].split())
        # the row that adds one offers the first list and mode, never line 0
        draw = alias("dccore.sw.srv.drawrows")
        self.assertIn("dccore.sw.pickline 1771 $iif($dccore.sw.s(srv.order) != $null,1,0)", draw)
        self.assertIn("if (!$did(dccore.set,1772).sel) { did -c dccore.set 1772 1 }", draw)
        # every Mode menu gets the modes once, at init
        combos = alias("dccore.sw.combos")
        self.assertIn("while (%r <= 15) { dccore.sw.fillfrom $calc($dccore.sw.srv.rowbase(%r) + 2)", combos)
        self.assertIn("dccore.sw.fillfrom 1772", combos)

    def test_the_names_and_paths_shown_are_the_stored_ones_made_tokens(self):
        draw = alias("dccore.sw.srv.draw")
        self.assertIn("$dccore.sw.retok($gettok(%l,2-,32))", draw)
        self.assertIn("$dccore.sw.tok(%folders)", draw)
        self.assertEqual(statements(alias("dccore.sw.retok")), ["return $replace($1-,$chr(32),$chr(37) $+ 20)"])


class ApplySavesEverything(unittest.TestCase):
    """The operator: a Save button beside Apply is easy to miss, and easy to
    take for the one that saves. So the pages have none, Apply sends every
    kind of change, and leaving a page with changes not saved asks first."""

    def test_the_pages_have_no_save_buttons(self):
        controls = dialog("dccore.set")
        self.assertEqual([c[1] for c in controls if c[0] == "button" and c[1].lower().startswith("save")], [])
        click = alias("dccore.sw.click")
        self.assertNotIn("dccore.sw.srv.save", click)
        self.assertNotIn("dccore.sw.oc.save", click)

    def test_apply_sends_the_lists_and_the_on_connect_commands_too(self):
        lines = statements(alias("dccore.sw.apply"))
        oc = lines.index("if ($dccore.sw.ocdirty) && (!$dccore.sw.s(saving.oc)) { dccore.sw.oc.save | inc %sent }")
        srv = lines.index("if ($dccore.sw.s(srv.dirty)) && (!$dccore.sw.s(saving.srv)) { dccore.sw.srv.save | inc %sent }")
        keys = lines.index("var %keys = $iif($dccore.sw.s(loaded),$dccore.sw.changed)")
        self.assertLess(oc, keys)
        self.assertLess(srv, keys)
        # with nothing else to send, OK waits for those replies instead of closing at once
        self.assertIn("if (%sent) { dccore.sw.maybeclose | return }", lines)
        self.assertLess(lines.index("if (%sent) { dccore.sw.maybeclose | return }"),
                        lines.index("if ($1 == close) { dialog -x dccore.set | return }"))
        self.assertNotIn("has changes of its own not saved yet", alias("dccore.sw.apply"))

    def test_every_reply_closes_or_keeps_the_window(self):
        body = alias("dccore.sw.line")
        srv = body[body.index("if (%t == SRVDONE) || (%t == FLDDONE) {"):body.index("if (%t == FLDBEGIN)")]
        self.assertIn("if ($2 == ok) { hdel dccore.sws srv.dirty | dccore.sw.status $4- | dccore.sw.reask served | "
                      "dccore.sw.maybeclose | return }", srv)
        self.assertIn("if ($2 == unchanged) { hdel dccore.sws srv.dirty | dccore.sw.status $3- | dccore.sw.maybeclose | return }", srv)
        self.assertIn("if ($2 == error) { hdel dccore.sws close | dccore.sw.status Not saved:", srv)
        oc = body[body.index("if (%t == OCDONE) {"):body.index("if (%t == OCRESEND) {")]
        self.assertIn("if ($2 == ok) { dccore.sw.status $4- | dccore.sw.reask onconnect | dccore.sw.maybeclose | return }", oc)
        self.assertIn("if ($2 == unchanged) { dccore.sw.status $3- | dccore.sw.maybeclose | return }", oc)
        self.assertIn("if ($2 == error) { hdel dccore.sws close | dccore.sw.status Not saved:", oc)
        self.assertIn("hdel dccore.sws close", alias("dccore.sw.riskyask"))

    def test_leaving_a_page_with_changes_asks_first(self):
        click = alias("dccore.sw.click")
        self.assertIn("if (%id isnum 1001-1007) { dccore.sw.go tab $calc(%id - 1000) | return }", click)
        self.assertIn("if (%p) { dccore.sw.go page %p }", click)
        self.assertEqual(statements(alias("dccore.sw.dcxcb"))[-1], "dccore.sw.go page %p")
        go = statements(alias("dccore.sw.go"))
        self.assertIn("if (!$dccore.sw.anydirty) { dccore.sw.goon $1 $2 | return }", go)
        self.assertEqual(go[-3:], ["hadd dccore.sws going $1 $2", "dccore.sw.stay",
                                   ".timerdccoreSwAsk -m 1 0 dccore.sw.leaveask"])
        ask = alias("dccore.sw.leaveask")
        self.assertIn("if ($input(You have changes that are not saved. Save them before leaving this page? "
                      "Yes saves them and goes on; No stays here.,yq,DCCore - Settings)) "
                      "{ dccore.sw.apply apply | dccore.sw.goon %go }", ask)
        # the page the bot refused a setting on is shown without asking
        self.assertIn("dccore.sw.tab %g", alias("dccore.sw.goto"))
        self.assertNotIn("dccore.sw.go ", alias("dccore.sw.goto"))

    def test_what_counts_as_not_saved(self):
        lines = statements(alias("dccore.sw.anydirty"))
        self.assertEqual(lines, ["if ($dccore.sw.s(srv.dirty)) || ($dccore.sw.ocdirty) { return $true }",
                                 "if ($dccore.sw.s(loaded)) && ($dccore.sw.changed != $null) { return $true }",
                                 "if ($dccore.sw.localdirty) { return $true }",
                                 "return $dccore.sw.opt.dirty"])
        local = alias("dccore.sw.localdirty")
        self.assertIn("if ($did(dccore.set,$dccore.sw.m(lo. $+ %o)).state != $iif($dccore.opt(%o),1,0)) { return $true }",
                      local)
        # staying puts the tab and the page shown back, in either form
        stay = statements(alias("dccore.sw.stay"))
        self.assertEqual(stay[0], "did -f dccore.set $calc(1000 + $dccore.sw.s(tab))")
        self.assertIn("if ($dccore.sw.s(dcx)) { dccore.sw.dcxshow $dccore.sw.s(page) | return }", stay)
        self.assertIn("if (%at) { did -c dccore.set $calc(1019 + %at) }", stay)


if __name__ == "__main__":
    unittest.main()
