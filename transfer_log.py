"""A record of every request and transfer, sent and received (#1068).

WHY

The bot kept totals only - lifetime and daily counts in stats.txt, a per-file
count in download_counts.json, the last 500 downloads for the Downloads page -
so "who downloaded most this month", "which hours are busiest", "how long do
people wait" could never be answered, and whatever was not written down was
gone. This keeps one row per request, written when it is queued (or refused)
and finished however it ends, and one per download from another bot. Any
statistic is then a query, including ones nobody has thought of yet.

WHAT A ROW HOLDS

Nick (as shown, and lower-cased for grouping), the channel it was asked in,
the file, its path and the list it came from, the kind (file, folder pack,
list), sizes and bytes moved, the resume offset, the times it was requested,
started and ended, and how it ended. NICK ONLY: no host, no IP address, and
no searches - a user who changes nick appears under each nick.

    result, sent:      queued, sent, failed, removed, expired, cleared, refused
    result, received:  complete, failed, cancelled

WHY SQLITE

Writing is one row per request; reading is where a format earns its keep. A
text or JSON record would be read end to end for every question, and that
gets slow as the years add up. SQLite answers from an index, is in Python's
standard library, behaves the same on Windows and Linux, and the bot already
keeps list_index.db in it.

NOTHING WAITS ON IT

The calls below never touch the disk. They put the row on a list in
runtime.py and return; one background thread writes what has gathered about
once a second, in one transaction, on a connection it opens and closes each
time. A busy channel, a slow disk or a damaged file can therefore never hold
up the IRC thread, queue_lock or a transfer, and a write that fails is said
once in the log and dropped - it must never cost a transfer anything.

Each row carries the database path it was recorded under, read from config
at that moment. A test's rows therefore go to that test's file even when the
writer gets to them after the test is over, never to the real data/.

Every write is an upsert on the row's id: the first call for a request
creates it, later ones fill in what they know. A request queued before this
existed simply gets its row when it ends.
"""

import atexit
import os
import sqlite3
import threading
import time
import uuid

import defaults as config
import runtime

SCHEMA_VERSION = 1
FLUSH_SECONDS = 1.0

# The real Thread, taken once: some tests swap threading.Thread for a recorder
# that never runs anything, and the writer must not be one of those.
_THREAD = threading.Thread

COLUMNS = (
    "id", "direction", "nick", "nick_key", "channel", "kind", "name",
    "arrived_as", "path", "list_label", "size", "bytes", "resume_offset",
    "passive", "queue_position", "queue_length", "requested_at", "offered_at",
    "started_at", "ended_at", "result", "reason", "failures",
)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS requests ("
    " id TEXT PRIMARY KEY,"
    " direction TEXT,"          # sent | received
    " nick TEXT,"               # the user, or for a received file the other bot
    " nick_key TEXT,"           # lower-cased, for grouping
    " channel TEXT,"            # where it was asked for; our nick when private
    " kind TEXT,"               # file | folder | list
    " name TEXT,"               # what was sent, or what we asked for
    " arrived_as TEXT,"         # received: the name the file came with
    " path TEXT,"               # sent: where it is on disk
    " list_label TEXT,"         # sent: which shared list
    " size INTEGER,"
    " bytes INTEGER,"           # actually moved
    " resume_offset INTEGER,"
    " passive INTEGER,"         # received: the other bot needed a passive DCC
    " queue_position INTEGER,"  # sent: place in the user's own list; received: ours there
    " queue_length INTEGER,"    # sent: rows in the whole queue when asked
    " requested_at REAL,"
    " offered_at REAL,"
    " started_at REAL,"
    " ended_at REAL,"
    " result TEXT,"
    " reason TEXT,"
    " failures INTEGER)",
    "CREATE INDEX IF NOT EXISTS requests_ended ON requests(ended_at)",
    "CREATE INDEX IF NOT EXISTS requests_nick ON requests(nick_key, ended_at)",
    "CREATE INDEX IF NOT EXISTS requests_direction ON requests(direction, result)",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
)


def log_file():
    """Read through config every time: the tests redirect it per case, and a
    settings save can move it."""
    return getattr(config, "TRANSFER_LOG_FILE", os.path.join("data", "transfers.db"))


def enabled():
    return bool(getattr(config, "TRANSFER_LOG", True))


def new_id():
    return uuid.uuid4().hex[:16]


# ---------------------------------------------------------------- the writer

def _record(row):
    """Queue one row (a dict with an "id") for the writer. Never blocks on
    the disk and never raises."""
    if not enabled() or not row.get("id"):
        return
    try:
        item = (log_file(), {k: v for k, v in row.items() if k in COLUMNS})
        with runtime.transfer_log_lock:
            runtime.transfer_log_pending.append(item)
            _ensure_writer_locked()
            runtime.transfer_log_lock.notify_all()
    except Exception as err:
        print(f"[TRANSFER-LOG] Could not record a row: {err}")


