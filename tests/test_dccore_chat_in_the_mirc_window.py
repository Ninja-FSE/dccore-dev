"""#371: DCCore Chat in dccore.mrc - the window, relayed by the bot.

mIRC cannot run here, so these read the script, statement by statement, the
way the script's other tests do. The bot does the checking
(tests/test_servers_chat_is_relayed_by_the_bot.py); the script is held to
what is left for it:

- it only ever sends what the operator typed, and only through the bot;
- nothing in the message handler, or in anything drawing a CHAT line, sends
  anything (RFC 2812);
- a raw tagged message is hidden only while the relay is up and would draw
  it - otherwise it shows in the channel as usual, so nothing is lost;
- a line is drawn once, stripped, only for channels the operator chose;
- it says it is public;
- a menu row never puts a channel name into a command.
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
        body = statements(block(script(), "on ^*:TEXT:*:#:"))
        self.assertEqual(body[0], "if ($1 != $dccore.chat.tag) { return }")


class ARawNoticeIsHiddenOnlyWhenItWillBeDrawn(unittest.TestCase):
    def setUp(self):
        self.handler = statements(block(script(), "on ^*:TEXT:*:#:"))

    def test_every_condition_comes_before_haltdef(self):
        halt = self.handler.index("haltdef")
        for condition in ("if (!$dccore.chat.relaying) { return }",
                          "if (!$dccore.here) { return }",
                          "if (!$istok($dccore.st(chat.chans),$chan,32)) { return }",
                          "if (!$dccore.chat.listens($chan)) { return }",
                          "if (!$window($dccore.chat.win)) && (!$dccore.opt(chat.popup)) { return }"):
            self.assertLess(self.handler.index(condition), halt, condition)
        self.assertEqual(self.handler[-1], "haltdef", "it hides, and draws nothing itself")

    def test_the_relay_is_up_only_when_connected_and_structured(self):
        self.assertIn("alias dccore.chat.relaying { return $iif(($dccore.st(state) == in) && "
                      "($dccore.st(mode) == structured),$true,$false) }", script())


class NothingAnswers(unittest.TestCase):
    """RFC 2812 - checked in the handler AND in every alias drawing a line,
    since a send one call away is still an automatic reply."""

    REACHED = ["on ^*:TEXT:*:#:", "alias dccore.chat.feed", "alias dccore.chat.channels",
               "alias dccore.chat.listens", "alias dccore.chat.show", "alias dccore.chat.window",
               "alias dccore.chat.sys", "alias dccore.chat.title"]

    def test_no_send_anywhere_it_reaches(self):
        text = script()
        for header in self.REACHED:
            for line in statements(block(text, header)):
                self.assertIsNone(SENDS.search(line), f"{header}: {line}")

    def test_the_guard_does_see_a_send(self):
        """So the test above cannot pass by matching nothing."""
        self.assertIsNotNone(SENDS.search(".notice $nick hello"))
        self.assertIsNotNone(SENDS.search("if (x) { msg $chan hi }"))
        self.assertIsNotNone(SENDS.search("dccore.send chat %to %text"))


class WhatComesFromTheBot(unittest.TestCase):
    def setUp(self):
        self.text = script()
        self.feed = statements(block(self.text, "alias dccore.chat.feed"))

    def test_both_lines_are_read(self):
        self.assertIn("if (%type == CHAT) { dccore.chat.feed $2- | return }", self.text)
        self.assertIn("if (%type == CHANNELS) { dccore.chat.channels $2- | return }", self.text)

    def test_a_line_is_drawn_once(self):
        """The bot replays its recent lines after every HELLO."""
        self.assertIn("if ($dccore.st(chat.last) isnum) && ($1 <= $dccore.st(chat.last)) { return }",
                      self.feed)
        self.assertIn("hadd dccore.live chat.last $1", self.feed)

    def test_only_listened_channels_and_stripped(self):
        self.assertIn("if (!$dccore.chat.listens($2)) { return }", self.feed)
        self.assertTrue(all("$strip($4-)" in s for s in self.feed if s.startswith("dccore.chat.show")))
        self.assertIn("dccore.default chat.all 0", self.text)

    def test_its_own_lines_are_marked_as_its_own(self):
        self.assertTrue(any("$iif($3 == $dccore.bot,own,other)" in s for s in self.feed))


class WhatIsSent(unittest.TestCase):
    def setUp(self):
        self.say = statements(block(script(), "alias dccore.chat.say"))

    def test_through_the_bot_only(self):
        self.assertEqual(self.say[-1], "dccore.send chat %to %text")
        self.assertFalse([s for s in self.say if re.search(r"(^|[\s|{])\.?(notice|msg)\b", s)],
                         "never straight from this client")

    def test_stripped_and_only_when_the_relay_is_up(self):
        send = self.say.index("dccore.send chat %to %text")
        self.assertLess(self.say.index("var %text = $strip($1-)"), send)
        self.assertLess(next(i for i, s in enumerate(self.say) if s.startswith("if (!$dccore.chat.relaying)")), send)
        self.assertLess(next(i for i, s in enumerate(self.say) if s.startswith("if (%to == $null)")), send)

    def test_typing_in_the_window_sends(self):
        body = statements(block(script(), "on *:INPUT:@DCCore-Chat:"))
        self.assertIn("dccore.chat.say $1-", body)
        self.assertEqual(body[-1], "halt")


class ItSaysItIsPublic(unittest.TestCase):
    def test_the_title_and_the_first_lines(self):
        text = script()
        title = statements(block(text, "alias dccore.chat.title"))
        self.assertTrue(any("DCCore Chat $dccore.dot public" in s for s in title))
        self.assertIn("dccore.chat.sys Public: everyone in the channel reads what is typed here", text)
        self.assertIn('box "DCCore Chat (public)", 600,', text)

    def test_an_arriving_line_does_not_take_the_focus(self):
        body = statements(block(script(), "alias dccore.chat.window"))
        self.assertIn("if ($1 == quiet) { window -en $dccore.chat.win }", body)
        self.assertEqual(statements(block(script(), "alias dccore.chat.show"))[0],
                         "dccore.chat.window quiet")


class TheWindowIsTheInterface(unittest.TestCase):
    def test_its_menu_picks_from_the_bot_s_channels(self):
        text = script()
        menu = block(text, "menu @DCCore-Chat")
        self.assertIn(".$submenu($dccore.chat.sendrow($1))", menu)
        self.assertIn(".$submenu($dccore.chat.listenrow($1))", menu)
        self.assertIn("alias dccore.chat.chan { return $gettok($dccore.st(chat.chans),$1,32) }", text)

    def test_a_menu_row_runs_a_number_never_a_channel_name(self):
        """mIRC parses a row's command text on the click, and a channel name
        can hold | or $. The name may be the label; the command carries only
        the row number, resolved inside the alias."""
        text = script()
        for row in ("alias dccore.chat.sendrow", "alias dccore.chat.listenrow"):
            ret = [s for s in statements(block(text, row)) if s.startswith("return $iif(")][0]
            _label, command = ret.split(":", 1)
            self.assertNotIn("%c", command, f"{row}: {command}")
            self.assertTrue(command.endswith(" $1"), command)
        self.assertIn("alias dccore.chat.to.n { if ($1 isnum) && ($dccore.chat.chan($1) != $null) "
                      "{ dccore.chat.to $dccore.chat.chan($1) } }", text)

    def test_picking_where_to_send_also_listens_there(self):
        body = statements(block(script(), "alias dccore.chat.to"))
        self.assertIn("dccore.set chat.to $1", body)
        self.assertTrue(any("$addtok($dccore.opt(chat.listen),$1,32)" in s for s in body))

    def test_the_options_dialog_keeps_both_boxes(self):
        text = script()
        init = block(text, "on *:dialog:dccore.opt:init:0:")
        ok = block(text, "on *:dialog:dccore.opt:sclick:1:")
        self.assertIn("if ($dccore.opt(chat.all)) { did -c dccore.opt 601 }", init)
        self.assertIn("hadd dccore chat.popup $did(dccore.opt,602).state", ok)

    def test_no_flood_table_of_its_own(self):
        """The bot limits what arrives; a second limit here would only hide
        lines the bot already decided to show."""
        self.assertNotIn("dccore.chatrate", script())

    def test_it_is_in_the_command_list_and_the_menus(self):
        text = script()
        self.assertIn("/dccore chat [text]", text)
        self.assertIn(".Open DCCore Chat:dccore chat", text)
        self.assertIn("alias dccore.ver { return 1.6 }", text)


class TheDefaultIsEveryChannelWithOtherDccoreBots(unittest.TestCase):
    """Typing in the window says it where the bot has seen another DCCore
    bot (`chat *`), unless one channel was picked from the menu."""

    def test_no_pick_means_star(self):
        body = "\n".join(statements(block(script(), "alias dccore.chat.say")))
        self.assertIn("if (%to == $null) { var %to = * }", body)
        self.assertIn("dccore.send chat %to %text", body)

    def test_star_is_not_checked_against_the_channel_list(self):
        body = "\n".join(statements(block(script(), "alias dccore.chat.say")))
        self.assertIn("if (%to != *) && ($dccore.st(chat.chans) != $null)", body)

    def test_a_menu_row_goes_back_to_it(self):
        body = "\n".join(statements(block(script(), "alias dccore.chat.to.all")))
        self.assertIn("dccore.set chat.to *", body)
        self.assertIn("dccore.chat.to.all", script())


if __name__ == "__main__":
    unittest.main()
