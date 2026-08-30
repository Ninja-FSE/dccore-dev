# db.py - Central state storage for DCCore
import io
import json
import os
import time
import datetime
import tempfile
import threading
import config

# Every on-disk file this module owns is small and rewritten in full, so a single
# lock serialising the writes is enough. It is deliberately NOT dcc.queue_lock:
# dcc.py calls save_dcc_queue() while already holding queue_lock, and threading.Lock
# is not reentrant, so reusing it would deadlock on the first save.
_disk_lock = threading.Lock()

# save and load previously used two different literals ("data/..." vs "./data/...").
# One constant now, built with os.path.join so it is correct on Windows too.
DCC_QUEUE_FILE = getattr(config, "DCC_QUEUE_FILE", os.path.join("data", "dcc_queue.txt"))
SPEED_RECORD_FILE = getattr(config, "SPEED_RECORD_FILE", os.path.join("data", "speed_record.txt"))
KNOWN_BOTS_FILE = getattr(config, "KNOWN_BOTS_FILE", os.path.join("data", "known_bots.json"))
FETCHED_BOT_LISTS_FILE = getattr(config, "FETCHED_BOT_LISTS_FILE",
                                 os.path.join("data", "fetched_bot_lists.json"))
FETCH_HISTORY_FILE = getattr(config, "FETCH_HISTORY_FILE",
                              os.path.join("data", "fetch_history.json"))


def _atomic_write(path, text):
    """Write `text` to `path` atomically.

    Writes to a temporary file in the SAME directory (so the final step is a rename
    within one filesystem), flushes and fsyncs it, then swaps it into place.

    os.replace() is used rather than os.rename(): on Windows os.rename() raises
    FileExistsError when the destination already exists, while os.replace()
    overwrites atomically on both Windows and POSIX.

    A reader therefore always sees either the complete previous file or the complete
    new one - never a half-written file, and never an empty one.
    """
    path = os.path.abspath(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".swap")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

# =================================================================----
# SECTION 1: BANS.TXT (banned users)
# =================================================================----

