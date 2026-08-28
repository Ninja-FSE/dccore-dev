# =====================================================================
# CONFIG.PY - CENTRAL CONFIGURATION FOR THE DCCORE DAEMON
# =====================================================================
# ---------------------------------------------------------------------
# 1. SYSTEM AND GLOBAL ENGINE SETTINGS
# ---------------------------------------------------------------------
DEBUG_MODE     = False
SCRIPT_VERSION = "DCCore v1.10.0-RC2"
LIST_BASE_NAME = "DCCore"

# ---------------------------------------------------------------------
# 2. IRC NETWORK AND CHANNEL SETTINGS
# ---------------------------------------------------------------------
SERVER        = "irc.undernet.org"
PORT          = 6667
NICKNAME      = "DCCore"
ALT_NICKNAME = "DCCore_"
ADMIN_NICK    = "FLAC,Samoth"
CHANNEL       = "#mp3passion,#mp3servers,#mp3-best-of,#mp3country,#mp3albums4u,#mp3download"
DEBUG_CHANNEL = "#flac-serv"

# The single channel a "search all bots" broadcast (@find) goes into - see
# webserver.py's POST /api/search/broadcast. Deliberately ONE channel, never
# all of them: broadcasting into every channel this bot has joined multiplies
# the disruption to every other operator sharing those channels, for one
# search. Defaults to the first entry of CHANNEL above; override explicitly
# here (or in local_config.py) if that is not the right one.
# Derived from CHANNEL - but NOT here. See "DERIVED VALUES" at the end of this
# file: computing it at this point captures the tracked default above and
# silently ignores an operator's own CHANNEL. None means "derive it below";
# setting it explicitly, here or in local_config.py, still wins.
BROADCAST_SEARCH_CHANNEL = None

# ---------------------------------------------------------------------
# 3. FILESYSTEM, PATHS AND TEXT STORES
# ---------------------------------------------------------------------
PAUSE_ON_UPDATE = True  # MAINTENANCE SWITCH: when True the bot pauses ALL sharing and searching during !update
FILE_DIRECTORY = "/mnt/nfs-musik"
RAR_BINARY     = None       # None = look for rar/rar.exe on PATH (and WinRAR's install dir)
TMP_ZIP_DIR = "./data/tmp_zips"
LOCAL_LIST_DIR = "./lists"

# Where files fetched FROM other bots (dcc_fetch.py) land. Deliberately
# separate from FILE_DIRECTORY: that directory is the served library, scanned
# by update_list.py and offered to everyone via @find/!<nick> - fetched files
# must never be reachable through that path. They are dashboard-download-only
# (GET /api/fetch/<id>/download in webserver.py).
FETCHED_FILES_DIR = "./data/fetched"

# Safe, normalised paths into the data/ subdirectory
BANS_FILE      = "./data/bans.txt"
STATS_FILE     = "./data/stats.txt"
HARD_BANS_FILE = "./data/hard_bans.txt"

# ---------------------------------------------------------------------
# 4. KANALANNONSERING (REKLAMKLOCKAN)
# ---------------------------------------------------------------------
ANNOUNCE_INTERVAL = 300     # Tid mellan varje kanalreklam (i sekunder)

# ---------------------------------------------------------------------
# 5. LIMITS, SLOTS AND QUEUE CONTROL
# ---------------------------------------------------------------------
MAX_DCC_SLOTS      = 3      # Max antal samtidiga live-nedladdningar
MAX_USER_QUEUE     = 100    # Most files a single user may queue
MAX_GLOBAL_QUEUE   = 1000   # Most files across every queue combined
MAX_SEARCH_RESULTS = 5      # Max antal textrader som spottas ut vid @find
MSG_DELAY          = 5.0    # Delay in seconds for the ordinary message queue
DEBUG_MSG_DELAY    = 0.5    # Paustid mellan varje rad till debug-kanalen

