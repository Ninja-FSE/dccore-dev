# commands.py - User commands, mostly queue handling
import sys
import config
import db

# The five admin handlers below take authorised=False. The DCC CHAT console
# passes authorised=True: a console session has already proved the operator's
# Undernet services login via their +x host and then a password, which is a
# STRONGER claim than this nick comparison, and re-checking the nick here would
# refuse them unless their current nick also happened to be in ADMIN_NICK. Every
# channel caller keeps the default and the nick check.


def is_admin(user):
    """Return True if `user` may run admin commands.

    Centralises a check that was duplicated across five handlers, so the eventual
    hostmask-based gate is a change in one place instead of five.

    Two deliberate changes from the copies this replaces:

    * The hardcoded `or user.lower() == "flac"` fallback is gone. It made the literal nick
      "flac" an admin regardless of what config.ADMIN_NICK was set to - an undocumented
      second account nobody could turn off. It is a no-op today because ADMIN_NICK is
      already "FLAC", so removing it changes nothing until that value is edited.
    * ADMIN_NICK may now be a comma-separated list, so a second operator can be added
      without reintroducing a hardcoded name.

    KNOWN LIMITATION: this is still nick-based, and an Undernet nick is not owned without
    services auth - anyone can take the nick while the real admin is offline and gain
    every admin command, now including the destructive !clearqueue. Closing that properly
    means matching ident@host, which irc.py does not currently capture: its PRIVMSG regex
    keeps only the nick. That is a separate change to irc.py plus this file.
    """
    import config

    raw = getattr(config, 'ADMIN_NICK', 'FLAC') or ''
    allowed = {n.strip().lower() for n in str(raw).split(',') if n.strip()}
    return str(user).lower() in allowed


def handle_queue_check(s, user, target):
    """Count one user's queued files and answer them through the VIP queue."""
    user_key = user.lower()
    oserve = sys.modules.get('oserve')
    import list
    import dcc
    
    # 1. How many files this particular user has queued
    file_count = 0
    if hasattr(config, 'dcc_queue') and user_key in config.dcc_queue:
        file_count = len(config.dcc_queue[user_key])
        
    # 2. Live statistics, for the fuller empty-queue notice
    file_count_total, list_date, total_size, raw_bytes = list.get_file_count_date_size_and_raw_bytes()
    formatted_total_files = f"{file_count_total:,}"
    
    active_dl = oserve.active_downloads if oserve else 0
    free_slots = max(0, config.MAX_DCC_SLOTS - active_dl)
    slots_str = f"{free_slots}/{config.MAX_DCC_SLOTS}"
    
    queued_count = dcc.get_total_queued_count()
    queue_str = f"{queued_count}/{config.MAX_QUEUE_LIMIT}" if hasattr(config, 'MAX_QUEUE_LIMIT') else f"{queued_count}"

    # 3. Pick the layout depending on whether the queue is empty
    if file_count > 0:
        # Layout when they do have files queued; only the number and trigger are bold
        msg = (
            f"NOTICE {user} :You have {config.C_BOLD}{config.C_RED}{file_count}{config.C_RESET} files in queue. "
            f"To remove your entire queue, type: {config.C_BOLD}{config.C_RED}@{config.NICKNAME}-remove{config.C_RESET} "
            f"or send CTCP: {config.C_BOLD}{config.C_GREEN}REMOVE{config.C_RESET}\r\n"
        )
    else:
        # The fuller layout: only numbers, the trigger and values are bold and coloured
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
    """Clear the user's queue from memory and remove it from dcc_queue.txt on disk."""
    user_key = user.lower()
    oserve = sys.modules.get('oserve')
    import dcc

    removed_archives = []
    with dcc.queue_lock:
        # Remove them from the ordinary queue
        if hasattr(config, 'dcc_queue') and user_key in config.dcc_queue:
            # BEFORE dropping the rows: they are the only record that the temp
            # archives exist. The freeze sweep, the freeze timer and !clearqueue
            # all cleaned up here; this path - the one users actually type - did
            # not, so every archive it orphaned stayed in TMP_ZIP_DIR until
            # somebody noticed the disk filling.
            removed_archives = dcc.discard_orphaned_temp_archives(user_key)
            del config.dcc_queue[user_key]
            db.save_dcc_queue()  # Write the cleared queue straight to disk

        # Also drop them from the freezer, in case they were frozen
        if hasattr(config, 'frozen_queues') and user_key in config.frozen_queues:
            del config.frozen_queues[user_key]

    msg = f"NOTICE {user} :Your queue has been completely removed. \r\n"
    if oserve:
        oserve.queue_message(user, msg)
    if removed_archives:
        print(f"[COMMANDS] Removed {len(removed_archives)} orphaned temp archive(s) with {user}'s queue.")
    print(f"[COMMANDS] {user} removed their entire queue from the disk layout.")

