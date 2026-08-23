# announce.py - Krockfri utgåva med sys.modules
import time
import os
import datetime
import threading
import sys
import collections
import config
import list
import dcc
import db
import stats_mgr

is_ready = False

# IRC lines are capped at 512 bytes INCLUDING the CRLF, and the server prepends
# ":nick!ident@host " when relaying to the channel - which counts against the same 512 for
# every recipient. Worst case on Undernet: 1 + nick(12) + 1 + ident(10) + 1 + host(63) + 1
# = 89 bytes of prefix, leaving 512 - 89 - 2 = 421 for what we send. Real hostmasks are
# usually far shorter, so this is deliberately the pessimistic figure: trimming a filename
# ourselves with an ellipsis is always better than the server cutting mid-colour-code, which
# smears background colour to the end of the line in the recipient's client.
IRC_LINE_BUDGET = 420


def fit_irc_line(build, value, budget=IRC_LINE_BUDGET):
    """Render build(value), shrinking `value` until the encoded line fits `budget` bytes.

    The line is re-rendered from the template on every attempt rather than being cut at the
    end, so a colour code can never be sliced in half. Byte length is measured, not
    character length - non-ASCII artist and album names cost 2-3 bytes each.
    """
    line = build(value)
    if len(line.encode("utf-8", errors="ignore")) <= budget:
        return line

    text = str(value)
    while text and len(build(text + "...").encode("utf-8", errors="ignore")) > budget:
        text = text[:-1]

    trimmed = build(text + "..." if text else "...")
    if len(trimmed.encode("utf-8", errors="ignore")) > budget:
        # The template's FIXED part alone exceeds the budget, so no amount of shrinking the
        # variable helps. Return it rather than looping; the caller's line is structurally
        # too long and that is a separate bug, not something to hang on.
        print(f"[IRC LINE] Fixed template exceeds {budget} bytes even with an empty field.")
    return trimmed


# Debug lines are produced by the IRC READ THREAD (security.check_user_status runs for every
# PRIVMSG) and by transfer threads. They used to be written straight to the socket with a
# blocking time.sleep(0.5) held under a lock, which stalled whoever called it: 30 banned
# nicks resuming after a netsplit meant 15 seconds during which the read loop answered no
# PING, parsed no NAMES and dispatched no request. They are now queued here and drained by
# one background thread, so every caller returns immediately.
# commands.py:283 runs importlib.reload on this module for every !rehash, and reload
# re-executes the module body in the SAME module dict. A plain assignment here would throw
# away every queued-but-unsent line - including the "Rehash triggered by ..." notice appended
# one statement earlier - so the existing deque is reused when one is already present.
_debug_queue = globals().get("_debug_queue") or collections.deque(maxlen=200)

# Generation token, same idea as current_worker_id below. A reload resets _debug_drain_started
# to False, so the next send_debug would start a SECOND drain while the first kept running
# against the module global - N rehashes leaving N+1 pumps, each on its own timer, multiplying
# the outbound rate on the one shared socket. A superseded drain now retires itself.
_debug_drain_id = 0
_debug_drain_started = False
_debug_drain_guard = threading.Lock()


def _debug_drain_worker(my_id):
    """Drain the debug queue at a steady pace, one line at a time."""
    while True:
        try:
            if _debug_drain_id != my_id:
                print("[DEBUG DRAIN] Superseded by a newer drain. Retiring quietly.")
                return

            if not _debug_queue:
                time.sleep(0.2)
                continue

            # Two gates, both required. The socket alone is not enough: irc.py publishes
            # oserve.irc_connection immediately after connect(), several seconds before the
            # debug channel has been joined, so draining on that signal alone would flush the
            # whole reconnect backlog into a window where the server rejects it - and those
            # are precisely the lines describing the outage.
            oserve_mod = sys.modules.get('oserve')
            irc_sock = getattr(oserve_mod, 'irc_connection', None)
            if not irc_sock or not getattr(config, 'bot_joined_channel', False):
                # Check BEFORE removing anything: popping first and discovering there is
                # nowhere to send is how reconnects silently ate queued messages.
                time.sleep(0.5)
                continue

            msg = _debug_queue.popleft()
            try:
                irc_sock.sendall(msg.encode("utf-8", errors="ignore"))
            except Exception as send_err:
                print(f"[DEBUG SEND ERROR] Could not write debug line: {send_err}")

            time.sleep(getattr(config, 'DEBUG_MSG_DELAY', 0.5))
        except Exception as drain_err:
            print(f"[DEBUG DRAIN ERROR] {drain_err}")
            time.sleep(1.0)


