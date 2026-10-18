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
  * DEV_ONLY names every dev-only file there is, and no file that is not;
  * the launchers git carries as 100755 are named, executable in the tree
    on POSIX, and printed last as the `git update-index --chmod=+x` line
    step 4 runs (#1441).

Dev-only itself: the script and this test are in DEV_ONLY, so the public
tree never runs this.
"""

import contextlib
import glob
import io
import os
import shlex
import shutil
import stat
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
        # #1441: every call that sets the executable bit is recorded, and on
        # POSIX the archive comes out with no bit at all - what NTFS does -
        # so a launcher executable in the tree is one the extraction made so.
        # Every git command it runs is recorded too, as it ran.
        cls.marked, cls.git_calls = [], []
        archive, keep, real_subprocess = extract._archive, extract._keep_the_executable_bit, extract.subprocess

        def run(argv, **kwargs):
            cls.git_calls.append(list(argv))
            return subprocess.run(argv, **kwargs)

        def archive_without_the_bit(rev, into, repo):
            archive(rev, into, repo)
            if os.name != "nt":
                for name in tree_names(into):
                    target = os.path.join(into, name)
                    os.chmod(target, stat.S_IMODE(os.stat(target).st_mode) & ~0o111)

        def recorded(tree, paths):
            cls.marked.append((tree, list(paths)))
            keep(tree, paths)

        extract._archive, extract._keep_the_executable_bit = archive_without_the_bit, recorded
        extract.subprocess = _Over(subprocess, run=run)
        try:
            cls.out, cls.paths, cls.sha = extract.extract("HEAD", os.path.join(cls.home, "public"))
        except BaseException:  # no tearDownClass after a failed setUpClass
            shutil.rmtree(cls.home, ignore_errors=True)
            raise
        finally:
            extract._archive, extract._keep_the_executable_bit = archive, keep
            extract.subprocess = real_subprocess
        cls.names = tree_names(cls.out)
        cls.archived = archive_names()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def test_no_dev_only_file_is_in_it(self):
        self.assertEqual(sorted(set(extract.DEV_ONLY) & self.names), [])

    def test_it_returns_the_commit_it_read(self):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, timeout=60).stdout.strip()
        self.assertEqual(self.sha, head)

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

    def launchers(self):
        found = sorted(os.path.relpath(path, self.out).replace(os.sep, "/")
                       for pattern in (("scripts", "linux", "*.sh"), ("scripts", "macos", "*.command"))
                       for path in glob.glob(os.path.join(self.out, *pattern)))
        self.assertTrue(found, "control: the tree has launchers")
        return found

    def test_its_executables_are_the_launchers(self):
        """#1441: read from git, so the same on a filesystem with no bit.
        Forward slashes, sorted: they go on a git command line as they are."""
        paths = extract.executables("HEAD", self.out)
        self.assertEqual(paths, self.launchers())
        self.assertFalse([p for p in paths if "\\" in p], paths)

    def test_the_extraction_set_the_bit_on_exactly_those(self):
        """Once, on the scratch tree that becomes <out>, with the launchers."""
        self.assertEqual(self.marked, [(self.out + ".partial", self.launchers())])

    def test_it_returns_the_paths_it_marked(self):
        """What main() prints is what the extraction set the bit on."""
        self.assertEqual(self.marked, [(self.out + ".partial", self.paths)])

    def test_archive_and_ls_tree_read_one_commit(self):
        """<rev> is resolved once, to a sha, and both read that sha: a fetch
        in between cannot pair one commit's tree with another's modes."""
        commands = [argv[1] for argv in self.git_calls]
        self.assertEqual(commands, ["rev-parse", "archive", "ls-tree"])
        archived, listed = self.git_calls[1][-1], self.git_calls[2][-1]
        self.assertRegex(archived, r"\A[0-9a-f]{40}([0-9a-f]{24})?\Z")
        self.assertEqual(listed, archived)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, timeout=60).stdout.strip()
        self.assertEqual(archived, head)

    def test_the_launchers_are_executable_in_it(self):
        if os.name == "nt":
            self.skipTest("NTFS has no executable bit; the printed update-index line carries it"
                          " (test_the_extraction_set_the_bit_on_exactly_those runs here)")
        for name in self.launchers():
            with self.subTest(name=name):
                self.assertTrue(os.access(os.path.join(self.out, name), os.X_OK))
        self.assertFalse(os.access(os.path.join(self.out, "README.md"), os.X_OK),
                         "control: the archive came out with no bit")

    def test_the_command_prints_the_line_that_marks_them_last(self):
        """main() on what this extraction returned: the real line."""
        saved = extract.extract
        self.addCleanup(setattr, extract, "extract", saved)
        extract.extract = lambda rev, out: (self.out, self.paths, self.sha)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = extract.main(["HEAD", os.path.join(self.home, "unused")])
        self.assertEqual(code, 0)
        last = printed.getvalue().splitlines()[-1].strip()
        self.assertEqual(last, "git update-index --chmod=+x -- " + " ".join(self.launchers()))


class _Over:
    """A module with some names replaced: everything else is the real one."""

    def __init__(self, real, **replaced):
        self._real = real
        self.__dict__.update(replaced)

    def __getattr__(self, name):
        return getattr(self._real, name)


def ls_tree(*entries):
    """`git ls-tree -r -z` output: mode, type, sha, a tab, the path, a NUL."""
    return "".join(f"{mode} {kind} {'0' * 40}\t{path}\0" for mode, kind, path in entries).encode("utf-8")


class ItNamesTheExecutables(unittest.TestCase):
    """executables() on a faked `git ls-tree`: what it keeps, what it drops."""

    def setUp(self):
        self.tree = temp_dir(self, prefix="dccore-extract-modes-")
        self.calls = []

    def files(self, *names):
        for name in names:
            path = os.path.join(self.tree, *name.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write("placeholder\n")

    def git(self, stdout=b"", returncode=0, stderr=b"", raises=None):
        def run(argv, **kwargs):
            self.calls.append((argv, kwargs.get("cwd")))
            if raises is not None:
                raise raises
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)
        self.addCleanup(setattr, extract, "subprocess", extract.subprocess)
        extract.subprocess = _Over(subprocess, run=run)

    def test_it_asks_git_for_that_revision(self):
        self.git(ls_tree())
        self.assertEqual(extract.executables("placeholder-rev", self.tree, "placeholder-repo"), [])
        self.assertEqual(self.calls, [(["git", "ls-tree", "-r", "-z", "placeholder-rev"], "placeholder-repo")])

    def test_only_mode_100755_counts(self):
        """100644, a symlink (120000) and a gitlink (160000) are not
        executables, even with a file of that name in the tree."""
        self.files("bin/run.sh", "README.md", "bin/link.sh", "vendor")
        self.git(ls_tree(("100644", "blob", "README.md"), ("100755", "blob", "bin/run.sh"),
                         ("120000", "blob", "bin/link.sh"), ("160000", "commit", "vendor")))
        self.assertEqual(extract.executables("HEAD", self.tree), ["bin/run.sh"])

    def test_one_not_in_the_tree_is_dropped(self):
        """DEV_ONLY and export-ignored files are in the revision, not the tree."""
        self.files("bin/run.sh")
        self.git(ls_tree(("100755", "blob", "bin/gone.sh"), ("100755", "blob", "bin/run.sh")))
        self.assertEqual(extract.executables("HEAD", self.tree), ["bin/run.sh"])

    def test_a_folder_of_that_name_is_not_a_file(self):
        os.makedirs(os.path.join(self.tree, "bin", "tool.sh"))
        self.git(ls_tree(("100755", "blob", "bin/tool.sh")))
        self.assertEqual(extract.executables("HEAD", self.tree), [])

    def test_they_come_back_sorted_with_forward_slashes(self):
        names = ("scripts/z/last.sh", "Tools/first-one.sh", "scripts/a/mid.command")
        self.files(*names)
        self.git(ls_tree(*[("100755", "blob", name) for name in names]))
        self.assertEqual(extract.executables("HEAD", self.tree), sorted(names))

    UNPORTABLE = ("bin/run me.sh", "bin/it's.sh", "bin/$HOME.sh", "bin/semi;colon.sh", "bin/café.sh",
                  "bin/δοκιμή.sh", 'bin/say"hi.sh', "bin/amp&er.sh")

    def test_an_executable_a_shell_would_read_otherwise_refuses(self):
        """Step 4's line names it unquoted, on cmd, PowerShell and sh alike,
        so a name any of them would read as more than text refuses - and the
        refusal names it, ascii() so an unprintable one is still legible."""
        for name in self.UNPORTABLE:
            with self.subTest(name=ascii(name)):
                try:
                    self.files(name)
                except OSError:  # `"` cannot be in a Windows name; the next test covers it absent
                    pass
                self.git(ls_tree(("100755", "blob", "bin/fine.sh"), ("100755", "blob", name)))
                with self.assertRaises(extract.Refused) as caught:
                    extract.executables("HEAD", self.tree)
                self.assertIn(ascii(name), str(caught.exception))

    def test_it_refuses_one_that_is_not_in_the_tree_too(self):
        """A non-ASCII name may not round-trip to the file on Windows, so
        the in-the-tree filter would drop it without a word."""
        for name in self.UNPORTABLE:
            with self.subTest(name=ascii(name)):
                self.git(ls_tree(("100755", "blob", name)))
                with self.assertRaises(extract.Refused) as caught:
                    extract.executables("HEAD", self.tree)
                self.assertIn(ascii(name), str(caught.exception))

    def test_a_file_that_is_not_executable_may_be_called_anything(self):
        self.files("my notes é.md", "bin/run.sh")
        self.git(ls_tree(("100644", "blob", "my notes é.md"), ("100755", "blob", "bin/run.sh")))
        self.assertEqual(extract.executables("HEAD", self.tree), ["bin/run.sh"])

    def test_dots_and_an_inner_dash_are_portable(self):
        names = ["scripts/.hidden/run.v2.sh", "scripts/-n.sh"]
        self.files(*names)
        self.git(ls_tree(*[("100755", "blob", name) for name in names]))
        self.assertEqual(extract.executables("HEAD", self.tree), sorted(names))

    def test_a_leading_dash_refuses(self):
        """Windows PowerShell 5.1 splits a bare `-n.sh` at the dot before git
        sees it, so step 4's line could not name it there, `--` or not."""
        self.files("-n.sh")
        self.git(ls_tree(("100755", "blob", "-n.sh")))
        with self.assertRaises(extract.Refused) as caught:
            extract.executables("HEAD", self.tree)
        self.assertIn(ascii("-n.sh"), str(caught.exception))

    def test_a_failing_ls_tree_refuses(self):
        self.git(returncode=128, stderr=b"fatal: not a valid object name placeholder-rev")
        with self.assertRaises(extract.Refused) as caught:
            extract.executables("placeholder-rev", self.tree)
        self.assertIn("ls-tree", str(caught.exception))
        self.assertIn("not a valid object name", str(caught.exception))

    def test_git_that_cannot_run_refuses(self):
        for error in (OSError("placeholder: no git"), subprocess.TimeoutExpired(["git"], 600)):
            with self.subTest(error=type(error).__name__):
                self.git(raises=error)
                with self.assertRaises(extract.Refused) as caught:
                    extract.executables("HEAD", self.tree)
                self.assertIn("ls-tree", str(caught.exception))

    def test_a_revision_git_does_not_know_refuses(self):
        if not git_works():
            self.skipTest("git cannot run here")
        with self.assertRaises(extract.Refused):
            extract.executables("no-such-revision-1441", self.tree)


class ItResolvesTheRevisionFirst(unittest.TestCase):
    """<rev> becomes one commit sha before anything is written (#1441)."""

    SHA = "0123456789abcdef0123456789abcdef01234567"

    def setUp(self):
        self.out = os.path.join(temp_dir(self, prefix="dccore-extract-rev-"), "public")
        self.calls, self.made = [], []
        self.addCleanup(setattr, extract, "os", extract.os)
        extract.os = _Over(os, makedirs=lambda *a, **k: (self.made.append(a), os.makedirs(*a, **k)))

    def git(self, stdout=b"", returncode=0, raises=None):
        def run(argv, **kwargs):
            self.calls.append((list(argv), kwargs.get("timeout")))
            if argv[1] != "rev-parse":
                raise AssertionError(f"{argv[1]} ran before <rev> was a commit")
            if raises is not None:
                raise raises
            return subprocess.CompletedProcess(argv, returncode, stdout, b"")
        self.addCleanup(setattr, extract, "subprocess", extract.subprocess)
        extract.subprocess = _Over(subprocess, run=run)

    def refused(self, rev="placeholder-rev"):
        with self.assertRaises(extract.Refused) as caught:
            extract.extract(rev, self.out)
        self.assertEqual(self.made, [], "a scratch folder was made for a revision that is not a commit")
        self.assertFalse(os.path.exists(self.out))
        self.assertFalse(os.path.exists(self.out + ".partial"))
        return str(caught.exception)

    def test_it_asks_for_a_commit_with_a_timeout(self):
        self.git(self.SHA.encode("ascii") + b"\n")
        self.assertEqual(extract._commit("placeholder-rev", "placeholder-repo"), self.SHA)
        (argv, timeout), = self.calls
        self.assertEqual(argv, ["git", "rev-parse", "--verify", "--quiet", "placeholder-rev^{commit}"])
        self.assertIsNotNone(timeout)

    def test_a_revision_that_is_not_a_commit_refuses_before_any_folder(self):
        self.git(returncode=1)
        self.assertIn("placeholder-rev", self.refused())
        self.assertEqual(len(self.calls), 1)

    def test_an_answer_with_no_sha_refuses(self):
        self.git(b"\n", returncode=0)
        self.assertIn("placeholder-rev", self.refused())

    def test_git_that_cannot_run_refuses(self):
        for error in (OSError("placeholder: no git"), subprocess.TimeoutExpired(["git"], 60)):
            with self.subTest(error=type(error).__name__):
                self.git(raises=error)
                self.assertIn("rev-parse", self.refused())

    def test_a_blob_is_not_a_commit(self):
        """Real git: `HEAD:README.md` names a file, and refuses as one."""
        if not git_works():
            self.skipTest("git cannot run here")
        self.refused("HEAD:README.md")


class ItSetsTheBit(unittest.TestCase):
    """_keep_the_executable_bit(): +x wherever +r on POSIX, nothing on nt."""

    def fake_os(self, name, mode):
        calls = []
        fake = _Over(os, name=name, stat=lambda path: os.stat_result((mode,) + (0,) * 9),
                     chmod=lambda path, new: calls.append((path, new)))
        self.addCleanup(setattr, extract, "os", extract.os)
        extract.os = fake
        return calls

    def test_posix_marks_each_path(self):
        calls = self.fake_os("posix", 0o100644)
        extract._keep_the_executable_bit("placeholder-tree", ["bin/a.sh", "b.command"])
        self.assertEqual(calls, [(os.path.join("placeholder-tree", "bin/a.sh"), 0o100755),
                                 (os.path.join("placeholder-tree", "b.command"), 0o100755)])

    def test_x_follows_r(self):
        """Owner-only readable stays owner-only: no bit for whoever cannot read."""
        calls = self.fake_os("posix", 0o100600)
        extract._keep_the_executable_bit("placeholder-tree", ["a.sh"])
        self.assertEqual(calls, [(os.path.join("placeholder-tree", "a.sh"), 0o100700)])

    def test_windows_touches_nothing(self):
        calls = self.fake_os("nt", 0o100644)
        extract._keep_the_executable_bit("placeholder-tree", ["bin/a.sh"])
        self.assertEqual(calls, [])

    def test_a_real_file_becomes_executable(self):
        if os.name == "nt":
            self.skipTest("NTFS has no executable bit to set (test_posix_marks_each_path runs here)")
        tree = temp_dir(self, prefix="dccore-extract-bit-")
        path = os.path.join(tree, "run.sh")
        with open(path, "w") as handle:
            handle.write("placeholder\n")
        os.chmod(path, 0o644)
        extract._keep_the_executable_bit(tree, ["run.sh"])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o755)


