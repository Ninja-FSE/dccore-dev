"""dccore.mrc: the login line counts only after the password prompt.

The bot always sends `Enter Your Password:` before `Entering DCC Chat Admin
Interface`. The script's prompt branch is where the peer check runs and the
only place a chat's state becomes `auth` (the token was sent) or `password`
(the operator is asked to type it); a typed password and a refused token
move only between those two. The script accepts the login line only in one
of them: in any other state it says so in the window and leaves the chat
as it leaves one whose peer check said no (dccore.abandon), and the chat
never reaches state `in` - so nothing is sent, and no `DCCORE` line is read
after it. A chat already logged in is left alone by a second login line.

mIRC cannot run here. These tests RUN the script's own statements - on
^*:CHAT, dccore.line, dccore.peerok, dccore.abandon, dccore.set,
dccore.timers.off and on *:INPUT:@DCCore - with the evaluator of
tests/test_the_start_page_says_when_the_bot_is_offline.py, against fake
tables and a fake chat. Identifiers it does not know are refused loudly.
"""

import fnmatch
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_the_start_page_says_when_the_bot_is_offline import (  # noqa: E402
    BOT, Mirc, _Return, body_of, roll_changelog, split_words)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Placeholders, nobody's real host or token.
HOST = "*!*@bot.example.invalid"
ELSEWHERE = "*!*@elsewhere.example.invalid"
TOKEN = "tok-example-0000"
LOGIN = "Entering DCC Chat Admin Interface"
PROMPT = "Enter Your Password:"
BANNER = "Welcome to the example console"
IGNORED = "Ignored a login line from a chat that never asked for the password."

# Every state a chat can be in that is not auth, password or in.
NOT_ASKED = (None, "opening", "waiting", "banner", "closed", "taken")

RUN = ("dccore.line", "dccore.peerok", "dccore.abandon", "dccore.set", "dccore.timers.off")


class Chat(Mirc):
    """The evaluator, with what a chat event needs: $nick, $address, a
    halt that stops the event, and the `iswm` operator."""

    def __init__(self, host=HOST, token=TOKEN, bothost=HOST, **live):
        super().__init__(real=RUN, stubs={
            "strip": lambda text: text,
            "address": lambda nick, kind: self.host if nick.lower() == BOT.lower() else "",
            "dccore.ver": lambda: "1.20.0",
            "dccore.client": lambda: "dccore.mrc-0000",
            "dccore.prompt": lambda: ">",
            "left": lambda text, n: text[:int(n)],
        })
        self.host = host
        self.nick = BOT
        self.table("dccore").update({"bot": BOT, "wantopen": "1"})
        if token:
            self.table("dccore")["token"] = token
        if bothost:
            self.table("dccore")["bothost"] = bothost
        self.chats.add(BOT)
        # what dccore.connect leaves behind for a dial (byhand=1: the operator's own)
        defaults = dict(state="opening", byhand=0, tokentried=0, typed=0, vetted=0)
        defaults.update(live)
        self.live(**{k: v for k, v in defaults.items() if v is not None})
        if defaults.get("state") is None:
            self.table("dccore.live").pop("state", None)

    def identifier(self, name, raw, frame):
        if name == "nick" and raw is None:
            return self.nick
        return super().identifier(name, raw, frame)

    def command(self, text, frame):
        if text.split(" ", 1)[0] == "halt":
            self.calls.append(("halt", ""))
            raise _Return("")
        super().command(text, frame)

    def part(self, text, frame):
        inner = text.strip()
        if inner.startswith("(") and inner.endswith(")"):
            inner = inner[1:-1].strip()
        words = split_words(inner)
        if "iswm" in words[1:]:
            k = words.index("iswm")
            pattern = self.value(" ".join(words[:k]), frame)
            subject = self.value(" ".join(words[k + 1:]), frame)
            return fnmatch.fnmatchcase(subject.lower(), pattern.lower())
        return super().part(text, frame)

    def event(self, header, line):
        try:
            self.run(body_of(header), {"args": line.split(" "), "vars": {}})
        except _Return:
            pass

    def hear(self, *lines):
        """Each line as the bot's chat delivers it."""
        for line in lines:
            self.event("on ^*:CHAT:*: {", line)

    def type(self, line):
        """A line typed in @DCCore."""
        self.event("on *:INPUT:@DCCore: {", line)

    @property
    def state(self):
        return self.table("dccore.live").get("state", "")

    def sent(self):
        return [args for head, args in self.calls if head in ("dccore.send", "dccore.sendpass", "msg")]

    def said(self):
        return [args for head, args in self.calls if head == "dccore.sys"]

    def structured(self):
        return [args for head, args in self.calls if head == "dccore.structured"]