def _ensure_debug_drain():
    global _debug_drain_started, _debug_drain_id
    if _debug_drain_started:
        return
    with _debug_drain_guard:
        if _debug_drain_started:
            return
        _debug_drain_id = time.time()
        threading.Thread(target=_debug_drain_worker, args=(_debug_drain_id,), daemon=True).start()
        _debug_drain_started = True


def format_size_human(bytes_size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.1f}{unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.1f}PB"

def load_advanced_stats():
    """Format i stats.txt: total_files total_bytes yest_files yest_bytes today_files today_bytes last_date"""
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    default_stats = [0, 0, 0, 0, 0, 0, today_str]
    STATS_FILE = config.STATS_FILE 
    if not os.path.exists(STATS_FILE):
        return default_stats
    try:
        with open(STATS_FILE, "r") as f:
            parts = f.read().strip().split()
            if len(parts) == 7:
                return [int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5]), parts[6]]
            elif len(parts) == 2:
                return [int(parts[0]), int(parts[1]), 0, 0, 0, 0, today_str]
    except:
        pass
    return default_stats

def save_advanced_stats(stats):
    STATS_FILE = config.STATS_FILE 
    try:
        with open(STATS_FILE, "w") as f:
            f.write(f"{stats[0]} {stats[1]} {stats[2]} {stats[3]} {stats[4]} {stats[5]} {stats[6]}")
    except Exception as e:
        print(f"[STATS ERROR] Kunde inte spara stats.txt: {e}")

def check_and_rotate_day():
    stats = load_advanced_stats()
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    if stats[6] != today_str:
        stats[2] = stats[4]
        stats[3] = stats[5]
        stats[4] = 0
        stats[5] = 0
        stats[6] = today_str
        save_advanced_stats(stats)
    return stats

