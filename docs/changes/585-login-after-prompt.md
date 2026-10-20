### 🔐 dccore.mrc: the login line counts only once the password prompt was answered (#585 follow-up)

Security advisory GHSA-4mm3-288v-xvmg (high), fixed in its private fork and merged with this release.

The bot always sends `Enter Your Password:`, and `Entering DCC Chat Admin Interface` only once that prompt has been
answered. The chat's state is `auth` only then: the script's prompt branch sent the token after the peer check
(#585), or the operator typed the password. `password` means the prompt was shown and nothing has answered it yet. The
login branch now goes on to state `in` only from `auth`.

- **Any other state** (`password`, `banner`, `opening`, `waiting`, `closed`, `taken`, none): the login line is
  ignored, the window says "Ignored a login line that came before the password prompt was answered", and the chat is
  given up with `dccore.abandon`, as for a peer that fails the check: closed, no redial until `/dccore connect`. No
  `hello`, no `pair`, and no `DCCORE` line after it is read, as the structured feed still needs state `in`. A second
  login line in a chat already `in` changes nothing.
- **A typed password answers the prompt on every way of typing it:** the new `dccore.typedpass` (state `auth`,
  `typed`, then `dccore.sendpass`) is what `@DCCore` ran inline, and `@DCCore-console`, `/dccore raw` and the menu's
  console command use it too at the prompt. `@DCCore-console` showed and sent a password typed there as it was; it
  now shows `********`, as `@DCCore` does. Once logged in, all four send a line as before.
- **Each route keeps what it can of a spaced password (#1273):** `dccore.typedpass` and `dccore.sendpass` take the
  window the password was typed in (`-` for none), and `dccore.sendpass` reads that window's editbox, so
  `@DCCore-console` keeps runs of spaces as `@DCCore` does. `/dccore raw` and the menu have no editbox holding the
  password alone, and send it as mIRC gives it, closed up; `docs/ADMIN-CONSOLE.md` says so, and that
  `/dccore raw <password>` stays in mIRC's editbox history.
- **The menu's console command hides what is typed at the password prompt:** `dccore.askraw` asks with `$input`'s
  `p` flag (`eop`) in state `password`, and with the plain box (`eo`) otherwise.
- **Unchanged:** a token login, a typed password in a chat the script dialled after the check, a typed password in a
  chat opened with `/dccore connect` or `/dccore pair`, and a refused token followed by a typed password all reach
  `in` as before.
- `docs/ADMIN-CONSOLE.md` says what "the login" means for the script.
- The prompt branch calls the peer check as `$dccore.peerok(password)`, so the peer check before the prompt now stops
  the prompt branch when it says no. Written without the parentheses, mIRC passes an identifier no parameters, and
  the if-line read `$false password`, which is true.
- **Tests:** `tests/test_the_login_line_follows_the_prompt.py` runs `on ^*:CHAT`, `dccore.line`, `dccore.peerok`,
  `dccore.abandon`, `dccore.typedpass`, both `on *:INPUT` handlers, `/dccore` and `dccore.askraw` statement by
  statement with the evaluator of `tests/test_the_start_page_says_when_the_bot_is_offline.py`.
  `tests/test_a_refused_token_is_not_sent_again.py` and `tests/test_a_typed_password_keeps_its_spaces.py` follow the
  typed password into `dccore.typedpass`.
- **Mutation checks, all caught:** the guard removed, `banner` allowed, `password` allowed again, the abandon dropped,
  the `in` early return dropped, the negation dropped, the guard's `return` dropped, the `DCCORE` gate removed, the
  peer check written without parentheses, each typing route (`@DCCore-console`, `/dccore raw`, the menu) left
  without the move to `auth`, the menu's `p` flag dropped, and the console window reading `@DCCore`'s editbox.
