# security.py - Dedikerad modul för OmenServe Flood- och Banskydd
import time
import os
import sys
import config
import db

# Nickar vi redan har skickat en debug-notis om (en notis per nick och körning).
# send_debug sover 0.5s under lås och anropas härifrån av IRC-läsartråden, så en
# notis per BLOCKERAT MEDDELANDE skulle frysa nätverksloopen och slita anslutningen.
_ban_notified = set()


def check_user_status(user):
    """Kollar användaren mot tidsbegränsade bans (RAM) och hard_bans.txt.

    Returnerar False om användaren ska ignoreras helt, annars True.
    """
    import os
    import re
    import time
    import config
    import announce

    user_lower = user.lower()

    def _deny(reason, category):
        """Loggar alltid till konsolen, men skickar ENBART en debug-notis per nick."""
        print(f"[SECURITY BLOCK] Nekade {user}: {reason}")
        if user_lower not in _ban_notified:
            _ban_notified.add(user_lower)
            try:
                announce.send_debug(
                    f"Access denied for {config.C_BOLD}{user}{config.C_RESET} ({reason}).",
                    category=category
                )
            except Exception as notify_err:
                print(f"[SECURITY ERROR] Kunde inte skicka ban-notis: {notify_err}")
        return False

    # ---------------------------------------------------------------------
    # 1. TIDSBEGRÄNSADE BANS (dags-bans från flood-skyddet)
    # Dessa lever i config.banned_users som {nick: utgångstid} och läses in från
    # bans.txt vid boot. De är RADER MED TIDSSTÄMPEL, inte wildcard-mönster, och
    # fick därför aldrig regex-matchas som den gamla koden gjorde.
    # ---------------------------------------------------------------------
    expire_ts = config.banned_users.get(user_lower)
    if expire_ts is not None:
        try:
            expire_ts = float(expire_ts)
        except (TypeError, ValueError):
            expire_ts = 0.0

        if time.time() < expire_ts:
            until = time.strftime("%H:%M:%S", time.localtime(expire_ts))
            return _deny(f"temporary ban active until {until}", "TBAN")

        # Bannet har löpt ut - städa bort det ur RAM och av disken.
        del config.banned_users[user_lower]
        _ban_notified.discard(user_lower)
        try:
            import db
            db.save_bans_to_file()
        except Exception as save_err:
            print(f"[SECURITY ERROR] Kunde inte spara utgånget ban: {save_err}")

    # ---------------------------------------------------------------------
    # 2. PERMANENTA WILDCARD-BANS (hard_bans.txt, ett mönster per rad)
    # ---------------------------------------------------------------------
    hard_file = getattr(config, "HARD_BANS_FILE", "./data/hard_bans.txt")
    # Tracks whether the hard-ban list was actually READ. If the file is missing or the
    # read raised, this stays False and we must not treat "no match" as "definitely clean".
    hard_check_ok = False
    if os.path.exists(hard_file):
        try:
            with open(hard_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    pattern = line.strip().lower()
                    if not pattern or pattern.startswith("#"):
                        continue

                    # 🛡️ BREDD-SPÄRR: Ett mönster som bara består av stjärnor skulle
                    # stänga ute hela kanalen. Hoppa över det och skrik i loggen.
                    if not pattern.replace("*", ""):
                        print(f"[SECURITY WARNING] Ignorerade alltför brett mönster i {hard_file}: {pattern!r}")
                        continue

                    regex_pattern = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
                    if re.match(regex_pattern, user_lower):
                        return _deny(f"matched banned pattern '{pattern}'", "BAN")
            hard_check_ok = True
        except Exception as e:
            print(f"[SECURITY ERROR] Kunde inte läsa filen {hard_file}: {e}")

    # This user was seen clean, so drop any stale "already notified" mark: a LATER ban on
    # the same nick then notifies once more. Previously the mark was only cleared on the
    # timed-ban expiry path, so after an !unban and a re-!ban the debug channel stayed
    # silent and the admin never saw the pattern take effect.
    #
    # Only clear when the hard-ban list was genuinely read. The scan fails open - a missing
    # file, or a read landing inside the truncate window of an !unban rewrite - and clearing
    # on that path would re-arm the notice, costing another 0.5s read-thread stall the next
    # time the same still-banned nick speaks.
    #
    # Note this does not otherwise prune the set: a nick is only removed if it speaks again
    # while unbanned.
    if hard_check_ok:
        _ban_notified.discard(user_lower)

    return True  # Användaren är grön och fri att använda boten!


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
