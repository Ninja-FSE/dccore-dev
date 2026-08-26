"""Verify a Windows setup WITHOUT connecting to IRC.

Run this before the first real start. It loads the same config the daemon will,
reports what it resolved, and refuses to pass on the two things that actually
cause trouble: a music directory that is not there, and a channel list still
pointing at somebody else's live bot.

It never opens a socket to a server and never joins anything.
"""

import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
os.chdir(REPO)

# Channels belonging to the upstream production bot. Inheriting these is the
# one mistake that can get somebody else banned as well as you: two bots with
# near-identical names advertising in the same rooms reads as a clone.
UPSTREAM_CHANNELS = ("#mp3passion", "#mp3servers", "#mp3-best-of",
                     "#mp3country", "#mp3albums4u", "#mp3download")
UPSTREAM_NICKS = ("dccore", "dccore_")

problems = []
warnings = []


def fail(text):
    problems.append(text)
    print(f"  FAIL   {text}")


def warn(text):
    warnings.append(text)
    print(f"  WARN   {text}")


def ok(text):
    print(f"  ok     {text}")


print()
print("=" * 68)
print("  DCCore setup check - nothing here connects to IRC")
print("=" * 68)
print()

# --- interpreter and platform ------------------------------------------
print("Environment")
version = sys.version_info
if version < (3, 10):
    fail(f"Python {version.major}.{version.minor} - the daemon needs 3.10 or newer")
else:
    ok(f"Python {version.major}.{version.minor}.{version.micro}")

if os.name != "nt":
    warn("this check is written for Windows; on Linux use the normal start script")
else:
    ok("running on Windows")

# --- config -------------------------------------------------------------
print()
print("Configuration")

if not os.path.exists(os.path.join(REPO, "local_config.py")):
    fail("no local_config.py - copy local_config.py.sample and fill it in, "
         "or the daemon will use the upstream defaults")

try:
    import config
except Exception as err:
    fail(f"config.py did not load: {err}")
    print()
    print("  Cannot continue without a config.")
    sys.exit(1)

import platform_compat  # noqa: E402

ok(f"version {getattr(config, 'SCRIPT_VERSION', '?')}")
ok(f"nickname {getattr(config, 'NICKNAME', '?')} "
   f"(alt {getattr(config, 'ALT_NICKNAME', '?')})")

nick = str(getattr(config, "NICKNAME", "")).strip().lower()
if nick in UPSTREAM_NICKS:
    fail(f"NICKNAME is still {config.NICKNAME!r} - it will collide with the "
         f"production bot. Set your own in local_config.py.")

channels = str(getattr(config, "CHANNEL", ""))
shared = [c for c in UPSTREAM_CHANNELS if c in channels.lower()]
if shared:
    fail(f"CHANNEL still points at the production bot's channels: "
         f"{', '.join(shared)}. Use your own test channel.")
else:
    ok(f"channels {channels}")

ok(f"debug channel {getattr(config, 'DEBUG_CHANNEL', '?')}")
ok(f"admin nick {getattr(config, 'ADMIN_NICK', '?')}")

# --- paths --------------------------------------------------------------
print()
print("Paths")

music = getattr(config, "FILE_DIRECTORY", "")
if not music:
    fail("FILE_DIRECTORY is not set")
elif not os.path.isdir(music):
    fail(f"FILE_DIRECTORY does not exist: {music}  "
         f"(the daemon exits at startup if this is missing)")
else:
    count = 0
    for _root, _dirs, files in os.walk(music):
        count += sum(1 for f in files if f.lower().endswith((".mp3", ".flac")))
        if count > 5000:
            break
    ok(f"music directory {music}")
    ok(f"{'over 5000' if count > 5000 else count} audio file(s) visible - "
       f"the first scan walks all of them")

for label, path in (("lists", getattr(config, "LOCAL_LIST_DIR", "")),
                    ("temp archives", getattr(config, "TMP_ZIP_DIR", ""))):
    resolved = os.path.abspath(path) if path else "(unset)"
    if path and not os.path.isabs(path):
        ok(f"{label} -> {resolved}  (relative: correct only when started "
           f"from the repo folder)")
    else:
        ok(f"{label} -> {resolved}")

# --- external tools ------------------------------------------------------
print()
print("Tools")
rar = platform_compat.rar_command(getattr(config, "RAR_BINARY", None))
if rar:
    ok(f"rar {rar}")
else:
    warn("no rar/rar.exe found - whole-album (!rar) packing will fail, "
         "single files are unaffected")

# --- DCC ports -----------------------------------------------------------
print()
print("DCC ports")
start = int(getattr(config, "DCC_PORT_START", 55000))
end = int(getattr(config, "DCC_PORT_END", 55010))
free = 0
for port in range(start, end + 1):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        platform_compat.prepare_listener(probe)
        probe.bind(("0.0.0.0", port))
        free += 1
    except OSError:
        pass
    finally:
        probe.close()

if free == 0:
    fail(f"no port in {start}-{end} could be bound - something else is using them")
elif free < (end - start + 1):
    warn(f"{free} of {end - start + 1} ports free in {start}-{end}")
else:
    ok(f"all {free} ports free in {start}-{end}")
print("         (these must also be forwarded to this PC for anyone to "
      "download from you)")

# --- admin console -------------------------------------------------------
print()
print("Admin console")
masks = getattr(config, "ADMIN_HOSTMASKS", []) or []
has_hash = bool(getattr(config, "ADMIN_PASSWORD_HASH", ""))
if not masks:
    ok("disabled (ADMIN_HOSTMASKS is empty) - this is fine")
elif not has_hash:
    fail("ADMIN_HOSTMASKS is set but ADMIN_PASSWORD_HASH is empty - "
         "the console will refuse every connection. Run: python adminchat.py")
else:
    ok(f"enabled for {len(masks)} host pattern(s)")

# --- verdict --------------------------------------------------------------
print()
print("=" * 68)
if problems:
    print(f"  {len(problems)} problem(s) - fix these before starting:")
    for text in problems:
        print(f"    - {text}")
    print("=" * 68)
    sys.exit(1)

if warnings:
    print(f"  Ready to start, with {len(warnings)} warning(s).")
else:
    print("  Ready to start.")
print()
print("  Start it with:  windows\\start-dccore.bat")
print("  Stop it with:   Ctrl-C in that window")
print("=" * 68)
print()
