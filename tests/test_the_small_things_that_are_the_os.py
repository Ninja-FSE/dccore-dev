"""#547, Proposal 6: the small things that are the OS, not DCCore.

Four of them: the host firewall (Windows asks once and "Cancel" means every
send times out with no hint why), starting with the system, the console
window that is the bot, and port forwarding. The first two get scripts,
each with a twin that undoes it; the third was already said by the
launchers (#551) and is only pinned here; the fourth is three lines of
docs.

Executed, not grepped, with the OS commands faked on PATH ahead of the
real ones - schtasks, net, netsh, powershell, systemctl, launchctl - each
fake appending its arguments to a file. So nothing here creates a real
task, rule, unit or agent on the machine running the tests, and what is
asserted is exactly what the script would have asked the OS to do. The
Windows scripts run under cmd.exe (skipped elsewhere); the Linux and macOS
ones under whatever POSIX shell is on PATH, which on a Windows box is Git
Bash, so all three families run on all three CI runners where a shell
exists. The plist is parsed back with plistlib, the unit with configparser.
"""

import configparser
import io
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
SCRIPTS = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import setup_check  # noqa: E402

WIN = os.path.join(SCRIPTS, "windows")
LIN = os.path.join(SCRIPTS, "linux")
MAC = os.path.join(SCRIPTS, "macos")
PORTS = "import sys\nprint('55000 55010 8420 1' if '--web' in sys.argv else '55000 55010 8420 0')\n"


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


class ThePortsLine(unittest.TestCase):
    """One place knows the ports; scripts/ports.py only prints it."""

    def test_the_line_from_a_config(self):
        class C:
            DCC_PORT_START = 60000
            DCC_PORT_END = 60005
            WEBUI_PORT = 9000
            WEBUI_ENABLED = True
        self.assertEqual(setup_check.ports_line(C), "60000 60005 9000 1")

    def test_defaults_when_a_setting_is_absent(self):
        self.assertEqual(setup_check.ports_line(object()), "55000 55010 8420 0")

    def test_ports_py_prints_it_from_the_real_settings(self):
        done = subprocess.run([sys.executable, os.path.join(SCRIPTS, "ports.py")],
                              capture_output=True, text=True, timeout=60, cwd=REPO_ROOT)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stdout.strip(), r"^\d+ \d+ \d+ [01]$")


class TheFirewallHint(unittest.TestCase):
    def test_both_platforms_have_one_with_the_range_filled_in(self):
        for platform in (setup_check.LINUX, setup_check.WINDOWS):
            text = platform.firewall_hint.format(start=55000, end=55010)
            self.assertIn("55000", text)
            self.assertIn("55010", text)
            self.assertNotIn("{", text)

    def test_windows_names_the_script_and_linux_names_the_commands(self):
        self.assertIn("allow-firewall.bat", setup_check.WINDOWS.firewall_hint)
        self.assertIn("ufw allow", setup_check.LINUX.firewall_hint)
        self.assertIn("firewall-cmd", setup_check.LINUX.firewall_hint)

    def test_it_is_printed_after_the_port_check(self):
        source = read(os.path.join(SCRIPTS, "setup_check.py"))
        ports = source.index('ok(f"all {free} ports free')
        self.assertIn("platform.firewall_hint.format(start=start, end=end)", source[ports:ports + 600])


class TheConsoleWindowIsTheBot(unittest.TestCase):
    """Pinned by #551's tests too; here because it is the third small thing."""

    def test_both_launchers_say_closing_it_stops_the_bot(self):
        self.assertIn("Closing this window stops the bot", read(os.path.join(WIN, "start-dccore.bat")))
        self.assertIn("Closing this terminal stops the bot", read(os.path.join(LIN, "start-dccore.sh")))


