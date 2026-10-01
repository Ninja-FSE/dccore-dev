"""A record of every request and transfer, sent and received (#1068).

One row per request - written when it is queued or refused, finished however
it ends - and one per download from another bot, in an SQLite database the
statistics are worked out from. Nick only: no hosts, no IP addresses, no
searches. Nothing waits on it: rows are gathered in memory and a background
writer puts them on disk.
"""

import io
import os
import sqlite3
import sys
import time
import types
import unittest

from tests import support  # noqa: F401  (path setup)

import announce  # noqa: E402
import commands  # noqa: E402
import db  # noqa: E402
import dcc  # noqa: E402
import dcc_fetch  # noqa: E402
import defaults as config  # noqa: E402
import runtime  # noqa: E402
import transfer_log  # noqa: E402
import webserver  # noqa: E402
from tests.support import no_disk_writes, silence_debug  # noqa: E402
from tests.test_an_unknown_filename_does_not_scan_the_library_unbounded import LookupBase  # noqa: E402

REPO_ROOT = support.REPO_ROOT


def queue_row(name="Song.flac", user="dave", **extra):
    return dict({"file": name, "path": "/music/" + name, "channel": "#c",
                 "user_raw": user, "is_temporary_zip": False}, **extra)


class Case(support.DCCoreTestCase):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.make_tree().root, "transfers.db")
        self.set_config(TRANSFER_LOG=True, TRANSFER_LOG_FILE=self.path)

    def rows(self, **where):
        return [r for r in transfer_log.read_rows(self.path)
                if all(r.get(k) == v for k, v in where.items())]

    def only(self, **where):
        found = self.rows(**where)
        self.assertEqual(len(found), 1, found)
        return found[0]


class OneRequestFromStartToEnd(Case):
    def test_a_queued_send_is_one_row_filled_in_as_it_goes(self):
        row = transfer_log.stamp(queue_row())
        transfer_log.queued(row, "Dave", "#somechan", "file", position=2, queue_length=7,
                            list_label="main")
        transfer_log.send_started(row, "Dave", 1000.0, 5000, resume_offset=100)
        transfer_log.send_finished(row, "Dave", 5000, ended_at=1010.0)
        transfer_log.settled(row, "Dave", delivered=True, retained=False, reason="transfer complete")

        got = self.only()
        self.assertEqual(got["id"], row["log_id"])
        self.assertEqual((got["direction"], got["nick"], got["nick_key"], got["channel"]),
                         ("sent", "Dave", "dave", "#somechan"))
        self.assertEqual((got["kind"], got["name"], got["list_label"]), ("file", "Song.flac", "main"))
        self.assertEqual((got["queue_position"], got["queue_length"]), (2, 7))
        self.assertEqual((got["size"], got["bytes"], got["resume_offset"]), (5000, 5000, 100))
        self.assertEqual((got["started_at"], got["ended_at"]), (1000.0, 1010.0))
        self.assertAlmostEqual(got["requested_at"], row["requested_at"])
        self.assertEqual((got["result"], got["reason"]), ("sent", None))

    def test_the_id_and_the_time_asked_travel_with_the_queue_row(self):
        """Kept in dcc_queue.txt, so a restart finds the same record."""
        row = transfer_log.stamp(queue_row())
        self.assertEqual(len(row["log_id"]), 16)
        self.assertLessEqual(row["requested_at"], time.time())
        again = dict(row)
        transfer_log.stamp(again)
        self.assertEqual(again["log_id"], row["log_id"], "stamping twice changes nothing")

    def test_a_row_queued_before_the_record_existed_gets_one_when_it_ends(self):
        row = queue_row()
        transfer_log.settled(row, "dave", delivered=True, retained=False, reason="transfer complete")
        self.assertTrue(row.get("log_id"))
        self.assertEqual(self.only()["result"], "sent")

    def test_nothing_is_written_when_it_is_off(self):
        self.set_config(TRANSFER_LOG=False)
        row = transfer_log.stamp(queue_row())
        transfer_log.queued(row, "dave", "#c", "file")
        self.assertNotIn("log_id", row)
        transfer_log._record({"id": "abc", "direction": "sent", "nick": "dave"})
        self.assertEqual(transfer_log.read_rows(self.path), [])


