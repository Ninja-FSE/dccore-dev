# list_fetch.py - safe extraction and parsing of ANOTHER bot's fetched
# master-list zip. Same "we are not the trusted party here" posture as
# dcc_fetch.py's own module docstring, one layer further in: by the time
# process_fetched_list_zip() below is called, dcc_fetch.py has already
# received a complete, size-verified zip file from a third-party bot into
# FETCHED_FILES_DIR - but a zip file is a container, and nothing about a
# successful byte-for-byte transfer proves anything about what is safe to do
# with its CONTENTS. That is what this module is responsible for, and
# nothing else:
#
#   * "zip slip" (path traversal): a member whose name is something like
#     "../../../etc/cron.d/evil" or an absolute path, which - if extracted
#     naively - writes OUTSIDE the intended extraction directory. Every
#     member's resolved destination is validated against dcc.is_safe_path()
#     (the same containment check used elsewhere in this codebase for exactly
#     this class of problem) BEFORE anything is extracted; the whole archive
#     is rejected, not just the offending member, and nothing is written for
#     a rejected archive.
#   * zip bombs: a small file that decompresses to an enormous one. Guarded
#     before touching disk, by summing the zip's own declared uncompressed
#     sizes against MAX_FETCH_FILE_SIZE (the same ceiling dcc_fetch.py
#     applies to a raw DCC SEND offer) - a zip that under-declares a
#     member's size to sneak past this sum still cannot over-deliver at
#     extraction time, because Python's own zipfile.ZipExtFile already
#     truncates every read to that member's declared file_size regardless of
#     its real compressed payload (see _extract_member()'s docstring for the
#     detail). _extract_member() also tracks a running byte budget as it
#     copies, but given that stdlib truncation, that tracking is inert
#     defense-in-depth today, not an independently load-bearing second
#     barrier - the pre-check above is what actually makes this safe.
#   * a zip with an implausible number of entries - a real master-list
#     archive (see update_list.py) contains at most two files (the master
#     list and the RAR/album-folder list); MAX_LIST_ZIP_ENTRIES rejects
#     anything wildly beyond that shape outright, before either guard above
#     even runs.
#
# Extraction lands in a per-bot subdirectory - <FETCHED_FILES_DIR>/lists/<bot
# nick>/ - deliberately never alongside dcc_fetch.py's own raw fetched files:
# those are "a file I fetched to listen to", this is "a list archive I
# extracted to browse", and conflating the two on disk would make it easy to
# mistake one for the other later.
#
# Parsing reuses list.py's existing pipeline (find_matching_entries() with
# its new `list_path` parameter, which reuses _split_entry_line() and
# strip_info_suffix() under the hood) rather than writing a second parser -
# the same "::INFO::" tolerance this project already added for OTHER bots'
# formatting variance (see strip_info_suffix()'s own docstring) applies here
# automatically, for free.
import os
import shutil
import threading
import time
import zipfile

import config
import dcc
import platform_compat
import list as list_mod

# A real master-list zip (update_list.py's generate_master_list()) contains
# at most two files. A few hundred is a generous ceiling that still rejects
# anything shaped like an attempt to smuggle a large number of small files
# past the total-size guard below (many tiny files can add up to a large
# total while each individually looking innocuous) - a module constant, not a
# config.py tunable, same reasoning as webserver.WEBUI_MAX_SEARCH_RESULTS:
# this is an internal safety bound, not an operator-facing knob.
MAX_LIST_ZIP_ENTRIES = 300

_COPY_CHUNK = 65536


_FALLBACK_LOCK = threading.Lock()


def _lock():
    """The dedicated lock oserve.py allocates at startup for
    config.fetched_bot_lists, or a module-level fallback - same idiom as
    dcc_fetch._fetch_lock(), needed so tests (and any other caller that never
    ran oserve.startup()) still have something to synchronise on.

    The fallback is allocated once, at import, rather than per call: returning
    a fresh Lock() each time would hand every caller a different object and so
    would serialise nothing at all."""
    return getattr(config, "fetched_bot_lists_lock", None) or _FALLBACK_LOCK


def _ensure_fetched_bot_lists():
    if not hasattr(config, "fetched_bot_lists") or config.fetched_bot_lists is None:
        config.fetched_bot_lists = {}
    return config.fetched_bot_lists


def _sanitize_bot_dir_name(bot):
    """Never trust a bot nick as a literal path component either - the same
    discipline dcc_fetch._sanitize_offer_filename() applies to a filename,
    applied here to what becomes a directory name instead."""
    name = list_mod.strip_control_codes(str(bot))
    name = name.replace('\x00', '')
    name = name.replace('/', '_').replace('\\', '_')
    name = name.replace('..', '')
    name = name.strip().strip('.').strip()
    return name or "unknown_bot"


