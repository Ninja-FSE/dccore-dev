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
    """Hittar den senaste .zip-filen för när någon skriver botens nickname"""
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

# list.py - Slimmad sökmodul för OmenServe (Del 1 av 2)
import os
import glob
import re
import sys
import config
import announce

def find_latest_list():
    """Hittar den absolut senaste normala textlistan i lists-mappen"""
    try:
        all_txt_files = sorted(glob.glob(os.path.join(config.LOCAL_LIST_DIR, f"{config.NICKNAME}-*.txt")))
        # Sortera strikt bort din RAR-lista från sökningen så vi bara skannar masterlistan
        true_master_lists = [f for f in all_txt_files if "-RAR-" not in f]
        if true_master_lists:
            return true_master_lists[-1]
    except Exception as e:
        print(f"[SEARCH ERROR] Kunde inte hitta senaste listan: {e}")
    return None

def execute_search(irc_sock, user, search_term, channel):
    """Söker i listfilen - Kopierar och skickar raderna spikrakt precis som de står!"""
        # 🛡️ GLOBAL UNDERHÅLLSSPÄRR: Blockera sökningen om växeln är aktiv i config och uppdatering pågår!
    if getattr(config, 'PAUSE_ON_UPDATE', False) is True and getattr(config, 'search_inprogress', False) is True:
        oserve = sys.modules.get('oserve')
        if oserve:
            oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}System Message{config.C_RESET}: Search engine is temporarily paused during MasterList rebuild. Please wait a moment.\r\n")
        print(f"[MAINTENANCE BLOCK] Nekade sökning (@find) från {user} pga pågående !update.")
        return

    # Skydda systemet mot dubbelsökningar
    if getattr(config, 'search_inprogress', False):
        print(f"[SEARCH BLOCK] Ignorerade sökning från {user} eftersom en annan skanning pågår.")
        return
        
    if len(search_term) < 3:
        oserve = sys.modules.get('oserve')
        if oserve:
            oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}Error{config.C_RESET}: Search term must be at least 3 characters long.\r\n")
        return

    config.search_inprogress = True
    
    try:
        current_list_path = find_latest_list()
        if not current_list_path or not os.path.exists(current_list_path):
            oserve = sys.modules.get('oserve')
            if oserve:
                oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}Error{config.C_RESET}: No MasterList found.\r\n")
            return

        print(f"[NEW SEARCH] {user} in {channel} searched for '{search_term}'")
        
        # Tvätta bort dolda mIRC-färgkoder och kontrolltecken från sökorden
        raw_clean = search_term.replace('\x02', '').replace('\x1f', '').replace('\x0f', '')
        raw_clean = re.sub(r'\x03(?:\d{1,2}(?:,\d{1,2})?)?', '', raw_clean)
        
        # Dela upp sökorden
        clean_term = re.sub(r'[-*_.]', ' ', raw_clean)
        search_words = [w.strip().lower() for w in clean_term.split() if w.strip()]
        
        matches = []
        total_matches = 0
        # ---------------------------------------------------------------------
        # DIREKT-KOPIERANDE SKANNING (0% Omformatering - Skickar filraden rå!)
        # ---------------------------------------------------------------------
        with open(current_list_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                # Ta bort dolda noll-bytes och rensa radslut
                line_strip = line.replace('\x00', '').strip()
                
                # SÄKERHETSFILTER: Släpp ENBART fram sanna fildelningsrader som börjar med utropstecken
                if not line_strip or not line_strip.startswith("!"):
                    continue
                
                line_lower = line_strip.lower()
                
                # Kontrollera om ALLA sökord finns på den aktuella raden
                is_match = True
                for word in search_words:
                    if word not in line_lower:
                        is_match = False
                        break
                
                if is_match and search_words:
                    total_matches += 1
                    
                    # Spara raden exakt som den står på disken upp till din max-gräns
                    max_results = getattr(config, 'MAX_SEARCH_RESULTS', 5)
                    if len(matches) < max_results:
                        matches.append(line_strip)

        if matches:
            # Skicka din officiella sök-header privat till mIRC
            announce.send_search_result_header(user, search_term, total_matches, channel)
            
            oserve = sys.modules.get('oserve')
            if oserve:
                BG_RED_BLOCK  = "\x0304,05" # Mörkröd kant
                BG_CYAN_BLOCK = "\x0310,10" # Turkos kant
                BG_TEXT_BOX   = "\x0301,00" # Svart text på VIT bakgrund
                R = "\x0f"                  # Total nollställning
                
                for match in matches: 
                    # Klä raden i din snygga färgblocks-ram och skicka den rått till användaren
                    block_match = f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} {match}{R} {BG_CYAN_BLOCK} {BG_RED_BLOCK} "
                    result_msg = f"PRIVMSG {user} :{block_match}\r\n"
                    oserve.queue_message(user, result_msg)
        else:
            print(f"[SEARCH RESULT] 0 Match(es) found for {user} in {channel} on '{search_term}'")
                
    except Exception as e:
        print(f"[SEARCH CRITICAL ERROR] Sökningen kraschade under filskanning: {e}")
        
    finally:
        # Släpp sökspärren så nästa användare kan söka direkt
        config.search_inprogress = False
        print(f"[SEARCH-FINISHED] Sökningen för {user} avslutades och låset frigjordes snyggt.")

def send_list_trigger_info(irc_sock, user):
    msg = f"List trigger(s): {config.C_RED}@{config.NICKNAME}{config.C_RESET} {config.SCRIPT_VERSION}{config.C_RESET}\r\n"
    oserve.queue_message(user, f"NOTICE {user} :{msg}")

def send_file_list(irc_sock, user, channel):
    """Hittar den befintliga .zip-listan och startar DCC SEND med rätt kanalspårning!"""
    # 🛡️ UNDERHÅLLS-STATUS LIVE: Om listuppdatering pågår, ge ett intelligent svar istället för felmeddelande!
    if getattr(config, 'update_inprogress', False) is True:
        msg = f"NOTICE {user} :{config.C_BOLD}System Notice{config.C_RESET}: Master list is currently rebuilding. Please wait a few minutes and try again. \r\n"
        oserve.queue_message(user, msg)
        return

    current_zip_path = find_latest_zip()
    
    if not current_zip_path or not os.path.exists(current_zip_path):
        oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}Error{config.C_RESET}: ZIP file missing. {config.C_BOLD}{config.SCRIPT_VERSION}{config.C_RESET} \r\n")
        return
        
    zip_filename = os.path.basename(current_zip_path)
    msg = f"Preparing full list ({zip_filename}) for {user}... {config.C_BOLD}{config.SCRIPT_VERSION}{config.C_RESET} \r\n"
    oserve.queue_message(user, msg)
    
    # Nu skickas 'channel' med till DCC-motorn i stället för 'user'
    dcc.handle_download_request(irc_sock, user, zip_filename, channel)


