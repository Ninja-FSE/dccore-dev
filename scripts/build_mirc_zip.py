#!/usr/bin/env python3
"""Build dccore-mirc.zip: the mIRC script and the folders it loads (#1412).

Installing the script used to mean finding scripts/mirc/ in the repository and
copying dccore.mrc with its dcx and dll folders - and not the build files that
sit beside them. This packs exactly what goes into mIRC, and is attached to
each GitHub release under one fixed name, so

    https://github.com/Ninja-FSE/dccore/releases/latest/download/dccore-mirc.zip

is always the newest script (docs/PUBLIC-REPO-WORKFLOW.md, the release steps).

WHAT GOES IN is a rule, not a list: dccore.mrc, and everything under dcx/ and
dll/ except the dll sources and the icon preview (SKIPPED). A file added to one
of those folders ships without anyone remembering to add it here.

NO TOP FOLDER. The script finds dcx\\ and dll\\ beside itself ($scriptdir), and
keeps its settings and token in dccore.ini beside itself too. Extracted where
dccore.mrc already is, an update overwrites the script and its folders and
leaves dccore.ini alone; a first install extracts into the mIRC folder.

THE SAME BYTES EVERY TIME: members in sorted order, one fixed timestamp and
mode, so a rebuild of the same tree gives the same file and the same SHA-256.

Usage:
    python3 scripts/build_mirc_zip.py [--source scripts/mirc] [--out dccore-mirc.zip]
"""

import argparse
import hashlib
import io
import os
import re
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO_ROOT, "scripts", "mirc")
ZIP_NAME = "dccore-mirc.zip"
README_NAME = "dccore-mirc-README.txt"

# What is installed: the script, and the two folders it loads from.
SCRIPT = "dccore.mrc"
FOLDERS = ("dcx", "dll")
# Inside those folders, what is for building them, not for running them.
SKIPPED = ("dll/src/", "dll/icons-preview.png")

# One timestamp for every member (the earliest a zip can hold), so the bytes
# do not depend on when the files were checked out.
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o644


def script_version(source=SOURCE):
    """dccore.ver of the script in `source`: what the README says it is."""
    with io.open(os.path.join(source, SCRIPT), encoding="latin-1") as handle:
        for line in handle:
            found = re.match(r"\s*alias\s+dccore\.ver\s*\{\s*return\s+(\S+)\s*\}", line)
            if found:
                return found.group(1)
    raise SystemExit(f"build_mirc_zip: no dccore.ver in {os.path.join(source, SCRIPT)}")


def members(source=SOURCE):
    """{name in the zip: path on disk}, by the rule above."""
    found = {SCRIPT: os.path.join(source, SCRIPT)}
    if not os.path.isfile(found[SCRIPT]):
        raise SystemExit(f"build_mirc_zip: {found[SCRIPT]} is missing")
    for folder in FOLDERS:
        top = os.path.join(source, folder)
        if not os.path.isdir(top):
            raise SystemExit(f"build_mirc_zip: {top} is missing")
        for root, dirs, files in os.walk(top):
            dirs[:] = [name for name in dirs if name != "__pycache__"]
            for name in files:
                path = os.path.join(root, name)
                inside = os.path.relpath(path, source).replace(os.sep, "/")
                if inside.startswith(SKIPPED) or inside in SKIPPED:
                    continue
                found[inside] = path
    return found


def readme(version):
    """The few lines a person needs, CRLF so Notepad shows them as lines."""
    lines = [
        f"DCCore - the mIRC window script, dccore.mrc {version}",
        "",
        "FIRST TIME",
        "  1. Extract everything here into your mIRC folder (in mIRC: //echo -a $mircdir).",
        "     dccore.mrc goes in it, with the dcx and dll folders beside it.",
        "  2. In mIRC:  /load -rs dccore.mrc",
        "  3. Then:     /dccore pair <your bot's nick>",
        "     and type the bot's admin password in the window, once.",
        "",
        "UPDATING",
        "  Extract everything over the old files, where dccore.mrc already is, then in mIRC:",
        "     /reload -rs dccore.mrc",
        "  Your settings and the bot's token are in dccore.ini beside the script;",
        "  this zip has no dccore.ini, so they are kept.",
        "",
        "WHAT IS IN HERE",
        "  dccore.mrc   the script",
        "  dcx\\         DCX, which draws the control panel (licence inside)",
        "  dll\\         the dark theme and the menu icons",
        "  Without the two folders the script still works, with plain windows.",
        "",
        "Everything else: https://ninja-fse.github.io/dccore/mirc.html",
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _info(name):
    info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = FILE_MODE << 16
    info.create_system = 0
    return info


def build(out, source=SOURCE):
    """Write the zip to `out`; returns (sha256 hex, number of members)."""
    files = members(source)
    contents = {}
    for name, path in files.items():
        with open(path, "rb") as handle:
            contents[name] = handle.read()
    contents[README_NAME] = readme(script_version(source))
    folder = os.path.dirname(os.path.abspath(out))
    os.makedirs(folder, exist_ok=True)
    temp = out + ".tmp"
    with zipfile.ZipFile(temp, "w") as archive:
        for name in sorted(contents):
            archive.writestr(_info(name), contents[name])
    os.replace(temp, out)
    with open(out, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    return digest, len(contents)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build dccore-mirc.zip, the mIRC script to install.")
    parser.add_argument("--source", default=SOURCE, help="the scripts/mirc folder to pack")
    parser.add_argument("--out", default=os.path.join(REPO_ROOT, "dist", ZIP_NAME), help="where to write the zip")
    args = parser.parse_args(argv)
    digest, count = build(args.out, args.source)
    print(f"build_mirc_zip: {args.out} - dccore.mrc {script_version(args.source)}, "
          f"{count} files, sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
