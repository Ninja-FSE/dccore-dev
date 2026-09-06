# =====================================================================
# DEFAULTS.PY - CENTRAL CONFIGURATION FOR THE DCCORE DAEMON
# =====================================================================
# Renamed from config.py as part of #170's RFC: every module that reads a
# setting still does `import defaults as config` and reads `config.X` -
# see any other module's own import line for why the internal name did not
# change along with the file. This file's own job did not change either: it
# is the tracked, always-present base and type declaration for every
# setting (~60 of them), which admin_config.py and settings.conf below then
# optionally override. Without this file, an operator would have to type
# out every single setting by hand just to get a working bot.
# =====================================================================
# The live in-memory containers this file used to define are in runtime.py
# now, and are bound below in section 8. See that module's docstring for why:
# !rehash reloads THIS file, which reset every one of them.
import os
import runtime

# ---------------------------------------------------------------------
# 1. SYSTEM AND GLOBAL ENGINE SETTINGS
# ---------------------------------------------------------------------
DEBUG_MODE: bool    = False
SCRIPT_VERSION: str = "DCCore v1.11.0"

# Where this bot came from. Defined once because two things say it: the CTCP
# VERSION reply, and the header of every generated list. Before this there was
# no project URL anywhere in the tree, so anyone who received a list had no way
# to find out what produced it.
PROJECT_URL: str = "https://github.com/Ninja-FSE/dccore"

# Answer CTCP VERSION with SCRIPT_VERSION and PROJECT_URL. Operators who would
# rather not advertise a version can turn this off; the bot then ignores the
# query exactly as it did before, rather than answering with something evasive.
#
# The reply is a NOTICE, never a PRIVMSG. That is the CTCP rule, and the reason
# is practical: two bots that both answer CTCP with a privmsg answer each other
# forever. It is also why nothing here lands in a channel - the reply goes
# straight back to whoever asked, and no one else sees it.
CTCP_VERSION_REPLY: bool = True
# Names every generated list file ("<LIST_BASE_NAME>-<date>.txt" and its
# .zip/.rar counterparts). Automatically takes NICKNAME's own value once
# NICKNAME is set, unless this is given an explicit value of its own first -
# see the "DERIVED VALUES" section below. Only worth setting here if the
# list should be named differently from the bot's own nickname.
LIST_BASE_NAME: str = "DCCore"

# ---------------------------------------------------------------------
# 2. IRC NETWORK AND CHANNEL SETTINGS
# ---------------------------------------------------------------------
# SERVER keeps a real, working default on purpose - "irc.undernet.org" is
# correct for essentially every operator of an Undernet file server, not one
# operator's identity to avoid. It is NOT in settings_file.REQUIRED for
# exactly that reason - see REQUIRED's own comment.
SERVER: str        = "irc.undernet.org"
PORT: int          = 6667
# NICKNAME, ADMIN_NICK and CHANNEL are None - not a real value - because
# they ARE in settings_file.REQUIRED: oserve.startup() refuses to boot while
# any of them is still blank, so there is no shipped value here for a
# copy-paste install to silently inherit and run under somebody else's
# identity. See REQUIRED's own comment in settings_file.py for the full
# reasoning, and RAR_BINARY above for the same "None means unset" convention
# this already used before REQUIRED existed.
NICKNAME: str      = None
ALT_NICKNAME: str  = "DCCore_"
ADMIN_NICK: str    = None
CHANNEL: str       = None
# Ships BLANK, and that is a deliberate reversal of #171's "#dccore-debug".
#
# That default was fine while this project was two operators who knew each
# other: a shared debug room is convenient. It stops being fine the moment the
# repository is public. irc.py joins this channel automatically on connect and
# streams the daemon's internals into it - bans, pack failures, transfer
# detail, nicknames - so every adopter of a public DCCore would broadcast their
# own operation into one room, and read everybody else's.
#
# Blank means "no debug channel", not "misconfigured": irc.py already guards
# the JOIN with `if debug_chan:` and says so when there is none, and both
# getattr call sites already fall back to ''. An operator who wants one names
# their own, which is the only answer that is right for more than one install.
#
# Still not in settings_file.REQUIRED - having no debug channel is a perfectly
# good state to run in, unlike having no NICKNAME.
DEBUG_CHANNEL: str = ""

# The single channel a "search all bots" broadcast (@find) goes into - see
# webserver.py's POST /api/search/broadcast. Deliberately ONE channel, never
# all of them: broadcasting into every channel this bot has joined multiplies
# the disruption to every other operator sharing those channels, for one
# search. Defaults to the first entry of CHANNEL above; override explicitly
# here (or in admin_config.py) if that is not the right one.
# Derived from CHANNEL - but NOT here. See "DERIVED VALUES" at the end of this
# file: computing it at this point captures the tracked default above and
# silently ignores an operator's own CHANNEL. None means "derive it below";
# setting it explicitly, here or in admin_config.py, still wins.
BROADCAST_SEARCH_CHANNEL: str = None

# ---------------------------------------------------------------------
# 3. FILESYSTEM, PATHS AND TEXT STORES
# ---------------------------------------------------------------------
PAUSE_ON_UPDATE: bool = True  # MAINTENANCE SWITCH: when True the bot pauses ALL sharing and searching during !update
# None, not a real path - a shipped literal path would let a copy-paste
# install silently inherit somebody else's actual music folder path. Unlike
# NICKNAME/CHANNEL/ADMIN_NICK, FILE_DIRECTORY is NOT in settings_file.
# REQUIRED (see its own comment): the daemon boots fine while this is still
# blank, specifically so the web dashboard's own Settings page can be the
# place that sets it, rather than needing it typed blind before the
# dashboard is even reachable. oserve.startup() still refuses to start on a
# value that IS set but does not exist - only "not chosen yet" is fine.
FILE_DIRECTORY: str   = None
RAR_ENABLED: bool     = True        # Off refuses every !rar request with a notice; ordinary single-file transfers are unaffected
RAR_BINARY: str       = None       # None = look for rar/rar.exe on PATH (and WinRAR's install dir)
TMP_ZIP_DIR: str      = "./data/tmp_zips"
LOCAL_LIST_DIR: str   = "./lists"

# How the master list is handed to somebody who types "@<nick>": as the plain
# text file, packed into a .zip, or packed into a .rar. OmenServe has offered
# the same three for years and people's clients differ in what they open
# without complaint, which is the whole reason it is a choice.
#
# All three are always available. This is NOT tied to RAR_ENABLED: that switch
# governs whether the bot will pack an album folder on request, and packing the
# list is the operator's own machine doing one small job at build time.
#
# "rar" needs a rar binary (see RAR_BINARY). If none can be found the build
# falls back to .zip and says so, rather than leaving the bot with no list to
# serve at all.
LIST_FORMAT: str      = "zip"      # "txt", "zip" or "rar"

