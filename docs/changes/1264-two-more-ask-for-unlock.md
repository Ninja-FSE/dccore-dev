### 🩹 `checkupdates` and `consolefeed` with an argument ask for unlock, as `set` does (#1264 follow-up)

Since the settings window came to the console (#1264), a console session that logged in with a paired token reads
the settings and changes them only after `unlock <password>` once in that session: `set`, `setcommit`,
`served commit`, `folders commit`, `onconnect`, `pair` and `unpair` answer `DCCORE LOCKED <command> <sentence>`
until then. Two older commands that save a setting were not on that list: `checkupdates on|off` writes
`CHECK_FOR_UPDATES` and `consolefeed on|off` writes `DEBUG_TO_CONSOLE`, each through `settings_file.save()` and a
rehash.

- **`src/adminchat.py`:** `_cmd_checkupdates` and `_cmd_consolefeed` call `console_settings.refused_while_locked()`
  once the argument is known to be `on` or `off`, before anything is said or saved - the same check, reply and
  sentence as the other commands, no second copy. With no argument they only report the setting and stay allowed;
  a bad argument still gets its usage line. A session that logged in with the password, and the dashboard's own
  Console (behind its password login, `unlocked = True`), are not affected. A read-only token is refused both with
  an argument before the handler runs, as before.
- **`dccore.mrc`:** a `LOCKED checkupdates` or `LOCKED consolefeed` line is said in `@DCCore` (the bot's sentence,
  "Type unlock <password> first ..."), routed before the settings window's lines. The window's unlock sends the
  refused command word again without its `on|off`, and with the window closed the ten seconds of late replies
  would have taken the line and said nothing. The menu's "Daily update check" and "Console feed" items are refused
  the same way on a locked console; type `unlock <password>` in `@DCCore` and send it again. The control panel's
  "Console feed on" box (1685), which Apply sends as `consolefeed on|off`, would have kept the refused state with
  the window's baseline (`opt.sig`) taken with it, so it showed as saved and the status could say "Nothing has
  changed." until the window was reopened: on `LOCKED consolefeed` with the window open, the routing line also
  calls the new `dccore.sw.locked.toggle`, which ticks the box from the bot's live value as `dccore.sw.opt.fill`
  does, takes the baseline again, and sets the status "Console feed not changed: type unlock <password> in
  @DCCore, then Apply again."; the `@DCCore` line is said as well.
- **`docs/ADMIN-CONSOLE.md`:** both commands in the token paragraph, in "Settings over the console" and in the
  `LOCKED` row's list of command words; the settings window's password paragraph names the console-feed box as
  the exception (unlock in `@DCCore`, Apply again), and the `/dccore consolefeed on|off` row says a token login
  unlocks first.
- An audit of every other console command for `settings_file.save`, `_save_settings_and_rehash`,
  `apply_settings_changes` and the other `apply_*_changes`, and writes to `conf/`: none saves a setting outside
  the lock.
- **Tests:** `tests/test_two_toggles_ask_for_unlock_as_set_does.py` (20 tests), through `handle_command()` with the
  save, the rehash and the background run recorded: a token session that has not unlocked gets one
  `DCCORE LOCKED checkupdates|consolefeed <sentence>` for `on`, `off`, `OFF` and ` on `, and no "Turning ..." line, no
  save, no rehash, no background run, the setting and settings.conf unchanged; a person gets the sentence; with no
  argument both still report, a bad argument still gets its usage line; a password login, a session after `unlock`
  and the dashboard's Console save as before, a wrong password does not unlock; a read-only token is answered its own
  LOCKED line before the handler runs. dccore.mrc's dispatcher is evaluated on the bot's own LOCKED lines (full and
  read-only): said once in @DCCore with the sentence, whether or not the settings window waits for it or is in its
  tail, and `dccore.sw.locked.toggle` called for `consolefeed` only, only with the window open; `setcommit`, `set`,
  `served commit`, `onconnect` and `pair` go where they went. `dccore.sw.locked.toggle` is run on the window as
  Apply left it, the bot's value `on` and `off`: the box shows the bot's value, the baseline equals it, and the one
  status line names `unlock <password>`, `@DCCore` and Apply and fits control 1018. `handling()` in
  `tests/test_a_failed_rehash_is_not_said_to_be_in_effect.py` now runs a one-line body, a nested inline `if`,
  `$istok(<words>,$N,32)` and `$N == <word>` on the line's fields and `$dialog(dccore.set)`, and stops at the
  `return` (`route()` reads from it); the doc's
  `LOCKED` row names both; `TheAuditOfBothHalves.LOCKABLE` gains both.
- **Mutants:** 25 of 25 caught: either `refused_while_locked()` call deleted; either check moved above the no-argument
  report; the check above the usage check; the two command words swapped; `set` and `consolefeeds` for the words; in
  dccore.mrc the `return` dropped, `$3` for `$2`, `$2-` said for `$3-`, `consolefeed` left out of the list, the line
  moved below the settings window's routing; `src/adminchat.py`, dccore.mrc, and both, put back as on main; the
  box reset dropped, the baseline not taken again, the baseline taken before the reset, `did -c` and `did -u`
  swapped, the box ticked from its own state rather than the bot's, the status line dropped, the reset not called,
  called with the window closed, called for `checkupdates` too.
