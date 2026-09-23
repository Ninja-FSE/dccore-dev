"""#567: duration and quality after the size, on the list's MP3 and FLAC rows.

    !DCCore Artist - Album - 01 - Track.mp3  ::INFO:: 10.3MB 4m31s 320/44.1/JS

Every audio file here is built byte by byte in the test - frame headers, a
Xing / Info / VBRI header, a FLAC STREAMINFO block - so the expected numbers
come from the formats' own arithmetic, not from a sample file nobody can
regenerate. Names are invented.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import announce  # noqa: E402
import audio_info  # noqa: E402
import list as list_mod  # noqa: E402
import update_list  # noqa: E402

from tests.support import DCCoreTestCase, RecordingSocket  # noqa: E402
from tests.test_webserver import write_master_list  # noqa: E402

# MPEG-1 Layer III, 128 kbps, 44.1 kHz, no CRC, no padding. The last byte is
# the channel mode: 0x00 stereo, 0x40 joint stereo, 0x80 dual, 0xC0 mono.
MPEG1_128 = b"\xff\xfb\x90"
FRAME_128 = 417  # 144 * 128000 / 44100, rounded down
# MPEG-2 Layer III, 64 kbps, 22.05 kHz: 72 * 64000 / 22050 -> 208 bytes.
MPEG2_64 = b"\xff\xf3\x80"
FRAME_64 = 208


def frames(count, header=MPEG1_128, mode=0x40, length=FRAME_128):
    one = header + bytes([mode]) + b"\x00" * (length - 4)
    return one * count


def id3v2(body_size=1000):
    size = bytes([(body_size >> 21) & 0x7F, (body_size >> 14) & 0x7F,
                  (body_size >> 7) & 0x7F, body_size & 0x7F])
    # A frame-sync pattern inside the tag, which must be skipped with it.
    body = (b"\xff\xfb\x90\x40" * 8).ljust(body_size, b"\x00")
    return b"ID3\x03\x00\x00" + size + body


def id3v1():
    return b"TAG" + b"\x00" * 125


def xing_frame(tag, frame_count, byte_count, mode=0x40, side=32):
    frame = bytearray(frames(1, mode=mode))
    at = 4 + side
    frame[at:at + 16] = (tag + (3).to_bytes(4, "big") + frame_count.to_bytes(4, "big")
                         + byte_count.to_bytes(4, "big"))
    return bytes(frame)


def vbri_frame(frame_count, byte_count):
    frame = bytearray(frames(1))
    frame[36:54] = (b"VBRI" + (1).to_bytes(2, "big") + b"\x00\x00" + b"\x00\x4b"
                    + byte_count.to_bytes(4, "big") + frame_count.to_bytes(4, "big"))
    return bytes(frame)


def flac(rate=44100, channels=2, bits=16, samples=44100 * 2, audio_bytes=278750, padding=100):
    word = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | samples
    streaminfo = (b"\x10\x00\x10\x00" + b"\x00" * 6 + word.to_bytes(8, "big") + b"\x00" * 16)
    blocks = (bytes([0x00]) + len(streaminfo).to_bytes(3, "big") + streaminfo
              + bytes([0x81]) + padding.to_bytes(3, "big") + b"\x00" * padding)
    return b"fLaC" + blocks + b"\x55" * audio_bytes


class FileCase(DCCoreTestCase):
    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()

    def write(self, name, data, folder=None):
        directory = os.path.join(self.tree.music, folder) if folder else self.tree.music
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def described(self, name, data):
        return audio_info.describe(audio_info.read(self.write(name, data)))


class ReadingMp3(FileCase):
    def test_cbr_from_its_bytes_and_bitrate(self):
        # 1000 frames of 417 bytes at 128 kbps: 417000 * 8 / 128000 = 26.06 s.
        self.assertEqual(self.described("cbr.mp3", frames(1000)), "0m26s 128/44.1/JS")

    def test_tags_at_either_end_are_not_audio(self):
        # 940 frames: 24.499 s of audio. Counting the ID3v1 tag's 128 bytes
        # as audio would make it 24.507 s, and the row would say 0m25s.
        data = id3v2() + frames(940) + id3v1()
        self.assertEqual(self.described("tagged.mp3", data), "0m24s 128/44.1/JS")

    def test_a_tag_bigger_than_the_search_window_is_skipped(self):
        """Embedded cover art makes ID3v2 tags of hundreds of KB; the frame
        search only looks MP3_SYNC_WINDOW past where the tag ends."""
        data = id3v2(body_size=audio_info.MP3_SYNC_WINDOW * 2) + frames(1000)
        self.assertEqual(self.described("cover-art.mp3", data), "0m26s 128/44.1/JS")

    def test_a_false_sync_before_the_audio_is_passed_over(self):
        """A header-shaped pattern whose "next frame" is not there."""
        junk = bytes([0xFF, 0xFB, 0xE0, 0x40]) + bytes(10)  # claims 320 kbps
        info = audio_info.read(self.write("junk.mp3", junk + frames(1000)))
        self.assertEqual(audio_info.describe(info), "0m26s 128/44.1/JS")

    def test_the_channel_modes(self):
        for mode, word in ((0x00, "S"), (0x40, "JS"), (0x80, "DC"), (0xC0, "M")):
            self.assertTrue(self.described(f"mode{mode}.mp3", frames(1000, mode=mode)).endswith("/" + word))

    def test_vbr_is_the_xing_frame_count_and_an_average(self):
        # 2297 frames * 1152 / 44100 = 60.003 s; 1837592 bytes over that = 245 kbps.
        data = xing_frame(b"Xing", 2297, 1837592) + frames(5)
        self.assertEqual(self.described("vbr.mp3", data), "1m0s ~245/44.1/JS")

    def test_a_mono_xing_header_sits_closer(self):
        data = xing_frame(b"Xing", 2297, 1837592, mode=0xC0, side=17) + frames(5, mode=0xC0)
        self.assertEqual(self.described("vbr-mono.mp3", data), "1m0s ~245/44.1/M")

    def test_an_info_header_is_cbr_with_an_exact_length(self):
        data = xing_frame(b"Info", 2297, 0) + frames(5)
        self.assertEqual(self.described("info.mp3", data), "1m0s 128/44.1/JS")

    def test_vbri(self):
        data = vbri_frame(2297, 1837592) + frames(5)
        self.assertEqual(self.described("vbri.mp3", data), "1m0s ~245/44.1/JS")

    def test_mpeg2_rates(self):
        # 1000 frames of 208 bytes at 64 kbps: 208000 * 8 / 64000 = 26 s.
        data = frames(1000, header=MPEG2_64, mode=0x00, length=FRAME_64)
        self.assertEqual(self.described("low.mp3", data), "0m26s 64/22.05/S")


class ReadingFlac(FileCase):
    def test_duration_and_real_bitrate(self):
        # 88200 samples at 44.1 kHz = 2 s; 278750 audio bytes * 8 / 2 = 1115 kbps.
        self.assertEqual(self.described("track.flac", flac()), "0m2s 1115/44.1/S")

    def test_channels_and_rates(self):
        self.assertEqual(self.described("mono.flac", flac(channels=1)), "0m2s 1115/44.1/M")
        six = self.described("six.flac", flac(rate=96000, channels=6, samples=96000 * 2))
        self.assertEqual(six, "0m2s 1115/96/6ch")

    def test_a_big_picture_block_is_skipped_not_read(self):
        data = flac(padding=3 * 1024 * 1024)
        self.assertEqual(self.described("art.flac", data), "0m2s 1115/44.1/S")


class NothingItCannotReadIsGuessed(FileCase):
    def test_every_bad_file_is_none(self):
        cases = {
            "empty.mp3": b"",
            "noise.mp3": bytes(range(256)) * 40,
            "cut.flac": flac()[:20],
            "notflac.flac": b"OggS" + b"\x00" * 100,
            "zero-samples.flac": flac(samples=0),
            "tag-only.mp3": id3v2(),
        }
        for name, data in cases.items():
            self.assertIsNone(audio_info.read(self.write(name, data)), name)

    def test_other_files_are_never_opened(self):
        self.assertIsNone(audio_info.read(self.write("cover.jpg", frames(10))))
        self.assertFalse(audio_info.is_audio("cover.jpg"))
        self.assertTrue(audio_info.is_audio("Loud.MP3"))

    def test_a_missing_file_is_none_not_an_exception(self):
        self.assertIsNone(audio_info.read(os.path.join(self.tree.music, "gone.mp3")))

    def test_describe_of_nothing_is_empty(self):
        self.assertEqual(audio_info.describe(None), "")


class TheCache(FileCase):
    def counting(self):
        self.reads = []

        def reader(path, size=None):
            self.reads.append(os.path.basename(path))
            return audio_info.read(path, size)
        return reader

    def open(self, scope=""):
        cache = audio_info.Cache.open(reader=self.reader, scope=scope)
        self.addCleanup(cache.close)
        return cache

    def setUp(self):
        super().setUp()
        self.reader = self.counting()
        self.path = self.write("a.mp3", frames(1000))
        self.size = os.path.getsize(self.path)

    def test_an_unchanged_file_is_not_read_again(self):
        first = self.open()
        first.observe("k", self.path, self.size)
        first.publish()
        first.close()
        second = self.open()
        second.observe("k", self.path, self.size)
        self.assertEqual(self.reads, ["a.mp3"])
        self.assertEqual(second.suffix("k"), "0m26s 128/44.1/JS")
        self.assertEqual((second.read_count, second.reused_count), (0, 1))

    def test_a_changed_file_is(self):
        first = self.open()
        first.observe("k", self.path, self.size)
        first.close()
        self.write("a.mp3", frames(2000))
        second = self.open()
        second.observe("k", self.path, os.path.getsize(self.path))
        self.assertEqual(self.reads, ["a.mp3", "a.mp3"])
        self.assertEqual(second.suffix("k"), "0m52s 128/44.1/JS")

    def test_a_published_rebuild_forgets_what_it_did_not_see(self):
        first = self.open()
        first.observe("gone", self.path, self.size)
        first.publish()
        first.close()
        second = self.open()
        second.publish()
        second.close()
        third = self.open()
        third.observe("gone", self.path, self.size)
        self.assertEqual(len(self.reads), 2, "the row was dropped, so it was read again")

    def test_a_rebuild_that_did_not_see_a_file_has_no_suffix_for_it(self):
        first = self.open()
        first.observe("k", self.path, self.size)
        first.close()
        self.assertEqual(self.open().suffix("k"), "")

    def test_one_list_never_prunes_another(self):
        other = self.open(scope="other")
        other.observe("k", self.path, self.size)
        other.publish()
        other.close()
        primary = self.open(scope="")
        primary.publish()
        primary.close()
        again = self.open(scope="other")
        again.observe("k", self.path, self.size)
        self.assertEqual(self.reads, ["a.mp3"], "the other list's row survived")

    def test_a_cache_that_cannot_open_says_so(self):
        blocker = os.path.join(self.tree.root, "not-a-dir")
        with open(blocker, "w") as handle:
            handle.write("x")
        said = []
        self.assertIsNone(audio_info.Cache.open(path=os.path.join(blocker, "audio.db"), log=said.append))
        self.assertIn("written with sizes only", said[0])


class TheList(FileCase):
    def setUp(self):
        super().setUp()
        self.set_config(LOCAL_LIST_DIR=self.tree.lists, LIST_BASE_NAME="DCCoreTest",
                        NICKNAME="DCCoreTest", RAR_ENABLED=False, LIST_FORMAT="txt")
        self.write("Example Artist - 01 - Opening.mp3", frames(1000), folder="Album")
        self.write("Example Artist - 02 - Closing.flac", flac(), folder="Album")
        self.write("Front.jpg", b"\xff\xd8" + b"\x00" * 500, folder="Album")
        self.write("Broken.mp3", b"not audio at all", folder="Album")

    def rows(self, **overrides):
        self.set_config(**overrides)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            built = update_list.generate_master_list()
        self.assertTrue(built, buffer.getvalue())
        path = list_mod.find_latest_list()
        with open(path, encoding="utf-8") as handle:
            return {line.split("  ::INFO:: ")[0].split(" ", 1)[1]: line.rstrip("\n").split("  ::INFO:: ")[1]
                    for line in handle if line.startswith("!")}, buffer.getvalue()

    def test_on_the_audio_rows_carry_it_and_nothing_else_does(self):
        rows, said = self.rows(LIST_SHOW_AUDIO_INFO=True)
        self.assertRegex(rows["Example Artist - 01 - Opening.mp3"], r"^\S+ 0m26s 128/44\.1/JS$")
        self.assertRegex(rows["Example Artist - 02 - Closing.flac"], r"^\S+ 0m2s 1115/44\.1/S$")
        self.assertNotIn(" ", rows["Front.jpg"])
        self.assertNotIn(" ", rows["Broken.mp3"], "unreadable: size only")
        # make_tree() ships a few audio files of its own; every one is read.
        self.assertRegex(said, r"\[LIST-GEN\] Audio info: [1-9]\d* file\(s\) read, 0 unchanged")

    def test_off_nothing_is_opened_and_the_rows_are_as_before(self):
        rows, said = self.rows(LIST_SHOW_AUDIO_INFO=False)
        self.assertTrue(all(" " not in tail for tail in rows.values()), rows)
        self.assertNotIn("Audio info", said)
        self.assertFalse(os.path.exists(audio_info.cache_path()))

    def test_the_second_rebuild_reads_nothing(self):
        self.rows(LIST_SHOW_AUDIO_INFO=True)
        rows, said = self.rows(LIST_SHOW_AUDIO_INFO=True)
        self.assertRegex(said, r"\[LIST-GEN\] Audio info: 0 file\(s\) read, [1-9]\d* unchanged")
        self.assertRegex(rows["Example Artist - 01 - Opening.mp3"], r" 0m26s 128/44\.1/JS$")

    def test_a_published_rebuild_forgets_a_removed_file(self):
        import sqlite3
        self.rows(LIST_SHOW_AUDIO_INFO=True)
        os.remove(os.path.join(self.tree.music, "Album", "Example Artist - 02 - Closing.flac"))
        self.rows(LIST_SHOW_AUDIO_INFO=True)
        conn = sqlite3.connect(audio_info.cache_path())
        self.addCleanup(conn.close)
        keys = [row[0] for row in conn.execute("SELECT key FROM audio")]
        self.assertTrue(any(key.endswith("Opening.mp3") for key in keys), keys)
        self.assertFalse(any(key.endswith("Closing.flac") for key in keys), keys)

    def test_our_own_parser_reads_the_row_back(self):
        self.rows(LIST_SHOW_AUDIO_INFO=True)
        entries, _total = list_mod.find_matching_entries(["opening"])
        self.assertEqual([e["filename"] for e in entries], ["Example Artist - 01 - Opening.mp3"])


class Search(DCCoreTestCase):
    """The name comes before the audio tail when a result must be cut."""

    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()
        os.makedirs(self.tree.lists, exist_ok=True)
        self.long_name = "Example Artist - " + "Very Long Title " * 19 + ".mp3"  # fits only without its tail
        write_master_list(self.tree.lists, "DCCoreTest", [(None, [
            ("Short Song.mp3", "4.1MB 4m31s 320/44.1/JS"),
            (self.long_name, "9.9MB 7m2s ~245/44.1/JS"),
        ])])
        self.set_config(FILE_DIRECTORY=self.tree.music, LOCAL_LIST_DIR=self.tree.lists,
                        LIST_BASE_NAME="DCCoreTest", NICKNAME="DCCoreTest", CHANNEL="#chan",
                        search_inprogress=False, update_inprogress=False)

    def find(self, term):
        self.oserve.queued.clear()
        list_mod.execute_search(RecordingSocket(), "dave", term, "#chan")
        return [m for _u, m, *_ in self.oserve.queued if "::INFO::" in m]

    def test_a_row_that_fits_keeps_its_tail(self):
        rows = self.find("short song")
        self.assertEqual(len(rows), 1)
        self.assertIn("Short Song.mp3  ::INFO:: 4.1MB 4m31s 320/44.1/JS", rows[0])

    def test_a_row_that_does_not_loses_the_tail_before_the_name(self):
        rows = self.find("very long title")
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(len(rows[0].encode("utf-8")), announce.IRC_LINE_BUDGET)
        self.assertIn(self.long_name + "  ::INFO:: 9.9MB", rows[0])
        self.assertNotIn("7m2s", rows[0])

    def test_the_tail_pattern_touches_only_a_tail(self):
        row = "!Bot 4m31s 320/44.1/JS.mp3  ::INFO:: 4.1MB 4m31s 320/44.1/JS"
        self.assertEqual(list_mod.without_audio_info(row), "!Bot 4m31s 320/44.1/JS.mp3  ::INFO:: 4.1MB")
        self.assertEqual(list_mod.without_audio_info("!Bot a.mp3  ::INFO:: 4.1MB"), "!Bot a.mp3  ::INFO:: 4.1MB")


if __name__ == "__main__":
    unittest.main()