# EVERY file under FILE_DIRECTORY goes into the list, except the extensions
# named here. Comma-separated, with or without the leading dot, matched
# case-insensitively - ".DB", "db" and ".db" are the same thing. Blank means
# nothing is skipped.
#
# The walk used to ask `endswith(('.mp3', '.flac'))`, which meant a library of
# anything else - video, .m4a, .ogg - scanned to nothing and published an
# empty list while reporting success. Naming what to KEEP could never be
# right: the set of things people serve is open-ended, and every format left
# out is silently invisible. Naming what to SKIP is a short, closed list, and
# the failure mode of getting it wrong is a file listed that need not have
# been, rather than a library that does not appear.
#
# OmenServe has the same shape (`Exclude = .mpu,.db`), so this is also the
# form operators coming from it already know.
#
# The default is only what is never a served file: Windows and browser
# droppings, and the partial-download suffixes. Covers, .nfo, .cue and
# playlists are NOT skipped - people do serve them, and an operator who does
# not want them can say so here. Add to this rather than expecting the
# shipped list to guess.
#
# WORTH KNOWING: "every file" means exactly that. Anything sitting under
# FILE_DIRECTORY is offered to anyone who asks - a stray backup, a document,
# a private note dropped in the tree by accident. That directory is the
# public face of the bot; keep out of it whatever should not leave.
#
# A note before relying on the list from a script: every row is written
# "!<nick> <filename>  ::INFO:: <size>". This project's own parser (list.py)
# splits on the ::INFO:: marker regardless of extension, as do the OmenServe
# bots the convention came from. AutoQ.mrc is reported to strip that tail
# only for .mp3 and .flac (see update_list.py's note at the row write), so a
# row in any other format may reach it with the size still attached. That
# costs an AutoQ user a failed request they can retry by hand; it does not
# affect anyone reading the list themselves.
LIST_IGNORED_EXTENSIONS: list = [
    ".db", ".ini", ".lnk", ".url",          # Windows and shell droppings
    ".tmp", ".part", ".crdownload", ".!ut",  # downloads still in flight
]

# Publish film and series as a SEPARATE list from the music, instead of one
# list carrying both.
#
# Off is a real answer, not a fallback. There are two ways to end up with a
# music list and a film list, and they suit different libraries:
#
#   - This switch, when audio and video are mixed together in the same
#     folders and only the file itself says which is which.
#   - Several lists, each over its own set of folders, when the library is
#     already sorted that way on disk. That is the roadmap's multi-list
#     feature, and for an operator who keeps films and music apart it is the
#     better route - the folders already carry the answer, and the lists then
#     differ in more than content type.
#
# Turning this off gives the single combined list again.
SEPARATE_VIDEO_LIST: bool = True

# WHICH LIST a file goes into, when SEPARATE_VIDEO_LIST is on. One scan
# publishes two: the music list, and a separate one for film and series.
# Same comma-separated form as the setting above - dots optional, spacing
# free, case ignored.
#
# They travel together. "@<botnick>" already hands out ONE archive containing
# several text files (the master list and the !rar album list), so the video
# list is a third member of the same download: no new trigger, nothing for
# anyone to learn.
#
# The split exists because a list carrying tracks and episodes mixed together
# is one a person or a script has to sort out afterwards. It follows the
# pattern already here rather than inventing one - the !rar album list has
# been a separate file built from the same walk since long before this.
#
# Anything that is NOT one of these goes into the music list, including a
# file with no extension at all. That is the rule for artwork, cue sheets and
# notes, which sit beside the tracks they belong to; a film's subtitles land
# there too, which is the one rough edge of deciding per file rather than per
# folder. Per file is deliberate: a folder holding both an album and a video
# should not have to pick.
#
# The video list is only published when there is video to put in it, so a
# music-only library never gains an empty file it has no use for.
LIST_VIDEO_EXTENSIONS: list = [
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".mpg", ".mpeg",
    ".flv", ".webm", ".ts", ".m2ts", ".vob", ".divx", ".ogv", ".3gp",
]

# WHICH FOLDERS may be packed on demand with "!rar <folder>".
#
# A folder earns a !rar row only if it holds one of these. Everything else is
# still listed and still directly requestable by name - this decides packing,
# nothing else.
#
# It is a set of its own, and not simply "whatever is in the list", because
# for a while it WAS that: a folder became packable if it held any file the
# scan indexed. While the scan only took .mp3 and .flac that read as "album
# folders", and it was fine. The moment the scan took everything, every
# folder in the library became packable - including one holding a single
# text file, and including a season of a series that is tens of gigabytes.
#
# There is no size cap anywhere on packing (see the roadmap), so an
# unbounded amount of CPU, disk and one transfer slot sat behind a line
# anybody in the channel could paste. RAR_ENABLED was the only defence and
# it is all-or-nothing: an operator who wanted albums packable had to accept
# films packable too.
#
# An album is a genuine multi-file collection - tracks, a cover, a cue sheet
# - which is what makes packing it useful. A film is one large file that can
# simply be requested by name.
RAR_EXTENSIONS: list = [
    ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac", ".wma",
    ".ape", ".wv", ".alac", ".aiff", ".aif",
]

# Where files fetched FROM other bots (dcc_fetch.py) land. Deliberately
# separate from FILE_DIRECTORY: that directory is the served library, scanned
# by update_list.py and offered to everyone via @find/!<nick> - fetched files
# must never be reachable through that path. They are dashboard-download-only
# (GET /api/fetch/<id>/download in webserver.py).
FETCHED_FILES_DIR: str = "./data/fetched"

# Safe, normalised paths into the data/ subdirectory
BANS_FILE: str      = "./data/bans.txt"
STATS_FILE: str     = "./data/stats.txt"
HARD_BANS_FILE: str = "./data/hard_bans.txt"

# The ordered set of folders served, once there is more than one of them
# (#164). JSON rather than a settings.conf list for the reason KNOWN_BOTS_FILE
# gives - the record has more than one field - and because settings_file.py
# refuses a list entry containing a comma, which music paths routinely have
# ("D:\Rock, Metal").
#
# An OVERRIDE, not a replacement: with no file here, library.folders() returns
# one folder built from FILE_DIRECTORY, which is every install today. So an
# upgrade migrates nothing, and this file appears the first time an operator
# saves a folder set from the dashboard.
LIBRARY_FOLDERS_FILE: str = "./data/library_folders.json"
# Where the served LISTS are defined (#26). Absent on every install today,
# which resolves to one implicit list over LIBRARY_FOLDERS_FILE/FILE_DIRECTORY
# - so nothing changes until an operator defines more than one.
LISTS_FILE: str = "./data/lists.json"