class ALoginLineBeforeThePromptIsIgnored(unittest.TestCase):

    def assertLeft(self, chat):
        """Left as a chat whose peer check said no: closed, not redialled."""
        self.assertEqual(chat.state, "closed")
        self.assertIn(IGNORED, " ".join(chat.said()))
        self.assertIn(("window", "-c =" + BOT), chat.calls)
        self.assertEqual(chat.table("dccore")["wantopen"], "0")
        self.assertIn(("timer", "timerdccoreRetry off"), chat.calls)
        self.assertNotIn("Logged in", " ".join(chat.said()))

    def test_as_the_first_line_of_a_chat(self):
        for byhand in (0, 1):
            for token in (TOKEN, None):
                with self.subTest(byhand=byhand, token=token):
                    chat = Chat(token=token, byhand=byhand)
                    chat.hear(LOGIN)
                    self.assertLeft(chat)
                    self.assertEqual(chat.sent(), [])

    def test_after_a_banner_with_no_prompt(self):
        chat = Chat()
        chat.hear(BANNER, "", LOGIN)
        self.assertLeft(chat)
        self.assertEqual(chat.sent(), [])

    def test_no_dccore_line_is_read_after_it(self):
        chat = Chat()
        chat.hear(LOGIN, "DCCORE CHATHIST 1 1", "DCCORE LOCKED", "DCCORE CHAT #example SomeNick hi")
        self.assertNotEqual(chat.state, "in")
        self.assertEqual(chat.structured(), [])
        self.assertEqual(chat.sent(), [])

    def test_in_every_state_but_auth_password_and_in(self):
        for state in NOT_ASKED:
            with self.subTest(state=state):
                chat = Chat(state=state)
                chat.call("dccore.line", LOGIN.split(" "))
                self.assertLeft(chat)
                self.assertEqual(chat.sent(), [])

    def test_after_a_peer_check_that_said_no(self):
        """The token is withheld and the chat left; a login line in it after
        that still does not log in."""
        chat = Chat(host=ELSEWHERE)
        chat.hear(BANNER, PROMPT)
        self.assertEqual(chat.state, "closed")
        self.assertEqual(chat.sent(), [])
        chat.hear(LOGIN)
        self.assertEqual(chat.state, "closed")
        self.assertIn(IGNORED, " ".join(chat.said()))
        chat.hear("DCCORE STATUS x")
        self.assertNotEqual(chat.state, "in")
        self.assertEqual(chat.sent(), [])
        self.assertEqual(chat.structured(), [])

    def test_the_line_is_not_shown_as_the_bots(self):
        chat = Chat()
        chat.hear(LOGIN)
        self.assertNotIn(("dccore.echo", LOGIN), chat.calls)


