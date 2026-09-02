"""Record what other bots say in a channel, so the advert parser can be
written against real traffic instead of guesses.

This produced the fixtures in tests/test_advert_listener.py: 392 lines from
one busy public file-sharing channel, 33 bots, four different advert
wordings, five of them splitting their advert across two messages. None of
that was predictable from the outside, and every one of those facts is now
a test.

Run it again to refresh the sample, or to look at a channel this one has never
seen:

    python scripts/capture_adverts.py --nick observer_x --channel "#example-channel" \
        --minutes 25 --out adverts.log

Output is one tab-separated row per message: time, kind (PRIVMSG / NOTICE /
CTCP), sender, body. Nothing is filtered - the noise is the point, since the
parser has to tell an advert apart from everything else in a busy channel.

IT ONLY LISTENS

It joins, writes down what it hears, and leaves. The only bytes it ever sends
are NICK/USER/JOIN at the start, PONG in reply to the server's PING, and QUIT
at the end. It answers no one, requests nothing, and never speaks in the
channel - so it cannot disrupt the channel it is measuring.

Use a nick that is not the daemon's. Two clients on one nick collide, and the
one that loses is the bot that was serving files.
"""

import argparse
import io
import socket
import sys
import time

CTCP = "\x01"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--server", default="irc.undernet.org")
    parser.add_argument("--port", type=int, default=6667)
    parser.add_argument("--nick", required=True,
                        help="a nick of its own - NOT the daemon's")
    parser.add_argument("--channel", required=True)
    parser.add_argument("--minutes", type=float, default=20.0,
                        help="adverts run on a five-minute cycle, so give it "
                             "at least twenty to see most of a channel")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    deadline = time.time() + args.minutes * 60
    out = io.open(args.out, "w", encoding="utf-8", newline="\n")
    written = 0

    sock = socket.create_connection((args.server, args.port), timeout=30)
    sock.settimeout(5.0)

    def send(line):
        sock.sendall((line + "\r\n").encode("utf-8", "replace"))

    # The username field must be lowercase on Undernet: it answers anything
    # else with "468 Your username is invalid" and closes the link before
    # registration finishes. irc.py sends config.IDENT, which defaults to
    # "dccore", for exactly this reason.
    ident = "".join(c for c in args.nick.lower() if c.isalnum()) or "observer"
    send(f"NICK {args.nick}")
    send(f"USER {ident} 0 * :advert capture")

    buffer = b""
    joined = False
    try:
        while time.time() < deadline:
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                continue
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                # Decoded exactly the way irc.py decodes the socket, so the
                # sample is what the daemon would actually have seen - several
                # bots decorate with bytes that do not survive this.
                line = raw.decode("utf-8", "ignore").rstrip("\r")
                if not line:
                    continue

                if line.startswith("PING"):
                    send("PONG" + line[4:])
                    continue

                if not joined and (" 001 " in line or " 376 " in line or " 422 " in line):
                    send(f"JOIN {args.channel}")
                    joined = True
                    print(f"[capture] joined {args.channel}", flush=True)
                    continue

                # A nick collision would otherwise leave it unregistered and
                # silent for the whole run.
                if " 433 " in line:
                    args.nick += "_"
                    send(f"NICK {args.nick}")
                    continue

                if " PRIVMSG " in line or " NOTICE " in line:
                    sender = line[1:].split("!", 1)[0] if line.startswith(":") else "?"
                    body = line.split(" :", 1)[1] if " :" in line else line
                    kind = "CTCP" if body.startswith(CTCP) else (
                        "NOTICE" if " NOTICE " in line else "PRIVMSG")
                    out.write("%s\t%s\t%s\t%s\n"
                              % (time.strftime("%H:%M:%S"), kind, sender, body))
                    out.flush()
                    written += 1
    except KeyboardInterrupt:
        print("[capture] stopped early", flush=True)
    finally:
        try:
            send("QUIT :done")
            sock.close()
        except Exception:
            pass
        out.close()

    print(f"[capture] {written} line(s) written to {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
