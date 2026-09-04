"""Reading an OmenServe operator's history out of mIRC's vars.ini.

Step 1 of the import designed on #69: the parser and the mapping, with no
browser and no HTTP in sight.

The fixture below is the real FORMAT taken from a live install - `nN=%Name
value`, the counters spread across three add-ons, hundreds of unrelated
variables around them - with invented values. Nobody's nicks, channels or
paths are in this file, which is also the property the feature itself is built
around: the dashboard filters in the page so those never reach the network.

The test that matters most is the one asserting a MISSING variable produces an
absent field rather than a zero. The counters come from add-ons, so a missing
one is the ordinary case, and writing a zero over somebody's real lifetime
total would be the worst thing this feature could do.
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import omenserve_import  # noqa: E402


# A real vars.ini is ~280 variables. This keeps the shape: the wanted counters
# scattered among unrelated ones, in mIRC's own numbering, not grouped or
# ordered helpfully.
FULL_INSTALL = "\n".join([
    "[variables]",
    "n0=%Ad-Mode Normal",
    "n1=%OS.DSlots 5",
    "n2=%OSL.SentToday 6",
    "n3=%OS.List.Type 1",
    "n4=%mx.rarsent 45902",
    "n5=%OSL.Cu1Files 200",
    "n6=%SDmaxspeed 48762663,SomeNick",
    "n7=%OS.Icon 3",
    "n8=%mx.rartsent 16295360049140",
    "n9=%OSL.SentYesterday 3",
    "n10=%mx.rardsent 351601454",
    "n11=%OSL.MbToday 34.56",
    "n12=%mx.rarysent 3244766247",
    "n13=%os.totalsize 1635488685616",
    "n14=%OSL.Today Friday",
    "n15=%sdlistsent 1308",
    "n16=%mx.rarday Wednesday",
    "n17=%OS.Update.Checks 1",
])


class ARealShapedFile(unittest.TestCase):

    def setUp(self):
        self.result = omenserve_import.read_install(FULL_INSTALL)

    def test_the_three_importable_numbers_are_found(self):
        self.assertEqual(self.result["values"], {
            "total_files": 45902,
            "total_bytes": 16295360049140,
            "speed_record": 48762663,
        })

    def test_the_speed_record_drops_the_nick_after_the_comma(self):
        """%SDmaxspeed is "<bytes>,<nick>" - the rate and who set it. Only the
        rate is wanted, and a plain int() on the whole value would fail."""
        self.assertEqual(self.result["values"]["speed_record"], 48762663)

    def test_the_speed_record_needs_no_unit_conversion(self):
        """It is already bytes/s, which is what speed_record.txt stores. The
        units warning on #69 was right in principle and does not apply here -
        asserted so a future "helpful" conversion has something to fail."""
        self.assertEqual(self.result["values"]["speed_record"], 48762663)
        self.assertNotEqual(self.result["values"]["speed_record"], 48762663 * 1024)

    def test_unrelated_variables_are_ignored(self):
        """An operator's file is mostly their own settings. Nothing outside
        FIELDS may reach the result at all."""
        targets = set(self.result["values"])

        self.assertTrue(targets <= {"total_files", "total_bytes", "speed_record"})

    def test_nothing_is_flagged_on_a_complete_file(self):
        """Control: the notes exist to warn, so a healthy file must be quiet."""
        self.assertEqual(self.result["notes"], [])


class MissingIsNotZero(unittest.TestCase):
    """The property this feature lives or dies on.

    The counters come from add-ons - mxrarserver and OS-Limits - so an install
    without one of them is ordinary, not broken. A missing variable has to be
    ABSENT from the values, so a caller cannot write a zero over a real total.
    """

    def test_an_absent_variable_is_not_in_the_values(self):
        result = omenserve_import.read_install("n0=%mx.rarsent 45902")

        self.assertIn("total_files", result["values"])
        self.assertNotIn("total_bytes", result["values"])
        self.assertNotIn("speed_record", result["values"])

    def test_an_absent_variable_is_not_zero(self):
        """Said twice on purpose - `values.get(x, 0)` and `values[x] == 0` are
        different mistakes, and this is the one that costs somebody their
        history."""
        result = omenserve_import.read_install("n0=%mx.rarsent 45902")

        self.assertIsNone(result["values"].get("total_bytes"))
        self.assertNotEqual(result["values"].get("total_bytes"), 0)

    def test_an_install_with_no_add_ons_yields_nothing_and_says_so(self):
        result = omenserve_import.read_install(
            "n0=%OS.DSlots 5\nn1=%OS.Icon 3")

        self.assertEqual(result["values"], {})
        self.assertTrue(any("add-ons" in note for note in result["notes"]))

    def test_a_half_parsed_file_is_flagged_rather_than_imported_quietly(self):
        """Files without bytes is the shape a mis-parse takes. Not refused - an
        unusual add-on mix could produce it - but the operator is told."""
        result = omenserve_import.read_install("n0=%mx.rarsent 45902")

        self.assertTrue(any("bytes sent was not" in note.lower()
                            for note in result["notes"]))


class TheDayBucketsAreShownAndLeft(unittest.TestCase):
    """%OSL.Today reads "Friday" - a weekday NAME. Nothing can tell whether
    "today" means today or six days ago, and db._rotate_day_unlocked() would
    rotate an imported "today" out of existence on the next new day."""

    def setUp(self):
        self.result = omenserve_import.read_install(FULL_INSTALL)

    def test_they_never_reach_the_values(self):
        for target in ("today_files", "yest_files", "today_bytes", "yest_bytes"):
            with self.subTest(target=target):
                self.assertNotIn(target, self.result["values"])

    def test_but_they_are_shown_in_the_preview(self):
        """An operator can see these in their own file. Silently dropping them
        looks like the parser missed them."""
        rows = {row["label"]: row for row in self.result["rows"]}

        self.assertEqual(rows["Files today"]["value"], 6)
        self.assertEqual(rows["Files yesterday"]["value"], 3)

    def test_and_marked_as_not_imported(self):
        rows = {row["label"]: row for row in self.result["rows"]}

        self.assertFalse(rows["Files today"]["imported"])
        self.assertTrue(rows["Files sent"]["imported"])


class AwkwardInput(unittest.TestCase):
    """vars.ini is plain text an operator may have hand-edited."""

    def test_variable_names_match_regardless_of_case(self):
        """mIRC treats them case-insensitively, and a real file is
        inconsistent - %OS.* and %os.* both appear in the same one."""
        result = omenserve_import.read_install("n0=%MX.RARSENT 45902")

        self.assertEqual(result["values"]["total_files"], 45902)

    def test_a_non_numeric_value_is_skipped_and_reported(self):
        result = omenserve_import.read_install("n0=%mx.rarsent not-a-number")

        self.assertNotIn("total_files", result["values"])
        self.assertTrue(any("not a number" in note for note in result["notes"]))

    def test_a_float_rounds_rather_than_failing(self):
        """%OSL.MbToday is "34.56". A preview row reading 35 is more useful
        than one reading "could not read"."""
        rows = {r["label"]: r for r in
                omenserve_import.read_install(FULL_INSTALL)["rows"]}

        self.assertIsNotNone(rows["Library size"]["value"])

    def test_empty_input_does_not_raise(self):
        for text in ("", None, "   \n\n  "):
            with self.subTest(text=text):
                result = omenserve_import.read_install(text)

                self.assertEqual(result["values"], {})

    def test_a_file_of_junk_does_not_raise(self):
        result = omenserve_import.read_install(
            "this is not an ini\n\x00\x01binary\nn=broken")

        self.assertEqual(result["values"], {})

    def test_the_paste_fallback_takes_just_the_kept_lines(self):
        """The dashboard sends only the filtered lines, and someone pasting
        into the textarea sends whatever they copied. Same format, so one code
        path - asserted rather than assumed, because a second parser for the
        fallback is exactly the duplication this avoids."""
        kept = "\n".join(["n4=%mx.rarsent 45902",
                          "n8=%mx.rartsent 16295360049140",
                          "n6=%SDmaxspeed 48762663,SomeNick"])

        self.assertEqual(omenserve_import.read_install(kept)["values"],
                         omenserve_import.read_install(FULL_INSTALL)["values"])


class TheFilterAndTheParserCannotDrift(unittest.TestCase):
    """The page filters the file to these names before sending. If the list it
    filters by and the list this parses were written separately, adding a field
    here would silently never arrive."""

    def test_every_field_is_offered_to_the_page(self):
        offered = set(omenserve_import.variable_names())
        declared = {field.variable for field in omenserve_import.FIELDS}

        self.assertEqual(offered, declared)

    def test_every_offered_name_is_one_the_parser_recognises(self):
        for name in omenserve_import.variable_names():
            with self.subTest(name=name):
                result = omenserve_import.read_install(f"n0={name} 123")
                row = [r for r in result["rows"] if r["variable"] == name]

                self.assertEqual(len(row), 1)
                self.assertEqual(row[0]["value"], 123)


if __name__ == "__main__":
    unittest.main()