# The operator's own banner, printed at the top of every generated list above
# the bot's identity line. Free-form: several lines, ASCII art, a channel name,
# whatever should greet whoever opens the file. Missing or empty means no
# banner, which is the normal state for most installs.
#
# A file rather than a settings.conf value because multi-line ASCII art does
# not survive "key = value", and editing it there would be miserable.
LIST_HEADER_FILE: str = "./data/list_header.txt"

# Ceiling on the above. Someone will eventually point LIST_HEADER_FILE at the
# wrong file, and without a cap that staples an arbitrary number of megabytes
# onto every list request. Past this the banner is truncated and the run says
# so, rather than shipping it silently.
LIST_HEADER_MAX_BYTES: int = 8192

# Other bots seen advertising in our channels, and what each last published
# about its own list. JSON rather than the column format the files above use:
# the record has several fields and will grow, and stats.txt's fixed seven
# columns is exactly the shape that turns "add a field" into a migration.
KNOWN_BOTS_FILE: str = "./data/known_bots.json"

# The cross-list search index: every fetched bot list in one SQLite database,
# so the List Browser's filter can search all of them at once.
#
# SQLite rather than JSON because this one is not tens of rows - ten held
# lists is millions - and re-reading the list FILES to search them is out of
# reach rather than merely slow: #133 measured that at two to eleven seconds
# per keystroke. sqlite3 is stdlib, so the no-third-party-packages property
# holds. See list_index.py for why it is FTS5 specifically.
#
# EXPECT IT TO BE LARGE. Roughly the size of the lists again - four million
# rows measured at 452MB. Built as each list is fetched, and safe to delete:
# the filter stops working until the next fetch rebuilds it, and nothing else
# reads it.
LIST_INDEX_FILE: str = "./data/list_index.db"

# One row per thing this bot has ever sent, {relative path or archive name ->
# {name, kind, count}}. Feeds the Stats page's "Most downloaded" table. Not
# bounded on purpose: a bot can only send what it shares, so the row count is
# capped by the library itself.
DOWNLOAD_COUNTS_FILE: str = "./data/download_counts.json"

# Which bots we hold a fetched list for, and where it lives on disk - one
# small entry per bot ("bot", "fetched_at", "list_path", "entry_count",
# "source_zip"), not the parsed list itself. Without this, the extracted
# files under FETCHED_FILES_DIR survived a restart untouched but the File
# Lists switcher had no memory of them at all, since config.fetched_bot_lists
# is otherwise populated only by a live fetch completing. Same JSON-file
# treatment as KNOWN_BOTS_FILE, for the same reason.
FETCHED_BOT_LISTS_FILE: str = "./data/fetched_bot_lists.json"

# Every 'complete'/'failed' cross-bot fetch row - the dashboard Downloads
# table's only record of a finished fetch, and what its Delete button acts
# on. Without this, config.fetch_queue was in-memory only: a file finished
# downloading, survived on disk under FETCHED_FILES_DIR untouched, but its
# row (and therefore its Download/Delete buttons) vanished on the very next
# restart. Same JSON-file treatment as FETCHED_BOT_LISTS_FILE, for the same
# reason - in-flight rows (pending/offered/listening/receiving) are
# deliberately never written here, since none of those can mean anything
# once the process that was driving them is gone.
FETCH_HISTORY_FILE: str = "./data/fetch_history.json"

# ---------------------------------------------------------------------
# 4. CHANNEL ADVERTISING (THE ADVERT CLOCK)
# ---------------------------------------------------------------------
ANNOUNCE_INTERVAL: int = 300     # Time between each channel advert, in seconds

# ---------------------------------------------------------------------
# 5. LIMITS, SLOTS AND QUEUE CONTROL
# ---------------------------------------------------------------------
MAX_DCC_SLOTS: int      = 3      # Maximum simultaneous live downloads
MAX_USER_QUEUE: int     = 100    # Most files a single user may queue
MAX_GLOBAL_QUEUE: int   = 1000   # Most files across every queue combined
MAX_SEARCH_RESULTS: int = 5      # Maximum result lines sent in reply to an @find
MSG_DELAY: float        = 5.0    # Delay in seconds for the ordinary message queue
DEBUG_MSG_DELAY: float  = 0.5    # Pause between each line sent to the debug channel

# Port range for DCC sends (must be open on the firewall and router)
# ---------------------------------------------------------------------
# ADMIN CONSOLE (DCC CHAT)
# ---------------------------------------------------------------------
# Empty list = console disabled, and every DCC CHAT request is ignored.
#
# Each entry is a HOST pattern. It may be written bare ("operator.users.undernet.org")
# or in the familiar IRC form ("*!*@operator.users.undernet.org"); either way only the
# part after the last "@" is used. The nick and ident halves are discarded on
# purpose - the ident is supplied by the client and anyone can set theirs to
# "operator", so constraining it grants nothing. Only the host is issued by the server.
#
# On Undernet, log into X and set usermode +x. The server then replaces your host
# with "<your-account>.users.undernet.org", which nobody else can obtain. That
# host IS the proof of your services login.
#
# Put the real values in admin_config.py, which is gitignored, NOT here.
ADMIN_HOSTMASKS: list = []
# Generated with:  python adminchat.py
ADMIN_PASSWORD_HASH: str = ""

# How the DCC CHAT connection gets made:
#
#   "auto"     dial the client, and listen instead if that fails.  (default)
#   "listen"   always listen and offer the connection back.
#   "connect"  only ever dial the client; never listen.
#
# "auto" is right for most setups. Choose "listen" when the client is behind a
# VPN, a router that does not forward the port, or a firewall that drops rather
# than rejects - all of which show up as a TIMEOUT on the dial and then cost the
# full connect timeout on every login before the fallback takes over. The bot's
# own listener is already proven reachable by every DCC SEND it does.
ADMIN_CHAT_MODE: str = "auto"

# Whether !ban, !unban, !rehash, !update and !clearqueue still work when typed in
# a channel or a private message.
#
# Left ON. The console is new, and locking yourself out of every admin command
# because a hostmask has a typo in it is a bad first experience. Turn it off once
# the console has proved itself - at which point admin authority rests entirely
# on the services host plus the password, and no longer on a nick anyone can take
# while you are offline.
#
# The user commands (!list, !ping, !debugnames, @find, the queue triggers) are
# not affected by this.
ADMIN_CHANNEL_COMMANDS: bool = True

