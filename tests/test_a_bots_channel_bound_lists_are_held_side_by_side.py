"""#1240: a bot that binds a different list to each of several channels we
share with it (DCCore's own multi-list-per-channel feature, which another
DCCore-family bot can equally run) gets EVERY one of those lists held and
browsable, not just whichever channel happened to answer first.

This is the fix for a real incident found while reviewing #1239: asking for a
bot's list from a channel other than the one already on record replaced the
bot's entire held entry with whatever that one channel answered - on a bot
with Main, RAR and a channel-bound second list, the second list's answer
silently deleted Main and RAR. The explicit-channel route that could trigger
it was reverted (#1239's final version); this issue is the real fix:
`_install_fetched_list()` now MERGES a channel's own markers into the bot's
existing entry rather than replacing the whole thing, and extracts a
secondary channel's archive into its own subdirectory so it can never
overwrite what is already on disk for another channel either.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import announce  # noqa: E402
import defaults as config  # noqa: E402
import list_fetch  # noqa: E402
import list_index  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402
from tests.test_list_fetch import _write_zip, _list_txt  # noqa: E402

BOT = "SomeServer"
KEY = "someserver"


class TheIncidentItself(DCCoreTestCase):
    """The exact shape of what went wrong for real: Main + RAR already held,
    a request to the bot's OTHER channel answers with only its video content."""

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-1240-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp
        self.music_zip = os.path.join(self.tmp, "music.zip")
        self.video_zip = os.path.join(self.tmp, "video.zip")

    def fetch_music(self):
        _write_zip(self.music_zip, [
            (f"{BOT}-2026-08-27.txt", _list_txt(base_name=BOT, files=(("Song.flac", "10.0MB"),))),
            (f"{BOT}-RAR-2026-08-27.txt", _list_txt(base_name=BOT, files=(("Album.rar", "500.0MB"),))),
        ])
        return list_fetch.process_fetched_list_zip(BOT, self.music_zip, channel="#music")

    def fetch_video(self, filename="Clip.mkv"):
        _write_zip(self.video_zip, [
            (f"{BOT}-VIDEO-2026-08-27.txt", _list_txt(base_name=BOT, files=((filename, "700.0MB"),))),
        ])
        return list_fetch.process_fetched_list_zip(BOT, self.video_zip, channel="#video")

    def test_main_and_rar_survive_a_fetch_from_the_other_channel(self):
        ok, _reason = self.fetch_music()
        self.assertTrue(ok)
        before = dict(config.fetched_bot_lists[KEY]["lists"])

        ok, _reason = self.fetch_video()
        self.assertTrue(ok, _reason)

        after = config.fetched_bot_lists[KEY]["lists"]
        self.assertEqual(after[""], before[""], "the main (music) list must be untouched")
        self.assertEqual(after["RAR"], before["RAR"], "the RAR list must be untouched")

    def test_the_video_channels_list_is_added_as_its_own_marker(self):
        self.fetch_music()
        ok, _reason = self.fetch_video()
        self.assertTrue(ok, _reason)

        lists = config.fetched_bot_lists[KEY]["lists"]
        self.assertIn("video", lists)
        self.assertEqual(lists["video"]["channel"], "#video")
        self.assertEqual(lists["video"]["entry_count"], 1)
        rows = list_fetch.get_fetched_bot_page(
            {**config.fetched_bot_lists[KEY], **lists["video"]}, 0, 10)[0]
        self.assertIn("Clip.mkv", str(rows))

    def test_the_primary_channel_and_marker_are_unchanged(self):
        self.fetch_music()
        self.fetch_video()
        entry = config.fetched_bot_lists[KEY]
        self.assertEqual(entry["channel"], "#music")
        self.assertEqual(entry["lists"][""]["channel"], "#music")
        self.assertEqual(entry["lists"]["RAR"]["channel"], "#music")

    def test_the_video_list_is_searchable_under_its_own_name(self):
        self.fetch_music()
        self.fetch_video()
        results = list_index.search(["Clip"], bots=[f"{BOT}/video"])
        self.assertTrue(results, "the video list must be reachable by its index key")

    def test_refetching_video_again_replaces_only_the_video_marker(self):
        self.fetch_music()
        self.fetch_video(filename="First.mkv")
        before_main = dict(config.fetched_bot_lists[KEY]["lists"][""])
        before_rar = dict(config.fetched_bot_lists[KEY]["lists"]["RAR"])

        ok, _reason = self.fetch_video(filename="Second.mkv")
        self.assertTrue(ok, _reason)

        lists = config.fetched_bot_lists[KEY]["lists"]
        self.assertEqual(lists[""], before_main)
        self.assertEqual(lists["RAR"], before_rar)
        rows = list_fetch.get_fetched_bot_page(
            {**config.fetched_bot_lists[KEY], **lists["video"]}, 0, 10)[0]
        self.assertIn("Second.mkv", str(rows))
        self.assertNotIn("First.mkv", str(rows))

    def test_refetching_the_primary_channel_leaves_the_video_marker_alone(self):
        """The direction that matters just as much: an ordinary refresh of
        the bot's usual channel must not wipe out a secondary channel's list
        either."""
        self.fetch_music()
        self.fetch_video()
        video_before = dict(config.fetched_bot_lists[KEY]["lists"]["video"])

        ok, _reason = self.fetch_music()
        self.assertTrue(ok, _reason)

        self.assertEqual(config.fetched_bot_lists[KEY]["lists"]["video"], video_before)

    def test_an_empty_or_unparseable_secondary_answer_changes_nothing(self):
        self.fetch_music()
        self.fetch_video()
        before = dict(config.fetched_bot_lists[KEY]["lists"])

        _write_zip(self.video_zip, [(f"{BOT}-VIDEO-2026-08-27.txt", "not a file list at all\n")])
        ok, reason = list_fetch.process_fetched_list_zip(BOT, self.video_zip, channel="#video")

        self.assertFalse(ok)
        self.assertIn("video", reason.lower())
        self.assertEqual(config.fetched_bot_lists[KEY]["lists"], before)

    def test_the_secondary_channel_extracts_into_its_own_subdirectory(self):
        self.fetch_music()
        primary_dir = list_fetch.list_extract_dir(BOT)
        primary_files_before = set(os.listdir(primary_dir))

        self.fetch_video()

        # The primary channel's own files are still exactly as they were - a
        # new "_channels" entry (the secondary channel's own subdirectory,
        # asserted separately below) is the only addition, not a replacement.
        self.assertTrue(primary_files_before.issubset(set(os.listdir(primary_dir))),
                        "the primary channel's own extracted files must be untouched")
        secondary_dir = list_fetch.secondary_channel_extract_dir(BOT, "#video")
        self.assertTrue(os.path.isdir(secondary_dir))
        self.assertTrue(os.listdir(secondary_dir))


