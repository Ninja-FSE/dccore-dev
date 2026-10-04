"""Regression tests for DCCore.

Run from the repository root with:

    python -m unittest discover -s tests -t .

Stdlib only, so this works on the production LXC and on Windows with nothing
installed. Each test names the defect it guards against; the suite exists so a
future change cannot silently reintroduce one of them.

NOTHING IN THIS SUITE MAY OPEN A REAL BROWSER.

webserver.start() opens the dashboard in the operator's browser, and two tests
in test_webserver.py drive start() directly to check the WEBUI_ENABLED and
WEBUI_HOST gates. Those tests already replace app.run() so that a broken gate
cannot bind a real socket - "a test must not be able to start a live listener
because the code it is testing broke" - but the browser call sits one line
above that, and was missed. Every full-suite run launched
http://127.0.0.1:8420/ on the developer's desktop, minutes apart, on a machine
where the bot itself was not running.

The guard lives here rather than in tests/support.py because only 90 of the
121 test files import that harness, and the class that tripped this is a plain
unittest.TestCase. Importing this package is the one thing every run does.

Calls are RECORDED rather than dropped, so a test can still assert that the
dashboard would have been opened - see BROWSER_OPENS.

EVERY DAEMON MODULE LIVES IN src/ now (#959) - one plain directory, not a
package, so the modules still only ever import each other by bare name,
never a package prefix, exactly as when they all sat at the repository
root. A test file's own "import irc" or "import defaults as config" needs
src/ on sys.path, and this is the one place that puts it there: importing
this package is the one thing every run does (see the browser guard's own
note above), so a test file never has to set this up itself - 345 of them
used to, identically, before this. One fact, one place, not repeated.
"""

import os as _os
import sys as _sys

_SRC = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "src")
if _SRC not in _sys.path:
    _sys.path.insert(0, _SRC)

import webbrowser as _webbrowser

# Every URL the suite tried to open, in order.
BROWSER_OPENS = []


def _record_browser_open(url, *_args, **_kwargs):
    BROWSER_OPENS.append(url)
    return True


_webbrowser.open = _record_browser_open
_webbrowser.open_new = _record_browser_open
_webbrowser.open_new_tab = _record_browser_open


# EVERY SHARD LISTENS ON PORTS OF ITS OWN (#1146).
#
# scripts/run_tests_in_parallel.py runs the suite in up to four processes at
# once. Every one of them would scan the same DCC ports: the shipped
# 55000-55010, and the ranges tests pin for themselves, one of which seven
# modules share. Two shards could then reach for one port at the same moment.
# On Linux both binds can succeed (SO_REUSEADDR, before either socket
# listens) and the second listen() fails; elsewhere the loser moves on to the
# next port, and a test that holds two ports, or counts the free ones, fails
# on a busy machine and passes on a quiet one.
#
# So the runner gives each shard a DCCORE_TEST_PORT_SHIFT of its own (1000,
# 2000, 3000 or 8000: see PORT_SHIFTS there), and every DCC port a test
# listens on moves by it:
#   - a range a test pins for itself is written as dcc_ports(start, end), and
#     moved here;
#   - the configured range, which every other test uses, is moved when this
#     package is imported, and again after each reload of defaults (a !rehash
#     test reloads it and gets the shipped numbers back).
# tests/test_every_shard_listens_on_ports_of_its_own.py proves the moved
# ranges of different shards never meet, and that none of them is the
# shipped 55000-55010 that check-setup's own children still probe. A plain
# run has no shift, and nothing below changes anything in it.
PORT_SHIFT = int(_os.environ.get("DCCORE_TEST_PORT_SHIFT") or 0)


def dcc_ports(start, end):
    """(start, end) of a DCC port range a test pins for itself, moved into this
    process's own window: unchanged in a plain run, moved by the shard's
    shift in a parallel one."""
    return start + PORT_SHIFT, end + PORT_SHIFT


def _move_the_configured_range(config):
    """Whatever range defaults resolved to, moved into this process's window."""
    config.DCC_PORT_START, config.DCC_PORT_END = dcc_ports(config.DCC_PORT_START,
                                                           config.DCC_PORT_END)


if PORT_SHIFT:
    import importlib as _importlib

    import defaults as _defaults

    _move_the_configured_range(_defaults)
    _plain_reload = _importlib.reload

    def _reload_into_the_shards_window(module):
        reloaded = _plain_reload(module)
        if getattr(reloaded, "__name__", None) == "defaults":
            _move_the_configured_range(reloaded)
        return reloaded

    _importlib.reload = _reload_into_the_shards_window
