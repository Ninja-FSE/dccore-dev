# stats_mgr.py - Dedikerad modul för storleksformatering, hastighet och drifttidsdata (Skottsäker)
import time
import db

# Vi sparar botens starttid lokalt här.
#
# Guarded, because !rehash reloads this module and importlib.reload re-executes
# the body against the SAME module dict - a bare assignment reset the uptime to
# zero on every rehash. The name already being bound is exactly what tells us
# this is a reload rather than the first import, so the original value stands.
try:
    start_time
except NameError:
    start_time = time.time()

def load_stats():
    """Läser det nya 7-kolonnsformatet från den konfigurerade sökvägen via db-modulen"""
    return db.load_advanced_stats()

def get_total_sent():
    """Returnerar det totala antalet skickade filer som ett rent heltal (index 0)"""
    stats = load_stats()
    try:
        # Om databasen returnerar en nästlad lista [[319]], hämta innersta värdet
        if isinstance(stats, list) and len(stats) > 0:
            val = stats[0]
            if isinstance(val, list):
                val = val[0]
            return int(str(val).strip())
    except:
        pass
    return 0

def get_total_sent_bytes():
    """Returnerar det totala antalet skickade bytes som ett rent heltal (index 1)"""
    stats = load_stats()
    try:
        # Om databasen returnerar en nästlad lista [[319], [9303203296]], hämta innersta värdet
        if isinstance(stats, list) and len(stats) > 1:
            val = stats[1]
            if isinstance(val, list):
                val = val[0]
            return int(str(val).strip())
    except:
        pass
    return 0

def format_size_human(bytes_size):
    """Gör om råa bytes till ett smidigt format (t.ex. 361.2GB)"""
    try:
        bytes_size = float(bytes_size)
    except:
        return "0B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f}{unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f}PB"

def format_speed(bytes_per_sec):
    """Gör om råa bytes per sekund till en snygg sträng (t.ex. 4.5MB/s)"""
    if bytes_per_sec == 0:
        return "0k/s"
    kb = bytes_per_sec / 1024
    if kb < 1024:
        return f"{kb:.1f}k/s"
    mb = kb / 1024
    return f"{mb:.2f}MB/s"

def get_uptime_seconds():
    """Returnerar drifttid i sekunder för din mIRC-kanalreklam"""
    return int(time.time() - start_time)
