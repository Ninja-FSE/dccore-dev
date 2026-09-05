# list_index.py - the cross-list search index for the dashboard's List Browser
"""One SQLite database holding every fetched bot list, searchable at once.

WHY THIS EXISTS AT ALL

The List Browser's filter bar searches every list you hold in one go. Doing
that by re-reading the files is arithmetically out of reach, not merely slow:
`list_fetch` re-parses a list fresh on every call - deliberately, since #76
removed unbounded retention - and #133 measured one 719k-file list at 2.0s and
ten held lists at about 11s. No amount of debouncing turns eleven seconds into
typing.

WHY FTS5 AND NOT A PLAIN TABLE

#133 proposed an ordinary indexed table and called it "milliseconds across
millions of rows". That is true of ONE of the two queries this feature needs
and false of the other, which is worth writing down because the false half is
the headline behaviour.

Measured here against 4,000,000 rows in ten lists, the sizes #133's own
channel capture recorded:

    plain table, LIKE '%term%'   page  1-41ms     which-bots  1150-1400ms
    FTS5, per-bot LIMIT 1        page   1- 4ms    which-bots     2-  4ms

Paging is fast either way because LIMIT stops the scan early. "Which bots have
no match", the question that greys out the sidebar, has no such escape: it must
prove a negative for every bot, which is a full scan per keystroke.

FTS5 with `bot` as an INDEXED column is what fixes it. The existence question
is then asked once per bot as `bot:"<name>" AND (terms)` with LIMIT 1, and each
one stops at its first hit instead of enumerating every match.

WHAT THIS CHANGES ABOUT MATCHING

FTS5 tokenises; `find_matching_entries()` does substring. So "andma" no longer
finds "Sandman" - a mid-word fragment is not a token. Whole words and prefixes
both work, and the filter bar appends a prefix wildcard to the last word so
typing behaves the way a search box is expected to.

That is a real difference in behaviour and not merely an implementation
detail. It buys the feature at all: substring matching over four million rows
is the 1.4s query above. `@find` over our own list is untouched and still
substring - this index is only the dashboard's cross-list filter.

FAILURE POSTURE

Every function here is best-effort. A missing, locked, corrupt or
old-schema database costs the filter bar and nothing else: the lists
themselves are on disk, the browser still pages them, and the index rebuilds
the next time a list is fetched. Nothing in the daemon's serving path reads
this file, so it is never a reason to refuse to start or to fail a fetch.
"""

import os
import sqlite3
import threading

import defaults as config

# Bound from runtime for the reason db.py's own lock is: !rehash re-executes
# this module, and a fresh Lock() on every reload would let two callers both
# believe they hold it. sqlite3 serialises writers itself, but the connection
# cache below is ordinary Python state and needs its own guard.
import runtime

_conn_lock = getattr(runtime, "list_index_lock", None)
if _conn_lock is None:  # pragma: no cover - runtime always defines it
    _conn_lock = threading.Lock()

INDEX_FILE = getattr(config, "LIST_INDEX_FILE",
                     os.path.join("data", "list_index.db"))

# The page the route hands back. Deliberately small: the filter bar shows the
# first screenful and the operator narrows the term, which is faster for them
# than paging thousands of rows and far cheaper here.
DEFAULT_SEARCH_LIMIT = 200
MAX_SEARCH_LIMIT = 2000

_SCHEMA_VERSION = 1

# One connection, reused. Opening a database per keystroke would be the
# cheapest thing here to get wrong.
_connection = None
_connection_path = None


def _index_path():
    """The configured path, resolved fresh each call.

    Read through config every time rather than captured at import: the tests
    redirect it per case, and !rehash can move it.
    """
    return getattr(config, "LIST_INDEX_FILE", INDEX_FILE)