def _ensure_writer_locked():
    writer = runtime.transfer_log_writer_thread
    if writer is not None and writer.is_alive():
        return
    writer = _THREAD(target=_write_forever, name="transfer-log", daemon=True)
    runtime.transfer_log_writer_thread = writer
    writer.start()


def _write_forever():
    while True:
        with runtime.transfer_log_lock:
            runtime.transfer_log_lock.wait_for(
                lambda: bool(runtime.transfer_log_pending), timeout=60)
        # Let the rest of a burst gather, so it goes in one transaction.
        time.sleep(FLUSH_SECONDS)
        flush()


def flush():
    """Write everything gathered so far, now, on this thread. The writer
    calls it; so do exit and the tests. Returns how many rows were written.

    The rows are taken while the write lock is held, so a flush that finds
    nothing has still waited for a write already under way: whoever flushes
    and then reads sees every row recorded before the flush."""
    written = 0
    with runtime.transfer_log_write_lock:
        with runtime.transfer_log_lock:
            batch = list(runtime.transfer_log_pending)
            del runtime.transfer_log_pending[:]
        if not batch:
            return 0
        by_path = {}
        for path, row in batch:
            by_path.setdefault(path, []).append(row)
        for path, rows in by_path.items():
            try:
                written += _write(path, rows)
            except Exception as err:
                print(f"[TRANSFER-LOG] Could not write {len(rows)} row(s) to "
                      f"{path}: {err}. Transfers are unaffected.")
    return written


def _write(path, rows):
    conn = _open(path)
    try:
        with conn:
            for row in rows:
                columns = [c for c in COLUMNS if c in row]
                updates = ", ".join(f"{c}=excluded.{c}" for c in columns if c != "id")
                sql = (f"INSERT INTO requests ({', '.join(columns)}) "
                       f"VALUES ({', '.join('?' for _ in columns)})")
                if updates:
                    sql += f" ON CONFLICT(id) DO UPDATE SET {updates}"
                else:
                    sql += " ON CONFLICT(id) DO NOTHING"
                conn.execute(sql, [row[c] for c in columns])
        return len(rows)
    finally:
        conn.close()


def _open(path):
    """A connection with the schema in place. A file sqlite3 says is not a
    database, or is damaged, is moved aside - kept, never deleted - and a
    fresh one started, so one bad file cannot stop the record for good."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    try:
        return _open_with_schema(path)
    except sqlite3.DatabaseError as err:
        if isinstance(err, sqlite3.OperationalError) or not os.path.isfile(path):
            raise
        aside = f"{path}.damaged-{time.strftime('%Y%m%d-%H%M%S')}"
        os.replace(path, aside)
        print(f"[TRANSFER-LOG] {path} was damaged ({err}); moved it to {aside} "
              f"and started a new record.")
        return _open_with_schema(path)


def _open_with_schema(path):
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        for statement in _SCHEMA:
            conn.execute(statement)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


atexit.register(flush)


def read_rows(path=None):
    """Every row, oldest request first, after writing what is pending. For
    the tests, and for anything that wants the whole record."""
    flush()
    path = path or log_file()
    if not os.path.isfile(path):
        return []
    conn = _open(path)
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM requests ORDER BY COALESCE(requested_at, started_at, ended_at), rowid")]
    finally:
        conn.close()


# ---------------------------------------------------------- the send side

def _nick(nick):
    nick = str(nick or "")
    return {"nick": nick, "nick_key": nick.lower()}


def row_id(queue_row):
    """The id this queue row is recorded under, given one if it has none (a
    row queued before the record existed). Stored on the row, so it is kept
    in dcc_queue.txt and every later event finds the same record."""
    if not isinstance(queue_row, dict):
        return None
    if not queue_row.get("log_id"):
        queue_row["log_id"] = new_id()
    return queue_row["log_id"]


def stamp(queue_row):
    """Give a new queue row its id and the time it was asked for. Called
    where the row is built, before it is appended, so the time is the
    request's own and not when it was first logged."""
    if isinstance(queue_row, dict) and enabled():
        queue_row.setdefault("log_id", new_id())
        queue_row.setdefault("requested_at", time.time())
    return queue_row


def queued(queue_row, nick, channel, kind, position=None, queue_length=None, list_label=None):
    """A request was queued, or sent straight away (position None)."""
    if not enabled() or not isinstance(queue_row, dict):
        return
    _mark_request_recorded()
    _record(dict(_nick(nick), id=row_id(queue_row), direction="sent", channel=channel,
                 kind=kind, name=queue_row.get("file"), path=queue_row.get("path"),
                 list_label=list_label, queue_position=position, queue_length=queue_length,
                 requested_at=queue_row.get("requested_at"), result="queued"))


