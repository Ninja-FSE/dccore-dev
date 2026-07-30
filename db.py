# db.py - Central databashantering för FLAC-Serv
import os
import time
import datetime
import config

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
    """Skriver alla aktiva bans från minnet till bans.txt"""
    try:
        with open(config.BANS_FILE, "w") as f:
            for user_key, expire_ts in config.banned_users.items():
                f.write(f"{user_key} {expire_ts}\n")
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
    """Skriver ner 7-kolonnsraden till stats.txt"""
    STATS_FILE = config.STATS_FILE
    try:
        with open(STATS_FILE, "w") as f:
            f.write(f"{stats[0]} {stats[1]} {stats[2]} {stats[3]} {stats[4]} {stats[5]} {stats[6]}")
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
    """Räknar upp Total och Today på disken när en filöverföring är klar"""
    stats = check_and_rotate_day()
    stats[0] += 1          # Totala filer +1
    stats[1] += file_size  # Totala bytes +storlek
    stats[4] += 1          # Dagens filer +1
    stats[5] += file_size  # Dagens bytes +storlek
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
    """Sparar ett nytt hastighetsrekord till hårddisken"""
    import os
    os.makedirs("./data", exist_ok=True)
    try:
        with open("./data/speed_record.txt", "w") as f:
            f.write(str(int(new_record)))
    except Exception as e:
        print(f"[DB ERROR] Kunde inte spara hastighetsrekord: {e}")

def save_dcc_queue():
    """Sparar hela den globala DCC-kön permanent till hårddisken i JSON-format"""
    import json
    import os
    import config
    os.makedirs("./data", exist_ok=True)
    file_path = "./data/dcc_queue.txt"
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            # json.dump sparar hela din kö-struktur spikrakt till textfilen
            json.dump(config.dcc_queue, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"[DB ERROR] Kunde inte spara DCC-kön till hårddisken: {e}")

def load_dcc_queue():
    """Laddar in den sparade DCC-kön från hårddisken vid boot"""
    import json
    import os
    import config
    file_path = "./data/dcc_queue.txt"
    if not os.path.exists(file_path):
        config.dcc_queue = {}
        return
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            config.dcc_queue = json.load(f)
        print(f"[DB] Laddade in sparade köplatser från hårddisken!")
    except Exception as e:
        print(f"[DB ERROR] Kunde inte läsa sparad DCC-kö, startar tom: {e}")
        config.dcc_queue = {}
