"""The launchers find a set-up install in conf/ as well as at the root (#959).

The daemon moves settings.conf and admin_config.py into conf/ the first time
it starts. Each launcher decides "first run, ask the setup questions" by
looking for those files before any Python runs - and looked at the root only,
so the SECOND start of an upgraded install, an unattended one after a reboot
included, went back to first-run setup. A unit test of the migration cannot
see this, since the check runs first; these read each launcher's own check,
and run the shell one where bash is available.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SHELL_LAUNCHERS = ("scripts/linux/start-dccore.sh", "scripts/linux/install-autostart.sh",
                   "scripts/macos/install-autostart.command")


def read(relative):
    with open(os.path.join(REPO_ROOT, relative), encoding="utf-8") as handle:
        return handle.read().replace("\r\n", "\n")


def first_run_condition(text):
    """The `if [ ! -f "admin_config.py" ] ...; then` line, continuations joined."""
    start = text.index('if [ ! -f "admin_config.py" ]')
    end = text.index("; then", start) + len("; then")
    return text[start:end].replace("\\\n", " ")


class EachLauncherLooksInConf(unittest.TestCase):

    def test_the_shell_launchers(self):
        for relative in SHELL_LAUNCHERS:
            with self.subTest(launcher=relative):
                condition = first_run_condition(read(relative))
                for wanted in ('! -f "settings.conf"', '! -f "admin_config.py"',
                               '! -f "conf/settings.conf"', '! -f "conf/admin_config.py"'):
                    self.assertIn(wanted, condition)

    def test_the_windows_launcher(self):
        lines = [line for line in read("scripts/windows/start-dccore.bat").split("\n")
                 if not line.lstrip().lower().startswith("rem")
                 and 'if not exist "settings.conf"' in line]
        self.assertEqual(len(lines), 3, "the local_config, configure and no-configure branches")
        for line in lines:
            with self.subTest(line=line):
                self.assertIn('if not exist "conf\\settings.conf"', line)
                self.assertIn('if not exist "conf\\admin_config.py"', line)

    def test_the_setup_check(self):
        text = read("scripts/setup_check.py")
        self.assertIn('admin_config_present = any(os.path.exists(os.path.join(REPO, *where, "admin_config.py"))\n'
                      '                               for where in ((), ("conf",)))', text)


@unittest.skipUnless(shutil.which("bash"), "bash is not installed here")
class TheShellCheckDecides(unittest.TestCase):

    def decides(self, *present):
        """What the launcher's own condition says in a tree holding `present`."""
        tree = tempfile.mkdtemp(prefix="dccore-launcher-")
        self.addCleanup(shutil.rmtree, tree, ignore_errors=True)
        for relative in present:
            os.makedirs(os.path.dirname(os.path.join(tree, relative)) or tree, exist_ok=True)
            open(os.path.join(tree, relative), "w").close()
        condition = first_run_condition(read("scripts/linux/start-dccore.sh"))
        script = f'cd "$1" || exit 2\n{condition} echo first-run; else echo set-up; fi\n'
        done = subprocess.run(["bash", "-c", script, "bash", tree], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def test_nothing_anywhere_is_a_first_run(self):
        self.assertEqual(self.decides(), "first-run")

    def test_moved_into_conf_is_set_up(self):
        self.assertEqual(self.decides("conf/settings.conf"), "set-up")
        self.assertEqual(self.decides("conf/admin_config.py"), "set-up")

    def test_not_yet_moved_is_set_up(self):
        self.assertEqual(self.decides("settings.conf"), "set-up")


if __name__ == "__main__":
    unittest.main()
