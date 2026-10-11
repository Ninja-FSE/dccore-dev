# webserver.py - Web dashboard (Search / Queue / File Lists / cross-bot fetch).
"""A small, optional status page for the daemon.

FLASK IS OPTIONAL. The daemon has no external dependencies today, and CI never
installs Flask - so importing this module must never fail, and starting the
dashboard when Flask is missing must log and return, never crash the daemon.
That is why the import is guarded and why HAVE_FLASK exists.

The build_*_payload()/start_broadcast_search()/build_fetch_*() functions below
are pure: they read/write config (and, for search/file-lists, the master list
via list.py) and return plain dicts/lists or (status, dict) tuples. They never
import or touch flask, which is what lets tests/test_webserver.py exercise the
real data-shaping and mutation logic with plain unittest, no Flask install
required - keeping the "stdlib-only" property the rest of the test suite
relies on. create_app() and start() are the only things gated on HAVE_FLASK.

EVERY ROUTE REQUIRES A LOGIN, INCLUDING STATIC ASSETS. The dashboard used to
run with no authentication at all - a deliberate LAN-only decision, extended
on purpose even to /api/search/broadcast and /api/fetch/* despite those
routes mutating state (queuing an outbound IRC line, dialling an IP:port a
foreign bot supplies). That changed because WEBUI_HOST is no longer
guaranteed to stay LAN-only in practice: it shares one password with the DCC
CHAT admin console (config.ADMIN_PASSWORD_HASH, generated with `python
src/adminchat.py`) rather than a second credential to configure and forget about.
start() now refuses to run at all when that hash is unset - see the check
near the bottom of this file - so the dashboard is never reachable
unauthenticated, not even briefly on a fresh install.

The login route ("/login") is the ONLY exemption from the require_login()
before_request hook below; everything else, static files included, is behind
it. The session is a signed Flask cookie (app.secret_key, generated
fresh per process) - it does not survive a daemon restart, which is a
deliberate simplification: re-logging in after a restart costs nothing, and
it avoids a second secret to persist and protect. The cookie is not the
whole session, though: it carries an id the server must still hold (a
logout drops it) and a stamp of the password it was opened with (a password
change ends every session opened with the old one) - see create_app(). This is real
authentication, not the workaround kind the module docstring used to warn
against (an API key in a query string, a cookie nobody checks) - the password
is verified against ADMIN_PASSWORD_HASH via adminchat.verify_password() on
every login attempt, and nothing downstream trusts a request that has not
passed require_login(). Repeated failures from one address are temporarily
blocked (_note_bad_web_login()/_is_bad_web_ip(), same attempt-count and
block-duration policy as adminchat.py's own DCC CHAT console tracker, reused
from there directly) - in a POOL SEPARATE FROM adminchat.py's, on purpose:
since the password is shared, a shared block budget would let a web attacker
spend it down and lock the real operator out of the DCC console too. A POST
whose Origin (or Referer) names another site is refused unread and uncounted
(_login_origin_ok(), #609): on the loopback install a hostile page in the
operator's own browser arrives from the same address as the operator. An
attempt is claimed before its password is hashed (_reserve_web_login()), so
parallel requests from one address get no more guesses than the limit. A
request for a host name the dashboard does not answer to (DNS rebinding) is
refused before anything else (_dashboard_host_ok()), and no response may be
shown inside another page's frame (_refuse_framing()).

What plain-HTTP session/password transmission still cannot fix: an attacker
already sharing the network segment can read the password and the session
cookie off the wire. The WEBUI_HOST comment in config.py's warning against
untrusted networks is about exactly that, and still applies with auth in
place - a password gate stops a stranger from finding the dashboard and
using it, not from a wire-level eavesdropper on a network the operator should
not have put this host on to begin with.

Nothing here is added to commands.py's CORE_MODULES. A !rehash reload
re-executes this module's body, which would try to re-bind a live listening
socket out from under app.run() - the same reasoning that already excludes
adminchat.py. Route handlers read config fresh via getattr() on every request
instead, so a rehash's new values (e.g. a changed WEBUI_* setting takes effect
only on the next daemon restart, but MAX_DCC_SLOTS, the queue, etc. are always
current) are visible without needing a reload of this module.
"""

import collections
import os
import sys
import threading
import time

import adminchat
import defaults as config
import platform_compat
import runtime


try:
    from flask import (Flask, Response, g, jsonify, redirect, request, send_file,
                       send_from_directory, session)
    HAVE_FLASK = True
except ImportError:
    HAVE_FLASK = False


# The shared limits and helpers live in web_common.py since #1311, the
# first step of splitting this file. Imported back by name, so
# webserver.<name> still answers for every one of them.
from web_common import (  # noqa: F401
    FETCH_ENQUEUE_MAX_ITEMS,
    FILELISTS_DEFAULT_PAGE_SIZE,
    FILELISTS_MAX_PAGE_SIZE,
    FOLDER_BROWSE_MAX_ENTRIES,
    IRC_LINE_FIELD_MAX_LEN,
    MAX_FOLDER_PATH_LEN,
    MAX_SERVED_FOLDERS,
    WEBUI_MAX_SEARCH_RESULTS,
    WEB_DASHBOARD_SOURCE,
    WEB_DIR,
    json_object,
    parse_pagination_params,
    reject_if_unsafe_for_irc_line,
    split_list_search_words,
)


# ==========================================================================
# Pure data-building functions - no Flask, unit tested directly.
# ==========================================================================

def count_rar_album_folders(name=None):
    """How many album folders the RAR list offers, or None if there is no list.

    The file update_list.py writes opens with three lines of explanation and
    then one "!<nick> !rar <path>" row per folder, so the rows are exactly the
    lines starting with "!". Counting those rather than the file's length is
    what keeps the header out of the total.

    None rather than 0 when there is no list at all: a bot whose first list has
    not been built yet has an unknown album count, and zero is a different
    claim - it would read on the page as "this bot offers no albums".

    `name` picks a served list (#952): a list's files live in its own
    directory (list.list_dir()), and without a name this reads the primary's,
    which is where every install's RAR list was before there were several.
    """
    import io

    if name is None:
        directory = getattr(config, "LOCAL_LIST_DIR", "./lists")
    else:
        import list as list_mod
        directory = list_mod.list_dir(name)
    prefix = f"{getattr(config, 'LIST_BASE_NAME', 'DCCore')}-RAR-"
    try:
        names = sorted(name for name in os.listdir(directory)
                       if name.startswith(prefix) and name.endswith(".txt"))
    except OSError:
        return None
    if not names:
        return None

    path = os.path.abspath(os.path.join(directory, names[-1]))
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size, st.st_ino)
        cached = _rar_counts.get(path)
        if cached is not None and cached[0] == key:
            return cached[1]
        with io.open(path, encoding="utf-8", errors="ignore") as handle:
            count = sum(1 for line in handle if line.startswith("!"))
    except OSError:
        return None
    _rar_counts[path] = (key, count)
    return count


# The RAR list's row count by its path (#1123): it changes only when the list is
# rebuilt, and the Stats page counted every row of it on every poll - a quarter
# of a second at 420k rows, per served list. Keyed per path, one entry each, so
# a bot serving several lists keeps them all rather than recounting whichever
# was not asked last. A rebuild publishes with os.replace, which changes the
# key. webserver.py is not reloaded by !rehash.
_rar_counts = {}


def _format_library_size(raw_bytes):
    """A byte total the way a list's own size file writes it - two decimals,
    "1.85TB" - so a total summed from several lists reads like the figures it
    is made of and not in a second style (#952)."""
    size = float(raw_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.2f}{unit}"
        size /= 1024.0
    return f"{size:.2f}PB"


def build_library_payload():
    """The Library block of the Stats page: totals across EVERY served list,
    and one row per list (#952).

    It used to read the primary list alone - no name given, so the primary's
    files, its side files for the size, and LOCAL_LIST_DIR's RAR list - and on
    a bot serving music and film lists the page showed the music list's
    numbers as though they were the library. The channel adverts already ask
    per list, so the page and the advert disagreed.

    The top-level keys are the ones the page has always had (files, size,
    raw_bytes, list_date, rar_folders) and are now the totals; a single-list
    install therefore reads exactly as before. `lists` holds each list's own
    figures, in the operator's order.

    Every list is read in its own guard, the way every source in this module
    is: one list whose side file is unreadable costs its own row and nothing
    else. The primary is asked for with NO name, not its own - that is the
    path the page has always taken, and a single-list install has no reason to
    be routed differently.

    A folder that belongs to two lists is counted in both, and the totals do
    not de-duplicate; the rows are there so that can be seen.
    """
    import library
    import list as list_mod

    try:
        entries = [(str(entry.name), bool(entry.primary)) for entry in library.lists()]
    except Exception:
        entries = []
    if not entries:
        entries = [("", True)]

    rows = []
    newest_row, newest_when = None, None
    for name, primary in entries:
        row = {"name": name, "primary": primary, "files": 0, "size": None,
               "raw_bytes": 0, "list_date": None, "rar_folders": None}
        asked = None if primary else name
        try:
            files, list_date, size, raw_bytes = list_mod.get_file_count_date_size_and_raw_bytes(asked)
            row.update({"files": int(files or 0), "list_date": list_date or None,
                        "size": size or None, "raw_bytes": int(raw_bytes or 0)})
        except Exception:
            pass
        try:
            row["rar_folders"] = count_rar_album_folders(asked)
        except Exception:
            pass
        try:
            latest = list_mod.find_latest_list(asked)
            when = os.path.getmtime(latest) if latest else None
        except Exception:
            when = None
        if when is not None and (newest_when is None or when > newest_when):
            newest_row, newest_when = row, when
        rows.append(row)

    primary_row = next((row for row in rows if row["primary"]), rows[0])
    total_bytes = sum(row["raw_bytes"] for row in rows)
    albums = [row["rar_folders"] for row in rows if row["rar_folders"] is not None]

    if len(rows) == 1:
        size = primary_row["size"]
    else:
        size = _format_library_size(total_bytes) if total_bytes else primary_row["size"]

    return {
        "files": sum(row["files"] for row in rows),
        "size": size,
        "raw_bytes": total_bytes,
        # The newest build among the lists; when none has been built at all,
        # what the primary says (a bot with no list yet says so).
        "list_date": (newest_row or primary_row)["list_date"],
        # None only when NO list has a RAR list: a total of the ones that do
        # is a true count of the albums on offer, while a list with no RAR list
        # simply has none to add.
        "rar_folders": sum(albums) if albums else None,
        "lists": rows,
    }


# The ceilings the import refuses beyond. A stray digit from a hand-edited
# text file is far likelier than a genuine value up here, and importing one
# silently is worse than refusing it - the operator can retype a number, but
# they cannot tell that a total is wrong once it looks plausible.
#
# 2^63 is where a 64-bit counter stops meaning anything; a byte total past a
# petabyte and a speed record past 10 GB/s are both past any real DCC link by
# orders of magnitude, and both are what a mis-parsed field looks like.
MAX_IMPORT_FILES = 2 ** 63 - 1
MAX_IMPORT_BYTES = 1 << 50           # 1 PiB
MAX_IMPORT_SPEED = 10 * 1000 ** 3    # 10 GB/s

_IMPORT_LIMITS = {
    "total_files": (MAX_IMPORT_FILES, "files sent"),
    "total_bytes": (MAX_IMPORT_BYTES, "bytes sent"),
    "speed_record": (MAX_IMPORT_SPEED, "speed record"),
    # KeepTrack's (#1062), into the transfer record's imported totals.
    "received_files": (MAX_IMPORT_FILES, "files received"),
    "received_bytes": (MAX_IMPORT_BYTES, "bytes received"),
}
_RECEIVED = ("received_files", "received_bytes")


def current_importable_stats():
    """What the three importable figures are right now.

    Read through the same accessors the Stats page uses, so the "before" column
    of the preview cannot disagree with the page the operator is looking at.
    """
    import db

    row = db.load_advanced_stats() or [0] * 7
    try:
        total_files = int(row[0])
    except (IndexError, TypeError, ValueError):
        total_files = 0
    try:
        total_bytes = int(row[1])
    except (IndexError, TypeError, ValueError):
        total_bytes = 0
    try:
        record = int(db.get_speed_record() or 0)
    except (TypeError, ValueError):
        record = 0
    # What an earlier KeepTrack import put in the record (#1062) - the figure
    # a new one replaces, so before and after compare like with like.
    received = {}
    try:
        import transfer_log
        received = transfer_log.imported_totals("keeptrack").get(transfer_log.RECEIVED) or {}
    except Exception as err:
        print(f"[STATS IMPORT] The imported received totals are unavailable: {err}")
    return {"total_files": total_files, "total_bytes": total_bytes,
            "speed_record": record,
            "received_files": int(received.get("files") or 0),
            "received_bytes": int(received.get("bytes") or 0)}


def validate_import_values(raw):
    """(clean, errors) for the three figures an import may carry.

    Every field optional - an operator missing an add-on imports what they
    have - but a field that IS present must be a whole number in range.
    Checked HERE and not only in the page: the page is convenience, this is
    the boundary, and everything arriving is from a text file somebody may
    have hand-edited.
    """
    clean = {}
    errors = []
    for name, (ceiling, label) in _IMPORT_LIMITS.items():
        if name not in (raw or {}):
            continue
        value = raw[name]
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            errors.append(f"{label}: expected a whole number.")
            continue
        try:
            # str() first so "45902" works and 45902.0 does not silently
            # truncate a fraction somebody meant to be there.
            number = int(str(value).strip())
        except (TypeError, ValueError):
            errors.append(f"{label}: {value!r} is not a whole number.")
            continue
        if number < 0:
            errors.append(f"{label}: cannot be negative.")
            continue
        if number > ceiling:
            errors.append(f"{label}: {number:,} is beyond anything real "
                          f"(the limit is {ceiling:,}). A stray digit in a "
                          f"hand-edited file looks exactly like this.")
            continue
        clean[name] = number
    # When KeepTrack began counting, as the import found it (#1062). A date
    # or nothing: it is written into the record as it stands.
    if (raw or {}).get("received_since") not in (None, ""):
        since = str(raw["received_since"]).strip()
        import datetime
        try:
            datetime.date.fromisoformat(since)
            clean["received_since"] = since
        except ValueError:
            errors.append(f"received since: {since!r} is not a date (YYYY-MM-DD).")
    # A date with no received figure is not a figure to write. Kept when the
    # figure it dates was refused, it reached the apply on its own, nothing
    # wrote it, and the import answered 500 "could not be written" although
    # everything else had been (#1062 review).
    if not any(name in clean for name in _RECEIVED):
        clean.pop("received_since", None)
    return clean, errors


def build_stats_import_preview(text):
    """POST /api/stats/import/preview: what a vars.ini would do, changing nothing.

    Separate from the write for the reason the issue gives: on a fresh install
    nobody reads the sentence about overwriting, and on a used one it is the
    only thing that matters. The page shows before and after side by side and
    the operator confirms.
    """
    import omenserve_import

    found = omenserve_import.read_install(text)
    clean, errors = validate_import_values(found.get("values") or {})
    notes = list(found.get("notes", []))
    if any(name in clean for name in _RECEIVED):
        import transfer_log
        if not transfer_log._path():
            # Nowhere to put them: the record is turned off (#1068).
            for name in _RECEIVED + ("received_since",):
                clean.pop(name, None)
            notes.append("The received totals are not imported: the transfer record is off "
                         "(Settings > Advanced, TRANSFER_LOG_FILE is empty).")
    # Every source offered as a choice is checked as the default one is (#1062
    # review): only `values` was, so the other could show -5 in the preview and
    # then have the whole import refused when picked, here or in configure.py.
    # The default's faults are in `errors` already.
    sources = []
    for entry in found.get("sent_sources", []):
        figures = {k: v for k, v in entry.items() if k not in ("name", "label")}
        good, bad = validate_import_values(figures)
        sources.append(dict(good, name=entry["name"], label=entry["label"]))
        if entry["name"] != found.get("sent_source"):
            errors.extend(f"{entry['label']}: {fault}" for fault in bad)
    return {
        "rows": found.get("rows", []),
        "notes": notes + errors,
        "current": current_importable_stats(),
        "values": clean,
        # Where the sent totals can come from (#1062); the page offers a
        # choice when there are two, and posts back the one picked.
        "sent_sources": sources,
        "sent_source": found.get("sent_source"),
        # OVERWRITTEN, NOT COMBINED, and said where the page can put it in
        # front of the operator rather than buried in prose they will not read
        # on a fresh install and cannot miss on a used one.
        "replaces": {name: value for name, value in clean.items()
                     if current_importable_stats().get(name)},
    }


def apply_stats_import(raw):
    """(http_status, payload) for POST /api/stats/import.

    Writes through db.set_lifetime_totals() and db.save_speed_record(),
    which take the disk lock and write atomically - a caller, not a second
    implementation of either.

    Returns the before and after, so the page reports what actually happened
    rather than what it asked for.
    """
    import db

    clean, errors = validate_import_values(raw)
    if errors:
        return 400, {"error": errors[0], "errors": errors}
    if not clean:
        return 400, {"error": "Nothing to import - no recognised figure was "
                              "supplied."}

    before = current_importable_stats()

    written = None
    if "total_files" in clean or "total_bytes" in clean:
        # The 7-column row read-modify-written as a whole, under one lock
        # (#690): the day columns and the date belong to the daemon's own
        # rotation and are not this feature's to touch, and a transfer that
        # completes while this runs must not lose its count to a stale row.
        written = db.set_lifetime_totals(total_files=clean.get("total_files"),
                                         total_bytes=clean.get("total_bytes"))

    if "speed_record" in clean:
        db.save_speed_record(clean["speed_record"])

    # KeepTrack's received totals (#1062): into the transfer record's imported
    # totals, replacing an earlier KeepTrack import's. A half given keeps the
    # other half as it is.
    received_written = None
    if any(name in clean for name in _RECEIVED):
        import transfer_log
        received_written = transfer_log.import_totals(
            "keeptrack", transfer_log.RECEIVED,
            clean.get("received_files", before["received_files"]),
            clean.get("received_bytes", before["received_bytes"]),
            since=clean.get("received_since"))

    # WHAT ACTUALLY LANDED, compared against what was asked for. Both writers
    # swallow their own errors and return None - correct for them, since a
    # failed stats write must not take the daemon down - which meant this
    # returned 200 and "imported: [...]" for figures that never reached the
    # disk. The operator would have been told their history came across and
    # found half of it, with nothing to say which half.
    after = current_importable_stats()

    def landed_as_asked(name):
        # The totals are judged by the row the locked write produced, not by
        # a later read (#690): a transfer completing right after the import
        # legitimately moves them on, and that is not a failed write.
        if name == "total_files":
            return written is not None and written[0] == clean[name]
        if name == "total_bytes":
            return written is not None and written[1] == clean[name]
        if name == "received_since":
            return bool(received_written)
        return after.get(name) == clean[name]

    landed = [name for name in sorted(clean) if landed_as_asked(name)]
    missing = [name for name in sorted(clean) if name not in landed]
    if missing:
        return 500, {
            "error": "Some figures could not be written: "
                     + ", ".join(missing)
                     + ". Check the daemon log and the data directory.",
            "before": before, "after": after, "imported": landed,
            "failed": missing,
        }

    return 200, {"before": before, "after": after, "imported": landed}


# KeepTrack's per-nick history (#1064). A KTData.txt of a long-running bot is
# a few hundred KB. The app already refuses a body over 8 MB
# (MAX_CONTENT_LENGTH); this says the same about the text itself.
MAX_KTDATA_CHARS = 8 * 1024 * 1024


def _read_ktdata_text(text):
    """(parsed, error) for an uploaded KTData.txt's text."""
    import omenserve_import
    text = str(text or "")
    if len(text) > MAX_KTDATA_CHARS:
        return None, "That file is far larger than a KTData.txt; nothing was read."
    # Not MAX_IMPORT_FILES (2**63-1): a nick's figure is summed with its
    # recorded transfers in SQL, and at that size the sum overflows (#1064
    # review). read_ktdata()'s own limits, which leave that room.
    return omenserve_import.read_ktdata(text, max_bytes=MAX_IMPORT_BYTES), None


def build_ktdata_preview(text):
    """POST /api/stats/import-ktdata/preview: what a KTData.txt would add,
    writing nothing - the nicks per direction, their totals, the top ten, the
    lines skipped and why, and whether an earlier import would be replaced."""
    import transfer_log
    parsed, error = _read_ktdata_text(text)
    if error:
        return 400, {"error": error}
    if not transfer_log._path():
        return 409, {"error": "The transfer record is off (Settings > Advanced, TRANSFER_LOG_FILE "
                              "is empty), so there is nowhere to put the per-nick history."}
    rows = parsed["rows"]
    figures = {}
    for direction in (transfer_log.SENT, transfer_log.RECEIVED):
        mine = [r for r in rows if r[0] == direction]
        top = sorted(mine, key=lambda r: (-r[2], -r[3], r[1]))[:10]
        figures[direction] = {
            "nicks": len(mine),
            "files": sum(r[2] for r in mine),
            "bytes": sum(r[3] for r in mine),
            "top": [{"nick": r[1], "files": r[2], "bytes": r[3]} for r in top],
        }
    return 200, {"lines": parsed["lines"], "skipped": parsed["skipped"], "figures": figures,
                 "replaces": transfer_log.imported_nicks("keeptrack")}


def apply_ktdata_import(text):
    """POST /api/stats/import-ktdata: read the file again - the preview's
    answer is not trusted back - and write its per-nick totals into the
    transfer record, replacing an earlier KeepTrack per-nick import."""
    import transfer_log
    status, preview = build_ktdata_preview(text)
    if status != 200:
        return status, preview
    parsed, _error = _read_ktdata_text(text)
    if not parsed["rows"]:
        return 400, {"error": "Nothing in that file could be imported."}
    since = (transfer_log.imported_totals("keeptrack").get(transfer_log.RECEIVED) or {}).get("since")
    written = transfer_log.import_nicks("keeptrack", parsed["rows"], since=since)
    if written is None:
        return 500, {"error": "The per-nick history could not be written. Check the daemon log."}
    return 200, {"imported": written, "nicks": transfer_log.imported_nicks("keeptrack")}


# THE TRANSFER RECORD ON THE STATS PAGE (#1102). The record (#1068) has been
# written since #1069, but nothing read it. A period is one of these; "all"
# is the only one the imported KeepTrack figures count in, as everywhere in
# transfer_log.
RECORD_PERIODS = {"day": 86400, "week": 7 * 86400, "month": 30 * 86400, "all": None}


def _record_since(period):
    """(since, error) for a period name: a Unix time, or None for all of it."""
    if period not in RECORD_PERIODS:
        return None, f"Unknown period {str(period)[:20]!r}: use day, week, month or all."
    seconds = RECORD_PERIODS[period]
    return (int(time.time()) - seconds if seconds else None), None


def _record_off():
    import transfer_log
    if transfer_log._path():
        return None
    return {"enabled": False,
            "error": "The transfer record is off (Settings > Advanced, TRANSFER_LOG_FILE is empty)."}


def _wait_text(seconds):
    """A queue wait as the page shows it: format_uptime() reads anything under
    a minute as "0 Min", which for a wait is the wrong answer."""
    if seconds is None:
        return ""
    import adminchat
    return f"{int(seconds)} Sec" if seconds < 60 else adminchat.format_uptime(seconds)


def _nick_rows(rows):
    import stats_mgr
    return [{"nick": nick, "files": files, "bytes": size, "bytes_text": stats_mgr.format_size_human(size)}
            for nick, files, size in rows]


def _lifetime_sent():
    """(files, bytes) the bot has sent over its life, from stats.txt."""
    import db
    try:
        row = db.load_advanced_stats_rolled()
        return int(str(row[0]).strip()), int(str(row[1]).strip())
    except Exception:
        return 0, 0


def _record_tops(since):
    """The most-sent files and the most-sent album folders for a period, each as
    [{name, count}]. A period comes from the record. All time comes from the
    count file, which holds everything the bot has ever sent (the record only
    began with #1069); the record answers only when that file has nothing."""
    import db
    import transfer_log
    out = []
    for kind in (transfer_log.KIND_FILE, transfer_log.KIND_ALBUM):
        rows = []
        if since is None:
            try:
                rows = [{"name": row["name"], "count": row["count"]} for row in db.top_downloads(limit=10, kind=kind)]
            except Exception:
                rows = []
        if not rows:
            rows = [{"name": name, "count": count} for name, count in transfer_log.top_files(10, since, kind)]
        out.append(rows)
    return out[0], out[1]


