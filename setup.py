# setup.py - guided first-run configuration for a new DCCore install.
"""Answers settings_file.REQUIRED (NICKNAME, CHANNEL, ADMIN_NICK,
FILE_DIRECTORY) plus SERVER and the admin console password, then writes them
where they actually belong, so a fresh checkout can go from "just cloned" to
"the daemon will actually boot" in one guided pass instead of hand-editing
two files by comparing them against admin_config.py.sample/
settings.conf.sample.

#170's RFC (issue #170's discussion) asked for exactly six fields here, no
more: Nickname, IRC server, channel(s), admin nick, admin password, and
FILE_DIRECTORY - the last one added after review, since it is also in
settings_file.REQUIRED and oserve.startup() will not boot without it either,
so leaving it out would hand back a freshly-configured install that still
cannot start. Everything else this daemon can be tuned with - the ~50 other
settings.conf.sample entries, the web dashboard's own Settings page - is
deliberately NOT asked here. This script's only job is "can it boot at all
and can I log into the console", not "configure everything".

WHERE EACH ANSWER GOES

    NICKNAME, SERVER, CHANNEL, ADMIN_NICK, FILE_DIRECTORY   -> settings.conf
    ADMIN_PASSWORD_HASH                                     -> admin_config.py

The first five are ordinary operational settings - settings_file.py already
treats them as primitives any operator can change from the CLI or the
dashboard's Settings page, so settings.conf (the mechanism BOTH of those
already write to) is where they belong, via settings_file.save() - the exact
same read-modify-write-and-verify path the dashboard's own settings route
uses, not a hand-rolled file write. ADMIN_PASSWORD_HASH is different: a
credential, never meant to be edited from a web form running under the
password it protects, so it goes to admin_config.py (Python, gitignored)
instead - matching what running `python3 adminchat.py` directly has always
told an operator to do with the hash it prints.

WHY THE PASSWORD STEP REUSES adminchat.py RATHER THAN REPROMPTING

adminchat.make_password_hash() and adminchat._read_password() are imported
and called directly, in-process - not by shelling out to
`python3 adminchat.py` as a subprocess and parsing its printed output, which
would mean two ways to get a password into this process (once through this
script's own prompt, once through whatever adminchat.py's subprocess did
with its inherited stdin) and a fragile text-scrape of a line meant for a
human to read. Importing the same function this project already uses everywhere
else that needs this hash is the "one definition, not two" pattern this
codebase leans on repeatedly - PRESERVE_RUNTIME, the shared PRIVMSG/NOTICE
parsers, the rar-archive-name sanitiser, settings_file.REQUIRED itself.
_read_password() already handles the no-real-terminal case (falls back to
plain, announced stdin) on its own, so this script does not need to.

RE-RUNNING THIS SCRIPT IS SAFE

Every prompt shows the CURRENTLY CONFIGURED value (from whatever
admin_config.py/settings.conf already resolve to) as its default - press
Enter to keep it. An operator who already has a working install and just
wants to change one thing can run this again rather than hand-editing
settings.conf, and nothing here can silently revert an already-correct
answer to a blank one.
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import adminchat  # noqa: E402
import defaults as config  # noqa: E402
import settings_file  # noqa: E402
import setup_check  # noqa: E402 - reuses its UPSTREAM_* tuples, not its checks


def _ask(prompt, default=None, allow_blank=False):
    """One prompt, with `default` shown and used on a bare Enter.

    `allow_blank` is only True for SERVER, whose empty answer is itself
    meaningful ("no explicit choice" - config.py's own real, working default
    already covers everyone). Every other field here is in
    settings_file.REQUIRED and refuses to boot on blank, so re-prompting
    rather than accepting an empty string is what actually gets the operator
    to a bootable daemon, instead of a technically-finished setup run that
    still fails at oserve.startup() with no further guidance.
    """
    suffix = f" [{default}]" if default else ""
    while True:
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw:
            if default:
                return default
            if allow_blank:
                return ""
            print("  This can't be blank - it's required before the daemon will start.")
            continue
        return raw


def _warn_if_upstream(value, upstream, field_name, example):
    """Non-blocking - scripts/setup_check.py's own pre-flight (and, for
    NICKNAME/CHANNEL/ADMIN_NICK, oserve.startup()'s REQUIRED gate) still
    catch this for real. This is only the earliest possible moment to say
    so, while the operator is still looking at the prompt that caused it."""
    if str(value).strip().lower() in upstream:
        print(f"  WARNING: {field_name}={value!r} is the upstream operator's "
              f"own value, not yours. {example}")


def _current(name, fallback=""):
    """The currently resolved value for `name`, or `fallback` if it is still
    unset (settings_file.REQUIRED ships these blank - see config.py's own
    comment) - what an already-partially-configured install already has,
    shown back as this prompt's own default so re-running never silently
    reverts a real answer to a placeholder."""
    value = getattr(config, name, None)
    return value if value else fallback


def collect_answers():
    print("=" * 68)
    print("  DCCore first-run setup")
    print("=" * 68)
    print()
    print("Six questions, then the daemon should be able to start. Anything")
    print("else - the ~50 other settings, admin console hostmasks, the web")
    print("dashboard - is configured afterwards, from settings.conf, the")
    print("dashboard's own Settings page, or admin_config.py directly.")
    print()

    nickname = _ask("Nickname", default=_current("NICKNAME"))
    _warn_if_upstream(nickname, setup_check.UPSTREAM_NICKS, "NICKNAME",
                      "It will collide with the production bot.")

    server = _ask("IRC server", default=_current("SERVER", "irc.undernet.org"),
                  allow_blank=True)

    channel = _ask("Channel(s), comma-separated", default=_current("CHANNEL"))
    shared = [c for c in setup_check.UPSTREAM_CHANNELS
             if c in channel.lower()]
    if shared:
        print(f"  WARNING: {', '.join(shared)} belongs to the production bot. "
              f"A second bot with your own nick is fine there; a clone of the "
              f"upstream nick in it is what gets people banned.")

    admin_nick = _ask("Admin nick (who may run !ban/!rehash/!update/!clearqueue)",
                      default=_current("ADMIN_NICK"))
    _warn_if_upstream(admin_nick, setup_check.UPSTREAM_ADMIN_NICKS, "ADMIN_NICK",
                      "That grants the upstream operator's nick full control "
                      "of your bot.")

    print()
    print("Admin console password (for the DCC CHAT console - see")
    print("docs/ADMIN-CONSOLE.md). Same prompt as running adminchat.py by itself.")
    while True:
        first = adminchat._read_password("Password: ")
        second = adminchat._read_password("Again: ")
        if not first:
            print("  Empty password refused.")
            continue
        if first != second:
            print("  Passwords did not match - try again.")
            continue
        break
    password_hash = adminchat.make_password_hash(first)

    print()
    file_directory = _ask("Music directory (full path)",
                          default=_current("FILE_DIRECTORY"))
    while not os.path.isdir(file_directory):
        print(f"  {file_directory!r} does not exist.")
        create = input("  Create it now? [y/N]: ").strip().lower()
        if create in ("y", "yes"):
            try:
                os.makedirs(file_directory, exist_ok=True)
            except OSError as err:
                print(f"  Could not create it: {err}")
            else:
                break
        else:
            file_directory = _ask("Music directory (full path)", default=file_directory)

    return {
        "NICKNAME": nickname,
        "SERVER": server,
        "CHANNEL": channel,
        "ADMIN_NICK": admin_nick,
        "FILE_DIRECTORY": file_directory,
    }, password_hash


def write_settings_conf(answers, path=None):
    """Everything except the password, through settings_file.save() - the
    exact same read-modify-write-and-verify path the dashboard's Settings
    page and CLI edits already use, not a hand-rolled write. SERVER is
    included only when the operator gave an explicit, non-blank answer -
    config.py's own real default already covers everyone who did not.
    `path` overrides settings_file's own default location - tests use it,
    real runs never pass it."""
    changes = {name: value for name, value in answers.items()
              if value or name != "SERVER"}
    # save() already logs its own "[CONFIG] Wrote N setting(s)..." line
    # (log=print by default) - nothing more to print here.
    return settings_file.save(vars(config), changes, path=path)


def build_admin_config_text(existing_text, password_hash):
    """The pure edit: `existing_text` (whatever admin_config.py, or failing
    that admin_config.py.sample, or failing that a bare fresh comment,
    already reads as - see write_admin_config_password()) with
    ADMIN_PASSWORD_HASH set to `password_hash`, replacing an existing
    assignment in place or appending a new one. Pulled out as a pure
    function, no file I/O, so the text transformation itself is directly
    testable - matching this codebase's own settled pattern for exactly
    this reason (rehash_nick_change_line(), subprocess_failure_message(),
    and others all exist for the identical reason).

    Deliberately narrow: only ADMIN_PASSWORD_HASH is ever touched. A hand-
    edited ADMIN_HOSTMASKS (or anything else already in the file) survives
    untouched either way.
    """
    new_line = f'ADMIN_PASSWORD_HASH = "{password_hash}"'
    pattern = re.compile(r'^ADMIN_PASSWORD_HASH\s*=.*$', re.MULTILINE)
    if pattern.search(existing_text):
        return pattern.sub(new_line, existing_text, count=1)
    text = existing_text
    if text and not text.endswith("\n"):
        text += "\n"
    return text + new_line + "\n"


def write_admin_config_password(password_hash, path=None, sample_path=None):
    """Only ADMIN_PASSWORD_HASH - never overwrites anything else a hand-
    edited admin_config.py already has (ADMIN_HOSTMASKS, most notably).
    Replaces an existing ADMIN_PASSWORD_HASH line in place if this is a
    re-run; appends a new one otherwise, creating the file from
    admin_config.py.sample's own template if it does not exist yet at all.
    `path`/`sample_path` override the real repo locations - tests use them,
    real runs never pass them.
    """
    path = path or os.path.join(REPO_ROOT, "admin_config.py")
    sample_path = sample_path or os.path.join(REPO_ROOT, "admin_config.py.sample")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            existing_text = handle.read()
    elif os.path.exists(sample_path):
        with open(sample_path, "r", encoding="utf-8") as handle:
            existing_text = handle.read()
    else:
        existing_text = "# admin_config.py - created by setup.py\n"

    text = build_admin_config_text(existing_text, password_hash)

    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"[SETUP] Wrote ADMIN_PASSWORD_HASH to {os.path.basename(path)}.")


def main():
    answers, password_hash = collect_answers()
    write_settings_conf(answers)
    write_admin_config_password(password_hash)

    print()
    print("=" * 68)
    print("  Setup complete.")
    print("=" * 68)
    print()
    print("Verify it before the first real start:")
    print("    ./scripts/linux/start-dccore.sh check      (Linux)")
    print("    scripts\\windows\\start-dccore.bat check     (Windows)")
    print()
    print("Then start the daemon the same way, without \"check\".")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled - nothing was written past what already "
              "completed above.")
        sys.exit(1)
