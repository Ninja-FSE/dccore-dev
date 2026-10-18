### 🔐 A token login no longer clears the address's failed-password count (#1264, #1374 follow-up)

The DCC console counts wrong passwords per address, at the login prompt and in `unlock` (#1264), and blocks the
address for 15 minutes at `MAX_PASSWORD_ATTEMPTS` (3). `_check_password()` called `clear_bad_ip()` for a paired
token (full or read-only, #1374) as for the password; only the admin password clears it now.

- **The password clears, a token takes back its own attempt.** At the prompt, `clear_bad_ip()` runs only when the
  admin password itself verified (as typed or as `DCCORE PASSWORD <value>`), as in `unlock` already. A token login
  calls the new `release_password_attempt(ip, reservation)`, which takes back the one attempt counted for it, on the
  record it was counted on only: a record cleared since (by the password, or as stale) and made again by a later
  attempt is left alone. The count goes down by one, a block is lifted when, without this attempt, the count is
  under the limit, and the last-failure time goes back to what it was when no other attempt was counted meanwhile
  (token logins side by side can still move it forward, which only keeps the failures longer). A right token is
  still not counted as a wrong password.
- **`_reserve_attempt(ip)`** is `reserve_password_attempt()` answering `(counted, reservation)`: `None` when nothing
  was counted, otherwise the record the attempt was counted on with its count and last-failure time ahead of it.
  `reserve_password_attempt()` wraps it and still answers a bool, so `cmd_unlock()` and every caller are unchanged.
- **Unchanged:** `unlock` already counted on the address and refused a blocked one; a token login on a blocked
  address is refused, as before; the 1 s delay, the block length, and the dashboard's own login limit
  (`web_login.py`).
- **`docs/ADMIN-CONSOLE.md`:** "address is temporarily blocked" says only the admin password clears the count.
- **Tests:** `tests/test_a_token_login_keeps_the_failed_password_count.py` (24 tests), over the real
  `_check_password()`, `cmd_unlock()` (through `handle_command()`), `reserve_password_attempt()`,
  `release_password_attempt()` and `clear_bad_ip()`, with only `_promote()` and the delay stood in for: wrong
  unlocks and wrong passwords spread over full and read-only token logins reach the block after 3; a blocked
  address is refused at the prompt with either token and at `unlock`, the right password included; the password
  still clears the record at the prompt and in `unlock`; a token login leaves an existing count, block and
  last-failure time exactly as they were and adds no record to an address without one; an `_IN_USE` or
  `_SUPERSEDED` token login leaves it too; wrong unlocks add up on the address across sessions; the release on its
  own (no record, a block its attempt set, another attempt counted meanwhile, a record cleared meanwhile, a record
  cleared or dropped as stale and made again, a refused attempt, no address). The code before this fails 23 of
  the 24.
- **Mutation checks, 14/14 caught:** a token login clearing the record again; the token's reservation not taken
  back; `unlock` checking the block without counting; `clear_bad_ip()` kept before `_promote()`; a read-only token
  clearing; the block not applied at `unlock`; the release not lifting the block, not restoring the time, always
  deleting the record, restoring the time after another attempt, getting no earlier count and time, dropping the
  same-record check, or acting on whatever record is there now; a right `unlock` password not clearing.
