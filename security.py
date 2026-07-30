# security.py - Dedikerad modul för OmenServe Flood- och Banskydd
import time
import os
import sys
import config
import db

def check_user_status(user):
    """Kollar om användaren är bannad till midnatt (Läser enbart från RAM under körning)"""
    user_key = user.lower()
    
    if user_key in config.banned_users:
        if time.time() > config.banned_users[user_key]:
            del config.banned_users[user_key]
            db.save_bans_to_file()
            print(f"[BAN CONTROL] Unbanned {user}. Midnight has passed.")
            return True
        return False 
    return True

def is_flooding(user):
    """Skyddar boten mot flood, rensar kön vid ban, bannar till midnatt och sparar i data/ via db"""
    now = time.time()
    user_key = user.lower()
    oserve = sys.modules.get('oserve')
    
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
            if oserve:
                oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}[WARNING]{config.C_RESET} You flooded the server, you are now banned until midnight\r\n")
            return True
        else:
            del config.muted_until[user_key]
            
    if user_key not in config.user_requests:
        config.user_requests[user_key] = []
        
    config.user_requests[user_key] = [ts for f, ts in enumerate(config.user_requests[user_key]) if now - ts < config.REQUEST_WINDOW]
    config.user_requests[user_key].append(now)
    
    if len(config.user_requests[user_key]) > config.MAX_REQUESTS:
        config.muted_until[user_key] = now + config.MUTE_TIME
        
        if user_key in config.send_queue:
            del config.send_queue[user_key]
            
        print(f"[FLOOD CONTROL] Temporarily muted {user} for {config.MUTE_TIME} seconds. Queue cleared.")
        if oserve:
            oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}[WARNING]{config.C_RESET} You are moving too fast! Ignored and queue cleared for {config.MUTE_TIME} seconds.\r\n")
        return True
        
    return False