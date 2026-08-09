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
        if isinstance(next_file, dict):
            target_chan = next_file.get('channel', config.CHANNEL.split(',')[0])
        else:
            target_chan = config.CHANNEL.split(',')[0]
        
        # --- SKOTTSÄKER OCH SKIFTLÄGES-OBEROENDE UNIVERSAL-SKANNING ---
        user_is_actively_in_channel = False
        if hasattr(config, 'channel_users'):
            for chan_name, users_set in config.channel_users.items():
                lowered_channel_users = [u.lower() for u in users_set]
                if user_key in lowered_channel_users:
                    user_is_actively_in_channel = True
                    break
            
        if user_is_actively_in_channel is True:
            # ---------------------------------------------------------------------
            # HÄR ÄR DEN LINJÄRA MAPP-PACKAREN: Körs en och en inuti sändnings-slussen!
            # ---------------------------------------------------------------------
            if isinstance(next_file, dict) and next_file.get('is_unpacked_rar_folder') is True:
                if getattr(config, 'rar_inprogress', False):
                    print(f"[RAR-HOLD] {completed_user} väntar i kön eftersom en annan packning pågår live...")
                    return

                config.rar_inprogress = True

                with queue_lock:
                    if user_key in config.dcc_queue and config.dcc_queue[user_key]:
                        config.dcc_queue[user_key].pop(0)
                        db.save_dcc_queue()

                def inline_rar_packer(sock):
                    import announce
                    true_source_dir = next_file['path']
                    
                    # RÄTTAD: Vi hämtar det färdiga namnet (med artist) direkt ur kön!
                    raw_filename = next_file['file']
                    
                    # REGEX-TVÄTT: Rensar bort eventuella dubbla .rar-ändelser live på 0ms!
                    clean_name = re.sub(r'(?:\.rar)+$', '', raw_filename, flags=re.IGNORECASE)
                    rar_filename = f"{clean_name}.rar"
                    
                    target_rar_path = os.path.normpath(os.path.join(config.TMP_ZIP_DIR, rar_filename))
                    
                    if not os.path.exists(config.TMP_ZIP_DIR):
                        os.makedirs(config.TMP_ZIP_DIR, exist_ok=True)
                        
                    announce.send_debug(f"Packing folder for {completed_user}: {config.C_BOLD}{rar_filename}{config.C_RESET} into RAR archive...", category="INFO")
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
                        
                        config.active_transfers.append({"user": completed_user, "file": rar_filename, "bytes_sent": 0, "next_file_obj": rar_filename})
                        if oserve: oserve.active_downloads = len(config.active_transfers)
                        
                        announce.send_dcc_sending_notice(completed_user, rar_filename)
                        announce.send_debug(f"RAR pack successfully completed! Starting DCC transfer for {completed_user}.", category="JOIN")
                        
                        start_dcc_send(sock, completed_user, target_rar_path, rar_filename, target_chan, next_file)
                    else:
                        config.rar_inprogress = False
                        error_msg = process.stderr.strip() if process.stderr else "Unknown RAR engine issue"
                        print(f"[LINJÄR RAR ERROR] {error_msg}")
                        announce.send_debug(f"Pack FAILED in queue slot for {completed_user}: {error_msg}", category="PART")
                        check_queue_and_send(sock, completed_user)
                        
                threading.Thread(target=inline_rar_packer, args=(irc_sock,), daemon=True).start()
                return
            # ---------------------------------------------------------------------
            # Vanlig fildelning (om det var en färdig ljudfil)
            with queue_lock:
                if user_key in config.dcc_queue and config.dcc_queue[user_key]:
                    config.dcc_queue[user_key].pop(0)
                    db.save_dcc_queue()
            
            f_name = next_file['file'] if isinstance(next_file, dict) else os.path.basename(str(next_file))
            f_path = next_file['path'] if isinstance(next_file, dict) else str(next_file)
            
            print(f"[DCC QUEUE] Verified live in RAM for {target_chan}! Next file for {completed_user}: {f_name}")
            config.active_transfers.append({"user": completed_user, "file": f_name, "bytes_sent": 0, "next_file_obj": f_name})
            if oserve: oserve.active_downloads = len(config.active_transfers)
            
            announce.send_dcc_sending_notice(completed_user, f_name)
            threading.Thread(target=start_dcc_send, args=(irc_sock, completed_user, f_path, f_name, target_chan, next_file), daemon=True).start()
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
                    
                g_next = user_files
                real_username = g_next.get('user_raw', waiting_user) if isinstance(g_next, dict) else waiting_user
                w_key = real_username.lower()
                
                g_chan = g_next.get('channel', config.CHANNEL.split(',')) if isinstance(g_next, dict) else config.CHANNEL.split(',')
                g_name = g_next['file'] if isinstance(g_next, dict) else os.path.basename(str(g_next))
                g_path = g_next['path'] if isinstance(g_next, dict) else str(g_next)
                
                user_is_globally_active = False
                n_chan = g_chan.lower() if isinstance(g_chan, str) else g_chan.lower()
                if hasattr(config, 'channel_users') and n_chan in config.channel_users:
                    lowered_glob_users = [u.lower() for u in config.channel_users[n_chan]]
                    if w_key in lowered_glob_users:
                        user_is_globally_active = True
                        
                if user_is_globally_active is True:
                    if isinstance(g_next, dict) and g_next.get('is_unpacked_rar_folder') is True:
                        threading.Thread(target=check_queue_and_send, args=(irc_sock, real_username), daemon=True).start()
                        break
                        
                    user_files.pop(0)
                    db.save_dcc_queue()
                    
                    print(f"[DCC QUEUE] New user {real_username} verified live in RAM for {g_chan}. Got slot.")
                    config.active_transfers.append({"user": real_username, "file": g_name, "bytes_sent": 0, "next_file_obj": g_name})
                    if oserve: oserve.active_downloads = len(config.active_transfers)
                    
                    announce.send_dcc_sending_notice(real_username, g_name)
                    threading.Thread(target=start_dcc_send, args=(irc_sock, real_username, g_path, g_name, g_chan, g_next), daemon=True).start()
                    break

