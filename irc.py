# =====================================================================
# IRC.PY - THE IRC NETWORK MODULE FOR UNDERNET (PART 1 OF 3)
# =====================================================================
import socket
import threading
import time
import re
import sys
import os
import traceback
import urllib.request

# The bot's own modules
import config
import platform_compat
import list
import dcc
import runtime
import security

# Tracks whether the channels have been joined
bot_joined_channel = False


def _release_socket():
    """Clear the shared network reference as soon as a socket has closed.

    The queue_mgr pump and announce.send_debug both check `if current_sock:` before
    writing. Without this reset the reference still pointed at a CLOSED socket for
    the whole reconnect, so those guards did nothing and every write raised
    OSError for no reason.
    """
    import sys
    oserve_mod = sys.modules.get('oserve')
    if oserve_mod:
        oserve_mod.irc_connection = None


def is_server_numeric(line, code):
    """True only when `line` is a genuine server numeric with this code.

    A server numeric is always ':<prefix> <code> <target> ...'. Testing for the
    bare substring instead lets any user forge one by typing it in a channel,
    because the read loop sees every PRIVMSG as raw text before the PRIVMSG
    parser and the ban check ever run.

    That is not theoretical for this bot. `"001" in line` matched an ordinary
    music request - "!DCCore 001 - Enter Sandman.flac" - and `" 513 " in line
    and "PONG" in line` let anyone make the daemon emit unthrottled raw PONGs
    straight to the socket, bypassing queue_mgr's pacing entirely.
    """
    return re.match(r"^:\S+\s+" + code + r"\s+\S+", line) is not None


def is_user_event(line, command):
    """True only when `line` is a genuine user event with this command.

    A user event is always ':<nick>!<user>@<host> <COMMAND> ...' - the command
    sits in the command position, immediately after the prefix. Testing for the
    bare substring instead matches the command name ANYWHERE, including inside a
    PRIVMSG body, a PART or QUIT reason, or a channel TOPIC.

    That was reachable by accident, not only by attack: `" QUIT " in line` matched
    an ordinary search like "@find QUIT PLAYING GAMES", and the QUIT handler then
    removed the searcher from every channel in config.channel_users - which
    freezes their queue and hands it to the five-minute delete timer, for the
    crime of looking for a song.
    """
    return re.match(r"^:\S+!\S+\s+" + command + r"(\s|$)", line) is not None


def event_source_nick(line):
    """The nick a user event came FROM, read out of the prefix.

    Never search the whole line for a nick. A QUIT reason is free text, so
    ':attacker!u@h QUIT :bye :DCCore!x@y' made `":DCCore!" in line.lower()` true
    and handed anyone the handlers that key off who an event came from.
    """
    match = re.match(r"^:([^!\s]+)!", line)
    return match.group(1).lower() if match else None


def event_source_host(line):
    """The host a line came FROM, read out of the prefix. Lowercased, or None.

    Anchored at the start for the same reason as event_source_nick: a message
    body is free text, and a hostmask quoted inside one must never be mistaken
    for the sender's own.

    This is what makes the admin console possible. On Undernet a user who has
    logged into X and set +x is given the host "<account>.users.undernet.org",
    which only the server can issue - so the host, unlike the nick, is proof of
    who someone is. The PRIVMSG parser below used to discard it.
    """
    match = re.match(r"^:[^!\s]+!\S*@(\S+)", line)
    return match.group(1).lower() if match else None


# Anchored to the START of the line, deliberately.
#
# Every bot that answers an @find replies with a header line and then one
# line per match, and the HEADER also contains a "!" token - it is telling
# the user what to type:
#
#   Search Result 1 Match For X   Copy And Paste !Vibessono FILENAME To ...
#   !Vibessono 50 Oldies Party - ... .mp3  ::INFO:: 4.6MB
#
# Searching anywhere in the line matched the header too, and produced a
# Download button for a file literally named "FILENAME To The Channel To
# Request. (25/25) Free Slots...". Ordinary channel chatter arriving during
# the window was worse: "Thank You !!! I have now received 1 file(s)..."
# yielded bot="!!" and offered to fetch from it.
#
# Both would have sent a nonsense "!" request into a live channel on click.
# A real result line always begins with its token; a sentence mentioning one
# never does.
_FETCH_TOKEN_RE = re.compile(r'^!(\S+)\s+(.+)$')


# ---------------------------------------------------------------------
# Reading the HEADER line other bots send before their matches
# ---------------------------------------------------------------------
# An @find reply arrives as a header line and then one line per match. The
# header is not a result - it is the bot introducing itself - and it carries
# the things worth showing above a group of results: how many matches it
# found, how busy it is, and what it runs.
#
# Two families answer in these channels.
#
# OmenServe (v2.60 and v2.71 both seen) is the common one:
#
#   Search Result 3 Matches For X  Copy And Paste !bot FILENAME To The
#   Channel To Request. (4/4) Free Slots, 0 Queued OmeNServE v2.60
#
# Operators pick their own separator between sections - ":", "~", "*", or
# none at all - so nothing below anchors on punctuation, only on the words.
# When a search matches more than it will send, it says so instead of
# listing slots:
#
#   Search Result 12 Matches For X   Get My List Of 94,952 Files By Typing
#   @Beezer In The Channel Or Refine Your Search. Sending first 5 Results
#
# SPQR is an older, less widely used mIRC script - a minority of operators
# still run it. Different shape, no version string, no match count, and its
# RESULT lines carry no "::INFO:: <size>" tag either:
#
#   Matches for *X*  Copy and Paste in Channel to Request a File
#   (Slot:0/) (Que:0/16) in Use
#
# Anything unrecognised returns None rather than a wrong guess. The grouped
# view falls back to counting the result lines actually received, which
# works for every family including ones nobody here has seen.
_HDR_MATCHES    = re.compile(r'(\d[\d,]*)\s+Match(?:es)?\b', re.I)
_HDR_SLOTS      = re.compile(r'\((\d+)\s*/\s*(\d+)\)\s*Free\s*Slots', re.I)
_HDR_QUEUED     = re.compile(r'(\d+)\s+Queued\b', re.I)
_HDR_SERVER     = re.compile(r'\b(Omen\s*Serve?\s*v?\s*[\d.]+)', re.I)
_HDR_LIST_SIZE  = re.compile(r'List\s+Of\s+([\d,]+)\s+Files', re.I)
_HDR_SENDING    = re.compile(r'Sending\s+first\s+(\d+)', re.I)
_HDR_SPQR_SLOTS = re.compile(r'\(Slot:\s*(\d+)\s*/\s*(\d*)\)', re.I)
_HDR_SPQR_QUEUE = re.compile(r'\(Que:\s*(\d+)\s*/\s*(\d+)\)', re.I)


