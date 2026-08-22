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

def irc_loop():
    """Hanterar anslutningen, PING/PONG och alla inkommande PRIVMSG från Undernet"""
    global bot_joined_channel
    import announce
    oserve = sys.modules.get('oserve')
    
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
    
    # 🛡️ GLOBAL SPARING: Sätts HÄR först av allt så att variabeln ALLTID finns i RAM!
    if not hasattr(config, 'ORIGINAL_NICK'):
        config.ORIGINAL_NICK = getattr(config, 'NICKNAME', 'DCCore')

    # 🔄 AUTOMATISK ÅTERANSLUTNINGSLOOP (Säkrar att tråden ALDRIG dör vid split eller disconnect!)
    while True:
        # Vi utgår ALLTID ifrån att försöka ta botens sanna original-nick vid varje anslutning
        config.NICKNAME = config.ORIGINAL_NICK

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(70.0)
        
        print(f"[CONNECT] Attempting to connect to {config.SERVER}:{config.PORT} as {config.NICKNAME}...")
        
        try:
            s.connect((config.SERVER, config.PORT))
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            
            if hasattr(socket, 'TCP_KEEPIDLE'):
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 2)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                
            oserve_mod = sys.modules.get('oserve')
            if oserve_mod:
                oserve_mod.irc_connection = s
            print(f"[CONNECT] Connected to socket successfully!")
        except Exception as e:
            print(f"[ERROR] Connection failed: {e}. Återansluter om 10 sekunder...")
            time.sleep(10)
            continue
            
        # 🔑 SKICKA AUTENTISERING DIREKT (Serverstyrt NICK-val via sanna 433-returer!)
        try:
            s.send(f"NICK {config.NICKNAME}\r\n".encode())
            
            auth_buffer = ""
            while True:
                auth_data = s.recv(1024).decode("utf-8", errors="ignore")
                if not auth_data:
                    break
                auth_buffer += auth_data
                auth_lines = auth_buffer.split("\r\n")
                auth_buffer = auth_lines.pop()
                
                for a_line in auth_lines:
                    if " 433 " in a_line or "erroneous nickname" in a_line.lower():
                        alt_nick = getattr(config, 'ALT_NICKNAME', f"{config.ORIGINAL_NICK}`")
                        print(f"[SERVER 433] Nicket {config.NICKNAME} var upptaget! Växlar CURRENT_NICK till: {alt_nick}")
                        s.send(f"NICK {alt_nick}\r\n".encode())
                        config.NICKNAME = alt_nick
                    
                    if " 001 " in a_line or " 002 " in a_line or "PING" in a_line or "NOTICE" in a_line:
                        ident_str = getattr(config, 'IDENT', 'dccore')
                        real_str = getattr(config, 'REALNAME', 'dccore bot')
                        s.send(f"USER {ident_str} 0 * :{real_str}\r\n".encode())
                        break
                else:
                    continue
                break
                
            print(f"[INFO] Handskakning klar! CURRENT_NICK spikat som: {config.NICKNAME}. Startar läsaren...")

            s.settimeout(70.0)
        except Exception as auth_err:
            print(f"[ERROR] Fel under server-handskakningen: {auth_err}")
            try: s.close()
            except: pass
            continue

        buffer = ""
        joined = False
        bot_joined_channel = False
        announce.is_ready = False
        
        total_channels_to_join = len(config.CHANNEL.split(","))
        namespaces_received = 0
        last_recv_time = time.time()
        
        while True:
            try:
                if time.time() - last_recv_time > 45.0:
                    try:
                        s.send(b"PING :lagcheck\r\n")
                    except:
                        pass
                    last_recv_time = time.time()

                try:
                    data = s.recv(2048).decode("utf-8", errors="ignore")
                except socket.timeout:
                    print("[TIMEOUT] Ingen data mottagen på 70 sekunder (Undernet PING uteblev). Sliter av socketen!")
                    try: s.close()
                    except: pass
                    break
                except socket.error as net_err:
                    print(f"[DISCONNECT FIX] Linux Keepalive upptäckte dött nätverk ({net_err}). Bryter för återanslutning!")
                    try: s.close()
                    except: pass
                    break
                except Exception as e:
                    print(f"[IRC READ ERROR] Oväntat fel vid nätverksavläsning: {e}")
                    try: s.close()
                    except: pass
                    break

                if not data:
                    print("[DISCONNECT] Server closed connection. Breaking to reconnect motor...")
                    try: s.close()
                    except: pass
                    break
                    
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
                    # 🛡️ LIVE 433-BACKUP: Fångar enbart upp OFFICIELLA server-krockar (Ignorerar kanalsnack!)
                    if " 433 " in line or "erroneous nickname" in line.lower():
                        # 🚫 SPÄRR: Om det är kanaltrafik (PRIVMSG) är det bara någon som snackar – IGNORERA!
                        if " PRIVMSG " not in line and " NOTICE " not in line:
                            main_nick = getattr(config, 'ORIGINAL_NICK', 'DCCore')
                            if str(config.NICKNAME).lower() == main_nick.lower():
                                alt_nick = getattr(config, 'ALT_NICKNAME', f"{main_nick}`")
                                print(f"[LIVE NICK COLLISION] Servern rapporterade en äkta krock för {main_nick}! Reservnick: {alt_nick}")
                                s.send(f"NICK {alt_nick}\r\n".encode())
                                config.NICKNAME = alt_nick

                    # 🛡️ AUTOMATISKT ÅTERTAGANDE: Återta huvudnicket live i samma millisekund som din mIRC-klient stängs!
                    if " QUIT " in line or " PART " in line:
                        main_nick = getattr(config, 'ORIGINAL_NICK', 'DCCore')
                        if str(config.NICKNAME).lower() != main_nick.lower():
                            if f":{main_nick.lower()}!" in line.lower():
                                print(f"[NICK RECOVERY] Upptäckte att huvudnicket {main_nick} loggade ut! Återtar namnet live...")
                                try:
                                    s.send(f"NICK {main_nick}\r\n".encode())
                                    config.NICKNAME = main_nick
                                except Exception as recovery_err:
                                    print(f"[NICK RECOVERY ERROR] Misslyckades att återta nick: {recovery_err}")


                    if not joined and ("001" in line or "376" in line):
                        joined = True
                        print(f"[INFO] Ansluten till servern! Väntar 5 sekunder på stabilisering innan JOIN...")
                        
                        def delayed_join(socket_conn, channels):
                            time.sleep(5)
                            try:
                                socket_conn.send(f"JOIN {channels}\r\n".encode())
                                debug_chan = getattr(config, 'DEBUG_CHANNEL', '#flac-serv')
                                socket_conn.send(f"JOIN {debug_chan}\r\n".encode())
                                print(f"[JOIN] Gick med i huvudkanaler och debug-kanal: {debug_chan}")
                            except Exception as join_err:
                                print(f"[ERROR] Kunde inte skicka JOIN: {join_err}")
                                
                        threading.Thread(target=delayed_join, args=(s, config.CHANNEL), daemon=True).start()

                    if joined and not getattr(config, 'activation_triggered', False) and " 366 " in line:
                        namespaces_received += 1
                        print(f"[INFO] Received End of NAMES for channel ({namespaces_received}/{total_channels_to_join})")
                        
                        if namespaces_received >= total_channels_to_join:
                            config.activation_triggered = True
                            print(f"[INFO] All channels joined successfully! Waiting 5 seconds for settle...")
                            
                            def delayed_activate():
                                import sys
                                import time
                                import announce
                                import threading
                                
                                time.sleep(5)
                                config.bot_joined_channel = True
                                
                                oserve_mod = sys.modules.get('oserve')
                                if oserve_mod:
                                    oserve_mod.bot_joined_channel = True
                                    oserve_mod.irc_connection = s
                                    
                                announce.is_ready = True
                                if hasattr(announce, 'last_announce_time'):
                                    announce.last_announce_time = time.time()
                                    
                                # 🛡️ AUTOMATISK LIVE-ÅTERTAGARSLUSS (Bevakar och återtar tronen efter netsplits!)
                                def background_nick_monitor(sock_inst):
                                    main_nick = getattr(config, 'ORIGINAL_NICK', 'DCCore')
                                    # Loopa och kolla läget var 10:e sekund i upp till 5 minuter efter JOIN
                                    for _ in range(30):
                                        if str(config.NICKNAME).lower() == main_nick.lower():
                                            break # Vi kör redan på huvudnicket, stäng ner bevakningen!
                                            
                                        main_nick_active = False
                                        if hasattr(config, 'channel_users') and isinstance(config.channel_users, dict):
                                            for chan_name, users_set in config.channel_users.items():
                                                if main_nick.lower() in [u.lower() for u in users_set]:
                                                    main_nick_active = True
                                                    break
                                                    
                                        if not main_nick_active:
                                            print(f"\n[NICK RECOVERY] Upptäckte att spöknicket {main_nick} har timeoutat! Byter nick...")
                                            try:
                                                sock_inst.send(f"NICK {main_nick}\r\n".encode())
                                                config.NICKNAME = main_nick
                                                break
                                            except:
                                                break
                                        time.sleep(10)
                                
                                # Starta den uthålliga klockan asynkront i bakgrunden
                                threading.Thread(target=background_nick_monitor, args=(s,), daemon=True).start()
                                    
                                print("[CONNECT FIX] Startar om kanalreklamen helautomatiskt...")
                                threading.Thread(target=announce.announce_worker, daemon=True).start()
                                config.announce_thread_alive = True
                                    
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
                    if " 353 " in line:
                        name_match = re.search(r" 353 [^#]+([#\w\-]+) :(.+)$", line)
                        if name_match:
                            chan = name_match.group(1).lower()
                            names = [n.strip("@+~&%").lower() for n in name_match.group(2).split()]
                            if chan not in config.channel_users:
                                config.channel_users[chan] = set()
                            config.channel_users[chan].update(names)

                            # -------------------------------------------------
                            # 🛡️ RECONNECT-TINING (KRITISK KÖRÄDDARE):
                            # Efter en reconnect kommer alla som redan står i kanalen tillbaka via
                            # NAMES (353) och INTE via JOIN. Utan den här slussen låg deras köer kvar
                            # frysta och raderades av 5-minuterstimern trots att de aldrig lämnat.
                            # -------------------------------------------------
                            thawed_users = [n for n in names if n in getattr(config, 'frozen_queues', {})]
                            for frozen_user in thawed_users:
                                del config.frozen_queues[frozen_user]
                                files_in_q = len(config.dcc_queue.get(frozen_user, []))
                                print(f"[DCC RECONNECT VÄCKNING] {frozen_user} stod kvar i {chan} vid NAMES-synk! Tinar upp {files_in_q} fil(er).")
                                threading.Thread(target=dcc.check_queue_and_send, args=(s, frozen_user), daemon=True).start()

                            if thawed_users:
                                announce.send_debug(f"Reconnect sync in {chan}: thawed {config.C_BOLD}{len(thawed_users)}{config.C_RESET} queue(s) for users who never left.", category="JOIN")

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
                            
                            if msg.startswith("\x01") and msg.endswith("\x01"):
                                ctcp_cmd = msg.strip("\x01").strip().upper()
                                if ctcp_cmd == "QUE":
                                    threading.Thread(target=commands.handle_queue_check, args=(s, user, target_chan), daemon=True).start()
                                    continue
                                elif ctcp_cmd == "REMOVE":
                                    threading.Thread(target=commands.handle_queue_remove, args=(s, user, target_chan), daemon=True).start()
                                    continue
                            elif msg.lower() == f"@{config.NICKNAME.lower()}":
                                threading.Thread(target=list.send_file_list, args=(s, user, target_chan)).start()
                            elif msg.lower() == f"@{config.NICKNAME.lower()}-que":
                                threading.Thread(target=commands.handle_queue_check, args=(s, user, target_chan), daemon=True).start()
                                continue
                            elif msg.lower() == f"@{config.NICKNAME.lower()}-remove":
                                threading.Thread(target=commands.handle_queue_remove, args=(s, user, target_chan), daemon=True).start()
                                continue
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

            except Exception as inner_loop_err:
                print(f"[IRC INTERNAL ERROR] Oväntat fel inuti meddelandeloopen: {inner_loop_err}")
                try: s.close()
                except: pass
                break

        # 🧹 ÅTERSTÄLL ALLA FLAGOR INNAN NÄSTA VARV I WHILE TRUE DRAR IGÅNG ÅTERANSLUTNINGEN
        print("[CONNECT] Tappade anslutning. Återansluter till IRC Server om 10 sekunder...")
        config.bot_joined_channel = False
        
        # 🛡️ FIXAD: Tömmer kanallistorna i RAM helt vid krasch så boten inte blockerar sitt eget nick nästa varv!
        if hasattr(config, 'channel_users') and isinstance(config.channel_users, dict):
            config.channel_users.clear()
            
        config.activation_triggered = False
        oserve_mod = sys.modules.get('oserve')
        if oserve_mod:
            oserve_mod.bot_joined_channel = False
        announce.is_ready = False
        import queue_mgr
        if "channel_announce" in queue_mgr.config.send_queue:
            queue_mgr.config.send_queue["channel_announce"] = []
        time.sleep(10)

