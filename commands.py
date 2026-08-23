# commands.py - Dedikerad modul för användarkommandon (Kö-hantering)
import sys
import config
import db

def is_admin(user):
    """Return True if `user` may run admin commands.

    Centralises a check that was duplicated across five handlers, so the eventual
    hostmask-based gate is a change in one place instead of five.

    Two deliberate changes from the copies this replaces:

    * The hardcoded `or user.lower() == "flac"` fallback is gone. It made the literal nick
      "flac" an admin regardless of what config.ADMIN_NICK was set to - an undocumented
      second account nobody could turn off. It is a no-op today because ADMIN_NICK is
      already "FLAC", so removing it changes nothing until that value is edited.
    * ADMIN_NICK may now be a comma-separated list, so a second operator can be added
      without reintroducing a hardcoded name.

    KNOWN LIMITATION: this is still nick-based, and an Undernet nick is not owned without
    services auth - anyone can take the nick while the real admin is offline and gain
    every admin command, now including the destructive !clearqueue. Closing that properly
    means matching ident@host, which irc.py does not currently capture: its PRIVMSG regex
    keeps only the nick. That is a separate change to irc.py plus this file.
    """
    import config

    raw = getattr(config, 'ADMIN_NICK', 'FLAC') or ''
    allowed = {n.strip().lower() for n in str(raw).split(',') if n.strip()}
    return str(user).lower() in allowed


def handle_queue_check(s, user, target):
    """Räknar antal köade filer för en specifik användare och ger anpassade svar i VIP-kön!"""
    user_key = user.lower()
    oserve = sys.modules.get('oserve')
    import list
    import dcc
    
    # 1. Räkna hur många filer just denna användare har i kön
    file_count = 0
    if hasattr(config, 'dcc_queue') and user_key in config.dcc_queue:
        file_count = len(config.dcc_queue[user_key])
        
    # 2. Hämta live-statistik för den utökade 0-notisen
    file_count_total, list_date, total_size, raw_bytes = list.get_file_count_date_size_and_raw_bytes()
    formatted_total_files = f"{file_count_total:,}"
    
    active_dl = oserve.active_downloads if oserve else 0
    free_slots = max(0, config.MAX_DCC_SLOTS - active_dl)
    slots_str = f"{free_slots}/{config.MAX_DCC_SLOTS}"
    
    queued_count = dcc.get_total_queued_count()
    queue_str = f"{queued_count}/{config.MAX_QUEUE_LIMIT}" if hasattr(config, 'MAX_QUEUE_LIMIT') else f"{queued_count}"

    # 3. VÄLJ LAYOUT BASERAT PÅ OM KÖN ÄR TOM ELLER INTE (Knivskarp fetstils-spärr!)
    if file_count > 0:
        # Layout om de faktiskt har filer i kön (Endast siffran och triggern är bold)
        msg = (
            f"NOTICE {user} :You have {config.C_BOLD}{config.C_RED}{file_count}{config.C_RESET} files in queue. "
            f"To remove your entire queue, type: {config.C_BOLD}{config.C_RED}@{config.NICKNAME}-remove{config.C_RESET} "
            f"or send CTCP: {config.C_BOLD}{config.C_GREEN}REMOVE{config.C_RESET}\r\n"
        )
    else:
        # DIN UTÖKADE LYX-LAYOUT: Enbart siffror, trigger och värden är bold + färg!
        msg = (
            f"NOTICE {user} :"
            f"You have {config.C_BOLD}{config.C_RED}0{config.C_RESET} files in queue. "
            f"Download my list with {config.C_BOLD}{config.C_GREEN}@{config.NICKNAME}{config.C_RESET} "
            f"of {config.C_BOLD}{config.C_RED}{formatted_total_files}{config.C_RESET}. "
            f"Slots {config.C_BOLD}{config.C_GREEN}{slots_str}{config.C_RESET}. "
            f"Queue {config.C_BOLD}{config.C_GREEN}{queue_str}{config.C_RESET}. "
            f"List {config.C_BOLD}{config.C_RED}{list_date}{config.C_RESET}. "
            f"({config.SCRIPT_VERSION})\r\n"
        )

        
    if oserve:
        oserve.queue_message(user, msg, is_vip=True)
    print(f"[COMMANDS] {user} checked their queue status ({file_count} files).")