# Port range for DCC sends (must be open on the firewall and router)
# ---------------------------------------------------------------------
# ADMIN CONSOLE (DCC CHAT)
# ---------------------------------------------------------------------
# Empty list = console disabled, and every DCC CHAT request is ignored.
#
# Each entry is a HOST pattern. It may be written bare ("FLAC.users.undernet.org")
# or in the familiar IRC form ("*!*@FLAC.users.undernet.org"); either way only the
# part after the last "@" is used. The nick and ident halves are discarded on
# purpose - the ident is supplied by the client and anyone can set theirs to
# "flac", so constraining it grants nothing. Only the host is issued by the server.
#
# On Undernet, log into X and set usermode +x. The server then replaces your host
# with "<your-account>.users.undernet.org", which nobody else can obtain. That
# host IS the proof of your services login.
#
# Put the real values in local_config.py, which is gitignored, NOT here.
ADMIN_HOSTMASKS    = []
# Generated with:  python adminchat.py
ADMIN_PASSWORD_HASH = ""

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
ADMIN_CHAT_MODE    = "auto"

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
ADMIN_CHANNEL_COMMANDS = True

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
DEBUG_TO_CHANNEL   = True
DEBUG_TO_CONSOLE   = True

# The two side files update_list.py publishes alongside the master list, holding
# the human-readable total size and the raw byte count that the channel advert and
# @<nick>-que read back. Named here rather than as a literal in both list.py and
# update_list.py: that split literal is the same shape as issue #34, where the
# reader and the writer of speed_record.txt agreed only by coincidence.
#
# The "flac-serv" prefix is historical and deliberately kept. Renaming it would
# orphan the stats on every existing deployment until the next successful !update.
LIST_SIZE_FILE     = "flac-serv-size.txt"
LIST_RAWBYTES_FILE = "flac-serv-rawbytes.txt"

DCC_PORT_START     = 55000
DCC_PORT_END       = 55010

# ---------------------------------------------------------------------
# CROSS-BOT FILE FETCH (dcc_fetch.py - receiving files FROM other bots)
# ---------------------------------------------------------------------
# Deliberately separate from MAX_DCC_SLOTS above: that governs OUR outbound
# SENDs to people requesting from us. Conflating the two directions would let
# outbound leech traffic (us fetching from others) starve our own serving
# capacity, or vice versa.
MAX_FETCH_SLOTS         = 3        # Max simultaneous in-flight/offered fetches
MAX_FETCH_FILE_SIZE     = 200 * 1024 * 1024   # 200 MB - reject the offer before we even connect
FETCH_TRANSFER_TIMEOUT  = 600      # Seconds - total wall-clock per transfer (against a slow "drip" that keeps resetting the idle timeout)
FETCH_OFFER_TIMEOUT     = 60       # Seconds an "offered" row waits for a DCC SEND before it's marked failed

# How often a new @find broadcast (POST /api/search/broadcast) is allowed to
# start. Independent of the UI - courtesy to other bots/operators on a shared
# public channel, not just a UI detail.
BROADCAST_SEARCH_COOLDOWN = 30     # Seconds

# ---------------------------------------------------------------------
# 6. ANTI-FLOOD AND AUTOMATIC PROTECTION
# ---------------------------------------------------------------------
MAX_REQUESTS     = 10       # Most commands (search or file) per time window
REQUEST_WINDOW   = 5       # Size of the rolling time window, in seconds
MUTE_TIME        = 30       # Mute in seconds on the first flood violation
MAX_SEND_FAILS   = 3        # Attempts per queued file before it is dropped (see dcc.release_queue_entry)
RAR_TIMEOUT      = 1800     # Longest a rar packing run may take, in seconds, before it is abandoned

# ---------------------------------------------------------------------
# 7. MIRC COLOUR CODES AND CONTROL CHARACTERS (IRC STANDARD)
# ---------------------------------------------------------------------
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
C_BOLD         = "\x02"     # Fetstil
C_UNDERLINE    = "\x1F"     # Understruken
C_ITALIC       = "\x1D"     # Kursiv

# ---------------------------------------------------------------------
# 8. LIVE STATE (held in memory only, for the lifetime of the process)
# ---------------------------------------------------------------------
search_inprogress = False    # Search lock: True while a scan is running
failed_transfers  = {}       # Failed-transfer counter, per user
channel_users     = {}       # Users currently seen in the channels
banned_users      = {}       # Currently banned users, in memory
user_requests     = {}       # Command timestamps per user, for anti-flood
muted_until       = {}       # Timers for temporarily muted users
whois_status      = {}       # Online-status via WHO-svar (True = Online)
frozen_queues     = {}       # Saved timestamps for users in the freezer
rar_inprogress = False