def _record_outcomes(since):
    """How the period's transfers ended (#1203), as the Stats page's outcomes
    table shows them: sent files, albums and lists, and everything received.

    `failed` includes the albums whose folder could not be packed, which
    `pack_failed` also gives on its own. The success rate is completed out of
    completed and failed: a cancelled transfer was stopped by the operator,
    the user or the bot, not lost by the network, and counting it would
    blame the network for it. Rounded down, so 100% means nothing failed.
    "since" is set when the counting starts later than the period does,
    because failures were not written before then."""
    import transfer_log
    found = transfer_log.outcomes(since)
    lines = []
    for direction, kind in ((transfer_log.SENT, transfer_log.KIND_FILE),
                            (transfer_log.SENT, transfer_log.KIND_ALBUM),
                            (transfer_log.SENT, transfer_log.KIND_LIST),
                            (transfer_log.RECEIVED, None)):
        counts = {}
        for (row_direction, row_kind), by_status in found["rows"].items():
            if row_direction == direction and (kind is None or row_kind == kind):
                for status, count in by_status.items():
                    counts[status] = counts.get(status, 0) + count
        completed = counts.get(transfer_log.STATUS_COMPLETED, 0)
        pack_failed = counts.get(transfer_log.STATUS_PACK_FAILED, 0)
        failed = counts.get(transfer_log.STATUS_FAILED, 0) + pack_failed
        cancelled = counts.get(transfer_log.STATUS_CANCELLED, 0)
        decided = completed + failed
        rate = (completed * 100) // decided if decided else None
        lines.append({"direction": direction, "kind": kind or "all",
                      "attempts": completed + failed + cancelled, "completed": completed,
                      "failed": failed, "pack_failed": pack_failed, "cancelled": cancelled,
                      "success_rate": rate, "success_text": f"{rate}%" if rate is not None else ""})
    return {"since": found["since"], "rows": lines}


def build_record_payload(period="all"):
    """GET /api/stats/record?period=: the record's figures for a period - the
    totals, the most-sent files, the nicks sent to and received from most, and
    how the transfers ended (#1203). Every figure comes raw and as the page
    shows it, as in build_stats_payload."""
    import stats_mgr
    import transfer_log
    since, error = _record_since(period)
    if error:
        return 400, {"error": error}
    off = _record_off()
    if off:
        return 200, dict(off, period=period)
    figures = transfer_log.summary(since)
    top_files, top_albums = _record_tops(since)
    if since is None:
        # All time is never less than the lifetime counter: the record only
        # began with #1069, and what the bot sent before is in stats.txt only.
        files, size = _lifetime_sent()
        figures["files_sent"] = max(figures["files_sent"], files)
        figures["bytes_sent"] = max(figures["bytes_sent"], size)
    figures.update(
        bytes_sent_text=stats_mgr.format_size_human(figures["bytes_sent"]),
        bytes_received_text=stats_mgr.format_size_human(figures["bytes_received"]),
        top_speed_text=stats_mgr.format_speed(figures["top_speed"]),
        average_speed_text=stats_mgr.format_speed(figures["average_speed"]),
        queue_wait_text=_wait_text(figures["queue_wait_seconds"]))
    return 200, {
        "enabled": True,
        "period": period,
        "since": since,
        "summary": figures,
        "top_files": top_files,
        "top_albums": top_albums,
        "albums_enabled": bool(getattr(config, "RAR_ENABLED", True)),
        "top_sent": _nick_rows(transfer_log.top_nicks(transfer_log.SENT, 10, since)),
        "top_received": _nick_rows(transfer_log.top_nicks(transfer_log.RECEIVED, 10, since)),
        "outcomes": _record_outcomes(since),
        # Said on the page: all time holds figures from before the record began -
        # the bot's own totals, a nick's, or both (#1102 review: a per-nick import
        # alone left the note hidden while the tables counted it).
        "includes_imported": since is None and transfer_log.has_imported(),
    }


def build_record_nick_payload(nick, period="all"):
    """GET /api/stats/record/nick?nick=&period=: what one nick has had from
    this bot and what this bot has had from it."""
    import stats_mgr
    import transfer_log
    since, error = _record_since(period)
    if error:
        return 400, {"error": error}
    off = _record_off()
    if off:
        return 409, off
    wanted = transfer_log._nick(nick)
    if wanted is None:
        return 400, {"error": "Type a nick to look up."}
    figures = transfer_log.nick_summary(wanted, since)
    figures.update(bytes_sent_text=stats_mgr.format_size_human(figures["bytes_sent"]),
                   bytes_received_text=stats_mgr.format_size_human(figures["bytes_received"]))
    return 200, {"nick": wanted, "period": period, "figures": figures,
                 "found": any(figures[key] for key in ("files_sent", "lists_sent", "files_received"))}


def apply_record_forget(body):
    """POST /api/stats/record/forget: {"nick": "..."} takes one nick out of
    the record, imported figures included; {"everyone": true} empties it.
    Two separate words rather than an empty nick meaning everything, so a
    blank box can never wipe the record."""
    import transfer_log
    off = _record_off()
    if off:
        return 409, off
    try:
        if body.get("everyone") is True:
            removed = transfer_log.forget_all()
            print(f"[TRANSFER-LOG] The record was emptied from the dashboard ({removed} row(s)).")
            return 200, {"removed": removed, "everyone": True}
        nick = transfer_log._nick(body.get("nick"))
        if nick is None:
            return 400, {"error": "Say which nick to forget."}
        removed = transfer_log.forget_nick(nick)
    except Exception as err:
        return 500, {"error": f"The record could not be changed: {err}"}
    # The nick is not printed: the log would keep what the record just let go.
    print(f"[TRANSFER-LOG] A nick was forgotten from the dashboard ({removed} row(s)).")
    return 200, {"removed": removed, "nick": nick}


# A cell a spreadsheet would read as a formula. A nick or a file name is
# somebody else's text, and "=HYPERLINK(...)" in one runs when the export is
# opened; a leading apostrophe makes it text again.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value):
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def record_csv_lines(since=None):
    """The CSV export, one line at a time (#1102): a header, then every row
    from `since` on. The time is local and readable; the rest as stored."""
    import csv
    import io
    import transfer_log
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")

    def line(cells):
        buffer.seek(0)
        buffer.truncate()
        writer.writerow(cells)
        return buffer.getvalue()

    yield line(("time",) + transfer_log.EXPORT_COLUMNS[1:])
    for row in transfer_log.iter_rows(since):
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row[0]))
        yield line([stamp] + [_csv_cell(value) for value in row[1:]])


STATS_PARTS = ("transfer", "sent", "library", "top")


def build_stats_payload(parts=None):
    """Everything the Stats view shows, in one request - or, with `parts`,
    only those (#1123): Live Transfers polls this every few seconds and shows
    the transfer figures alone, while "top" and "library" read and rank whole
    files. None is every part.

    Every figure comes twice: the raw number, and the daemon's own rendering of
    it. The raw one is for anything that is not this page - a script, a future
    CTCP reply - which should not have to parse "1.21TB" back into an integer.
    The rendered one uses stats_mgr.format_speed(), format_size_human() and
    adminchat.format_uptime(), which are what the channel advert and the admin
    console already print, so the dashboard cannot disagree with the advert
    about how the same number reads.

    Nothing here takes a lock, matching build_queue_payload(): a shallow copy of
    the live containers is good enough for a status display and cannot deadlock
    against the daemon's own queue processing.

    Every source is guarded individually. This is a read-only status page and a
    missing stats file, an unbuilt list or a permissions error on lists/ must
    cost the tile that needs it and nothing else - a dashboard that 500s
    because one counter is unreadable is worse than one showing a gap.
    """
    # Imported here, not at module scope. tests/test_import_graph.py pins that
    # importing webserver.py pulls in none of the daemon, so that
    # tests/test_webserver.py can exercise every route without one running -
    # and db/list/stats_mgr are named in that test explicitly. Same reason the
    # File Lists payload imports `list` inside its own function.
    import db
    import stats_mgr

    wanted = set(STATS_PARTS if parts is None else parts)
    active = list(getattr(config, "active_transfers", []))
    queue = dict(getattr(config, "dcc_queue", {}))

    try:
        speed_now = int(stats_mgr.live_speed())
    except Exception:
        speed_now = 0

    try:
        record = int(db.get_speed_record())
    except Exception:
        record = 0

    try:
        uptime = int(stats_mgr.get_uptime_seconds())
    except Exception:
        uptime = 0

    # When the first busy slot is likely to free up (#1207), from the same
    # copy of active_transfers the Sending card counts, so the two cannot
    # disagree about whether a slot is free. "now", whole minutes, or None.
    try:
        next_slot = stats_mgr.next_slot_estimate(transfers=active)
    except Exception:
        next_slot = None

    # The 7-column row: total files, total bytes, yesterday's pair, today's
    # pair, and the date the day last rolled over. Read through
    # load_advanced_stats_rolled(), which rolls a COPY, so a bot that has sent
    # nothing since midnight does not show yesterday's figures labelled Today -
    # and so answering a GET does not write to disk.
    sent = {"total_files": 0, "total_bytes": 0, "today_files": 0,
            "today_bytes": 0, "yesterday_files": 0, "yesterday_bytes": 0}
    try:
        if "sent" not in wanted:
            raise LookupError("not asked for")
        row = db.load_advanced_stats_rolled()
        names = ("total_files", "total_bytes", "yesterday_files",
                 "yesterday_bytes", "today_files", "today_bytes")
        for index, name in enumerate(names):
            try:
                sent[name] = int(str(row[index]).strip())
            except (IndexError, TypeError, ValueError):
                pass
    except Exception:
        pass

    # Every served list, not the primary alone (#952) - see the function.
    try:
        if "library" not in wanted:
            raise LookupError("not asked for")
        library = build_library_payload()
    except Exception:
        library = {"files": 0, "size": None, "raw_bytes": 0, "list_date": None,
                   "rar_folders": None, "lists": []}

    for name in ("total", "today", "yesterday"):
        sent[name + "_text"] = stats_mgr.format_size_human(sent[name + "_bytes"])

    # Counted together, reported apart. A 700 MB album and a 4 MB track are not
    # comparable, so one merged table would rank by whichever kind this bot
    # happens to send more of - a fact about the library, not about demand.
    #
    # albums_enabled is config.RAR_ENABLED (#140). With folder packing off no
    # album can ever be sent, so the page says that instead of showing a table
    # that would stay empty for ever with no explanation. Any counts from
    # before it was switched off are still returned - they are history, and
    # deciding they never happened would be the wrong kind of tidy.
    top = {"files": [], "albums": [],
           "albums_enabled": bool(getattr(config, "RAR_ENABLED", True))}
    if "top" in wanted:
        try:
            top["files"] = db.top_downloads(limit=10, kind="file")
            top["albums"] = db.top_downloads(limit=10, kind="album")
        except Exception:
            pass

    payload = {
        "top": top,
        "transfer": {
            "speed_now": speed_now,
            "speed_now_text": stats_mgr.format_speed(speed_now),
            "record": record,
            "record_text": stats_mgr.format_speed(record),
            "sending": len(active),
            "slots": int(getattr(config, "MAX_DCC_SLOTS", 0) or 0),
            "next_slot": next_slot,
            "next_slot_text": stats_mgr.format_next_slot(next_slot),
            "queued_files": sum(len(entries) for entries in queue.values()),
            "queued_users": len(queue),
            "uptime_seconds": uptime,
            "uptime_text": adminchat.format_uptime(uptime),
        },
        "sent": sent,
        "library": library,
        "version": str(getattr(config, "SCRIPT_VERSION", "")),
    }
    for part in STATS_PARTS:
        if part not in wanted:
            del payload[part]
    return payload


def _security_seconds_left(user_key):
    """How long a timed ignore or ban on this nick has to run, 0 for none -
    what turns a Queue row's Ignore button into "Ignored ... Lift" (#1206)."""
    import security
    return security.ban_seconds_left(user_key)


def build_queue_payload(user=None):
    """The Queue view's data.

    With no `user`, one summary row per queued user (their status, a preview
    of the next file, how many are waiting, the FULL list of what is waiting,
    and - for whoever is sending - the file in flight and its progress) -
    what the Queue table shows. With `user`, the full file list queued for
    that one user instead - what a click-through or `?user=<nick>` on
    /api/queue returns.

    "preview"/"count"/"files" describe the QUEUE: config.dcc_queue's rows.
    A send that found a free slot at once never has a row; a queued file
    keeps its row while it is sent, until the send settles it (#1245). The
    progress lives in config.active_transfers either way, which is why a
    sending user's progress needs its own fields rather than being folded
    into the queue ones -
    an operator asked to see the whole queue, not just its head, and a
    progress bar for the transfer actually running, not for whatever is
    queued behind it.

    "file_ids" is dcc.queue_row_id() of each of "files", in step: what a move
    or a removal sends back to name the file it means (#1245).

    No lock is taken, matching adminchat.py's _cmd_queue/_cmd_status idiom: a
    shallow dict()/list() copy of the live containers, read without a lock,
    is good enough for a status display and cannot deadlock against the
    daemon's own queue processing.
    """
    queue = dict(getattr(config, "dcc_queue", {}))
    frozen = dict(getattr(config, "frozen_queues", {}))
    active = list(getattr(config, "active_transfers", []))
    sending_users = {str(tx.get("user", "")).lower() for tx in active}

    active_by_user = {}
    for tx in active:
        active_by_user.setdefault(str(tx.get("user", "")).lower(), tx)

    # The user a folder is being packed for (#1202): without it their row read
    # "queued" for as long as rar ran. The folder's name, never its path.
    import dcc
    pack = None
    try:
        pack = dcc.pack_status()
    except Exception as err:
        print(f"[WEBSERVER] The running pack could not be read for the queue: {err}")
    packing_user = str(pack["user"]).lower() if pack else ""

    def _progress_fields(user_key):
        # None, not 0, when there is nothing sending or the size is not
        # known yet (dcc.start_dcc_send() writes "size" onto the row the
        # moment it has read the file's real size off disk - a fresh
        # dispatch's row does not have it for the brief window before that).
        # The dashboard tells "no bar to draw" apart from "0% so far" by
        # this, not by a size of 0, which a genuinely empty file would also
        # report honestly.
        tx = active_by_user.get(user_key)
        if tx is None:
            return None, None, None
        return tx.get("file"), tx.get("bytes_sent"), tx.get("size")

    if user:
        user_key = str(user).strip().lower()
        entries = queue.get(user_key, [])
        if user_key in sending_users:
            status = "sending"
        elif user_key == packing_user:
            status = "packing"
        elif user_key in frozen:
            status = "frozen"
        elif entries:
            status = "queued"
        else:
            status = "empty"
        files = [e.get("file", "?") if isinstance(e, dict) else str(e) for e in entries]
        current_file, bytes_sent, size = _progress_fields(user_key)
        result = {"user": user_key, "status": status, "count": len(entries), "files": files,
                  "file_ids": [dcc.queue_row_id(e) for e in entries],
                  "current_file": current_file, "bytes_sent": bytes_sent, "size": size,
                  "ignored_seconds": _security_seconds_left(user_key)}
        if status == "packing":
            result["pack"] = _pack_fields(pack)
        return result

    # #220: a user sent with a free slot and nothing already queued never
    # enters dcc_queue at all - dcc.py's admission check appends straight to
    # active_transfers and returns (see start_dcc_send()). Iterating only
    # queue.items(), as this used to, left them off the Queue table entirely
    # while /api/stats (which reads active_transfers directly) showed the
    # transfer correctly - the two disagreeing about the same page. The
    # single-user branch above already got this right by checking
    # sending_users regardless of whether entries exist; this does the same.
    rows = []
    # In the order the dispatcher gives out free slots (#1206): the page shows
    # who is next, and Move up/down on a row changes that. The dispatcher's
    # own key (#1245): ranked by the wait alone, a waiting list was shown
    # last while it went first, and the arrows moved against a line the
    # page did not show.
    try:
        waiting_order = dcc.slot_order(queue, dcc.a_list_may_go_first(active))
        waiting_order += [key for key in queue if not queue[key]]
    except Exception as err:
        print(f"[WEBSERVER] The queue's order could not be read: {err}")
        waiting_order = list(queue.keys())
    for user_key in dict.fromkeys(waiting_order + list(sending_users) + ([packing_user] if packing_user else [])):
        # sending_users comes from active_transfers, whose rows are built by
        # dcc.py rather than keyed by it - an entry missing its "user" field
        # contributes "" to that set and used to reach the page as a blank row
        # with a "?" preview. Iterating dcc_queue alone could not produce that,
        # because its keys are always real nicks; taking senders from a list of
        # dicts is what introduced the possibility.
        if not user_key:
            continue
        entries = queue.get(user_key, [])
        if user_key in sending_users:
            status = "sending"
        elif user_key == packing_user:
            status = "packing"
        else:
            status = "frozen" if user_key in frozen else "queued"
        files = [e.get("file", "?") if isinstance(e, dict) else str(e) for e in entries]
        first = entries[0] if entries else None
        if first is not None:
            preview = first.get("file", "?") if isinstance(first, dict) else str(first)
        elif user_key in active_by_user:
            preview = active_by_user[user_key].get("file", "?")
        else:
            preview = ""
        current_file, bytes_sent, size = _progress_fields(user_key)
        row = {"user": user_key, "preview": preview, "count": len(entries), "status": status,
               "files": files, "file_ids": [dcc.queue_row_id(e) for e in entries],
               "current_file": current_file,
               "bytes_sent": bytes_sent, "size": size,
               "ignored_seconds": _security_seconds_left(user_key)}
        if status == "packing":
            row["pack"] = _pack_fields(pack)
        rows.append(row)
    return rows


def _pack_fields(pack):
    """The running pack as the Queue page draws it: the folder as the list
    shows it, how long, and the archive so far against the folder's size."""
    import announce
    return {"name": pack["name"], "elapsed": pack["elapsed"],
            "done": pack["done"], "total": pack["total"],
            "done_text": announce.format_size_human(pack["done"]),
            "total_text": announce.format_size_human(pack["total"]) if pack["total"] else "",
            "cancelling": bool(pack.get("cancelled"))}


def build_pack_cancel_result():
    """POST /api/queue/pack/cancel (#1202): stop the folder pack that is
    running. 200 with who and what it was; 404 when nothing is packing."""
    import dcc
    cancelled = dcc.cancel_pack()
    if cancelled is None:
        return 404, {"error": "Nothing is being packed."}
    return 200, {"cancelled": {"user": cancelled["user"], "name": cancelled["name"]}}


def build_ignore_result(body):
    """POST /api/ignore (#1206): ignore a nick for some minutes. 200 with
    how long, 400 with the reason when security.ignore_user() refuses."""
    import security
    nick = str(body.get("nick") or "").strip()
    ok, message = security.ignore_user(nick, body.get("minutes"))
    if not ok:
        return 400, {"error": message}
    return 200, {"user": nick.lower(), "message": message,
                 "seconds_left": security.ban_seconds_left(nick)}


def build_unignore_result(body):
    """POST /api/unignore (#1206): end a timed ignore or ban now. 404 when
    the nick has none."""
    import security
    nick = str(body.get("nick") or "").strip()
    ok, message = security.lift_ban(nick)
    if not ok:
        return 404, {"error": message}
    return 200, {"user": nick.lower(), "message": message}


def _queue_nick(body):
    return str(body.get("nick") or "").strip()


def build_queue_move_user_result(body):
    """POST /api/queue/move-user (#1206): one place up or down the line for a
    free slot. 200 with the new place, 400 with the reason."""
    import commands
    nick = _queue_nick(body)
    ok, message = commands.move_waiting_user(nick, body.get("direction"))
    if not ok:
        return 400, {"error": message}
    return 200, {"user": nick.lower(), "message": message}


def _queue_row_id(body):
    """The `id` of the file a move or a removal means - one of the queue
    payload's "file_ids" - or None when it is missing. Required: without it
    nothing says the file is still the one the page showed (#1245).
    """
    row_id = str(body.get("id") or "").strip()
    return row_id or None


def build_queue_move_file_result(body):
    """POST /api/queue/move-file (#1206): one place up or down inside the
    nick's own queue. `position` is 1-based and `id` the file the page saw
    there, so a queue that moved meanwhile is refused, not misread."""
    import commands
    nick = _queue_nick(body)
    row_id = _queue_row_id(body)
    if row_id is None:
        return 400, {"error": "Which file? The page sent no id - reload it."}
    ok, message = commands.move_queued_file(nick, body.get("position"), body.get("direction"), row_id)
    if not ok:
        return 400, {"error": message}
    return 200, {"user": nick.lower(), "message": message}


def build_queue_remove_file_result(body):
    """POST /api/queue/remove-file (#1206): the one file out of the queue, with
    the same notice to the nick as their own `@<bot>-remove <file>`. `id`
    names the file, as for a move."""
    import commands
    nick = _queue_nick(body)
    row_id = _queue_row_id(body)
    if row_id is None:
        return 400, {"error": "Which file? The page sent no id - reload it."}
    ok, message = commands.remove_queued_file(nick, body.get("position"), row_id)
    if not ok:
        return 400, {"error": message}
    return 200, {"user": nick.lower(), "message": message}


def build_queue_clear_result(body):
    """POST /api/queue/clear (#1206): everything queued for the nick, as the
    console's `clearqueue` does. 404 when nothing was queued."""
    import commands
    nick = _queue_nick(body)
    if not nick or any(ch.isspace() for ch in nick):
        return 400, {"error": "Give the nick."}
    key = nick.lower()
    import dcc
    with dcc.queue_lock:
        count = len(getattr(config, "dcc_queue", {}).get(key) or [])
    if not count:
        return 404, {"error": f"{nick} has nothing queued."}
    commands.handle_admin_clear_queue("dashboard", "", f"!clearqueue {nick}", authorised=True)
    return 200, {"user": key, "removed": count, "message": f"Cleared {count} file(s) for {nick}."}




def build_search_payload(query):
    """The Search view's data: up to WEBUI_MAX_SEARCH_RESULTS matches for `query`.

    Delegates the actual scan to list.find_matching_entries() - the same
    IRC-agnostic function execute_search() now calls - so a web search and an
    IRC @find agree on what matches. This just applies a browser-sized limit
    and its own JSON shape instead of IRC formatting.
    """
    import list as list_mod

    search_words = split_list_search_words(query)
    if not search_words:
        return []

    entries, _total = list_mod.find_matching_entries(search_words, limit=WEBUI_MAX_SEARCH_RESULTS)

    # The data model has no real per-file "which channel is this in" - a file is
    # just a line in the one master list, shared by every channel the bot joins
    # (config.CHANNEL). So "channel" here means "everywhere this bot is
    # present", not "this specific file was seen in this specific channel".
    channel_str = ", ".join(part.strip() for part in str(getattr(config, "CHANNEL", "")).split(","))

    return [
        {
            "title": entry.get("filename", "?"),
            "path": entry.get("folder") or "",
            "size": entry.get("size", ""),
            "channel": channel_str,
        }
        for entry in entries
    ]


def build_filelists_payload(offset=0, limit=None, name=None, q=""):
    """The File Lists view's data: a page of one of THIS bot's own lists.

    `name` picks which. None means the primary, which is what every list
    function already resolves it to and what this route meant before lists
    had names - so an unqualified request is unchanged.

    `q`, when given, narrows the list to rows matching every word in it -
    #399's follow-up, the List Browser's own per-list search, asked for
    because a fetched bot's archive can run to tens of thousands of rows and
    paging through them by hand to find one file is not a real option.
    split_list_search_words("") is [], and find_matching_entries() already
    treats an empty word list as "match everything" - the same rule that
    makes an unfiltered page unchanged, so this needs no branch of its own.

    v1 scope is deliberately THIS BOT ONLY - it serves DCCore's own master
    list, decomposed into rows, with "source" hardcoded to config.NICKNAME.
    Real cross-bot deduplication (parsing other bots' adverts in the channel)
    is out of scope; this dedup is the trivial single-source case, collapsing
    only the same filename listed under two different folders.

    Issue #76, option 3: what gets returned is a page, `[offset:offset+limit]`
    in folders, plus the `total` counts, never the whole list. Unfiltered, the
    page comes from list.page_of_list_files() (#1128): a table of where each
    folder is, built once per version of the lists, so a page parses only its
    own folders - it used to parse the ENTIRE list for every page, 31 s and
    2.1 GB at two million rows. A `q` search, and any list the table cannot
    answer for, still parses the whole list as before. `limit=None` (the route's
    default when `?limit=` was omitted) means FILELISTS_DEFAULT_PAGE_SIZE, not
    "unlimited" - a caller that genuinely wants no cap must pass a `limit` up
    to FILELISTS_MAX_PAGE_SIZE explicitly.

    Returns {"entries": [...], "total": N, "offset": offset, "limit": limit} -
    the shape both filelists routes now return, so the frontend's paging code
    is shared between "our own list" and "a fetched bot's list".
    """
    import list as list_mod

    if limit is None:
        limit = FILELISTS_DEFAULT_PAGE_SIZE

    search_words = split_list_search_words(q)
    source = getattr(config, "NICKNAME", "?")
    answer = None
    if not search_words:
        answer = list_mod.page_of_list_files(
            list_mod.all_list_paths(name), offset, limit, source,
            max_rows=list_mod.FILELISTS_MAX_PAGE_ROWS)
    if answer is None:
        entries, _total = list_mod.find_matching_entries(search_words, limit=None, name=name)
        rows = list_mod.entries_to_filelist_rows(entries, source)
        groups = list_mod.group_rows_by_folder(rows)
        answer = list_mod.page_folder_groups(
            groups, offset, limit, max_rows=list_mod.FILELISTS_MAX_PAGE_ROWS)
    page, total_folders, total_rows, row_capped = answer
    return {
        "folders": page,
        "total": total_folders,
        "total_files": total_rows,
        "offset": offset,
        "limit": limit,
        # What the caller actually got. The row ceiling can end a page early,
        # so the frontend advances by this rather than by `limit` - otherwise
        # a truncated page would silently skip the folders it did not receive.
        "returned": len(page),
        # #477: distinguishes "this page is short because the safety valve
        # cut it" from "this page is short because it is the last one" - a
        # page of one huge folder otherwise reads as the pager being broken.
        "row_capped": row_capped,
    }


