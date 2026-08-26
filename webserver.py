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


def reject_if_unsafe_for_irc_line(value, field_name):
    """Return an error string if `value` is not safe to interpolate into an
    outbound raw IRC line, or None if it is safe.

    Every mutating route in this module eventually hands web input to
    oserve.queue_message(), whose one job is to sit in a per-user list until
    queue_mgr.queue_worker() writes it straight to the live socket
    (`current_sock.send(msg.encode())`) - no re-validation, no re-splitting on
    the way out. A value that is not actually a string gets silently
    str()-coerced by a careless caller (see the bot/filename type-confusion
    bug this replaced), and a string containing an embedded \\r or \\n lets
    the caller smuggle one or more ADDITIONAL raw IRC lines - QUIT, JOIN/PART
    an arbitrary channel, PRIVMSG/NOTICE as this bot - past whatever single
    line the route intended to send. Both are rejected here, at the boundary,
    before the value goes anywhere near an outbound message.

    Shared because it is already needed in >= 2 places (POST
    /api/search/broadcast's `term`, POST /api/fetch/enqueue's `bot` and
    `filename`) - any future route that builds an outbound IRC line from web
    input must run every such value through this too.
    """
    if not isinstance(value, str):
        return f"'{field_name}' must be a string."
    if "\r" in value or "\n" in value:
        return f"'{field_name}' must not contain line breaks."
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


def build_filelists_payload():
    """The File Lists view's data: every file in THIS bot's own master list.

    v1 scope is deliberately THIS BOT ONLY - it serves DCCore's own master
    list, decomposed into rows, with "source" hardcoded to config.NICKNAME.
    Real cross-bot deduplication (parsing other bots' adverts in the channel)
    is out of scope; this dedup is the trivial single-source case, collapsing
    only the same filename listed under two different folders.
    """
    import list as list_mod

    entries, _total = list_mod.find_matching_entries([], limit=None)

    seen = set()
    rows = []
    for entry in entries:
        filename = entry.get("filename", "?")
        size = entry.get("size", "")
        key = (filename.lower(), size)
        if key in seen:
            continue
        seen.add(key)
        ext = os.path.splitext(filename)[1].lstrip(".").upper()
        rows.append({
            "title": filename,
            "size": size,
            "format": ext,
            "source": getattr(config, "NICKNAME", "?"),
        })
    return rows


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
    config.broadcast_search_results = []
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
            return jsonify(build_filelists_payload())

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
            body = request.get_json(silent=True) or {}
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
