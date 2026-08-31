"""First tests for commands.py - the admin gate and the two destructive commands.

commands.py is the second-largest module in the daemon and had no test at all.
It holds every admin gate (!ban, !unban, !rehash, !update, !clearqueue), the
user-facing queue commands, and the only two places that edit hard_bans.txt.

Writing them found three defects, and each has a test here named after it:

* @<nick>-remove / CTCP REMOVE dropped a user's queue rows WITHOUT deleting the
  temporary .rar archives those rows named. The rows are the only record the
  archives exist, so they stayed in TMP_ZIP_DIR forever. The freeze sweep, the
  freeze timer and !clearqueue all cleaned up; the path users actually type did
  not.
* !unban truncated hard_bans.txt with open(..., "w") and wrote the survivors
  back line by line. Interrupted, that leaves the file short or empty - and it
  fails OPEN, because security.check_user_status cannot tell a truncated file
  from one with no bans in it.
* !ban appended without checking the previous line ended in a newline, so on a
  hand-edited file it glued two patterns into one and unbanned both.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if os.path.join(REPO_ROOT, "tests") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import announce  # noqa: E402
import commands  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import dcc  # noqa: E402

from tests.support import (DCCoreTestCase, RecordingSocket, TempTree,  # noqa: E402
                           no_disk_writes, silence_debug)


class AdminGate(DCCoreTestCase):
    """is_admin decides who may run !ban, !unban, !rehash, !update, !clearqueue."""

    def test_the_configured_admin_is_admin(self):
        config.ADMIN_NICK = "FLAC"
        self.assertTrue(commands.is_admin("FLAC"))

    def test_the_check_is_case_insensitive(self):
        """IRC nicks are case-insensitive, so the gate has to be too."""
        config.ADMIN_NICK = "FLAC"
        for typed in ("flac", "Flac", "fLaC"):
            with self.subTest(typed=typed):
                self.assertTrue(commands.is_admin(typed))

    def test_an_ordinary_user_is_not_admin(self):
        config.ADMIN_NICK = "FLAC"
        self.assertFalse(commands.is_admin("dave"))

    def test_a_comma_separated_list_admits_every_name(self):
        config.ADMIN_NICK = "FLAC, Neo ,erin"
        for name in ("FLAC", "neo", "ERIN"):
            with self.subTest(name=name):
                self.assertTrue(commands.is_admin(name))
        self.assertFalse(commands.is_admin("dave"))

    def test_the_hardcoded_flac_fallback_stays_gone(self):
        """It made the literal nick "flac" an admin whatever ADMIN_NICK said."""
        config.ADMIN_NICK = "Neo"
        self.assertFalse(commands.is_admin("flac"),
                         "removing the undocumented second admin account must stay removed")

    def test_an_empty_admin_nick_admits_nobody(self):
        """Fail closed: a blank setting must not make everyone an admin."""
        for blank in ("", "   ", ",", None):
            with self.subTest(blank=blank):
                config.ADMIN_NICK = blank
                self.assertFalse(commands.is_admin("dave"))
                self.assertFalse(commands.is_admin(""))


class HardBanFileDurability(DCCoreTestCase):
    """hard_bans.txt was the last file in the project still written in place."""

    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()
        self.bans = os.path.join(self.tree.root, "hard_bans.txt")
        config.HARD_BANS_FILE = self.bans
        config.ADMIN_NICK = "FLAC"
        self.debug = silence_debug(announce)

    def write(self, text):
        with open(self.bans, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def read(self):
        with open(self.bans, "r", encoding="utf-8") as handle:
            return handle.read()

    # --- db-level behaviour ------------------------------------------------

    def test_add_writes_the_pattern(self):
        self.assertTrue(db.add_hard_ban("*!*@spammer.net"))
        self.assertEqual(db.load_hard_bans(), ["*!*@spammer.net"])

    def test_adding_a_duplicate_is_a_no_op(self):
        db.add_hard_ban("*!*@spammer.net")
        self.assertFalse(db.add_hard_ban("*!*@SPAMMER.NET"))
        self.assertEqual(db.load_hard_bans(), ["*!*@spammer.net"])

    def test_remove_takes_only_the_named_pattern(self):
        self.write("alpha*\nbeta*\ngamma*\n")
        self.assertTrue(db.remove_hard_ban("beta*"))
        self.assertEqual(db.load_hard_bans(), ["alpha*", "gamma*"])

    def test_removing_something_absent_leaves_the_file_alone(self):
        self.write("alpha*\nbeta*\n")
        before = self.read()
        self.assertFalse(db.remove_hard_ban("nothere*"))
        self.assertEqual(self.read(), before)

    def test_a_file_with_no_trailing_newline_does_not_glue_patterns(self):
        """The old append wrote straight onto the end of the last line."""
        self.write("alpha*\nbeta*")           # note: no trailing newline
        db.add_hard_ban("gamma*")
        self.assertEqual(db.load_hard_bans(), ["alpha*", "beta*", "gamma*"],
                         "a hand-edited file without a trailing newline merged two patterns")
        self.assertNotIn("beta*gamma*", self.read())

    def test_comments_and_blank_lines_are_ignored(self):
        self.write("# spammers\n\nalpha*\n\n# note\nbeta*\n")
        self.assertEqual(db.load_hard_bans(), ["alpha*", "beta*"])

    def test_a_missing_file_reads_as_no_bans(self):
        self.assertEqual(db.load_hard_bans(), [])

    def test_add_creates_the_file_when_it_does_not_exist(self):
        self.assertFalse(os.path.exists(self.bans))
        db.add_hard_ban("alpha*")
        self.assertTrue(os.path.exists(self.bans))

    # --- the durability property itself ------------------------------------

    def test_a_failed_write_leaves_the_previous_bans_intact(self):
        """The whole point of temp + os.replace, stated as a behaviour.

        Truncate-then-rewrite could not pass this: it destroys the old contents
        before the new ones are safe.
        """
        self.write("alpha*\nbeta*\ngamma*\n")
        original = self.read()

        real_write = db._atomic_write

        def explode(path, text):
            raise OSError(28, "No space left on device")

        db._atomic_write = explode
        try:
            with self.assertRaises(OSError):
                db.remove_hard_ban("beta*")
        finally:
            db._atomic_write = real_write

        self.assertEqual(self.read(), original,
                         "an interrupted !unban must not cost the operator their ban list")

    def test_no_temp_file_is_left_behind_on_failure(self):
        """A litter of .tmp_*.swap files would be read by nothing but confuse everyone."""
        self.write("alpha*\n")
        real_replace = os.replace

        def refuse(src, dst):
            raise OSError("replace refused")

        os.replace = refuse
        try:
            with self.assertRaises(OSError):
                db.add_hard_ban("beta*")
        finally:
            os.replace = real_replace

        leftovers = [n for n in os.listdir(self.tree.root) if n.startswith(".tmp_")]
        self.assertEqual(leftovers, [], f"temp files left behind: {leftovers}")

    def test_the_handlers_no_longer_open_the_file_for_writing(self):
        """Source check, because the two defects above were both in this shape."""
        with open(os.path.join(REPO_ROOT, "commands.py"), encoding="utf-8") as handle:
            source = handle.read()
        for stale in ('open(filename, "w"', 'open(filename, "a"'):
            with self.subTest(stale=stale):
                self.assertFalse(stale in source,
                                 f"{stale} writes hard_bans.txt in place")

    # --- the admin gate on the handlers ------------------------------------

    def test_a_non_admin_cannot_add_a_ban(self):
        self.write("alpha*\n")
        commands.handle_hard_ban_request("dave", "#mp3passion", "!ban *!*@victim.net")
        self.assertEqual(db.load_hard_bans(), ["alpha*"])

    def test_a_non_admin_cannot_remove_a_ban(self):
        """The dangerous direction: unbanning is how a spammer would get back in."""
        self.write("alpha*\n")
        commands.handle_hard_unban_request("dave", "#mp3passion", "!unban alpha*")
        self.assertEqual(db.load_hard_bans(), ["alpha*"])

    def test_the_admin_can_add_and_remove(self):
        commands.handle_hard_ban_request("FLAC", "#mp3passion", "!ban *!*@spammer.net")
        self.assertEqual(db.load_hard_bans(), ["*!*@spammer.net"])
        commands.handle_hard_unban_request("FLAC", "#mp3passion", "!unban *!*@spammer.net")
        self.assertEqual(db.load_hard_bans(), [])

    def test_ban_with_no_pattern_changes_nothing(self):
        self.write("alpha*\n")
        for text in ("!ban", "!ban    "):
            with self.subTest(text=text):
                commands.handle_hard_ban_request("FLAC", "#c", text)
                self.assertEqual(db.load_hard_bans(), ["alpha*"])


class QueueRemoveCleansTempArchives(DCCoreTestCase):
    """@<nick>-remove dropped the rows and orphaned the archives they named."""

    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()
        self.tmp_zips = os.path.join(self.tree.root, "tmp_zips")
        os.makedirs(self.tmp_zips, exist_ok=True)
        config.TMP_ZIP_DIR = self.tmp_zips
        config.ADMIN_NICK = "FLAC"
        self.sock = RecordingSocket()
        silence_debug(announce)
        no_disk_writes(db)

    def archive(self, name="Black_Album_(1991).rar"):
        path = os.path.join(self.tmp_zips, name)
        with open(path, "wb") as handle:
            handle.write(b"RAR payload")
        return path

    def packed_row(self, path, user="dave"):
        """A row as it looks AFTER inline_rar_packer completes: path is the .rar."""
        return {"file": os.path.basename(path), "path": path, "channel": "#mp3passion",
                "user_raw": user, "is_temporary_zip": True, "is_unpacked_rar_folder": False}

    # --- the bug -----------------------------------------------------------

    def test_remove_deletes_the_users_temp_archive(self):
        path = self.archive()
        config.dcc_queue = {"dave": [self.packed_row(path)]}

        commands.handle_queue_remove(self.sock, "dave", "#mp3passion")

        self.assertFalse(os.path.exists(path),
                         "the archive is orphaned the moment the row naming it is dropped")
        self.assertNotIn("dave", config.dcc_queue)

    def test_remove_still_clears_the_queue_and_the_freeze(self):
        config.dcc_queue = {"dave": [self.packed_row(self.archive())]}
        config.frozen_queues = {"dave": 1234.0}

        commands.handle_queue_remove(self.sock, "dave", "#mp3passion")

        self.assertNotIn("dave", config.dcc_queue)
        self.assertNotIn("dave", config.frozen_queues)

    # --- what it must NOT delete -------------------------------------------

    def test_remove_never_deletes_the_source_album(self):
        """An is_unpacked_rar_folder row's path is the music library directory."""
        config.dcc_queue = {"dave": [{
            "file": "Black Album.rar", "path": self.tree.album, "channel": "#c",
            "user_raw": "dave", "is_temporary_zip": True, "is_unpacked_rar_folder": True}]}

        commands.handle_queue_remove(self.sock, "dave", "#c")

        self.assertTrue(os.path.isdir(self.tree.album),
                        "deleting the source folder would delete the music itself")

    def test_the_source_album_is_never_even_offered_to_os_remove(self):
        """Assert the GUARD, not the outcome.

        The test above passes even without the is_unpacked_rar_folder check,
        because os.remove refuses a directory and the OSError is swallowed. That
        is luck, not intent: it relies on every such row's path being a directory
        forever. Record the calls instead, so the skip itself is what is pinned.
        """
        config.dcc_queue = {"dave": [{
            "file": "Black Album.rar", "path": self.tree.album, "channel": "#c",
            "user_raw": "dave", "is_temporary_zip": True, "is_unpacked_rar_folder": True}]}

        attempted = []
        real_remove = os.remove

        def record(path):
            attempted.append(path)
            return real_remove(path)

        os.remove = record
        try:
            commands.handle_queue_remove(self.sock, "dave", "#c")
        finally:
            os.remove = real_remove

        self.assertEqual(attempted, [],
                         "an unpacked-folder row must be skipped before os.remove is reached")

    def test_remove_keeps_an_archive_another_queue_still_wants(self):
        """Archive names come from the FOLDER, so two users share one file."""
        path = self.archive()
        config.dcc_queue = {"dave": [self.packed_row(path)],
                            "erin": [self.packed_row(path, user="erin")]}

        commands.handle_queue_remove(self.sock, "dave", "#c")

        self.assertTrue(os.path.exists(path),
                        "erin's queue still points at this archive")
        self.assertIn("erin", config.dcc_queue)

    def test_remove_keeps_an_archive_that_is_being_streamed(self):
        path = self.archive()
        row = self.packed_row(path)
        config.dcc_queue = {"dave": [row]}
        config.active_transfers = [{"user": "erin", "file": row["file"], "bytes_sent": 512}]

        commands.handle_queue_remove(self.sock, "dave", "#c")

        self.assertTrue(os.path.exists(path),
                        "pulling the file out from under a live transfer truncates it")

    def test_a_plain_library_file_is_never_touched(self):
        """is_temporary_zip is False for an ordinary track request."""
        track = self.tree.tracks[0]
        config.dcc_queue = {"dave": [{
            "file": os.path.basename(track), "path": track, "channel": "#c",
            "user_raw": "dave", "is_temporary_zip": False}]}

        commands.handle_queue_remove(self.sock, "dave", "#c")

        self.assertTrue(os.path.exists(track))

    def test_a_row_that_is_not_a_dict_does_not_crash_it(self):
        """dcc_queue.txt is hand-editable, so a malformed row has to be survivable."""
        config.dcc_queue = {"dave": ["not-a-dict", self.packed_row(self.archive())]}
        commands.handle_queue_remove(self.sock, "dave", "#c")
        self.assertNotIn("dave", config.dcc_queue)


