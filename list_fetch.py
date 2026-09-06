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
import io
import os
import re
import shutil
import threading
import time
import zipfile

import defaults as config
import db
import dcc
import platform_compat
import runtime
import list as list_mod
import list_index

# A real master-list zip (update_list.py's generate_master_list()) contains
# at most two files. A few hundred is a generous ceiling that still rejects
# anything shaped like an attempt to smuggle a large number of small files
# past the total-size guard below (many tiny files can add up to a large
# total while each individually looking innocuous) - a module constant, not a
# config.py tunable, same reasoning as webserver.WEBUI_MAX_SEARCH_RESULTS:
# this is an internal safety bound, not an operator-facing knob.
MAX_LIST_ZIP_ENTRIES = 300

# Issue #76: every guard up to this point counts bytes or zip members - none
# of them counts LINES, and every "!" line in the extracted text becomes a
# permanently-retained dict once parsed. Checked on the EXTRACTED file's real
# size on disk, before it is parsed - not the zip's declared/compressed size,
# which is exactly what let a small download expand into hundreds of megabytes
# of retained rows in the first place.
#
# THE FIRST NUMBER WAS WRONG, and wrong in the way a guess about other
# people's data usually is. It was 20MB, reasoned as "5x headroom over the
# largest real list anyone here has actually seen" - that list being this
# operator's own 4MB one, from a 1.21TB/47,420-file library. Three lists in
# one channel then arrived at 25.7MB, 26.8MB and 31.5MB and were all refused,
# which is not a guard doing its job; it is a guard set from a sample of one.
#
# A FLAC library with long filenames produces a far bigger text list than a
# similarly sized MP3 one, and the ceiling has to hold for libraries this
# operator will never see. 128MB is four times the largest observed, and at
# roughly 80 bytes a row that is ~1.6M rows - inside the four million the
# cross-list index is measured against, so it is a size this project already
# knows it can hold.
#
# A SETTING rather than a constant now, which reverses the earlier reasoning
# deliberately. "An internal safety bound, not an operator-facing knob" holds
# when the right value is knowable here. It is not: it depends on the
# libraries of bots in somebody else's channel, and the last time this was
# fixed at a number it cost three real lists silently.
DEFAULT_MAX_LIST_TEXT_SIZE = 128 * 1024 * 1024


def max_list_text_size():
    """The ceiling, read through config so an operator can raise it.

    Resolved per call rather than captured at import, for the same reason
    every other path in this project is: !rehash reloads config.
    """
    try:
        value = int(getattr(config, "MAX_LIST_TEXT_SIZE",
                            DEFAULT_MAX_LIST_TEXT_SIZE))
    except (TypeError, ValueError):
        return DEFAULT_MAX_LIST_TEXT_SIZE
    return value if value > 0 else DEFAULT_MAX_LIST_TEXT_SIZE

_COPY_CHUNK = 65536

# How much of a plain-text list is read to decide it IS one. A real list
# reaches its first request line within a header plus a banner, and the
# banner is capped at 8KB, so this is comfortable headroom over the point
# the answer is knowable.
_PLAUSIBLE_LIST_PREFIX = 64 * 1024


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
    # config.fetched_bot_lists is bound from runtime.py at import time and
    # always exists as a real dict - never rebind it here, see runtime.py's
    # docstring.
    return config.fetched_bot_lists


# What may appear in a directory name built from a bot's nick. Mirrors
# dcc_fetch._FILENAME_CHARSET_RE, minus the space and parentheses a filename
# needs: a nick has neither, and a directory name with fewer moving parts is
# easier to recognise in a file manager.
_BOT_DIR_CHARSET_RE = re.compile(r'[^\w\-\.\[\]{}^`]')


def _advert_snapshot(bot):
    """What `bot` is advertising right now, as far as we have seen.

    Only the fields that bot actually published. A missing key means "this bot
    did not say", never zero - the rule irc.parse_channel_advert() already
    follows, and the one that keeps a bot which publishes no date from being
    permanently marked stale against an invented one.

    {} when we have never seen an advert from them, which is an ordinary state:
    a list can be fetched from a bot whose advert has not come round yet.
    """
    entry = dict(runtime.known_bots.get(str(bot).strip().lower()) or {})
    snapshot = {}
    for field in ("files", "list_date"):
        value = entry.get(field)
        if value not in (None, "", 0):
            snapshot[field] = value
    return snapshot