def handle_admin_clear_queue(user, target_chan, msg_text, authorised=False):
    """NEW (issue #15): force-clears ANOTHER user's queue entirely - a ghost nick, a
    efter en netsplit/reconnect, etc.) - enbart admin. handle_queue_remove ovan kan bara
    a user could only ever run against themselves over IRC; this gives the admin the
    same power over ANYONE, from a single line of text, without touching dcc_queue.txt
    manuellt."""
    import config
    import announce
    import db
    import dcc

    if not authorised and not is_admin(user):
        print(f"[SECURITY] Unauthorised user {user} tried to run !clearqueue.")
        return

    parts = msg_text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        announce.send_debug("Syntax error! Usage: !clearqueue <nick>", category="INFO")
        return

    target_nick = parts[1].strip()
    target_key = target_nick.lower()

    removed_count = 0
    was_frozen = False

    with dcc.queue_lock:
        if hasattr(config, 'dcc_queue') and target_key in config.dcc_queue:
            removed_count = len(config.dcc_queue[target_key])

            # Shared with @<nick>-remove and, in a cruder form, the two freeze
            # sweeps in dcc.py. Must run before the rows are dropped, because they
            # are the only record that these archives exist.
            for temp_path in dcc.discard_orphaned_temp_archives(target_key):
                print(f"[ADMIN CLEARQUEUE] Removed orphaned temp archive: {temp_path}")

            del config.dcc_queue[target_key]
            db.save_dcc_queue()

        if hasattr(config, 'frozen_queues') and target_key in config.frozen_queues:
            del config.frozen_queues[target_key]
            was_frozen = True

    if removed_count > 0 or was_frozen:
        extra = " (was frozen too)" if was_frozen else ""
        announce.send_debug(
            f"Admin {config.C_BOLD}{user}{config.C_RESET} force-cleared queue for {config.C_BOLD}{target_nick}{config.C_RESET}: {config.C_BOLD}{removed_count}{config.C_RESET} file(s) removed{extra}.",
            category="INFO")
        print(f"[ADMIN CLEARQUEUE] {user} force-cleared {target_nick}'s queue ({removed_count} files, frozen={was_frozen}).")
    else:
        announce.send_debug(
            f"Clearqueue: {config.C_BOLD}{target_nick}{config.C_RESET} had no queue or frozen entry to remove.",
            category="INFO")
        print(f"[ADMIN CLEARQUEUE] {user} tried to clear {target_nick}, but no queue or frozen entry was found.")

def handle_ping_request(irc_sock, user, target_chan):
    """Start the timer and send a unique latency PING to the IRC server."""
    import time
    import config
    
    # Keep the measurement in shared memory so the pong handler can read it later
    config.ping_start_time = time.time()
    config.ping_triggered_by = user
    config.ping_channel_source = target_chan
    
    # Send the probe straight to the server's raw socket
    try:
        irc_sock.send(b"PING :OSERVE_LATENCY_CHECK\r\n")
        print(f"[PING COMMAND] Latency measurement started by {user} in {target_chan}.")
    except Exception as e:
        print(f"[PING ERROR] Could not send the PING packet: {e}")

