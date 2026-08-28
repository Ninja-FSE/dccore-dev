"""local_config.py overrides reach the values that are derived from them.

config.py applies local_config.py at the very bottom, on purpose: an operator
overrides a tracked default without their deployment showing up as a diff.

That works for a plain setting, because nothing has read it yet. It does NOT
work for a setting COMPUTED from another one further up the file - by the time
the override lands, the derived value has already been built from the tracked
default and nothing recomputes it.

BROADCAST_SEARCH_CHANNEL was exactly that. Its own comment promised it
"defaults to the first entry of CHANNEL", and it did - to the first entry of
the SHIPPED default, not the operator's. So an operator running on their own
channel still had a dashboard broadcast search fire its @find into
#mp3passion, a real public channel they may not even be in:

    operator set CHANNEL      = #dccore-test
    BROADCAST_SEARCH_CHANNEL  = #mp3passion

webserver.start_broadcast_search() prefers BROADCAST_SEARCH_CHANNEL over
CHANNEL, so the stale value is the one that gets used.

The last test here is the general guard: it derives the rule from config.py's
own source rather than naming this one setting, so the NEXT value someone
derives above the override point fails instead of shipping.
"""

import ast
import contextlib
import importlib
import io
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import config  # noqa: E402

from tests.support import DCCoreTestCase  # noqa: E402


class LocalConfigOverrideTests(DCCoreTestCase):

    def _reload_with_local_config(self, body):
        """Reload config.py with `body` as the machine's local_config.py.

        Every test writes one - an empty file when it wants "no overrides" -
        so a developer who has a real local_config.py on their machine gets
        the same result as CI, which has none. Without that, "no overrides"
        would mean "whatever this particular box happens to be configured
        for", which is not a test.
        """
        tmp = tempfile.mkdtemp(prefix="dccore-localcfg-test-")

        # addCleanup is LIFO, so these run in REVERSE order. The final reload
        # has to happen with the temp directory already off sys.path and the
        # module already purged, or it just re-applies the override it is
        # supposed to be undoing - hence it is registered FIRST.
        self.addCleanup(importlib.reload, config)
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.addCleanup(lambda: tmp in sys.path and sys.path.remove(tmp))
        self.addCleanup(sys.modules.pop, "local_config", None)

        with open(os.path.join(tmp, "local_config.py"), "w", encoding="utf-8") as handle:
            handle.write(body)

        sys.path.insert(0, tmp)
        sys.modules.pop("local_config", None)
        with contextlib.redirect_stdout(io.StringIO()):
            importlib.reload(config)
        return config

    def test_a_channel_override_moves_the_broadcast_channel(self):
        """The defect. Overriding CHANNEL must move the derived value with it."""
        cfg = self._reload_with_local_config('CHANNEL = "#dccore-test"\n')

        self.assertEqual(cfg.CHANNEL, "#dccore-test")
        self.assertEqual(
            cfg.BROADCAST_SEARCH_CHANNEL, "#dccore-test",
            "the broadcast channel still points at the shipped default's first "
            "channel, so a broadcast search would @find into a channel the "
            "operator never configured")

    def test_the_first_entry_is_taken_when_several_are_overridden(self):
        """CHANNEL is a comma-separated list; only the first is broadcast to,
        deliberately - broadcasting into every joined channel multiplies the
        disruption for one search."""
        cfg = self._reload_with_local_config(
            'CHANNEL = "#first-one,#second-one,#third-one"\n')

        self.assertEqual(cfg.BROADCAST_SEARCH_CHANNEL, "#first-one")

    def test_an_explicit_broadcast_channel_still_wins(self):
        """Control. Deriving the value must not start overwriting an operator
        who set it on purpose - config.py's own comment offers that choice."""
        cfg = self._reload_with_local_config(
            'CHANNEL = "#dccore-test"\n'
            'BROADCAST_SEARCH_CHANNEL = "#somewhere-else"\n')

        self.assertEqual(cfg.BROADCAST_SEARCH_CHANNEL, "#somewhere-else")

    def test_with_no_overrides_it_derives_from_the_shipped_default(self):
        """Control. The behaviour without a local_config.py must not change."""
        cfg = self._reload_with_local_config("# no overrides\n")

        self.assertEqual(cfg.BROADCAST_SEARCH_CHANNEL,
                         cfg.CHANNEL.split(",")[0].strip())

    def test_a_blank_broadcast_channel_falls_back_rather_than_staying_blank(self):
        """An operator who sets it to "" gets the derived value, not an empty
        string - webserver.start_broadcast_search() returns 503 "No broadcast
        channel configured" on a blank one, which would be a confusing way to
        learn you typed an empty string."""
        cfg = self._reload_with_local_config(
            'CHANNEL = "#dccore-test"\n'
            'BROADCAST_SEARCH_CHANNEL = ""\n')

        self.assertEqual(cfg.BROADCAST_SEARCH_CHANNEL, "#dccore-test")


class NoSettingIsDerivedBeforeOverridesLand(unittest.TestCase):
    """The general rule, read out of config.py's own source.

    This deliberately does not name BROADCAST_SEARCH_CHANNEL. The bug was not
    that one setting was wrong, it was that the file's layout let any derived
    value be computed before overrides arrive - so the check is on the layout,
    and the next one someone adds fails here instead of shipping.
    """

    def _parse(self):
        with io.open(os.path.join(REPO_ROOT, "config.py"), encoding="utf-8") as handle:
            return ast.parse(handle.read())

    def _override_line(self, tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "local_config":
                return node.lineno
        return None

    def test_overrides_are_applied_somewhere_in_the_file(self):
        """Fixture invariant: if the import is gone or renamed, the test below
        would pass vacuously by having nothing to compare against."""
        self.assertIsNotNone(
            self._override_line(self._parse()),
            "config.py no longer imports local_config - the check below has "
            "no reference point and is silently passing")

    def test_no_derived_setting_is_computed_above_the_override_point(self):
        tree = self._parse()
        override_line = self._override_line(tree)

        assigned = {t.id: node.lineno
                    for node in tree.body if isinstance(node, ast.Assign)
                    for t in node.targets if isinstance(t, ast.Name)}

        offenders = []
        for node in tree.body:
            if not isinstance(node, ast.Assign) or node.lineno > override_line:
                continue
            sources = sorted({inner.id for inner in ast.walk(node.value)
                              if isinstance(inner, ast.Name) and inner.id in assigned})
            if not sources:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name):
                offenders.append(f"{target.id} (line {node.lineno}, from "
                                 f"{', '.join(sources)})")

        self.assertEqual(
            offenders, [],
            "these settings are computed from another setting ABOVE line "
            f"{override_line}, where local_config.py is applied, so they "
            "capture the tracked default and silently ignore the operator's "
            "override: " + "; ".join(offenders) + ". Move the computation to "
            "the DERIVED VALUES section at the end of config.py.")


if __name__ == "__main__":
    unittest.main()
