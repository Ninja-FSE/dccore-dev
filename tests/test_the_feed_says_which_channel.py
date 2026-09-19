"""#550: "the channel is missing from SEARCH, and empty in SENDING and SENT."

The structured lines carried no channel at all, so a client had nothing to
show. Every event line now has a `<channel>` token straight after the nick
(`-` when the event has none), and each emitter passes the channel it knows:
SEARCH the channel it was typed in, REQUEST and QUEUED the channel of the
request, SENDING the one the send is started for (a request picked up from
the queue later included), SENT and FAIL the channel of the transfer.
RESUMED still has none to give and says `-`.

The line format is tested with the rest of it in
test_a_structured_feed_for_the_admin_chat.py; this file is the wiring: that
each emitter hands the channel to feed_event, read from a real event sink
where a function can be called and from the source where it cannot.
"""

import io
import os
import re
import sys
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import announce  # noqa: E402
import defaults as config  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


def source(name):
    with io.open(os.path.join(REPO_ROOT, name), encoding="utf-8") as handle:
        return handle.read()


class TheEmittersHandTheChannelOn(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.events = []
        real = announce.send_debug
        announce.send_debug = lambda *a, **k: None
        self.addCleanup(setattr, announce, "send_debug", real)
        announce.add_event_sink(self.sink)
        self.addCleanup(announce.remove_event_sink, self.sink)
        self.set_config(CONSOLE_SHOW_SENDS=True, CONSOLE_SHOW_QUEUE=True,
                        ANNOUNCE_TRANSFERS=False, MAX_DCC_SLOTS=3)

    def sink(self, kind, fields, text):
        self.events.append((kind, fields))

    def last(self, kind):
        return [fields for k, fields in self.events if k == kind][-1]

    def test_sent(self):
        announce.send_transfer_complete("#chan", "dave", "A.flac", 1000, time.time() - 5, 200, duration=5.0)

        self.assertEqual(self.last("SENT")["channel"], "#chan")

    def test_sending(self):
        announce.send_dcc_sending_notice("dave", "A.flac", channel="#chan")

        self.assertEqual(self.last("SENDING")["channel"], "#chan")

    def test_sending_without_one_says_none_rather_than_guessing(self):
        announce.send_dcc_sending_notice("dave", "A.flac")

        self.assertIsNone(self.last("SENDING")["channel"])

    def test_queued(self):
        announce.send_dcc_queue_notice("dave", "A.flac", 2, channel="#chan")

        self.assertEqual(self.last("QUEUED")["channel"], "#chan")

    def test_fail(self):
        import dcc
        dcc._report_transfer_failure("dave", "A.flac", "stopped", channel="#chan")

        self.assertEqual(self.last("FAIL")["channel"], "#chan")

    def test_the_search_and_the_requests_read_from_the_source(self):
        """Not callable without a live socket and a list on disk; what matters
        is that the call names the channel variable in scope."""
        self.assertRegex(source("list.py"),
                         r'feed_event\(\s*"SEARCH",[^)]*nick=user, channel=channel,')
        dcc = source("dcc.py")
        self.assertIn('nick=user, channel=target_chan, kind="file"', dcc)
        self.assertIn('nick=user, channel=target_chan, kind="folder"', dcc)


class EverySendPassesTheChannelItStartsFor(unittest.TestCase):
    """A send started later, from the queue, still says where it was asked for:
    the three queue-pickup sites and the direct one all hand the channel that
    goes to start_dcc_send on to the notice."""

    def test_the_four_sending_notices_name_a_channel(self):
        dcc = source("dcc.py")
        calls = re.findall(r"send_dcc_sending_notice\([^\n]*\)", dcc)
        self.assertEqual(len(calls), 4, calls)
        for call in calls:
            self.assertIn("channel=", call, call)

    def test_the_two_queue_notices_name_a_channel(self):
        calls = re.findall(r"send_dcc_queue_notice\([^\n]*\)", source("dcc.py"))
        self.assertEqual(len(calls), 2, calls)
        for call in calls:
            self.assertIn("channel=target_chan", call, call)

    def test_each_notice_gets_the_channel_its_send_is_started_with(self):
        """The notice and the start_dcc_send that follows it must not name
        different channels."""
        dcc = source("dcc.py")
        for notice, starter in (("rar_filename", "target_rar_path, rar_filename, target_chan"),
                                ("path=f_path", "f_path, f_name, target_chan"),
                                ("path=g_path", "g_path, g_name, g_chan"),
                                ("path=full_path", None)):
            with self.subTest(notice=notice):
                self.assertRegex(dcc, r"send_dcc_sending_notice\([^\n]*" + re.escape(notice)
                                 + r"[^\n]*channel=(target_chan|g_chan)")
                if starter:
                    self.assertIn(starter, dcc)


class EveryFailureOfATransferCarriesItsChannel(unittest.TestCase):

    def start_dcc_send_source(self):
        dcc = source("dcc.py")
        start = dcc.index("def start_dcc_send(")
        return dcc[start:]

    def test_start_dcc_send_reports_through_one_wrapper_that_adds_the_channel(self):
        body = self.start_dcc_send_source()

        self.assertIn("_report_transfer_failure(*args, channel=channel, **kwargs)", body)
        # the wrapper is the only direct caller left in the function
        self.assertEqual(body.count("_report_transfer_failure("), 1)
        self.assertGreaterEqual(body.count("report_failure("), 9)


if __name__ == "__main__":
    unittest.main()
