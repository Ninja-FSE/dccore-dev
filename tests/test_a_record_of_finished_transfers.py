"""A record of finished transfers, with the nick each one went to or came from (#1068).

One row is written when a transfer ends, so the figures an operator wants -
the most-sent files, files and lists sent, top and average speed, files
received and how big, the average wait in the queue, and the nicks with the
most files - can be worked out from one place. The nick is kept in lower case;
no host and no channel is.
"""

import io
import os
import shutil
import socket
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import unittest

from tests import support  # noqa: F401  (path setup)

import announce  # noqa: E402
import dcc  # noqa: E402
import dcc_fetch  # noqa: E402
import defaults as config  # noqa: E402
import runtime  # noqa: E402
import transfer_log  # noqa: E402

from tests.support import DCCoreTestCase, silence_debug  # noqa: E402
from tests.test_a_failing_bot_is_paused import TcpLike  # noqa: E402
from tests.test_dcc_resume_end_to_end import RecordingIrcSocket, loopback_is_usable  # noqa: E402

USER = "SomeNick"
CHANNEL = "#somechannel"
CONTENT = bytes(range(256)) * 400          # 102,400 bytes


class Case(DCCoreTestCase):
    def sent(self, name="Song.mp3", key=None, kind="file", size=1000, speed=None, waited=None,
             seconds=1.0, wire=None, nick=None):
        return transfer_log.record_sent(kind, key or name, name, size,
                                        size if wire is None else wire, seconds, speed, waited, nick=nick)

    def rows(self):
        conn = sqlite3.connect(config.TRANSFER_LOG_FILE)
        try:
            return conn.execute("SELECT * FROM transfers ORDER BY id").fetchall()
        finally:
            conn.close()


class WhatIsKept(Case):
    def test_a_row_has_a_nick_and_no_host_or_channel_column(self):
        self.sent()
        conn = sqlite3.connect(config.TRANSFER_LOG_FILE)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(transfers)")}
        conn.close()
        self.assertEqual(columns, {"id", "direction", "nick", "kind", "ended_at", "item_key", "name",
                                   "size", "bytes", "seconds", "speed", "waited"})

    def test_a_list_is_kept_without_its_name(self):
        self.sent("Bot-List-2026-10-01.zip", kind="list")
        row = self.rows()[0]
        self.assertEqual((row[3], row[5], row[6]), ("list", None, None))

    def test_a_received_file_is_kept_without_its_name(self):
        transfer_log.record_received("file", 4096)
        row = self.rows()[0]
        self.assertEqual((row[1], row[3], row[5], row[6], row[7]), ("received", "file", None, None, 4096))

    def test_the_nick_is_kept_in_lower_case(self):
        self.sent(nick="  SomeNick ")
        transfer_log.record_received("file", 10, nick="OtherBot")
        self.assertEqual([row[2] for row in self.rows()], ["somenick", "otherbot"])

    def test_a_row_without_a_nick_keeps_none(self):
        self.sent()
        self.sent(nick="   ")
        self.assertEqual([row[2] for row in self.rows()], [None, None])

    def test_the_record_can_be_turned_off(self):
        self.set_config(TRANSFER_LOG_FILE="")
        self.assertFalse(self.sent())
        self.assertEqual(transfer_log.summary()["files_sent"], 0)

    def test_a_write_that_fails_is_dropped_and_not_raised(self):
        self.set_config(TRANSFER_LOG_FILE=tempfile.gettempdir())   # a directory, not a file
        self.assertFalse(self.sent())