def handle_download_request(irc_sock, user, requested_file, target_chan):
    """Triggas när någon begär en fil eller en hel mapp via !rar (Helt kraschsäkrad mot .mp3/.flac list-fel)"""
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
            win_path = re.sub(r'\s*\[[^\]]+\]$', '', raw_win_path).strip()
            
            clean_win_path = win_path.replace("\\", "/").replace("D:/", "").replace("d:/", "")
            if clean_win_path.upper().startswith("MUSIC/"):
                clean_win_path = clean_win_path[6:]
                
            linux_sub_path = clean_win_path.strip("/")
            true_source_dir = os.path.normpath(os.path.join(config.FILE_DIRECTORY, linux_sub_path))
            
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
                # MASTER-SYNKERAD ARTIST-DETEKTOR (Spikas direkt vid begäran!)
                # ---------------------------------------------------------------------
                full_sub_path = os.path.relpath(true_source_dir, config.FILE_DIRECTORY)
                path_parts = [p for p in full_sub_path.replace("\\", "/").split("/") if p]
                
                artist_name = "Unknown_Artist"
                if len(path_parts) > 0:
                    raw_artist = path_parts[0]
                    artist_name = re.sub(r'[^\w\-_\. ]', '', raw_artist).replace(" ", "_")
                
                folder_name = os.path.basename(true_source_dir.rstrip("/"))
                clean_folder_name = re.sub(r'[^\w\-_\. ]', '', folder_name).replace(" ", "_")
                
                # Vi bygger det fullständiga, scen-verifierade namnet på en mikrosekund!
                master_rar_filename = f"{artist_name}_-_{clean_folder_name}.rar"

                config.dcc_queue[user_key].append({
                    "file": master_rar_filename, # SPARAD: Nu ligger det korrekta namnet spikat i dcc_queue.txt!
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
                    raise e
                for tx in config.active_transfers:
                    if tx['user'].lower() == user.lower():
                        tx['bytes_sent'] += len(chunk)
                oserve = sys.modules.get('oserve')
                if oserve: oserve.total_sent_bytes += len(chunk)
                
        print(f"[DCC-SUCCESS] Filen skickades felfritt till {user}!")
        try: time.sleep(1.5)
        except: pass

        import db
        import stats_mgr
        calc_speed = 0
        total_duration = time.time() - start_time
        disktid = total_duration - 1.5
        disktid = max(1.0, disktid)
        
        # --- FIXA FILSTORLEK OCH BERÄKNA HASTIGHET (Typ-säkrad) ---
        clean_file_size = 0
        try:
            if isinstance(file_size, list) and len(file_size) > 0:
                file_size = file_size[0]
            clean_file_size = int(float(str(file_size).strip()))
        except:
            clean_file_size = 0

        if disktid > 0 and clean_file_size > 0:
            calc_speed = int(clean_file_size / disktid)
            if calc_speed > db.get_speed_record():
                db.save_speed_record(calc_speed)
                human_speed = stats_mgr.format_speed(calc_speed)
                print(f"[SPEED RECORD!] Nytt hastighetsrekord satt: {human_speed}")

        # --- ANROP TILL DB: Låt db.py sköta all uppdatering spikrakt och kraschsäkert ---
        try:
            db.update_stats_on_complete(clean_file_size)
            current_stats = db.load_advanced_stats()
            total_files_display = current_stats[0] if isinstance(current_stats, list) else current_stats
            print(f"[DB COUNTER] Statistik uppdaterad live på disken! (Ny total: {total_files_display} filer)")
        except Exception as stats_err:
            print(f"[DB ERROR] Kunde inte räkna upp fildelningsstatistiken via db-modulen: {stats_err}")


        try: conn.close()
        except: pass
        announce.send_transfer_complete(channel, user, file_name, file_size, start_time, calc_speed)

    except socket.timeout:
        oserve = sys.modules.get('oserve')
        if oserve: oserve.send_fails_count += 1
    except Exception as e:
        oserve = sys.modules.get('oserve')
        if oserve: oserve.send_fails_count += 1
    finally:
        try: dcc_sock.close()
        except: pass
        
        config.rar_inprogress = False
        
        # NYTT: Vi låser upp användarens nick ur RAM-minnet i samma millisekund som sändningen avslutas!
        if hasattr(config, 'user_processing_lock'):
            config.user_processing_lock.discard(user.lower())

        
        config.rar_inprogress = False
        
        # ---------------------------------------------------------------------
        # INTELLIGENT RAR-CACHE & SÄNDNINGSLÅS (Totalsäkrad för 3 slots parallellt!)
        # Skyddar filen från att raderas om den fortfarande skickas till någon annan!
        # ---------------------------------------------------------------------
        if isinstance(next_file, dict) and next_file.get('is_temporary_zip') is True:
            true_rar_path = next_file.get('path', '')
            current_file_name = next_file.get('file', '')
            
            if true_rar_path and ("data" in true_rar_path or "tmp" in true_rar_path) and os.path.exists(true_rar_path):
                if os.path.isfile(true_rar_path):
                    
                    file_still_needed = False
                    with queue_lock:
                        # 1. Kolla om filen ligger kvar i KÖN för någon användare
                        for q_user, q_files in config.dcc_queue.items():
                            for q_obj in q_files:
                                if isinstance(q_obj, dict) and (q_obj.get('file') == current_file_name or q_obj.get('path') == true_rar_path):
                                    file_still_needed = True
                                    break
                        
                        # 2. Kolla om filen fortfarande skickas AKTIVT till någon annan i en annan slot!
                        active_matches = 0
                        for tx in config.active_transfers:
                            if tx.get('file') == current_file_name:
                                active_matches += 1
                        
                        # Om det finns fler än 1 matchning betyder det att en annan tråd strömmar filen just nu!
                        if active_matches > 1:
                            file_still_needed = True
                    
                    try:
                        time.sleep(0.5)
                        if file_still_needed:
                            # CACHE- & SÄNDNINGSHIT: Vi bevarar filen i absolut säkerhet på disken!
                            print(f"[RAR-CACHE] Bevarar {current_file_name} på disken. Filen skickas eller köas parallellt för en annan slot.")
                        else:
                            # Absolut ingen annan skickar eller köar filen, spola rent disken permanent!
                            os.remove(true_rar_path)
                            print(f"[ZIP CLEANUP] Raderade temporärt album-arkiv helt säkert från disken: {current_file_name}")
                    except Exception as clean_err:
                        print(f"[ZIP CLEANUP ERROR] Kunde inte hantera {true_rar_path}: {clean_err}")
        # ---------------------------------------------------------------------

        
        with queue_lock:
            config.active_transfers = [tx for tx in config.active_transfers if tx['user'].lower() != user.lower()]
            oserve = sys.modules.get('oserve')
            if oserve: oserve.active_downloads = len(config.active_transfers)
        
        # RÄTTAD: Startar nästa sändning efter 10 sekunders andningspaus helt kraschsäkert!
        def delayed_queue_trigger():
            time.sleep(5)
            check_queue_and_send(irc_sock, user)
        threading.Thread(target=delayed_queue_trigger, daemon=True).start()