def handle_queue_remove(s, user, target):
    """Raderar användarens kö helt från RAM-minnet och städar bort den från dcc_queue.txt på hårddisken"""
    user_key = user.lower()
    oserve = sys.modules.get('oserve')
    import dcc

    with dcc.queue_lock:
        # Ta bort från den vanliga kön
        if hasattr(config, 'dcc_queue') and user_key in config.dcc_queue:
            del config.dcc_queue[user_key]
            db.save_dcc_queue() # Spika den rensade kön till hårddisken direkt!

        # Ta även bort användaren ur frysboxen ifall de var frysta
        if hasattr(config, 'frozen_queues') and user_key in config.frozen_queues:
            del config.frozen_queues[user_key]

    msg = f"NOTICE {user} :Your queue has been completely removed. \r\n"
    if oserve:
        oserve.queue_message(user, msg)
    print(f"[COMMANDS] {user} removed their entire queue from the disk layout.")

def handle_admin_clear_queue(user, target_chan, msg_text):
    """🛡️ NY (issue #15): Tvångsrensar en ANNAN användares kö helt (spöknick, hängd post
    efter en netsplit/reconnect, etc.) - enbart admin. handle_queue_remove ovan kan bara
    en användare köra på sig själv via IRC; det här ger admin motsvarande makt över
    VEM SOM HELST, direkt via en enkel textrad, utan att behöva röra dcc_queue.txt på disken
    manuellt."""
    import os
    import config
    import announce
    import db
    import dcc

    if not is_admin(user):
        print(f"[SECURITY] Obehörig användare {user} försökte köra !clearqueue.")
        return

    parts = msg_text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        announce.send_debug("Syntax error! Använd: !clearqueue <nick>", category="INFO")
        return

    target_nick = parts[1].strip()
    target_key = target_nick.lower()

    removed_count = 0
    was_frozen = False

    with dcc.queue_lock:
        if hasattr(config, 'dcc_queue') and target_key in config.dcc_queue:
            removed_count = len(config.dcc_queue[target_key])

            # Delete any temporary .rar this queue owned before dropping the entries,
            # matching what the freeze-timeout sweep already does (dcc.py:93 and :313).
            # Without this the archives stay on the SSD cache forever, because the queue
            # rows naming them are the only record that they exist.
            for f_obj in config.dcc_queue[target_key]:
                if not isinstance(f_obj, dict):
                    continue
                if f_obj.get('is_temporary_zip') is not True or f_obj.get('is_unpacked_rar_folder'):
                    continue
                temp_path = f_obj.get('path')
                if not temp_path or not os.path.exists(temp_path):
                    continue
                # Never touch a file another user's queue still points at, and never one
                # that is being streamed right now.
                still_needed = any(
                    isinstance(o, dict) and o.get('path') == temp_path
                    for k, files in config.dcc_queue.items() if k != target_key
                    for o in files
                )
                if not still_needed:
                    still_needed = any(tx.get('file') == f_obj.get('file') for tx in config.active_transfers)
                if still_needed:
                    continue
                try:
                    os.remove(temp_path)
                    print(f"[ADMIN CLEARQUEUE] Removed orphaned temp archive: {temp_path}")
                except OSError as rm_err:
                    print(f"[ADMIN CLEARQUEUE] Could not remove {temp_path}: {rm_err}")

            del config.dcc_queue[target_key]
            db.save_dcc_queue()

        if hasattr(config, 'frozen_queues') and target_key in config.frozen_queues:
            del config.frozen_queues[target_key]
            was_frozen = True

    if removed_count > 0 or was_frozen:
        extra = " (var även fryst)" if was_frozen else ""
        announce.send_debug(
            f"Admin {config.C_BOLD}{user}{config.C_RESET} force-cleared queue for {config.C_BOLD}{target_nick}{config.C_RESET}: {config.C_BOLD}{removed_count}{config.C_RESET} file(s) removed{extra}.",
            category="INFO")
        print(f"[ADMIN CLEARQUEUE] {user} tvångsrensade {target_nick}s kö ({removed_count} filer, frozen={was_frozen}).")
    else:
        announce.send_debug(
            f"Clearqueue: {config.C_BOLD}{target_nick}{config.C_RESET} had no queue or frozen entry to remove.",
            category="INFO")
        print(f"[ADMIN CLEARQUEUE] {user} försökte rensa {target_nick}, men ingen kö eller fryst post hittades.")

