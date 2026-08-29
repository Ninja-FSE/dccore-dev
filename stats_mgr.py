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
    """The total number of files sent, as a plain integer (index 0)."""
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
    """The total number of bytes sent, as a plain integer (index 1)."""
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

# How long a completed transfer must have run before its measured rate is
# allowed to stand as a record.
#
# dcc.py floors the measured duration at 0.1s and derives it from a start_time
# that is not guaranteed to be bound - when it is not, the duration comes out
# as 0, is floored, and the rate reads as ten times the file size however long
# the send really took. A record is permanent and public: one bad sample and
# the advert publishes a number the bot can never beat, for good. Requiring a
# real second of transfer throws away nothing anyone would recognise as a
# record and closes that off.
MIN_RECORD_SECONDS = 1.0


def update_speed_record(bytes_per_sec, duration=None):
    """Store `bytes_per_sec` if it beats the saved record. Returns the record
    in force afterwards, whether or not this call changed it.

    Reads and writes through db, so the atomic write and the disk lock are the
    same ones every other persisted counter uses.

    Refuses a sample that is not a positive number, and - when a duration is
    given - one measured over less than MIN_RECORD_SECONDS. Refusing is silent
    on purpose: a transfer too short to time is the ordinary case for a small
    file, not something worth a line in the log on every send.
    """
    current = db.get_speed_record()

    try:
        speed = int(bytes_per_sec)
    except (TypeError, ValueError):
        return current

    if speed <= 0:
        return current
    if duration is not None and duration < MIN_RECORD_SECONDS:
        return current
    if speed <= current:
        return current

    db.save_speed_record(speed)
    return speed


def get_uptime_seconds():
    """Uptime in seconds, for the channel advert."""
    return int(time.time() - start_time)