def list_extract_dir(bot):
    """Where `bot`'s fetched list gets extracted to:
    <FETCHED_FILES_DIR>/lists/<sanitised bot nick, lowercased>/.
    """
    base = os.path.abspath(getattr(config, "FETCHED_FILES_DIR", "./data/fetched"))
    lists_root = os.path.join(base, "lists")
    candidate = os.path.join(lists_root, _sanitize_bot_dir_name(bot).lower())
    if not dcc.is_safe_path(lists_root, candidate):
        # Defense-in-depth, expected to be unreachable given the sanitiser
        # above already strips "/", "\\" and "..": fall back to a fixed,
        # definitely-safe name rather than ever extracting somewhere
        # unintended.
        candidate = os.path.join(lists_root, "unknown_bot")
    return candidate


def _validate_zip_members(infolist, extract_dir):
    """Check EVERY member before anything is extracted. Returns a short
    rejection reason string, or None if the whole archive is clear to
    extract. Never partial: the caller only proceeds if this returns None.
    """
    if not infolist:
        return "zip archive is empty"
    if len(infolist) > MAX_LIST_ZIP_ENTRIES:
        return (f"zip contains {len(infolist)} entries, more than the "
                f"{MAX_LIST_ZIP_ENTRIES} a real master-list archive should "
                f"ever need (zip-bomb-shaped guard)")

    max_total = int(getattr(config, "MAX_FETCH_FILE_SIZE", 200 * 1024 * 1024))
    total_uncompressed = 0
    for info in infolist:
        if info.is_dir():
            continue
        total_uncompressed += info.file_size
        if total_uncompressed > max_total:
            return (f"zip's declared total uncompressed size exceeds "
                     f"MAX_FETCH_FILE_SIZE ({max_total} bytes) - refusing to "
                     f"extract (zip-bomb guard)")

        member_name = info.filename.replace('\\', '/')
        # An absolute path (POSIX "/etc/..." or a Windows drive letter like
        # "C:/...") smuggled into a zip entry name - checked explicitly and
        # BEFORE the join below, rather than relying on is_safe_path() to
        # catch every possible form of it after the fact.
        if member_name.startswith('/') or (len(member_name) > 1 and member_name[1] == ':'):
            return f"zip entry {info.filename!r} has an absolute path"

        parts = [p for p in member_name.split('/') if p not in ('', '.')]
        if not parts:
            continue

        dest_path = os.path.join(extract_dir, *parts)
        if not dcc.is_safe_path(extract_dir, dest_path):
            return (f"zip entry {info.filename!r} would extract outside the "
                     f"target directory (path traversal / zip-slip)")

        # A component made only of dots - "..", "...", "...." and so on.
        #
        # ".." is caught by is_safe_path() below, because it genuinely
        # resolves outside. Longer runs are NOT: "...." is a legal directory
        # name that resolves INSIDE the target, so the containment check
        # passes it, correctly.
        #
        # The problem is what Win32 does with it afterwards. Trailing dots are
        # stripped during path parsing, so "<extract>/...." resolves to
        # "<extract>" itself - a path that names a child but operates on the
        # parent. Extraction then fails, and every later attempt to prepare
        # that directory fails too:
        #
        #   [WinError 145] The directory is not empty: ...\lists\<bot>\....
        #
        # so one hostile archive permanently disables list fetching from that
        # bot until somebody deletes it by hand. An extended-length "\\?\\"
        # path does not rescue the cleanup either - it returns
        # ERROR_INVALID_NAME. Refusing the name is the fix.
        #
        # Nothing legitimate is lost: no master-list archive has a member whose
        # directory is called "....".
        for part in parts:
            if set(part) == {'.'}:
                return (f"zip entry {info.filename!r} has a path component "
                         f"made only of dots ({part!r})")

    return None


