# =====================================================================
# DCC.PY - FULLSTÄNDIGT KRASCHSÄKRAD OCH DUBBELFIL-OPTIMERAD MASTER-KOD
# =====================================================================
import socket
import threading
import time
import os
import sys
import re
import subprocess

import config
import list as list_mod
import announce
import db

# Skapa trådlåset direkt i toppen av modulen så att kön räknas upp spikrakt
queue_lock = threading.Lock()

def is_safe_path(base_dir, path, follow_symlinks=True):
    """Säkerhetsfilter: Förhindrar Directory Traversal-attacker"""
    if follow_symlinks:
        matchpath = os.path.realpath(path)
    else:
        matchpath = os.path.abspath(path)

    base = os.path.realpath(base_dir)

    # 🛡️ FIXAD: Jämför per katalogsteg istället för en rå startswith.
    # Med enbart startswith skulle "/mnt/nfs-musik-backup" felaktigt godkännas
    # som en del av "/mnt/nfs-musik", eftersom strängen råkar börja likadant.
    return matchpath == base or matchpath.startswith(base + os.sep)

def user_is_present_in_ram(user_key):
    """Kollar om en användare finns kvar i NÅGON av botens live-kanallistor (353/JOIN-synkade)."""
    u = str(user_key).lower()
    for users_set in getattr(config, 'channel_users', {}).values():
        for known_user in users_set:
            if str(known_user).lower() == u:
                return True
    return False

def get_total_queued_count():
    """Räknar ut det totala antalet filer som står i alla personliga köer just nu"""
    total = 0
    for user_key, files in config.dcc_queue.items():
        total += len(files)
    return total

def get_public_ip_long():
    """Konverterar botens detekterade IP till ett mIRC-kompatibelt Long-format"""
    try:
        ip = config.MY_IP_OR_DOCK
        parts = ip.split('.')
        if len(parts) == 4:
            return (int(parts[0]) << 24) + (int(parts[1]) << 16) + (int(parts[2]) << 8) + int(parts[3])
    except Exception as e:
        print(f"[DCC IP ERROR] Kunde inte konvertera IP till Long: {e}")
    return 0