def _connect():
    """The shared connection, opening and creating the schema if needed.

    Returns None when the database cannot be opened at all - a read-only
    directory, a disk that is full, sqlite3 built without FTS5. The caller
    treats that as "no index", which costs the filter bar and nothing else.
    """
    global _connection, _connection_path

    path = _index_path()
    if _connection is not None and _connection_path == path:
        return _connection

    _close_locked()
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        # check_same_thread=False: the dashboard answers on Flask's threads
        # while a fetch completing writes from the transfer thread, and every
        # caller here holds _conn_lock for the whole operation anyway.
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS entries USING fts5("
            # bot is INDEXED - that is the whole point. It lets the existence
            # question below be asked per bot and stop at the first hit,
            # instead of enumerating every match to find out who is missing.
            "bot, filename, folder UNINDEXED, size UNINDEXED, "
            "tokenize='unicode61')")
        conn.execute("CREATE TABLE IF NOT EXISTS meta "
                     "(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)",
                     (str(_SCHEMA_VERSION),))
        conn.commit()
    except Exception as err:
        print(f"[LIST-INDEX] Unavailable ({err}); the cross-list filter is "
              f"off until the next fetch. Browsing and @find are unaffected.")
        _connection = None
        _connection_path = None
        return None

    _connection = conn
    _connection_path = path
    return conn


def _close_locked():
    global _connection, _connection_path
    if _connection is not None:
        try:
            _connection.close()
        except Exception:
            pass
    _connection = None
    _connection_path = None


def close():
    """Drop the cached connection. For tests, and for a rehash moving the file."""
    with _conn_lock:
        _close_locked()


def _quote(text):
    """One FTS5 string literal. Doubling the quote is the escape it defines."""
    return '"' + str(text).replace('"', '""') + '"'


def build_match_query(terms, prefix_last=True):
    """The FTS5 MATCH expression for a filter-bar query, or None for nothing.

    Every term must be present - the same AND-across-words rule
    find_matching_entries() applies - and the last one gets a prefix wildcard
    so that typing shows results before the word is finished. That is what
    makes it a filter bar rather than a search button.

    Terms are quoted, never interpolated: a query is whatever somebody typed
    into a box, and FTS5's expression syntax has plenty of operators in it.
    An unquoted `-` or `*` or `NEAR` would be read as syntax, and at best
    answers the wrong question.
    """
    cleaned = [str(t).strip().lower() for t in (terms or [])]
    cleaned = [t for t in cleaned if t]
    if not cleaned:
        return None

    parts = []
    for i, term in enumerate(cleaned):
        last = (i == len(cleaned) - 1)
        if prefix_last and last and len(term) >= 2:
            # The wildcard sits OUTSIDE the quotes: "meta"* is FTS5's prefix
            # form. Inside them it would be a literal asterisk to match.
            parts.append(_quote(term) + "*")
        else:
            parts.append(_quote(term))
    return "filename:(" + " AND ".join(parts) + ")"


def index_bot_list(bot, rows):
    """Replace everything indexed for `bot` with `rows`.

    Called from the parse list_fetch already performs at fetch time - that
    parse walked the whole file only to count it and threw the rows away, so
    the index costs one pass that was already happening.

    `rows` are the dicts entries_to_filelist_rows() produces. Returns the
    number indexed, or 0 if the index is unavailable.
    """
    name = str(bot or "").strip()
    if not name:
        return 0

    with _conn_lock:
        conn = _connect()
        if conn is None:
            return 0
        try:
            # Delete first, in the same transaction as the insert: a refetch
            # that replaced a list must not leave the old rows searchable
            # beside the new ones, and a crash between the two must not leave
            # the bot indexed twice.
            conn.execute("DELETE FROM entries WHERE bot = ?", (name,))
            conn.executemany(
                "INSERT INTO entries (bot, filename, folder, size) "
                "VALUES (?, ?, ?, ?)",
                [(name,
                  str(row.get("filename") or ""),
                  str(row.get("folder") or ""),
                  str(row.get("size") or ""))
                 for row in rows])
            conn.commit()
            return len(rows)
        except Exception as err:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[LIST-INDEX] Could not index {name}'s list ({err}); the "
                  f"cross-list filter will not see it. Browsing it still works.")
            return 0


