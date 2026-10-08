"""#1232: a cross-bot request goes to the right channel, not a random one.

A request used to go wherever dcc.channel_containing_user(bot) found the bot
first - the first of OUR channels, in configured order, it happened to be in
right now - with no regard for which channel a bot's list (or a second list it
serves, the same multi-list-per-channel feature this bot itself has) actually
answers in. A bot shared with us over two channels, bound to a different list
in each, silently only ever gave up its "first channel" list, and a file or
folder request for it could land in a channel where it serves nothing at all.

Now:
- a fetch row can carry a preferred channel; the dispatcher resolves a real
  one from it (falling back to the bot's advert channel, then to today's
  "first channel we share" rule) and remembers what it used, so a retried
  row keeps asking in the same place;
- the list a fetch brings back remembers which channel it came from;
- a File list browser or Search request for a bot we already hold a list
  from reuses that list's own channel automatically - no picker, no new
  field for the dashboard to send;
- `fetch <bot> <channel>` (the console / dccore.mrc "fetch" command) asks a
  SPECIFIC channel explicitly - how a bot's other list, bound elsewhere,
  gets fetched at all.
"""

import os
import sys
import time
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dcc  # noqa: E402
import dcc_fetch  # noqa: E402
import defaults as config  # noqa: E402
import list_fetch  # noqa: E402
import runtime  # noqa: E402
import webserver  # noqa: E402

from tests.support import DCCoreTestCase, bots_in_the_channel  # noqa: E402
from tests.test_list_fetch import _write_zip, _list_txt  # noqa: E402