def load_bans_from_file():
    """Load the active bans from bans.txt into memory."""
    if not os.path.exists(config.BANS_FILE):
        return
    try:
        with open(config.BANS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if " " in line:
                    user_key, expire_ts = line.split(" ", 1)
                    config.banned_users[user_key.lower()] = float(expire_ts)
        print(f"[DB] Loaded bans from {config.BANS_FILE}")
    except Exception as e:
        print(f"[DB ERROR] Could not read {config.BANS_FILE}: {e}")

def save_bans_to_file():
    """Write the active bans from memory to bans.txt, atomically."""
    try:
        with _disk_lock:
            snapshot = dict(config.banned_users)
            body = "".join(f"{user_key} {expire_ts}\n" for user_key, expire_ts in snapshot.items())
            _atomic_write(config.BANS_FILE, body)
    except Exception as e:
        print(f"[DB ERROR] Could not save to {config.BANS_FILE}: {e}")


# ---------------------------------------------------------------------------
# hard_bans.txt - permanent wildcard patterns, edited live by !ban and !unban.
#
# security.check_user_status reads this file itself on every command. That hot
# path is deliberately untouched; what follows exists because the two command
# handlers have to READ-MODIFY-WRITE it, and doing that by hand went wrong in
# three separate ways:
#
#   * !unban truncated the file with open(..., "w") and wrote the kept lines
#     back one at a time. A crash, a full disk or a kill in between leaves it
#     short or empty and the permanent bans are gone. os.replace() already
#     fixed exactly this for bans.txt, dcc_queue.txt and the rest -
#     hard_bans.txt was the last file still written in place.
#
#   * That failure mode is fail-OPEN. check_user_status only distrusts a "no
#     match" when the read RAISED; a file that is readable but truncated looks
#     identical to a file with no bans in it, so every hard-banned user is let
#     through for as long as the window lasts.
#
#   * !ban appended with f.write(f"{pattern}\n") without checking the previous
#     line ended in a newline. On a hand-edited file with no trailing newline
#     that glues two patterns into one, silently unbanning both.
#
# Both operations run in their own daemon thread, so the whole read-modify-write
# is done under a single _disk_lock acquisition: two of them interleaving would
# otherwise drop whichever entry lost the race.
# ---------------------------------------------------------------------------

def _hard_bans_path():
    return getattr(config, "HARD_BANS_FILE", os.path.join("data", "hard_bans.txt"))


def _read_hard_bans_unlocked(path):
    """Patterns from `path`, lowercased, in file order, deduplicated.

    Blank lines and #-comments are dropped. Comments are NOT preserved across a
    rewrite; security.py ignores them, and keeping them would mean tracking
    their position relative to entries that come and go.
    """
    if not os.path.exists(path):
        return []
    patterns = []
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            pattern = line.strip().lower()
            if pattern and not pattern.startswith("#") and pattern not in patterns:
                patterns.append(pattern)
    return patterns


def load_hard_bans():
    """Every permanent wildcard pattern currently on disk."""
    try:
        with _disk_lock:
            return _read_hard_bans_unlocked(_hard_bans_path())
    except Exception as e:
        print(f"[DB ERROR] Could not read {_hard_bans_path()}: {e}")
        raise


def add_hard_ban(pattern):
    """Add one pattern. Returns True if it was added, False if already present."""
    pattern = str(pattern).strip().lower()
    if not pattern:
        return False
    path = _hard_bans_path()
    with _disk_lock:
        patterns = _read_hard_bans_unlocked(path)
        if pattern in patterns:
            return False
        patterns.append(pattern)
        _atomic_write(path, "".join(f"{p}\n" for p in patterns))
    return True


def remove_hard_ban(pattern):
    """Remove one pattern. Returns True if it was removed, False if not found."""
    pattern = str(pattern).strip().lower()
    if not pattern:
        return False
    path = _hard_bans_path()
    with _disk_lock:
        patterns = _read_hard_bans_unlocked(path)
        if pattern not in patterns:
            return False
        _atomic_write(path, "".join(f"{p}\n" for p in patterns if p != pattern))
    return True


# =================================================================----
# SEKTION 2: STATS.TXT (Avancerad OmenServe-statistik)
# =================================================================----

# ---------------------------------------------------------------------------
# stats.txt - lifetime and per-day transfer counters.
#
# Same read-modify-write hazard as hard_bans.txt above, and it matters more
# here: these counters are only ever derived from their own previous value, so
# nothing recomputes them and a lost update is permanent.
#
# Up to MAX_DCC_SLOTS transfers finish concurrently, each in its own thread,
# and check_and_rotate_day() runs from the IRC read loop on every channel
# message. Holding _disk_lock across only the WRITE - which is all
# save_advanced_stats used to do - leaves the load-modify-save pair
# unsynchronised: two completions that overlap both read the same row, and
# whichever writes second discards the other's increment. A 300MB and a 7MB
# transfer finishing together added one file and 300MB instead of two files
# and 307MB.
#
# The midnight case is worse than a miscount: a transfer thread that loaded
# before check_and_rotate_day() rotated, and saves after it, writes the OLD
# date back along with un-rotated counters, so the next rotation runs a second
# time and yesterday's totals collapse to that one transfer.
#
# So every public entry point below takes _disk_lock exactly once and does the
# whole sequence under it. The _unlocked helpers exist because threading.Lock
# is not reentrant - update_stats_on_complete() rotates and saves while it is
# already holding the lock.
# ---------------------------------------------------------------------------

def _load_advanced_stats_unlocked():
    """Parse stats.txt. Caller must hold _disk_lock."""
    STATS_FILE = config.STATS_FILE
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    default_stats = [0, 0, 0, 0, 0, 0, today_str]

    if not os.path.exists(STATS_FILE):
        return default_stats
    try:
        with open(STATS_FILE, "r") as f:
            parts = f.read().strip().split()
            if len(parts) == 7:
                return [int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5]), parts[6]]
            elif len(parts) == 2:
                return [int(parts[0]), int(parts[1]), 0, 0, 0, 0, today_str]
    except Exception as e:
        print(f"[DB ERROR] Could not parse stats.txt, using defaults: {e}")
    return default_stats


