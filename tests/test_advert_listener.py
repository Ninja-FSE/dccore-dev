"""Reading the periodic advert every file-serving bot sends to the channel.

The daemon has never done this. It parses @find reply headers, which arrive
only when we broadcast, and nothing watched ordinary channel traffic - so it
knew nothing about who else serves files, what they hold, or whether the list
we hold for them is still current.

THE FIXTURES ARE REAL

Every advert below was captured from #Mp3Passion on 2026-08-29 by a read-only
observer and is reproduced as it arrived. They are kept verbatim rather than
tidied because the untidiness is the point: no two bots format alike, and a
parser written against a cleaned-up sample is a parser written against a bot
nobody runs.

One transformation was applied, and only because the daemon applies it first.
Five of the sixteen decorate their fields with bytes that are not valid UTF-8,
and irc.py reads the socket with errors="ignore" - so those bytes never reach
the parser at all. Those adverts appear here as the daemon actually sees them,
separators already gone.

The four "\x95" you will see below are the other half of that story. They are a
cp1252 bullet that arrived double-encoded, which makes them a perfectly valid
UTF-8 control character - so they survive the decode, and strip_control_codes()
leaves them alone as well. Six adverts are plain ASCII and D_F_D's yen sign is
ordinary UTF-8. Four encodings across sixteen bots, in one channel.

What the sample settles:

  * The labels are stable, the layout is not. Separators seen include a yen sign, a tilde,
    ":", a control character, nothing at all, and - for five bots - nothing
    that survives the decode. Nothing may key on position or on what sits
    between fields.
  * Preamble is normal. The advert is not the whole line.
  * Nicks can carry brackets - @[tjserv].
  * Adverts get truncated. Vibessono's ends mid-field, at a bare "List:",
    because it ran into the 512-byte line limit.
  * A list DATE is published by fifteen of the sixteen; a list SIZE by none
    except DCCore itself, so nothing may depend on the size.
"""

import os
import sys
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import db  # noqa: E402
import irc  # noqa: E402
import runtime  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


# Captured from #Mp3Passion on 2026-08-29. Reproduced as they arrived.

BSK = (
    '\x95 Type: @Bsk For My List Of: 719,041 Files \x95 Slots: 10/10 \x95 '
    'Queued: 0 \x95 Speed: 0cps \x95 Next: NOW \x95 Served: 3,456,016 \x95 List: '
    'Aug 10th \x95 Search: ON \x95 Mode: Normal \x95'
)

DJVIRTUAL = (
    "Hello friend if you can't download please active the passive DCC\x95 "
    'Type: @DJVirtual For My List Of: 101,736 Files \x95 Slots: 20/20 \x95 '
    'Queued: 0 \x95 Speed: 0cps \x95 Next: NOW \x95 Served: 505 \x95 List: Aug '
    '25th \x95 Search: ON \x95'
)

FIREHORSESSS = (
    '  Type: @FireHorsesss For My List Of: 308,738 Files  Slots: 3/3  '
    'Queued: 0  Speed: 0cps  Next: NOW  Served: 1,105,288  List: Aug '
    '24th  Search: OFF  Mode: Normal'
)

KARAOKE_DUDE = (
    'Type: @karaoke_dude For My List Of: 475,128 Files Slots: 4/4 '
    'Queued: 0 Speed: 0cps Next: NOW Served: 320,664 List: Aug 19th '
    'Search: OFF Mode: Servers Only'
)

TJSERV = (
    '27,500 full albums in .rar: Type: @[tjserv] For My List Of: '
    '77,018 Files : Slots: 6/6 : Queued: 0 : Speed: 0cps : Next: NOW : '
    'Served: 1,624,853 : List: Aug 29th : Search: ON : Mode: Normal :'
)

VIBESSONO = (
    'NOUVEAUT ... Indefendable Sans Trace.... tu cherches de la music, '
    'Karok, film, srie il a tout sa dans ma liste :-) Type: @Vibessono '
    'For My List Of: 718,005 Files  Slots: 25/25  Queued: 9  Speed: '
    '0cps  Next: NOW  Served: 16,399  List:'
)

