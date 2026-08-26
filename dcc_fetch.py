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
                   \\-----------------------------> failed (any timeout/
                                                      admission-rejection/
                                                      size-mismatch/connect-
                                                      error - see the row's
                                                      "reason" field)

Rows live in config.fetch_queue (config.py section 8), keyed by a generated
request id. webserver.py's /api/fetch/* routes are the only thing that
creates `pending` rows (POST /api/fetch/enqueue); check_fetch_queue() below
promotes them to `offered`; handle_incoming_offer() (dispatched from irc.py's
CTCP branch) takes it from there.
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

# Connect timeout for dialling the offering bot, and the idle-recv timeout
# once connected. Not config knobs - dcc.py does not expose its own mirror-
# image idle timeout (conn.settimeout(60.0), dcc.py:1024) as one either, and
# these are exactly that same convention on the receiving side.
CONNECT_TIMEOUT = 15.0
IDLE_RECV_TIMEOUT = 60.0

RECV_CHUNK = 65536

# The one convention that is actually standardised across file-sharing bots:
# "!<botnick> <filename>", the same syntax this bot itself answers to (see
# irc.py's get_bot_aliases()/dcc.handle_download_request). Used both by
# irc.py's broadcast-search capture (to offer a "Download" button) and here,
# defensively, nowhere else - dcc_fetch never parses this out of anything,
# it only ever receives a filename we ourselves already chose when the fetch
# was enqueued.
_FILENAME_CHARSET_RE = re.compile(r'[^\w\-_\. \(\)]')  # mirrors dcc.py:762


def _fetch_lock():
    """The dedicated fetch_queue lock oserve.py allocates at startup, or a
    fresh one as a fallback - same idiom as dcc.py's
    `queue_lock if 'queue_lock' in globals() else threading.Lock()`, needed
    because tests and any other caller that never ran oserve.startup() would
    otherwise have nothing to synchronise on.
    """
    return getattr(config, "fetch_queue_lock", None) or threading.Lock()


def _ensure_fetch_queue():
    if not hasattr(config, "fetch_queue") or config.fetch_queue is None:
        config.fetch_queue = {}
    return config.fetch_queue


def new_fetch_row(bot, filename, now=None):
    """Build a fresh `pending` row in the shape every reader of
    config.fetch_queue expects. Does not insert it - callers decide the key.
    """
    now = time.time() if now is None else now
    return {
        "bot": str(bot).strip(),
        "filename": str(filename).strip(),
        "state": "pending",
        "requested_at": now,
        "offered_at": None,
        "bytes_received": 0,
        "total_size": None,
        "reason": "",
        "stored_filename": None,
    }


def enqueue_fetch(bot, filename):
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
        queue[request_id] = new_fetch_row(bot, filename)
    return request_id


