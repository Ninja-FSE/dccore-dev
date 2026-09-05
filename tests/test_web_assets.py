"""The dashboard's static files are never parsed by anything until a browser
loads them.

There are over a thousand tests in this suite and, until this file, not one of
them read web/. That gap cost a whole feature: two PRs that each appended a
rule to the end of web/style.css were merged, the join lost one closing brace,
and every rule after it - the entire File Lists folder grouping, including
`.file-row.is-hidden` which is what collapsing a folder actually does - became
part of an unclosed block and stopped applying. The dashboard rendered every
folder permanently expanded, Expand all and Collapse all did nothing, and the
suite stayed green.

A browser will not tell you either. CSS is specified to recover from errors
silently: an unclosed block swallows what follows and the page just looks
wrong. That is why this is a test rather than something anyone would notice.

These checks are deliberately structural, not stylistic. They do not care what
the CSS says, only that a browser will read all of it; not what the JavaScript
does, only that it parses; and not what the page looks like, only that the
elements the script reaches for are actually in it. Each one corresponds to a
mistake that has actually happened here.
"""

import io
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(REPO_ROOT, "web")


def read(name):
    with io.open(os.path.join(WEB, name), encoding="utf-8") as handle:
        return handle.read()


def strip_css_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


class TheStylesheetIsWellFormed(unittest.TestCase):

    def test_every_block_is_closed(self):
        """The bug this file was written for.

        An unclosed rule does not fail loudly anywhere: the browser folds the
        rest of the file into it, so rules that read perfectly well in the
        source never apply.
        """
        css = strip_css_comments(read("style.css"))

        depth, line, unclosed = 0, 1, []
        for char in css:
            if char == "\n":
                line += 1
            elif char == "{":
                depth += 1
                unclosed.append(line)
            elif char == "}":
                depth -= 1
                self.assertGreaterEqual(
                    depth, 0, f"style.css line {line}: closing brace with "
                              f"nothing open")
                if unclosed:
                    unclosed.pop()

        self.assertEqual(
            unclosed, [],
            "style.css has block(s) opened and never closed, starting at "
            "line(s) " + ", ".join(str(n) for n in unclosed) +
            " - everything after the first one is swallowed and never applies")

    def test_the_collapse_rule_is_reachable(self):
        """Named explicitly because it is the one that broke, and because a
        rule can be present in the file and still never apply.

        Counting braces before it is how you tell the difference between "the
        rule is in the stylesheet" and "the browser will use it".
        """
        css = strip_css_comments(read("style.css"))
        needle = ".file-row.is-hidden"

        self.assertIn(needle, css, "the rule that collapses a folder is gone")
        before = css[:css.index(needle)]
        self.assertEqual(
            before.count("{") - before.count("}"), 0,
            f"{needle} sits inside an unclosed block, so a collapsed folder "
            f"would stay visible")


class TheScriptIsWellFormed(unittest.TestCase):

    def test_brackets_and_strings_balance(self):
        """Not a parser - a structural check for the mistakes that actually
        happen when JavaScript is assembled from concatenated strings: a
        bracket left open, or a quote left unterminated."""
        source = read("app.js")
        pairs = {")": "(", "]": "[", "}": "{"}
        stack, line, i, n, problems = [], 1, 0, len(source), []

        while i < n:
            char = source[i]
            if char == "\n":
                line += 1
                i += 1
                continue
            if source.startswith("//", i):
                i = source.find("\n", i)
                i = n if i < 0 else i
                continue
            if source.startswith("/*", i):
                end = source.find("*/", i + 2)
                line += source.count("\n", i, end)
                i = end + 2
                continue
            if char in "\"'":
                quote, j = char, i + 1
                while j < n:
                    if source[j] == "\\":
                        j += 2
                        continue
                    if source[j] == quote:
                        break
                    if source[j] == "\n":
                        problems.append(f"line {line}: unterminated string")
                        break
                    j += 1
                i = j + 1
                continue
            if char in "([{":
                stack.append((char, line))
            elif char in ")]}":
                if not stack or stack[-1][0] != pairs[char]:
                    problems.append(f"line {line}: unexpected {char!r}")
                elif stack:
                    stack.pop()
            i += 1

        problems += [f"line {ln}: {ch!r} never closed" for ch, ln in stack]
        self.assertEqual(problems, [], "app.js: " + "; ".join(problems))