# This section is web_lists.py since #1315, a step of splitting this file. Imported back by
# name, so webserver.<name> still answers for each of them.
from web_lists import (  # noqa: F401
    BOT_ALONE_FETCH_CONFLICT_ERROR,
    OWN_SOURCE,
    PACK_FETCH_CONFLICT_ERROR,
    _ADVERT_LIVE_FIELDS,
    _MAX_SAFE_JS_INT,
    _NICK_RE,
    _advert_live,
    _advert_now,
    _display_nick,
    _enqueue_folder_request,
    _fetched_list_entry,
    _freshness,
    _ident_merges,
    _looks_like_a_nick,
    _preferred_fetch_channel,
    bot_not_here_error,
    build_add_source_result,
    build_crosslist_search_payload,
    build_fetched_bot_list_payload,
    build_fetched_bot_list_summaries,
    build_folder_rar_batch_enqueue_result,
    build_folder_rar_fetch_enqueue_result,
    build_list_fetch_enqueue_result,
    build_own_list_summaries,
    build_purge_all_fetched_lists_result,
    build_purge_offline_fetched_lists_result,
    build_remove_source_result,
    fetch_feature_error,
    fetch_marks_by_bot,
    held_list_channel,
    mark_rows_with_fetch_state,
    own_list_name,
    own_list_source,
    present_nicks,
    requested_own_list,
    source_is_ours,
)








































































# This section is web_search.py since #1313, a step of splitting this file. Imported back by
# name, so webserver.<name> still answers for each of them.
from web_search import (  # noqa: F401
    BROADCAST_SEARCH_MIN_TERM_LEN,
    BROADCAST_SEARCH_WINDOW,
    build_broadcast_status_payload,
    start_broadcast_search,
)








# This section is web_fetch.py since #1317, a step of splitting this file. Imported back by
# name, so webserver.<name> still answers for each of them.
from web_fetch import (  # noqa: F401
    FETCH_CLEAR_STATES,
    build_fetch_clear_result,
    build_fetch_delete_many_result,
    build_fetch_delete_result,
    build_fetch_enqueue_result,
    build_fetch_pause_result,
    build_fetch_status_payload,
    build_fetched_list_purge_result,
)
















# This section is web_list_update.py since #1317, a step of splitting this file. Imported back by
# name, so webserver.<name> still answers for each of them.
from web_list_update import (  # noqa: F401
    build_update_list_status_payload,
    build_verify_list_payload,
    read_list_progress,
    start_audio_info_reading,
    start_list_update,
)












# ==========================================================================
# Settings (mutating - behind the same login as every other route). Pure
# logic here, same reasoning as every other build_*_payload()/apply_*()
# function in this module: no Flask import, fully unit testable.
# ==========================================================================

# Hand-written UX grouping of every setting config.py annotates (see
# declared_types() below) except ADMIN_PASSWORD_HASH, which never appears as
# a field - only the "admin_password_set" boolean does (see
# build_settings_payload()). Every name here is checked against config.py's
# actual annotations by SettingsPayloadTests' completeness guard, so a
# setting added to config.py later and never slotted in here fails a test
# instead of silently never showing up on the page.
# REGROUPED, and the old grouping is worth recording because it is how any
# grouping ends up: by accretion. Each feature put its settings wherever there
# was room, so "Slots & queue" had grown to 25 and "Paths & storage" to 31 -
# between them two thirds of the page - while the two settings that tune a
# transfer sat in DIFFERENT categories.
#
# From the operator: "Settings pages at webpage are a mess. Need better
# grouping, hiding some that are never used like folder locations under
# advanced settings etc. Also why is packet size on different page than buffer
# size."
#
# THREE RULES, applied here and worth keeping:
#
#   * Settings that are read together live together. DCC_BLOCK_SIZE and
#     DCC_SEND_BUFFER are the transfer-tuning pair - the 3 MB/s investigation
#     needed both - and were a category apart.
#   * A category is named for what an operator came looking for, not for the
#     part of the code it configures. The colour theme was under "Advertising
#     & search" because announce.py draws it.
#   * The things set once at install and never again go last, in their own
#     section. Seventeen file paths at the same level as MAX_DCC_SLOTS is
#     seventeen chances to wonder whether you should be changing one.
#
# NOTHING MOVES ON DISK. settings.conf is flat; a category is a grouping for
# this page and nothing else, so this reorders what an operator reads without
# touching a single stored value.
#
# Every name here is checked against config.py's actual annotations by
# SettingsPayloadTests' completeness guard, so a setting added later and never
# slotted in fails a test instead of silently never showing up.
SETTINGS_CATEGORIES = (
    ("identity",      "Identity & network",    ["SERVER", "PORT", "NICKNAME", "ALT_NICKNAME", "REJOIN_ATTEMPTS",
                                                "ON_CONNECT_CHECK_MINUTES",
                                                "ADMIN_NICK", "CHANNEL", "DEBUG_CHANNEL",
                                                "CHECK_FOR_UPDATES"]),
    ("sharing",       "Sharing & queue",       ["MAX_DCC_SLOTS", "MAX_USER_QUEUE",
                                                "MAX_GLOBAL_QUEUE", "SEARCH_ENABLED", "MAX_SEARCH_RESULTS",
                                                "SEARCH_SHOW_FOLDER", "SEARCH_FOLDER_MAX_CHARS",
                                                "PAUSE_ON_UPDATE", "PAUSE_FOR_WHOLE_UPDATE",
                                                "REHASH_TRANSFER_WAIT"]),
    # The transfer-tuning pair, together. Anyone reaching for one wants the
    # other in front of them.
    ("transfers",     "Transfers",             ["DCC_BLOCK_SIZE", "DCC_SEND_BUFFER",
                                                "DCC_PORT_START", "DCC_PORT_END",
                                                "DCC_ACCEPT_TIMEOUT", "MAX_SEND_FAILS"]),
    ("your-list",     "Your list",             ["FILE_DIRECTORY", "LIST_BASE_NAME",
                                                "LIST_FORMAT", "LIST_IGNORED_EXTENSIONS",
                                                "SEPARATE_VIDEO_LIST", "LIST_VIDEO_EXTENSIONS",
                                                "LIST_VIDEO_COMPANION_EXTENSIONS",
                                                "RAR_ENABLED", "RAR_EXTENSIONS", "RAR_BINARY",
                                                "MAX_RAR_FOLDER_SIZE", "RAR_TIMEOUT",
                                                "LIST_HEADER_FILE",
                                                "LIST_HEADER_MAX_BYTES",
                                                "LIST_SHOW_AUDIO_INFO"]),
    # #776: when the list rebuilds by itself, beside the two limits every
    # rebuild runs under. Its own category because "Your list" had reached
    # the sixteen the grouping test allows before one becomes a dumping ground.
    ("list-rebuild",  "List rebuild",          ["LIST_REBUILD_SCHEDULE",
                                                "LIST_UPDATE_TIMEOUT",
                                                "LIST_UPDATE_STALL_SECONDS",
                                                "LIST_AUDIO_INFO_MINUTES",
                                                "LIST_AUDIO_INFO_THREADS",
                                                "LIST_SCAN_THREADS"]),
    # #926: fetching lists nobody asked for is its own decision, with its
    # own rules - not four more lines at the end of "Fetching from bots".
    ("list-grab",     "Grabbing lists",        ["AUTO_GRAB_LISTS", "AUTO_GRAB_EVERY_MINUTES",
                                                "AUTO_GRAB_MIN_FILES", "AUTO_GRAB_MIN_SPEED_KB",
                                                "AUTO_DISCOVER_CHANNEL_LISTS",
                                                "MULTI_CHANNEL_LIST_STABLE_SECONDS"]),
    ("fetching",      "Fetching from bots",    ["MAX_FETCH_SLOTS", "AUTO_REFETCH_LISTS",
                                                "AUTO_REFETCH_INTERVAL_HOURS",
                                                "AUTO_REFETCH_MAX_PER_RUN",
                                                "FETCH_OFFER_TIMEOUT",
                                                "FETCH_TRANSFER_TIMEOUT",
                                                "FETCH_FOLDER_OFFER_TIMEOUT",
                                                "FETCH_FOLDER_OFFER_TIMEOUT_UNADVERTISED",
                                                "FETCH_FOLDER_TRANSFER_TIMEOUT",
                                                "MAX_FETCH_FILE_SIZE",
                                                "MAX_FETCH_FOLDER_FILE_SIZE",
                                                "MAX_FETCH_LIST_FILE_SIZE",
                                                "MAX_LIST_TEXT_SIZE",
                                                "FETCH_HISTORY_DAYS",
                                                "FETCH_HISTORY_MAX_ROWS",
                                                "LIST_REQUEST_TAG"]),
    # #926: how the fetch queue paces itself with another bot.
    # #1210: a bot that keeps failing is paused for a while.
    ("fetch-queue",   "Fetch queue",           ["FETCH_MAX_PER_BOT", "FETCH_QUEUED_TIMEOUT",
                                                "FETCH_BOT_MAX_FAILS",
                                                "FETCH_BOT_COOLDOWN_MINUTES"]),
    ("advertising",   "Advertising & search",  ["ANNOUNCE_INTERVAL", "ANNOUNCE_TRANSFERS",
                                                "BROADCAST_SEARCH_CHANNEL",
                                                "BROADCAST_SEARCH_COOLDOWN", "CTCP_VERSION_REPLY",
                                                "MSG_DELAY", "DEBUG_MSG_DELAY"]),
    # Its own category, not a corner of "Advertising & search". The theme
    # lived there because announce.py is what draws it - which is a fact about
    # the code, not about what an operator came looking for.
    ("appearance",    "Appearance",            ["THEME", "CUSTOM_THEME_BORDER",
                                                "CUSTOM_THEME_SEPARATOR", "CUSTOM_THEME_TEXTBOX",
                                                "CUSTOM_THEME_VALUE", "CUSTOM_THEME_ALERT",
                                                "CUSTOM_THEME_ACCENT"]),
    ("anti-flood",    "Anti-flood",            ["MAX_REQUESTS", "REQUEST_WINDOW", "MUTE_TIME",
                                                "FLOOD_BAN_SECONDS"]),
    # Its own category for the reason Appearance gives above. The cooldown
    # below is an anti-flood setting by mechanism, but an operator looking
    # for it is thinking about private messages, not about flooding.
    ("private-messages", "Private messages",   ["PRIVATE_MESSAGES_ENABLED",
                                                "PRIVATE_MESSAGE_COOLDOWN_SECONDS",
                                                "PRIVATE_MESSAGE_DECLINE_TEXT",
                                                "PRIVATE_MESSAGE_DECLINE_INTERVAL_SECONDS",
                                                "PRIVATE_MESSAGE_DECLINE_BURST",
                                                "PRIVATE_MESSAGE_DECLINE_BURST_SECONDS"]),
    ("admin-console", "Admin console",         ["ADMIN_HOSTMASKS", "ADMIN_CHAT_MODE",
                                                "ADMIN_CHANNEL_COMMANDS", "ADMIN_CHAT_COLOURS"]),
    ("web-dashboard", "Web dashboard",         ["WEBUI_ENABLED", "WEBUI_HOST", "WEBUI_PORT",
                                                "WEBUI_ALLOWED_HOSTS", "WEBUI_CONSOLE_ENABLED", "WEBUI_OPEN_BROWSER",
                                                "WEBUI_FOLDER_BROWSER_ENABLED"]),
    ("console-feed",  "Console feed",          ["CONSOLE_SHOW_REQUESTS", "CONSOLE_SHOW_QUEUE",
                                                "CONSOLE_SHOW_SENDS", "CONSOLE_SHOW_FAILURES",
                                                "CONSOLE_SHOW_SEARCHES", "DEBUG_CHANNEL_FEED"]),
    ("debug",         "Debug & logging",       ["DEBUG_MODE", "DEBUG_TO_CHANNEL",
                                                "DEBUG_TO_CONSOLE",
                                                "CONSOLE_TIMESTAMP_FORMAT", "CONSOLE_LOG_FILE",
                                                "CONSOLE_LOG_MAX_MB", "CONSOLE_LOG_KEEP", "BOT_WINDOW",
                                                "PROJECT_URL"]),
    # LAST, and named so nobody opens it by accident. Set once at install, and
    # a wrong value here loses a queue or a statistics file rather than
    # mis-tuning something. They were interleaved with the settings changed
    # weekly.
    ("advanced",      "Advanced: file locations",
                                               ["TMP_ZIP_DIR", "LOCAL_LIST_DIR",
                                                "FETCHED_FILES_DIR", "BANS_FILE", "HARD_BANS_FILE",
                                                "STATS_FILE", "KNOWN_BOTS_FILE",
                                                "FETCHED_BOT_LISTS_FILE", "LIST_INDEX_FILE",
                                                "LIST_AUDIO_INFO_CACHE",
                                                "FETCH_HISTORY_FILE", "DOWNLOAD_COUNTS_FILE",
                                                "TRANSFER_LOG_FILE",
                                                "LIST_SIZE_FILE", "LIST_RAWBYTES_FILE",
                                                "LIST_PROGRESS_FILE", "LIBRARY_FOLDERS_FILE",
                                                "LISTS_FILE", "ADMIN_TOKENS_FILE", "ON_CONNECT_FILE",
                                                "NOTICES_FILE",
                                                "PRIVATE_MESSAGES_FILE", "DCC_QUEUE_FILE"]),
)

# A human-readable label per setting, since the raw config.py name
# (MAX_DCC_SLOTS, DCC_PORT_START, ...) is what an operator edits in a text
# file, not what they expect to read on a form. Checked against
# SETTINGS_CATEGORIES by SettingsPayloadTests' completeness guard, same
# reasoning as that list itself: a setting added to config.py and slotted
# into a category but never given a label here would otherwise silently show
# its raw name instead of failing a test.
SETTINGS_LABELS = {
    "SERVER": "IRC server",
    "PORT": "Port",
    "NICKNAME": "Nickname",
    "ALT_NICKNAME": "Alt nickname",
    "ADMIN_NICK": "Admin nick(s)",
    "CHANNEL": "Channels",
    "DEBUG_CHANNEL": "Debug channel",

    "MAX_DCC_SLOTS": "Max simultaneous sends",
    "MAX_USER_QUEUE": "Max queue per user",
    "MAX_GLOBAL_QUEUE": "Max global queue",
    "SEARCH_ENABLED": "Answer @find searches",
    "MAX_SEARCH_RESULTS": "Max search results",
    "SEARCH_SHOW_FOLDER": "Name the folder in search replies",
    "SEARCH_FOLDER_MAX_CHARS": "Longest folder shown (characters)",
    "MSG_DELAY": "Message delay (seconds)",
    "DEBUG_MSG_DELAY": "Debug message delay (seconds)",
    "DCC_PORT_START": "DCC port range start",
    "DCC_PORT_END": "DCC port range end",
    "MAX_FETCH_SLOTS": "Max fetch slots",
    "FETCH_HISTORY_DAYS": "Keep finished downloads for (days)",
    "FETCH_HISTORY_MAX_ROWS": "Maximum finished downloads kept",
    "LIST_REQUEST_TAG": "Tag on your list requests",
    "MAX_FETCH_FILE_SIZE": "Max fetch file size",
    "MAX_LIST_TEXT_SIZE": "Largest list text accepted from a peer",
    "DCC_BLOCK_SIZE": "Packet size",
    "DCC_SEND_BUFFER": "Socket send buffer (0 = the default for your platform)",
    "REHASH_TRANSFER_WAIT": "Seconds a rehash waits for transfers to finish",
    "AUTO_REFETCH_LISTS": "Re-fetch a held list when its bot advertises a new one",
    "AUTO_GRAB_LISTS": "Grab the lists of bots you have no list from",
    "AUTO_GRAB_EVERY_MINUTES": "Minutes between automatic grabs",
    "AUTO_GRAB_MIN_FILES": "Skip bots with fewer files than",
    "AUTO_GRAB_MIN_SPEED_KB": "Skip bots slower than (KB/s)",
    "AUTO_DISCOVER_CHANNEL_LISTS": "Discover a bot's other channel-bound lists",
    "MULTI_CHANNEL_LIST_STABLE_SECONDS": "Hold stable this long first (seconds)",
    "AUTO_REFETCH_INTERVAL_HOURS": "Least time between re-fetches of one bot (hours)",
    "AUTO_REFETCH_MAX_PER_RUN": "Most lists to re-fetch in one sweep",
    "FETCH_TRANSFER_TIMEOUT": "Fetch transfer timeout (seconds)",
    # Named from the operator's side, like the pill in the Downloads table:
    # this is the wait AFTER we send a request, not a timeout on an offer
    # anybody made us.
    "FETCH_QUEUED_TIMEOUT": "Wait for a queued request (s)",
    "FETCH_MAX_PER_BOT": "Files asked of one bot at once",
    "FETCH_BOT_MAX_FAILS": "Failed requests in a row that pause a bot",
    "FETCH_BOT_COOLDOWN_MINUTES": "Minutes a failing bot stays paused",
    "FETCH_OFFER_TIMEOUT": "Wait for a reply to a fetch request (seconds)",
    "FETCH_FOLDER_OFFER_TIMEOUT": "Wait for a reply to a folder (.rar) request (seconds)",
    "FETCH_FOLDER_OFFER_TIMEOUT_UNADVERTISED":
        "...from a bot that publishes no .rar list (seconds)",
    "MAX_FETCH_FOLDER_FILE_SIZE": "Max folder (.rar) fetch size",
    "MAX_FETCH_LIST_FILE_SIZE": "Max fetched master-list zip size",
    "FETCH_FOLDER_TRANSFER_TIMEOUT": "Folder (.rar) fetch transfer timeout (seconds)",

    "LIST_BASE_NAME": "List base name",
    "PAUSE_ON_UPDATE": "Pause sharing during !update",
    "PAUSE_FOR_WHOLE_UPDATE": "Pause for the whole rebuild",
    # Named for what it now IS. From an operator: "under Paths & Storage, this is not
    # needed anymore" - not quite, it is still the fallback for an install
    # with no folder list, which is most of them. But presenting it as a
    # plain "Music directory" beside a folder editor that overrides it is
    # what made it read as the setting that matters.
    "FILE_DIRECTORY": "Music directory (used only when no folders are set)",
    "LIST_FORMAT": "List delivery format",
    "LIST_IGNORED_EXTENSIONS": "File types to leave out of the list",
    "SEPARATE_VIDEO_LIST": "Publish film and series as a separate list",
    "LIST_VIDEO_EXTENSIONS": "File types that go in the film list",
    "LIST_VIDEO_COMPANION_EXTENSIONS": "File types that follow a film into its list (subtitles, .nfo, .sfv)",
    "RAR_EXTENSIONS": "File types a folder needs to be !rar-packable",
    "RAR_ENABLED": "Enable !rar folder packing",
    "RAR_BINARY": "RAR binary path",
    "TMP_ZIP_DIR": "Temp archive directory",
    "MAX_RAR_FOLDER_SIZE": "Largest folder !rar will pack (0 = no limit)",
    "LOCAL_LIST_DIR": "Master list directory",
    "FETCHED_FILES_DIR": "Fetched files directory",
    "BANS_FILE": "Bans file",
    "STATS_FILE": "Stats file",
    "HARD_BANS_FILE": "Hard bans file",
    "DCC_QUEUE_FILE": "DCC queue file",
    "KNOWN_BOTS_FILE": "Known bots file",
    "LIST_INDEX_FILE": "Cross-list search index",
    "LIST_AUDIO_INFO_CACHE": "Audio info cache",
    "LIST_SHOW_AUDIO_INFO": "Length and quality in the list",
    "LIST_AUDIO_INFO_MINUTES": "Time limit for reading audio files (no longer used)",
    "LIST_AUDIO_INFO_THREADS": "Audio files read at once",
    "LIST_SCAN_THREADS": "Folders scanned at once",
    "DOWNLOAD_COUNTS_FILE": "Download counts file",
    "TRANSFER_LOG_FILE": "Transfer record file",
    "FETCHED_BOT_LISTS_FILE": "Fetched bot lists file",
    "FETCH_HISTORY_FILE": "Fetch history file",
    "NOTICES_FILE": "Operator notices file",
    "PRIVATE_MESSAGES_FILE": "Private messages file",
    "PRIVATE_MESSAGE_COOLDOWN_SECONDS": "Record one private message per sender every (seconds)",
    "PRIVATE_MESSAGES_ENABLED": "Keep private messages (off: keep none, reply once instead)",
    "PRIVATE_MESSAGE_DECLINE_TEXT": "That reply's wording (%admin becomes the admin nick)",
    "PRIVATE_MESSAGE_DECLINE_INTERVAL_SECONDS": "Reply to the same sender once every (seconds)",
    "PRIVATE_MESSAGE_DECLINE_BURST": "Most replies to send in one burst window",
    "PRIVATE_MESSAGE_DECLINE_BURST_SECONDS": "How long that burst window is (seconds)",
    "LIST_SIZE_FILE": "List size file",
    "LIST_PROGRESS_FILE": "List rebuild progress file",
    "LIST_RAWBYTES_FILE": "List raw bytes file",
    "LIST_HEADER_FILE": "List banner file",
    "LIST_HEADER_MAX_BYTES": "List banner size limit",
    "LIBRARY_FOLDERS_FILE": "Served folders file",
    "LISTS_FILE": "Served lists file",
    "ADMIN_TOKENS_FILE": "Paired console scripts file",
    "ON_CONNECT_FILE": "On-connect commands file",

    "THEME": "Colour theme",
    "CUSTOM_THEME_BORDER": "Custom theme: border colour",
    "CUSTOM_THEME_SEPARATOR": "Custom theme: separator colour",
    "CUSTOM_THEME_TEXTBOX": "Custom theme: text box colour",
    "CUSTOM_THEME_VALUE": "Custom theme: value colour",
    "CUSTOM_THEME_ALERT": "Custom theme: alert colour",
    "CUSTOM_THEME_ACCENT": "Custom theme: accent colour",
    "ANNOUNCE_TRANSFERS": "Announce finished transfers in the channel",
    "REJOIN_ATTEMPTS": "Rejoin attempts after a kick (0 = never)",
    "ON_CONNECT_CHECK_MINUTES": "Check the on-connect commands worked every (minutes, 0 = never)",
    "ANNOUNCE_INTERVAL": "Advert interval (seconds)",
    "BROADCAST_SEARCH_CHANNEL": "Broadcast search channel",
    "BROADCAST_SEARCH_COOLDOWN": "Broadcast search cooldown (seconds)",
    "CTCP_VERSION_REPLY": "Answer CTCP VERSION",

    "MAX_REQUESTS": "Max commands per window",
    "REQUEST_WINDOW": "Request window (seconds)",
    "MUTE_TIME": "Mute duration (seconds)",
    "FLOOD_BAN_SECONDS": "Ban after flooding while muted (seconds)",
    "DCC_ACCEPT_TIMEOUT": "Wait for the receiver to connect (seconds)",
    "MAX_SEND_FAILS": "Max send failures",
    "CHECK_FOR_UPDATES": "Tell me when a new version is out",
    "RAR_TIMEOUT": "RAR pack timeout (seconds)",
    "LIST_UPDATE_TIMEOUT": "List rebuild hard cap (seconds, 0 = none)",
    "LIST_UPDATE_STALL_SECONDS": "Give up if a rebuild reports nothing for (seconds)",
    "LIST_REBUILD_SCHEDULE": "Rebuild the list automatically",

    "ADMIN_HOSTMASKS": "Admin hostmasks",
    "ADMIN_CHAT_MODE": "DCC chat connection mode",
    "ADMIN_CHANNEL_COMMANDS": "Allow admin commands in channel",
    "ADMIN_CHAT_COLOURS": "Colour the tags in the admin DCC chat",

    "WEBUI_ENABLED": "Enable web dashboard",
    "WEBUI_HOST": "Host",
    "WEBUI_PORT": "Port",
    "WEBUI_ALLOWED_HOSTS": "Other names the dashboard answers to",
    "WEBUI_FOLDER_BROWSER_ENABLED": "Folder picker on the Settings page",
    "WEBUI_CONSOLE_ENABLED": "Enable the Console page (remote admin)",
    "WEBUI_OPEN_BROWSER": "Open the dashboard in a browser at startup",

    "DEBUG_MODE": "Debug mode",
    "DEBUG_TO_CHANNEL": "Send debug lines to channel",
    "DEBUG_TO_CONSOLE": "Send debug lines to admin console",
    "CONSOLE_SHOW_REQUESTS": "Show requests (who asked for what)",
    "CONSOLE_SHOW_QUEUE": "Show queue positions",
    "CONSOLE_SHOW_SENDS": "Show transfers starting, resuming and completing",
    "CONSOLE_SHOW_FAILURES": "Show failed transfers",
    "CONSOLE_SHOW_SEARCHES": "Show searches and their result counts",
    "DEBUG_CHANNEL_FEED": "Also send requests, queue positions, starts and searches to the IRC debug channel",
    "CONSOLE_TIMESTAMP_FORMAT": "Time prefix on every console line (strftime; blank = none)",
    "CONSOLE_LOG_FILE": "Log file (blank = none)",
    "CONSOLE_LOG_MAX_MB": "Start a new log file at (MB)",
    "CONSOLE_LOG_KEEP": "Old log files to keep",
    "BOT_WINDOW": "The bot's window on Windows (normal, minimised, hidden)",
    "PROJECT_URL": "Project URL",
}