class TheArrivedMessageReportsTheRealCount(DCCoreTestCase):
    """A real incident, hit live: a channel's own archive can sub-split
    inside itself - its base file (what _channel_marker_name(channel) alone
    would look up) turned out empty and was dropped, while a sibling file in
    the SAME archive (stored as "<channel>-VIDEO") held the real content.
    The console reported "0 files" for a fetch that genuinely brought back
    thousands, because the old lookup only ever checked the one key named
    after the channel itself.
    """

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-1240-arrived-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp
        self.music_zip = os.path.join(self.tmp, "music.zip")
        self.video_zip = os.path.join(self.tmp, "video.zip")
        self.events = []
        real = announce.feed_event
        announce.feed_event = lambda kind, text, **fields: self.events.append((kind, text, fields))
        self.addCleanup(setattr, announce, "feed_event", real)

    def arrived_text(self):
        for kind, text, fields in self.events:
            if kind == "LISTFETCH" and fields.get("action") == "arrived":
                return text
        return None

    def test_a_channel_whose_own_main_file_is_empty_still_reports_its_real_count(self):
        _write_zip(self.music_zip, [
            (f"{BOT}-2026-08-27.txt", _list_txt(base_name=BOT, files=(("Song.flac", "10.0MB"),))),
        ])
        list_fetch.process_fetched_list_zip(BOT, self.music_zip, channel="#music")
        self.events.clear()

        # The channel's own archive: an empty base file (just headers, no
        # request lines) alongside a real "-VIDEO-" one - exactly the shape
        # that left nothing stored under the base channel marker name.
        empty_header = "List of 0 Files generated on Jan 1st\n"
        _write_zip(self.video_zip, [
            (f"{BOT}-2026-08-27.txt", empty_header),
            (f"{BOT}-VIDEO-2026-08-27.txt", _list_txt(base_name=BOT, files=(("Clip.mkv", "700.0MB"),))),
        ])
        ok, _reason = list_fetch.process_fetched_list_zip(BOT, self.video_zip, channel="#video")

        self.assertTrue(ok)
        self.assertNotIn("video", config.fetched_bot_lists[KEY]["lists"])
        self.assertIn("video-VIDEO", config.fetched_bot_lists[KEY]["lists"])
        self.assertEqual(self.arrived_text(), f"{BOT}'s list arrived: 1 files")