def handle_pong_response(category="INFO"):
    """Catch the server's reply, work out the latency to three decimals, and report it."""
    import time
    import config
    import announce
    
    start_time = getattr(config, 'ping_start_time', None)
    if start_time:
        # The value is kept in seconds rather than milliseconds
        latency_sec = time.time() - start_time
        
        trigger_user = getattr(config, 'ping_triggered_by', config.NICKNAME)
        source_chan = getattr(config, 'ping_channel_source', config.CHANNEL)
        
        # The :.3f format always shows exactly three decimals (e.g. 0.129)
        announce.send_debug(
            f"Latency Check triggered by {trigger_user} from {source_chan} -> IRC Server Response Time: {latency_sec:.3f} sec", 
            category=category
        )
        
        config.ping_start_time = None

# ---------------------------------------------------------------------------
# Runtime state that must survive a !rehash.
#
# Module level on purpose. This used to be a local inside
# handle_rehash_request(), which is part of why it fell out of date: a list
# that has to be updated whenever config.py gains a container is useless if
# nothing can see it. tests/test_commands.py now derives the set of
# containers from config.py and asserts each one is either in here or
# explicitly excused, so the next omission fails a test instead of silently
# emptying something on the next rehash.
# ---------------------------------------------------------------------------
# =====================================================================
# 1b. RUNTIME STATE THAT MUST SURVIVE THE RELOAD
# =====================================================================
# importlib.reload re-executes a module body, so every name config.py assigns in its
# "GLOBALT LIVE-MINNE" section is reset to an empty container. The block above already
# rescues dcc_queue and channel_users; everything else in that section was being
# silently destroyed on every !rehash.
#
# NOT preserved, deliberately - these are cleared ON PURPOSE and that behaviour is kept:
#   send_queue          - blanked below so stale text cannot collide after the reload
#   rar_inprogress      - the documented "lock-clearing rehash" escape hatch for a
#   user_processing_lock  packer that wedged; !rehash is the only way to clear them
PRESERVE_RUNTIME = (
    'active_transfers',   # losing this reports 0 active slots while transfers run,
                          # so the bot admits work beyond MAX_DCC_SLOTS
    'banned_users',       # every timed ban silently released
    'frozen_queues',      # freeze timers lost, so departed users' queues never expire
    'muted_until',        # flood mutes released
    'whois_status',
    'user_requests',      # flood history, so a flooder gets a clean slate
    'failed_transfers',   # per-file retry counters
    'fetch_queue',        # the cross-bot fetch pool. Losing it is the same failure the
                          # active_transfers comment above describes, in the other slot
                          # pool: count_active_fetches() counts rows here, so an empty
                          # dict reports 0 active while transfers are still moving bytes
                          # and the next batch runs past MAX_FETCH_SLOTS. Orphaned rows
                          # also strand their finished files - the transfer completes,
                          # writes state='complete' onto a row nothing reads, and
                          # /api/fetch/<id>/download 404s for a file plainly on disk.
    'fetched_bot_lists',  # every fetched cross-bot list, each one a real multi-MB DCC
                          # transfer from another bot. Losing it empties the Download
                          # tab with no log line saying why.
)


