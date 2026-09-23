"""Duration and quality for the list's file rows (#567).

    !DCCore Artist - Album - 01 - Track.mp3  ::INFO:: 10.3MB 4m31s 320/44.1/JS
    !DCCore Artist - Album - 02 - Track.flac  ::INFO:: 16.7MB 2m5s 1115/44.1/S

After the size: the duration, then bitrate kbps / sample rate kHz / channels.
The spelling is the one other servers' lists already use, so every reader that
parses theirs - this project's own list.py included - reads ours unchanged. A
VBR MP3's number is its average, marked with a leading "~" (~245/44.1/JS); a
FLAC's is its real bitrate, what the file actually costs per second. Channels:
S stereo, JS joint stereo, DC dual channel, M mono, and "6ch" beyond two.

STANDARD LIBRARY ONLY. Reading audio metadata normally means mutagen, and zero
third-party packages is a property of the project. MP3 and FLAC are the two the
issue asked for and the two these lists are made of; both answer from a seek
and a few KB, never a full read:

- MP3: skip any ID3v2 tags, find the first frame whose header decodes AND whose
  successor sits where the header says, decode version / layer / bitrate /
  sample rate / channel mode, then look inside that frame for a Xing / Info /
  VBRI header, which carries the frame count - the only honest duration for
  VBR. Without one it is CBR and the duration is the audio bytes over the
  bitrate (an ID3v1 tag at the end is not audio).
- FLAC: the "fLaC" magic, then the metadata blocks; STREAMINFO has the sample
  rate, channels and total samples, and where the blocks end is where the audio
  starts - which is what the real bitrate is measured from.

Anything else, and anything these cannot make sense of, is None: the row keeps
its size and nothing more. A malformed file must never take a list build down,
so read() never raises.

THE CACHE. The scan otherwise asks each file for nothing but its size; this
opens every one. Kept in SQLite (stdlib) at LIST_AUDIO_INFO_CACHE, keyed by the
row's list path and checked against the file's size and mtime, so a rebuild
re-reads only what changed - the first one pays, the rest are a stat each.
Rows for files no longer in the library are dropped when a rebuild publishes.
"""

import os
import sqlite3
import time

import defaults as config

# Extensions read at all. Everything else is size-only without being opened.
AUDIO_EXTENSIONS = (".mp3", ".flac")

# How far past the ID3v2 tag the first MP3 frame may start. Encoders pad; a
# file whose first frame is further out than this is size-only, not a stall.
MP3_SYNC_WINDOW = 64 * 1024
# How many FLAC metadata blocks are walked before giving up on a file.
FLAC_MAX_BLOCKS = 256