def send_transfer_complete(channel, user, file_name, file_size, start_time, actual_speed):
    """Skickar den färdigbakade, block-designade fildelningsnotisen när en fil är helt klar!"""
    import sys
    import db
    import stats_mgr
    import time
    import config
    oserve = sys.modules.get('oserve')
    
    # Hämta live-mätare från databasen och statistikmotorn
    total_sent = stats_mgr.get_total_sent()
    total_sent_bytes = stats_mgr.get_total_sent_bytes()
    total_sent_str = f"{total_sent} Files ({stats_mgr.format_size_human(total_sent_bytes)})"

    # Hämta statistik för igår och idag (Modul-säkrad för att förhindra krock med list.py!)
    yesterday_str = "0 Files"
    today_str = "0 Files"
    try:
        stats = db.load_advanced_stats()
        # RÄTTAD: Vi använder type(stats) == list för att helt undvika krock med list-modulen!
        if (type(stats) == list or type(stats).__name__ == 'list') and len(stats) > 6:
            yesterday_str = f"{str(stats[2])} Files"
            today_str = f"{str(stats[4])} Files"
    except Exception as e:
        print(f"[ANNOUNCE ERROR] Siffrorna krockade i minnet: {e}")

    speed_str = stats_mgr.format_speed(actual_speed) if actual_speed > 0 else "0k/s"
    current_time_str = time.strftime("%I:%M %p").lower().lstrip("0")
    
    # ---------------------------------------------------------------------
    # DITT CENTRALSTYRDA BLOCK-TEMA (Exakt kopia av din vackra kanalreklam!)
    # ---------------------------------------------------------------------
    BG_RED_BLOCK  = "\x0304,05" # Mörkröd kant
    BG_CYAN_BLOCK = "\x0310,10" # Turkos kant
    BG_TEXT_BOX   = "\x0301,00" # Svart text på VIT bakgrund
    R = "\x0f"                  # Total nollställning efter varje enskild sektion
    B = "\x02"                  # Fetstil för live-siffror och triggers
    
    def _build(shown_name):
        return (
        f"PRIVMSG {channel} :"
        f"{BG_RED_BLOCK} {BG_CYAN_BLOCK} {BG_TEXT_BOX} {B}{config.C_GREEN}Sent{B}{BG_TEXT_BOX}: {B}{shown_name}{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} To: {B}{config.C_GREEN}{user}{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Total Sent: {B}{config.C_GREEN}{total_sent_str}{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Yesterday: {B}{config.C_RED}{yesterday_str}{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Today: {B}{config.C_RED}{today_str}{B} {config.C_ROYAL_BLUE}[as of {current_time_str}] "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Speed: {B}{config.C_GREEN}{speed_str}{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} \r\n"
        )
    

    # The filename is the only unbounded field here and it comes straight off the disk. A
    # long classical track name pushed this line past 512 bytes and the server truncated it
    # mid-colour-code, so the channel saw the announcement smear into background colour.
    msg = fit_irc_line(_build, file_name)
    if oserve:
        oserve.queue_message("channel_announce", msg)
    print(f"[ANNOUNCE] Sent block transfer complete notice to {channel} for {user} ({speed_str})")

    # 🧼 RÄTTAD OCH ULTRA-SLIMMAD SLUTRAD: Använder din levande 'speed_str' helt kraschsäkert!
    try:
        safe_file = str(file_name)
        send_debug(f"Sent: \"{safe_file}\" to {user} [{speed_str}]", category="INFO")
    except Exception as debug_err:
        print(f"[DEBUG-SENT ERROR] Kunde inte skicka slutnotis till debug-kanal: {debug_err}")

def send_dcc_sending_notice(user, file_name):
    """Skickar en matchande privat NOTICE till användaren när överföringen startar eller köas!"""
    import sys
    oserve = sys.modules.get('oserve')
    
    # ---------------------------------------------------------------------
    # PRIVAT NOTICE-BLOCK: Matchad med exakt samma färgblocks-ram!
    # ---------------------------------------------------------------------
    BG_RED_BLOCK  = "\x0304,05" 
    BG_CYAN_BLOCK = "\x0310,10" 
    BG_TEXT_BOX   = "\x0301,00" 
    R = "\x0f"                  
    B = "\x02"                  
    
    msg = (
        f"NOTICE {user} :"
        f"{BG_RED_BLOCK} {BG_CYAN_BLOCK} {BG_TEXT_BOX} Sending: {B}{file_name}{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Status: {B}{config.C_GREEN}Active Transfer Started{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} \r\n"
    )
    
    if oserve:
        oserve.queue_message(user, msg, is_vip=True)
    print(f"[ANNOUNCE] Sent custom block notice to {user} for '{file_name}'")

def get_formatted_stats_strings():
    """Helt passiv: Läser bara av värdena från stats.txt till kanalreklamen utan att rotera!"""
    oserve = sys.modules.get('oserve')
    
    # RÄTTAT: Vi bara LÄSER från databasen, vi rör inte datumen här!
    stats = db.load_advanced_stats()
    
    # Index 1 = Total bytes
    total_bytes_count = stats[1]
    
    # Index 0 = Total filer, Index 2 = Gårdagens filer, Index 4 = Dagens filer
    total_str = f"{stats[0]:,} Files ({format_size_human(total_bytes_count)})"
    yesterday_str = f"{stats[2]} Files"
    
    time_now_str = time.strftime("%I:%M %p").lower()
    if time_now_str.startswith("0"):
        time_now_str = time_now_str[1:]
        
    today_str = f"{stats[4]} Files [as of {time_now_str}]"
    return total_str, yesterday_str, today_str