DCCORE = (
    'Type: @DCCore For My List Of: 61,101 Files (1.84TB) created Aug '
    '27th Slots: 3/3 Queued: 0 Speed: 0k/s / Record: 53.59MB/s Total '
    'Sent: 2,333 Files (254.0GB) Search: ON DCCore v1.10.0-RC3'
)

DCCOREWEB = (
    'Type: @DCCoreWeb For My List Of: 61,156 Files (1.84TB) created '
    'Aug 28th Slots: 3/3 Queued: 0 Speed: 0k/s / Record: 0k/s Total '
    'Sent: 35 Files (776.0MB) Search: ON DCCore v1.10.0-RC4'
)

DCCOREWIN = (
    'Type: @DCCoreWin For My List Of: 36,208 Files (1.21TB) created '
    'Aug 27th Slots: 3/3 Queued: 0 Speed: 0k/s / Record: 0k/s Total '
    'Sent: 31 Files (833.1MB) Search: ON DCCore v1.10.0-RC4'
)

LMELGHT = (
    ' Type: @Lmelght For My List Of: 242,558 Files  Slots: 3/3  '
    'Queued: 0  Speed: 0cps  Next: NOW  Served: 104,598  List: Aug '
    '13th  Search: OFF  Mode: Normal'
)

FALLGUYF00 = (
    'an OP heLped me fix this NO OS LiMiTS... xD\x95 Type: @fallguyf00 '
    'For My List Of: 39,659 Files \x95 Slots: 2/2 \x95 Queued: 0 \x95 Speed: '
    '0cps \x95 Next: NOW \x95 Served: 2,810 \x95 List: Aug 29th \x95 Search: ON \x95 '
    'Mode:'
)

D_F_D = (
    '\xa5 Type: @D_F_D For My List Of: 127,312 Files \xa5 Utilization: '
    '0.94TB / 17.75TB \xa5 Speed: 0cps \xa5 Served: 283,570 \xa5 List: Aug 28th '
    '\xa5 Parallel Sends: OFF \xa5 Mode: Normal \xa5'
)

ALY = (
    '~ Type: @Aly For My List Of: 389,136 Files ~ Slots: 2/2 ~ Queued: '
    '1 ~ Speed: 0cps ~ Next: NOW ~ Served: 30,753 ~ List: Jun 20th ~ '
    'Search: ON ~ Mode: Normal ~'
)

SONOTA = (
    'Feel free to take what you need. Type: @Sonota For My List Of: '
    '534,636 Files  Slots: 5/5  Queued: 0  Speed: 0cps  Next: NOW  '
    'Served: 158,621  List: Oct 18th  Search: ON  Mode: Normal'
)

BALTE = (
    '(`.* Musique Pour Tous *.) Type: @Balte For My List Of: 391,854 '
    'Files  Slots: 5/5  Queued: 0  Speed: 0cps  Next: NOW  Served: '
    '410,186  List: Aug 26th  Search: OFF  Mode: Normal'
)

FLACME = (
    'Flac & mp3 Oldies \x95 Type: @FlacMe For My List Of: 313,435 Files \x95 '
    'Utilization: 4.79331TB / 7.74221TB \x95 Speed: 0cps \x95 Served: 50,880 '
    '\x95 List: Jan 2nd \x95 Parallel Sends: OFF \x95 Mode: Servers Priority \x95'
)

ALL_CAPTURED = [
    ('Bsk', BSK),
    ('DJVirtual', DJVIRTUAL),
    ('FireHorsesss', FIREHORSESSS),
    ('karaoke_dude', KARAOKE_DUDE),
    ('[tjserv]', TJSERV),
    ('Vibessono', VIBESSONO),
    ('DCCore', DCCORE),
    ('DCCoreWeb', DCCOREWEB),
    ('DCCoreWin', DCCOREWIN),
    ('Lmelght', LMELGHT),
    ('fallguyf00', FALLGUYF00),
    ('D_F_D', D_F_D),
    ('Aly', ALY),
    ('Sonota', SONOTA),
    ('Balte', BALTE),
    ('FlacMe', FLACME),
]