def count_active_fetches(queue=None):
    """Rows currently occupying a slot. Derived, not tracked separately - see
    the comment on config.fetch_queue for why."""
    queue = _ensure_fetch_queue() if queue is None else queue
    return sum(1 for row in queue.values() if row.get("state") in ("offered", "receiving"))


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
    if getattr(config, "fetch_feature_disabled", False):
        # FETCHED_FILES_DIR could not be created at startup (see
        # oserve.startup()) - leave rows sitting `pending` rather than ever
        # promoting them; there is nowhere safe to write a completed file.
        return

    queue = _ensure_fetch_queue()
    max_slots = int(getattr(config, "MAX_FETCH_SLOTS", 3))
    offer_timeout = float(getattr(config, "FETCH_OFFER_TIMEOUT", 60))
    now = time.time()

    to_dispatch = []
    with _fetch_lock():
        # Expire offers nobody ever answered. A row stuck in "offered" forever
        # would otherwise hold a slot open permanently and starve every other
        # pending request behind it.
        for row in queue.values():
            if row.get("state") == "offered" and row.get("offered_at") is not None:
                if (now - row["offered_at"]) > offer_timeout:
                    _mark_failed_locked(row, "no response")

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
            to_dispatch.append((rid, row["bot"], row["filename"]))

    if not to_dispatch:
        return

    oserve = sys.modules.get("oserve")
    channel = (getattr(config, "BROADCAST_SEARCH_CHANNEL", None)
               or str(getattr(config, "CHANNEL", "")).split(",")[0].strip())
    for rid, bot, filename in to_dispatch:
        message = f"PRIVMSG {channel} :!{bot} {filename}\r\n"
        if oserve and hasattr(oserve, "queue_message"):
            oserve.queue_message(bot, message)
        print(f"[FETCH] Requested {filename!r} from {bot} (request {rid}).")


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

    Returns {"filename", "ip", "port", "size"} or None for anything
    malformed. The ip_long decode is the exact inverse of
    dcc.get_public_ip_long() (dcc.py:201-210).
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
    if port <= 0 or port > 65535:
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
    """
    wanted_bot = str(from_nick).strip().lower()
    wanted_name = _normalize_filename_for_match(filename)
    for rid, row in queue.items():
        if row.get("state") != "offered":
            continue
        if str(row.get("bot", "")).strip().lower() != wanted_bot:
            continue
        if _normalize_filename_for_match(row.get("filename", "")) != wanted_name:
            continue
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
    'offered' a moment ago), enforces the size cap BEFORE connecting, then
    runs the bounded transfer. Every exit path that is not a clean 'complete'
    leaves the claimed row 'failed' with a short reason - it never leaves a
    row stuck in 'receiving' forever.
    """
    offer = parse_dcc_send_offer(ctcp_payload)
    if offer is None:
        print(f"[FETCH] Unusable DCC SEND offer from {from_nick}: {ctcp_payload!r}")
        return

    queue = _ensure_fetch_queue()
    with _fetch_lock():
        request_id, row = _claim_matching_offer_locked(queue, from_nick, offer["filename"])
        if row is None:
            # ADMISSION CONTROL: no matching outbound request. This is the
            # core safety guardrail - without it, any user or bot in the
            # channel could hand the daemon an arbitrary IP:port to connect
            # to just by sending an unsolicited DCC SEND.
            print(f"[FETCH] Rejected unsolicited DCC SEND from {from_nick} "
                  f"({offer['filename']!r}): no matching pending request.")
            return

        max_size = int(getattr(config, "MAX_FETCH_FILE_SIZE", 200 * 1024 * 1024))
        if offer["size"] > max_size:
            _mark_failed_locked(row, f"declared size {offer['size']} exceeds MAX_FETCH_FILE_SIZE ({max_size})")
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

    _run_transfer(row, offer, dest_dir, stored_name)


def _run_transfer(row, offer, dest_dir, stored_name):
    """The actual bounded socket transfer. `row` has already been claimed
    ('receiving') and validated by handle_incoming_offer(); this just moves
    bytes, with three independent guards:

      * CONNECT_TIMEOUT on the dial itself
      * IDLE_RECV_TIMEOUT per recv() call (mirrors dcc.py:1024's conn.settimeout(60.0))
      * FETCH_TRANSFER_TIMEOUT as a wall-clock ceiling, for a slow-drip peer
        that keeps resetting the idle timer without ever finishing

    and aborts (deleting the partial file) if the peer sends more than it
    declared.
    """
    total_size = offer["size"]
    dest_path = os.path.join(dest_dir, stored_name)
    wall_deadline = time.time() + float(getattr(config, "FETCH_TRANSFER_TIMEOUT", 600))

    try:
        os.makedirs(dest_dir, exist_ok=True)
    except Exception as mkdir_err:
        _mark_failed_locked(row, f"could not create destination dir: {mkdir_err}")
        print(f"[FETCH] {mkdir_err}")
        return

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

    bytes_received = 0
    failure_reason = None
    handle = None
    try:
        handle = open(dest_path, "wb")
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
        print(f"[FETCH] Complete: {stored_name} ({bytes_received} bytes) from {offer.get('ip')}.")
        return

    if failure_reason is None:
        failure_reason = f"incomplete transfer ({bytes_received}/{total_size} bytes)"

    _mark_failed_locked(row, failure_reason)
    print(f"[FETCH] Failed ({failure_reason}): {stored_name}.")
    try:
        if os.path.exists(dest_path):
            os.remove(dest_path)
    except OSError:
        pass
