"""dcc_fetch.py - cross-bot file fetch (the "receiving bytes an untrusted
third party hands us" role, deliberately separate from dcc.py's "we are the
trusted server" role - see dcc_fetch.py's module docstring).

Covers, per the brief: offer parsing, admission control (an unsolicited offer
must be dropped, never dialled), filename sanitisation/path-traversal
rejection, the size cap being enforced BEFORE connecting, the state machine's
transitions, and one true end-to-end loopback-socket transfer for the happy
path plus one abort-on-oversize-during-transfer case - mirroring
tests/test_adminchat.py's real-socket pattern rather than mocking everything.

Deliberately stdlib-only, like the rest of the suite.
"""

import ipaddress
import os
import socket
import sys
import threading
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import config  # noqa: E402
import dcc  # noqa: E402
import dcc_fetch  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


def ip_long(ip):
    return int(ipaddress.IPv4Address(ip))


class OfferParsingTests(unittest.TestCase):

    def test_a_normal_offer_parses(self):
        offer = dcc_fetch.parse_dcc_send_offer(
            "DCC SEND Song.flac 2130706433 55000 4096")
        self.assertEqual(offer, {"filename": "Song.flac", "ip": "127.0.0.1",
                                  "port": 55000, "size": 4096})

    def test_ip_long_decode_is_the_inverse_of_dcc_get_public_ip_long(self):
        config.MY_IP_OR_DOCK = "203.0.113.7"
        as_long = dcc.get_public_ip_long()
        offer = dcc_fetch.parse_dcc_send_offer(f"DCC SEND x.txt {as_long} 55000 1")
        self.assertEqual(offer["ip"], "203.0.113.7")

    def test_a_ctcp_wrapped_offer_parses_the_same(self):
        offer = dcc_fetch.parse_dcc_send_offer("\x01DCC SEND Song.flac 2130706433 55000 4096\x01")
        self.assertIsNotNone(offer)
        self.assertEqual(offer["filename"], "Song.flac")

    def test_a_quoted_filename_with_spaces_parses(self):
        offer = dcc_fetch.parse_dcc_send_offer(
            'DCC SEND "My Favourite Song.flac" 2130706433 55000 4096')
        self.assertEqual(offer["filename"], "My Favourite Song.flac")

    def test_not_dcc_send_returns_none(self):
        self.assertIsNone(dcc_fetch.parse_dcc_send_offer("DCC CHAT chat 2130706433 55000"))
        self.assertIsNone(dcc_fetch.parse_dcc_send_offer("just some text"))

    def test_missing_fields_returns_none(self):
        self.assertIsNone(dcc_fetch.parse_dcc_send_offer("DCC SEND Song.flac 2130706433 55000"))

    def test_non_numeric_fields_return_none(self):
        self.assertIsNone(dcc_fetch.parse_dcc_send_offer("DCC SEND Song.flac notanumber 55000 4096"))

    def test_port_out_of_range_returns_none(self):
        self.assertIsNone(dcc_fetch.parse_dcc_send_offer("DCC SEND Song.flac 2130706433 70000 4096"))
        self.assertIsNone(dcc_fetch.parse_dcc_send_offer("DCC SEND Song.flac 2130706433 0 4096"))

    def test_negative_size_returns_none(self):
        self.assertIsNone(dcc_fetch.parse_dcc_send_offer("DCC SEND Song.flac 2130706433 55000 -1"))

    def test_zero_size_returns_none(self):
        """A declared size of exactly 0 must be rejected the same way a
        negative size is - not just "not negative". _run_transfer()'s loop
        is `while bytes_received < total_size`; with total_size == 0 that
        never runs even once, so a naive `size < 0` check would let a
        zero-declared-size offer sail through admission control, open a real
        connection, read nothing, and mark the row 'complete' with no actual
        transfer having happened."""
        self.assertIsNone(dcc_fetch.parse_dcc_send_offer("DCC SEND Song.flac 2130706433 55000 0"))

    def test_ip_long_out_of_range_returns_none(self):
        self.assertIsNone(dcc_fetch.parse_dcc_send_offer("DCC SEND Song.flac 99999999999 55000 4096"))