class ReadingRealAdverts(unittest.TestCase):

    def test_a_typical_advert(self):
        advert = irc.parse_channel_advert(BSK)

        self.assertEqual(advert["nick"], "Bsk")
        self.assertEqual(advert["files"], 719041)
        self.assertEqual(advert["list_date"], "Aug 10th")

    def test_decoration_between_every_field(self):
        """D_F_D separates with a yen sign, Aly with a tilde. Keying on anything but the
        labels would need a rule per bot."""
        for text, nick, files in ((D_F_D, "D_F_D", 127312), (ALY, "Aly", 389136)):
            with self.subTest(nick=nick):
                advert = irc.parse_channel_advert(text)
                self.assertEqual(advert["nick"], nick)
                self.assertEqual(advert["files"], files)

    def test_separators_the_daemon_never_sees(self):
        """Five bots decorate with bytes that are not valid UTF-8, and irc.py
        reads the socket with errors="ignore" - so the separators are gone
        before the parser is ever called. Five of sixteen is reason enough on
        its own not to key on them."""
        for text, nick in ((FIREHORSESSS, "FireHorsesss"), (BALTE, "Balte"),
                           (SONOTA, "Sonota"), (LMELGHT, "Lmelght")):
            with self.subTest(nick=nick):
                self.assertEqual(irc.parse_channel_advert(text)["nick"], nick)

    def test_a_control_character_between_the_fields(self):
        """The four "\x95" separators are a cp1252 bullet that arrived
        double-encoded. That makes them valid UTF-8, so unlike the five above
        they survive the decode - and strip_control_codes() does not remove
        them either, since it strips mIRC formatting rather than C1."""
        for text, nick in ((BSK, "Bsk"), (DJVIRTUAL, "DJVirtual"),
                           (FALLGUYF00, "fallguyf00"), (FLACME, "FlacMe")):
            with self.subTest(nick=nick):
                self.assertEqual(irc.parse_channel_advert(text)["nick"], nick)

    def test_no_separators_at_all(self):
        """karaoke_dude uses plain single spacing, so "Files Slots:" runs
        together with nothing between the fields at all."""
        advert = irc.parse_channel_advert(KARAOKE_DUDE)

        self.assertEqual(advert["nick"], "karaoke_dude")
        self.assertEqual(advert["files"], 475128)
        self.assertEqual(advert["list_date"], "Aug 19th")

    def test_a_bracketed_nick(self):
        """@[tjserv] - a word-characters-only pattern misses this entirely."""
        advert = irc.parse_channel_advert(TJSERV)

        self.assertEqual(advert["nick"], "[tjserv]")
        self.assertEqual(advert["files"], 77018)

    def test_preamble_before_the_advert(self):
        """Four of the sixteen lead with a sentence of their own - an album
        count, French, a note about an OP. The advert is not the whole line and
        cannot be anchored to its start."""
        for text, nick in ((TJSERV, "[tjserv]"), (DJVIRTUAL, "DJVirtual"),
                           (FALLGUYF00, "fallguyf00"), (FLACME, "FlacMe")):
            with self.subTest(nick=nick):
                self.assertEqual(irc.parse_channel_advert(text)["nick"], nick)

    def test_our_own_wording_for_the_date_and_size(self):
        """DCCore says "created Aug 27th" where the rest say "List: Aug 27th",
        and is the only family that publishes a size at all."""
        advert = irc.parse_channel_advert(DCCOREWIN)

        self.assertEqual(advert["nick"], "DCCoreWin")
        self.assertEqual(advert["files"], 36208)
        self.assertEqual(advert["list_date"], "Aug 27th")
        self.assertEqual(advert["list_size"], "1.21TB")

    def test_dates_from_other_months(self):
        """Sonota's list is from last October and FlacMe's from January. Stale
        lists are the whole reason for reading the date, so a pattern that only
        matched the current month would read as "everything is current"."""
        self.assertEqual(irc.parse_channel_advert(SONOTA)["list_date"], "Oct 18th")
        self.assertEqual(irc.parse_channel_advert(FLACME)["list_date"], "Jan 2nd")
        self.assertEqual(irc.parse_channel_advert(ALY)["list_date"], "Jun 20th")

    def test_a_truncated_advert_still_yields_what_arrived(self):
        """Vibessono's line hit the 512-byte limit and ends at a bare "List:"
        with no date after it. What did arrive is still worth having, and the
        date must read as absent rather than as some fragment of the label."""
        advert = irc.parse_channel_advert(VIBESSONO)

        self.assertEqual(advert["nick"], "Vibessono")
        self.assertEqual(advert["files"], 718005)
        self.assertNotIn("list_date", advert)

    def test_a_missing_field_is_absent_not_zero(self):
        """The same rule parse_search_header() follows: absent means "did not
        say". A freshness check that treats silence as a value invents one."""
        for text in (BSK, D_F_D, TJSERV, DJVIRTUAL, VIBESSONO, ALY):
            with self.subTest(text=text[:24]):
                self.assertNotIn("list_size", irc.parse_channel_advert(text))

    def test_colour_codes_are_stripped_before_the_numbers_are_read(self):
        """The one case here that is not a verbatim capture, and it is marked
        as such: the observer stripped mIRC colour before writing the sample,
        so the fixtures above have none left to strip.

        It cannot be left untested, because a colour code carries DIGITS. mIRC
        writes it as \x03 followed by up to two of them, and bots put one
        immediately before the figure they want coloured - so the count pattern
        would read the colour number as part of the count and silently return a
        file count off by a factor of ten thousand.
        """
        coloured = BSK.replace("719,041", "04719,041")

        self.assertEqual(irc.parse_channel_advert(coloured)["files"], 719041)

    def test_every_captured_advert_parses(self):
        """The sample is the specification. If any real advert stops parsing,
        that is the regression."""
        for nick, text in ALL_CAPTURED:
            with self.subTest(nick=nick):
                advert = irc.parse_channel_advert(text)
                self.assertIsNotNone(advert, "a real advert stopped parsing")
                self.assertEqual(advert["nick"], nick)
                self.assertGreater(advert["files"], 0)


