# Changelog

## Unreleased

- **The dashboard's tabs are named for what they do.** "File Lists" is now **List Browser**, "Download" is **Downloads**, and Queue has moved into Stats — where the queue table sits below everything else the bot knows about itself. The order runs from what you use daily to what you touch occasionally.

- **Pick a run of files in one go.** Tick a file in the List Browser, then shift-click another, and everything between them is selected. Handy for queueing a whole stretch of an album list.

- **A burst of DCC CHAT requests can no longer stop the bot sending files.** Each passive request opened a listening port immediately, before any check — so a handful of them took every port reserved for transfers and held it for a minute. Only one is opened at a time now; the rest are declined and can simply be retried.
- **A list rebuild that fails partway no longer leaves you advertising one list and handing out another.** If someone was downloading your list when a rebuild ran, the rebuild could publish the new index while the archive people actually received stayed last week's — with the file count, the size and the search all reporting the new scan. It said "the previous list was left untouched" while that was no longer true. The rebuild now either lands completely or not at all.

- **A fetched file with a very long name can be downloaded from the dashboard.** The file arrived, was marked complete and appeared in the list, and its Download button then answered "not found" for ever — on Windows, where the full path was too long for the way that route opened it.

- **Small dashboard repairs:** browsing up from a lowercase drive letter no longer wanders off to wherever the bot was started from, an absurdly long folder path is refused rather than echoed back, and the "Get folder as .rar" button no longer appears on a group of files that has no folder to pack.
- **An indented line in `settings.conf` is now reported instead of silently joining the setting above it.** A stray indented line became part of the previous value, newline and all — so a nickname could quietly contain a second line and go out that way.

- **A typo in the admin chat mode is refused rather than ignored.** Writing `lisen` selected automatic mode silently; now it tells you the valid options.

- **Download counters survive renaming a folder's label**, and no longer rewrite themselves on every start.

- **Banning a host now actually bans it.** `!ban *.some.isp.net` was accepted, reported as successful and listed among your active bans — and never enforced, because a pattern without `!` or `@` was compared against the nickname, which can never contain a dot. Write it either way now; both work.

- **Fetching a list from a bot whose nick contains `|` works.** Nicks like `Bot|Away` are ordinary on IRC and illegal in a Windows folder name, so the fetch downloaded the whole list and then failed at the last step, every time, for that bot. Same for `*`, `"`, `<`, `>`, `?` and `:`.
- **Your library's total size no longer vanishes from the advert.** If your bot's nickname made the list's base name a prefix of the size files' names, every rebuild wrote those files and then deleted them again — so the advert published `Files (0B)` for ever, while the log said it had tidied up some old lists.

- **A custom list banner can no longer break the list it sits on.** A banner line made only of `=` reads as a folder separator, which shifted every folder heading after it by one; a line starting with `!` reads as a file, so it was counted in your file total and returned by searches as something nobody could download. Both are handled now — a rule line is redrawn in `-`, a `!` line is left out — and the bot tells you which line and why, so you can adjust the banner.