def handle_ping_request(irc_sock, user, target_chan):
    """Startar tidtagaruret och skickar en unik latens-PING till IRC-servern"""
    import time
    import config
    
    # Spara mätdata i det globala minnet så att pong-funktionen kan läsa det sen
    config.ping_start_time = time.time()
    config.ping_triggered_by = user
    config.ping_channel_source = target_chan
    
    # Skicka mätpaketet direkt till serverns råa socket
    try:
        irc_sock.send(b"PING :OSERVE_LATENCY_CHECK\r\n")
        print(f"[PING COMMAND] Latensmätning startad av {user} i {target_chan}.")
    except Exception as e:
        print(f"[PING ERROR] Kunde inte skicka PING-paket: {e}")

def handle_pong_response(category="INFO"):
    """Fångar serverns svar, räknar ut latens i sekunder med 3 decimaler och skickar VIP-debug!"""
    import time
    import config
    import announce
    
    start_time = getattr(config, 'ping_start_time', None)
    if start_time:
        # ÄNDRAD: Vi behåller värdet i sekunder direkt i stället för millisekunder
        latency_sec = time.time() - start_time
        
        trigger_user = getattr(config, 'ping_triggered_by', config.NICKNAME)
        source_chan = getattr(config, 'ping_channel_source', config.CHANNEL)
        
        # ÄNDRAD: Formatet :.3f tvingar Python att alltid visa exakt 3 decimaler (t.ex. 0.129)
        announce.send_debug(
            f"Latency Check triggered by {trigger_user} from {source_chan} -> IRC Server Response Time: {latency_sec:.3f} sec", 
            category=category
        )
        
        config.ping_start_time = None