def _sanitize_bot_dir_name(bot):
    """Never trust a bot nick as a literal path component either - the same
    discipline dcc_fetch._sanitize_offer_filename() applies to a filename,
    applied here to what becomes a directory name instead.

    A WHITELIST, which is what that claim always meant and what this was not.
    It used to strip a blacklist - NUL, the two separators, ".." and
    surrounding dots - and pass everything else through. But "|" is a perfectly
    ordinary IRC nick character (RFC 2812's specials are []\\`_^{|}, and
    "Bot|Away" is one of the commonest nick shapes on the network) and is
    ILLEGAL in a Windows path.

    So os.makedirs() on the extraction directory failed with WinError 123 -
    AFTER the zip had already been fetched over DCC. The transfer worked, the
    bytes were on disk, and the fetch failed at the last step, every time, for
    that bot. Found by audit.

    Same charset as dcc_fetch, for the same reason: what is legal in a nick and
    what is legal in a path are different sets, and only one of them is ours to
    choose.
    """
    name = list_mod.strip_control_codes(str(bot))
    name = name.replace('\x00', '')
    name = name.replace('/', '_').replace('\\', '_')
    name = name.replace('..', '')
    name = _BOT_DIR_CHARSET_RE.sub('_', name)
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
    # long_path()-wrapped, like every other path in this module (see the
    # comment at the top of _write_member()). This was the one function
    # without it, and both halves bite on Windows: os.walk() silently returns
    # nothing for a directory past MAX_PATH, and the getsize() below raises
    # FileNotFoundError out of a function whose caller documents it as never
    # raising. A fetched list lands under a temp directory plus the sending
    # bot's own nick plus whatever it called its file, so the depth is not
    # this bot's to control.
    #
    # The walk root is wrapped and the results are joined onto that same
    # wrapped root, so nothing downstream mixes a prefixed path with an
    # unprefixed one - the mistake that turns a silent omission into a
    # ValueError.
    long_root = platform_compat.long_path(extract_dir)
    txt_files = []
    for root, _dirs, files in os.walk(long_root):
        for fname in files:
            if fname.lower().endswith(".txt"):
                txt_files.append(os.path.join(root, fname))

    if not txt_files:
        return None

    # "-video-" alongside "-rar-", and for the same reason twice over. Since
    # the film-and-series split, THIS bot's own archive carries two .txt
    # files - the master and "<base>-VIDEO-<date>.txt" - so a peer running
    # DCCore is the ORDINARY case here, not an exotic one. Without this the
    # largest-wins tiebreak below decides which is "the" list, and a bot whose
    # films outweigh its music hands us its film list as its master: we would
    # index the films, show them as that bot's whole catalogue, and report its
    # music as absent.
    #
    # Excluding it drops those films from the fetched copy rather than
    # merging them in, which is the same thing find_latest_list() does locally
    # with the album list. Reading both into one fetched list is a change to
    # what this function returns and to the size ceiling that guards it; it is
    # recorded in docs/FUTURE.md rather than smuggled in here.
    skip = ("-rar-", f"-{list_mod.VIDEO_LIST_MARKER.lower()}-")
    candidates = [p for p in txt_files
                  if not any(m in os.path.basename(p).lower() for m in skip)]
    if not candidates:
        candidates = txt_files

    if len(candidates) == 1:
        return candidates[0]

    # A member that vanished between the walk and here (an antivirus quarantine
    # mid-fetch is the realistic one) sorts last rather than taking the whole
    # fetch down: this function's caller documents it as never raising.
    #
    # The long_path() here is belt to the walk's braces and currently
    # redundant - every path in txt_files was joined onto the already-wrapped
    # root, so it arrives prefixed and the call is idempotent. It stays so the
    # two do not have to be reasoned about together: a later change that
    # unwraps the walk would otherwise reintroduce half the bug silently.
    def _size(path):
        try:
            return os.path.getsize(platform_compat.long_path(path))
        except OSError as err:
            print(f"[LIST-FETCH] Could not size {os.path.basename(path)!r} "
                  f"while picking the list file: {err}")
            return -1

    candidates.sort(key=_size, reverse=True)
    print(f"[LIST-FETCH] WARNING: {len(candidates)} candidate .txt files found "
          f"in {extract_dir!r}; picking the largest "
          f"({os.path.basename(candidates[0])}) as the master list - a "
          f"best-effort guess, not a confident match.")
    return candidates[0]


