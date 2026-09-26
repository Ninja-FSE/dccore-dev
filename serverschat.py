"""DCCore Chat, relayed by the bot (#371).

Public chat between operators, over a NOTICE to a channel whose first word
is the tag `[ServersChat]`. The bot is the relay: it reads a tagged NOTICE
in the channels it is in and hands it to the admin session (dccore.mrc's
DCCore Chat window), and it sends what the operator types there as a tagged
NOTICE of its own. The operator's mIRC does not have to be in any channel.

THE WIRE IS `[ServersChat] <text>`, nothing else. The sender is the nick that
sent the NOTICE - the bot, for a relayed line, or a person on plain mIRC -
which the server vouches for. No name goes inside the text: a name there is
a claim anybody can write, and the format cannot change once people use it.
The tag itself is a presentation filter, not a trust boundary.

WHAT ARRIVES is other people's text, on the IRC read loop, so it is taken
the way every capture there is (irc._capture_chat_notice() wraps it in
never_breaks_the_read_loop):

- the tag is the FIRST word, and a NOTICE that is not chat is not touched;
- only channels the bot is in, never our own nick;
- control codes stripped, the text capped;
- a per-nick limit, so one sender cannot fill the operator's window;
- memory bounded, and IN MEMORY ONLY: the recent lines are a record of what
  other people said, and none of it is ever written to disk;
- it never reaches dispatch, and it never answers: nothing on this path
  sends anything (RFC 2812 - a NOTICE is never answered automatically).

WHAT IS SENT is what an authenticated operator typed (`chat #chan text`),
through the same paced outbound queue as everything else the bot says, with
a cap of its own so a chatty moment cannot delay the queue's own notices.

State is runtime.py's - see the chat_* names there - so a rehash that
reloads this module keeps the recent lines and the limits.
"""

import re
import sys
import time

import defaults as config
import runtime

TAG = "[ServersChat]"

# What one arriving line may carry, in characters, after stripping.
MAX_TEXT = 400
# What one sent line may carry, in BYTES: "NOTICE #channel :[ServersChat] "
# plus this must stay inside IRC's 512-byte line.
MAX_SEND_BYTES = 350

# Arriving: more than INBOUND_MAX lines in INBOUND_PER seconds from one nick
# hides that nick for INBOUND_HIDE seconds.
INBOUND_MAX = 5
INBOUND_PER = 10
INBOUND_HIDE = 60

# Sent: at most OUTBOUND_MAX lines per OUTBOUND_PER seconds per session.
OUTBOUND_MAX = 6
OUTBOUND_PER = 60

# The recent lines a window that (re)connects is shown.
RECENT_MAX = 50

# Past this many tracked nicks the stale ones are dropped.
_TRACK_MAX = 200

_FORMATTING = re.compile(r"\x03(\d{1,2}(,\d{1,2})?)?|[\x02\x0f\x16\x1d\x1e\x1f]")
_CHANNEL_PREFIXES = ("#", "&", "+", "!")


def _plain(text):
    """Colour and control codes out, every other control character a space."""
    text = _FORMATTING.sub("", str(text or ""))
    return "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in text).strip()


def chat_text(message):
    """The text of a chat NOTICE, or None when it is not one. The tag has to
    be the first word - anywhere else, it is somebody's sentence."""
    words = str(message or "").split(None, 1)
    if not words or words[0].lower() != TAG.lower():
        return None
    return words[1] if len(words) > 1 else ""


def channels():
    """The channels the bot is in, which are the ones it relays."""
    with runtime.channel_users_lock():
        names = list((getattr(config, "channel_users", {}) or {}).keys())
    return sorted(str(name).lower() for name in names
                  if str(name)[:1] in _CHANNEL_PREFIXES)


def _next_id(now):
    """A line's id: its time in milliseconds, strictly increasing. The window
    remembers the last one it drew, so a replay after a reconnect is not
    drawn twice - and ids that are times keep increasing across a restart."""
    with runtime.chat_lock:
        candidate = int(now * 1000)
        if candidate <= runtime.chat_last_id:
            candidate = runtime.chat_last_id + 1
        runtime.chat_last_id = candidate
    return candidate


def _record(chan, nick, text, now):
    line = {"id": _next_id(now), "chan": chan, "nick": nick, "text": text}
    with runtime.chat_lock:
        runtime.chat_recent.append(line)
        del runtime.chat_recent[:-RECENT_MAX]
    return line


def structured_line(line):
    """`DCCORE CHAT <id> <chan> <nick> <text>` - the text last, as every
    feed line's free text is."""
    import adminchat
    return (f"DCCORE CHAT {int(line['id'])} {adminchat._channel_token(line['chan'])} "
            f"{adminchat._clean(line['nick'], token=True)} {adminchat._clean(line['text'])}")