def handle_rehash_request(user, target_chan):
    """Laddar om moduler live helt i RAM-minnet UTAN att smutsa ner eller läsa från hårddisken!"""
    import importlib
    import sys
    import config
    import announce
    import copy
    
    if not is_admin(user):
        print(f"[REHASH SECURITY] Ignorerade rehash-försök från obehörig användare: {user}")
        return

    # =====================================================================
    # 1. ULTIMAT RAM-BACKUP: Spara ALLT live-data i tillfälliga variabler
    # =====================================================================
    # A. Backup på användarlistor (NAMES)
    ram_backup_users = {}
    if hasattr(config, 'channel_users') and isinstance(config.channel_users, dict):
        ram_backup_users = copy.deepcopy(config.channel_users)
        print(f"[REHASH RAM] Tog backup på användarlistor för {len(ram_backup_users)} kanaler.")

    # B. Backup på aktiva DCC slots
    ram_backup_slots = 0
    ram_user_slots = {}
    for mod_name in ['dcc', 'config', 'oserve']:
        mod = sys.modules.get(mod_name)
        if mod:
            for attr in ['active_downloads', 'current_sends', 'total_sends']:
                if hasattr(mod, attr) and getattr(mod, attr) > 0:
                    ram_backup_slots = getattr(mod, attr)
            for attr in ['user_slots', 'active_users', 'current_user_slots']:
                if hasattr(mod, attr) and isinstance(getattr(mod, attr), dict):
                    raw_slots = getattr(mod, attr)
                    ram_user_slots = {k.lower(): v for k, v in raw_slots.items()}

    # C. Backup på KÖN (Behåll original-objekten i RAM utan disk-mellanlandning)
    ram_backup_queue = {}
    for mod_name in ['dcc', 'config', 'oserve', 'queue_mgr', 'list']:
        mod = sys.modules.get(mod_name)
        if mod:
            for attr in ['dcc_queue', 'rar_queue', 'download_queue']:
                if hasattr(mod, attr) and isinstance(getattr(mod, attr), dict):
                    raw_q = getattr(mod, attr)
                    # Spara bara användare som faktiskt har äkta låtar kvar i kön
                    ram_backup_queue = {k.lower(): v for k, v in raw_q.items() if v and len(v) > 0}

    if ram_backup_queue:
        print(f"[REHASH RAM] Säkrat {len(ram_backup_queue)} aktiva fildelningsköer live i RAM-minnet.")

    # Spara undan gamla kanaler för JOIN/PART-jämförelsen
    old_chans = [c.strip().lower() for c in config.CHANNEL.split(",") if c.strip()]

    # PAUSA REKLAM TEMPORÄRT
    announce.is_ready = False
    announce.send_debug(f"Rehash triggered by {user} from {target_chan}. PAUSING NOTICES & ADVERTISEMENT...", category="INFO")
    
    try:
        # 2. REHASH: Ladda om alla centrala moduler live i minnet
        modules_to_reload = ['config', 'list', 'dcc', 'announce', 'security', 'db', 'stats_mgr']
        for mod_name in modules_to_reload:
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
                
        if 'commands' in sys.modules:
            importlib.reload(sys.modules['commands'])
            
        print(f"[REHASH SUCCESS] Alla Python-moduler har blivit live-uppdaterade i RAM av {user}!")
        
        # Läs in den nyladdade configen
        import config
        import announce
        announce.is_ready = True
        
         # =====================================================================
        # 3. ÅTERSTÄLL FRÅN RAM: Skriv tillbaka all data till de nya modulerna
        # =====================================================================
        # Återställ användare
        config.channel_users = ram_backup_users if ram_backup_users else {}
        print(f"[REHASH RAM] Återställde framgångsrikt {len(config.channel_users)} kanallistor i nya RAM.")

        # Återställ slots
        for mod_name in ['dcc', 'config', 'oserve']:
            mod = sys.modules.get(mod_name)
            if mod:
                if ram_backup_slots > 0:
                    for attr in ['active_downloads', 'current_sends', 'total_sends']:
                        if hasattr(mod, attr): setattr(mod, attr, ram_backup_slots)
                if ram_user_slots:
                    for attr in ['user_slots', 'active_users', 'current_user_slots']:
                        if hasattr(mod, attr):
                            combined_slots = {k.lower(): v for k, v in ram_user_slots.items()}
                            for k, v in ram_user_slots.items(): combined_slots[k.upper()] = v
                            setattr(mod, attr, combined_slots)

        # Återställ kön (Tryck tillbaka de exakta, rena objekten STRICT på små bokstäver)
        if ram_backup_queue:
            combined_queue = {}
            for k, v in ram_backup_queue.items():
                combined_queue[k.lower()] = v
                
            for mod_name in ['dcc', 'config', 'oserve', 'queue_mgr', 'list']:
                mod = sys.modules.get(mod_name)
                if mod:
                    for attr in ['dcc_queue', 'rar_queue', 'download_queue']:
                        if hasattr(mod, attr):
                            setattr(mod, attr, combined_queue)
            print(f"[REHASH RAM] Aktiv fildelningskö återställd spikrakt i minnet på små bokstäver!")


        # Nollställ textköerna (send_queue) till tomma dicts så de inte krockar med text
        for mod_name in ['queue_mgr', 'config', 'oserve', 'irc']:
            mod = sys.modules.get(mod_name)
            if mod:
                for attr in ['send_queue', 'msg_queue', 'out_queue']:
                    if hasattr(mod, attr): setattr(mod, attr, {})
        print(f"[REHASH RAM] Textutmatningsköerna (send_queue) har nollställts i RAM.")

        # Återställ din reklam-timer så den väntar 5 nya minuter
        if hasattr(announce, 'last_announce_time'):
            import time
            announce.last_announce_time = time.time()
            
        # ---------------------------------------------------------------------
        # 4. HELAUTOMATISK KANAL-SYNK (JOIN NYA / PART BORTTAGNA)
        # ---------------------------------------------------------------------
        oserve = sys.modules.get('oserve')
        irc_sock = getattr(oserve, 'irc_connection', None)
        
        if irc_sock:
            new_chans = [c.strip().lower() for c in config.CHANNEL.split(",") if c.strip()]
            
            for chan in new_chans:
                if chan not in old_chans:
                    irc_sock.send(f"JOIN {chan}\r\n".encode())
                    announce.send_debug(f"Joining channel {chan} due to new configuration layout!", category="JOIN")
                    if chan.lower() not in config.channel_users:
                        config.channel_users[chan.lower()] = set()
            
            for chan in old_chans:
                if chan not in new_chans:
                    debug_chan = getattr(config, 'DEBUG_CHANNEL', '#flac-debug').lower()
                    if chan != debug_chan:
                        irc_sock.send(f"PART {chan} :Removed from DDCore\r\n".encode())
                        announce.send_debug(f"Parting channel {chan} due to new configuration layout!", category="PART")
                        if chan.lower() in config.channel_users:
                            del config.channel_users[chan.lower()]
            
            print("[REHASH SYNK] Skickar en bakgrunds-NAMES för att hålla listorna helt färska...")
            for chan in new_chans:
                irc_sock.send(f"NAMES {chan}\r\n".encode())
                
            print(f"[REHASH SYNC] Channel sync completed successfully.")
        else:
            print("[REHASH WARNING] Kunde inte synka kanaler eftersom rå socket saknades i minnet.")
        # ---------------------------------------------------------------------
        
        # 5. BEKRÄFTELSE VIA VIP-EXPRESSEN
        announce.send_debug(f"Rehash completed! RAM-Memory preserved seamlessly without disk-paging.", category="INFO")
        
        # 🔥 SLUSS-ÖPPNARE: Nollställer alla gamla hängda RAM-lås och rensar spök-spärrar vid rehash!
        config.rar_inprogress = False
        if hasattr(config, 'user_processing_lock'):
            config.user_processing_lock = set()

        # Hämta den sanna, levande nätverkssocketen direkt ur RAM-minnet
        oserve_mod = sys.modules.get('oserve')
        live_socket = getattr(oserve_mod, 'irc_connection', None) if oserve_mod else None
        
        if live_socket:
            import dcc
            import threading
            print("[REHASH-WAKE] Släpper fram köade användare i lediga slots...")
            threading.Thread(
                target=dcc.check_queue_and_send, 
                args=(live_socket, "system_next_trigger_fallback"), 
                daemon=True
            ).start()
        else:
            print("[REHASH ERROR] Kunde inte väcka kön helautomatiskt eftersom live_socket saknades i RAM.")
        
    except Exception as e:
        import announce
        announce.is_ready = True
        print(f"[REHASH CRITICAL ERROR] Det gick inte att live-ladda om filerna: {e}")
        announce.send_debug(f"Rehash FAILED (Notices Resumed for safety): {e}", category="INFO")


