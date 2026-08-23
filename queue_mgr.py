# queue_mgr.py - Dedikerad modul för OmenServe Flood Protection med isolerad VIP-kö
import time
import sys
import builtins
import socket
import config

# Skapa den vanliga flood-skyddskön
config.send_queue = {}

def queue_worker():
    """Flood protection worker med en helt fristående expressfil för sökningar och reklam!"""
    print("[QUEUE] Isolate-Priority flood protection worker started.")

    while True:
        try:
            # 🛡️ FIXAD: Socket-hämtningen låg tidigare UTANFÖR try-blocket. Allt som
            # kan kasta ett undantag måste ligga innanför, annars kan pumpen dö tyst.
            oserve_mod = sys.modules.get('oserve')
            current_sock = None
            if oserve_mod and hasattr(oserve_mod, 'irc_connection'):
                current_sock = oserve_mod.irc_connection

            # ---------------------------------------------------------------------
            # 🛡️ KÖTAK: Medan boten är frånkopplad hinner ingenting skickas, och båda
            # köerna växer obegränsat. Klipp de äldsta raderna så att RAM-minnet inte
            # äts upp under en lång frånkoppling.
            # ---------------------------------------------------------------------
            max_vip = getattr(config, 'MAX_VIP_QUEUE', 200)
            if hasattr(config, 'vip_queue') and len(config.vip_queue) > max_vip:
                dropped = len(config.vip_queue) - max_vip
                del config.vip_queue[:dropped]
                print(f"[QUEUE CAP] Slängde {dropped} gamla VIP-rader (taket är {max_vip}).")

            max_user = getattr(config, 'MAX_USER_SEND_QUEUE', 100)
            for q_user in list(config.send_queue.keys()):
                if len(config.send_queue.get(q_user, [])) > max_user:
                    q_dropped = len(config.send_queue[q_user]) - max_user
                    del config.send_queue[q_user][:q_dropped]
                    print(f"[QUEUE CAP] Slängde {q_dropped} gamla rader för {q_user} (taket är {max_user}).")

            # FIXED: hold everything while the bot is offline instead of draining into
            # a void. Both lanes below pop BEFORE testing `if current_sock:`, so once
            # irc.py started clearing oserve.irc_connection on close, every message
            # popped during a reconnect was silently discarded - search results, queue
            # notices and adverts alike, with no error anywhere. Waiting here keeps the
            # queues intact until a live socket exists; the caps above stop them growing
            # without bound during a long outage.
            if not current_sock:
                time.sleep(0.5)
                continue

            # ---------------------------------------------------------------------
            # ISOLERAD EXPRESSFIL (PRIO 1): Töm den fristående VIP-listan först!
            # ---------------------------------------------------------------------
            if hasattr(config, 'vip_queue') and config.vip_queue:
                msg = config.vip_queue.pop(0)
                try:
                    if current_sock:
                        current_sock.send(msg.encode())
                        if getattr(config, 'DEBUG_MODE', False):
                            print(f"[RAW OUT VIP] {msg.strip()}")
                except socket.error as net_err:
                    # 🛡️ FIXAD: Här stod tidigare "break". Det bröt sig ur while True:,
                    # tråden returnerade, och eftersom queue_worker startas EN gång i
                    # oserve.py utan någon övervakare var botens enda utgående
                    # meddelandepump död för resten av processens livstid. Boten
                    # återanslöt och såg frisk ut i kanalen medan ingenting som gick
                    # via queue_message någonsin skickades igen.
                    print(f"[QUEUE NET ERROR] Anslutningen är bruten ({net_err}). Rensar VIP och väntar på ny socket.")
                    config.vip_queue = []
                    time.sleep(1.0)
                    continue

                time.sleep(config.MSG_DELAY)
                continue # Gå direkt upp och kolla om det finns mer VIP-data
            # ---------------------------------------------------------------------

            # STANDARD-LINAN (PRIO 2): Om VIP-listan är tom, beta av de vanliga låtarna (Round-Robin)
            if config.send_queue:
                active_users = builtins.list(config.send_queue.keys())

                for user in active_users:
                    if user in config.send_queue and config.send_queue[user]:
                        msg = config.send_queue[user].pop(0)

                        try:
                           if current_sock:
                               current_sock.send(msg.encode())
                               if getattr(config, 'DEBUG_MODE', False):
                                   print(f"[RAW OUT] {msg.strip()}")
                        except socket.error as net_err:
                           print(f"[QUEUE NET ERROR] Anslutningen är bruten ({net_err}).")
                           break
                        except Exception as e:
                           print(f"[ERROR] Failed to send queued message: {e}")

                        time.sleep(config.MSG_DELAY)

                    if user in config.send_queue and not config.send_queue[user]:
                        del config.send_queue[user]

        except Exception as queue_err:
            print(f"[ERROR] Error inside queue worker loop: {queue_err}")
            time.sleep(1)

        time.sleep(0.1)