class TheAdvertSignatureIsStamped(DCCoreTestCase):
    """Each marker remembers what its own channel was advertising when it was
    fetched (#1240) - list_grab._secondary_channel_candidates() reads this to
    know when an already-discovered secondary list has moved on."""

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-1240-sig-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp
        self.zip_path = os.path.join(self.tmp, "x.zip")

    def test_the_primary_markers_carry_the_channels_live_signature(self):
        import runtime
        runtime.known_bots["someserver"] = {"channels": {"#music": {"files": 7, "since": 1.0}}}
        _write_zip(self.zip_path, [(f"{BOT}-2026-08-27.txt", _list_txt(base_name=BOT))])
        list_fetch.process_fetched_list_zip(BOT, self.zip_path, channel="#music")
        self.assertEqual(config.fetched_bot_lists[KEY]["lists"][""]["advert_signature"],
                         {"files": 7, "since": 1.0})

    def test_a_secondary_markers_signature_is_the_channel_it_came_from(self):
        import runtime
        _write_zip(self.zip_path, [(f"{BOT}-2026-08-27.txt", _list_txt(base_name=BOT))])
        list_fetch.process_fetched_list_zip(BOT, self.zip_path, channel="#music")
        runtime.known_bots["someserver"] = {"channels": {"#video": {"files": 9, "since": 2.0}}}
        video_zip = os.path.join(self.tmp, "v.zip")
        _write_zip(video_zip, [(f"{BOT}-VIDEO-2026-08-27.txt", _list_txt(base_name=BOT))])
        list_fetch.process_fetched_list_zip(BOT, video_zip, channel="#video")
        self.assertEqual(config.fetched_bot_lists[KEY]["lists"]["video"]["advert_signature"],
                         {"files": 9, "since": 2.0})


class ABotHeldBeforeChannelsWereTracked(DCCoreTestCase):
    """A real incident, hit live on the real bot: an entry fetched before
    #1232 ever existed has no "channel" field at all - sometimes not even a
    "lists" dict, only the old flat list_path/entry_count shape from before
    #1209's multi-list-per-archive feature. Either shape must be preserved
    exactly as safely as a channel-tagged one once a secondary channel's
    fetch comes in, not read as "nothing recorded here to protect"."""

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-1240-legacy-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp
        self.video_zip = os.path.join(self.tmp, "video.zip")

    def fetch_video(self):
        _write_zip(self.video_zip, [(f"{BOT}-VIDEO-2026-08-27.txt",
                                     _list_txt(base_name=BOT, files=(("Clip.mkv", "700.0MB"),)))])
        return list_fetch.process_fetched_list_zip(BOT, self.video_zip, channel="#video")

    def test_an_entry_with_no_channel_field_at_all_is_still_treated_as_secondary(self):
        config.fetched_bot_lists[KEY] = {
            "bot": BOT, "entry_count": 1116789,
            "list_path": "/somewhere/real/list.txt",  # no "channel" key, no "lists" dict
        }
        self.assertTrue(list_fetch._is_secondary_channel_fetch(BOT, "#video"))

    def test_a_bot_with_no_lists_dict_at_all_keeps_its_old_content(self):
        """The pre-#1209 flat shape: the one list lives in list_path/
        entry_count directly on the entry, no "lists" dict exists yet."""
        config.fetched_bot_lists[KEY] = {
            "bot": BOT, "entry_count": 1116789,
            "list_path": "/somewhere/real/list.txt",
        }
        ok, _reason = self.fetch_video()
        self.assertTrue(ok, _reason)
        lists = config.fetched_bot_lists[KEY]["lists"]
        self.assertEqual(lists[""]["entry_count"], 1116789)
        self.assertEqual(lists[""]["list_path"], "/somewhere/real/list.txt")
        self.assertIn("video", lists)
        self.assertEqual(lists["video"]["entry_count"], 1)

    def test_a_bot_with_a_lists_dict_but_no_channel_field_keeps_its_markers(self):
        """The #1209-but-pre-#1232 shape: a real "lists" dict (maybe Main
        and RAR already), just never tagged with which channel it came
        from."""
        config.fetched_bot_lists[KEY] = {
            "bot": BOT,
            "lists": {
                "": {"list_path": "/x/main.txt", "entry_count": 500, "file_name": "main.txt"},
                "RAR": {"list_path": "/x/rar.txt", "entry_count": 12, "file_name": "rar.txt"},
            },
        }
        ok, _reason = self.fetch_video()
        self.assertTrue(ok, _reason)
        lists = config.fetched_bot_lists[KEY]["lists"]
        self.assertEqual(lists[""]["entry_count"], 500)
        self.assertEqual(lists["RAR"]["entry_count"], 12)
        self.assertIn("video", lists)

    def test_a_bot_with_nothing_held_at_all_is_not_secondary(self):
        """No previous entry whatsoever - the FIRST ever fetch for a bot
        must still take the ordinary, full-replace path."""
        self.assertFalse(list_fetch._is_secondary_channel_fetch(BOT, "#video"))
        ok, _reason = self.fetch_video()
        self.assertTrue(ok, _reason)
        self.assertEqual(set(config.fetched_bot_lists[KEY]["lists"]), {""})