class NotAnAdvert(unittest.TestCase):
    """Both halves are needed. Either alone is ordinary chatter."""

    def test_plain_conversation(self):
        self.assertIsNone(irc.parse_channel_advert("anyone got the new Slayer?"))

    def test_someone_telling_a_friend_what_to_type(self):
        self.assertIsNone(irc.parse_channel_advert("just type @Bsk and wait"))

    def test_a_count_with_no_trigger(self):
        self.assertIsNone(irc.parse_channel_advert("For My List Of: 500 Files"))

    def test_a_trigger_with_no_count(self):
        """The other half. An op answering a newcomer writes the trigger
        exactly as a bot would - and an advert is a bot describing its own
        list, so without a count there is nothing to describe."""
        self.assertIsNone(irc.parse_channel_advert(
            "welcome - Type: @Bsk in the channel and he will send you his list"))

    def test_a_bot_quoted_in_conversation(self):
        self.assertIsNone(irc.parse_channel_advert(
            "he said Type: @FlacMe but it never answers me"))

    def test_a_search_result_line(self):
        self.assertIsNone(irc.parse_channel_advert(
            "!Bsk Slayer - Angel Of Death.mp3  ::INFO:: 4.6MB"))

    def test_nothing_at_all(self):
        self.assertIsNone(irc.parse_channel_advert(""))
        self.assertIsNone(irc.parse_channel_advert(None))


