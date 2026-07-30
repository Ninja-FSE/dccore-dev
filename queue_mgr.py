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
        oserve_mod = sys.modules.get('oserve')
        current_sock = None
        if oserve_mod and hasattr(oserve_mod, 'irc_connection'):
            current_sock = oserve_mod.irc_connection

        try:
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
                    print(f"[QUEUE NET ERROR] Anslutningen är bruten ({net_err}). Rensar VIP.")
                    config.vip_queue = []
                    break

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
