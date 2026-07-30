# dcc.py - Dedikerad modul för OmenServe DCC-överföringar (Del 1)
import socket
import threading
import time
import os
import sys

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
    """Kollar köer i realtid med bevarad disksynk - Tinar upp och skickar direkt vid JOIN!"""
    user_key = completed_user.lower()
    oserve = sys.modules.get('oserve')
    import db
    import config
    
    with queue_lock:
        # --- AUTOMATISK RENSNING AV GAMLA FRYSTA KÖER (Äldre än 5 min / 300s) ---
        current_time = time.time()
        for f_user, freeze_timestamp in list(config.frozen_queues.items()):
            if (current_time - freeze_timestamp) > 300.0:
                if f_user in config.dcc_queue:
                    del config.dcc_queue[f_user]
                    db.save_dcc_queue() # Spika borttagningen till hårddisken direkt!
                if f_user in config.frozen_queues:
                    del config.frozen_queues[f_user]
                print(f"[DCC QUEUE CLEAN] {f_user} var borta i mer än 5 minuter. Kö raderad permanent från disken.")

        # Systemfallback för kön
        if user_key == "system_next_trigger_fallback":
            user_key = ""

        # 1. Har samma användare fler filer som väntar i sin personliga kö? (Om de inte är frysta!)
        if user_key and user_key in config.dcc_queue and config.dcc_queue[user_key]:
            if user_key in config.frozen_queues:
                # Om användaren fortfarande är flaggad som fryst, hoppa över den för tillfället
                pass
            else:
                # RÄTTAD TILLBAKA TILL ORIGINAL: Tjuvkika enbart med [0] så att filen inte raderas i förtid!
                next_file = config.dcc_queue[user_key][0]
                target_chan = next_file.get('channel', config.CHANNEL.split(',')[0])
                
                # --- STARTA AKTIV KANAL-KONTROLL ---
                config.whois_status[user_key] = None
                try:
                    irc_sock.send(f"WHO {target_chan}\r\n".encode())
                except:
                    return
                    
                # Vänta i max 2.5 sekunder på att servern ska skicka hela kanallistan
                start_wait = time.time()
                while config.whois_status.get(user_key) is None and (time.time() - start_wait) < 2.5:
                    time.sleep(0.1)
                    
                # BESLUTSFATTANDE: Befinner sig användaren fysiskt inuti kanalen?
                if config.whois_status.get(user_key) is True:
                    # Användaren är verifierad live! FÖRST NU plockar vi ur filen och sparar till disken
                    config.dcc_queue[user_key].pop(0)
                    db.save_dcc_queue()
                    
                    print(f"[DCC QUEUE] Verified in channel {target_chan}! Next file for {completed_user}: {next_file['file']}")
                    config.active_transfers.append({"user": completed_user, "file": next_file['file'], "bytes_sent": 0})
                    if oserve:
                        oserve.active_downloads = len(config.active_transfers)
                    
                    announce.send_dcc_sending_notice(completed_user, next_file['file'])
                    threading.Thread(target=start_dcc_send, args=(irc_sock, completed_user, next_file['path'], next_file['file'], next_file['channel']), daemon=True).start()
                    return
                else:
                    # SMART LOKAL TIMER-SLUSS: Sätt tidsstämpel och starta en dedikerad 5-minutersklocka!
                    config.frozen_queues[user_key] = time.time()
                    print(f"[DCC REACTIVE FREEZE] {completed_user} har lämnat {target_chan}! Startar en dedikerad 5-minuters timer...")
                    
                    # (Den gamla överflödiga announce.send_debug-raden är nu helt raderad härifrån!)
                    
                    def user_queue_timer(sock, target_user, original_chan):
                        # Sov i exakt 5 minuter (300 sekunder) i en helt egen tråd
                        time.sleep(300)
                        t_key = target_user.lower()
                        
                        # Kolla om användaren fortfarande ligger kvar i frysboxen
                        if hasattr(config, 'frozen_queues') and t_key in config.frozen_queues:
                            with queue_lock:
                                if t_key in config.dcc_queue:
                                    del config.dcc_queue[t_key]
                                    import db
                                    db.save_dcc_queue() # Spika städningen till hårddisken direkt!
                                del config.frozen_queues[t_key]
                            print(f"[DCC TIMER EXPIRED] {target_user} kom inte tillbaka till {original_chan} inom 5 minuter. Kö raderad.")
                            
                            # BEHÅLLS: Loggar till #flac-debug när tiden faktiskt har gått ut och kön raderas!
                            announce.send_debug(f"Timer expired for {target_user}. Queue has been erased from disk layout.")
                        else:
                            print(f"[DCC TIMER CANCELLED] Timern för {target_user} avbröts eftersom användaren klev in i kanalen i tid.")
                            
                    # Startar den fristående timern i bakgrunden för just denna användare
                    threading.Thread(target=user_queue_timer, args=(irc_sock, completed_user, target_chan), daemon=True).start()
                    
                    # Gå vidare direkt och släpp in nästa person i kön under tiden!
                    user_key = ""


        # 2. Om samma användare inte har fler filer, kolla nästa person i globala kön
        if oserve:
            oserve.active_downloads = len(config.active_transfers)
            
        if len(config.active_transfers) < config.MAX_DCC_SLOTS and config.dcc_queue:
            for waiting_user, user_files in list(config.dcc_queue.items()):
                w_key = waiting_user.lower()
                
                if w_key in config.frozen_queues:
                    continue
                    
                if user_files:
                    next_req = user_files[0] # Tjuvkika enbart med [0]
                    real_username = next_req.get('user_raw', waiting_user)
                    w_key = real_username.lower()
                    next_chan = next_req.get('channel', config.CHANNEL.split(',')[0])
                    
                    config.whois_status[w_key] = None
                    try: irc_sock.send(f"WHO {next_chan}\r\n".encode())
                    except: continue
                    
                    start_wait = time.time()
                    while config.whois_status.get(w_key) is None and (time.time() - start_wait) < 2.5:
                        time.sleep(0.1)
                        
                    if config.whois_status.get(w_key) is True:
                        user_files.pop(0)
                        db.save_dcc_queue()
                        
                        print(f"[DCC QUEUE] New user {real_username} verified in channel {next_chan}. Got slot.")
                        config.active_transfers.append({"user": real_username, "file": next_req['file'], "bytes_sent": 0})
                        if oserve:
                            oserve.active_downloads = len(config.active_transfers)
                        
                        announce.send_dcc_sending_notice(real_username, next_req['file'])
                        threading.Thread(target=start_dcc_send, args=(irc_sock, real_username, next_req['path'], next_req['file'], next_req['channel']), daemon=True).start()
                        break