def _accept_plain_text_list(source_path, extract_dir):
    """Take a list that arrived as plain text, returning (path, reason).

    The archive guards it skips are all guards about ARCHIVES - member counts,
    traversal in member names, a compressed size that expands - and none of
    them has anything to say about a single file that is already on disk at a
    size we have measured. The one guard that does apply, the text-size
    ceiling, runs where it always did: on the file this returns, in the caller.

    Copied into the extraction directory rather than parsed where it landed,
    so everything downstream sees the same shape from both routes and the
    caller's cleanup covers both.
    """
    # IT STILL HAS TO LOOK LIKE A LIST. The zip route gets its plausibility
    # from the archive guards and _pick_list_file(); this route has neither, so
    # without a check here any file at all that is not a zip would be stored as
    # a bot's list - parsing to zero rows, reported as a successful fetch, and
    # answering every filter with nothing.
    #
    # The property is the one the parser needs: a request line. Read from a
    # BOUNDED prefix rather than the whole file, because the file may be
    # 128MB and the answer is in the first few lines - after a header and a
    # banner, which is itself capped at 8KB.
    try:
        with io.open(platform_compat.long_path(source_path), "r",
                     encoding="utf-8", errors="replace") as handle:
            head = handle.read(_PLAUSIBLE_LIST_PREFIX)
    except OSError as err:
        shutil.rmtree(platform_compat.long_path(extract_dir), ignore_errors=True)
        return None, f"could not read the fetched list: {err}"

    if not any(line.lstrip().startswith("!") for line in head.splitlines()):
        shutil.rmtree(platform_compat.long_path(extract_dir), ignore_errors=True)
        return None, ("the file is not a zip and holds no request lines, so it "
                      "is not a file list")

    try:
        destination = os.path.join(extract_dir, os.path.basename(source_path))
        shutil.copyfile(platform_compat.long_path(source_path),
                        platform_compat.long_path(destination))
    except OSError as err:
        shutil.rmtree(platform_compat.long_path(extract_dir), ignore_errors=True)
        return None, f"could not read the fetched list: {err}"
    return destination, None


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

    # #162 finding #10: the entry-count/size guards below all run on
    # zf.infolist(), which zipfile.ZipFile() has ALREADY eagerly built (one
    # ZipInfo per entry, plus a NameToInfo dict) by the time this code can see
    # it - the cost those guards exist to prevent is paid before they can
    # refuse anything. dcc_fetch.handle_incoming_offer() now refuses an
    # oversized "list" offer before ever connecting (MAX_FETCH_LIST_FILE_SIZE),
    # which is what actually prevents this; this is the belt to that braces -
    # a cheap check on the file already sitting on disk, before opening it,
    # in case that admission-time cap is ever bypassed or misconfigured.
    try:
        on_disk_size = os.path.getsize(platform_compat.long_path(zip_path))
    except OSError as err:
        return None, f"could not stat the fetched zip: {err}"
    list_zip_cap = int(getattr(config, "MAX_FETCH_LIST_FILE_SIZE", 10 * 1024 * 1024))
    if on_disk_size > list_zip_cap:
        shutil.rmtree(platform_compat.long_path(extract_dir), ignore_errors=True)
        return None, (f"fetched zip is {on_disk_size} bytes, more than "
                       f"MAX_FETCH_LIST_FILE_SIZE ({list_zip_cap}) - refusing "
                       f"to open it")

    # NOT EVERY LIST IS A ZIP. A bot that publishes its list as a plain .txt
    # sends exactly that, and this refused it with "extraction aborted: File
    # is not a zip file" - a real fetch, completed at 100%, thrown away at the
    # last step. update_list.py has published .txt as a LIST_FORMAT since #201;
    # there was never a reason to expect only archives back.
    #
    # Detected by CONTENT, not by the offered filename: the name comes from
    # the sending bot and a peer calling a zip "list.txt" must not skip the
    # archive guards. zipfile.is_zipfile() reads the file's own end-of-archive
    # record.
    if not zipfile.is_zipfile(platform_compat.long_path(zip_path)):
        return _accept_plain_text_list(zip_path, extract_dir)

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
        #
        # long_path()-wrapped like every other rmtree() in this function
        # (#222): this branch is the one a hand-crafted archive with long
        # member paths actually reaches, so on Windows it is also the one
        # most likely to be cleaning up a directory over the 260-character
        # limit - unwrapped, ignore_errors=True would swallow that failure
        # and leave the rejected, partially-extracted contents on disk
        # permanently, under FETCHED_FILES_DIR.
        shutil.rmtree(platform_compat.long_path(extract_dir), ignore_errors=True)
        return None, f"extraction aborted: {type(err).__name__}: {err}"

    return _pick_list_file(extract_dir), None


