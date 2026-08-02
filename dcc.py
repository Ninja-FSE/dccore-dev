# =====================================================================
# DCC.PY - DEDIKERAD MODUL FÖR OMENSERVE DCC-ÖVERFÖRINGAR (DEL 1 AV 3)
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

# Skapa trådlåset direkt i toppen av modulen så att kön räknas upp spikrakt
queue_lock = threading.Lock()

def is_safe_path(base_dir, path, follow_symlinks=True):
    """Säkerhetsfilter: Förhindrar Directory Traversal-attacker"""
    if follow_symlinks:
        matchpath = os.path.realpath(path)
    else:
        matchpath = os.path.abspath(path)
    return matchpath.startswith(os.path.realpath(base_dir))

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
    user_key = completed_user.lower()
    oserve = sys.modules.get('oserve')
    import db
    import config
    import announce
    import time
    import threading
    import re
    
    # 1. AUTOMATISK RENSNING AV GAMLA FRYSTA KÖER (Äldre än 5 min)
    with queue_lock:
        current_time = time.time()
        for f_user, freeze_timestamp in list(config.frozen_queues.items()):
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
                print(f"[DCC QUEUE CLEAN] {f_user} rensad permanent pga timeout.")

    # Systemfallback för kön
    if user_key == "system_next_trigger_fallback":
        user_key = ""

    # Tjuvkika på det absolut första objektet i kön UTAN att låsa hela tråden
    next_file = None
    with queue_lock:
        if user_key and user_key in config.dcc_queue and config.dcc_queue[user_key]:
            if user_key not in config.frozen_queues:
                next_file = config.dcc_queue[user_key][0]
    if next_file:
        target_chan = next_file.get('channel', config.CHANNEL.split(',')[0])
        
        # --- SKOTTSÄKER OCH SKIFTLÄGES-OBEROENDE RAM-SKANNING ---
        user_is_actively_in_channel = False
        c_chan = target_chan.lower()
        if hasattr(config, 'channel_users') and c_chan in config.channel_users:
            # RÄTTAD: Vi gör om alla nicks i kanallistan till gemener live för att undvika falska frysningar!
            lowered_channel_users = [u.lower() for u in config.channel_users[c_chan]]
            if user_key in lowered_channel_users:
                user_is_actively_in_channel = True
            
        if user_is_actively_in_channel is True:
            # ---------------------------------------------------------------------
            # HÄR ÄR DEN LINJÄRA MAPP-PACKAREN: Körs en och en inuti sändnings-slussen!
            # ---------------------------------------------------------------------
            if next_file.get('is_unpacked_rar_folder') is True:
                with queue_lock:
                    if user_key in config.dcc_queue and config.dcc_queue[user_key]:
                        config.dcc_queue[user_key].pop(0)
                        db.save_dcc_queue()

                # RÄTTAD: Vi skickar med socketen som ett argument (sock) in i underfunktionen!
                def inline_rar_packer(sock):
                    import subprocess
                    import announce
                    
                    true_source_dir = next_file['path']
                    folder_name = next_file['file']
                    clean_folder_name = re.sub(r'[^\w\-_\. ]', '', folder_name).replace(" ", "_")
                    
                    rar_filename = f"{completed_user}_{clean_folder_name}.rar"
                    target_rar_path = os.path.normpath(os.path.join(config.TMP_ZIP_DIR, rar_filename))
                    
                    if not os.path.exists(config.TMP_ZIP_DIR):
                        os.makedirs(config.TMP_ZIP_DIR, exist_ok=True)
                        
                    announce.send_debug(f"Packing folder for {completed_user}: {config.C_BOLD}{folder_name}{config.C_RESET} into RAR archive...", category="INFO")
                    print(f"[LINJÄR RAR] Startar packning av: {true_source_dir} -> {target_rar_path}")
                    
                    base_dir = os.path.dirname(true_source_dir)
                    rel_dir = os.path.basename(true_source_dir)
                    
                    process = subprocess.run(
                        ["rar", "a", "-ep1", os.path.abspath(target_rar_path), rel_dir],
                        cwd=base_dir, capture_output=True, text=True, timeout=None
                    )
                    
                    if process.returncode == 0 and os.path.exists(target_rar_path):
                        print(f"[LINJÄR RAR] Komprimering lyckades. Väntar 2.0s på disksynk...")
                        time.sleep(2.0)
                        
                        final_size = os.path.getsize(target_rar_path)
                        print(f"[LINJÄR RAR] Arkiv helt spikat på disken: {final_size:,} bytes")
                        
                        next_file['path'] = target_rar_path
                        next_file['file'] = rar_filename
                        next_file['is_unpacked_rar_folder'] = False
                        
                        config.active_transfers.append({"user": completed_user, "file": rar_filename, "bytes_sent": 0, "next_file_obj": next_file})
                        if oserve: oserve.active_downloads = len(config.active_transfers)
                        
                        announce.send_dcc_sending_notice(completed_user, rar_filename)
                        announce.send_debug(f"RAR pack successfully completed! Starting DCC transfer for {completed_user}.", category="JOIN")
                        
                        # RÄTTAD: Här använder vi nu den lokala 'sock'-variabeln helt kraschsäkert!
                        start_dcc_send(sock, completed_user, target_rar_path, rar_filename, target_chan, next_file)
                    else:
                        error_msg = process.stderr.strip() if process.stderr else "Unknown RAR engine issue"
                        print(f"[LINJÄR RAR ERROR] {error_msg}")
                        announce.send_debug(f"Pack FAILED in queue slot for {completed_user}: {error_msg}", category="PART")
                        check_queue_and_send(sock, completed_user)
                        
                # RÄTTAD: Vi skickar med din 'irc_sock' som parameter när tråden sparkas igång!
                threading.Thread(target=inline_rar_packer, args=(irc_sock,), daemon=True).start()
                return

            # ---------------------------------------------------------------------
            with queue_lock:
                if user_key in config.dcc_queue and config.dcc_queue[user_key]:
                    config.dcc_queue[user_key].pop(0)
                    db.save_dcc_queue()
            
            print(f"[DCC QUEUE] Verified live in RAM for {target_chan}! Next file for {completed_user}: {next_file['file']}")
            config.active_transfers.append({"user": completed_user, "file": next_file['file'], "bytes_sent": 0, "next_file_obj": next_file})
            if oserve: oserve.active_downloads = len(config.active_transfers)
            
            announce.send_dcc_sending_notice(completed_user, next_file['file'])
            threading.Thread(target=start_dcc_send, args=(irc_sock, completed_user, next_file['path'], next_file['file'], next_file['channel'], next_file), daemon=True).start()
            return
        else:
            with queue_lock:
                config.frozen_queues[user_key] = time.time()
            print(f"[DCC REACTIVE FREEZE] {completed_user} har lämnat {target_chan} på riktigt! Startar timer...")
            announce.send_debug(f"DCC reactive freeze triggered for {completed_user} in {target_chan}. Initiating 5-minute cooldown timer.", category="QUIT")
            
            def user_queue_timer(sock, target_user, original_chan):
                time.sleep(300)
                t_key = target_user.lower()
                if hasattr(config, 'frozen_queues') and t_key in config.frozen_queues:
                    with queue_lock:
                        if t_key in config.dcc_queue:
                            for f_obj in config.dcc_queue[t_key]:
                                if isinstance(f_obj, dict) and f_obj.get('is_temporary_zip') is True and os.path.exists(f_obj['path']) and not f_obj.get('is_unpacked_rar_folder'):
                                    try: os.remove(f_obj['path'])
                                    except: pass
                            del config.dcc_queue[t_key]
                            import db
                            db.save_dcc_queue()
                        del config.frozen_queues[t_key]
                    announce.send_debug(f"Timer expired for {target_user} in {original_chan}. Personal queue has been erased.", category="PART")
                    
            threading.Thread(target=user_queue_timer, args=(irc_sock, completed_user, target_chan), daemon=True).start()
            user_key = ""

    # B) Global köhantering för nästa person i kön
    if oserve:
        oserve.active_downloads = len(config.active_transfers)
        
    if len(config.active_transfers) < config.MAX_DCC_SLOTS:
        with queue_lock:
            for waiting_user, user_files in list(config.dcc_queue.items()):
                w_key = waiting_user.lower()
                if w_key in config.frozen_queues or not user_files:
                    continue
                    
                next_req = user_files[0]
                real_username = next_req.get('user_raw', waiting_user)
                w_key = real_username.lower()
                next_chan = next_req.get('channel', config.CHANNEL.split(',')[0])
                
                user_is_globally_active = False
                n_chan = next_chan.lower()
                if hasattr(config, 'channel_users') and n_chan in config.channel_users:
                    lowered_glob_users = [u.lower() for u in config.channel_users[n_chan]]
                    if w_key in lowered_glob_users:
                        user_is_globally_active = True
                        
                if user_is_globally_active is True:
                    if next_req.get('is_unpacked_rar_folder') is True:
                        threading.Thread(target=check_queue_and_send, args=(irc_sock, real_username), daemon=True).start()
                        break
                        
                    user_files.pop(0)
                    db.save_dcc_queue()
                    
                    print(f"[DCC QUEUE] New user {real_username} verified live in RAM for {next_chan}. Got slot.")
                    config.active_transfers.append({"user": real_username, "file": next_req['file'], "bytes_sent": 0, "next_file_obj": next_req})
                    if oserve: oserve.active_downloads = len(config.active_transfers)
                    
                    announce.send_dcc_sending_notice(real_username, next_req['file'])
                    threading.Thread(target=start_dcc_send, args=(irc_sock, real_username, next_req['path'], next_req['file'], next_req['channel'], next_req), daemon=True).start()
                    break