def handle_hard_ban_request(user, target_chan, msg_text):
    """Lägger till ett permanent wildcard-mönster i hard_bans.txt direkt via mIRC!"""
    import config
    import announce
    import os
    
    if not is_admin(user):
        print(f"[SECURITY] Obehörig användare {user} försökte köra !ban.")
        return

    parts = msg_text.split(" ", 1)
    if len(parts) < 2:
        announce.send_debug("Syntax error! Använd: !ban <mönster*>", category="INFO")
        return
        
    pattern = parts[1].strip().lower()
    if not pattern:
        return
        
    filename = config.HARD_BANS_FILE
    
    # Läs in befintliga bans för att undvika dubbletter
    existing_bans = []
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            existing_bans = [line.strip().lower() for line in f if line.strip()]
            
    if pattern not in existing_bans:
        # Skriv till filen och tvinga ner det på disken direkt
        with open(filename, "a", encoding="utf-8") as f:
            f.write(f"{pattern}\n")
            
        announce.send_debug(f"Added permanent wildcard to hard_bans.txt: {config.C_BOLD}{pattern}{config.C_RESET}", category="BAN")
        print(f"[HARD BAN] {user} lade till permanent mönster: {pattern}")
    else:
        announce.send_debug(f"Pattern {pattern} is already banned permanently.", category="INFO")

