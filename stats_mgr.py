# stats_mgr.py - Size formatting, transfer speed and uptime figures
import time
import db

# The bot's start time is kept here.
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
    """Read the 7-column format from the configured path, via the db module."""
    return db.load_advanced_stats()

def get_total_sent():
    """Returnerar det totala antalet skickade filer som ett rent heltal (index 0)"""
    stats = load_stats()
    try:
        # If the store returns a nested list [[319]], take the innermost value
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
        # If the store returns a nested list [[319], [9303203296]], take the innermost value
        if isinstance(stats, list) and len(stats) > 1:
            val = stats[1]
            if isinstance(val, list):
                val = val[0]
            return int(str(val).strip())
    except:
        pass
    return 0

def format_size_human(bytes_size):
    """Turn raw bytes into a readable size (e.g. 361.2GB)."""
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
    """Turn raw bytes per second into a readable rate (e.g. 4.5MB/s)."""
    if bytes_per_sec == 0:
        return "0k/s"
    kb = bytes_per_sec / 1024
    if kb < 1024:
        return f"{kb:.1f}k/s"
    mb = kb / 1024
    return f"{mb:.2f}MB/s"

def get_uptime_seconds():
    """Uptime in seconds, for the channel advert."""
    return int(time.time() - start_time)