class TheScriptAndThePageAgree(unittest.TestCase):

    def test_every_element_the_script_looks_up_exists(self):
        """getElementById returns null for a missing id, and the failure lands
        later and elsewhere - an addEventListener on null, halfway through
        setup, taking the rest of the script's initialisation with it."""
        page = read("index.html")

        missing = sorted({
            element_id
            for element_id in re.findall(r'getElementById\("([^"]+)"\)', read("app.js"))
            if f'id="{element_id}"' not in page
        })

        self.assertEqual(
            missing, [],
            "app.js looks up element id(s) that index.html does not define: "
            + ", ".join(missing))

    def test_the_script_never_removes_a_view_section(self):
        """The sibling of the test above: an element that exists at load time
        but is DELETED at runtime fails exactly the same way, and that check
        cannot see it because index.html still defines the id.

        It happened. `disableConsoleUi()` removed `#view-console` when the
        Console was switched off, and `activateView()` walks every key in
        `views` calling `getElementById("view-" + key).classList` on each - so
        after the removal that threw on EVERY view switch. The Console went
        away and took the rest of the navigation with it: Settings stayed on
        its "Loading" placeholder because the exception fired before the branch
        that loads it, and Queue, Stats and Downloads stopped refreshing too.
        Reported from a real install, running the shipped default.

        This is structural, not behavioural - nothing here executes
        JavaScript (see this module's own docstring). It cannot prove the
        navigation works; it can refuse the specific move that broke it, which
        is deleting a section the router still expects to find. Hiding one is
        fine and is what the fix does.
        """
        script = read("app.js")

        # `var x = document.getElementById("view-...")` followed later by
        # `x.remove()`, and the direct form.
        removed = set(re.findall(
            r'getElementById\("(view-[^"]+)"\)\s*;?\s*\n?[^\n]*\.remove\(\)', script))
        removed |= set(re.findall(
            r'getElementById\("(view-[^"]+)"\)\.remove\(\)', script))

        # The indirect form the real defect used: assigned to a name, removed
        # a line or two later.
        for name, view_id in re.findall(
                r'var\s+(\w+)\s*=\s*document\.getElementById\("(view-[^"]+)"\)', script):
            if re.search(r'\b' + re.escape(name) + r'\.remove\(\)', script):
                removed.add(view_id)

        self.assertEqual(
            sorted(removed), [],
            "app.js removes view section(s) that activateView() still looks up "
            "on every switch: " + ", ".join(sorted(removed)) +
            ". Hide them instead - .hidden = true - or the router throws and "
            "takes every other view down with it.")

    def test_the_view_router_tolerates_a_missing_section(self):
        """Belt to the braces above. Even with no section deleted, a null from
        getElementById must not take the whole router down - the failure that
        made one disabled feature disable the entire dashboard."""
        script = read("app.js")

        router = script[script.index("function activateView"):]
        router = router[:router.index("\n  }")]

        self.assertNotRegex(
            router, r'getElementById\("view-" \+ key\)\.classList',
            "activateView() dereferences getElementById directly. One absent "
            "section then throws before the per-view loaders below it run, so "
            "every other view stops working too. Assign it and check for null.")

    def test_every_view_the_nav_offers_exists_in_both(self):
        """A nav button whose view is missing throws on the first click; a view
        the script does not know about can never be activated."""
        page = read("index.html")
        script = read("app.js")

        nav = set(re.findall(r'data-view="([^"]+)"', page))
        sections = set(re.findall(r'id="view-([^"]+)"', page))

        self.assertEqual(nav, sections,
                         f"nav buttons and view sections disagree: "
                         f"buttons only {sorted(nav - sections)}, "
                         f"sections only {sorted(sections - nav)}")
        for name in sorted(nav):
            self.assertRegex(
                script, r"\b%s:\s*\{" % re.escape(name),
                f"index.html offers the {name!r} view but app.js's views table "
                f"has no entry for it, so its title and subtitle would be "
                f"undefined")



