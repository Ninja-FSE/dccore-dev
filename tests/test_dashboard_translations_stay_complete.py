"""The dashboard's translation dictionaries stay in step with each other
and with what the page actually references.

WHAT THIS GUARDS AGAINST

Three JSON files (web/lang/en.json, fr.json, es.json) and two source files
(web/index.html's data-i18n attributes, web/app.js's views{} object) all
have to agree on the same set of keys - and nothing enforces that by
construction. Four ways that drifts, each silent until an operator notices:

  * A key added to one language and forgotten in another. t() falls back to
    English for a viewer in that language, which is a real user staring at
    the wrong language on part of their own dashboard - not a crash, so
    nothing else would catch it.
  * A key referenced in HTML or app.js that no dictionary defines at all.
    t() then falls back to the literal key string - "view.search.title"
    printed on screen instead of "Search" - which is loud when it happens
    but invisible until it does, since nothing runs the page's JavaScript.
  * A key left in en.json after nothing references it any more - dead
    weight nobody notices removing, and a growing set of keys that need
    translating for no reason.
  * The files stop being valid JSON. web/lang/*.json ship as data, not
    code, so nothing else parses them before the browser does.

Deliberately not covering the Console or the debug channel: neither is
translated (see docs/UPDATES.md and issue #69) - both show the same lines
the daemon's own log does, which is a separate, larger piece of work.
"""

import io
import json
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

WEB = os.path.join(REPO_ROOT, "web")
LANG_DIR = os.path.join(WEB, "lang")
LANGUAGES = ("en", "fr", "es")

DATA_I18N = re.compile(r'data-i18n(?:-placeholder)?="([^"]+)"')
VIEW_TITLE_OR_SUB = re.compile(r'(?:title|sub):\s*"([^"]+)"')


def read(name):
    with io.open(os.path.join(WEB, name), encoding="utf-8") as handle:
        return handle.read()


def load_dict(code):
    with io.open(os.path.join(LANG_DIR, code + ".json"), encoding="utf-8") as handle:
        return json.load(handle)


def keys_referenced_in_source():
    """Every translation key the dashboard's own source actually asks for -
    index.html's data-i18n(-placeholder) attributes, plus the views{}
    object in app.js, whose title/sub fields are keys rather than literal
    text (see the comment above that object)."""
    html = read("index.html")
    found = set(DATA_I18N.findall(html))

    js = read("app.js")
    block = js.split("var views = {", 1)[1].split("\n  };", 1)[0]
    found.update(VIEW_TITLE_OR_SUB.findall(block))

    return found


class TheDictionariesAreValidJSON(unittest.TestCase):

    def test_each_language_file_parses(self):
        for code in LANGUAGES:
            with self.subTest(language=code):
                load_dict(code)  # raises if this is not valid JSON


class TheDictionariesAgreeWithEachOther(unittest.TestCase):
    """Same keys everywhere, so a viewer in any of the three languages sees
    that language on every string this page currently translates - never a
    silent drop back to English for one language and not the others."""

    def test_no_language_is_missing_a_key_another_one_has(self):
        key_sets = {code: set(load_dict(code).keys()) for code in LANGUAGES}
        union = set().union(*key_sets.values())

        for code, keys in key_sets.items():
            missing = sorted(union - keys)
            with self.subTest(language=code):
                self.assertEqual(missing, [],
                                 f"{code}.json is missing keys the other "
                                 f"languages define: {missing}")

    def test_no_translated_value_is_blank(self):
        """A key present but empty reads as "translated to nothing" rather
        than "not translated yet" - the one case a missing-key fallback
        cannot catch, since the key IS there."""
        for code in LANGUAGES:
            values = load_dict(code)
            blank = sorted(k for k, v in values.items() if not str(v).strip())
            with self.subTest(language=code):
                self.assertEqual(blank, [],
                                 f"{code}.json has blank values for: {blank}")


class EveryKeyTheSourceUsesExists(unittest.TestCase):
    """The other direction: a key the page actually asks for has to be
    defined, or t() falls back to printing the raw key on screen."""

    def test_every_referenced_key_is_in_english(self):
        english = load_dict("en")
        referenced = keys_referenced_in_source()

        missing = sorted(referenced - set(english.keys()))
        self.assertEqual(missing, [],
                         "referenced in index.html or app.js but not in "
                         "web/lang/en.json: " + ", ".join(missing))

    def test_the_scan_actually_finds_keys(self):
        """Fixture invariant. A regex or a moved views{} block that stopped
        matching would make the test above pass by finding nothing to check,
        on any tree, forever."""
        self.assertGreater(len(keys_referenced_in_source()), 15)


class NothingInEnglishGoesUnused(unittest.TestCase):
    """The direction the other guards do not cover: a key nobody references
    is dead weight that still has to be kept translated in fr.json and
    es.json for no reason. Not a fault on its own, but worth knowing about
    before it accumulates."""

    def test_every_english_key_is_referenced_somewhere(self):
        english = load_dict("en")
        referenced = keys_referenced_in_source()

        orphaned = sorted(set(english.keys()) - referenced)
        self.assertEqual(orphaned, [],
                         "defined in web/lang/en.json but not referenced by "
                         "index.html's data-i18n attributes or app.js's "
                         "views{} object: " + ", ".join(orphaned))


if __name__ == "__main__":
    unittest.main()