- **A folder picker on the Settings page, if you want one.** Instead of typing the full path to each served folder you can browse for it — drives, then folders, then "Use this folder". It is **off by default** and turned on with "Folder picker on the Settings page" under Web dashboard, because it lets anyone logged into your dashboard see the names of folders on your machine, which nothing else there does. Leaving it off costs you nothing except typing the path yourself.
- **Serving a whole drive works.** If you pointed DCCore at `D:\` rather than a folder inside it, the list advertised every file and then refused to send any of them, reporting each request as a security violation. Both the check that decides whether a folder can be saved and the one that decides whether a file can be sent had the same fault.

- **Two settings saves in quick succession can no longer make the bot leave every channel.** Each save triggers a reload, and two overlapping ones could leave one of them believing no channels were configured — so it parted all of them, cleared its user lists, joined nothing back, and logged that the sync had completed successfully. Every queue then froze, because the bot no longer believed anyone was present. Reloads are now serialised, and the second one waits rather than being skipped, so a save's changes are never lost.

- **A damaged download-counts file can no longer stop the bot starting.** One unreadable line in `data/download_counts.json` — from a hand-edit or an interrupted write — could make the daemon refuse to boot while carrying its own counters across. It now keeps what it can, says so, and starts.

- **Download counts are kept per folder, and survive a library move.** With more than one folder configured, two files with the same name in the same relative position — `Artist/Album/track.flac` in each — were counted as one, and the counts could end up recorded against wherever the bot happened to be started from. Counts now name the folder they belong to. **Your existing counts are carried over automatically on the first start**, so nothing is lost.

- **You can add, remove and reorder your served folders from the dashboard.** DCCore has been able to serve several folders since v1.11.0, but the list could only be created by hand-editing a JSON file — the Settings page showed a single "Music directory" box, which is really just the fallback used when no list exists. There is now a proper editor at the top of Paths & storage: add a folder, give it a label, drag the order you want them listed in. Bad combinations are refused with a line explaining each one — a folder inside another folder would list every file twice, and two folders sharing a label cannot be told apart in the list. Clearing the list puts you back to the single Music directory. After saving, rebuild the list from Tools for the new folders to appear in it.

- **A strangely named folder in another bot's list no longer hides the rest of it.** If a fetched list contained a folder whose name was made only of `=` characters, it looked like part of the list's own formatting — and everything after it silently disappeared from search and from the File Lists page, including files in perfectly ordinary folders. Only lists fetched from other bots were affected; your own was never at risk.

- **Files with very long names now fetch.** A file offered by another bot whose name ran past the filesystem's limit failed every time with an unhelpful "transfer error", because the length check the code believed it had only covered the full path, not the name itself. Long names are now shortened to fit, keeping the file extension.

- **A "Sent" announcement can no longer be addressed to a broken target.** For a queued item that did not record which channel it came from, the bot built its completion message with the whole channel list where one channel belongs - the line was malformed and the announcement silently vanished, even though the file had gone out fine.

- **Settings that the bot cannot run without can no longer be saved empty.** The server address, the fallback nickname, the list's base name and the dashboard's own bind address were all accepted blank, and each one is used exactly as typed - an empty server address is a connection to nowhere, an empty bind address exposes the dashboard on every network interface rather than just your own machine. Settings that ship blank on purpose, like the debug channel or the rar path, can still be cleared.

- **The version number is no longer an editable setting.** It was a field on the Settings page, and saving it pinned that number permanently - every future upgrade would go on reporting the old version in the advert, the list header, the CTCP VERSION reply and the dashboard. It describes the code you are running, so it is now read-only.

- **The fallback nickname is used properly when your main nick is taken.** If the fallback was empty the bot sent a nickname command with no nickname in it and ended up with no nick at all - reachable only while reconnecting after a split, which is exactly when it matters.

- **Saving a setting from the dashboard no longer shows your other settings as blank.** Nickname, admin nick and channels could come back empty on the Settings page straight after a save - alarming, and never true: the file always had them, and a refresh showed them again. The page now waits for the reload it just triggered instead of reporting its halfway state.

- **A debug channel set from the dashboard is joined straight away.** It used to save correctly, report "Rehash started", and not be joined until the next restart - with nothing to tell you it had been skipped.

- **A rehash cannot leave a running bot with no nickname, channels or admin.** If `settings.conf` cannot be read at the moment you rehash - antivirus holding it open, a network share blinking, an editor mid-save - the daemon now keeps the values it is already running with and says so, instead of quietly carrying on with none.

- **`settings.conf` stays tidy when the dashboard adds a setting to it.** The explanatory comment block is written once rather than again on every save, blank lines no longer accumulate, and the file ends with a newline.

- **Channels with a space after the comma now all get joined.** `#one, #two, #three` used to join `#one` and silently drop the rest - IRC treats the space as the end of the list - while the bot went on advertising in channels it had never entered. Write the list however reads best; it works either way now.

- **The bot now uses the nickname the server actually gave it.** If your nickname is longer than the network allows, the server shortens it silently - Undernet's limit is 12 characters. DCCore used to carry on believing the longer name, so it advertised a nick nobody could message. It now reads its real name back from the server, says so, and tells you the limit that caused it. Requests pasted from your existing list keep working either way.
- **The Windows setup instructions now use `py`**, not `python3`. Windows does not give you a `python3` command, and typing it opens the Microsoft Store instead — so the documented first step failed on a machine where Python was installed and working. The launcher scripts were always right; only the instructions were wrong.

- **A Console page in the web dashboard** — off by default; turn it on with `WEBUI_CONSOLE_ENABLED` if you want it. It gives the admin console's commands and live log in the browser. Worth knowing before you enable it: the dashboard asks only for your password, where the DCC CHAT console also checks your services host, and it has no TLS — so this is the admin command set behind one factor on your LAN rather than two.

## v1.11.0 — The Several Folders Release

