# commands.py - Dedikerad modul för användarkommandon (Kö-hantering)
import sys
import config
import db

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
    """Laddar om botens moduler, nollställer timern och synkar kanaler live (Både JOIN och PART)!"""
    import importlib
    import sys
    import config
    import announce
    
    allowed_admin = getattr(config, 'ADMIN_NICK', 'FLAC').lower()
    if user.lower() != allowed_admin and user.lower() != "flac":
        print(f"[REHASH SECURITY] Ignorerade rehash-försök från obehörig användare: {user}")
        return

    # 1. SPARA GAMLA KANALER INNAN RELOAD (För att kunna jämföra PART-behov)
    old_chans = [c.strip().lower() for c in config.CHANNEL.split(",") if c.strip()]

    # PAUSA REKLAM TEMPORÄRT
    announce.is_ready = False
    announce.send_debug(f"Rehash triggered by {user} from {target_chan}. PAUSING NOTICES & ADVERTISEMENT...", category="INFO")
    
    try:
        # 2. REHASH: Definiera och ladda om alla centrala moduler live i minnet
        modules_to_reload = ['config', 'list', 'dcc', 'announce', 'security', 'db', 'stats_mgr']
        for mod_name in modules_to_reload:
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
                
        # Läs om sig själv
        if 'commands' in sys.modules:
            importlib.reload(sys.modules['commands'])
            
        print(f"[REHASH SUCCESS] Alla Python-moduler har blivit live-uppdaterade i RAM av {user}!")
        
        # Läs in den nyladdade configen och meddelandemodulen
        import config
        import announce
        announce.is_ready = True
        
        # Återställ din reklam-timer så den väntar 5 nya minuter
        if hasattr(announce, 'last_announce_time'):
            import time
            announce.last_announce_time = time.time()
            
        # ---------------------------------------------------------------------
        # 3. HELAUTOMATISK KANAL-SYNK (JOIN NYA / PART BORTTAGNA)
        # ---------------------------------------------------------------------
        oserve = sys.modules.get('oserve')
        irc_sock = getattr(oserve, 'irc_connection', None)
        
        if irc_sock:
            # Skapa listor över dina NYA önskade kanaler från din nyladdade config.py
            new_chans = [c.strip().lower() for c in config.CHANNEL.split(",") if c.strip()]
            
            # --- A. DETEKTERA OCH SKICKA JOIN FÖR NYA KANALER ---
            for chan in new_chans:
                if chan not in old_chans:
                    irc_sock.send(f"JOIN {chan}\r\n".encode())
                    # Skickar en snygg färgkodad [JOIN]-tagg till din #flac-debug
                    announce.send_debug(f"Joining channel {chan} due to new configuration layout!", category="JOIN")
                    print(f"[REHASH SYNC] Joined new channel: {chan}")
            
            # --- B. DETEKTERA OCH SKICKA PART FÖR BORTTAGNA KANALER ---
            for chan in old_chans:
                if chan not in new_chans:
                    # Se till att vi aldrig lämnar din dolda debug-kanal av misstag
                    debug_chan = getattr(config, 'DEBUG_CHANNEL', '#flac-debug').lower()
                    if chan != debug_chan:
                        irc_sock.send(f"PART {chan} :Removed from DDCore\r\n".encode())
                        # Skickar en snygg färgkodad [PART]-tagg till din #flac-debug
                        announce.send_debug(f"Parting channel {chan} due to new configuration layout!", category="PART")
                        print(f"[REHASH SYNC] Parted from removed channel: {chan}")
                        
            print(f"[REHASH SYNC] Channel sync completed successfully.")
        else:
            print("[REHASH WARNING] Kunde inte synka kanaler eftersom rå socket saknades i minnet.")
        # ---------------------------------------------------------------------
        
        # 4. BEKRÄFTELSE: Skickar VIP-notisen till #flac-debug
        announce.send_debug(f"Rehash completed! ADVERTISEMENT RESUMED (Timer reset to 5 minutes).", category="INFO")
        
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
    
    allowed_admin = getattr(config, 'ADMIN_NICK', 'FLAC').lower()
    if user.lower() != allowed_admin and user.lower() != "flac":
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
    
    allowed_admin = getattr(config, 'ADMIN_NICK', 'FLAC').lower()
    if user.lower() != allowed_admin and user.lower() != "flac":
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