class TheFigures(Case):
    def test_an_empty_record_says_zero_and_no_wait(self):
        self.assertEqual(transfer_log.summary(), {
            "files_sent": 0, "lists_sent": 0, "bytes_sent": 0, "top_speed": 0, "average_speed": 0,
            "queue_wait_seconds": None, "files_received": 0, "bytes_received": 0})
        self.assertEqual(transfer_log.top_files(), [])

    def test_files_and_lists_are_counted_apart(self):
        self.sent("A.mp3")
        self.sent("B.mp3")
        self.sent("Pack.rar", kind="album")
        self.sent("List-2026-10-01.zip", kind="list")
        figures = transfer_log.summary()
        self.assertEqual((figures["files_sent"], figures["lists_sent"]), (3, 1))

    def test_the_bytes_sent_leave_the_lists_out(self):
        self.sent("A.mp3", size=1000)
        self.sent("List.zip", kind="list", size=500000)
        self.assertEqual(transfer_log.summary()["bytes_sent"], 1000)

    def test_top_speed_and_a_weighted_average(self):
        self.sent("A.mp3", size=1000, seconds=1.0, speed=1000)
        self.sent("B.mp3", size=9000, seconds=1.0, speed=9000)
        self.sent("Tiny.mp3", size=10, seconds=0.01, speed=None)
        figures = transfer_log.summary()
        self.assertEqual(figures["top_speed"], 9000)
        self.assertEqual(figures["average_speed"], 5000)

    def test_the_wait_is_averaged_over_the_rows_that_know_it(self):
        self.sent("A.mp3", waited=10)
        self.sent("B.mp3", waited=30)
        self.sent("C.mp3", waited=None)
        self.assertEqual(transfer_log.summary()["queue_wait_seconds"], 20)

    def test_received_files_and_their_size_leave_the_lists_out(self):
        transfer_log.record_received("file", 1000)
        transfer_log.record_received("album", 5000)
        transfer_log.record_received("list", 70000)
        figures = transfer_log.summary()
        self.assertEqual((figures["files_received"], figures["bytes_received"]), (2, 6000))

    def test_what_was_sent_does_not_count_as_received(self):
        self.sent("A.mp3")
        figures = transfer_log.summary()
        self.assertEqual((figures["files_received"], figures["bytes_received"]), (0, 0))

    def test_a_period_leaves_out_what_came_before_it(self):
        self.sent("Old.mp3")
        time.sleep(1.1)
        cut = int(time.time())
        self.sent("New.mp3")
        self.assertEqual(transfer_log.summary(since=cut)["files_sent"], 1)
        self.assertEqual(transfer_log.top_files(since=cut), [("New.mp3", 1)])


class TheNicks(Case):
    def test_the_nick_with_most_files_comes_first_then_the_larger_total_then_the_name(self):
        for _ in range(3):
            self.sent("A.mp3", size=100, nick="nickb")
        self.sent("B.mp3", size=500, nick="nicka")
        self.sent("C.mp3", size=500, nick="nicka")
        self.sent("D.mp3", size=900, nick="nickc")
        self.sent("E.mp3", size=900, nick="nickd")
        self.sent("F.mp3", size=900, nick="nickd")
        self.assertEqual(transfer_log.top_nicks(),
                         [("nickb", 3, 300), ("nickd", 2, 1800), ("nicka", 2, 1000), ("nickc", 1, 900)])

    def test_lists_and_nickless_rows_are_not_ranked(self):
        self.sent("Bot-List.zip", kind="list", nick="nicka")
        self.sent("A.mp3", nick=None)
        self.sent("B.mp3", nick="nickb")
        self.assertEqual(transfer_log.top_nicks(), [("nickb", 1, 1000)])

    def test_sent_and_received_are_ranked_apart(self):
        self.sent("A.mp3", nick="nicka")
        transfer_log.record_received("file", 700, nick="botone")
        transfer_log.record_received("album", 300, nick="botone")
        self.assertEqual(transfer_log.top_nicks(transfer_log.SENT), [("nicka", 1, 1000)])
        self.assertEqual(transfer_log.top_nicks(transfer_log.RECEIVED), [("botone", 2, 1000)])

    def test_the_limit_and_the_period_apply(self):
        for n in range(5):
            self.sent(f"{n}.mp3", nick=f"nick{n}")
        self.assertEqual(len(transfer_log.top_nicks(limit=3)), 3)
        self.assertEqual(transfer_log.top_nicks(since=time.time() + 60), [])

    def test_one_nick_in_figures(self):
        self.sent("A.mp3", size=100, nick="NickA")
        self.sent("B.mp3", size=200, nick="nicka")
        self.sent("Bot-List.zip", kind="list", nick="nicka")
        self.sent("C.mp3", size=900, nick="nickb")
        transfer_log.record_received("file", 50, nick="NICKA")
        self.assertEqual(transfer_log.nick_summary("NickA"), {
            "files_sent": 2, "lists_sent": 1, "bytes_sent": 300, "files_received": 1, "bytes_received": 50})

    def test_an_unknown_or_empty_nick_has_all_zeros(self):
        zero = {"files_sent": 0, "lists_sent": 0, "bytes_sent": 0, "files_received": 0, "bytes_received": 0}
        self.assertEqual(transfer_log.nick_summary("nobody"), zero)
        self.assertEqual(transfer_log.nick_summary(""), zero)
        self.assertEqual(transfer_log.nick_summary(None), zero)

    def test_forgetting_a_nick_removes_its_rows_and_only_its_rows(self):
        self.sent("A.mp3", nick="nicka")
        self.sent("B.mp3", nick="nicka")
        self.sent("C.mp3", nick="nickb")
        self.assertEqual(transfer_log.forget_nick("NickA"), 2)
        self.assertEqual(transfer_log.top_nicks(), [("nickb", 1, 1000)])
        self.assertEqual(transfer_log.summary()["files_sent"], 1)

    def test_forgetting_nobody_removes_nothing(self):
        self.sent("A.mp3", nick="nicka")
        self.assertEqual(transfer_log.forget_nick(""), 0)
        self.assertEqual(transfer_log.forget_nick(None), 0)
        self.assertEqual(transfer_log.forget_nick("nobody"), 0)
        self.assertEqual(transfer_log.summary()["files_sent"], 1)

    def test_forgetting_everything_empties_the_record(self):
        self.sent("A.mp3", nick="nicka")
        transfer_log.record_received("file", 10, nick="botone")
        self.assertEqual(transfer_log.forget_all(), 2)
        self.assertEqual(transfer_log.summary()["files_sent"], 0)
        self.assertEqual(transfer_log.summary()["files_received"], 0)

    def test_forgetting_with_no_record_is_harmless(self):
        self.assertEqual(transfer_log.forget_all(), 0)
        self.assertEqual(transfer_log.forget_nick("nicka"), 0)

    def test_forgetting_does_nothing_when_the_record_is_off(self):
        self.sent("A.mp3", nick="nicka")
        self.set_config(TRANSFER_LOG_FILE="")
        self.assertEqual(transfer_log.forget_all(), 0)