def handle_rehash_request(user, target_chan, authorised=False):
    """Reload the modules live, in memory, without reading anything back from disk."""
    import importlib
    import sys
    import config
    import announce
    import copy
    
    if not authorised and not is_admin(user):
        print(f"[REHASH SECURITY] Ignored a rehash attempt from an unauthorised user: {user}")
        return

    # =====================================================================
    # 1. Back EVERY piece of live state up into local variables
    # =====================================================================
    # A. The channel user lists (from NAMES)
    ram_backup_users = {}
    if hasattr(config, 'channel_users') and isinstance(config.channel_users, dict):
        ram_backup_users = copy.deepcopy(config.channel_users)
        print(f"[REHASH RAM] Backed up the user lists for {len(ram_backup_users)} channel(s).")

    # B. The active DCC slots
    ram_backup_slots = 0
    ram_user_slots = {}
    for mod_name in ['dcc', 'config', 'oserve']:
        mod = sys.modules.get(mod_name)
        if mod:
            for attr in ['active_downloads', 'current_sends', 'total_sends']:
                if hasattr(mod, attr) and getattr(mod, attr) > 0:
                    ram_backup_slots = getattr(mod, attr)
            for attr in ['user_slots', 'active_users', 'current_user_slots']:
                if hasattr(mod, attr) and isinstance(getattr(mod, attr), dict):
                    raw_slots = getattr(mod, attr)
                    ram_user_slots = {k.lower(): v for k, v in raw_slots.items()}

    # C. The QUEUE - keep the original objects in memory, never via disk
    ram_backup_queue = {}
    for mod_name in ['dcc', 'config', 'oserve', 'queue_mgr', 'list']:
        mod = sys.modules.get(mod_name)
        if mod:
            for attr in ['dcc_queue', 'rar_queue', 'download_queue']:
                if hasattr(mod, attr) and isinstance(getattr(mod, attr), dict):
                    raw_q = getattr(mod, attr)
                    # Only keep users who still have real tracks queued
                    ram_backup_queue = {k.lower(): v for k, v in raw_q.items() if v and len(v) > 0}

    if ram_backup_queue:
        print(f"[REHASH RAM] Secured {len(ram_backup_queue)} active sharing queue(s) in memory.")

    # Keep the old channel list, for the JOIN/PART comparison
    old_chans = [c.strip().lower() for c in config.CHANNEL.split(",") if c.strip()]

    # Pause the advert for the moment
    announce.is_ready = False
    announce.send_debug(f"Rehash triggered by {user} from {target_chan}. PAUSING NOTICES & ADVERTISEMENT...", category="INFO")
    
    # vip_queue is deliberately NOT preserved, for the same reason send_queue is not: it is
    # transient OUTPUT, not state. Restoring it would also replay lines addressed to channels
    # this very handler is about to PART, which the server answers with 404.
    preserved_runtime = {}
    for _key in PRESERVE_RUNTIME:
        if hasattr(config, _key):
            preserved_runtime[_key] = getattr(config, _key)

    # config.NICKNAME is BOTH a setting and runtime state: irc.py rebinds it to ALT_NICKNAME
    # after a 433. Reloading config resets it to the file value, so a rehash while running as
    # the alternate nick left the bot answering to a name the server no longer knew it by -
    # every @trigger dead until the next reconnect.
    live_nick = getattr(config, 'NICKNAME', None)
    baseline_nick = getattr(config, 'ORIGINAL_NICK', None)

    # announce.current_worker_id is the advert worker's generation token. Reload resets it to
    # 0, the running worker sees the mismatch and retires itself, and nothing starts a new one
    # until the next reconnect - so !rehash silently stopped all channel advertising.
    live_worker_id = getattr(announce, 'current_worker_id', None)

    try:
        # 2. REHASH: reload every core module live, in memory
        modules_to_reload = ['config', 'list', 'dcc', 'announce', 'security', 'db', 'stats_mgr']
        for mod_name in modules_to_reload:
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
                
        if 'commands' in sys.modules:
            importlib.reload(sys.modules['commands'])
            
        print(f"[REHASH SUCCESS] Alla Python-moduler har blivit live-uppdaterade i RAM av {user}!")

        # Put the live state back before anything else runs against the fresh modules. The
        # explicit dcc_queue / channel_users restores further down still run afterwards and
        # win for those two keys, so this does not fight them.
        #
        # MERGE rather than overwrite. reload(config) rebinds each of these to a fresh empty
        # container, so anything another thread wrote during the reload landed there and a
        # blind setattr would discard it. The reload window is only the few milliseconds of
        # eight module reloads, but a transfer finishing inside it is exactly the case that
        # matters: its removal from active_transfers would be undone and the finished entry
        # would come back as a phantom holding a DCC slot.
        import config as _cfg
        for _key, _value in preserved_runtime.items():
            during_reload = getattr(_cfg, _key, None)
            if isinstance(_value, dict) and isinstance(during_reload, dict) and during_reload:
                merged = dict(_value)
                merged.update(during_reload)      # window writes win
                setattr(_cfg, _key, merged)
            elif isinstance(_value, list) and isinstance(during_reload, list) and during_reload:
                merged = list(_value)
                for _row in during_reload:
                    if _row not in merged:
                        merged.append(_row)
                setattr(_cfg, _key, merged)
            else:
                setattr(_cfg, _key, _value)

        if preserved_runtime:
            print(f"[REHASH RAM] Restored {len(preserved_runtime)} live runtime structures "
                  f"({', '.join(sorted(preserved_runtime))}).")

        # Name the restored slots explicitly. A phantom left by the window above is rare and
        # self-clears the next time that nick is promoted, but an operator staring at "3/3
        # slots busy" with nothing moving needs to be able to see whose they are.
        if getattr(_cfg, 'active_transfers', None):
            _holders = ', '.join(sorted({str(t.get('user', '?')) for t in _cfg.active_transfers}))
            print(f"[REHASH RAM] {len(_cfg.active_transfers)} DCC slot(s) still held by: {_holders}")

        # Nickname: if config.py's value is unchanged, the LIVE nick wins - it may be the 433
        # fallback and the server knows us by it. If the admin edited NICKNAME in the file,
        # adopt the new value and re-baseline ORIGINAL_NICK so the fallback logic follows it.
        if baseline_nick and _cfg.NICKNAME == baseline_nick:
            if live_nick and live_nick != _cfg.NICKNAME:
                _cfg.NICKNAME = live_nick
                print(f"[REHASH RAM] Kept the live nickname {live_nick!r} (alternate nick in use).")
        else:
            # The admin renamed the bot in config.py. Re-baseline so the rename survives the
            # next reconnect (irc.py resets NICKNAME from ORIGINAL_NICK on every connect), but
            # keep the OLD name addressable: the published master list was stamped with it
            # (update_list.py writes "!<nick> <file>" lines), so without this every request
            # pasted from a list users already downloaded would be dropped with no reply.
            # irc.get_bot_aliases picks this up; config.py never assigns it, so like
            # ORIGINAL_NICK it survives future reloads.
            if baseline_nick:
                _cfg.PREVIOUS_NICK = baseline_nick
            _cfg.ORIGINAL_NICK = _cfg.NICKNAME
            print(f"[REHASH RAM] config.py changed the nickname to {_cfg.NICKNAME!r}; "
                  f"re-baselined, still answering to {baseline_nick!r}.")

        import announce as _ann
        # Only reinstate the token if nothing newer claimed it. A reconnect completing inside
        # the reload window starts a fresh advert worker and stamps a higher id; restoring
        # blindly would retire that new worker and leave the channels silent.
        if live_worker_id and not getattr(_ann, 'current_worker_id', 0):
            _ann.current_worker_id = live_worker_id
            print("[REHASH RAM] Advert worker kept alive across the reload.")
            print("[REHASH NOTE] announce_worker's own code is NOT re-entered by a rehash; "
                  "restart the daemon to pick up changes to the advert loop itself.")
        
        # Read the freshly reloaded config
        import config
        import announce
        announce.is_ready = True
        
         # =====================================================================
        # 3. RESTORE: write every value back into the newly loaded modules
        # =====================================================================
        # Restore the users
        # In place: rebinding would detach config.channel_users from the
        # object runtime.py holds, and ram_backup_users is a deep COPY, so
        # the two would diverge from here on (see runtime.py's docstring).
        config.channel_users.clear()
        if ram_backup_users:
            config.channel_users.update(ram_backup_users)
        print(f"[REHASH RAM] Restored {len(config.channel_users)} channel list(s) into the new modules.")

        # Restore the slots
        for mod_name in ['dcc', 'config', 'oserve']:
            mod = sys.modules.get(mod_name)
            if mod:
                if ram_backup_slots > 0:
                    for attr in ['active_downloads', 'current_sends', 'total_sends']:
                        if hasattr(mod, attr): setattr(mod, attr, ram_backup_slots)
                if ram_user_slots:
                    for attr in ['user_slots', 'active_users', 'current_user_slots']:
                        if hasattr(mod, attr):
                            combined_slots = {k.lower(): v for k, v in ram_user_slots.items()}
                            for k, v in ram_user_slots.items(): combined_slots[k.upper()] = v
                            setattr(mod, attr, combined_slots)

        # Restore the queue, putting the exact objects back under lowercased keys
        if ram_backup_queue:
            combined_queue = {}
            for k, v in ram_backup_queue.items():
                combined_queue[k.lower()] = v
                
            for mod_name in ['dcc', 'config', 'oserve', 'queue_mgr', 'list']:
                mod = sys.modules.get(mod_name)
                if mod:
                    for attr in ['dcc_queue', 'rar_queue', 'download_queue']:
                        if not hasattr(mod, attr):
                            continue
                        existing = getattr(mod, attr)
                        if isinstance(existing, dict):
                            # Mutate rather than rebind. config.dcc_queue is
                            # the object runtime.py holds; setattr here would
                            # silently detach it and the queue would drift.
                            existing.clear()
                            existing.update(combined_queue)
                        else:
                            setattr(mod, attr, combined_queue)
            print(f"[REHASH RAM] The active sharing queue was restored in memory, lowercased.")


        # Reset the text queues (send_queue) to empty dicts so they cannot clash
        for mod_name in ['queue_mgr', 'config', 'oserve', 'irc']:
            mod = sys.modules.get(mod_name)
            if mod:
                for attr in ['send_queue', 'msg_queue', 'out_queue']:
                    if hasattr(mod, attr): setattr(mod, attr, {})
        print(f"[REHASH RAM] The outgoing text queues (send_queue) were reset.")

        # Reset the advert timer, so it waits a fresh five minutes
        if hasattr(announce, 'last_announce_time'):
            import time
            announce.last_announce_time = time.time()
            
        # ---------------------------------------------------------------------
        # 4. HELAUTOMATISK KANAL-SYNK (JOIN NYA / PART BORTTAGNA)
        # ---------------------------------------------------------------------
        oserve = sys.modules.get('oserve')
        irc_sock = getattr(oserve, 'irc_connection', None)
        
        if irc_sock:
            new_chans = [c.strip().lower() for c in config.CHANNEL.split(",") if c.strip()]
            
            for chan in new_chans:
                if chan not in old_chans:
                    irc_sock.send(f"JOIN {chan}\r\n".encode())
                    announce.send_debug(f"Joining channel {chan} due to new configuration layout!", category="JOIN")
                    if chan.lower() not in config.channel_users:
                        config.channel_users[chan.lower()] = set()
            
            for chan in old_chans:
                if chan not in new_chans:
                    debug_chan = getattr(config, 'DEBUG_CHANNEL', '#flac-debug').lower()
                    if chan != debug_chan:
                        irc_sock.send(f"PART {chan} :Removed from DDCore\r\n".encode())
                        announce.send_debug(f"Parting channel {chan} due to new configuration layout!", category="PART")
                        if chan.lower() in config.channel_users:
                            del config.channel_users[chan.lower()]
            
            print("[REHASH SYNC] Sending a background NAMES to keep the lists fresh...")
            for chan in new_chans:
                irc_sock.send(f"NAMES {chan}\r\n".encode())
                
            print(f"[REHASH SYNC] Channel sync completed successfully.")
        else:
            print("[REHASH WARNING] Could not sync the channels: no raw socket was available.")
        # ---------------------------------------------------------------------
        
        # 5. Confirm, through the VIP express lane
        announce.send_debug(f"Rehash completed! RAM-Memory preserved seamlessly without disk-paging.", category="INFO")
        
        # Clear any stale locks and ghost blocks left over before the rehash
        config.rar_inprogress = False
        if hasattr(config, 'user_processing_lock'):
            config.user_processing_lock = set()

        # Take the real, live network socket straight from memory
        oserve_mod = sys.modules.get('oserve')
        live_socket = getattr(oserve_mod, 'irc_connection', None) if oserve_mod else None
        
        if live_socket:
            import dcc
            import threading
            print("[REHASH-WAKE] Letting queued users into the free slots...")
            threading.Thread(
                target=dcc.check_queue_and_send, 
                args=(live_socket, "system_next_trigger_fallback"), 
                daemon=True
            ).start()
        else:
            print("[REHASH ERROR] Could not wake the queue automatically: live_socket was not in memory.")
        
    except Exception as e:
        import announce
        announce.is_ready = True
        print(f"[REHASH CRITICAL ERROR] The files could not be reloaded live: {e}")
        announce.send_debug(f"Rehash FAILED (Notices Resumed for safety): {e}", category="INFO")


