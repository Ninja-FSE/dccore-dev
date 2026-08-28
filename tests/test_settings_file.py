"""settings.conf: plain-text overrides, typed from the Python defaults.

The daemon targets Python 3.10, where the only stdlib config parser is
configparser and everything it returns is a string. tomllib, which carries real
types, is 3.11+. So something has to say what type each value is - and 23 of the
settings are not strings.

Keeping the default as a Python literal makes the default itself that
declaration: MAX_DCC_SLOTS = 5 is an int, so its override is read as an int.
The alternative was a hand-maintained name-to-type table, which is the shape of
thing this codebase has already been bitten by twice (PRESERVE_RUNTIME, and the
two drifted import lists).

The trap in doing it that way is small and specific, and the first test class
below exists for it: bool is a SUBCLASS of int in Python, so a naive
isinstance(default, int) check matches True and False too and every flag would
be read as a number.
"""

import ast
import contextlib
import importlib
import io
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import config  # noqa: E402
import settings_file  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


class Coercion(unittest.TestCase):
    """A raw string becomes the type of the default beside it."""

    def test_a_flag_is_not_read_as_a_number(self):
        """The trap. bool is a subclass of int, so an isinstance(default, int)
        test written before the bool test matches True and False as well, and
        every yes/no setting silently becomes 1 or 0."""
        self.assertIs(settings_file.coerce("DEBUG_MODE", "true", False), True)
        self.assertIs(settings_file.coerce("DEBUG_MODE", "false", False), False)

    def test_every_spelling_of_yes_and_no(self):
        for text in ("true", "TRUE", "yes", "on", "1", " True "):
            with self.subTest(value=text):
                self.assertIs(settings_file.coerce("F", text, False), True)
        for text in ("false", "FALSE", "no", "off", "0", " False "):
            with self.subTest(value=text):
                self.assertIs(settings_file.coerce("F", text, False), False)

    def test_a_flag_rejects_something_that_is_neither(self):
        with self.assertRaises(ValueError) as caught:
            settings_file.coerce("DEBUG_MODE", "maybe", False)
        self.assertIn("yes/no", str(caught.exception))

    def test_whole_numbers_and_decimals(self):
        self.assertEqual(settings_file.coerce("MAX_DCC_SLOTS", " 7 ", 3), 7)
        self.assertEqual(settings_file.coerce("MSG_DELAY", "1.25", 0.5), 1.25)
        self.assertIsInstance(settings_file.coerce("MAX_DCC_SLOTS", "7", 3), int)

    def test_a_number_that_is_not_a_number_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            settings_file.coerce("MAX_DCC_SLOTS", "lots", 3)
        self.assertIn("whole number", str(caught.exception))

    def test_lists_split_on_commas_and_drop_blanks(self):
        self.assertEqual(
            settings_file.coerce("ADMIN_HOSTMASKS", "a.users.net, b.users.net,", []),
            ["a.users.net", "b.users.net"])

    def test_a_none_default_means_unset_unless_you_say_otherwise(self):
        """RAR_BINARY = None means "look on PATH". An empty value in the file
        has to mean the same thing, not an empty string that then gets used as
        a path."""
        self.assertIsNone(settings_file.coerce("RAR_BINARY", "", None))
        self.assertEqual(settings_file.coerce("RAR_BINARY", "/usr/bin/rar", None),
                         "/usr/bin/rar")

    def test_strings_are_left_alone(self):
        self.assertEqual(settings_file.coerce("NICKNAME", "DCCoreWin", "DCCore"),
                         "DCCoreWin")


class Parsing(unittest.TestCase):

    def test_sections_are_only_for_reading_and_are_flattened(self):
        parsed = settings_file.parse(
            "[irc]\nNICKNAME = Bot\n\n[limits]\nMAX_DCC_SLOTS = 4\n")
        self.assertEqual(parsed, {"NICKNAME": "Bot", "MAX_DCC_SLOTS": "4"})

    def test_a_file_with_no_sections_at_all_works(self):
        """The headers are optional - a flat list of settings is valid."""
        self.assertEqual(settings_file.parse("NICKNAME = Bot\n"), {"NICKNAME": "Bot"})

    def test_the_same_setting_twice_is_refused(self):
        """Silently keeping one of two conflicting values for one setting is
        exactly the quiet wrongness this file exists to avoid."""
        with self.assertRaises(settings_file.SettingsError) as caught:
            settings_file.parse("[a]\nNICKNAME = One\n\n[b]\nNICKNAME = Two\n")
        self.assertIn("set twice", str(caught.exception))

    def test_a_percent_sign_is_an_ordinary_character(self):
        """configparser treats % as a reference by default, so a Windows path
        or a password containing one fails in a way that reads like a corrupt
        file."""
        parsed = settings_file.parse("ADMIN_PASSWORD_HASH = abc%def%ghi\n")
        self.assertEqual(parsed["ADMIN_PASSWORD_HASH"], "abc%def%ghi")

    def test_keys_are_matched_whatever_case_they_are_written_in(self):
        parsed = settings_file.parse("nickname = Bot\n")
        self.assertEqual(parsed, {"NICKNAME": "Bot"})

    def test_a_backslash_path_survives(self):
        """Windows operators write these constantly."""
        parsed = settings_file.parse(r"FILE_DIRECTORY = Z:\1 Metal" + "\n")
        self.assertEqual(parsed["FILE_DIRECTORY"], r"Z:\1 Metal")