# ==========================================================================
# Keeping held lists current (#302).
# ==========================================================================

def _hours_to_seconds(hours):
    try:
        return max(0.0, float(hours) * 3600.0)
    except (TypeError, ValueError):
        return 0.0


def lists_worth_refetching(now=None):
    """The bots whose held list their own advert says has moved on.

    Returns a list of nicks, oldest fetch first, so a run that is capped takes
    the most stale ones. Empty when the feature is off, when nothing is held,
    or when nothing has changed.

    THE ADVERT DECIDES, not a timer. #286 already worked out what "moved on"
    means and why: their advert THEN against their advert NOW, date first and
    count second, because bots count differently and an off-by-a-few would
    mark a list permanently stale. Re-fetching on a timer alone would ask
    every bot for a list we already have, every interval, for ever - which is
    other people's bandwidth and other people's transfer slots.

    "unknown" is not "changed". A bot that publishes no date, or one whose
    advert we have not seen since starting, gives no evidence either way, and
    acting on no evidence is what makes an automatic feature untrustworthy.
    """
    import webserver

    if not getattr(config, "AUTO_REFETCH_LISTS", False):
        return []

    now = time.time() if now is None else now
    interval = _hours_to_seconds(getattr(config, "AUTO_REFETCH_INTERVAL_HOURS", 24))

    held = dict(getattr(config, "fetched_bot_lists", {}) or {})
    due = []
    for entry in held.values():
        if not isinstance(entry, dict):
            continue
        bot = str(entry.get("bot") or "").strip()
        if not bot:
            continue

        # NOT MORE OFTEN THAN THE INTERVAL, whatever the advert says. A bot
        # rebuilding its list hourly would otherwise be re-fetched hourly.
        fetched_at = entry.get("fetched_at") or 0
        if interval and (now - float(fetched_at or 0)) < interval:
            continue

        rows = [row for row in webserver.build_fetched_bot_list_summaries()
                if str(row.get("bot", "")).strip().lower() == bot.lower()]
        if not rows or rows[0].get("freshness") != "changed":
            continue
        due.append((float(fetched_at or 0), bot))

    due.sort()
    return [bot for _when, bot in due]


def refetch_due_lists(log=print, now=None):
    """Ask again for the held lists their own adverts say have changed.

    Returns the nicks actually enqueued. Bounded per run by
    AUTO_REFETCH_MAX_PER_RUN: a bot that has been offline for a month comes
    back to thirty stale lists, and asking all thirty at once is a burst of
    outbound requests nobody asked for - the rest are picked up next time
    round, oldest first.

    Goes through the SAME enqueue the dashboard's own Refresh uses, so the
    slot limits, the duplicate guard and the queue ceiling all apply exactly
    as they do to a fetch an operator started by hand.
    """
    import webserver

    due = lists_worth_refetching(now=now)
    if not due:
        return []

    try:
        cap = int(getattr(config, "AUTO_REFETCH_MAX_PER_RUN", 3))
    except (TypeError, ValueError):
        cap = 3
    if cap > 0:
        due = due[:cap]

    started = []
    for bot in due:
        status, result = webserver.build_list_fetch_enqueue_result({"bot": bot})
        if status == 200:
            started.append(bot)
            log(f"[LIST-FETCH] {bot}'s list has changed since we took our copy "
                f"- asking again automatically.")
        else:
            # Not an error worth stopping for: the usual reason is that a
            # fetch for that bot is already outstanding, which is the right
            # outcome and needs no announcement.
            log(f"[LIST-FETCH] Did not re-ask {bot}: "
                f"{result.get('error', 'refused')}")
    return started