# ---------------------------------------------------------------------
# WHERE RUNTIME REPORTS GO
# ---------------------------------------------------------------------
# announce.send_debug() is the daemon's running commentary - transfers, joins,
# bans, pack failures. Both destinations are on by default.
#
#   DEBUG_TO_CHANNEL   the coloured line in DEBUG_CHANNEL, as it has always been
#   DEBUG_TO_CONSOLE   the plain text in an attached DCC admin console
#
# Set DEBUG_TO_CHANNEL = False once the console is doing the job, and the
# daemon's internals stop being published to a channel other people can sit in.
#
# Neither switch can lose a line: if the channel is off and no console happens to
# be connected, send_debug falls back to stdout, so the LXC console and the
# journal always have it. That case - something going wrong while nobody is
# watching - is the one worth protecting.
DEBUG_TO_CHANNEL: bool = True
DEBUG_TO_CONSOLE: bool = True

# The two side files update_list.py publishes alongside the master list, holding
# the human-readable total size and the raw byte count that the channel advert and
# @<nick>-que read back. Named here rather than as a literal in both list.py and
# update_list.py: that split literal is the same shape as issue #34, where the
# reader and the writer of speed_record.txt agreed only by coincidence.
#
# The "flac-serv" prefix these carried was one operator's server name, and it
# was kept because renaming alone would orphan the stats on every existing
# deployment until the next successful !update - the advert would publish "0B"
# and @<nick>-que would report no size in the meantime.
#
# So it is not a rename. db.migrate_legacy_side_files() moves the old files to
# these names at startup, and only when the setting is still at the default
# below, so an operator who chose their own name is left alone.
# A DOT and not a dash, which is not cosmetic: find_latest_list() globs
# LIST_BASE_NAME + "-*.txt", and on a case-insensitive filesystem
# "dccore-size.txt" matches "DCCore-*.txt" and sorts AFTER the dated list - so
# the daemon would have picked its own size file as the master list. Caught by
# tests/test_long_paths.py, which read "15.59KB" where a track should have been.
LIST_SIZE_FILE: str     = "dccore.size.txt"
LIST_RAWBYTES_FILE: str = "dccore.rawbytes.txt"

# How many bytes are read and written per pass of a DCC send, in bytes.
#
# mIRC calls this the packet size and defaults it to 4 KB, which is why raising
# it there is so noticeable. DCCore has always used 64 KB - sixteen times that
# - and does not wait for the receiver to acknowledge each block before sending
# the next, which is the other half of what mIRC's "fast send" does. So the
# thing operators come here looking for is already on; this only exposes the
# number.
#
# RAISING IT FURTHER USUALLY CHANGES NOTHING, and it is worth saying so rather
# than implying a free win. Past a few tens of kilobytes the limit is TCP's own
# window and the link, not how much this loop hands the kernel at a time - the
# bytes are already in flight while the next read happens. Where it can help is
# a very fast, very high-latency link; where it can hurt is memory, since each
# concurrent transfer holds one buffer of this size.
#
# Clamped to 4 KB - 1 MB when read (see dcc.dcc_block_size), because a value
# of 0 would busy-loop and a value of 500 MB would hold half a gigabyte per
# transfer for no gain.
DCC_BLOCK_SIZE: int = 65536      # 64 KB - 16x mIRC's default; 4096..1048576
DCC_PORT_START: int = 55000
DCC_PORT_END: int   = 55010

# ---------------------------------------------------------------------
# CROSS-BOT FILE FETCH (dcc_fetch.py - receiving files FROM other bots)
# ---------------------------------------------------------------------
# Deliberately separate from MAX_DCC_SLOTS above: that governs OUR outbound
# SENDs to people requesting from us. Conflating the two directions would let
# outbound leech traffic (us fetching from others) starve our own serving
# capacity, or vice versa.
MAX_FETCH_SLOTS: int        = 3        # Max simultaneous in-flight/offered fetches
# How long a finished (complete/failed) cross-bot fetch stays in the Downloads
# table. Age is the primary rule because that table is a recent record of what
# happened, not an archive; the row cap below is only a backstop for a burst of
# activity inside the window. Pruning forgets the ROW, never the downloaded
# file - that stays under FETCHED_FILES_DIR. 0 disables either rule.
# Ask again, by itself, for a held list whose owner's advert says it has moved
# on (#302). OFF by default: it spends other people's bandwidth and other
# people's transfer slots, which is an operator's decision to make rather than
# one to inherit.
#
# The ADVERT decides, not a timer - #286 already worked out what "moved on"
# means. A timer alone would re-ask every bot for a list we already have.
AUTO_REFETCH_LISTS: bool = False
# How stale a held list may get before it is re-asked for, in hours. Not how
# often the check runs (that is hourly); this is the floor on how often any one
# bot is asked, so a bot rebuilding hourly is not re-fetched hourly.
AUTO_REFETCH_INTERVAL_HOURS: int = 24
# Most lists to ask for in one sweep. A bot back after a month offline has a
# lot of stale lists, and asking for all of them at once is a burst of
# outbound requests nobody asked for. The rest go next sweep, oldest first.
AUTO_REFETCH_MAX_PER_RUN: int = 3
# How long a rehash waits for transfers in flight to finish before reloading
# anyway, in seconds (#310). A transfer can sit idle for as long as the far
# end keeps its socket open, so this cannot be unbounded: a bot that cannot
# be reconfigured while one stuck peer holds a socket is worse than one that
# occasionally interrupts a transfer. 0 reloads immediately, as before.
REHASH_TRANSFER_WAIT: int   = 120
FETCH_HISTORY_DAYS: int     = 30       # Days a finished fetch stays in the history
FETCH_HISTORY_MAX_ROWS: int = 500      # Hard cap on finished rows, whatever their age
# Interacts with flood protection: a bot's DCC SEND offers are metered like any
# other command, so this must stay well below MAX_REQUESTS (per REQUEST_WINDOW)
# or a bot answering your own fetch requests can trip the flood gate and be muted.
# 0 DISABLES IT - #302 asked for these limits to go entirely, and switching
# them off is the same outcome for the operator who wants that without
# taking the choice from everyone else. Defensible here because a fetch is
# SOLICITED: only an offer matching a row this operator created is ever
# accepted, so it is their own request landing on their own disk.
MAX_FETCH_FILE_SIZE: int    = 200 * 1024 * 1024   # 200 MB - reject the offer before we even connect; 0 = no limit
# The largest EXTRACTED list text this bot will parse from a peer, in bytes.
# Every "!" line in it becomes a retained row, so this bounds memory rather
# than disk. It was a fixed 20 MB, set from this operator's own 4 MB list -
# and three real lists in one channel arrived at 25-31 MB and were refused.
# The right value depends on OTHER people's libraries, which is why it is a
# setting now. 0 restores the default.
MAX_LIST_TEXT_SIZE: int     = 128 * 1024 * 1024   # 128 MB - 4x the largest list actually seen

