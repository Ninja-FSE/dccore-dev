# DCCore

**Current version: v1.10.0-RC1**

An extremely fast, stable, and tailored IRC DCC file-sharing engine (OmenServe architecture) built in Python 3.10, running on both Linux and Windows for any IRC Network. The script is fully optimized for Proxmox LXC containers, delivering file-sharing notifications, advanced database statistics, and real-time monitoring with mIRC colors at an absolute gold standard.

## ✨ Key Features

- **⚡ VIP Express Queue:** An isolated, high-priority queue (`vip_queue`) that fires out private queue controls (`@DCCore-que`) and search headers in under 1 millisecond—completely unaffected by standard flood protection or channel advertisements.
- **❄️ Smart Freeze Box & Real-Time Wakeup:** If a user with files in the queue performs a `PART` or disconnects (`QUIT`), their queue is automatically frozen in a background thread for 5 minutes. If the user rejoins (`JOIN`), the queue thaws instantly (0ms) and resumes transmission.
- **🎨 Central mIRC Block Theme:** Fully themed via `announce.py` featuring heavy color blocks (Teal/Dark Red) and crisp white background plates, clinically cleared of color bleeding and client-specific cache artifacts.
- **📊 Advanced 7-Column Database:** Bulletproof live statistics (`stats.txt`) tracking total sent files/bytes, yesterday's and today's activity, and synced list dates in real time with forced disk-flush (`fsync`) for local storage optimization.
- **🛠️ Dedicated VIP Debug Channel:** A fully automated network gateway streaming timestamped and color-coded CLI logs (`[SENT]`, `[PART]`, `[QUIT]`, `[JOIN]`) live to the `#your-debug` channel via the VIP Express queue.

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

### Starting the Daemon
To perform a completely clean reboot of the bot, clear hidden cache files, and force a fresh reload of code modifications into RAM, execute:
```bash
pkill -f oserve.py && rm -rf __pycache__ */__pycache__ && python3 oserve.py
```