def send_started(queue_row, nick, started_at, size, resume_offset=0):
    if not enabled() or not isinstance(queue_row, dict):
        return
    _record(dict(_nick(nick), id=row_id(queue_row), direction="sent",
                 name=queue_row.get("file"), path=queue_row.get("path"),
                 started_at=started_at, size=size, resume_offset=resume_offset or 0))


def send_finished(queue_row, nick, bytes_moved, ended_at=None):
    """The bytes a send attempt moved and when it stopped. How it ended is
    settled() - the queue's own decision, which this cannot know."""
    if not enabled() or not isinstance(queue_row, dict):
        return
    _record(dict(_nick(nick), id=row_id(queue_row), direction="sent",
                 bytes=bytes_moved, ended_at=ended_at or time.time()))


def settled(queue_row, nick, delivered, retained, reason):
    """dcc.release_queue_entry() decided: sent, kept for another try, or
    given up on. A kept row stays "queued" with its failures counted."""
    if not enabled() or not isinstance(queue_row, dict):
        return
    if delivered:
        result, why = "sent", None
    elif retained:
        result, why = "queued", reason
    else:
        result, why = "failed", reason
    _record(dict(_nick(nick), id=row_id(queue_row), direction="sent", result=result,
                 reason=why, failures=int(queue_row.get("send_fails") or 0)))


def removed(queue_rows, nick, result, reason=None):
    """Rows taken out of the queue without being sent: "removed" by the user,
    "expired" after they left, "cleared" by an admin, or "failed"."""
    if not enabled():
        return
    now = time.time()
    for queue_row in queue_rows or ():
        if isinstance(queue_row, dict):
            _record(dict(_nick(nick), id=row_id(queue_row), direction="sent",
                         name=queue_row.get("file"), path=queue_row.get("path"),
                         channel=queue_row.get("channel"),
                         requested_at=queue_row.get("requested_at"),
                         ended_at=now, result=result, reason=reason))


# A request is followed through handle_download_request() so a refusal can be
# recorded with the reason the user was told. Per thread: requests are
# handled on several at once.
_request = threading.local()


def begin_request(nick, requested, channel, kind):
    if not enabled():
        return None
    ctx = {"nick": nick, "requested": requested, "channel": channel, "kind": kind,
           "at": time.time(), "reason": None, "recorded": False}
    _request.current = ctx
    return ctx


def note_refusal(reason):
    """Called where the user is told no. The first reason wins."""
    ctx = getattr(_request, "current", None)
    if ctx is not None and ctx["reason"] is None:
        ctx["reason"] = reason


def _mark_request_recorded():
    ctx = getattr(_request, "current", None)
    if ctx is not None:
        ctx["recorded"] = True


def end_request(ctx, error=None):
    """A request that was neither queued nor sent, and was answered with a
    reason (or broke), is recorded as refused. One that was silently ignored
    - not addressed to a list here at all - is not a request."""
    _request.current = None
    if ctx is None or ctx["recorded"]:
        return
    reason = ctx["reason"] or (f"error: {error}" if error is not None else None)
    if reason is None:
        return
    _record(dict(_nick(ctx["nick"]), id=new_id(), direction="sent", channel=ctx["channel"],
                 kind=ctx["kind"], name=ctx["requested"], requested_at=ctx["at"],
                 ended_at=time.time(), result="refused", reason=reason))


# ------------------------------------------------------- the receive side

def fetch_ended(request_id, row, result=None, reason=None):
    """A download from another bot reached an end: complete, failed or
    cancelled. A "no response" failure revived by a late offer simply ends
    again later under the same id."""
    if not enabled() or not request_id or not isinstance(row, dict):
        return
    passive = 1 if (row.get("listening_since") or row.get("passive_peer_ip")) else 0
    asked = row.get("requested_filename") or row.get("filename")
    result = result or row.get("state")
    if reason is None:
        # A complete one has no reason, even if an earlier "no response" is
        # still on the row from before a late offer revived it.
        reason = None if result == "complete" else (row.get("reason") or None)
    _record(dict(_nick(row.get("bot")), id=str(request_id), direction="received",
                 kind=row.get("request_type") or "file", name=asked,
                 arrived_as=row.get("filename") if row.get("filename") != asked else None,
                 size=row.get("total_size"), bytes=row.get("bytes_received"),
                 passive=passive, queue_position=row.get("queue_position"),
                 requested_at=row.get("requested_at"), offered_at=row.get("offered_at"),
                 started_at=row.get("receiving_since"), ended_at=time.time(),
                 result=result, reason=reason or None))
