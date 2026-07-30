# =====================================================================
# CONFIG.PY - CENTRAL KONFIGURATION FÖR FLAC-SERV DCCORE DAEMON
# =====================================================================
# ---------------------------------------------------------------------
# 1. SYSTEM- OCH GLOBALA MOTORINSTÄLLNINGAR
# ---------------------------------------------------------------------
DEBUG_MODE     = False
SCRIPT_VERSION = "DCCore v1.4.3-BETA"
LIST_BASE_NAME = "FLAC-Serv"

# ---------------------------------------------------------------------
# 2. IRC NÄTVERKS- OCH KANALINSTÄLLNINGAR
# ---------------------------------------------------------------------
SERVER        = "irc.undernet.org"
PORT          = 6667
NICKNAME      = "FLAC-Serv"
ADMIN_NICK    = "FLAC"
CHANNEL       = "#mp3passion,#mp3servers,#mp3-best-of,#mp3country,#mp3albums4u,#mp3download"
DEBUG_CHANNEL = "#flac-serv"

# ---------------------------------------------------------------------
# 3. FILSYSTEM, SÖKVÄGAR OCH TEXTDATABASER
# ---------------------------------------------------------------------
FILE_DIRECTORY = "/mnt/nfs-musik"
LOCAL_LIST_DIR = "./lists"

# Säkra, normaliserade sökvägar direkt in i din data/ undermapp
BANS_FILE      = "./data/bans.txt"
STATS_FILE     = "./data/stats.txt"
HARD_BANS_FILE = "./data/hard_bans.txt"

# ---------------------------------------------------------------------
# 4. KANALANNONSERING (REKLAMKLOCKAN)
# ---------------------------------------------------------------------
ANNOUNCE_INTERVAL = 300     # Tid mellan varje kanalreklam (i sekunder)

# ---------------------------------------------------------------------
# 5. BEGRÄNSNINGAR, SLOTS OCH KÖ-KONTROLL
# ---------------------------------------------------------------------
MAX_DCC_SLOTS      = 3      # Max antal samtidiga live-nedladdningar
MAX_USER_QUEUE     = 100    # Max antal filer en unik användare får köa upp
MAX_GLOBAL_QUEUE   = 1000   # Max totalt antal filer i alla köer sammanlagt
MAX_SEARCH_RESULTS = 5      # Max antal textrader som spottas ut vid @find
MSG_DELAY          = 5.0    # Paustid i sekunder för din ordinarie meddelandekö

# Portspann för DCC-sändningar (Måste öppnas i brandvägg/router!)
DCC_PORT_START     = 55000
DCC_PORT_END       = 55010

# ---------------------------------------------------------------------
# 6. ANTI-FLOOD OCH AUTOMATISKT SÄKERHETSSKYDD
# ---------------------------------------------------------------------
MAX_REQUESTS     = 10       # Max antal kommandon (sök/fil) per tidsfönster
REQUEST_WINDOW   = 10       # Storlek på det rullande tidsfönstret (i sekunder)
MUTE_TIME        = 30       # Paustid i sekunder för varning vid första flood-överskridelsen
MAX_SEND_FAILS   = 3        # Automatiskt ban efter x antal misslyckade sändningar

# ---------------------------------------------------------------------
# 7. MIRC FÄRGKODER OCH KONTROLLTECKEN (IRC STANDARD)
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
C_RESET        = "\x03"     # Nollställer färg/fetstil
C_BOLD         = "\x02"     # Fetstil
C_UNDERLINE    = "\x1F"     # Understruken
C_ITALIC       = "\x1D"     # Kursiv

# ---------------------------------------------------------------------
# 8. GLOBALT LIVE-MINNE (HÅLLERS ENBART I RAM UNDER KÖRNING)
# ---------------------------------------------------------------------
search_inprogress = False    # Sök-lås (True om en genomsökning körs)
failed_transfers  = {}       # Räknare för misslyckade överföringar per användare
banned_users      = {}       # Aktiva spärrade användare i RAM
user_requests     = {}       # Tidsstämplar för användares kommandon (Anti-flood)
muted_until       = {}       # Timers för tillfälligt tystade användare
whois_status      = {}       # Online-status via WHO-svar (True = Online)
frozen_queues     = {}       # Sparade tidsstämplar för användare i frysboxen

# Centrala könätverk strukturer
dcc_queue         = {}       # Centrala fildelningskön (användarnamn: [filer])
vip_queue         = []       # Isolerad express-kö för sök-headers och reklam
active_transfers  = []       # Trådade aktiva DCC-sändningar i realtid