# A size an operator reads in the unit they think in, while the file keeps
# bytes. Counting the zeros in 10737418240 to check it says ten gigabytes is
# not work anybody should be doing, and the sizes here span 8 KB to 10 GB.
#
# STORED IN BYTES, UNCHANGED. settings.conf, admin_config.py and every reader
# in the daemon keep the number they have always had, so nothing migrates and
# an operator who edits the file by hand sees exactly what they saw before.
# Only the dashboard divides, and only for display.
#
# The unit is per setting rather than picked from the magnitude: a value that
# changed unit as it grew would move under the operator mid-edit, and 0 -
# which several of these use for "no limit" - has no magnitude to read.
# KB where MB would print 0.0078.
SETTINGS_UNITS = {
    "MAX_RAR_FOLDER_SIZE": ("MB", 1024 * 1024),
    "MAX_FETCH_FILE_SIZE": ("MB", 1024 * 1024),
    "MAX_LIST_TEXT_SIZE": ("MB", 1024 * 1024),
    "MAX_FETCH_FOLDER_FILE_SIZE": ("MB", 1024 * 1024),
    "MAX_FETCH_LIST_FILE_SIZE": ("MB", 1024 * 1024),
    "DCC_SEND_BUFFER": ("KB", 1024),
    "LIST_HEADER_MAX_BYTES": ("KB", 1024),
}

# DCC_BLOCK_SIZE is a menu, not a number to type, so it keeps its byte values
# as the stored choice and gains readable text beside each one.
CHOICE_LABELS = {
    "DCC_BLOCK_SIZE": {str(1024 * n): f"{n} KB"
                       for n in (4, 8, 16, 32, 64, 128, 256)},
}


def _settings_field(name, declared, value):
    """One field for the settings form.

    "choices" is present only for a setting that has a fixed few - LIST_FORMAT
    is the first. It carries the same tuple settings_file.CHOICES validates
    against, so the page cannot offer a value the save would then refuse, and
    an operator picks a format instead of typing one of three words correctly.
    """
    import settings_file
    field = {"name": name, "label": SETTINGS_LABELS.get(name, name),
             "type": declared.__name__, "value": value}
    if name in settings_file.CHOICES:
        field["choices"] = list(settings_file.CHOICES[name])
        labels = CHOICE_LABELS.get(name)
        if labels:
            field["choice_labels"] = [labels.get(str(c), str(c))
                                      for c in field["choices"]]

    # A COLOUR THE OPERATOR CAN READ, AND PICK. config holds the decoded
    # bytes - a role of "\x0313" is four characters, two of them a control
    # code - and the page was being handed them raw. A text input cannot show
    # 0x03, so the field for that accent read "13": the code was present,
    # invisible, and what the operator could see was not what was set. Typing
    # back what they read would have put a literal "13" in every advert.
    #
    # So the value crosses as the escape text settings.conf.sample documents,
    # which is also what the save writes back - and `irc_colour` tells the
    # page it may offer the sixteen colours as menus instead of asking for a
    # control code to be typed into a box that cannot display one.
    if name.startswith("CUSTOM_THEME_"):
        import settings_file as _settings_file
        field["value"] = _settings_file.encode_irc_escapes(value or "")
        field["irc_colour"] = True

    unit = SETTINGS_UNITS.get(name)
    if unit:
        field["unit"], field["unit_factor"] = unit

    # WHAT THE SETTING MEANS (#528). "A lot of settings are not easy to
    # understand" - and the explanation for every one of them already
    # existed, as the comment block beside it in defaults.py, which
    # settings.conf.sample is generated from. settings_help reads the same
    # block, so the code, the sample and the page can never disagree. The
    # page draws a "?" beside the label that shows this on hover; a setting
    # with nothing to say gets no "?" rather than an empty box.
    import settings_help
    help_text = settings_help.help_text(name)
    if help_text:
        field["help"] = help_text

    # A TRI-STATE NEEDS A THIRD ANSWER. WEBUI_CONSOLE_ENABLED is declared
    # `bool = None`, and None does not mean False: console_is_enabled() reads
    # it as "yes if nobody else can reach it", so a stock loopback install has
    # the Console ON. The page renders a bool as a checkbox from `!!value`,
    # which drew None as unchecked - telling the operator that ban, unban,
    # clearqueue, rehash and update were NOT reachable behind the dashboard
    # password when they were.
    #
    # A note rather than sending the effective value as `value`: that would
    # make the checkbox truthful, but the next save would then write an
    # explicit True, and an operator who later moved the dashboard onto the
    # LAN would keep a Console that should have switched itself off. The
    # value stays unset; what changes is that the page says what unset means
    # here and now.
    if name == "WEBUI_CONSOLE_ENABLED" and value is None:
        field["note"] = ("Not set: on while the dashboard is loopback-only. "
                         "Currently " + ("ON" if console_is_enabled() else "OFF") + ".")
    return field
# The roles a theme has, and the one sample line each of them shows up in.
# Named here so the page can say which setting it is that the operator just
# changed nothing visible with - and so a role added later without a sample is
# a failing test rather than a silent gap.
THEME_PREVIEW_ROLES = {
    "border": "both", "separator": "both", "textbox": "both",
    "value": "both", "alert": "both", "accent": "notice",
}


def build_theme_preview(overrides=None):
    """The two lines a theme is judged by, rendered with `overrides`.

    WHY A PREVIEW EXISTS. The six CUSTOM_THEME_* settings hold raw mIRC colour
    codes, typed into text boxes. Finding out what a change did meant saving
    it, rehashing, and watching the channel for the next advert - and the next
    advert is up to ANNOUNCE_INTERVAL away, on a bot other people are using.

    WHY BOTH LINES. Between them they use all six roles, and neither uses all
    six alone: the advert never touches `accent`, only the completion notice
    does. A single sample would leave one setting looking like it does
    nothing, which is the confusion this is meant to end.

    Built by announce.py's own builders, not by a copy of their templates. A
    copy drifts the first time one of them changes, and then the preview lies
    with a straight face.

    `overrides` is what the operator has typed and not saved. It never reaches
    config: a live daemon is serving a channel while they are choosing
    colours.

    Returns the RAW lines, colour codes and all. Rendering them is the page's
    job - it is the one that knows how wide the box is.
    """
    import announce

    settings = dict(overrides or {})
    nick = str(getattr(config, "NICKNAME", "") or "DCCore")
    channel = (str(getattr(config, "CHANNEL", "") or "#channel")
               .split(",")[0].strip() or "#channel")

    # Plausible rather than real. Reading the live figures would make the
    # preview flicker as transfers come and go, and a sample that changes
    # while you are comparing two colours is a sample you cannot compare.
    advert = announce.build_advert_line(
        channel, nick, "719,041", "5.48 TB", "Sep 7th", "3/3", "0",
        "2.4MB/s", "24.7MB/s", "1,204 Files (8.9 TB)",
        str(config.SCRIPT_VERSION), settings=settings)
    notice = announce.build_transfer_complete_line(
        channel, "someuser", "Some Artist - Some Album - 01 - A Track.flac",
        "1,204 Files (8.9 TB)", "37", "12", "3:04 pm", "24.7MB/s",
        settings=settings)

    # The PRIVMSG envelope is protocol, not something anybody sees in a
    # channel. Showing it would put "PRIVMSG #chan :" at the front of a
    # preview of what the channel looks like.
    def body(line):
        return line.split(" :", 1)[1].rstrip("\r\n") if " :" in line else line

    return {
        "advert": body(advert),
        "notice": body(notice),
        "roles": THEME_PREVIEW_ROLES,
    }


def theme_preview_overrides(payload):
    """The subset of a posted body that may steer a preview.

    A whitelist, not a filter: this reaches theme.palette(), and everything
    else on the settings page has nothing to say about colour. THEME is
    checked against the presets that exist, because an unknown name would
    otherwise pick the configured one and quietly preview the wrong thing.

    AND IT COERCES THE WAY THE FILE WOULD. A colour crosses the wire as the
    escape text `\x0313` - see _settings_field() for why it must, and
    settings_file.encode_irc_escapes() for the other half of that trip - while
    theme.palette() deals in the decoded BYTE, which is what theme.CLASSIC and
    every other preset holds.

    Nothing was decoding it here, so the preview rendered the operator's typed
    text as nine literal characters. The advert sample began with a visible
    `\x0309,07` and overflowed to the right, which is exactly the failure the
    preview exists to show them they are about to cause. Reported against the
    colour picker, but it arrived with the preview itself and would have hit
    anyone who typed a code by hand.

    Through settings_file.coerce(), not a private copy of the rule: the
    preview's whole claim is that it shows what saving would produce, and two
    functions that merely happen to agree today are two functions that can
    stop agreeing.
    """
    import settings_file
    import theme

    body = json_object(payload)
    wanted = {}
    name = str(body.get("THEME", "") or "").strip().lower()
    if name in theme.THEMES:
        wanted["THEME"] = name
    for role in theme.ROLES:
        key = f"CUSTOM_THEME_{role.upper()}"
        if key in body:
            value = body.get(key)
            wanted[key] = (settings_file.coerce(key, value, "", str)
                           if isinstance(value, str) else "")
    # SEARCH_ENABLED (#1249 review): announce.search_state() already reads
    # the unsaved value first - `(settings or {}).get("SEARCH_ENABLED", ...)`
    # - but nothing ever put it in `settings`, since this whitelist stopped
    # at THEME and CUSTOM_THEME_*. The advert sample's "Search: ON/OFF"
    # followed the SAVED setting regardless of what the operator had just
    # toggled on the page, same as every other preview bug this function
    # exists to prevent.
    if "SEARCH_ENABLED" in body:
        wanted["SEARCH_ENABLED"] = bool(body.get("SEARCH_ENABLED"))
    return wanted


def build_notices_payload():
    """GET /api/notices: what the operator has not been told, and the rest.

    Newest FIRST here, and oldest first in storage. The store appends, which
    is the cheap end to write; a panel is read from the top, which is where
    the thing that just happened belongs. Reversing at the boundary keeps both
    ends natural rather than making one of them pay for the other.

    `unread` and `severity` are what the badge draws, and they are computed
    here rather than in the page: the page would have to know the read marker
    to work them out, and two places computing "is there anything new" is two
    places that can disagree about whether to light up.
    """
    import announce

    count, worst = announce.unread_notices()
    with runtime.notices_lock:
        rows = list(reversed(config.notices))
        seen = int(config.notice_state.get("seen_id", 0) or 0)

    return {
        "notices": rows,
        "unread": count,
        "severity": worst,
        "seen_id": seen,
    }


def mark_notices_read_result():
    """POST /api/notices/read: the operator has looked. Returns the payload
    the page would have got from a fresh GET, so the badge clears from the
    same answer that cleared it rather than from a second round trip that
    could race a notice arriving in between."""
    import announce

    announce.mark_notices_read()
    return build_notices_payload()


def messages_are_off():
    """True when this bot keeps no private messages at all."""
    return not getattr(config, "PRIVATE_MESSAGES_ENABLED", True)


def messages_payload_or_404():
    """(payload, status) for GET /api/messages.

    404, NOT an empty list. An empty list means "nobody has messaged you",
    which is a fact about the world; this is "there is no such page here",
    which is a fact about the bot. web/app.js hides the Messages nav item on
    exactly this status - the same signal the Console has used since it got
    an off-switch.

    Out here rather than inside the route because Flask is an optional
    dependency: a bot running without it still serves nothing, and logic that
    lives in a route body is logic no test in this repo can reach.
    """
    if messages_are_off():
        return {"error": "Private messages are turned off."}, 404
    return build_messages_payload(), 200


def messages_read_or_404():
    """(payload, status) for POST /api/messages/read. Same gate: turned off
    while somebody had the page open must not be a button that silently does
    nothing."""
    if messages_are_off():
        return {"error": "Private messages are turned off."}, 404
    return mark_messages_read_result(), 200


def build_messages_payload():
    """GET /api/messages: private messages nobody answered.

    Newest FIRST here and oldest first in storage, for the reason
    build_notices_payload() gives: appending is the cheap end to write, and a
    list is read from the top.

    No reply field and no per-message action that sends anything. The bot has
    no conversation path at all - see announce.record_private_message() - and
    an API that looked like it could answer would be a promise the daemon
    cannot keep.
    """
    import announce

    with runtime.private_messages_lock:
        rows = list(reversed(config.private_messages))
        seen = int(config.private_message_state.get("seen_id", 0) or 0)

    return {
        "messages": rows,
        "unread": announce.unread_private_messages(),
        "seen_id": seen,
    }


def mark_messages_read_result():
    """POST /api/messages/read: the operator has looked. Returns what a fresh
    GET would say, so the count and the list cannot disagree."""
    import announce

    announce.mark_private_messages_read()
    return build_messages_payload()


def build_settings_payload():
    """GET /api/settings payload: every editable setting, grouped for the
    Settings view's category rail, plus whether an admin password is set.

    Filters through settings_file.declared_types(vars(config)) - names
    config.py itself annotates - rather than vars(config) directly, so a
    runtime-only name (ORIGINAL_NICK, MY_IP_OR_DOCK, ...) can never appear
    here even before settings_file.save() would refuse to write it.

    ADMIN_PASSWORD_HASH never appears as a field's raw value anywhere - only
    the boolean admin_password_set at the top level. A "leftover" category is
    appended for any declared+overridable setting SETTINGS_CATEGORIES above
    forgot to mention, so a config.py addition that nobody categorised is
    still reachable rather than silently missing from the page (see
    SettingsPayloadTests' completeness guard, which currently keeps this at
    zero entries by keeping SETTINGS_CATEGORIES exhaustive).

    Read under runtime.config_reload_lock, because this page is what found the
    reload window (see that lock's comment). Saving a setting from here starts
    a rehash on a background thread and returns immediately; the browser then
    re-fetches this endpoint at once and, without the lock, read config while
    importlib.reload(defaults) was part way through re-executing it - so the
    Settings page rendered Nickname, Admin nick(s) and Channels blank on a
    daemon whose settings.conf had all three. The lock makes this wait for the
    reload it just triggered rather than reporting its halfway state.
    """
    import settings_file
    with runtime.config_reload_lock:
        return _settings_payload_unlocked(settings_file)


def _settings_payload_unlocked(settings_file):
    """build_settings_payload()'s body, split out only so the lock above wraps
    one expression and cannot be left un-taken by a later edit adding an early
    return."""
    types = settings_file.declared_types(vars(config))
    categories = []
    seen = set()
    for cat_id, label, names in SETTINGS_CATEGORIES:
        fields = []
        for name in names:
            if name not in types:
                continue
            value = getattr(config, name, None)
            if not settings_file.is_overridable(name, value):
                continue
            fields.append(_settings_field(name, types[name], value))
            seen.add(name)
        categories.append({"id": cat_id, "label": label, "fields": fields})

    # A leftover has no entry in SETTINGS_LABELS either - by construction, it
    # was never in SETTINGS_CATEGORIES, and SETTINGS_LABELS only ever gets a
    # name added alongside slotting it into a category. Falls back to the raw
    # name rather than raising, matching how it already reaches the page at
    # all despite being uncategorised.
    leftover = [n for n in types if n not in seen and n != "ADMIN_PASSWORD_HASH"
                and settings_file.is_overridable(n, getattr(config, n, None))]
    if leftover:
        categories.append({"id": "other", "label": "Other", "fields": [
            _settings_field(n, types[n], getattr(config, n, None))
            for n in sorted(leftover)]})

    return {
        "categories": categories,
        "admin_password_set": bool(getattr(config, "ADMIN_PASSWORD_HASH", "")),
    }


# Settings that only take effect on a full daemon restart - webserver.py owns
# a live listening socket and is deliberately excluded from
# commands.py's CORE_MODULES, so a rehash after saving one of these three
# cannot apply it live. Surfaced in the save response's "restart_required" so
# the frontend can tell the operator, rather than implying "rehash" fixed it.
# Settings a running daemon cannot pick up, whatever it reloads.
#
# NOT "everything to do with the network" - most of it IS live, and listing
# those here would train an operator to ignore the notice. The DCC port range
# is read per send, so a rehash applies it to the very next transfer; the
# channel list is compared and JOIN/PARTed by the rehash itself.
#
# What is genuinely stuck is anything read ONCE, against a socket already open:
#
#   WEBUI_*        oserve.startup() starts the dashboard thread once, bound to
#                  the host and port it read then. Changing them from inside
#                  that dashboard cannot move it.
#   SERVER / PORT  irc.py reads these in connect(), and the connection the bot
#                  is on was made with the old ones. #302 named this exactly -
#                  "when a user changes ports ... the changes do not take
#                  effect immediately" - and they were the two missing here, so
#                  the save said nothing and the operator was left to work out
#                  why the bot was still on the old server.
#
# A restart is PROMPTED, not performed. #302 offered either; restarting a
# process from inside itself while it holds live transfers is a different order
# of risk from telling the operator what to do next.
SETTINGS_RESTART_ONLY = {"WEBUI_ENABLED", "WEBUI_HOST", "WEBUI_PORT",
                         "SERVER", "PORT"}


# ---------------------------------------------------------------------
# SERVED FOLDERS
#
# A list of {label, path} pairs, which is why it is not on the Settings page
# with everything else: every other setting there is one scalar an operator
# types into one box, and settings_file.save() writes exactly that. This is
# ordered, validated as a SET (two folders can each be fine and still conflict
# with each other), and stored as JSON in its own file - so it gets its own
# endpoint pair rather than being bent into the settings shape.
#
# #164 step 4. Steps 1-3 shipped in v1.11.0 and made the daemon serve from a
# list of folders; until now the only way to WRITE that list was to create
# data/library_folders.json by hand, so the feature existed and no operator
# could reach it.
#
# `library` is imported inside each function, never at module scope: see
# tests/test_import_graph.py, which holds this module to a short list of
# imports that must work with no daemon running.
# ---------------------------------------------------------------------

def build_folders_payload():
    """GET /api/folders: the served folders, and where they came from.

    `source` is the honest answer to "what is this bot serving", which the
    folder list alone cannot give:

      "file"           - data/library_folders.json exists and is being used
      "file_directory" - no folder list; the single FILE_DIRECTORY is served
      "none"           - neither, so nothing is served at all

    An operator looking at one folder needs to know which of the first two
    they are in, because editing the list is what switches them.
    """
    import library

    stored = library.load_folders()
    single = str(getattr(config, "FILE_DIRECTORY", "") or "").strip()
    if stored:
        source = "file"
    elif single:
        source = "file_directory"
    else:
        source = "none"

    return {
        "folders": [{"name": entry.name, "path": entry.path}
                    for entry in library.folders()],
        "source": source,
        "file_directory": single,
        "path": library.folders_file(),
        # So the page can offer a Browse button, or say plainly why there
        # isn't one, instead of the operator wondering.
        "browser_enabled": bool(getattr(config, "WEBUI_FOLDER_BROWSER_ENABLED",
                                        False)),
    }


def browse_roots():
    """The top of the tree: drive letters on Windows, "/" everywhere else.

    Probed rather than assumed. A machine has the drives it has, and a letter
    that is mapped but disconnected raises rather than answering, which is why
    each one is tested individually and a failure just leaves it out.
    """
    if not platform_compat.IS_WINDOWS:
        return ["/"]

    found = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        try:
            if os.path.isdir(platform_compat.long_path(root)):
                found.append(root)
        except OSError:
            continue
    return found


def build_folder_browse_payload(raw_path):
    """GET /api/folders/browse: the immediate SUBDIRECTORIES of `raw_path`.

    DIRECTORIES ONLY, NEVER FILES. The caller is choosing a folder to serve;
    listing files would expose far more of the machine and answer nothing the
    picker asks. This is also why there are no sizes, no timestamps and no
    contents anywhere in the payload - a name and whether it can be opened is
    the whole of what a picker needs.

    An empty path means the top of the tree rather than the working directory,
    because "where does the operator start" has no sensible relative answer.

    Every entry is tested individually and a failure drops that entry rather
    than the listing: a directory holding one item the daemon may not stat -
    a system folder, a dead symlink, a disconnected network mount - must still
    list the other forty.
    """
    target = str(raw_path or "").strip()
    roots = browse_roots()

    if not target:
        return {"path": "", "parent": None, "at_root": True,
                "entries": [{"name": root, "path": root} for root in roots],
                "truncated": False}

    target = os.path.abspath(target)
    if not os.path.isdir(platform_compat.long_path(target)):
        return {"error": f"{target} is not a folder on this machine."}

    names = []
    try:
        with os.scandir(platform_compat.long_path(target)) as scan:
            for entry in scan:
                try:
                    if entry.is_dir():
                        names.append(entry.name)
                except OSError:
                    continue
    except OSError as err:
        return {"error": f"Could not read {target}: {err}"}

    names.sort(key=str.lower)
    truncated = len(names) > FOLDER_BROWSE_MAX_ENTRIES
    names = names[:FOLDER_BROWSE_MAX_ENTRIES]

    parent = os.path.dirname(target.rstrip(os.sep)) or None
    # At a drive root, dirname() answers the drive itself and the operator
    # would climb to where they already are. "" sends them to the root list.
    #
    # Compared with normcase, because browse_roots() builds its entries from
    # the uppercase letters A-Z while os.path.abspath() preserves whatever
    # case the caller sent. "c:\\" therefore missed the root list, and the
    # parent fell through to ntpath.dirname("c:") - which is "c:", a
    # DRIVE-RELATIVE path meaning "the current directory on C:". Clicking Up
    # from a lowercase drive root browsed the daemon's own working directory.
    # Found by audit.
    normalised_roots = {os.path.normcase(root) for root in roots}
    if parent == target or os.path.normcase(target) in normalised_roots:
        parent = ""

    return {
        "path": target,
        "parent": parent,
        "at_root": False,
        "entries": [{"name": name, "path": os.path.join(target, name)}
                    for name in names],
        "truncated": truncated,
    }


MAX_SERVED_LISTS = 16


def build_on_connect_payload():
    """GET /api/on-connect: the commands sent once registered, and the gap.

    THE COMMANDS COME BACK IN FULL, and that is a decision rather than an
    oversight. One of them is very likely an X login with a password in it, so
    the alternative is a box an operator can overwrite but never read - which
    means never correcting a typo without retyping the lot.

    They are already behind the dashboard login, and anybody who has that can
    read data/on_connect.json off the disk anyway. What must NOT happen is the
    text reaching a log or a debug channel, and that is enforced where it would
    happen - see on_connect.redacted().
    """
    import on_connect

    commands, delay = on_connect.load()
    return {
        "commands": commands,
        "delay_seconds": delay,
        "max_commands": on_connect.MAX_COMMANDS,
        "max_delay_seconds": on_connect.MAX_DELAY_SECONDS,
    }


