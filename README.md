# DCCore

**Current version: v1.10.0**

An extremely fast, stable, and tailored IRC DCC file-sharing engine (OmenServe architecture) built in Python 3.10, running on both Linux and Windows for any IRC Network. The script is fully optimized for Proxmox LXC containers, delivering file-sharing notifications, advanced database statistics, and real-time monitoring with mIRC colors at an absolute gold standard.

## ✨ Key Features

- **⚡ VIP Express Queue:** An isolated, high-priority queue (`vip_queue`) that fires out private queue controls (`@DCCore-que`) and search headers in under 1 millisecond—completely unaffected by standard flood protection or channel advertisements.
- **❄️ Smart Freeze Box & Real-Time Wakeup:** If a user with files in the queue performs a `PART` or disconnects (`QUIT`), their queue is automatically frozen in a background thread for 5 minutes. If the user rejoins (`JOIN`), the queue thaws instantly (0ms) and resumes transmission.
- **🎨 Central mIRC Block Theme:** Fully themed via `announce.py` featuring heavy color blocks (Teal/Dark Red) and crisp white background plates, clinically cleared of color bleeding and client-specific cache artifacts.
- **📊 Advanced 7-Column Database:** Bulletproof live statistics (`stats.txt`) tracking total sent files/bytes, yesterday's and today's activity, and synced list dates in real time with forced disk-flush (`fsync`) for local storage optimization.
- **🛠️ Dedicated VIP Debug Channel:** A fully automated network gateway streaming timestamped and color-coded CLI logs (`[SENT]`, `[PART]`, `[QUIT]`, `[JOIN]`) live to the `#your-debug` channel via the VIP Express queue.
- **🔐 Authenticated Admin Console (DCC CHAT):** A private administration channel over DCC CHAT, gated on two independent factors - the operator's Undernet services login (proved by their `+x` host, which only the IRC server can issue) and a PBKDF2-hashed password. Replaces the old nick-based admin gate, which anyone could inherit simply by taking the nick while the real operator was offline. Read-only commands (`status`, `queue`, `slots`, `bans`, `uptime`, `version`) and action commands (`ban`, `unban`, `clearqueue`, `rehash`, `update`) all run from the same session; runtime reports can be routed there instead of the public debug channel. See [docs/ADMIN-CONSOLE.md](docs/ADMIN-CONSOLE.md).
- **🌐 Web Dashboard & Cross-Bot Fetch:** An optional dashboard (Search / Queue / File Lists, grouped by folder / a duplicate-filename Verify tool) served alongside the daemon, plus a genuinely new capability - the daemon can now *receive* files from other bots on the network, not just send them. Broadcast a `@find` and collect answers from every bot that replies, grouped under each bot's own parsed header; request a specific file with `!<bot> <filename>`, or a bot's entire list with `@<botnick>`, and track the transfer from a bulk-paste Download tab. Off by default (`WEBUI_ENABLED = False`), Flask is an optional dependency, and every safety boundary - admission control, size caps, zip-slip/zip-bomb protection - assumes every other bot on the network is untrusted. Every route, static assets included, requires logging in with the same password as the DCC CHAT admin console (`ADMIN_PASSWORD_HASH`, generated with `python adminchat.py`) - the daemon refuses to even start the dashboard while that hash is unset. It is still meant for a trusted LAN (see the warning blocks in `webserver.py` and `defaults.py`'s `WEBUI_HOST` comment: there is no TLS, so a login gate does not stop someone sharing the network segment from reading the password or session cookie off the wire), and it is not read-only: three of its routes mutate state, sending a real `@find` into a channel and dialling out to a bot-supplied address. See `docs/UPDATES.md`'s v1.10.0-RC4 entry for the full list of what this feature line added.

## 📁 File Structure

- `oserve.py` — Core engine, thread manager, and flood-protection queues.
- `irc.py` — Dedicated network module, asynchronous loop, and chained command parser.
- `dcc.py` — DCC handshakes, socket transmitters, and smart freeze-box timers.
- `announce.py` — Central mIRC theme, channel announcements, and the VIP debug engine.
- `commands.py` — User commands (`que`, `remove`) isolated from the main network loop.
- `platform_compat.py` — The handful of genuine Linux/Windows differences (socket options, the rar binary lookup, long paths, keepalive tuning), isolated so the rest of the codebase stays platform-neutral.
- `adminchat.py` — Authenticated DCC CHAT console for the operator, gated on the Undernet `+x` services host plus a password. See [docs/ADMIN-CONSOLE.md](docs/ADMIN-CONSOLE.md).
- `stats_mgr.py` — Dumb data module managing file sizes and transfer speed calculations.
- `db.py` — Database I/O interface featuring forced write-protection at EOF.
- `webserver.py` / `web/` — Optional Flask dashboard (Search, Queue, File Lists, Download); silently disables itself if Flask isn't installed.
- `dcc_fetch.py` — Receives files FROM other bots (active and passive/reverse DCC SEND), the opposite role from `dcc.py`.
- `list_fetch.py` — Safely unpacks another bot's fetched file-list zip (zip-slip/zip-bomb guarded) for the File Lists dashboard view.
- `defaults.py` — Central configuration file and runtime memory registry containing:
  - Global Engine Settings (Debug flags, script versions, and list base names)
  - IRC Network Settings (Servers, ports, nicknames, channels, and admin control tags)
  - Filesystem & Storage Paths (Maintenance switches, storage directories, and database file paths)
  - Channel Advertisement Clock (Interval timers for public automated broadcasts)
  - Bandwidth & Queue Caps (Max DCC slot counts, user/global limits, and DCC port ranges)
  - Anti-Flood & Security Shields (Request windows, warning thresholds, and auto-ban triggers)
  - Native mIRC Formatting (IRC-standard control codes for colors, text weight, and styling)
  - Global In-Memory RAM Arrays (Live trackers for active transfers, search-locks, and frozen background queues)

## ⚙️ Installation & Startup

### Prerequisites
The script is developed and tested for **Python 3.10+** within a Linux environment (e.g., Debian/Ubuntu LXC in Proxmox), and runs on Windows as well - the platform differences are isolated in `platform_compat.py` and verified on both operating systems.

### Dependencies
**The daemon itself needs no third-party packages.** Everything it uses to talk to IRC, serve files over DCC, pack albums with `rar`, and run the admin console is in the standard library:
```bash
pip install -r requirements.txt      # succeeds, installs nothing
```
The one optional extra is the web dashboard, which needs Flask and is opt-in - the bot starts fine without it.

### Guided first-run setup
```bash
python3 setup.py
```
Asks nickname, IRC server, channel(s), admin nick, admin console password (the same prompt as running `adminchat.py` directly), the music directory, and whether to enable the web dashboard - and writes them to `settings.conf` and `admin_config.py` for you. The music directory is optional; it is often easier to browse-and-confirm it from the web dashboard's Settings page once the daemon is running than to type a path here. Everything else stays configurable afterwards from the CLI or the web dashboard's Settings page. Safe to run again later - every prompt shows what is already configured as its default.

### Local overrides
Copy [`admin_config.py.sample`](admin_config.py.sample) to `admin_config.py` (gitignored) to set machine-specific values - such as the admin console's `ADMIN_HOSTMASKS` and `ADMIN_PASSWORD_HASH` - without editing a tracked file, or without running `setup.py` at all. See [docs/ADMIN-CONSOLE.md](docs/ADMIN-CONSOLE.md) for the full admin console setup guide.

### settings.conf
Copy `settings.conf.sample` to `settings.conf` (also gitignored) to set the same kind of machine-specific values in a plain text file instead - no Python syntax required. It is what the web dashboard's Settings page and the admin console's CLI both write to, and `defaults.py` applies it *after* `admin_config.py`, so a value set in both places takes its value from `settings.conf`. The daemon starts fine from `settings.conf` alone, with no `admin_config.py` at all; use whichever fits how you prefer to configure things, or both.

### First-time setup
Before the first real start, verify the setup without connecting to IRC. This catches the mistakes that actually cause trouble at boot - most notably a music directory that is set but does not exist - without ever opening a socket.

The daemon itself backstops this too: `oserve.startup()` refuses to boot while `NICKNAME`, `CHANNEL` or `ADMIN_NICK` is still blank (`settings_file.REQUIRED`), regardless of how the daemon is started - the check below is the friendlier, earlier warning. `SERVER` and `DEBUG_CHANNEL` are not in that list on purpose: their shipped defaults are already correct for almost every install, so requiring them too would just make an operator retype a value that was already right. `FILE_DIRECTORY` is not in that list either, deliberately: requiring it would block the daemon from ever reaching the web dashboard, the one place that is genuinely easier to set it from - a blank one only warns, both here and at boot.

**Linux:**
```bash
./scripts/linux/start-dccore.sh check
```
Once it reports "Ready to start", `./scripts/linux/start-dccore.sh` runs the same check and then starts the daemon - correct working directory included, so it also works from a cron job, a systemd unit, or a shortcut.

**Windows:**
```bat
scripts\windows\start-dccore.bat check
```
Same check, same refusal conditions. Once it reports "Ready to start", double-clicking `scripts\windows\start-dccore.bat` (or running it from a shortcut) runs the check and then starts the daemon - it anchors its own working directory first, so it doesn't matter where the shortcut or the double-click happens from. The full Windows setup guide is [docs/WINDOWS.md](docs/WINDOWS.md).

### Starting the Daemon
To perform a completely clean reboot of the bot on Linux, clear hidden cache files, and force a fresh reload of code modifications into RAM, execute:
```bash
pkill -f oserve.py && rm -rf __pycache__ */__pycache__ && python3 oserve.py
```