class ItPrintsTheLineThatMarksThem(unittest.TestCase):

    LAUNCHERS = ["scripts/linux/start-dccore.sh", "scripts/macos/start-dccore.command"]

    def test_the_line(self):
        self.assertEqual(extract.mark_executable_command(self.LAUNCHERS),
                         "git update-index --chmod=+x -- scripts/linux/start-dccore.sh"
                         " scripts/macos/start-dccore.command")

    def test_a_leading_dash_stays_a_path(self):
        """`--` before the paths: git would read `-n.sh` as an option."""
        line = extract.mark_executable_command(["-n.sh", "scripts/.hidden/run.v2.sh"])
        self.assertEqual(shlex.split(line),
                         ["git", "update-index", "--chmod=+x", "--", "-n.sh", "scripts/.hidden/run.v2.sh"])

    def main(self, *flags, paths=None, suite_code=0, refuse=None):
        """main() with the extraction and the suite faked. Any further git
        question from main() is an error: extract() already answered it."""
        out = os.path.join(temp_dir(self, prefix="dccore-extract-main-"), "public")
        ran = []

        def fake_extract(rev, out):
            if refuse:
                raise extract.Refused(refuse)
            return out, list(self.LAUNCHERS if paths is None else paths), self.SHA

        def asked_again(*args, **kwargs):
            raise AssertionError("main() asked git for the executables again")

        def fake_suite(tree, processes=2):
            ran.append(tree)
            print("placeholder suite output")
            return suite_code

        for name, value in (("extract", fake_extract), ("executables", asked_again),
                            ("run_the_suite", fake_suite)):
            self.addCleanup(setattr, extract, name, getattr(extract, name))
            setattr(extract, name, value)
        printed, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
            code = extract.main(["placeholder-rev", out] + list(flags))
        return code, printed.getvalue().splitlines(), errors.getvalue(), ran

    SHA = "0123456789abcdef0123456789abcdef01234567"

    def test_it_names_the_commit_it_read(self):
        """The resolved commit, not only the revision asked for: that is what
        #1442's mIRC zip is compared against."""
        _code, lines, _err, _ran = self.main()
        self.assertTrue(any("placeholder-rev (" + self.SHA + ") extracted into" in line
                            for line in lines), lines)

    def test_without_test_it_is_the_last_line(self):
        code, lines, _err, ran = self.main()
        self.assertEqual((code, ran), (0, []))
        self.assertEqual(lines[-1].strip(), extract.mark_executable_command(self.LAUNCHERS))

    def test_with_test_it_comes_after_the_suite(self):
        """So a long suite run does not scroll it away."""
        code, lines, _err, ran = self.main("--test")
        self.assertEqual((code, len(ran)), (0, 1))
        self.assertIn("placeholder suite output", lines)
        self.assertLess(lines.index("placeholder suite output"), len(lines) - 1)
        self.assertEqual(lines[-1].strip(), extract.mark_executable_command(self.LAUNCHERS))

    def test_with_test_it_returns_the_suites_code(self):
        code, lines, _err, _ran = self.main("--test", suite_code=3)
        self.assertEqual(code, 3)
        self.assertEqual(lines[-1].strip(), extract.mark_executable_command(self.LAUNCHERS))

    def test_no_executables_says_so(self):
        code, lines, _err, _ran = self.main(paths=[])
        self.assertEqual(code, 0)
        self.assertIn("has no executable files to mark", lines[-1])
        self.assertFalse([line for line in lines if "update-index" in line], lines)

    def test_a_refused_extraction_runs_no_suite_and_marks_nothing(self):
        code, lines, err, ran = self.main("--test", refuse="placeholder refusal")
        self.assertEqual((code, ran), (2, []))
        self.assertIn("refused - placeholder refusal", err)
        self.assertFalse([line for line in lines if "update-index" in line], lines)


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

    def test_a_failing_ls_tree_leaves_nothing(self):
        """#1441: reading the modes is inside the all-or-nothing."""
        real = subprocess.run

        def run(argv, **kwargs):
            if argv[:2] == ["git", "ls-tree"]:
                return subprocess.CompletedProcess(argv, 128, b"", b"fatal: placeholder")
            return real(argv, **kwargs)

        said = self.refused(subprocess=_Over(subprocess, run=run))
        self.assertIn("ls-tree", said)

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
