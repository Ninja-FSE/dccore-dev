"""#1418: the public tree is extracted by scripts/extract_public_tree.py.

docs/PUBLIC-REPO-WORKFLOW.md's steps 1-3 were done by hand at each release;
for v1.17.0 they were a throwaway shell script, and running the suite in
what it made caught fourteen failures (#1409). Checked here on a real
extraction of HEAD into a temp folder:

  * nothing internal and nothing dev-only is in it, and nothing else is
    missing from it;
  * docs/UPDATES.md is the public changelog, and the links to it are rewritten;
  * .gitattributes loses exactly the one line and keeps the one
    tests/exported_tree.py reads;
  * a step that does not find what it expects refuses and leaves no tree;
  * DEV_ONLY names every dev-only file there is, and no file that is not.

Dev-only itself: the script and this test are in DEV_ONLY, so the public
tree never runs this.
"""

import io
import os
import shutil
import subprocess
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
try:
    import extract_public_tree as extract  # noqa: E402
finally:
    sys.path.remove(os.path.join(REPO_ROOT, "scripts"))

from tests.support import temp_dir  # noqa: E402


def git_works():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def read(*parts):
    with io.open(os.path.join(*parts), encoding="utf-8") as handle:
        return handle.read()


def archive_names(rev="HEAD"):
    import tarfile
    out = subprocess.run(["git", "archive", "--format=tar", rev], cwd=REPO_ROOT,
                         capture_output=True, timeout=600).stdout
    with tarfile.open(fileobj=io.BytesIO(out)) as archive:
        return {member.name for member in archive.getmembers() if member.isfile()}


def tree_names(root):
    found = set()
    for folder, _dirs, files in os.walk(root):
        for name in files:
            found.add(os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/"))
    return found


class OneExtraction(unittest.TestCase):
    """One real extraction of HEAD, shared by the checks below."""

    @classmethod
    def setUpClass(cls):
        if not git_works():
            raise unittest.SkipTest("git cannot run here, so there is nothing to extract")
        cls.home = __import__("tempfile").mkdtemp(prefix="dccore-extract-")
        cls.out = extract.extract("HEAD", os.path.join(cls.home, "public"))
        cls.names = tree_names(cls.out)
        cls.archived = archive_names()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def test_no_dev_only_file_is_in_it(self):
        self.assertEqual(sorted(set(extract.DEV_ONLY) & self.names), [])

    def test_nothing_but_those_and_the_changelog_swap_is_missing(self):
        """The rest of the archive is all there: the strip took only its list."""
        expected = (self.archived - set(extract.DEV_ONLY) - {extract.PUBLIC_CHANGELOG}) | {extract.SHIPPED_CHANGELOG}
        self.assertEqual(self.names, expected)

    def test_nothing_internal_is_in_it(self):
        for name in ("docs/PUBLIC-REPO-WORKFLOW.md", "tests/forbidden_identifiers.py"):
            with self.subTest(name=name):
                self.assertNotIn(name, self.names)
        self.assertFalse(any(n.startswith(("docs/changes/", "site/")) for n in self.names))

    def test_its_changelog_is_the_public_one(self):
        self.assertNotIn(extract.PUBLIC_CHANGELOG, self.names)
        with io.open(os.path.join(REPO_ROOT, "docs", "UPDATES-PUBLIC.md"), encoding="utf-8") as handle:
            public = handle.read()
        self.assertEqual(read(self.out, "docs", "UPDATES.md"), public)

    def test_the_links_point_at_it(self):
        for name, here, there in extract.LINKS:
            with self.subTest(name=name):
                text = read(self.out, name)
                self.assertIn(there, text)
                self.assertNotIn("UPDATES-PUBLIC", text)

    def test_gitattributes_loses_one_line_and_keeps_the_signal(self):
        here = read(REPO_ROOT, ".gitattributes").splitlines()
        there = read(self.out, ".gitattributes").splitlines()
        self.assertEqual([line for line in here if line not in there], [extract.INTERNAL_CHANGELOG_RULE])
        self.assertIn("docs/PUBLIC-REPO-WORKFLOW.md    export-ignore", there)


class ItRefusesAndLeavesNothing(unittest.TestCase):

    def setUp(self):
        if not git_works():
            self.skipTest("git cannot run here, so there is nothing to extract")
        self.home = temp_dir(self, prefix="dccore-extract-refused-")
        self.out = os.path.join(self.home, "public")

    def refused(self, **patches):
        saved = {name: getattr(extract, name) for name in patches}
        self.addCleanup(lambda: [setattr(extract, n, v) for n, v in saved.items()])
        for name, value in patches.items():
            setattr(extract, name, value)
        with self.assertRaises(extract.Refused) as caught:
            extract.extract("HEAD", self.out)
        self.assertFalse(os.path.exists(self.out))
        self.assertFalse(os.path.exists(self.out + ".partial"))
        return str(caught.exception)

    def test_a_dev_only_file_that_is_not_there(self):
        said = self.refused(DEV_ONLY=extract.DEV_ONLY + ("scripts/no_such_tool.py",))
        self.assertIn("scripts/no_such_tool.py", said)

    def test_a_link_that_is_not_where_the_step_says(self):
        said = self.refused(LINKS=(("README.md", "no such link text", "x"),))
        self.assertIn("README.md", said)

    def test_a_gitattributes_line_that_is_not_there(self):
        said = self.refused(INTERNAL_CHANGELOG_RULE="docs/NO_SUCH.md    export-ignore")
        self.assertIn(".gitattributes", said)

    def test_a_tree_that_is_already_there(self):
        os.makedirs(self.out)
        with self.assertRaises(extract.Refused):
            extract.extract("HEAD", self.out)
        self.assertEqual(os.listdir(self.out), [])

    def test_the_command_says_refused(self):
        code = extract.main(["no-such-revision-1418", self.out])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.out))


class DevOnlyIsComplete(unittest.TestCase):

    def test_every_preflight_test_is_named(self):
        tests_dir = os.path.join(REPO_ROOT, "tests")
        preflight = {f"tests/{name}" for name in os.listdir(tests_dir)
                     if name.startswith(("test_preflight", "test_preflights")) and name.endswith(".py")}
        self.assertTrue(preflight, "control: there are preflight tests")
        self.assertEqual(sorted(preflight - set(extract.DEV_ONLY)), [])

    def test_every_name_in_it_is_a_file_here(self):
        missing = [name for name in extract.DEV_ONLY if not os.path.isfile(os.path.join(REPO_ROOT, name))]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
