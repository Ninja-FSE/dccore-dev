# =====================================================================
# CONFIG.PY - CENTRAL CONFIGURATION FOR THE DCCORE DAEMON
# =====================================================================
# ---------------------------------------------------------------------
# 1. SYSTEM AND GLOBAL ENGINE SETTINGS
# ---------------------------------------------------------------------
DEBUG_MODE     = False
SCRIPT_VERSION = "DCCore v1.10.0-RC3"
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

# ---------------------------------------------------------------------
# 3. FILESYSTEM, PATHS AND TEXT STORES
# ---------------------------------------------------------------------
PAUSE_ON_UPDATE = True  # MAINTENANCE SWITCH: when True the bot pauses ALL sharing and searching during !update
FILE_DIRECTORY = "/mnt/nfs-musik"
RAR_BINARY     = None       # None = look for rar/rar.exe on PATH (and WinRAR's install dir)
TMP_ZIP_DIR = "./data/tmp_zips"
LOCAL_LIST_DIR = "./lists"

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
