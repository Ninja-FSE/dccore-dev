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
        conn = None
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
        # THE HANDLE IS CLOSED BEFORE IT IS DROPPED. sqlite3.connect() is
        # lazy - it succeeds on a corrupt file, on a text file, on anything
        # openable - and the failure lands on the first execute() below, with
        # a real open handle already in hand. Returning None without closing
        # it leaked one connection PER CALL, and this function is called on
        # every search: an operator whose index file is damaged leaks one for
        # every keystroke in the filter bar. On Windows the file also stays
        # locked until the object is collected, so the next attempt fails for
        # a new reason and the log stops describing the original one.
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
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

    `rows` are the dicts list.entries_to_filelist_rows() produces, whose
    filename key is "title" - NOT "filename". This read "filename" at first,
    which is not a key that function has ever produced, so every row went into
    the index with an empty name and the whole cross-list filter matched
    nothing in production. The tests missed it by building their own rows with
    a "filename" key instead of calling the producer.

    Returns the number indexed, or 0 if the index is unavailable.
    """
    # NORMALISED, and the same normalisation on both sides of the delete.
    #
    # This stored whatever the operator typed and deleted with SQLite's binary
    # `=`, while every reader case-folds: fetched_bot_lists is keyed
    # lower-case, indexed_bots() lowers, and FTS5 MATCH folds. So fetching
    # from "Dude" and then re-fetching from "DUDE" - the ordinary case, since
    # the sidebar prefills the nick and a refetch is retyped by hand - left
    # BOTH copies in the index. The stale one answered searches beside the new
    # one, indexed_bots() reported a single bot, and nothing could ever free
    # it: at the sizes this project measures, ~80MB per orphan.
    name = str(bot or "").strip().lower()
    if not name:
        return 0

    with _conn_lock:
        conn = _connect()
        if conn is None:
            return 0
        try:
            # THE LOCK IS HELD FOR THE WHOLE WRITE, deliberately. It is the
            # transaction boundary as well as the connection guard: releasing
            # it between the delete and the insert would let a search on
            # Flask's thread read a list that had been emptied and not yet
            # refilled, and report that bot as holding no match - the same
            # false "empty" bots_with_a_match() takes care to avoid. A fetch
            # completing is rare and a search is cheap; blocking one for the
            # length of one write is the right side of that trade.
            #
            # Delete first, in the same transaction as the insert: a refetch
            # that replaced a list must not leave the old rows searchable
            # beside the new ones, and a crash between the two must not leave
            # the bot indexed twice.
            conn.execute("DELETE FROM entries WHERE bot = ?", (name,))
            conn.executemany(
                "INSERT INTO entries (bot, filename, folder, size) "
                "VALUES (?, ?, ?, ?)",
                # "title" is the key entries_to_filelist_rows() writes;
                # "filename" is accepted too because find_matching_entries()
                # uses that name and a future caller may reasonably pass its
                # rows straight through.
                # A GENERATOR, not a list: executemany consumes it a row at
                # a time, where a comprehension built a second complete copy
                # of the list in memory first - at the four million rows this
                # index is measured against, hundreds of megabytes of tuples
                # held for the length of the write and for no reason.
                ((name,
                  str(row.get("title") or row.get("filename") or ""),
                  str(row.get("folder") or ""),
                  str(row.get("size") or ""))
                 for row in rows))
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
    name = str(bot or "").strip().lower()
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
    # A bot whose own query FAILED is neither matched nor empty. Falling out
    # of `matched` used to put it straight into `empty` below, and `empty` is
    # rendered as a positive statement - the list greys out and the status
    # line counts it as holding no match. That is the same false claim the
    # "no index" branch two lines down already refuses to make, arrived at by
    # a different route: one sqlite error and a list that DOES match is shown
    # to the operator as one that does not.
    unknown = set()
    with _conn_lock:
        conn = _connect()
        if conn is None:
            # No index is not "no bot matches": claiming every list is empty
            # would grey out the whole sidebar and read as a broken filter.
            return set(), set()
        for bot in candidates:
            # The MATCH is a cheap PRE-FILTER, not the answer. `bot:"name"` is
            # an FTS5 PHRASE over a tokenised column, not equality: "Bot"
            # matches "Bot-2", "Bot_away" and "Bot|gone", because unicode61
            # splits on the punctuation. Holding both Bot and Bot-2 with only
            # Bot-2 matching, the sidebar left Bot undimmed and the status
            # line said "1 match in 2 lists". Every fixture in the tests was
            # token-disjoint, which is why they passed.
            #
            # So the row's own bot column is compared for equality before the
            # bot counts as matched. LIMIT stays small rather than 1: a
            # near-miss neighbour can occupy the first row.
            wanted = bot.strip().lower()
            try:
                rows = conn.execute(
                    "SELECT bot FROM entries WHERE entries MATCH ? LIMIT 25",
                    (f"bot:{_quote(wanted)} AND {query}",)).fetchall()
            except Exception as err:
                print(f"[LIST-INDEX] Could not check {bot!r} against the "
                      f"filter ({err}); its list is left unmarked rather "
                      f"than shown as empty.")
                unknown.add(wanted)
                continue
            if any(str(r[0]).strip().lower() == wanted for r in rows):
                matched.add(wanted)
    empty = {b.lower() for b in candidates} - matched - unknown
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
        # Same pre-filter, same reason - the equality check is below, on the
        # rows that come back, because bot:"Bot" also matches "Bot-2".
        held_keys = {b.strip().lower() for b in held}
        query = ("(" + " OR ".join(f"bot:{_quote(b)}" for b in sorted(held_keys))
                 + ") AND " + query)

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

    rows = [{"bot": row[0], "filename": row[1], "folder": row[2],
             "size": row[3]} for row in found]
    if bots is not None:
        # The MATCH above is a phrase filter; this is the equality. Without
        # it, holding "Bot" would return "Bot-2"'s files and offer a download
        # from a list we do not have.
        rows = [row for row in rows
                if str(row["bot"]).strip().lower() in held_keys]
    return rows


def backfill_missing(held, log=print):
    """Index any held list that is not in the index yet. Returns how many.

    THE UPGRADE CASE, and it is the ordinary one. `index_bot_list()` has
    exactly one caller in the daemon - a fetch completing - while held lists
    survive restarts, because `list_fetch` persists them and `oserve` restores
    them at startup. So an operator who upgrades with lists already fetched
    has a full `fetched_bot_lists` and an empty index.

    That is worse than it sounds, because the "we cannot tell" guard does not
    cover it: `_connect()` creates the database on demand, so the connection
    is NOT None, every per-bot query simply misses, and the page states
    positively that no list holds a match. The filter reads as working and
    answers wrongly, for lists that are full of matches, until each is
    re-fetched by hand.

    `held` is `config.fetched_bot_lists`. Each entry carries the `list_path`
    the fetch stored, and re-parsing it here is the same one-pass parse
    `list_fetch` does - not a second implementation of it.

    Best-effort per bot: one unreadable list costs its own row in the filter
    and nothing else, which is the posture the whole module already takes.
    """
    import list as list_mod
    import platform_compat

    already = indexed_bots()
    done = 0
    for key, entry in (held or {}).items():
        if not isinstance(entry, dict):
            continue
        bot = str(entry.get("bot") or key).strip()
        if not bot or bot.lower() in already:
            continue
        path = entry.get("list_path")
        if not path or not os.path.exists(platform_compat.long_path(path)):
            continue
        try:
            entries, _total = list_mod.find_matching_entries(
                [], limit=None, list_path=platform_compat.long_path(path))
            rows = list_mod.entries_to_filelist_rows(entries, bot)
        except Exception as err:
            log(f"[LIST-INDEX] Could not re-read {bot}'s list to index it "
                f"({err}); the filter will not see it until the next fetch.")
            continue
        if index_bot_list(bot, rows):
            done += 1
    if done:
        log(f"[LIST-INDEX] Indexed {done} list(s) held from before the search "
            f"index existed.")
    return done


def reset_for_tests():
    """Close the connection and forget the path. Tests only."""
    close()