# Globala variabler for levande trafikstatistik (Mäts i realtid via dcc.py)
# Global variabel som lagrar ID:t på den tråd som faktiskt har rätt att köra just nu
current_worker_id = 0

def announce_worker():
    """Fristående tidstråd för OmenServe-reklam - 100% återanslutningssäker!"""
    global current_worker_id
    import time
    import sys
    import config
    
    # Skapa ett unikt ID för just denna trådstart baserat på nuvarande klockslag
    my_worker_id = time.time()
    current_worker_id = my_worker_id
    
    print(f"[ANNOUNCE] Multi-channel announce worker started (Thread ID: {my_worker_id}).")
    
    while True:
        try:
            # 🛡️ TRÅDDÖDARE: Om en återanslutning har skapat en nyare tråd, stäng ner denna direkt!
            if current_worker_id != my_worker_id:
                print(f"[REKLAM-DÖD] Gamla spöktråden (ID: {my_worker_id}) stänger ner sig själv i tystnad.")
                break
                
            if is_ready:
                channels_to_spam = config.CHANNEL.split(",")
                # DYNAMIC LIVE SPEED. Computed ONCE per advert cycle, above the per-channel
                # loop. Inside the loop it ran six times a cycle, and with a delta window
                # the 2nd-6th passes measured a few milliseconds each - either reporting
                # nothing or, if a pass took over a second, a wildly inflated figure.
                #
                # tx["bytes_sent"] is a LIFETIME counter that accumulates from the DCC
                # accept, so dividing it by a window that starts at first observation
                # over-reports by up to 2x. Rate the DELTA between observations instead:
                # bytes moved since the last cycle, over the time since the last cycle.
                speed_bytes_per_sec = 0
                speed_contributors = 0
                _now = time.time()
                with config.queue_lock if hasattr(config, "queue_lock") else threading.Lock():
                    for tx in config.active_transfers:
                        b_sent = tx.get("bytes_sent", 0)
                        prev_bytes = tx.get("_speed_bytes")
                        prev_time = tx.get("_speed_time")
                        tx["_speed_bytes"] = b_sent
                        tx["_speed_time"] = _now

                        if prev_time is None:
                            # First sighting: no window to measure yet.
                            continue

                        window = _now - prev_time
                        if window < 1.0:
                            continue

                        moved = b_sent - prev_bytes
                        if moved > 0:
                            speed_bytes_per_sec += int(moved / window)
                            speed_contributors += 1

                # Average across the transfers that actually contributed, not across every
                # active slot - a slot skipped for lack of a window must not drag the mean.
                if speed_bytes_per_sec > 0 and speed_contributors > 0:
                    speed_str = stats_mgr.format_speed(int(speed_bytes_per_sec / speed_contributors))
                else:
                    speed_str = "0k/s"

                for chan in channels_to_spam:
                    chan = chan.strip()
                    if not chan:
                        continue
                        
                    # Hämta live-data i exakt denna millisekund
                    file_count, list_date, total_size, raw_bytes = list.get_file_count_date_size_and_raw_bytes()
                    formatted_count = f"{file_count:,}"
                    
                    oserve = sys.modules.get('oserve')
                    active_dl = oserve.active_downloads if oserve else 0
                    fails_count = oserve.send_fails_count if oserve else 0
                    

                    free_slots = max(0, config.MAX_DCC_SLOTS - active_dl)
                    queue_status = "NOW" if active_dl < config.MAX_DCC_SLOTS else "0"
                    queued_count = dcc.get_total_queued_count()
                    queued_str = f"{queued_count}"
                    
                    total_sent_str, yesterday_str, today_str = get_formatted_stats_strings()
                    slots_str = f"{free_slots}/{config.MAX_DCC_SLOTS}"
                    
                    import db
                    raw_record = db.get_speed_record()
                    record_str = stats_mgr.format_speed(raw_record) if raw_record > 0 else "0k/s"

                    BG_RED_BLOCK  = "\x0304,05"
                    BG_CYAN_BLOCK = "\x0310,10"
                    BG_TEXT_BOX   = "\x0301,00"
                    R = "\x0f"
                    B = "\x02"

                    announce_msg = (
                        f"PRIVMSG {chan} :"
                        f"{BG_RED_BLOCK} {BG_CYAN_BLOCK} {BG_TEXT_BOX} Type: {B}{config.C_GREEN}@{config.NICKNAME}{R}{BG_TEXT_BOX} For My List Of: {B}{config.C_RED}{formatted_count}{R}{BG_TEXT_BOX} Files ({total_size}) created {config.C_GREEN}{list_date} "
                        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Slots: {slots_str} "
                        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Queued: {queued_str} "
                        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Speed: {speed_str} / Record: {record_str} "
                        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Total Sent: {total_sent_str} "
                        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Search: {B}{config.C_GREEN}ON{R}{BG_TEXT_BOX} "
                        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} {config.SCRIPT_VERSION} {BG_CYAN_BLOCK} {BG_RED_BLOCK} \r\n"
                    )

                    if oserve:
                        oserve.queue_message("channel_announce", announce_msg)
                    
                    raw_stats_bytes = stats_mgr.get_total_sent_bytes()
                    sent_mbs = int(raw_stats_bytes / 1024 / 1024)
                    
                    ctcp_payload = f"SLOTS {config.MAX_DCC_SLOTS} {free_slots} {queue_status} {queued_count} 999 {int(speed_bytes_per_sec)} {file_count} {raw_bytes} {fails_count} {sent_mbs} {raw_stats_bytes} {config.SCRIPT_VERSION}"
                    ctcp_msg = f"PRIVMSG {chan} :\x01{ctcp_payload}\x01\r\n"
                    if oserve:
                        oserve.queue_message("channel_announce", ctcp_msg)
                
                time.sleep(config.ANNOUNCE_INTERVAL)
            else:
                time.sleep(5)
                
        except Exception as loop_error:
            print(f"[CRITICAL ANNOUNCE ERROR] Tråden stötte på ett fel: {loop_error}")
            time.sleep(10)