def drop_bot(bot):
    """Forget one bot's list. Called when its fetched list is deleted."""
    name = str(bot or "").strip()
    if not name:
        return False
    with _conn_lock:
        conn = _connect()
        if conn is None:
            return False
        try:
            conn.execute("DELETE FROM entries WHERE bot = ?", (name,))
            conn.commit()
            return True
        except Exception as err:
            print(f"[LIST-INDEX] Could not drop {name} from the index: {err}")
            return False


def indexed_bots():
    """Every bot with rows in the index, lower-cased for comparison."""
    with _conn_lock:
        conn = _connect()
        if conn is None:
            return set()
        try:
            return {str(row[0]).strip().lower()
                    for row in conn.execute("SELECT DISTINCT bot FROM entries")}
        except Exception:
            return set()


def bots_with_a_match(terms, bots):
    """Which of `bots` have at least one row matching, and which have none.

    Returns (matched, empty), both lower-cased sets.

    Asked once per bot with LIMIT 1 rather than as one DISTINCT over every
    match. That is the difference between 2ms and 1.4s at four million rows:
    a bot WITH a match stops at its first row, and a bot without one is the
    only case that pays for a scan of its own rows alone.
    """
    query = build_match_query(terms)
    candidates = [str(b).strip() for b in (bots or []) if str(b).strip()]
    if query is None or not candidates:
        return set(), set()

    matched = set()
    with _conn_lock:
        conn = _connect()
        if conn is None:
            # No index is not "no bot matches": claiming every list is empty
            # would grey out the whole sidebar and read as a broken filter.
            return set(), set()
        for bot in candidates:
            try:
                row = conn.execute(
                    "SELECT 1 FROM entries WHERE entries MATCH ? LIMIT 1",
                    (f"bot:{_quote(bot)} AND {query}",)).fetchone()
            except Exception:
                continue
            if row:
                matched.add(bot.lower())
    empty = {b.lower() for b in candidates} - matched
    return matched, empty


def search(terms, limit=None, bots=None):
    """A page of matches across every indexed list.

    Returns a list of {"bot", "filename", "folder", "size"}. Capped: the
    filter bar shows a screenful and the operator narrows the term, which is
    both faster for them and far cheaper here than paging a million rows.

    `bots` restricts the answer to lists we currently hold, and the caller
    always passes it. The index is not the record of what is held -
    `fetched_bot_lists` is - and the two can drift: a list file removed by
    hand, a reset store, a bot whose entry went away while its rows stayed.
    Returning a row for a list we no longer have would offer a file that
    cannot be requested, which is a worse answer than not finding it.
    """
    query = build_match_query(terms)
    if query is None:
        return []

    if bots is not None:
        held = [str(b).strip() for b in bots if str(b).strip()]
        if not held:
            return []
        query = ("(" + " OR ".join(f"bot:{_quote(b)}" for b in held) + ")"
                 " AND " + query)

    if limit is None:
        limit = DEFAULT_SEARCH_LIMIT
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_SEARCH_LIMIT
    # Non-positive means "omitted", not "give me nothing" - and not "give me
    # one" either, which is what max(1, ...) quietly did. The same rule
    # webserver.parse_pagination_params() already states for the paging
    # routes, so the two boundaries answer a bad limit the same way.
    if limit <= 0:
        limit = DEFAULT_SEARCH_LIMIT
    limit = min(limit, MAX_SEARCH_LIMIT)

    with _conn_lock:
        conn = _connect()
        if conn is None:
            return []
        try:
            found = conn.execute(
                "SELECT bot, filename, folder, size FROM entries "
                "WHERE entries MATCH ? LIMIT ?", (query, limit)).fetchall()
        except Exception as err:
            print(f"[LIST-INDEX] Search failed ({err}); returning nothing "
                  f"rather than a partial answer.")
            return []

    return [{"bot": row[0], "filename": row[1], "folder": row[2],
             "size": row[3]} for row in found]


def reset_for_tests():
    """Close the connection and forget the path. Tests only."""
    close()