def _as_int(text):
    """"94,952" -> 94952. Returns None for anything that is not a number."""
    try:
        return int(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_search_header(text):
    """Stats out of another bot's @find header line, or None if it is not one.

    Returns a dict with whatever that family actually publishes - callers
    must treat every key as optional. A missing key means "this bot did not
    say", never "zero": showing 0 free slots for a bot that simply does not
    report them would be worse than showing nothing.
    """
    if not text:
        return None

    # A RESULT line is never a header, even though it often ends in the same
    # version string inside its "::INFO:: 4.6MB OmeNServE v2.60" tail. Gating
    # here rather than at the call site means the function cannot be misused
    # into decorating a result row with the sender's stats.
    if _FETCH_TOKEN_RE.search(text):
        return None

    stats = {}

    slots = _HDR_SLOTS.search(text)
    if slots:
        stats["family"] = "omenserve"
        stats["slots_free"] = _as_int(slots.group(1))
        stats["slots_total"] = _as_int(slots.group(2))
    else:
        spqr = _HDR_SPQR_SLOTS.search(text)
        if spqr:
            stats["family"] = "spqr"
            # SPQR reports slots IN USE, and often leaves the total blank -
            # the opposite sense from OmenServe's "free". Kept as its own
            # key so the frontend never has to guess which one it holds.
            stats["slots_in_use"] = _as_int(spqr.group(1))
            total = _as_int(spqr.group(2))
            if total is not None:
                stats["slots_total"] = total

    queued = _HDR_QUEUED.search(text)
    if queued:
        stats["queued"] = _as_int(queued.group(1))
    else:
        spqr_q = _HDR_SPQR_QUEUE.search(text)
        if spqr_q:
            stats["family"] = stats.get("family", "spqr")
            stats["queued"] = _as_int(spqr_q.group(1))
            stats["queue_total"] = _as_int(spqr_q.group(2))

    server = _HDR_SERVER.search(text)
    if server:
        stats["server"] = " ".join(server.group(1).split())
        stats.setdefault("family", "omenserve")

    matches = _HDR_MATCHES.search(text)
    if matches:
        stats["matches"] = _as_int(matches.group(1))

    list_size = _HDR_LIST_SIZE.search(text)
    if list_size:
        stats["list_size"] = _as_int(list_size.group(1))

    sending = _HDR_SENDING.search(text)
    if sending:
        # "12 Matches ... Sending first 5 Results" - it found more than it
        # sent, which is worth saying above the group so the operator knows
        # to refine rather than assuming five is all there is.
        stats["sending"] = _as_int(sending.group(1))

    return stats or None


def _capture_broadcast_search_reply(user, target, msg):
    """Cross-bot search broadcast capture (webserver.py's POST
    /api/search/broadcast starts the window this reads).

    Only fires for a line sent DIRECTLY TO OUR OWN NICK (`target`), never
    channel chatter - a PRIVMSG or NOTICE-to-self, which is how @find-style
    replies normally arrive (list.execute_search() itself only ever replies
    to the requester, never the channel). No-ops instantly outside an open
    broadcast window, so this costs nothing on the overwhelmingly common case
    of no broadcast in progress.

    Deliberately bypasses security.is_flooding(): that gate exists to meter
    OUR users against OUR command surface. Applying it here would mean the
    daemon could start ignoring or muting a foreign bot we explicitly asked
    to reply, for the crime of answering the broadcast we just sent it -
    quite apart from the fact that this capture never dispatches a command or
    a reply of our own, so there is nothing here for a flood gate to protect.
    """
    if str(target).lower() != str(getattr(config, 'NICKNAME', '')).lower():
        return
    if not getattr(config, 'broadcast_search_inprogress', False):
        return
    if time.time() >= getattr(config, 'broadcast_search_deadline', 0):
        return

    cleaned = list.strip_control_codes(msg)
    entry = {"from": user, "text": cleaned, "received_at": time.time()}

    # Best-effort extraction of the one convention that is actually
    # standardised across file-sharing bots: "!<botnick> <filename>", the
    # same syntax this bot itself answers to (see get_bot_aliases() and
    # dcc.handle_download_request()). Deliberately does NOT attempt to parse
    # size, format, or anything else out of arbitrary bots' bespoke
    # colour/box formatting - that is unrealistic and out of scope. If no
    # such token is found, the raw cleaned text is still recorded above; the
    # frontend just shows it without a Download button.
    token_match = _FETCH_TOKEN_RE.search(cleaned)
    if token_match:
        entry["bot"] = token_match.group(1)
        # A reply that is itself a master-list line - the overwhelmingly
        # common case, since this is what most file bots echo back for a
        # match - carries a trailing "  ::INFO:: <size>" tag. Strip it here
        # (list.strip_info_suffix(), the same split update_list.py's own
        # writer/reader pair uses) so the stored filename is the bare name a
        # later `!<bot> <filename>` request can actually be answered against -
        # the real DCC SEND offer that comes back never includes the size tag,
        # so leaving it in place made every such fetch fail admission control
        # as "unsolicited" (filenames never matched).
        filename, _size = list.strip_info_suffix(token_match.group(2).strip())
        entry["filename"] = filename
    else:
        # Not a result, so it may be the header the bot sends before its
        # matches - the one line that says how many it found, how busy it is
        # and what it runs. Parsed here, once, rather than in the dashboard on
        # every poll. None for anything unrecognised, which is most channel
        # chatter that happens to land inside the window.
        header = parse_search_header(cleaned)
        if header:
            entry["header"] = header

    # config.broadcast_search_results is bound from runtime.py at import time
    # and always exists as a real list - never rebind it, see runtime.py's
    # docstring.
    config.broadcast_search_results.append(entry)


def get_bot_aliases():
    """Every name this bot should answer to, lowercased, current nick first.

    config.NICKNAME, config.ORIGINAL_NICK and config.LIST_BASE_NAME are normally the same
    string, so this collapses to ONE entry and the triggers built from it are byte-identical
    to the previous hardcoded ones. They only diverge after a 433 nick collision.

    That divergence is why this exists. The master list is generated by update_list.py as a
    SUBPROCESS, so its request lines are always stamped with the config.py default nick
    (update_list.py:156). The dispatcher matched only the LIVE nick, so while the bot was
    running as the alternate nick every pasted "!DCCore Song.flac" was dropped with no reply,
    no error and no log line - and the flood counter never even saw it. Users had no way to
    tell the bot was ignoring them.

    DELIBERATELY USED FOR ONE TRIGGER ONLY: the "!<nick> <file>" request, whose text comes
    from a list the user cannot be expected to retype. Everything typed live - "@<nick>",
    "@<nick>-que", "@<nick>-remove" - stays on the LIVE nick, because the advert publishes
    the live nick (announce.py:247) so the user can see what to type. Widening those would
    make this bot act on messages addressed to whichever client currently holds the main
    nick, and for "-remove" that means destroying the queue of someone who was talking to
    somebody else.

    config.LIST_BASE_NAME is intentionally NOT included: it is a filename constant, and it
    should not become a live public IRC trigger. ORIGINAL_NICK already covers the real case,
    since the subprocess stamps the list with the config.py default nick.
    """
    aliases = []
    for candidate in (getattr(config, 'NICKNAME', None),
                      getattr(config, 'ORIGINAL_NICK', None),
                      getattr(config, 'PREVIOUS_NICK', None)):
        if not candidate:
            continue
        low = str(candidate).strip().lower()
        if low and low not in aliases:
            aliases.append(low)
    return aliases


def irc_loop():
    """The connection, PING/PONG, and every incoming PRIVMSG from Undernet."""
    global bot_joined_channel
    import announce
    oserve = sys.modules.get('oserve')
    
    print("[IP CHECK] Fetching the public address from the ipify API...")
    try:
        req = urllib.request.Request(
            "https://api.ipify.org", 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        config.MY_IP_OR_DOCK = urllib.request.urlopen(req, timeout=5.0).read().decode("utf-8").strip()
        print(f"[IP CHECK] IP-identifiering klar! DCC IP satt till: {config.MY_IP_OR_DOCK}")
    except Exception as e:
        print(f"[WARNING] Could not reach the ipify API ({e}). Falling back to 127.0.0.1")
        config.MY_IP_OR_DOCK = "127.0.0.1"
    
     # ---------------------------------------------------------------------
    
    # Set HERE before anything else, so the variable always exists in memory
    if not hasattr(config, 'ORIGINAL_NICK'):
        config.ORIGINAL_NICK = getattr(config, 'NICKNAME', 'DCCore')

    # Per-connection epoch. Threads spawned for one connection must not act on a later
    # one; they compare this token before touching any shared state.
    if not hasattr(config, 'connection_epoch'):
        config.connection_epoch = 0

    # THE RECONNECT LOOP: makes sure this thread never dies on a split or a disconnect
    while True:
        # Every connection starts by trying to claim the bot's real original nick
        config.NICKNAME = config.ORIGINAL_NICK

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(70.0)
        
        print(f"[CONNECT] Attempting to connect to {config.SERVER}:{config.PORT} as {config.NICKNAME}...")
        
        try:
            s.connect((config.SERVER, config.PORT))
            # The three timing knobs are Linux-only; platform_compat guards them so
            # Windows still gets SO_KEEPALIVE with system defaults.
            platform_compat.apply_keepalive(s, idle=10, interval=2, count=3)
                
            oserve_mod = sys.modules.get('oserve')
            if oserve_mod:
                oserve_mod.irc_connection = s
            print(f"[CONNECT] Connected to socket successfully!")
        except Exception as e:
            print(f"[ERROR] Connection failed: {e}. Reconnecting in 10 seconds...")
            # `joined` lives in the irc_loop frame and is only reset after a SUCCESSFUL
            # handshake, so a failed attempt used to leave it latched True from the
            # previous connection - which is what let a stale watchdog pass its guard.
            joined = False
            time.sleep(10)
            continue
            
        # Send the handshake immediately; the server decides the nick via real 433 replies
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
                        print(f"[SERVER 433] The nick {config.NICKNAME} was taken. Switching CURRENT_NICK to: {alt_nick}")
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
                
            print(f"[INFO] Handshake complete. CURRENT_NICK settled as: {config.NICKNAME}. Starting the reader...")

            # SHORT RECV TIMEOUT (see the clock logic below): recv() lets go every
            # 20 seconds so we can run the keepalive and the silence timer ourselves.
            # The old 70-second timeout tore down HEALTHY links during quiet periods.
            s.settimeout(20.0)
        except Exception as auth_err:
            print(f"[ERROR] Fel under server-handskakningen: {auth_err}")
            try: s.close()
            except: pass
            _release_socket()
            joined = False
            # BACKOFF: without a pause here the loop spun as fast as TCP could connect.
            # Undernet's connection throttle closes the link immediately when you come
            # back too fast, which gave tens of attempts a second and guaranteed we
            # stayed throttled - or got K-lined - instead of recovering.
            time.sleep(10)
            continue

        # This connection is now live: claim a fresh epoch. Any thread still running from
        # a previous connection holds an older token and will bail out on its next check.
        config.connection_epoch += 1
        my_epoch = config.connection_epoch

        buffer = ""
        joined = False
        bot_joined_channel = False
        announce.is_ready = False
        
        # FIXED (issue #9): tracks WHICH channels were confirmed by 366, not just
        # a loose count. The old code counted every 366 line it saw, including the
        # debug channel's, so the threshold could be reached even if a real channel
        # never answered - and if two or more failed the threshold was never reached,
        # which silenced ALL advertising permanently, with no error message.
        target_channels = set(c.strip().lower() for c in config.CHANNEL.split(",") if c.strip())
        channels_confirmed = set()
        ACTIVATION_TIMEOUT = 20.0  # seconds after JOIN before giving up on the rest
        last_recv_time = time.time()

        # ---------------------------------------------------------------------
        # TWO SEPARATE CLOCKS, replacing the old 45s check plus 70s timeout:
        #
        # The old keepalive check sat at the top of the loop, but the loop was blocked
        # in recv() for up to 70 seconds. So recv() ALWAYS timed out before the 45
        # seconds could be checked - the keepalive PING was never reachable, and a
        # quiet channel was torn down as though the link were dead. The bot
        # reconnected, rejoined seven channels, cleared channel_users and restarted
        # the advert thread - over and over, for as long as the channels were quiet.
        #
        # Silence is now measured separately from the keepalive: recv() lets go every
        # 20 seconds, we PING the server after 45s of silence, and only tear the link
        # down if the server has not been heard from for SILENCE_LIMIT. Genuinely dead
        # links are still caught by TCP_KEEPIDLE/INTVL/CNT above, in about 16 seconds.
        # ---------------------------------------------------------------------
        SILENCE_LIMIT = 180.0    # only at this point is the link considered dead
        KEEPALIVE_AFTER = 45.0   # this much silence is allowed before we PING
        last_ping_sent = 0.0

        # HOISTED (issue #9): delayed_activate lives here now, once, instead of being
        # nested inside the 366 handler - so both the ordinary NAMES path AND
        # the timeout watchdog below can trigger the same activation logic.
        def delayed_activate(sock=s, epoch=my_epoch):
            # `sock` and `epoch` are bound as DEFAULT ARGUMENTS on purpose. Both were
            # previously free variables resolved through irc_loop's frame, and that frame
            # is rebound by every reconnect - so a thread from a dead connection read the
            # CURRENT connection's values and published the wrong socket.
            import sys
            import time
            import announce
            import threading

            if config.connection_epoch != epoch:
                return

            time.sleep(5)

            # Re-check after sleeping: the connection can die inside this window.
            if config.connection_epoch != epoch:
                print("[ACTIVATE] Connection changed while settling. Abandoning stale activation.")
                return

            # Only claim channel sync if NAMES actually populated something. The watchdog
            # path can reach here without any 353 having arrived, and dcc.py's stale-freeze
            # sweep treats bot_joined_channel as proof that channel_users is authoritative -
            # with it empty, every frozen user looks absent and their queue gets reaped.
            if getattr(config, 'channel_users', None):
                config.bot_joined_channel = True
            else:
                print("[ACTIVATE] No channel members known yet; advertising without claiming channel sync.")

            oserve_mod = sys.modules.get('oserve')
            if oserve_mod:
                oserve_mod.bot_joined_channel = config.bot_joined_channel
                oserve_mod.irc_connection = sock
                
            announce.is_ready = True
            if hasattr(announce, 'last_announce_time'):
                announce.last_announce_time = time.time()
                
            # Watches for the main nick to free up after a netsplit, and reclaims it
            def background_nick_monitor(sock_inst):
                main_nick = getattr(config, 'ORIGINAL_NICK', 'DCCore')
                # Check every 10 seconds, for up to 5 minutes after JOIN
                for _ in range(30):
                    if str(config.NICKNAME).lower() == main_nick.lower():
                        break  # Already on the main nick; stop watching
                        
                    main_nick_active = False
                    with runtime.channel_users_lock():
                        if hasattr(config, 'channel_users') and isinstance(config.channel_users, dict):
                            for chan_name, users_set in config.channel_users.items():
                                if main_nick.lower() in [u.lower() for u in users_set]:
                                    main_nick_active = True
                                    break
                                
                    if not main_nick_active:
                        print(f"\n[NICK RECOVERY] The ghost nick {main_nick} timed out. Changing nick...")
                        try:
                            sock_inst.send(f"NICK {main_nick}\r\n".encode())
                            config.NICKNAME = main_nick
                            break
                        except:
                            break
                    time.sleep(10)
            
            # Start the persistent timer in the background
            threading.Thread(target=background_nick_monitor, args=(s,), daemon=True).start()
                
            print("[CONNECT FIX] Restarting the channel advert automatically...")
            threading.Thread(target=announce.announce_worker, daemon=True).start()
            config.announce_thread_alive = True

        def activation_watchdog(epoch=my_epoch):
            """NEW (issue #9): forces activation after a reasonable wait, even if
            some channels never answered the JOIN - banned, invite-only, misspelled.
            Without this watchdog, two or more broken channels could silence ALL
            advertising for the whole life of the connection, with no error at all."""
            time.sleep(ACTIVATION_TIMEOUT)
            # Bail if this connection is gone. config.activation_triggered alone is not a
            # staleness guard: the disconnect epilogue RESETS it, which re-armed exactly
            # the threads it should have invalidated.
            if config.connection_epoch != epoch:
                print("[WATCHDOG] Connection changed before the timeout elapsed. Standing down.")
                return
            if joined and not getattr(config, 'activation_triggered', False):
                missing = target_channels - channels_confirmed
                config.activation_triggered = True
                if missing:
                    print(f"[WARNING] Activating the advert despite {len(missing)} unconfirmed channel(s): {', '.join(missing)}")
                    try:
                        announce.send_debug(
                            f"Activated after {int(ACTIVATION_TIMEOUT)}s with {config.C_BOLD}{len(missing)}{config.C_RESET} channel(s) never confirmed via NAMES: {', '.join(missing)}",
                            category="PART")
                    except Exception as watchdog_debug_err:
                        print(f"[WARNING] Could not send the watchdog debug notice: {watchdog_debug_err}")
                else:
                    print(f"[INFO] Watchdog: every channel confirmed just in time.")
                threading.Thread(target=delayed_activate, daemon=True).start()

        while True:
            try:
                try:
                    data = s.recv(2048).decode("utf-8", errors="ignore")
                except socket.timeout:
                    now = time.time()
                    quiet_for = now - last_recv_time

                    if quiet_for > SILENCE_LIMIT:
                        print(f"[TIMEOUT] The server has been silent for {int(quiet_for)}s. Dropping the link to reconnect.")
                        try: s.close()
                        except: pass
                        _release_socket()
                        break

                    if quiet_for > KEEPALIVE_AFTER and (now - last_ping_sent) > KEEPALIVE_AFTER:
                        try:
                            s.send(b"PING :lagcheck\r\n")
                            last_ping_sent = now
                        except Exception as ping_err:
                            print(f"[TIMEOUT] The keepalive PING did not get through ({ping_err}). Dropping the link to reconnect.")
                            try: s.close()
                            except: pass
                            _release_socket()
                            break
                    continue
                except socket.error as net_err:
                    print(f"[DISCONNECT FIX] TCP keepalive detected a dead network ({net_err}). Dropping the link to reconnect.")
                    try: s.close()
                    except: pass
                    _release_socket()
                    break
                except Exception as e:
                    print(f"[IRC READ ERROR] Unexpected error while reading from the network: {e}")
                    try: s.close()
                    except: pass
                    _release_socket()
                    break

                if not data:
                    print("[DISCONNECT] Server closed connection. Breaking to reconnect motor...")
                    try: s.close()
                    except: pass
                    _release_socket()
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
                            
                    # Anchored: this writes a raw PONG straight to the socket with no
                    # pacing, ahead of the ban check and the flood gate, so an unanchored
                    # test was a one-paste Excess Flood disconnect for any user in any
                    # channel - banned or not.
                    if is_server_numeric(line, "513") and "PONG" in line:
                        parts = line.split()
                        pong_code = parts[-1].strip()
                        s.send(f"PONG {pong_code}\r\n".encode())
                    # Catches ONLY official server collisions; channel chatter is ignored
                    # Anchored: " 433 " matched those digits anywhere in the line, and the
                    # PRIVMSG/NOTICE exclusion under it did not cover PART or QUIT reasons
                    # or a channel TOPIC - so parting with "bye 433" pushed the bot off its
                    # main nick and wrote an unpaced NICK straight to the socket.
                    # "erroneous nickname" was matching numeric 432 by its English wording;
                    # match the numeric itself, which no server translates and no user can
                    # forge. The PRIVMSG/NOTICE test is gone because anchoring makes it not
                    # merely dead but harmful: a genuine 433 whose text happened to contain
                    # the word PRIVMSG would have been discarded.
                    if is_server_numeric(line, "433") or is_server_numeric(line, "432"):
                        main_nick = getattr(config, 'ORIGINAL_NICK', 'DCCore')
                        if str(config.NICKNAME).lower() == main_nick.lower():
                            alt_nick = getattr(config, 'ALT_NICKNAME', f"{main_nick}`")
                            print(f"[LIVE NICK COLLISION] The server reported a genuine collision for {main_nick}. Fallback nick: {alt_nick}")
                            s.send(f"NICK {alt_nick}\r\n".encode())
                            config.NICKNAME = alt_nick

                    # Reclaim the main nick the moment the other client releases it
                    # Anchored twice over. The old test matched " QUIT "/" PART " anywhere,
                    # then looked for ":<mainnick>!" anywhere - and a QUIT reason is free
                    # text. Together with the 433 handler above that was a two-line flood:
                    # forge a 433 to push the bot onto its alt nick, forge a QUIT to pull it
                    # back, repeat. Two unpaced NICK commands per round, from channel text.
                    if is_user_event(line, "QUIT") or is_user_event(line, "PART"):
                        main_nick = getattr(config, 'ORIGINAL_NICK', 'DCCore')
                        if str(config.NICKNAME).lower() != main_nick.lower():
                            if event_source_nick(line) == main_nick.lower():
                                print(f"[NICK RECOVERY] The main nick {main_nick} logged out. Reclaiming it now...")
                                try:
                                    s.send(f"NICK {main_nick}\r\n".encode())
                                    config.NICKNAME = main_nick
                                except Exception as recovery_err:
                                    print(f"[NICK RECOVERY ERROR] Could not reclaim the nick: {recovery_err}")


                    # Anchored: "001" in line matched any message containing those three
                    # digits anywhere, including a perfectly ordinary track request.
                    if not joined and (is_server_numeric(line, "001") or is_server_numeric(line, "376")):
                        joined = True
                        print(f"[INFO] Connected to the server. Waiting 5 seconds to settle before JOIN...")
                        
                        def delayed_join(socket_conn, channels):
                            time.sleep(5)
                            try:
                                socket_conn.send(f"JOIN {channels}\r\n".encode())
                                debug_chan = getattr(config, 'DEBUG_CHANNEL', '#flac-serv')
                                socket_conn.send(f"JOIN {debug_chan}\r\n".encode())
                                print(f"[JOIN] Joined the main channels and the debug channel: {debug_chan}")
                                # NEW (issue #9): start the watchdog HERE, right after the JOIN
                                # has actually been sent, so the timeout starts from the right moment.
                                threading.Thread(target=activation_watchdog, daemon=True).start()
                            except Exception as join_err:
                                print(f"[ERROR] Could not send JOIN: {join_err}")
                                
                        threading.Thread(target=delayed_join, args=(s, config.CHANNEL), daemon=True).start()

                    if joined and not getattr(config, 'activation_triggered', False) and " 366 " in line:
                        # FIXED (issue #9): parses WHICH channel the 366 line refers to instead
                        # of just counting them. Activates only once every real target channel
                        # is confirmed - the debug channel's 366 can no longer mask a broken
                        # huvudkanal.
                        # Anchored to the server prefix: the old unanchored search matched
                        # anywhere in the line, so a user could PRIVMSG " 366 x #chan" and
                        # forge a channel confirmation, activating the bot early.
                        m366 = re.match(r"^:\S+ 366 \S+ ([#\w\-]+)", line)
                        if m366:
                            confirmed_chan = m366.group(1).lower()
                            channels_confirmed.add(confirmed_chan)
                            print(f"[INFO] Received End of NAMES for {confirmed_chan} ({len(channels_confirmed & target_channels)}/{len(target_channels)} target channels confirmed)")
                        
                        if target_channels.issubset(channels_confirmed):
                            config.activation_triggered = True
                            print(f"[INFO] All channels joined successfully! Waiting 5 seconds for settle...")
                            threading.Thread(target=delayed_activate, daemon=True).start()



                    # Anchored: ".* NICK :" let the command sit anywhere, so "hey NICK
                    # :victim" typed in a channel renamed the SPEAKER's send_queue key onto
                    # the victim - and the assignment below overwrites, so it destroyed
                    # whatever that victim had pending.
                    if is_user_event(line, "NICK"):
                        nick_match = re.match(r"^:([^!\s]+)!\S*\s+NICK\s+:?(\S+)", line)
                        if nick_match:
                            old_nick = nick_match.group(1).lower()
                            new_nick = nick_match.group(2).strip()
                            if old_nick in config.send_queue:
                                import queue_mgr
                                queue_mgr.config.send_queue[new_nick.lower()] = queue_mgr.config.send_queue.pop(old_nick)
                            
                    # Anchored: this writes straight into config.whois_status.
                    if is_server_numeric(line, "352"):
                        parts = line.split()
                        if len(parts) > 7:
                            target_nick = parts[7].lower()
                            config.whois_status[target_nick] = True
                    # Anchored: this populates config.channel_users, which dcc.py treats as
                    # proof a user is present when deciding whether to thaw a frozen queue
                    # and dispatch to them. A forged line injected fake presence.
                    if is_server_numeric(line, "353"):
                        name_match = re.search(r" 353 [^#]+([#\w\-]+) :(.+)$", line)
                        if name_match:
                            chan = name_match.group(1).lower()
                            names = [n.strip("@+~&%").lower() for n in name_match.group(2).split()]
                            with runtime.channel_users_lock():
                                if chan not in config.channel_users:
                                    config.channel_users[chan] = set()
                                config.channel_users[chan].update(names)

                            # -------------------------------------------------
                            # RECONNECT THAW - this is what saves the queues:
                            # After a reconnect, everyone already in the channel comes back via
                            # NAMES (353) and NOT via JOIN. Without this, their queues stayed
                            # frozen and were deleted by the 5-minute timer despite never leaving.
                            # -------------------------------------------------
                            thawed_users = [n for n in names if n in getattr(config, 'frozen_queues', {})]
                            for frozen_user in thawed_users:
                                del config.frozen_queues[frozen_user]
                                files_in_q = len(config.dcc_queue.get(frozen_user, []))
                                print(f"[DCC RECONNECT THAW] {frozen_user} was still in {chan} at the NAMES sync. Thawing {files_in_q} file(s).")
                                threading.Thread(target=dcc.check_queue_and_send, args=(s, frozen_user), daemon=True).start()

                            if thawed_users:
                                announce.send_debug(f"Reconnect sync in {chan}: thawed {config.C_BOLD}{len(thawed_users)}{config.C_RESET} queue(s) for users who never left.", category="JOIN")

                    # Anchored: " JOIN " matched the word anywhere, so a PRIVMSG containing
                    # it thawed the speaker's own frozen queue on demand, and let them insert
                    # themselves into config.channel_users for a channel they are not in -
                    # which dcc.py reads as proof of presence before it dispatches.
                    elif is_user_event(line, "JOIN") and event_source_nick(line) != config.NICKNAME.lower():
                        join_match = re.search(r"^:([^!]+)!.* JOIN :?([#\w\-]+)", line)
                        if join_match:
                            joined_user = join_match.group(1)
                            joined_chan = join_match.group(2)
                            j_key = joined_user.lower()
                            
                            with runtime.channel_users_lock():
                                if joined_chan.lower() not in config.channel_users:
                                    config.channel_users[joined_chan.lower()] = set()
                                config.channel_users[joined_chan.lower()].add(j_key)
                            
                            if hasattr(config, 'frozen_queues') and j_key in config.frozen_queues:
                                del config.frozen_queues[j_key]
                                print(f"[DCC REALTIME THAW] {joined_user} rejoined {joined_chan}. Thawing their queue.")
                                files_in_q = len(config.dcc_queue.get(j_key, [])) if hasattr(config, 'dcc_queue') else 0
                                announce.send_debug(f"User {config.C_BOLD}{joined_user}{config.C_RESET} returned to {joined_chan}, continuing queue of {config.C_BOLD}{files_in_q}{config.C_RESET} file(s)", category="JOIN")
                                threading.Thread(target=dcc.check_queue_and_send, args=(s, joined_user), daemon=True).start()
                                
                    # Anchored: as JOIN. This one removes people from channel_users, which
                    # freezes their queue and starts the five-minute delete timer.
                    elif is_user_event(line, "PART"):
                        part_match = re.search(r"^:([^!]+)!.* PART ([#\w\-]+)", line)
                        if part_match:
                            p_user = part_match.group(1).lower()
                            p_chan = part_match.group(2).lower()
                            with runtime.channel_users_lock():
                                if p_chan in config.channel_users and p_user in config.channel_users[p_chan]:
                                    config.channel_users[p_chan].remove(p_user)

                    # Anchored: the worst of the three, because it removes the user from
                    # EVERY channel at once. "@find QUIT PLAYING GAMES" is an ordinary
                    # search that silently cost the searcher their whole queue five minutes
                    # later.
                    elif is_user_event(line, "QUIT"):
                        quit_match = re.search(r"^:([^!]+)!", line)
                        if quit_match:
                            q_user = quit_match.group(1).lower()
                            with runtime.channel_users_lock():
                                for chan in config.channel_users:
                                    if q_user in config.channel_users[chan]:
                                        config.channel_users[chan].remove(q_user)

                    # Cross-bot search broadcast capture, NOTICE half. NOTICE
                    # lines are not parsed anywhere else in this loop - many
                    # file-sharing bots reply to @find via NOTICE rather than
                    # PRIVMSG, so without this branch every such reply would be
                    # silently dropped exactly like a PRIVMSG-to-self used to
                    # be before the branch below existed. Read-only: this never
                    # dispatches a command, so it needs none of the PRIVMSG
                    # branch's ban/flood/dispatch machinery.
                    notice_match = re.match(r"^:([^!]+)!.* NOTICE ([#\w\-]+) :(.+)$", line)
                    if notice_match:
                        notice_user = notice_match.group(1)
                        if notice_user.lower() != config.NICKNAME.lower():
                            _capture_broadcast_search_reply(
                                notice_user, notice_match.group(2), notice_match.group(3).strip())

                    match = re.match(r"^:([^!]+)!.* PRIVMSG ([#\w\-]+) :(.+)$", line)
                    if match:
                        user = match.group(1)
                        target_chan = match.group(2)
                        msg = match.group(3).strip()
                        if user.lower() == config.NICKNAME.lower():
                            continue

                        # Cross-bot search broadcast capture, PRIVMSG half (see
                        # the NOTICE branch above and _capture_broadcast_search_reply()'s
                        # own docstring). Placed before the ban check on
                        # purpose: a foreign bot answering a broadcast we asked
                        # for is not subject to OUR channel's ban list, and
                        # this never dispatches anything of its own for a ban
                        # to meaningfully gate.
                        _capture_broadcast_search_reply(user, target_chan, msg)

                        if not security.check_user_status(user):
                            continue
                            
                        msg_lower = msg.lower()
                        bot_aliases = get_bot_aliases()
                        # FIXED (issue #35): the gate previously covered only 4 of the ~11
                        # dispatch paths below, so -que, -remove, both CTCP variants, !list,
                        # !debugnames and !ping could each spawn a thread or send a NOTICE
                        # for ANY user with no rate limit at all. Admin commands (!ban,
                        # !unban, !rehash, !update, !clearqueue) are deliberately left out:
                        # each self-checks against ADMIN_NICK in its own handler already, so
                        # gating them here would only meter the operator against themselves.
                        is_bot_command = (
                            msg_lower == f"@{config.NICKNAME.lower()}"
                            or msg_lower == f"@{config.NICKNAME.lower()}-que"
                            or msg_lower == f"@{config.NICKNAME.lower()}-remove"
                            or msg.startswith("@find ")
                            or msg.startswith("@locator ")
                            or any(msg_lower.startswith(f"!{alias} ") for alias in bot_aliases)
                            or msg_lower in ("!list", "!debugnames", "!ping")
                            or (msg.startswith("\x01") and msg.strip("\x01").strip().upper() in ("QUE", "REMOVE"))
                        )
                        if is_bot_command and security.is_flooding(user):
                            continue 
                            
                        try:
                            import commands
                            import db
                            db.check_and_rotate_day()
                            
                            if msg.startswith("\x01") and msg.endswith("\x01"):
                                ctcp_cmd = msg.strip("\x01").strip().upper()
                                # Admin console. Only ever from a private message -
                                # a DCC CHAT offer addressed to a channel is
                                # meaningless, and accepting one would mean acting
                                # on a line every user in that channel can send.
                                if (ctcp_cmd.startswith("DCC CHAT ")
                                        and target_chan.lower() == config.NICKNAME.lower()):
                                    import adminchat
                                    adminchat.handle_dcc_chat(
                                        s, line, user, msg.strip("\x01").strip())
                                    continue
                                # Cross-bot file fetch: a DCC SEND offer answering
                                # a fetch WE requested (see dcc_fetch.py). Private
                                # only, same reasoning as DCC CHAT above - a DCC
                                # SEND offer addressed to a channel is meaningless
                                # and would mean acting on a line any channel
                                # member could send. Admission control lives
                                # entirely inside handle_incoming_offer(): it only
                                # proceeds if this matches a row we ourselves
                                # marked 'offered' a moment ago, so an unsolicited
                                # offer from anyone is dropped there, not here.
                                if (ctcp_cmd.startswith("DCC SEND ")
                                        and target_chan.lower() == config.NICKNAME.lower()):
                                    import dcc_fetch
                                    threading.Thread(
                                        target=dcc_fetch.handle_incoming_offer,
                                        args=(s, user, msg.strip("\x01").strip()),
                                        daemon=True).start()
                                    continue
                                if ctcp_cmd == "QUE":
                                    threading.Thread(target=commands.handle_queue_check, args=(s, user, target_chan), daemon=True).start()
                                    continue
                                elif ctcp_cmd == "REMOVE":
                                    threading.Thread(target=commands.handle_queue_remove, args=(s, user, target_chan), daemon=True).start()
                                    continue
                            elif msg_lower == f"@{config.NICKNAME.lower()}":
                                threading.Thread(target=list.send_file_list, args=(s, user, target_chan)).start()
                            elif msg_lower == f"@{config.NICKNAME.lower()}-que":
                                threading.Thread(target=commands.handle_queue_check, args=(s, user, target_chan), daemon=True).start()
                                continue
                            elif msg_lower == f"@{config.NICKNAME.lower()}-remove":
                                threading.Thread(target=commands.handle_queue_remove, args=(s, user, target_chan), daemon=True).start()
                                continue
                            elif msg.startswith("@find ") or msg.startswith("@locator "):
                                parts = msg.split(" ", 1)
                                if len(parts) > 1:
                                    search_term = parts[1].strip()
                                    if search_term:
                                        threading.Thread(target=list.execute_search, args=(s, user, search_term, target_chan), daemon=True).start()
                            elif msg_lower == "!list":
                                list.send_list_trigger_info(s, user)
                            elif msg.lower() == "!debugnames":
                                with runtime.channel_users_lock():
                                    have_count = hasattr(config, 'channel_users') and target_chan.lower() in config.channel_users
                                    if have_count:
                                        current_qty = len(config.channel_users[target_chan.lower()])
                                if have_count:
                                    s.send(f"NOTICE {user} :[RAM-CHECK] Currently tracking {current_qty} user(s) live via 353-numeric in {target_chan}.\r\n".encode())
                                else:
                                    s.send(f"NOTICE {user} :[RAM-CHECK] Critical: No 353 names loaded yet for {target_chan} in config structure.\r\n".encode())
                            elif msg.lower() == "!ping":
                                threading.Thread(target=commands.handle_ping_request, args=(s, user, target_chan), daemon=True).start()
                            # Admin commands in channel. ADMIN_CHANNEL_COMMANDS retires these
                            # once the DCC console is trusted; the console reaches the same
                            # handlers with authorised=True. User commands are unaffected.
                            elif (getattr(config, 'ADMIN_CHANNEL_COMMANDS', True)
                                  and (msg.lower() in ('!rehash', '!update')
                                       or msg.startswith('!ban ') or msg.startswith('!unban ')
                                       or msg_lower == '!clearqueue'
                                       or msg_lower.startswith('!clearqueue '))):
                                if msg.lower() == "!rehash":
                                    threading.Thread(target=commands.handle_rehash_request, args=(user, target_chan), daemon=True).start()
                                elif msg.startswith("!ban "):
                                    threading.Thread(target=commands.handle_hard_ban_request, args=(user, target_chan, msg), daemon=True).start()
                                elif msg.startswith("!unban "):
                                    threading.Thread(target=commands.handle_hard_unban_request, args=(user, target_chan, msg), daemon=True).start()
                                elif msg.lower() == "!update":
                                    threading.Thread(target=commands.handle_list_update_request, args=(user, target_chan), daemon=True).start()
                                elif msg_lower == "!clearqueue" or msg_lower.startswith("!clearqueue "):
                                    # commands.handle_admin_clear_queue was added in #16 but never
                                    # wired into this dispatch chain, so the command had no caller
                                    # anywhere and typing it did nothing at all. The handler does
                                    # its own admin check.
                                    threading.Thread(target=commands.handle_admin_clear_queue, args=(user, target_chan, msg), daemon=True).start()
                            elif any(msg_lower.startswith(f"!{alias} ") for alias in bot_aliases):
                                # Split on the first space only, so "!DCCore !rar Artist/Album"
                                # still hands "!rar Artist/Album" to the download handler.
                                parts = msg.split(" ", 1)
                                if len(parts) > 1:
                                    requested_file = parts[1].strip()
                                    threading.Thread(target=dcc.handle_download_request, args=(s, user, requested_file, target_chan)).start()
                                    
                        except Exception as cmd_err:
                            print(f"[ERROR] Error handling a bot command from {user}: {cmd_err}")

            except Exception as inner_loop_err:
                print(f"[IRC INTERNAL ERROR] Unexpected error inside the message loop: {inner_loop_err}")
                try: s.close()
                except: pass
                _release_socket()
                break

        # Reset every flag before the next pass through the reconnect loop
        print("[CONNECT] Lost the connection. Reconnecting to the IRC server in 10 seconds...")
        config.bot_joined_channel = False
        
        # FIXED: clears the in-memory channel lists on a crash, so the bot does not block its own nick next time
        with runtime.channel_users_lock():
            if hasattr(config, 'channel_users') and isinstance(config.channel_users, dict):
                config.channel_users.clear()
            
        config.activation_triggered = False
        # Invalidate every thread spawned for the connection that just died.
        config.connection_epoch += 1
        oserve_mod = sys.modules.get('oserve')
        if oserve_mod:
            oserve_mod.bot_joined_channel = False
        _release_socket()
        announce.is_ready = False
        import queue_mgr
        if "channel_announce" in queue_mgr.config.send_queue:
            queue_mgr.config.send_queue["channel_announce"] = []
        time.sleep(10)
