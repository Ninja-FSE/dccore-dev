# =====================================================================
# IRC.PY - DEDIKERAD IRC-NÄTVERKSMODUL FÖR UNDERNET (DEL 1 AV 3)
# =====================================================================
import socket
import threading
import time
import re
import sys
import os
import traceback
import urllib.request 
import builtins

# Importera botens moduler
import config
import list
import dcc
import security

# Flagga för att hålla koll på om kanaler är joinade
bot_joined_channel = False

def send_ctcp_version_reply(irc_sock, target_user):
    """Svarar live i din #flac-debug-kanal i stället för privat för att helt bypassa Undernets botskydd!"""
    import sys
    version_str = getattr(config, 'SCRIPT_VERSION', 'DCCore v1.4.5-BETA')
    full_reply = f"PRIVMSG {config.DEBUG_CHANNEL} :[CTCP VERSION] Requested by {target_user} -> Bot Version: {version_str} by FLAC (OmenServe Architecture)\r\n"
    try:
        oserve = sys.modules.get('oserve')
        if oserve:
            oserve.queue_message(config.DEBUG_CHANNEL, full_reply, is_vip=True)
            print(f"[CTCP] Version-notis skickad säkert till {config.DEBUG_CHANNEL}.")
    except Exception as e:
        print(f"[CTCP ERROR] Misslyckades att slussa meddelande: {e}")