# The largest folder !rar will pack, in bytes. 0 means no limit.
#
# NOTHING BOUNDED THIS BEFORE. A request packs whatever the folder holds, and
# the only thing that ever stopped it was RAR_TIMEOUT - by which point the
# archive is already on disk in TMP_ZIP_DIR, the pack slot has been held for
# half an hour, and the requester has had no answer. The film list made it
# reachable rather than theoretical: it publishes folder headings inside the
# archive every user downloads, and a heading can be pasted straight back as a
# request, so a folder deliberately kept out of the album list is nameable by
# anyone in the channel.
#
# 10 GB is chosen to refuse the pathological case without refusing anything
# real: a FLAC album is a few hundred megabytes and a large box set is a few
# gigabytes, while the folders this exists for are tens or hundreds. An
# operator who genuinely serves larger albums can raise it or set 0.
MAX_RAR_FOLDER_SIZE: int = 10 * 1024 * 1024 * 1024   # 10 GB - refuse to pack more than this
# A "list" request_type row (a fetched master-list zip, see list_fetch.py) is a
# text index, never a real download - a whole 1.21TB/47,420-file library
# compresses to a few MB. MAX_FETCH_FILE_SIZE's 200MB let a hostile "list" offer
# claim up to that much, and by the time zipfile.ZipFile() opened it, the whole
# central directory (one ZipInfo per entry, eagerly, before any guard can refuse
# anything) was already parsed - hundreds of MB of RAM and several seconds spent
# on an archive that should have been refused at admission (#162 finding #10).
# Enforced BEFORE connecting, same as MAX_FETCH_FILE_SIZE/MAX_FETCH_FOLDER_FILE_SIZE.
#
# 10MB was 'generous over any real master-list zip' on the same evidence
# that put the text ceiling at 20MB: one 4MB list. Real lists in one
# channel run to 31MB of text, and a zip of one is several MB - close
# enough to this that the next library along lands on it. 64MB, and 0
# disables it.
MAX_FETCH_LIST_FILE_SIZE: int = 64 * 1024 * 1024  # 64 MB - the archive, not the text inside it; 0 = no limit
FETCH_TRANSFER_TIMEOUT: int = 600      # Seconds - total wall-clock per transfer (against a slow "drip" that keeps resetting the idle timeout)
FETCH_OFFER_TIMEOUT: int    = 60       # Seconds an "offered" row waits for a DCC SEND before it's marked failed

# A "folder" request_type row (dcc_fetch.py) asks another bot to pack a whole
# folder/album as .rar via its own "!rar" convention and shares the same
# MAX_FETCH_SLOTS pool as every other fetch - it gets its own, separate
# timeout and size cap instead, below.
#
# FETCH_FOLDER_OFFER_TIMEOUT: how long a "folder" row waits for the other
# bot's DCC SEND before failing - much longer than FETCH_OFFER_TIMEOUT
# because the other bot has to run its own !rar packing pipeline first.
# config.fetch_queue is in-memory only (not persisted across a restart), so a
# folder row waiting this long is lost on restart same as any other
# offered/listening row - just for longer.
FETCH_FOLDER_OFFER_TIMEOUT: int = 1800
# MAX_FETCH_FOLDER_FILE_SIZE: 2GB - separate, larger cap than
# MAX_FETCH_FILE_SIZE for a whole packed album/discography archive.
MAX_FETCH_FOLDER_FILE_SIZE: int = 2147483648
# FETCH_FOLDER_TRANSFER_TIMEOUT: a "folder" row's own wall-clock ceiling for
# _run_transfer(), separate from FETCH_TRANSFER_TIMEOUT. That value is sized for
# the 200MB MAX_FETCH_FILE_SIZE cap; a folder row's 2GB cap is 10x larger but
# used to inherit the SAME 600s ceiling, so a 900MB discography at a modest
# 800KB/s died at t=600s having received ~480MB, threw it all away (no resume),
# and burned a MAX_FETCH_SLOTS slot for ten minutes doing it - every retry
# identical (#162 finding #11).
#
# 3600 UNDERSHOT ITS OWN GOAL (#223): the cap grew 10.24x (200MB -> 2GB) but
# the timeout only 6x (600s -> 3600s), so the IMPLIED throughput floor a peer
# has to sustain went UP for a folder, not down - 349,525 B/s for a plain
# file, 596,523 B/s for a folder. A peer exactly fast enough to complete a
# maximum-size plain file could not complete a maximum-size folder, which is
# the opposite of "give a large discography more room to be slow". Scaled by
# the same 10.24x the size cap was: 600 * (2147483648 / MAX_FETCH_FILE_SIZE).
FETCH_FOLDER_TRANSFER_TIMEOUT: int = 6144

# How often a new @find broadcast (POST /api/search/broadcast) is allowed to
# start. Independent of the UI - courtesy to other bots/operators on a shared
# public channel, not just a UI detail.
BROADCAST_SEARCH_COOLDOWN: int = 30     # Seconds

# ---------------------------------------------------------------------
# 6. ANTI-FLOOD AND AUTOMATIC PROTECTION
# ---------------------------------------------------------------------
MAX_REQUESTS: int   = 10       # Most commands (search or file) per time window
REQUEST_WINDOW: int = 5       # Size of the rolling time window, in seconds
MUTE_TIME: int      = 30       # Mute in seconds on the first flood violation
# Escalation ban, in seconds, for someone who keeps flooding while already
# muted. This used to expire at local midnight, which made the punishment
# depend on the clock rather than the offence: trip it at 00:01 and you were
# banned for nearly a day, trip it at 23:59 and you were banned for seconds.
# Same offence, arbitrary sentence. A fixed duration is predictable for the
# operator and for the person banned. Note it does NOT self-escalate the way
# midnight accidentally did - repeat offenders are a hard-ban case (!ban).
FLOOD_BAN_SECONDS: int = 3600  # Ban in seconds when someone floods while muted
MAX_SEND_FAILS: int = 3        # Attempts per queued file before it is dropped (see dcc.release_queue_entry)
RAR_TIMEOUT: int    = 1800     # Longest a rar packing run may take, in seconds, before it is abandoned
LIST_UPDATE_TIMEOUT: int = 1800  # Longest a !update / update_list.py run may take, in seconds, before it is abandoned. A full NFS walk legitimately takes minutes, so this is shaped like RAR_TIMEOUT above, not a short fixed number.

