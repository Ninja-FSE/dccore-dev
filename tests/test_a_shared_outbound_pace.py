"""One clock for every outbound send, not two that never met.

Reported live (#406): an operator was killed with Excess Flood shortly after
growing their bot to 14 channels. queue_mgr.py's queue_worker slept MSG_DELAY
after every send; announce.py's debug drain slept DEBUG_MSG_DELAY after every
send, on its own thread, unaware the first thread existed. The server only
ever sees the SUM of what both lanes send - on stock settings, up to 2.2
lines/second against a server that kills a client sustaining much more than
about 0.5 - and nothing an operator can see names that sum.

runtime.OutboundPacer replaces both independent timers with one shared clock:
whichever lane sends next, from either thread, reserves the clock for its own
interval before anyone else's next send. The combined rate can therefore
never exceed one interval's worth of traffic, however the two lanes
interleave.
"""

import contextlib
import io
import threading
import time
import unittest

from tests.support import DCCoreTestCase

import announce
import defaults as config
import queue_mgr
import runtime


class OutboundPacerTests(unittest.TestCase):
    """The clock itself, with no daemon code involved."""

    def setUp(self):
        self.pacer = runtime.OutboundPacer()

    def test_the_first_call_does_not_wait(self):
        started = time.monotonic()
        self.pacer.wait_for_slot(0.2)
        self.assertLess(time.monotonic() - started, 0.05,
                        "an unclaimed pacer must not delay the first sender")

    def test_a_second_call_waits_out_the_interval(self):
        self.pacer.wait_for_slot(0.15)
        started = time.monotonic()
        self.pacer.wait_for_slot(0.15)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.13,
                                "the second reservation must wait for the first's slot")

    def test_a_slot_is_reserved_even_if_the_caller_never_sends(self):
        """Both queue_mgr.py and announce.py call wait_for_slot() BEFORE
        attempting the socket write, so a failed send still costs its slot
        rather than letting a broken pipe retry in a tight loop. Modelled
        here simply as: nothing about the call depends on what happens after
        it returns."""
        self.pacer.wait_for_slot(0.1)
        started = time.monotonic()
        self.pacer.wait_for_slot(0.1)
        self.assertGreaterEqual(time.monotonic() - started, 0.08)

    def test_two_threads_are_serialised_onto_the_same_clock(self):
        """The property the whole fix rests on: it does not matter which
        thread asks, the clock is shared - this is what makes two lanes on
        two threads behave as one budget instead of two that sum."""
        interval = 0.05
        calls_per_thread = 5
        timestamps = []
        lock = threading.Lock()

        def hammer():
            for _ in range(calls_per_thread):
                self.pacer.wait_for_slot(interval)
                with lock:
                    timestamps.append(time.monotonic())

        threads = [threading.Thread(target=hammer) for _ in range(2)]
        started = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        total_slots = calls_per_thread * len(threads)
        self.assertEqual(len(timestamps), total_slots)
        elapsed = time.monotonic() - started
        # If the two threads paced independently instead of sharing the
        # clock - the bug this fixes - each would only wait out its OWN
        # history and the whole thing would finish in about half this time.
        self.assertGreaterEqual(elapsed, (total_slots - 1) * interval * 0.8)


class _SleepShim:
    """Replacement for a module's `time`, so a worker loop spins fast in the
    test and can be told to stop.

    Caps every sleep and raises SystemExit once stopped. SystemExit is not
    an Exception, so it escapes both queue_worker's and the debug drain's
    catch-all handlers and ends the thread - neither loop has any other way
    to stop, by design, in production.

    Does NOT intercept runtime.OutboundPacer.wait_for_slot(): that sleeps on
    its own `time` import in runtime.py, not on the caller's. Kept genuinely
    short in these tests (MSG_DELAY/DEBUG_MSG_DELAY in the tens of
    milliseconds) so the gap between a stop request and the thread actually
    exiting stays well inside the join() timeout below.
    """

    def __init__(self, cap=0.02):
        self.cap = cap
        self.stopped = threading.Event()

    def sleep(self, seconds):
        if self.stopped.is_set():
            raise SystemExit
        time.sleep(min(float(seconds), self.cap))
        if self.stopped.is_set():
            raise SystemExit

    def time(self):
        return time.time()


