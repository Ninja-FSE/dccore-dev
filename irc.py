# irc.py - Dedikerad IRC-nätverksmodul för Undernet (Del 1)
import socket
import threading
import time
import re
import sys
import os
import traceback
import urllib.request # NY IMPORT FÖR EXTERN IP-KOLL
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
    oserve = sys.modules.get('oserve')
    
    # ---------------------------------------------------------------------
    # FRAMTIDSSÄKRAD IP-KOLL via api.ipify.org
    # Körs EN GÅNG innan anslutning. Gör boten immun mot dolda IRC-cloaks (+x)!
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
        print(f"[ERROR] Connection failed: {e}")
        return
        
    s.send(f"USER flacserv 0 * :flacserv bot\r\n".encode())
    s.send(f"NICK {config.NICKNAME}\r\n".encode())
    
    buffer = ""
    joined = False
    bot_joined_channel = False
    
    import announce
    announce.is_ready = False
    
    total_channels_to_join = len(config.CHANNEL.split(","))
    namespaces_received = 0
    last_recv_time = time.time()
    
    while True:
        try:
            if time.time() - last_recv_time > 45.0:
                try:
                    s.send(b"PING :lagcheck\r\n")
                    if getattr(config, 'DEBUG_MODE', False):
                        print("[RAW OUT] PING :lagcheck (Aktiv Keep-Alive)")
                except:
                    pass
                last_recv_time = time.time()

            data = s.recv(2048).decode("utf-8", errors="ignore")
            if not data:
                print("[DISCONNECT] Server closed connection.")
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
                        if getattr(config, 'DEBUG_MODE', False):
                            print(f"[RAW OUT] PONG {pong_code}")
                                        # ---------------------------------------------------------------------
                # NYTT: PONG-SLUSS FÖR !PING-KOMMANDOT (Placerad perfekt i Del 1!)
                # Fångar latenssvaret live och skickar det direkt till commands.py
                # ---------------------------------------------------------------------
                if " PONG " in line and "OSERVE_LATENCY_CHECK" in line:
                    import commands
                    commands.handle_pong_response(category="INFO")
                    continue
                # ---------------------------------------------------------------------
                        
                if " 513 " in line and "PONG" in line:
                    parts = line.split()
                    pong_code = parts[-1].strip()
                    s.send(f"PONG {pong_code}\r\n".encode())
                    print(f"[RAW OUT DIRECT] SPECIAL PONG {pong_code}")
                
                # FÖRDRÖJD KANAL-JOIN
                if not joined and ("001" in line or "376" in line):
                    joined = True
                    print(f"[INFO] Ansluten till servern! Väntar 5 sekunder på stabilisering innan JOIN...")
                    
                    def delayed_join(socket_conn, channels):
                        time.sleep(5)
                        try:
                            socket_conn.send(f"JOIN {channels}\r\n".encode())
                            print(f"[INFO] Skickade JOIN-kommando till alla kanaler: {channels}")
                        except Exception as join_err:
                            print(f"[ERROR] Kunde inte skicka JOIN: {join_err}")
                            
                    threading.Thread(target=delayed_join, args=(s, config.CHANNEL), daemon=True).start()

                # irc.py - Dedikerad IRC-nätverksmodul för Undernet (Del 2)
                if joined and not getattr(config, 'bot_joined_channel', False) and " 366 " in line:
                    namespaces_received += 1
                    print(f"[INFO] Received End of NAMES for channel ({namespaces_received}/{total_channels_to_join})")
                    if namespaces_received >= total_channels_to_join:
                        print(f"[INFO] All channels joined successfully! Waiting 5 seconds for settle...")
                        
                        def delayed_activate():
                            time.sleep(5)
                            config.bot_joined_channel = True
                            
                            oserve_mod = sys.modules.get('oserve')
                            if oserve_mod:
                                oserve_mod.bot_joined_channel = True
                            
                            import announce
                            announce.is_ready = True
                            
                            # ---------------------------------------------------------------------
                            # NYTT: Tvinga boten att joina din debug-kanal live efter stabilisering!
                            # ---------------------------------------------------------------------
                            s.send(f"JOIN {config.DEBUG_CHANNEL}\r\n".encode())
                            print(f"[INFO] Skickade ett dedikerat JOIN-kommando till {config.DEBUG_CHANNEL}")
                            # ---------------------------------------------------------------------
                            
                            if not getattr(config, 'announce_thread_alive', False):
                                announce.start_announce_thread()
                                config.announce_thread_alive = True
                                print("[ANNOUNCE] Skapade en helt ny, exklusiv master-reklamtråd.")
                            else:
                                print("[ANNOUNCE] Reklamtråden lever redan sedan innan. Återaktiverade den beklagliga klockan.")
                            
                            print(f"[INFO] FLAC-Serv is now FULLY ACTIVE across all channels!")                          
                        threading.Thread(target=delayed_activate, daemon=True).start()

                        
                nick_match = re.match(r"^:([^!]+)!.* NICK :(.+)$", line)
                if nick_match:
                    old_nick = nick_match.group(1).lower()
                    new_nick = nick_match.group(2).strip()
                    if old_nick in config.send_queue:
                        print(f"[NICK TRACK] {old_nick} changed nick to {new_nick}. Moving queue.")
                        import queue_mgr
                        queue_mgr.config.send_queue[new_nick.lower()] = queue_mgr.config.send_queue.pop(old_nick)
                        
                # ---------------------------------------------------------------------
                # SMART KANAL-SPÅRARE: Fångar WHO-svar och reagerar live på nya JOIN!
                # ---------------------------------------------------------------------
                if " 352 " in line:
                    parts = line.split()
                    if len(parts) > 7:
                        target_nick = parts[7].lower()
                        config.whois_status[target_nick] = True

                # ---------------------------------------------------------------------
                # REALTIDS-SPÅRARE FÖR PART OCH QUIT: Skottsäker och sväljer alla meddelanden!
                # ---------------------------------------------------------------------
                # 1. FÅNGA UPP DETEKTERAD KANAL-LÄMNING (PART)
                if " PART " in line:
                    part_match = re.search(r"^:([^!]+)!.* PART :?([#\w\-]+)(?:\s+:(.+))?$", line)
                    if part_match:
                        p_user = part_match.group(1)
                        p_chan = part_match.group(2)
                        
                        # Stensäker spärr: Om p_user är boten själv, ignorera!
                        if p_user.lower() != config.NICKNAME.lower():
                            p_reason = part_match.group(3).strip() if part_match.group(3) else "No parting comment"
                            p_key = p_user.lower()
                            
                            # Logga enbart till #flac-debug om användaren faktiskt har filer i kön
                            if hasattr(config, 'dcc_queue') and p_key in config.dcc_queue:
                                import announce
                                announce.send_debug(f"User {config.C_BOLD}{p_user}{config.C_RESET} parted from {p_chan} (Reason: {p_reason}). Frys-timer aktiv.", category="PART")

                # 2. FÅNGA UPP SERVER-NEDKOPPLING (QUIT)
                if " QUIT " in line:
                    quit_match = re.search(r"^:([^!]+)!.* QUIT\s+:(.+)$", line)
                    if quit_match:
                        q_user = quit_match.group(1)
                        
                        # Samma här, se till att boten inte reagerar på sitt eget quit-paket
                        if q_user.lower() != config.NICKNAME.lower():
                            q_reason = quit_match.group(2).strip() if quit_match.group(2) else "Disconnected from IRC"
                            q_key = q_user.lower()
                            
                            # Logga om användaren har en levande kö i RAM-minnet
                            if hasattr(config, 'dcc_queue') and q_key in config.dcc_queue:
                                import announce
                                announce.send_debug(f"User {config.C_BOLD}{q_user}{config.C_RESET} disconnected from IRC (Reason: {q_reason}). Frys-timer aktiv.", category="QUIT")
                # ---------------------------------------------------------------------

                # VÄCK KÖN VID JOIN: Med stenhård realtids-felsökning och kanalspårning!
                if " JOIN " in line and f":{config.NICKNAME}!" not in line:
                    # Vi loggar att boten faktiskt ser en JOIN-rad överhuvudtaget
                    #print(f"[DEBUG JOIN 1] Boten fångade en JOIN-rad: {line.strip()}")
                    
                    # RÄTTAD REGEX: Fångar nu upp både användarnamn (group 1) och kanalnamn (group 2)
                    join_match = re.search(r"^:([^!]+)!.* JOIN :?([#\w\-]+)", line)
                    if join_match:
                        joined_user = join_match.group(1)
                        joined_chan = join_match.group(2)
                        j_key = joined_user.lower()
                        #print(f"[DEBUG JOIN 2] Regex lyckades! Användare: {joined_user} i kanal: {joined_chan}")
                        
                        if hasattr(config, 'frozen_queues') and j_key in config.frozen_queues:
                            del config.frozen_queues[j_key]
                            print(f"[DCC REALTIME VÄCKNING] {joined_user} klev in i {joined_chan} igen! Tinar upp kön på 0ms.")
                            
                            # ---------------------------------------------------------------------
                            # NY DEBUG-RAD: Visar nu exakt vilken kanal användaren återvände till!
                            # ---------------------------------------------------------------------
                            files_in_q = len(config.dcc_queue.get(j_key, [])) if hasattr(config, 'dcc_queue') else 0
                            import announce
                            announce.send_debug(f"User {config.C_BOLD}{joined_user}{config.C_BOLD} returned to {joined_chan}, continuing queue of {config.C_BOLD}{files_in_q}{config.C_BOLD} file(s)", category="JOIN")

                            # ---------------------------------------------------------------------
                            
                            # Starta om sändningen direkt i en egen tråd
                            threading.Thread(target=dcc.check_queue_and_send, args=(s, joined_user), daemon=True).start()
                        #else:
                            #print(f"[DEBUG JOIN 4] Hoppade över väckning eftersom {j_key} inte fanns i frysboxen.")
                    #else:
                        #print("[DEBUG JOIN ERROR] Regex misslyckades med att parsa användarnamnet!")
                        
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
                        
                    # INBYGGD KOMMANDOTOLK: Kedjad och säkrad mot dubbelpostningar!
                    try:
                        import commands
                       
                        # 1. CTCP-KOMMANDON (Använder nu return för att stänga direkt!)
                        if msg.startswith("\x01") and msg.endswith("\x01"):
                            ctcp_cmd = msg.strip("\x01").strip().upper()
                            if ctcp_cmd == "QUE":
                                threading.Thread(target=commands.handle_queue_check, args=(s, user, target_chan), daemon=True).start()
                                return
                            elif ctcp_cmd == "REMOVE":
                                threading.Thread(target=commands.handle_queue_remove, args=(s, user, target_chan), daemon=True).start()
                                return
                        
                        # 2. VANLIGA TEXT- OCH KANALKOMMANDON (Kedjade i en enda stängd elif-lina!)
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
                        # ---------------------------------------------------------------------
                        # Standard !PING-kommando
                        # ---------------------------------------------------------------------
                        elif msg.lower() == "!ping":
                            threading.Thread(target=commands.handle_ping_request, args=(s, user, target_chan), daemon=True).start()
                        # ---------------------------------------------------------------------
                        # NYTT: !REHASH-KOMMANDO (Laddar om koden live via commands.py!)
                        # ---------------------------------------------------------------------
                        elif msg.lower() == "!rehash":
                            threading.Thread(target=commands.handle_rehash_request, args=(user, target_chan), daemon=True).start()
                        # ---------------------------------------------------------------------
                        # ---------------------------------------------------------------------
                        # NYTT: !BAN OCH !UNBAN (Styr din permanenta hard_bans.txt live!)
                        # ---------------------------------------------------------------------
                        elif msg.startswith("!ban "):
                            threading.Thread(target=commands.handle_hard_ban_request, args=(user, target_chan, msg), daemon=True).start()
                        elif msg.startswith("!unban "):
                            threading.Thread(target=commands.handle_hard_unban_request, args=(user, target_chan, msg), daemon=True).start()
                        # ---------------------------------------------------------------------
                        # NYTT: !UPDATE-KOMMANDO (Kör externt skript och räknar nya filer live!)
                        # ---------------------------------------------------------------------
                        elif msg.lower() == "!update":
                            threading.Thread(target=commands.handle_list_update_request, args=(user, target_chan), daemon=True).start()
                        # ---------------------------------------------------------------------
                        elif msg.startswith(f"!{config.NICKNAME} "):
                            parts = msg.split(" ", 1)
                            if len(parts) > 1:
                                requested_file = parts[1].strip()
                                threading.Thread(target=dcc.handle_download_request, args=(s, user, requested_file, target_chan)).start()
                                
                    except Exception as cmd_err:
                        print(f"[ERROR] Fel vid hantering av botkommando från {user}: {cmd_err}")



        except socket.timeout:
            print("[TIMEOUT-PROTECTION] Servern var tyst i 90s. Kör en tvingad Keep-Alive...")
            try:
                # Vi skickar en äkta PING med botens eget nick. Detta tvingar Undernet att svara oss!
                s.send(f"PING {config.NICKNAME}\r\n".encode())
                
                # Vi slänger även upp en blixtsnabb notis i din nya #flac-debug-kanal via VIP-expressen!
                import announce
                announce.send_debug("Network silent for 90s. Sent active Keep-Alive PING to Undernet Server.", category="INFO")
                
                last_recv_time = time.time()
            except Exception as ping_err:
                print(f"[TIMEOUT ERROR] Gick inte att skicka Keep-Alive: {ping_err}")
                try: s.close() # Tvinga stängning direkt för att eliminera 90s Zombie-läge!
                except: pass
                break


    config.bot_joined_channel = False
    oserve_mod = sys.modules.get('oserve')
    if oserve_mod:
        oserve_mod.bot_joined_channel = False
    announce.is_ready = False
    import queue_mgr
    if "channel_announce" in queue_mgr.config.send_queue:
        queue_mgr.config.send_queue["channel_announce"] = []