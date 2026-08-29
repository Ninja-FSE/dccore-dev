# DCCore

**Current version: v1.10.0-RC4**

An extremely fast, stable, and tailored IRC DCC file-sharing engine (OmenServe architecture) built in Python 3.10, running on both Linux and Windows for any IRC Network. The script is fully optimized for Proxmox LXC containers, delivering file-sharing notifications, advanced database statistics, and real-time monitoring with mIRC colors at an absolute gold standard.

## ✨ Key Features

- **⚡ VIP Express Queue:** An isolated, high-priority queue (`vip_queue`) that fires out private queue controls (`@DCCore-que`) and search headers in under 1 millisecond—completely unaffected by standard flood protection or channel advertisements.
- **❄️ Smart Freeze Box & Real-Time Wakeup:** If a user with files in the queue performs a `PART` or disconnects (`QUIT`), their queue is automatically frozen in a background thread for 5 minutes. If the user rejoins (`JOIN`), the queue thaws instantly (0ms) and resumes transmission.
- **🎨 Central mIRC Block Theme:** Fully themed via `announce.py` featuring heavy color blocks (Teal/Dark Red) and crisp white background plates, clinically cleared of color bleeding and client-specific cache artifacts.
- **📊 Advanced 7-Column Database:** Bulletproof live statistics (`stats.txt`) tracking total sent files/bytes, yesterday's and today's activity, and synced list dates in real time with forced disk-flush (`fsync`) for local storage optimization.
- **🛠️ Dedicated VIP Debug Channel:** A fully automated network gateway streaming timestamped and color-coded CLI logs (`[SENT]`, `[PART]`, `[QUIT]`, `[JOIN]`) live to the `#your-debug` channel via the VIP Express queue.
- **🔐 Authenticated Admin Console (DCC CHAT):** A private administration channel over DCC CHAT, gated on two independent factors - the operator's Undernet services login (proved by their `+x` host, which only the IRC server can issue) and a PBKDF2-hashed password. Replaces the old nick-based admin gate, which anyone could inherit simply by taking the nick while the real operator was offline. Read-only commands (`status`, `queue`, `slots`, `bans`, `uptime`, `version`) and action commands (`ban`, `unban`, `clearqueue`, `rehash`, `update`) all run from the same session; runtime reports can be routed there instead of the public debug channel. See [docs/ADMIN-CONSOLE.md](docs/ADMIN-CONSOLE.md).
- **🌐 Web Dashboard & Cross-Bot Fetch:** An optional dashboard (Search / Queue / File Lists, grouped by folder / a duplicate-filename Verify tool) served alongside the daemon, plus a genuinely new capability - the daemon can now *receive* files from other bots on the network, not just send them. Broadcast a `@find` and collect answers from every bot that replies, grouped under each bot's own parsed header; request a specific file with `!<bot> <filename>`, or a bot's entire list with `@<botnick>`, and track the transfer from a bulk-paste Download tab. Off by default (`WEBUI_ENABLED = False`), Flask is an optional dependency, and every safety boundary - admission control, size caps, zip-slip/zip-bomb protection - assumes every other bot on the network is untrusted. The dashboard itself has **no authentication** - it is LAN-only by design (see the warning blocks in `webserver.py`), and it is not read-only: three of its routes mutate state, sending a real `@find` into a channel and dialling out to a bot-supplied address. See `docs/UPDATES.md`'s v1.10.0-RC4 entry for the full list of what this feature line added.

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
- `config.py` — Central configuration file and runtime memory registry containing:
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
The script is developed and tested for **Python 3.10+** within a Linux environment (e.g., Debian/Ubuntu LXC in Proxmox), and runs on Windows as well - the platform differences are isolated in `platform_compat.py` and covered by CI on both operating systems.

### Dependencies
**The daemon itself needs no third-party packages.** Everything it uses to talk to IRC, serve files over DCC, pack albums with `rar`, and run the admin console is in the standard library:
```bash
pip install -r requirements.txt      # succeeds, installs nothing
```
The one optional extra is the web dashboard, which needs Flask and is opt-in - the bot starts fine without it.

### Local overrides
Copy [`local_config.py.sample`](local_config.py.sample) to `local_config.py` (gitignored) to set machine-specific values - such as the admin console's `ADMIN_HOSTMASKS` and `ADMIN_PASSWORD_HASH` - without editing a tracked file. See [docs/ADMIN-CONSOLE.md](docs/ADMIN-CONSOLE.md) for the full admin console setup guide.

### First-time setup
Before the first real start, verify the setup without connecting to IRC. This catches the two mistakes that actually cause trouble: a music directory that does not exist, and a config still carrying the upstream nickname or channels - which would put a near-identical second bot into the production bot's live channels.

**Linux:**
```bash
./scripts/linux/start-dccore.sh check
```
Once it reports "Ready to start", `./scripts/linux/start-dccore.sh` runs the same check and then starts the daemon - correct working directory included, so it also works from a cron job, a systemd unit, or a shortcut.

**Windows:**
```bat
scripts\windows\start-dccore.bat check
```
Same check, same refusal conditions. Once it reports "Ready to start", double-clicking `scripts\windows\start-dccore.bat` (or running it from a shortcut) runs the check and then starts the daemon - it anchors its own working directory first, so it doesn't matter where the shortcut or the double-click happens from. The full Windows setup guide (`docs/WINDOWS.md`) lives on the `windows` branch, not here on `beta`.

### Starting the Daemon
To perform a completely clean reboot of the bot on Linux, clear hidden cache files, and force a fresh reload of code modifications into RAM, execute:
```bash
pkill -f oserve.py && rm -rf __pycache__ */__pycache__ && python3 oserve.py
```
