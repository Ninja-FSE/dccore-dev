# config.py
DEBUG_MODE = False
# IRC-inställningar
SERVER = "irc.undernet.org"
PORT = 6667
CHANNEL = "#mp3passion,#mp3servers,#mp3-best-of,#mp3country,#mp3albums4u,#mp3download"
DEBUG_CHANNEL = "#flac-serv"
NICKNAME = "FLAC-Serv"       # Ändrat till ditt nya projektnamn
ADMIN_NICK = "FLAC"

# Filserver-inställningar (Peka på din Proxmox bind-mount)
FILE_DIRECTORY = "/mnt/nfs-musik"
LOCAL_LIST_DIR= "./lists"
HARD_BANS_FILE = "./data/hard_bans.txt"
BANS_FILE = "./data/bans.txt"
STATS_FILE= "./data/stats.txt"

# Det dynamiska namnet uppdateras automatiskt i list.py med dagens datum
LIST_BASE_NAME = "FLAC-Serv"
SCRIPT_VERSION = "DCCore v1.4.1-BETA"
# Announce-inställningar
ANNOUNCE_INTERVAL = 300     # Hur ofta boten gör reklam i kanalen (i sekunder)

# Begränsningar och Säkerhet
MAX_SEND_FAILS = 3          # Bans efter x misslyckade försök
MAX_DCC_SLOTS = 3           # Hur många som får ladda ner samtidigt
DCC_PORT_START = 55000       # Portspann för DCC (måste öppnas i brandvägg/router)
DCC_PORT_END = 55010
MSG_DELAY = 5.0             # MSG Kösystem
MAX_USER_QUEUE = 100        # Max antal filer EN unik användare får ha i sin personliga kö
MAX_GLOBAL_QUEUE = 1000     # Max totalt antal filer i ALLA användares köer sammanlagt
MAX_SEARCH_RESULTS = 5

# Anti-Flood inställningar
MAX_REQUESTS = 100            # Max antal kommandon (sök/fil) per period
REQUEST_WINDOW = 100         # Tidsperiod i sekunder (t.ex. max 3 reqs per 10s)
MUTE_TIME = 30              # Hur många sekunder användaren blir spärrad om de floodar

# Mirc färgkoder för IRC-bot
C_WHITE = "\x0300"
C_BLACK = "\x0301"
C_BLUE = "\x0302"
C_GREEN = "\x0303"
C_RED = "\x0304"
C_BROWN = "\x0305"
C_PURPLE = "\x0306"
C_ORANGE = "\x0307"
C_YELLOW = "\x0308"
C_LIGHT_GREEN = "\x0309"
C_CYAN = "\x0310"
C_LIGHT_CYAN = "\x0311"
C_ROYAL_BLUE = "\x0312"
C_PINK = "\x0313"
C_GREY = "\x0314"
C_LIGHT_GREY = "\x0315"

# Kontrolltecken för formatering
C_RESET = "\x03"      # Nollställer färg
C_BOLD = "\x02"       # Fetstil
C_UNDERLINE = "\x1F"  # Understruken
C_ITALIC = "\x1D"     # Kursiv


# Globalt minne (Hålls i RAM under körning)
search_inprogress = False
failed_transfers = {}
banned_users = {}
user_requests = {}
muted_until = {}


# Initiera den globala fildelningskön för DCC-överföringar
dcc_queue = {}
# Helt separat expressfil för sök-headers och kanalreklam (Rör inte vanliga kön)
vip_queue = []
# Initiera listan för att hålla koll på aktiva DCC-slottar i realtid
active_transfers = []
# Håller koll på WHOIS-svar live från servern (True = Online, False = Offline)
whois_status = {}
# Håller koll på när en användares kö frystes (användarnamn: timestamp)
frozen_queues = {}