def _save_advanced_stats_unlocked(stats):
    """Write the 7-column row atomically. Caller must hold _disk_lock."""
    row = f"{stats[0]} {stats[1]} {stats[2]} {stats[3]} {stats[4]} {stats[5]} {stats[6]}"
    _atomic_write(config.STATS_FILE, row)


def _rotate_day_unlocked(stats):
    """Move Today to Yesterday if the date changed. Returns True if it did.

    Mutates `stats` in place and does NOT write - the caller decides when to
    save, so a rotation and an increment become one write instead of two.

    Deliberately does NOT log: the caller prints after releasing _disk_lock.
    Logging from in here would put console I/O inside the critical section, and
    worse, a print() that raises would abandon a rotation the caller had already
    decided to make. That is not theoretical - the Swedish log strings raise
    UnicodeEncodeError on any console whose code page cannot encode them, which
    is exactly how this was found.
    """
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    if stats[6] == today_str:
        return False
    stats[2] = stats[4]   # Yesterday files = Today files
    stats[3] = stats[5]   # Yesterday bytes = Today bytes
    stats[4] = 0
    stats[5] = 0
    stats[6] = today_str
    return True


def _coerce_file_size(file_size):
    """Best-effort integer byte count from whatever the caller passed.

    Kept verbatim from the original update_stats_on_complete: callers have been
    seen passing a list, a dict, and a decimal string. Runs OUTSIDE the lock -
    parsing does not need it.
    """
    try:
        if isinstance(file_size, list):
            file_size = file_size[0] if len(file_size) > 0 else 0
        if isinstance(file_size, dict):
            file_size = file_size.get('bytes', file_size.get('size', 0))
        return int(float(str(file_size).strip()))
    except Exception as type_err:
        print(f"[DB WARNING] Could not parse file size '{file_size}', falling back to 0: {type_err}")
        return 0


def load_advanced_stats():
    """Read stats.txt. Format: total_files total_bytes yest_files yest_bytes today_files today_bytes last_date"""
    with _disk_lock:
        return _load_advanced_stats_unlocked()


def save_advanced_stats(stats):
    """Write the 7-column row to stats.txt, atomically.

    An earlier version truncated the live file and then wrote into it, so a crash
    or a concurrent writer could leave a short row behind - which load_advanced_stats
    silently discards, resetting every counter to zero.
    """
    try:
        with _disk_lock:
            _save_advanced_stats_unlocked(stats)
    except Exception as e:
        print(f"[DB ERROR] Could not save to stats.txt: {e}")


def load_advanced_stats_rolled():
    """The stats row as it stands TODAY, without writing anything.

    check_and_rotate_day() is the writer: it rotates and saves, and the daemon
    calls it when a transfer completes. This is the reader's version, for a
    status display that must not write to disk to answer a GET.

    It matters because the daemon only rotates when something finishes. A bot
    that has sent nothing since midnight still has yesterday's figures sitting
    in the Today columns, and a dashboard reading the row raw would label them
    Today - wrong, and wrong in the direction that flatters the bot.

    Rolling through _rotate_day_unlocked() keeps one definition of what "a new
    day" means. Duplicating that comparison here is the second-list problem
    this codebase keeps getting bitten by.

    Mutating the row in place is safe because _load_advanced_stats_unlocked()
    builds a fresh list on every call - there is no shared row to corrupt, and
    a defensive copy here would be guarding nothing.
    """
    with _disk_lock:
        stats = _load_advanced_stats_unlocked()
    _rotate_day_unlocked(stats)
    return stats


def check_and_rotate_day():
    """Roll Today into Yesterday at midnight. Returns the current row.

    No try/except here on purpose. An earlier draft caught everything and
    returned a freshly loaded row on failure, which meant a logging error could
    make this hand back UN-ROTATED counters that the caller would treat as
    current - silently wrong data instead of a loud failure. The original had no
    handler either; this keeps that contract.
    """
    with _disk_lock:
        stats = _load_advanced_stats_unlocked()
        rotated = _rotate_day_unlocked(stats)
        if rotated:
            _save_advanced_stats_unlocked(stats)
    if rotated:
        print(f"[DB ROTATE] New day detected ({stats[6]}). Moving statistics to yesterday.")
    return stats


