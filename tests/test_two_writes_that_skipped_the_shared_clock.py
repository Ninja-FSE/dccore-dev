"""Two outbound lines went straight onto the IRC socket.

Every other thing the bot says waits for a slot on `runtime.outbound_pacer` -
the shared clock that exists because this bot has been disconnected with
"Excess Flood" in production. These two did not:

  * `commands.handle_ping_request()`, the `!ping` latency probe, which is
    dispatched in the ORDINARY-USER branch of the read loop and so is
    reachable by anybody in the channel (#425);
  * the `DCC ACCEPT` reply that answers a peer's resume request (#453).

The per-user flood limiter cannot cover the first one. It stops one person
asking repeatedly; it has nothing to say about ten different people asking
once each, and the server does not meter users - it meters this connection.

Neither fix moves either line into the round-robin queue. A latency probe and
a resume handshake are both things somebody is waiting on, so they stay
immediate; what changes is that they now take a slot on the same clock instead
of ignoring it.
"""

import io
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import commands  # noqa: E402
import defaults as config  # noqa: E402
import runtime  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


class RecordingSocket:
    """Every line the code under test writes to the socket."""

    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)
        return len(payload)


class CountingPacer:
    """A stand-in for runtime.outbound_pacer that never sleeps.

    Records how many slots were asked for, and in what order relative to the
    writes - which is the property at issue, not the timing.
    """

    def __init__(self, log):
        self.log = log

    def wait_for_slot(self, min_interval):
        self.log.append(("slot", min_interval))


class PacedTestCase(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.log = []
        self.sock = RecordingSocket()
        self._real_pacer = runtime.outbound_pacer
        runtime.outbound_pacer = CountingPacer(self.log)
        config.MSG_DELAY = 2
        # The probe's cooldown is process-wide by design, so a leftover from
        # one test would silence the next one.
        config.ping_state.clear()

    def tearDown(self):
        runtime.outbound_pacer = self._real_pacer
        config.ping_state.clear()
        super().tearDown()


class TheLatencyProbeTakesItsTurn(PacedTestCase):

    def test_a_probe_goes_out_when_somebody_asks(self):
        self.assertTrue(commands.handle_ping_request(self.sock, "SomeUser", "#chan"))

        self.assertEqual(len(self.sock.sent), 1)

    def test_it_is_a_real_ping_line(self):
        """A guard on the fix itself: the escape in this literal was written
        wrong once, and b"...\\\\r\\\\n" is two backslashes on the wire rather
        than a line ending - a malformed line, sent to the server, every
        time."""
        commands.handle_ping_request(self.sock, "SomeUser", "#chan")

        self.assertTrue(self.sock.sent[0].endswith(b"\r\n"), self.sock.sent[0])
        self.assertTrue(self.sock.sent[0].startswith(b"PING :"))

    def test_it_asks_for_a_slot_before_it_writes(self):
        commands.handle_ping_request(self.sock, "SomeUser", "#chan")

        self.assertEqual(self.log, [("slot", config.MSG_DELAY)])

    def test_a_second_ask_inside_the_cooldown_sends_nothing(self):
        commands.handle_ping_request(self.sock, "SomeUser", "#chan")

        self.assertFalse(commands.handle_ping_request(self.sock, "SomeUser", "#chan"))
        self.assertEqual(len(self.sock.sent), 1)

    def test_ten_different_people_asking_at_once_send_one_probe(self):
        """The case the per-user flood limiter is structurally unable to
        cover, and the one that puts ten lines on the wire at once."""
        results = [commands.handle_ping_request(self.sock, "User%d" % n, "#chan")
                   for n in range(10)]

        self.assertEqual(results.count(True), 1)
        self.assertEqual(len(self.sock.sent), 1)

    def test_the_cooldown_is_not_a_permanent_mute(self):
        commands.handle_ping_request(self.sock, "SomeUser", "#chan")
        # Far enough back that the next ask is a fresh measurement.
        config.ping_state["last_sent"] -= commands.PING_COOLDOWN_SECONDS + 1

        self.assertTrue(commands.handle_ping_request(self.sock, "Another", "#chan"))

    def test_a_refused_ask_does_not_disturb_the_measurement_in_flight(self):
        """The second fault in the old version: a second request overwrote
        ping_start_time, so two people asking together produced two PINGs and
        one meaningless answer. Now the refused ask leaves the first one's
        timer alone."""
        commands.handle_ping_request(self.sock, "SomeUser", "#chan")
        started = config.ping_start_time

        commands.handle_ping_request(self.sock, "Another", "#chan")

        self.assertEqual(config.ping_start_time, started)
        self.assertEqual(config.ping_triggered_by, "SomeUser")

    def test_a_socket_that_refuses_the_write_is_reported_not_raised(self):
        """The read loop calls this on a thread of its own; an exception here
        would take that thread down for a latency check."""
        class Broken:
            def send(self, payload):
                raise OSError("connection reset")

        self.assertFalse(commands.handle_ping_request(Broken(), "SomeUser", "#chan"))


class TheResumeHandshakeTakesItsTurn(unittest.TestCase):
    """dcc.py's DCC ACCEPT. Read from the source: reaching the real function
    means a peer, a socket and a partly-transferred file, and the property is
    about WHICH call comes first rather than about any of that."""

    @staticmethod
    def accept_block():
        with io.open(os.path.join(REPO_ROOT, "dcc.py"), encoding="utf-8") as f:
            body = f.read()
        block = body.split("DCC ACCEPT ", 1)[1]
        return block.split("[DCC-RESUME] Could not answer", 1)[0]

    def test_the_accept_waits_for_a_slot_before_the_socket_write(self):
        block = self.accept_block()

        self.assertIn("runtime.outbound_pacer.wait_for_slot(config.MSG_DELAY)",
                      block)

    def test_the_slot_is_taken_before_the_write_and_not_after(self):
        """Ordering is the whole property. A slot taken after the send paces
        the NEXT line and lets this one out unmetered, which is precisely the
        bug."""
        block = self.accept_block()

        self.assertLess(block.index("wait_for_slot"),
                        block.index("irc_sock.send(reply.encode())"))
