"""Splitting webserver.py (docs/FUTURE.md): what every module split out of it
keeps to.

Step 0 (#1311) moved the shared limits and helpers to web_common.py; each
later step moves one section (#1313: web_search.py, the search broadcast).
webserver imports every moved name back. Two promises keep that safe, for
every module in SPLIT_MODULES:

  * every name it defines is the same object on webserver, so
    `webserver.<name>` keeps working for every caller and test;
  * it imports neither webserver (a cycle) nor Flask (the build_*/apply_*
    layer runs without it).
"""

import ast
import io
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.support import parse_source  # noqa: E402

import importlib  # noqa: E402

import web_common  # noqa: E402
import webserver  # noqa: E402

# One entry per step of the split, in order.
SPLIT_MODULES = ("web_common", "web_search")


def module_tree(name):
    with io.open(os.path.join(REPO_ROOT, "src", name + ".py"), encoding="utf-8") as handle:
        return parse_source(handle.read())


def defined_names(name):
    names = []
    for node in module_tree(name).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [target.id for target in node.targets if isinstance(target, ast.Name)]
    return names


class WebserverStillAnswersForEveryName(unittest.TestCase):

    def test_there_is_something_to_check(self):
        for module in SPLIT_MODULES:
            with self.subTest(module=module):
                self.assertGreaterEqual(len(defined_names(module)), 4)

    def test_each_is_the_same_object_there(self):
        for module in SPLIT_MODULES:
            loaded = importlib.import_module(module)
            for name in defined_names(module):
                with self.subTest(module=module, name=name):
                    self.assertIs(getattr(webserver, name, None), getattr(loaded, name))


class ItStandsOnItsOwn(unittest.TestCase):

    def imported(self, module):
        found = set()
        for node in ast.walk(module_tree(module)):
            if isinstance(node, ast.Import):
                found |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                found.add(str(node.module).split(".")[0])
        return found

    def test_none_imports_webserver_or_flask(self):
        for module in SPLIT_MODULES:
            with self.subTest(module=module):
                self.assertFalse({"webserver", "flask"} & self.imported(module), self.imported(module))

    def test_the_web_folder_is_still_the_repositorys(self):
        """web/ is a sibling of src/, and web_common is in src/ as webserver is."""
        self.assertEqual(web_common.WEB_DIR, os.path.join(REPO_ROOT, "web"))
        self.assertTrue(os.path.isfile(os.path.join(web_common.WEB_DIR, "index.html")))


if __name__ == "__main__":
    unittest.main()