def handle_hard_unban_request(user, target_chan, msg_text):
    """Tar bort ett permanent wildcard-mönster ur hard_bans.txt direkt via mIRC!"""
    import config
    import announce
    import os
    
    if not is_admin(user):
        return

    parts = msg_text.split(" ", 1)
    if len(parts) < 2:
        announce.send_debug("Syntax error! Använd: !unban <mönster*>", category="INFO")
        return
        
    pattern = parts[1].strip().lower()
    if not pattern:
        return
        
    filename = config.HARD_BANS_FILE
    if not os.path.exists(filename):
        announce.send_debug("The permanent hard_bans.txt file is empty.", category="INFO")
        return
        
    # Läs in alla rader och filtrera bort just det mönster du vill häva
    lines_to_keep = []
    found = False
    
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip().lower() == pattern:
                found = True
            else:
                if line.strip():
                    lines_to_keep.append(line.strip())
                    
    if found:
        # Skriv tillbaka de sparade raderna till filen
        with open(filename, "w", encoding="utf-8") as f:
            for line in lines_to_keep:
                f.write(f"{line}\n")
                
        announce.send_debug(f"Removed permanent wildcard from hard_bans.txt: {config.C_BOLD}{pattern}{config.C_RESET}", category="BAN")
        print(f"[HARD UNBAN] {user} hävde permanent mönster: {pattern}")
    else:
        announce.send_debug(f"Pattern {pattern} was not found in hard_bans.txt.", category="INFO")