def apply_on_connect_changes(payload):
    """POST /api/on-connect: validate the whole set, then write it.

    Returns (http_status, payload_dict).

    NO REHASH. These are read fresh at every connect, so the next one picks
    them up - and rehashing to apply them would be misleading, because what
    they need is a reconnect, not a reload. The response says so.
    """
    import on_connect

    if not isinstance(payload, dict):
        return 400, {"error": "Expected an object with 'commands'."}

    raw = payload.get("commands")
    if isinstance(raw, str):
        # The page sends a textarea, one command per line, because that is what
        # an operator pastes. Splitting here rather than in the browser keeps
        # the API usable by hand.
        raw = raw.splitlines()
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        return 400, {"error": "'commands' must be a list or a block of text."}

    delay = payload.get("delay_seconds", on_connect.DEFAULT_DELAY_SECONDS)

    try:
        written = on_connect.save(raw, delay)
    except ValueError as err:
        return 400, {"error": str(err)}
    except OSError as err:
        return 500, {"error": f"Could not write the commands: {err}"}

    return 200, {
        "commands": written,
        "delay_seconds": float(delay),
        "reconnect_required": bool(written),
        "message": ("Saved. They run when the bot next reconnects - or now, "
                    "with Resend commands." if written
                    else "Cleared. Nothing is sent on connect."),
    }


def build_on_connect_resend_result():
    """POST /api/on-connect/resend: send the SAVED commands to the server now
    (#1066). For when the automatic check gave up, or the operator wants the
    X login and +x again after a net split without reconnecting.

    Returns (http_status, payload_dict). The text of the commands is never in
    the answer or the log: an X login holds a password.
    """
    import on_connect

    commands, _gap = on_connect.load()
    if not commands:
        return 400, {"error": "No on-connect commands are saved."}
    oserve = sys.modules.get("oserve")
    sock = getattr(oserve, "irc_connection", None) if oserve else None
    if sock is None:
        return 409, {"error": "The bot is not connected, so nothing was sent."}
    # Not whether the channel is joined (#1085): a +r channel refuses the bot
    # exactly when the X login did not take, and sending the login again is
    # what this button is for. Only that this connection has sent its own,
    # so a press while it is still registering does not send them twice.
    if not on_connect.sent_on(getattr(config, "connection_epoch", None)):
        return 409, {"error": "The bot is still connecting; its on-connect "
                              "commands are about to go out, so nothing was sent."}
    try:
        sent = on_connect.resend_now(sock, getattr(config, "NICKNAME", ""))
    except Exception as err:
        return 500, {"error": f"Could not send them: {err}"}
    return 200, {"sent": sent,
                 "message": f"Sending {sent} on-connect command(s) to the server."}


def build_lists_payload():
    """GET /api/lists: every configured list, and whether one is implied.

    `source` answers "is this what I configured, or what the daemon fell back
    to", which the rows alone cannot: with no lists.json there is still
    exactly one list in this payload - the implicit one, over whatever
    FILE_DIRECTORY or the folder file resolved to - and an operator needs to
    know that editing it is what makes it real.
    """
    import library

    stored = library.load_lists()
    rows = []
    for entry in library.lists():
        rows.append({
            "name": entry.name,
            "primary": entry.primary,
            "channels": list(entry.channels),
            "modes": dict(getattr(entry, "modes", None) or {}),
            "folders": [{"name": f.name, "path": f.path} for f in entry.folders],
        })
        # Only when there is one (#1295), the way lists.json keeps `modes`:
        # a list without exclusions reads exactly as it did before.
        if getattr(entry, "exclude", None):
            rows[-1]["exclude"] = list(entry.exclude)
    return {
        "lists": rows,
        "source": "file" if stored else "implied",
        "max_lists": MAX_SERVED_LISTS,
    }


def apply_list_changes(payload):
    """POST /api/lists: validate the whole set, then write it.

    Returns (http_status, payload_dict).

    THE WHOLE SET, not each row, for the reason apply_folder_changes() gives
    and one more of its own: two lists can each be perfectly good and still be
    an invalid pair - the same channel bound to both, or the same name twice -
    and only the set can show that.

    AN EMPTY LIST IS ALLOWED and means "go back to one list over the
    configured folders". The file is REMOVED rather than written as [],
    because library.load_lists() already returns None for an empty file and
    falls back - so writing [] would leave a file on disk that does nothing.

    NO REHASH, same as folders: library.lists() re-reads on every call, so the
    running daemon picks this up immediately. It does NOT rebuild the
    published lists, and a list with no index is one nobody can request from -
    hence rebuild_required.
    """
    import library

    if not isinstance(payload, dict):
        return 400, {"error": "Expected an object with a 'lists' list."}
    rows = payload.get("lists")
    if not isinstance(rows, list):
        return 400, {"error": "'lists' must be a list."}
    if len(rows) > MAX_SERVED_LISTS:
        return 400, {"error": f"At most {MAX_SERVED_LISTS} lists."}

    # A row that does not carry 'exclude' keeps what the list of that name
    # has (#1295). The mIRC settings window, a dashboard tab opened before
    # the upgrade and the console's `served` transaction all send the whole
    # set without it; treated as "none", each of those saves would wipe every
    # list's exclusions without a word.
    stored_exclude = {entry.name.lower(): tuple(getattr(entry, "exclude", None) or ())
                      for entry in (library.load_lists() or [])}

    entries = []
    for row in rows:
        if not isinstance(row, dict):
            return 400, {"error": "Each list must be an object with a 'name'."}
        name = str(row.get("name", "") or "").strip()
        if len(name) > MAX_FOLDER_PATH_LEN:
            return 400, {"error": f"A list name is longer than "
                                  f"{MAX_FOLDER_PATH_LEN} characters."}
        raw_channels = row.get("channels")
        if raw_channels is None:
            raw_channels = []
        if not isinstance(raw_channels, list):
            return 400, {"error": f"{name or 'A list'}: 'channels' must be a list."}
        raw_modes = row.get("modes")
        if raw_modes is None:
            raw_modes = {}
        if not isinstance(raw_modes, dict):
            return 400, {"error": f"{name or 'A list'}: 'modes' must be an object "
                                  f"of channel -> mode."}
        if "exclude" in row:
            raw_exclude = row.get("exclude")
            if raw_exclude is None:
                raw_exclude = []
            if not isinstance(raw_exclude, (list, str)):
                return 400, {"error": f"{name or 'A list'}: 'exclude' must be a list "
                                      f"or a comma-separated text."}
        else:
            raw_exclude = stored_exclude.get(name.lower(), ())
        raw_folders = row.get("folders")
        if raw_folders is None:
            raw_folders = []
        if not isinstance(raw_folders, list):
            return 400, {"error": f"{name or 'A list'}: 'folders' must be a list."}
        if len(raw_folders) > MAX_SERVED_FOLDERS:
            return 400, {"error": f"{name or 'A list'}: at most "
                                  f"{MAX_SERVED_FOLDERS} folders."}

        folders = []
        for folder in raw_folders:
            if not isinstance(folder, dict):
                return 400, {"error": "Each folder must be an object with "
                                      "'name' and 'path'."}
            raw_path = str(folder.get("path", "") or "").strip()
            raw_name = str(folder.get("name", "") or "").strip()
            if (len(raw_path) > MAX_FOLDER_PATH_LEN
                    or len(raw_name) > MAX_FOLDER_PATH_LEN):
                return 400, {"error": f"A folder path or label is longer than "
                                      f"{MAX_FOLDER_PATH_LEN} characters."}
            folders.append(library.Folder(
                raw_name or library.default_label(raw_path), raw_path))

        entries.append(library.ServedList(
            name, bool(row.get("primary")),
            [str(c).strip() for c in raw_channels if str(c).strip()],
            folders,
            {str(c).strip().lower(): str(m or "").strip().lower()
             for c, m in raw_modes.items()
             if str(m or "").strip().lower() not in (library.NORMAL, "")},
            raw_exclude))

    if not entries:
        import os as os_mod
        try:
            os_mod.remove(library.lists_file())
        except OSError:
            pass
        return 200, {"lists": build_lists_payload()["lists"],
                     "rebuild_required": True,
                     "message": "Back to one list over the configured folders."}

    try:
        library.save_lists(entries)
    except ValueError as err:
        return 400, {"error": str(err)}
    except OSError as err:
        return 500, {"error": f"Could not write the list definitions: {err}"}

    return 200, {"lists": build_lists_payload()["lists"],
                 "rebuild_required": True,
                 "message": "Saved. Rebuild the lists to publish them."}


def apply_folder_changes(payload):
    """POST /api/folders: validate the whole set, then write it.

    Returns (http_status, payload_dict).

    THE WHOLE SET, not each row. Two folders can each be perfectly good and
    still be an invalid pair - one nested inside the other lists every file
    under it twice, and two sharing a label make the label useless for telling
    them apart. library.problems() returns every fault at once for the same
    reason: an operator fixing three things should be told about three things.

    AN EMPTY LIST IS ALLOWED and means "go back to the single Music
    directory". The file is REMOVED rather than written as [], because
    library.load_folders() already returns None for an empty list and falls
    back - so writing [] would leave a file on disk that does nothing, and the
    next operator to read it would have to work that out.

    NO REHASH. Unlike a settings save, nothing here needs reloading:
    library.folders() re-reads the file on every call, so the running daemon
    picks this up immediately. What it does NOT do is rebuild the published
    list, and a folder nobody can see in the list is not really served yet -
    hence rebuild_required, which the page turns into a sentence rather than
    leaving the operator to discover it.
    """
    import library

    if not isinstance(payload, dict):
        return 400, {"error": "Expected an object with a 'folders' list."}
    rows = payload.get("folders")
    if not isinstance(rows, list):
        return 400, {"error": "'folders' must be a list."}
    if len(rows) > MAX_SERVED_FOLDERS:
        return 400, {"error": f"At most {MAX_SERVED_FOLDERS} folders."}

    entries = []
    for row in rows:
        if not isinstance(row, dict):
            return 400, {"error": "Each folder must be an object with "
                                  "'name' and 'path'."}
        raw_path = str(row.get("path", "") or "").strip()
        raw_name = str(row.get("name", "") or "").strip()
        if len(raw_path) > MAX_FOLDER_PATH_LEN or len(raw_name) > MAX_FOLDER_PATH_LEN:
            return 400, {"error": f"A folder path or label is longer than "
                                  f"{MAX_FOLDER_PATH_LEN} characters."}
        # An unnamed folder is named after itself, exactly as the file format
        # does - so an operator who only wants to add a path can.
        entries.append(library.Folder(raw_name or library.default_label(raw_path),
                                      raw_path))

    faults = library.problems(entries)
    if faults:
        return 400, {"error": "Those folders cannot be served.",
                     "problems": faults}

    target = library.folders_file()
    if not entries:
        try:
            if os.path.exists(target):
                os.remove(target)
        except OSError as err:
            return 400, {"error": f"Could not remove {os.path.basename(target)}: {err}"}
        return 200, dict(build_folders_payload(), written=0,
                         rebuild_required=True)

    try:
        library.save_folders(entries)
    except (ValueError, OSError) as err:
        return 400, {"error": str(err)}

    return 200, dict(build_folders_payload(), written=len(entries),
                     rebuild_required=True)




def _save_settings_and_rehash(changes, confirmed_debug_removal=False, source=None, on_applied=None):
    """Write `changes` to settings.conf and dispatch a rehash on its own
    daemon thread. The shared tail of apply_settings_changes() (POST
    /api/settings) and build_password_change_result() (POST
    /api/settings/password): a plain function call does not skip a caller's
    own `if` checks, so build_password_change_result reaches this directly
    rather than through apply_settings_changes() - going through that
    function instead would always hit its own ADMIN_PASSWORD_HASH rejection,
    which exists to stop the *general* settings endpoint from being used to
    change the password, not to block the password endpoint's own write of
    the one setting it exists to change.

    The rehash runs detached rather than inline: commands.handle_rehash_request()
    does real socket I/O and importlib.reload()s several modules, which would
    freeze this request for its duration - the same reason adminchat.py's own
    _cmd_rehash wraps the identical call in _run_detached(). This module
    already has the same shape for a different slow/async action; see
    start_broadcast_search()'s threading.Thread(target=_close_window, ...).

    settings_file.save() holds its own lock around the read-modify-write of
    settings.conf, so two concurrent saves from here cannot race each other -
    see that function's docstring. Both settings_file.SettingsWriteError
    (save()'s own validation failures) and a plain OSError (e.g. an
    unwritable settings directory - the underlying atomic write can raise
    this too) are caught here and turned into a clean 400 JSON error; letting
    an OSError escape would surface as an unhandled 500 with a non-JSON body,
    which the frontend's postJson() cannot parse.

    Returns (http_status, payload_dict).
    """
    import settings_file
    try:
        result = settings_file.save(vars(config), changes)
    except (settings_file.SettingsWriteError, OSError) as err:
        return 400, {"error": str(err)}

    # Imported lazily, exactly like `list`/`dcc_fetch` above - see the module
    # docstring and tests/test_import_graph.py: `commands` must never load at
    # module scope, only from inside a handler that actually needs it.
    import commands
    # `source` is (who, where) for the rehash's log line. The admin console
    # saves through this same function (#1264) and names itself, so a change
    # made from mIRC is not logged as the dashboard's.
    who, where = source or (WEB_DASHBOARD_SOURCE, WEB_DASHBOARD_SOURCE)
    rehash_kwargs = {"authorised": True, "confirmed_debug_removal": confirmed_debug_removal}

    def rehash():
        # `on_applied` is told once the rehash has finished - the console's
        # settings window reloads its page then (#1264), not at the save,
        # when config still holds the old values: the rehash first waits up
        # to REHASH_TRANSFER_WAIT for transfers to finish.
        try:
            commands.handle_rehash_request(who, where, **rehash_kwargs)
        finally:
            if on_applied is not None:
                on_applied()

    threading.Thread(target=rehash, daemon=True).start()

    restart_required = sorted(set(result["written"]) & SETTINGS_RESTART_ONLY)
    return 200, dict(result, rehash="started", restart_required=restart_required)


def apply_settings_changes(changes, source=None, on_applied=None):
    """POST /api/settings's pure logic: validate `changes` (a flat
    {SETTING: "string value"} object - settings_file.save() coerces each
    value the same way settings.conf itself would be read), then hand off to
    _save_settings_and_rehash() for the actual write + dispatched rehash.

    `confirm_debug_channel_removed` (#1008 follow-up) is not a setting - it
    is popped out here, before `changes` ever reaches settings_file.save(),
    and threaded through as its own argument instead. It means nothing on
    its own: sync_channels() only ever reads it in the one case it exists
    for (DEBUG_CHANNEL going from a real channel to blank), so a stray or
    even malicious `true` sent on an unrelated save has no effect - there is
    nothing there for it to gate.

    Returns (http_status, payload_dict).
    """
    if not isinstance(changes, dict):
        return 400, {"error": "Expected a non-empty object of {SETTING: value}."}

    # Popped before the emptiness check below (the #1011 review):
    # a body whose only key is this flag is not a settings change, and used
    # to pass the check, save nothing and still start a rehash - harmless in
    # practice since the dashboard never sends the flag alone, but a body
    # this empty should 400 like any other.
    changes = dict(changes)
    confirmed_debug_removal = bool(changes.pop("confirm_debug_channel_removed", False))

    if not changes:
        return 400, {"error": "Expected a non-empty object of {SETTING: value}."}
    if "ADMIN_PASSWORD_HASH" in changes:
        return 400, {"error": "Use POST /api/settings/password to change the "
                               "admin password."}

    return _save_settings_and_rehash(changes, confirmed_debug_removal=confirmed_debug_removal,
                                     source=source, on_applied=on_applied)


def build_password_change_result(new_password, confirm_password):
    """POST /api/settings/password's pure logic: validate the pair, hash the
    new password the same way `python src/adminchat.py` does, and write it
    through _save_settings_and_rehash() - the same save-then-dispatch-rehash
    tail apply_settings_changes() uses, without its ADMIN_PASSWORD_HASH
    rejection (see that helper's docstring for why this cannot go through
    apply_settings_changes() itself).
    """
    if not new_password:
        return 400, {"error": "Password cannot be empty."}
    if new_password != confirm_password:
        return 400, {"error": "Passwords do not match."}

    new_hash = adminchat.make_password_hash(new_password)
    status, result = _save_settings_and_rehash({"ADMIN_PASSWORD_HASH": new_hash})
    if status == 200:
        # In force NOW, not when the rehash it started gets round to it: a
        # dashboard session is stamped with the password it was opened with
        # (_password_stamp), so this is also the moment every session opened
        # with the old password stops working. The rehash reads the same
        # hash back from settings.conf.
        config.ADMIN_PASSWORD_HASH = new_hash
    return status, result


# ==========================================================================
# Flask app - only built/used when Flask is actually installed.
# ==========================================================================