def send_search_result_header(user, search_term, match_count, channel):
    """Skickar sök-headern i privat PM - Nu med både färgblock och din vita text-box!"""
    import sys
    import dcc
    import config
    oserve = sys.modules.get('oserve')
    
    active_dl = oserve.active_downloads if oserve else 0
    free_slots = max(0, config.MAX_DCC_SLOTS - active_dl)
    queued_count = dcc.get_total_queued_count()
    sending_count = min(match_count, config.MAX_SEARCH_RESULTS)
    
    # DITT LYX-TEMA MED DEN VITA TEXT-BOXEN
    BG_RED_BLOCK  = "\x0304,05" 
    BG_CYAN_BLOCK = "\x0310,10" 
    BG_TEXT_BOX   = "\x0301,00" # Din vita bakgrundsplatta!
    R = "\x0f"                  
    B = "\x02"                  
    
    def _build(shown_term):
        return (
        f"PRIVMSG {user} :"
        f"{BG_RED_BLOCK} {BG_CYAN_BLOCK} {BG_TEXT_BOX} Search Result: {B}{config.C_GREEN}ON{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Found: {B}{config.C_RED}{match_count}{B} Match(es) For {B}{config.C_GREEN}{shown_term}{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Sending: {B}{config.C_RED}{sending_count}{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Slots: {B}{config.C_GREEN}{free_slots}/{config.MAX_DCC_SLOTS}{B} Free "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Queued: {B}{config.C_GREEN}{queued_count}{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} \r\n"
        )
    

    # search_term is supplied by any user in the channel and is only checked for a MINIMUM
    # length, so a long one pushed this private notice past 512 bytes and the server cut it
    # mid-colour-code.
    msg = fit_irc_line(_build, search_term)
    if oserve:
        oserve.queue_message("channel_announce", msg)
    print(f"[SEARCH RESULTS] Found {match_count} sending {sending_count} to {user} in {channel} for '{search_term}'")