def check_queue_and_send(irc_sock, completed_user):
    """Kollar köer live och kör linjär RAR-packning via RAM-minnet (0% SERVERFLOOD!)"""
    import announce as announce_mod
    import subprocess
    import threading
    import socket
    import sys
    import os
    import re
    import time
    import config
    import db
    
    user_key = completed_user.lower()
    oserve = sys.modules.get('oserve')
    
    # 1. AUTOMATISK RENSNING AV GAMLA FRYSTA KÖER (Äldre än 5 min)
    # 🛡️ NY NÄTVERKS-SLUSS: Svepet får ENBART köras när boten själv är fullt kanalsynkad.
    # Under en reconnect är channel_users tom, och då raderade det gamla svepet köer för
    # användare som aldrig hade lämnat kanalen.
    if getattr(config, 'bot_joined_channel', False):
        with queue_lock if 'queue_lock' in globals() else threading.Lock():
            current_time = time.time()
            for f_user, freeze_timestamp in list(config.frozen_queues.items()):
                # ❄️ TINING: Användaren finns live i RAM igen – släpp frysen istället för att radera!
                if user_is_present_in_ram(f_user):
                    del config.frozen_queues[f_user]
                    print(f"[DCC FREEZE-THAW] {f_user} är tillbaka i kanallistan. Kön räddad från rensning.")
                    continue
                if (current_time - freeze_timestamp) > 300.0:
                    if f_user in config.dcc_queue:
                        for f_obj in config.dcc_queue[f_user]:
                            if isinstance(f_obj, dict) and f_obj.get('is_temporary_zip') is True and os.path.exists(f_obj['path']) and not f_obj.get('is_unpacked_rar_folder'):
                                try: os.remove(f_obj['path'])
                                except: pass
                        del config.dcc_queue[f_user]
                        db.save_dcc_queue()
                    if f_user in config.frozen_queues:
                        del config.frozen_queues[f_user]
                    print(f"[DCC QUEUE_CLEAN] {f_user} rensad permanent pga timeout.")

    if user_key == "system_next_trigger_fallback":
        user_key = ""

    next_file = None
    with queue_lock if 'queue_lock' in globals() else threading.Lock():
        if user_key and user_key in config.dcc_queue and config.dcc_queue[user_key]:
            if user_key not in config.frozen_queues:
                next_file = config.dcc_queue[user_key][0] # 🛡️ FIXAD: Plockar det översta elementet ur dcc_queue.txt!


    if next_file:
        if isinstance(next_file, dict):
            target_chan = next_file.get('channel', config.CHANNEL.split(','))
        else:
            target_chan = config.CHANNEL.split(',')
        
        user_is_actively_in_channel = False
        
        # 🛡️ TOTAL SKIFTLÄGES- OCH SYSTEM-BYPASS SLUSS (Inbäddad i perfekt symmetri):
        # Om completed_user är system-triggern ELLER om användaren precis har rehashats, 
        # så slår vi vidöppet för att utplåna alla tysta skiftläges-blockeringar live!
        if "system_next_trigger_fallback" in [str(completed_user).lower(), str(user_key)]:
            user_is_actively_in_channel = True
        else:
            if hasattr(config, 'channel_users'):
                for chan_name, users_set in config.channel_users.items():
                    lowered_channel_users = [u.lower() for u in users_set]
                    if user_key in lowered_channel_users or str(completed_user).lower() in lowered_channel_users:
                        user_is_actively_in_channel = True
                        break
            
        if user_is_actively_in_channel is True:
            # ---------------------------------------------------------------------
            # HÄR ÄR DEN LINJÄRA MAPP-PACKAREN: Körs en och en inuti sändnings-slussen!
            # ---------------------------------------------------------------------
            if isinstance(next_file, dict) and next_file.get('is_unpacked_rar_folder') is True:
                if hasattr(config, 'user_processing_lock') and completed_user.lower() in config.user_processing_lock:
                    print(f"[RAR-BLOCK] {completed_user} redan låst i RAM, blockerar spöktråd.")
                    return
                    
                if getattr(config, 'rar_inprogress', False):
                    print(f"[RAR-HOLD] {completed_user} väntar i kön eftersom en annan packning pågår live...")
                    return

                config.rar_inprogress = True
                if hasattr(config, 'user_processing_lock'):
                    config.user_processing_lock.add(completed_user.lower())

                def inline_rar_packer(sock):
                    true_source_dir = next_file['path']
                    raw_filename = next_file['file']

                    # 🛡️ ANDRA FÖRSVARSLINJEN: Köposter överlever omstarter via dcc_queue.txt,
                    # så en förgiftad rad som köades INNAN traversal-spärren fanns skulle annars
                    # fortfarande packas här. Verifiera sökvägen igen precis före rar-anropet.
                    if not is_safe_path(config.FILE_DIRECTORY, true_source_dir):
                        print(f"[SECURITY] Blockade förgiftad köpost för {completed_user}: {true_source_dir}")
                        with queue_lock:
                            if completed_user.lower() in config.dcc_queue:
                                config.dcc_queue[completed_user.lower()] = [
                                    e for e in config.dcc_queue[completed_user.lower()] if e is not next_file
                                ]
                        db.save_dcc_queue()
                        config.rar_inprogress = False
                        if hasattr(config, 'user_processing_lock'):
                            config.user_processing_lock.discard(completed_user.lower())
                        announce_mod.send_debug(
                            f"Poisoned queue entry discarded for {config.C_BOLD}{completed_user}{config.C_RESET}: path outside the music root.",
                            category="HARDBAN")
                        return

                    # 1. Rensa bort eventuella gamla .rar-ändelser från strängen
                    clean_name = re.sub(r'(?:\.rar)+$', '', raw_filename, flags=re.IGNORECASE)
                    
                    # 2. 🛡️ APOSTROF-RÄDDARE: Om originalmappen på disken har en apostrof, återställ den live!
                    # Detta garanterar att AutoQ matchar filnamnet till 100% i alla lägen.
                    folder_leaf = os.path.basename(true_source_dir.rstrip('/\\'))
                    if "'" in folder_leaf and "'" not in clean_name:
                        # Om originalet har apostrof men namnet saknar det, byt ut motsvarande understreck
                        # genom att matcha strukturen från disk-mappen
                        clean_name = folder_leaf.replace(' ', '_')
                        clean_name = re.sub(r'[^a-zA-Z0-9\s\(\)\-_\']', '_', clean_name)
                    else:
                        # Standard-tvätt om ingen apostrof-krock upptäcktes
                        clean_name = clean_name.replace(' ', '_')
                        clean_name = re.sub(r'[^a-zA-Z0-9\s\(\)\-_\']', '_', clean_name)
                        
                    # 3. Spika det fullständiga, apostrof-säkrade .rar-filnamnet!
                    rar_filename = f"{clean_name}.rar"
                    target_rar_path = os.path.normpath(os.path.join(config.TMP_ZIP_DIR, rar_filename))

                    
                    if not os.path.exists(config.TMP_ZIP_DIR):
                        os.makedirs(config.TMP_ZIP_DIR, exist_ok=True)
                        
                    # 🧼 VARIABEL-TVÄTT: Klipper bort dolda radbrytningar (\n) från sökvägen på 0ms!
                    if isinstance(true_source_dir, str):
                        true_source_dir = true_source_dir.strip()

                    print(f"[LINJÄR RAR] Startar packning av: {true_source_dir} -> {target_rar_path}")


                    # 🛡️ SCEN-SÄKRAD OCH TOTAL-ISOLERAD ARGUMENT-SLUSS:
                    work_dir_switch = f"-w{os.path.abspath(config.TMP_ZIP_DIR)}"
                    cmd = ["rar", "a", "-ep1", work_dir_switch, os.path.abspath(target_rar_path), os.path.abspath(true_source_dir)]
                    process = subprocess.run(cmd, capture_output=True, text=True, timeout=None)
                    
                    if process.returncode == 0 and os.path.exists(target_rar_path):
                        print(f"[LINJÄR RAR] Komprimering lyckades. Väntar 2.0s på disksynk...")
                        time.sleep(2.0)
                        
                        final_size = os.path.getsize(target_rar_path)
                        print(f"[LINJÄR RAR] Arkiv helt spikat på disken: {final_size:,} bytes")
                        
                        next_file['path'] = target_rar_path
                        next_file['file'] = rar_filename
                        next_file['is_unpacked_rar_folder'] = False
                        
                        config.active_transfers.append({"user": completed_user, "file": rar_filename, "bytes_sent": 0, "next_file_obj": rar_filename})
                        if oserve: oserve.active_downloads = len(config.active_transfers)
                        
                        announce_mod.send_dcc_sending_notice(completed_user, rar_filename)
                        
                        threading.Thread(
                            target=start_dcc_send, 
                            args=(sock, completed_user, target_rar_path, rar_filename, target_chan, next_file), 
                            daemon=True
                        ).start()
                        return
                    else:
                        config.rar_inprogress = False
                        if hasattr(config, 'user_processing_lock'):
                            config.user_processing_lock.discard(completed_user.lower())
                        error_msg = process.stderr.strip() if process.stderr else "Unknown RAR engine issue"
                        print(f"[LINJÄR RAR ERROR] {error_msg}")
                        announce_mod.send_debug(f"Pack FAILED in queue slot for {completed_user}: {error_msg}", category="PART")
                        check_queue_and_send(sock, completed_user)
                        return

                # 🚀 TÄNDNINGEN AKTIV: Denna ligger på exakt rätt nivå och väcker funktionen ovanför direkt!
                threading.Thread(target=inline_rar_packer, args=(irc_sock,), daemon=True).start()
                return

             # 🛡️ STENHÅRD ELSE-ISOLERING: Hit kliver den ENBART om det är en vanlig ljudfil (.mp3/.flac)!
            else:
                f_name = next_file['file'] if isinstance(next_file, dict) else os.path.basename(str(next_file))
                f_path = next_file['path'] if isinstance(next_file, dict) else str(next_file)
                
                print(f"[DCC QUEUE] Verified live in RAM for {target_chan}! Next file for {completed_user}: {f_name}")
                config.active_transfers.append({"user": completed_user, "file": f_name, "bytes_sent": 0, "next_file_obj": f_name})
                if oserve: oserve.active_downloads = len(config.active_transfers)
                
                announce_mod.send_dcc_sending_notice(completed_user, f_name)
                threading.Thread(target=start_dcc_send, args=(irc_sock, completed_user, f_path, f_name, target_chan, next_file), daemon=True).start()
                return
        else:
            # -----------------------------------------------------------------
            # 🛡️ NÄTVERKS-SLUSS: Frys ALDRIG en kö när boten själv är av nätet!
            # Vid netsplit/reconnect är channel_users tom eller halvsynkad. Då VET vi inte
            # om användaren har gått – vi låter kön ligga helt orörd tills NAMES-synken är klar.
            # -----------------------------------------------------------------
            if not getattr(config, 'bot_joined_channel', False) or not getattr(config, 'channel_users', None):
                print(f"[DCC FREEZE-SKIP] Boten är inte kanalsynkad ännu. Behåller kön för {completed_user} orörd.")
                return

            # 🛡️ DUBBELTIMER-SPÄRR: En användare får ha exakt EN nedräkning åt gången.
            if user_key in getattr(config, 'frozen_queues', {}):
                print(f"[DCC FREEZE-HOLD] {completed_user} har redan en aktiv nedräkning. Startar ingen till.")
                return

            with queue_lock if 'queue_lock' in globals() else threading.Lock():
                config.frozen_queues[user_key] = time.time()
            print(f"[DCC REACTIVE FREEZE] {completed_user} har lämnat {target_chan} på riktigt! Startar timer...")
            announce_mod.send_debug(f"DCC reactive freeze triggered for {completed_user} in {target_chan}. Initiating 5-minute cooldown timer.", category="QUIT")
            
            def user_queue_timer(sock, target_user, original_chan):
                """🕒 VERIFIERANDE NEDRÄKNING (ersätter den blinda 300-sekunderssömnen).
                Klockan pausas helt medan boten är frånkopplad – botens egen downtime får
                ALDRIG räknas mot användarens kö – och nedräkningen avbryts direkt om
                användaren dyker upp igen via JOIN eller NAMES-synk."""
                t_key = target_user.lower()
                elapsed = 0
                
                while elapsed < 300:
                    time.sleep(10)
                    
                    # A) Någon annan har redan tinat upp kön (JOIN / NAMES / !rehash) – avbryt tyst.
                    if t_key not in getattr(config, 'frozen_queues', {}):
                        print(f"[DCC FREEZE-ABORT] {target_user} är redan upptinad. Nedräkningen avbryts, kön är räddad.")
                        return
                        
                    # B) Boten är själv offline – frys klockan helt och räkna INTE upp elapsed.
                    if not getattr(config, 'bot_joined_channel', False):
                        print(f"[DCC FREEZE-PAUSE] Boten är av nätet. Pausar nedräkningen för {target_user} på {elapsed}s.")
                        continue
                        
                    # C) Boten är online igen – verifiera mot den färska kanallistan i RAM.
                    if user_is_present_in_ram(t_key):
                        with queue_lock if 'queue_lock' in globals() else threading.Lock():
                            config.frozen_queues.pop(t_key, None)
                        print(f"[DCC FREEZE-ABORT] {target_user} hittades live i kanallistan. Kön behålls och väcks.")
                        announce_mod.send_debug(f"Queue for {config.C_BOLD}{target_user}{config.C_RESET} preserved – user verified back in channel before timeout.", category="JOIN")
                        threading.Thread(target=check_queue_and_send, args=(sock, target_user), daemon=True).start()
                        return
                        
                    elapsed += 10
                
                if hasattr(config, 'frozen_queues') and t_key in config.frozen_queues:
                    with queue_lock if 'queue_lock' in globals() else threading.Lock():
                        if t_key in config.dcc_queue:
                            for f_obj in config.dcc_queue[t_key]:
                                if isinstance(f_obj, dict) and f_obj.get('is_temporary_zip') is True and os.path.exists(f_obj['path']) and not f_obj.get('is_unpacked_rar_folder'):
                                    try: os.remove(f_obj['path'])
                                    except: pass
                            del config.dcc_queue[t_key]
                            db.save_dcc_queue()
                        del config.frozen_queues[t_key]
                    announce_mod.send_debug(f"Timer expired for {target_user} in {original_chan}. Personal queue has been erased.", category="PART")
                    
            threading.Thread(target=user_queue_timer, args=(irc_sock, completed_user, target_chan), daemon=True).start()
            return

    # =====================================================================
    # B) Global köhantering för nästa person i kön (Helsäkrad för 3 slots!)
    # =====================================================================
    if oserve:
        oserve.active_downloads = len(config.active_transfers)
        
    if len(config.active_transfers) < config.MAX_DCC_SLOTS:
        with queue_lock if 'queue_lock' in globals() else threading.Lock():
            for waiting_user, user_files in list(config.dcc_queue.items()):
                w_key = waiting_user.lower()
                
                # 🛡️ GLOBAL DUBBEL-SPÄRR: Skippa om användaren redan håller på att packas eller skickas!
                if hasattr(config, 'user_processing_lock') and w_key in config.user_processing_lock:
                    continue
                    
                if not user_files or len(user_files) == 0 or w_key in config.frozen_queues:
                    continue
                    
                g_next = user_files
                if not isinstance(g_next, dict):
                    continue
                    
                real_username = g_next.get('user_raw', waiting_user)
                w_key = real_username.lower()
                
                g_chan = g_next.get('channel', config.CHANNEL.split(','))
                g_name = g_next.get('file', '')
                g_path = g_next.get('path', '')
                
                user_is_globally_active = False
                if isinstance(g_chan, str):
                    channels_to_check = [g_chan]
                elif isinstance(g_chan, list):
                    channels_to_check = g_chan
                else:
                    channels_to_check = config.CHANNEL.split(',')

                if hasattr(config, 'channel_users'):
                    for single_chan in channels_to_check:
                        n_chan = str(single_chan).strip().lower()
                        if n_chan in config.channel_users:
                            lowered_glob_users = [u.lower() for u in config.channel_users[n_chan]]
                            if w_key in lowered_glob_users:
                                user_is_globally_active = True
                                break
                        
                if user_is_globally_active is True:
                    # 🛡️ GLOBAL ELSE-ISOLERING: Om det är en mapp, låt den lokala tråden köra i fred, starta inte dubbelt!
                    if g_next.get('is_unpacked_rar_folder') is True:
                        print(f"[DCC QUEUE] Globala kön upptäckte pågående mappladdning för {real_username}. Synkar lås...")
                        break
                    else:
                        print(f"[DCC QUEUE] New user {real_username} verified live in RAM for {g_chan}. Got slot.")
                        config.active_transfers.append({"user": real_username, "file": g_name, "bytes_sent": 0, "next_file_obj": g_name})
                        if oserve: oserve.active_downloads = len(config.active_transfers)
                        
                        announce_mod.send_dcc_sending_notice(real_username, g_name)
                        threading.Thread(target=start_dcc_send, args=(irc_sock, real_username, g_path, g_name, g_chan, g_next), daemon=True).start()
                        break