class TheMergeHelpersInIsolation(DCCoreTestCase):

    def test_is_secondary_requires_an_existing_entry(self):
        self.assertFalse(list_fetch._is_secondary_channel_fetch(BOT, "#video"))

    def test_an_entry_with_no_channel_and_no_real_content_is_not_secondary(self):
        """No channel AND nothing actually held (an empty "lists" dict, no
        list_path either) - contrast with ABotHeldBeforeChannelsWereTracked
        below, where the entry has no channel but DOES hold real content and
        must be treated as secondary instead."""
        config.fetched_bot_lists[KEY] = {"bot": BOT, "channel": None, "lists": {}}
        self.assertFalse(list_fetch._is_secondary_channel_fetch(BOT, "#video"))

    def test_is_secondary_is_false_for_the_same_channel(self):
        config.fetched_bot_lists[KEY] = {"bot": BOT, "channel": "#music", "lists": {}}
        self.assertFalse(list_fetch._is_secondary_channel_fetch(BOT, "#music"))
        self.assertFalse(list_fetch._is_secondary_channel_fetch(BOT, " #MUSIC "))

    def test_is_secondary_is_false_with_no_channel_given(self):
        config.fetched_bot_lists[KEY] = {"bot": BOT, "channel": "#music", "lists": {}}
        self.assertFalse(list_fetch._is_secondary_channel_fetch(BOT, None))
        self.assertFalse(list_fetch._is_secondary_channel_fetch(BOT, ""))

    def test_is_secondary_is_true_for_a_genuinely_different_channel(self):
        config.fetched_bot_lists[KEY] = {"bot": BOT, "channel": "#music", "lists": {}}
        self.assertTrue(list_fetch._is_secondary_channel_fetch(BOT, "#video"))

    def test_channel_marker_name_strips_the_hash(self):
        self.assertEqual(list_fetch._channel_marker_name("#video"), "video")

    def test_channel_marker_name_sanitises_unsafe_characters(self):
        self.assertEqual(list_fetch._channel_marker_name("#a/b"), "a_b")

    def test_channel_marker_name_never_returns_the_empty_marker(self):
        """"" means "the main list" - a channel name must never collide with
        that meaning by sanitising down to nothing."""
        self.assertEqual(list_fetch._channel_marker_name("#"), "channel")
        self.assertEqual(list_fetch._channel_marker_name(""), "channel")

    def test_extract_dir_for_is_the_ordinary_one_with_no_existing_entry(self):
        self.assertEqual(list_fetch._extract_dir_for(BOT, "#video"),
                         list_fetch.list_extract_dir(BOT))

    def test_extract_dir_for_is_the_secondary_one_once_confirmed(self):
        config.fetched_bot_lists[KEY] = {"bot": BOT, "channel": "#music", "lists": {}}
        self.assertEqual(list_fetch._extract_dir_for(BOT, "#video"),
                         list_fetch.secondary_channel_extract_dir(BOT, "#video"))


if __name__ == "__main__":
    unittest.main()
