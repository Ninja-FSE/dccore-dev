"""#371: DCCore Chat, relayed by the bot (serverschat.py).

The bot reads a NOTICE whose first word is [ServersChat] in one of its
channels and hands it to the operator's console; `chat #chan text` sends one.
What arrives is other people's text on the read loop, so it is held to the
capture rules: first word only, the bot's channels only, stripped, capped,
limited per nick, bounded, in memory only, never sent anywhere else and
never answered.
"""

import io
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import adminchat  # noqa: E402
import announce  # noqa: E402
import defaults as config  # noqa: E402
import irc  # noqa: E402
import runtime  # noqa: E402
import serverschat  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402

CHAN = "#examplechan"
T0 = 1_000_000.0


class FakeSession:
    def __init__(self, structured=True):
        self.authenticated = True
        self.structured = structured
        self.closed = False
        self.nick = "SomeOperator"
        self.client = "dccore.mrc"
        self.sent = []

    def send(self, text):
        self.sent.append(text)

    def send_status(self):
        self.sent.append("(status)")


class Case(DCCoreTestCase):
    def setUp(self):
        super().setUp()
        self.set_config(NICKNAME="OurBot")
        config.channel_users[CHAN] = {"ourbot", "someoperator"}
        self.session = FakeSession()
        real = adminchat.active_session
        adminchat.active_session = lambda: self.session
        self.addCleanup(setattr, adminchat, "active_session", real)
        self.debug = []
        real_debug = announce.send_debug
        announce.send_debug = lambda *a, **k: self.debug.append(a)
        self.addCleanup(setattr, announce, "send_debug", real_debug)
        # No daemon here: what would go out lands in config.send_queue.
        self.oserve = sys.modules.pop("oserve", None)
        self.addCleanup(lambda: self.oserve and sys.modules.__setitem__("oserve", self.oserve))

    def arrive(self, text, nick="OtherOperator", target=CHAN, now=T0):
        return serverschat.capture(nick, target, text, now=now)

    def chat_lines(self):
        return [line for line in self.session.sent if line.startswith("DCCORE CHAT ")]

    def queued(self):
        return {key: list(lines) for key, lines in (config.send_queue or {}).items()}


class WhatArrives(Case):
    def test_a_tagged_notice_in_our_channel_reaches_the_console(self):
        line = self.arrive("[ServersChat] hello all")
        self.assertIsNotNone(line)
        self.assertEqual(len(self.chat_lines()), 1)
        fields = self.chat_lines()[0].split(" ", 5)
        self.assertEqual(fields[3:], [CHAN, "OtherOperator", "hello all"])
        self.assertTrue(fields[2].isdigit())

    def test_the_tag_has_to_be_the_first_word(self):
        self.assertIsNone(self.arrive("hello [ServersChat] all"))
        self.assertIsNone(self.arrive("[ServersChatX] hello"))
        self.assertIsNone(self.arrive("plain notice"))
        self.assertEqual(self.chat_lines(), [])

    def test_only_the_bot_s_own_channels(self):
        self.assertIsNone(self.arrive("[ServersChat] hi", target="#elsewhere"))
        self.assertIsNone(self.arrive("[ServersChat] hi", target="OurBot"))

    def test_never_our_own_nick(self):
        self.assertIsNone(self.arrive("[ServersChat] echo", nick="OurBot"))

    def test_colours_and_control_codes_are_stripped(self):
        line = self.arrive("[ServersChat] \x0304red\x03 and \x02bold\x02\x07")
        self.assertEqual(line["text"], "red and bold")

    def test_the_text_is_capped(self):
        line = self.arrive("[ServersChat] " + "x" * 1000)
        self.assertEqual(len(line["text"]), serverschat.MAX_TEXT)

    def test_nothing_left_after_stripping_is_not_a_line(self):
        self.assertIsNone(self.arrive("[ServersChat] \x02\x02"))

    def test_it_never_goes_to_the_debug_channel(self):
        self.arrive("[ServersChat] private enough")
        self.assertEqual(self.debug, [])

    def test_a_plain_console_gets_it_as_words(self):
        self.session.structured = False
        self.arrive("[ServersChat] hi")
        self.assertEqual(self.session.sent, [f"[CHAT] {CHAN} <OtherOperator> hi"])

    def test_ids_only_go_up(self):
        first = self.arrive("[ServersChat] one", now=T0)
        second = self.arrive("[ServersChat] two", now=T0)
        third = self.arrive("[ServersChat] three", now=T0 - 5)
        self.assertLess(first["id"], second["id"])
        self.assertLess(second["id"], third["id"])


