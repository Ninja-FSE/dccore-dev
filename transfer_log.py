"""A record of finished transfers, with nothing in it that points at a person (#1068).

One row is written when a transfer ends, so a figure nobody thought of yet can
still be worked out later. A row says WHAT moved, how big it was, how fast, and
how long it waited in the queue. It does not say who asked: no nick, no
user@host, no channel and no other bot's name are ever stored, so the file
cannot answer "who downloaded what", and that is the point.

Only completed transfers are written, as with stats.txt. A write that fails is
printed and dropped, and never reaches the transfer that called it.
"""

import os
import sqlite3
import time

import defaults as config
import runtime

SENT = "sent"
RECEIVED = "received"

KIND_FILE = "file"
KIND_ALBUM = "album"
KIND_LIST = "list"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transfers (
    id         INTEGER PRIMARY KEY,
    direction  TEXT    NOT NULL,
    kind       TEXT    NOT NULL,
    ended_at   INTEGER NOT NULL,
    item_key   TEXT,
    name       TEXT,
    size       INTEGER NOT NULL,
    bytes      INTEGER NOT NULL,
    seconds    REAL,
    speed      INTEGER,
    waited     REAL
);
CREATE INDEX IF NOT EXISTS transfers_by_direction_and_time ON transfers (direction, ended_at);
CREATE INDEX IF NOT EXISTS transfers_by_item ON transfers (direction, kind, item_key);
"""


def _path():
    return str(getattr(config, "TRANSFER_LOG_FILE", "") or "").strip()


def _connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.executescript(_SCHEMA)
    return conn


def _record(row):
    path = _path()
    if not path:
        return False
    try:
        with runtime.transfer_log_lock:
            conn = _connect(path)
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO transfers (direction, kind, ended_at, item_key, name,"
                        " size, bytes, seconds, speed, waited) VALUES (?,?,?,?,?,?,?,?,?,?)", row)
            finally:
                conn.close()
        return True
    except Exception as err:
        print(f"[TRANSFER-LOG ERROR] Could not record the transfer: {err}")
        return False


def _whole(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def record_sent(kind, item_key, name, size, wire_bytes, seconds, speed, waited):
    """One completed send.

    `speed` is None when the transfer was too small to measure (see
    stats_mgr.speed_is_measurable), `waited` None when the queue row carried no
    time of its own (a row saved before this existed). A list is stored without
    a name, since its name carries the build date and nothing is ranked by it.
    """
    if kind == KIND_LIST:
        item_key = name = None
    return _record((SENT, kind, int(time.time()), item_key, name, _whole(size),
                    _whole(wire_bytes), _positive(seconds),
                    None if speed is None else _whole(speed),
                    None if waited is None else max(0.0, float(waited))))


def record_received(kind, size):
    """One completed download from another bot: how big, and nothing else.

    No name, so nothing about what another bot shares is kept either.
    """
    return _record((RECEIVED, kind, int(time.time()), None, None, _whole(size),
                    _whole(size), None, None, None))


def _query(sql, args=()):
    path = _path()
    if not path or not os.path.exists(path):
        return []
    with runtime.transfer_log_lock:
        conn = _connect(path)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()


def _one(width, sql, args=()):
    rows = _query(sql, args)
    return rows[0] if rows else (None,) * width


def top_files(limit=10, since=None):
    """The most-sent files as [(name, times sent)], most first, ties by name.

    Lists are not files and never appear here; an album (a packed folder)
    counts as one item, the way the Most downloaded table counts it.
    """
    rows = _query(
        "SELECT MAX(name), COUNT(*) AS n FROM transfers"
        " WHERE direction = ? AND kind != ? AND item_key IS NOT NULL AND ended_at >= ?"
        " GROUP BY item_key ORDER BY n DESC, MAX(name) LIMIT ?",
        (SENT, KIND_LIST, since or 0, max(0, int(limit))))
    return [(name, count) for name, count in rows]


def summary(since=None):
    """The figures the record exists for, over everything or since a Unix time.

    files_sent leaves the file lists out and lists_sent counts them; the speed
    figures come from the sends that were large enough to measure, the average
    being bytes over seconds rather than a mean of means; queue_wait_seconds is
    the mean time from being asked for to the send starting, over the rows that
    know it.
    """
    since = since or 0
    sent = _one(3,
        "SELECT SUM(kind != ?), SUM(kind = ?), SUM(CASE WHEN kind != ? THEN bytes ELSE 0 END)"
        " FROM transfers WHERE direction = ? AND ended_at >= ?",
        (KIND_LIST, KIND_LIST, KIND_LIST, SENT, since))
    speed = _one(3,
        "SELECT MAX(speed), SUM(bytes), SUM(seconds) FROM transfers"
        " WHERE direction = ? AND speed IS NOT NULL AND ended_at >= ?",
        (SENT, since))
    waited = _one(1,
        "SELECT AVG(waited) FROM transfers WHERE direction = ? AND waited IS NOT NULL"
        " AND ended_at >= ?", (SENT, since))
    received = _one(2,
        "SELECT SUM(kind != ?), SUM(CASE WHEN kind != ? THEN size ELSE 0 END)"
        " FROM transfers WHERE direction = ? AND ended_at >= ?",
        (KIND_LIST, KIND_LIST, RECEIVED, since))
    seconds = speed[2] or 0
    return {
        "files_sent": int(sent[0] or 0),
        "lists_sent": int(sent[1] or 0),
        "bytes_sent": int(sent[2] or 0),
        "top_speed": int(speed[0] or 0),
        "average_speed": int((speed[1] or 0) / seconds) if seconds > 0 else 0,
        "queue_wait_seconds": float(waited[0]) if waited[0] is not None else None,
        "files_received": int(received[0] or 0),
        "bytes_received": int(received[1] or 0),
    }
