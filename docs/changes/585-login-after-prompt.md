### 🔐 dccore.mrc: the login line counts only after the password prompt (#585 follow-up)

The bot always sends `Enter Your Password:` before `Entering DCC Chat Admin Interface`, and the script's prompt branch
is where the peer check (#585) runs and the only place the chat's state becomes `auth` (token sent) or `password`
(the operator asked to type it). The login branch now goes on to state `in` only from one of those two states, so a
chat logs in only after the script asked for the password in it.

- **Any other state** (`banner`, `opening`, `waiting`, `closed`, `taken`, none): the login line is ignored, the window
  says "Ignored a login line from a chat that never asked for the password", and the chat is given up with
  `dccore.abandon`, as for a peer that fails the check: closed, no redial until `/dccore connect`. No `hello`, no
  `pair`, and no `DCCORE` line after it is read, as the structured feed still needs state `in`.
- **Unchanged:** a token login, a typed password in a chat the script dialled after the check, and a typed password in
  a chat opened with `/dccore connect` or `/dccore pair` all reach `in` as before.
- `docs/ADMIN-CONSOLE.md` says what "the login" means for the script.
- The prompt branch calls the peer check as `$dccore.peerok(password)`: written without the parentheses, mIRC passes an
  identifier no parameters, so the check could not tell the password case from the token case.
- **Tests:** `tests/test_the_login_line_follows_the_prompt.py` runs `on ^*:CHAT`, `dccore.line`, `dccore.peerok`,
  `dccore.abandon` and `on *:INPUT:@DCCore` statement by statement with the evaluator of
  `tests/test_the_start_page_says_when_the_bot_is_offline.py`.
- **Mutation checks, 8/8 caught:** the guard removed, `banner` allowed, the abandon dropped, the `in` early return
  dropped, only `auth` allowed, the negation dropped, the guard's `return` dropped, the `DCCORE` gate removed.