class TheFilesShip(unittest.TestCase):
    def test_every_helper_has_its_twin(self):
        for a, b in (("allow-firewall.bat", "remove-firewall.bat"),
                     ("install-autostart.bat", "remove-autostart.bat")):
            self.assertTrue(os.path.isfile(os.path.join(WIN, a)), a)
            self.assertTrue(os.path.isfile(os.path.join(WIN, b)), b)
        self.assertTrue(os.path.isfile(os.path.join(LIN, "install-autostart.sh")))
        self.assertTrue(os.path.isfile(os.path.join(LIN, "remove-autostart.sh")))
        self.assertTrue(os.path.isfile(os.path.join(MAC, "install-autostart.command")))
        self.assertTrue(os.path.isfile(os.path.join(MAC, "remove-autostart.command")))

    def test_the_posix_ones_are_executable_in_git(self):
        """Finder and a shell need the bit; git carries it as mode 100755."""
        if not shutil.which("git"):
            if os.name == "nt":
                raise unittest.SkipTest("no git to read the index mode with")
            for path in (os.path.join(LIN, "install-autostart.sh"), os.path.join(LIN, "remove-autostart.sh"),
                         os.path.join(MAC, "install-autostart.command"), os.path.join(MAC, "remove-autostart.command")):
                self.assertTrue(os.access(path, os.X_OK), path)
            return
        done = subprocess.run(["git", "ls-files", "-s", "scripts/linux", "scripts/macos"],
                              capture_output=True, text=True, cwd=REPO_ROOT, timeout=30)
        if done.returncode != 0:
            raise unittest.SkipTest("not a git checkout")
        modes = {line.split("\t")[1]: line.split()[0] for line in done.stdout.splitlines()}
        for rel in ("scripts/linux/install-autostart.sh", "scripts/linux/remove-autostart.sh",
                    "scripts/macos/install-autostart.command", "scripts/macos/remove-autostart.command"):
            self.assertEqual(modes.get(rel), "100755", rel)

    def test_the_bats_are_crlf_and_call_their_os_commands(self):
        """`call`, so a wrapper on PATH returns instead of taking over - the
        fakes below depend on it, and so would any operator's shim."""
        for name in ("allow-firewall.bat", "remove-firewall.bat", "install-autostart.bat", "remove-autostart.bat"):
            with io.open(os.path.join(WIN, name), encoding="utf-8", newline="") as handle:
                text = handle.read()
            self.assertIn("\r\n", text, name)
            self.assertNotIn("\n\n\n", text.replace("\r\n", "\n") + "x", name)  # no bare LF lines
            for cmd in ("schtasks", "netsh", "net session", "powershell"):
                if cmd in text:
                    self.assertIn("call " + cmd, text, f"{name}: {cmd} without call")

    def test_the_autostart_scripts_run_the_launcher_not_oserve(self):
        """The launcher is what puts the working directory right."""
        self.assertIn("start-dccore.bat", read(os.path.join(WIN, "install-autostart.bat")))
        self.assertIn("scripts/linux/start-dccore.sh", read(os.path.join(LIN, "install-autostart.sh")))
        self.assertIn("scripts/linux/start-dccore.sh", read(os.path.join(MAC, "install-autostart.command")))
        for path in (os.path.join(WIN, "install-autostart.bat"), os.path.join(LIN, "install-autostart.sh"),
                     os.path.join(MAC, "install-autostart.command")):
            self.assertNotIn("oserve.py", read(path).replace("not oserve.py directly", ""), path)