def send_dcc_error(user, error_type):
    """Skickar standardiserade DCC-felmeddelanden till användaren"""
    oserve = sys.modules.get('oserve')
    errors = {
        "invalid_path": "Error: Invalid path.",
        "file_not_found": "Error: File not found.",
        "global_full": f"Error: The server's global queue is full ({config.MAX_GLOBAL_QUEUE} max).",
        "user_full": f"Error: You have reached your personal queue limit of {config.MAX_USER_QUEUE} files."
    }
    msg_text = errors.get(error_type, "Error: Unknown transfer issue.")
    msg = f"NOTICE {user} :{config.C_BOLD}{msg_text}{config.C_RESET}\r\n"
    if oserve:
        oserve.queue_message(user, msg)

def send_dcc_queue_notice(user, file_name, position):
    """Skickar köplatser till användaren privat, perfekt inramad i ditt lyxiga färgtema!"""
    import sys
    oserve = sys.modules.get('oserve')
    if oserve:
        # Dina exakta mIRC-färgblock och stolpar
        BG_RED_BLOCK  = "\x0304,05" # Mörkröd kant
        BG_CYAN_BLOCK = "\x0310,10" # Turkos kant
        BG_TEXT_BOX   = "\x0301,00" # Svart text på VIT bakgrund
        R = "\x0f"                  # Total nollställning
        
        # Vi bygger meddelandet i din exakta färgblocks-stil!
        text_content = f"Added {file_name} to your personal queue at position #{position} of 100."
        block_msg = f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} {text_content}{R} {BG_CYAN_BLOCK} {BG_RED_BLOCK} "
        
        result_msg = f"NOTICE {user} :{block_msg}\r\n"
        oserve.queue_message(user, result_msg)