def auto_refetch_worker(sleep=None):
    """The loop. Started from oserve.startup() when AUTO_REFETCH_LISTS is on.

    Deliberately its own thread and not a branch of the fetch dispatcher: that
    one runs every two seconds and only touches the queue, while this reads
    every held list and talks to webserver. Sharing it would make a slow
    read here delay every fetch promotion.
    """
    import time as time_mod

    naptime = sleep or (lambda seconds: time_mod.sleep(seconds))
    print("[LIST-FETCH] Automatic list refresh is on.")
    while True:
        try:
            refetch_due_lists()
        except Exception as err:
            print(f"[LIST-FETCH] Automatic refresh error: {err}")
        # A fixed hour between sweeps, not the configured interval: the
        # interval is how STALE a list may be before it is re-asked for, and
        # checking more often than that costs one pass over a dict.
        naptime(3600.0)



def process_fetched_list_zip(bot, zip_path):
    """Entry point, called by dcc_fetch.py once a request_type="list" fetch
    reaches 'complete'. Safely extracts `zip_path`, locates the master-list
    .txt inside it, and stores a REFERENCE to it - not its parsed contents -
    in config.fetched_bot_lists keyed by lowercased bot nick, REPLACING any
    previous entry for the same bot, per the operator's explicit
    "switchable, not accumulating" requirement.

    Issue #76, option 2: earlier versions of this function parsed the whole
    extracted list here and stored the resulting row list permanently in
    memory - alongside the byte-size cap above, that meant the single largest
    cost of a fetched list (every "!" line, forever, until the next fetch or
    a process restart) was paid once at fetch time and then never freed. This
    still does ONE parse+dedup pass below (to prove the file is genuinely
    parseable - a file that merely LOOKS like a valid master list but is
    actually garbage must still be caught here, same as before - and to get
    an accurate post-dedup row count for the dashboard switcher) but keeps
    only that count afterward. get_fetched_bot_page() below re-parses
    `list_path` fresh on every later request, exactly the way
    webserver.build_filelists_payload() has always done for THIS bot's own
    list - nothing about a fetched list is retained in memory between views
    except this small summary dict.

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

    get_fetched_bot_page() below acquires this SAME lock around its read, for
    exactly the reason this docstring already gives for writers: extraction
    reuses the same on-disk list_path a concurrent read could be parsing
    (list_extract_dir() keys on the bot nick, not on any per-fetch id), and
    rewrites it via rmtree+open("wb") rather than write-then-rename. Without
    the read side sharing this lock, a same-bot re-fetch could race a read
    into a torn, partially-rewritten file - not an exception, a silently
    wrong `total` and row set. The bounded stall this adds to a read (waiting
    out an in-progress fetch, itself already a background step measured in
    seconds) is the same accepted tradeoff as above, extended to reads.
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

    # Issue #76: nothing before this point bounds the number of LINES the
    # extracted text file contains, only the zip's own byte/member counts -
    # and every line becomes a permanently-retained dict below. Checked here,
    # on the real extracted size, before a single line is parsed.
    try:
        text_size = os.path.getsize(platform_compat.long_path(list_path))
    except OSError as err:
        reason = f"could not stat the extracted list file: {err}"
        print(f"[LIST-FETCH] Rejected list zip from {bot}: {reason}")
        shutil.rmtree(platform_compat.long_path(extract_dir), ignore_errors=True)
        return False, reason
    if text_size > max_list_text_size():
        reason = (f"the extracted list is {text_size} bytes, over the "
                  f"{max_list_text_size()}-byte ceiling for a real master "
                  f"list (raise MAX_LIST_TEXT_SIZE if your peers publish "
                  f"bigger ones)")
        print(f"[LIST-FETCH] Rejected list zip from {bot}: {reason}")
        shutil.rmtree(platform_compat.long_path(extract_dir), ignore_errors=True)
        return False, reason

    # list.py opens this path directly and does not wrap it itself, so the
    # prefix goes on here - at the point of use, the same idiom dcc.py uses.
    # Without it, widening the write path above would only move the failure:
    # extraction would succeed and the parse would raise FileNotFoundError,
    # outside the extraction guard, on a file that is plainly there.
    #
    # This is the ONE courtesy parse - see process_fetched_list_zip()'s
    # docstring. `rows` was only ever used for its length; nothing keeps a
    # reference to it (or to `entries`) once the count is taken and the index
    # below has been written, so both are free to be garbage-collected as soon
    # as this function returns.
    entries, _total = list_mod.find_matching_entries(
        [], limit=None, list_path=platform_compat.long_path(list_path))
    rows = list_mod.entries_to_filelist_rows(entries, str(bot).strip())
    entry_count = len(rows)

    # THE SEARCH INDEX (#133 step 5), written from the parse that was already
    # happening. The dashboard's filter bar searches every held list at once,
    # and re-reading the files to do it is out of reach rather than merely
    # slow - #133 measured ten held lists at about eleven seconds a keystroke.
    #
    # Deliberately here and not in a pass of its own: this walk of the whole
    # file is a cost already paid, and the rows are about to be discarded.
    #
    # Best-effort by design. index_bot_list() swallows its own failures and
    # returns 0, because an index that cannot be written costs the filter bar
    # and nothing else - the list is on disk, the browser still pages it, and
    # the next fetch tries again. A fetch that succeeded must not be reported
    # as failed over it.
    indexed = list_index.index_bot_list(str(bot).strip(), rows)
    if indexed != entry_count:
        print(f"[LIST-FETCH] {bot}'s list was stored but only {indexed} of "
              f"{entry_count} entries reached the search index; the "
              f"cross-list filter may not show it until the next fetch.")

    store = _ensure_fetched_bot_lists()
    store[str(bot).strip().lower()] = {
        "bot": str(bot).strip(),
        "fetched_at": time.time(),
        # The plain, already-absolute path _pick_list_file() returned -
        # NOT long_path()-wrapped here. Every reader of this field (the parse
        # call just above, and get_fetched_bot_page() below) wraps it with
        # platform_compat.long_path() itself, at the point of use - the same
        # "wrap on use, not on store" idiom the rest of this module already
        # follows for `list_path`/`extract_dir`. Storing the plain path keeps
        # it portable to whatever wraps it next, rather than baking in
        # Windows' "\\\\?\\" prefix (a no-op on Linux, but still a form this
        # value should not permanently commit to).
        "list_path": list_path,
        "entry_count": entry_count,
        "source_zip": os.path.basename(zip_path),
        # WHAT THEY WERE ADVERTISING WHEN WE TOOK THIS COPY (#133).
        #
        # Freshness is "their advert then vs their advert now", never "their
        # advert vs our parsed row count": bots count differently - some
        # include the header lines, some count album rows separately - and an
        # off-by-a-few would leave a list permanently marked stale with
        # nothing actually wrong. Comparing a bot against its own earlier
        # claim has no such problem.
        #
        # Absent when they were not in the registry at fetch time (we can
        # fetch from a bot whose advert we have not seen yet), and that
        # absence is the honest answer rather than a zero - see
        # _advert_snapshot().
        "advert_when_fetched": _advert_snapshot(bot),
    }

    # Persisted immediately, not on a timer: unlike the bot registry (updated
    # on every advert, throttled for exactly that reason), a list fetch
    # completing is already an infrequent, deliberate event. Without this, the
    # extracted files under FETCHED_FILES_DIR survived a restart untouched
    # while the daemon's memory of which bots they belonged to did not, and
    # the File Lists switcher went blank until the next fetch.
    db.save_fetched_bot_lists(dict(store))

    print(f"[LIST-FETCH] Stored a reference to {entry_count} entries from "
          f"{bot}'s fetched list ({os.path.basename(list_path)}) - parsed "
          f"fresh from disk on each view, not retained in memory.")
    return True, None


def get_fetched_bot_page(entry, offset, limit):
    """Issue #76, option 2's on-demand reader: given one
    config.fetched_bot_lists[...] entry (the dict process_fetched_list_zip()
    above builds - "bot", "fetched_at", "list_path", "entry_count",
    "source_zip"), re-parse its `list_path` FRESH via
    list.find_matching_entries() + list.entries_to_filelist_rows() - no
    caching between calls, exactly like webserver.build_filelists_payload()
    already does for this bot's own list - dedup, and return one page of the
    result.

    Returns (page_rows, total_folders, total_rows, error): `error` is None on
    success. Four values, not the three this said until #232 - a new caller
    written from the docstring alone would have unpacked it wrong.
    otherwise a short, human-readable string (e.g. the file having gone
    missing from disk since the fetch - an operator manually clearing
    data/fetched/, or some other bug entirely) and `page_rows`/`total` are
    ([], 0). Never raises - the caller (webserver.build_fetched_bot_list_payload)
    turns a non-None `error` into an HTTP error response, the same "pure
    logic returns a result, the route just serialises it" shape as every
    other build_*_payload() function in webserver.py.

    `offset`/`limit` are applied to the deduped row list, after re-parsing -
    the same slicing webserver.py applies to this bot's own list, so the two
    endpoints share one pagination contract even though only one of them
    shares this module's parsing code.

    Held under the same module-wide _lock() process_fetched_list_zip() uses
    around its own extract->parse->store sequence, for the read (the
    existence check and the parse below) - not just the dict lookups, which
    are plain, fast, GIL-atomic reads and stay unlocked either way. Without
    this, a same-bot re-fetch racing a read here is a genuine torn-read
    hazard, not a hypothetical one: _extract_and_locate_list_file() reuses the
    exact same list_path (list_extract_dir() keys only on the bot nick) and
    rewrites it via rmtree+open("wb") rather than write-then-rename, so a read
    that lands mid-rewrite can see a truncated file and silently return a
    wrong `total` instead of raising - no exception, no "file missing", just
    a plausible-looking short page. Taking the same lock here makes such a
    read block briefly until the in-progress fetch finishes, instead of
    reading a half-written file - the same "one thing touches the on-disk
    representation at a time" guarantee process_fetched_list_zip() already
    gives writers, extended to readers.

    No deadlock risk: this is the only other place in the codebase that
    acquires this lock, dcc_fetch.py's call into process_fetched_list_zip()
    happens with no other lock held (see _handle_completed_list_fetch()'s
    docstring), and nothing this function calls (list_mod.find_matching_entries,
    entries_to_filelist_rows) ever acquires config.fetched_bot_lists_lock
    itself - so there is no cycle and no re-entrant acquisition of this
    plain, non-reentrant Lock.
    """
    bot = entry.get("bot", "?")
    list_path = entry.get("list_path")
    if not list_path:
        reason = f"no list file is on record for {bot}'s fetched list"
        print(f"[LIST-FETCH] {reason}.")
        return [], 0, 0, reason

    resolved_path = platform_compat.long_path(list_path)
    with _lock():
        if not os.path.exists(resolved_path):
            reason = (f"{bot}'s fetched list file is no longer on disk "
                       f"({os.path.basename(list_path)!r} is missing - it may have "
                       f"been cleared manually since the fetch); fetch the list again")
            print(f"[LIST-FETCH] {reason}.")
            return [], 0, 0, reason

        try:
            entries, _total = list_mod.find_matching_entries(
                [], limit=None, list_path=resolved_path)
            rows = list_mod.entries_to_filelist_rows(entries, bot)
        except OSError as err:
            # Caught here, not left to propagate into the Flask route: a file
            # that exists (the check above passed) but became unreadable between
            # that check and this open() - permissions changed, a network mount
            # dropped - is the same class of "gone since the fetch" problem as
            # the missing-file case above, just caught a moment later.
            reason = f"could not read {bot}'s fetched list file: {err}"
            print(f"[LIST-FETCH] {reason}")
            return [], 0, 0, reason

    # Grouped and paged by FOLDER, the same contract as this bot's own list -
    # see list.FILELISTS_MAX_PAGE_ROWS for why a folder count alone is not a
    # sufficient bound.
    groups = list_mod.group_rows_by_folder(rows)
    page, total_folders, total_rows = list_mod.page_folder_groups(
        groups, offset, limit, max_rows=list_mod.FILELISTS_MAX_PAGE_ROWS)
    return page, total_folders, total_rows, None
