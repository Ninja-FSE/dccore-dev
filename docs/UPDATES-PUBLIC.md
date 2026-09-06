# Changelog

## Unreleased

- **Fixed: reported transfer speeds were far too low.** The timer kept running through the two seconds of settling after a transfer finished — the pause that lets the receiver close its file — so a 10 MB file that really moved at 46 MB/s was reported at about 4.5 MB/s. Small files were hit hardest, and the wrong figure also fed your speed record and your channel advert. The clock now stops when the last byte goes out. **Your transfers were always this fast; only the number was wrong.**

- **`scripts/send_benchmark.py`** measures the send loop over loopback, so "does this feel slow?" can be answered with a number. On a typical machine it reaches ~950 MB/s at 64 KB — well above any real link — which is how the above was tracked down.

- **The Console is on by default when your dashboard is only reachable from your own machine**, which is how it ships. It stays off when the dashboard is bound to your LAN — there it would put ban, rehash and update behind one password over plain HTTP, which is a choice to make rather than one to inherit. If you have already set `WEBUI_CONSOLE_ENABLED` either way, your setting is untouched.

- **The dashboard opens in your browser when the bot starts.** Only when it is bound to your own machine — a LAN-bound dashboard is as likely to be on a headless box, where opening a browser helps nobody. Turn it off with **`WEBUI_OPEN_BROWSER`**.

- **Packet size is now a menu, like mIRC's: 4, 8, 16, 32, 64 or 128 KB.** DCCore has always used 64 KB and never waits for the receiver to acknowledge each block, so both halves of mIRC's "fast send" were already on — but you can now try the others from Settings.

- **And if transfers feel slow at 64 KB, the packet size is probably not why.** On a fast link to a distant peer, what limits you is the socket send buffer, not the write size: at 100 Mbps with 100 ms round-trip, 64 KB of buffer caps you near 5 Mbps whatever the packet size. **`DCC_SEND_BUFFER`** lets you raise it. It is `0` (leave it to the operating system) by default on purpose — Windows and Linux both tune this automatically, and setting it by hand switches that off, so it is worth measuring rather than guessing.

- **The DCC packet size is now a setting — and it was already 16× mIRC's.** mIRC defaults to 4 KB per packet, which is why raising it there is so noticeable; DCCore has always sent 64 KB blocks and never waits for the receiver to acknowledge each one. **`DCC_BLOCK_SIZE`** exposes that number if you want to tune it, but raising it further usually changes nothing: past a few tens of kilobytes your speed is set by TCP and the link, not by this. It is clamped to 4 KB–1 MB, because the value is held once per running transfer.

- **`!rehash` no longer interrupts a transfer in progress.** It now stops starting new sends, waits for the ones already running to finish, reloads, and then lets the queue go again — so reconfiguring the bot no longer lands in the middle of somebody's download. If a transfer is stuck it gives up waiting after **`REHASH_TRANSFER_WAIT`** seconds (120 by default) and reloads anyway, saying so in the log, because a bot that cannot be reconfigured while one peer holds a socket open is worse.
- **Changing the IRC server or port now tells you a restart is needed.** The dashboard already warned you about settings a running bot cannot pick up — but the server and port were missing from that list, so those two saved silently and left you wondering why the bot was still connected to the old one. Settings a rehash genuinely does apply live, like the DCC port range and your channel list, are still not listed: a warning that appears too often is one you stop reading.

- **Fixed: `!rehash` could delete a user's queue.** Every rehash wakes the queue to let waiting users into free slots — and if that attempt could not go ahead because the bot has no usable public address set, the failure was counted against the *user*. Three rehashes and their queue was silently gone. A missing address is the bot's configuration, not the user's problem, so it no longer costs them anything; a genuinely missing file still does, which is what stops a dead entry being retried for ever.

