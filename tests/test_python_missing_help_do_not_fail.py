"""#547, Proposal 2: Python missing - help, do not fail.

After Proposal 1 the one wall left for a Windows first-timer was "Python
was not found" and a URL. The launcher now offers to fetch python.org's
own installer, checks it against a SHA-256 pinned in the file, and runs
it unattended with both boxes ticked, per user. Linux and macOS name the
package command and stop, as before - that is the whole of the proposal
there, and it is pinned here too.

Executed where cmd.exe exists: the launcher runs in a throwaway tree with
a PATH that has no Python on it and LOCALAPPDATA/ProgramFiles pointing at
empty folders, so the search finds nothing; `choice` is answered through
stdin; `curl` is a fake on PATH ahead of the real one that writes what the
test wants downloaded; certutil is the real one, or a fake that prints the
pinned hash in certutil's format. The installer itself is never run for
real - what the fake download writes is not a program, so `start /wait`
fails at once and the launcher's own "exited with code" path is what is
seen. The pin's truth against python.org is a network check, opt-in.
"""

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

WINDOWS = os.path.join(REPO_ROOT, "scripts", "windows", "start-dccore.bat")
LINUX = os.path.join(REPO_ROOT, "scripts", "linux", "start-dccore.sh")


def bat_text():
    with io.open(WINDOWS, encoding="utf-8", newline="") as handle:
        return handle.read()


def pins():
    text = bat_text()
    version = re.search(r'set "PY_VERSION=([0-9.]+)"', text).group(1)
    amd64 = re.search(r'set "PY_SHA256_AMD64=([0-9a-f]+)"', text).group(1)
    arm64 = re.search(r'set "PY_SHA256_ARM64=([0-9a-f]+)"', text).group(1)
    return version, amd64, arm64


class ThePin(unittest.TestCase):
    def test_version_and_two_hashes_are_pinned(self):
        version, amd64, arm64 = pins()
        self.assertRegex(version, r"^3\.1[0-9]\.[0-9]+$")
        self.assertRegex(amd64, r"^[0-9a-f]{64}$")
        self.assertRegex(arm64, r"^[0-9a-f]{64}$")
        self.assertNotEqual(amd64, arm64)

    def test_the_url_is_built_from_the_version_and_the_processor(self):
        text = bat_text()
        self.assertIn('https://www.python.org/ftp/python/%PY_VERSION%/python-%PY_VERSION%-%PY_ARCH%.exe', text)
        self.assertIn('"%PROCESSOR_ARCHITECTURE%"=="AMD64"', text)
        self.assertIn('"%PROCESSOR_ARCHITEW6432%"=="AMD64"', text)
        self.assertIn('"%PROCESSOR_ARCHITECTURE%"=="ARM64"', text)

    def test_the_pinned_version_is_in_the_ci_matrix(self):
        """The suite runs on every minor the launcher can install."""
        version, _, _ = pins()
        minor = ".".join(version.split(".")[:2])
        with io.open(os.path.join(REPO_ROOT, ".github", "workflows", "tests.yml"), encoding="utf-8") as handle:
            self.assertIn(f'"{minor}"', handle.read())

    @unittest.skipUnless(os.environ.get("DCCORE_VERIFY_PYTHON_PIN"),
                         "network: set DCCORE_VERIFY_PYTHON_PIN=1 to download both installers and hash them")
    def test_the_pins_match_python_org(self):
        version, amd64, arm64 = pins()
        for arch, pinned in (("amd64", amd64), ("arm64", arm64)):
            url = f"https://www.python.org/ftp/python/{version}/python-{version}-{arch}.exe"
            with urllib.request.urlopen(url, timeout=120) as response:
                self.assertEqual(hashlib.sha256(response.read()).hexdigest(), pinned, url)