class AdminClearQueueKeptItsBehaviour(DCCoreTestCase):
    """!clearqueue's inline cleanup was replaced by the shared helper."""

    def setUp(self):
        super().setUp()
        self.tree = self.make_tree()
        self.tmp_zips = os.path.join(self.tree.root, "tmp_zips")
        os.makedirs(self.tmp_zips, exist_ok=True)
        config.TMP_ZIP_DIR = self.tmp_zips
        config.ADMIN_NICK = "FLAC"
        silence_debug(announce)
        no_disk_writes(db)

    def archive(self, name="Album.rar"):
        path = os.path.join(self.tmp_zips, name)
        with open(path, "wb") as handle:
            handle.write(b"RAR payload")
        return path

    def packed_row(self, path, user="dave"):
        return {"file": os.path.basename(path), "path": path, "channel": "#c",
                "user_raw": user, "is_temporary_zip": True, "is_unpacked_rar_folder": False}

    def test_admin_clear_still_removes_the_archive(self):
        path = self.archive()
        config.dcc_queue = {"dave": [self.packed_row(path)]}

        commands.handle_admin_clear_queue("FLAC", "#c", "!clearqueue dave")

        self.assertFalse(os.path.exists(path))
        self.assertNotIn("dave", config.dcc_queue)

    def test_admin_clear_still_spares_a_shared_archive(self):
        path = self.archive()
        config.dcc_queue = {"dave": [self.packed_row(path)],
                            "erin": [self.packed_row(path, user="erin")]}

        commands.handle_admin_clear_queue("FLAC", "#c", "!clearqueue dave")

        self.assertTrue(os.path.exists(path))

    def test_a_non_admin_cannot_clear_someone_elses_queue(self):
        config.dcc_queue = {"erin": [self.packed_row(self.archive(), user="erin")]}

        commands.handle_admin_clear_queue("dave", "#c", "!clearqueue erin")

        self.assertIn("erin", config.dcc_queue)

    def test_clearqueue_without_a_nick_changes_nothing(self):
        config.dcc_queue = {"erin": [self.packed_row(self.archive(), user="erin")]}
        commands.handle_admin_clear_queue("FLAC", "#c", "!clearqueue")
        self.assertIn("erin", config.dcc_queue)


