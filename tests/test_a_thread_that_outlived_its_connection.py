"""A dispatch thread kept using the socket that died under it.

Every dispatch path in `dcc.py` threads the IRC socket down as a parameter,
and nothing in the module ever looked at the live one: `grep -c irc_connection
dcc.py` returned **zero**, while six other modules read it. So a thread armed
before a reconnect went on using the closed socket afterwards (#430).

That is not a narrow race. `user_queue_timer()` waits up to FIVE MINUTES
holding a socket before it dispatches, and `redispatch_waiting_pack()` and
`delayed_port_retry()` carry the same object.

What it cost the user was worse than the failed send. The handshake raised,
the bare `except` printed and execution fell straight through into
`accept()` - which then blocked for the full socket timeout waiting for a
connection nobody had been invited to make. The timeout was charged to the
queue row as a send failure, and three of those deleted the file from the
user's queue with a notice blaming the send, delivered over a connection that
by then worked again.

Two changes, and the second is the one that matters to whoever was queued:

  * the socket is resolved at the point of USE, which covers every capture
    site at once including the next one somebody writes;
  * an offer that never left this machine is not charged to the row.
"""

import os
import socket
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dcc  # noqa: E402
import defaults as config  # noqa: E402

from tests.support import DCCoreTestCase, install_fake_oserve  # noqa: E402


class RaisingSocket:
    """A socket that has been closed under the thread holding it."""

    def __init__(self):
        self.attempts = 0

    def send(self, payload):
        self.attempts += 1
        raise OSError("simulated: the socket was closed by a reconnect")


class WorkingSocket:
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)
        return len(payload)


class WhichSocketIsUsed(DCCoreTestCase):
    """dcc.live_irc_socket()."""

    def setUp(self):
        super().setUp()
        self.oserve = install_fake_oserve()

    def test_the_live_socket_wins_over_the_one_this_thread_was_handed(self):
        """The whole point. A thread that captured a socket before a reconnect
        must send over the one the daemon has NOW."""
        self.oserve.irc_connection = "the socket after reconnecting"

        self.assertEqual(dcc.live_irc_socket("the socket it was handed"),
                         "the socket after reconnecting")

    def test_with_no_live_connection_the_captured_one_is_all_there_is(self):
        """Between connections there is nothing better to offer, and trying
        the old socket is now harmless: the write raises, and the caller
        returns instead of waiting for a guest nobody invited.

        What must NOT happen is preferring the captured socket while a live
        one exists - that is the defect, and the test above it covers that."""
        self.oserve.irc_connection = None

        self.assertEqual(dcc.live_irc_socket("the socket it was handed"),
                         "the socket it was handed")

    def test_with_neither_it_is_none_so_the_caller_holds_the_queue(self):
        self.oserve.irc_connection = None

        self.assertIsNone(dcc.live_irc_socket(None))

    def test_without_the_daemon_loaded_the_captured_socket_is_all_there_is(self):
        """A test or an embedding, rather than a disconnect - a different
        thing, and it must not be read as one."""
        real = sys.modules.pop("oserve", None)
        self.addCleanup(lambda: sys.modules.__setitem__("oserve", real)
                        if real is not None else None)

        self.assertEqual(dcc.live_irc_socket("the socket it was handed"),
                         "the socket it was handed")


