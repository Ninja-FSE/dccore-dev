# dcc_fetch.py - Cross-bot file fetch: RECEIVING bytes an untrusted third party hands us.
"""We are the client here, not the server.

dcc.py is exclusively the "we are the trusted server choosing what to share"
role: it decides what exists in FILE_DIRECTORY, offers it, and streams it out.
This module is the opposite role - dialling an IP:port a foreign bot handed us
in a channel, and writing whatever bytes arrive to disk - and that distinction
matters for anyone auditing the code later. Nothing in here is trusted by
default:

  * an inbound "DCC SEND" CTCP is only ever acted on if it matches a fetch we
    ourselves requested moments earlier (admission control, see
    handle_incoming_offer()) - an unsolicited offer from anyone in the channel
    is logged and dropped, never dialled;
  * the declared size is capped BEFORE we connect (MAX_FETCH_FILE_SIZE);
  * the filename is never trusted as a literal path component;
  * a lying peer that sends more than it declared gets the partial file
    deleted and the transfer marked failed, not silently truncated.

State machine, owned entirely by this module:

    pending -> offered -> receiving -> complete
                   |             ^
                   |             |
                   `-> listening-'
                   \\-----------------------------> failed (any timeout/
                                                      admission-rejection/
                                                      size-mismatch/connect-
                                                      error - see the row's
                                                      "reason" field)

`listening` is the passive/reverse-DCC branch: a third-party bot that cannot
accept an inbound connection (usually firewalled) answers our request with
`DCC SEND <filename> <ip> 0 <size> <token>` - port 0 plus a token is the
standard convention meaning "you listen, and reply with your own ip:port plus
this same token" (mirrors adminchat.py's identical handling of passive DCC
CHAT). A row only ever reaches `listening` AFTER admission control has
already matched it to an `offered` row we ourselves created, exactly like the
active path - see handle_incoming_offer() and _serve_passive_offer(). Once
the offering bot connects back, the row moves to `receiving` and joins the
same bounded-transfer code (_run_transfer()) the active path uses; if nobody
ever connects, it fails with reason "passive offer: no connection received"
and the listening socket is closed, never leaked.

Rows live in config.fetch_queue (config.py section 8), keyed by a generated
request id. webserver.py's /api/fetch/* routes are the only thing that
creates `pending` rows (POST /api/fetch/enqueue); check_fetch_queue() below
promotes them to `offered`; handle_incoming_offer() (dispatched from irc.py's
CTCP branch) takes it from there.

A `"folder"` row (a whole album/discography, requested as another bot's own
"!<bot> !rar <folder>" packing convention) walks this exact same state
machine as `"file"`/`"list"` - the only differences are which
_claim_matching_offer_locked() branch admits it (bot-alone, like `"list"`,
since we cannot know what the target bot will name the resulting .rar), a
longer FETCH_FOLDER_OFFER_TIMEOUT (packing a whole album takes real time on
the other end), and a larger MAX_FETCH_FOLDER_FILE_SIZE cap (a packed
archive is bigger than any single file).
"""

import ipaddress
import os
import re
import socket
import sys
import threading
import time
import uuid

import config
import dcc
import list as list_mod
import platform_compat

# Connect timeout for dialling the offering bot, and the idle-recv timeout
# once connected. Not config knobs - dcc.py does not expose its own mirror-
# image idle timeout (conn.settimeout(60.0), dcc.py:1024) as one either, and
# these are exactly that same convention on the receiving side.
CONNECT_TIMEOUT = 15.0
IDLE_RECV_TIMEOUT = 60.0

# How long we wait, once we start listening for a passive/reverse DCC SEND,
# for the offering bot to actually connect back. Same value and same
# reasoning as adminchat.LISTEN_TIMEOUT (60s "waiting for the operator to
# accept our offer back") - this is the identical protocol shape, just for
# DCC SEND instead of DCC CHAT, so there is no reason to pick a different
# number.
PASSIVE_LISTEN_TIMEOUT = 60.0

RECV_CHUNK = 65536

# The one convention that is actually standardised across file-sharing bots:
# "!<botnick> <filename>", the same syntax this bot itself answers to (see
# irc.py's get_bot_aliases()/dcc.handle_download_request). Used both by
# irc.py's broadcast-search capture (to offer a "Download" button) and here,
# defensively, nowhere else - dcc_fetch never parses this out of anything,
# it only ever receives a filename we ourselves already chose when the fetch
# was enqueued.
_FILENAME_CHARSET_RE = re.compile(r'[^\w\-_\. \(\)]')  # mirrors dcc.py:762

# THE canonical definition of "which bytes are unsafe to interpolate into a
# raw outbound IRC/CTCP line" - webserver.reject_if_unsafe_for_irc_line()
# imports and calls contains_unsafe_ctcp_bytes() below rather than keeping
# its own copy of this regex. That merge happened after this exact bug class
# recurred a THIRD time (once in webserver.py's web-enqueue routes, once
# here in dcc_fetch's offer parsing, and a third time because
# reject_if_unsafe_for_irc_line() rejected \r/\n but not \x01, so it was not
# actually equivalent to this check) - see this project's CONVENTIONS.md rule
# against writing one fact in two places. If you are ever tempted to add a
# second copy of this regex anywhere else in the codebase: don't - import
# this one instead.
#
# A value that gets interpolated into a raw outbound IRC line lets an
# embedded line break smuggle one or more ADDITIONAL lines (QUIT, JOIN/PART
# an arbitrary channel, PRIVMSG/NOTICE as this bot, ...) past whatever single
# line was intended. \x01 is unsafe for the same reason PLUS one more: any of
# these values may end up wrapped in a CTCP (\x01...\x01) reply (see
# _serve_passive_offer()) - an embedded \x01 closes that CTCP early and lets
# the attacker inject arbitrary trailing content into what the receiving
# peer parses as a second CTCP or as plain text. Rejecting \x01 everywhere,
# even for values that only ever reach a plain PRIVMSG body (not a CTCP), is
# deliberately conservative: a PRIVMSG body containing \x01 can itself be
# interpreted as an inline CTCP by the receiving client.
#
# Checked at PARSE time (parse_dcc_send_offer(), below) for both
# offer["filename"] (active and passive alike) and offer["token"] (passive
# only) - not only at the one call site that currently echoes them back
# (_serve_passive_offer()) - because this bug class had already recurred once
# by the time that check was added (see the module-merge note above).
# Rejecting the whole offer here means any future call site that touches
# offer["filename"]/offer["token"] inherits the protection automatically,
# instead of depending on every future author remembering to sanitize again.
_UNSAFE_CTCP_BYTES_RE = re.compile(r'[\r\n\x01]')