class RulesThatOverrideActuallyWin(unittest.TestCase):
    """A CSS rule can be present, well-formed, and still do nothing.

    Dimming the file rows under a folder heading was written as
    `.file-row td`, which is specificity (0,1,1). `.data-table tbody td` had
    already set the full-strength colour at (0,1,2), and specificity beats
    source order - so the new rule lost wherever it was placed in the file,
    and the rows rendered exactly as before.

    That is the same failure as the unclosed brace in shape: the stylesheet
    reads correctly and the page ignores it. Nothing here checks appearance,
    only that a rule written to override another one is capable of doing so.
    """

    @staticmethod
    def specificity(selector):
        """(ids, classes, elements) - CSS's own ordering.

        Parsed by walking the selector rather than by regex: the element count
        needs word boundaries, and every attempt to express those through the
        layers between here and the file turned one into a literal backspace.
        This is duller and cannot be mangled.
        """
        ids = classes = elements = 0
        # Combinators separate compound selectors; they carry no weight.
        for part in (selector.replace(">", " ").replace("+", " ")
                     .replace("~", " ").split()):
            i, n = 0, len(part)
            while i < n:
                char = part[i]
                if char == "#":
                    ids += 1
                    i += 1
                elif char == ".":
                    classes += 1
                    i += 1
                elif char == "[":
                    classes += 1
                    close = part.find("]", i)
                    i = n if close < 0 else close + 1
                    continue
                elif char == ":":
                    # ::before and friends count as elements, :hover as a class.
                    if part.startswith("::", i):
                        elements += 1
                        i += 2
                    else:
                        classes += 1
                        i += 1
                elif char == "*":
                    i += 1
                    continue
                elif char.isalpha():
                    elements += 1
                else:
                    i += 1
                    continue
                # Consume the name that follows the sigil (or the bare element).
                while i < n and (part[i].isalnum() or part[i] in "-_"):
                    i += 1
        return (ids, classes, elements)

    def selectors_setting(self, prop):
        """[(selector, specificity)] for every rule that sets `prop`."""
        css = strip_css_comments(read("style.css"))
        found = []
        for match in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
            selector, body = match.group(1).strip(), match.group(2)
            if re.search(r"(^|;)\s*%s\s*:" % re.escape(prop), body):
                for one in selector.split(","):
                    found.append((one.strip(), self.specificity(one)))
        return found

    def test_the_file_row_colour_beats_the_general_cell_colour(self):
        """The concrete case. Both set `color` on the same cells, so the more
        specific one is the one that renders."""
        rules = dict(self.selectors_setting("color"))

        general = [s for s in rules if s == ".data-table tbody td"]
        self.assertTrue(general, "the general table cell colour rule is gone; "
                                 "this test needs updating rather than deleting")

        overrides = [(s, spec) for s, spec in rules.items()
                     if "file-row" in s and ":hover" not in s]
        self.assertTrue(overrides, "nothing dims the file rows any more")

        for selector, spec in overrides:
            self.assertGreater(
                spec, rules[".data-table tbody td"],
                f"{selector!r} has specificity {spec}, which does not beat "
                f"'.data-table tbody td' at {rules['.data-table tbody td']} - "
                f"so the file rows keep the full-strength colour and the rule "
                f"does nothing")

    def test_the_specificity_calculation_is_correct(self):
        """Fixture invariant, and asserted on exact values rather than on
        ordering.

        An earlier version compared only orderings, and passed while the
        element count was stuck at zero for every selector - the word-boundary
        escape in its regex had been mangled into a literal backspace. Every
        comparison still held, because they were all equally wrong.
        """
        cases = [
            ("td", (0, 0, 1)),
            (".x td", (0, 1, 1)),
            (".file-row td", (0, 1, 1)),
            (".data-table tbody td", (0, 1, 2)),
            (".data-table tbody tr.file-row td", (0, 2, 3)),
            (".file-row:hover td", (0, 2, 1)),
            ("#id .c e", (1, 1, 1)),
            ("a[href]", (0, 1, 1)),
            ("li::before", (0, 0, 2)),
            ("ul > li + li", (0, 0, 3)),
        ]

        wrong = [f"{sel!r}: got {self.specificity(sel)}, expected {expected}"
                 for sel, expected in cases
                 if self.specificity(sel) != expected]

        self.assertEqual(wrong, [], "; ".join(wrong))


