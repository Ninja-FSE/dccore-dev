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


# This section is web_library.py since #1318, a step of splitting this file. Imported back by
# name, so webserver.<name> still answers for each of them.
from web_library import (  # noqa: F401
    _format_library_size,
    _rar_counts,
    build_filelists_payload,
    build_library_payload,
    build_search_payload,
    count_rar_album_folders,
)








# This section is web_stats.py since #1318, a step of splitting this file. Imported back by
# name, so webserver.<name> still answers for each of them.
from web_stats import (  # noqa: F401
    MAX_IMPORT_BYTES,
    MAX_IMPORT_FILES,
    MAX_IMPORT_SPEED,
    MAX_KTDATA_CHARS,
    RECORD_PERIODS,
    STATS_PARTS,
    _FORMULA_START,
    _IMPORT_LIMITS,
    _RECEIVED,
    _csv_cell,
    _lifetime_sent,
    _nick_rows,
    _read_ktdata_text,
    _record_off,
    _record_outcomes,
    _record_since,
    _record_tops,
    _wait_text,
    apply_ktdata_import,
    apply_record_forget,
    apply_stats_import,
    build_ktdata_preview,
    build_record_nick_payload,
    build_record_payload,
    build_stats_import_preview,
    build_stats_payload,
    current_importable_stats,
    record_csv_lines,
    validate_import_values,
)



















































# This section is web_queue.py since #1318, a step of splitting this file. Imported back by
# name, so webserver.<name> still answers for each of them.
from web_queue import (  # noqa: F401
    _pack_fields,
    _queue_nick,
    _queue_row_id,
    _security_seconds_left,
    build_ignore_result,
    build_pack_cancel_result,
    build_queue_clear_result,
    build_queue_move_file_result,
    build_queue_move_user_result,
    build_queue_payload,
    build_queue_remove_file_result,
    build_unignore_result,
)






























# This section is web_lists.py since #1315, a step of splitting this file. Imported back by
# name, so webserver.<name> still answers for each of them.
from web_lists import (  # noqa: F401
    BOT_ALONE_FETCH_CONFLICT_ERROR,
    OWN_SOURCE,
    PACK_FETCH_CONFLICT_ERROR,
    _ADVERT_LIVE_FIELDS,
    _MAX_SAFE_JS_INT,
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












# This section is web_settings.py since #1320, a step of splitting this file. Imported back by
# name, so webserver.<name> still answers for each of them.
from web_settings import (  # noqa: F401
    CHOICE_LABELS,
    SETTINGS_CATEGORIES,
    SETTINGS_LABELS,
    SETTINGS_RESTART_ONLY,
    SETTINGS_UNITS,
    THEME_PREVIEW_ROLES,
    _save_settings_and_rehash,
    _settings_field,
    _settings_payload_unlocked,
    apply_settings_changes,
    build_password_change_result,
    build_settings_payload,
    build_theme_preview,
    console_is_enabled,
    dashboard_is_loopback_only,
    theme_preview_overrides,
)












# This section is web_messages.py since #1320, a step of splitting this file. Imported back by
# name, so webserver.<name> still answers for each of them.
from web_messages import (  # noqa: F401
    build_messages_payload,
    build_notices_payload,
    mark_messages_read_result,
    mark_notices_read_result,
    messages_are_off,
    messages_payload_or_404,
    messages_read_or_404,
)




















# This section is web_served.py since #1320, a step of splitting this file. Imported back by
# name, so webserver.<name> still answers for each of them.
from web_served import (  # noqa: F401
    MAX_SERVED_LISTS,
    apply_folder_changes,
    apply_list_changes,
    browse_roots,
    build_folder_browse_payload,
    build_folders_payload,
    build_lists_payload,
)








# This section is web_on_connect.py since #1320, a step of splitting this file. Imported back by
# name, so webserver.<name> still answers for each of them.
from web_on_connect import (  # noqa: F401
    apply_on_connect_changes,
    build_on_connect_payload,
    build_on_connect_resend_result,
)




















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