def channels_line():
    return ("DCCORE CHANNELS " + " ".join(channels())).rstrip()


def recent_lines():
    with runtime.chat_lock:
        recent = list(runtime.chat_recent)
    return [structured_line(line) for line in recent]


def _deliver(line):
    """To the operator's console session, if there is one. A pure append to
    its outbox - never the socket - since this runs on the IRC read loop."""
    try:
        import adminchat
        session = adminchat.active_session()
    except Exception:
        return
    if session is None or not getattr(session, "authenticated", False):
        return
    if getattr(session, "structured", False):
        session.send(structured_line(line))
    else:
        session.send(f"[CHAT] {line['chan']} <{line['nick']}> {line['text']}")


def _limited(table, key, most, per, now):
    """Count one event for `key` in `table` ({key: [window_start, count]});
    True when it is past `most` in `per` seconds."""
    record = table.get(key)
    if record is None or now - record[0] >= per:
        table[key] = [now, 1]
        return False
    record[1] += 1
    return record[1] > most


def _prune(now):
    """Keep the limit tables bounded: past _TRACK_MAX nicks, drop the ones
    whose window and hide are both over."""
    if len(runtime.chat_rate) > _TRACK_MAX:
        for key in [k for k, (start, _n) in runtime.chat_rate.items()
                    if now - start >= INBOUND_PER]:
            runtime.chat_rate.pop(key, None)
    for key in [k for k, until in runtime.chat_muted.items() if until <= now]:
        runtime.chat_muted.pop(key, None)


def capture(nick, target, message, now=None):
    """A NOTICE arrived. Relay it if it is chat on a channel we are in.
    Returns the recorded line, or None. Sends nothing, ever."""
    text = chat_text(message)
    if text is None:
        return None
    chan = str(target or "").lower()
    if chan[:1] not in _CHANNEL_PREFIXES or chan not in channels():
        return None
    nick = str(nick or "").strip()
    if not nick or nick.lower() == str(getattr(config, "NICKNAME", "")).lower():
        return None
    text = _plain(text)[:MAX_TEXT]
    if not text:
        return None
    now = time.time() if now is None else now
    key = nick.lower()
    with runtime.chat_lock:
        _prune(now)
        if runtime.chat_muted.get(key, 0) > now:
            return None
        if _limited(runtime.chat_rate, key, INBOUND_MAX, INBOUND_PER, now):
            runtime.chat_muted[key] = now + INBOUND_HIDE
            hidden = True
        else:
            hidden = False
    if hidden:
        print(f"[CHAT] {nick} is sending too fast in {chan}: hidden for {INBOUND_HIDE}s.")
        _deliver({"id": _next_id(now), "chan": chan, "nick": "*",
                  "text": f"{nick} is sending too fast: hidden for {INBOUND_HIDE} seconds."})
        return None
    line = _record(chan, nick, text, now)
    _deliver(line)
    return line


def _enqueue(key, line):
    """Onto the bot's paced outbound queue, the lane oserve.queue_message()
    uses for everything addressed to one target."""
    oserve = sys.modules.get("oserve")
    if oserve is not None and hasattr(oserve, "queue_message"):
        oserve.queue_message(key, line)
        return
    with runtime.send_queue_lock:
        config.send_queue.setdefault(key.lower(), []).append(line)


def say(sender, channel, text, now=None):
    """Send what an operator typed as a tagged NOTICE. (ok, message)."""
    chan = str(channel or "").strip().lower()
    if chan[:1] not in _CHANNEL_PREFIXES:
        return False, "Usage: chat #channel <text> - or just chat, for the channels."
    if chan not in channels():
        return False, f"Not in {chan}: the bot can only chat in its own channels ({', '.join(channels()) or 'none yet'})."
    clean = _plain(text)
    clean = clean.encode("utf-8")[:MAX_SEND_BYTES].decode("utf-8", "ignore").strip()
    if not clean:
        return False, "Nothing to send."
    now = time.time() if now is None else now
    with runtime.chat_lock:
        if _limited(runtime.chat_outbound, str(sender or "console").lower(),
                    OUTBOUND_MAX, OUTBOUND_PER, now):
            return False, (f"Slow down: {OUTBOUND_MAX} chat lines a minute, so chat "
                           f"never holds up the queue's own messages.")
    _enqueue(chan, f"NOTICE {chan} :{TAG} {clean}\r\n")
    # The server does not send a NOTICE back to whoever sent it, so the
    # operator's own line is recorded and shown from here.
    _deliver(_record(chan, str(getattr(config, "NICKNAME", "") or "?"), clean, now))
    return True, f"Sent to {chan}."
