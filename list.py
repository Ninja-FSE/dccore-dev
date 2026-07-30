# list.py - Slimmad utgåva (Scanning borttagen - Sköts av update_list.py)
import os
import time
import datetime
import config
import oserve
import dcc
import announce

LIST_FILE_PATH = os.path.join(config.LOCAL_LIST_DIR, "flac-serv.txt")
SIZE_FILE_PATH = os.path.join(config.LOCAL_LIST_DIR, "flac-serv-size.txt")
RAWBYTES_FILE_PATH = os.path.join(config.LOCAL_LIST_DIR, "flac-serv-rawbytes.txt")

def find_latest_list():
    """Hittar den absolut senaste .txt-listfilen i den lokala list-mappen"""
    if not os.path.exists(config.LOCAL_LIST_DIR):
        os.makedirs(config.LOCAL_LIST_DIR)
        return None
    files = [f for f in os.listdir(config.LOCAL_LIST_DIR) if f.startswith(config.LIST_BASE_NAME) and f.endswith(".txt")]
    if not files:
        return None
    files.sort(reverse=True)
    return os.path.join(config.LOCAL_LIST_DIR, files[0])

def find_latest_zip():
    """Hittar den senaste .zip-filen för när någon skriver @FLAC-Serv"""
    if not os.path.exists(config.LOCAL_LIST_DIR):
        return None
    files = [f for f in os.listdir(config.LOCAL_LIST_DIR) if f.startswith(config.LIST_BASE_NAME) and f.endswith(".zip")]
    if not files:
        return None
    files.sort(reverse=True)
    return os.path.join(config.LOCAL_LIST_DIR, files[0])

