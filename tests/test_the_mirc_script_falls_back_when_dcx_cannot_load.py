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


class ThePageList(unittest.TestCase):
    """Phase 2 and 3: the built-in window, marked, with the mockup's
    navigation - the tab strip for the groups, and a DCX list of the chosen
    tab's pages where the page buttons are."""

    def test_it_is_built_before_the_first_page_is_shown(self):
        lines = statements(alias("dccore.sw.init"))
        self.assertLess(lines.index("dccore.sw.dcx"), lines.index("dccore.sw.tab 1"))

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
        self.assertGreaterEqual(y, builder.TABS[1] + builder.TABS[3])
        self.assertLessEqual(y + h, builder.CONNECTED[1] - 2)
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
        self.assertEqual(lines[-1], "dccore.sw.page %p")


TABLES = (1520, 1540, 1560, 1580, 1595)


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
        self.assertEqual(self.data["p.13.layout"], "roomy 2")
        import settings_window_layout as layout
        fetch = dict(dict(layout.GROUPS)["Downloads"])["Fetch tuning"]
        labels = [item["label"] for _h, items in fetch for item in items if isinstance(item, dict) and "label" in item]
        self.assertIn("Re-fetch a list when its bot has a new one", labels)

    def test_the_title_sits_between_the_tabs_and_the_page(self):
        tid, x, y, w, h = (int(n) for n in self.data["dcxtitle"].split())
        self.assertEqual(tid, self.builder.DCX_TITLE_ID)
        self.assertGreaterEqual(y, self.builder.TABS[1] + self.builder.TABS[3])
        self.assertLessEqual(y + h, self.builder.CONTENT_Y)
        self.assertGreaterEqual(x, self.builder.CONTENT_X)
        body = alias("dccore.sw.dcx")
        self.assertIn("-c $gettok(%t,1,32) text $dccore.sw.dcxrect($gettok(%t,2-5,32)) nowrap noprefix noformat", body)
        self.assertIn("-f +b default 9 Tahoma", body)
        show = alias("dccore.sw.showpage")
        self.assertIn("if ($2 == -v) { dccore.dcx xdid dccore.set $gettok($dccore.sw.m(dcxtitle),1,32) -t "
                      "$dccore.sw.untext($dccore.sw.m(p. $+ $1)) }", show)

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
        self.assertEqual(self.data["lvc.1540"], "150 Name,44 Primary,0 Folder path")
        self.assertEqual(self.data["lvc.1560"], "110 Channel,110 List,0 Mode")

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

    def test_a_row_picked_in_a_listview_drives_the_same_pick_as_the_native_list(self):
        cb = statements(alias("dccore.sw.dcxcb"))
        self.assertEqual(cb[1], "if ($istok(sclick selected,$2,32)) && ($3 == $dccore.sw.lv.id(1540)) { dccore.sw.srv.pickl | return }")
        self.assertEqual(cb[2], "if ($istok(sclick selected,$2,32)) && ($3 == $dccore.sw.lv.id(1560)) { dccore.sw.srv.pickc | return }")
        click = alias("dccore.sw.click")
        self.assertIn("if (%id == 1540) { dccore.sw.srv.pickl | return }", click)
        self.assertIn("if (%id == 1560) { dccore.sw.srv.pickc | return }", click)

    def test_lists_and_channels_are_two_tables_with_their_own_buttons(self):
        import settings_window_layout as layout
        page = dict(dict(layout.GROUPS)["Sharing"])["Lists & channels"]
        self.assertEqual([(h, [i["widget"] for i in items if isinstance(i, dict) and "widget" in i]) for h, items in page],
                         [("", []), ("Lists", ["srvlists"]), ("Channel bindings", ["srvchans"]), ("", ["srvsave"])])
        draw = alias("dccore.sw.srv.draw")
        self.assertIn("dccore.sw.lv.clear 1540", draw)
        self.assertIn("dccore.sw.lv.clear 1560", draw)
        self.assertIn("hadd dccore.sws srv.rowl. $+ %line L %id", draw)
        self.assertIn("hadd dccore.sws srv.rowc. $+ %i $gettok(%c,%i,32)", draw)
        click = alias("dccore.sw.click")
        for cid, action in ((1554, "changel"), (1555, "removel"), (1562, "changec"), (1563, "removec"),
                            (1551, "addlist"), (1553, "newfolder"), (1552, "newchan")):
            self.assertIn("if (%%id == %d) { dccore.sw.srv.%s | return }" % (cid, action), click)
        # a folder goes to the list selected in the lists table, not a menu
        self.assertIn("dccore.sw.srv.addfolder $dccore.sw.srv.sellist", alias("dccore.sw.srv.newfolder"))
        self.assertNotIn("dccore.sw.srv.change {", code())
        self.assertNotIn("dccore.sw.srv.remove {", code())

    def test_the_names_and_paths_shown_are_the_stored_ones_made_tokens(self):
        draw = alias("dccore.sw.srv.draw")
        self.assertIn("$dccore.sw.retok($gettok(%l,2-,32))", draw)
        self.assertIn("$dccore.sw.retok($gettok(%row,3-,32))", draw)
        self.assertEqual(statements(alias("dccore.sw.retok")), ["return $replace($1-,$chr(32),$chr(37) $+ 20)"])


if __name__ == "__main__":
    unittest.main()
