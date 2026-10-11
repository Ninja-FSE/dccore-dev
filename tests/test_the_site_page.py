"""#1309 (was #875): the one-page site in site/, published by GitHub Pages.

What the page must hold to, checked on the file itself:

  * every link resolves - to a section of the page, or to a file that is in
    the PUBLIC tree, where docs/UPDATES-PUBLIC.md has become docs/UPDATES.md
    and the export-ignored files are gone;
  * it makes no third-party requests: no scripts, stylesheets, fonts, images
    or frames from anywhere, so opening it tells nobody anything;
  * the workflow deploys only in the public repository, and only site/.

The identifier sweep (test_no_personal_identifiers_ship.py) already scans
.html, so a caption with a real channel or nick in it fails there.
"""

import io
import os
import re
import sys
import unittest
from html.parser import HTMLParser

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

PAGE = os.path.join(REPO_ROOT, "site", "index.html")
WORKFLOW = os.path.join(REPO_ROOT, ".github", "workflows", "pages.yml")
PUBLIC_REPO = "https://github.com/Ninja-FSE/dccore"

# What extraction renames: the public changelog ships under the internal one's
# name (docs/PUBLIC-REPO-WORKFLOW.md, step 3).
RENAMED_AT_EXTRACTION = {"docs/UPDATES.md": "docs/UPDATES-PUBLIC.md"}


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


class _Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if attrs.get("id"):
            self.ids.add(attrs["id"])


def parsed():
    collector = _Collect()
    collector.feed(read(PAGE))
    return collector


def export_ignored():
    ignored = set()
    for line in read(os.path.join(REPO_ROOT, ".gitattributes")).splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.lstrip().startswith("#") and "export-ignore" in parts[1:]:
            ignored.add(parts[0])
    return ignored


class EveryLinkResolves(unittest.TestCase):

    def links(self):
        return [attrs["href"] for tag, attrs in parsed().tags if tag == "a" and "href" in attrs]

    def test_there_are_links_to_check(self):
        self.assertGreaterEqual(len(self.links()), 8)

    def test_each_goes_to_the_page_or_to_the_public_repository(self):
        for href in self.links():
            with self.subTest(href=href):
                self.assertTrue(href.startswith("#") or href == PUBLIC_REPO or href.startswith(PUBLIC_REPO + "/"),
                                "a link out of the project")

    def test_a_section_link_names_a_section(self):
        ids = parsed().ids
        for href in self.links():
            if href.startswith("#"):
                with self.subTest(href=href):
                    self.assertIn(href[1:], ids)

    def test_a_file_link_names_a_file_the_public_tree_has(self):
        ignored = export_ignored()
        checked = 0
        for href in self.links():
            found = re.match(re.escape(PUBLIC_REPO) + r"/blob/main/(.+)$", href)
            if not found:
                continue
            path = found.group(1)
            source = RENAMED_AT_EXTRACTION.get(path, path)
            with self.subTest(path=path):
                self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, source)), f"{source} is not in the tree")
                if path not in RENAMED_AT_EXTRACTION:
                    self.assertNotIn(path, ignored, "export-ignored: the public tree does not have it")
            checked += 1
        self.assertGreaterEqual(checked, 5)

    def test_any_other_repository_link_is_a_page_github_has(self):
        allowed = {PUBLIC_REPO, PUBLIC_REPO + "/releases", PUBLIC_REPO + "/releases/latest"}
        for href in self.links():
            if href.startswith(PUBLIC_REPO) and "/blob/main/" not in href:
                with self.subTest(href=href):
                    self.assertIn(href, allowed)


class NothingIsLoadedFromAnywhere(unittest.TestCase):

    def test_no_tag_loads_anything(self):
        for tag, attrs in parsed().tags:
            with self.subTest(tag=tag):
                self.assertNotIn(tag, ("script", "iframe", "object", "embed", "img", "link", "source", "video",
                                       "audio"), f"<{tag}> loads something: {attrs}")

    def test_the_css_fetches_nothing(self):
        style = "".join(re.findall(r"<style>(.*?)</style>", read(PAGE), re.S))
        self.assertNotIn("@import", style)
        self.assertNotIn("url(", style)

    def test_it_is_one_complete_page(self):
        page = read(PAGE)
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn('<html lang="en">', page)
        self.assertRegex(page, r"<title>[^<]+</title>")
        self.assertIn('name="viewport"', page)


class TheWorkflowDeploysOnlyThere(unittest.TestCase):

    def test_the_deploy_job_runs_only_in_the_public_repository(self):
        workflow = read(WORKFLOW)
        job = workflow.split("jobs:", 1)[1]
        self.assertIn("if: github.repository == 'Ninja-FSE/dccore'", job.split("steps:", 1)[0])

    def test_it_publishes_site_and_nothing_else(self):
        workflow = read(WORKFLOW)
        self.assertRegex(workflow, r"upload-pages-artifact@v\d+\s+with:\s+path: site\s")
        self.assertNotIn("path: docs", workflow)

    def test_site_ships_to_the_public_repository(self):
        """export-ignore would keep it out of the extraction, and out of Pages."""
        for pattern in export_ignored():
            with self.subTest(pattern=pattern):
                self.assertFalse(pattern.startswith(("site", ".github")), pattern)


if __name__ == "__main__":
    unittest.main()
