"""#1311: web_common.py, step 0 of splitting webserver.py.

The limits and helpers every section of webserver.py used moved here, and
webserver imports each one back by name. Two promises keep that safe:

  * every name web_common defines is the same object on webserver, so
    `webserver.<name>` keeps working for every caller and test;
  * web_common imports neither webserver (the next steps move sections out
    of webserver that import web_common - a cycle would start there) nor
    Flask (the build_*/apply_* layer runs without it).
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

import web_common  # noqa: E402
import webserver  # noqa: E402


def module_tree():
    with io.open(os.path.join(REPO_ROOT, "src", "web_common.py"), encoding="utf-8") as handle:
        return parse_source(handle.read())


def defined_names():
    names = []
    for node in module_tree().body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [target.id for target in node.targets if isinstance(target, ast.Name)]
    return names


class WebserverStillAnswersForEveryName(unittest.TestCase):

    def test_there_is_something_to_check(self):
        self.assertGreaterEqual(len(defined_names()), 14)

    def test_each_is_the_same_object_there(self):
        for name in defined_names():
            with self.subTest(name=name):
                self.assertIs(getattr(webserver, name, None), getattr(web_common, name))


class ItStandsOnItsOwn(unittest.TestCase):

    def imported(self):
        found = set()
        for node in ast.walk(module_tree()):
            if isinstance(node, ast.Import):
                found |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                found.add(str(node.module).split(".")[0])
        return found

    def test_it_does_not_import_webserver_or_flask(self):
        self.assertFalse({"webserver", "flask"} & self.imported(), self.imported())

    def test_the_web_folder_is_still_the_repositorys(self):
        """web/ is a sibling of src/, and web_common is in src/ as webserver is."""
        self.assertEqual(web_common.WEB_DIR, os.path.join(REPO_ROOT, "web"))
        self.assertTrue(os.path.isfile(os.path.join(web_common.WEB_DIR, "index.html")))


if __name__ == "__main__":
    unittest.main()