def get_file_count_date_size_and_raw_bytes():
    """Hämtar det EXAKTA antalet musikfiler genom att enbart räkna rader som startar med trigger!"""
    latest_list = find_latest_list()
    if not latest_list or not os.path.exists(latest_list):
        return 0, "No List", "0B", 0
        
    try:
        count = 0
        with open(latest_list, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_strip = line.strip()
                # RÄTTAD LOGIK: Räkna ENBART rader som faktiskt startar med din trigger!
                # Hoppa över alla mapprobrik-avskiljare (===), tomma rader och text-headers.
                if line_strip.startswith(f"!{config.NICKNAME} "):
                    count += 1
            
        mtime = os.path.getmtime(latest_list)
        dt = datetime.datetime.fromtimestamp(mtime)
        day = dt.day
        suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        date_str = dt.strftime(f"%b {day}{suffix}")
        
        size_str = "0B"
        if os.path.exists(SIZE_FILE_PATH):
            with open(SIZE_FILE_PATH, "r", encoding="utf-8") as sf:
                size_str = sf.read().strip()
                
        raw_bytes = 0
        if os.path.exists(RAWBYTES_FILE_PATH):
            with open(RAWBYTES_FILE_PATH, "r", encoding="utf-8") as rbf:
                raw_bytes = int(rbf.read().strip())
                
        return count, date_str, size_str, raw_bytes
    except Exception as e:
        print(f"[ERROR] Kunde inte läsa exakt filstatistik: {e}")
        return 0, "Error", "0B", 0

def execute_search(irc_sock, user, search_term, channel):
    """Söker i listfilen med dynamiska wildcards - ALLA ord måste finnas på raden (oberoende av ordning)!"""
    import config, announce, os, sys
    
    # Om en sökning redan pågår, avbryt direkt för att skydda systemet
    if getattr(config, 'search_inprogress', False):
        print(f"[SEARCH BLOCK] Ignorerade sökning från {user} eftersom en annan skanning pågår.")
        return
        
    if len(search_term) < 3:
        oserve = sys.modules.get('oserve')
        if oserve:
            oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}Error{config.C_RESET}: Search term must be at least 3 characters long.\r\n")
        return

    # Sätt flaggan direkt vid start
    config.search_inprogress = True
    
    try:
        current_list_path = find_latest_list()
        if not current_list_path or not os.path.exists(current_list_path):
            oserve = sys.modules.get('oserve')
            if oserve:
                oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}Error{config.C_RESET}: No MasterList found.\r\n")
            return

        print(f"[NEW SEARCH] {user} in {channel} searched for '{search_term}'")
        
        # ---------------------------------------------------------------------
        # DYNAMISK WILDCARD-PREPARERING
        # Vi splittar sökningen till en lista med enskilda ord i lowercase,
        # och rensar bort eventuella tomma tecken eller lösa bindestreck.
        # ---------------------------------------------------------------------
        search_words = [w.strip().lower() for w in search_term.replace("-", " ").split() if w.strip()]
        
        matches = []
        total_matches = 0
        
        with open(current_list_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_strip = line.strip()
                line_lower = line_strip.lower()
                
                # Säkerhetsfilter: Hoppa över mapprobrik-avskiljare och headers
                if line_strip.startswith("=====") or line_strip.upper().startswith("D:\\MUSIC\\") or line_strip.startswith("List of "):
                    continue
                
                # INTELLIGENT MATCHNING: Vi antar att raden är en träff tills motsatsen bevisats
                is_match = True
                for word in search_words:
                    if word not in line_lower:
                        is_match = False
                        break # Ett ord saknades, hoppa till nästa rad direkt!
                
                if is_match and search_words: # Alla sökord fanns på raden!
                    total_matches += 1
                    
                    # Spara bara filen om vi inte har nått maxgränsen från config än
                    max_results = getattr(config, 'MAX_SEARCH_RESULTS', 5)
                    if len(matches) < max_results:
                        if line_strip.startswith(f"!{config.NICKNAME} "):
                            matches.append(line_strip)
                        else:
                            matches.append(f"!{config.NICKNAME} {line_strip}")

        if matches:
            # Skickar din VIP-header direkt i privat PM
            announce.send_search_result_header(user, search_term, total_matches, channel)
            
            # Skicka filraderna (max 5) till användaren i privat PM med ditt nya tema
            oserve = sys.modules.get('oserve')
            if oserve:
                BG_RED_BLOCK  = "\x0304,05" # Mörkröd kant
                BG_CYAN_BLOCK = "\x0310,10" # Turkos kant
                BG_TEXT_BOX   = "\x0301,00" # Svart text på VIT bakgrund
                R = "\x0f"                  # Total nollställning
                
                for match in matches: 
                    # Kläder in sökresultatet i en perfekt vit ruta inramad av dina stolpar!
                    block_match = f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} {match}{R} {BG_CYAN_BLOCK} {BG_RED_BLOCK} "
                    result_msg = f"PRIVMSG {user} :{block_match}\r\n"
                    oserve.queue_message(user, result_msg)
        else:
            print(f"[SEARCH RESULT] 0 Match(es) found for {user} in {channel} on '{search_term}'")
                
    except Exception as e:
        print(f"[SEARCH CRITICAL ERROR] Sökningen kraschade under filskanning: {e}")
        
    finally:
        # Frigör sökflaggan på 0ms så nästa person kan söka direkt
        config.search_inprogress = False
        print(f"[SEARCH-FINISHED] Sökningen för {user} avslutades och låset frigjordes snyggt.")


def send_list_trigger_info(irc_sock, user):
    msg = f"{config.C_BOLD}{config.SCRIPT_VERSION}{config.C_RESET} Trigger: {config.C_RED}@{config.NICKNAME}{config.C_RESET} | Type @find <search_term> to search!\r\n"
    oserve.queue_message(user, f"NOTICE {user} :{msg}")

def send_file_list(irc_sock, user, channel):
    """Hittar den befintliga .zip-listan och startar DCC SEND med rätt kanalspårning!"""
    current_zip_path = find_latest_zip()
    
    if not current_zip_path or not os.path.exists(current_zip_path):
        oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}Error{config.C_RESET}: ZIP file missing.\r\n")
        return
        
    zip_filename = os.path.basename(current_zip_path)
    msg = f"{config.C_BOLD}{config.SCRIPT_VERSION}{config.C_RESET} Preparing full list ({zip_filename}) for {user}...\r\n"
    oserve.queue_message(user, msg)
    
    # Nu skickas 'channel' med till DCC-motorn i stället för 'user'
    dcc.handle_download_request(irc_sock, user, zip_filename, channel)

