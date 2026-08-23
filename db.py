# db.py - Central databashantering för DCCore
import os
import time
import datetime
import tempfile
import threading
import config

# Every on-disk file this module owns is small and rewritten in full, so a single
# lock serialising the writes is enough. It is deliberately NOT config.queue_lock:
# dcc.py calls save_dcc_queue() while already holding queue_lock, and threading.Lock
# is not reentrant, so reusing it would deadlock on the first save.
_disk_lock = threading.Lock()

# save and load previously used two different literals ("data/..." vs "./data/...").
# One constant now, built with os.path.join so it is correct on Windows too.
DCC_QUEUE_FILE = getattr(config, "DCC_QUEUE_FILE", os.path.join("data", "dcc_queue.txt"))
SPEED_RECORD_FILE = getattr(config, "SPEED_RECORD_FILE", os.path.join("data", "speed_record.txt"))


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
# SEKTION 1: BANS.TXT (Bannlysta användare)
# =================================================================----

def load_bans_from_file():
    """Läser in aktiva bans från bans.txt till det globala minnet"""
    if not os.path.exists(config.BANS_FILE):
        return
    try:
        with open(config.BANS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if " " in line:
                    user_key, expire_ts = line.split(" ", 1)
                    config.banned_users[user_key.lower()] = float(expire_ts)
        print(f"[DB] Laddade in bans från {config.BANS_FILE}")
    except Exception as e:
        print(f"[DB ERROR] Kunde inte läsa {config.BANS_FILE}: {e}")

def save_bans_to_file():
    """Write the active bans from memory to bans.txt, atomically."""
    try:
        with _disk_lock:
            snapshot = dict(config.banned_users)
            body = "".join(f"{user_key} {expire_ts}\n" for user_key, expire_ts in snapshot.items())
            _atomic_write(config.BANS_FILE, body)
    except Exception as e:
        print(f"[DB ERROR] Kunde inte spara till {config.BANS_FILE}: {e}")


# =================================================================----
# SEKTION 2: STATS.TXT (Avancerad OmenServe-statistik)
# =================================================================----

def load_advanced_stats():
    """Läser stats.txt. Format: total_files total_bytes yest_files yest_bytes today_files today_bytes last_date"""
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
        print(f"[DB ERROR] Kunde inte tolka stats.txt, använder standard: {e}")
    return default_stats

def save_advanced_stats(stats):
    """Write the 7-column row to stats.txt, atomically.

    The previous version truncated the live file and then wrote into it, so a crash
    or a concurrent writer could leave a short row behind - which load_advanced_stats
    silently discards, resetting every counter to zero.
    """
    STATS_FILE = config.STATS_FILE
    try:
        with _disk_lock:
            row = f"{stats[0]} {stats[1]} {stats[2]} {stats[3]} {stats[4]} {stats[5]} {stats[6]}"
            _atomic_write(STATS_FILE, row)
    except Exception as e:
        print(f"[DB ERROR] Kunde inte spara till stats.txt: {e}")

def check_and_rotate_day():
    """Kollar midnattsskifte och roterar Today till Yesterday automatiskt"""
    stats = load_advanced_stats()
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    
    if stats[6] != today_str:
        print(f"[DB ROTATE] Ny dag upptäckt ({today_str}). Flyttar statistik till igår.")
        stats[2] = stats[4]  # Yesterday files = Today files
        stats[3] = stats[5]  # Yesterday bytes = Today bytes
        stats[4] = 0         # Nollställ idag-filer
        stats[5] = 0         # Nollställ idag-bytes
        stats[6] = today_str
        save_advanced_stats(stats)
    return stats

def update_stats_on_complete(file_size):
    """Räknar upp Total och Today på disken när en filöverföring är klar (Typ-säkrad)"""
    stats = check_and_rotate_day()
    
    # --- STENHÅRD TYP-REPARATION MOD MOT DB-ERROR ---
    clean_size = 0
    try:
        # Om file_size råkar komma in som en lista, plocka ut första elementet
        if isinstance(file_size, list):
            if len(file_size) > 0:
                file_size = file_size[0]
            else:
                file_size = 0
                
        # Om det är en dictionary, leta efter kända fältnamn för storlek
        if isinstance(file_size, dict):
            file_size = file_size.get('bytes', file_size.get('size', 0))
            
        # Tvinga fram ett rent heltal via float-omväg ifall det är en textsträng med decimaler
        clean_size = int(float(str(file_size).strip()))
    except Exception as type_err:
        print(f"[DB WARNING] Kunde inte tolka filstorlek '{file_size}' automatisk fallback till 0: {type_err}")
        clean_size = 0
    # ---------------------------------------------------------------------

    stats[0] += 1          # Totala filer +1
    stats[1] += clean_size # Totala bytes +storlek (Helt säkrad siffra)
    stats[4] += 1          # Dagens filer +1
    stats[5] += clean_size # Dagens bytes +storlek
    save_advanced_stats(stats)

def get_speed_record():
    """Hämtar det sparade hastighetsrekordet i bytes/s från hårddisken"""
    import os
    file_path = "./data/speed_record.txt"
    if not os.path.exists(file_path):
        return 0
    try:
        with open(file_path, "r") as f:
            return int(f.read().strip())
    except:
        return 0

def save_speed_record(new_record):
    """Save a new speed record to disk, atomically."""
    try:
        with _disk_lock:
            _atomic_write(SPEED_RECORD_FILE, str(int(new_record)))
    except Exception as e:
        print(f"[DB ERROR] Kunde inte spara hastighetsrekord: {e}")

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
        config.dcc_queue = {}
        return
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            raise ValueError(f"expected a JSON object, got {type(loaded).__name__}")
        config.dcc_queue = loaded
        total = sum(len(v) for v in loaded.values() if isinstance(v, list))
        print(f"[DB] Laddade in {total} sparad(e) köplats(er) för {len(loaded)} användare från hårddisken!")
    except Exception as e:
        config.dcc_queue = {}
        print(f"[DB ERROR] Kunde inte läsa sparad DCC-kö, startar tom: {e}")
        try:
            backup = file_path + ".corrupt"
            os.replace(file_path, backup)
            print(f"[DB ERROR] Den skadade filen sparades som {backup} för manuell räddning.")
        except Exception as backup_err:
            print(f"[DB ERROR] Kunde inte ens säkerhetskopiera den skadade filen: {backup_err}")