- **Your held lists can keep themselves up to date.** Turn on **`AUTO_REFETCH_LISTS`** and the bot re-fetches a list when the bot that published it starts advertising a different one — using the same "has this moved on?" check the List Browser already shows you, so it never re-asks for a list that has not changed, and never acts when it cannot tell. Off by default, since it spends other people's bandwidth; `AUTO_REFETCH_INTERVAL_HOURS` sets how stale a list may get and `AUTO_REFETCH_MAX_PER_RUN` how many are asked for at once.

- **The Windows install guide is now seven numbered steps**, including how to check the install actually took — and what it means when `where python` prints only a `WindowsApps` path (that is Windows' placeholder, not Python).

- **Fixed: files requested from the dashboard were refused when they arrived.** Clicking a file in another bot's list sent the whole row — including the `::INFO:: 79.53MB` part — as the filename, so when the bot answered with the real name the request no longer matched and the transfer was dropped as unsolicited. The size is stripped off now, the way it already was for requests coming *in*.

- **The size limits on fetching from other bots can be switched off.** Set **`MAX_FETCH_FILE_SIZE`** or **`MAX_FETCH_LIST_FILE_SIZE`** to `0` and nothing you ask another bot for is refused for being too big. The limit on a fetched list archive has also gone from 10 MB to 64 MB, which was set on the same one-list sample that made lists get rejected in the first place. If a fetch is ever refused for size, the message now names the setting and says how to turn it off.

- **Fixed: a stale "Music directory" could stop the bot starting.** If you had served folders configured and that older setting still pointed at a drive you had unplugged, the daemon refused to boot — even though everything it actually serves was right there. It now judges by the folders you configured, and only refuses to start when *none* of them exist. It also stops claiming it "cannot serve anything" when you have folders set and that field left blank, and the field itself now says it is only used when no folders are set.

- **Downloads shows the newest first, and a failed fetch has a Redownload button.** The row you want is almost always the most recent one, so it is now at the top instead of below every completed transfer. And when a fetch fails or is rejected there is a **Redownload** button beside Delete — no more going back to the List Browser to retype the nick. The failed row stays where it is, so you can still see why it failed.

- **Fixed: lists from other bots were being refused for two wrong reasons.** A bot that publishes its list as a plain `.txt` had every fetch thrown away at the last step with "File is not a zip file" — those are read now. And the size limit on an incoming list was 20 MB, set from one 4 MB example; real lists in a busy channel run to 25–31 MB and were all rejected after downloading in full. The limit is now 128 MB and adjustable as **`MAX_LIST_TEXT_SIZE`**, and if a list ever does exceed it the message says so by name.

- **Folder headings in the list now read `D:\MEDIA\` instead of `D:\MUSIC\`.** That prefix is not a real path on your machine — it is a fixed label so the list looks the same whatever your server actually runs — and with film and series in there too, `D:\MUSIC\TV\...` said the wrong thing. **Lists your users already downloaded keep working**: rows pasted back with the old prefix are still understood.

- **More than one list, each over its own folders and its own channels.** You can now serve, say, a music list in one channel and a film list in another — each built from its own folders, each advertising its own count and size, and each answering `@yourbot`, `@find` and file requests only where you have said it belongs. A channel you have not given a list to is one the bot stays quiet in. Set them up under **Settings → Paths**; if you serve one list, nothing changes and the folder editor is exactly where it was.

- **Setup now offers to bring your OmenServe totals across.** If you are migrating, `configure.py` asks for your mIRC `scripts/vars.ini`, shows exactly what it found, and writes it only after you say yes — instead of you having to know the Stats page can do it. Getting the path wrong, or pointing it at a file with nothing in it, just says so and carries on with the rest of setup.

- **A list rebuild now tells you when two files share a name.** If the same filename sits in two folders, only the first copy can ever be sent — a request names a file, not a path, so anyone asking for the second one silently receives the first instead. The dashboard's **Tools ▸ Verify list** has always been able to show you these; now the rebuild says how many it found, so you hear about it without going to look.

- **`!rar` will no longer pack a folder of any size.** There was no limit: a request packed whatever the folder held, and the only thing that stopped it was the half-hour timeout — by which point a part-built archive was on disk and the one pack slot had been busy the whole time, with the person who asked told nothing. **`MAX_RAR_FOLDER_SIZE`** now bounds it, 10 GB by default, and an over-size request is refused straight away with a note that the files can still be requested by name. Set it to 0 if you want the old behaviour.

- **The dashboard now refuses an oversized request instead of reading it into memory.** Anything over 8MB — far more than any page here sends — is turned away before it is read, and the limit applies to the login page too. On a machine that is also sending files, memory the bot does not need to hold is memory transfers can use.

- **Fixed: the cross-list filter found nothing at all.** It was storing every file under an empty name, so typing anything returned no matches from any list. It works now — and if you already had lists downloaded before this update, they are indexed automatically when the bot next starts, rather than staying invisible to the filter until you re-fetch each one by hand.

- **Fixed: re-downloading a bot's list could leave the old copy searchable.** If you typed the nick with different capitalisation the second time, both copies stayed in the index, the stale one turned up in results, and nothing could clear it. Related: a bot whose name contains another's — `Bot` and `Bot-2` — could be credited with the other's matches.

- **Fixed: the bot list reset itself every four seconds while you were using it.** The filter's highlighting, your scroll position and keyboard focus were all lost on each refresh. Typing quickly could also leave an earlier search's results on screen under a newer term.

- **The List Browser now shows what you have already asked for.** A file you have requested is tagged **asked** while it is on its way and **have it** once it has arrived, so a long list does not leave you guessing whether you already queued something. A failed request is not tagged at all — the useful thing to do with one is ask again. Tags are per bot: two bots can hold a file of the same name, and asking one says nothing about the other.

- **Fixed: filtering before picking a bot left you unable to queue anything.** Results appeared with no tick boxes and no folder buttons, because the page was deciding what you could request from whichever list was selected rather than from the results in front of you.

- **Search every bot list you hold at once.** Type in the List Browser's filter box and matches from every list you have downloaded appear together, live as you type. Bots with nothing matching are crossed out in the sidebar, so you can see at a glance who has what. Click a bot while filtering to show or hide its results, or use “Show all lists” / “Show none”. Tick results from several different bots and queue them in one go. Two things worth knowing: the filter matches whole words and the beginnings of words, so typing the middle of a word will not find it (`@find` in the channel is unchanged); and the search index it needs is roughly as big again as the lists themselves, so ten large lists cost noticeably more disk than before. If the index is ever missing or damaged the filter simply stops working until your next fetch — nothing else is affected.

- **The List Browser now shows every bot as a row with a coloured dot.** Green means the list you hold is still what that bot advertises, amber means theirs has changed since you downloaded it, red means you have not downloaded it at all, and grey means there is not enough to say either way. Bots you have only seen advertising in the channel are listed too — clicking one puts its nick in the fetch box rather than opening an empty table. Every dot also says what it means when you hover it, and the legend names all four, so the colour is never the only thing telling you.

- **A hand-edited `data/known_bots.json` no longer breaks the List Browser.** One malformed entry in that file used to make the whole view fail to load, every time, until somebody found and fixed the file. A bad entry now costs its own row and nothing else — and that row comes back on its own within minutes, from the next advert that bot sends.
- **Fixed: a file in your library whose name starts with your bot's name could never be sent.** Anything called, say, `Muzik-Collection.rar` when your list is named after `Muzik` was looked for among your list files instead of in your library — so it was advertised, counted, requested, and answered "file not found", every time, for ever.

- **Fixed: some bot names and folder paths made the bot report an empty list.** If the name your lists are built under, or the folder you keep them in, happened to contain `-RAR-` or `-FULL-`, the bot skipped over its own list: `@find` answered "no list found" and your advert published 0 files while the list sat there complete.

- **Fixed: `!update` reported “0 files, added 0” every time.** It was reading the new film list instead of your music list. It now counts across every list you publish, so `!update` and the channel advert always agree — and the warning that tells you your library has SHRUNK works again, which is what you want when a drive comes back half-mounted.

- **Fixed: `!rar` packed folders it was told not to.** `RAR_EXTENSIONS` decided which folders got a row in the album list, but the bot would still pack any folder somebody named — including a film folder, whose path the new film list publishes. Those are refused now, and the files inside are still requestable by name.

- **Fixed: deleting a film and rebuilding the same day left it in the list.** It still turned up in searches and still counted towards your advertised total, while being absent from the archive people actually download.

- **Film and series now get their own list.** One scan publishes two: your music list and a separate one for video, both inside the same archive people already get by typing your bot's name — nothing new to learn, and no mixed list of tracks and episodes to sort out afterwards. If your films and music already live in separate folders you may not want this: set `SEPARATE_VIDEO_LIST = No` and everything goes back into one list. The film list is only created when you actually have video.

- **Only album folders can be packed with `!rar` now.** A folder used to become packable just by containing something in the list, which was fine when the list only held `.mp3` and `.flac` — but with everything listed, a single stranger could paste one line and have your bot spend an hour packing a forty-gigabyte film folder, using your CPU, your disk and one of your transfer slots. `RAR_EXTENSIONS` decides what makes a folder packable; films are still listed and still directly requestable by name.

- **Your whole library goes into the list now, not just `.mp3` and `.flac`.** Anything else — video, `.m4a`, `.ogg`, artwork, liner notes — used to be skipped, so a library of it scanned to an empty list and said nothing about why. Everything is listed now, and the new `LIST_IGNORED_EXTENSIONS` setting names what to leave out. Write it however you like: `db,ini,tmp`, `db, ini, tmp` or `.DB, .INI` all mean the same thing. It ships skipping only what is never a real file — `Thumbs.db`, `desktop.ini`, shortcuts, and half-finished downloads. **Worth knowing:** everything under your music directory is now offered to anyone who asks, so keep out of it whatever should not leave.

- **The pre-flight check counts the same files the list build will.** It had its own copy of the old two-format rule, so it could report a healthy library and then build an empty list.
- **Coming from OmenServe? Bring your totals with you.** The Stats page can now read your files sent, bytes sent and speed record straight out of mIRC's `scripts/vars.ini` — choose the file, see exactly what would change, and confirm. Your counters come from the OmenServe add-ons rather than OmenServe itself, so it takes whatever is there and leaves the rest alone; nothing you are missing, and nothing sitting at zero, overwrites a total you already have. If any figure cannot be written it says which, rather than reporting success. Only the counter lines ever leave your machine — the rest of that file, including your nicks and passwords, is filtered out in the browser before anything is sent. Note it **replaces** your current figures rather than adding to them, and the page says so when you have some.
- **Asking for your list from AutoQ's menu now works.** Its "Get Listfile" button sends `@yourbot` with a small tag added on the end, and the bot only answered a bare `@yourbot` — so it stayed silent, which looks exactly like a bot that is down. It answers now. Note "Que Status" in the same menu still does not work: it sends `yourbot-que` without the `@`, and `@yourbot-que` typed by hand is the form the bot reads.

- **If your users queue with AutoQ, check their mIRC accept list.** AutoQ silently drops any list row whose file type is not in mIRC's own accept list, and it only adds `*.mp3` and `*.rar` itself. Now that lists carry every file type, a `.flac` or `.mkv` row pasted into a queue just vanishes with nothing said. [INSTALL.md](docs/INSTALL.md) has the setting to change.
- **Expired bans are cleared even if the person never comes back.** A timed ban used to be forgotten only when that same nick asked for something again — which is exactly what a banned person stops doing — so `data/bans.txt` grew one dead row per flooder and never shrank. Nothing changes about who is banned or for how long; the expired ones are now tidied away on their own.

- **The List Browser tells you when a bot's list has moved on.** Pick a bot whose list you hold and, if what they are advertising now differs from what they advertised when you downloaded it, you get a line saying so with both sets of numbers — so you can decide whether to fetch it again. Lists in a busy channel go stale by months, and until now nothing said which.

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