class NothingAnswers(Case):
    def test_capture_sends_nothing(self):
        for i in range(10):
            self.arrive(f"[ServersChat] line {i}", nick=f"Operator{i}")
        self.assertEqual(self.queued(), {})

    def test_the_capture_path_has_no_send_in_it(self):
        with io.open(os.path.join(REPO_ROOT, "serverschat.py"), encoding="utf-8") as handle:
            code = handle.read()
        body = code[code.index("def capture("):code.index("def _enqueue(")]
        # Statements only: the docstring says "A NOTICE arrived", which is
        # not a send (a guard that reads prose passes or fails on words).
        body = re.sub(r'"""[\s\S]*?"""', "", body)
        body = "\n".join(line for line in body.split("\n") if not line.strip().startswith("#"))
        for word in ("_enqueue(", "queue_message", "send_queue", '"NOTICE', "f\"NOTICE", "session.sock"):
            self.assertNotIn(word, body)
        self.assertIn("_deliver(line)", body, "the body was really read")


class OneSenderCannotFloodIt(Case):
    def test_five_in_ten_seconds_then_hidden_for_sixty(self):
        for i in range(serverschat.INBOUND_MAX):
            self.assertIsNotNone(self.arrive(f"[ServersChat] {i}", now=T0 + i))
        self.assertIsNone(self.arrive("[ServersChat] too many", now=T0 + 5))
        notices = [line for line in self.chat_lines() if " * " in line]
        self.assertEqual(len(notices), 1, "said once")
        self.assertIsNone(self.arrive("[ServersChat] still", now=T0 + 30))
        self.assertEqual(len([line for line in self.chat_lines() if " * " in line]), 1)
        self.assertIsNotNone(self.arrive("[ServersChat] back", now=T0 + 5 + serverschat.INBOUND_HIDE + 1))

    def test_another_nick_is_not_held_back(self):
        for i in range(serverschat.INBOUND_MAX + 1):
            self.arrive(f"[ServersChat] {i}", now=T0)
        self.assertIsNotNone(self.arrive("[ServersChat] me", nick="ThirdOperator", now=T0))


class MemoryOnlyAndBounded(Case):
    def test_the_recent_lines_are_bounded(self):
        for i in range(serverschat.RECENT_MAX + 20):
            self.arrive(f"[ServersChat] {i}", nick=f"Operator{i}", now=T0 + i)
        self.assertEqual(len(runtime.chat_recent), serverschat.RECENT_MAX)
        self.assertEqual(runtime.chat_recent[-1]["text"], str(serverschat.RECENT_MAX + 19))

    def test_the_limit_table_is_bounded(self):
        for i in range(300):
            self.arrive(f"[ServersChat] {i}", nick=f"Operator{i}", now=T0)
        self.arrive("[ServersChat] later", nick="LateOperator", now=T0 + serverschat.INBOUND_PER + 1)
        self.assertLess(len(runtime.chat_rate), 50)

    def test_nothing_is_written_to_disk(self):
        with io.open(os.path.join(REPO_ROOT, "serverschat.py"), encoding="utf-8") as handle:
            code = handle.read()
        for word in ("import db", "db.", "open(", "json"):
            self.assertNotIn(word, code)