def irc_loop():
    """Hanterar anslutningen, PING/PONG och alla inkommande PRIVMSG från Undernet"""
    global bot_joined_channel
    import announce  # 🛡️ SÄKRAD: Importeras direkt här inuti funktionen så den ALLTID är definierad!
    oserve = sys.modules.get('oserve')
    
    # ---------------------------------------------------------------------
    # IP-KOLL via api.ipify.org (Körs EN GÅNG innan anslutning)
    # ---------------------------------------------------------------------
    print("[IP CHECK] Hämtar din publika via ipify API...")
    try:
        req = urllib.request.Request(
            "https://api.ipify.org", 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        config.MY_IP_OR_DOCK = urllib.request.urlopen(req, timeout=5.0).read().decode("utf-8").strip()
        print(f"[IP CHECK] IP-identifiering klar! DCC IP satt till: {config.MY_IP_OR_DOCK}")
    except Exception as e:
        print(f"[WARNING] Kunde inte nå ipify API ({e}). Fallback till 127.0.0.1")
        config.MY_IP_OR_DOCK = "127.0.0.1"
    # ---------------------------------------------------------------------
    
    # 🔄 AUTOMATISK ÅTERANSLUTNINGSLOOP (Säkrar att tråden ALDRIG dör vid split eller disconnect!)
    while True:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(15.0)
        print(f"[CONNECT] Attempting to connect to {config.SERVER}:{config.PORT}...")
        
        try:
            s.connect((config.SERVER, config.PORT))
            s.settimeout(90.0) 
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, 'TCP_KEEPIDLE'):
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
                
            if oserve:
                oserve.irc_connection = s
            print(f"[CONNECT] Connected to socket successfully!")
        except Exception as e:
            print(f"[ERROR] Connection failed: {e}. Återansluter om 10 sekunder...")
            time.sleep(10)
            continue
            
        # 🔑 SKICKA AUTENTISERING DIREKT (Utrotar frysningen vid reconnect!)
        s.send(f"USER flacserv 0 * :flacserv bot\r\n".encode())
        s.send(f"NICK {config.NICKNAME}\r\n".encode())
        print("[INFO] NICK och USER skickat! Startar nätverksläsaren...")
        
        buffer = ""
        joined = False
        bot_joined_channel = False
        announce.is_ready = False
        
        total_channels_to_join = len(config.CHANNEL.split(","))
        namespaces_received = 0
        last_recv_time = time.time()
        # 📡 INRE MEDDELANDELOOP (Huvudläsaren för all datatrafik live)
        while True:
            try:
                if time.time() - last_recv_time > 45.0:
                    try:
                        s.send(b"PING :lagcheck\r\n")
                    except:
                        pass
                    last_recv_time = time.time()

                data = s.recv(2048).decode("utf-8", errors="ignore")
                if not data:
                    print("[DISCONNECT] Server closed connection. Breaking to reconnect motor...")
                    break # Hoppar ut ur den inre loopen och återansluter direkt!
                    
                last_recv_time = time.time()
                buffer += data
                lines = buffer.split("\r\n")
                buffer = lines.pop()
                
                for line in lines:
                    if not line.strip():
                        continue
                    if getattr(config, 'DEBUG_MODE', False):
                        is_channel_traffic = " PRIVMSG #" in line
                        is_for_me = f"PRIVMSG {config.NICKNAME}" in line or f" {config.NICKNAME} " in line or f"@{config.NICKNAME.lower()}" in line.lower()
                        if not is_channel_traffic or is_for_me or "ERROR" in line:
                            print(f"[RAW IN] {line.strip()}")
                    if line.startswith("PING"):
                        parts = line.split()
                        if len(parts) > 1:
                            pong_code = parts[1].lstrip(':')
                            s.send(f"PONG {pong_code}\r\n".encode())
                    
                    if " PONG " in line and "OSERVE_LATENCY_CHECK" in line:
                        import commands
                        commands.handle_pong_response(category="INFO")
                        continue
                            
                    if " 513 " in line and "PONG" in line:
                        parts = line.split()
                        pong_code = parts[-1].strip()
                        s.send(f"PONG {pong_code}\r\n".encode())
                    
                    # FÖRDRÖJD KANAL-JOIN (Inklusive din dolda debug-kanal!)
                    if not joined and ("001" in line or "376" in line):
                        joined = True
                        print(f"[INFO] Ansluten till servern! Väntar 5 sekunder på stabilisering innan JOIN...")
                        
                        def delayed_join(socket_conn, channels):
                            time.sleep(5)
                            try:
                                socket_conn.send(f"JOIN {channels}\r\n".encode())
                                debug_chan = getattr(config, 'DEBUG_CHANNEL', '#flac-debug')
                                socket_conn.send(f"JOIN {debug_chan}\r\n".encode())
                                print(f"[JOIN] Gick med i huvudkanaler och debug-kanal: {debug_chan}")
                            except Exception as join_err:
                                print(f"[ERROR] Kunde inte skicka JOIN: {join_err}")
                                
                        threading.Thread(target=delayed_join, args=(s, config.CHANNEL), daemon=True).start()

                    # 🛡️ RÄTTAD OCH ÖVERLAPPSSÄKRAD AKTIVERINGSSPÄRR:
                    if joined and not getattr(config, 'activation_triggered', False) and " 366 " in line:
                        namespaces_received += 1
                        print(f"[INFO] Received End of NAMES for channel ({namespaces_received}/{total_channels_to_join})")
                        
                        if namespaces_received >= total_channels_to_join:
                            # Spika fast låset i RAM-minnet DIREKT på 0ms så nästa 366-rad blockeras!
                            config.activation_triggered = True
                            print(f"[INFO] All channels joined successfully! Waiting 5 seconds for settle...")
                            
                            def delayed_activate():
                                import sys
                                import time
                                import announce
                                
                                time.sleep(5)
                                config.bot_joined_channel = True
                                
                                oserve_mod = sys.modules.get('oserve')
                                if oserve_mod:
                                    oserve_mod.bot_joined_channel = True
                                    oserve_mod.irc_connection = s
                                    
                                announce.is_ready = True
                                if hasattr(announce, 'last_announce_time'):
                                    announce.last_announce_time = time.time()
                                    
                                if not getattr(config, 'announce_thread_alive', False):
                                    announce.start_announce_thread()
                                    config.announce_thread_alive = True
                                else:
                                    print("[CONNECT FIX] Reklamtråden var redan aktiv, länkade om till den nya nätverkssocketen.")
                                    
                            threading.Thread(target=delayed_activate, daemon=True).start()

                    nick_match = re.match(r"^:([^!]+)!.* NICK :(.+)$", line)
                    if nick_match:
                        old_nick = nick_match.group(1).lower()
                        new_nick = nick_match.group(2).strip()
                        if old_nick in config.send_queue:
                            import queue_mgr
                            queue_mgr.config.send_queue[new_nick.lower()] = queue_mgr.config.send_queue.pop(old_nick)
                            
                    if " 352 " in line:
                        parts = line.split()
                        if len(parts) > 7:
                            target_nick = parts[7].lower()
                            config.whois_status[target_nick] = True

                    # =====================================================================
                    # VÄCK KÖN VID JOIN & HELAUTOMATISK SYNC AV RAM-KANALLISTOR
                    # =====================================================================
                    if " 353 " in line:
                        name_match = re.search(r" 353 [^#]+([#\w\-]+) :(.+)$", line)
                        if name_match:
                            chan = name_match.group(1).lower()
                            names = [n.strip("@+~&%").lower() for n in name_match.group(2).split()]
                            if chan not in config.channel_users:
                                config.channel_users[chan] = set()
                            config.channel_users[chan].update(names)

                    elif " JOIN " in line and f":{config.NICKNAME}!" not in line:
                        join_match = re.search(r"^:([^!]+)!.* JOIN :?([#\w\-]+)", line)
                        if join_match:
                            joined_user = join_match.group(1)
                            joined_chan = join_match.group(2)
                            j_key = joined_user.lower()
                            
                            if joined_chan.lower() not in config.channel_users:
                                config.channel_users[joined_chan.lower()] = set()
                            config.channel_users[joined_chan.lower()].add(j_key)
                            
                            if hasattr(config, 'frozen_queues') and j_key in config.frozen_queues:
                                del config.frozen_queues[j_key]
                                print(f"[DCC REALTIME VÄCKNING] {joined_user} klev in i {joined_chan} igen! Tinar upp kön.")
                                
                                files_in_q = len(config.dcc_queue.get(j_key, [])) if hasattr(config, 'dcc_queue') else 0
                                announce.send_debug(f"User {config.C_BOLD}{joined_user}{config.C_RESET} returned to {joined_chan}, continuing queue of {config.C_BOLD}{files_in_q}{config.C_RESET} file(s)", category="JOIN")
                                
                                threading.Thread(target=dcc.check_queue_and_send, args=(s, joined_user), daemon=True).start()
                                
                    elif " PART " in line:
                        part_match = re.search(r"^:([^!]+)!.* PART ([#\w\-]+)", line)
                        if part_match:
                            p_user = part_match.group(1).lower()
                            p_chan = part_match.group(2).lower()
                            if p_chan in config.channel_users and p_user in config.channel_users[p_chan]:
                                config.channel_users[p_chan].remove(p_user)

                    elif " QUIT " in line:
                        quit_match = re.search(r"^:([^!]+)!", line)
                        if quit_match:
                            q_user = quit_match.group(1).lower()
                            for chan in config.channel_users:
                                if q_user in config.channel_users[chan]:
                                    config.channel_users[chan].remove(q_user)
                    # HUVUD-PRIVMSG TOLK: Hanterar sökningar och bot-kommandon
                    match = re.match(r"^:([^!]+)!.* PRIVMSG ([#\w\-]+) :(.+)$", line)
                    if match:
                        user = match.group(1)
                        target_chan = match.group(2)
                        msg = match.group(3).strip()
                        if user.lower() == config.NICKNAME.lower():
                            continue

                        if not security.check_user_status(user):
                            continue
                            
                        is_bot_command = msg.lower() == f"@{config.NICKNAME.lower()}" or msg.startswith("@find ") or msg.startswith("@locator ") or msg.startswith(f"!{config.NICKNAME} ")
                        if is_bot_command and security.is_flooding(user):
                            continue 
                            
                        try:
                            import commands
                            import db
                            db.check_and_rotate_day()
                            
                            # 1. CTCP-KOMMANDON
                            if msg.startswith("\x01") and msg.endswith("\x01"):
                                ctcp_cmd = msg.strip("\x01").strip().upper()
                                if ctcp_cmd == "QUE":
                                    threading.Thread(target=commands.handle_queue_check, args=(s, user, target_chan), daemon=True).start()
                                    continue
                                elif ctcp_cmd == "REMOVE":
                                    threading.Thread(target=commands.handle_queue_remove, args=(s, user, target_chan), daemon=True).start()
                                    continue
                            
                            # 2. VANLIGA TEXT- OCH KANALKOMMANDON
                            elif msg.lower() == f"@{config.NICKNAME.lower()}":
                                threading.Thread(target=list.send_file_list, args=(s, user, target_chan)).start()
                            elif msg.lower() == f"@{config.NICKNAME.lower()}-que":
                                threading.Thread(target=commands.handle_queue_check, args=(s, user, target_chan), daemon=True).start()
                            elif msg.lower() == f"@{config.NICKNAME.lower()}-remove":
                                threading.Thread(target=commands.handle_queue_remove, args=(s, user, target_chan), daemon=True).start()
                            elif msg.startswith("@find ") or msg.startswith("@locator "):
                                parts = msg.split(" ", 1)
                                if len(parts) > 1:
                                    search_term = parts[1].strip()
                                    if search_term:
                                        threading.Thread(target=list.execute_search, args=(s, user, search_term, target_chan), daemon=True).start()
                            elif msg == "!list":
                                list.send_list_trigger_info(s, user)
                            elif msg.lower() == "!debugnames":
                                if hasattr(config, 'channel_users') and target_chan.lower() in config.channel_users:
                                    current_qty = len(config.channel_users[target_chan.lower()])
                                    s.send(f"NOTICE {user} :[RAM-CHECK] Currently tracking {current_qty} user(s) live via 353-numeric in {target_chan}.\r\n".encode())
                                else:
                                    s.send(f"NOTICE {user} :[RAM-CHECK] Critical: No 353 names loaded yet for {target_chan} in config structure.\r\n".encode())
                            elif msg.lower() == "!ping":
                                threading.Thread(target=commands.handle_ping_request, args=(s, user, target_chan), daemon=True).start()
                            elif msg.lower() == "!rehash":
                                threading.Thread(target=commands.handle_rehash_request, args=(user, target_chan), daemon=True).start()
                            elif msg.startswith("!ban "):
                                threading.Thread(target=commands.handle_hard_ban_request, args=(user, target_chan, msg), daemon=True).start()
                            elif msg.startswith("!unban "):
                                threading.Thread(target=commands.handle_hard_unban_request, args=(user, target_chan, msg), daemon=True).start()
                            elif msg.lower() == "!update":
                                threading.Thread(target=commands.handle_list_update_request, args=(user, target_chan), daemon=True).start()
                            elif msg.startswith(f"!{config.NICKNAME} "):
                                parts = msg.split(" ", 1)
                                if len(parts) > 1:
                                    requested_file = parts[1].strip()
                                    threading.Thread(target=dcc.handle_download_request, args=(s, user, requested_file, target_chan)).start()
                                    
                        except Exception as cmd_err:
                            print(f"[ERROR] Fel vid hantering av botkommando från {user}: {cmd_err}")

            except socket.timeout:
                try:
                    s.send(f"PING {config.NICKNAME}\r\n".encode())
                    announce.send_debug("Network silent for 90s. Sent active Keep-Alive PING to Undernet Server.", category="INFO")
                    last_recv_time = time.time()
                except Exception as ping_err:
                    try: s.close()
                    except: pass
                    break
            except (socket.error, Exception) as loop_err:
                print(f"[CRITICAL MAIN ERROR] Huvudloopen dippade: {loop_err}")
                break

        # 🧹 ÅTERSTÄLL ALLA FLAGOR INNAN NÄSTA VARV I WHILE TRUE DRAR IGÅNG ÅTERANSLUTNINGEN
        print("[CONNECT] Tappade anslutning. Återansluter till IRC Server om 10 sekunder...")
        config.bot_joined_channel = False
        config.activation_triggered = False # NYTT: Nollställ spärren inför nästa reconnect!
        oserve_mod = sys.modules.get('oserve')
        if oserve_mod:
            oserve_mod.bot_joined_channel = False
        announce.is_ready = False
        import queue_mgr
        if "channel_announce" in queue_mgr.config.send_queue:
            queue_mgr.config.send_queue["channel_announce"] = []
        time.sleep(10)