def contains_unsafe_ctcp_bytes(value):
    """True if `value` contains a byte that must never reach a raw outbound
    IRC/CTCP line unsanitized - see _UNSAFE_CTCP_BYTES_RE's comment above.

    Public (no leading underscore) because webserver.py's
    reject_if_unsafe_for_irc_line() imports and calls this directly - see
    that comment for why the two checks were merged into this one function.
    """
    return bool(_UNSAFE_CTCP_BYTES_RE.search(str(value)))


_FALLBACK_FETCH_LOCK = threading.Lock()


def _fetch_lock():
    """The dedicated fetch_queue lock oserve.py allocates at startup, or a
    shared module-level fallback for any caller that never ran
    oserve.startup() (tests, most notably).

    The fallback is a single object reused on every call, not a fresh
    `threading.Lock()` built on the spot: constructing a new lock per call
    would let two concurrent callers each acquire a DIFFERENT lock and
    achieve no mutual exclusion at all, silently. Same pattern as
    list_fetch.py's `_lock()` and runtime.channel_users_lock().
    """
    return getattr(config, "fetch_queue_lock", None) or _FALLBACK_FETCH_LOCK


def _ensure_fetch_queue():
    # config.fetch_queue is bound from runtime.py at import time and always
    # exists as a real dict - never rebind it here, see runtime.py's docstring.
    return config.fetch_queue


def new_fetch_row(bot, filename, now=None, request_type="file"):
    """Build a fresh `pending` row in the shape every reader of
    config.fetch_queue expects. Does not insert it - callers decide the key.

    request_type is "file" (default - existing behaviour: admission control
    requires an exact bot+filename match, see _claim_matching_offer_locked()),
    "list" (a cross-bot list fetch: we send a bare "@<bot>", per irc.py's own
    @<nick> trigger, and cannot know in advance what the target bot will name
    its list zip - admission control for these rows matches on bot alone), or
    "folder" (a cross-bot folder-as-rar fetch: we send "!<bot> !rar <folder
    path>" - the same convention this bot's own dcc.py "!rar" handler answers
    on its own nick - and, just like "list", cannot know in advance what the
    target bot will name the resulting .rar, so admission control for these
    rows also matches on bot alone). Every existing call site that does not
    pass request_type keeps getting "file" rows, so nothing about today's
    behaviour changes.

    "requested_filename" is set once here, to the same (stripped) value as
    `filename`, and never touched again - it preserves the original request
    text (e.g. "!rar Artist/Album") since _claim_matching_offer_locked() will
    later overwrite row["filename"] with whatever name the responding bot
    actually sends, exactly as it already does for "list" rows.
    """
    now = time.time() if now is None else now
    clean_filename = str(filename).strip()
    return {
        "bot": str(bot).strip(),
        "filename": clean_filename,
        "requested_filename": clean_filename,
        "request_type": request_type if request_type in ("file", "list", "folder") else "file",
        "state": "pending",
        "requested_at": now,
        "offered_at": None,
        "bytes_received": 0,
        "total_size": None,
        "reason": "",
        "stored_filename": None,
    }


def enqueue_fetch(bot, filename, request_type="file"):
    """Append one `pending` row to config.fetch_queue and return its id.

    Does NOT dispatch anything - check_fetch_queue() (the background
    dispatcher) is what promotes pending rows, so this is safe to call from
    a Flask request thread without blocking on IRC pacing.
    """
    queue = _ensure_fetch_queue()
    request_id = uuid.uuid4().hex[:12]
    with _fetch_lock():
        while request_id in queue:  # practically never, but be certain
            request_id = uuid.uuid4().hex[:12]
        queue[request_id] = new_fetch_row(bot, filename, request_type=request_type)
    return request_id


def count_active_fetches(queue=None):
    """Rows currently occupying a slot. Derived, not tracked separately - see
    the comment on config.fetch_queue for why.

    'listening' counts too: a passive/reverse DCC SEND has already claimed a
    listening socket in the shared DCC port range and a slot in the fetch
    queue by the time it reaches this state, exactly like 'receiving' - it
    must count against MAX_FETCH_SLOTS or an offering bot that never connects
    would let listeners pile up unbounded.
    """
    queue = _ensure_fetch_queue() if queue is None else queue
    return sum(1 for row in queue.values()
               if row.get("state") in ("offered", "listening", "receiving"))


def _mark_failed_locked(row, reason):
    row["state"] = "failed"
    row["reason"] = reason