def update_stats_on_complete(file_size):
    """Count one completed transfer into the Total and Today columns.

    The whole rotate-load-increment-save sequence happens under ONE _disk_lock
    acquisition, which is the entire point of this function: dcc.py used to do
    it by hand with load and save as separate locked calls, and concurrent
    completions silently overwrote each other's increments.
    """
    clean_size = _coerce_file_size(file_size)
    with _disk_lock:
        stats = _load_advanced_stats_unlocked()
        rotated = _rotate_day_unlocked(stats)
        stats[0] += 1           # Total files
        stats[1] += clean_size  # Total bytes
        stats[4] += 1           # Today files
        stats[5] += clean_size  # Today bytes
        _save_advanced_stats_unlocked(stats)
    if rotated:
        print(f"[DB ROTATE] New day detected ({stats[6]}). Moving statistics to yesterday.")
    return stats


def get_speed_record():
    """Read the saved speed record, in bytes/s, from disk.

    FIXED (issue #34): previously read a hardcoded "./data/speed_record.txt" literal
    while save_speed_record() already wrote to the SPEED_RECORD_FILE constant -
    the same split-literal class of bug the DCC_QUEUE_FILE fix removed. Both resolved
    to the same file only by coincidence, when the daemon's cwd is the repo root.
    """
    if not os.path.exists(SPEED_RECORD_FILE):
        return 0
    try:
        with open(SPEED_RECORD_FILE, "r") as f:
            return int(f.read().strip())
    except:
        return 0

def save_speed_record(new_record):
    """Save a new speed record to disk, atomically."""
    try:
        with _disk_lock:
            _atomic_write(SPEED_RECORD_FILE, str(int(new_record)))
    except Exception as e:
        print(f"[DB ERROR] Could not save the speed record: {e}")