class BothPathsShareOneImplementation(unittest.TestCase):
    """The inline copy in !clearqueue was replaced, not duplicated."""

    def setUp(self):
        with open(os.path.join(REPO_ROOT, "commands.py"), encoding="utf-8") as handle:
            self.source = handle.read()

    def test_both_handlers_call_the_shared_helper(self):
        self.assertEqual(self.source.count("dcc.discard_orphaned_temp_archives("), 2,
                         "@<nick>-remove and !clearqueue must both use it")

    def test_commands_no_longer_removes_files_itself(self):
        self.assertFalse("os.remove(" in self.source,
                         "temp-archive deletion belongs in dcc.py, where the guards live")


if __name__ == "__main__":
    unittest.main()


class RehashPreservesEveryRuntimeContainer(unittest.TestCase):
    """!rehash reloads config, which re-executes config.py and rebinds every
    module-level container to a fresh empty one. commands.PRESERVE_RUNTIME is
    the list of what gets rescued first.

    The list falls out of date. It was written for the containers that existed
    when it was added, and the cross-bot fetch feature later added
    config.fetch_queue and config.fetched_bot_lists without touching it - so a
    rehash silently emptied both: every fetched bot list (each one a real
    multi-MB DCC transfer) gone, and count_active_fetches() reporting 0 active
    while transfers were still moving bytes, which is the same failure the
    'active_transfers' comment in that list already warns about for the other
    slot pool.

    So this does not check for those two names. It derives the set from
    config.py and asserts every runtime container is either preserved or
    deliberately excluded with a reason - which catches the NEXT one somebody
    adds, not just the two that prompted it.
    """

    # Excluded on purpose. Each entry is a claim about WHY losing it is fine.
    NOT_PRESERVED = {
        "CUSTOM_THEME":
            "a setting, not runtime state: the operator's per-role colour "
            "overrides, read from config.py or local_config.py. Losing it on a "
            "rehash is not a loss - re-reading it is the point, the same way "
            "ADMIN_HOSTMASKS below is meant to be re-read.",

        "ADMIN_HOSTMASKS":
            "a setting read from local_config.py, not runtime state - it is "
            "SUPPOSED to be re-read from the file on a rehash",
        "vip_queue":
            "transient OUTPUT, not state. commands.py says so explicitly: "
            "restoring it would replay lines addressed to channels the "
            "handler is about to PART",
        "broadcast_search_results":
            "the results of a 30-second @find broadcast window. The window "
            "cannot outlive the reload, so the rows have nothing to belong to",
        "channel_users":
            "preserved, but by the dedicated ram_backup_users path rather "
            "than PRESERVE_RUNTIME, because it is deep-copied",
        "dcc_queue":
            "preserved, but by the dedicated ram_backup_queue path, which "
            "also lowercases the keys on the way back in",
    }

    def _config_containers(self):
        """Module-level dict/list assignments in config.py, read from source.

        Read from the FILE rather than from the imported module, so a
        container another test has added at runtime cannot make this pass.

        Two shapes count, because config.py has both:

            dcc_queue = {}                 a literal - PRESERVE_RUNTIME's job
            dcc_queue = runtime.dcc_queue   bound from runtime.py (see that
                                           module) - structurally survives a
                                           reload regardless of PRESERVE_RUNTIME,
                                           but still a "container this file
                                           defines" for this test's purpose:
                                           accounted for one way or the other,
                                           not silently missed by either.
        """
        import ast
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names = []
        for node in tree.body:
            # `ADMIN_HOSTMASKS = [...]` is an ast.Assign, but
            # `ADMIN_HOSTMASKS: list = [...]` is an ast.AnnAssign - a different
            # node type carrying `.target` rather than `.targets`. Matching
            # only ast.Assign made every annotated container invisible here,
            # and this guard then reported the name as one config.py no longer
            # defines.
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets, value = [node.target], node.value
            else:
                continue
            if value is None:
                continue  # a bare annotation declares a type and binds nothing
            is_literal = isinstance(value, (ast.Dict, ast.List))
            is_runtime_binding = (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "runtime")
            if not (is_literal or is_runtime_binding):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
        return names

    def test_every_runtime_container_is_preserved_or_explicitly_excluded(self):
        containers = self._config_containers()
        self.assertTrue(containers, "found no containers in config.py - the "
                                    "parser is wrong, not the code")

        preserved = set(commands.PRESERVE_RUNTIME)
        unaccounted = [n for n in containers
                       if n not in preserved and n not in self.NOT_PRESERVED]

        self.assertEqual(
            unaccounted, [],
            "config.py defines runtime container(s) that a rehash will silently "
            "empty: " + ", ".join(unaccounted) + ". Add each to "
            "commands.PRESERVE_RUNTIME, or to this test's NOT_PRESERVED with a "
            "reason why losing it is safe.")

    def test_the_fetch_containers_are_in_the_preserved_list(self):
        """The two that prompted this. Named explicitly so a future edit that
        drops them fails with an obvious message rather than a derived one."""
        for name in ("fetch_queue", "fetched_bot_lists"):
            with self.subTest(container=name):
                self.assertIn(name, commands.PRESERVE_RUNTIME)

    def test_the_exclusion_list_has_not_gone_stale(self):
        """Control. If a name is removed from config.py, its excuse here should
        go too - otherwise the exclusion list silently grows into a place where
        real containers can hide."""
        containers = set(self._config_containers())
        stale = [n for n in self.NOT_PRESERVED if n not in containers]
        self.assertEqual(stale, [],
                         "NOT_PRESERVED names something config.py no longer "
                         "defines: " + ", ".join(stale))

    def test_preserved_containers_survive_a_config_reload(self):
        """The behaviour itself, not just the list: values put in before a
        reload are still there afterwards."""
        import importlib
        config.fetch_queue = {"abc123": {"state": "receiving", "bot": "somebot"}}
        config.fetched_bot_lists = {"somebot": {"bot": "somebot", "entries": [1, 2, 3]}}

        preserved = {k: getattr(config, k) for k in commands.PRESERVE_RUNTIME
                     if hasattr(config, k)}
        importlib.reload(config)
        self.addCleanup(importlib.reload, config)

        # The reload wipes them - that is the whole point of the rescue.
        self.assertEqual(config.fetch_queue, {},
                         "fixture invariant: the reload should have emptied this")

        for key, value in preserved.items():
            setattr(config, key, value)

        self.assertEqual(config.fetch_queue["abc123"]["state"], "receiving")
        self.assertEqual(config.fetched_bot_lists["somebot"]["entries"], [1, 2, 3])