def check_fetch_queue():
    """Dispatcher: expire stale offers, then promote pending rows while a slot
    is free. Mirrors dcc.py's check_queue_and_send() claim-before-dispatch
    discipline (dcc.py has its own comments about the project having been
    bitten by queue races before) - the row is flipped to `offered` INSIDE
    the lock, before the outbound message is even built, so two overlapping
    calls to this function can never both claim the same pending row.

    Called periodically from fetch_dispatcher_worker() (a small dedicated
    thread started by oserve.startup(), mirroring how queue_mgr.queue_worker
    is started) rather than being wired into an unrelated existing loop.
    """
    # Absent means DISABLED: see webserver.fetch_feature_error() for why
    # the missing attribute has to fail toward not-fetching.
    if getattr(config, "fetch_feature_disabled", True):
        # FETCHED_FILES_DIR could not be created at startup (see
        # oserve.startup()) - leave rows sitting `pending` rather than ever
        # promoting them; there is nowhere safe to write a completed file.
        return

    queue = _ensure_fetch_queue()
    max_slots = int(getattr(config, "MAX_FETCH_SLOTS", 3))
    offer_timeout = float(getattr(config, "FETCH_OFFER_TIMEOUT", 60))
    folder_offer_timeout = float(getattr(config, "FETCH_FOLDER_OFFER_TIMEOUT", 1800))
    now = time.time()

    to_dispatch = []
    with _fetch_lock():
        # Expire offers nobody ever answered. A row stuck in "offered" forever
        # would otherwise hold a slot open permanently and starve every other
        # pending request behind it. A "folder" row gets its own, much longer
        # timeout (folder_offer_timeout) - the other bot has to run its own
        # !rar packing pipeline before it can even start the DCC SEND, which
        # plain file/list fetches never have to wait on.
        for row in queue.values():
            if row.get("state") == "offered" and row.get("offered_at") is not None:
                this_timeout = (folder_offer_timeout
                                 if row.get("request_type") == "folder" else offer_timeout)
                if (now - row["offered_at"]) > this_timeout:
                    _mark_failed_locked(row, "no response")

        # Independent safety net for "listening" rows (passive DCC SEND).
        # _serve_passive_offer() already bounds its own accept() with
        # PASSIVE_LISTEN_TIMEOUT and marks the row 'failed' on the way out no
        # matter how it exits (timeout, OSError, or any other exception - see
        # its own comment on that last case). This second check exists for
        # the failure mode none of that can cover: the daemon thread running
        # _serve_passive_offer() dies or hangs BEFORE it gets that far (e.g.
        # thread-start failure), leaving the row 'listening' with nothing left
        # to ever revisit it. A generous multiple of PASSIVE_LISTEN_TIMEOUT
        # avoids racing a passive transfer that is still legitimately waiting.
        listen_timeout = PASSIVE_LISTEN_TIMEOUT * 3
        for row in queue.values():
            if row.get("state") == "listening" and row.get("listening_since") is not None:
                if (now - row["listening_since"]) > listen_timeout:
                    _mark_failed_locked(row, "listening row expired without a resolution")

        active = count_active_fetches(queue)
        free_slots = max_slots - active
        if free_slots <= 0:
            return

        pending_ids = sorted(
            (rid for rid, row in queue.items() if row.get("state") == "pending"),
            key=lambda rid: queue[rid].get("requested_at", 0),
        )
        for rid in pending_ids[:free_slots]:
            row = queue[rid]
            row["state"] = "offered"
            row["offered_at"] = now
            to_dispatch.append((rid, row["bot"], row["filename"], row.get("request_type", "file")))

    if not to_dispatch:
        return

    oserve = sys.modules.get("oserve")
    channel = (getattr(config, "BROADCAST_SEARCH_CHANNEL", None)
               or str(getattr(config, "CHANNEL", "")).split(",")[0].strip())
    for rid, bot, filename, request_type in to_dispatch:
        # Defense-in-depth only, expected to be unreachable: `bot` (and, for
        # a "file" row, `filename`) already passed
        # webserver.reject_if_unsafe_for_irc_line() - which now delegates to
        # this exact same contains_unsafe_ctcp_bytes() check - at enqueue
        # time (see build_fetch_enqueue_result()/build_list_fetch_enqueue_
        # result() in webserver.py). This is one of several places this
        # exact injection class has recurred in this feature (see
        # contains_unsafe_ctcp_bytes()'s own comment above), so this
        # dispatch site - the one that actually interpolates these values
        # into a raw outbound IRC line - does not simply trust that the
        # enqueue-time check was applied; it refuses to build or send the
        # message at all if either value is still unsafe for some future
        # reason, mirroring _serve_passive_offer()'s identical re-check
        # right before IT builds its own outbound CTCP line.
        if contains_unsafe_ctcp_bytes(bot) or (
                request_type != "list" and contains_unsafe_ctcp_bytes(filename)):
            _mark_failed_locked(queue[rid], "unsafe characters in bot/filename this late")
            print(f"[FETCH] Refusing to dispatch request {rid} "
                  f"(bot={bot!r}, filename={filename!r}): unsafe characters "
                  f"this late (should be unreachable - see "
                  f"webserver.reject_if_unsafe_for_irc_line()).")
            continue
        if request_type == "list":
            # The exact same bare "@<bot>" trigger irc.py answers on this
            # bot's own nick (irc.py's `elif msg_lower == f"@{config.NICKNAME.lower()}":`
            # branch, dispatching to list.send_file_list()) - other
            # OmenServe-family bots on the network answer the same convention
            # the same way: a DCC SEND of their own list zip, filename
            # unknown to us ahead of time (see _claim_matching_offer_locked()
            # for how admission control handles that).
            message = f"PRIVMSG {channel} :@{bot}\r\n"
            log_desc = f"{bot}'s file list"
        elif request_type == "folder":
            # filename is already the literal string "!rar <folder path>" at
            # this point (see webserver.build_folder_rar_fetch_enqueue_result()),
            # so it falls into the same wire line the plain "file" branch below
            # builds - only the log line differs, purely cosmetic.
            message = f"PRIVMSG {channel} :!{bot} {filename}\r\n"
            log_desc = f"{filename!r} (folder pack) from {bot}"
        else:
            message = f"PRIVMSG {channel} :!{bot} {filename}\r\n"
            log_desc = f"{filename!r} from {bot}"
        if oserve and hasattr(oserve, "queue_message"):
            oserve.queue_message(bot, message)
        print(f"[FETCH] Requested {log_desc} (request {rid}).")


def fetch_dispatcher_worker():
    """Small dedicated background loop, started as a daemon thread from
    oserve.startup() alongside queue_mgr.queue_worker. Kept separate rather
    than piggybacked onto queue_mgr's own loop: that loop paces OUTBOUND
    socket writes one at a time and is already dense; this one only ever
    touches config.fetch_queue and never blocks on the network itself.
    """
    print("[FETCH] Dispatcher worker started.")
    while True:
        try:
            check_fetch_queue()
        except Exception as dispatch_err:
            print(f"[FETCH] Dispatcher loop error: {dispatch_err}")
        time.sleep(2.0)


# ==========================================================================
# Inbound offer: parsing, admission control, and the actual transfer.
# ==========================================================================