def load_known_bots():
    """The bot registry from disk, or {} if there is none yet.

    A registry that will not parse is not a reason to refuse to start - it is
    rebuilt from adverts within a few minutes of connecting, so a corrupt or
    hand-edited file costs an empty sidebar until then and nothing else. Same
    posture as load_advanced_stats(), which returns defaults rather than
    raising.
    """
    if not os.path.exists(KNOWN_BOTS_FILE):
        return {}
    try:
        with io.open(KNOWN_BOTS_FILE, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else {}
    except Exception as err:
        print(f"[DB ERROR] Could not read the bot registry, starting empty: {err}")
        return {}


def save_known_bots(registry):
    """Write the bot registry, atomically, through the same lock and the same
    temp-file-then-replace the other state files use."""
    try:
        with _disk_lock:
            _atomic_write(KNOWN_BOTS_FILE,
                          json.dumps(registry, indent=1, sort_keys=True, ensure_ascii=False))
    except Exception as err:
        print(f"[DB ERROR] Could not save the bot registry: {err}")


def load_fetched_bot_lists():
    """The fetched-bot-lists registry from disk, or {} if there is none yet.

    Same posture as load_known_bots(): a file that fails to parse costs an
    empty File Lists switcher until the next fetch, not a refusal to start.
    The entries this restores are references (bot/fetched_at/list_path/
    entry_count/source_zip), not parsed list content - the actual extracted
    files under FETCHED_FILES_DIR were never touched by a restart in the
    first place, only the daemon's memory of which bots they belong to.
    """
    if not os.path.exists(FETCHED_BOT_LISTS_FILE):
        return {}
    try:
        with io.open(FETCHED_BOT_LISTS_FILE, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else {}
    except Exception as err:
        print(f"[DB ERROR] Could not read the fetched-lists registry, starting empty: {err}")
        return {}


def save_fetched_bot_lists(registry):
    """Write the fetched-bot-lists registry, atomically. Called once per
    completed list fetch (list_fetch.py), not on a timer - unlike the bot
    registry above, nothing updates this often enough to need throttling."""
    try:
        with _disk_lock:
            _atomic_write(FETCHED_BOT_LISTS_FILE,
                          json.dumps(registry, indent=1, sort_keys=True, ensure_ascii=False))
    except Exception as err:
        print(f"[DB ERROR] Could not save the fetched-lists registry: {err}")


def load_fetch_history():
    """Every 'complete'/'failed' cross-bot fetch row from disk, or {} if
    there is none yet.

    Same posture as load_known_bots()/load_fetched_bot_lists(): a file that
    fails to parse costs an empty Downloads table until the next fetch
    finishes, not a refusal to start. The files these rows point at (via
    stored_filename) were never touched by a restart in the first place,
    only the daemon's memory of which fetch produced each one.
    """
    if not os.path.exists(FETCH_HISTORY_FILE):
        return {}
    try:
        with io.open(FETCH_HISTORY_FILE, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else {}
    except Exception as err:
        print(f"[DB ERROR] Could not read the fetch history, starting empty: {err}")
        return {}


def save_fetch_history(rows):
    """Write the finished-fetch history, atomically. Called from
    dcc_fetch.py's dispatcher tick (every 2s, skipped when nothing changed)
    and immediately on a dashboard delete, so a row disappears from disk
    right away rather than only up to one tick later."""
    try:
        with _disk_lock:
            _atomic_write(FETCH_HISTORY_FILE,
                          json.dumps(rows, indent=1, sort_keys=True, ensure_ascii=False))
    except Exception as err:
        print(f"[DB ERROR] Could not save the fetch history: {err}")


def save_dcc_queue():
    """Persist the DCC queue, dropping users whose list is now empty.

    Previously this truncated dcc_queue.txt and then serialised straight into the open
    handle. Any crash, disk-full or concurrent writer between those two steps left a
    truncated file - and load_dcc_queue() treats an unparseable file as "start empty",
    so the entire queue disappeared silently on the next boot.
    """
    import json

    try:
        # Drop users whose queue is now empty.
        for user_key in list(config.dcc_queue.keys()):
            if not config.dcc_queue[user_key]:
                del config.dcc_queue[user_key]

        with _disk_lock:
            # Serialise from a snapshot: another thread mutating config.dcc_queue during
            # json.dump would otherwise raise "dictionary changed size during iteration"
            # and abort the save.
            snapshot = {k: list(v) for k, v in config.dcc_queue.items()}
            _atomic_write(DCC_QUEUE_FILE, json.dumps(snapshot, indent=4))

        print("[DB-QUEUE] Queue structure saved and sanitised successfully.")
    except Exception as e:
        print(f"[DB-QUEUE ERROR] Could not save {DCC_QUEUE_FILE}: {e}")


def load_dcc_queue():
    """Load the persisted DCC queue at boot.

    An unreadable file still means starting empty - there is nothing else to do - but
    the damaged file is preserved rather than silently overwritten by the next save,
    so the queue can be recovered by hand instead of vanishing without trace.
    """
    import json

    file_path = DCC_QUEUE_FILE
    if not os.path.exists(file_path):
        # In place, not `= {}`: config.dcc_queue is the object runtime.py
        # holds, and rebinding it here would detach the two (see runtime.py).
        config.dcc_queue.clear()
        return
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError(f"expected a JSON object, got {type(loaded).__name__}")
        config.dcc_queue.clear()
        config.dcc_queue.update(loaded)
        total = sum(len(v) for v in loaded.values() if isinstance(v, list))
        print(f"[DB] Loaded {total} saved queue slot(s) for {len(loaded)} user(s) from disk.")
    except Exception as e:
        config.dcc_queue.clear()
        print(f"[DB ERROR] Could not read the saved DCC queue, starting empty: {e}")
        try:
            backup = file_path + ".corrupt"
            os.replace(file_path, backup)
            print(f"[DB ERROR] The damaged file was kept as {backup} for manual recovery.")
        except Exception as backup_err:
            print(f"[DB ERROR] Could not even back up the damaged file: {backup_err}")