# ---------------------------------------------------------------------
# 7. MIRC COLOUR CODES AND CONTROL CHARACTERS (IRC STANDARD)
# ---------------------------------------------------------------------
# How everything this bot says in a channel or a notice is coloured. One name
# selects a palette that all eight outbound message paths read - see theme.py
# for the roles and the presets.
#
# This is not decoration. In a busy channel a dozen bots advertise at once and
# the palette is how a person tells them apart at a glance, so two DCCore
# operators who both took the default are indistinguishable. "classic" is the
# look DCCore has always had, and stays the default so that no existing
# install silently changes identity.
THEME: str = "classic"        # classic, midnight, forest, orchid, plain

# Override individual roles on top of the chosen preset, for an operator who
# would rather be unique than pick from a list - each is a raw mIRC code
# string like "\x0306,06", or None (the default) to leave that role at
# whatever the chosen THEME preset already says.
#
# #170's RFC (issue #170's discussion, its Q1): this used to be one
# dict, CUSTOM_THEME, which is why it lived here or in admin_config.py rather
# than settings.conf - settings_file.is_overridable() takes only primitives.
# Six plain strings are primitives, so this is now settings.conf/dashboard
# configurable too, the same as every other setting - "one file, one format".
# The trade-off named out loud in that discussion: a future new theme ROLE
# still needs a new key here, a generator update and a theme.py change - the
# old dict form could have accepted an unknown role name for free. That loss
# is accepted; concrete uniformity now outweighs a hypothetical future role.
CUSTOM_THEME_BORDER: str    = None
CUSTOM_THEME_SEPARATOR: str = None
CUSTOM_THEME_TEXTBOX: str   = None
CUSTOM_THEME_VALUE: str     = None
CUSTOM_THEME_ALERT: str     = None
CUSTOM_THEME_ACCENT: str    = None

C_WHITE        = "\x0300"
C_BLACK        = "\x0301"
C_BLUE         = "\x0302"
C_GREEN        = "\x0303"
C_RED          = "\x0304"
C_BROWN        = "\x0305"
C_PURPLE       = "\x0306"
C_ORANGE       = "\x0307"
C_YELLOW       = "\x0308"
C_LIGHT_GREEN  = "\x0309"
C_CYAN         = "\x0310"
C_LIGHT_CYAN   = "\x0311"
C_ROYAL_BLUE   = "\x0312"
C_PINK         = "\x0313"
C_GREY         = "\x0314"
C_LIGHT_GREY   = "\x0315"

# Formateringstecken
C_RESET        = "\x03"     # Resets colour and bold
C_BOLD         = "\x02"     # Bold
C_UNDERLINE    = "\x1F"     # Underline
C_ITALIC       = "\x1D"     # Italic

# ---------------------------------------------------------------------
# 8. LIVE STATE (held in memory only, for the lifetime of the process)
# ---------------------------------------------------------------------
# Bound to the objects runtime.py holds - the SAME objects, not copies - so
# every existing config.<name> reference keeps working unchanged. !rehash does
# not reload runtime.py, so a reload of this file re-runs these bindings and
# picks the same live containers back up instead of emptying them.
#
# Mutate them in place. Never rebind them: `config.dcc_queue = {}` detaches
# this name from the object runtime.py still holds, and the two silently drift
# apart. tests/test_runtime_state.py fails the build if anything does.
failed_transfers  = runtime.failed_transfers   # Failed-transfer counter, per user
channel_users     = runtime.channel_users      # Users currently seen in the channels
banned_users      = runtime.banned_users       # Currently banned users, in memory
user_requests     = runtime.user_requests      # Command timestamps per user, anti-flood
muted_until       = runtime.muted_until        # Timers for temporarily muted users
whois_status      = runtime.whois_status       # Online status via WHO reply (True = online)
frozen_queues     = runtime.frozen_queues      # Saved timestamps for users in the freezer

# The central queue structures
dcc_queue         = runtime.dcc_queue          # The main sharing queue, {username: [files]}
vip_queue         = runtime.vip_queue          # Express queue for search headers and adverts
active_transfers  = runtime.active_transfers   # Live DCC sends, one thread each

# Scalars stay here. The binding above only works for mutable objects - a bool
# rebound in this file could never write through to runtime.py, so moving them
# would look like a fix without being one. Their behaviour across a rehash is
# unchanged: both are reset by the reload, and rar_inprogress being reset is
# the documented "lock-clearing rehash" escape hatch for a wedged packer.
search_inprogress = False    # Search lock: True while a scan is running
rar_inprogress    = False

# Cross-bot search broadcast (webserver.py POST /api/search/broadcast, capture
# in irc.py's PRIVMSG/NOTICE-to-self dispatch). broadcast_search_results is
# append-only during the listening window: {from, text, received_at} per
# captured line, plus {bot, filename} when a "!<bot> <file>" token was found.
#
# Bound from runtime.py, same as the section above and for the same reason: a
# !rehash used to empty this (and fetch_queue/fetched_bot_lists below) because
# they were plain globals here. Mutate in place; never rebind - see
# runtime.py's docstring.
broadcast_search_inprogress = False
broadcast_search_deadline   = 0
broadcast_search_term       = ""
broadcast_search_results    = runtime.broadcast_search_results
last_broadcast_search_at    = 0     # BROADCAST_SEARCH_COOLDOWN is measured from this

# Cross-bot file fetch (dcc_fetch.py). Keyed by a generated request id ->
# {id, bot, filename, request_type, state, requested_at, offered_at,
# bytes_received, total_size, reason, stored_filename}. state is one of
# pending / offered / listening / receiving / complete / failed.
# request_type is "file" (default - exact bot+filename admission match) or
# "list" (a cross-bot list fetch, admission matches on bot alone - see
# dcc_fetch._claim_matching_offer_locked()); filename starts "" for a "list"
# row and is filled in with the bot's actual advertised zip name once an
# offer is claimed. Active-count is DERIVED by scanning this
# (dcc_fetch.count_active_fetches()) rather than kept as a separate counter,
# on purpose - a separately-maintained counter touched from multiple threads
# (the dispatcher, the CTCP handler, the enqueue route) is exactly the kind of
# thing that drifts out of sync with the data it is supposed to describe.
fetch_queue = runtime.fetch_queue

