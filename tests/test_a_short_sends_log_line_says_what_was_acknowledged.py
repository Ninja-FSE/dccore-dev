"""A short send's "Not counted" line gives the bytes the receiver
acknowledged, not the bytes written (#1477).

A send the receiver closed early logged two lines that disagreed:

    [DCC-FAIL] ... having acknowledged 5,242,880 of 8,071,984 bytes. Not counted.
    [DB COUNTER] Not counted: ... ended short at 8071984 of 8071984 bytes.

The second read SentFile.bytes_sent, what was handed to the socket - the
whole file, once the loop has written it - so it read as a complete send
called short. record_completed_send() now reports SentFile.acked, with
thousands separators like the [DCC-FAIL] line, and start_dcc_send() hands
it acks.acked; a SentFile with acked=None says the count is not known and
never shows the written count in its place.

Driven twice: record_completed_send() on its own, and the real
start_dcc_send() with faked sockets (the harness of
test_a_nick_whose_sends_keep_failing_is_paused.py), the receiver
acknowledging 1,024 bytes of a file written whole and then closing.

Every nick and file here is invented.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests import test_a_finished_send_is_recorded_once as recorded  # noqa: E402
from tests import test_a_nick_whose_sends_keep_failing_is_paused as paused  # noqa: E402

LINE = "[DB COUNTER] Not counted:"
SIZE = 4_000_000          # recorded.SIZE, spelled out so the figures below read
WRITTEN = 3_500_000


def not_counted_lines(said):
    return [line for line in said.splitlines() if line.startswith(LINE)]


class TheLineOnItsOwn(recorded.RecordsASend):

    def short(self, **changes):
        said = self.record(transfer_completed=False, **changes)
        lines = not_counted_lines(said)
        self.assertEqual(len(lines), 1, said)
        self.assertEqual((self.stats, self.downloads, self.records), ([], [], []),
                         "a short send was counted")
        return lines[0]

    def assertNoWrittenCount(self, line, written):
        self.assertNotIn(f"{written:,}", line, "the bytes written are shown as if they arrived")
        self.assertNotIn(str(written), line, "the bytes written are shown as if they arrived")

    def test_it_gives_the_acknowledged_count_with_separators(self):
        line = self.short(bytes_sent=WRITTEN, acked=1_234_567)

        self.assertIn("the receiver acknowledged 1,234,567 of 4,000,000 bytes", line)
        self.assertNoWrittenCount(line, WRITTEN)

    def test_the_whole_file_written_is_not_called_short_of_itself(self):
        """The reported case: every byte written, a fraction acknowledged."""
        line = self.short(bytes_sent=SIZE, acked=1_048_576)

        self.assertIn("the receiver acknowledged 1,048,576 of 4,000,000 bytes", line)
        self.assertNotIn("4000000 of 4000000", line)
        self.assertNotIn("4,000,000 of 4,000,000", line)

    def test_nothing_acknowledged_is_zero_not_unknown(self):
        line = self.short(bytes_sent=WRITTEN, acked=0)

        self.assertIn("the receiver acknowledged 0 of 4,000,000 bytes", line)
        self.assertNotIn("not known", line)

    def test_an_unknown_count_says_so_and_never_shows_the_written_one(self):
        line = self.short(bytes_sent=WRITTEN, acked=None)

        self.assertIn("short of its 4,000,000 bytes "
                      "(how many the receiver acknowledged is not known)", line)
        self.assertNotIn("acknowledged 0", line)
        self.assertNoWrittenCount(line, WRITTEN)

    def test_it_still_names_the_file_and_the_nick(self):
        line = self.short(acked=10)
        self.assertTrue(line.startswith(f"{LINE} 01.flac for alfa ended short"), line)


class ThroughARealSend(paused.RealSendCase):
    """start_dcc_send() writes all 10,240 bytes; the receiver acknowledges
    1,024 and then closes, or goes quiet, in the final-ack wait."""

    def not_counted(self, ending):
        self.run_the_send(paused.all_sent_then(ending))
        lines = not_counted_lines(self.out.getvalue())
        self.assertEqual(len(lines), 1, self.out.getvalue())
        return lines[0]

    def test_a_receiver_that_closes_early_is_reported_at_what_it_acknowledged(self):
        line = self.not_counted(paused.closes)

        self.assertIn("the receiver acknowledged 1,024 of 10,240 bytes", line)
        self.assertNotIn("not known", line, "start_dcc_send() did not hand over acks.acked")
        self.assertNotIn("10240 of 10240", line)
        self.assertNotIn("10,240 of 10,240", line)

    def test_a_receiver_that_goes_quiet_is_reported_the_same_way(self):
        line = self.not_counted(paused.goes_quiet)

        self.assertIn("the receiver acknowledged 1,024 of 10,240 bytes", line)

    def test_the_two_lines_agree(self):
        """The [DCC-FAIL] line and the "Not counted" line name one figure."""
        line = self.not_counted(paused.closes)
        failed = [text for text in self.out.getvalue().splitlines() if text.startswith("[DCC-FAIL]")]

        self.assertEqual(len(failed), 1, self.out.getvalue())
        self.assertIn("having acknowledged 1,024 of 10,240 bytes", failed[0])
        self.assertIn("acknowledged 1,024 of 10,240 bytes", line)


if __name__ == "__main__":
    unittest.main()
