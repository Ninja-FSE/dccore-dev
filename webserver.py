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

NO AUTHENTICATION ON ANY ROUTE, INCLUDING THE MUTATING ONES ADDED FOR CROSS-BOT
SEARCH/FETCH. See the WEBUI_HOST comment in config.py: this is the same
deliberate operator decision for a LAN-only deployment, not an oversight -
extended, on purpose, to /api/search/broadcast and /api/fetch/* even though
those routes DO mutate state (they queue an outbound IRC line, and dial an
IP:port a foreign bot supplies). The operator was explicitly warned what that
means before choosing to proceed. See the heavy comment block directly above
each mutating route in create_app() below - do not add a new mutating route
without adding the same warning there, and do not add authentication-shaped
workarounds (an API key in a query string, an unchecked cookie) without
actually implementing authentication; that only adds false confidence.

Nothing here is added to commands.py's modules_to_reload. A !rehash reload
re-executes this module's body, which would try to re-bind a live listening
socket out from under app.run() - the same reasoning that already excludes
adminchat.py. Route handlers read config fresh via getattr() on every request
instead, so a rehash's new values (e.g. a changed WEBUI_* setting takes effect
only on the next daemon restart, but MAX_DCC_SLOTS, the queue, etc. are always
current) are visible without needing a reload of this module.
"""

import os
import sys
import threading
import time

import config

try:
    from flask import Flask, jsonify, request, send_from_directory
    HAVE_FLASK = True
except ImportError:
    HAVE_FLASK = False


# A browser tab is not an IRC channel: MAX_SEARCH_RESULTS (config.py, default 5)
# is sized to avoid flooding a channel and is the wrong number here. This is a
# module constant, not a config setting - it is a display cap on one page, not
# an operator-facing tunable like WEBUI_PORT.
WEBUI_MAX_SEARCH_RESULTS = 50

# Issue #76, option 3: GET /api/filelists and GET /api/filelists/bot/<nick>
# used to serialise every row of the list into one HTTP response (~36,208
# rows / ~12MB of JSON for this operator's own list, on every page view).
# These two constants bound how much of an already-parsed row list actually
# gets sent in one response - the parsing cost itself is unchanged (both
# endpoints already parsed everything every call; only what gets shipped over
# HTTP now differs).
#
# FILELISTS_DEFAULT_PAGE_SIZE (used when `?limit=` is omitted or invalid): 200
# rows is comfortably within the 100-300 range a plain HTML <table> renders
# instantly at, on any of this dashboard's supported screen sizes - a module
# constant, not a config.py tunable, same reasoning as WEBUI_MAX_SEARCH_RESULTS
# above: an internal display default, not an operator-facing knob.
FILELISTS_DEFAULT_PAGE_SIZE = 200

# FILELISTS_MAX_PAGE_SIZE: the ceiling `?limit=` is clamped to, regardless of
# what a caller asks for. Without this, a single `?limit=999999999` request
# would reintroduce exactly the problem this pagination feature exists to
# close - one response carrying the entire list again. 2000 is a generous 10x
# over the default (room for an operator who genuinely wants a bigger page,
# or a future "load more" control that fetches several pages at once) while
# still capping any one response to a small multiple of a real page, not the
# tens of thousands of rows a full list can contain.
FILELISTS_MAX_PAGE_SIZE = 2000


def parse_pagination_params(raw_offset, raw_limit):
    """Turn `?offset=&limit=` query-string values - always strings, or None
    when the parameter was omitted entirely - into a validated (offset, limit)
    pair of ints, for GET /api/filelists and GET /api/filelists/bot/<nick>.

    Never raises: a missing, non-numeric, or negative value silently falls
    back to a sane default rather than erroring the route - the same "never
    trust a query parameter to already be a well-formed integer" discipline
    every other web-input boundary in this module already applies (see
    reject_if_unsafe_for_irc_line() above for the mutating-route equivalent).
    `limit` is additionally clamped to FILELISTS_MAX_PAGE_SIZE - see its
    comment for why - and a non-positive `limit` (0 or negative) is treated
    the same as "omitted", not as "give me nothing back".
    """
    try:
        offset = int(raw_offset)
    except (TypeError, ValueError):
        offset = 0
    if offset < 0:
        offset = 0

    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = FILELISTS_DEFAULT_PAGE_SIZE
    if limit <= 0:
        limit = FILELISTS_DEFAULT_PAGE_SIZE
    if limit > FILELISTS_MAX_PAGE_SIZE:
        limit = FILELISTS_MAX_PAGE_SIZE

    return offset, limit


def reject_if_unsafe_for_irc_line(value, field_name):
    """Return an error string if `value` is not safe to interpolate into an
    outbound raw IRC line, or None if it is safe.

    Every mutating route in this module eventually hands web input to
    oserve.queue_message(), whose one job is to sit in a per-user list until
    queue_mgr.queue_worker() writes it straight to the live socket
    (`current_sock.send(msg.encode())`) - no re-validation, no re-splitting on
    the way out. A value that is not actually a string gets silently
    str()-coerced by a careless caller (see the bot/filename type-confusion
    bug this replaced), and a string containing an embedded \\r, \\n or \\x01
    lets the caller smuggle one or more ADDITIONAL raw IRC lines - QUIT,
    JOIN/PART an arbitrary channel, PRIVMSG/NOTICE as this bot - or close a
    CTCP wrapper early, past whatever single line/CTCP the route intended to
    send. All are rejected here, at the boundary, before the value goes
    anywhere near an outbound message.

    The actual byte check is dcc_fetch.contains_unsafe_ctcp_bytes() - THE
    single canonical definition of "which bytes are unsafe for an outbound
    IRC/CTCP line" in this codebase (see that function's comment for why
    this used to be two independently-maintained copies, and no longer is).
    Imported locally, not at module level, to keep this module's "importing
    it must never fail, even with nothing but the stdlib installed" property
    (see the module docstring) independent of dcc_fetch's own import graph.

    Shared because it is already needed in >= 2 places (POST
    /api/search/broadcast's `term`, POST /api/fetch/enqueue's `bot` and
    `filename`, POST /api/filelists/fetch's `bot`) - any future route that
    builds an outbound IRC line from web input must run every such value
    through this too.
    """
    if not isinstance(value, str):
        return f"'{field_name}' must be a string."
    import dcc_fetch
    if dcc_fetch.contains_unsafe_ctcp_bytes(value):
        return f"'{field_name}' must not contain line breaks or control characters."
    return None


# ==========================================================================
# Pure data-building functions - no Flask, unit tested directly.
# ==========================================================================

def build_queue_payload(user=None):
    """The Queue view's data.

    With no `user`, one summary row per queued user (their status, a preview
    of the next file, and how many are waiting) - what the Queue table shows.
    With `user`, the full file list queued for that one user instead - what a
    click-through or `?user=<nick>` on /api/queue returns.

    No lock is taken, matching adminchat.py's _cmd_queue/_cmd_status idiom: a
    shallow dict()/list() copy of the live containers, read without a lock,
    is good enough for a status display and cannot deadlock against the
    daemon's own queue processing.
    """
    queue = dict(getattr(config, "dcc_queue", {}))
    frozen = dict(getattr(config, "frozen_queues", {}))
    active = list(getattr(config, "active_transfers", []))
    sending_users = {str(tx.get("user", "")).lower() for tx in active}

    if user:
        user_key = str(user).strip().lower()
        entries = queue.get(user_key, [])
        if user_key in sending_users:
            status = "sending"
        elif user_key in frozen:
            status = "frozen"
        elif entries:
            status = "queued"
        else:
            status = "empty"
        files = [e.get("file", "?") if isinstance(e, dict) else str(e) for e in entries]
        return {"user": user_key, "status": status, "count": len(entries), "files": files}

    rows = []
    for user_key, entries in queue.items():
        status = "sending" if user_key in sending_users else ("frozen" if user_key in frozen else "queued")
        first = entries[0] if entries else None
        preview = first.get("file", "?") if isinstance(first, dict) else (str(first) if first is not None else "")
        rows.append({"user": user_key, "preview": preview, "count": len(entries), "status": status})
    return rows


def build_search_payload(query):
    """The Search view's data: up to WEBUI_MAX_SEARCH_RESULTS matches for `query`.

    Delegates the actual scan to list.find_matching_entries() - the same
    IRC-agnostic function execute_search() now calls - so a web search and an
    IRC @find agree on what matches. This just applies a browser-sized limit
    and its own JSON shape instead of IRC formatting.
    """
    import list as list_mod
    import re

    raw_clean = str(query or "")
    clean_term = re.sub(r'[-*_.]', ' ', raw_clean)
    search_words = [w.strip().lower() for w in clean_term.split() if w.strip()]
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


def build_filelists_payload(offset=0, limit=None):
    """The File Lists view's data: a page of THIS bot's own master list.

    v1 scope is deliberately THIS BOT ONLY - it serves DCCore's own master
    list, decomposed into rows, with "source" hardcoded to config.NICKNAME.
    Real cross-bot deduplication (parsing other bots' adverts in the channel)
    is out of scope; this dedup is the trivial single-source case, collapsing
    only the same filename listed under two different folders.

    Issue #76, option 3: still parses the ENTIRE list every call, exactly as
    before (this bot's own list has no size cap of its own, so there is no
    cheaper way to know how many rows exist or to dedup correctly) - only
    what gets returned is now a page of it, `[offset:offset+limit]`, plus the
    `total` row count, instead of the whole thing. `limit=None` (the route's
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

    entries, _total = list_mod.find_matching_entries([], limit=None)
    rows = list_mod.entries_to_filelist_rows(entries, getattr(config, "NICKNAME", "?"))
    total = len(rows)
    page = rows[offset:offset + limit]
    return {"entries": page, "total": total, "offset": offset, "limit": limit}


# ==========================================================================
# Cross-bot fetched file lists (mutating enqueue + read-only lookups). Another
# bot's full list, fetched via dcc_fetch.py's request_type="list" rows,
# extracted and parsed by list_fetch.py, and kept switchable in
# config.fetched_bot_lists (keyed by lowercased bot nick, one entry per bot -
# a later fetch for the same nick REPLACES it, see list_fetch.py). Pure logic
# here, same reasoning as every other build_*_payload()/build_*_result()
# function in this module: no Flask import, fully unit testable.
# ==========================================================================

def build_list_fetch_enqueue_result(bot_raw):
    """POST /api/filelists/fetch's pure logic: validate the bot nick and
    enqueue a request_type="list" row.

    Deliberately reuses dcc_fetch.enqueue_fetch() (extended with a
    request_type parameter) rather than build_fetch_enqueue_result() above:
    that function's shape - a {"bot","filename"} object or a list of them -
    is the file-fetch multi-select shape the Search view's "Download
    selected" and the Download tab's bulk-paste box both produce, and a
    list-fetch request has no filename at all (we do not know what the
    target bot will name its list zip - see dcc_fetch.py's module docstring
    for why request_type="list" matches on bot alone). Bolting an optional
    "type" field onto that shape would make every existing caller of it - and
    every existing test of it - reason about a case that never applies to
    them. The two HTTP-facing validators stay separate; the actual queue,
    dispatcher, admission control, size cap and transfer code they both feed
    into is the exact same one, all the way through (see dcc_fetch.py).

    Returns (http_status, payload_dict) with "created" (the new request id,
    as a one-element list, so the frontend can treat this the same shape as
    the file-fetch enqueue response).
    """
    import dcc_fetch

    bot_err = reject_if_unsafe_for_irc_line(bot_raw, "bot")
    if bot_err:
        return 400, {"error": bot_err}

    bot = bot_raw.strip()
    if not bot:
        return 400, {"error": "'bot' is required."}

    request_id = dcc_fetch.enqueue_fetch(bot, "", request_type="list")
    return 200, {"created": [request_id]}


def build_fetched_bot_list_summaries():
    """GET /api/filelists/bots payload: one row per bot with a fetched list
    currently available, for the File Lists view's switcher control.

    "count" reads the "entry_count" field process_fetched_list_zip() computes
    ONCE at fetch time (issue #76, option 2) rather than the actual row list -
    which is no longer stored at all - so this stays a cheap dict lookup even
    though the File Lists tab's switcher polls this route every
    FILELISTS_BOTS_POLL_MS (see web/app.js), for every fetched bot, forever.
    Re-parsing each bot's whole list from disk just to report a count on every
    poll would defeat much of the point of no longer retaining it in memory.
    """
    store = dict(getattr(config, "fetched_bot_lists", {}) or {})
    rows = [
        {
            "bot": entry.get("bot", key),
            "fetched_at": entry.get("fetched_at", 0),
            "count": entry.get("entry_count", 0),
        }
        for key, entry in store.items()
    ]
    rows.sort(key=lambda row: str(row["bot"]).lower())
    return rows


def build_fetched_bot_list_payload(nick, offset=0, limit=None):
    """GET /api/filelists/bot/<nick> payload: (http_status, payload_dict).

    Issue #76, options 2 and 3 together: the fetched bot's rows are no longer
    kept in memory at all (see list_fetch.process_fetched_list_zip()) - this
    re-parses the stored list_path FRESH on every call, via
    list_fetch.get_fetched_bot_page(), then returns one page of the result.
    "entries" is in the EXACT same row shape build_filelists_payload() returns
    for this bot's own list (both go through list.entries_to_filelist_rows()),
    so the frontend's File Lists table rendering needs no changes to display
    either one - only the data source (which endpoint it polled) differs.

    A 404 covers "never fetched" (no entry in config.fetched_bot_lists at
    all); a 502 covers "was fetched, but the on-disk file behind it can no
    longer be read" (get_fetched_bot_page() returned a non-None error - see
    its docstring) - both are failures a human reads on the dashboard, so the
    exact code matters less than that a route handler never lets either case
    raise an unhandled exception.
    """
    import list_fetch

    store = getattr(config, "fetched_bot_lists", {}) or {}
    entry = store.get(str(nick).strip().lower())
    if not entry:
        return 404, {"error": f"No fetched list is available for {nick!r} yet."}

    if limit is None:
        limit = FILELISTS_DEFAULT_PAGE_SIZE

    page, total, error = list_fetch.get_fetched_bot_page(entry, offset, limit)
    if error:
        return 502, {"error": error}

    return 200, {
        "bot": entry.get("bot", nick),
        "fetched_at": entry.get("fetched_at", 0),
        "entries": page,
        "total": total,
        "offset": offset,
        "limit": limit,
    }


# ==========================================================================
# Cross-bot search broadcast (mutating - see the NO AUTHENTICATION notice on
# each route below). Pure logic lives here, same reasoning as the
# build_*_payload() functions above: no Flask import, fully unit testable.
# ==========================================================================

BROADCAST_SEARCH_WINDOW = 30.0        # seconds the listening window stays open
BROADCAST_SEARCH_MIN_TERM_LEN = 3     # mirrors list.py's own @find minimum


def build_broadcast_status_payload():
    """GET /api/search/broadcast/status payload: whether a window is open
    right now, plus every result captured so far. `listening` (not just
    the raw `broadcast_search_inprogress` flag) also checks the deadline, so
    a window that flipped false only a poll-interval ago and one that is
    genuinely still counting down are never confused."""
    now = time.time()
    deadline = float(getattr(config, "broadcast_search_deadline", 0) or 0)
    inprogress = bool(getattr(config, "broadcast_search_inprogress", False))
    return {
        "listening": inprogress and now < deadline,
        "deadline": deadline,
        "term": getattr(config, "broadcast_search_term", ""),
        "results": list(getattr(config, "broadcast_search_results", []) or []),
    }


def json_object(body):
    """A parsed JSON request body as a dict, or an empty one.

    request.get_json(silent=True) returns whatever the body parsed to, and the
    `or {}` this replaces only substitutes for a FALSY result. A truthy
    non-dict - an array, a string, a number - passed straight through to
    .get() on the next line and raised AttributeError, which Flask turns into
    a 500 with a traceback, for input the routes should simply reject.

    An empty dict is the right substitute rather than an error of its own,
    because it is exactly what a missing body already produces: the route
    validators downstream turn that into their normal 400. Kept pure, and out
    of the request context, so tests can reach it with no Flask installed.
    """
    return body if isinstance(body, dict) else {}


def start_broadcast_search(term):
    """Validate and kick off a cross-bot @find broadcast. Returns
    (http_status, payload_dict); the Flask route below only parses the
    request body and calls this, so the actual behaviour - validation,
    cooldown, the in-memory state transition, queuing the outbound line - is
    exercised by tests/test_webserver.py with no Flask install required.

    NO AUTHENTICATION. Anyone who can reach this host:port can make the
    daemon send an @find into a real, public IRC channel. See the
    WEBUI_HOST comment in config.py - this is the same deliberate, already
    made "LAN-only, no auth" operator decision, extended to a route that
    (unlike the rest of this file) actually sends something.
    """
    term = "" if term is None else term
    term_err = reject_if_unsafe_for_irc_line(term, "term")
    if term_err:
        return 400, {"error": term_err}

    clean_term = term.strip()
    if len(clean_term) < BROADCAST_SEARCH_MIN_TERM_LEN:
        return 400, {"error": f"Search term must be at least {BROADCAST_SEARCH_MIN_TERM_LEN} characters long."}

    now = time.time()
    if getattr(config, "broadcast_search_inprogress", False) and now < getattr(config, "broadcast_search_deadline", 0):
        return 409, {"error": "A broadcast search is already in progress.",
                     "deadline": config.broadcast_search_deadline}

    cooldown = float(getattr(config, "BROADCAST_SEARCH_COOLDOWN", 30))
    since_last = now - float(getattr(config, "last_broadcast_search_at", 0) or 0)
    if since_last < cooldown:
        return 429, {"error": f"Please wait {int(cooldown - since_last)}s before broadcasting another search."}

    oserve = sys.modules.get("oserve")
    if not oserve or not getattr(oserve, "irc_connection", None):
        return 503, {"error": "IRC connection is not up."}

    channel = (getattr(config, "BROADCAST_SEARCH_CHANNEL", None)
               or str(getattr(config, "CHANNEL", "")).split(",")[0].strip())
    if not channel:
        return 503, {"error": "No broadcast channel configured."}

    deadline = now + BROADCAST_SEARCH_WINDOW
    config.broadcast_search_inprogress = True
    config.broadcast_search_deadline = deadline
    config.broadcast_search_term = clean_term
    # In place, not `= []`: config.broadcast_search_results is bound from
    # runtime.py - see runtime.py's docstring on why this must never rebind.
    config.broadcast_search_results.clear()
    config.last_broadcast_search_at = now

    oserve.queue_message(channel, f"PRIVMSG {channel} :@find {clean_term}\r\n")

    def _close_window(expected_deadline=deadline):
        # Runs on its own daemon thread so the request thread returns
        # immediately - see the module docstring's "must not block the Flask
        # request thread for 30s" requirement.
        time.sleep(BROADCAST_SEARCH_WINDOW)
        # Only close OUR window. A fresh broadcast should never start before
        # this one's deadline given the cooldown above, but this guard means
        # a stale timer can never clobber a newer window if that invariant
        # is ever violated (a rehash resetting last_broadcast_search_at, say).
        if getattr(config, "broadcast_search_deadline", None) == expected_deadline:
            config.broadcast_search_inprogress = False

    threading.Thread(target=_close_window, daemon=True).start()

    return 200, {"status": "listening", "deadline": deadline, "term": clean_term}


# ==========================================================================
# Cross-bot file fetch (mutating - see the NO AUTHENTICATION notice on each
# route below). Pure logic, same reasoning as above.
# ==========================================================================

def build_fetch_enqueue_result(payload):
    """Validate `payload` (one {"bot","filename"} object, or a list of them -
    the operator explicitly wants multi-select) and append a `pending` row
    per valid item to config.fetch_queue. Does NOT dispatch anything itself;
    dcc_fetch.check_fetch_queue()'s background dispatcher owns pacing.

    Returns (http_status, payload_dict) with "created" (the new request ids)
    and "errors" (one entry per rejected item, if any).
    """
    import dcc_fetch

    items = [payload] if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        return 400, {"error": 'Expected a {"bot": .., "filename": ..} object, '
                               'or a non-empty list of them.'}

    created = []
    errors = []
    for raw in items:
        if not isinstance(raw, dict):
            errors.append({"error": "Each item must be an object with bot/filename.", "item": raw})
            continue
        bot_raw = raw.get("bot", "")
        filename_raw = raw.get("filename", "")
        # reject_if_unsafe_for_irc_line() covers two bugs at once here: a
        # non-string bot/filename (previously silently str()-coerced into a
        # real queue row instead of rejected) and an embedded \r/\n (which
        # dcc_fetch.check_fetch_queue() would later interpolate verbatim into
        # an outbound "PRIVMSG <bot> :!<bot> <filename>\r\n" line - see the
        # function's own docstring).
        field_err = (reject_if_unsafe_for_irc_line(bot_raw, "bot")
                     or reject_if_unsafe_for_irc_line(filename_raw, "filename"))
        if field_err:
            errors.append({"error": field_err, "item": raw})
            continue
        bot = bot_raw.strip()
        filename = filename_raw.strip()
        if not bot or not filename:
            errors.append({"error": "Both 'bot' and 'filename' are required.", "item": raw})
            continue
        created.append(dcc_fetch.enqueue_fetch(bot, filename))

    status = 200 if created else 400
    return status, {"created": created, "errors": errors}


def build_fetch_status_payload():
    """GET /api/fetch/status payload: every fetch_queue row, oldest first, so
    the dashboard's Downloads panel has a stable order to render."""
    queue = dict(getattr(config, "fetch_queue", {}) or {})
    rows = []
    for request_id, row in sorted(queue.items(), key=lambda kv: kv[1].get("requested_at", 0)):
        row_out = dict(row)
        row_out["id"] = request_id
        rows.append(row_out)
    return rows


# ==========================================================================
# Flask app - only built/used when Flask is actually installed.
# ==========================================================================

if HAVE_FLASK:

    def create_app():
        app = Flask(__name__, static_folder="web", static_url_path="")

        @app.route("/")
        def index():
            return app.send_static_file("index.html")

        @app.route("/api/queue")
        def api_queue():
            return jsonify(build_queue_payload(user=request.args.get("user")))

        @app.route("/api/search")
        def api_search():
            return jsonify(build_search_payload(request.args.get("q", "")))

        @app.route("/api/filelists")
        def api_filelists():
            offset, limit = parse_pagination_params(
                request.args.get("offset"), request.args.get("limit"))
            return jsonify(build_filelists_payload(offset, limit))

        # ------------------------------------------------------------------
        # NO AUTHENTICATION ON ANY ROUTE BELOW, INCLUDING THESE MUTATING ONES.
        # See the WEBUI_HOST comment in config.py: the operator was explicitly
        # warned that this means anyone who can reach this host:port can make
        # the daemon broadcast an @find into a real public channel and dial
        # out to arbitrary bot-supplied IP:ports, and chose to proceed without
        # auth anyway - consistent with this dashboard's existing "LAN-only,
        # no auth, by design" stance. Do not add authentication-shaped
        # workarounds here (an API key in a query string, a cookie nobody
        # sets) without actually implementing authentication; that only adds
        # false confidence.
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

        @app.route("/api/filelists/fetch", methods=["POST"])
        def api_filelists_fetch():
            body = json_object(request.get_json(silent=True))
            status, result = build_list_fetch_enqueue_result(body.get("bot", ""))
            return jsonify(result), status

        @app.route("/api/filelists/bots")
        def api_filelists_bots():
            return jsonify(build_fetched_bot_list_summaries())

        @app.route("/api/filelists/bot/<nick>")
        def api_filelists_bot(nick):
            offset, limit = parse_pagination_params(
                request.args.get("offset"), request.args.get("limit"))
            status, result = build_fetched_bot_list_payload(nick, offset, limit)
            return jsonify(result), status

        @app.route("/api/fetch/<request_id>/download")
        def api_fetch_download(request_id):
            queue = getattr(config, "fetch_queue", {}) or {}
            row = queue.get(request_id)
            if not row or row.get("state") != "complete" or not row.get("stored_filename"):
                return jsonify({"error": "Unknown, incomplete, or failed fetch."}), 404
            # Never build this path from the URL parameter - request_id only
            # selects a row, and the row's OWN already-validated
            # stored_filename (set by dcc_fetch.py after it ran the offer's
            # filename through dcc.is_safe_path()) is what actually gets
            # opened.
            directory = os.path.abspath(getattr(config, "FETCHED_FILES_DIR", "./data/fetched"))
            return send_from_directory(
                directory, row["stored_filename"], as_attachment=True,
                download_name=row.get("filename") or row["stored_filename"])

        return app


def start():
    """Run the dashboard. Called on its own daemon thread from oserve.startup().

    Logs and returns rather than raising on any of: Flask missing, the feature
    disabled via config, or the port being unavailable - none of those may take
    the daemon down with them.
    """
    if not HAVE_FLASK:
        print("[WEBUI] Flask not installed; dashboard disabled.")
        return
    if not getattr(config, "WEBUI_ENABLED", True):
        print("[WEBUI] Disabled via config.WEBUI_ENABLED = False.")
        return

    host = getattr(config, "WEBUI_HOST", "0.0.0.0")
    port = getattr(config, "WEBUI_PORT", 8420)
    app = create_app()
    print(f"[WEBUI] Dashboard starting on http://{host}:{port}/ (no authentication - LAN-only).")
    try:
        # use_reloader=False is NOT optional: Flask's reloader re-execs the whole
        # process, and this runs on an already-live daemon thread - a re-exec
        # here would take the entire bot down with it, not just the dashboard.
        app.run(host=host, port=port, threaded=True, use_reloader=False, debug=False)
    except Exception as run_err:
        print(f"[WEBUI] Dashboard stopped: {run_err}")