class ResolvingTheChannel(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.set_config(CHANNEL="#chan,#video", bot_joined_channel=True)
        config.channel_users.clear()

    def test_the_preferred_channel_wins_when_the_bot_is_there(self):
        bots_in_the_channel("GoodBot", channel="#chan")
        bots_in_the_channel("GoodBot", channel="#video")
        self.assertEqual(dcc_fetch._resolve_fetch_channel("GoodBot", "#video"), "#video")

    def test_preferred_is_matched_case_and_space_insensitively_to_our_configured_spelling(self):
        bots_in_the_channel("GoodBot", channel="#video")
        self.assertEqual(dcc_fetch._resolve_fetch_channel("GoodBot", " #VIDEO "), "#video")

    def test_a_preferred_channel_we_are_not_configured_for_is_ignored(self):
        bots_in_the_channel("GoodBot", channel="#chan")
        self.assertEqual(dcc_fetch._resolve_fetch_channel("GoodBot", "#elsewhere"), "#chan")

    def test_a_preferred_channel_the_bot_has_left_falls_back(self):
        bots_in_the_channel("GoodBot", channel="#chan")
        self.assertEqual(dcc_fetch._resolve_fetch_channel("GoodBot", "#video"), "#chan")

    def test_with_no_preference_the_advert_channel_wins_next(self):
        bots_in_the_channel("GoodBot", channel="#chan")
        bots_in_the_channel("GoodBot", channel="#video")
        runtime.known_bots["goodbot"] = {"channel": "#video"}
        self.assertEqual(dcc_fetch._resolve_fetch_channel("GoodBot", None), "#video")

    def test_an_advert_channel_the_bot_has_left_falls_back_too(self):
        bots_in_the_channel("GoodBot", channel="#chan")
        runtime.known_bots["goodbot"] = {"channel": "#video"}
        self.assertEqual(dcc_fetch._resolve_fetch_channel("GoodBot", None), "#chan")

    def test_with_nothing_else_todays_first_shared_channel_rule_applies(self):
        bots_in_the_channel("GoodBot", channel="#video")
        self.assertEqual(dcc_fetch._resolve_fetch_channel("GoodBot", None), "#video")

    def test_a_bot_in_none_of_our_channels_resolves_to_nothing(self):
        self.assertIsNone(dcc_fetch._resolve_fetch_channel("GoneBot", "#chan"))

    def test_bot_in_our_channel_is_the_single_channel_question(self):
        bots_in_the_channel("GoodBot", channel="#video")
        self.assertTrue(dcc_fetch.bot_in_our_channel("GoodBot", "#video"))
        self.assertFalse(dcc_fetch.bot_in_our_channel("GoodBot", "#chan"))
        self.assertFalse(dcc_fetch.bot_in_our_channel("GoneBot", "#video"))


class TheDispatcherRemembersIt(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.set_config(fetch_queue={}, MAX_FETCH_SLOTS=10, fetch_feature_disabled=False,
                        CHANNEL="#chan,#video", FETCH_MAX_PER_BOT=0, bot_joined_channel=True)
        config.channel_users.clear()
        bots_in_the_channel("GoodBot", channel="#chan")
        bots_in_the_channel("GoodBot", channel="#video")

    def sent_to(self):
        for user, msg, *_r in self.oserve.queued:
            if "PRIVMSG" in msg:
                return msg.split(" ", 1)[1].split(" ", 1)[0]
        return None

    def test_a_rows_own_channel_is_honoured(self):
        rid = dcc_fetch.enqueue_fetch("GoodBot", "Song.flac", channel="#video")
        dcc_fetch.check_fetch_queue()
        self.assertEqual(self.sent_to(), "#video")
        self.assertEqual(config.fetch_queue[rid]["channel"], "#video")

    def test_with_no_preference_it_falls_back_as_resolve_fetch_channel_does(self):
        runtime.known_bots["goodbot"] = {"channel": "#video"}
        rid = dcc_fetch.enqueue_fetch("GoodBot", "Song.flac")
        dcc_fetch.check_fetch_queue()
        self.assertEqual(self.sent_to(), "#video")
        self.assertEqual(config.fetch_queue[rid]["channel"], "#video")

    def test_a_retry_keeps_asking_in_the_same_channel(self):
        """Even if the bot's advert channel changes between the first ask and
        a later retry - the row, once dispatched, has its own answer."""
        rid = dcc_fetch.enqueue_fetch("GoodBot", "Song.flac", channel="#video")
        dcc_fetch.check_fetch_queue()
        self.assertEqual(config.fetch_queue[rid]["channel"], "#video")
        self.oserve.queued.clear()
        config.fetch_queue[rid].update(state="pending", offered_at=None)
        runtime.known_bots["goodbot"] = {"channel": "#chan"}
        dcc_fetch.check_fetch_queue()
        self.assertEqual(self.sent_to(), "#video")
        self.assertEqual(config.fetch_queue[rid]["channel"], "#video")

    def test_new_fetch_row_carries_the_channel_given(self):
        row = dcc_fetch.new_fetch_row("GoodBot", "Song.flac", channel="#video")
        self.assertEqual(row["channel"], "#video")

    def test_new_fetch_row_defaults_the_channel_to_none(self):
        row = dcc_fetch.new_fetch_row("GoodBot", "Song.flac")
        self.assertIsNone(row["channel"])

    def test_a_blank_channel_is_the_same_as_none(self):
        row = dcc_fetch.new_fetch_row("GoodBot", "Song.flac", channel="   ")
        self.assertIsNone(row["channel"])

    def test_a_row_deleted_between_resolving_and_sending_crashes_nothing(self):
        """The dispatcher's own established atomicity rule (see
        test_a_fetch_request_is_not_timed_before_it_is_sent.py) - the channel
        write joins the same late, re-checked-under-lock step as
        request_line, not the earlier unlocked resolution."""
        rid = dcc_fetch.enqueue_fetch("GoodBot", "Song.flac", channel="#video")
        real = dcc_fetch._resolve_fetch_channel

        def resolve_then_delete(bot, preferred):
            config.fetch_queue.pop(rid, None)
            return real(bot, preferred)

        with mock.patch.object(dcc_fetch, "_resolve_fetch_channel", resolve_then_delete):
            dcc_fetch.check_fetch_queue()  # must not raise
        self.assertNotIn(rid, config.fetch_queue)


class ACompletedFetchRemembersWhereItCameFrom(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="dccore-1232-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        config.FETCHED_FILES_DIR = self.tmp
        self.zip_path = os.path.join(self.tmp, "incoming.zip")

    def test_process_fetched_list_zip_stores_the_channel_given(self):
        _write_zip(self.zip_path, [("VideoBot-2026-08-27.txt", _list_txt(base_name="VideoBot"))])
        ok, _reason = list_fetch.process_fetched_list_zip("VideoBot", self.zip_path, channel="#video")
        self.assertTrue(ok)
        self.assertEqual(config.fetched_bot_lists["videobot"]["channel"], "#video")

    def test_with_no_channel_given_the_entry_has_none(self):
        _write_zip(self.zip_path, [("PlainBot-2026-08-27.txt", _list_txt(base_name="PlainBot"))])
        ok, _reason = list_fetch.process_fetched_list_zip("PlainBot", self.zip_path)
        self.assertTrue(ok)
        self.assertIsNone(config.fetched_bot_lists["plainbot"]["channel"])

    def test_the_completion_handler_passes_the_rows_own_channel_through(self):
        row = {"bot": "VideoBot", "channel": "#video"}
        with mock.patch.object(list_fetch, "process_fetched_list_zip") as fake:
            fake.return_value = (True, None)
            dcc_fetch._handle_completed_list_fetch(row, self.zip_path)
        fake.assert_called_once_with("VideoBot", self.zip_path, channel="#video")


class HeldListChannelFeedsFileAndFolderRequests(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.set_config(fetch_queue={}, MAX_FETCH_SLOTS=10, fetch_feature_disabled=False,
                        CHANNEL="#chan,#video", bot_joined_channel=True,
                        fetched_bot_lists={})
        config.channel_users.clear()
        bots_in_the_channel("GoodBot", channel="#chan")
        bots_in_the_channel("GoodBot", channel="#video")

    def test_held_list_channel_reads_the_stored_entry(self):
        config.fetched_bot_lists["goodbot"] = {"channel": "#video"}
        self.assertEqual(webserver.held_list_channel("GoodBot"), "#video")

    def test_held_list_channel_is_none_for_an_unheld_bot(self):
        self.assertIsNone(webserver.held_list_channel("NeverFetched"))

    def test_held_list_channel_is_none_for_a_list_fetched_before_1232(self):
        config.fetched_bot_lists["goodbot"] = {"bot": "GoodBot"}  # no "channel" key at all
        self.assertIsNone(webserver.held_list_channel("GoodBot"))

    def test_a_file_request_uses_the_held_lists_channel(self):
        config.fetched_bot_lists["goodbot"] = {"channel": "#video"}
        status, result = webserver.build_fetch_enqueue_result({"bot": "GoodBot", "filename": "Song.flac"})
        self.assertEqual(status, 200)
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["channel"], "#video")

    def test_a_folder_request_uses_the_held_lists_channel(self):
        config.fetched_bot_lists["goodbot"] = {"channel": "#video"}
        status, result = webserver.build_folder_rar_fetch_enqueue_result("GoodBot", "Artist/Album")
        self.assertEqual(status, 200)
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["channel"], "#video")

    def test_a_request_for_a_bot_with_no_held_list_carries_no_channel_preference(self):
        status, result = webserver.build_fetch_enqueue_result({"bot": "GoodBot", "filename": "Song.flac"})
        self.assertEqual(status, 200)
        rid = result["created"][0]
        self.assertIsNone(config.fetch_queue[rid]["channel"])

    def held_bot_with_a_secondary_marker(self):
        config.fetched_bot_lists["goodbot"] = {
            "channel": "#chan",
            "lists": {
                "": {"channel": "#chan"},
                "video": {"channel": "#video"},
            },
        }

    def test_held_list_channel_with_a_marker_reads_that_markers_own_channel(self):
        self.held_bot_with_a_secondary_marker()
        self.assertEqual(webserver.held_list_channel("GoodBot", marker="video"), "#video")

    def test_held_list_channel_with_no_marker_still_reads_the_primary(self):
        self.held_bot_with_a_secondary_marker()
        self.assertEqual(webserver.held_list_channel("GoodBot"), "#chan")

    def test_held_list_channel_with_the_empty_marker_reads_the_primary(self):
        """"" names the main list itself - not a secondary one."""
        self.held_bot_with_a_secondary_marker()
        self.assertEqual(webserver.held_list_channel("GoodBot", marker=""), "#chan")

    def test_held_list_channel_with_an_unknown_marker_falls_back_to_the_primary(self):
        self.held_bot_with_a_secondary_marker()
        self.assertEqual(webserver.held_list_channel("GoodBot", marker="nonsense"), "#chan")

    def test_a_file_request_from_a_secondary_markers_list_uses_that_markers_channel(self):
        """The real incident: a file ticked on a bot's secondary ("-VIDEO")
        list went to the bot's ordinary channel instead of the one that list
        itself came from."""
        self.held_bot_with_a_secondary_marker()
        status, result = webserver.build_fetch_enqueue_result(
            {"bot": "GoodBot", "filename": "Clip.mkv", "marker": "video"})
        self.assertEqual(status, 200)
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["channel"], "#video")

    def test_a_folder_request_from_a_secondary_markers_list_uses_that_markers_channel(self):
        self.held_bot_with_a_secondary_marker()
        status, result = webserver.build_folder_rar_fetch_enqueue_result(
            "GoodBot", "Artist/Album", "video")
        self.assertEqual(status, 200)
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["channel"], "#video")

    def test_a_batch_folder_request_carries_the_marker_too(self):
        self.held_bot_with_a_secondary_marker()
        status, result = webserver.build_folder_rar_batch_enqueue_result(
            [{"bot": "GoodBot", "folder": "Artist/Album", "marker": "video"}])
        self.assertEqual(status, 200)
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["channel"], "#video")

    def test_a_request_with_no_marker_still_uses_the_primary_as_before(self):
        self.held_bot_with_a_secondary_marker()
        status, result = webserver.build_fetch_enqueue_result({"bot": "GoodBot", "filename": "Song.flac"})
        self.assertEqual(status, 200)
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["channel"], "#chan")