class TheShapeOfTheOffer(unittest.TestCase):
    """Source-level, so they run everywhere; the executed ones are below."""

    def test_it_asks_before_downloading(self):
        text = bat_text()
        self.assertLess(text.index("choice /c YN"), text.index("call curl -L"))
        self.assertIn("if errorlevel 2 goto :python_by_hand", text)

    def test_the_hash_is_checked_before_the_installer_runs(self):
        text = bat_text()
        self.assertLess(text.index("certutil -hashfile"), text.index("start /wait"))
        self.assertLess(text.index('if /i not "%PY_HASH%"=="%PY_SHA256%"'), text.index("start /wait"))

    def test_a_mismatch_deletes_the_file_and_does_not_run_it(self):
        text = bat_text()
        block = text.split('if /i not "%PY_HASH%"=="%PY_SHA256%" (', 1)[1].split("\n)", 1)[0]
        self.assertIn('del /q "%PY_INSTALLER%"', block)
        self.assertIn("goto :python_by_hand", block)
        self.assertNotIn("start", block)

    def test_the_installer_runs_unattended_per_user_with_both_boxes_ticked(self):
        self.assertIn("/passive InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0", bat_text())

    def test_a_reboot_required_exit_still_counts_as_installed(self):
        self.assertIn('if "%PY_INSTALL_RC%"=="3010" goto :installed_python', bat_text())

    def test_after_installing_it_searches_again_rather_than_giving_up(self):
        """This window's PATH predates the install; the per-user folder
        search from Proposal 1 is what finds the new Python."""
        text = bat_text()
        self.assertIn(":find_python", text)
        self.assertIn("goto :find_python", text.split(":installed_python", 1)[1])
        self.assertIn("if defined PY_INSTALL_TRIED goto :python_by_hand", text, "and only once")

    def test_curl_fails_on_an_http_error_rather_than_saving_the_error_page(self):
        self.assertIn("call curl -L --fail", bat_text())

    def test_a_32_bit_windows_gets_the_page(self):
        self.assertIn("This Windows is 32-bit", bat_text())

    def test_linux_and_macos_name_the_package_command_and_stop(self):
        with io.open(LINUX, encoding="utf-8") as handle:
            text = handle.read()
        for must in ("sudo apt install python3", "sudo dnf install python3", "brew install python"):
            self.assertIn(must, text)
        self.assertNotIn("curl", text.split("find an interpreter", 1)[1].split("check-only mode", 1)[0])


@unittest.skipUnless(os.name == "nt" and (shutil.which("cmd.exe") or shutil.which("cmd")),
                     "cmd.exe is only available on Windows")
