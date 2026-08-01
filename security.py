# security.py - Dedikerad modul för OmenServe Flood- och Banskydd
import time
import os
import sys
import config
import db

def check_user_status(user):
    """Kollar användaren mot tillfälliga bans.txt och hard_bans.txt, samt loggar till #flac-debug!"""
    import os
    import re
    import config
    import announce
    
    user_lower = user.lower()
    
    # Vi mappar filnamnen till deras respektive snygga färgblocks-kategorier
    ban_config = [
        {"file": "bans.txt", "category": "TBAN"},
        {"file": config.HARD_BANS_FILE, "category": "BAN"}
    ]
    
    for item in ban_config:
        filename = item["file"]
        category_tag = item["category"]
        
        if not os.path.exists(filename):
            continue
            
        try:
            with open(filename, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    pattern = line.strip().lower()
                    if not pattern or pattern.startswith("#"):
                        continue
                    
                    # INTELLIGENT WILDCARD-MATCHNING (Gör om * till .* live i RAM)
                    regex_pattern = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
                    if re.match(regex_pattern, user_lower):
                        # BINGO! Spambotten matchade ett mönster. Slå till spärren live!
                        print(f"[SECURITY BLOCK] Ignorerade meddelande från {user} (Matchade mönster i {filename}: {pattern})")
                        
                        # NYTT: Skickar en blixtsnabb färgkodad VIP-notis till #flac-debug via din råa socket!
                        announce.send_debug(
                            f"Access denied for banned user {user} (matched pattern '{pattern}').", 
                            category=category_tag
                        )
                        return False
                        
        except Exception as e:
            print(f"[SECURITY ERROR] Kunde inte läsa filen {filename}: {e}")
            
    return True # Användaren är grön och fri att använda boten!


def is_flooding(user):
    """Skyddar boten mot flood, rensar kön vid ban, bannar till midnatt och loggar allt till #flac-debug!"""
    import time
    import sys
    import config
    import db
    import announce
    
    now = time.time()
    user_key = user.lower()
    oserve = sys.modules.get('oserve')
    
    # ---------------------------------------------------------------------
    # STEG 2: ANVÄNDAREN FORTSATTE HAMRA UNDER MUTE -> HÅRD DAGS-BAN TILL MIDNATT!
    # ---------------------------------------------------------------------
    if user_key in config.muted_until:
        if now < config.muted_until[user_key]:
            current_time_struct = time.localtime(now)
            seconds_since_midnight = (current_time_struct.tm_hour * 3600) + (current_time_struct.tm_min * 60) + current_time_struct.tm_sec
            seconds_until_midnight = 86400 - seconds_since_midnight
            
            config.banned_users[user_key] = now + seconds_until_midnight
            db.save_bans_to_file()
            
            if user_key in config.muted_until:
                del config.muted_until[user_key]
            
            if user_key in config.send_queue:
                del config.send_queue[user_key]
            
            print(f"[SECURITY BAN] Banned {user} until midnight. Saved to {config.BANS_FILE} via db.py.")
            
            # NY VIP-LOGG: Skickar en lila dags-ban-notis direkt till mIRC på 0ms!
            announce.send_debug(
                f"User {user} ignored warnings and flooded during mute. Upgraded to daily ban until midnight! Saved to disk layout.", 
                category="TBAN"
            )
            
            if oserve:
                oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}[WARNING]{config.C_RESET} You flooded the server, you are now banned until midnight\r\n")
            return True
        else:
            del config.muted_until[user_key]
            
    # ---------------------------------------------------------------------
    # HISTORIK-SKANNING (Rensar gamla förfrågningar utanför fönstret)
    # ---------------------------------------------------------------------
    if user_key not in config.user_requests:
        config.user_requests[user_key] = []
        
    config.user_requests[user_key] = [ts for f, ts in enumerate(config.user_requests[user_key]) if now - ts < config.REQUEST_WINDOW]
    config.user_requests[user_key].append(now)
    
    # ---------------------------------------------------------------------
    # STEG 1: ANVÄNDAREN GÅR FÖR SNABBT -> TEMPORÄR MUTE/VARNING!
    # ---------------------------------------------------------------------
    if len(config.user_requests[user_key]) > config.MAX_REQUESTS:
        config.muted_until[user_key] = now + config.MUTE_TIME
        
        if user_key in config.send_queue:
            del config.send_queue[user_key]
            
        print(f"[FLOOD CONTROL] Temporarily muted {user} for {config.MUTE_TIME} seconds. Queue cleared.")
        
        # NY VIP-LOGG: Skickar en lila temporär varningsnotis direkt till mIRC på 0ms!
        announce.send_debug(
            f"User {user} moving too fast! Triggered temporary mute for {config.MUTE_TIME} seconds. Queue cleared.", 
            category="TBAN"
        )
        
        if oserve:
            oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}[WARNING]{config.C_RESET} You are moving too fast! Ignored and queue cleared for {config.MUTE_TIME} seconds.\r\n")
        return True
        
    return False