def handle_list_update_request(user, target_chan):
    """Kör update_list.py, väntar in processen och plockar filantalet blixtsnabbt från första raden i listfilen!"""
    import subprocess
    import sys
    import os
    import re
    import config
    import announce
    import glob
    import threading
    import time
    
    if not is_admin(user):
        print(f"[SECURITY] Obehörig användare {user} försökte köra !update.")
        return

    # 🛡️ DYNAMISK UNDERHÅLLSLÅSNING: Vi slår enbart på det globala RAM-låset om växeln är True i config!
    if getattr(config, 'PAUSE_ON_UPDATE', False) is True:
        if getattr(config, 'search_inprogress', False) is True:
            announce.send_debug(f"List update request from {user} denied: Another system scan is already running.", category="INFO")
            return
        config.search_inprogress = True
        print(f"[MAINTENANCE START] {user} aktiverade !update. Botens sök- och fildelningssystem är nu PAUSAT!")
        announce.send_debug(f"System maintenance initiated by {user}. MasterList is rebuilding, file requests temporarily paused...", category="INFO")
    else:
        print(f"[UPDATE START] {user} aktiverade !update. Paus-växeln är False, fildelningen rullar på under tiden.")
        announce.send_debug(f"List update triggered by {user} from {target_chan}. Indexing NFS-drive...", category="INFO")


    # Inre hjälpfunktion som läser ENBART rad 1 i din RIKTIGA masterlista (0% belastning!)
    def get_count_from_list():
        try:
            # Hitta alla textfiler som matchar botens namn i mappen
            # LIST_BASE_NAME, not NICKNAME: irc.py rebinds NICKNAME on a 433 fallback, and
            # update_list.py names the files with LIST_BASE_NAME. Keyed off the live nick this
            # counted 0 both before and after a rebuild, so !update reported "0 files, added 0"
            # while the advert reported the real total. Matches list.find_latest_list().
            all_txt_files = sorted(glob.glob(os.path.join(config.LOCAL_LIST_DIR, f"{config.LIST_BASE_NAME}-*.txt")))
            
            # SÄKERHETSSPÄRR: Rensa bort din nya RAR-lista så vi STRICT läser den sanna masterlistan!
            true_master_lists = [f for f in all_txt_files if "-RAR-" not in f]
            
            if true_master_lists:
                list_path = true_master_lists[-1] # Välj den absolut senaste sanna masterlistan
                if os.path.exists(list_path):
                    with open(list_path, "r", encoding="utf-8", errors="ignore") as f:
                        first_line = f.readline().strip()
                        
                        # Letar efter mönstret "List of X Files" via regex
                        match = re.search(r"List of\s+([\d,.]+)\s+Files", first_line, re.IGNORECASE)
                        if match:
                            raw_num = match.group(1).replace(",", "").replace(".", "")
                            if raw_num.isdigit():
                                return int(raw_num)
        except Exception as e:
            print(f"[LIST READ ERROR] Kunde inte läsa rad 1: {e}")
        return 0

    # 1. Hämta de gamla sanna filsiffrorna från rad 1
    old_count = get_count_from_list()
    announce.send_debug(f"List update triggered by {user} from {target_chan}. Indexing NFS-drive, bot paused...", category="INFO")
    def async_list_updater():
        try:
            base_path = os.path.dirname(os.path.abspath(__file__))
            script_path = os.path.join(base_path, "update_list.py")
            
            if not os.path.exists(script_path):
                announce.send_debug(f"Critical Error: Could not find update_list.py", category="INFO")
                return
                
            # 2. TRÅDAD RUN (Väntar in processen helt utan stumma tidsgränser)
            process = subprocess.run([sys.executable, script_path], capture_output=True, text=True, timeout=None)
            
            if process.returncode == 0:
                # ---------------------------------------------------------------------
                # STENHÅRD NFS- och DISKSYNKRONISERING: Vänta 2 sekunder efter stängning!
                # Detta ger din NAS och nätverksbufferten tid att flusha filerna rent på disken.
                # ---------------------------------------------------------------------
                print(f"[UPDATE-SYNCH] Skriptet klart. Väntar 2.0s på disksynk inför filavläsning...")
                time.sleep(2.0)
                # ---------------------------------------------------------------------
                
                # 3. HÄMTA DET NYA FILANTALET FRÅN RAD 1 (Nu helt krock-säkrat!)
                new_count = get_count_from_list()
                
                # Räkna ut den sanna, exakta skillnaden helt matematiskt
                added_files = new_count - old_count
                if added_files < 0: 
                    added_files = 0
                
                # 4. BEKRÄFTELSE VIA VIP-EXPRESSEN
                announce.send_debug(
                    f"List update successfully completed! MasterList now contains {config.C_BOLD}{new_count:,}{config.C_RESET} files. "
                    f"Added {added_files:,} new file(s) since last index.", 
                    category="INFO"
                )

            else:
                error_msg = process.stderr.strip() if process.stderr else "Unknown script error"
                announce.send_debug(f"External update_list.py failed (Exit Code {process.returncode}): {error_msg}", category="INFO")
                
        except subprocess.TimeoutExpired:
            announce.send_debug("List update FAILED: Script execution timed out after 90 seconds.", category="INFO")
        except Exception as e:
            print(f"[UPDATE ERROR] Det gick inte att köra listuppdateringen: {e}")
            announce.send_debug(f"List update FAILED critical error: {e}", category="INFO")
        finally:
            # 🔓 ÅTERSTÄLL AUTOMATISKT: Släpp upp det globala paus-låset till False igen!
            config.search_inprogress = False
            
            # 🧼 SLÄCK UNDERHÅLLS-FLAGGAN: Nu vet list.py att listan är klar och öppnar zip-slussen live!
            config.update_inprogress = False
            print("[MAINTENANCE END] Botens fildelning och sökfunktioner har återstartats automatiskt.")

    # 🛡️ TÄND UNDERHÅLLS-FLAGGAN: Nu vet hela botten på under 0ms att en uppdatering startar live!
    config.update_inprogress = True

    # Starta bakgrundstråden linjärt
    threading.Thread(target=async_list_updater, daemon=True).start()