class FilenameNormalizationTests(unittest.TestCase):

    def test_space_and_underscore_are_equivalent(self):
        a = dcc_fetch._normalize_filename_for_match("My Song.flac")
        b = dcc_fetch._normalize_filename_for_match("My_Song.flac")
        self.assertEqual(a, b)

    def test_case_insensitive(self):
        self.assertEqual(
            dcc_fetch._normalize_filename_for_match("SONG.FLAC"),
            dcc_fetch._normalize_filename_for_match("song.flac"))


class FilenameSanitizationTests(unittest.TestCase):

    def test_path_separators_are_stripped(self):
        clean = dcc_fetch._sanitize_offer_filename("../../etc/passwd")
        self.assertNotIn("/", clean)
        self.assertNotIn("\\", clean)
        self.assertNotIn("..", clean)

    def test_windows_style_separators_are_stripped(self):
        clean = dcc_fetch._sanitize_offer_filename("..\\..\\Windows\\System32\\evil.exe")
        self.assertNotIn("\\", clean)
        self.assertNotIn("..", clean)

    def test_null_bytes_and_control_codes_are_stripped(self):
        clean = dcc_fetch._sanitize_offer_filename("song\x00.flac\x0304,05colored\x0f")
        self.assertNotIn("\x00", clean)
        self.assertNotIn("\x03", clean)
        self.assertNotIn("\x0f", clean)

    def test_an_all_hostile_name_falls_back_to_a_placeholder(self):
        clean = dcc_fetch._sanitize_offer_filename("../../../..")
        self.assertTrue(clean)
        self.assertNotIn("/", clean)

    def test_a_normal_name_survives_unchanged(self):
        self.assertEqual(dcc_fetch._sanitize_offer_filename("Enter Sandman (1991).flac"),
                          "Enter Sandman (1991).flac")

    def test_resolve_destination_path_stays_inside_fetched_files_dir(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="dccore-fetch-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = tmp
        dest_dir, stored_name = dcc_fetch._resolve_destination_path("abc123", "../../etc/passwd")
        self.assertIsNotNone(stored_name)
        full = os.path.join(dest_dir, stored_name)
        self.assertTrue(dcc.is_safe_path(os.path.abspath(tmp), full))
        self.assertTrue(stored_name.startswith("abc123_"))


class AdmissionControlTests(DCCoreTestCase):
    """The core safety guardrail: an offer is only ever acted on if it
    matches a row WE marked 'offered' a moment earlier."""

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-fetch-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp

    def test_unsolicited_offer_is_dropped_not_dialled(self):
        """No pending/offered row at all - a stranger just sends a DCC SEND
        out of nowhere. Must never even try to connect."""
        real_socket = socket.socket

        def guard(*a, **kw):
            self.fail("handle_incoming_offer must not open a socket for an unsolicited offer")

        socket.socket = guard
        self.addCleanup(lambda: setattr(socket, "socket", real_socket))

        dcc_fetch.handle_incoming_offer(
            None, "randomstranger", "DCC SEND Song.flac 2130706433 55000 4096")

        self.assertEqual(config.fetch_queue, {})

    def test_an_offer_from_the_wrong_bot_is_dropped(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()

        real_socket = socket.socket
        socket.socket = lambda *a, **kw: self.fail("must not connect")
        self.addCleanup(lambda: setattr(socket, "socket", real_socket))

        dcc_fetch.handle_incoming_offer(
            None, "impostorbot", "DCC SEND Song.flac 2130706433 55000 4096")

        self.assertEqual(config.fetch_queue[rid]["state"], "offered")

    def test_an_offer_for_the_wrong_filename_is_dropped(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()

        real_socket = socket.socket
        socket.socket = lambda *a, **kw: self.fail("must not connect")
        self.addCleanup(lambda: setattr(socket, "socket", real_socket))

        dcc_fetch.handle_incoming_offer(
            None, "goodbot", "DCC SEND SomeOtherFile.flac 2130706433 55000 4096")

        self.assertEqual(config.fetch_queue[rid]["state"], "offered")

    def test_matching_offer_is_claimed_underscore_space_equivalence(self):
        """The offering bot echoing back a space as an underscore (dcc.py's
        own outbound SEND does the same thing) must still match."""
        rid = dcc_fetch.enqueue_fetch("goodbot", "My Song.flac")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()

        # Reject at the size cap so this stays a pure admission-control test
        # with no real socket involved.
        self.set_config(MAX_FETCH_FILE_SIZE=10)
        dcc_fetch.handle_incoming_offer(
            None, "goodbot", "DCC SEND My_Song.flac 2130706433 55000 999999")

        self.assertEqual(config.fetch_queue[rid]["state"], "failed")
        self.assertIn("exceeds", config.fetch_queue[rid]["reason"])


class SizeCapTests(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-fetch-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp

    def test_oversized_declared_size_is_rejected_before_connecting(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "Big.flac")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()
        self.set_config(MAX_FETCH_FILE_SIZE=1000)

        real_socket = socket.socket
        socket.socket = lambda *a, **kw: self.fail(
            "an oversized offer must be rejected before a socket is ever opened")
        self.addCleanup(lambda: setattr(socket, "socket", real_socket))

        dcc_fetch.handle_incoming_offer(
            None, "goodbot", "DCC SEND Big.flac 2130706433 55000 999999")

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "failed")
        self.assertIn("exceeds", row["reason"])


class DispatcherStateMachineTests(DCCoreTestCase):

    def test_pending_rows_are_promoted_to_offered_and_a_request_is_queued(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac")
        self.assertEqual(config.fetch_queue[rid]["state"], "pending")

        dcc_fetch.check_fetch_queue()

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "offered")
        self.assertIsNotNone(row["offered_at"])
        self.assertEqual(len(self.oserve.queued), 1)
        sent_user, sent_msg, _is_vip = self.oserve.queued[0]
        self.assertEqual(sent_user, "goodbot")
        self.assertIn("!goodbot Song.flac", sent_msg)

    def test_only_up_to_max_fetch_slots_are_promoted_at_once(self):
        self.set_config(MAX_FETCH_SLOTS=2)
        ids = [dcc_fetch.enqueue_fetch("bot", f"File{i}.flac") for i in range(4)]

        dcc_fetch.check_fetch_queue()

        states = [config.fetch_queue[rid]["state"] for rid in ids]
        self.assertEqual(states.count("offered"), 2)
        self.assertEqual(states.count("pending"), 2)

    def test_already_active_fetches_count_against_the_slot_limit(self):
        self.set_config(MAX_FETCH_SLOTS=1)
        busy_id = dcc_fetch.enqueue_fetch("bot", "Busy.flac")
        config.fetch_queue[busy_id]["state"] = "receiving"
        waiting_id = dcc_fetch.enqueue_fetch("bot", "Waiting.flac")

        dcc_fetch.check_fetch_queue()

        self.assertEqual(config.fetch_queue[waiting_id]["state"], "pending")

    def test_an_offer_nobody_ever_answers_expires_as_failed(self):
        self.set_config(FETCH_OFFER_TIMEOUT=60)
        rid = dcc_fetch.enqueue_fetch("silentbot", "Ghost.flac")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time() - 61

        dcc_fetch.check_fetch_queue()

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["reason"], "no response")

    def test_an_offer_still_within_its_timeout_is_left_alone(self):
        self.set_config(FETCH_OFFER_TIMEOUT=60)
        rid = dcc_fetch.enqueue_fetch("bot", "Song.flac")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time() - 5

        dcc_fetch.check_fetch_queue()

        self.assertEqual(config.fetch_queue[rid]["state"], "offered")

    def test_disabled_feature_never_promotes_anything(self):
        config.fetch_feature_disabled = True
        rid = dcc_fetch.enqueue_fetch("bot", "Song.flac")

        dcc_fetch.check_fetch_queue()

        self.assertEqual(config.fetch_queue[rid]["state"], "pending")
        self.assertEqual(self.oserve.queued, [])

    def test_count_active_fetches_only_counts_offered_and_receiving(self):
        config.fetch_queue = {
            "a": {"state": "pending"},
            "b": {"state": "offered"},
            "c": {"state": "receiving"},
            "d": {"state": "complete"},
            "e": {"state": "failed"},
        }
        self.assertEqual(dcc_fetch.count_active_fetches(), 2)


class LoopbackTransferTests(DCCoreTestCase):
    """Real sockets, like tests/test_adminchat.py's loopback pattern - not
    everything mocked, so the actual recv/write/size-accounting loop runs."""

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-fetch-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp

        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.addCleanup(self.listener.close)

    def _offer_line(self, filename, size):
        return f"DCC SEND {filename} {ip_long('127.0.0.1')} {self.port} {size}"

    def _enqueue_and_offer(self, bot, filename):
        rid = dcc_fetch.enqueue_fetch(bot, filename)
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()
        return rid

    def test_happy_path_end_to_end(self):
        payload = b"FLAC" + (b"\x01\x02\x03\x04" * 1024)
        rid = self._enqueue_and_offer("peerbot", "Song.flac")

        def serve():
            self.listener.settimeout(5.0)
            conn, _ = self.listener.accept()
            conn.settimeout(5.0)
            conn.sendall(payload)
            conn.close()

        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()

        dcc_fetch.handle_incoming_offer(None, "peerbot", self._offer_line("Song.flac", len(payload)))
        server_thread.join(timeout=5.0)

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "complete")
        self.assertEqual(row["bytes_received"], len(payload))

        stored_path = os.path.join(self.tmp, row["stored_filename"])
        self.assertTrue(os.path.exists(stored_path))
        with open(stored_path, "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_oversized_delivery_aborts_and_deletes_the_partial_file(self):
        """The offer DECLARES a small size but the peer actually sends more -
        a lying offer. Must abort and delete rather than keep the overflow."""
        declared_size = 100
        actual_payload = b"X" * 500
        rid = self._enqueue_and_offer("lyingbot", "Trap.flac")

        def serve():
            self.listener.settimeout(5.0)
            conn, _ = self.listener.accept()
            conn.settimeout(5.0)
            conn.sendall(actual_payload)
            conn.close()

        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()

        dcc_fetch.handle_incoming_offer(None, "lyingbot", self._offer_line("Trap.flac", declared_size))
        server_thread.join(timeout=5.0)

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "failed")
        self.assertIn("more bytes", row["reason"])

        stored_name = row.get("stored_filename")
        self.assertIsNotNone(stored_name)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, stored_name)))

    def test_idle_timeout_aborts_and_deletes_the_partial_file(self):
        """The peer accepts the connection, sends a partial chunk, then goes
        silent forever without closing. IDLE_RECV_TIMEOUT must still end it."""
        real_timeout = dcc_fetch.IDLE_RECV_TIMEOUT
        dcc_fetch.IDLE_RECV_TIMEOUT = 0.5
        self.addCleanup(lambda: setattr(dcc_fetch, "IDLE_RECV_TIMEOUT", real_timeout))

        rid = self._enqueue_and_offer("slowbot", "Slow.flac")
        held_open = []

        def serve():
            self.listener.settimeout(5.0)
            conn, _ = self.listener.accept()
            conn.sendall(b"only a little bit")
            held_open.append(conn)  # kept open, never closed, never sends more

        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()

        dcc_fetch.handle_incoming_offer(None, "slowbot", self._offer_line("Slow.flac", 5000))
        server_thread.join(timeout=5.0)
        for conn in held_open:
            conn.close()

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "failed")
        self.assertIn("idle timeout", row["reason"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, row["stored_filename"])))

    def test_connect_error_is_marked_failed_not_raised(self):
        """Nothing is listening on this port - connect() must be refused, and
        that must produce a 'failed' row, not an unhandled exception in the
        daemon thread this normally runs on."""
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()  # freed immediately; nothing is listening on it now

        rid = self._enqueue_and_offer("unreachablebot", "Nope.flac")
        offer_line = f"DCC SEND Nope.flac {ip_long('127.0.0.1')} {dead_port} 10"

        dcc_fetch.handle_incoming_offer(None, "unreachablebot", offer_line)

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "failed")
        self.assertIn("connect error", row["reason"])


if __name__ == "__main__":
    unittest.main()