- **DCCore can serve from more than one folder.** Configure several — a flac library and an mp3 one, or music spread across two drives — and they are built into a single list, in the order you choose.

  **Paths in the list now start with the folder's name**, so `D:\MUSIC\Artist\Album\` becomes `D:\MUSIC\Flac\Artist\Album\`. That is what lets the same album held in two folders be told apart. It applies even if you serve one folder, deliberately: doing it once now is better than changing every path again the day you add a second.

  **Rebuild your list after upgrading** (`!update`, or the dashboard's Update list button). Until you do, the file you are serving still has the old paths.

  **Almost nothing your users saved stops working.** A file request has always been `!YourBot Song.flac` — a name, not a path — so those are unaffected, and so is anything AutoQ queued from one. Only `!rar` folder requests carry a path, and those still resolve: an old unlabelled one is looked for in each of your folders in turn.

  A folder that is not available when the list is built — an unplugged drive, a share that is down — is skipped with a warning and the rest are built. Your bot stays up with a smaller list rather than going quiet.

- **The bot answers CTCP VERSION** with its build and a link to this project. It replies privately, by notice, straight back to whoever asked — nothing is said in the channel. Operators who would rather not advertise a build can turn it off with `CTCP_VERSION_REPLY`.
- **Every generated list now names the bot that made it** and links back here, on a line under the file count. The list is the one thing that travels: it gets sent to strangers over DCC and reopened weeks later in a text editor, and until now it carried nothing saying what produced it.
- **You can put your own banner on the list** — a greeting, your channel, ASCII art — by creating `data/list_header.txt`. Nothing to enable: if the file exists its contents are copied in verbatim on the next `!update`, so box-drawing characters survive intact. Capped at 8 KB. See [INSTALL.md](INSTALL.md#putting-your-own-banner-on-it).
- **The dashboard's colour theme is now a dropdown**, not a text box you had to already know the right word for. Picking one you cannot spell wrong also means the daemon can refuse a bad value at the moment you save it, with a reason, instead of quietly falling back later.

## v1.10.0 — General Availability

The first stable release, and the first public one.

**What it is:** an IRC file server. It serves files over DCC, keeps a searchable
master list of what it has, and — unlike the servers it grew out of — can fetch
files and lists *from* other bots as well as send them. Python 3.10+, GPLv3.

**No third-party dependencies.** Everything the daemon needs to talk to IRC,
serve files over DCC, pack albums and run its admin console is in the standard
library; `pip install -r requirements.txt` installs nothing. Flask is optional
and only for the web dashboard.

**Starting it** is a guided run of `python3 configure.py`, then a pre-flight
check that verifies the setup without connecting to IRC. Upgrading from an
earlier build migrates `local_config.py` to `admin_config.py` automatically.

### Security and reliability

- **Two audits.** The first closed 32 issues: forged IRC messages that could trigger admin-only behaviour, a channel-wide ban pattern that could lock out an entire channel, an unbounded fetch queue, and non-ASCII filenames that could corrupt on transfer, among others.
- **A second, deeper audit before publication** closed a further set, including: one `@find` refusing every other user's file requests while it ran; a queued `!rar` folder pack that was never re-dispatched and could wait forever; renaming the bot orphaning its entire master list, invisibly; an escalation ban whose length depended on what time of day the flooding happened; cross-bot fetch history that grew without limit for the life of the process; and a nickname containing `[ ]` — an ordinary IRC convention — making the bot advertise an empty library permanently.
- **The admin console's host gate no longer accepts `*.*`**, which matched essentially any real host. That gate is the first of the two factors deciding who reaches the password prompt at all.
- **`!rehash` no longer breaks mutual exclusion.** Reloading a module rebound every lock defined in it, so a thread already inside a critical section kept an object nothing else could see. It also restored a stale queue snapshot over live state, discarding requests that arrived during the reload and re-sending transfers that had completed during it.
- Several race conditions around shared in-memory state (channel membership, the fetch queue, stats) are fixed.

### Configuration and operation

- **Web dashboard**: a Settings page that writes configuration directly, a Stats page, a light theme, and login required on every route. It is built for a trusted LAN and has no TLS, so a login gate does not stop someone on the same network segment reading the password off the wire — do not expose it to the open internet.
- The master list can be downloaded as `.txt`, `.zip` or `.rar`; live transfer speed is now visible outside the channel advert.
- `NICKNAME`, `CHANNEL` and `ADMIN_NICK` are now required settings — a fresh install can no longer silently start under someone else's identity — and none of them can be blanked from the dashboard afterwards, which used to leave an install that could not start and no dashboard to fix it from.
- **`DEBUG_CHANNEL` ships blank.** It used to default to a real shared channel that every install joined automatically, streaming its internals to everyone else in it.

### Testing

- **Every public function is checked against a profiler**, not just a test count: fifteen of the daemon's 205 public functions were never entered by any test the first time this ran — including `!rehash`, the command that sends a user the file list, and the IRC read loop. Two remain uncovered, both `while True:` loops that own the process, each recorded with the reason.
- Linux and Windows, on two Python versions, on every push and pull request.

## v1.10.0-RC4 — The Web Dashboard Release

- **Web dashboard**: search, queue and file-list views served alongside the IRC daemon. Optional — the daemon runs fine without Flask installed, and it is off by default. Intended for a trusted LAN: there is no TLS.
- **Cross-bot search and fetch**: the dashboard can broadcast `@find` and collect replies from other bots, and can now *receive* files and file lists from other bots, not just send them.
- Fixed: a CRLF/CTCP injection issue found in three separate input paths; a stored XSS issue in the dashboard's result rendering; `!rehash` silently clearing the fetch queue; several path- and archive-handling issues in the cross-bot fetch feature (zip-slip protection, Windows long-path handling, exception classes that could escape the extraction guard).
- Every setting now declares its own type, closing a class of bug where an unset value had no type to infer from.

## v1.10.0-RC3 — The English & Reliability Release

- Every module's comments and log output translated to English.
- Fixed: the daemon could crash on non-Western console code pages; redirected logs could lose everything buffered when the process was killed; concurrent transfers could lose stats under a race; Windows library paths over 260 characters were silently unservable; a memory leak in ban-notification tracking.

## v1.10.0-RC2 — The Admin Console Release

- **Authenticated admin console over DCC CHAT**: replaces a nick-based admin check (which anyone could inherit by taking the admin nick) with two-factor authentication — the operator's IRC services login plus a hashed password compared in constant time.
- Full remote command set: status, queue, slots, bans, uptime, version, ban/unban, clearqueue, rehash, update.
- Fixed: a DCC dial-to-self bug when a client's IP failed to resolve; list side-files not rolling back together with a failed list rebuild; control characters corrupting the master list.

## v1.10.0-RC1 — The Platform & Forgery-Hardening Release

- **Windows support**: every genuine Linux/Windows difference isolated into one module; the daemon now runs on both platforms, verified in CI.
- **Per-machine config overrides**, without editing a tracked file.
- Fixed several message-forgery bugs: channel text could trigger server-numeric or user-event handlers meant only for real IRC events (a chat message containing the word "QUIT" could freeze that user's queue). Also fixed: orphaned temporary archives after a queue removal, a ban file that could be silently truncated on a crash, and a couple of flood-protection gaps that left some commands unmetered.
- **Known open item at this release** (fixed in RC2): the admin check was still nick-based with no host verification.

## v1.9.0 — The Gold & Audio Handshake Release

- Filenames containing an apostrophe now pack correctly into `.rar` archives.
- Multi-disc box sets are now packed as one archive automatically on request.
- The bot resumes sharing automatically after a restart, with no operator command needed.
- Fixed: a duplicate channel announcement per transfer; the advertised per-slot speed was wildly inflated; a case-sensitivity bug that could stall the queue after startup or a reload.

## v1.5.0 — The RAM Dictionary Queue & Inline RAR Packer Update

- The transfer queue moved from flat text to structured in-memory records.
- Albums are now packed into `.rar` on the fly rather than requiring a pre-packed archive, using a working directory that never touches the shared library.
- Locking added to prevent concurrent packs from fighting over CPU and disk.

## v1.4.x — Search, admin tooling and stability

- Search terms are sanitised so punctuation in a query no longer breaks matching, and matching became word-based and order-independent.
- Library reindexing can be triggered from IRC without shell access to the host.
- Permanent wildcard ban patterns, and IRC-based `!ban`/`!unban` commands.
- Live `!rehash` reloads every core module without restarting the process; a channel-sync step keeps joined channels matching configuration.
- Assorted stability fixes: a circular-import deadlock at boot, a name conflict between the bot's own `list` module and Python's built-in type, corrupted debug-log formatting.

## v1.1.0 – v1.2.0 — Early architecture

- A priority send path for system/admin messages that bypasses the normal flood-protection queue.
- The command parser rebuilt to stop duplicate replies in channels.
- Statistics tracking made accurate and durable across restarts.