# Fetched-and-parsed lists FROM OTHER BOTS (list_fetch.py), keyed by
# lowercased bot nick -> {"bot": <original-case nick>, "fetched_at":
# <timestamp>, "list_path": <file>, "entry_count": <n> (list_fetch.py writes
# those two; an "entries" key was described here until #232 and never existed)
# list.entries_to_filelist_rows() produces for our own list...],
# "source_zip": <the zip's stored filename>}. One entry per bot - a later
# fetch for a nick already present REPLACES it, it does not accumulate
# duplicates (see list_fetch.process_fetched_list_zip()). Populated only when
# a config.fetch_queue row with request_type="list" reaches "complete" and is
# then successfully extracted/parsed; a completed transfer whose zip could
# not be safely extracted or contained no recognisable list file leaves this
# untouched and records the reason on the fetch_queue row instead
# (row["list_processing_error"]).
fetched_bot_lists = runtime.fetched_bot_lists

# Other file-serving bots seen advertising in the channels, keyed by lowercase
# nick -> {nick, channel, files, list_date, list_size, last_seen}. Populated by
# irc._capture_channel_advert() purely from channel traffic; every field but
# nick/channel/last_seen is whatever that bot chose to publish, so absent means
# "did not say" rather than zero. Persisted to KNOWN_BOTS_FILE so the dashboard
# is not empty for the first advert cycle after a restart.
#
# Bound from runtime.py for the same reason as everything above it.
known_bots = runtime.known_bots

# ---------------------------------------------------------------------
# WEB DASHBOARD (read-only status page, see webserver.py)
# ---------------------------------------------------------------------
# OFF by default, same pattern as ADMIN_HOSTMASKS below: a feature that opens
# a network-facing surface should never be on just because someone pulled and
# restarted. Opt in from admin_config.py, not here.
#
# Flask is also an OPTIONAL dependency. If it is not installed, webserver.start()
# logs "[WEBUI] Flask not installed; dashboard disabled." and returns - it
# never crashes the daemon and CI never installs Flask, so this stays inert
# there. Install it yourself (`pip install flask`) to actually use the
# dashboard.
WEBUI_ENABLED: bool = False

# The Console page - the admin console in a browser tab - is OFF unless this
# says otherwise, separately from WEBUI_ENABLED above.
#
# The two ways to reach the admin command set are not equally protected:
#
#   DCC CHAT console   the operator's services host (ADMIN_HOSTMASKS) AND a
#                      PBKDF2 password. Two factors.
#   this dashboard     the same password, and nothing else. One factor, over
#                      HTTP with no TLS (see WEBUI_HOST's own comment).
#
# So the Console makes ban, unban, clearqueue, rehash and update reachable
# through the weaker door. That is a perfectly reasonable trade for an
# operator who wants it - it is their LAN and their password - but it must be
# a trade they CHOSE.
#
# Without this switch, turning the dashboard on for Search and Queue would
# have started granting remote admin as a side effect, and an operator who
# enabled it months ago would have gained a remote admin console on upgrade
# with no setting changed and nothing recording that their exposure had
# widened. WEBUI_ENABLED was deliberately made to fail closed (#116); this
# keeps what saying yes to it grants from quietly growing.
#
# Same shape as ADMIN_CHANNEL_COMMANDS, which exists for the same reason: an
# admin surface reachable from a weaker path gets its own switch and a written
# reason for its default.
WEBUI_CONSOLE_ENABLED: bool = False

# The Settings page's folder picker (#164 step 5). Off, and its own switch
# rather than riding along with WEBUI_CONSOLE_ENABLED, for the rule three
# lines above: an admin surface reachable from a weaker path gets its own
# switch and a written reason for its default.
#
# WHAT IT GRANTS. An authenticated dashboard session can list the names of
# directories on the machine the daemon runs on - anywhere it can read, not
# only under the served folders, because the whole point is to find a folder
# that is not being served yet. Never files, never contents, never sizes.
#
# WHY THAT IS A NEW THING AND NOT A CONVENIENCE ON AN OLD ONE. Without it the
# same session can already PROBE a path - saving a folder answers "not a
# folder on this machine" - which tells you about one path you already
# guessed. ENUMERATION is different in kind, and worth an explicit yes.
#
# WHY NOT GATED ON THE CONSOLE. The console is strictly the more dangerous of
# the two: it runs ban, clearqueue, rehash and update. Gating the weaker
# feature behind the stronger one would mean an operator who wants a folder
# picker, and specifically does not want a web admin console, has to enable
# the console to get it.
#
# The cost of leaving it off is typing a path instead of clicking one: the
# folder rows on the Settings page work either way.
WEBUI_FOLDER_BROWSER_ENABLED: bool = False

# LOGIN REQUIRED, shared with the DCC CHAT admin console: every route,
# including static assets, needs a session started by POSTing the password
# for ADMIN_PASSWORD_HASH to /login (see webserver.py's module docstring).
# webserver.start() refuses to run at all while that hash is empty, so there
# is no window where the dashboard is reachable unauthenticated.
#
# "127.0.0.1" is the tracked default: safe out of the box, reachable only from
# this machine. Set this to "0.0.0.0" in admin_config.py if you want it
# reachable from other devices on your LAN (phone, laptop).
#
#   DO NOT PORT-FORWARD THIS PORT TO THE INTERNET. A login gate does not stop
#   someone who shares the network segment from reading the password or the
#   session cookie off the wire - there is no TLS here. DO NOT put this host
#   on any network you do not trust.
WEBUI_HOST: str = "127.0.0.1"
WEBUI_PORT: int = 8420

# ---------------------------------------------------------------------
# 9. LOCAL OVERRIDES (not in git)
# ---------------------------------------------------------------------
# Two mechanisms, both supported, so nothing breaks for an existing install:
#
#   admin_config.py   the original - Python, `from admin_config import *`.
#                     Still read, still works. Nothing to do if you have one.
#   settings.conf     plain text, no Python. settings.conf.sample lists every
#                     setting with its default and what it does.
#
# settings.conf is applied SECOND and therefore wins where both set the same
# name, so a migration can move settings across a few at a time and the file
# being actively edited is the one that takes effect. See settings_file.py for
# why the defaults above stay as Python literals rather than moving into the
# text file as well.
import settings_file

# The exact value each name in settings_file.REQUIRED had BEFORE either
# override mechanism below gets a chance to touch it - i.e. what a fresh
# install that changes nothing would still be running. oserve.startup()
# compares the FINAL resolved value (after both overrides apply, a few lines
# down) against this snapshot via settings_file.unconfigured_required() to
# decide whether the daemon may boot. Snapshotting the values themselves,
# rather than re-reading config.py's source at startup, means a rehash's
# importlib.reload(config) re-executes this exact line and gets the same
# answer back every time.
SHIPPED_DEFAULTS = {name: globals()[name] for name in settings_file.REQUIRED
                    if name in globals()}