def handle_hard_ban_request(user, target_chan, msg_text, authorised=False):
    """Add a permanent wildcard pattern to hard_bans.txt, straight from IRC."""
    import config
    import announce
    
    if not authorised and not is_admin(user):
        print(f"[SECURITY] Unauthorised user {user} tried to run !ban.")
        return

    parts = msg_text.split(" ", 1)
    if len(parts) < 2:
        announce.send_debug("Syntax error! Usage: !ban <pattern*>", category="INFO")
        return
        
    pattern = parts[1].strip().lower()
    if not pattern:
        return
        
    
    # db.add_hard_ban does the whole read-modify-write under the disk lock and
    # replaces the file atomically. The old code appended with no newline check,
    # so on a hand-edited file whose last line lacked one it glued two patterns
    # into one and silently unbanned both; and two !ban threads could interleave
    # and lose whichever entry lost the race.
    import db
    try:
        added = db.add_hard_ban(pattern)
    except Exception as ban_err:
        announce.send_debug(f"Could not write hard_bans.txt: {ban_err}", category="INFO")
        print(f"[HARD BAN ERROR] {user} could not add {pattern}: {ban_err}")
        return

    if added:
        announce.send_debug(f"Added permanent wildcard to hard_bans.txt: {config.C_BOLD}{pattern}{config.C_RESET}", category="BAN")
        print(f"[HARD BAN] {user} added a permanent pattern: {pattern}")
    else:
        announce.send_debug(f"Pattern {pattern} is already banned permanently.", category="INFO")