class TimestampedSocket:
    """Stands in for the live IRC socket and remembers WHEN each write
    landed, not just what - the property under test is timing, not content.
    """

    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append((time.monotonic(), payload))
        return len(payload)

    def sendall(self, payload):
        self.sent.append((time.monotonic(), payload))

    def close(self):
        pass


class TheCombinedOutboundRateIsCapped(DCCoreTestCase):
    """End to end: queue_mgr's worker and announce's debug drain, running as
    the real threads they are in production, sharing one socket and one
    runtime.outbound_pacer."""

    def setUp(self):
        super().setUp()
        config.MSG_DELAY = 0.05
        config.DEBUG_MSG_DELAY = 0.01  # deliberately far below MSG_DELAY
        config.vip_queue = []
        config.send_queue = {}
        config.bot_joined_channel = True
        self.oserve.bot_joined_channel = True

        # A fresh clock per test - the real one is a process-wide singleton
        # and other tests must not see this test's tiny MSG_DELAY.
        runtime.outbound_pacer = runtime.OutboundPacer()

        self.sock = TimestampedSocket()
        self.oserve.irc_connection = self.sock

        self._real_queue_time = queue_mgr.time
        self._real_announce_time = announce.time
        self.queue_shim = _SleepShim()
        self.debug_shim = _SleepShim()
        queue_mgr.time = self.queue_shim
        announce.time = self.debug_shim

        self.queue_worker_thread = None
        self.debug_drain_thread = None

    def tearDown(self):
        self.queue_shim.stopped.set()
        self.debug_shim.stopped.set()
        if self.queue_worker_thread is not None:
            self.queue_worker_thread.join(timeout=3.0)
        if self.debug_drain_thread is not None:
            self.debug_drain_thread.join(timeout=3.0)
        queue_mgr.time = self._real_queue_time
        announce.time = self._real_announce_time
        runtime.outbound_pacer = runtime.OutboundPacer()
        super().tearDown()

    def start_queue_worker(self):
        def run():
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    queue_mgr.queue_worker()
                except SystemExit:
                    pass
        self.queue_worker_thread = threading.Thread(target=run, daemon=True)
        self.queue_worker_thread.start()

    def start_debug_drain(self):
        # Calling _debug_drain_worker() directly, rather than going through
        # send_debug()/_ensure_debug_drain(), sidesteps that pair's own
        # process-wide "already started?" latch entirely - this test wants
        # its OWN drain, on its OWN shimmed clock, not whichever one an
        # earlier test in the suite may have left running.
        announce._debug_drain_id = "test-drain"

        def run():
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    announce._debug_drain_worker("test-drain")
                except SystemExit:
                    pass
        self.debug_drain_thread = threading.Thread(target=run, daemon=True)
        self.debug_drain_thread.start()

    def test_the_combined_rate_never_exceeds_one_shared_slot_per_interval(self):
        for i in range(4):
            config.send_queue.setdefault("dave", []).append(
                f"PRIVMSG dave :queue line {i}\r\n")
        for i in range(4):
            announce._debug_queue.append(f"PRIVMSG #debug :debug line {i}\r\n")

        started = time.monotonic()
        self.start_queue_worker()
        self.start_debug_drain()

        deadline = time.time() + 3.0
        while time.time() < deadline and len(self.sock.sent) < 8:
            time.sleep(0.02)

        self.assertEqual(len(self.sock.sent), 8,
                         "all 4 queue lines and 4 debug lines must eventually be delivered")

        elapsed = self.sock.sent[-1][0] - started
        # 8 sends sharing ONE clock need at least 7 slots at the larger of
        # the two configured delays - here that resolves to MSG_DELAY for
        # both lanes, about 0.35s total. Two INDEPENDENT clocks - the bug
        # this fixes - would let the fast debug lane and the slower queue
        # lane run in parallel threads and finish in about 0.2s: whichever
        # lane is slower on its own, not the sum of both lanes' reservations.
        self.assertGreaterEqual(
            elapsed, 7 * config.MSG_DELAY * 0.8,
            "the two lanes must share one clock, not pace independently")


if __name__ == "__main__":
    unittest.main()
