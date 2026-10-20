### 🩹 The never-connected streak follows a nick change, as the daily failure count does (#1437)

Found in review. `DAILY_SEND_FAILS_LIMIT` (#1389) stops counting sends that never connected once a run of them with no
connect in between reaches `NEVER_CONNECTED_STREAK` sends to `NEVER_CONNECTED_STREAK_NICKS` different nicks
(`dcc.never_connected_counts()`), since several downloaders failing the same way at once is the bot's own address.
The nicks are kept in `runtime.never_connected_streak_nicks`, keyed like `dcc_queue` (`dcc._send_fails_key()`), and
`irc.note_nick_change()` carried the daily count (`dcc.carry_send_fails()`) but not that entry: one user renamed
between two unanswered offers counted as two nicks.

- **`note_nick_change()` moves the old key to the new key in the streak** through
  `dcc.carry_never_connected_streak()`, beside `carry_send_fails()`, so only dcc.py knows the streak's layout. It
  takes only `send_connected_lock`, after `queue_lock` is released; nothing is taken while that lock is held. A new
  key already in the streak stays there once. The streak's length is unchanged. `"never_connected_streak"` is
  reported among what moved whenever the old nick was in the streak.
- A case-only rename moves nothing (the key is case-folded, so it is the same key). `runtime` is not reloaded by a
  rehash, so the streak survives it as before.
- A NICK that lands between `finish_send()` reading the nick and `never_connected_counts()` adding it can still leave the
  old key in; the window is microseconds, the same kind of race as #1460's.
- **Tests:** `tests/test_the_never_connected_streak_follows_a_nick_change.py` (20 tests):
  - through the real NICK line (`irc.answer_nick_change()`) between sends that never connect, with some send
    connected and the limit at 3: one unanswered offer to a nick, a rename, and two more to the new nick count all
    three and pause the new nick with one notice and its queue removed - driven through the real `start_dcc_send()`
    with a listener nobody dials and through `finish_send()`; one short of the limit it is still served; a mixed-case
    NICK line, two renames in a row and a case-only rename (nothing moved) are each still one nick;
  - a renamed user and a second user still make two nicks, and the third offer is not counted, with the log line;
  - the move on its own: the old key moves to the new one and says so; a new key already there is kept once; an old
    key not in the streak and an empty streak move nothing and are not reported; the run length and its log flag are
    unchanged; "never_connected_streak" is among what moved; a connect after a move still clears everything;
  - the move looks and changes under `send_connected_lock` and not inside `queue_lock`, with recording locks, and the
    lock is free afterwards.
  The related modules (`test_a_paused_nick_stays_paused_under_a_new_nick`, `test_a_rename_carries_the_users_state`,
  `test_a_nick_whose_sends_keep_failing_is_paused` and others) are unchanged and green.
- **Mutation checks, 13/13 caught:** the call in `note_nick_change()` removed, no `discard`, no `add`, the membership
  guard inverted, the guard removed, always returning True, no `moved.append`, the move without its lock, the call
  inside `queue_lock`, the keys swapped at the call, the keys swapped in the move, the move also resetting the run
  length, and `src/` back at the base (there the renamed nick stays at 2 failures, unpaused).