def handle_hard_unban_request(user, target_chan, msg_text, authorised=False):
    """Remove a permanent wildcard pattern from hard_bans.txt, straight from IRC."""
    import config
    import announce
    import os
    
    if not authorised and not is_admin(user):
        return

    parts = msg_text.split(" ", 1)
    if len(parts) < 2:
        announce.send_debug("Syntax error! Usage: !unban <pattern*>", category="INFO")
        return
        
    pattern = parts[1].strip().lower()
    if not pattern:
        return
        
    filename = config.HARD_BANS_FILE
    if not os.path.exists(filename):
        announce.send_debug("The permanent hard_bans.txt file is empty.", category="INFO")
        return
        
    # db.remove_hard_ban rewrites the file through a temp + os.replace. The old
    # code truncated it with open(..., "w") and wrote the survivors back one at a
    # time: a crash, a full disk or a kill in between left it short or EMPTY.
    #
    # That fails OPEN. security.check_user_status re-reads this file on every
    # command and only distrusts a "no match" when the read RAISED - a file that
    # is readable but truncated is indistinguishable from one with no bans in it,
    # so every hard-banned user is admitted until somebody notices.
    import db
    try:
        removed = db.remove_hard_ban(pattern)
    except Exception as unban_err:
        announce.send_debug(f"Could not write hard_bans.txt: {unban_err}", category="INFO")
        print(f"[HARD UNBAN ERROR] {user} could not lift {pattern}: {unban_err}")
        return

    if removed:
        announce.send_debug(f"Removed permanent wildcard from hard_bans.txt: {config.C_BOLD}{pattern}{config.C_RESET}", category="BAN")
        print(f"[HARD UNBAN] {user} lifted the permanent pattern: {pattern}")
    else:
        announce.send_debug(f"Pattern {pattern} was not found in hard_bans.txt.", category="INFO")