# Self-contained on purpose: the login page must render before a session
# exists, so it cannot depend on web/style.css (that request would itself be
# behind the login it is trying to render) or on any operator data.
LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>DCCore Dashboard - Login</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", sans-serif; background: #0b0f12;
          color: #e6edf0; display: flex; align-items: center; justify-content: center;
          height: 100vh; margin: 0; }}
  form {{ background: #131a1f; padding: 2rem 2.25rem; border-radius: 10px;
          min-width: 260px; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }}
  h1 {{ font-size: 1.05rem; margin: 0 0 1.25rem; font-weight: 600; }}
  input {{ width: 100%; padding: 0.55rem 0.6rem; margin-bottom: 1rem; box-sizing: border-box;
           background: #0b0f12; border: 1px solid #2a343b; border-radius: 6px; color: #e6edf0; }}
  button {{ width: 100%; padding: 0.55rem; background: #2dd4c8; color: #06231f; border: 0;
            border-radius: 6px; font-weight: 600; cursor: pointer; }}
  .error {{ color: #f87171; font-size: 0.85rem; margin: -0.75rem 0 1rem; }}
</style></head>
<body>
  <form method="post" action="/login">
    <h1>DCCore Dashboard</h1>
    {error_html}
    <input type="password" name="password" placeholder="Admin password" autofocus>
    <button type="submit">Log in</button>
  </form>
</body></html>"""


# Failed-login tracking for THIS route, deliberately separate from
# adminchat.py's own _bad_ips pool even though the policy (attempt count,
# block duration) is identical and reused from there directly. The password
# is shared with the DCC CHAT admin console on purpose - but the block budget
# is not, because if it were, an attacker guessing at this HTTP form could
# spend down the same counter and lock the real operator out of the DCC
# console too. Same policy, separate pools: one abusive address costs it
# access to this route only.
_web_bad_ips = {}
# address -> how many of its logins are being checked right now. A login
# claims its attempt here BEFORE the password is hashed (_reserve_web_login)
# and settles it after (_settle_web_login), so parallel requests from one
# address cannot all pass the limit before any of them is counted.
_web_logins_in_flight = {}
# Bound from runtime.py, not built here: every lock lives there (see
# tests/test_no_reloaded_module_owns_a_lock.py and runtime.py's comment).
_web_bad_ips_lock = runtime.web_login_lock

# How long a login waits for one of runtime.web_password_checks' slots
# before it is told the dashboard is busy. Long enough that a few people
# logging in at once all get through; short enough that a flood is answered
# rather than queued for ever.
WEB_PASSWORD_CHECK_WAIT_SECONDS = 10.0

# How many dashboard sessions are kept open at once (see create_app()'s
# live_sessions); past it the oldest is logged out.
WEB_MAX_LIVE_SESSIONS = 100


def _note_bad_web_login_locked(ip, now):
    """Count one failure for `ip`. The caller holds _web_bad_ips_lock."""
    # The addresses that never reached a block are forgotten after the
    # block window (#677): the expiry below only ever saw blocked ones,
    # and on an internet-exposed bind the rest stayed for ever.
    adminchat.forget_stale_failures(_web_bad_ips, now)
    entry = _web_bad_ips.get(ip) or [0, 0.0, now]
    entry[0] += 1
    entry[2] = now
    if entry[0] >= adminchat.MAX_PASSWORD_ATTEMPTS:
        entry[1] = now + adminchat.BAD_IP_BLOCK_SECONDS
    _web_bad_ips[ip] = entry


def _note_bad_web_login(ip):
    if not ip:
        return
    with _web_bad_ips_lock:
        _note_bad_web_login_locked(ip, time.time())


def _reserve_web_login(ip):
    """Claim one of `ip`'s login attempts before its password is checked.

    Returns "ok" (claimed - the caller MUST call _settle_web_login() once,
    whatever happens next), "blocked" (the address is blocked) or "busy"
    (its earlier failures plus the attempts still being checked already
    use up MAX_PASSWORD_ATTEMPTS).

    The check and the claim are one step under the pool's lock. They used
    to be apart: the login checked the address, hashed the password, and
    only then counted a failure, so N parallel requests from one address
    all passed the check and N guesses were verified where the limit allows
    MAX_PASSWORD_ATTEMPTS. Counting the attempts in flight closes that:
    however many arrive at once, at most the remaining allowance is hashed.
    """
    if not ip:
        return "ok"
    with _web_bad_ips_lock:
        now = time.time()
        adminchat.forget_stale_failures(_web_bad_ips, now)
        entry = _web_bad_ips.get(ip)
        if entry and entry[1]:
            if now < entry[1]:
                return "blocked"
            del _web_bad_ips[ip]  # block expired; forget it so a typo is not permanent
            entry = None
        failed = entry[0] if entry else 0
        in_flight = _web_logins_in_flight.get(ip, 0)
        if failed + in_flight >= adminchat.MAX_PASSWORD_ATTEMPTS:
            return "busy"
        _web_logins_in_flight[ip] = in_flight + 1
        return "ok"


def _settle_web_login(ip, outcome):
    """Release the attempt _reserve_web_login() claimed for `ip`.

    `outcome` is True for the right password (the address's failures are
    forgotten), False for a wrong one (counted, and blocking at the limit
    exactly as before) or None when the password was never checked (the
    dashboard was too busy) - released without counting.
    """
    if not ip:
        return
    with _web_bad_ips_lock:
        left = _web_logins_in_flight.get(ip, 0) - 1
        if left > 0:
            _web_logins_in_flight[ip] = left
        else:
            _web_logins_in_flight.pop(ip, None)
        if outcome is True:
            _web_bad_ips.pop(ip, None)
        elif outcome is False:
            _note_bad_web_login_locked(ip, time.time())


def _check_web_password(stored, supplied, wait=None):
    """adminchat.verify_password(), but never more than
    runtime.WEB_PASSWORD_CHECKS_AT_ONCE at a time across the dashboard.

    None when no slot came free within `wait` seconds: the password was
    not checked, and the caller says the dashboard is busy instead of
    counting anything against the address.
    """
    wait = WEB_PASSWORD_CHECK_WAIT_SECONDS if wait is None else wait
    if not runtime.web_password_checks.acquire(timeout=wait):
        return None
    try:
        return bool(adminchat.verify_password(stored, supplied))
    finally:
        runtime.web_password_checks.release()


def _is_bad_web_ip(ip):
    if not ip:
        return False
    with _web_bad_ips_lock:
        entry = _web_bad_ips.get(ip)
        if not entry:
            return False
        if entry[1] and time.time() >= entry[1]:
            del _web_bad_ips[ip]  # block expired; forget it so a typo is not permanent
            return False
        return bool(entry[1])


def _clear_bad_web_ip(ip):
    with _web_bad_ips_lock:
        _web_bad_ips.pop(ip, None)


def _host_name(host_header):
    """The name half of a Host header: lower-cased, without the port, the
    IPv6 brackets or a trailing dot."""
    text = (host_header or "").strip().lower()
    if text.startswith("["):
        return text[1:].split("]", 1)[0]
    if text.count(":") == 1:
        text = text.rsplit(":", 1)[0]
    return text.rstrip(".")


def _configured_dashboard_names():
    """The host names the operator has said the dashboard answers to:
    WEBUI_ALLOWED_HOSTS, plus WEBUI_HOST itself when that is a name."""
    raw = getattr(config, "WEBUI_ALLOWED_HOSTS", []) or []
    if isinstance(raw, str):
        raw = raw.replace(",", " ").split()
    names = {_host_name(str(item)) for item in raw}
    names.add(_host_name(str(getattr(config, "WEBUI_HOST", "127.0.0.1") or "")))
    names.discard("")
    return names


# This machine's own names, looked up once: socket.getfqdn() can ask DNS.
_machine_name_cache = []


def _machine_names():
    """This computer's own host names - what somebody on the LAN types to
    reach a dashboard bound to every interface. Its short name, its full
    name and the short name's mDNS (.local) spelling."""
    if not _machine_name_cache:
        import socket
        names = set()
        for lookup in (socket.gethostname, socket.getfqdn):
            try:
                name = _host_name(lookup())
            except (OSError, UnicodeError):
                continue
            if name:
                names.add(name)
                names.add(name.split(".", 1)[0])
                names.add(name.split(".", 1)[0] + ".local")
        _machine_name_cache.append(frozenset(names))
    return _machine_name_cache[0]


def _dashboard_host_ok(host_header, bound=None):
    """Is `host_header` a name this dashboard answers to?

    DNS REBINDING. A page on evil.example can point its own name at
    127.0.0.1 (or at the machine's LAN address) after it has loaded. The
    browser then sends that page's requests to the dashboard as SAME-ORIGIN
    - the Origin check (_login_origin_ok) compares Origin with Host, and
    both say evil.example - so the page could spend the operator's own
    login attempts and lock them out, and with the password it would reach
    every route. What the browser cannot change is the Host header: it
    always carries the name the page was loaded from. So the dashboard
    answers only to names that cannot be an attacker's:

      * any IP address - a page loaded from an IP literal is the dashboard
        (or whatever else answers on that address) and no DNS is involved;
      * localhost;
      * WEBUI_ALLOWED_HOSTS and WEBUI_HOST itself when it is a name - the
        names the operator configured (a reverse proxy, a LAN DNS name);
      * this machine's own host names, when the dashboard is bound to
        something other than loopback (WEBUI_HOST = 0.0.0.0) - so a phone on
        the LAN that opens http://<computer name>:8420/ still works.

    WEBUI_ALLOWED_HOSTS = * turns the check off. The port is not compared:
    a router's port forward or a proxy may change it, and it is not what a
    rebinding page controls.
    """
    import ipaddress

    name = _host_name(host_header)
    if not name:
        return False
    if name == "localhost":
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    configured = _configured_dashboard_names()
    if "*" in configured or name in configured:
        return True
    if not dashboard_is_loopback_only(bound):
        return name in _machine_names()
    return False


def _refuse_framing(response):
    """No other page may show the dashboard inside a frame (clickjacking).

    A page on another port of the same host is the same SITE to a browser,
    so the SameSite=Lax session cookie goes with a framed dashboard, and a
    click lured onto a framed button arrives from the dashboard's own page
    - past the Origin check. X-Frame-Options for older browsers, CSP
    frame-ancestors for current ones; the dashboard never frames itself.
    """
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    return response


def _password_stamp(secret_key, stored_hash):
    """What a dashboard session records about the password it was opened
    with: an HMAC of ADMIN_PASSWORD_HASH under the app's own secret. A
    session whose stamp no longer matches the current hash is refused, so
    changing the password ends every session opened with the old one. The
    hash itself never goes into the cookie, which the browser can read."""
    import hashlib
    import hmac
    key = secret_key if isinstance(secret_key, bytes) else str(secret_key).encode("utf-8")
    message = ("dccore-dashboard-session:" + str(stored_hash or "")).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:32]


def _origin_netloc(url):
    """host[:port] of an Origin, Referer or Host value, lower-cased, without
    a default port - a browser writes neither Origin nor Host with :80 or
    :443, but a proxy or a hand-typed URL may, and the two must still agree."""
    from urllib.parse import urlsplit
    netloc = urlsplit((url or "").strip()).netloc.lower()
    for default in (":80", ":443"):
        if netloc.endswith(default):
            netloc = netloc[:-len(default)]
    return netloc


def _login_origin_ok(origin, referer, host):
    """False when a login POST was sent by a page on another site.

    The failed-attempt pool above is keyed on the address, and on the default
    loopback install the operator's browser and any hostile page open in it
    both arrive as 127.0.0.1: MAX_PASSWORD_ATTEMPTS cross-site POSTs with a
    wrong password would block the operator's own login for
    BAD_IP_BLOCK_SECONDS, repeatable for ever (#609). Every browser puts the
    sending page's origin in Origin on a cross-site POST (a sandboxed frame
    sends the literal "null"), an older one only in Referer; whichever is
    present is compared with the Host header the request itself carries, the
    way _setup_host_ok() guards /setup. A request with neither header (curl,
    the test client) is not a browser forwarding another site's form and
    passes: this is a guard against the lockout, not a second password."""
    source = origin or referer
    if not source:
        return True
    sender = _origin_netloc(source)
    return bool(sender) and sender == _origin_netloc("//" + (host or "").strip())


# ---------------------------------------------------------------------
# CONSOLE
#
# A web-native alternative to the DCC CHAT admin console (adminchat.py) and
# the debug channel, for an operator who wants neither: no second IRC client
# to keep open, no channel broadcasting the daemon's internals to whoever
# joins it.
#
# Two halves, kept separate on purpose:
#   - the LOG is the ambient stream every announce.send_debug() call already
#     produces. Registering below as another consumer of the same fan-out
#     adminchat.py's own DCC sessions use (announce.add_debug_sink) - nothing
#     downstream of send_debug() has to learn a new destination exists, and
#     DEBUG_TO_CONSOLE (already on by default, independent of DEBUG_TO_CHANNEL
#     / DEBUG_CHANNEL) is the only switch that gates it - see send_debug()'s
#     own "ROUTING" comment. An operator who wants no debug channel at all
#     still gets this for free.
#   - a COMMAND is request/response: POST one line, get back exactly the
#     lines that one command produced, not interleaved with the ambient log.
#
# adminchat.COMMANDS is reused directly rather than re-implemented: a second
# copy of "queue", "status", "rehash" and the rest would drift the moment one
# of them changed - the same shape as every drift this codebase has already
# been bitten by (PRESERVE_RUNTIME, the two check-setup.py copies).
#
# announce.py is reloaded on every !rehash (it is in commands.CORE_MODULES),
# which resets announce._debug_sinks to [] - but commands.py's rehash handler
# already snapshots every live sink before that reload and reattaches the
# same objects after (see reattach_debug_sinks()), generically, regardless of
# which module registered them. webserver.py is NOT itself in CORE_MODULES -
# it is never reloaded - so _console_log and _console_next_id need none of
# the "survive my own reload" handling announce._debug_queue uses for itself.
# ---------------------------------------------------------------------

_console_log = collections.deque(maxlen=500)
_console_log_lock = threading.Lock()
_console_next_id = 1
_console_sink_registered = False


def _console_debug_sink(msg_text, category="INFO"):
    """announce.add_debug_sink's callback shape: (plain text, category).

    Must not block and must not raise - announce.py's own comment on the sink
    contract says why: send_debug() is called from the IRC read thread. A
    plain append under a short lock, the same shape as
    adminchat.Session.send().

    strip_irc_formatting(), not a fresh copy of it: some send_debug() callers
    embed mIRC control characters of their own inside msg_text for the
    channel's benefit, and adminchat.Session.debug_sink() already strips them
    for the identical reason - a console is read as a log, not rendered by an
    IRC client.
    """
    global _console_next_id
    with _console_log_lock:
        _console_log.append({
            "id": _console_next_id,
            "category": str(category),
            "time": time.time(),
            "text": adminchat.strip_irc_formatting(msg_text),
        })
        _console_next_id += 1


# The browser is already on the dashboard's login (#689, audit L25): the
# setup page's "Saved" screen polls /login and navigates there by itself
# as soon as the real app answers, so start() opening the dashboard as well
# gave a first run two tabs, one on /login and one on / (which redirects to
# /login). Set by run_setup_until_configured() when the page it served is
# going to do that; read and cleared by _open_in_browser(), once.
_browser_is_on_the_saved_page = False


def _open_in_browser(host, port, opener=None, log=print):
    """Open the dashboard in the default browser, if that was asked for.

    Not when the setup page's own browser tab is about to arrive here on
    its own (#689): a first run ends on one dashboard tab, not two.

    LOOPBACK ONLY, and not because of security - because of what the machine
    probably is. A dashboard bound to the LAN is as likely to be running on a
    headless box as on somebody's desktop, and a daemon that tries to spawn a
    browser there is doing something nobody asked for and nobody will see.

    Never fatal. A machine with no browser, no display, or a wayward
    BROWSER variable is not a reason to stop the bot starting - the address
    was printed a line above either way.
    """
    global _browser_is_on_the_saved_page
    if _browser_is_on_the_saved_page:
        _browser_is_on_the_saved_page = False
        log("[WEBUI] The setup page's tab opens the login by itself; not opening another.")
        return False
    if not getattr(config, "WEBUI_OPEN_BROWSER", True):
        return False
    if not dashboard_is_loopback_only(host):
        return False
    try:
        import webbrowser

        (opener or webbrowser.open)(f"http://{host}:{port}/")
        return True
    except Exception as err:
        log(f"[WEBUI] Could not open a browser ({err}); the dashboard is "
            f"still running at http://{host}:{port}/")
        return False


def dashboard_is_loopback_only(host=None):
    """Is the dashboard reachable only from the machine it runs on?

    The whole question the Console's default turns on. Anything that is not a
    loopback address means somebody else can reach the login page, and from
    there the Console is admin behind one factor.

    Unparseable is treated as NOT loopback. A host this cannot read is a host
    this cannot vouch for, and the safe answer to "is this exposed" is yes.
    """
    import ipaddress

    text = str(host if host is not None
               else getattr(config, "WEBUI_HOST", "127.0.0.1")).strip()
    if not text:
        return False
    if text.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def console_is_enabled():
    """Whether the dashboard's Console is available.

    An explicit True or False in the operator's own configuration always wins:
    somebody who has already made this choice does not get it made again for
    them on upgrade.

    With no explicit value the answer is "yes if nobody else can reach it".
    The Console puts ban/unban/clearqueue/rehash/update behind the dashboard
    password alone - one factor, over plain HTTP - which is a real widening
    when the dashboard is on the LAN and no widening at all when it is on
    loopback, where the only person who can reach it already has the files and
    admin_config.py.
    """
    configured = getattr(config, "WEBUI_CONSOLE_ENABLED", None)
    if configured is not None:
        return bool(configured)
    return dashboard_is_loopback_only()


def _quiet_the_request_log():
    """Stop werkzeug printing a line per HTTP request onto the operator's
    console, unless DEBUG_MODE asks for it.

    From the beta: "maybe those lines shouldnt be visible on cmd.exe except
    you run dccore on something like debug mode. you miss the important lines
    like search results etc".

    Exactly that. The dashboard polls five endpoints every two seconds, so an
    idle bot with one page open writes on the order of a hundred lines a
    minute:

        127.0.0.1 - - [...] "GET /api/console/log?since=33 HTTP/1.1" 200 -

    Every one of them says the same thing - the dashboard is still open - and
    together they push the lines that matter (a search, a transfer, a
    disconnect) off the screen faster than anyone can read them. The console
    is the operator's only view on a daemon with no window; filling it with
    the dashboard's own heartbeat costs them that view.

    SILENCED, NOT REDIRECTED. Werkzeug's log is a development convenience,
    and every request it reports is one this process just served itself - the
    information is not lost, it was never news. ERROR is left through so a
    genuine failure inside the server still reaches the console.

    Never raises: a logging tweak is not a reason for the dashboard not to
    start.
    """
    if getattr(config, "DEBUG_MODE", False):
        return
    try:
        import logging
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
    except Exception as err:
        print(f"[WEBUI] Could not quieten the request log: {err}")


def _ensure_console_sink():
    """Register the console's debug sink, at most once.

    Safe to call from more than one place - the log route and start() both
    do, since a test that calls build_console_log_payload() directly never
    runs start() - because announce.add_debug_sink() is itself idempotent
    (a no-op when the sink is already present). The module-level flag just
    avoids importing announce on every poll.
    """
    global _console_sink_registered
    if _console_sink_registered:
        return
    import announce
    announce.add_debug_sink(_console_debug_sink)
    _console_sink_registered = True


def build_console_log_payload(since=0):
    """Every console log line newer than `since`, plus the cursor to poll
    from next.

    A cursor rather than an offset/limit pair: this is a live stream a client
    polls repeatedly, and an offset would either replay lines already shown
    or, worse, silently skip ones that arrived between two polls. since=0 -
    the first poll - returns whatever is currently buffered (up to 500
    lines): a newly opened console showing recent history is strictly more
    useful than one that starts blank, and costs nothing extra since the
    buffer already exists.
    """
    _ensure_console_sink()
    try:
        since = int(since)
    except (TypeError, ValueError):
        since = 0
    with _console_log_lock:
        lines = [line for line in _console_log if line["id"] > since]
        cursor = _console_next_id - 1
    return {"lines": lines, "cursor": cursor}


# Commands that make no sense over a stateless HTTP request. "quit" closes a
# DCC CHAT session (session.close()) - _WebConsoleSession below has no socket
# to close and no persistent identity for that to mean anything about.
#
# "hello" switches a session to the structured feed (#550): it sets
# session.structured and pushes STATUS lines to a client that draws a window
# from them. A one-shot HTTP request has no feed to switch, and the dashboard's
# own Console is prose. It used to fail halfway - after printing the DCCORE
# HELLO line - on an attribute the shim did not have (#581).
_CONSOLE_UNSUPPORTED_COMMANDS = frozenset({"quit", "hello", "downloads"})
_CONSOLE_UNSUPPORTED_MESSAGES = {
    "quit": "'quit' closes a DCC CHAT session; there is not one here. Just close this tab.",
    "hello": "'hello' switches a DCC CHAT session to the structured feed for a script "
             "such as dccore.mrc; this console is the dashboard's own and has no feed to switch.",
    "downloads": "'downloads' tells a DCC CHAT session that its Downloads window is open; this console "
                 "has none. The Downloads page is the dashboard's own.",
}


class _WebConsoleSession:
    """Just enough of adminchat.Session's shape for COMMANDS' handlers, which
    is only ever .send() plus the two attributes handle_command() itself
    touches (.nick, .last_activity) - checked directly against adminchat.py,
    not guessed at. Never a real Session: no socket, no writer thread,
    nothing to close.
    """

    # What the handlers read besides .send(): pair asks .structured to choose
    # between a DCCORE TOKEN line and prose (#581). A web request is always
    # prose - it used to be missing, so `pair` wrote the new token to disk and
    # then raised before showing it.
    structured = False
    draws_dlqueue = False
    client = "web"
    # Behind the dashboard login, which is the admin password itself - so
    # unlocked, like a console that logged in with the password (#1264).
    unlocked = True

    def __init__(self, nick):
        self.nick = nick
        self.last_activity = time.time()
        self.lines = []

    def send(self, text=""):
        self.lines.append(str(text))


def build_console_command_result(command_text, remote_addr=None):
    """Run one admin command through adminchat.handle_command() and return
    exactly the lines it produced.

    Reuses the real command set rather than a second copy of it - see the
    CONSOLE section's own comment above for why.

    ASYNC COMMANDS RETURN AN ACKNOWLEDGEMENT, NOT THE RESULT. ban, unban,
    clearqueue, rehash and update each run the real work on a background
    thread (adminchat._run_detached) and reply immediately with only a
    "Banning ..." / "Rehashing ..." line - the same as a DCC CHAT session
    sees. Their eventual result is not lost: the handlers underneath all call
    announce.send_debug() when they finish, which reaches this same page
    through the LOG half above, a few seconds later. A caller of this
    function only ever gets what handle_command() produced synchronously.
    """
    # ASCII whitespace only, as adminchat.handle_command() strips (#1264
    # review): a settings value may end in a no-break space.
    stripped = str(command_text or "").strip(" \t\r\n")
    if not stripped:
        return 400, {"error": "No command given."}

    name = stripped.split(None, 1)[0].lower()
    if name in _CONSOLE_UNSUPPORTED_COMMANDS:
        return 200, {"lines": [_CONSOLE_UNSUPPORTED_MESSAGES.get(
            name, f"'{name}' is not available in this console.")]}

    session = _WebConsoleSession(f"web:{remote_addr or 'unknown'}")
    adminchat.handle_command(session, stripped)
    return 200, {"lines": session.lines}


if HAVE_FLASK:

    def _keep_json_key_order(app):
        """Turn off Flask's key sorting for every JSON answer (#1143).

        Sorting every key of every dict was most of what /api/fetch/status
        cost to serialise, and the Downloads view polls it every few seconds.
        Nothing reads the keys in sorted order: the page looks fields up by
        name and json.loads gives the same dict either way.

        app.json, the provider object, arrived in Flask 2.2; 2.0 and 2.1 (the
        floor requirements-web.txt allows) read app.config["JSON_SORT_KEYS"]
        instead, which 2.3 dropped. So the setting goes wherever the installed
        Flask looks for it.

        ensure_ascii is left on deliberately. Off, a filename os.listdir()
        returned as a lone surrogate is written out raw, Werkzeug cannot
        encode the body, and the whole route answers 500.
        """
        provider = getattr(app, "json", None)
        if provider is not None and hasattr(provider, "sort_keys"):
            provider.sort_keys = False
        else:
            app.config["JSON_SORT_KEYS"] = False

    def create_app():
        # Flask resolves a relative static_folder against this module's OWN
        # directory (its root_path) - now src/, not the repository root - so
        # this has to be the absolute path WEB_DIR already computed (#959).
        app = Flask(__name__, static_folder=WEB_DIR, static_url_path="")
        app.secret_key = os.urandom(32)
        # The mutating routes (broadcast search, fetch enqueue, list fetch)
        # all POST; Lax is the app's own decision instead of whatever the
        # visitor's browser happens to default to.
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
        # A CEILING ON THE REQUEST BODY. Without one, Flask reads whatever is
        # sent into memory before any route decides what to do with it, and
        # this daemon shares a machine with transfers it must not starve.
        # 8MB is far above every legitimate body here - the largest by a wide
        # margin is a pasted vars.ini, a text file of a few hundred lines -
        # and Flask answers anything past it with 413 rather than buffering
        # it. The login page is behind this too, which is the point: the
        # ceiling applies before authentication, where the daemon has the
        # least reason to trust what it is being handed.
        app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
        _keep_json_key_order(app)

        # SESSIONS THE SERVER CAN END. The cookie alone used to be the whole
        # session - {"authenticated": True}, signed - so a logout only
        # forgot it in the browser that clicked, and a copied cookie stayed
        # good until the daemon restarted. Now each login gets an id that
        # must still be in this table (a logout takes it out), and a stamp
        # of the password it was opened with (_password_stamp) that must
        # still match the current one (a password change ends them all).
        # Oldest dropped first past WEB_MAX_LIVE_SESSIONS: that is a lot of
        # browsers for one operator, and the table must not grow for ever.
        live_sessions = collections.OrderedDict()

        def start_session():
            import secrets
            sid = secrets.token_urlsafe(24)
            with runtime.web_sessions_lock:
                live_sessions[sid] = time.time()
                while len(live_sessions) > WEB_MAX_LIVE_SESSIONS:
                    live_sessions.popitem(last=False)
            # A fresh session, not the one the request came with: nothing
            # set before the login survives into the logged-in one.
            session.clear()
            session["authenticated"] = True
            session["sid"] = sid
            session["pw"] = _password_stamp(
                app.secret_key, getattr(config, "ADMIN_PASSWORD_HASH", ""))

        def session_is_live():
            import hmac
            if not session.get("authenticated"):
                return False
            sid, stamp = session.get("sid"), session.get("pw")
            if not isinstance(sid, str) or not isinstance(stamp, str):
                return False
            current = _password_stamp(app.secret_key,
                                      getattr(config, "ADMIN_PASSWORD_HASH", ""))
            if not hmac.compare_digest(stamp, current):
                return False
            with runtime.web_sessions_lock:
                return sid in live_sessions

        def end_session():
            sid = session.get("sid")
            if isinstance(sid, str):
                with runtime.web_sessions_lock:
                    live_sessions.pop(sid, None)
            session.clear()

        def restamp_session():
            """After this session changed the password: it stays open, every
            other session opened with the old password does not."""
            session["pw"] = _password_stamp(
                app.secret_key, getattr(config, "ADMIN_PASSWORD_HASH", ""))

        @app.after_request
        def deny_framing(response):
            return _refuse_framing(response)

        @app.before_request
        def require_login():
            # FIRST, before the login and before any file: a request for a
            # name this dashboard does not answer to is a DNS-rebinding
            # page, or a name the operator has not configured - refused
            # without reading the password and without counting anything
            # against the address (see _dashboard_host_ok).
            if not _dashboard_host_ok(request.host):
                text = ("This dashboard does not answer to the name {}. Open it by "
                        "its IP address or as localhost, or add the name to "
                        "WEBUI_ALLOWED_HOSTS.").format(_host_name(request.host) or "(none)")
                if request.path.startswith("/api/"):
                    return jsonify({"error": text}), 403
                return Response(text, status=403, mimetype="text/plain")
            # The sole exemption: everything else, static files included,
            # needs a session already marked authenticated by a prior POST
            # here. Endpoint rather than path, so a future route can't
            # accidentally slip past this by sharing a path prefix.
            if request.endpoint == "login":
                return None
            if not session_is_live():
                # A cookie from before a logout or a password change is
                # dropped, so the browser does not keep sending it.
                if session:
                    session.clear()
                if request.path.startswith("/api/"):
                    return jsonify({"error": "Authentication required."}), 401
                return redirect("/login")
            # EVERY POST IS CHECKED AGAINST ITS OWN HOST (#672, audit L8),
            # the way the login's is (#609). SameSite=Lax was the only
            # defence for the routes that take no JSON body - update-list,
            # purge-offline, a source's remove, a fetch's delete, the two
            # read marks - and "site" does not include the port: a plain
            # HTML form on any other local port (a dev server, a NAS UI)
            # submitted them with the operator's cookie attached. A browser
            # sends Origin on every POST, a form's included; a request with
            # neither header (curl, a script) is not a page forwarding
            # another site's form and passes, as at the login.
            if request.method == "POST" and not _login_origin_ok(
                    request.headers.get("Origin"), request.headers.get("Referer"),
                    request.host):
                return jsonify({"error": "This request was sent by another site "
                                         "and was ignored."}), 403
            return None

        @app.route("/login", methods=["GET", "POST"])
        def login():
            error = None
            if request.method == "POST":
                ip = request.remote_addr
                if not _login_origin_ok(request.headers.get("Origin"),
                                        request.headers.get("Referer"),
                                        request.host):
                    # Refused before the password is looked at, and never
                    # counted against the address: a foreign page must not
                    # be able to spend the operator's own attempts (#609).
                    error = ("This login was sent by another site and was "
                             "ignored. Open the dashboard at its own address.")
                    return LOGIN_PAGE.format(
                        error_html='<p class="error">{}</p>'.format(error)), 403
                # The attempt is claimed BEFORE the password is hashed and
                # settled after, so a burst of parallel POSTs from one
                # address gets no more guesses than the limit allows.
                claim = _reserve_web_login(ip)
                if claim == "blocked":
                    error, status = "Too many failed attempts. Try again later.", 401
                elif claim == "busy":
                    error, status = ("Another login from this address is still being "
                                     "checked. Try again in a moment."), 429
                else:
                    outcome = None
                    try:
                        outcome = _check_web_password(
                            getattr(config, "ADMIN_PASSWORD_HASH", ""),
                            request.form.get("password", ""))
                    finally:
                        _settle_web_login(ip, outcome)
                    if outcome:
                        start_session()
                        return redirect("/")
                    if outcome is None:
                        error, status = ("The dashboard is busy checking other logins. "
                                         "Try again in a moment."), 503
                    else:
                        error, status = "Incorrect password.", 401
            error_html = '<p class="error">{}</p>'.format(error) if error else ""
            return LOGIN_PAGE.format(error_html=error_html), (status if error else 200)

        # POST only (#673, audit L9): the page's own button is a form, and a
        # GET that changes state is a link any other site can make the
        # operator's browser follow with the Lax cookie attached - a cross-
        # site navigation to /logout dropped the dashboard to the login form.
        # Ended on the server too: a copy of this session's cookie is
        # refused from now on, not only the browser that clicked.
        @app.route("/logout", methods=["POST"])
        def logout():
            end_session()
            return redirect("/login")

        @app.route("/")
        def index():
            return app.send_static_file("index.html")

        @app.route("/api/queue")
        def api_queue():
            return jsonify(build_queue_payload(user=request.args.get("user")))

        @app.route("/api/queue/pack/cancel", methods=["POST"])
        def api_queue_pack_cancel():
            status, result = build_pack_cancel_result()
            return jsonify(result), status

        @app.route("/api/queue/move-user", methods=["POST"])
        def api_queue_move_user():
            status, result = build_queue_move_user_result(json_object(request.get_json(silent=True)))
            return jsonify(result), status

        @app.route("/api/queue/move-file", methods=["POST"])
        def api_queue_move_file():
            status, result = build_queue_move_file_result(json_object(request.get_json(silent=True)))
            return jsonify(result), status

        @app.route("/api/queue/remove-file", methods=["POST"])
        def api_queue_remove_file():
            status, result = build_queue_remove_file_result(json_object(request.get_json(silent=True)))
            return jsonify(result), status

        @app.route("/api/queue/clear", methods=["POST"])
        def api_queue_clear():
            status, result = build_queue_clear_result(json_object(request.get_json(silent=True)))
            return jsonify(result), status

        @app.route("/api/ignore", methods=["POST"])
        def api_ignore():
            status, result = build_ignore_result(json_object(request.get_json(silent=True)))
            return jsonify(result), status

        @app.route("/api/unignore", methods=["POST"])
        def api_unignore():
            status, result = build_unignore_result(json_object(request.get_json(silent=True)))
            return jsonify(result), status

        @app.route("/api/stats/import/variables")
        def api_stats_import_variables():
            # Served rather than written into app.js so the page's filter and
            # the parser cannot drift: a field added to omenserve_import.FIELDS
            # is kept by the page without the JavaScript changing at all. If
            # they disagreed, the page would strip out a counter the parser
            # was waiting for, and the operator would be told their file has
            # nothing in it.
            import omenserve_import
            return jsonify({"variables": list(omenserve_import.variable_names())})

        @app.route("/api/stats/import/preview", methods=["POST"])
        def api_stats_import_preview():
            # Reads and writes nothing. POST rather than GET because the
            # vars.ini text travels in the body - it is a file's contents,
            # not an identifier.
            body = json_object(request.get_json(silent=True))
            return jsonify(build_stats_import_preview(body.get("text", "")))

        @app.route("/api/stats/import-ktdata/preview", methods=["POST"])
        def api_stats_import_ktdata_preview():
            # KeepTrack's per-nick history (#1064). Reads and writes nothing.
            body = json_object(request.get_json(silent=True))
            status, result = build_ktdata_preview(body.get("text", ""))
            return jsonify(result), status

        @app.route("/api/stats/import-ktdata", methods=["POST"])
        def api_stats_import_ktdata():
            body = json_object(request.get_json(silent=True))
            status, result = apply_ktdata_import(body.get("text", ""))
            return jsonify(result), status

        @app.route("/api/stats/import", methods=["POST"])
        def api_stats_import():
            body = json_object(request.get_json(silent=True))
            status, result = apply_stats_import(body)
            return jsonify(result), status

        @app.route("/api/stats")
        def api_stats():
            # ?parts=transfer is what Live Transfers polls (#1123): the
            # figures it shows, not the files only Stats reads.
            asked = request.args.get("parts")
            if asked is None:
                return jsonify(build_stats_payload())
            parts = [part for part in asked.split(",") if part]
            unknown = sorted(set(parts) - set(STATS_PARTS))
            if unknown or not parts:
                return jsonify({"error": "parts: one or more of " + ", ".join(STATS_PARTS)}), 400
            return jsonify(build_stats_payload(parts))

        @app.route("/api/stats/record")
        def api_stats_record():
            # The transfer record (#1102). Asked for when Stats is opened and
            # when the period changes, never on the page's few-second poll:
            # these are queries over every row.
            status, result = build_record_payload(request.args.get("period", "all"))
            return jsonify(result), status

        @app.route("/api/stats/record/nick")
        def api_stats_record_nick():
            status, result = build_record_nick_payload(request.args.get("nick", ""),
                                                       request.args.get("period", "all"))
            return jsonify(result), status

        @app.route("/api/stats/record/forget", methods=["POST"])
        def api_stats_record_forget():
            status, result = apply_record_forget(json_object(request.get_json(silent=True)))
            return jsonify(result), status

        @app.route("/api/stats/record.csv")
        def api_stats_record_csv():
            period = request.args.get("period", "all")
            since, error = _record_since(period)
            if error:
                return jsonify({"error": error}), 400
            off = _record_off()
            if off:
                return jsonify(off), 409
            # Streamed: a record of years is written out a row at a time.
            return Response(record_csv_lines(since), mimetype="text/csv",
                            headers={"Content-Disposition":
                                     f'attachment; filename="dccore-transfers-{period}.csv"'})

        @app.route("/api/search")
        def api_search():
            return jsonify(build_search_payload(request.args.get("q", "")))

        @app.route("/api/filelists")
        def api_filelists():
            offset, limit = parse_pagination_params(
                request.args.get("offset"), request.args.get("limit"))
            # ?list= names one of OUR served lists; absent means the primary.
            # ?q= narrows it to rows matching every word in it (#399's
            # follow-up) - absent or blank means unfiltered, same as before.
            return jsonify(build_filelists_payload(
                offset, limit, name=requested_own_list(request.args.get("list")),
                q=request.args.get("q", "")))

        # ------------------------------------------------------------------
        # These routes below DO mutate state (queuing an outbound IRC line,
        # dialling an IP:port a foreign bot supplies) - which is exactly why
        # they sit behind the same require_login() as everything else, same
        # as every other route in this app. See the module docstring.
        # ------------------------------------------------------------------

        @app.route("/api/search/broadcast", methods=["POST"])
        def api_search_broadcast():
            body = json_object(request.get_json(silent=True))
            status, result = start_broadcast_search(body.get("term", ""))
            return jsonify(result), status

        @app.route("/api/search/broadcast/status")
        def api_search_broadcast_status():
            return jsonify(build_broadcast_status_payload())

        @app.route("/api/fetch/enqueue", methods=["POST"])
        def api_fetch_enqueue():
            payload = request.get_json(silent=True)
            status, result = build_fetch_enqueue_result(payload)
            return jsonify(result), status

        @app.route("/api/fetch/status")
        def api_fetch_status():
            return jsonify(build_fetch_status_payload())

        @app.route("/api/fetch/paused")
        def api_fetch_paused():
            import dcc_fetch
            return jsonify(dcc_fetch.paused_bots())

        @app.route("/api/fetch/pause", methods=["POST"])
        def api_fetch_pause():
            status, result = build_fetch_pause_result(request.get_json(silent=True), True)
            return jsonify(result), status

        @app.route("/api/fetch/resume", methods=["POST"])
        def api_fetch_resume():
            status, result = build_fetch_pause_result(request.get_json(silent=True), False)
            return jsonify(result), status

        @app.route("/api/tools/verify-list")
        def api_tools_verify_list():
            return jsonify(build_verify_list_payload())

        @app.route("/api/tools/update-list", methods=["POST"])
        def api_tools_update_list():
            status, result = start_list_update()
            return jsonify(result), status

        @app.route("/api/tools/update-list/status")
        def api_tools_update_list_status():
            return jsonify(build_update_list_status_payload())

        # #1182: Read audio info. Its progress is in the status above.
        @app.route("/api/tools/audio-info", methods=["POST"])
        def api_tools_audio_info():
            status, result = start_audio_info_reading()
            return jsonify(result), status

        # #572: what the version check last found, and a check on request.
        @app.route("/api/version-check")
        def api_version_check():
            import version_check
            return jsonify(version_check.state())

        @app.route("/api/version-check", methods=["POST"])
        def api_version_check_now():
            import version_check
            return jsonify(version_check.manual_check())

        @app.route("/api/filelists/fetch", methods=["POST"])
        def api_filelists_fetch():
            body = json_object(request.get_json(silent=True))
            status, result = build_list_fetch_enqueue_result(body.get("bot", ""))
            return jsonify(result), status

        @app.route("/api/filelists/fetch-folder-rar", methods=["POST"])
        def api_filelists_fetch_folder_rar():
            payload = request.get_json(silent=True)
            if isinstance(payload, list):
                status, result = build_folder_rar_batch_enqueue_result(payload)
                return jsonify(result), status
            body = json_object(payload)
            status, result = build_folder_rar_fetch_enqueue_result(
                body.get("bot", ""), body.get("folder", ""), body.get("marker", ""), body.get("channel", ""))
            return jsonify(result), status

        @app.route("/api/settings/theme-preview", methods=["POST"])
        def api_theme_preview():
            # POST because it carries what the operator has typed. It changes
            # nothing - see build_theme_preview() - and sits behind the same
            # login as every other route here.
            return jsonify(build_theme_preview(
                theme_preview_overrides(request.get_json(silent=True))))

        @app.route("/api/filelists/bots")
        def api_filelists_bots():
            # EVERY source the List Browser can show: ours, then theirs.
            #
            # Ours first and in the operator's own order rather than
            # alphabetically - they arranged that order themselves, and the
            # primary being first is the one piece of it the page should not
            # reshuffle. Composed HERE rather than inside either builder,
            # because each of those answers a question its own name states and
            # neither should start answering the other's.
            return jsonify(build_own_list_summaries()
                           + build_fetched_bot_list_summaries())

        @app.route("/api/filelists/sources", methods=["POST"])
        def api_filelists_add_source():
            body = json_object(request.get_json(silent=True))
            status, result = build_add_source_result(body.get("bot", ""))
            return jsonify(result), status

        @app.route("/api/filelists/sources/<nick>/remove", methods=["POST"])
        def api_filelists_remove_source(nick):
            # POST .../remove rather than DELETE, the shape /api/fetch/<id>/
            # delete already uses: one verb everywhere the page mutates.
            status, result = build_remove_source_result(nick)
            return jsonify(result), status

        @app.route("/api/filelists/purge-offline", methods=["POST"])
        def api_filelists_purge_offline():
            # POST, not DELETE: it does not name what to remove - the caller
            # asks "clear whatever is offline right now" rather than naming a
            # specific bot, so there is no resource URL for DELETE to name.
            status, result = build_purge_offline_fetched_lists_result()
            return jsonify(result), status

        @app.route("/api/filelists/purge-all", methods=["POST"])
        def api_filelists_purge_all():
            # Same shape as purge-offline right above, deliberately a
            # separate route rather than a flag on it (#1260): one clears
            # what is already gone, the other clears everything regardless
            # of whether it answers right now - different enough blast
            # radius that a typo flipping one into the other should not be
            # possible.
            status, result = build_purge_all_fetched_lists_result()
            return jsonify(result), status

        @app.route("/api/filelists/search")
        def api_filelists_search():
            # Read-only, and it stays that way: this answers a question about
            # lists already on disk and queues nothing. Selecting a result and
            # queueing it goes through the existing POST /api/fetch/enqueue,
            # which is where the admission control for that already lives.
            _offset, limit = parse_pagination_params(
                None, request.args.get("limit"))
            return jsonify(build_crosslist_search_payload(
                request.args.get("q", ""), limit,
                online_only=request.args.get("online", "") in ("1", "true")))

        @app.route("/api/filelists/bot/<nick>")
        def api_filelists_bot(nick):
            offset, limit = parse_pagination_params(
                request.args.get("offset"), request.args.get("limit"))
            status, result = build_fetched_bot_list_payload(
                nick, offset, limit, list_marker=request.args.get("list", ""),
                q=request.args.get("q", ""))
            if status == 200:
                # Opened: no longer new (#926 item 6).
                import list_fetch
                list_fetch.mark_seen(nick)
            return jsonify(result), status

        @app.route("/api/fetch/<request_id>/download")
        def api_fetch_download(request_id):
            import dcc_fetch
            # Snapshot just the two fields needed, under the same lock the
            # writers use, so "complete"/stored_filename can never be read as
            # a torn pair - the lock is released before send_from_directory()
            # touches the filesystem.
            with dcc_fetch._fetch_lock():
                row = getattr(config, "fetch_queue", {}).get(request_id)
                if row and row.get("state") == "complete" and row.get("stored_filename"):
                    stored_filename = row["stored_filename"]
                    download_name = row.get("filename") or stored_filename
                else:
                    stored_filename = None
            if not stored_filename:
                return jsonify({"error": "Unknown, incomplete, or failed fetch."}), 404
            # Never build this path from the URL parameter - request_id only
            # selects a row, and the row's OWN already-validated
            # stored_filename (set by dcc_fetch.py after it ran the offer's
            # filename through dcc.is_safe_path()) is what actually gets
            # opened.
            directory = os.path.abspath(getattr(config, "FETCHED_FILES_DIR", "./data/fetched"))

            # NOT send_from_directory(), and the reason is specific.
            #
            # dcc_fetch fits a stored name to MAX_NAME_BYTES (255) and touches
            # every path through platform_compat.long_path(). This route was
            # the one that did not, so on Windows a fetch with a long
            # remote-chosen name was written, marked "complete", listed in the
            # UI - and its Download button answered 404 for ever, while the
            # file sat there the whole time. Found by audit.
            #
            # Handing send_from_directory() the WRAPPED directory does not fix
            # it either: werkzeug's safe_join() joins with a FORWARD SLASH, and
            # a \\?\ path is the one kind Windows will not accept those in. So
            # the path is built here, with a backslash, and wrapped after.
            #
            # The containment safe_join() was providing is kept explicitly:
            # dcc.is_safe_path() is the same gate every request path in the
            # project uses, and it resolves symlinks, which safe_join() does
            # not. `import dcc` is deferred exactly like `import dcc_fetch`
            # above - see tests/test_import_graph.py.
            import dcc

            candidate = os.path.join(directory, stored_filename)
            if not dcc.is_safe_path(directory, candidate):
                print(f"[WEB SECURITY] Refused a fetch download outside "
                      f"{directory}: {stored_filename!r}")
                return jsonify({"error": "Unknown, incomplete, or failed fetch."}), 404

            wrapped = platform_compat.long_path(candidate)
            if not os.path.isfile(wrapped):
                return jsonify({"error": "The fetched file is no longer on disk."}), 404

            return send_file(wrapped, as_attachment=True,
                             download_name=download_name)

        @app.route("/api/fetch/clear", methods=["POST"])
        def api_fetch_clear():
            status, result = build_fetch_clear_result(request.get_json(silent=True))
            return jsonify(result), status

        @app.route("/api/fetch/<request_id>/delete", methods=["POST"])
        def api_fetch_delete(request_id):
            # Cancel says {"only_waiting": true} (#1046): it means a request
            # that has not started, so a row that finished while the page was
            # stale or its confirm dialog open is refused, never its file removed.
            body = json_object(request.get_json(silent=True))
            only = ("pending", "offered", "queued") if body.get("only_waiting") is True else None
            status, result = build_fetch_delete_result(request_id, only_states=only)
            return jsonify(result), status

        @app.route("/api/settings")
        def api_settings():
            return jsonify(build_settings_payload())

        @app.route("/api/settings", methods=["POST"])
        def api_settings_save():
            body = json_object(request.get_json(silent=True))
            status, result = apply_settings_changes(body)
            return jsonify(result), status

        @app.route("/api/folders")
        def api_folders():
            return jsonify(build_folders_payload())

        # 404 rather than 403 when it is off, for the reason the console
        # routes give: 403 confirms the route exists and is merely disabled,
        # which tells anyone probing that this build has one worth returning
        # for.
        def _browser_is_available():
            return bool(getattr(config, "WEBUI_FOLDER_BROWSER_ENABLED", False))

        @app.route("/api/folders/browse")
        def api_folders_browse():
            if not _browser_is_available():
                return jsonify({"error": "not found"}), 404
            payload = build_folder_browse_payload(request.args.get("path", ""))
            return jsonify(payload), (400 if "error" in payload else 200)

        @app.route("/api/folders", methods=["POST"])
        def api_folders_save():
            body = json_object(request.get_json(silent=True))
            status, result = apply_folder_changes(body)
            return jsonify(result), status

        @app.route("/api/lists")
        def api_lists():
            return jsonify(build_lists_payload())

        @app.route("/api/lists", methods=["POST"])
        def api_lists_save():
            body = json_object(request.get_json(silent=True))
            status, result = apply_list_changes(body)
            return jsonify(result), status

        @app.route("/api/on-connect")
        def api_on_connect():
            return jsonify(build_on_connect_payload())

        @app.route("/api/tools/stop", methods=["POST"])
        def api_tools_stop():
            # The Tools page's Stop the bot (#1065): answered first, then the
            # same stop as Ctrl-C a moment later, so the page hears back.
            import stopping
            stopping.request_stop_soon("asked from the dashboard")
            return jsonify({"stopping": True}), 200

        @app.route("/api/on-connect/resend", methods=["POST"])
        def api_on_connect_resend():
            status, result = build_on_connect_resend_result()
            return jsonify(result), status

        @app.route("/api/on-connect", methods=["POST"])
        def api_on_connect_save():
            body = json_object(request.get_json(silent=True))
            status, result = apply_on_connect_changes(body)
            return jsonify(result), status

        @app.route("/api/settings/password", methods=["POST"])
        def api_settings_password():
            body = json_object(request.get_json(silent=True))
            status, result = build_password_change_result(
                body.get("new_password", ""), body.get("confirm_password", ""))
            if status == 200:
                restamp_session()
            return jsonify(result), status

        # 404, not 403, when the Console is off. 403 would confirm the routes
        # exist and are merely disabled, which tells anyone probing that this
        # build has an admin console worth coming back for. Not found is the
        # honest answer to "is there a console here" when there is not one
        # reachable, and the operator who wants it is looking at the setting,
        # not at the URL.
        #
        # Both routes check independently rather than sharing a decorator: they
        # are the whole attack surface of this feature, and a decorator applied
        # to one and forgotten on the other is a silent hole. Two lines each is
        # cheap enough to not be clever about.
        def _console_is_available():
            # ONE answer to "is the Console on", shared with the startup
            # notice and with anything else that asks. Reading the setting
            # directly here would have kept the old flat default alive on the
            # only paths that actually gate the feature - the routes - while
            # everything else moved.
            return console_is_enabled()

        @app.route("/api/filelists/<path:source>/purge", methods=["POST"])
        def api_filelists_purge(source):
            # POST, and <path:source> because a bot's other lists are named
            # "<nick>/<marker>" - a plain <source> stops at the slash and
            # would 404 on exactly those rows.
            status, result = build_fetched_list_purge_result(source)
            return jsonify(result), status

        @app.route("/api/messages")
        def api_messages():
            payload, status = messages_payload_or_404()
            return jsonify(payload), status

        @app.route("/api/messages/read", methods=["POST"])
        def api_messages_read():
            # POST because it changes what the operator has acknowledged.
            payload, status = messages_read_or_404()
            return jsonify(payload), status

        @app.route("/api/notices")
        def api_notices():
            return jsonify(build_notices_payload())

        @app.route("/api/notices/read", methods=["POST"])
        def api_notices_read():
            # POST because it changes what the operator has acknowledged.
            return jsonify(mark_notices_read_result())

        @app.route("/api/console/log")
        def api_console_log():
            if not _console_is_available():
                return jsonify({"error": "not found"}), 404
            return jsonify(build_console_log_payload(request.args.get("since")))

        @app.route("/api/console/command", methods=["POST"])
        def api_console_command():
            if not _console_is_available():
                return jsonify({"error": "not found"}), 404
            body = json_object(request.get_json(silent=True))
            status, result = build_console_command_result(
                body.get("command", ""), request.remote_addr)
            return jsonify(result), status

        return app


# ---------------------------------------------------------------------------
# SET IT UP IN THE BROWSER (#547, Proposal 4)
#
# configure.py's questions are right; the terminal is the wrong place for a
# first-timer to answer them, on any OS. With no configuration at all -
# settings_file.REQUIRED still blank - oserve.startup() used to exit 1 with a
# message about admin_config.py.sample. Now, when Flask is present (Proposal
# 1's launchers make sure of that before the daemon starts), it serves ONE
# page on 127.0.0.1 until the form below has written settings.conf and
# admin_config.py exactly as configure.py writes them, then carries on down
# the same straight line it always ran. Same process, same port, freed and
# re-bound by the real dashboard a moment later; no launcher involvement.
#
# What makes it safe to run with no password yet:
#   - loopback only, never WEBUI_HOST (nothing is configured to read);
#   - it exists only while there is no configuration - the moment the form
#     has written one, the server stops, and the real app's login gate is
#     what answers on that port from then on;
#   - a ONE-TIME TOKEN. Loopback alone is not "only the person at the
#     machine": any website open in the same browser can POST to
#     127.0.0.1:8420, and with no password yet that POST would set ITS
#     password. So the token is printed in the terminal, put in the URL the
#     launcher opens, required on every request, and never in a form action
#     a page from another origin could guess; the Host header is checked
#     too, against DNS rebinding.
#
# The form is the Settings page's own field machinery (_settings_field), so
# the plain-language help from #545 and the fr/es strings from the lang
# files appear here without a second copy of either.
# ---------------------------------------------------------------------------

SETUP_FIELDS = ("NICKNAME", "SERVER", "CHANNEL", "ADMIN_NICK", "FILE_DIRECTORY",
                "WEBUI_ENABLED", "WEBUI_HOST", "CHECK_FOR_UPDATES")
SETUP_LANGS = ("en", "fr", "es")
_SETUP_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


def setup_page_is_possible():
    """Can the daemon offer /setup instead of exiting on a blank config?
    Flask, and nothing else: the page is loopback and one-shot by design,
    so there is no setting that turns it off."""
    return HAVE_FLASK


def _setup_strings(lang):
    """The lang file's strings for the setup page, or {} for English -
    English is what SETTINGS_LABELS and settings_help carry already."""
    lang = lang if lang in SETUP_LANGS else "en"
    if lang == "en":
        return {}
    path = os.path.join(WEB_DIR, "lang", f"{lang}.json")
    try:
        import json
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def build_setup_fields(lang="en", values=None):
    """The form's fields, from the same _settings_field() the Settings page
    uses, with the lang file's label and help laid over for fr/es. `values`
    are what the operator typed, redisplayed after an error."""
    import settings_file
    strings = _setup_strings(lang)
    types = settings_file.declared_types(vars(config))
    # A fresh page (nothing typed yet) starts with the dashboard box TICKED.
    # defaults.py ships WEBUI_ENABLED = False (convention 1: a listener ships
    # off), and read as-is that unticked the box on every first run - while
    # the intro, the folder placeholder and the folder error all defer the
    # music folder to "the dashboard's Settings page" (#603). A novice who
    # took that advice and did not notice the box ended with a bot that
    # served nothing and had no Settings page to fix it from. This page
    # exists only because Flask is installed - the launcher just installed
    # it for the dashboard - and the box still binds loopback unless the LAN
    # box is ticked too; unticking it stays one click. A redisplay after an
    # error keeps what the operator chose: an unticked box is simply absent
    # from the POST, so `values` then lacks the key and config's False wins.
    fresh = values is None
    values = values or {}
    fields = []
    for name in SETUP_FIELDS:
        current = values.get(name, getattr(config, name, None))
        if name in ("WEBUI_ENABLED", "CHECK_FOR_UPDATES"):
            # Both start ticked: the operator sees the choice and can untick
            # it (#572 - the daily version check is on by default, visibly).
            # On a redisplay the box is what was sent - absent is unticked,
            # never config's value: CHECK_FOR_UPDATES ships True, and falling
            # back to it re-ticked a box the operator had just unticked.
            current = True if fresh else str(values.get(name, "")).lower() in ("1", "on", "true", "yes")
        field = _settings_field(name, types.get(name, str), current)
        label = strings.get(f"settings.field.{name}")
        if label:
            field["label"] = label
        help_text = strings.get(f"settings.field.{name}.help")
        if help_text:
            field["help"] = help_text
        fields.append(field)
    return fields


def validate_setup_form(form, lang="en"):
    """The form's answers -> (changes, password_hash, errors). `changes` is
    the same {NAME: value} dict configure.collect_answers() builds - what
    was answered is written, what was left blank is not - so the files the
    page writes are the files the terminal writes. The error messages come
    from the lang file for fr/es, like the rest of the page (#621); the
    nickname problem itself (settings_file.nick_problem) stays English."""
    import settings_file
    strings = _setup_strings(lang)
    errors = []
    changes = {}

    def text(name):
        return str(form.get(name, "") or "").strip()

    def say(key, english):
        return strings.get(key, english)

    def not_a_nick(problem):
        return say("setup.error.nickname_invalid",
                   "That is not an IRC nickname: {problem}.").replace("{problem}", problem)

    nickname = text("NICKNAME")
    if not nickname:
        errors.append(("NICKNAME", say("setup.error.nickname_needed", "A nickname is needed.")))
    elif settings_file.nick_problem(nickname):
        errors.append(("NICKNAME", not_a_nick(settings_file.nick_problem(nickname))))
    else:
        changes["NICKNAME"] = nickname

    server = text("SERVER")
    if not server:
        errors.append(("SERVER", say("setup.error.server_needed",
                                     "An IRC server is needed (irc.undernet.org is "
                                     "the usual one).")))
    elif settings_file.server_problem(server):
        # ":" and "/" as well as a space (#687): "irc.undernet.org:6667" and
        # a pasted irc:// URL were accepted and the bot looped on
        # getaddrinfo every ten seconds with nothing saying why.
        errors.append(("SERVER", say("setup.error.server_shape",
                                     "A server name has no spaces, no port and no irc:// - "
                                     "just the host, e.g. irc.undernet.org (the port is a "
                                     "separate setting).")))
    else:
        changes["SERVER"] = server

    channel = text("CHANNEL")
    channels = [c.strip() for c in channel.split(",") if c.strip()]
    if not channels:
        errors.append(("CHANNEL", say("setup.error.channel_needed",
                                      "At least one channel is needed, like #mychannel.")))
    elif settings_file.channels_problem(channel):
        # settings_file's rule, not a copy of it (#1272): configure.py and the
        # Settings page accepted what this refused, so "the files the page
        # writes are the files the terminal writes" was not true of CHANNEL.
        errors.append(("CHANNEL", say("setup.error.channel_shape",
                                      "Each channel starts with # and has no spaces; "
                                      "separate several with commas.")))
    else:
        changes["CHANNEL"] = ",".join(channels)

    admin_nick = text("ADMIN_NICK")
    if not admin_nick:
        errors.append(("ADMIN_NICK", say("setup.error.admin_needed",
                                         "Your own nick is needed - the person who may "
                                         "run the admin commands.")))
    elif settings_file.nicks_problem(admin_nick):
        errors.append(("ADMIN_NICK", not_a_nick(settings_file.nicks_problem(admin_nick))))
    else:
        changes["ADMIN_NICK"] = admin_nick

    # Optional (#811): not a real setting name, wrapped into ADMIN_HOSTMASKS
    # below. Blank is a supported, unremarkable answer - it leaves the
    # console exactly as unconfigured as it always shipped.
    admin_host = text("ADMIN_HOST")
    if admin_host:
        problem = settings_file.admin_host_problem(admin_host)
        if problem:
            errors.append(("ADMIN_HOST", say("setup.error.admin_host_shape",
                                             "That is not a services host: {problem}.").replace("{problem}", problem)))
        else:
            changes["ADMIN_HOSTMASKS"] = [f"*!*@{admin_host}"]

    password = str(form.get("password", "") or "")
    confirm = str(form.get("password_confirm", "") or "")
    password_hash = None
    if not password:
        errors.append(("password", say("setup.error.password_needed",
                                       "A password is needed - it opens the admin "
                                       "console and this dashboard.")))
    elif password != confirm:
        errors.append(("password", say("setup.error.password_mismatch",
                                       "The two passwords do not match.")))
    else:
        password_hash = adminchat.make_password_hash(password)

    enable_webui = str(form.get("WEBUI_ENABLED", "") or "").lower() in ("1", "on", "true", "yes")

    file_directory = text("FILE_DIRECTORY")
    if file_directory:
        if os.path.isdir(file_directory):
            changes["FILE_DIRECTORY"] = file_directory
        else:
            # "Later on the Settings page" is only true with the dashboard on;
            # with the box unticked the folder can only be set in the file (#603).
            errors.append(("FILE_DIRECTORY",
                           say("setup.error.folder_missing",
                               "That folder does not exist. Leave it blank to choose "
                               "it later on the dashboard's Settings page.")
                           if enable_webui else
                           say("setup.error.folder_missing_no_dashboard",
                               "That folder does not exist. With the dashboard off, "
                               "leave it blank and set FILE_DIRECTORY in settings.conf "
                               "later, or tick the dashboard box to choose it on its "
                               "Settings page.")))

    changes["WEBUI_ENABLED"] = enable_webui
    # #572: ticked unless the operator unticked it; a box left unticked is
    # not sent at all, which is the "off".
    changes["CHECK_FOR_UPDATES"] = str(form.get("CHECK_FOR_UPDATES", "") or "").lower() in ("1", "on", "true", "yes")
    if enable_webui:
        lan = str(form.get("WEBUI_LAN", "") or "").lower() in ("1", "on", "true", "yes")
        changes["WEBUI_HOST"] = "0.0.0.0" if lan else "127.0.0.1"

    return changes, password_hash, errors


def apply_setup(changes, password_hash, log=print, settings_path=None, admin_path=None):
    """Write both files as configure.py does, then make the running process
    see them: settings.conf through settings_file.apply_to(), the password
    by hand - admin_config.py was imported (or not) long before this and
    is not re-imported. The two paths are for tests; a real run writes
    where configure.py writes."""
    import configure
    import settings_file
    # admin_config.py FIRST. The two writes are not one transaction, and
    # only one order fails safe: once settings.conf carries NICKNAME, CHANNEL
    # and ADMIN_NICK the REQUIRED gate is satisfied, so if it were written
    # first and the password write then failed (the file held open by an
    # editor or a scanner, a full disk), a restart would skip this page,
    # join IRC and refuse the dashboard for the missing hash - with no way
    # back to the form. A hash written before settings.conf hurts nothing:
    # the gate still trips, the page is offered again and the next attempt
    # replaces the line in place (#624).
    # The writer returns the settings.conf path when THAT file also sets
    # ADMIN_PASSWORD_HASH (#676, audit L12): defaults.py applies it after
    # admin_config.py, so after a restart the hash written here loses to
    # it. The writer prints the warning to the daemon's window; the person
    # at the form is in a browser and never saw it - so it is returned, and
    # the saved page says it too.
    # BUT ONLY ONCE settings.conf IS KNOWN TO TAKE THE SAVE (#1272). A
    # settings.conf with one malformed line cannot be edited, so the second
    # write failed after the first had already replaced ADMIN_PASSWORD_HASH:
    # the page showed a write error and the password had changed anyway.
    # check_save() runs every check save() runs and writes nothing, so a save
    # that is going to fail fails here, with both files untouched.
    settings_file.check_save(vars(config), changes, path=settings_path)
    shadow = configure.write_admin_config_password(password_hash, path=admin_path)
    configure.write_settings_conf(changes, path=settings_path)
    settings_file.apply_to(vars(config), path=settings_path, log=log)
    config.ADMIN_PASSWORD_HASH = password_hash
    # apply_to() assigned NICKNAME but did not re-run the derivations that
    # depend on it; the list rebuild is a new process and does (#590).
    config.derive_list_base_name()
    return {"written": sorted(changes),
            "shadowed_by": os.path.basename(shadow) if shadow else None}


def _setup_host_ok(host_header):
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        host = host.split("]", 1)[0] + "]"
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return host in _SETUP_HOSTS


def _html(text):
    import html
    return html.escape(str(text if text is not None else ""), quote=True)


def render_setup_page(fields, token, lang="en", errors=(), values=None, port=8420):
    """The one page. Server-rendered - the real dashboard's app.js is behind
    the login and is not loaded here; the page has no script but the
    language switch, which is a link."""
    strings = _setup_strings(lang)
    errors = dict(errors)
    values = values or {}
    rows = []
    for field in fields:
        name = field["name"]
        label = _html(field["label"])
        help_text = field.get("help") or ""
        help_html = (f' <span class="help" title="{_html(help_text)}">?</span>'
                     if help_text else "")
        error_html = (f'<div class="error">{_html(errors[name])}</div>'
                      if name in errors else "")
        value = values.get(name, field.get("value"))
        if name in ("WEBUI_ENABLED", "CHECK_FOR_UPDATES"):
            checked = " checked" if (value is None or value in (True, "on", "1", "true")) else ""
            rows.append(f'<label class="check"><input type="checkbox" name="{name}" value="1"{checked}> '
                        f'{label}{help_html}</label>')
            continue
        if name == "WEBUI_HOST":
            lan = str(value or "") == "0.0.0.0" or values.get("WEBUI_LAN") in ("1", "on")
            checked = " checked" if lan else ""
            rows.append(f'<label class="check sub"><input type="checkbox" name="WEBUI_LAN" value="1"{checked}> '
                        f'{_html(strings.get("setup.lan", "Reachable from other devices on my network (phone, laptop), not only this machine"))}'
                        f'{help_html}</label>')
            continue
        placeholder = ""
        if name == "SERVER" and not value:
            value = "irc.undernet.org"
        if name == "CHANNEL":
            placeholder = ' placeholder="' + _html(strings.get("setup.channel_placeholder", "#mychannel, #another")) + '"'
        if name == "FILE_DIRECTORY":
            placeholder = ' placeholder="' + _html(strings.get("setup.folder_placeholder", "optional - can be chosen later on the Settings page")) + '"'
        rows.append(f'<label>{label}{help_html}<input type="text" name="{name}" '
                    f'value="{_html(value or "")}"{placeholder} autocomplete="off"></label>{error_html}')
        if name == "ADMIN_NICK":
            # Not a real setting field (#811): the operator types the host
            # alone, and validate_setup_form() wraps it as ADMIN_HOSTMASKS =
            # ["*!*@<host>"]. Optional, so no placeholder value is offered -
            # a blank here is a real, supported "skip it" answer, not an
            # unanswered required field.
            admin_host_error = (f'<div class="error">{_html(errors["ADMIN_HOST"])}</div>'
                                if "ADMIN_HOST" in errors else "")
            rows.append(
                f'<label>{_html(strings.get("setup.admin_host", "Your services host (optional)"))}'
                f' <span class="help" title="{_html(strings.get("setup.admin_host_help", "Locks the admin console, and the in-channel admin commands once this is set, to your account rather than just your nick. Log into services, set +x, then /whois yourself for the host - looks like yourname.users.undernet.org."))}">?</span>'
                f'<input type="text" name="ADMIN_HOST" value="{_html(values.get("ADMIN_HOST", ""))}" '
                f'placeholder="{_html(strings.get("setup.admin_host_placeholder", "optional - blank leaves the console open to the nick alone"))}" '
                f'autocomplete="off"></label>{admin_host_error}')
    password_error = f'<div class="error">{_html(errors["password"])}</div>' if "password" in errors else ""
    rows.append(f'<label>{_html(strings.get("setup.password", "Admin password"))}'
                f' <span class="help" title="{_html(strings.get("setup.password_help", "Opens the admin console and this dashboard. Kept as a hash, never in clear."))}">?</span>'
                f'<input type="password" name="password" autocomplete="new-password"></label>')
    rows.append(f'<label>{_html(strings.get("setup.password_again", "The same password again"))}'
                f'<input type="password" name="password_confirm" autocomplete="new-password"></label>{password_error}')
    switch = " ".join(
        f'<a href="/setup?token={_html(token)}&amp;lang={code}"{" class=on" if code == lang else ""}>{code.upper()}</a>'
        for code in SETUP_LANGS)
    from string import Template
    return Template(SETUP_PAGE).substitute(
        title=_html(strings.get("setup.title", "Set up DCCore")),
        intro=_html(strings.get("setup.intro", "A few questions and the bot can start. Everything else is on the dashboard's Settings page afterwards, behind the password you choose here.")),
        rows="\n".join(rows), token=_html(token), lang=_html(lang), switch=switch,
        submit=_html(strings.get("setup.submit", "Save and start the bot")),
        note=_html(strings.get("setup.note", "Only this machine can reach this page, and only until the settings are saved.")))


def render_setup_saved_page(changes, lang="en", port=8420, shadowed_by=None):
    strings = _setup_strings(lang)
    dashboard = bool(changes.get("WEBUI_ENABLED"))
    from string import Template
    warning = ""
    if shadowed_by:
        # The password just chosen works until the next restart, and then
        # the hash in settings.conf wins (#676). Said on the page, where the
        # person who chose it is.
        text = strings.get("setup.saved.shadowed",
                           "{file} also sets ADMIN_PASSWORD_HASH, and it is applied after admin_config.py - "
                           "so after the next restart the password you just chose will stop working. "
                           "Remove the ADMIN_PASSWORD_HASH line from {file}, or change the password from "
                           "the dashboard, which writes to that file.")
        warning = '<p class="warn">%s</p>' % _html(text.replace("{file}", str(shadowed_by)))
    return Template(SETUP_SAVED_PAGE).substitute(
        title=_html(strings.get("setup.saved.title", "Saved - starting the bot")),
        body=_html(strings.get("setup.saved.dashboard" if dashboard else "setup.saved.no_dashboard",
                               "The bot is starting. This page opens the dashboard's login as soon as it answers - log in with the password you just chose."
                               if dashboard else
                               "The bot is starting. You chose no dashboard, so this page has nothing more to show: the bot's window is where it reports from now on. Close this tab.")),
        warning=warning,
        poll="true" if dashboard else "false", port=str(port))


SETUP_PAGE = """<!doctype html>
<html lang="$lang"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
<style>
  body { font-family: -apple-system, "Segoe UI", sans-serif; background: #0b0f12; color: #e6edf0; margin: 0; padding: 2rem 1rem; }
  form { background: #131a1f; padding: 1.75rem 2rem 1.5rem; border-radius: 10px; max-width: 34rem; margin: 0 auto; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }
  h1 { font-size: 1.15rem; margin: 0 0 0.5rem; font-weight: 600; }
  p.intro { color: #9fb0ba; font-size: 0.9rem; margin: 0 0 1.25rem; line-height: 1.45; }
  label { display: block; font-size: 0.85rem; margin-bottom: 0.9rem; color: #c9d4da; }
  label.check { display: flex; align-items: center; gap: 0.5rem; }
  label.check.sub { margin-left: 1.6rem; }
  input[type=text], input[type=password] { display: block; width: 100%; padding: 0.55rem 0.6rem; margin-top: 0.3rem; box-sizing: border-box;
    background: #0b0f12; border: 1px solid #2a343b; border-radius: 6px; color: #e6edf0; font-size: 0.95rem; }
  input[type=checkbox] { accent-color: #2dd4c8; }
  .help { display: inline-block; width: 1.1rem; height: 1.1rem; line-height: 1.1rem; text-align: center; border-radius: 50%; background: #23303a; color: #9fd8d3; font-size: 0.75rem; margin-left: 0.35rem; cursor: help; }
  .error { color: #f87171; font-size: 0.82rem; margin: -0.5rem 0 0.9rem; }
  button { width: 100%; padding: 0.65rem; background: #2dd4c8; color: #06231f; border: 0; border-radius: 6px; font-weight: 600; font-size: 0.95rem; cursor: pointer; margin-top: 0.5rem; }
  .langs { text-align: right; font-size: 0.75rem; margin-bottom: 0.5rem; }
  .langs a { color: #9fb0ba; text-decoration: none; margin-left: 0.6rem; }
  .langs a.on { color: #2dd4c8; font-weight: 600; }
  p.note { color: #6b7c86; font-size: 0.75rem; margin: 1rem 0 0; }
</style></head>
<body>
  <form method="post" action="/setup">
    <div class="langs">$switch</div>
    <h1>$title</h1>
    <p class="intro">$intro</p>
    <input type="hidden" name="token" value="$token">
    <input type="hidden" name="lang" value="$lang">
$rows
    <button type="submit">$submit</button>
    <p class="note">$note</p>
  </form>
</body></html>"""

SETUP_SAVED_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>$title</title>
<style>
  body { font-family: -apple-system, "Segoe UI", sans-serif; background: #0b0f12; color: #e6edf0; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
  div { background: #131a1f; padding: 2rem 2.25rem; border-radius: 10px; max-width: 30rem; box-shadow: 0 4px 24px rgba(0,0,0,0.4); }
  h1 { font-size: 1.05rem; margin: 0 0 0.75rem; font-weight: 600; }
  p { color: #9fb0ba; font-size: 0.9rem; line-height: 1.5; margin: 0; }
  p.warn { color: #fbbf24; margin-top: 0.9rem; }
</style></head>
<body><div><h1>$title</h1><p>$body</p>$warning</div>
<script>
  if ($poll) {
    setInterval(function () {
      fetch("/login", {cache: "no-store"}).then(function (r) {
        if (r.ok) { window.location.href = "/login"; }
      }).catch(function () {});
    }, 2000);
  }
</script>
</body></html>"""


if HAVE_FLASK:

    def create_setup_app(token, on_done, port=8420):
        """The setup-only app: /setup, and nothing the real app has."""
        app = Flask("dccore-setup", static_folder=None)
        app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
        # `bound`: the browser the code was first presented from (#675,
        # audit L11). The link is handed to the OS opener, and on Linux
        # that is xdg-open with the URL in argv, which the browser keeps in
        # its own argv for as long as it runs - readable by any other local
        # user via ps, for the whole setup window, on a host where 127.0.0.1
        # is reachable by everyone. So the code is good for one browser:
        # the first request that carries it gets a cookie, and from then on
        # the code is accepted only together with that cookie. A second
        # browser with the code from ps is refused, and if it was somehow
        # first, the operator's own is - loudly, with what to do.
        state = {"done": False, "changes": None, "bound": None, "shadowed_by": None}
        SETUP_COOKIE = "dccore-setup"

        def refused(why):
            return (f"<!doctype html><meta charset='utf-8'><p style='font-family:sans-serif'>"
                    f"{_html(why)}</p>"), 403

        @app.before_request
        def gate():
            if not _setup_host_ok(request.headers.get("Host", "")):
                return refused("This page answers only to 127.0.0.1.")
            if state["done"]:
                return None
            supplied = request.args.get("token") or request.form.get("token") or ""
            import hmac
            if not hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
                return refused("Open the exact link printed in DCCore's window - it "
                               "carries a one-time code, so that only the person at "
                               "this machine can set the bot up.")
            if state["bound"] is None:
                import secrets
                state["bound"] = secrets.token_urlsafe(24)
                g.setup_cookie_to_set = state["bound"]
                return None
            held = request.cookies.get(SETUP_COOKIE, "")
            if not hmac.compare_digest(held.encode("utf-8"), state["bound"].encode("utf-8")):
                return refused("This link has already been opened in another browser, "
                               "and the code in it is good for one. If that was not "
                               "you, stop DCCore and start it again: it prints a new "
                               "link with a new code.")
            return None

        @app.after_request
        def bind_the_first_browser(response):
            value = getattr(g, "setup_cookie_to_set", None)
            if value:
                response.set_cookie(SETUP_COOKIE, value, httponly=True, samesite="Lax", path="/")
            return _refuse_framing(response)

        @app.route("/")
        def root():
            return redirect(f"/setup?token={token}")

        @app.route("/setup", methods=["GET", "POST"])
        def setup():
            lang = (request.values.get("lang") or "en").lower()
            lang = lang if lang in SETUP_LANGS else "en"
            if state["done"]:
                return render_setup_saved_page(state["changes"] or {}, lang, port,
                                               shadowed_by=state["shadowed_by"])
            if request.method == "GET":
                return render_setup_page(build_setup_fields(lang), token, lang, port=port)
            changes, password_hash, errors = validate_setup_form(request.form, lang)
            if errors:
                values = {k: v for k, v in request.form.items() if k not in ("password", "password_confirm", "token")}
                return render_setup_page(build_setup_fields(lang, values), token, lang,
                                         errors=errors, values=values, port=port), 400
            try:
                applied = apply_setup(changes, password_hash)
            except Exception as err:  # a full disk, a read-only folder
                message = _setup_strings(lang).get("setup.error.write", "Could not write the settings: {error}")
                return render_setup_page(build_setup_fields(lang, dict(request.form)), token, lang,
                                         errors=[("NICKNAME", message.replace("{error}", str(err)))],
                                         values=dict(request.form), port=port), 500
            state["done"] = True
            state["changes"] = changes
            state["shadowed_by"] = (applied or {}).get("shadowed_by")
            on_done(changes)
            return render_setup_saved_page(changes, lang, port, shadowed_by=state["shadowed_by"])

        @app.route("/login")
        def not_yet():
            # The saved page polls this; while the setup server still holds
            # the port the answer is "not yet", and once the real app has
            # it, its own login page answers with 200 and the poll redirects.
            return "", 503

        return app

    def run_setup_until_configured(host="127.0.0.1", port=None, log=print, opener=None,
                                   wait=None, token=None):
        """Serve /setup until the form has written the configuration; then
        stop and return what was written, so oserve.startup() can carry on.
        Returns None when the page could not be served at all (the port is
        taken), in which case the caller falls back to its old refusal."""
        import secrets
        from werkzeug.serving import make_server
        port = port or getattr(config, "WEBUI_PORT", 8420)
        token = token or secrets.token_urlsafe(24)
        done = threading.Event()
        result = {}

        def on_done(changes):
            result["changes"] = changes
            done.set()

        app = create_setup_app(token, on_done, port=port)
        try:
            server = make_server(host, port, app, threaded=True)
        except (OSError, SystemExit) as err:
            # SystemExit too: werkzeug's server calls sys.exit(1) on a bind
            # failure ("port in use", printed by it to stderr), and a taken
            # port must not take the daemon down - the caller falls back to
            # its refusal. That SystemExit printed as "(1)" here, and the
            # only causes and the only ways out went unsaid (#617).
            reason = "the port is taken" if isinstance(err, SystemExit) else str(err)
            log(f"[SETUP] Could not open the setup page on {host}:{port} - {reason}.")
            log("[SETUP] Is another DCCore still running, maybe in a minimised window? "
                "Stop it, or free the port, or put another WEBUI_PORT in settings.conf - "
                "or answer the questions in the terminal instead: python3 configure.py")
            return None
        thread = threading.Thread(target=server.serve_forever, name="dccore-setup", daemon=True)
        thread.start()
        url = f"http://{host}:{port}/setup?token={token}"
        log("[SETUP] No configuration yet. Set DCCore up in your browser:")
        log(f"[SETUP]     {url}")
        log("[SETUP] (The code in the link is what lets only you use this page. "
            "The bot starts as soon as the form is saved.)")
        _quiet_the_request_log()
        opened = False
        if getattr(config, "WEBUI_OPEN_BROWSER", True):
            try:
                import webbrowser
                opened = bool((opener or webbrowser.open)(url))
            except Exception as err:
                log(f"[SETUP] Could not open a browser ({err}); open the link above yourself.")
        # A page on this machine's 127.0.0.1 is no use to someone who is not at
        # it (#595). webbrowser.open() says False on a machine with no browser
        # and this used to ignore that, then wait for ever with nothing on
        # screen saying how else to go on.
        if not opened:
            log("[SETUP] No browser was opened here. If this machine is one you reach over "
                "SSH, tunnel the port (ssh -L "
                f"{port}:127.0.0.1:{port} <this machine>) and open the link on your own "
                "computer.")
        log("[SETUP] To answer the questions in this window instead, press Ctrl-C and run: "
            "python3 configure.py")
        try:
            # A loop rather than one wait(): a Windows console cannot deliver
            # Ctrl-C into an indefinite Event.wait(). `wait`, for tests, is
            # asked between waits whether to give up.
            give_up = wait or (lambda: False)
            while not done.wait(0.5):
                if give_up():
                    break
        except KeyboardInterrupt:
            # Ctrl-C is how somebody who cannot reach the page leaves; it used to
            # end in a traceback with nothing about what to do next (#595).
            log("[SETUP] Stopped. Answer the questions here instead: python3 configure.py")
        finally:
            # A moment for the "Saved" page to reach the browser before the
            # socket goes away; then the port is free for the real dashboard.
            if done.is_set():
                time.sleep(0.5)
            server.shutdown()
            server.server_close()
        if not done.is_set():
            return None
        # The saved page in the browser polls /login and goes there when the
        # dashboard answers (#689) - exactly when the dashboard was chosen
        # and a browser was opened here. start() must not open a second tab.
        global _browser_is_on_the_saved_page
        _browser_is_on_the_saved_page = bool(opened and result["changes"].get("WEBUI_ENABLED"))
        log(f"[SETUP] Settings written: {', '.join(result['changes'])}. Starting.")
        return result["changes"]


def start():
    """Run the dashboard. Called on its own daemon thread from oserve.startup().

    Logs and returns rather than raising on any of: Flask missing, the feature
    disabled via config, or the port being unavailable - none of those may take
    the daemon down with them.
    """
    if not HAVE_FLASK:
        print("[WEBUI] Flask not installed; dashboard disabled.")
        return
    # See oserve.startup(): absent means off, the same way config.py ships it.
    if not getattr(config, "WEBUI_ENABLED", False):
        print("[WEBUI] Disabled via config.WEBUI_ENABLED = False.")
        return
    if not adminchat.password_is_configured():
        print("[WEBUI] ADMIN_PASSWORD_HASH is not set; refusing to start the dashboard "
              "without a login. Generate one with `python src/adminchat.py` and put the "
              "result in admin_config.py or settings.conf.")
        return

    # 127.0.0.1 when absent, matching config.py. 0.0.0.0 would bind every
    # interface and put the dashboard on the LAN, which is the opposite of
    # what a missing setting should buy anyone - even with a login gate, that
    # is a decision the operator should make explicitly, not by omission.
    host = getattr(config, "WEBUI_HOST", "127.0.0.1")
    port = getattr(config, "WEBUI_PORT", 8420)
    _ensure_console_sink()
    app = create_app()
    print(f"[WEBUI] Dashboard starting on http://{host}:{port}/ (login required).")
    if console_is_enabled():
        print("[WEBUI] The Console is on. It reaches ban, rehash and update "
              "behind this one password - see WEBUI_CONSOLE_ENABLED.")
    _quiet_the_request_log()
    _open_in_browser(host, port)
    try:
        # use_reloader=False is NOT optional: Flask's reloader re-execs the whole
        # process, and this runs on an already-live daemon thread - a re-exec
        # here would take the entire bot down with it, not just the dashboard.
        app.run(host=host, port=port, threaded=True, use_reloader=False, debug=False)
    except Exception as run_err:
        print(f"[WEBUI] Dashboard stopped: {run_err}")