def parse_dcc_send_offer(ctcp_text):
    """Parse 'DCC SEND <filename> <ip_long> <port> <size>' (optionally with a
    quoted filename containing spaces, mIRC's own convention for that case).

    Returns {"filename", "ip", "port", "size"} for a normal (active) offer,
    where the caller dials `ip`:`port` itself; or, for the passive/reverse
    form 'DCC SEND <filename> <ip_long> 0 <size> <token>',
    {"filename", "ip": None, "port": 0, "size", "token"} - port 0 plus a
    trailing token is the standard convention meaning the offering bot cannot
    accept an inbound connection (usually firewalled) and wants US to listen
    instead, then reply with our own ip:port plus the same token echoed back.
    Mirrors adminchat.parse_offer()'s identical (ip, port, token) shape for
    passive DCC CHAT - see that function's docstring for the same convention
    explained in more depth. Returns None for anything malformed, including
    a bare 'port 0' with no token (nothing to answer it with).

    The ip_long decode is the exact inverse of dcc.get_public_ip_long()
    (dcc.py:201-210).
    """
    text = str(ctcp_text).strip().strip("\x01").strip()
    if not text.upper().startswith("DCC SEND "):
        return None
    rest = text[len("DCC SEND "):].strip()
    if not rest:
        return None

    if rest.startswith('"'):
        m = re.match(r'"([^"]*)"\s+(.+)$', rest)
        if not m:
            return None
        filename, remainder = m.group(1), m.group(2)
    else:
        parts = rest.split()
        if len(parts) < 4:
            return None
        filename, remainder = parts[0], " ".join(parts[1:])

    fields = remainder.split()
    if len(fields) < 3:
        return None

    try:
        ip_long = int(fields[0])
        port = int(fields[1])
        size = int(fields[2])
    except (ValueError, TypeError):
        return None

    if not filename:
        return None
    if contains_unsafe_ctcp_bytes(filename):
        # CRLF/CTCP injection guard, checked here rather than only where the
        # filename is later used - see _UNSAFE_CTCP_BYTES_RE's comment above
        # for why. Applies to the active form too, even though only the
        # passive reply currently echoes the filename back: a filename this
        # hostile is not a real DCC client's output either way.
        return None
    if port < 0 or port > 65535:
        return None
    if ip_long < 0 or ip_long > 0xFFFFFFFF:
        return None
    if size <= 0:
        # Not just "not negative": a declared size of exactly 0 is just as
        # malformed as a negative one. `_run_transfer()`'s own loop is
        # `while bytes_received < total_size` - with total_size == 0 that
        # condition is 0 < 0, so it never runs even once, and the row would
        # mark 'complete' immediately after opening a connection but reading
        # zero bytes. Treat it the same as any other unusable offer: rejected
        # here, before a connection is ever made.
        return None

    if port == 0:
        # Passive/reverse DCC SEND. The token identifies this request and
        # MUST come back in our own reply offer, or the offering bot cannot
        # match the two and ignores us - so a "port 0" with no token is just
        # as unusable as any other malformed offer, not a valid passive one.
        # ip_long is deliberately NOT validated as a real address here (it
        # often is not one - some bots send 0): it is never dialled, so
        # nothing depends on it being well-formed.
        if len(fields) < 4 or not fields[3]:
            return None
        token = fields[3]
        if contains_unsafe_ctcp_bytes(token):
            # Same CRLF/CTCP injection guard as the filename above - the
            # token is echoed back into our own reply CTCP verbatim (see
            # _serve_passive_offer()), so it is just as much an injection
            # vector as the filename is.
            return None
        # Best-effort only (see _serve_passive_offer()'s comment above its
        # listener.accept() call): ip_long is still present on the wire for a
        # passive offer even though it is never dialled - real passive-DCC
        # senders typically fill it with their own detected address, the
        # same convention as the active-offer wire format. Decode it, if it
        # decodes to anything at all, purely so the eventual accept() can
        # compare the peer that actually connects against what the offer
        # itself claimed. Deliberately not validated/trusted any further
        # here (0 and other junk are explicitly tolerated) - nothing above
        # depends on it being well-formed, this is purely for that one later
        # best-effort comparison.
        try:
            claimed_ip = str(ipaddress.IPv4Address(ip_long)) if ip_long else None
        except (ipaddress.AddressValueError, ValueError):
            claimed_ip = None
        return {"filename": filename, "ip": None, "port": 0, "size": size,
                "token": token, "claimed_ip": claimed_ip}

    try:
        ip = str(ipaddress.IPv4Address(ip_long))
    except (ipaddress.AddressValueError, ValueError):
        return None

    return {"filename": filename, "ip": ip, "port": port, "size": size}


def _normalize_filename_for_match(name):
    """Loosen filename comparison enough to survive the one transformation
    every DCC client applies: replacing spaces with underscores (see dcc.py's
    own outbound SEND, dcc.py:1012, `file_name.replace(" ", "_")`). Treats
    runs of whitespace/underscore as equivalent and compares case-insensitively.
    """
    return re.sub(r'[\s_]+', ' ', str(name).strip()).strip().lower()