def _extract_member(zf, info, dest_path, budget):
    """Copy one zip member to `dest_path`, tracking bytes written against
    `budget` (the remaining slice of MAX_FETCH_FILE_SIZE after every earlier
    member in this archive) and raising ValueError if it is ever exceeded,
    which the caller treats as a hard abort of the whole archive.

    In practice this loop cannot actually observe more than `info.file_size`
    bytes per member: `zf.open(info)` returns a stdlib `ZipExtFile`, whose
    own `read()`/`_read1()` already truncates to the entry's declared
    `file_size` internally, regardless of how much compressed data the entry
    actually contains - so a member that lies about its size (over- or
    under-declaring) can never make this loop copy more than it declared.
    The real protection against a size-lying entry is
    _validate_zip_members()'s caller summing every declared file_size against
    MAX_FETCH_FILE_SIZE BEFORE any bytes are copied (see
    process_fetched_list_zip() below) - that pre-check, combined with this
    stdlib truncation behaviour, is what actually makes a zip bomb via a
    false declared size impossible here. This function's own `written >
    budget` check is therefore inert defense-in-depth against a
    hypothetical future change to how this module reads zip members (e.g.
    reading raw compressed bytes instead of through `ZipExtFile`), not an
    independently-necessary second barrier today - it is kept because it is
    cheap and correct, not because it currently catches anything the
    pre-check doesn't already rule out. Returns the number of bytes written.
    """
    written = 0
    # Every path below goes through platform_compat.long_path(), the same
    # way dcc.py wraps each path it touches. A zip member name is chosen by
    # the remote bot and is never truncated, so a perfectly legal 240-
    # character name pushes the destination past Windows' 260-character
    # MAX_PATH and the write fails with "No such file or directory" - for a
    # file this code is itself trying to create.
    os.makedirs(platform_compat.long_path(os.path.dirname(dest_path)),
                exist_ok=True)
    long_dest = platform_compat.long_path(dest_path)
    with zf.open(info) as src, open(long_dest, "wb") as dst:
        while True:
            chunk = src.read(_COPY_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > budget:
                raise ValueError(
                    "zip entry decompressed past its declared-size budget "
                    "(zip-bomb guard tripped during extraction)")
            dst.write(chunk)
    return written


def _pick_list_file(extract_dir):
    """Find the extracted master-list .txt file.

    The real naming convention (see update_list.py) is
    "<LIST_BASE_NAME>-<date>.txt", but a fetched zip came from someone else's
    bot running its own base name - not necessarily ours - so this does not
    hardcode config.LIST_BASE_NAME. Instead:

      * excludes anything matching update_list.py's own "-RAR-" convention
        for the separate album-folder list, the same way list.find_latest_list()
        already excludes it from search;
      * if exactly one plausible .txt remains, uses it;
      * if more than one remains (ambiguous), picks the LARGEST one - a real
        master list enumerates every track and is by far the biggest text
        file in a list archive - and logs a clear warning that it had to
        guess, rather than silently choosing wrong or crashing;
      * if none remain, returns None so the caller can report "no
        recognisable list file" instead of guessing at all.
    """
    txt_files = []
    for root, _dirs, files in os.walk(extract_dir):
        for fname in files:
            if fname.lower().endswith(".txt"):
                txt_files.append(os.path.join(root, fname))

    if not txt_files:
        return None

    candidates = [p for p in txt_files if "-rar-" not in os.path.basename(p).lower()]
    if not candidates:
        candidates = txt_files

    if len(candidates) == 1:
        return candidates[0]

    candidates.sort(key=lambda p: os.path.getsize(p), reverse=True)
    print(f"[LIST-FETCH] WARNING: {len(candidates)} candidate .txt files found "
          f"in {extract_dir!r}; picking the largest "
          f"({os.path.basename(candidates[0])}) as the master list - a "
          f"best-effort guess, not a confident match.")
    return candidates[0]


def _extract_and_locate_list_file(zip_path, extract_dir):
    """Validate, then safely extract, `zip_path` into `extract_dir` (wiped
    and recreated first, so a previous fetch's leftovers can never be
    mistaken for this one's), and return (list_path, reason):

      * (path, None) - extraction succeeded and a plausible list file was
        found at `path`.
      * (None, None) - extraction succeeded but no plausible list .txt was
        found anywhere inside the archive.
      * (None, reason) - the archive was rejected outright (path traversal,
        zip bomb, too many entries, not a valid zip, ...); nothing from it
        was left on disk, including any partial extraction from before the
        rejection was detected.
    """
    try:
        if os.path.exists(platform_compat.long_path(extract_dir)):
            # The directory itself is short, but a previous archive may have
            # left long-named members inside it; rmtree cannot delete what
            # it cannot open, and the prefix is inherited by every child.
            shutil.rmtree(platform_compat.long_path(extract_dir))
        os.makedirs(platform_compat.long_path(extract_dir), exist_ok=True)
    except OSError as err:
        return None, f"could not prepare the extraction directory: {err}"

    try:
        with zipfile.ZipFile(platform_compat.long_path(zip_path), "r") as zf:
            infolist = zf.infolist()
            reason = _validate_zip_members(infolist, extract_dir)
            if reason:
                shutil.rmtree(platform_compat.long_path(extract_dir), ignore_errors=True)
                return None, reason

            max_total = int(getattr(config, "MAX_FETCH_FILE_SIZE", 200 * 1024 * 1024))
            budget = max_total
            for info in infolist:
                if info.is_dir():
                    continue
                member_name = info.filename.replace('\\', '/')
                parts = [p for p in member_name.split('/') if p not in ('', '.')]
                if not parts:
                    continue
                dest_path = os.path.join(extract_dir, *parts)
                written = _extract_member(zf, info, dest_path, budget)
                budget -= written
    except (zipfile.BadZipFile, ValueError, OSError) as err:
        # Covers a corrupt/non-zip file, the zip-bomb guard tripping mid-copy
        # (ValueError from _extract_member), and any filesystem error - every
        # one of them means "abort the whole extraction", never "keep what
        # extracted so far".
        shutil.rmtree(platform_compat.long_path(extract_dir), ignore_errors=True)
        return None, f"extraction aborted: {err}"
    except Exception as err:
        # zipfile leaks more than those three for a hand-crafted archive:
        # zlib.error for a corrupt deflate stream, NotImplementedError for a
        # compression method it has no decompressor for, and RuntimeError for
        # a member flagged encrypted. None of them subclass the cases above.
        #
        # A remote peer chooses these bytes, and process_fetched_list_zip()
        # promises callers it never raises, so anything that gets past the
        # specific cases still means "abort this extraction" - never an
        # exception loose in the fetch thread. The type name goes into the
        # reason because, unlike the cases above, it is not self-describing.
        shutil.rmtree(extract_dir, ignore_errors=True)
        return None, f"extraction aborted: {type(err).__name__}: {err}"

    return _pick_list_file(extract_dir), None


def process_fetched_list_zip(bot, zip_path):
    """Entry point, called by dcc_fetch.py once a request_type="list" fetch
    reaches 'complete'. Safely extracts `zip_path`, locates and parses the
    master-list .txt inside it via list.py's existing pipeline, and stores
    the result in config.fetched_bot_lists keyed by lowercased bot nick -
    REPLACING any previous entry for the same bot, per the operator's
    explicit "switchable, not accumulating" requirement.

    Returns (success, reason): reason is None on success, otherwise a short
    human-readable string suitable for logging/dashboard display. Never
    raises - every anticipated failure mode (bad zip, zip bomb, path
    traversal, no recognisable list file) is handled here.

    The whole extract -> parse -> store sequence runs under the lock, not just
    the store write at the end. list_extract_dir() keys on the bot nick alone,
    and extraction opens by rmtree-ing that directory, so two fetches for the
    same bot would otherwise delete each other's files mid-extraction - and
    the fetch slot pool allows several transfers to complete at once.

    The lock is module-wide rather than per bot, which also serialises fetches
    for DIFFERENT bots. That is deliberate: it needs no nick-keyed registry to
    grow, and it means only one list is ever being parsed into memory at a
    time, so the peak cost of a parse is one list instead of one per slot.
    Extraction is a background step measured in seconds, so the wait costs
    nothing that matters.
    """
    with _lock():
        return _process_fetched_list_zip_unlocked(bot, zip_path)


def _process_fetched_list_zip_unlocked(bot, zip_path):
    """The body of process_fetched_list_zip. Caller must hold _lock()."""
    extract_dir = list_extract_dir(bot)
    list_path, reason = _extract_and_locate_list_file(zip_path, extract_dir)
    if reason:
        print(f"[LIST-FETCH] Rejected list zip from {bot}: {reason}")
        return False, reason
    if list_path is None:
        reason = "no recognizable master-list .txt file was found inside the zip"
        print(f"[LIST-FETCH] {bot}'s list zip extracted, but {reason}.")
        return False, reason

    # list.py opens this path directly and does not wrap it itself, so the
    # prefix goes on here - at the point of use, the same idiom dcc.py uses.
    # Without it, widening the write path above would only move the failure:
    # extraction would succeed and the parse would raise FileNotFoundError,
    # outside the extraction guard, on a file that is plainly there.
    entries, _total = list_mod.find_matching_entries(
        [], limit=None, list_path=platform_compat.long_path(list_path))
    rows = list_mod.entries_to_filelist_rows(entries, str(bot).strip())

    store = _ensure_fetched_bot_lists()
    store[str(bot).strip().lower()] = {
        "bot": str(bot).strip(),
        "fetched_at": time.time(),
        "entries": rows,
        "source_zip": os.path.basename(zip_path),
    }

    print(f"[LIST-FETCH] Stored {len(rows)} entries from {bot}'s fetched list "
          f"({os.path.basename(list_path)}).")
    return True, None