def handle_list_update_request(user, target_chan, authorised=False):
    """Run update_list.py, wait for it, and read the file count from line 1 of the list."""
    import subprocess
    import sys
    import os
    import re
    import config
    import announce
    import glob
    import threading
    import time
    
    if not authorised and not is_admin(user):
        print(f"[SECURITY] Unauthorised user {user} tried to run !update.")
        return

    # The global maintenance lock is only taken if the switch is True in config
    if getattr(config, 'PAUSE_ON_UPDATE', False) is True:
        if getattr(config, 'search_inprogress', False) is True:
            announce.send_debug(f"List update request from {user} denied: Another system scan is already running.", category="INFO")
            return
        config.search_inprogress = True
        print(f"[MAINTENANCE START] {user} ran !update. Searching and sharing are now PAUSED.")
        announce.send_debug(f"System maintenance initiated by {user}. MasterList is rebuilding, file requests temporarily paused...", category="INFO")
    else:
        print(f"[UPDATE START] {user} ran !update. The pause switch is False, so sharing continues meanwhile.")
        announce.send_debug(f"List update triggered by {user} from {target_chan}. Indexing NFS-drive...", category="INFO")


    # Reads ONLY line 1 of the real master list, so it costs almost nothing
    def get_count_from_list():
        try:
            # Find every text file in the directory matching the bot's name
            # LIST_BASE_NAME, not NICKNAME: irc.py rebinds NICKNAME on a 433 fallback, and
            # update_list.py names the files with LIST_BASE_NAME. Keyed off the live nick this
            # counted 0 both before and after a rebuild, so !update reported "0 files, added 0"
            # while the advert reported the real total. Matches list.find_latest_list().
            all_txt_files = sorted(glob.glob(os.path.join(config.LOCAL_LIST_DIR, f"{config.LIST_BASE_NAME}-*.txt")))
            
            # Filter out the RAR list, so only the true master list is read
            true_master_lists = [f for f in all_txt_files if "-RAR-" not in f]
            
            if true_master_lists:
                list_path = true_master_lists[-1]  # The very newest master list
                if os.path.exists(list_path):
                    with open(list_path, "r", encoding="utf-8", errors="ignore") as f:
                        first_line = f.readline().strip()
                        
                        # Look for the "List of X Files" pattern
                        match = re.search(r"List of\s+([\d,.]+)\s+Files", first_line, re.IGNORECASE)
                        if match:
                            raw_num = match.group(1).replace(",", "").replace(".", "")
                            if raw_num.isdigit():
                                return int(raw_num)
        except Exception as e:
            print(f"[LIST READ ERROR] Could not read line 1: {e}")
        return 0

    # 1. Take the previous real file count from line 1
    old_count = get_count_from_list()
    announce.send_debug(f"List update triggered by {user} from {target_chan}. Indexing NFS-drive, bot paused...", category="INFO")
    def async_list_updater():
        try:
            base_path = os.path.dirname(os.path.abspath(__file__))
            script_path = os.path.join(base_path, "update_list.py")
            
            if not os.path.exists(script_path):
                announce.send_debug(f"Critical Error: Could not find update_list.py", category="INFO")
                return
                
            # 2. Threaded run, waiting for the process without a blind time limit
            process = subprocess.run([sys.executable, script_path], capture_output=True, text=True, timeout=None)
            
            if process.returncode == 0:
                # ---------------------------------------------------------------------
                # NFS and disk sync: wait two seconds after the process closes.
                # That gives the NAS and the network buffer time to flush the files.
                # ---------------------------------------------------------------------
                print(f"[UPDATE-SYNC] The script finished. Waiting 2.0s for the disk to sync before reading...")
                time.sleep(2.0)
                # ---------------------------------------------------------------------
                
                # 3. Read the new file count from line 1
                new_count = get_count_from_list()
                
                # Work out the exact difference
                added_files = new_count - old_count
                if added_files < 0: 
                    added_files = 0
                
                # 4. Confirm, through the VIP express lane
                announce.send_debug(
                    f"List update successfully completed! MasterList now contains {config.C_BOLD}{new_count:,}{config.C_RESET} files. "
                    f"Added {added_files:,} new file(s) since last index.", 
                    category="INFO"
                )

            else:
                error_msg = process.stderr.strip() if process.stderr else "Unknown script error"
                announce.send_debug(f"External update_list.py failed (Exit Code {process.returncode}): {error_msg}", category="INFO")
                
        except subprocess.TimeoutExpired:
            announce.send_debug("List update FAILED: Script execution timed out after 90 seconds.", category="INFO")
        except Exception as e:
            print(f"[UPDATE ERROR] The list update could not be run: {e}")
            announce.send_debug(f"List update FAILED critical error: {e}", category="INFO")
        finally:
            # Release the global pause lock again
            config.search_inprogress = False
            
            # Clear the maintenance flag, so list.py knows the list is ready
            config.update_inprogress = False
            print("[MAINTENANCE END] Sharing and searching have been restarted automatically.")

    # Raise the maintenance flag, so the whole daemon knows an update is starting
    config.update_inprogress = True

    # Start the background thread
    threading.Thread(target=async_list_updater, daemon=True).start()