class APostJsonResultIsReadTheWayPostJsonReturnsIt(unittest.TestCase):
    """postJson() resolves with { ok, status, data } - and nothing here
    executes JavaScript, so reading a property it does not have is silent.

    Found by writing it wrong. The served-folder editor (#164 step 4) was
    written against `res.body`, which is `undefined` on every one of those
    objects: the save would have thrown a TypeError on its first success, in a
    handler with no catch, and the page would have gone quiet with no error
    anywhere. Exactly the shape of #267 - a JavaScript defect that every test
    in this suite passed straight through, found by a real click.

    Cheap to state as a rule, so it is stated as one rather than left to the
    next author to remember which of the two helpers returns which shape.
    """

    def test_no_caller_reads_res_body(self):
        source = read("app.js")

        self.assertNotIn("res.body", source,
                         "postJson() resolves with { ok, status, data } - "
                         "res.body is undefined, and reading through it throws "
                         "inside a promise nothing catches")

    def test_the_helper_still_returns_that_shape(self):
        """The assertion above is only meaningful while this is true. If
        postJson ever does return `body`, this fails first and says so, rather
        than the rule silently becoming wrong."""
        source = read("app.js")

        body = source.split("function postJson(", 1)[1].split("\n  }", 1)[0]

        self.assertIn("ok: res.ok", body)
        self.assertIn("data: data", body)


class TheNavTheViewsAndTheSectionsAgree(unittest.TestCase):
    """Three lists that have to name the same set, and nothing checks them at
    runtime because nothing here executes JavaScript.

    activateView() walks every key in `views` and looks up "view-" + key, so a
    key with no section is a null dereference that takes the whole router
    down - which is exactly what #267 was. A nav button with no `views` entry
    reads `views[name].title` on undefined. And a section nobody can reach is
    dead markup that still costs a lookup on every switch.

    Written when Queue was folded into Stats and three tabs were renamed
    (#133), because that change touched all three lists at once and a
    half-applied rename would have been invisible until somebody clicked.
    """

    def sets(self):
        import re

        js = read("app.js")
        html = read("index.html")
        block = js.split("var views = {", 1)[1].split("};", 1)[0]
        return (set(re.findall(r"^\s*([a-z]+):", block, re.M)),
                set(re.findall(r'id="view-([a-z]+)"', html)),
                set(re.findall(r'data-view="([a-z]+)"', html)))

    def test_every_views_key_has_a_section(self):
        views, sections, _nav = self.sets()

        self.assertEqual(sorted(views - sections), [],
                         "activateView() looks up view-<key> for every key in "
                         "`views`; a missing section is a null dereference on "
                         "EVERY view switch, not just that one")

    def test_every_nav_button_has_a_views_entry(self):
        views, _sections, nav = self.sets()

        self.assertEqual(sorted(nav - views), [],
                         "activateView() reads views[name].title, which is a "
                         "TypeError for a nav button with no entry")

    def test_every_section_is_reachable(self):
        _views, sections, nav = self.sets()

        self.assertEqual(sorted(sections - nav), [],
                         "a view section with no nav button cannot be opened")

    def test_the_default_view_exists(self):
        """state.active starts at "search" before any click."""
        views, sections, nav = self.sets()

        for name, group in (("views", views), ("sections", sections),
                            ("nav", nav)):
            with self.subTest(group=name):
                self.assertIn("search", group)


if __name__ == "__main__":
    unittest.main()