def send_debug(msg_text, category="INFO"):
    """Skickar en färgblocks-designad loggrad live till #flac-debug via en RÅ socket-send på 0ms!"""
    import sys
    import time
    import config
    
    current_time = time.strftime("%H:%M:%S")
    
    # ---------------------------------------------------------------------
    # DITT UTÖKADE BLOCK-TEMA (Kritvit bakgrund + mIRC färg- och krockskydd!)
    # ---------------------------------------------------------------------
    BG_RED_BLOCK  = "\x0304,05" # Mörkröd kant
    BG_CYAN_BLOCK = "\x0310,10" # Turkos kant
    BG_TEXT_BOX   = "\x0301,00" # Svart text på KRITVIT bakgrund
    R = "\x0f"                  # Total nollställning
    B = "\x02"                  # Fetstil
    
    # 1. Start-blocket (Tidstämpel inramad i vitt)
    msg = f"PRIVMSG {config.DEBUG_CHANNEL} :{BG_RED_BLOCK} {BG_CYAN_BLOCK} {BG_TEXT_BOX} [{current_time}] {B}DEBUG{B} "
    
    # 2. TAGG-blocket (Färgkodad baserat på händelse)
    if category.upper() == "SENT":
        tag_str = f"{config.C_GREEN}[SENT]{R}{BG_TEXT_BOX}"
    elif category.upper() == "PART":
        tag_str = f"{config.C_RED}[PART]{R}{BG_TEXT_BOX}"
    elif category.upper() == "QUIT":
        tag_str = f"{config.C_PURPLE}[QUIT]{R}{BG_TEXT_BOX}"
    elif category.upper() == "JOIN":
        tag_str = f"{config.C_CYAN}[JOIN]{R}{BG_TEXT_BOX}"
    elif category.upper() == "BAN":
        # NYTT: Stensnygg röd block-etikett för PERMANENTA bans!
        tag_str = f"{config.C_RED}[HARDBAN]{R}{BG_TEXT_BOX}"
    elif category.upper() == "HARDBAN":
        # dcc.py raises this for a blocked path traversal and for a poisoned queue entry -
        # the two most serious alerts the daemon can produce. Without this branch they fell
        # through to the grey [INFO] tag, visually identical to routine chatter, so a
        # filesystem probing campaign looked like ordinary traffic in the debug channel.
        #
        # Labelled [SECURITY], not [HARDBAN]: the "BAN" category above already renders
        # [HARDBAN], and that one is an admin confirming a !ban. These two must not look
        # alike - one is routine administration, the other is someone probing the filesystem.
        tag_str = f"{config.C_RED}[SECURITY]{R}{BG_TEXT_BOX}"
    elif category.upper() == "TBAN":
        # NYTT: Stensnygg lila block-etikett för TEMPORÄRA dags-bans!
        tag_str = f"{config.C_PURPLE}[TEMPBAN]{R}{BG_TEXT_BOX}"
    else:
        tag_str = f"{config.C_GREY}[INFO]{R}{BG_TEXT_BOX}"
  
    msg += f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} {B}Category{B}: {tag_str} "
    
    # 3. TEXT-blocket (Rensat från gamla krockande färgkoder för en spikrak lina)
    clean_text = msg_text.replace(config.C_BOLD, "").replace(config.C_RESET, "").replace("\x02", "").replace("\x0f", "")
    msg += f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Log: {clean_text} "
        
    # 4. Slut-blocket (Stänger raden snyggt med färgstolpar)
    msg += f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {R}\r\n"
    
    # ---------------------------------------------------------------------
    # NON-BLOCKING HAND-OFF. This used to hold config.debug_flood_lock across a
    # time.sleep(0.5) and write the socket directly, so every caller paid 0.5s - including
    # the IRC read thread, which calls this once per denied PRIVMSG. The queue is bounded
    # (deque maxlen), so a flood of alerts drops the oldest lines instead of growing without
    # limit or stalling the daemon. sendall() in the drain replaces send(), whose return
    # value was discarded so a short write truncated the line silently.
    _debug_queue.append(msg)
    _ensure_debug_drain()

# ---------------------------------------------------------------------
# START-SLUSS FÖR REKLAMTRÅDEN: Anropas av irc.py vid lyckad boot!
# ---------------------------------------------------------------------
is_ready = False

def start_announce_thread():
    """Startar den fristående bakgrundsklockan för kanalreklam och dolda SLOTS i en egen tråd"""
    import threading
    threading.Thread(target=announce_worker, daemon=True).start()
    print("[ANNOUNCE] Reklam- och debug-klockan startades live i bakgrunden.")

def send_pack_error_notice(irc_sock, user):
    """Skickar en privat NOTICE till användaren i exakt samma lyxiga färgblocks-tema vid root-spärr!"""
    import config
    import sys
    
    # Vi hämtar dina exakta färgkoder från din befintliga struktur
    BG_RED_BLOCK  = "\x0304,05" 
    BG_CYAN_BLOCK = "\x0310,10" 
    BG_TEXT_BOX   = "\x0301,00" 
    B = "\x02"                  
    
    # Vi bygger meddelandet i din officiella ram
    msg = (
        f"NOTICE {user} :"
        f"{BG_RED_BLOCK} {BG_CYAN_BLOCK} {BG_TEXT_BOX} DCC-PACK: {B}Access Denied{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} {BG_TEXT_BOX} Error: {B}Artist root folders cannot be requested. Please select a specific album sub-folder.{B} "
        f"{BG_CYAN_BLOCK} {BG_RED_BLOCK} \r\n"
    )
    
    try:
        # Vi slussar ut den direkt på 0ms via oserve-motorn om den är laddad
        oserve = sys.modules.get('oserve')
        if oserve:
            oserve.queue_message(user, msg, is_vip=True)
        else:
            irc_sock.send(msg.encode())
    except Exception as e:
        print(f"[DCC NOTICE ERROR] Kunde inte slussa färgblocks-felmeddelande: {e}")