class WhatTheQueueDecided(Case):
    """dcc.release_queue_entry() is where every send attempt is settled."""

    def setUp(self):
        super().setUp()
        no_disk_writes(db)
        silence_debug(announce)
        self.set_config(MAX_SEND_FAILS=3)
        self.row = transfer_log.stamp(queue_row())
        config.dcc_queue["dave"] = [self.row]

    def settle(self, delivered, reason="transfer did not complete"):
        with _quiet():
            dcc.release_queue_entry("dave", self.row, delivered=delivered, reason=reason)

    def test_a_failed_try_stays_queued_with_its_failures_counted(self):
        self.settle(False)
        got = self.only()
        self.assertEqual((got["result"], got["failures"], got["reason"]),
                         ("queued", 1, "transfer did not complete"))

    def test_then_sent(self):
        self.settle(False)
        self.settle(True, "transfer complete")
        got = self.only()
        self.assertEqual((got["result"], got["reason"]), ("sent", None))

    def test_given_up_after_the_last_try(self):
        self.row["send_fails"] = 2
        self.settle(False)
        got = self.only()
        self.assertEqual(got["result"], "failed")
        self.assertIn("gave up after 3 tries", got["reason"])


class _quiet:
    def __enter__(self):
        self._out = sys.stdout
        sys.stdout = io.StringIO()

    def __exit__(self, *exc):
        sys.stdout = self._out


class TakenOutOfTheQueue(Case):
    def setUp(self):
        super().setUp()
        no_disk_writes(db)
        silence_debug(announce)
        self.notices = []
        old = sys.modules.get("oserve")
        sys.modules["oserve"] = types.SimpleNamespace(
            queue_message=lambda to, msg, *a, **k: self.notices.append(msg))
        self.addCleanup(lambda: sys.modules.__setitem__("oserve", old) if old
                        else sys.modules.pop("oserve", None))

    def test_one_file_removed_by_the_user(self):
        config.dcc_queue["dave"] = [transfer_log.stamp(queue_row("A.flac")),
                                    transfer_log.stamp(queue_row("B.flac"))]
        with _quiet():
            commands.handle_queue_remove_file(None, "Dave", "#c", "A.flac")
        got = self.only(name="A.flac")
        self.assertEqual((got["result"], got["reason"], got["nick"]),
                         ("removed", "removed by the user", "Dave"))
        self.assertEqual(self.rows(name="B.flac"), [])

    def test_the_whole_queue_removed_by_the_user(self):
        config.dcc_queue["dave"] = [transfer_log.stamp(queue_row("A.flac")),
                                    transfer_log.stamp(queue_row("B.flac"))]
        with _quiet():
            commands.handle_queue_remove(None, "Dave", "#c")
        self.assertEqual(sorted(r["result"] for r in self.rows()), ["removed", "removed"])

    def test_cleared_by_an_admin(self):
        config.dcc_queue["dave"] = [transfer_log.stamp(queue_row("A.flac"))]
        with _quiet():
            commands.handle_admin_clear_queue("SomeAdmin", "#c", "!clearqueue dave", authorised=True)
        got = self.only()
        self.assertEqual((got["result"], got["reason"]), ("cleared", "cleared by SomeAdmin"))


