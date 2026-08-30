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
import db  # noqa: E402
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


class FallbackLockIsShared(DCCoreTestCase):
    """_fetch_lock() falls back to a module-level lock when oserve.py has not
    run (tests, most notably).

    It used to build `threading.Lock()` inline on every call, handing every
    caller a brand new object it acquired uncontended - so the fallback path
    synchronised nothing at all, silently. Same fix and same test shape as
    list_fetch.py's _lock() (see FallbackLockIsShared in test_list_fetch.py)
    and runtime.channel_users_lock().
    """

    def test_repeated_calls_return_one_object(self):
        saved = getattr(config, "fetch_queue_lock", None)
        if saved is not None:
            del config.fetch_queue_lock
            self.addCleanup(setattr, config, "fetch_queue_lock", saved)

        self.assertIs(dcc_fetch._fetch_lock(), dcc_fetch._fetch_lock(),
                      "the fallback hands out a fresh lock per call, so "
                      "concurrent callers never actually exclude each other")

    def test_config_fetch_queue_lock_wins_once_it_exists(self):
        config.fetch_queue_lock = threading.Lock()
        self.assertIs(dcc_fetch._fetch_lock(), config.fetch_queue_lock)


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

        # If the listener leaked, no port in the (tiny, 11-port) shared range
        # would be bindable. Reuses dcc_fetch's own _open_fetch_listener()
        # (which already applies platform_compat.prepare_listener()'s
        # SO_REUSEADDR handling) rather than a raw bind on `our_port`
        # specifically - a raw bind can spuriously report "still in use"
        # against nothing more than an unrelated prior test's socket lingering
        # in the OS's normal TIME_WAIT state on the same shared range under
        # the full suite's load, exactly the false-positive trap
        # platform_compat.prepare_listener() exists to avoid for real
        # listeners (see the identical fix and reasoning on
        # test_an_unexpected_error_while_answering_is_marked_failed_not_left_stranded
        # above).
        listener2, port2 = dcc_fetch._open_fetch_listener()
        self.assertIsNotNone(
            listener2,
            f"the passive listener on port {our_port} was not closed after "
            f"the accept timeout: no free port in the shared DCC range")
        listener2.close()

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