class WhenTheOfferCannotBeSent(DCCoreTestCase):
    """Driving the real start_dcc_send(), because the cost of this defect was
    in what it did afterwards rather than in the failed write itself."""

    def setUp(self):
        super().setUp()
        self.oserve = install_fake_oserve()
        # Dialable from outside, and one of the addresses the identifier guard
        # allows to ship. A documentation address is refused earlier by
        # is_offerable_to_strangers(), before this code is reached at all.
        config.MY_IP_OR_DOCK = "1.2.3.4"

        folder = tempfile.mkdtemp()
        self.path = os.path.join(folder, "SomeFile.bin")
        with open(self.path, "wb") as handle:
            handle.write(b"x" * 1024)

        self.row = {"path": self.path, "file": "SomeFile.bin", "send_fails": 0}
        config.dcc_queue["someuser"] = [self.row]
        config.user_processing_lock = {"someuser"}

    def send(self, sock):
        started = time.perf_counter()
        dcc.start_dcc_send(sock, "SomeUser", self.path, "SomeFile.bin",
                           "#chan", self.row)
        return time.perf_counter() - started

    def test_a_handshake_that_failed_does_not_wait_for_a_guest(self):
        """It used to fall through into accept() and block for the full socket
        timeout - about thirty seconds - waiting for a connection nobody had
        been invited to make."""
        self.oserve.irc_connection = RaisingSocket()

        elapsed = self.send(RaisingSocket())

        self.assertLess(elapsed, 10.0,
                        "the send still waits for a peer that was never asked")

    def test_the_user_is_not_charged_for_our_dead_socket(self):
        """The retry budget counts times a user was offered a file and did not
        take it. A handshake this machine could not put on the wire is not one
        of those, and three of them used to delete their file."""
        self.oserve.irc_connection = RaisingSocket()

        self.send(RaisingSocket())

        self.assertEqual(self.row.get("send_fails"), 0)

    def test_the_row_is_still_there_to_retry(self):
        self.oserve.irc_connection = RaisingSocket()

        self.send(RaisingSocket())

        self.assertEqual(len(config.dcc_queue.get("someuser", [])), 1)

    def test_a_disconnected_bot_holds_the_queue_rather_than_spending_it(self):
        """No live socket at all. Nothing about the user's request failed, so
        nothing is charged to it."""
        self.oserve.irc_connection = None

        self.send("the socket this thread captured before the reconnect")

        self.assertEqual(self.row.get("send_fails"), 0)
        self.assertEqual(len(config.dcc_queue.get("someuser", [])), 1)

    def test_the_processing_lock_is_released_either_way(self):
        """Held, it would block every later dispatch for this user - one dead
        socket costing them their queue for the rest of the process."""
        self.oserve.irc_connection = None

        self.send("stale")

        self.assertNotIn("someuser", config.user_processing_lock)

    def test_no_socket_at_all_does_not_read_as_a_failed_handshake(self):
        """Two different faults, and an operator reading the log has to tell
        them apart. Without the explicit check, having no socket at all falls
        into the handshake branch and reports "'NoneType' object has no
        attribute 'send'" - which describes our own code rather than the thing
        that actually happened."""
        import contextlib
        import io as _io

        self.oserve.irc_connection = None
        printed = _io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.send(None)
        log = printed.getvalue()

        self.assertIn("no connection to send the offer over", log)
        self.assertNotIn("Failed to send the handshake", log)

    def test_the_stale_socket_is_not_used_once_a_live_one_exists(self):
        """The defect itself. Resolution happens at the point of use, so the
        object this thread was handed is not touched at all once the daemon
        has a newer one - which is the whole situation a reconnect creates."""
        stale = RaisingSocket()
        live = WorkingSocket()
        self.oserve.irc_connection = live

        self.send(stale)

        self.assertEqual(stale.attempts, 0)
        self.assertTrue(live.sent, "the handshake did not go out on the live "
                                   "socket either")


class TheModuleAsksForTheLiveSocket(unittest.TestCase):
    """The property that made this possible in the first place, asserted over
    the module rather than over the one call site that was wrong."""

    @staticmethod
    def source():
        import io
        with io.open(os.path.join(REPO_ROOT, "dcc.py"), encoding="utf-8") as f:
            return f.read()

    def test_dcc_reads_the_live_connection_at_all(self):
        """It did not, once: six other modules read oserve.irc_connection and
        this one never did, which is what let a closed socket survive here."""
        self.assertIn("irc_connection", self.source())

    def test_the_handshake_returns_instead_of_falling_into_accept(self):
        """Ordering is the property. The failed-handshake branch must leave
        the function before accept() is reached, not print and carry on."""
        source = self.source()
        block = source.split("Failed to send the handshake", 1)[1]
        head = block.split("dcc_sock.accept()", 1)[0]

        self.assertIn("return", head,
                      "a handshake that could not be sent still falls through "
                      "into accept(), which waits for a peer nobody invited")
