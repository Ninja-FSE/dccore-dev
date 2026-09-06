"""Commands to send to the server as soon as we are registered.

WHAT THIS IS FOR

Every network wants something different done before you are really "on" it.
Undernet wants you to log in to X and take `+x` so your host is hidden;
somewhere else it is NickServ, or a usermode, or a bot-flag your channel
insists on. None of that belongs hardcoded in the connect path, and all of it
has to happen at a very specific moment.

THE MOMENT MATTERS MORE THAN THE COMMANDS

These run after registration and BEFORE the JOIN. On Undernet that ordering is
not cosmetic: `+x` replaces your visible host, and joining first means your
real host is in the channel for everyone who was already there. Auth, then
mode, then join. irc.py's delayed_join() is where that sequence lives.

WHY A FILE OF ITS OWN

settings.conf is one `NAME = value` per line and deliberately refuses a value
that spans lines - an indented continuation used to join itself onto the
setting above, silently. A list of commands is exactly the shape that breaks
on, so it gets its own JSON file, the same way library_folders.json and
lists.json do.

THE FILE HOLDS A PASSWORD

An X login line contains one in plain text. Two consequences, both enforced
here rather than left to callers:

  * The command text is NEVER logged. Not to stdout, not to the debug channel -
    a debug channel is a CHANNEL, and printing an X password into one would
    hand it to everybody watching. Callers get a count.
  * `redacted()` is what any caller that must show something uses.
"""

import io
import json
import os

import defaults as config
import platform_compat

# One IRC line is 512 bytes including the trailing CRLF. A command longer than
# that is not a command the server will ever see whole, so it is refused at the
# point it is written rather than truncated on the wire.
MAX_COMMAND_BYTES = 510
MAX_COMMANDS = 50
MAX_DELAY_SECONDS = 60
DEFAULT_DELAY_SECONDS = 2


def on_connect_file():
    """Where the commands live. Resolved per call, like every other path here:
    !rehash reloads config, and a path captured at import would keep pointing
    at the old location for the life of the process."""
    return getattr(config, "ON_CONNECT_FILE",
                   os.path.join("data", "on_connect.json"))


def _clean_command(raw):
    """One command, or "" if there is nothing usable in it.

    CR and LF come out rather than being refused. They are how one entry would
    become two commands on the wire, and an operator who pastes a block with a
    stray newline means one command - not one command and whatever the tail
    happens to parse as.
    """
    text = str(raw or "").replace("\r", " ").replace("\n", " ").strip()
    return text


def load(path=None):
    """(commands, delay_seconds). Empty list when there is nothing to send.

    Never raises. A file that cannot be read means "no on-connect commands",
    which is what every install has by default - it must not be able to stop
    the bot connecting.
    """
    target = on_connect_file() if path is None else path
    try:
        with io.open(platform_compat.long_path(target), encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return [], DEFAULT_DELAY_SECONDS

    if not isinstance(raw, dict):
        return [], DEFAULT_DELAY_SECONDS

    commands = []
    for item in raw.get("commands") or []:
        text = _clean_command(item)
        if text:
            commands.append(text)

    try:
        delay = float(raw.get("delay_seconds", DEFAULT_DELAY_SECONDS))
    except (TypeError, ValueError):
        delay = DEFAULT_DELAY_SECONDS
    delay = max(0.0, min(float(MAX_DELAY_SECONDS), delay))

    return commands[:MAX_COMMANDS], delay


def problems(commands, delay_seconds):
    """Every reason this set could not be sent, as operator-readable lines.

    Returns [] when it is usable. Same shape and same reasoning as
    library.problems(): every fault at once, each naming the command it is
    about by POSITION rather than by text, because the text may be a password.
    """
    found = []

    if len(commands) > MAX_COMMANDS:
        found.append(f"{len(commands)} commands - at most {MAX_COMMANDS}.")

    for index, command in enumerate(commands, start=1):
        text = _clean_command(command)
        if not text:
            found.append(f"command {index}: blank.")
            continue
        size = len(text.encode("utf-8", "replace"))
        if size > MAX_COMMAND_BYTES:
            found.append(f"command {index}: {size} bytes, over the "
                         f"{MAX_COMMAND_BYTES}-byte IRC line limit. The server "
                         f"would never see all of it.")

    try:
        delay = float(delay_seconds)
    except (TypeError, ValueError):
        found.append(f"the delay must be a number of seconds.")
    else:
        if delay < 0 or delay > MAX_DELAY_SECONDS:
            found.append(f"the delay must be between 0 and "
                         f"{MAX_DELAY_SECONDS} seconds.")

    return found


def save(commands, delay_seconds, path=None):
    """Write them, refusing a set that could not be sent.

    Raises ValueError naming every problem at once. Written to a temporary
    file and renamed, like every other file this project writes: a half-written
    command list read at the next connect would send half a login.
    """
    import tempfile

    cleaned = [_clean_command(c) for c in commands or []]
    cleaned = [c for c in cleaned if c]

    found = problems(cleaned, delay_seconds)
    if found:
        raise ValueError("\n".join(found))

    target = on_connect_file() if path is None else path
    directory = os.path.dirname(os.path.abspath(target)) or "."
    os.makedirs(platform_compat.long_path(directory), exist_ok=True)

    payload = json.dumps({"delay_seconds": float(delay_seconds),
                          "commands": cleaned},
                         indent=2, ensure_ascii=False)

    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=directory,
        prefix=".on-connect-", suffix=".tmp", delete=False)
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

    return cleaned


def expand(command, nickname=None):
    """Substitute the placeholders a command may use.

    Only `%nick%` so far, and it earns its place: `MODE %nick% +x` is the
    Undernet line, and the nick it needs is the one the SERVER gave us - which
    is not necessarily the one in the config, because a 433 collision rebinds
    it. Writing the configured nick into that command would send a MODE for
    somebody else.
    """
    nick = str(nickname if nickname is not None
               else getattr(config, "NICKNAME", "") or "")
    return str(command).replace("%nick%", nick)


def redacted(commands):
    """What a caller may show or log: the command word, and nothing after it.

    "PRIVMSG X@channels.undernet.org :LOGIN someone hunter2" becomes
    "PRIVMSG ...". The first word is enough for an operator to recognise which
    line ran, and everything that could be a password is in the rest.
    """
    shown = []
    for command in commands or []:
        first = str(command).strip().split(" ", 1)[0]
        shown.append(f"{first} ..." if first else "...")
    return shown