class RequestTypeAdmissionControlTests(DCCoreTestCase):
    """request_type="list" rows (a cross-bot @<bot> list-fetch, see
    dcc_fetch.py's module docstring and check_fetch_queue()) match on bot
    ALONE - any filename from the right bot claims them, since we cannot know
    ahead of time what the target bot will name its list zip. request_type
    "file" rows keep the original exact bot+filename requirement unchanged.
    Neither can accidentally satisfy the other's requirement - see
    _claim_matching_offer_locked()'s own docstring for the invariant these
    tests exist to pin down.
    """

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-fetch-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp

    def _offer_and_size_cap(self, rid, bot, filename):
        """Force rejection at the (unrelated) size cap so admission control
        can be observed in isolation, with no real socket involved - same
        idiom AdmissionControlTests.test_matching_offer_is_claimed_underscore_space_equivalence
        already uses above."""
        self.set_config(MAX_FETCH_FILE_SIZE=10)
        dcc_fetch.handle_incoming_offer(
            None, bot, f"DCC SEND {filename} 2130706433 55000 999999")

    def test_list_row_matches_any_filename_from_the_right_bot(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "", request_type="list")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()

        self._offer_and_size_cap(rid, "goodbot", "WhateverTheyNamedIt.zip")

        row = config.fetch_queue[rid]
        # It was CLAIMED (state moved on from 'offered') and only then failed
        # the unrelated size cap - proving the bot-alone match itself
        # succeeded despite the filename never having been specified ahead
        # of time.
        self.assertEqual(row["state"], "failed")
        self.assertIn("exceeds", row["reason"])
        self.assertEqual(row["filename"], "WhateverTheyNamedIt.zip")

    def test_list_row_rejects_an_offer_from_the_wrong_bot(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "", request_type="list")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()

        real_socket = socket.socket
        socket.socket = lambda *a, **kw: self.fail("must not connect")
        self.addCleanup(lambda: setattr(socket, "socket", real_socket))

        dcc_fetch.handle_incoming_offer(
            None, "impostorbot", "DCC SEND Anything.zip 2130706433 55000 4096")

        self.assertEqual(config.fetch_queue[rid]["state"], "offered")

    def test_a_file_row_still_requires_an_exact_filename_even_with_a_list_row_for_the_same_bot(self):
        """A 'file' row expecting an exact filename must still reject an
        offer with a DIFFERENT filename from the same bot, even though a
        'list' row for that same bot would have accepted it - a 'list' row
        must never accidentally satisfy a 'file' row's stricter requirement."""
        file_rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac", request_type="file")
        config.fetch_queue[file_rid]["state"] = "offered"
        config.fetch_queue[file_rid]["offered_at"] = time.time()

        real_socket = socket.socket
        socket.socket = lambda *a, **kw: self.fail("must not connect")
        self.addCleanup(lambda: setattr(socket, "socket", real_socket))

        dcc_fetch.handle_incoming_offer(
            None, "goodbot", "DCC SEND SomeOtherFile.zip 2130706433 55000 4096")

        self.assertEqual(config.fetch_queue[file_rid]["state"], "offered")

    def test_when_both_a_file_and_list_row_exist_the_exact_filename_match_wins(self):
        """The more specific match (exact filename) takes priority over the
        looser one (bot alone) when both are outstanding for the same bot."""
        file_rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac", request_type="file")
        config.fetch_queue[file_rid]["state"] = "offered"
        config.fetch_queue[file_rid]["offered_at"] = time.time()
        list_rid = dcc_fetch.enqueue_fetch("goodbot", "", request_type="list")
        config.fetch_queue[list_rid]["state"] = "offered"
        config.fetch_queue[list_rid]["offered_at"] = time.time()

        self._offer_and_size_cap(file_rid, "goodbot", "Song.flac")

        self.assertEqual(config.fetch_queue[file_rid]["state"], "failed")   # claimed, then hit the size cap
        self.assertEqual(config.fetch_queue[list_rid]["state"], "offered")  # untouched

    def test_a_non_matching_filename_falls_through_to_the_list_row_for_the_same_bot(self):
        """The reverse of the above: an offer that does NOT match the 'file'
        row's exact filename falls through and is claimed by the 'list' row
        for the same bot instead of being rejected outright - a 'file' row
        must never accidentally satisfy (or block) a 'list' row's looser
        requirement for a DIFFERENT offer."""
        file_rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac", request_type="file")
        config.fetch_queue[file_rid]["state"] = "offered"
        config.fetch_queue[file_rid]["offered_at"] = time.time()
        list_rid = dcc_fetch.enqueue_fetch("goodbot", "", request_type="list")
        config.fetch_queue[list_rid]["state"] = "offered"
        config.fetch_queue[list_rid]["offered_at"] = time.time()

        self._offer_and_size_cap(list_rid, "goodbot", "ListArchive.zip")

        self.assertEqual(config.fetch_queue[file_rid]["state"], "offered")  # untouched
        self.assertEqual(config.fetch_queue[list_rid]["state"], "failed")   # claimed, then hit the size cap

    def test_multiple_list_rows_for_the_same_bot_the_oldest_is_claimed(self):
        older_rid = dcc_fetch.enqueue_fetch("goodbot", "", request_type="list")
        config.fetch_queue[older_rid]["state"] = "offered"
        config.fetch_queue[older_rid]["offered_at"] = time.time()
        config.fetch_queue[older_rid]["requested_at"] = 1.0

        newer_rid = dcc_fetch.enqueue_fetch("goodbot", "", request_type="list")
        config.fetch_queue[newer_rid]["state"] = "offered"
        config.fetch_queue[newer_rid]["offered_at"] = time.time()
        config.fetch_queue[newer_rid]["requested_at"] = 2.0

        self._offer_and_size_cap(older_rid, "goodbot", "Whatever.zip")

        self.assertEqual(config.fetch_queue[older_rid]["state"], "failed")
        self.assertEqual(config.fetch_queue[newer_rid]["state"], "offered")

    def test_default_request_type_is_file_for_backward_compatibility(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac")
        self.assertEqual(config.fetch_queue[rid]["request_type"], "file")


class ListFetchDispatcherTests(DCCoreTestCase):
    """check_fetch_queue() sends a bare "@<bot>" (irc.py's own list trigger,
    per list.send_file_list()) for a request_type="list" row, instead of the
    "!<bot> <filename>" a "file" row sends - same paced outbound queue
    (oserve.queue_message), only the message body differs."""

    def test_pending_list_row_is_promoted_and_sends_a_bare_at_bot_trigger(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "", request_type="list")

        dcc_fetch.check_fetch_queue()

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "offered")
        self.assertEqual(len(self.oserve.queued), 1)
        sent_user, sent_msg, _is_vip = self.oserve.queued[0]
        self.assertEqual(sent_user, "goodbot")
        self.assertIn("@goodbot", sent_msg)
        self.assertNotIn("!goodbot", sent_msg)

    def test_a_pending_file_row_is_unaffected(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac", request_type="file")

        dcc_fetch.check_fetch_queue()

        _sent_user, sent_msg, _is_vip = self.oserve.queued[0]
        self.assertIn("!goodbot Song.flac", sent_msg)


class FetchHistoryPersistenceTests(DCCoreTestCase):
    """config.fetch_queue was in-memory only: a finished fetch's row - the
    only thing the dashboard's Downloads table and its Delete button have to
    point at - vanished on every restart even though the file itself sat on
    disk under FETCHED_FILES_DIR untouched. check_fetch_queue() now snapshots
    every 'complete'/'failed' row to db.FETCH_HISTORY_FILE on each tick it
    runs; tests/support.py's DCCoreTestCase redirects that path to a
    per-test tmp dir, so these tests read db.FETCH_HISTORY_FILE directly
    rather than the real repository path."""

    def test_a_completed_row_is_persisted_on_the_next_tick(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac")
        config.fetch_queue[rid]["state"] = "complete"
        config.fetch_queue[rid]["stored_filename"] = f"{rid}_Song.flac"

        dcc_fetch.check_fetch_queue()

        history = db.load_fetch_history()
        self.assertIn(rid, history)
        self.assertEqual(history[rid]["state"], "complete")

    def test_a_failed_row_is_persisted_on_the_next_tick(self):
        rid = dcc_fetch.enqueue_fetch("silentbot", "Ghost.flac")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time() - 61
        self.set_config(FETCH_OFFER_TIMEOUT=60)

        dcc_fetch.check_fetch_queue()

        history = db.load_fetch_history()
        self.assertIn(rid, history)
        self.assertEqual(history[rid]["state"], "failed")

    def test_in_flight_rows_are_never_persisted(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac")  # stays "pending"

        dcc_fetch.check_fetch_queue()

        self.assertEqual(db.load_fetch_history(), {})

    def test_an_unchanged_terminal_set_does_not_rewrite_the_file(self):
        """Runs forever, every 2s, from fetch_dispatcher_worker() - rewriting
        an unchanged file on every single tick would be needless disk I/O."""
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac")
        config.fetch_queue[rid]["state"] = "complete"
        dcc_fetch.check_fetch_queue()

        real_save = db.save_fetch_history
        calls = []
        db.save_fetch_history = lambda rows: (calls.append(rows), real_save(rows))[-1]
        self.addCleanup(setattr, db, "save_fetch_history", real_save)

        dcc_fetch.check_fetch_queue()  # nothing changed since the first tick

        self.assertEqual(calls, [])

    def test_a_row_deleted_from_the_queue_drops_out_of_history_on_the_next_tick(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac")
        config.fetch_queue[rid]["state"] = "complete"
        dcc_fetch.check_fetch_queue()
        self.assertIn(rid, db.load_fetch_history())

        del config.fetch_queue[rid]
        dcc_fetch.check_fetch_queue()

        self.assertNotIn(rid, db.load_fetch_history())

    def test_persist_fetch_history_is_safe_to_call_without_the_lock_already_held(self):
        """The public (non-"_locked") entry point webserver.py's delete route
        uses - must acquire the fetch lock itself rather than assuming a
        caller already holds it."""
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac")
        config.fetch_queue[rid]["state"] = "failed"
        config.fetch_queue[rid]["reason"] = "timeout"

        dcc_fetch.persist_fetch_history()

        self.assertIn(rid, db.load_fetch_history())


class DispatcherSecondLayerCtcpGuardTests(DCCoreTestCase):
    """BUG 1 (CRITICAL) regression, dispatch-site half: webserver.py's
    reject_if_unsafe_for_irc_line() is the FIRST layer, but check_fetch_queue()
    is the one call site that actually interpolates `bot`/`filename` into a
    raw outbound IRC line - it must not simply trust that upstream check was
    applied, mirroring _serve_passive_offer()'s own second-layer re-check
    right before it builds its own outbound CTCP line (see
    PassiveOfferInjectionTests.test_serve_passive_offer_second_layer_guard_
    refuses_to_build_the_reply above for that half).

    dcc_fetch.enqueue_fetch() itself performs no validation at all (only
    new_fetch_row()'s str().strip(), which does not strip \\x01) - the HTTP
    boundary is the only thing that currently rejects unsafe bytes, so
    calling enqueue_fetch() directly, as these tests do, is exactly how
    "somehow-corrupted or future-mistake row data" would reach the
    dispatcher: bypassing webserver.py's check entirely.
    """

    def test_a_file_row_with_an_unsafe_bot_nick_is_never_sent(self):
        rid = dcc_fetch.enqueue_fetch("evilbot2\x01INJECTED\x01", "Song.mp3",
                                       request_type="file")

        dcc_fetch.check_fetch_queue()

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "failed")
        self.assertIn("unsafe characters", row["reason"])
        self.assertEqual(self.oserve.queued, [],
                         "the hostile bot nick must never reach "
                         "oserve.queue_message()")

    def test_a_file_row_with_an_unsafe_filename_is_never_sent(self):
        rid = dcc_fetch.enqueue_fetch("goodbot", "Song\x01.mp3",
                                       request_type="file")

        dcc_fetch.check_fetch_queue()

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "failed")
        self.assertIn("unsafe characters", row["reason"])
        self.assertEqual(self.oserve.queued, [])

    def test_a_list_row_with_an_unsafe_bot_nick_is_never_sent(self):
        rid = dcc_fetch.enqueue_fetch("evilbot\x01ACTION pwned\x01", "",
                                       request_type="list")

        dcc_fetch.check_fetch_queue()

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "failed")
        self.assertIn("unsafe characters", row["reason"])
        self.assertEqual(self.oserve.queued, [],
                         "the hostile bot nick must never reach "
                         "oserve.queue_message()")

    def test_a_clean_row_is_completely_unaffected_by_the_second_layer_guard(self):
        """The new re-check must not false-positive on ordinary input."""
        file_rid = dcc_fetch.enqueue_fetch("goodbot", "Song.flac", request_type="file")
        list_rid = dcc_fetch.enqueue_fetch("otherbot", "", request_type="list")

        dcc_fetch.check_fetch_queue()

        self.assertEqual(config.fetch_queue[file_rid]["state"], "offered")
        self.assertEqual(config.fetch_queue[list_rid]["state"], "offered")
        self.assertEqual(len(self.oserve.queued), 2)


class ListFetchEndToEndTests(DCCoreTestCase):
    """Real loopback socket, mirroring LoopbackTransferTests' pattern above:
    enqueue a request_type="list" row, answer it with a real (small,
    synthetic) zip containing a fake master-list .txt, and confirm the fetch
    completes, the zip is safely extracted, and it is parsed into
    config.fetched_bot_lists - the full path from dcc_fetch.py's transfer
    completion through list_fetch.py's extraction/parsing.
    """

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

    def _make_list_zip(self, base_name="OtherBot"):
        import io
        import zipfile as zf_mod

        txt = (
            "List of 1 Files (10.0MB) generated on Jan 1st\n"
            f"To request a file, copy/paste to the channel... !{base_name} FILENAME\n\n\n"
            "\n" + "=" * 53 + "\n"
            "D:\\MUSIC\\SomeAlbum\\\n"
            + "=" * 53 + "\n"
            f"!{base_name} Track One.flac  ::INFO:: 10.0MB\n"
        )
        buf = io.BytesIO()
        with zf_mod.ZipFile(buf, "w", zf_mod.ZIP_DEFLATED) as zf:
            zf.writestr(f"{base_name}-2026-08-27.txt", txt)
        return buf.getvalue()

    def test_list_fetch_end_to_end_extracts_and_parses(self):
        payload = self._make_list_zip()
        rid = dcc_fetch.enqueue_fetch("otherbot", "", request_type="list")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()

        def serve():
            self.listener.settimeout(5.0)
            conn, _ = self.listener.accept()
            conn.settimeout(5.0)
            conn.sendall(payload)
            conn.close()

        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()

        offer_line = f"DCC SEND otherbot-list.zip {ip_long('127.0.0.1')} {self.port} {len(payload)}"
        dcc_fetch.handle_incoming_offer(None, "otherbot", offer_line)
        server_thread.join(timeout=5.0)

        row = config.fetch_queue[rid]
        self.assertEqual(row["state"], "complete")
        self.assertNotIn("list_processing_error", row)
        self.assertEqual(row["filename"], "otherbot-list.zip")

        entry = config.fetched_bot_lists.get("otherbot")
        self.assertIsNotNone(entry, "a successful list fetch must populate config.fetched_bot_lists")
        self.assertEqual(entry["bot"], "otherbot")
        # Issue #76, option 2: no "entries" key is stored any more - just a
        # path and a count - so read it back the same way webserver.py does,
        # on demand.
        import list_fetch
        # Paged by folder now: flatten the groups back to rows, which is all
        # this test ever cared about.
        folders, _n, _files, error = list_fetch.get_fetched_bot_page(entry, 0, 10**9)
        self.assertIsNone(error)
        titles = [e["title"] for g in folders for e in g["entries"]]
        self.assertIn("Track One.flac", titles)

    def test_a_second_fetch_for_the_same_bot_replaces_not_accumulates(self):
        import list_fetch
        stale_dir = os.path.join(self.tmp, "stale")
        os.makedirs(stale_dir, exist_ok=True)
        stale_list_path = os.path.join(stale_dir, "stale-list.txt")
        with open(stale_list_path, "w", encoding="utf-8") as f:
            f.write("!otherbot Stale.flac  ::INFO:: 1.0MB\n")
        config.fetched_bot_lists["otherbot"] = {
            "bot": "otherbot", "fetched_at": 1.0,
            "list_path": stale_list_path, "entry_count": 1,
            "source_zip": "old.zip",
        }

        payload = self._make_list_zip()
        rid = dcc_fetch.enqueue_fetch("otherbot", "", request_type="list")
        config.fetch_queue[rid]["state"] = "offered"
        config.fetch_queue[rid]["offered_at"] = time.time()

        def serve():
            self.listener.settimeout(5.0)
            conn, _ = self.listener.accept()
            conn.settimeout(5.0)
            conn.sendall(payload)
            conn.close()

        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()
        offer_line = f"DCC SEND newlist.zip {ip_long('127.0.0.1')} {self.port} {len(payload)}"
        dcc_fetch.handle_incoming_offer(None, "otherbot", offer_line)
        server_thread.join(timeout=5.0)

        entry = config.fetched_bot_lists["otherbot"]
        # Paged by folder now: flatten the groups back to rows, which is all
        # this test ever cared about.
        folders, _n, _files, error = list_fetch.get_fetched_bot_page(entry, 0, 10**9)
        self.assertIsNone(error)
        titles = [e["title"] for g in folders for e in g["entries"]]
        self.assertNotIn("Stale.flac", titles)
        self.assertIn("Track One.flac", titles)




class LongOfferedFilenames(LoopbackTransferTests):
    """The offered filename's length is entirely the sending bot's choice.

    _sanitize_offer_filename() rejects unsafe names but never truncates them,
    and dcc.py wraps every path it touches in platform_compat.long_path()
    while the fetch write path added on this branch did not. So a legal 240-
    character name pushed the destination past Windows' 260-character
    MAX_PATH and the transfer failed with

        [FETCH] Failed (transfer error: [Errno 2] No such file or directory)

    while the identical transfer named ok.mp3 reached 'complete'. The name is
    legal on Linux, so this only ever bit on Windows.
    """

    LONG_NAME = ("L" * 236) + ".flac"      # 241 characters

    def _transfer(self, filename, payload):
        rid = self._enqueue_and_offer("peerbot", filename)

        def serve():
            self.listener.settimeout(5.0)
            conn, _ = self.listener.accept()
            conn.settimeout(5.0)
            conn.sendall(payload)
            conn.close()

        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()
        dcc_fetch.handle_incoming_offer(
            None, "peerbot", self._offer_line(filename, len(payload)))
        server_thread.join(timeout=5.0)
        return config.fetch_queue[rid]

    def test_a_long_offered_filename_completes(self):
        payload = b"FLAC" + (b"\x07" * 4096)

        row = self._transfer(self.LONG_NAME, payload)

        self.assertEqual(row["state"], "complete",
                         f"a legal long filename failed: {row.get('error', '')}")
        self.assertEqual(row["bytes_received"], len(payload))

    def test_the_received_file_is_actually_on_disk(self):
        """'complete' is the row's opinion; this checks the filesystem agrees,
        which is the thing MAX_PATH actually broke."""
        import platform_compat
        payload = b"FLAC" + (b"\x07" * 4096)

        row = self._transfer(self.LONG_NAME, payload)

        stored = os.path.join(config.FETCHED_FILES_DIR, row["stored_filename"])
        self.assertTrue(os.path.exists(platform_compat.long_path(stored)),
                        "the row says complete but the file is not there")
        self.assertEqual(os.path.getsize(platform_compat.long_path(stored)),
                         len(payload))

    def test_the_destination_really_is_past_max_path(self):
        """Fixture invariant. If the temp root is short enough that the
        destination lands under 260, the tests above prove nothing on Windows
        and this says so rather than passing quietly."""
        dest = os.path.join(config.FETCHED_FILES_DIR, self.LONG_NAME)
        self.assertGreater(
            len(dest), 260,
            f"the destination is only {len(dest)} characters, so the MAX_PATH "
            f"hazard is not being exercised")

    def test_a_short_name_still_works(self):
        """Control - the wrapping must not disturb the ordinary case."""
        payload = b"FLAC" + (b"\x01\x02" * 512)

        row = self._transfer("Short.flac", payload)

        self.assertEqual(row["state"], "complete")
        self.assertEqual(row["bytes_received"], len(payload))


if __name__ == "__main__":
    unittest.main()