class TheSenderIsTheAuthority(DCCoreTestCase):
    """"Type: @Someone" is characters in a message, and any user can type them.

    The registry exists to decide whether a list we hold is current, so a
    poisoned entry means showing a list as that bot's current one when it was
    never theirs.
    """

    def setUp(self):
        super().setUp()
        runtime.known_bots.clear()
        self.addCleanup(runtime.known_bots.clear)
        runtime.known_bots_flushed_at = time.time()   # suppress disk writes

    def test_an_advert_matching_its_sender_is_recorded(self):
        irc._capture_channel_advert("Bsk", "#Mp3Passion", BSK)

        self.assertIn("bsk", runtime.known_bots)
        self.assertEqual(runtime.known_bots["bsk"]["files"], 719041)

    def test_a_user_claiming_to_be_a_bot_is_ignored(self):
        irc._capture_channel_advert("randomuser", "#Mp3Passion", BSK)

        self.assertEqual(runtime.known_bots, {},
                         "a user impersonated a bot and it was believed")

    def test_the_match_is_case_insensitive(self):
        """IRC nicks are case-insensitive, and a server may echo a different
        case than the bot uses inside its own advert."""
        irc._capture_channel_advert("BSK", "#Mp3Passion", BSK)

        self.assertIn("bsk", runtime.known_bots)

    def test_the_sender_supplies_the_casing_too(self):
        """The advert text is a template the bot wrote once; the sender nick is
        what the server says it is called right now. That is the one somebody
        would type at it, so that is the one worth storing."""
        irc._capture_channel_advert("BSK", "#Mp3Passion", BSK)   # text says "Bsk"

        self.assertEqual(runtime.known_bots["bsk"]["nick"], "BSK")

    def test_a_private_message_is_not_a_channel_advert(self):
        """Adverts are broadcast. A direct message shaped like one is somebody
        trying something."""
        irc._capture_channel_advert("Bsk", "DCCoreWin", BSK)

        self.assertEqual(runtime.known_bots, {})

    def test_ordinary_chatter_registers_nobody(self):
        irc._capture_channel_advert("Bsk", "#Mp3Passion", "back in 10")

        self.assertEqual(runtime.known_bots, {})

    def test_a_later_advert_updates_rather_than_duplicates(self):
        irc._capture_channel_advert("Bsk", "#Mp3Passion", BSK)
        irc._capture_channel_advert("Bsk", "#Mp3Passion",
                                    BSK.replace("719,041", "720,000"))

        self.assertEqual(len(runtime.known_bots), 1)
        self.assertEqual(runtime.known_bots["bsk"]["files"], 720000)

    def test_a_field_a_bot_stops_publishing_is_not_forgotten(self):
        """Adverts get truncated - Vibessono's is, in the captured sample, and
        the cut lands right where the date would be. Dropping a date because
        one advert arrived without it would flip a bot to "freshness unknown"
        at random."""
        truncated = BSK[:BSK.index("List:")]
        self.assertIsNone(irc.parse_channel_advert(truncated).get("list_date"),
                          "the fixture still carries a date - nothing is tested")

        irc._capture_channel_advert("Bsk", "#Mp3Passion", BSK)
        irc._capture_channel_advert("Bsk", "#Mp3Passion", truncated)

        self.assertEqual(runtime.known_bots["bsk"]["list_date"], "Aug 10th")


class TheWholeCapturedSample(DCCoreTestCase):
    """All sixteen through the real capture path, not just the parser."""

    def setUp(self):
        super().setUp()
        runtime.known_bots.clear()
        self.addCleanup(runtime.known_bots.clear)
        runtime.known_bots_flushed_at = time.time()
        for nick, text in ALL_CAPTURED:
            irc._capture_channel_advert(nick, "#Mp3Passion", text)

    def test_every_bot_is_registered_once(self):
        self.assertEqual(len(runtime.known_bots), len(ALL_CAPTURED))
        for nick, _text in ALL_CAPTURED:
            self.assertIn(nick.lower(), runtime.known_bots)

    def test_each_entry_carries_what_that_bot_published(self):
        every = runtime.known_bots

        self.assertEqual(every["[tjserv]"]["files"], 77018)
        self.assertEqual(every["d_f_d"]["list_date"], "Aug 28th")
        self.assertEqual(every["dccorewin"]["list_size"], "1.21TB")
        self.assertNotIn("list_date", every["vibessono"])

    def test_every_entry_knows_where_and_when_it_was_seen(self):
        for nick, entry in runtime.known_bots.items():
            with self.subTest(nick=nick):
                self.assertEqual(entry["channel"], "#Mp3Passion")
                self.assertGreater(entry["last_seen"], 0)

    def test_the_stored_nick_keeps_the_bot_s_own_casing(self):
        """Keyed lowercase because IRC nicks are case-insensitive, displayed as
        the bot writes it - "@DCCoreWin", not "@dccorewin"."""
        self.assertEqual(runtime.known_bots["dccorewin"]["nick"], "DCCoreWin")
        self.assertEqual(runtime.known_bots["karaoke_dude"]["nick"], "karaoke_dude")


