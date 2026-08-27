"""The daemon's own source stays English and ASCII.

TWO INVARIANTS, AND WHY BOTH ARE NEEDED

#65 and #66 translated every module from Swedish. Two things then went wrong,
and each of these tests exists because of one of them:

  * #57 added new Swedish comments to config.py after the translation had
    branched, so they were never in its scope and shipped untranslated.
    Translation is not a one-off - without a guard the next feature branch
    reintroduces it.

  * The translation itself was driven by searching for accented characters,
    which silently missed eighteen lines of Swedish that happen to contain no
    a-ring, a-umlaut or o-umlaut - "Kunde inte skicka JOIN", "Startar
    tidtagaruret", and so on. An accent scan cannot see those.

So one test looks for non-ASCII, and one looks for Swedish WORDS, because
neither finds what the other does.

WHY THIS IS NOT MERELY TIDINESS

print() encodes with whatever code page the attached stream has. cp1252
contains a-ring/a-umlaut/o-umlaut; cp1253, cp1251, cp932 and ascii do not, and
an uncaught UnicodeEncodeError kills the thread that printed. #59 added a guard
so no character can take the process down, but the guard is a safety net -
English ASCII source is the actual fix.

tests/ is deliberately exempt. Its fixtures include "Bjork/Joga.flac" and a
400-character run of a-umlaut precisely to prove non-ASCII FILENAMES work;
translating those would destroy what they test.
"""
import io
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Words with no English homograph, so a single hit is conclusive. Deliberately
# excludes ones that read as English too - "men", "till", "per", "med", "name"
# - which produced false positives when this list was first calibrated.
SWEDISH = re.compile(
    r"\b("
    r"och|att|inte|ingen|inget|inga|kunde|skicka|skickar|skickas|skickade|"
    r"startar|returnerar|misslyckades|ladda|laddade|hitta|hittade|"
    r"konvertera|konverterar|loggar|anvandare|nekade|rensar|sparar|"
    r"vantar|packar|raderade|filerna|mappen|namnet|minnet|heltal|antalet|"
    r"totala|botens|gick|handskakning|understreck|apostrof|saknar|"
    r"originalet|matchar|textfiler|huvudkanaler|konsolen|slutnotis|"
    r"tidtagaruret|kanalen|fildelning|sokning|sokvag|listan|kon|fran"
    r")\b",
    re.IGNORECASE,
)


def _modules():
    """Every .py file the daemon itself is built from."""
    out = []
    for name in sorted(os.listdir(REPO_ROOT)):
        if name.endswith(".py") and name != "local_config.py":
            out.append(os.path.join(REPO_ROOT, name))
    return out


class TheSourceIsAscii(unittest.TestCase):
    """#59's table: cp1253, cp1251, cp932 and ascii cannot encode a-ring,
    a-umlaut or o-umlaut, and an uncaught UnicodeEncodeError from a print()
    kills the thread that ran it."""

    def test_no_module_contains_a_non_ascii_character(self):
        offenders = []
        for path in _modules():
            with io.open(path, encoding="utf-8") as handle:
                lines = handle.readlines()
            for n, line in enumerate(lines, 1):
                if any(ord(c) > 127 for c in line):
                    offenders.append(
                        f"{os.path.basename(path)}:{n}: "
                        + line.strip().encode("ascii", "backslashreplace").decode()[:70])
        self.assertEqual(
            offenders, [],
            "non-ASCII in module source:\n  " + "\n  ".join(offenders))


class TheSourceIsEnglish(unittest.TestCase):
    """An accent scan cannot see 'Kunde inte skicka JOIN'. This can."""

    def test_no_module_contains_swedish(self):
        offenders = []
        for path in _modules():
            with io.open(path, encoding="utf-8") as handle:
                lines = handle.readlines()
            for n, line in enumerate(lines, 1):
                stripped = line.strip()
                # Only prose - comments, docstrings and message strings.
                if not (stripped.startswith("#") or '"' in stripped or "'" in stripped):
                    continue
                found = SWEDISH.findall(line)
                if found:
                    offenders.append(
                        f"{os.path.basename(path)}:{n}: {sorted(set(w.lower() for w in found))} "
                        f"-> {stripped[:60]}")
        self.assertEqual(
            offenders, [],
            "Swedish in module source:\n  " + "\n  ".join(offenders))

    def test_the_detector_actually_detects(self):
        """Control. A word list that matched nothing would let this pass on a
        fully Swedish file."""
        sample = 'print(f"[DB ERROR] Kunde inte skicka ban-notis: {err}")'
        self.assertTrue(SWEDISH.findall(sample),
                        "the detector no longer recognises Swedish")


if __name__ == "__main__":
    unittest.main()