def _claim_matching_offer_locked(queue, from_nick, filename):
    """Find and claim (mark 'receiving') the 'offered' row this CTCP answers.

    Must be called with the fetch lock held. Returns (request_id, row), or
    (None, None) if nothing pending matches - which is the admission-control
    rejection path: an offer with no matching outbound request is never
    acted on.

    Branches on the candidate row's request_type, which is the ONLY thing
    that differs between the three - everything else (size cap, path
    containment, passive-vs-active handling) runs identically afterwards in
    handle_incoming_offer(), regardless of which branch matched here:

      * "file" (the original, default behaviour): exact bot+filename match,
        underscore/space-normalised - unchanged from before request_type
        existed.
      * "list": bot alone - we sent a bare "@<bot>" (see check_fetch_queue())
        and cannot know ahead of time what the target bot will name its list
        zip, so any filename from the right bot is acceptable. If more than
        one "list" row somehow ends up outstanding for the same bot at once
        (not the normal case, but not assumed impossible either), the OLDEST
        one is claimed - the same requested_at tie-break the dispatcher
        itself already uses when promoting pending rows.
      * "folder": bot alone, identical reasoning and identical oldest-wins
        tie-break to "list" - we sent "!<bot> !rar <folder path>" and cannot
        know ahead of time what the target bot will name the resulting .rar
        either. On a match, row["filename"] is overwritten with the real
        advertised name (row["requested_filename"], set once at creation, is
        left untouched - see new_fetch_row()).

    The exact-match "file" check runs FIRST and independently of the "list"
    and "folder" checks below it (not as an either/or on the same row) - a
    "file" row can only ever be satisfied by an exact filename match, and a
    "list"/"folder" row can only ever be satisfied by its own bot-alone
    match; no branch can accidentally satisfy another request_type's
    requirement for a DIFFERENT row, because each loop only ever looks at
    rows of its own request_type - a "folder" row must never satisfy a
    "list" row's match (or vice versa) any more than either can satisfy a
    "file" row's, and vice versa.
    """
    wanted_bot = str(from_nick).strip().lower()
    wanted_name = _normalize_filename_for_match(filename)

    for rid, row in queue.items():
        if row.get("state") != "offered":
            continue
        if row.get("request_type", "file") != "file":
            continue
        if str(row.get("bot", "")).strip().lower() != wanted_bot:
            continue
        if _normalize_filename_for_match(row.get("filename", "")) != wanted_name:
            continue
        row["state"] = "receiving"
        return rid, row

    list_candidates = [
        (rid, row) for rid, row in queue.items()
        if row.get("state") == "offered"
        and row.get("request_type") == "list"
        and str(row.get("bot", "")).strip().lower() == wanted_bot
    ]
    if list_candidates:
        rid, row = min(list_candidates, key=lambda pair: pair[1].get("requested_at", 0))
        # Record the actual advertised filename now that we know it - the row
        # was created with filename="" (see webserver.build_list_fetch_enqueue_result()),
        # since it genuinely was not knowable before this moment.
        row["filename"] = filename
        row["state"] = "receiving"
        return rid, row

    folder_candidates = [
        (rid, row) for rid, row in queue.items()
        if row.get("state") == "offered"
        and row.get("request_type") == "folder"
        and str(row.get("bot", "")).strip().lower() == wanted_bot
    ]
    if folder_candidates:
        rid, row = min(folder_candidates, key=lambda pair: pair[1].get("requested_at", 0))
        # Same reasoning as the "list" branch above: the row was created with
        # filename="!rar <folder path>" (see
        # webserver.build_folder_rar_fetch_enqueue_result()), the literal
        # request text, not the name the target bot will actually give its
        # packed .rar - that is only known now. row["requested_filename"] was
        # set once at creation (new_fetch_row()) and is left untouched here,
        # so the original request text survives even after this overwrite.
        row["filename"] = filename
        row["state"] = "receiving"
        return rid, row

    return None, None


def _sanitize_offer_filename(raw_name):
    """Never trust an offer's filename as a literal path component.

    Strips control/colour codes (list.py's regex), path separators, `..`,
    null bytes, and anything outside dcc.py's own charset whitelist
    (dcc.py:762) - in that order, then falls back to a safe placeholder if
    nothing printable survives. The CALLER still must run the result through
    dcc.is_safe_path() against FETCHED_FILES_DIR before opening a file with
    it; this function only produces a plausible bare filename, it does not
    itself prove the final path is safe.
    """
    name = list_mod.strip_control_codes(raw_name)
    name = name.replace('\x00', '')
    name = name.replace('/', '_').replace('\\', '_')
    name = name.replace('..', '')
    name = _FILENAME_CHARSET_RE.sub('', name)
    name = name.strip().strip('.').strip()
    if not name:
        name = "fetched_file"
    return name


def _resolve_destination_path(request_id, raw_filename):
    """Build the on-disk path a completed fetch will be written to, or None
    if it fails the path-containment check. The request id is folded into
    the stored filename so two fetches that happen to share a cleaned
    filename can never collide or overwrite each other.
    """
    dest_dir = os.path.abspath(getattr(config, "FETCHED_FILES_DIR", "./data/fetched"))
    clean_name = _sanitize_offer_filename(raw_filename)
    stored_name = f"{request_id}_{clean_name}"
    candidate = os.path.join(dest_dir, stored_name)
    if not dcc.is_safe_path(dest_dir, candidate):
        return None, None
    return dest_dir, stored_name


def handle_incoming_offer(irc_sock, from_nick, ctcp_payload):
    """Entry point, dispatched from irc.py's CTCP branch in a daemon thread.

    Parses the offer, enforces admission control (must match a row WE marked
    'offered' a moment ago), enforces the size cap BEFORE connecting or
    listening, then runs the bounded transfer. Every exit path that is not a
    clean 'complete' leaves the claimed row 'failed' with a short reason - it
    never leaves a row stuck in 'receiving'/'listening' forever.

    Admission control, the size cap and the destination-path check are
    IDENTICAL for the active and passive (port 0) forms and all run here,
    BEFORE either a socket is dialled or a listening socket is ever opened -
    an unsolicited passive offer is dropped in exactly the same place, and
    exactly as early, as an unsolicited active one.
    """
    offer = parse_dcc_send_offer(ctcp_payload)
    if offer is None:
        print(f"[FETCH] Unusable DCC SEND offer from {from_nick}: {ctcp_payload!r}")
        return

    is_passive = offer["port"] == 0

    queue = _ensure_fetch_queue()
    with _fetch_lock():
        request_id, row = _claim_matching_offer_locked(queue, from_nick, offer["filename"])
        if row is None:
            # ADMISSION CONTROL: no matching outbound request. This is the
            # core safety guardrail - without it, any user or bot in the
            # channel could hand the daemon an arbitrary IP:port to connect
            # to (active) or make it open a listening socket and accept
            # arbitrary bytes (passive) just by sending an unsolicited DCC
            # SEND. Applies identically to both forms.
            print(f"[FETCH] Rejected unsolicited{' passive' if is_passive else ''} "
                  f"DCC SEND from {from_nick} ({offer['filename']!r}): "
                  f"no matching pending request.")
            return

        # A "folder" row packs a whole album/discography into one .rar, which
        # routinely dwarfs any single file - MAX_FETCH_FILE_SIZE (default
        # 200MB) would make this feature fail on its very first real use, so
        # it gets its own, larger cap instead.
        if row.get("request_type") == "folder":
            max_size = int(getattr(config, "MAX_FETCH_FOLDER_FILE_SIZE", 2147483648))
            cap_name = "MAX_FETCH_FOLDER_FILE_SIZE"
        else:
            max_size = int(getattr(config, "MAX_FETCH_FILE_SIZE", 200 * 1024 * 1024))
            cap_name = "MAX_FETCH_FILE_SIZE"
        if offer["size"] > max_size:
            _mark_failed_locked(row, f"declared size {offer['size']} exceeds {cap_name} ({max_size})")
            print(f"[FETCH] Rejected oversized offer from {from_nick}: "
                  f"{offer['size']} > {max_size}. Never connected.")
            return

        dest_dir, stored_name = _resolve_destination_path(request_id, offer["filename"])
        if stored_name is None:
            _mark_failed_locked(row, "unsafe destination path")
            print(f"[FETCH] Rejected offer from {from_nick}: sanitised filename escaped FETCHED_FILES_DIR.")
            return

        row["total_size"] = offer["size"]
        row["stored_filename"] = stored_name
        if is_passive:
            row["state"] = "listening"
            row["listening_since"] = time.time()

    if is_passive:
        _serve_passive_offer(irc_sock, from_nick, row, offer, dest_dir, stored_name)
        return

    _run_transfer(row, offer, dest_dir, stored_name)


