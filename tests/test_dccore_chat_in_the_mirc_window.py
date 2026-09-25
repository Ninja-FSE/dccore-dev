"""#371: DCCore Chat - public operator chat in dccore.mrc, over a tagged NOTICE.

mIRC cannot run here, so these read the script, statement by statement, the
way the script's other tests do. What they hold it to is what #371 decided:

- the tag is matched as the first word and nowhere else, and a NOTICE that
  is not chat is left exactly as mIRC shows it (the handler returns before
  haltdef);
- NOTHING reachable from the NOTICE handler sends anything (RFC 2812: never
  answer a NOTICE automatically);
- a per-nick flood limit on what arrives;
- colours stripped both ways;
- only channels the operator chose, and it says it is public.
"""

import io
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "mirc", "dccore.mrc")

# A command that puts something on the wire, at the start of a statement.
SENDS = re.compile(r"(?:^|[{|]\s*)\.?(?:notice|msg|ctcp|describe|say|raw|quote|amsg|ame|"
                   r"dccore\.send|dccore\.chat\.say)\b", re.IGNORECASE)


def script():
    with io.open(SCRIPT, encoding="ascii", newline="") as handle:
        return handle.read().replace("\r\n", "\n")


def block(text, header):
    """The body of the block that `header` opens, braces balanced."""
    start = text.index(header)
    at = text.index("{", start + len(header) - 1)
    depth = 0
    for i in range(at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[at + 1:i]
    raise AssertionError("unbalanced block: " + header)


def statements(body):
    return [line.strip() for line in body.split("\n")
            if line.strip() and not line.strip().startswith(";")]


class TheTag(unittest.TestCase):
    def test_it_is_neutral_and_built_without_evaluation_brackets(self):
        self.assertIn("alias dccore.chat.tag { return $+($chr(91),ServersChat,$chr(93)) }", script())

    def test_it_is_the_first_word_and_the_first_check(self):
        body = statements(block(script(), "on ^*:NOTICE:*:#:"))
        self.assertEqual(body[0], "if ($1 != $dccore.chat.tag) { return }")


class WhatArrives(unittest.TestCase):
    def setUp(self):
        self.handler = statements(block(script(), "on ^*:NOTICE:*:#:"))

    def test_a_notice_that_is_not_chat_is_left_alone(self):
        """Every return that lets a line through comes before haltdef, so
        mIRC shows it as it always has."""
        halt = self.handler.index("haltdef")
        self.assertLess(self.handler.index("if (!$dccore.chat.listens($chan)) { return }"), halt)
        self.assertLess(self.handler.index(
            "if (!$window($dccore.chat.win)) && (!$dccore.opt(chat.popup)) { return }"), halt)
        self.assertEqual(sum(1 for line in self.handler if line == "haltdef"), 1)

    def test_the_flood_limit_comes_before_anything_is_shown(self):
        self.assertLess(self.handler.index("if ($dccore.chat.flooding($nick)) { return }"),
                        self.handler.index("dccore.chat.show $chan $nick other %text"))

    def test_what_is_shown_is_stripped(self):
        self.assertIn("var %text = $strip($2-)", self.handler)

    def test_only_chosen_channels_unless_all_is_ticked(self):
        text = script()
        body = statements(block(text, "alias dccore.chat.listens"))
        self.assertEqual(body, ["if ($dccore.opt(chat.all)) { return $true }",
                                "return $istok($dccore.opt(chat.listen),$1,32)"])
        self.assertIn("dccore.default chat.all 0", text)


class NothingAnswersANotice(unittest.TestCase):
    """RFC 2812 - and the reason a presence ping or an auto-acknowledge can
    never be built on this tag. Checked in the handler AND in every alias it
    reaches, since a send one call away is still an automatic reply."""

    REACHED = ["on ^*:NOTICE:*:#:", "alias dccore.chat.listens", "alias dccore.chat.flooding",
               "alias dccore.chat.show", "alias dccore.chat.window", "alias dccore.chat.sys",
               "alias dccore.chat.title"]

    def test_no_send_anywhere_it_reaches(self):
        text = script()
        for header in self.REACHED:
            for line in statements(block(text, header)):
                self.assertIsNone(SENDS.search(line), f"{header}: {line}")

    def test_the_guard_does_see_a_send(self):
        """So the test above cannot pass by matching nothing."""
        self.assertIsNotNone(SENDS.search(".notice $nick hello"))
        self.assertIsNotNone(SENDS.search("if (x) { msg $chan hi }"))
        self.assertIsNotNone(SENDS.search("dccore.chat.say %text"))


class TheFloodLimit(unittest.TestCase):
    def test_five_in_ten_seconds_then_hidden_for_sixty(self):
        text = script()
        self.assertIn("alias dccore.chat.max { return 5 }", text)
        self.assertIn("alias dccore.chat.per { return 10 }", text)
        self.assertIn("alias dccore.chat.hide { return 60 }", text)

    def test_the_hide_expires_by_itself_and_is_said_once(self):
        body = statements(block(script(), "alias dccore.chat.flooding"))
        self.assertIn("if ($hget(dccore.chatmute,%k)) { return $true }", body)
        self.assertIn("hadd -u $+ $dccore.chat.hide dccore.chatmute %k 1", body)
        self.assertIn("if (%n > $dccore.chat.max) {", body)
        self.assertEqual(sum("dccore.chat.sys" in line for line in body), 1)

    def test_the_tables_are_this_session_only(self):
        text = script()
        self.assertIn("if (!$hget(dccore.chatrate)) { hmake dccore.chatrate 32 }", text)
        self.assertIn("if ($hget(dccore.chatmute)) { hfree dccore.chatmute }", text)
        self.assertNotIn("hsave -o dccore.chat", text)


class WhatIsSent(unittest.TestCase):
    def setUp(self):
        self.say = statements(block(script(), "alias dccore.chat.say"))

    def test_a_tagged_notice_from_your_own_client_stripped(self):
        self.assertIn("var %text = $strip($1-)", self.say)
        self.assertIn(".notice %to $dccore.chat.tag %text", self.say)
        self.assertLess(self.say.index("var %text = $strip($1-)"),
                        self.say.index(".notice %to $dccore.chat.tag %text"))

    def test_only_to_a_channel_picked_and_one_you_are_in(self):
        send = self.say.index(".notice %to $dccore.chat.tag %text")
        self.assertLess(next(i for i, s in enumerate(self.say) if s.startswith("if (%to == $null)")), send)
        self.assertLess(next(i for i, s in enumerate(self.say) if s.startswith("if ($me !ison %to)")), send)

    def test_never_privmsg(self):
        self.assertFalse([s for s in self.say if re.search(r"(^|\s)\.?msg\b", s)])

    def test_typing_in_the_window_sends(self):
        body = statements(block(script(), "on *:INPUT:@DCCore-Chat:"))
        self.assertIn("dccore.chat.say $1-", body)
        self.assertEqual(body[-1], "halt")


class ItSaysItIsPublic(unittest.TestCase):
    def test_the_title_and_the_first_line(self):
        text = script()
        title = statements(block(text, "alias dccore.chat.title"))
        self.assertTrue(any("DCCore Chat $dccore.dot public" in s for s in title))
        self.assertIn("dccore.chat.sys Public: everyone in the channel reads what is typed here",
                      text)
        self.assertIn('box "DCCore Chat (public)", 600,', text)

    def test_an_arriving_line_does_not_take_the_focus(self):
        body = statements(block(script(), "alias dccore.chat.window"))
        self.assertIn("if ($1 == quiet) { window -en $dccore.chat.win }", body)
        show = statements(block(script(), "alias dccore.chat.show"))
        self.assertEqual(show[0], "dccore.chat.window quiet")


class TheWindowIsTheInterface(unittest.TestCase):
    def test_its_menu_picks_the_channels(self):
        menu = block(script(), "menu @DCCore-Chat")
        self.assertIn(".$submenu($dccore.chat.sendrow($1))", menu)
        self.assertIn(".$submenu($dccore.chat.listenrow($1))", menu)
        self.assertIn("Listen on all my channels:dccore.chat.all", menu)

    def test_a_menu_row_runs_a_number_never_a_channel_name(self):
        """#955 review: mIRC parses a row's command text on the click, and a
        channel name can hold | or $. The name may be the label; the command
        carries only the row number, resolved inside the alias."""
        text = script()
        for row in ("alias dccore.chat.sendrow", "alias dccore.chat.listenrow"):
            body = statements(block(text, row))
            ret = [s for s in body if s.startswith("return $iif(")][0]
            label, command = ret.split(":", 1)
            self.assertNotIn("%c", command, f"{row}: {command}")
            self.assertTrue(command.endswith(" $1"), command)
        self.assertIn("alias dccore.chat.to.n { if ($1 isnum) && ($chan($1) != $null) "
                      "{ dccore.chat.to $chan($1) } }", text)
        self.assertIn("alias dccore.chat.listen.n { if ($1 isnum) && ($chan($1) != $null) "
                      "{ dccore.chat.listen $chan($1) } }", text)

    def test_picking_where_to_send_also_listens_there(self):
        body = statements(block(script(), "alias dccore.chat.to"))
        self.assertIn("dccore.set chat.to $1", body)
        self.assertTrue(any("$addtok($dccore.opt(chat.listen),$1,32)" in s for s in body))

    def test_the_options_dialog_keeps_both_boxes(self):
        text = script()
        init = block(text, "on *:dialog:dccore.opt:init:0:")
        ok = block(text, "on *:dialog:dccore.opt:sclick:1:")
        self.assertIn("if ($dccore.opt(chat.all)) { did -c dccore.opt 601 }", init)
        self.assertIn("if ($dccore.opt(chat.popup)) { did -c dccore.opt 602 }", init)
        self.assertIn("hadd dccore chat.all $did(dccore.opt,601).state", ok)
        self.assertIn("hadd dccore chat.popup $did(dccore.opt,602).state", ok)

    def test_it_is_in_the_command_list_and_the_menus(self):
        text = script()
        self.assertIn("/dccore chat [text]", text)
        self.assertIn(".Open DCCore Chat:dccore chat", text)
        self.assertIn("alias dccore.ver { return 1.6 }", text)


if __name__ == "__main__":
    unittest.main()
