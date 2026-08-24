"""Tests for platform_compat - the few places Linux and Windows genuinely differ.

These run on both platforms in CI and assert the correct behaviour for whichever
one they are on, so a change that is right on Linux and wrong on Windows fails
before it merges rather than after somebody tries the port.
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

import platform_compat  # noqa: E402


class RarCommandTests(unittest.TestCase):
    """dcc.py used the bare name "rar", which WinRAR does not put on PATH."""

    def test_configured_absolute_path_wins(self):
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as handle:
            fake = handle.name
        try:
            self.assertEqual(platform_compat.rar_command(fake), fake)
        finally:
            os.unlink(fake)

    def test_configured_path_that_does_not_exist_falls_back_to_lookup(self):
        missing = os.path.join(tempfile.gettempdir(), "definitely-not-here-rar")
        # Falls through to the PATH search; on a machine without rar that is None,
        # and on one with it, whatever was found. Either way it must not return
        # the bogus configured value.
        self.assertNotEqual(platform_compat.rar_command(missing), missing)

    def test_returns_none_or_an_existing_file(self):
        """Never return a name that cannot actually be executed."""
        found = platform_compat.rar_command(None)
        if found is not None:
            self.assertTrue(os.path.isfile(found), f"returned {found!r} which is not a file")

    def test_no_argument_is_accepted(self):
        platform_compat.rar_command()


class ListenerOptionTests(unittest.TestCase):
    """SO_REUSEADDR has the OPPOSITE meaning on Windows - it permits hijacking."""

    def setUp(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(self.sock.close)

    def test_prepare_listener_returns_the_socket(self):
        self.assertIs(platform_compat.prepare_listener(self.sock), self.sock)

    def test_correct_option_for_this_platform(self):
        platform_compat.prepare_listener(self.sock)
        if platform_compat.IS_WINDOWS:
            # SO_EXCLUSIVEADDRUSE must be on, and SO_REUSEADDR must NOT be, or
            # another process could bind the same DCC port and take the transfer.
            self.assertTrue(
                self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE),
                "Windows listener must set SO_EXCLUSIVEADDRUSE",
            )
            self.assertFalse(
                self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR),
                "Windows listener must NOT set SO_REUSEADDR - it allows port hijacking",
            )
        else:
            # POSIX needs it, or a DCC port stays unusable through TIME_WAIT and
            # there are only eleven of them.
            self.assertTrue(
                self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR),
                "POSIX listener must set SO_REUSEADDR",
            )

    def test_a_prepared_listener_can_actually_bind_and_accept(self):
        platform_compat.prepare_listener(self.sock)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.assertGreater(self.sock.getsockname()[1], 0)


class KeepaliveTests(unittest.TestCase):
    def setUp(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(self.sock.close)

    def test_keepalive_is_enabled_on_every_platform(self):
        platform_compat.apply_keepalive(self.sock)
        self.assertTrue(self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE))

    def test_linux_timing_knobs_are_applied_where_they_exist(self):
        platform_compat.apply_keepalive(self.sock, idle=7, interval=3, count=2)
        if hasattr(socket, "TCP_KEEPIDLE"):
            self.assertEqual(self.sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE), 7)

    def test_missing_knobs_do_not_raise(self):
        """The whole point of the guard: no AttributeError on a platform without them."""
        platform_compat.apply_keepalive(self.sock)


class LongPathTests(unittest.TestCase):
    """Windows caps paths at 260 chars; a deep music library passes that easily."""

    def test_identity_on_posix(self):
        if platform_compat.IS_WINDOWS:
            self.skipTest("POSIX behaviour")
        p = "/mnt/nfs-musik/Artist/Album/Track.flac"
        self.assertEqual(platform_compat.long_path(p), p)

    def test_windows_gets_the_extended_prefix(self):
        if not platform_compat.IS_WINDOWS:
            self.skipTest("Windows behaviour")
        result = platform_compat.long_path("C:\\music\\Artist\\Track.flac")
        self.assertTrue(result.startswith("\\\\?\\"), result)

    def test_already_prefixed_is_left_alone(self):
        if not platform_compat.IS_WINDOWS:
            self.skipTest("Windows behaviour")
        already = "\\\\?\\C:\\music\\Track.flac"
        self.assertEqual(platform_compat.long_path(already), already)

    def test_unc_share_uses_the_unc_form(self):
        if not platform_compat.IS_WINDOWS:
            self.skipTest("Windows behaviour")
        result = platform_compat.long_path("\\\\nas\\music\\Track.flac")
        self.assertTrue(result.startswith("\\\\?\\UNC\\"), result)

    def test_empty_and_none_are_passed_through(self):
        self.assertEqual(platform_compat.long_path(""), "")
        self.assertIsNone(platform_compat.long_path(None))

    def test_a_deep_path_survives_a_round_trip_to_disk(self):
        """The behaviour that matters: open a file whose path is long."""
        root = tempfile.mkdtemp(prefix="dccore-long-")
        deep = root
        for i in range(12):
            deep = os.path.join(deep, "Artist Name With A Long Title %02d" % i)
        try:
            os.makedirs(platform_compat.long_path(deep), exist_ok=True)
        except OSError:
            self.skipTest("filesystem refused the deep path outright")
        target = os.path.join(deep, "12 - A Rather Long Classical Track Title.flac")
        with open(platform_compat.long_path(target), "wb") as handle:
            handle.write(b"audio")
        self.assertTrue(os.path.exists(platform_compat.long_path(target)))
        self.assertGreater(len(target), 260 if platform_compat.IS_WINDOWS else 0)


class MissingRarBinaryTests(unittest.TestCase):
    """A machine with no rar installed must degrade, not wedge.

    This is the most likely first failure on a fresh Windows box, and it is
    exactly the case CI exposed: the runners have no rar, so resolving the
    binary raises before subprocess.run is ever reached. Both process-wide
    interlocks must still be released, or folder packing is dead for every user
    until the daemon restarts.
    """

    def setUp(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))
        from tests.support import (reset_config, install_fake_oserve, silence_debug,
                                   no_disk_writes, RecordingSocket, TempTree)
        import announce, db, dcc
        self.config = reset_config()
        install_fake_oserve()
        silence_debug(announce)
        no_disk_writes(db)
        self.dcc = dcc
        self.sock = RecordingSocket()
        self.tree = TempTree()
        self.addCleanup(self.tree.cleanup)
        self.config.FILE_DIRECTORY = self.tree.music
        self.config.TMP_ZIP_DIR = os.path.join(self.tree.root, "tmp_zips")

        self._real = platform_compat.rar_command
        platform_compat.rar_command = lambda configured=None: None
        self.addCleanup(lambda: setattr(platform_compat, "rar_command", self._real))

    def test_missing_rar_releases_both_interlocks(self):
        row = {"file": "Album.rar", "path": self.tree.album, "channel": "#c",
               "user_raw": "dave", "is_unpacked_rar_folder": True, "is_temporary_zip": True}
        self.config.dcc_queue = {"dave": [row]}
        self.config.channel_users = {"#c": {"dave"}}
        self.config.bot_joined_channel = True

        self.dcc.check_queue_and_send(self.sock, "dave")
        time.sleep(0.4)

        self.assertFalse(self.config.rar_inprogress,
                         "a missing rar must not latch rar_inprogress - that kills packing "
                         "for every user until restart")
        self.assertNotIn("dave", getattr(self.config, "user_processing_lock", set()))

    def test_missing_rar_charges_the_row_rather_than_looping(self):
        row = {"file": "Album.rar", "path": self.tree.album, "channel": "#c",
               "user_raw": "dave", "is_unpacked_rar_folder": True, "is_temporary_zip": True}
        self.config.dcc_queue = {"dave": [row]}
        self.config.channel_users = {"#c": {"dave"}}
        self.config.bot_joined_channel = True

        self.dcc.check_queue_and_send(self.sock, "dave")
        time.sleep(0.4)

        self.assertEqual(row.get("send_fails"), 1,
                         "the failure must be charged to the retry budget, not retried forever")


class DescribeTests(unittest.TestCase):
    def test_describe_names_the_platform_and_rar_state(self):
        line = platform_compat.describe()
        self.assertIn("platform=", line)
        self.assertIn("python=", line)
        self.assertIn("rar=", line)


class WiringTests(unittest.TestCase):
    """The daemon must actually use these rather than keeping its own copies."""

    def test_dcc_uses_prepare_listener_not_a_bare_reuseaddr(self):
        source = open(os.path.join(REPO_ROOT, "dcc.py"), encoding="utf-8").read()
        self.assertIn("platform_compat.prepare_listener", source)
        self.assertNotIn("socket.SO_REUSEADDR", source,
                         "dcc.py must not set SO_REUSEADDR directly - it is wrong on Windows")

    def test_dcc_resolves_the_rar_binary(self):
        source = open(os.path.join(REPO_ROOT, "dcc.py"), encoding="utf-8").read()
        self.assertIn("platform_compat.rar_command", source)
        self.assertNotIn('["rar", "a"', source, "dcc.py must not hardcode the bare name 'rar'")

    def test_irc_uses_apply_keepalive(self):
        source = open(os.path.join(REPO_ROOT, "irc.py"), encoding="utf-8").read()
        self.assertIn("platform_compat.apply_keepalive", source)

    def test_config_supports_a_local_override(self):
        source = open(os.path.join(REPO_ROOT, "config.py"), encoding="utf-8").read()
        self.assertIn("from local_config import *", source)

    def test_local_config_is_gitignored(self):
        ignored = open(os.path.join(REPO_ROOT, ".gitignore"), encoding="utf-8").read()
        self.assertIn("local_config.py", ignored)


if __name__ == "__main__":
    unittest.main()
