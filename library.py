"""The library: the ordered set of folders this bot serves from.

DCCore has always served one directory. OmenServe has always served a list of
them, and #164 is that gap. This module is the first step: one place that
answers "which folders, in what order", so the rest of the daemon stops asking
`config.FILE_DIRECTORY` directly.

Nothing here changes behaviour yet. With no folder file on disk - which is
every install today - `folders()` returns exactly one entry built from
FILE_DIRECTORY, so every caller sees what it saw before. The design and the
decisions behind it are on #164.

WHY AN ACCESSOR RATHER THAN A LIST-SHAPED SETTING

FILE_DIRECTORY has 54 references across ten modules, concentrated in dcc.py
(16) and update_list.py (9). Teaching each of them about a list of folders
would mean touching all 54 again when multi-list arrives and the folder set
moves inside a list. Funnelled through here, that later change rebinds one
accessor instead.

WHY A FILE RATHER THAN settings.conf

settings_file.py refuses any list entry containing a comma, because list
settings serialise as ", ".join(...) - and music paths routinely contain one
("D:\\Rock, Metal"). It strips leading and trailing spaces too. So the folder
list needs its own file, and JSON for the same reason KNOWN_BOTS_FILE gives:
the record has more than one field and will grow.

The file is an OVERRIDE, not a replacement. Absent, FILE_DIRECTORY is the
single folder. That means an upgrade writes nothing and migrates nothing; the
file appears the first time an operator saves folders from the dashboard.
"""

import collections
import io
import json
import os
import re
import tempfile

import defaults as config
import platform_compat


# One served folder: a label and a real path on this machine.
#
# A named tuple rather than a dict so a mistyped attribute is an AttributeError
# here, rather than a None quietly reaching a path join and producing a path
# that looks plausible.
Folder = collections.namedtuple("Folder", ("name", "path"))

# A leading drive specifier: "C:", "D:Music". Matched literally rather than
# with os.path.splitdrive() because a label made on Linux is still read on
# Windows - see problems(). Same pattern list.py uses on heading components.
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


def folders_file():
    """Where the folder list lives.

    Resolved per call, not once at import: it is a config value and !rehash
    reloads config, so a path baked in at import would keep pointing at the old
    location for the life of the process. Same reasoning as list.py's own
    size_file_path().
    """
    return getattr(config, "LIBRARY_FOLDERS_FILE",
                   os.path.join("data", "library_folders.json"))


def default_label(path):
    """The label a folder gets when the operator does not choose one.

    Its own basename: "D:\\Flac" becomes "Flac". A path ending in a separator
    or naming a drive root has no basename to take, and falls back to the
    drive letter or "library" rather than to an empty label - an empty one
    would produce a doubled separator in every path built from it.
    """
    cleaned = str(path or "").replace("/", os.sep).rstrip(os.sep + "/")
    base = os.path.basename(cleaned)
    if base:
        return base
    drive = os.path.splitdrive(cleaned)[0].rstrip(":")
    return drive or "library"


def _normalise(path):
    """A path in the one form comparisons here are made in.

    normcase for Windows' case-insensitivity, abspath so a relative
    FILE_DIRECTORY and an absolute one compare equal, and no realpath: this
    runs on operator input at save time, where resolving symlinks would make
    the error message name a path the operator never typed.
    """
    return os.path.normcase(os.path.abspath(str(path or "")))


def is_inside(base, path):
    """True if `path` is `base` or sits underneath it.

    Compared with a separator boundary rather than a plain startswith, for the
    reason dcc.is_safe_path() gives about the same trap: "/srv/library-backup"
    begins with "/srv/library" as a string and is not inside it.

    This is CONFIG validation, not the request-time security gate. That gate is
    dcc.is_safe_path(), which additionally resolves symlinks, and it must stay
    the one used on a request path. The two are separate today only because
    dcc.py imports list, announce, db and runtime, and importing it from here
    would cycle - #164's step 3 touches that resolution anyway and is the place
    to give both one implementation.
    """
    base_n = _normalise(base)
    path_n = _normalise(path)
    if not base_n or not path_n:
        return False
    return path_n == base_n or path_n.startswith(base_n + os.sep)