class ARefusedRequest(Case):
    def test_the_reason_the_user_was_told_is_kept(self):
        ctx = transfer_log.begin_request("Dave", "Missing.flac", "#c", "file")
        transfer_log.note_refusal("file_not_found")
        transfer_log.note_refusal("busy")
        transfer_log.end_request(ctx)
        got = self.only()
        self.assertEqual((got["result"], got["reason"], got["name"], got["kind"]),
                         ("refused", "file_not_found", "Missing.flac", "file"))

    def test_one_that_was_queued_is_not_also_refused(self):
        ctx = transfer_log.begin_request("Dave", "Song.flac", "#c", "file")
        transfer_log.queued(transfer_log.stamp(queue_row()), "Dave", "#c", "file")
        transfer_log.end_request(ctx)
        self.assertEqual([r["result"] for r in self.rows()], ["queued"])

    def test_a_notice_after_it_was_queued_does_not_make_it_refused(self):
        ctx = transfer_log.begin_request("Dave", "Song.flac", "#c", "file")
        transfer_log.queued(transfer_log.stamp(queue_row()), "Dave", "#c", "file")
        transfer_log.note_refusal("busy")
        transfer_log.end_request(ctx)
        self.assertEqual([r["result"] for r in self.rows()], ["queued"])

    def test_one_silently_ignored_is_not_a_request(self):
        ctx = transfer_log.begin_request("Dave", "x", "#notours", "file")
        transfer_log.end_request(ctx)
        self.assertEqual(self.rows(), [])

    def test_the_notices_say_why(self):
        """The real notice functions note the reason."""
        ctx = transfer_log.begin_request("Dave", "x.flac", "#c", "file")
        with _quiet():
            announce.send_dcc_error("Dave", "user_full")
        transfer_log.end_request(ctx)
        self.assertEqual(self.only()["reason"], "user_full")


class ThroughTheRealRequestPath(LookupBase):
    """handle_download_request() itself, with a real library tree."""

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tree.root, "transfers.db")
        self.set_config(TRANSFER_LOG=True, TRANSFER_LOG_FILE=self.path)
        # The real error notice, so the refusal reason is noted where it is
        # in the daemon; the harness only recorded it.
        announce.send_dcc_error = self._saved_announce["send_dcc_error"]

    def test_a_file_that_is_there_is_recorded_as_asked_for(self):
        self.ask(os.path.basename(self.tree.tracks[0]), user="Dave")
        got = [r for r in transfer_log.read_rows(self.path)]
        self.assertEqual(len(got), 1, got)
        self.assertEqual((got[0]["nick"], got[0]["channel"], got[0]["kind"], got[0]["result"]),
                         ("Dave", "#dccore-test", "file", "queued"))
        self.assertEqual(got[0]["name"], os.path.basename(self.tree.tracks[0]))

    def test_a_file_that_is_not_there_is_recorded_as_refused(self):
        self.ask("Nothing Here At All.flac", user="Dave")
        got = transfer_log.read_rows(self.path)
        self.assertEqual([(r["result"], r["reason"]) for r in got], [("refused", "file_not_found")])

    def test_the_handler_is_wrapped(self):
        self.assertTrue(hasattr(dcc.handle_download_request, "__wrapped__"))


class Received(Case):
    def put(self, state="offered", **extra):
        row = dcc_fetch.new_fetch_row("SomeBot", "Wanted.flac", now=1000.0)
        row.update(dict({"state": state, "total_size": 900, "bytes_received": 0}, **extra))
        config.fetch_queue["abc123def456"] = row
        return row

    def test_a_failure_is_recorded_under_the_request_id(self):
        row = self.put()
        dcc_fetch._mark_failed_locked(row, "no response")
        got = self.only()
        self.assertEqual((got["id"], got["direction"], got["nick"], got["result"], got["reason"]),
                         ("abc123def456", "received", "SomeBot", "failed", "no response"))
        self.assertEqual((got["kind"], got["name"], got["requested_at"]), ("file", "Wanted.flac", 1000.0))

    def test_a_late_offer_that_completes_ends_the_same_row_without_the_old_reason(self):
        row = self.put()
        dcc_fetch._mark_failed_locked(row, "no response")
        row.update(state="complete", bytes_received=900, filename="Wanted (2).flac",
                   passive_peer_ip="203.0.113.5")
        dcc_fetch._record_end(row)
        got = self.only()
        self.assertEqual((got["result"], got["reason"], got["bytes"]), ("complete", None, 900))
        self.assertEqual((got["arrived_as"], got["passive"]), ("Wanted (2).flac", 1))

    def test_a_cancel_before_it_started(self):
        self.put(state="pending")
        with _quiet():
            status, _ = webserver.build_fetch_delete_result("abc123def456")
        self.assertEqual(status, 200)
        self.assertEqual(self.only()["result"], "cancelled")

    def test_deleting_a_finished_one_only_forgets_it_here(self):
        row = self.put(state="complete", bytes_received=900)
        dcc_fetch._record_end(row)
        with _quiet():
            webserver.build_fetch_delete_result("abc123def456")
        self.assertEqual(self.only()["result"], "complete")