class _Tree:
    """A throwaway copy of scripts/ with fakes first on PATH."""

    def make_tree(self, configured=True):
        self.root = tempfile.mkdtemp(prefix="dccore-os-things-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for sub in ("windows", "linux", "macos"):
            os.makedirs(os.path.join(self.root, "scripts", sub))
            for name in os.listdir(os.path.join(SCRIPTS, sub)):
                src = os.path.join(SCRIPTS, sub, name)
                if os.path.isfile(src):
                    shutil.copy(src, os.path.join(self.root, "scripts", sub, name))
                    os.chmod(os.path.join(self.root, "scripts", sub, name), 0o755)
        with io.open(os.path.join(self.root, "scripts", "ports.py"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(PORTS)
        if configured:
            with io.open(os.path.join(self.root, "settings.conf"), "w", encoding="utf-8") as handle:
                handle.write("NICKNAME = X\n")
        self.fakebin = os.path.join(self.root, "fakebin")
        os.makedirs(self.fakebin)
        self.calls = os.path.join(self.root, "calls.txt")
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home)

    def calls_made(self):
        if not os.path.exists(self.calls):
            return []
        with io.open(self.calls, encoding="utf-8", errors="replace") as handle:
            return [line.strip() for line in handle if line.strip()]


@unittest.skipUnless(os.name == "nt" and (shutil.which("cmd.exe") or shutil.which("cmd")),
                     "cmd.exe is only available on Windows")
class TheWindowsHelpers(_Tree, unittest.TestCase):
    def setUp(self):
        self.make_tree()

    def fake(self, name, body):
        with io.open(os.path.join(self.fakebin, name + ".bat"), "w", encoding="ascii", newline="") as handle:
            handle.write("@echo off\r\n" + body.replace("\n", "\r\n"))

    def recorder(self, name, rc=0):
        self.fake(name, f'echo {name} %*>> "{self.calls}"\r\nexit /b {rc}\r\n')

    def run_bat(self, name, with_python=True):
        system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        env = dict(os.environ)
        path = [self.fakebin]
        if with_python:
            path.append(os.path.dirname(sys.executable))
        path.append(system32)
        env.update({"PATH": os.pathsep.join(path), "TEMP": self.root, "TMP": self.root,
                    "LOCALAPPDATA": self.home, "ProgramFiles": self.home})
        cmd = shutil.which("cmd.exe") or shutil.which("cmd")
        with io.open(os.devnull) as devnull:
            done = subprocess.run([cmd, "/c", os.path.join("scripts", "windows", name)], cwd=self.root,
                                  stdin=devnull, capture_output=True, text=True, errors="replace",
                                  timeout=120, env=env)
        return done.returncode, done.stdout + done.stderr

    # --- firewall ------------------------------------------------------

    def test_allow_adds_the_dcc_range_and_no_dashboard_rule_when_it_is_off(self):
        self.recorder("net")          # elevated
        self.recorder("netsh")
        rc, out = self.run_bat("allow-firewall.bat")
        self.assertEqual(rc, 0, out)
        calls = self.calls_made()
        adds = [c for c in calls if "add rule" in c]
        self.assertEqual(len(adds), 1, calls)
        self.assertIn('name="DCCore DCC sends" dir=in action=allow protocol=TCP localport=55000-55010', adds[0])
        self.assertIn("The dashboard is off", out)
        self.assertNotIn("DCCore dashboard\" dir=in", " ".join(adds))

    def test_allow_adds_the_dashboard_rule_when_it_is_on(self):
        with io.open(os.path.join(self.root, "scripts", "ports.py"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("print('55000 55010 8420 1')\n")
        self.recorder("net")
        self.recorder("netsh")
        rc, out = self.run_bat("allow-firewall.bat")
        self.assertEqual(rc, 0, out)
        adds = [c for c in self.calls_made() if "add rule" in c]
        self.assertEqual(len(adds), 2)
        self.assertIn('name="DCCore dashboard" dir=in action=allow protocol=TCP localport=8420', adds[1])

    def test_allow_deletes_before_adding_so_twice_is_once(self):
        self.recorder("net")
        self.recorder("netsh")
        self.run_bat("allow-firewall.bat")
        calls = self.calls_made()
        first_delete = next(i for i, c in enumerate(calls) if "delete rule" in c and "DCC sends" in c)
        first_add = next(i for i, c in enumerate(calls) if "add rule" in c)
        self.assertLess(first_delete, first_add)

    def test_allow_not_elevated_asks_windows_and_does_not_touch_netsh(self):
        self.recorder("net", rc=2)    # `net session` fails: not an administrator
        self.recorder("powershell")
        self.recorder("netsh")
        rc, out = self.run_bat("allow-firewall.bat")
        calls = self.calls_made()
        self.assertTrue(any("powershell" in c and "-Verb RunAs" in c and "allow-firewall.bat" in c for c in calls), calls)
        self.assertFalse(any(c.startswith("netsh") for c in calls), calls)
        self.assertIn("administrator", out)

    def test_allow_reads_the_ports_from_the_settings_not_from_itself(self):
        with io.open(os.path.join(self.root, "scripts", "ports.py"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("print('61000 61003 9999 0')\n")
        self.recorder("net")
        self.recorder("netsh")
        self.run_bat("allow-firewall.bat")
        adds = [c for c in self.calls_made() if "add rule" in c]
        self.assertIn("localport=61000-61003", adds[0])

    def test_allow_without_python_says_run_the_launcher(self):
        self.recorder("net")
        self.recorder("netsh")
        rc, out = self.run_bat("allow-firewall.bat", with_python=False)
        self.assertEqual(rc, 1)
        self.assertIn("run start-dccore.bat first", out)
        self.assertEqual([c for c in self.calls_made() if c.startswith("netsh")], [])

    def test_remove_deletes_both_rules(self):
        self.recorder("net")
        self.recorder("netsh")
        rc, out = self.run_bat("remove-firewall.bat")
        self.assertEqual(rc, 0)
        deletes = [c for c in self.calls_made() if "delete rule" in c]
        self.assertEqual(len(deletes), 2)
        self.assertIn('name="DCCore DCC sends"', deletes[0])
        self.assertIn('name="DCCore dashboard"', deletes[1])

    # --- autostart -----------------------------------------------------

    def test_install_creates_an_on_logon_task_running_the_launcher(self):
        self.recorder("schtasks")
        rc, out = self.run_bat("install-autostart.bat")
        self.assertEqual(rc, 0, out)
        creates = [c for c in self.calls_made() if "/create" in c]
        self.assertEqual(len(creates), 1)
        self.assertIn('/tn "DCCore" /sc onlogon /tr', creates[0])
        self.assertIn("start-dccore.bat", creates[0])
        self.assertIn("/f", creates[0])
        self.assertNotIn("/ru", creates[0], "no account or password stored")
        self.assertIn("starts the next time you log on", out)

    def test_install_refuses_an_unconfigured_tree(self):
        os.remove(os.path.join(self.root, "settings.conf"))
        self.recorder("schtasks")
        rc, out = self.run_bat("install-autostart.bat")
        self.assertEqual(rc, 1)
        self.assertIn("not set up yet", out)
        self.assertEqual(self.calls_made(), [])

    def test_install_reports_a_refusal(self):
        self.recorder("schtasks", rc=1)
        rc, out = self.run_bat("install-autostart.bat")
        self.assertEqual(rc, 1)
        self.assertIn("Task Scheduler refused", out)

    def test_remove_deletes_the_task(self):
        self.recorder("schtasks")
        rc, out = self.run_bat("remove-autostart.bat")
        self.assertEqual(rc, 0)
        self.assertEqual([c for c in self.calls_made() if "/delete" in c], ['schtasks /delete /tn "DCCore" /f'])

    def test_remove_with_nothing_to_remove_is_not_an_error(self):
        self.recorder("schtasks", rc=1)
        rc, out = self.run_bat("remove-autostart.bat")
        self.assertEqual(rc, 0)
        self.assertIn("no \"DCCore\" entry", out)


class _Posix(_Tree):
    @classmethod
    def setUpClass(cls):
        cls.shell = shutil.which("bash") or shutil.which("sh")
        if not cls.shell:
            raise unittest.SkipTest("no POSIX shell on PATH")

    def recorder(self, name, rc=0):
        path = os.path.join(self.fakebin, name)
        with io.open(path, "w", encoding="ascii", newline="\n") as handle:
            handle.write('#!/bin/sh\necho "%s $*" >> "%s"\nexit %d\n' % (name, self.calls.replace("\\", "/"), rc))
        os.chmod(path, 0o755)

    def run_sh(self, rel, without=()):
        env = dict(os.environ)
        env["PATH"] = self.fakebin + os.pathsep + env.get("PATH", "")
        env["HOME"] = self.home
        env.pop("XDG_CONFIG_HOME", None)
        for name in without:
            # hide a real command by shadowing it with a failing stub, then
            # telling the script it is absent: `command -v` finds the stub,
            # so the scripts' "not found" branches are exercised through
            # a fake that exits 127 the way a missing command would.
            self.recorder(name, rc=127)
        with io.open(os.devnull) as devnull:
            done = subprocess.run([self.shell, os.path.join("scripts", *rel)], cwd=self.root, stdin=devnull,
                                  capture_output=True, text=True, errors="replace", timeout=120, env=env)
        return done.returncode, done.stdout + done.stderr


class TheLinuxAutostart(_Posix, unittest.TestCase):
    def setUp(self):
        self.make_tree()

    def unit_path(self):
        return os.path.join(self.home, ".config", "systemd", "user", "dccore.service")

    def test_install_writes_a_user_unit_and_enables_it_now(self):
        self.recorder("systemctl")
        rc, out = self.run_sh(("linux", "install-autostart.sh"))
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(self.unit_path()))
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(self.unit_path(), encoding="utf-8")
        root = os.path.realpath(self.root).replace("\\", "/")
        self.assertTrue(unit["Service"]["ExecStart"].endswith("/scripts/linux/start-dccore.sh"))
        self.assertTrue(unit["Service"]["WorkingDirectory"].replace("\\", "/").lower().endswith(os.path.basename(root).lower()))
        self.assertEqual(unit["Service"]["Restart"], "on-failure")
        self.assertEqual(unit["Install"]["WantedBy"], "default.target")
        calls = self.calls_made()
        self.assertIn("systemctl --user daemon-reload", calls)
        self.assertIn("systemctl --user enable --now dccore.service", calls)
        self.assertIn("loginctl enable-linger", out)

    def test_install_refuses_an_unconfigured_tree(self):
        os.remove(os.path.join(self.root, "settings.conf"))
        self.recorder("systemctl")
        rc, out = self.run_sh(("linux", "install-autostart.sh"))
        self.assertEqual(rc, 1)
        self.assertIn("not set up yet", out)
        self.assertFalse(os.path.exists(self.unit_path()))
        self.assertEqual(self.calls_made(), [])

    def test_remove_disables_and_deletes(self):
        self.recorder("systemctl")
        self.run_sh(("linux", "install-autostart.sh"))
        self.assertTrue(os.path.isfile(self.unit_path()))
        rc, out = self.run_sh(("linux", "remove-autostart.sh"))
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.unit_path()))
        self.assertIn("systemctl --user disable --now dccore.service", self.calls_made())

    def test_remove_with_nothing_there_is_fine(self):
        self.recorder("systemctl")
        rc, out = self.run_sh(("linux", "remove-autostart.sh"))
        self.assertEqual(rc, 0)
        self.assertIn("nothing to remove", out)
        self.assertEqual(self.calls_made(), [])


class TheMacAutostart(_Posix, unittest.TestCase):
    def setUp(self):
        self.make_tree()

    def plist_path(self):
        return os.path.join(self.home, "Library", "LaunchAgents", "com.dccore.bot.plist")

    def test_install_writes_an_agent_and_loads_it(self):
        self.recorder("launchctl")
        rc, out = self.run_sh(("macos", "install-autostart.command"))
        self.assertEqual(rc, 0, out)
        with io.open(self.plist_path(), "rb") as handle:
            plist = plistlib.load(handle)
        self.assertEqual(plist["Label"], "com.dccore.bot")
        self.assertTrue(plist["ProgramArguments"][0].endswith("/scripts/linux/start-dccore.sh"))
        self.assertTrue(plist["RunAtLoad"])
        self.assertEqual(plist["KeepAlive"], {"SuccessfulExit": False})
        self.assertTrue(plist["StandardOutPath"].endswith("dccore.log"))
        calls = self.calls_made()
        self.assertTrue(any(c.startswith("launchctl load -w ") and c.endswith("com.dccore.bot.plist") for c in calls), calls)

    def test_the_plist_survives_a_path_xml_would_choke_on(self):
        """An & in the folder name must be escaped, not break the plist."""
        odd = os.path.join(self.root, "Tom & Jerry")
        shutil.copytree(os.path.join(self.root, "scripts"), os.path.join(odd, "scripts"))
        shutil.copy(os.path.join(self.root, "settings.conf"), odd)
        self.recorder("launchctl")
        env = dict(os.environ, PATH=self.fakebin + os.pathsep + os.environ.get("PATH", ""), HOME=self.home)
        with io.open(os.devnull) as devnull:
            done = subprocess.run([self.shell, os.path.join("scripts", "macos", "install-autostart.command")],
                                  cwd=odd, stdin=devnull, capture_output=True, text=True, errors="replace",
                                  timeout=120, env=env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        with io.open(self.plist_path(), "rb") as handle:
            plist = plistlib.load(handle)
        self.assertIn("Tom & Jerry", plist["WorkingDirectory"])

    def test_install_refuses_an_unconfigured_tree(self):
        os.remove(os.path.join(self.root, "settings.conf"))
        self.recorder("launchctl")
        rc, out = self.run_sh(("macos", "install-autostart.command"))
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(self.plist_path()))
        self.assertEqual(self.calls_made(), [])

    def test_remove_unloads_and_deletes(self):
        self.recorder("launchctl")
        self.run_sh(("macos", "install-autostart.command"))
        rc, out = self.run_sh(("macos", "remove-autostart.command"))
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.plist_path()))
        self.assertTrue(any(c.startswith("launchctl unload -w ") for c in self.calls_made()))


class TheDocsCarryTheFourThings(unittest.TestCase):
    def test_windows_guide(self):
        doc = read(os.path.join(REPO_ROOT, "docs", "WINDOWS.md"))
        for must in ("allow-firewall.bat", "install-autostart.bat", "remove-autostart.bat",
                     "Port forwarding", "router"):
            self.assertIn(must, doc, must)

    def test_install_guide(self):
        doc = read(os.path.join(REPO_ROOT, "docs", "INSTALL.md"))
        for must in ("install-autostart.sh", "install-autostart.command", "ufw allow",
                     "Port forwarding", "enable-linger"):
            self.assertIn(must, doc, must)


if __name__ == "__main__":
    unittest.main()
