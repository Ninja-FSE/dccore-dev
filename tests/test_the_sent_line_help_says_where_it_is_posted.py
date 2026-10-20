"""ANNOUNCE_TRANSFERS_EXCLUDE's help says where the "sent" line goes when
the list is empty (#1477).

It said "Leave it empty to post the line in every channel." The line is
only ever posted in the channel the file was asked for in, so "every
channel" read as all of them at once. Every place the operator reads that
help now says "Leave it empty to post it where the file was asked for.":
the dashboard's "?" (settings_help), settings.conf.sample (generated from
it and from defaults.py's comment), and the mIRC settings window, whose
three lines of help keep whole sentences only - so the sentence has to fit,
and is checked in what the window is given, not only in the source.

The texts are read through the code that serves them, not grepped.
"""

import io
import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (REPO_ROOT, os.path.join(REPO_ROOT, "src"), os.path.join(REPO_ROOT, "scripts"),
              os.path.join(REPO_ROOT, "scripts", "mirc"), os.path.join(REPO_ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import settings_help  # noqa: E402

KEY = "ANNOUNCE_TRANSFERS_EXCLUDE"
SENTENCE = "Leave it empty to post it where the file was asked for."
DEVELOPER = "Empty posts it where the file was asked for."
OLD = "every channel"


class TheDashboardsHelp(unittest.TestCase):

    def test_the_plain_help_says_where_it_is_posted(self):
        text = settings_help.help_text(KEY)
        self.assertIn(SENTENCE, text)
        self.assertNotIn(OLD, text)

    def test_the_developers_note_says_the_same(self):
        text = settings_help.developer_text(KEY)
        self.assertIn(DEVELOPER, text)
        self.assertNotIn(OLD, text)

    def test_the_translations_no_longer_say_every_channel(self):
        """fr and es carry their own sentence; the old one said "all the
        channels" in each."""
        old = {"fr": "dans tous les salons", "es": "en todos los canales"}
        for lang, phrase in old.items():
            with self.subTest(lang=lang):
                with io.open(os.path.join(REPO_ROOT, "web", "lang", lang + ".json"),
                             encoding="utf-8") as handle:
                    text = json.load(handle)["settings.field.%s.help" % KEY]
                self.assertNotIn(phrase, text)


class TheSample(unittest.TestCase):

    def entry(self):
        import gen_settings_sample
        sample = gen_settings_sample.build()
        end = sample.index("#%s = " % KEY)
        start = sample.rfind("\n\n", 0, end)       # the blank line before the entry
        self.assertGreater(start, 0, "no blank line before the entry")
        return " ".join(sample[start:end].replace("#", " ").split())

    def test_the_sample_says_where_it_is_posted(self):
        entry = self.entry()
        self.assertIn(SENTENCE, entry)
        self.assertIn(DEVELOPER, entry)
        self.assertNotIn(OLD, entry)


class TheMircWindow(unittest.TestCase):

    def test_the_three_lines_keep_the_sentence(self):
        """help_for() cuts to whole sentences within three lines: a longer
        sentence here would have been dropped from the window."""
        import build_settings_window as generator
        shown = generator.help_for(settings_help.help_text(KEY))
        self.assertIn(SENTENCE, shown)
        self.assertNotIn(OLD, shown)

    def test_the_generated_block_carries_it(self):
        import build_settings_window as generator
        from test_the_mirc_settings_window import untext
        block, _builder = generator.generate()
        rows = [line.strip().split(" ", 3)[3] for line in block
                if line.strip().startswith("hadd dccore.swm h.%s " % KEY)]
        self.assertEqual(len(rows), 1, rows)
        self.assertIn(SENTENCE, untext(rows[0]))

    def test_the_shipped_script_carries_it(self):
        """What mIRC loads: the block as it is in dccore.mrc."""
        from test_the_mirc_settings_window import generated_data, untext
        shown = untext(generated_data()["h." + KEY])
        self.assertIn(SENTENCE, shown)
        self.assertNotIn(OLD, shown)


if __name__ == "__main__":
    unittest.main()