def problems(entries):
    """Every reason `entries` could not be served, as operator-readable lines.

    Returns [] when the set is usable. Each message names the specific other
    entry it conflicts with, because "invalid folder list" tells an operator
    with eight folders nothing about which two to look at.

    Deliberately NOT checked here: whether a folder is reachable right now. A
    network share that is down is a scan-time condition - update_list skips it
    with a warning and builds from the rest - not a reason to refuse the whole
    configuration and lock the operator out of editing it.
    """
    found = []
    seen_paths = {}
    seen_labels = {}

    for index, entry in enumerate(entries):
        position = index + 1
        name = str(getattr(entry, "name", "") or "").strip()
        path = str(getattr(entry, "path", "") or "").strip()

        if not path:
            found.append(f"folder {position}: no path given.")
            continue

        if not name:
            found.append(f"{path}: no label. It becomes part of every path in "
                         f"the list, so it cannot be blank.")
        else:
            # Both separators and the drive pattern are checked LITERALLY, not
            # through os.path.splitdrive() or os.sep. Those follow the host's
            # own rules, and on Linux a backslash is an ordinary filename
            # character and "C:" is an ordinary directory name - so a label
            # generated there would pass, then be read as a drive specifier by
            # every Windows client that pastes the path back. The label travels
            # further than the machine that made it.
            if name in (".", "..") or any(
                    sep in name for sep in ("/", "\\")) or _DRIVE_PREFIX_RE.match(name):
                found.append(
                    f"{path}: the label {name!r} must be a single folder name "
                    f"- no slashes, no \"..\", no drive letter. It is written "
                    f"into paths users copy back.")
            key = name.lower()
            if key in seen_labels:
                found.append(
                    f"{path}: the label {name!r} is already used by "
                    f"{seen_labels[key]!r}. Labels are what tell two folders "
                    f"apart in the list, so they have to differ.")
            else:
                seen_labels[key] = path

        if not os.path.isdir(platform_compat.long_path(path)):
            found.append(f"{path}: not a folder on this machine.")

        norm = _normalise(path)
        if norm in seen_paths:
            found.append(f"{path}: already listed as entry {seen_paths[norm]}.")
            continue
        seen_paths[norm] = position

        for other in entries[:index]:
            other_path = str(getattr(other, "path", "") or "").strip()
            if not other_path or _normalise(other_path) == norm:
                continue
            if is_inside(other_path, path):
                found.append(
                    f"{path}: sits inside {other_path}, which is already "
                    f"served. Every file under it would be listed twice.")
            elif is_inside(path, other_path):
                found.append(
                    f"{path}: contains {other_path}, which is already served. "
                    f"Every file under that one would be listed twice.")

    return found


def load_folders(path=None):
    """The folder list as stored, or None if there is no usable file.

    None rather than [] on any problem, and the difference matters: [] would
    read as "the operator configured no folders" and serve nothing, where the
    truth is "we could not read the file", and folders() then falls back to
    FILE_DIRECTORY exactly as it does on a fresh install.
    """
    target = folders_file() if path is None else path
    try:
        with io.open(platform_compat.long_path(target), encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return None

    if not isinstance(raw, list):
        print(f"[LIBRARY] {target} does not contain a list of folders; "
              f"falling back to FILE_DIRECTORY.")
        return None

    entries = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        folder_path = str(item.get("path", "") or "").strip()
        if not folder_path:
            continue
        label = str(item.get("name", "") or "").strip() or default_label(folder_path)
        entries.append(Folder(label, folder_path))

    return entries or None


def save_folders(entries, path=None):
    """Write the folder list, refusing a set that could not be served.

    Raises ValueError naming every problem at once rather than the first, so
    an operator fixing three things is told about three things.

    Written to a temporary file and renamed, through the same
    replace_with_retry() every other file in this project uses: a half-written
    folder list read at startup would serve part of a library with no
    indication anything was wrong.
    """
    entries = [Folder(getattr(e, "name", "") or default_label(getattr(e, "path", "")),
                      getattr(e, "path", "")) for e in entries]

    found = problems(entries)
    if found:
        raise ValueError("\n".join(found))

    target = folders_file() if path is None else path
    directory = os.path.dirname(os.path.abspath(target)) or "."
    os.makedirs(platform_compat.long_path(directory), exist_ok=True)

    payload = json.dumps([{"name": e.name, "path": e.path} for e in entries],
                         indent=2, ensure_ascii=False)

    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=directory,
        prefix=".library-", suffix=".tmp", delete=False)
    try:
        with handle:
            handle.write(payload + "\n")
        platform_compat.replace_with_retry(handle.name, target)
    except BaseException:
        try:
            os.remove(handle.name)
        except OSError:
            pass
        raise

    return entries


def folders():
    """Every folder this bot serves, in the order they are served.

    The order is the operator's: it decides the order the list is built in,
    and which folder wins when the same relative path exists in two of them.

    With no folder file - every install today - this is one entry built from
    FILE_DIRECTORY, so callers see exactly what they saw before this module
    existed. An unset FILE_DIRECTORY gives [], which is the honest answer to
    "which folders are configured" on an install that has not chosen one yet;
    callers already handle that case, because FILE_DIRECTORY is deliberately
    allowed to be blank until the dashboard sets it.
    """
    stored = load_folders()
    if stored:
        return stored

    single = str(getattr(config, "FILE_DIRECTORY", "") or "").strip()
    if not single:
        return []
    return [Folder(default_label(single), single)]


def folder_paths():
    """Just the paths, in order. For callers that have no use for labels."""
    return [entry.path for entry in folders()]


def folder_for_label(label):
    """The folder a list path's first component names, or None.

    Matched case-insensitively: the label is written into paths that users
    copy, paste and retype, and a request that differs only in case is the
    same request.
    """
    wanted = str(label or "").strip().lower()
    if not wanted:
        return None
    for entry in folders():
        if entry.name.lower() == wanted:
            return entry
    return None