def _open_fetch_listener():
    """Bind a listener inside the shared DCC port range, for answering a
    passive/reverse DCC SEND offer.

    Three different listeners already share this one small range (11 ports
    by default): dcc.py's own outbound SEND (start_dcc_send()) scans UPWARD
    from DCC_PORT_START, and adminchat._open_chat_listener()'s passive DCC
    CHAT scans DOWNWARD from DCC_PORT_END. An earlier version of this
    function also scanned downward from DCC_PORT_END - meaning it shared
    adminchat's exact first-probed port and every port after it, despite a
    docstring here claiming the two landed at "opposite ends" (that claim
    had only ever compared this function to dcc.py, never to adminchat, the
    listener it actually collides with).
    Starting from the MIDPOINT and scanning outward (then wrapping) instead
    means this function's first-probed port is never the same as either of
    the other two listeners' first-probed port, for as long as there is more
    than one free port in the range - all three still ultimately compete for
    the same finite pool once it's nearly full, which no ordering can avoid.
    Same range as every other DCC listener in this project either way, so no
    extra firewall rule is needed for this to work.
    """
    start = int(getattr(config, "DCC_PORT_START", 55000))
    end = int(getattr(config, "DCC_PORT_END", 55010))
    mid = (start + end) // 2
    ordered_ports = list(range(mid, end + 1)) + list(range(mid - 1, start - 1, -1))
    for port in ordered_ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR means the OPPOSITE thing on Windows - platform_compat
        # picks the right option per platform. Same call adminchat.py and
        # dcc.py's own listener setup already make.
        platform_compat.prepare_listener(sock)
        try:
            sock.bind(("0.0.0.0", port))
            # listen() HERE, before returning - see adminchat._open_chat_listener()'s
            # comment: on POSIX, SO_REUSEADDR lets a second socket bind the
            # same port while the first is bound but not yet listening, so a
            # merely-bound socket could be raced by a second passive offer.
            sock.listen(1)
            return sock, port
        except OSError:
            sock.close()
            continue
    return None, None


