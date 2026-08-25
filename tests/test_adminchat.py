"""Tests for the DCC CHAT admin console gate (phase 1).

This module is the whole security surface of the admin console, so the tests are
about two questions and little else: who gets in, and what does a stranger learn.

The end-to-end cases use a real loopback socket, because the thing being verified
is the actual connect-out path - the bot dials the client's listener, exactly as
iroffer's non-passive branch does. A mock would prove the mock works.
"""

import os
import socket
import sys
import threading
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if os.path.join(REPO_ROOT, "tests") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import adminchat  # noqa: E402
import config  # noqa: E402
import irc  # noqa: E402

ADMIN_LINE = ":FLAC!~flac@FLAC.users.undernet.org PRIVMSG DCCore :\x01DCC CHAT chat 2130706433 55555\x01"
STRANGER_LINE = ":dave!~d@cpe-91-22-33-44.isp.net PRIVMSG DCCore :\x01DCC CHAT chat 2130706433 55555\x01"

PASSWORD = "correct horse battery staple"


def wait_for(predicate, timeout=5.0, interval=0.02):
    """Poll rather than sleep a fixed time, so the suite is neither slow nor flaky."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class HostMatching(unittest.TestCase):
    """The gate. Everything else is depth behind this."""

    def setUp(self):
        adminchat.reset_state_for_tests()
        self.addCleanup(adminchat.reset_state_for_tests)
        config.ADMIN_HOSTMASKS = ["*!*@FLAC.users.undernet.org"]

    def test_the_configured_host_matches(self):
        self.assertTrue(adminchat.is_admin_host(ADMIN_LINE))

    def test_a_stranger_does_not_match(self):
        self.assertFalse(adminchat.is_admin_host(STRANGER_LINE))

    def test_a_bare_host_pattern_works_too(self):
        """He may write it either way; both must mean the same thing."""
        config.ADMIN_HOSTMASKS = ["FLAC.users.undernet.org"]
        self.assertTrue(adminchat.is_admin_host(ADMIN_LINE))

    def test_the_ident_is_ignored(self):
        """The point of the whole design: only the host is server-issued.

        A pattern that appears to pin the ident must not actually pin it - if it
        did, the gate would break the moment his client's ident setting changed,
        while granting nothing, because anyone can set their ident to 'flac'.
        """
        config.ADMIN_HOSTMASKS = ["*!flac@FLAC.users.undernet.org"]
        for ident in ("~flac", "flac", "~anything", "x"):
            line = f":FLAC!{ident}@FLAC.users.undernet.org PRIVMSG DCCore :hi"
            with self.subTest(ident=ident):
                self.assertTrue(adminchat.is_admin_host(line))

    def test_the_nick_is_ignored(self):
        """Nick theft is the attack this replaces, so the nick cannot be the check."""
        line = ":SomeoneElse!~x@FLAC.users.undernet.org PRIVMSG DCCore :hi"
        self.assertTrue(adminchat.is_admin_host(line),
                        "the host is the proof; whoever holds it holds the account")

    def test_taking_the_admin_nick_from_another_host_gets_nothing(self):
        """The exact scenario the console exists to close."""
        line = ":FLAC!~flac@cpe-91-22-33-44.isp.net PRIVMSG DCCore :hi"
        self.assertFalse(adminchat.is_admin_host(line))

    def test_matching_is_case_insensitive(self):
        line = ":FLAC!~f@flac.USERS.undernet.ORG PRIVMSG DCCore :hi"
        self.assertTrue(adminchat.is_admin_host(line))

    def test_a_wildcard_account_pattern_works(self):
        config.ADMIN_HOSTMASKS = ["*.users.undernet.org"]
        self.assertTrue(adminchat.is_admin_host(ADMIN_LINE))
        self.assertFalse(adminchat.is_admin_host(STRANGER_LINE))

    def test_an_empty_list_admits_nobody(self):
        """Fail closed: an unconfigured console must be an off console."""
        for empty in ([], "", None):
            with self.subTest(empty=empty):
                config.ADMIN_HOSTMASKS = empty
                self.assertFalse(adminchat.is_admin_host(ADMIN_LINE))

    def test_an_all_wildcard_pattern_is_refused(self):
        """'*' would admit the entire network and make the gate decorative."""
        for broad in ("*", "*!*@*", "**"):
            with self.subTest(broad=broad):
                config.ADMIN_HOSTMASKS = [broad]
                self.assertFalse(adminchat.is_admin_host(ADMIN_LINE))

    def test_a_hostmask_in_the_message_body_cannot_forge_it(self):
        """Anchoring, for the same reason event_source_nick is anchored."""
        line = (":dave!~d@isp.net PRIVMSG DCCore :"
                "look at this FLAC!~flac@FLAC.users.undernet.org")
        self.assertFalse(adminchat.is_admin_host(line))

    def test_a_prefixless_line_cannot_donate_a_host_from_its_body(self):
        """The case that actually separates anchoring from searching.

        The test above passes even against a searching implementation, because a
        PRIVMSG's own prefix contains the first "@" on the line and is found
        first. Only a line with NO user prefix tells the two apart - and there a
        searching implementation hands back a host the line is merely ABOUT.

        Nothing reaches is_admin_host this way today: irc.py only calls it from
        inside the PRIVMSG branch, whose regex already demands a nick!user@host
        prefix. This pins the helper's contract rather than the current caller's
        good manners, exactly as the sibling test does for event_source_nick.
        """
        for line in (":irc.undernet.org 372 DCCore :please mail admin@FLAC.users.undernet.org",
                     "NOTICE AUTH :checking ident@FLAC.users.undernet.org"):
            with self.subTest(line=line):
                self.assertIsNone(adminchat.source_host(line))
                self.assertFalse(adminchat.is_admin_host(line))

    def test_a_server_line_has_no_source_host(self):
        self.assertIsNone(adminchat.source_host(":irc.undernet.org 001 DCCore :Welcome"))
        self.assertFalse(adminchat.is_admin_host(":irc.undernet.org 001 DCCore :Welcome"))

    def test_irc_helper_agrees_with_the_console(self):
        """irc.event_source_host is what feeds this; they must not drift."""
        self.assertEqual(irc.event_source_host(ADMIN_LINE),
                         adminchat.source_host(ADMIN_LINE))
        self.assertEqual(irc.event_source_host(ADMIN_LINE), "flac.users.undernet.org")

    def test_irc_helper_is_anchored_too(self):
        """Same contract, same failure mode, so the same test on both sides."""
        for line in (":irc.undernet.org 372 DCCore :please mail admin@FLAC.users.undernet.org",
                     "PING :cookie",
                     ":irc.undernet.org 001 DCCore :Welcome"):
            with self.subTest(line=line):
                self.assertIsNone(irc.event_source_host(line))


class PasswordHandling(unittest.TestCase):

    def test_hash_and_verify_round_trip(self):
        stored = adminchat.make_password_hash(PASSWORD, iterations=1000)
        self.assertTrue(adminchat.verify_password(stored, PASSWORD))

    def test_a_wrong_password_is_refused(self):
        stored = adminchat.make_password_hash(PASSWORD, iterations=1000)
        self.assertFalse(adminchat.verify_password(stored, PASSWORD + "x"))
        self.assertFalse(adminchat.verify_password(stored, ""))

    def test_the_hash_does_not_contain_the_password(self):
        stored = adminchat.make_password_hash(PASSWORD, iterations=1000)
        self.assertNotIn(PASSWORD, stored)

    def test_the_salt_makes_two_hashes_of_one_password_differ(self):
        a = adminchat.make_password_hash(PASSWORD, iterations=1000)
        b = adminchat.make_password_hash(PASSWORD, iterations=1000)
        self.assertNotEqual(a, b)
        self.assertTrue(adminchat.verify_password(a, PASSWORD))
        self.assertTrue(adminchat.verify_password(b, PASSWORD))

    def test_an_unset_password_refuses_everyone(self):
        """Fail closed. A console with no password must not be an open console."""
        for stored in ("", None):
            with self.subTest(stored=stored):
                self.assertFalse(adminchat.verify_password(stored, PASSWORD))
                self.assertFalse(adminchat.verify_password(stored, ""))

    def test_a_malformed_stored_value_refuses_rather_than_raising(self):
        """A mistyped local_config must lock the console, not crash the daemon."""
        for junk in ("garbage", "pbkdf2_sha256$notanint$aa$bb", "pbkdf2_sha256$1$zz$zz",
                     "bcrypt$1$aa$bb", "$$$", "pbkdf2_sha256$1000$aa"):
            with self.subTest(junk=junk):
                self.assertFalse(adminchat.verify_password(junk, PASSWORD))

    def test_verification_is_constant_time(self):
        source = open(os.path.join(REPO_ROOT, "adminchat.py"), encoding="utf-8").read()
        self.assertTrue("hmac.compare_digest" in source,
                        "a plain == on the digest leaks the password through timing")


class OfferParsing(unittest.TestCase):

    def test_a_normal_offer_parses(self):
        self.assertEqual(adminchat.parse_offer("DCC CHAT chat 2130706433 55555"),
                         ("127.0.0.1", 55555))

    def test_the_passive_form_is_refused_for_now(self):
        """Port 0 means 'you listen instead', which phase 1 does not implement."""
        self.assertIsNone(adminchat.parse_offer("DCC CHAT chat 2130706433 0"))

    def test_a_privileged_port_is_refused(self):
        self.assertIsNone(adminchat.parse_offer("DCC CHAT chat 2130706433 22"))

    def test_junk_is_refused_rather_than_raising(self):
        for junk in ("", "DCC CHAT", "DCC SEND file 1 2", "DCC CHAT chat x y",
                     "DCC CHAT chat 999999999999 55555", "DCC CHAT chat -1 55555",
                     "DCC CHAT chat 2130706433 99999"):
            with self.subTest(junk=junk):
                self.assertIsNone(adminchat.parse_offer(junk))


class BadIpTracking(unittest.TestCase):
    """Per-session attempt counting is useless alone - an attacker reconnects."""

    def setUp(self):
        adminchat.reset_state_for_tests()
        self.addCleanup(adminchat.reset_state_for_tests)

    def test_an_address_blocks_after_the_attempt_limit(self):
        for _ in range(adminchat.MAX_PASSWORD_ATTEMPTS):
            self.assertFalse(adminchat.is_bad_ip("10.0.0.9"))
            adminchat.note_bad_ip("10.0.0.9")
        self.assertTrue(adminchat.is_bad_ip("10.0.0.9"))

    def test_the_block_expires(self):
        for _ in range(adminchat.MAX_PASSWORD_ATTEMPTS):
            adminchat.note_bad_ip("10.0.0.9")
        self.assertTrue(adminchat.is_bad_ip("10.0.0.9"))
        adminchat._bad_ips["10.0.0.9"][1] = time.time() - 1
        self.assertFalse(adminchat.is_bad_ip("10.0.0.9"),
                         "a typo must not lock the operator out permanently")

    def test_a_success_clears_the_record(self):
        adminchat.note_bad_ip("10.0.0.9")
        adminchat.clear_bad_ip("10.0.0.9")
        self.assertNotIn("10.0.0.9", adminchat._bad_ips)

    def test_addresses_are_tracked_separately(self):
        for _ in range(adminchat.MAX_PASSWORD_ATTEMPTS):
            adminchat.note_bad_ip("10.0.0.9")
        self.assertFalse(adminchat.is_bad_ip("10.0.0.10"))


class TheRequestGate(unittest.TestCase):
    """handle_dcc_chat is called from the IRC read loop and must never block."""

    def setUp(self):
        adminchat.reset_state_for_tests()
        self.addCleanup(adminchat.reset_state_for_tests)
        config.ADMIN_HOSTMASKS = ["*!*@FLAC.users.undernet.org"]
        config.ADMIN_PASSWORD_HASH = adminchat.make_password_hash(PASSWORD, iterations=1000)

        self.dialled = []
        self._real = adminchat._connect_and_serve
        adminchat._connect_and_serve = lambda *a: self.dialled.append(a)
        self.addCleanup(lambda: setattr(adminchat, "_connect_and_serve", self._real))

    def test_a_stranger_gets_absolutely_nothing(self):
        """The deliberate difference from iroffer, which banners at anyone.

        No connection, no reply on the IRC socket, no debug line. A debug line
        would tell whoever watches the debug channel that a mask was wrong, and
        would let a stranger fill the log for free.
        """
        sent = []

        class Recorder:
            def send(self, payload):
                sent.append(payload)
            sendall = send

        self.assertFalse(adminchat.handle_dcc_chat(Recorder(), STRANGER_LINE, "dave",
                                                   "DCC CHAT chat 2130706433 55555"))
        self.assertEqual(self.dialled, [], "must not dial a stranger's listener")
        self.assertEqual(sent, [], "must not answer a stranger at all")

    def test_an_authorised_host_is_dialled(self):
        self.assertTrue(adminchat.handle_dcc_chat(None, ADMIN_LINE, "FLAC",
                                                  "DCC CHAT chat 2130706433 55555"))
        self.assertEqual(len(self.dialled), 1)
        nick, host, ip, port = self.dialled[0]
        self.assertEqual((host, ip, port), ("flac.users.undernet.org", "127.0.0.1", 55555))

    def test_no_password_configured_means_no_console(self):
        """Refuse rather than open an unauthenticated console."""
        config.ADMIN_PASSWORD_HASH = ""
        self.assertFalse(adminchat.handle_dcc_chat(None, ADMIN_LINE, "FLAC",
                                                   "DCC CHAT chat 2130706433 55555"))
        self.assertEqual(self.dialled, [])

    def test_a_blocked_address_is_refused_even_from_the_right_host(self):
        for _ in range(adminchat.MAX_PASSWORD_ATTEMPTS):
            adminchat.note_bad_ip("127.0.0.1")
        self.assertFalse(adminchat.handle_dcc_chat(None, ADMIN_LINE, "FLAC",
                                                   "DCC CHAT chat 2130706433 55555"))
        self.assertEqual(self.dialled, [])

    def test_a_malformed_offer_is_refused(self):
        self.assertFalse(adminchat.handle_dcc_chat(None, ADMIN_LINE, "FLAC",
                                                   "DCC CHAT chat nonsense"))
        self.assertEqual(self.dialled, [])


class OutboxNeverBlocksTheCaller(unittest.TestCase):
    """The failure that would take the daemon off the server.

    send_debug() is called from the IRC read loop. If a queued log line could
    block on a stalled admin client, a sleeping laptop would freeze the network
    thread. Session.send must be a pure append.
    """

    def setUp(self):
        adminchat.reset_state_for_tests()
        self.addCleanup(adminchat.reset_state_for_tests)

    def make_session(self, sock=None):
        return adminchat.Session(sock or socket.socket(), "127.0.0.1", "FLAC", "h")

    def test_send_does_not_touch_the_socket(self):
        class Exploding:
            def sendall(self, payload):
                raise AssertionError("send() wrote the socket on the caller's thread")
            def close(self):
                pass

        session = self.make_session(Exploding())
        for i in range(50):
            session.send(f"line {i}")

    def test_the_outbox_is_bounded_and_drops_oldest(self):
        session = self.make_session()
        self.addCleanup(session.close, None)
        for i in range(adminchat.OUTBOX_MAX + 250):
            session.send(f"line {i}")
        self.assertEqual(len(session._outbox), adminchat.OUTBOX_MAX,
                         "a dead client must drop lines, not grow without limit")
        self.assertEqual(session.dropped, 250)
        self.assertNotIn("line 0", session._outbox)

    def test_sending_to_a_closed_session_is_a_no_op(self):
        session = self.make_session()
        session.close(announce_text=None)
        session.send("after close")
        self.assertEqual(len(session._outbox), 0)

    def test_send_is_fast_even_when_the_writer_is_stuck(self):
        """Measured, not assumed: 500 lines against a wedged socket."""
        blocked = threading.Event()

        class Wedged:
            def sendall(self, payload):
                blocked.wait(30)
            def close(self):
                pass
            def shutdown(self, how):
                pass

        session = self.make_session(Wedged())
        session.start_writer()
        self.addCleanup(lambda: (blocked.set(), session.close(None)))

        started = time.time()
        for i in range(500):
            session.send(f"line {i}")
        self.assertLess(time.time() - started, 1.0,
                        "queueing must not wait on the socket")


class SessionExpiry(unittest.TestCase):

    def setUp(self):
        adminchat.reset_state_for_tests()
        self.addCleanup(adminchat.reset_state_for_tests)

    def session(self):
        s = adminchat.Session(socket.socket(), "127.0.0.1", "FLAC", "h")
        self.addCleanup(s.close, None)
        return s

    def test_an_unauthenticated_session_expires_on_the_short_clock(self):
        s = self.session()
        self.assertFalse(s.expired())
        s.opened_at = time.time() - adminchat.AUTH_TIMEOUT - 1
        self.assertTrue(s.expired())

    def test_an_authenticated_session_uses_the_idle_clock(self):
        s = self.session()
        s.authenticated = True
        s.opened_at = time.time() - adminchat.AUTH_TIMEOUT - 1
        s.last_activity = time.time()
        self.assertFalse(s.expired(), "activity, not age, keeps a live session open")
        s.last_activity = time.time() - adminchat.IDLE_TIMEOUT - 1
        self.assertTrue(s.expired())


class EndToEndOverLoopback(unittest.TestCase):
    """The real connect-out path, against a real listening socket.

    This is what the operator's mIRC does: listen, send the offer, wait for the
    bot to dial in. Nothing here is mocked except the clock's patience.
    """

    def setUp(self):
        adminchat.reset_state_for_tests()
        self.addCleanup(adminchat.reset_state_for_tests)
        config.ADMIN_HOSTMASKS = ["*!*@FLAC.users.undernet.org"]
        config.ADMIN_PASSWORD_HASH = adminchat.make_password_hash(PASSWORD, iterations=1000)

        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.addCleanup(self.listener.close)
        self.client = None

    def offer(self):
        return f"DCC CHAT chat 2130706433 {self.port}"

    def dial(self):
        """Trigger the bot, then accept its incoming connection like mIRC would."""
        adminchat.handle_dcc_chat(None, ADMIN_LINE, "FLAC", self.offer())
        self.listener.settimeout(5.0)
        self.client, _ = self.listener.accept()
        self.client.settimeout(5.0)
        self.addCleanup(self.client.close)
        return self.client

    def read_until(self, needle, timeout=5.0):
        buffer = ""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = self.client.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buffer += chunk.decode("utf-8", "replace")
            if needle in buffer:
                return buffer
        return buffer

    def test_the_banner_and_prompt_arrive(self):
        self.dial()
        text = self.read_until("Enter Your Password:")
        self.assertIn("Welcome to", text)
        self.assertIn(config.SCRIPT_VERSION, text)
        self.assertIn("Enter Your Password:", text)

    def test_the_banner_does_not_leak_the_password_or_the_mask(self):
        self.dial()
        text = self.read_until("Enter Your Password:")
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(config.ADMIN_PASSWORD_HASH, text)

    def test_the_right_password_opens_the_console(self):
        self.dial()
        self.read_until("Enter Your Password:")
        self.client.sendall((PASSWORD + "\n").encode())
        text = self.read_until("For help type")
        self.assertIn("Entering DCC Chat Admin Interface", text)
        self.assertTrue(wait_for(lambda: adminchat.active_session() is not None))

    def peer_closed(self, timeout=10.0):
        """True once the bot has actually hung up on us."""
        self.client.settimeout(timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.client.recv(4096) == b"":
                    return True
            except socket.timeout:
                return False
            except OSError:
                return True
        return False

    def test_a_wrong_password_reprompts(self):
        self.dial()
        self.read_until("Enter Your Password:")
        self.client.sendall(b"wrong\n")
        text = self.read_until("Enter Your Password:", timeout=8.0)
        self.assertIn("Incorrect Password.", text)
        self.assertIsNone(adminchat.active_session())

    def test_the_attempt_limit_actually_hangs_up(self):
        """Assert the disconnect, not just the message.

        Reprompting forever and disconnecting look identical if the test only
        checks that "Incorrect Password." arrived and that nobody got in - both
        are true either way. The security property is that the socket closes, so
        that is what gets asserted.
        """
        self.dial()
        self.read_until("Enter Your Password:")
        for _ in range(adminchat.MAX_PASSWORD_ATTEMPTS):
            self.client.sendall(b"wrong\n")
        self.assertTrue(self.peer_closed(),
                        "the console must hang up after the attempt limit, not reprompt forever")
        self.assertTrue(wait_for(lambda: adminchat.is_bad_ip("127.0.0.1"), timeout=8.0),
                        "failed attempts must be charged to the address")
        self.assertIsNone(adminchat.active_session())

    def test_commands_are_refused_before_authentication(self):
        """The console must not answer 'help' to an unauthenticated socket."""
        self.dial()
        self.read_until("Enter Your Password:")
        self.client.sendall(b"help\n")
        text = self.read_until("Available commands:", timeout=1.5)
        self.assertNotIn("Available commands:", text)

    def test_help_works_once_authenticated(self):
        self.dial()
        self.read_until("Enter Your Password:")
        self.client.sendall((PASSWORD + "\nhelp\n").encode())
        text = self.read_until("close this session")
        self.assertIn("Available commands:", text)

    def test_quit_closes_the_session(self):
        self.dial()
        self.read_until("Enter Your Password:")
        self.client.sendall((PASSWORD + "\n").encode())
        self.read_until("For help type")
        self.client.sendall(b"quit\n")
        self.assertTrue(wait_for(lambda: adminchat.active_session() is None))


class WiringIsInPlace(unittest.TestCase):
    """irc.py must actually route DCC CHAT here, and only from a private message."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "irc.py"), encoding="utf-8") as handle:
            self.source = handle.read()

    def test_irc_hands_dcc_chat_to_the_console(self):
        self.assertTrue("adminchat.handle_dcc_chat(" in self.source)

    def test_the_branch_requires_a_private_message(self):
        """A DCC CHAT offer to a channel is meaningless and anyone can send one."""
        self.assertTrue("target_chan.lower() == config.NICKNAME.lower()" in self.source)

    def test_the_privmsg_parser_keeps_the_hostmask(self):
        self.assertTrue("def event_source_host(" in self.source,
                        "the console cannot work if the host is discarded")

    def test_adminchat_is_not_reloaded_by_rehash(self):
        """importlib.reload would drop a live session's socket on every !rehash."""
        with open(os.path.join(REPO_ROOT, "commands.py"), encoding="utf-8") as handle:
            commands_source = handle.read()
        for line in commands_source.split("\n"):
            if "modules_to_reload" in line and "=" in line:
                self.assertNotIn("adminchat", line,
                                 "reloading adminchat would kill the live console")


if __name__ == "__main__":
    unittest.main()