class TheWriter(Case):
    def test_it_writes_on_its_own(self):
        """No flush from the test: the background writer does it."""
        transfer_log.queued(transfer_log.stamp(queue_row()), "dave", "#c", "file")
        deadline = time.time() + 10
        found = []
        while time.time() < deadline and not found:
            if os.path.isfile(self.path):
                conn = sqlite3.connect(self.path)
                try:
                    found = conn.execute("SELECT result FROM requests").fetchall()
                except sqlite3.OperationalError:
                    found = []
                finally:
                    conn.close()
            if not found:
                time.sleep(0.05)
        self.assertEqual(found, [("queued",)])

    def test_a_row_goes_to_the_file_it_was_recorded_under(self):
        """A test's rows never reach the real data/, however late they are written."""
        transfer_log.queued(transfer_log.stamp(queue_row()), "dave", "#c", "file")
        other = os.path.join(os.path.dirname(self.path), "elsewhere.db")
        self.set_config(TRANSFER_LOG_FILE=other)
        transfer_log.flush()
        self.assertEqual(len(transfer_log.read_rows(self.path)), 1)
        self.assertFalse(os.path.exists(other))

    def test_a_damaged_file_is_moved_aside_and_a_new_one_started(self):
        with open(self.path, "wb") as handle:
            handle.write(b"this is not a database" * 100)
        transfer_log.queued(transfer_log.stamp(queue_row()), "dave", "#c", "file")
        with _quiet():
            rows = transfer_log.read_rows(self.path)
        self.assertEqual(len(rows), 1)
        aside = [n for n in os.listdir(os.path.dirname(self.path)) if ".damaged-" in n]
        self.assertEqual(len(aside), 1, "kept, not deleted")

    def test_a_failing_write_never_raises(self):
        self.set_config(TRANSFER_LOG_FILE=os.path.join(self.path, "not", "a", "dir", "x.db"))
        with open(self.path, "w") as handle:
            handle.write("a file where a folder should be")
        transfer_log.queued(transfer_log.stamp(queue_row()), "dave", "#c", "file")
        with _quiet():
            self.assertEqual(transfer_log.flush(), 0)


def read(path):
    with io.open(os.path.join(REPO_ROOT, path), encoding="utf-8") as handle:
        return handle.read()


class TheHooksAreInPlace(unittest.TestCase):
    """Where driving the real thing needs a live DCC peer or the freeze clock,
    read the statement out of the source."""

    def test_a_send_is_started_and_finished_where_it_happens(self):
        code = read("dcc.py")
        body = code.split("def start_dcc_send(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("transfer_log.send_started(next_file, user, started_at, file_size, resume_offset)", body)
        finished = body.index("transfer_log.send_finished(next_file, user,")
        self.assertLess(finished, body.index("row_retained = release_queue_entry("))

    def test_the_queue_settles_every_attempt(self):
        code = read("dcc.py")
        body = code.split("def release_queue_entry(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("transfer_log.settled(next_file, user, delivered, retained,", body)

    def test_both_freeze_expiries_are_recorded(self):
        code = read("dcc.py")
        self.assertEqual(code.count('"expired", "left and did not come back in time")'), 1)
        self.assertEqual(code.count('transfer_log.removed(config.dcc_queue[t_key], target_user, "expired",'), 1)

    def test_a_download_end_is_recorded_where_it_completes(self):
        code = read("dcc_fetch.py")
        self.assertIn('        row["state"] = "complete"\n        row["bytes_received"] = bytes_received\n'
                      '        _record_end(row)\n', code)


if __name__ == "__main__":
    unittest.main()
