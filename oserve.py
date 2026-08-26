# oserve.py - Det centrala navet (Dirigenten för alla dina Python-moduler)
import threading
import time
import sys
import os

# Ladda alla botens specialiserade moduler
import config

# 🛡️ GLOBAL MINNES-ALLOKERING: Skapar låsen direkt i RAM vid uppstart!
# Detta håller config.py 100% ren från funktionsanrop och imports.
if not hasattr(config, 'queue_lock'):
    config.queue_lock = threading.Lock()

if not hasattr(config, 'debug_flood_lock'):
    config.debug_flood_lock = threading.Lock()

import list
import dcc
import db
import announce
import irc        # Hanterar nätverksporten till Undernet
import queue_mgr  # Hanterar flood-skyddskön (Round-Robin)
import security   # Hanterar användarbans och mutening
import stats_mgr  # Hanterar storlekar, hastighet och uptime
import commands    # Hanterar alla kommandon som användare kan skriva

# Kön för att hålla koll på unika användare (Flood Protection)
config.send_queue = {}
bot_joined_channel = False

# Global nätverksreferens så att trådar alltid använder den senaste live-anslutningen
irc_connection = None
threads_started = False

# Globala variabler for levande trafikstatistik (Mäts i realtid via dcc.py)
current_speed_bytes = 0    
active_downloads = 0       
send_fails_count = 0       
total_sent_bytes = 0       

def queue_message(user, message, is_vip=False):
    """Central hjälpfunktion för kön - Nu med stenhårt isolerad och kontrollerad VIP-express!"""
    user_key = user.lower()
    import config
    
    # VIP-SLUSS: Enbart äkta kanalreklam eller meddelanden som explicit flaggats som is_vip=True släpps in här!
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
        print("[WARNING] Ingen fillista hittades i lists/ ännu.")
    else:
        print(f"[INFO] Laddade senaste fillistan: {os.path.basename(latest_list)}")

    if os.path.exists(config.BANS_FILE):
        db.load_bans_from_file()

    # NYTT: Läser in alla sparade köplatser från hårddisken direkt vid boot!
    db.load_dcc_queue()

    # ---------------------------------------------------------------------
    # STENHÅRD SINGEL-START:
    # Vi startar enbart kön EN ENDA GÅNG här, helt utanför alla looppar!
    # Detta garanterar att du bara får en enda [QUEUE] på skärmen vid boot.
    # ---------------------------------------------------------------------
    import queue_mgr
    print("[SYSTEM] Initierar flood-skyddskön...")
    threading.Thread(target=queue_mgr.queue_worker, daemon=True).start()

    # Optional read-only web dashboard. Lazy import (not at module top) so a
    # missing Flask install - the normal case, since it is an optional
    # dependency CI never installs - never affects anything that imports
    # oserve.py itself; only the dashboard feature is unavailable.
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

    # CENTRAL ÅTERANSLUTNINGSLOOP (Hanterar ENBART nätverket!)
    while True:
        try:
            # Överlämna hela nätverksarbetet till IRC-modulen
            irc.irc_loop()
        except KeyboardInterrupt:
            print("\nStänger av...")
            sys.exit(0)
        except Exception as main_err:
            print(f"[CRITICAL MAIN ERROR] Huvudloopen dippade: {main_err}")

        # Om nätverket dör, rensar vi socketen snyggt inför nästa försök
        irc_connection = None
        bot_joined_channel = False

        # Säkerhetsspärr: Om nätverket dör, se till att reklamen vet om det
        import announce
        announce.is_ready = False

        print("[CONNECT] Tappade anslutning. Återansluter till IRC Server om 10 sekunder...")
        time.sleep(10)


if __name__ == "__main__":
    startup()
    run_forever()