class ARetriedRowCarriesItsOwnChannelDirectly(DCCoreTestCase):
    """The Downloads page's "Try again" button (#1240): a row already knows
    exactly which channel it went out in last time (fetch_queue's own
    "channel" field, set by whichever rule applied when it was first
    queued) and sends that back as an explicit "channel" - not a marker,
    which a retry has no reason to still know - so the retry must reuse it
    even where it disagrees with whatever held_list_channel() would resolve
    on its own."""

    def setUp(self):
        super().setUp()
        self.set_config(fetch_queue={}, MAX_FETCH_SLOTS=10, fetch_feature_disabled=False,
                        CHANNEL="#chan,#video,#other", bot_joined_channel=True,
                        fetched_bot_lists={})
        config.channel_users.clear()
        bots_in_the_channel("GoodBot", channel="#chan")
        bots_in_the_channel("GoodBot", channel="#video")
        bots_in_the_channel("GoodBot", channel="#other")
        config.fetched_bot_lists["goodbot"] = {
            "channel": "#chan",
            "lists": {"": {"channel": "#chan"}, "video": {"channel": "#video"}},
        }

    def test_an_explicit_channel_wins_over_the_bots_primary(self):
        status, result = webserver.build_fetch_enqueue_result(
            {"bot": "GoodBot", "filename": "Song.flac", "channel": "#other"})
        self.assertEqual(status, 200)
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["channel"], "#other")

    def test_an_explicit_channel_wins_over_a_markers_own_channel_too(self):
        """Not a real combination in practice - a retry sends one or the
        other - but the precedence must hold either way: the row's own
        remembered channel is the more specific fact."""
        status, result = webserver.build_fetch_enqueue_result(
            {"bot": "GoodBot", "filename": "Song.flac", "marker": "video", "channel": "#other"})
        self.assertEqual(status, 200)
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["channel"], "#other")

    def test_a_folder_retry_sends_its_own_channel_through_the_single_object_route(self):
        status, result = webserver.build_folder_rar_fetch_enqueue_result(
            "GoodBot", "Artist/Album", None, "#other")
        self.assertEqual(status, 200)
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["channel"], "#other")

    def test_a_folder_retry_through_the_batch_route_honours_channel_too(self):
        status, result = webserver.build_folder_rar_batch_enqueue_result(
            [{"bot": "GoodBot", "folder": "Artist/Album", "channel": "#other"}])
        self.assertEqual(status, 200)
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["channel"], "#other")

    def test_an_unsafe_channel_is_ignored_rather_than_rejecting_the_item(self):
        status, result = webserver.build_fetch_enqueue_result(
            {"bot": "GoodBot", "filename": "Song.flac", "channel": "#chan\r\nQUIT"})
        self.assertEqual(status, 200)
        rid = result["created"][0]
        self.assertEqual(config.fetch_queue[rid]["channel"], "#chan")


class DropOurRequestAtUsesTheRowsChannel(DCCoreTestCase):

    def setUp(self):
        super().setUp()
        self.set_config(CHANNEL="#chan,#video", bot_joined_channel=True)
        config.channel_users.clear()
        bots_in_the_channel("GoodBot", channel="#chan")
        bots_in_the_channel("GoodBot", channel="#video")

    def test_the_removed_rows_channel_is_used(self):
        import serverschat
        self.set_config(vip_queue=[])
        with mock.patch.object(serverschat, "is_known_peer", return_value=True):
            sent = dcc_fetch.drop_our_request_at("GoodBot", "Song.flac", channel="#video")
        self.assertTrue(sent)
        line = next(msg for _u, msg, *_r in self.oserve.queued if "PRIVMSG" in msg)
        self.assertTrue(line.startswith("PRIVMSG #video :"))

    def test_with_no_channel_it_resolves_one_itself(self):
        import serverschat
        self.set_config(vip_queue=[])
        with mock.patch.object(serverschat, "is_known_peer", return_value=True):
            sent = dcc_fetch.drop_our_request_at("GoodBot", "Song.flac")
        self.assertTrue(sent)


if __name__ == "__main__":
    unittest.main()
