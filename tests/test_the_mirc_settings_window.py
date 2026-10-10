"""#1264: dccore.mrc's settings window - the bot's settings, edited from mIRC.

`/dccore settings` opens `dialog dccore.set`: six tabs, the pages of the
chosen tab in a list, Apply / OK / Cancel. It speaks the console protocol
docs/ADMIN-CONSOLE.md defines under "Settings over the console".

mIRC cannot run here, so the script is read as source. Comment lines are
dropped first (a ";" starts a comment only at the start of a line): every
statement asserted on has to be code, not a sentence about it. The dialog
table and its lookup data are generated (tests/test_the_settings_window_
generator.py); this file checks the table's geometry and the hand-written
mSL around it.

Mutation checks (each run by hand against this file, see the PR): removing
a reply handler, breaking the value decoder, dropping a snapshot's count
check, and sending a `set` outside setbegin ... setcommit each fail a test
here.
"""

import io
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "mirc", "dccore.mrc")
for path in (os.path.join(REPO_ROOT, "src"), os.path.join(REPO_ROOT, "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

import adminchat  # noqa: E402
import console_settings  # noqa: E402
from test_the_options_dialog_labels_fit_their_controls import CHAR_PX, CHECK_BOX_PX, PX_PER_DBU  # noqa: E402

BEGIN = "; ==== BEGIN GENERATED settings window"
END = "; ==== END GENERATED settings window"


def raw():
    with io.open(SCRIPT, encoding="ascii", newline="") as handle:
        return handle.read().replace("\r\n", "\n")


def code(text=None):
    """The script (or `text`) with its comment lines removed."""
    text = raw() if text is None else text
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith(";"))


def block(header, source=None):
    """The block that starts with `header` (ending in "{"), braces matched."""
    source = code() if source is None else source
    start = source.index(header)
    depth = 0
    for j in range(source.index("{", start), len(source)):
        depth += source[j] == "{"
        depth -= source[j] == "}"
        if depth == 0:
            return source[start:j + 1]
    raise AssertionError(header)


def alias(name):
    return block("alias %s {" % name)


def statements(body):
    """The statements of a block, without its header and closing brace - a
    one-line block's single statement too."""
    if "\n" not in body.strip():
        return [body[body.index("{") + 1:body.rindex("}")].strip()]
    return [line.strip() for line in body.split("\n")[1:-1] if line.strip()]


def handwritten():
    """The settings window's own mSL: its section, without the generated block."""
    text = raw()
    start = text.index(";  Settings window (#1264)")
    end = text.index(BEGIN)
    return code(text[start:end])


def generated_data():
    """{item: value} of the generated `alias dccore.sw.data`."""
    data = {}
    for line in alias("dccore.sw.data").split("\n"):
        line = line.strip()
        if line.startswith("hadd dccore.swm "):
            parts = line.split(" ", 3)
            data[parts[2]] = parts[3] if len(parts) > 3 else ""
    return data


def untext(text):
    return re.sub(r"~([0-9A-F][0-9A-F])", lambda m: chr(int(m.group(1), 16)), text)


CONTROL = re.compile(
    r'^\s+(check|text|button|box|edit|combo|list|tab) (?:"([^"]*)", )?(\d+)(?:, (\d+) (\d+) (\d+) (\d+))?(?:, (.*))?$',
    re.M)


def dialog(name):
    """(kind, label, id, x, y, w, h, style) for every control of a dialog."""
    table = block("dialog %s {" % name)
    return [(k, (label or "").replace("&&", "&"), int(i), int(x or 0), int(y or 0), int(w or 0), int(h or 0),
             style or "")
            for k, label, i, x, y, w, h, style in CONTROL.findall(table)]


def ids_of(value):
    return [int(i) for i in value.split(",") if i]


def page_ids(data):
    pages = {}
    for page in range(1, int(data["pages"]) + 1):
        ids = []
        for part in range(1, int(data["p.%d.n" % page]) + 1):
            ids.extend(ids_of(data["p.%d.%d" % (page, part)]))
        pages[page] = ids
    return pages


GLOBAL_IDS = set(range(1001, 1007)) | {1010, 1012, 1013, 1014, 1015, 1016, 1017, 1018}


# ---------------------------------------------------------------------------
# The value encoding, in Python, from the script's own patterns
# ---------------------------------------------------------------------------

REGSUBEX = re.compile(r"\$regsubex\((?:\$1|%v),/(.+?)/([gi]*),(.+)\)$")


def regsubex_calls(alias_name):
    """[(pattern, flags, replacement)] of each $regsubex line of an alias, in order."""
    found = []
    for line in statements(alias(alias_name)):
        match = REGSUBEX.search(line)
        if match:
            found.append(match.groups())
    return found


def python_sub(pattern, flags, replacement, text):
    """One mIRC $regsubex, run by Python's re. The replacements the script
    uses are recognised by their text, so a changed one fails loudly."""
    if replacement == r"$chr($base(\1,16,10))":
        repl = lambda m: chr(int(m.group(1), 16))  # noqa: E731
    elif replacement == "$chr(37) $+ 25":
        repl = lambda m: "%25"  # noqa: E731
    elif replacement == "$chr(37) $+ 20":
        repl = lambda m: "%20"  # noqa: E731
    elif replacement == r"$chr(37) $+ $base($asc(\1),10,16,2)":
        repl = lambda m: "%%%02X" % ord(m.group(1))  # noqa: E731
    else:
        raise AssertionError("a replacement this test does not know: %s" % replacement)
    return re.sub(pattern, repl, text, count=0 if "g" in flags else 1,
                  flags=re.I if "i" in flags else 0)


def mirc_dec(text):
    (pattern, flags, replacement), = regsubex_calls("dccore.sw.dec")
    return python_sub(pattern, flags, replacement, text)


def mirc_enc(text):
    calls = regsubex_calls("dccore.sw.enc")
    assert len(calls) == 3, calls
    for pattern, flags, replacement in calls:
        text = python_sub(pattern, flags, replacement, text)
    return text


def mirc_tok(text):
    body = statements(alias("dccore.sw.tok"))
    assert body == ["if ($1 == $null) { return - }",
                    "if ($1 == -) { return $chr(37) $+ 2D }",
                    "return $replace($dccore.sw.enc($1),$chr(32),$chr(37) $+ 20)"], body
    if text == "":
        return "-"
    if text == "-":
        return "%2D"
    return mirc_enc(text).replace(" ", "%20")


BATTERY = ["", "plain", "Some Album", "a  b", "a   b", " lead", "trail ", "  ", " ", "%admin%", "%nick%",
           "100%", "50% off", "%41", "%20", "%2520", "%25", "%2d", "%2D", "x%2Dy", "%7f", "%1F", "%0a",
           "%", "%%20", "-", "--", "#music", "\\x0304,01", "tab\there", "bell\x07", "a\x7fb",
           "\x01start", "end\x1f", "mixed %20 and  spaces ", "PRIVMSG X :LOGIN alfa secret",
           ".mp3, .flac, .m4a", "./data/x.json", "C:\\Music\\A b", "%%%", "%2", "%g0"]


class TheValueEncodingMatchesTheBots(unittest.TestCase):

    def test_the_decoder_is_the_documented_one(self):
        self.assertEqual(statements(alias("dccore.sw.dec")),
                         [r"return $regsubex($1,/%(0[0-9a-f]|1[0-9a-f]|2[05d]|7f)/gi,$chr($base(\1,16,10)))"])

    def test_the_decoder_decodes_what_the_bot_encodes(self):
        for value in BATTERY:
            with self.subTest(value=value):
                encoded = console_settings.encode_value(value)
                self.assertEqual(mirc_dec(encoded), console_settings.decode_value(encoded))
                self.assertEqual(mirc_dec(encoded), value)

    def test_the_decoder_agrees_with_the_bot_on_text_it_did_not_encode(self):
        for value in BATTERY:
            with self.subTest(value=value):
                self.assertEqual(mirc_dec(value), console_settings.decode_value(value))

    def test_the_encoder_writes_what_the_bot_writes(self):
        for value in BATTERY:
            with self.subTest(value=value):
                self.assertEqual(mirc_enc(value), console_settings.encode_value(value))
                self.assertEqual(console_settings.decode_value(mirc_enc(value)), value)

    def test_a_token_is_what_the_bot_makes_one(self):
        for value in BATTERY:
            with self.subTest(value=value):
                self.assertEqual(mirc_tok(value), console_settings.encode_token(value))
                self.assertNotIn(" ", mirc_tok(value))

    def test_a_broken_decoder_would_be_caught(self):
        """Mutation check of the emulation: the same battery tells a pattern
        missing the %20 escape from the real one."""
        broken = r"%(0[0-9a-f]|1[0-9a-f]|2[5d]|7f)"
        replacement = r"$chr($base(\1,16,10))"
        self.assertTrue(any(python_sub(broken, "gi", replacement, console_settings.encode_value(v)) != v
                            for v in BATTERY))


# ---------------------------------------------------------------------------
# The protocol: every reply has a handler, every command is real
# ---------------------------------------------------------------------------

def documented_reply_types():
    """The DCCORE line types the doc's "Settings over the console" table defines."""
    with io.open(os.path.join(REPO_ROOT, "docs", "ADMIN-CONSOLE.md"), encoding="utf-8") as handle:
        doc = handle.read()
    section = doc[doc.index("## Settings over the console"):doc.index("## The window, in mIRC")]
    # OUT is the console's ordinary reply (a stalled snapshot ends with one):
    # dccore.out shows it, and dccore.sw.old reads the one that matters here.
    return sorted(set(re.findall(r"`DCCORE ([A-Z]+)", section)) - {"OUT"})


class EveryReplyHasAHandler(unittest.TestCase):

    def test_the_doc_lists_the_ones_this_test_expects(self):
        types = documented_reply_types()
        for kind in ("CAPS", "SETBEGIN", "SETF", "SETEND", "SETOPEN", "SETERR", "SETDONE", "PVBEGIN", "PVLINE",
                     "PVEND", "SRVBEGIN", "SRVLIST", "SRVCHAN", "SRVFOLDER", "SRVEND", "SRVOPEN", "SRVERR",
                     "SRVDONE", "FLDBEGIN", "FLDROW", "FLDEND", "FLDOPEN", "FLDERR", "FLDDONE", "OCBEGIN",
                     "OCLINE", "OCEND", "OCOPEN", "OCERR", "OCDONE", "OCRESEND", "BANBEGIN", "BANP", "BANT",
                     "BANEND"):
            self.assertIn(kind, types)

    def test_every_documented_reply_is_routed_to_the_window(self):
        routed = statements(alias("dccore.sw.types"))[0].split(" ", 1)[1].split()
        self.assertEqual(sorted(routed), documented_reply_types())

    def test_every_documented_reply_has_a_branch(self):
        body = alias("dccore.sw.line")
        for kind in documented_reply_types():
            with self.subTest(kind=kind):
                self.assertRegex(body, r"\(%%t == %s\)" % kind)

    def test_setdone_handles_every_status(self):
        body = alias("dccore.sw.done")
        for status in ("ok", "error", "confirm", "aborted"):
            self.assertIn("if ($1 == %s)" % status, body)

    def test_the_structured_feed_routes_them_while_the_window_is_open(self):
        body = alias("dccore.structured")
        self.assertIn("if ($istok($dccore.sw.types,%type,32)) && (($dialog(dccore.set)) || ($dccore.st(sw.tail))) "
                      "{ dccore.sw.line $1- | return }", body)
        # and before the fallback that shows an unknown type as a line
        self.assertLess(body.index("dccore.sw.line $1-"), body.index("dccore.echo $dccore.tag(%type,info) $2-"))

    def test_an_older_bot_is_told_apart(self):
        body = alias("dccore.structured")
        self.assertIn("if ($dccore.sw.old($2-)) { return }", body)
        self.assertIn("if (Unknown command: consolecaps* !iswm $1-) { return $false }", alias("dccore.sw.old"))
        self.assertIn("This bot is too old for the settings window - update it.", alias("dccore.sw.old"))
        self.assertIn("dccore.send consolecaps", alias("dccore.sw.start"))


def sent_commands():
    """Every console line the window sends: (command, rest) of each
    `dccore.send ...` in its section, and each snapshot it asks for."""
    sent = []
    for line in handwritten().split("\n"):
        for match in re.finditer(r"dccore\.send (\S+)(?: (\S+))?", line):
            sent.append(match.groups())
        for match in re.finditer(r"dccore\.sw\.(?:re)?ask (\w+)", line):
            sent.append((match.group(1), None))
    for item, value in generated_data().items():
        if item.endswith(".ask"):
            sent.extend((word, None) for word in value.split())
    return sent


SUBCOMMANDS = {
    "served": ("begin", "list", "chan", "folder", "commit", "abort"),
    "folders": ("begin", "row", "commit", "abort"),
    "onconnect": ("begin", "delay", "line", "commit", "abort", "resend"),
}


class EveryCommandIsReal(unittest.TestCase):

    def test_the_subcommands_are_the_ones_the_bot_accepts(self):
        source = io.open(os.path.join(REPO_ROOT, "src", "console_settings.py"), encoding="utf-8").read()
        for command, subs in SUBCOMMANDS.items():
            body = source[source.index("def cmd_%s(" % command):]
            body = body[:body.index("\ndef ", 1)]
            accepted = set(re.findall(r'sub == "(\w+)"', body)) | set(
                word for tup in re.findall(r"sub not in \(([^)]*)\)", body) for word in re.findall(r'"(\w+)"', tup))
            self.assertEqual(set(subs) - accepted, set(), command)

    def test_every_command_the_window_sends_is_a_console_command(self):
        sent = sent_commands()
        self.assertGreater(len(sent), 30)
        for command, rest in sent:
            if command.startswith("$") or command.startswith("%"):
                continue
            with self.subTest(command=command, rest=rest):
                self.assertIn(command, adminchat.COMMANDS)
                if command in SUBCOMMANDS and rest is not None and not rest.startswith(("$", "%")):
                    self.assertIn(rest, SUBCOMMANDS[command])

    def test_the_window_sends_every_part_of_the_protocol(self):
        sent = {command for command, _rest in sent_commands()}
        for command in ("consolecaps", "settings", "setbegin", "set", "setcommit", "setabort", "setpreview",
                        "served", "folders", "onconnect", "banlist", "ban", "unban", "ignore", "unignore"):
            self.assertIn(command, sent)

    def test_the_settings_commands_are_still_kept_out_of_the_menu_plumbing_list(self):
        """The window is how they are clicked (test_the_mirc_menu_has_every_command
        lists them as plumbing for that reason)."""
        self.assertIn("dccore.settings", alias("dccore"))


class ApplyIsOneTransaction(unittest.TestCase):

    def test_apply_sends_setbegin_then_each_set_then_setcommit(self):
        body = statements(alias("dccore.sw.apply"))
        begin = body.index("dccore.send setbegin")
        sets = [n for n, line in enumerate(body) if line.startswith("dccore.send set $gettok(%keys,%i,32) ")]
        commit = body.index("dccore.send setcommit")
        self.assertEqual(len(sets), 1)
        self.assertLess(begin, sets[0])
        self.assertLess(sets[0], commit)
        self.assertIn("$dccore.sw.enc($dccore.sw.value($gettok(%keys,%i,32)))", body[sets[0]])
        # the loop around the set is between the two
        self.assertTrue(any(line.startswith("while (%i <= $numtok(%keys,32))") for line in body[begin:commit]))

    def test_no_set_is_ever_sent_outside_a_transaction(self):
        """A lone `set` saves at once: every alias that sends one opens a
        transaction first and ends it after."""
        section = handwritten()
        senders = re.findall(r"\nalias (\S+) \{", section)
        checked = 0
        for name in senders:
            body = alias(name)
            if not re.search(r"dccore\.send set ", body):
                continue
            checked += 1
            first_set = re.search(r"dccore\.send set ", body).start()
            self.assertIn("dccore.send setbegin", body[:first_set], name)
            self.assertRegex(body[first_set:], r"dccore\.send set(commit|abort)", name)
        self.assertEqual(checked, 2)        # Apply and the preview

    def test_the_preview_never_commits(self):
        body = alias("dccore.sw.preview")
        self.assertNotIn("setcommit", body)
        self.assertLess(body.index("dccore.send setpreview"), body.index("dccore.send setabort"))

    def test_only_changed_settings_are_sent(self):
        self.assertIn("var %keys = $iif($dccore.sw.s(loaded),$dccore.sw.changed)", alias("dccore.sw.apply"))
        dirty = statements(alias("dccore.sw.dirty"))
        self.assertIn("if ($+(=,$dccore.sw.shown($1)) === $dccore.sw.s(d. $+ $1)) { return $false }", dirty)

    def test_ok_closes_only_once_the_bot_has_saved(self):
        done = alias("dccore.sw.done")
        ok_at = done.index("if ($1 == ok) {")
        error_at = done.index("if ($1 == error) {", ok_at)
        ok = done[ok_at:error_at]
        self.assertIn("if ($dccore.sw.s(close)) { dccore.sys Settings: %said | dialog -x dccore.set | return }", ok)
        error = done[error_at:]
        self.assertNotIn("dialog -x", error)
        self.assertIn("hdel dccore.sws close", error)
        # OK with nothing changed closes at once
        self.assertIn("if ($1 == close) { dialog -x dccore.set | return }", alias("dccore.sw.apply"))

    def test_the_confirm_question_is_asked_from_a_timer_and_answered(self):
        self.assertIn(".timerdccoreSwAsk -m 1 0 dccore.sw.confirm", alias("dccore.sw.done"))
        confirm = alias("dccore.sw.confirm")
        self.assertIn("if ($input(%q,yq,DCCore - Settings)) {", confirm)
        self.assertIn("dccore.send setcommit confirm", confirm)
        self.assertIn("dccore.send setabort", confirm)

    def test_a_file_location_asks_before_it_is_sent(self):
        apply = alias("dccore.sw.apply")
        self.assertLess(apply.index("if ($dccore.sw.risky(%keys)) {"), apply.index("dccore.send setbegin"))
        data = generated_data()
        files = data["confirm"].split()
        self.assertIn("TMP_ZIP_DIR", files)
        self.assertIn("DCC_QUEUE_FILE", files)
        self.assertEqual(len(files), 23)

    def test_cancel_sends_nothing(self):
        cancel = [c for c in dialog("dccore.set") if c[2] == 1016]
        self.assertEqual(cancel[0][7], "cancel")
        self.assertNotIn("1016", alias("dccore.sw.click"))


class EverySnapshotIsCounted(unittest.TestCase):
    """The outbox can drop lines, and a stalled snapshot ends with no END
    at all: nothing is shown from one that did not arrive whole."""

    def branch(self, kind):
        body = alias("dccore.sw.line")
        start = body.index("if (%%t == %s) {" % kind)
        end = body.find("\n  if (%t ==", start + 1)
        return body[start:] if end < 0 else body[start:end]

    def test_each_end_compares_what_came_with_what_was_announced(self):
        self.assertIn("if ($dccore.sw.s(setgot) != $2) || ($dccore.sw.s(setwant) != $2) {", self.branch("SETEND"))
        self.assertIn("if ($dccore.sw.s(srv.got) != $2-4) {", self.branch("SRVEND"))
        self.assertIn("if ($dccore.sw.s(fld.got) != $2) || ($dccore.sw.s(fld.want) != $2)", self.branch("FLDEND"))
        self.assertIn("if ($dccore.sw.s(oc.got) != $2) || ($dccore.sw.s(oc.want) != $2)", self.branch("OCEND"))
        self.assertIn("if ($dccore.sw.s(ban.gp) != $iif($2 > 200,200,$2)) || ($dccore.sw.s(ban.gt) != "
                      "$iif($3 > 200,200,$3)) {", self.branch("BANEND"))
        self.assertIn("if ($dccore.sw.s(pvgot) != $2)", self.branch("PVEND"))

    def test_the_settings_are_only_shown_once_complete(self):
        end = self.branch("SETEND")
        self.assertLess(end.index("!= $2"), end.index("dccore.sw.fillall"))
        self.assertLess(end.index("return"), end.index("dccore.sw.fillall"))

    def test_the_count_check_counts(self):
        self.assertIn("hinc dccore.sws setgot", self.branch("SETF"))
        self.assertIn("hadd dccore.sws setgot 0", self.branch("SETBEGIN"))
        self.assertIn("dccore.sw.srv.count 1", self.branch("SRVLIST"))
        self.assertIn("dccore.sw.srv.count 2", self.branch("SRVCHAN"))
        self.assertIn("dccore.sw.srv.count 3", self.branch("SRVFOLDER"))

    def test_a_missing_end_times_out(self):
        self.assertIn("dccore.sw.wait settings", alias("dccore.sw.load"))
        self.assertIn("dccore.sw.wait $1", alias("dccore.sw.ask"))
        for kind, what in (("SETEND", "settings"), ("SRVEND", "served"), ("FLDEND", "folders"),
                           ("OCEND", "onconnect"), ("BANEND", "banlist")):
            self.assertIn("dccore.sw.waited %s" % what, self.branch(kind))
        self.assertIn("dccore.sw.waited settings", alias("dccore.sw.done"))
        self.assertIn(".timerdccoreSwTick 0 5 dccore.sw.tick", alias("dccore.sw.init"))
        tick = alias("dccore.sw.tick")
        self.assertIn("if ($ctime > $dccore.sw.s(%item)) { hdel dccore.sws %item | dccore.sw.timeout "
                      "$gettok(%item,2,46) }", tick)
        self.assertIn("hadd dccore.sws phase idle", alias("dccore.sw.timeout"))


# ---------------------------------------------------------------------------
# The dialog itself
# ---------------------------------------------------------------------------

def text_px(text):
    return sum(CHAR_PX[c] for c in text)


def wrapped_lines(text, width_dbu):
    width = width_dbu * PX_PER_DBU
    lines, current = 1, 0
    for word in text.split(" "):
        need = text_px(word)
        if current == 0:
            current = need
        elif current + CHAR_PX[" "] + need <= width:
            current += CHAR_PX[" "] + need
        else:
            lines += 1
            current = need
    return lines


class TheDialog(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.controls = dialog("dccore.set")
        cls.data = generated_data()
        cls.pages = page_ids(cls.data)
        cls.size = tuple(map(int, re.search(r"size -1 -1 (\d+) (\d+)", block("dialog dccore.set {")).groups()))

    def texts(self):
        """(kind, text, id, w, h) for every labelled control, with the texts
        the init handler writes in (t.<id>) in place of their empty strings."""
        rows = []
        for kind, label, cid, _x, _y, w, h, _s in self.controls:
            if kind not in ("check", "text", "button", "tab"):
                continue
            if "t.%d" % cid in self.data:
                label = untext(self.data["t.%d" % cid]).replace("&&", "&")
            rows.append((kind, label, cid, w, h))
        return rows

    def test_the_size_is_about_the_mockups(self):
        self.assertLessEqual(self.size[0], 420)
        self.assertLessEqual(self.size[1], 300)

    def test_ids_are_unique_and_clear_of_the_other_dialogs(self):
        ids = [c[2] for c in self.controls]
        self.assertEqual(len(ids), len(set(ids)))
        others = {c[2] for c in dialog("dccore.opt")} | {c[2] for c in dialog("dccore.dq")}
        self.assertEqual(set(ids) & others, set())
        self.assertGreater(min(ids), 1000)

    def test_every_control_is_on_exactly_one_page(self):
        seen = {}
        for page, ids in self.pages.items():
            for cid in ids:
                self.assertNotIn(cid, seen, "%d is on pages %s and %d" % (cid, seen.get(cid), page))
                seen[cid] = page
        table = {c[2] for c in self.controls}
        self.assertEqual(set(seen) | GLOBAL_IDS, table)
        self.assertEqual(set(seen) & GLOBAL_IDS, set())

    def test_no_control_is_attached_to_a_tab(self):
        """mIRC would show a tab's controls again whenever it is clicked."""
        for kind, _label, cid, _x, _y, _w, _h, style in self.controls:
            self.assertNotRegex(style, r"\btab\b", cid)

    def test_every_label_fits(self):
        clipped = []
        for kind, text, cid, w, h in self.texts():
            if kind == "tab" or not text:
                continue
            available = w * PX_PER_DBU
            if kind == "check":
                if text_px(text) + CHECK_BOX_PX > available:
                    clipped.append("%d %r (check)" % (cid, text))
            elif kind == "button":
                if text_px(text) + 8 > available:
                    clipped.append("%d %r (button)" % (cid, text))
            elif wrapped_lines(text, w) * 8 > h:
                clipped.append("%d %r needs %d lines in %d dbu" % (cid, text, wrapped_lines(text, w), h))
        self.assertEqual(clipped, [])
        self.assertGreater(len(self.texts()), 200)

    def test_every_label_is_in_the_width_table(self):
        for _kind, text, cid, _w, _h in self.texts():
            self.assertEqual(sorted(set(text) - set(CHAR_PX)), [], cid)

    def test_the_label_arithmetic_would_catch_a_long_one(self):
        """Mutation check: a dashboard label in a narrow box is clipped."""
        self.assertGreater(wrapped_lines("Check the on-connect commands worked every (minutes, 0 = never)", 186), 1)
        self.assertGreater(text_px("Also send requests, queue positions, starts and searches to the IRC debug "
                                   "channel") + CHECK_BOX_PX, 157 * PX_PER_DBU)

    def test_every_control_is_inside_the_dialog_and_no_two_on_a_page_overlap(self):
        where = {c[2]: c for c in self.controls}

        def box(cid):
            kind, _l, _i, x, y, w, h, _s = where[cid]
            return x, y, x + w, y + (12 if kind == "combo" else h)
        for cid in where:
            if where[cid][0] == "tab" and cid != 1001:
                continue
            x0, y0, x1, y1 = box(cid)
            self.assertLessEqual(x1, self.size[0], cid)
            self.assertLessEqual(y1, self.size[1], cid)
        for page, ids in self.pages.items():
            for n, a in enumerate(ids):
                for b in ids[n + 1:]:
                    A, B = box(a), box(b)
                    overlap = A[0] < B[2] and B[0] < A[2] and A[1] < B[3] and B[1] < A[3]
                    self.assertFalse(overlap, "page %d: %d and %d overlap" % (page, a, b))

    def test_the_tabs_are_a_strip_above_the_pages(self):
        tabs = [c for c in self.controls if c[0] == "tab"]
        self.assertEqual([t[1] for t in tabs], ["General", "Sharing", "Downloads", "Security",
                                                "Dashboard & Console", "Advanced"])
        top = min(c[4] for c in self.controls if c[0] != "tab")
        self.assertLessEqual(tabs[0][4] + tabs[0][6], top)

    def test_the_footer_has_apply_ok_and_cancel(self):
        buttons = {c[2]: c[1] for c in self.controls if c[0] == "button"}
        self.assertEqual((buttons[1014], buttons[1015], buttons[1016]), ("Apply", "OK", "Cancel"))
        click = alias("dccore.sw.click")
        self.assertIn("if (%id == 1014) { dccore.sw.apply | return }", click)
        self.assertIn("if (%id == 1015) { dccore.sw.apply close | return }", click)
        # OK is not mIRC's own "ok" button, which would close before the bot answers
        ok = next(c for c in self.controls if c[2] == 1015)
        self.assertNotIn("ok", ok[7])

    def test_the_ids_the_handwritten_code_uses_exist(self):
        table = {c[2] for c in self.controls}
        used = set(int(i) for i in re.findall(r"\bdccore\.set (1[0-9]{3})\b", handwritten()))
        used |= set(int(i) for i in re.findall(r"%id == (1[0-9]{3})\b", handwritten()))
        self.assertGreater(len(used), 30)
        self.assertEqual(used - table, set())


class TheWindowsBehaviour(unittest.TestCase):

    def test_the_browse_buttons_need_a_local_bot(self):
        self.assertEqual(statements(alias("dccore.sw.local")),
                         ["return $iif($istok(127.0.0.1 ::1,$chat($dccore.bot).ip,32),$true,$false)"])
        self.assertTrue(statements(alias("dccore.sw.browse"))[0].startswith("if (!$dccore.sw.local) {"))
        enable = alias("dccore.sw.enable")
        self.assertIn("if ($1) && (!$dccore.sw.local) {", enable)
        self.assertGreater(len([k for k in generated_data() if k.startswith("br.")]), 25)

    def test_the_input_prompts_have_no_literal_comma(self):
        for match in re.finditer(r"\$input\(([^%][^)]*)\)", handwritten()):
            self.assertEqual(match.group(1).count(","), 2, match.group(0))

    def test_this_mirc_window_page_opens_the_old_options(self):
        self.assertIn("if (%id == 1616) { dccore.options | return }", alias("dccore.sw.click"))
        self.assertIn('"@DCCore window options..."', block("dialog dccore.set {"))
        # /dccore options is still the old dialog
        self.assertIn("if (%cmd == options) { dccore.options | return }", alias("dccore"))
        self.assertIn("if (%cmd == settings) { dccore.settings | return }", alias("dccore"))

    def test_the_local_switches_are_this_mircs(self):
        data = generated_data()
        self.assertEqual(data["locals"].split(), ["start.main", "start.chat", "start.downloads", "auto"])
        save = alias("dccore.sw.savelocal")
        self.assertIn("if (%state != $dccore.opt(%o)) { hadd dccore %o %state | inc %n }", save)
        self.assertIn("if (%n) { dccore.save }", save)

    def test_every_menu_with_options_has_settings_next_to_it(self):
        text = code()
        menus = re.findall(r"\nmenu [^\n]+ \{\n(.*?)\n\}", text, re.S)
        with_options = 0
        for menu in menus:
            lines = [line.strip() for line in menu.split("\n")]
            for n, line in enumerate(lines):
                if line.lstrip(".").startswith("Options...:"):
                    with_options += 1
                    dots = line[:len(line) - len(line.lstrip("."))]
                    self.assertEqual(lines[n + 1], dots + "Settings...:dccore.settings")
        self.assertEqual(with_options, 3)
        self.assertIn("\n  Bot Settings:dccore.settings\n", block("menu @DCCore {"))

    def test_the_preview_window_draws_the_raw_lines(self):
        body = alias("dccore.sw.line")
        self.assertIn("echo @DCCore-preview $+($chr(3),14,$2,:,$chr(15)) $3-", body)

    def test_the_window_needs_mirc_617_for_regsubex(self):
        self.assertIn("if ($version < 6.17) {", alias("dccore.settings"))

    def test_a_reconnect_or_a_closed_console_starts_the_window_again(self):
        self.assertIn("dccore.sw.lost", block("on *:CHATCLOSE: {"))
        hello = alias("dccore.structured")
        hello = hello[hello.index("if (%type == HELLO) {"):hello.index("if (%type == STATUS)")]
        self.assertIn("dccore.sw.lost", hello)

    def test_the_lines_are_well_under_mircs_limit(self):
        self.assertLess(max(len(line) for line in raw().split("\n")), 900)


class TheExamplesAreInvented(unittest.TestCase):
    """The mockup the layout follows holds real channel, nick and network
    names. Assert what the shipped files hold instead of listing what they
    must not: every channel is #music and every host is *.example."""

    def files(self):
        names = ["scripts/mirc/settings_window_layout.py", "scripts/mirc/build_settings_window.py"]
        texts = [io.open(os.path.join(REPO_ROOT, n), encoding="utf-8").read() for n in names]
        return texts + [handwritten()]

    def test_every_channel_and_host_is_an_invented_one(self):
        for text in self.files():
            for channel in re.findall(r"(?<![\w&])#[A-Za-z][\w-]*", text):
                self.assertIn(channel, ("#music",), channel)
            for host in re.findall(r"\*!\*@[\w.-]+|\*\.[\w-]+\.[\w.-]+", text):
                self.assertTrue(host.endswith(".example"), host)


class TheVersion(unittest.TestCase):

    def test_the_script_is_1_19_0(self):
        self.assertIn("\nalias dccore.ver { return 1.19.0 }\n", code())


if __name__ == "__main__":
    unittest.main()