class SurvivingARestart(DCCoreTestCase):
    """An empty sidebar until every bot has advertised again is a long wait on
    a five-minute cycle, so the registry is written to disk."""

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.make_tree().root, "known_bots.json")
        self.set_config(KNOWN_BOTS_FILE=self.path)
        previous = db.KNOWN_BOTS_FILE
        db.KNOWN_BOTS_FILE = self.path
        self.addCleanup(setattr, db, "KNOWN_BOTS_FILE", previous)
        runtime.known_bots.clear()
        self.addCleanup(runtime.known_bots.clear)
        runtime.known_bots_flushed_at = 0.0

    def test_a_registry_round_trips(self):
        irc._capture_channel_advert("Bsk", "#Mp3Passion", BSK)
        irc._flush_known_bots(force=True)
        runtime.known_bots.clear()

        runtime.known_bots.update(db.load_known_bots())

        self.assertEqual(runtime.known_bots["bsk"]["files"], 719041)
        self.assertEqual(runtime.known_bots["bsk"]["list_date"], "Aug 10th")

    def test_no_file_yet_is_an_empty_registry_not_an_error(self):
        self.assertEqual(db.load_known_bots(), {})

    def test_an_unreadable_registry_does_not_stop_the_daemon(self):
        """It is rebuilt from adverts within minutes, so a corrupt file costs
        an empty sidebar until then and nothing else."""
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json at all")

        self.assertEqual(db.load_known_bots(), {})

    def test_writes_are_throttled(self):
        """Sixteen bots on a five-minute cycle is a write every twenty seconds
        for nothing - the file is read once, at startup."""
        irc._capture_channel_advert("Bsk", "#Mp3Passion", BSK)   # first flush
        written_at = os.path.getmtime(self.path)
        stamp = runtime.known_bots_flushed_at

        wrote = irc._flush_known_bots(now=stamp + 1.0)

        self.assertFalse(wrote, "a second write landed inside the throttle window")
        self.assertEqual(os.path.getmtime(self.path), written_at)

    def test_the_throttle_lets_a_later_write_through(self):
        irc._capture_channel_advert("Bsk", "#Mp3Passion", BSK)
        stamp = runtime.known_bots_flushed_at

        self.assertTrue(
            irc._flush_known_bots(now=stamp + irc.KNOWN_BOTS_FLUSH_SECONDS + 1))


class TheListenerIsActuallyWired(unittest.TestCase):
    """A parser nothing calls collects nothing.

    This is the shape of #119 - a correct, tested function that no live path
    reached, so the speed record stayed at zero for the life of the daemon.
    Unit tests cannot see that, so it gets asked directly.
    """

    def _source(self, name):
        with open(os.path.join(REPO_ROOT, name), encoding="utf-8") as handle:
            return [line for line in handle.read().splitlines()
                    if not line.strip().startswith("#")]

    def test_channel_messages_reach_the_capture(self):
        calls = [line.strip() for line in self._source("irc.py")
                 if "_capture_channel_advert(" in line and "def " not in line]

        self.assertTrue(
            calls,
            "nothing calls _capture_channel_advert. It belongs on the channel "
            "PRIVMSG path beside _capture_broadcast_search_reply; without that "
            "call the registry is never populated and the parser is dead code.")

    def test_the_capture_sits_beside_the_broadcast_capture(self):
        """Both read passively from the same channel traffic for the same
        reason. Splitting them means one of the two gets a ban check or a
        dispatch guard bolted on and the other silently does not."""
        lines = self._source("irc.py")
        advert = [i for i, line in enumerate(lines)
                  if "_capture_channel_advert(" in line and "def " not in line]
        search = [i for i, line in enumerate(lines)
                  if "_capture_broadcast_search_reply(" in line and "def " not in line]
        self.assertTrue(advert and search, "could not locate both capture calls")

        self.assertLessEqual(
            min(abs(a - s) for a in advert for s in search), 4,
            "the advert capture has drifted away from the broadcast capture")

    def test_the_registry_is_loaded_at_startup(self):
        loads = [line.strip() for line in self._source("oserve.py")
                 if "load_known_bots(" in line]

        self.assertTrue(
            loads,
            "oserve.py no longer loads the bot registry, so persisting it "
            "buys nothing - the sidebar is empty on every restart until all "
            "sixteen have advertised again.")


if __name__ == "__main__":
    unittest.main()