def _serve_passive_offer(irc_sock, from_nick, row, offer, dest_dir, stored_name):
    """Answer a passive/reverse DCC SEND offer: we become the listener.

    Mirrors adminchat._listen_and_serve()'s identical pattern for passive DCC
    CHAT - open a listener in the shared DCC port range, announce our own
    ip:port plus the offer's token through the CTCP reply, and wait with a
    bounded timeout for the offering bot to connect back. On a successful
    accept, hand off into the SAME bounded-transfer code (_run_transfer())
    the active path uses - only how the socket was obtained differs.

    Called only from handle_incoming_offer(), and only AFTER admission
    control, the size cap and the destination-path check have all already
    passed - this function is not itself a fresh trust boundary, it only
    ever runs for a request already matched to a row this bot created.
    """
    ip_long = dcc.get_public_ip_long()
    if not ip_long:
        _mark_failed_locked(row, "our own public IP is unknown")
        print(f"[FETCH] Cannot answer {from_nick}'s passive DCC SEND offer: "
              f"the bot's own public IP is unknown (config.MY_IP_OR_DOCK did not resolve).")
        return

    listener, port = _open_fetch_listener()
    if listener is None:
        start = getattr(config, "DCC_PORT_START", 55000)
        end = getattr(config, "DCC_PORT_END", 55010)
        _mark_failed_locked(row, "no free DCC port for the passive listener")
        print(f"[FETCH] No free port in {start}-{end} to answer {from_nick}'s "
              f"passive DCC SEND offer for {offer['filename']!r}.")
        return

    oserve = sys.modules.get("oserve")
    if not (oserve and hasattr(oserve, "queue_message")):
        # No paced outbound queue available. This should not happen outside a
        # very early boot race or a test that forgot to stub it, but leaving
        # the row stuck 'listening' with no way to ever answer it would be
        # worse than failing it outright - and the listener must not leak.
        _mark_failed_locked(row, "outbound message queue unavailable")
        print(f"[FETCH] Cannot answer {from_nick}'s passive DCC SEND offer: "
              f"oserve.queue_message is unavailable.")
        try:
            listener.close()
        except OSError:
            pass
        return

    try:
        listener.settimeout(PASSIVE_LISTEN_TIMEOUT)
        # dcc.py's own outbound SEND replaces spaces with underscores before
        # sending the handshake (dcc.py:1012) - the same transformation any
        # DCC client applies, and needed here too so the filename cannot
        # swallow the positional fields that follow it.
        safe_filename = offer["filename"].replace(" ", "_")
        token = offer["token"]
        # Defense-in-depth only, expected to be unreachable: parse_dcc_send_
        # offer() already rejects any offer whose filename/token contains
        # \r, \n or \x01 before it is ever turned into an `offer` dict (see
        # contains_unsafe_ctcp_bytes() and its callers there). This is one of
        # several places this exact injection class has recurred in this
        # feature (see contains_unsafe_ctcp_bytes()'s own comment above), so
        # this call site - the one that actually interpolates both values
        # into a raw outbound CTCP line - does not simply trust that
        # upstream check was applied; it refuses to build the message at all
        # if either value is still unsafe for some future reason.
        if contains_unsafe_ctcp_bytes(safe_filename) or contains_unsafe_ctcp_bytes(token):
            _mark_failed_locked(row, "unsafe characters in offer filename/token")
            print(f"[FETCH] Refusing to answer {from_nick}'s passive DCC SEND "
                  f"offer: filename/token contains control characters this "
                  f"late (should be unreachable - see parse_dcc_send_offer()).")
            return
        message = (f"PRIVMSG {from_nick} :\x01DCC SEND {safe_filename} "
                   f"{ip_long} {port} {offer['size']} {token}\x01\r\n")
        oserve.queue_message(from_nick, message)
        print(f"[FETCH] Answered {from_nick}'s passive DCC SEND offer for "
              f"{offer['filename']!r} on port {port}; waiting for the connection.")

        # SECURITY (accepted, narrowed risk - not an oversight, same spirit as
        # WEBUI_HOST's no-auth comment in config.py): accept() below takes the
        # FIRST TCP connection that arrives on this port, from ANYONE who can
        # reach it, and cannot itself verify that the peer is actually
        # `from_nick`'s bot. Passive DCC is passive precisely because the
        # offering bot's real source address is not reliably knowable ahead of
        # time (that is WHY it asked us to listen instead of dialling it) -
        # there is no WHO/WHOIS-derived address lookup in this codebase to
        # check the peer against, and the DCC SEND protocol itself has no
        # post-connect handshake to authenticate the peer with (unlike admin
        # DCC CHAT, which layers its own password auth over the accepted
        # socket - see adminchat.py's _serve()/AUTH handling - there is
        # nothing equivalent to layer on top of a raw file byte stream, which
        # IS the payload here).
        #
        # Mitigations actually in place:
        #   1. Admission control (handle_incoming_offer(), before this
        #      function is ever called) means a listener is only ever opened
        #      for a fetch WE explicitly requested - the residual risk is
        #      specifically WHO answers a request we made, not whether an
        #      attacker can make us ask in the first place.
        #   2. The listener is single-shot: one accept(), then closed in the
        #      `finally` below - never re-armed - and only open for
        #      PASSIVE_LISTEN_TIMEOUT (60s) inside the narrow, already-
        #      firewalled config.DCC_PORT_START..DCC_PORT_END range.
        #   3. Best-effort peer check, below, after the accept succeeds: if
        #      the offer's own ip_long field decoded to something usable
        #      (offer["claimed_ip"], from parse_dcc_send_offer()), the
        #      accepted peer's address is compared against it and a mismatch
        #      is logged and recorded on the row - but deliberately NOT
        #      treated as a hard rejection. A real offering bot behind NAT
        #      routinely advertises a private/internal address that
        #      legitimately differs from its outbound public address, and
        #      hard-rejecting on that basis would break real-world interop
        #      for exactly the deployments passive DCC exists to support.
        #
        # Residual, accepted risk: a same-LAN or otherwise well-positioned
        # attacker who races the real offering bot's connection within the
        # ~60s window, on this narrow/low-cardinality port range, can still
        # win and have their bytes accepted as the "fetched" file. There is
        # no robust fix for this without either inventing a nonstandard
        # protocol extension a real third-party bot would not speak, or a
        # WHO/WHOIS round trip this codebase does not otherwise perform -
        # both judged disproportionate to what a plain-text file-sharing
        # protocol with no authentication of its own can realistically offer.
        conn, addr = listener.accept()
    except socket.timeout:
        _mark_failed_locked(row, "passive offer: no connection received")
        print(f"[FETCH] {from_nick} never connected back within "
              f"{int(PASSIVE_LISTEN_TIMEOUT)}s for the passive DCC SEND offer "
              f"on port {port}; giving the port back.")
        return
    except OSError as err:
        _mark_failed_locked(row, f"passive listen error: {err}")
        print(f"[FETCH] Passive DCC SEND listener error for {from_nick}: {err}")
        return
    except Exception as err:
        # Defense-in-depth, expected to be unreachable: everything above this
        # point already has its own specific handling. This exists so that
        # NO exception - not just the two anticipated above - can ever leave
        # this row stuck in 'listening' forever. handle_incoming_offer() runs
        # in a bare daemon thread with nothing above it to catch a stray
        # exception, and check_fetch_queue()'s own expiry loop only re-checks
        # 'offered' rows (see its docstring), not 'listening' ones - so
        # without this, an unanticipated error here would silently and
        # permanently strand a MAX_FETCH_SLOTS slot until the process
        # restarts. (check_fetch_queue() ALSO now expires a stale 'listening'
        # row on a timer, as a second, independent safety net in case this
        # function's own thread dies or hangs before even reaching this
        # try block.)
        _mark_failed_locked(row, f"unexpected error: {err}")
        print(f"[FETCH] Unexpected error answering {from_nick}'s passive DCC "
              f"SEND offer for {offer['filename']!r}: {err!r}")
        return
    finally:
        try:
            listener.close()
        except OSError:
            pass

    peer_ip = addr[0] if addr else None
    claimed_ip = offer.get("claimed_ip")
    row["passive_peer_ip"] = peer_ip
    if claimed_ip and peer_ip and claimed_ip != peer_ip:
        # Best-effort only - see the long comment above listener.accept() for
        # why this is logged/recorded rather than treated as a hard reject.
        row["passive_peer_ip_mismatch"] = True
        print(f"[FETCH] WARNING: passive DCC SEND connection answering "
              f"{from_nick}'s offer of {offer['filename']!r} arrived from "
              f"{peer_ip}, but the offer itself claimed {claimed_ip}. "
              f"Accepting it anyway (best-effort check only - see the "
              f"comment above listener.accept() in dcc_fetch.py); treat a "
              f"mismatch here as suspicious.")

    with _fetch_lock():
        row["state"] = "receiving"

    _run_transfer(row, offer, dest_dir, stored_name, sock=conn)


