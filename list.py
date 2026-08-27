# list.py - Slimmed down; scanning lives in update_list.py
import os
import time
import datetime
import config
import oserve
import dcc
import announce

LIST_FILE_PATH = os.path.join(config.LOCAL_LIST_DIR, "flac-serv.txt")


# Resolved per call rather than once at import. LOCAL_LIST_DIR is a config value,
# and !rehash reloads config - a path baked in at import time would keep pointing
# at the old directory for the life of the process after the operator moved it.
def size_file_path():
    return os.path.join(config.LOCAL_LIST_DIR, config.LIST_SIZE_FILE)


def rawbytes_file_path():
    return os.path.join(config.LOCAL_LIST_DIR, config.LIST_RAWBYTES_FILE)

# NOTE: a second, shadowing definition of find_latest_list() used to sit here. Python keeps
# the LAST definition, so this one never ran - and the two had drifted: this one did not
# exclude the "-RAR-" album list, so had it ever become the live one the bot would have
# served the album list as its master list. Removed so they cannot diverge again.

def find_latest_zip():
    """Find the newest .zip, for when somebody types the bot's nickname."""
    if not os.path.exists(config.LOCAL_LIST_DIR):
        return None
    files = [f for f in os.listdir(config.LOCAL_LIST_DIR) if f.startswith(config.LIST_BASE_NAME) and f.endswith(".zip")]
    if not files:
        return None
    files.sort(reverse=True)
    return os.path.join(config.LOCAL_LIST_DIR, files[0])

def get_file_count_date_size_and_raw_bytes():
    """The EXACT number of music files, counting only lines that start with the trigger."""
    latest_list = find_latest_list()
    if not latest_list or not os.path.exists(latest_list):
        return 0, "No List", "0B", 0
        
    try:
        count = 0
        with open(latest_list, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line_strip = line.strip()
                # Count only real request lines, skipping the "===" folder separators,
                # blank lines and text headers.
                #
                # This matches on "!" alone rather than on f"!{config.NICKNAME} ". The list
                # is written with whatever nick was current at generation time, so after a
                # 433 fallback the old test matched nothing and the advert reported 0 files
                # even though the list was fine. Every request line in the generated file
                # starts with "!" (update_list.py:156) and no header or separator does, so
                # this is the same filter execute_search already applies to the same file.
                if line_strip.startswith("!"):
                    count += 1
            
        mtime = os.path.getmtime(latest_list)
        dt = datetime.datetime.fromtimestamp(mtime)
        day = dt.day
        suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        date_str = dt.strftime(f"%b {day}{suffix}")
        
        # Each side file is read in its own try. They used to sit inside the outer
        # try below, so an unparseable rawbytes file - int("") on a truncated write
        # raises ValueError - collapsed the WHOLE tuple to (0, "Error", "0B", 0).
        # The count and the list date come from the master list and were perfectly
        # good; one clipped byte count must not throw them away and make the advert
        # announce an error.
        size_str = "0B"
        try:
            size_path = size_file_path()
            if os.path.exists(size_path):
                with open(size_path, "r", encoding="utf-8") as sf:
                    size_str = sf.read().strip() or "0B"
        except OSError as size_err:
            print(f"[LIST] Could not read {config.LIST_SIZE_FILE}: {size_err}")

        raw_bytes = 0
        try:
            raw_path = rawbytes_file_path()
            if os.path.exists(raw_path):
                with open(raw_path, "r", encoding="utf-8") as rbf:
                    raw_bytes = int(rbf.read().strip())
        except (OSError, ValueError) as raw_err:
            print(f"[LIST] Could not read {config.LIST_RAWBYTES_FILE}: {raw_err}")
                
        return count, date_str, size_str, raw_bytes
    except Exception as e:
        print(f"[ERROR] Could not read the exact file statistics: {e}")
        return 0, "Error", "0B", 0

# list.py - The search module (part 1 of 2)
import os
import glob
import re
import sys
import config
import announce

def find_latest_list():
    """Find the newest master text list in the lists directory.

    Globs on config.LIST_BASE_NAME, which is what update_list.py actually names the files
    with (update_list.py:38). It previously globbed on config.NICKNAME - a value irc.py
    REBINDS at runtime when the server returns 433 and the bot falls back to ALT_NICKNAME.
    From that moment the glob matched nothing: @find answered "No MasterList found" and the
    5-minute advert publicly announced "For My List Of: 0 Files" into all six channels.
    The two constants are equal in normal operation, so this changes nothing until a
    nick collision happens.
    """
    try:
        all_txt_files = sorted(glob.glob(os.path.join(config.LOCAL_LIST_DIR, f"{config.LIST_BASE_NAME}-*.txt")))
        # Keep the RAR list out of the search, so only the master list is scanned
        true_master_lists = [f for f in all_txt_files if "-RAR-" not in f]
        if true_master_lists:
            return true_master_lists[-1]
    except Exception as e:
        print(f"[SEARCH ERROR] Could not find the latest list: {e}")
    return None

def execute_search(irc_sock, user, search_term, channel):
    """Search the list file, sending the matching rows exactly as they are stored."""
        # MAINTENANCE GATE: block the search while an update is running, if config says so
    if getattr(config, 'PAUSE_ON_UPDATE', False) is True and getattr(config, 'search_inprogress', False) is True:
        oserve = sys.modules.get('oserve')
        if oserve:
            oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}System Message{config.C_RESET}: Search engine is temporarily paused during MasterList rebuild. Please wait a moment.\r\n")
        print(f"[MAINTENANCE BLOCK] Refused a search (@find) from {user}: an !update is running.")
        return

    # Guard against two searches at once
    if getattr(config, 'search_inprogress', False):
        print(f"[SEARCH BLOCK] Ignored a search from {user}: another scan is already running.")
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
        
        # Strip mIRC colour codes and control characters from the search terms
        raw_clean = search_term.replace('\x02', '').replace('\x1f', '').replace('\x0f', '')
        raw_clean = re.sub(r'\x03(?:\d{1,2}(?:,\d{1,2})?)?', '', raw_clean)
        
        # Split the search terms
        clean_term = re.sub(r'[-*_.]', ' ', raw_clean)
        search_words = [w.strip().lower() for w in clean_term.split() if w.strip()]
        
        matches = []
        total_matches = 0
        # ---------------------------------------------------------------------
        # Straight copy: no reformatting, the file row is sent raw
        # ---------------------------------------------------------------------
        with open(current_list_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                # Remove hidden null bytes and clean up the line ending
                line_strip = line.replace('\x00', '').strip()
                
                # SAFETY FILTER: only let through genuine file rows, which start with '!'
                if not line_strip or not line_strip.startswith("!"):
                    continue
                
                line_lower = line_strip.lower()
                
                # Does this row contain EVERY search term?
                is_match = True
                for word in search_words:
                    if word not in line_lower:
                        is_match = False
                        break
                
                if is_match and search_words:
                    total_matches += 1
                    
                    # Keep the row exactly as it is on disk, up to the configured limit
                    max_results = getattr(config, 'MAX_SEARCH_RESULTS', 5)
                    if len(matches) < max_results:
                        matches.append(line_strip)

        if matches:
            # Send the search header privately to the requester
            announce.send_search_result_header(user, search_term, total_matches, channel)
            
            oserve = sys.modules.get('oserve')
            if oserve:
                BG_RED_BLOCK  = "\x0304,05"  # Dark red border
                BG_CYAN_BLOCK = "\x0310,10" # Turkos kant
                BG_TEXT_BOX   = "\x0301,00"  # Black text on a WHITE background
                R = "\x0f"                  # Full reset
                
                for match in matches: 
                    # Wrap the row in the colour-block frame and send it raw to the user
                    block_match = f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} {match}{R} {BG_CYAN_BLOCK} {BG_RED_BLOCK} "
                    result_msg = f"PRIVMSG {user} :{block_match}\r\n"
                    oserve.queue_message(user, result_msg)
        else:
            print(f"[SEARCH RESULT] 0 Match(es) found for {user} in {channel} on '{search_term}'")
                
    except Exception as e:
        print(f"[SEARCH CRITICAL ERROR] The search crashed while scanning the file: {e}")
        
    finally:
        # Release the search lock so the next user can search immediately
        config.search_inprogress = False
        print(f"[SEARCH-FINISHED] The search for {user} finished and the lock was released cleanly.")