class WhatIsSent(Case):
    def test_a_tagged_notice_through_the_paced_queue(self):
        ok, _message = serverschat.say("SomeOperator", CHAN, "hello there", now=T0)
        self.assertTrue(ok)
        self.assertEqual(self.queued(), {CHAN: [f"NOTICE {CHAN} :[ServersChat] hello there\r\n"]})

    def test_the_own_line_comes_back_as_the_bot(self):
        serverschat.say("SomeOperator", CHAN, "hello there", now=T0)
        self.assertEqual(self.chat_lines()[0].split(" ", 5)[3:], [CHAN, "OurBot", "hello there"])

    def test_a_line_break_cannot_smuggle_a_second_command(self):
        serverschat.say("SomeOperator", CHAN, "hi\r\nQUIT :bye\x0304red", now=T0)
        line = self.queued()[CHAN][0]
        self.assertEqual(line.count("\r\n"), 1)
        self.assertTrue(line.endswith("\r\n"))
        self.assertNotIn("\x03", line)

    def test_the_line_fits_irc(self):
        serverschat.say("SomeOperator", CHAN, "é" * 500, now=T0)
        self.assertLessEqual(len(self.queued()[CHAN][0].encode("utf-8")), 512)

    def test_only_the_bot_s_channels(self):
        ok, message = serverschat.say("SomeOperator", "#elsewhere", "hi", now=T0)
        self.assertFalse(ok)
        self.assertIn("Not in #elsewhere", message)
        self.assertFalse(serverschat.say("SomeOperator", "nochannel", "hi", now=T0)[0])
        self.assertEqual(self.queued(), {})

    def test_nothing_to_send(self):
        self.assertFalse(serverschat.say("SomeOperator", CHAN, " \x02 ", now=T0)[0])

    def test_a_cap_of_its_own(self):
        for i in range(serverschat.OUTBOUND_MAX):
            self.assertTrue(serverschat.say("SomeOperator", CHAN, str(i), now=T0 + i)[0])
        ok, message = serverschat.say("SomeOperator", CHAN, "one more", now=T0 + 10)
        self.assertFalse(ok)
        self.assertIn("Slow down", message)
        self.assertTrue(serverschat.say("SomeOperator", CHAN, "later", now=T0 + serverschat.OUTBOUND_PER + 1)[0])


class TheConsoleCommand(Case):
    def test_chat_alone_lists_the_channels(self):
        adminchat._cmd_chat(self.session, "")
        self.assertEqual(self.session.sent, [f"DCCORE CHANNELS {CHAN}"])
        self.session.structured = False
        self.session.sent = []
        adminchat._cmd_chat(self.session, "")
        self.assertIn(CHAN, self.session.sent[0])
        self.assertIn("public", self.session.sent[0])

    def test_chat_sends_and_a_window_just_sees_its_line(self):
        adminchat._cmd_chat(self.session, f"{CHAN} hello")
        self.assertEqual(len(self.chat_lines()), 1)
        self.assertEqual([s for s in self.session.sent if not s.startswith("DCCORE CHAT")], [])

    def test_a_person_at_a_plain_console_is_told(self):
        self.session.structured = False
        adminchat._cmd_chat(self.session, f"{CHAN} hello")
        self.assertIn(f"Sent to {CHAN}.", self.session.sent)

    def test_a_refusal_is_said(self):
        adminchat._cmd_chat(self.session, "#elsewhere hello")
        self.assertTrue(any("Not in #elsewhere" in s for s in self.session.sent))

    def test_it_is_a_console_command(self):
        self.assertIn("chat", adminchat.COMMANDS)

    def test_hello_brings_the_channels_and_what_was_missed(self):
        self.arrive("[ServersChat] while you were away", now=T0)
        self.session.sent = []
        adminchat._cmd_hello(self.session, "dccore.mrc 1.6")
        self.assertIn(f"DCCORE CHANNELS {CHAN}", self.session.sent)
        replay = [s for s in self.session.sent if s.startswith("DCCORE CHAT ")]
        self.assertEqual(len(replay), 1)
        self.assertTrue(replay[0].endswith("while you were away"))


class TheReadLoop(Case):
    def test_the_notice_branch_hands_it_on(self):
        with io.open(os.path.join(REPO_ROOT, "irc.py"), encoding="utf-8") as handle:
            code = handle.read()
        at = code.index("notice_parsed = parse_notice(line)")
        self.assertIn("_capture_chat_notice(notice_user, notice_target, notice_text)",
                      code[at:at + 1200])

    def test_a_capture_that_raises_cannot_break_the_connection(self):
        real = serverschat.capture

        def explode(*_a, **_k):
            raise RuntimeError("bad line")

        serverschat.capture = explode
        self.addCleanup(setattr, serverschat, "capture", real)
        irc._capture_chat_notice("OtherOperator", CHAN, "[ServersChat] x")

    def test_the_real_capture_runs_from_it(self):
        irc._capture_chat_notice("OtherOperator", CHAN, "[ServersChat] via irc")
        self.assertEqual(len(self.chat_lines()), 1)


if __name__ == "__main__":
    unittest.main()