def handle_download_request(irc_sock, user, requested_file, target_chan):
    """Triggas när någon begär en fil eller en hel mapp via !rar (Helt kraschsäkrad mot .mp3/.flac list-fel)"""
    # ---------------------------------------------------------------------
    # 🛡️ GLOBAL UNDERHÅLLSSPÄRR: (Kliniskt ren utan dolda lokal-import krockar!)
    # ---------------------------------------------------------------------
    if getattr(config, 'PAUSE_ON_UPDATE', False) is True and getattr(config, 'search_inprogress', False) is True:
        oserve = sys.modules.get('oserve')
        if oserve:
            oserve.queue_message(user, f"NOTICE {user} :{config.C_BOLD}System Message{config.C_RESET}: MasterList is currently rebuilding. File requests temporarily paused. Please wait 1-2 minutes.\r\n")
        print(f"[MAINTENANCE BLOCK] Nekade fildelningsbegäran (.mp3/.flac/.rar) från {user} pga pågående !update.")
        return
    # ---------------------------------------------------------------------

    oserve = sys.modules.get('oserve')
    try:
        user_key = str(user).lower().strip()
        if oserve:
            oserve.active_downloads = len(config.active_transfers)
        print(f"[DCC] {user} requested: {requested_file}")

        # ---------------------------------------------------------------------
        # ASYNKRON MAPP-PACKNING (!rar Sluss med stenhård ROOT- och NFS-säkring)
        # ---------------------------------------------------------------------
        if requested_file.lower().startswith("!rar "):
            import announce as announce_mod
            
            raw_win_path = requested_file[5:].strip()
            
            # Vi klipper bort eventuella gamla rester om någon klistrar in en gammal rad
            if "::INFO::" in raw_win_path:
                raw_win_path = raw_win_path.split("::INFO::")[0].strip()
                
            win_path = re.sub(r'\s*\[[^\]]+\]$', '', raw_win_path).strip()
            
            # Ta bort eventuella dubbla snedstreck i slutet innan vi mappar mot Linux-disken
            clean_win_path = win_path.replace("\\", "/").replace("D:/", "").replace("d:/", "")


            if clean_win_path.upper().startswith("MUSIC/"):
                clean_win_path = clean_win_path[6:]
                
            linux_sub_path = clean_win_path.strip("/")
            true_source_dir = os.path.normpath(os.path.join(config.FILE_DIRECTORY, linux_sub_path))

            # ---------------------------------------------------------------------
            # 🛡️ TRAVERSAL-SPÄRR (KRITISK SÄKERHETSSLUSS):
            # Utan den här kontrollen kunde vem som helst i kanalen skriva
            # "!rar ../../root/.ssh" och få hela den katalogen packad och skickad
            # till sig. os.path.normpath äter upp alla "..", och den gamla
            # root-spärren nedan släppte igenom allt som innehöll ett snedstreck.
            # Vi verifierar därför att den FÄRDIGA sökvägen fortfarande ligger
            # innanför musikdisken innan något annat händer.
            # ---------------------------------------------------------------------
            if not is_safe_path(config.FILE_DIRECTORY, true_source_dir):
                print(f"[SECURITY] Blockade traversal-försök från {user}: {raw_win_path!r} -> {true_source_dir}")
                announce_mod.send_pack_error_notice(irc_sock, user)
                announce_mod.send_debug(
                    f"Path traversal denied for {config.C_BOLD}{user}{config.C_RESET}: request resolved outside the music root.",
                    category="HARDBAN")
                return

            if "/" not in linux_sub_path:
                print(f"[SECURITY] Blockade root-mappspackning från {user}: {linux_sub_path}")
                announce_mod.send_pack_error_notice(irc_sock, user)
                announce_mod.send_debug(f"Pack denied for {user}: {config.C_BOLD}{linux_sub_path}{config.C_RESET} is an artist root folder.", category="PART")
                return
            
            if not os.path.exists(true_source_dir) or not os.path.isdir(true_source_dir):
                announce_mod.send_debug(f"Pack error: Directory not found on disk storage for {user}.", category="PART")
                return

            with queue_lock:
                total_global_queued = get_total_queued_count()
                user_queued_count = len(config.dcc_queue.get(user_key, []))

                if total_global_queued >= config.MAX_GLOBAL_QUEUE:
                    announce_mod.send_dcc_error(user, "global_full")
                    return

                if user_queued_count >= config.MAX_USER_QUEUE:
                    announce_mod.send_dcc_error(user, "user_full")
                    return

                if user_key not in config.dcc_queue:
                    config.dcc_queue[user_key] = []

                # ---------------------------------------------------------------------
                # ÅTERSTÄLLD ALBUM-NAMNGIVARE (AutoQ-kompatibel med sparade parenteser!)
                # ---------------------------------------------------------------------
                folder_name = os.path.basename(true_source_dir.rstrip("/"))
                
                # RÄTTAD: Tillåter parenteser ( och ) samt vanliga binde-streck så att AutoQ.mrc kan matcha filnamnet!
                clean_folder_name = re.sub(r'[^\w\-_\. \(\)]', '', folder_name).replace(" ", "_")
                
                master_rar_filename = f"{clean_folder_name}.rar"


                config.dcc_queue[user_key].append({
                    "file": master_rar_filename, # SPARAD: Nu ligger det rena namnet spikat i dcc_queue.txt!
                    "path": true_source_dir,
                    "channel": target_chan,
                    "user_raw": user,
                    "is_unpacked_rar_folder": True,
                    "is_temporary_zip": True
                })
                import db
                db.save_dcc_queue() # Spika direkt till dcc_queue.txt!
                
                user_pos = len(config.dcc_queue[user_key])
                print(f"[RAR QUEUE] Added virtuell mapp {master_rar_filename} for {user} at position #{user_pos}.")
                
                # 🧼 SLIMMAD STARTRAD: Bevarad till 100%! Skickar en (1) enda ren rad till din debug-kanal direkt!
                announce_mod.send_debug(f"{user} requested \"{clean_folder_name}\". Starting rar and sending when done.", category="INFO")
                
                announce_mod.send_dcc_queue_notice(user, folder_name, user_pos)
                threading.Thread(target=check_queue_and_send, args=(irc_sock, user), daemon=True).start()
            return


        # --- FIX 1: Hämta index [0] ur listan INNAN .strip() körs! ---
        if " ::INFO::" in requested_file:
            parts = requested_file.split(" ::INFO::", 1)
            requested_file = parts[0].strip()

        requested_file = str(requested_file).lstrip("/")

        if requested_file.endswith(".zip") and config.LIST_BASE_NAME in requested_file:
            base_directory = os.path.abspath(config.LOCAL_LIST_DIR)
            full_path = os.path.join(base_directory, requested_file)
        else:
            base_directory = os.path.abspath(config.FILE_DIRECTORY)
            full_path = os.path.join(base_directory, requested_file)

        is_master_zip = requested_file.endswith(".zip") and config.LIST_BASE_NAME in requested_file
        if not is_master_zip and not os.path.exists(full_path):
            latest_list_path = list_mod.find_latest_list()
            if latest_list_path and os.path.exists(latest_list_path):
                try:
                    with open(latest_list_path, "r", encoding="utf-8", errors="ignore") as lf:
                        lines = lf.readlines()
                    target_folder_rel = None
                    clean_req = str(requested_file).lower().strip()
                    
                    for idx, line in enumerate(lines):
                        line_clean = line.strip()
                        if line_clean.startswith(f"!{config.NICKNAME} "):
                            # --- FIX 2: Säkra list-split för nick-sökningen ---
                            parts_nick = line_clean.split(f"!{config.NICKNAME} ", 1)
                            current_file_in_list = parts_nick[0].strip() if parts_nick else ""
                            
                            # --- FIX 3: Säkra list-split för info-sökningen ---
                            if "  ::INFO::" in current_file_in_list:
                                parts_info = current_file_in_list.split("  ::INFO::", 1)
                                current_file_in_list = parts_info[0].strip() if parts_info else ""
                                
                            if clean_req == str(current_file_in_list).lower().strip():
                                for back_idx in range(idx, -1, -1):
                                    back_line = lines[back_idx].strip()
                                    if back_line.upper().startswith("D:\\MUSIC\\"):
                                        raw_folder = back_line[9:]
                                        if raw_folder.endswith("\\"): raw_folder = raw_folder[:-1]
                                        target_folder_rel = raw_folder.replace("\\", "/")
                                        break
                                if target_folder_rel is not None: break
                                
                    if target_folder_rel is not None:
                        test_path = os.path.join(base_directory, target_folder_rel, requested_file)
                        if os.path.exists(test_path):
                            full_path = test_path
                except Exception as list_err:
                    print(f"[DCC-LOOKUP ERROR] {list_err}")
            if not os.path.exists(full_path):
                for root, dirs, files in os.walk(base_directory):
                    if requested_file in files:
                        full_path = os.path.join(root, requested_file)
                        break

        if not is_safe_path(base_directory, full_path):
            announce.send_dcc_error(user, "invalid_path")
            return

        if not os.path.exists(full_path) or os.path.isdir(full_path):
            announce.send_dcc_error(user, "file_not_found")
            return

        file_name = os.path.basename(full_path)

        with queue_lock:
            total_global_queued = get_total_queued_count()
            user_queued_count = len(config.dcc_queue.get(user_key, []))

            if total_global_queued >= config.MAX_GLOBAL_QUEUE:
                announce.send_dcc_error(user, "global_full")
                return

            if user_queued_count >= config.MAX_USER_QUEUE:
                announce.send_dcc_error(user, "user_full")
                return

            # STENHÅRD ANVÄNDARSLUSS: Vi kollar om nicket REDAN har en sändning igång inuti active_transfers
            user_already_transferring = any(str(tx['user']).lower() == user_key for tx in config.active_transfers)
            
            # Initiera ditt tillfälliga sändningslås i config om det saknas
            if not hasattr(config, 'user_processing_lock'):
                config.user_processing_lock = set()
                
            # Om användaren precis skickade in rader, kollar vi om nicket ligger låst i RAM-minnet
            user_is_processing = user_key in config.user_processing_lock
            user_has_queue = len(config.dcc_queue.get(user_key, [])) > 0

            # 🛡️ BESLUTSFATTANDE: Enbart om användaren är HELT ren i transfers, kö och minneslås får den sändas direkt!
            if not user_already_transferring and not user_is_processing and not user_has_queue and len(config.active_transfers) < config.MAX_DCC_SLOTS:
                # Lås användarens nick i RAM-minnet omedelbart så nästa rad kastas till kön!
                config.user_processing_lock.add(user_key)
                
                next_file_fake = {"path": full_path, "file": file_name, "channel": target_chan, "is_temporary_zip": False}
                config.active_transfers.append({"user": user, "file": file_name, "bytes_sent": 0, "next_file_obj": file_name})
                if oserve: oserve.active_downloads = len(config.active_transfers)
                announce.send_dcc_sending_notice(user, file_name)
                threading.Thread(target=start_dcc_send, args=(irc_sock, user, full_path, file_name, target_chan, next_file_fake), daemon=True).start()
                return
            else:
                # Användaren har redan en låt igång! Vi kastar raden spikrakt till dcc_queue.txt!
                if user_key not in config.dcc_queue:
                    config.dcc_queue[user_key] = []
                config.dcc_queue[user_key].append({"file": file_name, "path": full_path, "channel": target_chan, "user_raw": user, "is_temporary_zip": False})
                
                # Sparar och uppdaterar din dcc_queue.txt på hårddisken direkt!
                import db
                db.save_dcc_queue()
                
                user_pos = len(config.dcc_queue[user_key])
                announce.send_dcc_queue_notice(user, file_name, user_pos)
                return

    except Exception as e:
        print(f"[DCC ERROR] {e}")
        oserve = sys.modules.get('oserve')
        if oserve: oserve.send_fails_count += 1