class WhatMayBeOverridden(unittest.TestCase):

    def test_ordinary_settings_may_be(self):
        self.assertTrue(settings_file.is_overridable("MAX_DCC_SLOTS", 3))
        self.assertTrue(settings_file.is_overridable("NICKNAME", "DCCore"))

    def test_the_colour_codes_may_not_be(self):
        """They are raw mIRC control bytes, not preferences - expressing them
        in a text file would mean typing escape sequences. A named theme
        setting is the right shape for that if themes ever land."""
        self.assertFalse(settings_file.is_overridable("C_GREEN", "\x0303"))
        self.assertFalse(settings_file.is_overridable("C_BOLD", "\x02"))

    def test_runtime_containers_may_not_be(self):
        """They are lowercase, and they are live state rather than settings -
        see runtime.py."""
        self.assertFalse(settings_file.is_overridable("dcc_queue", {}))
        self.assertFalse(settings_file.is_overridable("active_transfers", []))


class ApplyingTheFile(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dccore-settings-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "settings.conf")
        self.logged = []

    def _write(self, text):
        with io.open(self.path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def _apply(self, namespace):
        return settings_file.apply_to(namespace, path=self.path,
                                      log=self.logged.append)

    def test_a_recognised_setting_is_applied_with_its_type(self):
        namespace = {"MAX_DCC_SLOTS": 3, "NICKNAME": "DCCore"}
        self._write("MAX_DCC_SLOTS = 9\n")

        report = self._apply(namespace)

        self.assertEqual(namespace["MAX_DCC_SLOTS"], 9)
        self.assertEqual(namespace["NICKNAME"], "DCCore", "an untouched "
                                                          "setting changed")
        self.assertEqual(report["applied"], {"MAX_DCC_SLOTS": 9})

    def test_a_misspelled_setting_is_reported_and_skipped(self):
        """Silently ignoring a typo is the worst outcome: the operator edited
        the file, saw no error, and the value never took effect."""
        namespace = {"MAX_DCC_SLOTS": 3}
        self._write("MAX_DCC_SLOT = 9\n")

        report = self._apply(namespace)

        self.assertEqual(namespace["MAX_DCC_SLOTS"], 3)
        self.assertEqual(report["unknown"], ["MAX_DCC_SLOT"])
        self.assertTrue(any("MAX_DCC_SLOT" in line for line in self.logged))

    def test_a_bad_value_keeps_the_default_and_says_so(self):
        namespace = {"MAX_DCC_SLOTS": 3}
        self._write("MAX_DCC_SLOTS = plenty\n")

        report = self._apply(namespace)

        self.assertEqual(namespace["MAX_DCC_SLOTS"], 3)
        self.assertEqual(len(report["bad"]), 1)
        self.assertTrue(any("Keeping the default" in line for line in self.logged))

    def test_a_missing_file_is_not_an_error(self):
        """Most installs will not have one, and that has to be silent."""
        namespace = {"MAX_DCC_SLOTS": 3}
        report = settings_file.apply_to(
            namespace, path=os.path.join(self.tmp, "nope.conf"),
            log=self.logged.append)

        self.assertEqual(namespace["MAX_DCC_SLOTS"], 3)
        self.assertEqual(self.logged, [], "a missing file should say nothing")
        self.assertIsNone(report["read_error"])

    def test_an_unparseable_file_does_not_stop_the_daemon(self):
        """A daemon that will not start at 3am over one malformed line is
        worse than one that starts on defaults and says which file it could
        not read - the same stance webserver.start() takes about Flask."""
        namespace = {"MAX_DCC_SLOTS": 3}
        self._write("this is not a settings file at all\n[[[\n")

        report = self._apply(namespace)

        self.assertEqual(namespace["MAX_DCC_SLOTS"], 3)
        self.assertIsNotNone(report["read_error"])
        self.assertTrue(any("continuing with the built-in defaults" in line
                            for line in self.logged))

    def test_one_bad_value_does_not_discard_the_good_ones(self):
        namespace = {"MAX_DCC_SLOTS": 3, "NICKNAME": "DCCore"}
        self._write("MAX_DCC_SLOTS = plenty\nNICKNAME = Working\n")

        self._apply(namespace)

        self.assertEqual(namespace["MAX_DCC_SLOTS"], 3)
        self.assertEqual(namespace["NICKNAME"], "Working")


class ExistingInstallsKeepWorking(DCCoreTestCase):
    """local_config.py is not deprecated by this and is not removed.

    Both the operator's Linux bot and the Windows one have a real one, with a
    nickname, channels, paths and an admin password hash in it. If adding
    settings.conf stopped that file being read, both would come back up on the
    shipped defaults - wrong nick, wrong channels, admin console disabled.
    """

    def _reload_config_with(self, local_config_body=None, settings_body=None):
        tmp = tempfile.mkdtemp(prefix="dccore-cfg-test-")

        # addCleanup is LIFO. The final reload has to happen once the temp
        # directory is off sys.path, the module is purged and the environment
        # variable is gone - so it is registered FIRST, to run LAST.
        self.addCleanup(self._quiet_reload)
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.addCleanup(lambda: tmp in sys.path and sys.path.remove(tmp))
        self.addCleanup(sys.modules.pop, "local_config", None)
        self.addCleanup(os.environ.pop, "DCCORE_SETTINGS_FILE", None)

        # Always write a local_config.py, even an empty one: a developer with
        # a real one on their machine would otherwise get a different result
        # from CI, which has none.
        with io.open(os.path.join(tmp, "local_config.py"), "w", encoding="utf-8") as handle:
            handle.write(local_config_body or "# none\n")

        if settings_body is not None:
            settings_path = os.path.join(tmp, "settings.conf")
            with io.open(settings_path, "w", encoding="utf-8") as handle:
                handle.write(settings_body)
            os.environ["DCCORE_SETTINGS_FILE"] = settings_path
        else:
            os.environ["DCCORE_SETTINGS_FILE"] = os.path.join(tmp, "absent.conf")

        sys.path.insert(0, tmp)
        sys.modules.pop("local_config", None)
        self._quiet_reload()
        return config

    def _quiet_reload(self):
        with contextlib.redirect_stdout(io.StringIO()):
            importlib.reload(config)

    def test_local_config_alone_still_works(self):
        cfg = self._reload_config_with(local_config_body='NICKNAME = "FromPy"\n')
        self.assertEqual(cfg.NICKNAME, "FromPy")

    def test_settings_conf_alone_works(self):
        cfg = self._reload_config_with(settings_body="NICKNAME = FromConf\n")
        self.assertEqual(cfg.NICKNAME, "FromConf")

    def test_settings_conf_wins_where_both_set_the_same_name(self):
        """It is applied second on purpose: during a migration the file being
        actively edited should be the one that takes effect."""
        cfg = self._reload_config_with(
            local_config_body='NICKNAME = "FromPy"\nPORT = 6667\n',
            settings_body="NICKNAME = FromConf\n")

        self.assertEqual(cfg.NICKNAME, "FromConf")
        self.assertEqual(cfg.PORT, 6667, "a setting only local_config.py sets "
                                         "was lost")

    def test_neither_file_leaves_the_shipped_defaults(self):
        cfg = self._reload_config_with()
        self.assertEqual(cfg.PORT, 6667)


class TheSampleStaysInStepWithConfig(unittest.TestCase):
    """settings.conf.sample is generated, and this is what keeps it honest.

    Written by hand it would drift the moment somebody adds a setting, which
    is the failure this project has already had with PRESERVE_RUNTIME and with
    the two import lists. Here the fix for a failure is to run the script.
    """

    def test_the_committed_sample_matches_what_the_generator_produces(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        self.addCleanup(lambda: sys.path.remove(os.path.join(REPO_ROOT, "scripts")))
        import gen_settings_sample

        expected = gen_settings_sample.build()
        with io.open(os.path.join(REPO_ROOT, "settings.conf.sample"),
                     encoding="utf-8") as handle:
            actual = handle.read()

        self.assertEqual(
            actual, expected,
            "settings.conf.sample is out of step with config.py. Run:\n"
            "    python scripts/gen_settings_sample.py")

    def test_every_overridable_setting_is_documented(self):
        with io.open(os.path.join(REPO_ROOT, "settings.conf.sample"),
                     encoding="utf-8") as handle:
            sample = handle.read()

        # From config.py's SOURCE, not vars(config): the live module also
        # carries names the daemon and the test harness set at runtime
        # (ORIGINAL_NICK, MY_IP_OR_DOCK), which are not settings anybody can
        # put in a file.
        import ast
        with io.open(os.path.join(REPO_ROOT, "config.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        missing = []
        # Both node types: an annotated setting (`MAX_DCC_SLOTS: int = 3`) is
        # an ast.AnnAssign, not an ast.Assign, and matching only the latter
        # would make this find nothing and pass vacuously.
        sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        self.addCleanup(lambda: sys.path.remove(os.path.join(REPO_ROOT, "scripts")))
        import gen_settings_sample

        for node in tree.body:
            targets, value_node = gen_settings_sample.assignment_parts(node)
            if targets is None:
                continue
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                try:
                    default = ast.literal_eval(value_node)
                except (ValueError, SyntaxError):
                    continue
                if (settings_file.is_overridable(target.id, default)
                        and f"#{target.id} = " not in sample):
                    missing.append(target.id)

        self.assertEqual(missing, [],
                         "settings an operator may set but the sample never "
                         "mentions: " + ", ".join(missing))


class BothAssignmentFormsAreSeen(unittest.TestCase):
    """`MAX_DCC_SLOTS = 3` and `MAX_DCC_SLOTS: int = 3` are different AST
    nodes - Assign and AnnAssign - and the tooling here matched only the
    first.

    That mattered the moment type annotations were proposed for every
    setting (issue #100): the generator would have emitted a sample with
    nothing in it, and two of the checks would have found nothing and passed
    vacuously, reporting green while guarding nothing.
    """

    def _generator(self):
        scripts = os.path.join(REPO_ROOT, "scripts")
        sys.path.insert(0, scripts)
        self.addCleanup(lambda: scripts in sys.path and sys.path.remove(scripts))
        import gen_settings_sample
        return gen_settings_sample

    def test_both_forms_give_the_same_name_and_value(self):
        generator = self._generator()
        for source in ("MAX_DCC_SLOTS = 3", "MAX_DCC_SLOTS: int = 3"):
            with self.subTest(source=source):
                targets, value = generator.assignment_parts(ast.parse(source).body[0])
                self.assertEqual([t.id for t in targets], ["MAX_DCC_SLOTS"])
                self.assertEqual(ast.literal_eval(value), 3)

    def test_a_bare_annotation_has_a_name_but_no_value(self):
        """`NICKNAME: str` declares the type without giving a value."""
        targets, value = self._generator().assignment_parts(
            ast.parse("NICKNAME: str").body[0])

        self.assertEqual([t.id for t in targets], ["NICKNAME"])
        self.assertIsNone(value)

    def test_something_that_is_not_an_assignment_is_ignored(self):
        """Control - a def or an import must not be mistaken for a setting."""
        targets, value = self._generator().assignment_parts(
            ast.parse("import os").body[0])
        self.assertIsNone(targets)


class AHashInsideAValueIsNotAComment(unittest.TestCase):
    """The generated sample carried a junk line above every channel setting.

    `_doc_lines()` found a setting's inline comment by splitting the source
    line on "#", which cuts inside the string literal for

        CHANNEL = "#mp3passion,#mp3servers,..."

    inventing a comment out of the value's own text. It shipped, above both
    CHANNEL and DEBUG_CHANNEL.
    """

    def _doc_lines(self, source):
        scripts = os.path.join(REPO_ROOT, "scripts")
        sys.path.insert(0, scripts)
        self.addCleanup(lambda: scripts in sys.path and sys.path.remove(scripts))
        import gen_settings_sample
        return gen_settings_sample._doc_lines([source], ast.parse(source).body[0])

    def test_a_hash_in_the_value_produces_no_comment(self):
        self.assertEqual(self._doc_lines('CHANNEL = "#mp3passion,#mp3servers"'), [])

    def test_a_real_inline_comment_is_still_found(self):
        """Control. The fix must not stop finding genuine comments, which are
        most of the sample's documentation."""
        self.assertEqual(
            self._doc_lines("MAX_DCC_SLOTS = 3      # Maximum simultaneous live downloads"),
            ["Maximum simultaneous live downloads"])

    def test_a_hash_in_the_value_and_a_real_comment_after_it(self):
        """Both on one line - the only case that tells the two apart."""
        self.assertEqual(
            self._doc_lines('CHANNEL = "#chan"   # the channels to join'),
            ["the channels to join"])

    def test_an_annotated_setting_keeps_its_comment(self):
        self.assertEqual(
            self._doc_lines("MAX_DCC_SLOTS: int = 3   # how many at once"),
            ["how many at once"])


if __name__ == "__main__":
    unittest.main()