def _handle_completed_list_fetch(row, zip_path):
    """Delegate a completed request_type="list" fetch to list_fetch.py for
    safe extraction/parsing, and record the outcome on the row for the
    dashboard - but never let a problem there affect the fetch itself, which
    already succeeded (the bytes arrived intact; this is purely about what is
    INSIDE them). Imported locally, not at module top, for the same reason
    dcc_fetch.py already imports `list as list_mod` and not e.g. `webserver`
    at top level: keeps this module's own import graph minimal and avoids a
    cycle (list_fetch.py imports dcc_fetch's sibling modules, not the other
    way around).
    """
    try:
        import list_fetch
        ok, reason = list_fetch.process_fetched_list_zip(row.get("bot", ""), zip_path)
        if not ok:
            row["list_processing_error"] = reason or "no recognizable list file found in the zip"
            print(f"[FETCH] {row.get('bot')}'s fetched list zip was received "
                  f"successfully but could not be processed: {row['list_processing_error']}")
    except Exception as err:
        # Defense-in-depth, expected to be unreachable: list_fetch.py's own
        # entry point already catches everything it knows how to anticipate.
        # This exists so that an unanticipated error while processing an
        # untrusted third party's zip can never propagate back out of a
        # transfer that itself already completed successfully.
        row["list_processing_error"] = f"unexpected error: {err}"
        print(f"[FETCH] Unexpected error processing {row.get('bot')}'s fetched list zip: {err!r}")


def _run_transfer(row, offer, dest_dir, stored_name, sock=None):
    """The actual bounded socket transfer. `row` has already been claimed
    ('receiving') and validated by handle_incoming_offer(); this just moves
    bytes, with three independent guards:

      * CONNECT_TIMEOUT on the dial itself (active offers only)
      * IDLE_RECV_TIMEOUT per recv() call (mirrors dcc.py:1024's conn.settimeout(60.0))
      * FETCH_TRANSFER_TIMEOUT as a wall-clock ceiling, for a slow-drip peer
        that keeps resetting the idle timer without ever finishing

    and aborts (deleting the partial file) if the peer sends more than it
    declared.

    `sock` is None for a normal (active) offer, in which case this dials
    offer["ip"]:offer["port"] itself exactly as before. For the passive/
    reverse form, _serve_passive_offer() has already listened and accepted
    the inbound connection, and hands the resulting socket in here directly -
    everything from this point on (size cap enforcement, idle/wall-clock
    timeouts, oversize-abort) is identical either way; only how the socket
    was obtained differs.
    """
    total_size = offer["size"]
    dest_path = os.path.join(dest_dir, stored_name)
    wall_deadline = time.time() + float(getattr(config, "FETCH_TRANSFER_TIMEOUT", 600))

    try:
        os.makedirs(platform_compat.long_path(dest_dir), exist_ok=True)
    except Exception as mkdir_err:
        _mark_failed_locked(row, f"could not create destination dir: {mkdir_err}")
        print(f"[FETCH] {mkdir_err}")
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass
        return

    if sock is None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(CONNECT_TIMEOUT)
        try:
            sock.connect((offer["ip"], offer["port"]))
        except Exception as connect_err:
            _mark_failed_locked(row, f"connect error: {connect_err}")
            print(f"[FETCH] Could not connect to {offer['ip']}:{offer['port']}: {connect_err}")
            try:
                sock.close()
            except Exception:
                pass
            return

    sock.settimeout(IDLE_RECV_TIMEOUT)

    # For a passive offer offer["ip"] is None (never dialled - see
    # parse_dcc_send_offer()); the peer's real address is only known once we
    # have actually accepted its connection.
    peer_desc = offer.get("ip")
    if peer_desc is None:
        try:
            peer_desc = sock.getpeername()[0]
        except OSError:
            peer_desc = "?"

    bytes_received = 0
    failure_reason = None
    handle = None
    try:
        # _sanitize_offer_filename() does not truncate, so the length of this
        # name is entirely the offering bot's choice - wrap it like dcc.py
        # wraps every path it touches.
        handle = open(platform_compat.long_path(dest_path), "wb")
        while bytes_received < total_size:
            if time.time() > wall_deadline:
                failure_reason = "overall transfer timeout"
                break
            try:
                chunk = sock.recv(RECV_CHUNK)
            except socket.timeout:
                failure_reason = "idle timeout"
                break
            if not chunk:
                # Peer closed early. Only acceptable if it happens to land
                # exactly on the declared size (some clients close instead
                # of lingering) - otherwise it is a short transfer.
                if bytes_received < total_size:
                    failure_reason = "connection closed before declared size was reached"
                break

            bytes_received += len(chunk)
            if bytes_received > total_size:
                # A lying offer: the peer is sending more than it declared.
                # Abort rather than silently keeping the overflow.
                failure_reason = "received more bytes than the declared size"
                handle.write(chunk[:max(0, len(chunk) - (bytes_received - total_size))])
                bytes_received = total_size
                break

            handle.write(chunk)
            row["bytes_received"] = bytes_received
    except Exception as recv_err:
        failure_reason = f"transfer error: {recv_err}"
    finally:
        try:
            if handle:
                handle.close()
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    if failure_reason is None and bytes_received == total_size:
        row["state"] = "complete"
        row["bytes_received"] = bytes_received
        print(f"[FETCH] Complete: {stored_name} ({bytes_received} bytes) from {peer_desc}.")
        if row.get("request_type") == "list":
            # The DCC transfer itself succeeded (declared size matched what
            # arrived) - that is what "complete" above means, and is left
            # alone either way. What happens NEXT - safely unzipping and
            # parsing an untrusted third party's list archive - is a
            # genuinely separate trust boundary (see list_fetch.py's module
            # docstring: zip-slip, zip-bomb, "no recognisable list file
            # inside"), so it is handled by a dedicated module and never
            # allowed to raise back into this transfer's own success path.
            _handle_completed_list_fetch(row, dest_path)
        return

    if failure_reason is None:
        failure_reason = f"incomplete transfer ({bytes_received}/{total_size} bytes)"

    _mark_failed_locked(row, failure_reason)
    print(f"[FETCH] Failed ({failure_reason}): {stored_name}.")
    try:
        if os.path.exists(platform_compat.long_path(dest_path)):
            # Unwrapped, exists() answers False for a >260 path and the
            # partial file from a failed long-named transfer is never
            # cleaned up.
            os.remove(platform_compat.long_path(dest_path))
    except OSError:
        pass