class RehashNickChangeGoesLive(unittest.TestCase):
    """A NICKNAME edit picked up by a rehash used to only re-baseline internal
    bookkeeping and defer the actual rename to whatever reconnect happened to
    occur next - which could be minutes away, or days. Reported live: an
    operator changed the nickname through the web dashboard's Settings page,
    watched the bot answer to the OLD name until an unrelated disconnect
    happened to occur, and only then saw the new one - looking exactly like
    the rehash had crashed the connection to apply itself, when really it had
    just silently deferred.

    handle_rehash_request() itself is not called here - nothing else in this
    suite calls it directly either, since it does a real importlib.reload()
    of half the daemon and rebinding module-level objects (a fresh
    threading.Lock(), a fresh {}) out from under a shared test process risks
    corrupting state for every other test that runs afterwards. These test
    the extracted pure decision (commands.rehash_nick_change_line()) instead.
    """

    def test_a_real_rename_produces_the_nick_line(self):
        self.assertEqual(
            commands.rehash_nick_change_line("DCCore", "DCCoreWeb"),
            "NICK DCCoreWeb\r\n")

    def test_case_only_difference_is_not_a_rename(self):
        """IRC nicks are case-insensitive; the server would reject this NICK
        as pointless (or worse, treat it as a fresh collision check) for a
        rename that never actually happened."""
        self.assertIsNone(commands.rehash_nick_change_line("DCCore", "dccore"))

    def test_no_baseline_yet_sends_nothing(self):
        """baseline_nick is None on a fresh daemon's very first rehash before
        ORIGINAL_NICK has ever been set - nothing to compare against, so no
        NICK line, not a crash."""
        self.assertIsNone(commands.rehash_nick_change_line(None, "DCCore"))

    def test_unchanged_nickname_sends_nothing(self):
        self.assertIsNone(commands.rehash_nick_change_line("DCCore", "DCCore"))