def handle_download_request(irc_sock, user, requested_file, channel):
    """Triggas när någon begär en fil. Garanterar stenhårt MAX 1 SLOT PER ANVÄNDARE via trådlåset!"""
    oserve = sys.modules.get('oserve')
    try:
        user_key = user.lower()
        if oserve:
            oserve.active_downloads = len(config.active_transfers)
        print(f"[DCC] {user} requested: {requested_file}")
        
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
                config.active_transfers.append({"user": user, "file": file_name, "bytes_sent": 0})
                if oserve:
                    oserve.active_downloads = len(config.active_transfers)
                announce.send_dcc_sending_notice(user, file_name)
                threading.Thread(target=start_dcc_send, args=(irc_sock, user, full_path, file_name, channel), daemon=True).start()
                return
            else:
                if user_key not in config.dcc_queue:
                    config.dcc_queue[user_key] = []
                config.dcc_queue[user_key].append({"file": file_name, "path": full_path, "channel": channel, "user_raw": user})
                user_pos = len(config.dcc_queue[user_key])
                print(f"[DCC QUEUE] Slots occupied or user transferring. Added {file_name} to {user}'s queue at #{user_pos}.")
                announce.send_dcc_queue_notice(user, file_name, user_pos)
                return
    except Exception as e:
        print(f"[DCC CRITICAL ERROR] Fel i filbegäran: {e}")
        oserve = sys.modules.get('oserve')
        if oserve:
            oserve.send_fails_count += 1

def start_dcc_send(irc_sock, user, file_path, file_name, channel):
    """Sköter nätverksportarna, dolda CTCP och strömmar byten över nätverket till användaren med 0ms lagg"""
    global active_transfers
    file_size = os.path.getsize(file_path)
    ip_long = get_public_ip_long()
    start_time = time.time()
    
    if ip_long == 0:
        try:
            irc_sock.send(f"NOTICE {user} :{config.C_BOLD}Error:{config.C_RESET} Server network issue, try again later.\r\n".encode())
        except:
            pass
        with queue_lock:
            config.active_transfers = [tx for tx in config.active_transfers if tx['user'].lower() != user.lower()]
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
        try:
            irc_sock.send(f"NOTICE {user} :{config.C_BOLD}Error:{config.C_RESET} No available DCC ports.\r\n".encode())
        except:
            pass
        with queue_lock:
            config.active_transfers = [tx for tx in config.active_transfers if tx['user'].lower() != user.lower()]
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
         # NYTT: Starta tidtagaruret HÄR i stället så vi mäter exakt som mIRC!
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
        
        # Vi flyttar upp vilan hit så att mIRC hinner flusha Windows-disken rent!
        try: time.sleep(1.5)
        except: pass

        # ---------------------------------------------------------------------
        # ULTRA-EXAKT LIVE REKORDMÄTARE (Mäter disktid synkat efter trådvila!)
        # ---------------------------------------------------------------------
        import db
        import stats_mgr
        
        # Sätt ett startvärde för hastigheten så att funktionen aldrig kan krascha!
        calc_speed = 0
        
        # Vi tar den totala tiden och drar av de 1.5 sekunderna vi nyss vilade
        total_duration = time.time() - start_time
        disktid = total_duration - 1.5
        
        # Säkerhetsmarginal: Sätt lägsta disktid till mIRC-standard (minst 1s vid snabba bursts)
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
                # Vi plussar på siffrorna på exakt rätt index, och sparar som strängar
                stats[0] = str(int(stats[0]) + 1)          # Totalt skickade filer (+1)
                stats[1] = str(int(stats[1]) + file_size)  # Totalt skickade bytes (+file_size)
                stats[4] = str(int(stats[4]) + 1)          # Dagens skickade filer (+1)
                stats[5] = str(int(stats[5]) + file_size)  # Dagens skickade bytes (+file_size)
                
                # Index 2, 3 och 6 (datumet) lämnas helt orörda och intakta!
                db.save_advanced_stats(stats)              # Spika fast ändringarna live till stats.txt!
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
        with queue_lock:
            config.active_transfers = [tx for tx in config.active_transfers if tx['user'].lower() != user.lower()]
            oserve = sys.modules.get('oserve')
            if oserve: oserve.active_downloads = len(config.active_transfers)
        
        def delayed_queue_trigger():
            time.sleep(5)
            check_queue_and_send(irc_sock, user)
        threading.Thread(target=delayed_queue_trigger, daemon=True).start()