def start_dcc_send(irc_sock, user, file_path, file_name, channel, next_file):
    """Sköter nätverksportarna, dolda CTCP och strömmar byten över nätverket med bevarad tidtagning"""
    global active_transfers
    import time
    import os
    import socket
    import sys
    import threading
    
    # 🛡️ TYPE-SÄKRING: Om file_path eller file_name råkar vara dictionaries, extrahera de sanna strängarna!
    if isinstance(next_file, dict):
        if not isinstance(file_path, str) or "{" in str(file_path):
            file_path = next_file.get('path', str(file_path))
        if not isinstance(file_name, str) or "{" in str(file_name):
            file_name = next_file.get('file', str(file_name))

    file_size = os.path.getsize(file_path) if (isinstance(file_path, str) and os.path.exists(file_path)) else 0
    ip_long = get_public_ip_long()
    start_time = time.time()
    bytes_sent = 0
    
    if ip_long == 0 or file_size == 0:
        print(f"[DCC CRITICAL ABORT] Avbröt sändning för {user}. Path: {file_path} (Size: {file_size})")
        try: 
            msg = f"NOTICE {user} :{config.C_BOLD}Error:{config.C_RESET} File access issue or empty payload. Please try again.\r\n"
            irc_sock.send(msg.encode('utf-8', errors='ignore'))
        except: 
            pass
            
        # 🛡️ BANTNINGS-BROMS: Vi rensar låsen och väntar i 3 sekunder för att utplåna Excess Flood permanent!
        config.rar_inprogress = False
        if hasattr(config, 'user_processing_lock'):
            config.user_processing_lock.discard(user.lower())
            
        with queue_lock if 'queue_lock' in globals() else threading.Lock():
            config.active_transfers = [tx for tx in config.active_transfers if tx['user'].lower() != user.lower()]
            
        time.sleep(3.0)
        check_queue_and_send(irc_sock, user)
        return


    dcc_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    dcc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    assigned_port = None
    for port in range(config.DCC_PORT_START, config.DCC_PORT_END + 1):
        try:
            dcc_sock.bind(('0.0.0.0', port))
            assigned_port = port
            break
        except socket.error:
            continue

    if assigned_port is None:
        try: irc_sock.send(f"NOTICE {user} :{config.C_BOLD}Error:{config.C_RESET} No available DCC ports.\r\n".encode())
        except: pass
        with queue_lock if 'queue_lock' in globals() else threading.Lock():
            config.active_transfers = [tx for tx in config.active_transfers if tx['user'].lower() != user.lower()]
        if isinstance(next_file, dict) and next_file.get('is_temporary_zip') is True and os.path.exists(file_path):
            try: os.remove(file_path)
            except: pass
        check_queue_and_send(irc_sock, user)
        return

    dcc_sock.settimeout(30.0)
    dcc_sock.listen(1)
    
    safe_file_name = file_name.replace(" ", "_")
    ctcp_handshake = f"PRIVMSG {user} :\x01DCC SEND {safe_file_name} {ip_long} {assigned_port} {file_size}\x01\r\n"
    
    try:
        irc_sock.send(ctcp_handshake.encode())
        print(f"[DCC-LISTEN] Listening on port {assigned_port} for {user} (Handshake sent directly).")
    except Exception as e:
        print(f"[DCC ERROR] Misslyckades att skicka handskakning: {e}")

    conn = None
    try:
        conn, addr = dcc_sock.accept()
        conn.settimeout(60.0)
        dcc_sock.settimeout(None)
        
       # ⚡ PROXMOX / BAHNHOF LINUX-OPTIMERING: Tvingar nätverkskortet att spruta paketen DIREKT!
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        print(f"[DCC-CONNECT] {user} connected from {addr}!")

        start_time = time.time()       

        with open(file_path, 'rb') as f:
            while True:
                # Topptrimmad 64 KB paketstorlek för maximal nätverksprestanda
                chunk = f.read(65536)
                if not chunk: break
                try:
                    conn.sendall(chunk)
                    bytes_sent += len(chunk)
                except socket.error as e:
                    raise e
                for tx in config.active_transfers:
                    if tx['user'].lower() == user.lower():
                        tx['bytes_sent'] += len(chunk)
                oserve = sys.modules.get('oserve')
                if oserve: oserve.total_sent_bytes += len(chunk)
                
        print(f"[DCC-SUCCESS] Filen skickades felfritt till {user}!")
        # 🕒 DIN BEPRÖVADE ORIGINAL-PAUS: Ger mIRC exakt 1.5 sekunder att stänga filen i lugn och ro!
        try: time.sleep(1.5)
        except: pass
 
        # ---------------------------------------------------------------------
        # NYTT: UPPDATERA STATISTIKEN PÅ DISKEN (Skräddarsydd för din stats.txt!)
        # ---------------------------------------------------------------------
        try:
            import db
            stats = db.load_advanced_stats()
            if isinstance(stats, list) and len(stats) > 6:
                stats[0] = str(int(stats[0]) + 1)          
                stats[1] = str(int(stats[1]) + file_size)  
                stats[4] = str(int(stats[4]) + 1)          
                stats[5] = str(int(stats[5]) + file_size)  
                db.save_advanced_stats(stats)              
                print(f"[DB COUNTER] Statistik uppdaterad live på disken! (Skickade filer: {stats[0]}st)")
        except Exception as db_err:
            print(f"[DB ERROR] Kunde inte räkna upp fildelningsstatistiken via db-modulen: {db_err}")
        # ---------------------------------------------------------------------

        try: conn.close()
        except: pass

    except socket.timeout:
        oserve = sys.modules.get('oserve')
        if oserve: oserve.send_fails_count += 1
    except Exception as e:
        oserve = sys.modules.get('oserve')
        if oserve: oserve.send_fails_count += 1
    finally:
        # 🛡️ DCC-FLUSH BROMS: Ger nätverksbufferten 0.5s andrum att flusha sista kvittot!
        try:
            import time
            time.sleep(0.5)
        except:
            pass

        # 📊 DYNAMISK REAL_TIME HASTIGHETSRÄKNARE: (0ms exekvering utan dolda trådkrockar!)
        acute_duration = time.time() - (start_time if 'start_time' in locals() else time.time())
        if acute_duration <= 0:
            acute_duration = 0.1
            
        acute_bytes = bytes_sent if 'bytes_sent' in locals() else 0
        final_calc_speed = int(acute_bytes / acute_duration)

        # 1. 🧼 SANERA TRANSFERS OCH SLOTTAR DIREKT (Dödar cache-spöket omedelbart!)
        try:
            with queue_lock if 'queue_lock' in globals() else threading.Lock():
                config.active_transfers = [tx for tx in config.active_transfers if tx['user'].lower() != user.lower()]
                oserve = sys.modules.get('oserve')
                if oserve: oserve.active_downloads = len(config.active_transfers)
        except Exception as trans_clean_err:
            print(f"[DCC CLEANUP ERROR] Kunde inte rensa active_transfers in RAM: {trans_clean_err}")


        # 2. TRYGGA KANALMEDDELANDET I ABSOLUT FÖRSTA HAND (Säkrar din lyxiga färgblocksreklam live!)
        try:
            import announce as announce_mod
            announce_mod.send_transfer_complete(channel, user, file_name, file_size, start_time, final_calc_speed)
        except Exception as ann_chan_err:
            print(f"[ANNOUNCE KANAL ERROR] Kunde inte trycka ut färgblocksreklam till mIRC: {ann_chan_err}")

        # 3. STÄNG NÄTVERKSSOCKETEN TRYGGT OCH SÄKERT
        try: conn.close()
        except: pass
        try: dcc_sock.close()
        except: pass
        # 4. 🛡️ INTELLIGENT RAR-CACHE & SÄNDNINGSLÅS (Nu helt fri från dolda spökkrockar!)
        try:
            file_still_needed = False
            safe_path = str(file_path)
            
            if "tmp_zips" in safe_path and ".zip" not in safe_path:
                with queue_lock if 'queue_lock' in globals() else threading.Lock():
                    # A. Kolla om filen ligger kvar i KÖN för någon ANNAN användare i dcc_queue.txt
                    for q_user, q_files in getattr(config, 'dcc_queue', {}).items():
                        if q_user.lower() != user.lower():
                            for q_obj in q_files:
                                if isinstance(q_obj, dict) and (q_obj.get('file') == file_name or q_obj.get('path') == file_path):
                                    file_still_needed = True
                                    break
                    
                    # B. Kolla om filen fortfarande skickas AKTIVT till någon annan i en annan slot!
                    active_matches = 0
                    for tx in getattr(config, 'active_transfers', []):
                        if tx.get('file') == file_name:
                            active_matches += 1
                    
                    if active_matches > 0:
                        file_still_needed = True

                # Om ingen annan användare eller aktiv slot behöver filen längre – RADERA FRÅN SSD!
                if not file_still_needed:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        print(f"[DCC CLEANUP] Raderade den temporära mappen helt säkert från SSD: {file_name}")
        except Exception as file_rm_err:
            print(f"[DCC CLEANUP ERROR] Kunde inte köra disksaneringen: {file_rm_err}")
        
        # 5. SÄKRAD KÖ-RENSNING OCH AUTOQ POPPING UR TEXTFILEN
        try:
            with queue_lock if 'queue_lock' in globals() else threading.Lock():
                u_key = user.lower()
                if u_key in config.dcc_queue and len(config.dcc_queue[u_key]) > 0:
                    config.dcc_queue[u_key].pop(0)
                    
            import db
            db.save_dcc_queue()
            print(f"[DCC CLEANUP] Raden för {user} har poppats från dcc_queue.txt efter avslutad sändning.")
        except Exception as pop_err:
            print(f"[DCC CLEANUP ERROR] Kunde inte poppa raden ur textfilen: {pop_err}")

        # 6. LÅS UPP RAM-MINNET OCH UTESLUT DUBBELTRÅDAR
        config.rar_inprogress = False
        if hasattr(config, 'user_processing_lock'):
            config.user_processing_lock.discard(user.lower())

        # 7. TRIGGER-VÄCKARE (Väcker kön automatiskt efter 3 sekunder på ett helt trådsäkrat sätt!)
        def delayed_queue_trigger_fallback():
            time.sleep(3)
            check_queue_and_send(irc_sock, user)
        threading.Thread(target=delayed_queue_trigger_fallback, daemon=True).start()
