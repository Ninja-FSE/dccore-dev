# webserver.py - Read-only web dashboard (Search / Queue / File Lists).
"""A small, optional status page for the daemon.

FLASK IS OPTIONAL. The daemon has no external dependencies today, and CI never
installs Flask - so importing this module must never fail, and starting the
dashboard when Flask is missing must log and return, never crash the daemon.
That is why the import is guarded and why HAVE_FLASK exists.

The three build_*_payload() functions below are pure: they read config (and,
for search/file-lists, the master list via list.py) and return plain
dicts/lists. They never import or touch flask, which is what lets
tests/test_webserver.py exercise the real data-shaping logic with plain
unittest, no Flask install required - keeping the "stdlib-only" property the
rest of the test suite relies on. create_app() and start() are the only things
gated on HAVE_FLASK.

NO AUTHENTICATION. See the WEBUI_HOST comment in config.py: this is a
deliberate operator decision for a LAN-only deployment, not an oversight, and
it is why every route here is read-only (GET only - no mutation, no admin
actions). Do not add a POST/DELETE route to this module without adding
authentication first.

Nothing here is added to commands.py's modules_to_reload. A !rehash reload
re-executes this module's body, which would try to re-bind a live listening
socket out from under app.run() - the same reasoning that already excludes
adminchat.py. Route handlers read config fresh via getattr() on every request
instead, so a rehash's new values (e.g. a changed WEBUI_* setting takes effect
only on the next daemon restart, but MAX_DCC_SLOTS, the queue, etc. are always
current) are visible without needing a reload of this module.
"""

import os

import config

try:
    from flask import Flask, jsonify, request
    HAVE_FLASK = True
except ImportError:
    HAVE_FLASK = False


# A browser tab is not an IRC channel: MAX_SEARCH_RESULTS (config.py, default 5)
# is sized to avoid flooding a channel and is the wrong number here. This is a
# module constant, not a config setting - it is a display cap on one page, not
# an operator-facing tunable like WEBUI_PORT.
WEBUI_MAX_SEARCH_RESULTS = 50


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