# The central queue structures
dcc_queue         = {}       # The main sharing queue, as {username: [files]}
vip_queue         = []       # Isolated express queue for search headers and adverts
active_transfers  = []       # Live DCC sends, one thread each

# Cross-bot search broadcast (webserver.py POST /api/search/broadcast, capture
# in irc.py's PRIVMSG/NOTICE-to-self dispatch). broadcast_search_results is
# append-only during the listening window: {from, text, received_at} per
# captured line, plus {bot, filename} when a "!<bot> <file>" token was found.
broadcast_search_inprogress = False
broadcast_search_deadline   = 0
broadcast_search_term       = ""
broadcast_search_results    = []
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
fetch_queue = {}

# Fetched-and-parsed lists FROM OTHER BOTS (list_fetch.py), keyed by
# lowercased bot nick -> {"bot": <original-case nick>, "fetched_at":
# <timestamp>, "entries": [...rows in the same shape
# list.entries_to_filelist_rows() produces for our own list...],
# "source_zip": <the zip's stored filename>}. One entry per bot - a later
# fetch for a nick already present REPLACES it, it does not accumulate
# duplicates (see list_fetch.process_fetched_list_zip()). Populated only when
# a config.fetch_queue row with request_type="list" reaches "complete" and is
# then successfully extracted/parsed; a completed transfer whose zip could
# not be safely extracted or contained no recognisable list file leaves this
# untouched and records the reason on the fetch_queue row instead
# (row["list_processing_error"]).
fetched_bot_lists = {}

# ---------------------------------------------------------------------
# WEB DASHBOARD (read-only status page, see webserver.py)
# ---------------------------------------------------------------------
# OFF by default, same pattern as ADMIN_HOSTMASKS below: a feature that opens
# a network-facing, unauthenticated surface should never be on just because
# someone pulled and restarted. Opt in from local_config.py, not here.
#
# Flask is also an OPTIONAL dependency. If it is not installed, webserver.start()
# logs "[WEBUI] Flask not installed; dashboard disabled." and returns - it
# never crashes the daemon and CI never installs Flask, so this stays inert
# there. Install it yourself (`pip install flask`) to actually use the
# dashboard.
WEBUI_ENABLED = False

# NO AUTHENTICATION. Every /api/* route is open to anyone who can reach this
# host:port - there is no login, no token, no password. The read-only routes
# (queue/search/file lists) are harmless to expose; the mutating ones (search
# broadcast, cross-bot fetch) let anyone who can reach this port make the bot
# dial out and pull files from other IRC bots.
#
# "127.0.0.1" is the tracked default: safe out of the box, reachable only from
# this machine. Set this to "0.0.0.0" in local_config.py if you want it
# reachable from other devices on your LAN (phone, laptop) - only do that on a
# network you trust, since there is still no authentication.
#
#   DO NOT PORT-FORWARD THIS PORT TO THE INTERNET. DO NOT put this host on
#   any network you do not trust, without adding authentication first.
WEBUI_HOST    = "127.0.0.1"
WEBUI_PORT    = 8420

# ---------------------------------------------------------------------
# 9. LOCAL OVERRIDES (not in git)
# ---------------------------------------------------------------------
# Create local_config.py next to this file to override anything above for one
# machine - paths, nickname, channels - without editing a tracked file and
# without every deployment showing up as a diff. It is gitignored.
try:
    from local_config import *  # noqa: F401,F403
    print('[CONFIG] Applied overrides from local_config.py')
except ImportError:
    pass


# ---------------------------------------------------------------------
# 10. DERIVED VALUES (computed AFTER local overrides, never before)
# ---------------------------------------------------------------------
# Anything whose value is computed from another setting belongs here, below the
# local_config import, not next to the setting it reads.
#
# BROADCAST_SEARCH_CHANNEL used to be derived at the top of this file, 264
# lines above the point where overrides land. Its own comment promised it
# "defaults to the first entry of CHANNEL" - and it did, but to the first entry
# of the TRACKED default, not the operator's. So an operator who set
# CHANNEL = "#their-channel" in local_config.py still had a dashboard broadcast
# search send its @find into #mp3passion: the first channel of the shipped
# default, a real public channel they may not even be in.
#
# Only an unset value is derived, so an explicit choice always wins.
if not BROADCAST_SEARCH_CHANNEL:
    BROADCAST_SEARCH_CHANNEL = CHANNEL.split(",")[0].strip()