class TheTopFiles(Case):
    def test_the_most_sent_first_and_ties_by_name(self):
        for name, times in (("B.mp3", 2), ("A.mp3", 2), ("C.mp3", 3), ("D.mp3", 1)):
            for _ in range(times):
                self.sent(name)
        self.assertEqual(transfer_log.top_files(), [("C.mp3", 3), ("A.mp3", 2), ("B.mp3", 2), ("D.mp3", 1)])

    def test_ten_at_most_by_default(self):
        for n in range(12):
            self.sent(f"Track {n:02d}.mp3")
        self.assertEqual(len(transfer_log.top_files()), 10)
        self.assertEqual(len(transfer_log.top_files(limit=3)), 3)

    def test_two_files_with_one_name_in_two_folders_are_two_rows(self):
        self.sent("01.mp3", key="Music/Album One/01.mp3")
        self.sent("01.mp3", key="Music/Album Two/01.mp3")
        self.sent("01.mp3", key="Music/Album Two/01.mp3")
        self.assertEqual(transfer_log.top_files(), [("01.mp3", 2), ("01.mp3", 1)])

    def test_a_list_never_appears(self):
        for _ in range(5):
            self.sent("List-2026-10-01.zip", kind="list")
        self.sent("A.mp3")
        self.assertEqual(transfer_log.top_files(), [("A.mp3", 1)])


class AReceivedFileIsRecorded(DCCoreTestCase):
    def setUp(self):
        super().setUp()
        self.set_config(fetch_queue={}, MAX_FETCH_SLOTS=10, fetch_feature_disabled=False,
                        CHANNEL="#chan", FETCH_MAX_PER_BOT=0)
        config.channel_users["#chan"] = {"serverone"}
        silence_debug(announce)

    def receive(self, payload, claimed, request_type="file"):
        ours, theirs = socket.socketpair()
        self.addCleanup(ours.close)
        self.addCleanup(theirs.close)
        theirs.sendall(payload)
        rid = dcc_fetch.enqueue_fetch("ServerOne", "Track.flac", request_type="file")
        row = config.fetch_queue[rid]
        row.update(state="receiving", request_type=request_type)
        dest = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, dest, ignore_errors=True)
        theirs.shutdown(socket.SHUT_WR)
        dcc_fetch._run_transfer(row, {"size": claimed, "ip": None, "port": 0}, dest, "Track.flac",
                                sock=TcpLike(ours))
        return row

    def test_a_finished_download_is_one_row(self):
        row = self.receive(b"x" * 64, 64)
        self.assertEqual(row["state"], "complete")
        figures = transfer_log.summary()
        self.assertEqual((figures["files_received"], figures["bytes_received"]), (1, 64))
        self.assertEqual(transfer_log.top_nicks(transfer_log.RECEIVED), [("serverone", 1, 64)])

    def test_a_download_that_fell_short_is_not(self):
        row = self.receive(b"x" * 32, 64)
        self.assertEqual(row["state"], "failed")
        self.assertEqual(transfer_log.summary()["files_received"], 0)