_BITRATES = {
    (1, 1): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (1, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (1, 3): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 3): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_SAMPLE_RATES = {1: (44100, 48000, 32000), 2: (22050, 24000, 16000), 25: (11025, 12000, 8000)}
_MP3_MODES = {0: "S", 1: "JS", 2: "DC", 3: "M"}


def is_audio(name):
    return name.lower().endswith(AUDIO_EXTENSIONS)


def _id3v2_end(handle, start=0):
    """The offset just past every ID3v2 tag at `start` (there may be more
    than one, back to back), or `start` when there is none."""
    offset = start
    for _ in range(8):
        handle.seek(offset)
        head = handle.read(10)
        if len(head) < 10 or head[:3] != b"ID3":
            break
        size = 0
        for byte in head[6:10]:
            if byte & 0x80:  # not a synchsafe integer: not a real tag
                return offset
            size = (size << 7) | byte
        offset += 10 + size + (10 if head[5] & 0x10 else 0)
    return offset


def _mp3_header(data, at):
    """The frame header at data[at:at+4], decoded, or None."""
    if at + 4 > len(data):
        return None
    word = int.from_bytes(data[at:at + 4], "big")
    if (word >> 21) & 0x7FF != 0x7FF:
        return None
    version = {0: 25, 2: 2, 3: 1}.get((word >> 19) & 3)
    layer = 4 - ((word >> 17) & 3)
    bitrate_index = (word >> 12) & 0xF
    rate_index = (word >> 10) & 3
    if version is None or layer == 4 or bitrate_index in (0, 15) or rate_index == 3:
        return None
    kbps = _BITRATES[(1 if version == 1 else 2, layer)][bitrate_index]
    rate = _SAMPLE_RATES[version][rate_index]
    padding = (word >> 9) & 1
    samples = 384 if layer == 1 else (1152 if layer == 2 or version == 1 else 576)
    if layer == 1:
        length = (12 * kbps * 1000 // rate + padding) * 4
    else:
        length = samples // 8 * kbps * 1000 // rate + padding
    return {"version": version, "layer": layer, "kbps": kbps, "rate": rate,
            "mode": (word >> 6) & 3, "samples": samples, "length": length}


def _first_frame(data):
    """(offset, header) of the first frame whose NEXT frame is where it says,
    or None. A lone 0xFFE bit pattern inside tag padding or cover art decodes
    as a header often enough that one match proves nothing."""
    at = data.find(b"\xff")
    while 0 <= at < len(data) - 4:
        header = _mp3_header(data, at)
        if header and header["length"] > 4:
            following = at + header["length"]
            if following + 4 > len(data):
                return at, header  # nothing to confirm against; take it
            nxt = _mp3_header(data, following)
            if nxt and (nxt["version"], nxt["layer"], nxt["rate"]) == \
                    (header["version"], header["layer"], header["rate"]):
                return at, header
        at = data.find(b"\xff", at + 1)
    return None


def _read_mp3(handle, size):
    start = _id3v2_end(handle)
    handle.seek(start)
    data = handle.read(MP3_SYNC_WINDOW)
    found = _first_frame(data)
    if not found:
        return None
    at, header = found
    audio_end = size
    if size >= 128:
        handle.seek(size - 128)
        if handle.read(3) == b"TAG":
            audio_end = size - 128
    audio_bytes = audio_end - (start + at)
    if audio_bytes <= 0:
        return None

    frames = total_bytes = None
    vbr = False
    if header["version"] == 1:
        side = 17 if header["mode"] == 3 else 32
    else:
        side = 9 if header["mode"] == 3 else 17
    tag_at = at + 4 + side
    tag = data[tag_at:tag_at + 4]
    if tag in (b"Xing", b"Info") and len(data) >= tag_at + 16:
        flags = int.from_bytes(data[tag_at + 4:tag_at + 8], "big")
        cursor = tag_at + 8
        if flags & 1:
            frames = int.from_bytes(data[cursor:cursor + 4], "big")
            cursor += 4
        if flags & 2:
            total_bytes = int.from_bytes(data[cursor:cursor + 4], "big")
        vbr = tag == b"Xing"
    elif data[at + 36:at + 40] == b"VBRI" and len(data) >= at + 54:
        total_bytes = int.from_bytes(data[at + 46:at + 50], "big")
        frames = int.from_bytes(data[at + 50:at + 54], "big")
        vbr = True

    if frames:
        seconds = frames * header["samples"] / header["rate"]
    else:
        vbr = False  # a VBR header without a frame count says nothing usable
        seconds = audio_bytes * 8 / (header["kbps"] * 1000)
    if seconds <= 0:
        return None
    if vbr:
        kbps = round((total_bytes or audio_bytes) * 8 / seconds / 1000)
    else:
        kbps = header["kbps"]
    return {"seconds": seconds, "kbps": kbps, "rate": header["rate"],
            "channels": _MP3_MODES[header["mode"]], "vbr": vbr}


def _read_flac(handle, size):
    start = _id3v2_end(handle)
    handle.seek(start)
    if handle.read(4) != b"fLaC":
        return None
    offset = start + 4
    info = None
    for _ in range(FLAC_MAX_BLOCKS):
        head = handle.read(4)
        if len(head) < 4:
            return None
        length = int.from_bytes(head[1:4], "big")
        if head[0] & 0x7F == 0:
            body = handle.read(length)
            if length < 34 or len(body) < 34:
                return None
            word = int.from_bytes(body[10:18], "big")
            info = {"rate": word >> 44, "channels": ((word >> 41) & 7) + 1,
                    "samples": word & 0xFFFFFFFFF}
        else:
            handle.seek(length, 1)
        offset += 4 + length
        if head[0] & 0x80:
            break
    else:
        return None
    if not info or not info["rate"] or not info["samples"] or offset >= size:
        return None
    seconds = info["samples"] / info["rate"]
    channels = {1: "M", 2: "S"}.get(info["channels"], f"{info['channels']}ch")
    return {"seconds": seconds, "kbps": round((size - offset) * 8 / seconds / 1000),
            "rate": info["rate"], "channels": channels, "vbr": False}


def read(path, size=None):
    """{"seconds", "kbps", "rate", "channels", "vbr"} for an MP3 or FLAC, or
    None - for any other file, and for any file that cannot be read or makes
    no sense. Never raises."""
    name = path.lower()
    try:
        if size is None:
            size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if name.endswith(".mp3"):
                return _read_mp3(handle, size)
            if name.endswith(".flac"):
                return _read_flac(handle, size)
    except Exception:
        return None
    return None


def describe(info):
    """"4m31s 320/44.1/JS" from what read() returned; "" for None."""
    if not info:
        return ""
    seconds = int(round(info["seconds"]))
    quality = f"{'~' if info.get('vbr') else ''}{info['kbps']}/{info['rate'] / 1000:g}/{info['channels']}"
    return f"{seconds // 60}m{seconds % 60}s {quality}"


def cache_path():
    return getattr(config, "LIST_AUDIO_INFO_CACHE", "./data/audio_info.db")


class Cache:
    """What a rebuild knows about each audio file, kept between rebuilds.

    observe() is called from the walk, once per listed audio file: it answers
    from the stored row when the file's size and mtime still match, and reads
    the file otherwise. suffix() is called while the list is written. publish()
    drops every row this rebuild did not observe - a file removed from the
    library - and is called only when the rebuild publishes, so an aborted scan
    that saw half the library does not throw away the other half.

    Opening can fail (a read-only data directory, a damaged file); then open()
    returns None, the caller says so, and the list is written size-only.
    """

    COMMIT_EVERY = 2000

    def __init__(self, conn, reader=None, scope=""):
        self.conn = conn
        self.scope = scope
        self.run = time.time_ns()
        self.reader = reader or read
        self.pending = 0
        self.read_count = 0
        self.reused_count = 0

    @classmethod
    def open(cls, path=None, reader=None, log=print, scope=""):
        """`scope` is the list being built: each list prunes only its own
        rows, so rebuilding one never empties another's."""
        path = path or cache_path()
        try:
            folder = os.path.dirname(path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE IF NOT EXISTS audio (scope TEXT, key TEXT, size INTEGER, "
                         "mtime INTEGER, suffix TEXT, run INTEGER, PRIMARY KEY (scope, key))")
            conn.commit()
            return cls(conn, reader, scope or "")
        except (sqlite3.Error, OSError) as err:
            log(f"[LIST-GEN] Could not open the audio info cache at {path!r} ({err}); "
                f"this list is written with sizes only.")
            return None

    def observe(self, key, path, size):
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError:
            return
        row = self.conn.execute("SELECT size, mtime FROM audio WHERE scope = ? AND key = ?",
                                (self.scope, key)).fetchone()
        if row and row[0] == size and row[1] == mtime:
            self.conn.execute("UPDATE audio SET run = ? WHERE scope = ? AND key = ?",
                              (self.run, self.scope, key))
            self.reused_count += 1
        else:
            suffix = describe(self.reader(path, size))
            self.conn.execute("INSERT OR REPLACE INTO audio (scope, key, size, mtime, suffix, run) "
                              "VALUES (?, ?, ?, ?, ?, ?)", (self.scope, key, size, mtime, suffix, self.run))
            self.read_count += 1
        self.pending += 1
        if self.pending >= self.COMMIT_EVERY:
            self.conn.commit()
            self.pending = 0

    def suffix(self, key):
        row = self.conn.execute("SELECT suffix FROM audio WHERE scope = ? AND key = ? AND run = ?",
                                (self.scope, key, self.run)).fetchone()
        return row[0] if row and row[0] else ""

    def publish(self):
        self.conn.execute("DELETE FROM audio WHERE scope = ? AND run != ?", (self.scope, self.run))
        self.conn.commit()

    def close(self):
        try:
            self.conn.commit()
            self.conn.close()
        except sqlite3.Error:
            pass


def row_key(folder, filename):
    """The cache key for one list row: its folder heading and its name, which
    is what the list itself identifies a file by."""
    return f"{folder}\x00{filename}"