def handle_download_request(irc_sock, user, requested_file, target_chan):
    """Triggas när någon begär en fil eller en hel mapp via !rar"""
    oserve = sys.modules.get('oserve')
    try:
        user_key = user.lower()
        if oserve:
            oserve.active_downloads = len(config.active_transfers)
        print(f"[DCC] {user} requested: {requested_file}")
        
        # ---------------------------------------------------------------------
        # ASYNKRON MAPP-PACKNING (!rar Sluss med stenhård ROOT- och NFS-säkring)
        # ---------------------------------------------------------------------
        if requested_file.lower().startswith("!rar "):
            import subprocess
            import announce as announce_mod  # RÄTTAD: Använder unikt alias för att undvika krasch!
            
            raw_win_path = requested_file[5:].strip()
            win_path = re.sub(r'\s*\[[^\]]+\]$', '', raw_win_path).strip()
            
            clean_win_path = win_path.replace("\\", "/").replace("D:/", "").replace("d:/", "")
            if clean_win_path.upper().startswith("MUSIC/"):
                clean_win_path = clean_win_path[6:]
                
            linux_sub_path = clean_win_path.strip("/")
            true_source_dir = os.path.normpath(os.path.join(config.FILE_DIRECTORY, linux_sub_path))
            
            # 🛡️ STENHÅRD ROOT-SPÄRR
            if "/" not in linux_sub_path:
                print(f"[SECURITY] Blockade root-mappspackning från {user}: {linux_sub_path}")
                
                # Anropar din fullständigt färgmatchade ram från announce.py
                announce_mod.send_pack_error_notice(irc_sock, user)
                
                # Logg till din dolda #flac-debug
                announce_mod.send_debug(
                    f"Pack denied for {user}: {config.C_BOLD}{linux_sub_path}{config.C_RESET} is an artist root folder. Please select a specific music sub-folder.", 
                    category="PART"
                )
                return
            
            if not os.path.exists(true_source_dir) or not os.path.isdir(true_source_dir):
                announce_mod.send_debug(f"Pack error: Directory not found on disk storage for {user}.", category="PART")
                return

            # ---------------------------------------------------------------------
            # NYTT: LINJÄR KÖ-SÄKRING (Sparar mappen i kön i stället för direkt-packning!)
            # ---------------------------------------------------------------------
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

                # Vi sparar undan din sökväg och sätter flaggor för det linjära skiftet!
                folder_name = os.path.basename(true_source_dir.rstrip("/"))
                config.dcc_queue[user_key].append({
                    "file": folder_name,
                    "path": true_source_dir,
                    "channel": target_chan,
                    "user_raw": user,
                    "is_unpacked_rar_folder": True,
                    "is_temporary_zip": True
                })
                import db
                db.save_dcc_queue() # Spika direkt till dcc_queue.txt!
                
                user_pos = len(config.dcc_queue[user_key])
                user_already_transferring = any(tx['user'].lower() == user_key for tx in config.active_transfers)
                
                print(f"[RAR QUEUE] Added virtuell mapp {folder_name} for {user} at position #{user_pos}.")
                announce_mod.send_dcc_queue_notice(user, folder_name, user_pos)
                
                # Om användaren inte har en aktiv sändning igång, väck kön direkt för packnings-start!
                if not user_already_transferring and len(config.active_transfers) < config.MAX_DCC_SLOTS and user_pos == 1:
                    threading.Thread(target=check_queue_and_send, args=(irc_sock, user), daemon=True).start()
            return

        # ---------------------------------------------------------------------
        if " ::INFO::" in requested_file:
            requested_file = requested_file.split(" ::INFO::", 1)[0].strip()

        requested_file = requested_file.lstrip("/")

        if requested_file.endswith(".zip") and config.LIST_BASE_NAME in requested_file:
            base_directory = os.path.abspath(config.LOCAL_LIST_DIR)
            full_path = os.path.join(base_directory, requested_file)
        else:
            base_directory = os.path.abspath(config.FILE_DIRECTORY)
            full_path = os.path.join(base_directory, requested_file)

        is_master_zip = requested_file.endswith(".zip") and config.LIST_BASE_NAME in requested_file
        if not is_master_zip and not os.path.exists(full_path):
            print(f"[DCC-LOOKUP] Söker efter filens mapprobrik i textlistan för: {requested_file}")
            latest_list_path = list_mod.find_latest_list()
            if latest_list_path and os.path.exists(latest_list_path):
                try:
                    with open(latest_list_path, "r", encoding="utf-8", errors="ignore") as lf:
                        lines = lf.readlines()
                    target_folder_rel = None
                    clean_req = requested_file.lower().strip()
                    for idx, line in enumerate(lines):
                        line_clean = line.strip()
                        if line_clean.startswith(f"!{config.NICKNAME} "):
                            current_file_in_list = line_clean.split(f"!{config.NICKNAME} ", 1)[1]
                            if "  ::INFO::" in current_file_in_list:
                                current_file_in_list = current_file_in_list.split("  ::INFO::", 1)[0]
                            if clean_req == current_file_in_list.lower().strip():
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
                            print(f"[DCC-LOOKUP] Garanterat hittad via rubrikspårning: {full_path}")
                except Exception as list_err:
                    print(f"[DCC-LOOKUP CRITICAL ERROR] Krasch under sökning: {list_err}")

            if not os.path.exists(full_path):
                print(f"[DCC-FALLBACK] Letar i undermappar efter: {requested_file}")
                for root, dirs, files in os.walk(base_directory):
                    if requested_file in files:
                        full_path = os.path.join(root, requested_file)
                        break

        if not is_safe_path(base_directory, full_path):
            print(f"[SECURITY WARNING] {user} directory traversal blocked!")
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
                print(f"[DCC QUEUE FULL] Global queue limit reached ({total_global_queued}/{config.MAX_GLOBAL_QUEUE}).")
                announce.send_dcc_error(user, "global_full")
                return

            if user_queued_count >= config.MAX_USER_QUEUE:
                print(f"[DCC USER QUEUE FULL] {user} reached personal queue limit.")
                announce.send_dcc_error(user, "user_full")
                return

            user_already_transferring = any(tx['user'].lower() == user_key for tx in config.active_transfers)
            user_has_queue = len(config.dcc_queue.get(user_key, [])) > 0

            if not user_already_transferring and len(config.active_transfers) < config.MAX_DCC_SLOTS and not user_has_queue:
                print(f"[DCC] Global slot and user slot available. Starting transfer for {user}.")
                next_file_fake = {"path": full_path, "file": file_name, "channel": target_chan, "is_temporary_zip": False}
                config.active_transfers.append({"user": user, "file": file_name, "bytes_sent": 0, "next_file_obj": next_file_fake})
                if oserve:
                    oserve.active_downloads = len(config.active_transfers)
                announce.send_dcc_sending_notice(user, file_name)
                threading.Thread(target=start_dcc_send, args=(irc_sock, user, full_path, file_name, target_chan, next_file_fake), daemon=True).start()
                return
            else:
                if user_key not in config.dcc_queue:
                    config.dcc_queue[user_key] = []
                config.dcc_queue[user_key].append({"file": file_name, "path": full_path, "channel": target_chan, "user_raw": user, "is_temporary_zip": False})
                user_pos = len(config.dcc_queue[user_key])
                print(f"[DCC QUEUE] Slots occupied or user transferring. Added {file_name} to {user}'s queue at #{user_pos}.")
                announce.send_dcc_queue_notice(user, file_name, user_pos)
                return
    except Exception as e:
        print(f"[DCC CRITICAL ERROR] Fel i filbegäran: {e}")
        oserve = sys.modules.get('oserve')
        if oserve: oserve.send_fails_count += 1

