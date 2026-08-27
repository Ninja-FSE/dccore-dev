# oserve.py - The central hub that wires every module together
import threading
import time
import sys
import os

# FIRST, before anything else can print. Several modules print at import time,
# and on a console whose code page cannot encode the Swedish log strings
# (cp1253, cp1251, cp932, ascii - anything but Western European) an unguarded
# print() raises UnicodeEncodeError and takes the thread down with it. See
# platform_compat.install_console_encoding_guard for the full explanation.
import platform_compat
platform_compat.install_console_encoding_guard()

# Load the bot's modules
import config

# Allocate the locks at startup, in memory. This keeps config.py free of
# function calls and imports.
if not hasattr(config, 'queue_lock'):
    config.queue_lock = threading.Lock()

if not hasattr(config, 'debug_flood_lock'):
    config.debug_flood_lock = threading.Lock()

if not hasattr(config, 'fetch_queue_lock'):
    config.fetch_queue_lock = threading.Lock()

import list
import dcc
import db
import announce
import irc        # The network connection to Undernet
import queue_mgr  # The flood-protection queue (round-robin)
import security   # User bans and muting
import stats_mgr  # Sizes, speed and uptime
import commands    # Every command a user can type

# Tracks unique users, for flood protection
config.send_queue = {}
bot_joined_channel = False

# Shared network reference, so threads always use the current live connection
irc_connection = None
threads_started = False

# Live traffic statistics, measured in real time by dcc.py
current_speed_bytes = 0    
active_downloads = 0       
send_fails_count = 0       
total_sent_bytes = 0       

def queue_message(user, message, is_vip=False):
    """The queue's entry point, with a strictly isolated VIP express lane."""
    user_key = user.lower()
    import config
    
    # VIP GATE: only genuine channel adverts, or messages explicitly flagged
    # is_vip=True, are allowed through here.
    if user_key == "channel_announce" or is_vip:
        config.vip_queue.append(message)
        return
        
    import queue_mgr
    if user_key not in queue_mgr.config.send_queue:
        queue_mgr.config.send_queue[user_key] = []
    queue_mgr.config.send_queue[user_key].append(message)



def startup():
    """Everything the daemon does before it touches the network.

    Split out of __main__ so a test can execute it. This was the one path CI
    could never run: every module was imported and every unit tested, but the
    boot itself was only exercised by starting the real bot, which connects to
    Undernet and joins live channels. It is also the first thing a Windows port
    meets.

    Behaviour is unchanged, sys.exit(1) on a missing music directory included -
    called from __main__ that ends the process exactly as before, and a test can
    assert the SystemExit instead.
    """
    print(f"--- {config.SCRIPT_VERSION} is starting up ---")
    if not os.path.exists(config.FILE_DIRECTORY):
        print(f"[CRITICAL] Saknar mapp: {config.FILE_DIRECTORY}")
        sys.exit(1)

    latest_list = list.find_latest_list()
    if not latest_list:
        print("[WARNING] No file list found in lists/ yet.")
    else:
        print(f"[INFO] Loaded the latest file list: {os.path.basename(latest_list)}")

    if os.path.exists(config.BANS_FILE):
        db.load_bans_from_file()

    # Read every saved queue slot back from disk at boot.
    db.load_dcc_queue()

    # ---------------------------------------------------------------------
    # SINGLE START: the queue is started exactly ONCE here, outside every loop,
    # so boot produces exactly one [QUEUE] line.
    # ---------------------------------------------------------------------
    import queue_mgr
    print("[SYSTEM] Starting the flood-protection queue...")
    threading.Thread(target=queue_mgr.queue_worker, daemon=True).start()

    # Cross-bot file fetch storage (dcc_fetch.py). Non-fatal on purpose,
    # unlike the FILE_DIRECTORY check above: FILE_DIRECTORY is a hard
    # precondition for the daemon's core purpose (serving the library), while
    # this is a newer, optional feature - a permissions failure here logs and
    # leaves fetch_feature_disabled set rather than taking the whole daemon
    # down. dcc_fetch/webserver check that flag before accepting an offer or
    # an enqueue request.
    try:
        fetched_dir = getattr(config, "FETCHED_FILES_DIR", "./data/fetched")
        os.makedirs(fetched_dir, exist_ok=True)
        config.fetch_feature_disabled = False
    except Exception as fetch_dir_err:
        print(f"[FETCH] Could not create {fetched_dir}: {fetch_dir_err}. Cross-bot file fetch disabled.")
        config.fetch_feature_disabled = True

    try:
        import dcc_fetch
        threading.Thread(target=dcc_fetch.fetch_dispatcher_worker, daemon=True).start()
    except Exception as fetch_worker_err:
        print(f"[FETCH] Could not start fetch dispatcher: {fetch_worker_err}")

    # Optional web dashboard (mostly read-only status views, plus the
    # cross-bot search/fetch routes - see webserver.py's module docstring).
    # Lazy import (not at module top) so a missing Flask install - the normal
    # case, since it is an optional dependency CI never installs - never
    # affects anything that imports oserve.py itself; only the dashboard
    # feature is unavailable.
    try:
        import webserver
    except Exception as web_err:
        print(f"[WEBUI] Could not import webserver: {web_err}")
    else:
        if getattr(config, "WEBUI_ENABLED", True):
            threading.Thread(target=webserver.start, daemon=True).start()
        else:
            print("[WEBUI] Disabled via config.WEBUI_ENABLED = False.")


def run_forever():
    """The reconnect loop. Never returns; only the network lives in here.

    The global declaration is load-bearing, not decoration. These two
    assignments used to sit at MODULE level inside __main__, so they rebound
    oserve.irc_connection and oserve.bot_joined_channel - which irc.py and
    dcc.py reach through sys.modules to find the live socket. Inside a function
    without this declaration they would quietly become locals, the reconnect
    cleanup would stop happening, and nothing would say so.
    """
    global irc_connection, bot_joined_channel

    # THE RECONNECT LOOP (network only)
    while True:
        try:
            # Hand the whole network job to the IRC module
            irc.irc_loop()
        except KeyboardInterrupt:
            print("\nShutting down...")
            sys.exit(0)
        except Exception as main_err:
            print(f"[CRITICAL MAIN ERROR] Huvudloopen dippade: {main_err}")

        # If the network dies, clear the socket cleanly before the next attempt
        irc_connection = None
        bot_joined_channel = False

        # Safety catch: if the network dies, make sure the advert knows
        import announce
        announce.is_ready = False

        print("[CONNECT] Lost the connection. Reconnecting to the IRC server in 10 seconds...")
        time.sleep(10)


if __name__ == "__main__":
    startup()
    run_forever()