class ALoginAfterThePromptLogsIn(unittest.TestCase):

    def assertIn_(self, chat):
        self.assertEqual(chat.state, "in")
        self.assertIn("hello dccore.mrc 1.20.0", chat.sent())
        self.assertNotIn(IGNORED, " ".join(chat.said()))
        self.assertEqual(chat.table("dccore")["wantopen"], "1")

    def test_the_stored_token_after_the_peer_check(self):
        chat = Chat()
        chat.hear(BANNER, PROMPT)
        self.assertEqual(chat.state, "auth")
        self.assertEqual(chat.sent(), [TOKEN])
        chat.hear("", LOGIN, 'For help type "help"', "DCCORE STATUS x")
        self.assertIn_(chat)
        self.assertEqual(chat.structured(), ["STATUS x"])

    def test_a_password_typed_in_a_chat_opened_by_hand(self):
        """/dccore connect with no token: the operator's own chat asks with
        no peer check, and the typed password logs in."""
        chat = Chat(token=None, bothost=None, host="", byhand=1)
        chat.hear(BANNER, PROMPT)
        self.assertEqual(chat.state, "password")
        self.assertEqual(chat.sent(), [])
        chat.type("example-password")
        self.assertEqual(chat.state, "auth")
        self.assertEqual(chat.sent(), ["example-password"])
        chat.hear(LOGIN)
        self.assertIn_(chat)

    def test_a_password_typed_in_a_chat_the_script_dialled(self):
        chat = Chat(token=None)
        chat.hear(BANNER, PROMPT)
        self.assertEqual(chat.state, "password")
        self.assertEqual(chat.table("dccore.live")["vetted"], "1")
        chat.type("example-password")
        chat.hear(LOGIN)
        self.assertIn_(chat)

    def test_a_refused_token_then_a_typed_password(self):
        chat = Chat()
        chat.hear(BANNER, PROMPT, "Incorrect Password.")
        self.assertEqual(chat.state, "password")
        chat.hear(PROMPT)
        self.assertEqual(chat.sent(), [TOKEN])
        chat.type("example-password")
        chat.hear(LOGIN)
        self.assertIn_(chat)

    def test_a_pairing_by_hand_sends_its_pair_after_the_login(self):
        chat = Chat(token=None, byhand=1, pairing=1)
        chat.hear(BANNER, PROMPT)
        chat.type("example-password")
        chat.hear(LOGIN)
        self.assertIn_(chat)
        self.assertIn("pair dccore.mrc-0000 1.20.0", chat.sent())

    def test_in_state_auth_or_password(self):
        for state in ("auth", "password"):
            with self.subTest(state=state):
                chat = Chat(state=state)
                chat.call("dccore.line", LOGIN.split(" "))
                self.assertIn_(chat)


class ALoggedInChatIsLeftAlone(unittest.TestCase):

    def test_a_second_login_line_changes_nothing(self):
        chat = Chat()
        chat.hear(BANNER, PROMPT, LOGIN)
        before = (dict(chat.table("dccore.live")), dict(chat.table("dccore")), len(chat.sent()))
        calls = len(chat.calls)
        chat.call("dccore.line", LOGIN.split(" "))
        self.assertEqual(chat.state, "in")
        self.assertEqual((dict(chat.table("dccore.live")), dict(chat.table("dccore")), len(chat.sent())), before)
        self.assertEqual(chat.calls[calls:], [])


class TheStructuredFeedNeedsTheLogin(unittest.TestCase):

    def test_only_state_in_reads_a_dccore_line(self):
        for state in NOT_ASKED + ("auth", "password", "in"):
            with self.subTest(state=state):
                chat = Chat(state=state)
                chat.call("dccore.line", "DCCORE CHATHIST 1 1".split(" "))
                self.assertEqual(chat.structured(), ["CHATHIST 1 1"] if state == "in" else [])


class TheEvaluatorReadsTheseLines(unittest.TestCase):
    """What the scenarios above lean on."""

    def test_iswm_and_halt(self):
        chat = Chat()
        frame = {"args": [], "vars": {"%t": "Session taken over from 192.0.2.1"}}
        self.assertTrue(chat.cond("(Session taken over from * iswm %t)", frame))
        self.assertFalse(chat.cond("(Unknown command: hello* iswm %t)", frame))
        chat.live(state="password")
        chat.type("example-password")
        self.assertEqual(chat.calls[-1], ("halt", ""))
        self.assertEqual(chat.sent(), ["example-password"])

    def test_a_chat_from_another_nick_is_not_read(self):
        chat = Chat(state="auth")
        chat.nick = "SomeoneElseZ"
        chat.hear(LOGIN)
        self.assertEqual(chat.state, "auth")
        self.assertEqual(chat.calls, [])


class TheFragments(unittest.TestCase):

    def test_they_roll(self):
        _fragments, problems = roll_changelog.load(REPO_ROOT)
        self.assertEqual([p for p in problems if p.startswith("585-login-after-prompt")], [])


if __name__ == "__main__":
    unittest.main()