# The same snapshot, for EVERY setting rather than only the REQUIRED three,
# and taken at the same moment - before either override mechanism runs, so
# these are the values this version of the code ships with rather than the
# values this install happens to be running.
#
# settings_file._check_writable() needs it to answer one question: may this
# setting be saved empty? A default of None means "unset unless you say
# otherwise" and blank is how an operator says it again (RAR_BINARY back to
# "look on PATH"). A non-empty shipped default means the daemon has no
# behaviour for blank at all - SERVER = "" is a connect() to no host,
# ALT_NICKNAME = "" is a NICK command with no nickname - and the CURRENT
# value cannot answer that, because by the time a second save asks, the first
# one has already blanked it.
SHIPPED_VALUES = {name: value for name, value in list(globals().items())
                  if settings_file.is_overridable(name, value)}

def _migrate_local_config_to_admin_config(directory=None, log=print):
    """Carry an existing local_config.py across to admin_config.py's name.

    #187's review, found on the real upgrade path: config.py ->
    defaults.py is a TRACKED file, so git renames it on every operator's disk
    automatically on pull. local_config.py -> admin_config.py is NOT - it is
    gitignored, so it was never in the repository for git to rename. An
    operator upgrading a real install keeps their old local_config.py,
    unchanged, sitting right next to a defaults.py that no longer imports it -
    so NICKNAME/CHANNEL/ADMIN_NICK read as blank (still the shipped default)
    and oserve.startup()'s REQUIRED gate refuses to boot, even though every
    one of those settings is correctly filled in, one file over.

    Must run HERE, at module import time before `from admin_config import *`
    below - not from oserve.startup() the way db.migrate_legacy_side_files()
    and update_list.migrate_list_base_name() are, both called well after that
    import has already happened. By the time startup() runs it is too late:
    the override this function exists to redirect would already have been
    skipped, and REQUIRED would already see blank.

    Same safety shape as those two: only when admin_config.py does not
    already exist (an operator who has genuinely started fresh under the new
    name wins over anything left from before), and os.replace so an
    interrupted run leaves one intact file rather than two halves.

    `directory` defaults to wherever this file itself lives - the real
    installation directory admin_config.py/local_config.py actually sit in -
    and is only ever overridden by a test.

    Returns True if a rename happened, for the tests.
    """
    if directory is None:
        directory = os.path.dirname(os.path.abspath(__file__))
    admin_config_path = os.path.join(directory, "admin_config.py")
    local_config_path = os.path.join(directory, "local_config.py")

    if os.path.exists(admin_config_path) or not os.path.exists(local_config_path):
        return False

    try:
        # Imported here rather than at the top of the file: this runs at import
        # time, before the rest of defaults.py has finished defining itself,
        # and a local import keeps that ordering obviously irrelevant.
        import platform_compat
        platform_compat.replace_with_retry(local_config_path, admin_config_path)
    except OSError as err:
        log(f"[MIGRATE] Could not rename local_config.py to admin_config.py: {err}. "
            f"Rename it yourself, or copy its settings into admin_config.py.")
        return False

    log("[MIGRATE] Renamed local_config.py to admin_config.py - see admin_config.py.sample "
        "if you want to know what changed.")
    return True


_migrate_local_config_to_admin_config()

try:
    from admin_config import *  # noqa: F401,F403
    print('[CONFIG] Applied overrides from admin_config.py')
except ImportError:
    pass

settings_file.apply_to(globals())

# ---------------------------------------------------------------------
# 10. DERIVED VALUES (computed AFTER local overrides, never before)
# ---------------------------------------------------------------------
# Anything whose value is computed from another setting belongs here, below
# BOTH override mechanisms (admin_config.py and settings.conf), not next to
# the setting it reads.
#
# BROADCAST_SEARCH_CHANNEL used to be derived at the top of this file, far
# above the point where overrides land. Its own comment promised it "defaults
# to the first entry of CHANNEL" - and it did, but to the first entry of the
# TRACKED default, not the operator's. So an operator who set
# CHANNEL = "#their-channel" (in admin_config.py OR settings.conf) still had a
# dashboard broadcast search send its @find into whatever channel the
# shipped default named: a real public channel they may not even be in.
#
# Only an unset value is derived, so an explicit choice always wins. CHANNEL
# itself may also still be unset here - #170's RFC made it one of
# settings_file.REQUIRED, shipped blank rather than a real tracked default
# (see CHANNEL's own comment) - so this must not crash simply importing
# config.py on a fresh, not-yet-configured install. oserve.startup()'s
# REQUIRED gate is what actually refuses to boot in that case; this just
# has nothing to derive from yet, and stays unset itself.
if not BROADCAST_SEARCH_CHANNEL and CHANNEL:
    BROADCAST_SEARCH_CHANNEL = CHANNEL.split(",")[0].strip()

# LIST_BASE_NAME predicted this exact gap: #170's RFC discussion (its own
# comment) noted it "can derive from NICKNAME rather than being asked for at
# all" once NICKNAME itself is required - see settings_file.py's own comment
# on why LIST_BASE_NAME is not in REQUIRED. Found live, running configure.py
# against a real install: NICKNAME came out "DCCoreTest", but the generated
# list still came out named "DCCore-<date>.zip", because nothing ever did
# the derivation the RFC predicted.
#
# LIST_BASE_NAME's own shipped literal is a real, non-blank value
# ("DCCore") - unlike BROADCAST_SEARCH_CHANNEL above, `if not LIST_BASE_NAME`
# would never catch an untouched install here, so "still exactly what
# shipped" (not "still blank") is what marks it as never explicitly chosen.
# NICKNAME may itself still be unset here on an install that has not reached
# oserve.startup()'s REQUIRED gate yet, so there may be nothing to derive
# from at all.
#
# The same value-comparison limitation settings_file.py's own REQUIRED
# comment documents for SERVER/DEBUG_CHANNEL applies here too, and is
# accepted for the same reason: comparing the FINAL resolved value against
# the shipped literal cannot tell "never touched" apart from "deliberately
# set back to the same value as the shipped default" - an operator who
# explicitly writes LIST_BASE_NAME = "DCCore" in admin_config.py or
# settings.conf gets it silently replaced by NICKNAME here, identically to
# one who never touched it at all. Narrow (it only misfires when the
# explicit choice is the literal word "DCCore") and no worse than shipping
# with no derivation at all, which is the alternative.
if LIST_BASE_NAME == "DCCore" and NICKNAME:
    LIST_BASE_NAME = NICKNAME
