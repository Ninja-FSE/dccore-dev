# Running DCCore on Windows

This is the `windows` branch. It carries the Windows packaging — a launcher and
a setup check — and merges from `beta`, which stays the Linux trunk. Nothing
here changes how the daemon behaves; the platform differences that do exist live
in `platform_compat.py` on `beta` and are covered by CI on both operating
systems.

The daemon itself already runs on Windows. Its whole boot sequence was verified
here before this branch existed: master list generated, every `data/` file
round-tripped, DCC listener bound, WinRAR found at its install path.

---

## Before you start

**Python 3.10 or newer.** Install from python.org and tick *Add python.exe to
PATH*. Nothing else is required — the daemon and its test suite are stdlib-only,
which is why they run on a bare machine with no `pip install` step.

**WinRAR is optional.** Without it, single-file transfers work normally and only
whole-album (`!rar`) packing fails. If you have it, no configuration is needed:
`platform_compat.rar_command()` looks in WinRAR's install directory as well as
on PATH, because WinRAR does not add itself to PATH.

---

## Setup

### 1. Create `local_config.py`

Copy `local_config.py.sample` to `local_config.py` and fill it in. That file is
gitignored and never leaves your machine.

> **The one line that matters most is `CHANNEL`.**
>
> `config.py` ships with the upstream operator's live channels and nickname. If
> you start the daemon without overriding them, you join his trading channels as
> a second bot with a near-identical name. That reads as a clone and can get
> **both** of you banned. Set your own nickname and your own test channel.

Set at minimum:

| setting | why |
|---|---|
| `NICKNAME`, `ALT_NICKNAME` | must not collide with the live bot |
| `LIST_BASE_NAME` | names your generated list files |
| `CHANNEL`, `DEBUG_CHANNEL` | your own test channel |
| `ADMIN_NICK` | your nick |
| `FILE_DIRECTORY` | your music folder — start with a small one |

### 2. Check the setup

```
windows\start-dccore.bat check
```

This loads the same config the daemon will and reports what it resolved. It
never opens a connection to a server and never joins anything. It fails on a
missing music directory, and on a config still pointing at the upstream nick or
channels.

### 3. Start it

```
windows\start-dccore.bat
```

Ctrl-C in that window stops it. The launcher runs the setup check first and
refuses to start if it fails.

---

## Why there is a launcher at all

Every data path in `config.py` is relative — `./data/bans.txt`, `./lists` — so
they resolve against the **working directory**, not the code. Started from
anywhere other than the repository folder, the daemon quietly creates an empty
`data` folder wherever it happened to start and boots with no bans, no queue and
no list.

`start-dccore.bat` does `cd /d "%~dp0.."` before anything else, so it is correct
from a double-click, a shortcut, or any other directory.

**This is also why there is no Windows service yet.** A service starts in
`C:\Windows\System32`, and no launcher is involved to correct it. Making that
work means anchoring the paths to the code's own location rather than the
working directory — a change worth doing deliberately, not as a side effect of
adding a service wrapper.

---

## Networking

**Forward TCP 55000–55010** to this machine for anyone to download from you.
That range is `DCC_PORT_START`–`DCC_PORT_END` in `config.py`, and the admin
console borrows a port from it too when it has to listen.

### Testing a download from your own machine will probably fail

This is not a bug. The daemon advertises its **public** IP in every DCC offer,
and most routers will not route a connection from inside the network back to
themselves. Either test from a second machine or a phone, or set
`MY_IP_OR_DOCK` to this PC's LAN address in `local_config.py` for local testing
only — and remove it before real use.

---

## The admin console

Optional, and off until configured. See [ADMIN-CONSOLE.md](ADMIN-CONSOLE.md) for
the full guide. In short: it is gated on your Undernet `+x` host plus a
password, and the setup check will tell you if you have set one without the
other.

Generate the password hash with:

```
python adminchat.py
```

Run that yourself. The password never needs to leave this machine.

---

## What is different from Linux

Very little, and all of it is already handled:

| | |
|---|---|
| DCC listener socket option | `SO_EXCLUSIVEADDRUSE` here, `SO_REUSEADDR` there — on Windows the latter would let another process steal the port |
| rar binary | looked up in WinRAR's install directory as well as PATH |
| long paths | `\\?\` prefix applied past 260 characters, which a deep music library reaches easily |
| TCP keepalive | the tuning knobs that only exist on Linux are skipped rather than raising |
| batch files | CRLF, per `.gitattributes`; everything else in the repo is LF |
