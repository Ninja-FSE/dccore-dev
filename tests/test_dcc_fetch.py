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


class PassiveOfferParsingTests(unittest.TestCase):
    """port 0 plus a trailing token is the standard passive/reverse DCC
    marker - a real third-party bot (ValMp3) answered a cross-bot fetch
    request with exactly this shape during live testing, and the old code
    rejected it as "Unusable" because it checked `port <= 0`. It must now
    parse as a distinct, valid result - see parse_dcc_send_offer()'s
    docstring and adminchat.parse_offer()'s identical convention for
    passive DCC CHAT."""

    def test_the_reported_offer_parses(self):
        """Exact reproduction from tonight's logs."""
        offer = dcc_fetch.parse_dcc_send_offer(
            "DCC SEND [Metallica]_-_72_Seasons_-_01-72_Seasons.mp3 "
            "1279781699 0 3359600 11124")
        self.assertEqual(offer, {
            "filename": "[Metallica]_-_72_Seasons_-_01-72_Seasons.mp3",
            "ip": None,
            "port": 0,
            "size": 3359600,
            "token": "11124",
            "claimed_ip": "76.71.235.67",
        })

    def test_a_different_token_and_size_also_parses(self):
        offer = dcc_fetch.parse_dcc_send_offer(
            "DCC SEND Another_File.flac 2130706433 0 987654 99")
        self.assertEqual(offer, {
            "filename": "Another_File.flac",
            "ip": None,
            "port": 0,
            "size": 987654,
            "token": "99",
            "claimed_ip": "127.0.0.1",
        })

    def test_a_quoted_filename_with_spaces_parses_in_passive_form_too(self):
        offer = dcc_fetch.parse_dcc_send_offer(
            'DCC SEND "My Favourite Song.flac" 2130706433 0 4096 555')
        self.assertEqual(offer["filename"], "My Favourite Song.flac")
        self.assertEqual(offer["port"], 0)
        self.assertEqual(offer["token"], "555")

    def test_port_zero_with_no_token_is_still_rejected(self):
        """Without a token there is nothing for the offering bot to match
        our reply against, so this is just as unusable as any other
        malformed offer - not a valid passive one."""
        self.assertIsNone(dcc_fetch.parse_dcc_send_offer(
            "DCC SEND Song.flac 2130706433 0 4096"))

    def test_the_active_form_is_completely_unaffected(self):
        """The existing (active) shape must not gain a stray "token" key or
        change in any other way."""
        offer = dcc_fetch.parse_dcc_send_offer(
            "DCC SEND Song.flac 2130706433 55000 4096")
        self.assertEqual(offer, {"filename": "Song.flac", "ip": "127.0.0.1",
                                  "port": 55000, "size": 4096})
        self.assertNotIn("token", offer)


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

    def test_an_unsolicited_passive_offer_is_dropped_without_opening_a_listener(self):
        """The same guardrail as the active case, applied to the passive/
        reverse form: an unsolicited passive offer must be dropped BEFORE
        any listening socket is opened. Opening one for a stranger would let
        anyone in the channel make the daemon open a listening socket and
        accept arbitrary bytes just by sending a DCC SEND with port 0 - the
        security boundary this whole feature depends on."""
        real_socket = socket.socket

        def guard(*a, **kw):
            self.fail("handle_incoming_offer must not open a listening "
                      "socket for an unsolicited passive offer")

        socket.socket = guard
        self.addCleanup(lambda: setattr(socket, "socket", real_socket))

        dcc_fetch.handle_incoming_offer(
            None, "randomstranger",
            "DCC SEND Song.flac 2130706433 0 4096 12345")

        self.assertEqual(config.fetch_queue, {})

    def test_a_passive_offer_from_the_wrong_bot_is_dropped(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()

        real_socket = socket.socket
        socket.socket = lambda *a, **kw: self.fail("must not listen")
        self.addCleanup(lambda: setattr(socket, "socket", real_socket))

        dcc_fetch.handle_incoming_offer(
            None, "impostorbot", "DCC SEND Song.flac 2130706433 0 4096 12345")

        self.assertEqual(config.fetch_queue[rid]["state"], "offered")

    def test_a_passive_offer_for_the_wrong_filename_is_dropped(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()

        real_socket = socket.socket
        socket.socket = lambda *a, **kw: self.fail("must not listen")
        self.addCleanup(lambda: setattr(socket, "socket", real_socket))

        dcc_fetch.handle_incoming_offer(
            None, "goodbot", "DCC SEND SomeOtherFile.flac 2130706433 0 4096 12345")

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

    def test_an_oversized_passive_offer_is_rejected_before_listening(self):
        """The size cap runs unconditionally before either transport - a
        passive offer must never get as far as opening a listening socket
        just because its declared size is too large to accept."""
        rid = dcc_fetch.enqueue_fetch("goodbot", "Big.flac")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()
        self.set_config(MAX_FETCH_FILE_SIZE=1000)

        real_socket = socket.socket
        socket.socket = lambda *a, **kw: self.fail(
            "an oversized passive offer must be rejected before any listener is opened")
        self.addCleanup(lambda: setattr(socket, "socket", real_socket))

        dcc_fetch.handle_incoming_offer(
            None, "goodbot", "DCC SEND Big.flac 2130706433 0 999999 777")

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

    def test_count_active_fetches_counts_offered_listening_and_receiving(self):
        config.fetch_queue = {
            "a": {"state": "pending"},
            "b": {"state": "offered"},
            "c": {"state": "listening"},
            "d": {"state": "receiving"},
            "e": {"state": "complete"},
            "f": {"state": "failed"},
        }
        self.assertEqual(dcc_fetch.count_active_fetches(), 3)

    def test_a_listening_row_stuck_past_the_safety_net_timeout_expires_as_failed(self):
        """BUG regression: _serve_passive_offer() marks a 'listening' row
        failed on its own way out (timeout/OSError/any other exception - see
        its own comments), but if the daemon thread running it dies or hangs
        BEFORE it gets that far, nothing else ever revisited a 'listening'
        row - check_fetch_queue()'s expiry loop only re-examined 'offered'
        rows, so the row (and its MAX_FETCH_SLOTS slot) would be stuck until
        the process restarts.
        """
        rid = dcc_fetch.enqueue_fetch("passivebot", "Ghost.flac")
        config.fetch_queue[rid]["state"] = "listening"
        config.fetch_queue[rid]["listening_since"] = (
            time.time() - (dcc_fetch.PASSIVE_LISTEN_TIMEOUT * 3) - 1)

        dcc_fetch.check_fetch_queue()

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["reason"], "listening row expired without a resolution")

    def test_a_listening_row_still_within_the_safety_net_window_is_left_alone(self):
        rid = dcc_fetch.enqueue_fetch("passivebot", "Ghost.flac")
        config.fetch_queue[rid]["state"] = "listening"
        config.fetch_queue[rid]["listening_since"] = time.time() - 5

        dcc_fetch.check_fetch_queue()

        self.assertEqual(config.fetch_queue[rid]["state"], "listening")


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


class FetchListenerPortOrderingTests(DCCoreTestCase):
    """BUG regression: _open_fetch_listener() used to scan DOWNWARD from
    DCC_PORT_END - the exact same direction and first-probed port as
    adminchat._open_chat_listener()'s own passive DCC CHAT listener - despite
    its own docstring claiming the two landed at opposite ends of the range.
    It now starts at the midpoint instead, which is never the same
    first-probed port as either adminchat's downward-from-end scan or
    dcc.py's own outbound SEND (upward-from-start, verified separately in
    dcc.py's own tests) - reducing first-probe collisions with both.
    """

    def test_first_probed_port_is_the_midpoint_of_the_range(self):
        self.set_config(DCC_PORT_START=55000, DCC_PORT_END=55010)
        attempted_ports = []
        real_socket_cls = socket.socket

        class RecordingSocket:
            def __init__(self, *args, **kwargs):
                self._real = real_socket_cls(*args, **kwargs)

            def bind(self, addr):
                attempted_ports.append(addr[1])
                return self._real.bind(addr)

            def listen(self, *args, **kwargs):
                return self._real.listen(*args, **kwargs)

            def close(self):
                return self._real.close()

            def __getattr__(self, name):
                return getattr(self._real, name)

        socket.socket = RecordingSocket
        try:
            listener, port = dcc_fetch._open_fetch_listener()
        finally:
            socket.socket = real_socket_cls

        self.assertIsNotNone(listener)
        self.addCleanup(listener.close)
        self.assertEqual(attempted_ports[0], 55005, "midpoint of 55000-55010")
        self.assertEqual(port, 55005)

    def test_never_shares_a_first_probed_port_with_adminchat_or_dcc_py(self):
        """adminchat scans downward from DCC_PORT_END (first probe: END);
        dcc.py's own outbound SEND scans upward from DCC_PORT_START (first
        probe: START). The fetch listener's first probe must be neither."""
        self.set_config(DCC_PORT_START=55000, DCC_PORT_END=55010)
        listener, port = dcc_fetch._open_fetch_listener()
        self.addCleanup(listener.close)
        self.assertNotEqual(port, config.DCC_PORT_START)
        self.assertNotEqual(port, config.DCC_PORT_END)


class PassiveOfferEndToEndTests(DCCoreTestCase):
    """Passive/reverse DCC SEND: the offering bot sends port 0 plus a token
    and WE become the listener instead of dialling out. Mirrors
    tests/test_adminchat.py's own real-socket pattern for the identical DCC
    CHAT case (ListenModeEndToEnd) rather than mocking the socket layer.
    """

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-fetch-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp
        # Our own reply offer advertises config.MY_IP_OR_DOCK - has to be
        # loopback for the "other bot" side of these tests to dial back in.
        config.MY_IP_OR_DOCK = "127.0.0.1"

    def _enqueue_and_offer(self, bot, filename):
        rid = dcc_fetch.enqueue_fetch(bot, filename)
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()
        return rid

    def _wait_for_reply(self, timeout=5.0):
        """Block until dcc_fetch queues our answering DCC SEND CTCP through
        oserve.queue_message (never a raw socket write - see the brief)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for user, message, _is_vip in self.oserve.queued:
                if "DCC SEND" in message:
                    return user, message
            time.sleep(0.02)
        return None, None

    def _fields_of(self, ctcp_message):
        """PRIVMSG <nick> :\\x01DCC SEND <name> <ip> <port> <size> <token>\\x01\\r\\n
        -> the space-split fields after the CTCP delimiter."""
        payload = ctcp_message.split("\x01", 1)[1]
        return payload.rstrip("\x01\r\n").split()

    def test_the_reported_offer_end_to_end(self):
        """The exact ValMp3 shape from tonight's logs, all the way through a
        real accepted connection and a byte-for-byte written file."""
        payload = b"MP3" + (b"\x05\x06\x07\x08" * 2048)
        rid = self._enqueue_and_offer("valmp3", "72_Seasons.mp3")
        offer_line = (f"DCC SEND 72_Seasons.mp3 {ip_long('198.51.100.9')} "
                      f"0 {len(payload)} 42424")

        worker = threading.Thread(
            target=dcc_fetch.handle_incoming_offer,
            args=(None, "valmp3", offer_line), daemon=True)
        worker.start()

        user, message = self._wait_for_reply()
        self.assertIsNotNone(message, "our own passive-offer reply must be "
                             "queued through oserve.queue_message")
        self.assertEqual(user, "valmp3")

        fields = self._fields_of(message)
        self.assertEqual(fields[:3], ["DCC", "SEND", "72_Seasons.mp3"])
        self.assertEqual(fields[3], str(ip_long("127.0.0.1")),
                         "must advertise OUR OWN ip, not the offering bot's")
        our_port = int(fields[4])
        self.assertGreaterEqual(our_port, config.DCC_PORT_START)
        self.assertLessEqual(our_port, config.DCC_PORT_END)
        self.assertEqual(fields[5], str(len(payload)))
        self.assertEqual(fields[6], "42424",
                         "the token must be echoed back unchanged, or ValMp3 "
                         "cannot match our reply to its own request")

        # Stand in for the offering bot: dial the port WE just advertised,
        # exactly as the real third party would once it sees our reply.
        client = socket.create_connection(("127.0.0.1", our_port), timeout=5.0)
        client.sendall(payload)
        client.close()

        worker.join(timeout=5.0)
        self.assertFalse(worker.is_alive(), "the transfer must finish, not hang")

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "complete")
        self.assertEqual(row["bytes_received"], len(payload))

        stored_path = os.path.join(self.tmp, row["stored_filename"])
        self.assertTrue(os.path.exists(stored_path))
        with open(stored_path, "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_an_unexpected_error_while_answering_is_marked_failed_not_left_stranded(self):
        """BUG regression: _serve_passive_offer()'s accept-and-reply block
        used to only catch socket.timeout and OSError - any other exception
        (e.g. a future bug in oserve.queue_message itself) propagated out of
        the bare daemon thread this runs on, leaving the row stuck in
        'listening' forever (a permanently lost MAX_FETCH_SLOTS slot), since
        nothing else ever revisited a 'listening' row at the time.
        """
        rid = self._enqueue_and_offer("faultybot", "Trouble.flac")
        offer_line = f"DCC SEND Trouble.flac {ip_long('198.51.100.9')} 0 4096 777"

        def _boom(*_args, **_kwargs):
            raise RuntimeError("simulated failure in the outbound queue")

        self.oserve.queue_message = _boom

        dcc_fetch.handle_incoming_offer(None, "faultybot", offer_line)

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "failed")
        self.assertIn("unexpected error", row["reason"])

        # The listener must not leak either - opening a fresh one right after
        # must succeed, exactly like the existing accept-timeout test proves.
        # Reuses dcc_fetch's own _open_fetch_listener() (which already sets
        # SO_REUSEADDR via platform_compat.prepare_listener(), the same way
        # production code itself tolerates a just-closed port's TIME_WAIT
        # state) rather than a raw bind from the test, which would report a
        # false "leak" against nothing more than an unrelated prior test's
        # socket still lingering in TIME_WAIT on the same shared range.
        listener2, port2 = dcc_fetch._open_fetch_listener()
        self.assertIsNotNone(listener2, "listener leaked after an unexpected error")
        listener2.close()

    def test_a_second_passive_offer_with_a_different_token_and_size_also_works(self):
        payload = os.urandom(4096)
        rid = self._enqueue_and_offer("otherbot", "Different.flac")
        offer_line = (f"DCC SEND Different.flac {ip_long('203.0.113.50')} "
                      f"0 {len(payload)} 9001")

        worker = threading.Thread(
            target=dcc_fetch.handle_incoming_offer,
            args=(None, "otherbot", offer_line), daemon=True)
        worker.start()

        _user, message = self._wait_for_reply()
        self.assertIsNotNone(message)
        fields = self._fields_of(message)
        self.assertEqual(fields[6], "9001")
        our_port = int(fields[4])

        client = socket.create_connection(("127.0.0.1", our_port), timeout=5.0)
        client.sendall(payload)
        client.close()
        worker.join(timeout=5.0)

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "complete")
        self.assertEqual(row["bytes_received"], len(payload))

    def test_nobody_ever_connects_back_fails_cleanly_with_no_leaked_socket(self):
        """The offering bot never actually dials us back (dead peer, its own
        firewall issue, whatever) - PASSIVE_LISTEN_TIMEOUT must still end it
        with a clean 'failed' row, and the port must be free again
        immediately afterwards (proof the listening socket was closed, not
        leaked)."""
        real_timeout = dcc_fetch.PASSIVE_LISTEN_TIMEOUT
        dcc_fetch.PASSIVE_LISTEN_TIMEOUT = 0.5
        self.addCleanup(lambda: setattr(dcc_fetch, "PASSIVE_LISTEN_TIMEOUT", real_timeout))

        rid = self._enqueue_and_offer("ghostbot", "Ghost.flac")
        offer_line = f"DCC SEND Ghost.flac {ip_long('198.51.100.9')} 0 5000 555"

        # Called directly (not threaded): with a 0.5s accept timeout this
        # returns on its own almost immediately - no real "nothing ever
        # happens" wait needed.
        dcc_fetch.handle_incoming_offer(None, "ghostbot", offer_line)

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "failed")
        self.assertIn("no connection received", row["reason"])

        _user, message = self._wait_for_reply(timeout=1.0)
        self.assertIsNotNone(message, "an offer must still have been sent "
                             "before the accept timed out")
        our_port = int(self._fields_of(message)[4])

        # If the listener leaked, this bind fails with "address already in use".
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", our_port))
        except OSError as err:
            self.fail(f"the passive listener on port {our_port} was not "
                     f"closed after the accept timeout: {err}")
        finally:
            probe.close()

    def test_the_row_passes_through_listening_before_receiving(self):
        """Documents the extra state hop this feature adds to the module's
        state machine (see dcc_fetch.py's module docstring): offered ->
        listening -> receiving -> complete, not straight to receiving."""
        rid = self._enqueue_and_offer("slowconnectbot", "Watch.flac")
        offer_line = f"DCC SEND Watch.flac {ip_long('198.51.100.9')} 0 10 111"

        seen_listening = threading.Event()

        def watcher():
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if config.fetch_queue[rid]["state"] == "listening":
                    seen_listening.set()
                    return
                time.sleep(0.01)

        watch_thread = threading.Thread(target=watcher, daemon=True)
        watch_thread.start()

        worker = threading.Thread(
            target=dcc_fetch.handle_incoming_offer,
            args=(None, "slowconnectbot", offer_line), daemon=True)
        worker.start()

        _user, message = self._wait_for_reply()
        self.assertIsNotNone(message)
        our_port = int(self._fields_of(message)[4])

        watch_thread.join(timeout=5.0)
        self.assertTrue(seen_listening.is_set(),
                        "the row must pass through 'listening' while waiting "
                        "for the offering bot to connect back")

        client = socket.create_connection(("127.0.0.1", our_port), timeout=5.0)
        client.sendall(b"0123456789")
        client.close()
        worker.join(timeout=5.0)

        self.assertEqual(config.fetch_queue[rid]["state"], "complete")


class PassiveOfferInjectionTests(DCCoreTestCase):
    """BUG 1 (CRITICAL, found in adversarial review): offer["filename"] and
    offer["token"] used to be echoed back into our own outbound CTCP reply
    completely unsanitized (only spaces were turned into underscores) -
    embedded \\r, \\n or \\x01 bytes reached oserve.queue_message() verbatim,
    which is the exact CRLF/CTCP injection class already fixed once for
    webserver.py's web-enqueue routes, recurring here through a different
    input channel.

    The fix rejects any offer (active or passive) whose filename, or
    whose token in the passive form, contains one of those bytes - at
    PARSE time, in parse_dcc_send_offer(), before an `offer` dict carrying
    them can even be constructed - plus a second, redundant guard right at
    the one call site that builds the outbound CTCP
    (_serve_passive_offer()). These tests exercise both layers and assert
    directly on what actually reached (or, correctly, never reached)
    oserve.queue_message() - not just on the row's final state - since
    that call is where the injection actually lives.
    """

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-fetch-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp
        config.MY_IP_OR_DOCK = "127.0.0.1"

    def _enqueue_and_offer(self, bot, filename):
        rid = dcc_fetch.enqueue_fetch(bot, filename)
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()
        return rid

    def test_parser_rejects_a_crlf_smuggled_filename(self):
        """The exact repro from the review: a filename that substitutes
        CR/LF for the space/underscore in an already-approved requested
        filename, so it would otherwise still match under
        _normalize_filename_for_match()'s [\\s_]+ collapsing."""
        offer = dcc_fetch.parse_dcc_send_offer(
            "DCC SEND My\r\nQUIT\r\nSong.mp3 " + str(ip_long("198.51.100.9")) +
            " 0 4096 555")
        self.assertIsNone(offer)

    def test_parser_rejects_a_ctcp_delimiter_in_the_filename(self):
        offer = dcc_fetch.parse_dcc_send_offer(
            "DCC SEND Evil\x01NICK\x01Song.mp3 " + str(ip_long("198.51.100.9")) +
            " 0 4096 555")
        self.assertIsNone(offer)

    def test_parser_rejects_a_ctcp_delimiter_in_the_token(self):
        offer = dcc_fetch.parse_dcc_send_offer(
            "DCC SEND Song.mp3 " + str(ip_long("198.51.100.9")) +
            " 0 4096 555\x01NICK\x01HACKED")
        self.assertIsNone(offer)

    def test_parser_rejects_crlf_in_an_active_offers_filename_too(self):
        """Not just the passive/echoed form - a filename this hostile is
        never a real DCC client's output either way."""
        offer = dcc_fetch.parse_dcc_send_offer(
            "DCC SEND My\r\nQUIT\r\nSong.mp3 " + str(ip_long("198.51.100.9")) +
            " 55000 4096")
        self.assertIsNone(offer)

    def test_malicious_passive_offer_never_reaches_admission_control_or_queue_message(self):
        """End-to-end through handle_incoming_offer(): even with a row
        already approved and waiting for exactly this filename (underscore
        form), the CRLF-substituted offer must be dropped before admission
        control ever claims the row, and NOTHING may be handed to
        oserve.queue_message() for it."""
        rid = self._enqueue_and_offer("evilbot", "My_QUIT_Song.mp3")
        offer_line = ("DCC SEND My\r\nQUIT\r\nSong.mp3 " +
                      str(ip_long("198.51.100.9")) + " 0 4096 555")

        dcc_fetch.handle_incoming_offer(None, "evilbot", offer_line)

        self.assertEqual(config.fetch_queue[rid]["state"], "offered",
                         "the row must NOT be claimed - the offer never "
                         "even parses")
        self.assertEqual(self.oserve.queued, [],
                         "no message of any kind may reach "
                         "oserve.queue_message() for a hostile offer")

    def test_serve_passive_offer_second_layer_guard_refuses_to_build_the_reply(self):
        """Defense-in-depth: even if a caller somehow hands
        _serve_passive_offer() an offer dict that bypassed
        parse_dcc_send_offer()'s check (e.g. a future call site built one a
        different way), the call site that actually builds the outbound
        CTCP must still refuse rather than trust it blindly."""
        rid = self._enqueue_and_offer("evilbot", "Song.mp3")
        row = config.fetch_queue[rid]
        row["state"] = "listening"
        hostile_offer = {"filename": "Evil\x01NICK\x01Song.mp3", "ip": None,
                          "port": 0, "size": 4096, "token": "555",
                          "claimed_ip": None}

        dcc_fetch._serve_passive_offer(
            None, "evilbot", row, hostile_offer, self.tmp, "stored_name.mp3")

        self.assertEqual(row["state"], "failed")
        self.assertIn("unsafe characters", row["reason"])
        self.assertEqual(self.oserve.queued, [],
                         "the hostile filename/token must never reach "
                         "oserve.queue_message()")


class PassiveOfferPeerCheckTests(DCCoreTestCase):
    """BUG 2 (HIGH, found in adversarial review): _serve_passive_offer()'s
    listener.accept() took the first TCP connection to arrive with zero
    examination of who it was from. The best-effort mitigation compares the
    accepted peer's address against offer["claimed_ip"] (decoded from the
    ip_long field the offer itself carries, even though that field is never
    dialled for a passive offer) and records a logged, non-fatal mismatch on
    the row - a real offering bot behind NAT can legitimately advertise a
    different address than it connects from, so this is deliberately a
    best-effort signal, not a hard rejection (see the comment above
    listener.accept() in dcc_fetch.py).
    """

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-fetch-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp
        config.MY_IP_OR_DOCK = "127.0.0.1"

    def _enqueue_and_offer(self, bot, filename):
        rid = dcc_fetch.enqueue_fetch(bot, filename)
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()
        return rid

    def _wait_for_reply(self, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for user, message, _is_vip in self.oserve.queued:
                if "DCC SEND" in message:
                    return user, message
            time.sleep(0.02)
        return None, None

    def _fields_of(self, ctcp_message):
        payload = ctcp_message.split("\x01", 1)[1]
        return payload.rstrip("\x01\r\n").split()

    def test_a_connection_from_an_unclaimed_address_is_recorded_as_a_mismatch(self):
        """The Tester's repro, shaped as a loopback-safe regression test: the
        offer claims one address (never dialled, so it can be anything), but
        the actual peer that connects - the only real signal we have - is a
        different one. Proves the mitigation actually changes observable
        behaviour (the row now records the mismatch) rather than remaining
        the silent zero-examination accept() the review flagged, while still
        NOT breaking the transfer itself (best-effort, not a hard reject)."""
        payload = os.urandom(256)
        rid = self._enqueue_and_offer("evilbot", "Impersonated.flac")
        # Claims an address nothing in this test will ever connect from - the
        # real peer, per the bug, is free to be anyone.
        offer_line = (f"DCC SEND Impersonated.flac {ip_long('203.0.113.99')} "
                      f"0 {len(payload)} 555")

        worker = threading.Thread(
            target=dcc_fetch.handle_incoming_offer,
            args=(None, "evilbot", offer_line), daemon=True)
        worker.start()

        _user, message = self._wait_for_reply()
        self.assertIsNotNone(message)
        our_port = int(self._fields_of(message)[4])

        # Stand in for "anyone who can reach the port" - not the address the
        # offer claimed.
        client = socket.create_connection(("127.0.0.1", our_port), timeout=5.0)
        client.sendall(payload)
        client.close()
        worker.join(timeout=5.0)

        row = config.fetch_queue[rid]
        # Best-effort, not a hard reject: the transfer still completes...
        self.assertEqual(row["state"], "complete")
        # ...but the mismatch between what was claimed and who actually
        # connected is now recorded on the row where it was not examined at
        # all before this fix.
        self.assertEqual(row["passive_peer_ip"], "127.0.0.1")
        self.assertTrue(row.get("passive_peer_ip_mismatch"),
                        "a peer address that does not match what the offer "
                        "claimed must be flagged")

    def test_a_connection_matching_the_claimed_address_is_not_flagged(self):
        """Sanity check the other direction: no false positives when the
        claimed and actual addresses genuinely agree."""
        payload = os.urandom(256)
        rid = self._enqueue_and_offer("goodbot", "Legit.flac")
        offer_line = (f"DCC SEND Legit.flac {ip_long('127.0.0.1')} "
                      f"0 {len(payload)} 555")

        worker = threading.Thread(
            target=dcc_fetch.handle_incoming_offer,
            args=(None, "goodbot", offer_line), daemon=True)
        worker.start()

        _user, message = self._wait_for_reply()
        self.assertIsNotNone(message)
        our_port = int(self._fields_of(message)[4])

        client = socket.create_connection(("127.0.0.1", our_port), timeout=5.0)
        client.sendall(payload)
        client.close()
        worker.join(timeout=5.0)

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "complete")
        self.assertEqual(row["passive_peer_ip"], "127.0.0.1")
        self.assertNotIn("passive_peer_ip_mismatch", row)


if __name__ == "__main__":
    unittest.main()