def send_list_trigger_info(irc_sock, user):
    msg = f"List trigger(s): {config.C_RED}@{config.NICKNAME}{config.C_RESET} {config.SCRIPT_VERSION}{config.C_RESET}\r\n"
    oserve.queue_message(user, f"NOTICE {user} :{msg}")

def send_file_list(irc_sock, user, channel):
    """Find the existing .zip list and start a DCC SEND, tracking the right channel."""
    # If a list update is running, answer with the status rather than an error
    if getattr(config, 'update_inprogress', False) is True:
        msg = f"NOTICE {user} :{config.C_BOLD}System Notice{config.C_RESET}: Master list is currently rebuilding. Please wait a few minutes and try again. \r\n"
        oserve.queue_message(user, msg)
        return

    current_zip_path = find_latest_zip()
    
    if not current_zip_path or not os.path.exists(current_zip_path):
        oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}Error{config.C_RESET}: ZIP file missing. {config.C_BOLD}{config.SCRIPT_VERSION}{config.C_RESET} \r\n")
        return
        
    zip_filename = os.path.basename(current_zip_path)
    # queue_message payloads go straight to the socket, so they must be complete IRC
    # commands. This one was a bare text line, so the server answered
    # "421 Preparing :Unknown command", the user never saw the notice, and a flood-queue
    # slot was spent on an error. The other NOTICE payloads in this module were already
    # correctly formed; the search results use PRIVMSG, which is equally valid.
    msg = f"NOTICE {user} :Preparing full list ({zip_filename}) for {user}... {config.C_BOLD}{config.SCRIPT_VERSION}{config.C_RESET} \r\n"
    oserve.queue_message(user, msg)
    
    # 'channel' is handed to the DCC engine now, instead of 'user'
    dcc.handle_download_request(irc_sock, user, zip_filename, channel)