class TheOfferRun(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dccore-no-python-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        target = os.path.join(self.root, "scripts", "windows")
        os.makedirs(target)
        shutil.copy(WINDOWS, target)
        self.fakebin = os.path.join(self.root, "fakebin")
        os.makedirs(self.fakebin)
        self.temp = os.path.join(self.root, "temp")
        os.makedirs(self.temp)
        for empty in ("localappdata", "programfiles"):
            os.makedirs(os.path.join(self.root, empty))
        self.version, self.amd64, self.arm64 = pins()

    def fake(self, name, body):
        with io.open(os.path.join(self.fakebin, name), "w", encoding="ascii", newline="\r\n") as handle:
            handle.write(body)

    def run_launcher(self, answer):
        system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        env = dict(os.environ)
        env.update({
            "PATH": self.fakebin + os.pathsep + system32,   # no python, no py
            "LOCALAPPDATA": os.path.join(self.root, "localappdata"),
            "ProgramFiles": os.path.join(self.root, "programfiles"),
            "TEMP": self.temp, "TMP": self.temp,
            "DCCORE_NO_BROWSER": "1",
        })
        cmd = shutil.which("cmd.exe") or shutil.which("cmd")
        done = subprocess.run([cmd, "/c", r"scripts\windows\start-dccore.bat"], cwd=self.root,
                              input=answer + "\n", capture_output=True, text=True, errors="replace", timeout=120, env=env)
        return done.returncode, done.stdout + done.stderr

    def installer_path(self):
        arch = "arm64" if os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() == "ARM64" else "amd64"
        return os.path.join(self.temp, f"python-{self.version}-{arch}.exe")

    def pinned_for_this_machine(self):
        return self.arm64 if os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() == "ARM64" else self.amd64

    def test_declining_the_offer_prints_the_page_and_stops(self):
        rc, out = self.run_launcher("N")
        self.assertEqual(rc, 1)
        self.assertIn("Python was not found", out)
        self.assertIn("Download and install Python now?", out)
        self.assertIn("https://www.python.org/downloads/windows/", out)
        self.assertNotIn("Downloading", out)
        self.assertFalse(os.path.exists(self.installer_path()))

    def test_no_answer_at_all_is_a_no(self):
        """An unattended run - stdin closed - must never download anything."""
        rc, out = self.run_launcher("")
        self.assertEqual(rc, 1)
        self.assertNotIn("Downloading", out)

    def test_a_download_that_does_not_match_the_pin_is_not_run(self):
        # curl "downloads" junk; the real certutil hashes it; it does not match
        self.fake("curl.bat", "@echo off\r\necho JUNK-NOT-AN-INSTALLER> %~5\r\n")
        rc, out = self.run_launcher("Y")
        self.assertEqual(rc, 1)
        self.assertIn("Downloading https://www.python.org/ftp/python/" + self.version, out)
        self.assertIn("does not match the fingerprint", out)
        self.assertIn("expected " + self.pinned_for_this_machine(), out)
        self.assertNotIn("Installing Python", out)
        self.assertFalse(os.path.exists(self.installer_path()), "the refused file is deleted")

    def test_a_download_that_fails_is_reported_not_hashed(self):
        self.fake("curl.bat", "@echo off\r\nexit /b 22\r\n")
        rc, out = self.run_launcher("Y")
        self.assertEqual(rc, 1)
        self.assertIn("The download did not complete", out)
        self.assertNotIn("fingerprint", out.split("did not complete", 1)[1])

    def test_a_download_that_matches_the_pin_is_installed(self):
        """The fake certutil vouches for the file, so the launcher runs it.
        What curl wrote is not a program, so Windows refuses it at once and
        the launcher's own exit-code path is what shows - the point is that
        the run was attempted only after the match, and told the truth."""
        self.fake("curl.bat", "@echo off\r\necho NOT-A-PROGRAM> %~5\r\n")
        self.fake("certutil.bat", "@echo off\r\necho SHA256 hash of file:\r\n"
                                  f"echo {self.pinned_for_this_machine()}\r\necho CertUtil: -hashfile command completed successfully.\r\n")
        rc, out = self.run_launcher("Y")
        self.assertEqual(rc, 1)
        self.assertIn("Fingerprint matches. Installing Python " + self.version, out)
        self.assertIn("The Python installer exited with code", out)
        self.assertFalse(os.path.exists(self.installer_path()), "the installer is removed afterwards")

    def test_a_hash_printed_with_spaces_still_matches(self):
        """Older certutil prints the bytes space-separated."""
        spaced = " ".join(re.findall("..", self.pinned_for_this_machine()))
        self.fake("curl.bat", "@echo off\r\necho NOT-A-PROGRAM> %~5\r\n")
        self.fake("certutil.bat", "@echo off\r\necho SHA256 hash of file:\r\n"
                                  f"echo {spaced}\r\necho CertUtil: done.\r\n")
        rc, out = self.run_launcher("Y")
        self.assertIn("Fingerprint matches", out)

    def test_with_python_present_none_of_this_appears(self):
        """The offer exists only where there is no Python at all."""
        rc, out = self.run_launcher_with_real_python()
        self.assertNotIn("Python was not found", out)
        self.assertNotIn("Download and install Python now?", out)

    def run_launcher_with_real_python(self):
        cmd = shutil.which("cmd.exe") or shutil.which("cmd")
        env = dict(os.environ, DCCORE_NO_BROWSER="1")
        with io.open(os.devnull) as devnull:
            done = subprocess.run([cmd, "/c", r"scripts\windows\start-dccore.bat", "check"], cwd=self.root,
                                  stdin=devnull, capture_output=True, text=True, errors="replace", timeout=120, env=env)
        return done.returncode, done.stdout + done.stderr


if __name__ == "__main__":
    unittest.main()
