### 🔁 The main nick is asked back only when it is freed, and at most every 30 seconds (#1291 follow-up)

When the bot is on its alternative nick, it asks for its main nick back as soon as the client holding it lets
go. It asked on a PART too, which frees no nick, so the server refused every one of those asks, and each was a
`NICK` written straight to the socket, outside the outbound pacer.

- **Only when the nick is freed:** `irc_loop()` calls `reclaim_main_nick()` on the holder's QUIT and NICK, no
  longer on PART.
- **Spaced:** `reclaim_main_nick()` asks at most once every `NICK_RECLAIM_GAP_SECONDS` (30 s), however often the
  holder comes and goes. Somebody else leaving does not use up the gap, and a clock set back does not stop the
  asks.
- **Kept across a rehash:** the time of the last ask is `runtime.nick_reclaim_asked_at`, so a `!rehash` that
  reloads irc.py does not reset the gap.