@unittest.skipUnless(loopback_is_usable(), "this runner cannot bind a listener and dial loopback")
class ASendIsRecorded(DCCoreTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="dccore-log-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.served = os.path.join(self.tmp, "Some_Song.mp3")
        with io.open(self.served, "wb") as handle:
            handle.write(CONTENT)
        self.set_config(
            active_transfers=[{"user": USER, "file": "Some_Song.mp3", "bytes_sent": 0,
                               "next_file_obj": "Some_Song.mp3"}],
            MAX_DCC_SLOTS=3, MY_IP_OR_DOCK="8.8.8.8", DCC_PORT_START=51320, DCC_PORT_END=51330,
            FILE_DIRECTORY=self.tmp)
        runtime.dcc_send_offers.clear()
        self.addCleanup(runtime.dcc_send_offers.clear)
        silence_debug(announce)
        self.oserve.total_sent_bytes = 0
        self._real_stall = dcc.ACK_STALL_SECONDS
        dcc.ACK_STALL_SECONDS = 2.0
        self.addCleanup(setattr, dcc, "ACK_STALL_SECONDS", self._real_stall)

    def send(self, ack=True, waited=30):
        irc = RecordingIrcSocket()
        self.oserve.irc_connection = irc
        next_file = {"path": self.served, "file": "Some_Song.mp3", "channel": CHANNEL,
                     "user_raw": USER, "is_temporary_zip": False, "queued_at": time.time() - waited}
        sender = threading.Thread(
            target=dcc.start_dcc_send,
            args=(irc, USER, self.served, "Some_Song.mp3", CHANNEL, next_file), daemon=True)
        sender.start()
        self.addCleanup(sender.join, 30)
        self.assertTrue(irc.handshake_seen.wait(20), "no DCC SEND handshake")
        client = socket.create_connection(("127.0.0.1", irc.port()), timeout=20)
        client.settimeout(20)
        self.addCleanup(client.close)
        held = 0
        while held < len(CONTENT):
            chunk = client.recv(4096)
            if not chunk:
                break
            held += len(chunk)
            if ack:
                client.sendall(struct.pack("!I", held & 0xFFFFFFFF))
        if not ack:
            client.close()
        sender.join(30)

    def test_a_completed_send_is_one_row_with_the_wait_and_the_nick_but_no_host_or_channel(self):
        self.send()
        conn = sqlite3.connect(config.TRANSFER_LOG_FILE)
        rows = conn.execute("SELECT direction, nick, kind, name, size, bytes, waited FROM transfers").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1, rows)
        direction, nick, kind, name, size, wire, waited = rows[0]
        self.assertEqual((direction, nick, kind, name, size, wire),
                         ("sent", USER.lower(), "file", "Some_Song.mp3", len(CONTENT), len(CONTENT)))
        self.assertAlmostEqual(waited, 30, delta=5)
        with io.open(config.TRANSFER_LOG_FILE, "rb") as handle:
            raw = handle.read()
        for what in (CHANNEL, "127.0.0.1"):
            self.assertNotIn(what.encode(), raw)

    def test_a_send_nobody_acknowledged_leaves_no_row(self):
        self.send(ack=False)
        self.assertEqual(transfer_log.summary()["files_sent"], 0)

    def test_a_row_saved_without_a_stamp_has_no_wait(self):
        irc = RecordingIrcSocket()
        self.oserve.irc_connection = irc
        sender = threading.Thread(
            target=dcc.start_dcc_send,
            args=(irc, USER, self.served, "Some_Song.mp3", CHANNEL, "Some_Song.mp3"), daemon=True)
        sender.start()
        self.addCleanup(sender.join, 30)
        self.assertTrue(irc.handshake_seen.wait(20))
        client = socket.create_connection(("127.0.0.1", irc.port()), timeout=20)
        client.settimeout(20)
        self.addCleanup(client.close)
        held = 0
        while held < len(CONTENT):
            held += len(client.recv(4096))
            client.sendall(struct.pack("!I", held & 0xFFFFFFFF))
        sender.join(30)
        self.assertEqual(transfer_log.summary()["files_sent"], 1)
        self.assertIsNone(transfer_log.summary()["queue_wait_seconds"])


class TheQueueStampsWhenAskedFor(unittest.TestCase):
    def test_every_row_the_requests_queue_carries_the_time_it_was_asked_for(self):
        with io.open(os.path.join(support.REPO_ROOT, "src", "dcc.py"), encoding="utf-8") as handle:
            code = handle.read()
        appended = code.count("config.dcc_queue[user_key].append({")
        self.assertEqual(appended, 2)
        self.assertEqual(code.count('"queued_at": time.time()'), 3, "two queued rows and the row sent at once")


if __name__ == "__main__":
    unittest.main()