def start_dcc_send(irc_sock, user, file_path, file_name, channel, next_file):
    """Sköter nätverksportarna, dolda CTCP och strömmar byten över nätverket med bevarad tidtagning"""
    global active_transfers
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    ip_long = get_public_ip_long()
    start_time = time.time()
    
    if ip_long == 0 or file_size == 0:
        try: irc_sock.send(f"NOTICE {user} :{config.C_BOLD}Error:{config.C_RESET} Server network issue, try again later.\r\n".encode())
        except: pass
        with queue_lock:
            config.active_transfers = [tx for tx in config.active_transfers if tx['user'].lower() != user.lower()]
        if isinstance(next_file, dict) and next_file.get('is_temporary_zip') is True and os.path.exists(file_path):
            try: os.remove(file_path)
            except: pass
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
        print("[DCC ERROR] Inga lediga portar!")
        try: irc_sock.send(f"NOTICE {user} :{config.C_BOLD}Error:{config.C_RESET} No available DCC ports.\r\n".encode())
        except: pass
        with queue_lock:
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
        print(f"[DCC-CONNECT] {user} connected from {addr}!")
        start_time = time.time()       

        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(16384)
                if not chunk: break
                try:
                    conn.sendall(chunk)
                except socket.error as e:
                    print(f"[DCC NET ERROR] Socket-fel under sändning till {user}: {e}")
                    raise e
                for tx in config.active_transfers:
                    if tx['user'].lower() == user.lower():
                        tx['bytes_sent'] += len(chunk)
                oserve = sys.modules.get('oserve')
                if oserve: oserve.total_sent_bytes += len(chunk)
                
        print(f"[DCC-SUCCESS] Filen skickades felfritt till {user}!")
        
        # Vi behåller din klockrena vila så att mIRC hinner flusha Windows-disken rent!
        try: time.sleep(1.5)
        except: pass

        # ---------------------------------------------------------------------
        # ULTRA-EXAKT LIVE REKORDMÄTARE (Mäter disktid synkat efter trådvila!)
        # ---------------------------------------------------------------------
        import db
        import stats_mgr
        calc_speed = 0
        total_duration = time.time() - start_time
        disktid = total_duration - 1.5
        disktid = max(1.0, disktid)
        
        if disktid > 0:
            calc_speed = int(file_size / disktid)
            if calc_speed > db.get_speed_record():
                db.save_speed_record(calc_speed)
                human_speed = stats_mgr.format_speed(calc_speed)
                print(f"[SPEED RECORD!] Nytt hastighetsrekord satt: {human_speed}")
        # ---------------------------------------------------------------------
        # NYTT: UPPDATERA STATISTIKEN PÅ DISKEN (Skräddarsydd för din stats.txt!)
        # ---------------------------------------------------------------------
        try:
            stats = db.load_advanced_stats()
            if isinstance(stats, list) and len(stats) > 6:
                stats[0] = str(int(stats[0]) + 1)          
                stats[1] = str(int(stats[1]) + file_size)  
                stats[4] = str(int(stats[4]) + 1)          
                stats[5] = str(int(stats[5]) + file_size)  
                db.save_advanced_stats(stats)              
                print(f"[DB COUNTER] Statistik uppdaterad live på disken! (Ny total: {stats[0]} filer)")
        except Exception as stats_err:
            print(f"[DB ERROR] Kunde inte räkna upp fildelningsstatistiken: {stats_err}")
        # ---------------------------------------------------------------------

        try: conn.close()
        except: pass
        announce.send_transfer_complete(channel, user, file_name, file_size, start_time, calc_speed)

    except socket.timeout:
        print(f"[DCC TIMEOUT] {user} failed to connect within 30 seconds.")
        oserve = sys.modules.get('oserve')
        if oserve: oserve.send_fails_count += 1
    except Exception as e:
        print(f"[DCC SEND ERROR] Överföringen bröts för {user}: {e}")
        oserve = sys.modules.get('oserve')
        if oserve: oserve.send_fails_count += 1
    finally:
        try: dcc_sock.close()
        except: pass
        
        # ---------------------------------------------------------------------
        # HELAUTOMATISK RAR-STÄDNING (SÄKRAD: Läser från det uppdaterade objektet!)
        # Skyddar din NFS-mount genom att enbart tillåta radering av data/tmp-filer!
        # ---------------------------------------------------------------------
        if isinstance(next_file, dict) and next_file.get('is_temporary_zip') is True:
            # Vi drar ur den fysiska, färdiga rar-sökvägen ur objektet live
            true_rar_path = next_file.get('path', '')
            
            # STENHÅRT SÄKERHETSFILTER: Filen MÅSTE innehålla "data" eller "tmp" samt existera
            if true_rar_path and ("data" in true_rar_path or "tmp" in true_rar_path) and os.path.exists(true_rar_path):
                # Dubbelkolla att det är en FIL och inte din NFS-mapp innan os.remove anropas!
                if os.path.isfile(true_rar_path):
                    try:
                        time.sleep(0.5)
                        os.remove(true_rar_path)
                        print(f"[ZIP CLEANUP] Raderade temporärt album-arkiv helt säkert från disken: {next_file.get('file')}")
                    except Exception as clean_err:
                        print(f"[ZIP CLEANUP ERROR] Kunde inte radera {true_rar_path}: {clean_err}")
        # ---------------------------------------------------------------------
        
        with queue_lock:
            config.active_transfers = [tx for tx in config.active_transfers if tx['user'].lower() != user.lower()]
            oserve = sys.modules.get('oserve')
            if oserve: oserve.active_downloads = len(config.active_transfers)
        
        def delayed_queue_trigger():
            time.sleep(5)
            check_queue_and_send(irc_sock, user)
        threading.Thread(target=delayed_queue_trigger, daemon=True).start()

