# DCCore Version Updates & Project Log 📝

All version changes, optimizations, and bug fixes made over time in the DCCore project are logged here.

## 🟨 Unreleased

### 🚦 A pasted album is not a flood, and the mute notice no longer says the queue was cleared (#888)

Follow-up to #886, with the operator's decision. Every line to the bot counted toward the flood gate -
`MAX_REQUESTS` (10) per `REQUEST_WINDOW` (5 s) - and a file request counted exactly like a search. So a user
pasting fifteen rows from the list, one album and the ordinary way these lists are used, got lines 1-10
queued, a **mute** on line 11, and a **one-hour ban** (`FLOOD_BAN_SECONDS`) on line 12, whenever their client
sent the paste faster than two lines a second. Undernet paces a paste itself - a burst, then roughly one line
every two seconds - which is the only reason this was rare rather than routine; any client, bouncer or network
that sends faster turned asking for an album into a ban.

**And the mute notice made it worse, which is the part that actually bit.** It said *"You are moving too fast!
Ignored and queue cleared for 30 seconds."* What `is_flooding()` drops is `config.send_queue` - the user's
pending outbound **replies**. `config.dcc_queue`, their file queue, is untouched and still sent. So a user was
told their queue had gone, asked again because of it, and asking again during a mute is the one action that
escalates to the hour. The notice was not merely inaccurate; it was the instruction that earned the ban.

The operator's decision, quoted: *"fix the misleading queue cleared for sure and i think requesting should be
handled more gently. i dont care if they paste a lot of lines at channel at once as long as the channel
accepts it. bot should just add them to queue one by one as he requests."*

- **File requests are not metered.** `irc.py` keeps `is_bot_command` exactly as it was - it is the dispatch
  set, and `tests/test_irc_dispatch.py` lifts it out of the source and evaluates it, so it may not lean on a
  local computed outside itself - and adds `is_file_request` as its own self-contained expression beside it.
  A file request never mutes, never escalates a mute into a ban, and is served during a mute earned by other
  commands. Searches, `-que`, `-remove`, list requests and the CTCPs are metered exactly as before, and
  `check_user_status()` still runs first, so a banned user's requests are refused as they always were.
- **The bound is the queue**, which already existed and is already a setting: `MAX_USER_QUEUE` (100) per
  person, `MAX_GLOBAL_QUEUE` (1000) overall. What else bounds the cost, none of it the gate: the library
  lookup is bounded by #580/#886, packing by the packer's one-at-a-time interlock, and how fast the lines may
  arrive at all by the server's own flood control - which is what the operator is relying on.
- **Past the cap the user is told once.** With the gate gone a 150-row paste against a 100-file cap would send
  fifty identical refusals, one per refused row, each costing the outbound pace every other user's replies are
  waiting on. `announce.send_dcc_error()` suppresses a repeat of `user_full`/`global_full` only - every other
  error names something about *that* request, so repeating one answers a different question. The memory is
  dropped the moment one of that user's requests succeeds (`forget_queue_full_notice()`, called from both
  success notices), so a refusal never goes silent once their queue drains; `QUEUE_FULL_REPEAT_SECONDS` is a
  backstop rather than the mechanism, and the memory is capped like every other one on this path.
- **The mute notice says what is true:** *"You are moving too fast! Other commands are ignored for 30 seconds
  - any files you have queued are safe and still on their way."* The operator's log line and the debug feed
  said "Queue cleared" too, and now say which queue was dropped and which was not.

`tests/test_a_pasted_album_is_not_a_flood.py` (11): the notice no longer claims the queue was cleared and says
what was actually ignored, the file queue is checked untouched while `send_queue` is checked dropped, the
meter still mutes and still escalates for everything else, and the told-once behaviour - one notice for a
whole refused batch, the two caps counted separately, per user rather than one global latch, every other error
still repeating, a success making the next refusal news again, and the memory bounded. `FloodGateCoverageTests`
in the dispatch tests changed shape but not strength: it used to assert the gate is never narrower than the
dispatch, since a gate narrower than the dispatch is an unmetered command path. There is now exactly one such
path, deliberately, so it asserts instead that every dispatched message is metered *or* is the file-request
exemption, that the exemption covers the file requests, and that it covers nothing else - a second unmetered
path added later still fails there.

### 📋 A batch of requests pasted from the list is served, not refused (#886)

Reported live, from the same evening as #879/#884: a user pasted nine request lines in about seven seconds -
the ordinary way these lists are used - and was answered `Error: Busy looking up other files - try again in a
moment`, which is also the one piece of advice that walks a novice into the flood gate (ten messages per five
seconds).

Three things made that the normal outcome rather than an unlucky one. Every row in a list is a bare filename
while the files live in subfolders, so `os.path.join(base_directory, requested_file)` never exists and *every*
request took #580's library scan - stream every published list, then walk every configured folder. Only
*misses* were remembered (`_lookup_misses`), so nothing a successful scan learned was ever reused: the same
file asked for twice was scanned twice. And the two scan slots were taken with a **non-blocking**
`acquire()`, so seven of those nine bounced instantly.

Three memories now, each verified before it is trusted, none of them deciding what may be sent -
`is_safe_path()` still checks every resolved path against every configured root afterwards, unchanged:

- `_remembered_path()` - the path a recent scan resolved a name to (`LOOKUP_HIT_TTL_SECONDS`, 5 minutes,
  `LOOKUP_HIT_MEMORY` entries). Re-checked on disk before it is used, so a library reorganised between two
  requests costs one stale check rather than a wrong answer.
- `_in_a_recent_folder()` - the folders recent lookups landed in (`LOOKUP_FOLDER_MEMORY`, newest first). A
  batch is nearly always siblings in one album, so the second row onwards is one `os.path.exists` instead of
  a scan. This is what the reported case needed: the hit memory alone does nothing for nine *distinct* names.
- `LOOKUP_SCAN_WAIT_SECONDS` (5) - the scan slot is waited for instead of refused at once. "Busy" is still the
  answer when the wait itself runs out.

`forget_library_lookups()` drops all three. `tests/test_a_batch_of_requests_is_not_refused.py` (10): one scan
for a whole pasted batch, a repeat costs nothing, a sibling costs nothing, a file that has since gone is not
served from memory, the TTL and both caps, the slot is waited for rather than bounced, "busy" still arrives
when the wait expires, and #580's miss memory is untouched. All ten fail on the old code. `OnlyAFewScansRunAtOnce`
in the #580 tests now shortens the wait and says why.

### 🗣️ The person downloading is told their own client never accepted it (#884)

Follow-up to #879, from the same operator, with the user's words this time: *"it says active transfer started
then gives error"* - and the bot's answer to them, `Error: Could not send 00-<release>-ELITE.nfo (transfer did
not complete). Ask for it again when you are ready.` #879 fixed what the operator sees; the user still got a
line that names no cause and suggests no action, for a failure that is entirely theirs to fix - a DCC prompt
nobody answered inside the window, or a client set to ignore that file type (this one could take a `.jpg` and
never a `.nfo`, which is a DCC ignore list almost every time). The one person who could fix it was the only
one not told what was wrong.

`dcc.never_connected_advice()` is the user-facing half of `never_connected_reason()`: the accept branch sets a
flag the `finally` reads, so the notice says *"Could not send &lt;file&gt; - your client never accepted it. Look
for a DCC prompt and accept it, and check your client is not set to ignore this kind of file. Ask for it again
when you are ready."* Every other failure keeps its old wording. The notice is rendered through
`announce.fit_irc_line()` like the other outbound lines that carry a filename (#162 finding #31) - with the
reason now a sentence, a long name pushed it past 512 bytes, where the server's cut takes the tail that says
what to do. `tests/test_the_user_is_told_their_client_never_accepted_it.py`: the advice itself, the notice
through the real send path with nobody on the other end, the trimming (driven at the settling, since a name
that overflows an IRC line is longer than Windows will create on disk), and the control that every other
failure reads as before. Three of five fail on the old code.

## 🟩 v1.13.0 (2026-09-22) - "The Audit Release"

### 🔐 Setup can ask for the operator's services host (#811)

Follow-up to audit M52/H4 (#654/#579): `is_admin(user, host)` already refuses a channel admin command or a
diagnostic whose sender cannot be matched against `ADMIN_HOSTMASKS`, but a fresh install had no way to set that
pattern from setup - the browser page and `configure.py` asked for `ADMIN_NICK` alone, and a first-timer stayed
nick-gated until they found `ADMIN_HOSTMASKS` on the Settings page or in `admin_config.py` by hand.

Both setup paths now ask for the operator's services host right after the admin nick, optionally - blank skips
it exactly as before. `configure.py` prompts in the terminal (`settings_file.admin_host_problem()` validates
the shape and refuses a wildcard outright, so the L5/#669 breadth mistake cannot be typed in here in the first
place); the browser setup page adds the same field with its own "?" help, in English, French and Spanish.
Either way, an answer is wrapped as `ADMIN_HOSTMASKS = ["*!*@<host>"]` and written to `settings.conf`, the same
file the dashboard's own edit of the setting already writes to. ADMIN-CONSOLE.md's step 3 and its
troubleshooting entry say what happens on both branches - answered, or left for later.
`tests/test_configure.py`, `tests/test_set_it_up_in_the_browser.py` and
`tests/test_the_console_guide_says_what_configure_does.py` cover the terminal path, the browser path and the
guide's wording against the real prompts.

### 🔐 The console listener also answers a LAN hairpin (#881)

Reported directly by the operator: `ADMIN_CHAT_MODE = "listen"` (#680) accepts a connection only from the
address the client's CTCP advertised. When the operator and the bot share one home router, the CTCP advertises
the router's public IP - the only address the client knows for itself - but the operator's own connection to
the bot's listener comes out the LAN side and arrives with a private source address instead, which the exact
match rejected as a stranger. `ADMIN_CHAT_MODE = "connect"` does not help either: dialling the same shared
public IP back in depends on the router supporting NAT hairpin/loopback for an arbitrary DCC port, which most
home routers do not, and the dial just times out.

`adminchat._is_private_address()` widens the listener's match: a peer is also accepted when its source address
is private (RFC1918) or link-local - never loopback, which is not a LAN-sharing case and is what every
same-machine test connection in this suite necessarily arrives over. A peer reaching the port from the public
internet can never present a private source address unless it is already inside the trusted network, so this
does not reopen the scanner risk #680 closed. `tests/test_a_lan_hairpin_reaches_the_console.py`: the helper on
its own (RFC1918, link-local, a documentation range, loopback excluded, garbage input), and the listener over a
real socket with the reported peer address patched to the operator's LAN address - served despite not matching
the advertised one - and the control, a public stranger, still dropped. ADMIN-CONSOLE.md says so.

### 📵 A receiver that never connects is said to be that, and the wait is a setting (#879)

Reported from a night's console feed: a run of `Failed: "<file>" to <nick> - the send blocked for the whole
socket timeout with the receiver not draining it. (0B of 0B arrived)` between transfers completing at 3 MB/s
to other people in the same minutes; one user's two `.nfo` offers failed that way and their `.jpg` went
through a minute later. Nothing was wrong with the link. `start_dcc_send()` listened with a fixed 30 s timeout
and its `accept()` raised `socket.timeout` into the send loop's own `except socket.timeout`, whose wording
describes a send that stalled - and `report_failure()` was called without the byte counts, so the feed said
`0B of 0B`. What those lines were: nobody connected - the receiver did not click Accept in time, or their
client's DCC filter dropped the file type - and every one cost the user a `MAX_SEND_FAILS` strike.

The accept timeout has its own branch now: `the receiver never connected within 30s - the offer was not
accepted, or their client ignored it.`, with `acked=0, total=file_size` so the feed reads `0B of 626MB`. The
send-loop timeout passes its counts too. The window is `DCC_ACCEPT_TIMEOUT` (default 30, floored at 1;
`dcc.accept_timeout()`), on the dashboard's Transfers page with help in three languages; a channel of people on
mIRC is better served by 60-90. `tests/test_a_receiver_that_never_connects_is_said_so.py`: the helpers, the
whole send path against a fake socket whose `accept()` times out (every runner), and a real loopback listener
nobody dials (where one can be bound). Six of eight fail on the old code - the loopback one after its full
30 s.

### 📊 The rebuild report counts every list, by the operator's names (#873)

Reported by an operator with a music list and a film/series list: `!update` (and the dashboard's Rebuild, which
goes through the same handler) ended with `MasterList now contains 64,136 files. Added 0 new file(s)` - the
music alone. `count_from_master_list()` read the count with no list name, which means the primary; its docstring
said "across every list", which was true before a bot could serve several separate lists (#26). So a second
list's total and its gain were never reported, and the #230 shrink guard watched the primary only - a film
folder that lost its mount was not noticed.

`commands.count_by_list()` returns `[(name, files)]` for every entry of `library.lists()`, by the name the
operator gave it in `lists.json`; a list that cannot be read counts 0 and the rest are still counted.
`describe_list_counts(old, new)` gives the sentence and the `[(name, was, now)]` that shrank. One list keeps the
wording it always had; several read `Main: 64,136 files (+0 new); video: 18,204 files (+12 new).` (signed, so a
shrink reads `-6`). The DROPPED warning is per list, names it (`of the list 'video'`) and points at *that list's*
folder/mount rather than "the music directory"; with several lists the summary still follows a warning so a
list that grew is not hidden behind one that shrank. `count_from_master_list()` is untouched.
`tests/test_the_rebuild_report_counts_every_list.py` (14): the counting, the sentence, and the report through
the real `!update` handler with the rebuild stubbed. Fix by Neo; wording of the warning and the changelogs here.

### 📦 A failed pack's partial archive is removed (#717)

Audit L53. `subprocess.run(timeout=RAR_TIMEOUT)` kills rar mid-write, and a non-zero exit leaves whatever it
wrote, at `target_rar_path` either way. The queue row points at the SOURCE folder, so neither
`discard_orphaned_temp_archives()` nor the send's own finally ever named that file; the row was retried and
after `MAX_SEND_FAILS` dropped, with a multi-GB partial left in `TMP_ZIP_DIR` until the same folder was packed
again or an operator found it (the auditor measured a 7 MB partial from a 0.4 s timeout with the real rar).
`dcc._discard_partial_archive()` removes it in both failure branches - the path is exclusively this pack's
output - and says what it removed and why; the timeout is re-raised afterwards, so the wrapper's handling of
the failure (the retry budget, the interlocks) is unchanged.
`tests/test_a_failed_packs_partial_archive_is_removed.py` runs the real packer path with rar stubbed to write a
partial and then exit 255 or time out: the file is gone, the log says so, `TMP_ZIP_DIR` is empty, and the row
is still charged and kept for a retry with the interlocks released. Three of four fail with the old code.

### 📦 The pack interlocks are released once (#714)

Audit L50. `_inline_rar_packer_body()`'s poisoned-row exit and no-room exit each cleared `rar_inprogress`,
woke the next waiting pack (`redispatch_waiting_pack`) and dropped the user lock, then returned None - on
which `inline_rar_packer()`'s finally did all three again. The second wake re-targeted the same waiting user
(harmlessly RAR-BLOCKed), but if that user's dispatch had claimed the interlocks in the microseconds between
the two releases, the wrapper's unconditional `rar_inprogress = False` cleared the claim while their rar ran -
leaving the packer interlock open for any later trigger (a JOIN thaw, a freeze-abort, a restored-queue wake)
to start a second rar on the same archive path, the corruption `wait_for_transfers_to_finish()`'s own comment
describes. The two exits now return to the wrapper without releasing; the finally is the one place.
`tests/test_the_pack_interlocks_are_released_once.py` drives both exits with the audit's own model - the
first wake claims the interlocks for the woken user - and checks one wake and a standing claim; both fail with
the old double release. `test_audit_rar_pack_and_slots`' two source-reading tests now read the property
(released once, by the wrapper) rather than the duplicated lines.

### 🧪 The VIP-lane drop on disconnect is executed, not read (#713)

Audit L49, test-only. The finding - the disconnect epilogue reset `send_queue["channel_announce"]`, a key
nothing writes, while the VIP lane kept stale adverts and "Sending:" notices across a reconnect - was closed
by #630 before this issue was filed: the dead reset is gone and the epilogue empties `config.vip_queue`, the
lane those lines actually use. #630 pinned it by reading the epilogue's text; since #663 the reconnect loop
runs for real against scripted connections, so `tests/test_a_dead_connections_vip_lines_are_dropped_for_real.py`
executes it: two VIP lines queued before a drop are gone after it and the epilogue says "Dropped 2 queued
VIP line(s)", an empty lane says nothing, and a user's standard lane - held across a reconnect on purpose -
is left alone with no `channel_announce` key touched. With the drop disabled, it fails.

### 🧪 The heartbeat does not wait on the disk lock either (#712)

Audit L48, test-only. The finding - the console's timer burst computed on the writer thread through
`status_lines()`, which takes `dcc.queue_lock` and `db._disk_lock`, so a long hold of either silenced the
console until the script called the link dead - is #614 (audit M12), fixed before this issue was filed: the
figures are computed on a helper with a deadline and `DCCORE PING` stands in past it.
`test_status_slot_queue_and_pairing` covers the queue lock; `tests/test_the_heartbeat_does_not_wait_on_the_disk_lock.py`
pins the disk lock the audit named beside it, with the audit's own probe (the writer running, the lock
held, the timer due, a reply queued): a PING is heard, the reply gets through, no figures are claimed. With
the helper's deadline removed (the old shape), it fails.

### 📝 The Python pin has a freshness step and one guarded copy (#711)

Audit L47. `start-dccore.bat` pins `PY_VERSION` and two SHA-256 hashes for the Python it installs for a
first-timer; the pin fails safe and is tested, but nothing in the release workflow said to look at it - a
3.x.y security release lands and the launcher keeps installing the old one until somebody remembers - and
WINDOWS.md's sample transcript repeated the literal number with nothing tying it to the launcher, so the
first bump would leave the guide naming a version the launcher no longer prints. PUBLIC-REPO-WORKFLOW.md's
release checklist now has the step (look at python.org's current release in the pinned minor; bump the three
lines; run the opt-in `DCCORE_VERIFY_PYTHON_PIN=1` check), and `tests/test_the_python_pin_has_one_home.py`
keeps the transcript's number equal to the launcher's - saying which to bump - and refuses the version in
any other prose.

### 🔒 One daemon per data folder, and the logon task asks nothing (#710)

Audit L46. Nothing checked for an already-running instance - not the daemon, not the launchers - so a
logon-task start plus a double-click (or a second logon session of the same account) ran two bots on one
data folder: the second took `ALT_NICKNAME`, both wrote `dcc_queue.txt` and `stats.txt` whole, and their DCC
listeners shared the eleven-port range; `install-autostart.bat`'s own closing text invited the second start.
And with the dashboard on and Flask missing, the task's launcher window stopped at `configure.py --flask`'s
`input()` until somebody answered it.

`platform_compat.take_instance_lock()` holds an OS lock on `data/dccore.lock` (`msvcrt.locking` on Windows,
`fcntl.flock` elsewhere; the lock, not the file, is the guard, so a crash leaves nothing stale) and writes the
pid for the message; `oserve.startup()` takes it before it reads or writes anything and refuses a second copy
with `[CRITICAL] DCCore is already running on this folder (pid N)` and `EXIT_ALREADY_RUNNING = 4`, which
`start-dccore.bat` names. The task runs `start-dccore.bat autostart`, which sets `DCCORE_AUTOSTART`, and
under it the Flask offer prints its command instead of asking. On Windows the locked byte sits past the pid,
because a locked byte cannot be read by anyone, the holder included. The test harness releases the lock
after every test, so a boot in one test is not the next test's second instance. WINDOWS.md and the installer
say so. `tests/test_one_daemon_per_data_folder.py`: a second process is refused and told the holder's pid,
the lock dies with its process, the same process may take it twice, a released lock can be taken; the daemon
exits with its code and message and a first start takes the lock beside the queue file; the task passes
`autostart`, the launcher reads it and names an already-running bot; and the Flask offer under the flag asks
nothing. With the code stashed, all nine fail.

### 🧩 The bot checks the script's version in hello (#709)

Audit L45. The channel field went into every event line without a number moving; #795 gave `HELLO` a minor
and taught `dccore.mrc` to check it. The other direction was still missing: `hello <client> <version>` carries
the script's own version and the bot logged it and nothing more, so an operator who pulled the bot but not
the script (a file copied into mIRC's folder by hand) got REQUEST lines reading "bob asked for file
Artist/Album" and SENDING slot numbers showing the channel, with nothing saying why.
`adminchat.MIN_SCRIPT_VERSION = "1.1"` names the oldest script that reads this bot's lines right, and
`_cmd_hello()` answers an older one - or one with no version it can read - with a plain `DCCORE OUT` line
right after `HELLO`: *"This dccore.mrc is version 1.0; this bot's lines are for 1.1 or later. Update the
script ... or the panel will read a field wrong."* Structured mode still switches on: the major is the same
and the script keeps parsing. Versions compare as tuples ("1.10" is ten, not one). ADMIN-CONSOLE.md's
example says `hello dccore.mrc 1.1` and both version notes mention the check.
`tests/test_the_bot_checks_the_scripts_version.py`: the comparison (older, equal, newer, "1.10", junk and
none), the pre-field script told right after HELLO with the feed still on, the current one not nagged, a
hello with no version told, the console log, and the shipped script not being too old for its own bot.

### 🔔 A lost connection is a notice, and what earns one is written down (#708)

Audit L44. `record_notice()` is reached only through `send_debug(notice=...)`, by the nine call sites that
know they are one - list-rebuild failures, kick and rejoin, join refusals. The help for `NOTICES_FILE` said
"kicks, failed rebuilds, disconnects", and the reconnect block only `print()`ed: a link that dropped every
night left the dashboard's badge clear, and an operator reading the help assumed no disconnect had happened.
The split relied on every future author remembering `notice=`, with no list of the intended events anywhere.

`announce.NOTICE_EVENTS` is that list now - the event, the module, a piece of the line it emits, the severity
- and the reconnect block raises a warning ("Lost the connection to the IRC server; reconnecting in N
seconds.") through `send_debug` like the others, so the sinks and the badge see it at once and the channel
line waits for the link. The help text (en, es, fr) names what is recorded: a lost connection, a kick or a
refused join, a failed list rebuild. `tests/test_what_earns_a_notice_is_written_down.py` finds each named
event's emitter by its line and checks it passes `notice=` with that severity; sweeps the tree for a
`notice=` the list does not name (comments excepted); pins the severities; reads the help; and records the
disconnect line through the real `send_debug` to see the warning land. Removing the disconnect notice, or
adding a `notice=` without a list entry, each fail one test.

### 🧪 Importing oserve touches nothing (#707)

Audit L43. `list.py` imports `oserve`, `announce` imports `list`, and `tests/support.py` imports `announce` -
so `oserve.py`'s two module-level installs (the console encoding guard and the `_TimestampedStream` proxy)
ran in every test process and wrapped the runner's `sys.stdout` and `sys.stderr` for the rest of the run:
unittest's summaries came out timestamped, and a test asserting an exact printed line saw a prefix that
depended on which module was imported first. `install_fake_oserve()`'s docstring said the real import was
avoided and would start worker threads; neither was true. The installs (and the operator's timestamp
format) sit under `if __name__ == "__main__":` at the top of `oserve.py` now - true exactly when
`python oserve.py` is the program, so the daemon's first lines are still stamped and guarded and the
"before config loads" order is kept - and an import touches nothing; the docstring says what happens.
`tests/test_importing_oserve_touches_nothing.py` checks in child processes, where the streams start clean:
`import announce; print('x')` prints a bare `x`, importing `oserve` wraps nothing and starts no thread, and
the file run as `__main__` (its entry point replaced by a print) stamps that line. `test_startup`'s
entry-point test splits on the last `__main__` guard now. The suite's own output loses its stamps.

### 🧪 A wait, not a sleep; and preflight's note names the pass that failed (#706)

Audit L42, test-only. `test_the_window_closes_itself_after_the_duration` slept 0.4 s for a 0.15 s window and
asserted the raw flag the closer thread sets - a bet on the scheduler that a loaded runner loses; it waits
for the condition with a deadline now (`wait_for`, from test_adminchat). `scripts/preflight.py`'s "only the
hidden-tooling pass failed" note fired whenever the LAST result was False and all earlier ones True - and with
the hostile pass SKIPPED (rar reachable regardless) the last result is the test-count floor, so an operator
whose count had dropped was told to fix a hidden-tooling dependency that does not exist. The condition is
`only_the_hidden_pass_failed(results, hostile_ran)` now: it knows whether the hidden pass ran and which of
its two results is the run, and says nothing for the state check or an earlier failure.
`tests/test_preflights_note_names_the_pass_that_failed.py` runs the audit's case and the three others, reads
the tail for the call and the flag, and the window test for the wait.

### 🧪 Three tests that could not fail (#705)

Audit L41, test-only. `test_the_old_shape_would_have_grown_without_limit` added 20,000 items to a plain
`set()` and asserted 20,000 - a test of Python's set - while the regression its class documents (a
`security._ban_notified` reverted to a plain set) left all seven of its neighbours green, because they build
their own `_NotifiedNicks`. `test_what_the_old_arithmetic_did_to_a_real_file` divided literals.
`test_every_emitter_goes_through_it` substring-matched the source for `feed_event("KIND"`, which a
commented-out or `if False:`-wrapped call satisfied, and its `category="KIND"` guard named a string that
exists nowhere. The first is replaced by a guard on the module global's type; the second is deleted; the
third now drives each emitter the way the daemon calls it - `send_dcc_sending_notice`,
`send_dcc_queue_notice`, `send_transfer_complete`, `_report_transfer_failure`, a registered offer resumed -
and reads the kinds off the event sink (REQUEST and SEARCH, which need a library, are driven in
`test_the_feed_says_which_channel`). The audit's two mutants - the SENDING call wrapped in `if False:`, the
registry reverted to `set()` - each fail one of the new tests.

### 🧪 The harness redirects the console's token store (#704)

Audit L40, test-only. `DCCoreTestCase` redirected sixteen state files but not `db.ADMIN_TOKENS_FILE`, and
every `_check_password()` path goes through `db.load_admin_tokens()` on it: `test_adminchat`'s login tests
read the operator's `data/adminchat_tokens.json` from the cwd - on a machine whose bot has paired
`dccore.mrc`, each wrong-password test verified PBKDF2 against every real token - and a `pair` reached from
any test but the two that redirected the path themselves would have written the live store (preflight's
`data/` walk would have caught the write, nothing the read). `FETCHED_FILES_DIR`, the audit's other name, was
redirected by #643. The harness now points `db.ADMIN_TOKENS_FILE` and `config.ADMIN_TOKENS_FILE` at a file in
its temp directory beside `known_bots.json`, and restores the constant on teardown.
`tests/test_a_login_test_never_reads_the_operators_token_store.py` runs the audit's probe (`os.path.exists`
recorded through a wrong-password check: only the temp store is probed), writes a pairing and checks the real
store is untouched, and runs a harness test from a plain `TestCase` to see the constant put back. Three of
four fail with the old harness.

### 🧪 The diagnostic gates in the read loop are executed, not read (#703)

Audit L39, test-only. `irc_loop()`'s inline dispatch - the admin gates on `!ping` and `!debugnames` and
the CTCP VERSION reply - was guarded by source-substring tests alone. They catch a gate moved after its
action, but the verifier's mutant - the gate kept in place and neutralised with `and False` - passed all
nine. `!ping` has an executed backstop inside `handle_ping_request()`; `!debugnames` does not (its RAM-CHECK
notice is built and queued inline), and neither does VERSION's reply. Since #789 the loop is driven for real
against a scripted server, so `tests/test_the_diagnostic_gates_are_executed_not_read.py` runs the lines
through it after 001, with the loop's threads recorded and the fake `oserve`'s queue watched: a stranger's
`!debugnames` queues nothing and the admin's gets the RAM-CHECK; a stranger's `!ping` starts no thread and
the admin's starts `handle_ping_request` for them; a CTCP VERSION is answered through the paced VIP queue and
not at all with the reply off. The audit's mutant fails two of the six. (The audit's other suggestion - lifting
the dispatch out of the loop into a function - is not needed for the coverage and is left as it is.)

### 📝 Stale phrasing and module names are gone from operator-facing text (#701)

Audit L37. Remnants of earlier phases and of the `config.py` rename sat where a novice reads them:
ADMIN-CONSOLE.md's "Until phase 2 flips the switch" (the switch is `ADMIN_CHANNEL_COMMANDS`; no phase
numbering exists in the doc), a dangling "Phase 4" bullet duplicating the prose above it, an example banner
from v1.10.0-RC1; "produced from config.py" in `settings.conf.sample` and its generator; "Every data path in
config.py" in both launchers' headers; the Windows launcher's first-run hint saying `python oserve.py` where
every guide says `py`; `requirements.txt` pointing at `docs/README.md`, which is at the root; and FUTURE.md
filing the finished multi-list work (all five stages "in", #26 complete) under "Planned", right under the
sentence that says everything there is not working. Each is fixed: the lockout names the switch, the bullet
is gone, the banner is this tree's version, the generator (and so the regenerated sample) and the launchers
say `defaults.py`, the hint says `py`, `requirements.txt` says `README.md`, and the multi-list section moved
under "Implemented" with its design narrative intact. `tests/test_no_stale_phrasing_in_operator_text.py` reads
for the class: no phase numbers in the console guide, the banner matches `SCRIPT_VERSION`, no bare
`config.py` in the operator files (the rename heading excepted), the launcher's `py`, the README the
requirements point at exists, and nothing under "Planned" reports a stage "in".

### 📝 The guide lists what configure.py asks (#700)

Audit L36. INSTALL.md said `configure.py` asks "six questions" and omitted the dashboard yes/no; WINDOWS.md
listed a different set; neither mentioned the two offers `main()` makes afterwards - generate the list now,
import OmenServe totals - so a novice expecting six was surprised by "Import them now?" with nothing
explaining it. INSTALL.md now carries the list in the order asked (the seven questions, the dashboard's LAN
and Flask follow-ups, the music folder's create-it offer, then the two offers) under an anchor, and WINDOWS.md
summarises and points at it. `tests/test_the_guide_lists_what_configure_asks.py` reads the prompts out of
`configure.py` in source order and requires the guide's numbered items to follow them one each, the
follow-ups and offers to be named, "six questions" to be gone, and the pointer to exist - so a prompt added
to `configure.py` without a line in the guide fails the suite.

### 📝 The setup check names the module that exists (#699)

Audit L35. Half of it is #685 (the check on an empty tree said "copy the samples"). The other half: the
check's `import defaults` failure was reported as *"config.py did not load"* - a file that has not existed
since the rename the guides describe - and sent an operator looking for it. It says *"defaults.py did not
load (it reads admin_config.py and settings.conf)"* now, and the two comments that still said `config.py`
say `defaults.py`. `tests/test_the_check_names_the_module_that_exists.py` reads the message, refuses any
other bare `config.py` in the check, and pins that the tree has `defaults.py` and no `config.py`.

### 📝 The test count is kept in one place (#698)

Audit L34. README.md said 4994 tests and FUTURE.md said 5237 on the same commit; the loader found 5287. Two
sentences in two files said the same number and drifted independently, and a reader comparing them saw a
project that could not count its own tests. The count lives in FUTURE.md's Quality section alone now (6266
on this tree, measured by the loader); README's Tests section says "thousands of them" and points there;
PUBLIC-REPO-WORKFLOW.md's release checklist names the one place. `tests/test_the_test_count_is_kept_in_one_place.py`
reads the shipped prose for any other four-digit "N tests" claim, checks README points at the roadmap, refuses
a FUTURE.md figure more than a tenth off what the loader discovers - so a roll that forgets the line fails the
suite while an ordinary PR need not touch it - and reads the checklist.

### 📝 The flood ban is described as timed, not as a day-ban (#697)

Audit L33. `FLOOD_BAN_SECONDS` ships as 3600, and its own comment explains that the old midnight expiry was
replaced precisely because it could be nearly a day - but FUTURE.md still said the flood escalation was "a
day-ban", and two comments in `security.py` did too; an operator expected a flooder gone for the day and saw
them back in an hour. The roadmap now says "a timed ban (`FLOOD_BAN_SECONDS`, one hour by default)" and the
comments name the setting. `tests/test_the_flood_ban_is_described_as_timed.py` pins the default, reads the
shipped prose and code for the word, and the roadmap for the sentence.

### 📝 Packing is said to be bounded everywhere (#696)

Audit L32. INSTALL.md's upgrade note justified the `RAR_EXTENSIONS` change with "packing has no size cap and a
film folder is a request to compress tens of gigabytes", and the comment above `RAR_EXTENSIONS` in
`defaults.py` (and so `settings.conf.sample`, generated from it) said "There is no size cap anywhere on
packing". Both predate `MAX_RAR_FOLDER_SIZE` (10 GB, enforced at request time) and contradicted INSTALL.md's
own settings paragraph, FUTURE.md, the help text and the Settings page. An upgrading operator read that
packing was unbounded and either added a workaround or distrusted the earlier paragraph. Both now give the
real reason - packing a film folder is pointless work for the receiver, and the cap alone still lets a 9 GB
film through - and the comment dates its history ("at the time there was no size cap ... has bounded it
since"); the sample is regenerated. `tests/test_packing_is_said_to_be_bounded_everywhere.py` reads the
shipped prose and the two samples for the claim, the guide for the reason beside the cap, and `defaults.py`
for the dated history.

### 📝 WINDOWS.md's "Did it actually start?" says what it means (#695)

Audit L31. The section was written under a since-removed "The seven steps" list. Read top to bottom, a
first-timer met "Step 7 prints a lot" right after "The two steps" (steps 1 and 2) and "That is step 4" with
no referent - the list meant is "Before you start", fifty lines further down - and either hunted through the
guide or assumed they had skipped something; the Setup section's "skipped step 4" had the same problem with
its three-step list. The three now say what they mean: the launcher's window; the optional Flask install,
with the command and where the item lives; "Flask was never installed".
`tests/test_the_windows_guide_names_what_a_step_means.py` guards the property rather than the wording: every
"step N" in the guide is preceded by a numbered item N above it - the old text reads as dangling at lines 70
and 104 - and reads the three places.

### 📡 The debug-channel line fits the wire (#694)

Audit L30. `send_debug()` wrapped its text in about 170 bytes of colour framing with no line-length budget -
the one outbound builder without one. A long folder name (`Pack denied for X: <folder> is an artist root
folder`), a hostmask or an exception's text with an absolute path pushed the PRIVMSG past the 512 bytes a
server relays, and the server cut it inside the text - possibly inside a colour code or a multibyte character -
so the debug channel showed a truncated line with the background colour smeared to the end and the closing
block gone. The console sinks and stdout got the full text. The text and the closing block are rendered
through `fit_irc_line()` now, as the adverts and the notices are: shrunk with an ellipsis until the whole line
fits `IRC_LINE_BUDGET`, re-rendered from the template each time so a colour code is never sliced.
`tests/test_the_debug_line_fits_the_wire.py`: the auditor's 500-byte text fits and ends properly, the closing
block is on the line with an ellipsis in the text, a multibyte name is measured in bytes and never split, a
short line is untouched, the console sink still gets the whole text, and `send_debug()` is read to render
through the shared builder. Four of six fail with the old code.

### 📡 A part reason cannot name the channel (#693)

Audit L29. The PART handler's `^:([^!]+)!.* PART (\S+)` with `re.search` was greedy: the channel group was
whatever followed the LAST ` PART <token>` in the line, which may sit inside the user-typed reason. bob
parting `#music` with reason "I PART #rock now" while also in `#rock` was kept in `#music` (his queue never
frozen while absent; a later send to a channel he had left) and wrongly removed from `#rock` (frozen while he
sat there). The JOIN handler shared the greedy `.*`, and an extended-join line carries an account and a real
name after the channel. `irc.parse_part()` and `irc.parse_join()` take the first token after the command,
anchored on the prefix with `\S*\s+` after the bang as `parse_kick()` is, and the two handlers go through
them. `tests/test_a_part_reason_cannot_name_the_channel.py`: the audit's line names the channel actually
left, the ordinary shapes, a PART or JOIN typed into a channel is neither, the verifier's `#music,#rock`
control is left to `is_valid_irc_target()` as before, the extended-join line, and the handlers read to go
through the named parsers. The membership-guard tests' anchors follow the handlers' new lines.

### 💾 Leftover temp files in data/ are swept, and state files are readable again (#692)

Audit L28. `db._atomic_write()` creates `data/.tmp_XXXX.swap` with `mkstemp()` and swaps it into place. A
hard kill between the two left it behind - and so did a Ctrl-C, because `KeyboardInterrupt` is not an
`Exception` and the cleanup branch did not run - and nothing at startup or on a later write removed it: every
crash added a hidden file to `data/`. `mkstemp()` also creates its file 0600, and the replace carried that
through, so on POSIX `hard_bans.txt` and `dcc_queue.txt` - documented as hand-editable - were owner-only after
their first save.

`db.discard_stale_swaps()` removes `.tmp_*.swap` files at startup (housekeeping beside the fetch-history
prune, never a reason to refuse to boot), the way `update_list` sweeps its own staging files; the cleanup in
`_atomic_write()` catches `BaseException`; and a new file gets 0644 while an existing file keeps the mode it
has - the console token store asks for 0600, since it holds secrets. `tests/test_leftover_temp_files_are_swept.py`:
the sweep takes the leftovers and nothing else (a `.conf` temp and the real files stay), is silent with
nothing to do, tolerates a missing directory and defaults to the queue's directory; a `KeyboardInterrupt`
between the two steps leaves nothing; the audit's hard-kill probe in a child process leaves one and the sweep
takes it; on POSIX a new file is 0644, an existing 0664 stays 0664, and the token store is 0600; and
`startup()` calls the sweep. Seven of ten fail with the old code.

### 💾 A failed bot-registry save is tried again, and said (#691)

Audit L27. `db.save_known_bots()` caught every exception and returned None; `irc._flush_known_bots()` then
stamped `runtime.known_bots_flushed_at` regardless and returned True. A failed write - disk full, a
permission, a replace that outlasted its retries - was not tried again for `KNOWN_BOTS_FLUSH_SECONDS` (30 s);
the dashboard's add-source and remove-source routes answered a plain 200 for a row that was not on disk; and
shutdown did no final flush, so a Ctrl-C inside the window lost the last adverts and a source just added.

`save_known_bots()` answers True or False and serialises a snapshot (the IRC thread inserts a bot in place
while a dashboard request flushes, and `json.dumps()` over a dict changing size is a RuntimeError);
`_flush_known_bots()` stamps the flush time only on True, so the next advert tries again; the two dashboard
routes carry a `warning` when the write did not land, and the page shows it as the error it is
(`filelists.sourceNotOnDisk`, en/es/fr); and the Ctrl-C path flushes once more on the way out, never fatally.
`tests/test_a_failed_registry_save_is_tried_again.py`: True/False from the saver, the audit's probe (the disk
refuses, the stamp stays and the next unforced call tries again), a landed flush recorded and on disk, a
registry that grows under the writer, both routes warning and neither when it landed, the page's two handlers
and three strings, and the shutdown flush. Six of nine fail with the old code.

### 💾 The stats import holds the disk lock once (#690)

Audit L26. `apply_stats_import()` loaded the 7-column row with `db.load_advanced_stats()`, set the two lifetime
columns and wrote it with `db.save_advanced_stats()` - two `_disk_lock` acquisitions. A transfer completing
in the gap (`db.update_stats_on_complete()`, on the send's own thread) had its +1 file and +bytes on Today and
Total discarded by the import's stale write - the lost update `db.py`'s own header describes for the old
`dcc.py` code - and a day rotation in the gap was undone; the import still answered 200.

`db.set_lifetime_totals(total_files, total_bytes)` reads, modifies and writes under one acquisition, leaving
the day columns and the date alone, and returns the row it wrote (None if the write raised, as
`save_advanced_stats()` swallows). The import calls it and judges the totals by that row rather than by a
later read: a transfer completing right after the import legitimately moves them on, and that is not a
failed write. `tests/test_a_transfer_during_a_stats_import_is_not_lost.py` forces the race rather than
betting on it - the completion thread is started from inside the import's own locked read and shown to be
waiting on that lock - and the row that lands carries the import's totals plus the transfer, with Today kept;
the day columns are untouched; the old two-call shape is modelled and shown to lose the transfer; the helper
sets only what it is given and starts a missing file from the default row; a totals write that raised is
reported as failed; and the import is read to go through the helper. Four of seven fail with the old code.

### 🖥️ A first run ends on one dashboard tab (#689)

Audit L25. `run_setup_until_configured()` had already put the browser on the setup page, whose "Saved" screen
polls `/login` and navigates there the moment the real app answers. `webserver.start()` then called
`_open_in_browser()` with no knowledge the setup page had just run, and opened `http://127.0.0.1:8420/` as
well: a first run with the dashboard on loopback (the form's default) ended on two dashboard tabs, one on
`/login` and one on `/` (which redirects to `/login`). `run_setup_until_configured()` now sets
`_browser_is_on_the_saved_page` when the tab it opened is going to arrive at the login by itself - a browser
was opened AND the dashboard was chosen - and `_open_in_browser()` stands down once, logging *"The setup
page's tab opens the login by itself; not opening another."*; the next start opens as it always did.
`tests/test_a_first_run_ends_on_one_dashboard_tab.py`: the flag on its own (stands down, once, and not
without it), and the audit's reproduction against the real setup server - a browser "opened" by the recorder,
the form saved with the dashboard on, then start()'s call opens nothing; with no dashboard chosen the flag
is not left set. Four of five fail with the old code.

### 📝 A legacy SCRIPT_VERSION line in settings.conf is explained, not called a misspelling (#688)

Audit L24. An older dashboard's Settings page offered `SCRIPT_VERSION` and wrote it into `settings.conf`;
nothing ever removed the line. `NOT_SETTINGS` rightly keeps the name out of the overridable set, so the line
is ignored - but the explanation table `RUNTIME_ASSIGNED` (#465) covered only `MY_IP_OR_DOCK` and
`ORIGINAL_NICK`, so every boot and every `!rehash` said *"not a setting this version recognises. Check the
spelling against settings.conf.sample"* of a name DCCore itself had written, spelled perfectly. The table now
carries it: *"the code's own version, which an older Settings page wrote here; it is no longer configurable.
Delete this line."* `tests/test_a_legacy_script_version_line_is_explained.py` applies such a file for real:
the line is still ignored, the operator is told what it is and what to do, a real misspelling still gets the
spelling hint, and every member of `NOT_SETTINGS` has an explanation. Two of four fail with the old table.

### 🔌 SERVER is a host name, and says so when it is not (#687)

Audit L23. The setup form refused only a space in SERVER, and PORT is not on the form, so the natural
first-timer spelling `irc.undernet.org:6667` - or a pasted `irc://irc.undernet.org` - was accepted and
written, and `connect()` failed on name resolution every ten seconds for ever: `[ERROR] Connection failed:
[Errno 11001] getaddrinfo failed. Reconnecting in 10 seconds...`, with nothing saying the colon or the scheme
was the problem. `configure.py`'s prompt and a hand-edited `settings.conf` took the same values.

`settings_file.server_problem()` names what is wrong and what to write instead - a URL ("SERVER is the host
name alone, e.g. irc.undernet.org"), `host:port` ("put 'irc.undernet.org' in SERVER and 6667 in PORT"), a `/`,
a `:` without a port, a space, a blank - and all three doors refuse through it: the setup form
(`setup.error.server_shape` in en/es/fr, replacing `server_spaces`, whose case it covers), `configure.py`'s
`_ask(..., check=server_problem)`, and `_check_writable()` for `settings.conf` and the dashboard's Settings
page. `tests/test_server_is_a_host_name.py`: the audit's three spellings and the other shapes, real hosts
passing, the form in three languages, the reader raising with the fix named, and the prompt wired. Seven of
nine fail with the old code.

### 🖥️ A size setting's help says the unit the page shows (#686)

Audit L22. `settings_help.PLAIN_HELP` is one text for two readers: `settings.conf.sample`, where the value IS
bytes, and the dashboard's "?", where `SETTINGS_UNITS` types and shows `MAX_RAR_FOLDER_SIZE`,
`MAX_FETCH_FILE_SIZE`, `MAX_LIST_TEXT_SIZE`, `MAX_FETCH_FOLDER_FILE_SIZE` and `MAX_FETCH_LIST_FILE_SIZE` in MB
and `DCC_SEND_BUFFER` and `LIST_HEADER_MAX_BYTES` in KB. The tooltip said "in bytes" beside an MB chip; an
operator who read it and typed 10737418240 into the MB box set a 10 PB limit. `settingsHelpHtml()` in
`web/app.js` now appends, for a field with a unit, *"On this page the value is typed and shown in {unit}; the
file keeps bytes."* after the shared text (`settings.help.shownIn`, in en/es/fr, through `t()` so it follows
the language picker). The shared text is untouched, so the sample stays right.
`tests/test_a_size_help_says_the_unit_the_page_shows.py` lifts `t()`, `fieldHelp()` and `settingsHelpHtml()`
out of app.js and renders them in node with the page's real dictionaries and `_settings_field()`'s real
fields: every unit field's tooltip ends with the sentence and its unit, in Spanish and French too, a field
without a unit gets nothing added, the sample's text still says bytes, and every language carries the
placeholder. Two of five fail with the old app.js.

### 📝 Nothing configured says "run the launcher", not "copy the sample" (#685)

Audit L21. `start-dccore check` is the documented pre-flight, and on a tree with neither `admin_config.py`
nor `settings.conf` it FAILed with "copy admin_config.py.sample to admin_config.py, or settings.conf.sample to
settings.conf, and fill one of them in" - exactly the manual step the launchers (#547) replaced, while the
next three FAILs on the same screen already said "Run configure.py". A novice who followed the stale line
created `admin_config.py` by hand, which is the launcher's first-run gate: the questions and the browser
setup page were never offered, and the copied sample turned the dashboard and the debug channel on for
them. `oserve.py`'s own refusal ("see admin_config.py.sample / settings.conf.sample") said the same.

Both say the same thing now: `nothing is configured yet. Run <the platform's launcher> (it asks the
questions, or opens the setup page in your browser), or <python> configure.py` - the check through
`Platform.start_cmd`, so Windows names the `.bat` and Linux the `.sh`. Still a FAIL; only the advice changed.
`tests/test_nothing_configured_says_run_the_launcher.py` runs `check-setup.py` for real against an empty
configuration and the daemon's own refusal through `startup()`, and checks neither names a sample any more.
Four of five fail with the old text.

### 🪟 The elevated firewall copy only runs netsh (#684)

Audit L20. After `Start-Process -Verb RunAs`, `allow-firewall.bat` ran under whichever account answered UAC
and searched for Python again with THAT account's `%LOCALAPPDATA%` and `py -3`. A standard-user operator whose
parent typed the admin password got "Python was not found - run start-dccore.bat first" on a machine where the
launcher works (the launcher's Python is per-user, `InstallAllUsers=0`), and no rule was added. Separately,
a folder with an apostrophe in its name (`C:\Users\O'Brien\...`) ended the PowerShell string in the relaunch
line early, so the script never elevated at all.

The unelevated half now finds the interpreter, reads the ports and learns `sys.executable` as the operator,
and hands them to the elevated copy as arguments - `elevated <dcc start> <dcc end> <web port> <web on>
"<python.exe>"` - through `$env:DCCORE_SELF` / `$env:DCCORE_ARGS`, never inside a quoted PowerShell string.
The copy takes its arguments at the top, skips the search and the ports, and runs the Block-rule step (its
interpreter path through `$env:DCCORE_PYEXE`, for the same apostrophe reason) and netsh. `remove-firewall.bat`'s
relaunch goes through `$env:` too. WINDOWS.md says so. `tests/test_the_elevated_firewall_copy_only_runs_netsh.py`
runs the .bat for real on Windows: the relaunch carries the operator's ports and interpreter; the elevated
half with NO Python on PATH adds both rules from its arguments and never relaunches; a folder named `O'Brien`
reaches PowerShell whole; and three source-reading checks for the other platforms. The #589 tests are
updated to the new shape (the path is read into `%PYEXE%` at once; nothing of cmd's is expanded inside the
PowerShell text now).

### 📝 The token file is said to be clear text (#683)

Audit L19, wording only. `hsave` writes mIRC's hash table as plain item/value text, so the console token
sits readable in `dccore.ini` beside the script. The script header and ADMIN-CONSOLE.md said where it was
kept but never that it was clear text, and the guide's pairing section named "a stolen `.mrc`" as the cost
when the `.mrc` carries nothing - the file that matters is `dccore.ini`; an operator who zipped their mIRC
folder to share the script shipped the token with it, pointed at the wrong file. The header now says
`dccore.ini is CLEAR TEXT`, treat it as a password file, and `/dccore unpair` the moment it may have travelled;
the "Paired as" message says so at the moment the token is stored; the guide names the right file and its
nature in "What a token does not do" and in the pairing walkthrough. No code change; `.gitignore` already
keeps the file out (#575). `tests/test_the_token_file_is_said_to_be_clear_text.py` reads all three, pins the
store as a plain `hsave` (so wording and code change together), and the `.gitignore` entry. Not verified in
mIRC: comment and message text only.

### 🧩 A FAIL line always parses in the script (#682)

Audit L18. `DCCORE FAIL <nick> <chan> <acked> <total> <name> :: <reason>`: only a ` :: ` INSIDE the name was
defused (to ` : : `). An empty name gave `... 0 0  :: reason` - two spaces - and `dccore.mrc` collapses runs of
spaces, so `$6-` was `:: reason`, `$pos` found no ` :: `, and the window showed the file as `:: reason` and
the reason as `failed`. A name ending in ` ::` gave `name :: :: reason`: the name parsed, the reason showed as
`:: reason`. An empty name needs a queue row without a file, a trailing ` ::` a non-Windows filesystem - real
but rare. `adminchat._name()` now defuses every standalone `::` in a name wherever it sits (`foo ::` ->
`foo : :`, `::` -> `: :`), leaves `a::b` and `Song: Part II` alone, and renders an empty name as `?` on every
kind. `tests/test_a_fail_line_always_parses_in_the_script.py` asserts through a model of the script's own
split - `DCCORE` stripped, spaces collapsed, `$6-`, the first ` :: ` - so the property is what the window
shows: name and reason come back right for every shape the audit listed, the model reads the OLD lines the
way the audit traced them, and every kind's empty name is `?`. Four of eight fail with the old code.

### 🧪 A mis-typed row does not kill the console's writer (#681)

Audit L17, test-only. The finding: `_writer_loop()` called `send_status()` -> `status_lines()` with no guard
but the one around the stats block, so a `dcc_queue` value with no `len()` or a transfer row with a string
`started_at` raised out of the writer thread and left the session a black hole - commands accepted, nothing
ever written, `dccore.mrc` reconnecting into the same wall every 90 s. Reachable only through a hand-edited or
future mis-typed row. Already closed in the tree by #614, which moved the figures onto a helper thread whose
body catches and prints `Status burst failed: ...` - the writer goes on draining. Pinned now with the
verifier's own two rows in `tests/test_a_bad_row_does_not_kill_the_console_writer.py`: the writer still
delivers the next line, the failure is reported, the session stays open, and the burst is back once the row
is gone. Without the helper's guard, two of the three fail.

### 🔐 The console listener takes only the operator's connection (#680)

Audit L16. The host check gates who can make the bot OPEN a listener; `accept()` then took whoever reached the
port first inside `LISTEN_TIMEOUT`, and the only check after it was `is_bad_ip()`. The DCC port range is
public and scanned: a scanner that connected in the window got the banner (nick, version, platform, the rar
binary's path) and three password prompts, the single listener was gone with it, the operator's own connect
found the port closed, and the scanner's failed attempts were logged under the operator's nick and host.

`handle_dcc_chat()`'s listen-mode branch now hands the address the CTCP advertised to
`_listen_and_serve_locked(..., expected_ip)`, and the listener loops on `accept()` until the window's
deadline: a peer from any other address is closed without a word - no banner, no prompt - and logged as
`Dropped a connection from <ip> on port <n>: the DCC CHAT was offered to <nick> at <ip>. Still waiting.`; the
operator's own connection is served when it arrives. A passive offer advertises no address, so there the first
peer is taken as before. The module docstring and ADMIN-CONSOLE.md say so.
`tests/test_the_listener_takes_only_the_operator.py` runs the real listener over loopback with `_serve()`
recorded and the window cut to 1.5 s: the operator at the advertised address is served; a stranger gets EOF
and no banner, the listener is still there for a second peer and closes on the timeout with nobody served;
a passive offer takes the first peer; and the listen-mode branch is read for the argument. All four fail with
the old listener. `test_one_passive_listener`'s stubs take the new argument.

### 🧩 Every line of a structured session starts with DCCORE (#679)

Audit L15. `Session.close(announce_text=...)` writes its text inline - the writer thread is about to stop, so
a queued goodbye would never leave - and bypassed `send()`'s `DCCORE OUT` wrapping. After `hello`, `quit`
("Goodbye.") and the 4096-byte guard ("Line too long.") sent bare lines, against ADMIN-CONSOLE.md's "from then
on, every line it sends on this session starts with `DCCORE`". `dccore.mrc` merely echoed them; a stricter
client would have treated them as a protocol error or routed them as plain chat. `close()` wraps as `send()`
wraps now; a line that already is a DCCORE line (`DCCORE TAKEN <ip>`) goes as it is, and a plain session is
untouched. `tests/test_every_structured_line_starts_with_dccore.py` runs both of the audit's probes over a
socket pair, the TAKEN and plain-mode controls, `None`, and reads the guide for the promise; two of six fail
with the old `close()`.

### 🔐 DEBUG_TO_CONSOLE off silences the structured feed too (#678)

Audit L14. `send_debug()` honoured `DEBUG_TO_CONSOLE` before fanning prose out to the debug sinks;
`feed_event()` handed the fields to the event sinks gated only by the per-kind tickboxes. A structured
`dccore.mrc` session drops the prose of feed kinds and lives on the fields, so after the operator unticked
"Send debug lines to admin console" the plain console went quiet as documented while the mIRC window kept
showing every REQUEST/QUEUED/SENDING/SENT/FAIL/SEARCH line - only `LOG` stopped - and the stdout floor printed
the same event as undelivered at the same moment. `feed_event()` now returns before the event fan-out when
the switch is off, exactly as it does for an unticked kind; the counts (#754) are still kept, and the prose
still goes to the channel and the floor. ADMIN-CONSOLE.md's routing table says so.
`tests/test_the_console_switch_gates_the_structured_feed.py`: no sink gets the event with the switch off, the
sink gets it with it on, the tickbox still gates on its own, the count is kept; and the audit's own probe - a
real `Session` in structured mode with both sinks attached - gets both lines with the console on and neither
with it off, with the floor still saying so. Two of six fail with the old `feed_event()`.

### 🔐 An address that failed to log in once or twice is forgotten (#677)

Audit L13. `webserver._web_bad_ips` (the dashboard's failed-login pool) deleted an entry only on a successful
login from that address or when a block expired - and a block was only set at `MAX_PASSWORD_ATTEMPTS`. An
address with one or two failures had `blocked_until == 0.0`, never met the expiry, and stayed for the life of
the process: on an internet-exposed `WEBUI_HOST` bind, one entry per scanner that ever sent a `POST /login`,
for ever (the auditor measured 100,000 entries that no amount of time removed). `adminchat._bad_ips`, the DCC
console's pool with the same policy, had the same shape.

Both entries carry when the address last failed, and `adminchat.forget_stale_failures()` runs under the lock
on every new failure: an address that never reached a block and has not failed inside `BAD_IP_BLOCK_SECONDS`
is dropped. A failure that old does not count towards a block either - two typos a day apart are not an
attack - and blocked addresses are left to the expiry that already forgets them. An older two-field entry is
treated as fresh by the first sweep. `tests/test_an_address_that_failed_once_is_forgotten.py` runs the same
cases against both pools with a stubbed clock: the audit's scan of 2,000 addresses is gone after the window,
forgotten at the window and kept just inside it, old failures do not add up to a block, three inside the
window still block and expire as before, a blocked address is not swept, and the sweep on its own.

### 📝 The setup page says when settings.conf will shadow the password (#676)

Audit L12. Half of it was closed by #624 (`admin_config.py` is written before `settings.conf`, so a failed
second write can no longer leave a configured bot with no password and no way back to the page). The other
half: `defaults.py` applies `admin_config.py` first and `settings.conf` second, so a pre-existing
`ADMIN_PASSWORD_HASH` in `settings.conf` (the dashboard's own change-password control writes there) overrides
the hash the setup form writes - the password the operator just chose works until the next restart and then
stops. `configure.write_admin_config_password()` prints that warning to the daemon's window, where
`configure.py`'s operator is; the person at the form is in a browser and never saw it.

`apply_setup()` now returns what the writer found (`shadowed_by`), the route keeps it, and
`render_setup_saved_page()` shows it in amber under the saved text, on the first render and on a reload of
the saved page: *"settings.conf also sets ADMIN_PASSWORD_HASH, and it is applied after admin_config.py - so
after the next restart the password you just chose will stop working. Remove the ADMIN_PASSWORD_HASH line
from settings.conf, or change the password from the dashboard, which writes to that file."* -
`setup.saved.shadowed` in en/es/fr. `tests/test_the_setup_page_says_when_the_password_is_shadowed.py`:
`apply_setup()` names the file or None, the saved page warns (and on reload), says nothing otherwise, in
Spanish and French with the placeholder filled, and every language file carries the key. Six of seven fail
with the old code.

### 🔐 The setup-page code is good for one browser (#675)

Audit L11. `run_setup_until_configured()` prints `http://127.0.0.1:8420/setup?token=...` and hands it to
`webbrowser.open()`. On Linux that is `xdg-open` with the URL in argv, and the browser it starts keeps the URL
in its own argv for as long as it runs - so on a host shared with other users, `ps aux | grep token=` during
the setup window gave a second user the code, and the page binds 127.0.0.1, which every local user reaches:
they could submit the form first with a password of their own. Single-user desktops (the target install),
macOS (the URL goes to osascript over a pipe) and Windows are unaffected.

The code is bound to the first browser that presents it: `create_setup_app()`'s gate gives that request an
HttpOnly `dccore-setup` cookie (`after_request`), and from then on the code is accepted only together with it.
A second browser with the code is refused with *"This link has already been opened in another browser, and
the code in it is good for one. If that was not you, stop DCCore and start it again: it prints a new link with
a new code."* - and if the other user was somehow first, that is what the operator sees, which is the alarm.
The cookie alone is not the code; once the form is saved the saved page and its `/login` poll need neither, as
before. INSTALL.md says so next to the one-time code.

`tests/test_the_setup_code_is_good_for_one_browser.py`: the first browser gets the cookie, a second is refused
on GET and on POST (nothing applied), the bound one saves, cookie-without-code and wrong-cookie-with-code are
refused, the saved page is open to all, and `/?token=` binds too; the guide is read. Six of nine fail with the
old gate. `test_set_it_up_in_the_browser`'s real-server test drives one cookie-keeping browser now and adds
the second-browser refusal over a real socket.

### 🧪 The setup app's routes are walked like the dashboard's (#674)

Audit L10, test-only. `tests/test_every_route_is_behind_the_login.py` walks `create_app()`'s `url_map` so a
route added tomorrow is gated without anybody remembering the file - and its own docstring warned that a
second Flask app would escape the walk. `create_setup_app()` is that app: its own `before_request` gate, three
routes, each pinned by hand in `test_set_it_up_in_the_browser.py`, none walked. A route or an exemption added
to it later would have passed the suite unnoticed. `EverySetupRouteIsBehindTheToken` now walks it the same
way: every rule and method answers 403 without the token and 403 with the token from a foreign Host, the walk
is checked to see the three routes it is meant to, and nothing is applied by any of it. Mutation-checked with
an ungated `/lang` route plus a gate exemption for it: both walks catch it.

### 🔐 /logout answers POST alone (#673)

Audit L9. The route accepted GET (and HEAD) and cleared the session: with the cookie `SameSite=Lax`, a
top-level navigation from any site - a link, a redirect - to `http://127.0.0.1:8420/logout` carried the cookie
and logged the operator out; the dashboard's next poll answered 401 and the page dropped to the login form. A
nuisance, not a breach. The page's own button has always been a POST form (`web/index.html`), so GET was unused
by the app. `methods=["POST"]` now; `tests/test_a_link_cannot_log_the_operator_out.py` runs the audit's
navigation and checks the session survives it (a GET lands on the static route's 404 rather than a 405, and
either way nothing happens), refuses HEAD/PUT/DELETE, keeps the page's own form working, reads the rule's
methods, and reads the page for the form. Three of five fail with the old route.

### 🧪 The running-pack fixture joins its packer before it ends (#828)

Test-only. `TheThreadIsTheAnswer` in `tests/test_a_rehash_keeps_the_interlocks_of_a_running_pack.py` (#651)
registered `addCleanup(self._let_everything_finish)` and then `addCleanup(setattr, runtime, "packer_thread",
None)`. Cleanups run last-in-first-out, so the reference was nulled first and the join found nothing: the
packer thread was released but never waited for, and its finally - `config.rar_inprogress = False`,
`redispatch_waiting_pack()` - ran into the next test's own pack. Seen on #827 (ubuntu / 3.14):
`test_while_rar_runs_the_pack_is_running` found the flag cleared under it. The cleanups are the other way
round now, the join asserts the thread finished, and `TheFixtureLeavesNoPackerBehind` runs one of the class's
tests on its own and checks no thread of its own is left alive and `runtime.packer_thread` is None - which
fails with the old order.
### 🔐 Every dashboard POST is checked against its own host (#672)

Audit L8. `SameSite=Lax` and the JSON content-type were the dashboard's only CSRF defences, and seven mutating
routes take no JSON body at all: `/api/tools/update-list`, `/api/filelists/purge-offline`,
`/api/filelists/sources/<nick>/remove`, `/api/filelists/<source>/purge`, `/api/fetch/<id>/delete`,
`/api/messages/read`, `/api/notices/read`. "Site" does not include the port, so a plain HTML form auto-submitted
on `http://127.0.0.1:9000` - a dev server, a NAS or media UI that renders attacker-influenced HTML - was sent
to `http://127.0.0.1:8420` with the operator's session cookie attached: every offline bot's fetched lists
purged, a full master-list rebuild started (the auditor's probe got `200 {"update":"started"}`).

The login's own check (`_login_origin_ok()`, #609 - Origin, or Referer for an older browser, must name the
request's own Host) now runs for every POST in the `require_login` before_request hook, after the login check
and before any route, so a route added later is covered without knowing it: `403 {"error": "This request was
sent by another site and was ignored."}`. A request with neither header (curl, a script, the test client) is not
a page forwarding another site's form and passes, as at the login; the page's own `fetch()` calls carry their
own origin and pass. `tests/test_a_form_on_another_local_port_cannot_drive_the_dashboard.py`: each of the seven
refused before it acts (the rebuild is not started), Referer-only and `null` origins refused, every POST rule
in the map covered, and the controls - own origin answered, headerless answered, a cross-port GET untouched,
an anonymous forgery still 401 first. With `webserver.py` unpatched, five of nine fail.

### 🤖 A burst of new nicks no longer evicts the real bots from the registry (#671)

Audit L7. Any channel member can register a "bot" in `runtime.known_bots` with one unauthenticated line
(`Type: @<theirnick> For My List Of: 1 Files`; the RAR wording needs no identity claim at all).
`_prune_known_bots()` evicted by `last_seen` ascending once the registry passed `KNOWN_BOTS_MAX` (2000), and
2001 fresh nicks carried the newest `last_seen` of all - so the genuine bots that had advertised minutes
earlier were the ones dropped, gone from the List Browser until their next advert, while the junk sat there for
up to a week. The docstring claimed the opposite, for exactly the burst the cap was added for.

`_record_bot()` now counts the adverts an entry is built from (`adverts`), and eviction goes by that count
first, then by `last_seen`: a nick that said it once is what goes; a bot that advertises every few minutes has
said it more than once by the time a flood of that size can arrive. An entry with no count (an older file)
counts as one; hand-entered bots are still never candidates.
`tests/test_a_burst_of_new_nicks_does_not_evict_the_real_bots.py` runs the auditor's recipe through the
capture path in both wordings, keeps the least-recently-seen-goes-first order among one-offs, checks the
older-file case, the count and the hand-entered exemption; with `irc.py` unpatched, three of six fail.

### 🧹 What a user typed reaches the log as printable text (#670)

Audit L6. `!DCCore !rar ]0;pwned4,4 SENT: admin.rar to victim` from any channel member: the
artist-root refusal printed the text to stdout (a Windows Terminal window was retitled by the ESC sequence) and
sent it through `send_debug()`, which strips only bold, reset and the mIRC colour byte, so the debug channel
and the colour-rendering admin chat showed a red block that read like a fake SENT line inside the PART line. A
search term took the same route through `execute_search()`'s own print and `feed_event()`. CR and LF cannot be
injected (the reader splits on them), so no IRC command can be forged: cosmetic and misleading, not a takeover.

`list.printable_text()` - `strip_control_codes()` and then every remaining C0 control, DEL and the C1 range -
is applied where `irc.py` takes the request and the search text off the wire, so what every handler prints,
logs and feeds is what the operator sees. NBSP and everything above U+009F are untouched; a search that is
nothing but control characters is not run. `tests/test_what_a_user_typed_reaches_the_log_printable.py`: the
cleaner on the audit's probe, on every control character, on real request text and on mIRC formatting; and
`irc_loop()` driven for real (the ladder harness, threads recorded) - the request and the search handlers are
handed the plain text, an all-control search is not started, an ordinary request arrives as typed. With
`irc.py` unpatched, three of the eight fail.

### 🔐 A very broad ADMIN_HOSTMASKS entry is said out loud (#669)

Audit L5. `is_admin_host()` refuses only a pattern that reduces to nothing once wildcards and separators are
stripped; a wildcard domain is accepted on purpose (`*.example.org` names a real set of hosts; pinned by
`test_ban_breadth_guard`). But the documented shape is `<account>.users.undernet.org`, and an operator who
writes `*.users.undernet.org` has put the wildcard where their account name goes: every X-authenticated user of
the network reaches the console's password prompt, can hold the single pending session against the real
operator, and costs the bot a dial per CTCP. `*.org` names a top-level domain. The audit's verdict was that at
most a warning is warranted, and that is the change: what matches is untouched.

`adminchat.broad_host_patterns()` names the two shapes with a reason - a bare wildcard in front of a shared
account-host suffix (`SHARED_ACCOUNT_HOST_SUFFIXES`: `users.undernet.org`, `users.quakenet.org`; case and the
`*!*@` form handled by `admin_host_patterns()`), or a literal part of one label - and
`report_broad_host_patterns()` prints one `[ADMINCHAT] WARNING: ADMIN_HOSTMASKS entry '...' is very broad`
line per entry, saying what to write instead. Called at boot (`oserve.startup()`), after the reload on
`!rehash` (which is how the setting changes live), and by `setup_check.py` as a `warn`. ADMIN-CONSOLE.md says
so next to "Wildcards work". `tests/test_a_very_broad_admin_hostmask_is_said_out_loud.py`: the classifier on
each shape and on the legitimate ones (`operator.users.undernet.org`, `*.example.org`, `op*.users.undernet.org`
- silent), the broad pattern still matching a stranger, the report text, the boot log with and without the
entry, the rehash saying it after the reload, and the pre-flight's `warn`.

### 📬 A request made during a rehash is queued, not dropped (#668)

Audit L4. While a rehash quiesced (`config.transfers_paused`, up to `REHASH_TRANSFER_WAIT` per rehash - and
several dashboard saves queue several waits back to back) `dcc.handle_download_request()` sent *"The bot is
reloading its configuration. Your request is not lost - try again in a moment."* and returned without queuing
anything; nothing replayed it after `resume_transfers()`. The request was lost unless the user typed it again,
and they had been told to wait. The console said "Held a file request", which it had not.

Only the dispatch has to wait. The request now goes on to the queue: the direct-send decision under
`queue_lock` also requires `not transfers_are_paused()` (that path never went through
`check_queue_and_send()`'s gate, so without it a request would have started a send the reload landed in the
middle of), a `!rar` row queues as before with its dispatch gated, and the notice says *"Your request is queued
and starts when the reload is done."* The rehash's wake after the reload runs `dcc.wake_restored_queues()` -
one look per free slot - instead of one `check_queue_and_send()` pass, which dispatches one user and breaks:
with requests queued rather than refused, several users can be waiting on that wake with nothing else due to
wake them.

`tests/test_a_request_during_a_rehash_is_queued_not_lost.py` drives `handle_download_request()` for real
during the pause: a file request with free slots is queued and no send starts; a `!rar` request is queued; the
notice says queued, not "try again"; after `resume_transfers()` the wake starts the send for dave, and for two
held users starts both; the console line says queued; and `commands.py`'s wake targets
`wake_restored_queues`. With the old gate restored, six of the seven fail.

### 🧪 The outbound pace is put back after a test (#667)

Audit L3, test-only. Six setUps - two in `test_a_shared_outbound_pace.py`, one each in `test_reconnect.py`,
`test_the_vip_lane_gets_one_slot_per_pass.py`, `test_the_pump_waits_for_the_joins_to_land.py` and
`test_announce_output.py` - assigned `config.MSG_DELAY` (0.01 / 0.05) or `config.DEBUG_MSG_DELAY` (0.01) directly,
and `reset_config()` did not reset them, so the shipped 5.0 s / 0 never came back for the rest of the process:
every test after them in the run - alphabetically most of the suite - was paced at 10 ms and would have stalled
five seconds a line on its own. The comment claiming isolation only replaced the pacer object. Nothing failed
today (every multi-send test pins its own pace), which is the hole: a later test that did not would pass in the
full run and time out in isolation.

Both names are in `support.SETTINGS_DEFAULTS` at the shipped values and the six setUps go through
`set_config()`. `tests/test_the_outbound_pace_is_put_back_after_a_test.py`: `reset_config()` restores both; the
harness values equal what `defaults.py` ships (a retune must retune both); the audited class's tearDown puts the
pace back; and no test file assigns either name on `config` directly (mutation-checked both ways). The full
suite runs in the same time with the shipped pace between tests.

### 📝 The mIRC docs and script no longer require a bot that does not exist (#666)

Audit L2. `docs/ADMIN-CONSOLE.md` ("a bot of 1.13 or later", "older than 1.13", "before the 1.13 release") and
`scripts/mirc/dccore.mrc` (its header, the `/dccore version` text and two comments) named 1.13 as the bot the
script needs, from a tree whose `SCRIPT_VERSION` is v1.12.2 - a release that has not been cut. An operator who
read the requirement against their bot's version would conclude the script could not work with it. The claims
now say what they mean: a bot that answers `hello`, the DCCore the script ships with or a later one; the bare
`1` in `HELLO` is "a bot from before the minor was added". The release roll is where a number may be named.

`tests/test_the_docs_do_not_require_a_bot_that_does_not_exist.py` reads the shipped operator docs and the
script: no "DCCore x.y", "bot of x.y", "older than x.y", "before x.y", "since x.y" or "from x.y" may name a
version above `SCRIPT_VERSION` (the changelogs and the roadmap are outside the sweep; they may name what is to
come). The regex is itself tested against the six phrases the audit found and against "protocol 1.1" and
"mIRC 6.10". Not verified in mIRC: the script change is comment and message text only.

### 🔐 config.send_queue has a lock (#665)

Audit L1. The per-user text lanes are written by every request, search and reply thread
(`oserve.queue_message()`: not-in, create, append) and drained by the pump (`queue_mgr.next_standard_line()`:
get, falsy, pop the key), with no lock: their correctness rested on where CPython happens to check for a thread
switch - unreachable on 3.11+, reachable on 3.10 (the documented minimum) and on a free-threaded build. Where
reachable, the line just appended landed on a list the pump was dropping with its key and vanished, or the
producer died on a KeyError mid-results.

`runtime.send_queue_lock` (runtime.py, so a rehash cannot rebind it) guards `next_standard_line()`, the pump's
per-user cap and `queue_message()`, whose three steps are one `setdefault(...).append(...)`.
`tests/test_the_send_queue_has_a_lock.py` makes the interleaving deterministic: the pump holds the lock and drops
an emptied key, a request thread arrives and must wait, and its line lands in the live dict afterwards.

### 🔁 The reconnect backs off, and the server's ERROR line is shown (#663)

Audit M61. Every reconnect path slept a flat 10 s. ircu's IPcheck throttles an address that reconnects too often
inside its clone period (4 in 40 s by default), counts refused connects too, and resets only after a gap longer
than the period - so once a run of drops tripped it, the 10 s cadence kept it tripped: every attempt was answered
with `ERROR :Your host is trying to (re)connect too fast -- throttled` and closed, and the bot never got back on
by itself. The ERROR line was read and dropped, so the log said "Server closed connection" and nothing about why.

`reconnect_delay(failures)` doubles from 10 s to a five-minute ceiling for attempts in a row that never reached
a 001 (a failed connect, a failed handshake send, a link closed before registering); a connection that
registers resets the count, so an ordinary drop still comes back in ten seconds, and the wait is printed as it
grows. An `ERROR` line from the server is printed as `[SERVER] ...`, and one that says throttled/too fast is
named for what it is. `tests/test_the_reconnect_backs_off.py` drives the real `irc_loop()` through a series of
scripted connections (the audit's fake-ircu probe: 10, 20, 40, 80; a registration resets; an ordinary drop is
10) with the reconnect sleep recorded.

### 🍎 The macOS Gatekeeper note covers Sequoia (#662)

Audit M60. The launcher's header, the autostart installer and INSTALL.md said: the first time, right-click the
.command, choose Open, confirm once. Apple removed that Control-click override in macOS 15 (Sequoia): after the
refusal the file has to be allowed from System Settings > Privacy & Security ("Open Anyway"), or de-quarantined
with `xattr -d com.apple.quarantine`. A first-timer on Sequoia following the note got the same refusal again with
no Open button. The header also pointed at a README-FIRST.txt that has never existed.

All three texts now give both roads - right-click > Open on macOS 14 and earlier, Privacy & Security > Open
Anyway on 15 and later - and the xattr one-liner for both launchers at once; the phantom file is gone.
`tests/test_the_gatekeeper_note_covers_sequoia.py` reads them. Docs and comments only.

### 🌐 dccore.mrc dials on the bot's network, whatever connection fired it (#661)

Audit M59. The script never recorded which network the bot lives on: `dcc chat <bot>` ran in whatever connection
invoked it - the event's own for CONNECT/JOIN/401/CHATCLOSE, the active window's for /dccore connect - and the
retry timer is one global name. On a client on two networks the CTCP went to the wrong one: a 401, a retry loop
stuck there ("X is not online" every two minutes while the bot was up), and every reconnect of the other network
saying "already open".

`dccore.remember.net` keeps `$network` (or `$server`) from the moment the operator types /dccore connect or
/dccore pair - that connection IS the bot's - or from the bot's own JOIN when nothing is recorded; `dccore.cid`
finds that network's connection id across every connection (`$scon`), `dccore.connect` moves itself onto it with
/scid (and says so when that network is not connected), and the CONNECT and JOIN triggers fire only there
(`dccore.here`). /dccore unpair forgets it; /dccore version names it. All mIRC 6.0-era multi-server identifiers.
ADMIN-CONSOLE.md gains the "more than one network" entry. `tests/test_the_script_dials_on_the_bots_network.py`
reads the script. Not verified in mIRC.

### 🔒 The freeze timer tests and takes the freeze under the lock, in one move (#659)

Audit M57. `user_queue_timer`'s expiry read `t_key in config.frozen_queues` outside `queue_lock`, then under the
lock deleted the user's queue and did an unconditional `del config.frozen_queues[t_key]` without looking again.
The JOIN thaw (an unlocked pop) and the sweep thaw (under the lock) both remove that key; one landing in the gap -
the user back at the 300 s mark - meant the timer erased the queue of someone who was verifiably present and then
died on the KeyError, with no "Timer expired" line. Rare, silent.

The expiry now does one `frozen_queues.pop(t_key, None)` under the lock and erases the queue only when that
returned a value; a thaw in the gap leaves the queue alone and says so.
`tests/test_the_freeze_timer_takes_the_freeze_under_the_lock.py` makes the race deterministic - the test holds
`queue_lock`, waits until the countdown is blocked on it, thaws the user, lets go - for the JOIN thaw and the
sweep thaw, with the plain expiry as the control; both race cases fail against the old code exactly as the audit
described.

### 📢 "Sent:" for a private request on the direct path goes to a channel (#658)

Audit M56. `handle_download_request()` handed the raw wire target to `start_dcc_send()` as the announce channel on
the direct-send path (a slot free, no queue - the common first request). For a private request that target is the
bot's own nick, so `send_transfer_complete()` built `PRIVMSG <ournick> :Sent ...`: queued into the VIP lane, a
pacer slot spent, dropped by the read loop as our own message. The transfer completed and the feed's SENT event
fired; only the public advert was lost. #530 fixed exactly this for rows picked up from the queue via
`announce_channel_for()`; the direct path never went through it, and a PM `!list` takes the same path.

The direct path now resolves `announce_channel_for(next_file_fake)` - the request's channel if it is one, the
configured default otherwise - and hands that to both the SENDING notice and `start_dcc_send()`, as the queued
paths do. `tests/test_a_private_requests_sent_line_goes_to_a_channel.py` drives a PM request through the real
path with the send captured, and checks the line `send_transfer_complete()` builds from it at the wire.

### 🔁 A packed archive whose send fails is retried, not deleted (#657)

Audit M55. On any failed send of a packed .rar - the 30 s accept timeout with the user away from the keyboard, a
receiver that hung up, a stall - `start_dcc_send()`'s finally deleted the archive (its step 4) before settling the
row (step 5), and `release_queue_entry()` then classified the row as a "consumed temporary archive" and dropped
it with "Removed from your queue". A plain file in the identical situation was kept and re-offered up to
MAX_SEND_FAILS times; the justification for the difference was circular - the archive was only unusable because
that same finally had just deleted it - and a 3 GB album that took ten minutes to pack got exactly one 30-second
window before the user had to !rar it again, holding rar_inprogress for everyone while it packed a second time.

The finally now settles the row first and the cleanup keeps the archive for a row that was kept; a packed row is
retryable while its archive exists on disk (the same MAX_SEND_FAILS budget, the same 15 s × attempts back-off),
and is dropped - with the archive - only when the budget runs out or the archive is gone. The dispatch paths
already guard temp rows with an existence check. `tests/test_a_failed_pack_send_keeps_its_archive_and_row.py`
sends a real archive over loopback: a hang-up keeps both, an exhausted budget removes both, a delivery still
cleans up; the existing `test_queue_integrity` case now says what it guards (an archive that is gone).

### 🧾 An acknowledgement past what was sent is not a completion (#656)

Audit M54. `_AckTracker` accepted any 32-bit word above what it held, with no upper bound tied to what had
actually been sent, and `_wait_for_final_ack()` only tested `acked >= file_size`. One word of 0xFFFFFFFF from a
peer that read nothing therefore completed any file under 4 GB: "Sent:" announced, Files/bytes totals and the
most-downloaded counter incremented, the queue row consumed, at no bandwidth cost - and repeated, the public stats
inflated. A legitimate client acking in the wrong byte order (4096 → 1 MB) was declared complete after one packet
and cut off.

The send loop keeps the tracker told what has been handed to the kernel (`acks.sent`), and `_advance()` ignores
and counts a word past it: not a position the receiver can hold. The transfer then lives or dies on the real acks
- a peer that sends only bogus words stalls and fails, and an honest client whose stream includes one stray high
word still completes on its real ones. `tests/test_an_ack_past_what_was_sent_is_not_a_completion.py` plays the
audit's probe over loopback (a failure, no "Sent:") and the honest-client case; the isolated tracker tests now
say what was sent, as the send loop does.

### 🎟️ The shared outbound clock serves its waiters in arrival order (#655)

Audit M53. `OutboundPacer.wait_for_slot()` was sleep-and-retry with no queue: every waiter slept until the same
instant and whoever woke first took the slot. Four threads share the clock - queue_worker's VIP and standard
lanes, the debug drain, the !ping and DCC ACCEPT direct waiters - so queue_worker's strict alternation bounded
VIP to two of its OWN slots while the worker lost each of those to the drain by coin toss: with a drain backlog
VIP got about a quarter of the slots and gaps of ten to fourteen slots (a minute at MSG_DELAY=5) between
consecutive VIP lines, long enough to push a "Sending:" notice past the receiver's accept window.

Tickets now, handed out in arrival order and served in that order, on a Condition: only the ticket being served
sleeps against the clock, the rest wait to be woken, and a waiter that leaves without its slot (its thread torn
down) marks its ticket abandoned so the line moves on. The combined rate is unchanged - every reservation still
holds the one clock for its interval - and the queue_mgr comment now states the bound it can actually promise.
`tests/test_the_outbound_clock_serves_in_arrival_order.py`: the audit's three-lane probe (no lane skipped while
waiting), a held clock released over four queued waiters served in the order they arrived, an abandoned ticket
stepped over, and the interval still enforced.

### 📬 A private `!rar` request is routed by its folder label (#653)

Audit M51. `list_for_request()` answers a target that is not a channel with the primary, on the premise that a PM
carries nothing to route on. A `!Bot !rar <Label>/<Album>` row copied from a list bound to another channel and
sent by /msg - a common habit with serving bots - was therefore resolved against the primary's folders: refused
as not found when the label existed only in the other list (no reply to the requester, "Directory not found" in
the debug channel), or, with the same label and path under the primary too, the primary's folder packed instead
of the one the row advertised - the outcome routing exists to prevent.

The label is something to route on. `library.list_name_for_label(label, default)`: the primary keeps the request
if it has the label; otherwise the one other list that has it; two others whose labels name the same folder are
one answer; two naming different folders are ambiguous and `None` says so. `handle_download_request()` applies it
to a `!rar` request whose target is not a channel and, on ambiguity, sends a new `ambiguous_list` notice: "request
it in the channel it was advertised in". A bare filename by PM is still the primary's - it carries no label.
FUTURE.md's stage-3 sentence says so. `tests/test_a_private_rar_request_is_routed_by_its_label.py` drives the
real request path over two lists on two trees.

### ⏱️ The freeze box has one clock (#652)

Audit M50. Two things measured an absent user's five minutes. The per-user timer thread counted ten seconds at a
time and refused to count while the bot was offline; the sweep at the top of `check_queue_and_send()` compared the
frozen timestamp with wall time and ran the moment `bot_joined_channel` came back - which activation sets as soon
as ANY channel's NAMES has arrived. So a user who left #b a minute before the bot lost its link for eleven
minutes had their queue and temp archives deleted by the wake-up sweep on the way back ("frozen for over five
minutes and never came back") while the timer thread still said sixty seconds - and before #b's NAMES had even
arrived.

The disconnect epilogue now stops the clock (`dcc.pause_freeze_clock()`, the moment kept in
`runtime.freeze_clock_paused_at` so a rehash cannot lose it) and activation restarts it
(`dcc.resume_freeze_clock()`, before the wake-up sweep) by moving every frozen timestamp forward by the outage. The
sweep, the timer thread - which now reads its elapsed time off that timestamp instead of counting on its own -
and the console's seconds-left therefore all measure the same thing: time the bot has been online since the
freeze. And neither deletes a queue whose channel the bot has no member list for yet
(`frozen_users_channel_is_synced()`): until that channel's NAMES arrives, absence is not an observation.
`tests/test_the_freeze_box_has_one_clock.py` replays the audit's scenario (kept as the control, it deletes; with
the outage taken out, it keeps) and parks the timer thread on an Event to show it reads the clock.

### 📦 A rehash keeps the packer's interlocks while a pack is still running (#651)

Audit M49. `wait_for_transfers_to_finish()` counts a running folder pack as busy, but after REHASH_TRANSFER_WAIT
(120 s) it returns False and carries on - and the rehash never looked at the value. The reload then reset
`config.rar_inprogress` to False (defaults.py re-executes) and the rehash rebound `user_processing_lock` to an
empty set, both while `rar` was still running (RAR_TIMEOUT is half an hour). The user's still-queued row passed
both interlocks on the next trigger for that nick - a second !rar, a JOIN thaw, the freeze-abort timer - and a
second packer started on the same archive path, unlinking the file the first was writing: the double-pack the
packer's own docstring records fixing, reopened by a dashboard Save two minutes into a big box set.

The flags cannot tell packing from wedged; the packer's thread can. `inline_rar_packer` records itself in
`runtime.packer_thread` (runtime.py is never reloaded) and clears it in its finally; `dcc.a_pack_is_running()`
reads it. The rehash reads that right after the wait and again after the reload, and
`commands.clear_or_keep_pack_interlocks()` keeps both interlocks (putting `rar_inprogress` back after the reload's
reset) and says so in the log while the thread is alive, and clears them - the documented escape hatch for a
packer that died holding them - when it is not. The packer's own finally still releases them when it finishes.
`tests/test_a_rehash_keeps_the_interlocks_of_a_running_pack.py` drives a real pack to the point where rar runs.

### ⏲️ DEBUG_MSG_DELAY says it is floored to MSG_DELAY, and ships as 0 = "the same" (#650)

Audit M48. The debug drain asks the shared clock for `max(MSG_DELAY, DEBUG_MSG_DELAY)` - on purpose since #406 -
so any DEBUG_MSG_DELAY below MSG_DELAY is inert, and the shipped 0.5 was one. The defaults.py comment, the
Settings-page help in three languages and settings.conf.sample all described it as "the same wait, for lines
going to your debug channel": an operator lowering it to speed the debug channel up saw nothing change and had no
way to learn why.

The pacer is unchanged. The default is now `0.0`, meaning "the same as MSG_DELAY" (no behaviour changes: the
expression already gave MSG_DELAY for anything below it), and every text says the floor: never less than
MSG_DELAY, every line the bot sends shares one clock, a smaller number has no effect, a larger one slows the debug
channel further. `tests/test_debug_msg_delay_says_it_is_floored.py`. Sample regenerated.

### 🔑 Each mIRC install pairs under its own name (#649)

Audit M47. Every copy of dccore.mrc paired as the literal `dccore.mrc`, and the bot keeps one token per name,
replacing it when the name pairs again - so pairing the script on a laptop silently revoked the desktop's token.
Before #601 made the script stop redialling on a refusal, the desktop then looped through refusals until the
shared home address was blocked for fifteen minutes, with nothing but a stdout line on the bot to say why.

The script now pairs (and unpairs) as `$dccore.client` = `dccore.mrc-<8 hex>`, the tail being the mIRC folder
hashed (`$md5($mircdir)`, both mIRC 6.0-era), so two installs are two names and two tokens; the same install
pairing again still replaces its own token, which is how a lost one is rotated. `hello` still names the client
type. `/dccore version` says which name this copy pairs as. ADMIN-CONSOLE.md's pairing passages say so. The bot
side needed no change; `tests/test_each_mirc_install_pairs_under_its_own_name.py` proves two names hold two
valid tokens and reads the script for the name it sends. Not verified in mIRC.

### 🧪 The dashboard's JavaScript is parsed by a real engine where one exists (#648)

Audit M46. Every web/ test was a hand-written scanner modelling comments, strings and bracket depth; the audit fed
`web/app.js` an unbalanced ternary, a missing operand, `var var`, misplaced-but-balanced braces and a dangling
`else`, and every test stayed green while `node --check` rejected each. The inline `<script>` at the top of
`web/index.html` was scanned by nothing. Node is on every GitHub-hosted runner and was unused.

`tests/test_the_dashboard_javascript_parses_in_a_real_engine.py` runs `node --check` on every `web/**/*.js` and on
every inline `<script>` block in `web/*.html` (written out to a temp file), skipping where there is no node -
the scanner is the everywhere half, and its docstring now says so - with a control that the same command refuses
the five snippets, so a node that accepted everything could not pass it. Test-only.

### 🔍 The launcher's search for an installed Python is executed (#647)

Audit M45. `start-dccore.bat` searches `%LOCALAPPDATA%\Programs\Python\Python3*` and `%ProgramFiles%\Python3*`
when `where` finds nothing - the commonest reason a first-timer's Python is invisible is the missed "Add to PATH"
box - and no test had ever run that search: every test either had Python on PATH or pointed both folders at
empty directories, and "searches again after installing" was a text match. The verifier also found that the
tests' `ProgramFiles` override was silently undone: a 64-bit cmd.exe resets ProgramFiles from ProgramW6432 on
start, so the launcher under test really searched `C:\Program Files\Python3*`, and every TheOfferRun test
would have failed on a machine with an all-users install.

`ThePythonTheInstallerPutSomewhere` (Windows-only, like every .bat test) plants a working python.exe - a venv
redirector copied up beside its pyvenv.cfg, no 30 MB install - under a tree whose name has a space, with nothing
on PATH: the per-user folder is found, the all-users folder is found, per-user wins when both exist, a leftover
folder with no python.exe is passed over, and the control with nothing planted reaches the offer. Breaking the
glob fails four of five. Both launcher fixtures now override ProgramFiles, ProgramW6432 and ProgramFiles(x86)
together - and case-insensitively (`env_with()`): os.environ upper-cases its keys on Windows, so a plain
`dict.update({"ProgramW6432": ...})` added a second key differing only in case, and which duplicate the child
saw was luck - the override won here and the original won on one CI runner, where the all-users tests then
searched the real Program Files. Test-only.

### 🚪 The setup page's "do not open the browser" branch is executed (#646)

Audit M44. The test for `WEBUI_OPEN_BROWSER = False` set the flag and then read `run_setup_until_configured()`'s
source for the `if` line; the executed server test ran with the flag on, so the False branch had never run under
a test - moving the opener call outside the guard (keeping the `if`) passed all 42 tests in the module.

`test_set_it_up_in_the_browser.py` now starts the real server with the flag off, an opener that records, and a
`wait` that gives up the moment the page has said "No browser was opened here" - and asserts the opener was never
called, the link and the SSH-tunnel hint were still printed, and the server returned None. The audit's mutant
fails it. The source pin stays as the everywhere half (the executed test needs loopback), renamed to say so.
Test-only.

### ⏳ The DCC ACCEPT pacing is driven, not read (#645)

Audit M43. `tests/test_the_resume_handshake_takes_its_turn.py` read dcc.py for the string "wait_for_slot" before
"irc_sock.sendall(reply.encode(" in `_send_resume_accept()`'s text - which a comment satisfies, and which stayed
green with the call commented out or moved into `if False:`; only "moved after the send" was caught. The function
needs only a `runtime.dcc_send_offers` entry and an object with `.sendall()`, and
`test_complete_means_the_receiver_acked_it.py` was already calling it for real.

A spy in place of `runtime.outbound_pacer` and a recording socket share one log, and the order of the two calls is
read off it: the ACCEPT goes out, it takes a slot of MSG_DELAY (not a number of its own), the slot comes before
the write, a stray RESUME for no offer of ours touches neither, and with `background=True` (what the read loop
passes) the same order holds on the helper thread. All three of the audit's mutants fail. Test-only.

### 🎯 The feed's channel wiring is driven, not read (#644)

Audit M42. `tests/test_the_feed_says_which_channel.py` checked the SEARCH and both REQUEST channel wirings by
regex on list.py and dcc.py, under a docstring saying the emitters were "not callable without a live socket and
a list on disk" - while `execute_search()` was driven in three other test files and `handle_download_request()`
in ten. A regex on the call is satisfied by a call whose `channel` has been shadowed with the wrong value two
lines above it; the queue-pickup SENDING sites were checked the same way, by counting call sites.

The file now drives the real functions against a one-track library and master list with dcc's threads recorded
(`InlineThread`) and reads a real event sink: a search with a hit and one without, a file request and a folder
request (and its QUEUED), and three of the four SENDING sites - the direct send, the per-user queue pickup and the
global sweep. The fourth (a packed archive picked up from the queue) needs a real rar run and stays a text check
that says so. The audit's own mutant - `channel = "#wrongroom"` before the SEARCH emit, `target_chan` before the
REQUEST one - passed the old file and fails six of these. Test-only.

### 🧵 Boot tests no longer leak a live fetch dispatcher thread (#799)

`tests/test_startup.py`'s BootCase stubbed `queue_mgr.queue_worker` so `oserve.startup()` does not leave a live
pump per test - but not `dcc_fetch.fetch_dispatcher_worker`, which startup() starts eleven lines later; only the
one subclass that tests the dispatcher stubbed it. Every other boot (and the browser-setup boot in
`test_set_it_up_in_the_browser.py`) left a real `while True` thread calling `check_fetch_queue()` every 2 s for
the rest of the suite, through whichever oserve stub a later test had installed. On ubuntu/3.10 the tick landed
between `paste(10)` and `set_config(transfers_paused=True)` in
`test_nothing_is_dispatched_while_transfers_are_paused`, which then found three requests already sent - the red
first runs of #797 and #798.

Both fixtures stub the dispatcher now; `tests/test_a_boot_test_leaves_no_dispatcher_behind.py` boots through the
fixture and asserts no thread runs the real loop afterwards, and asserts the same suite-wide for every boot that
ran before it. Test-only.

### 🧾 preflight's state guard checks every pass, sees directories, and data/fetched is redirected (#643)

Audit M41. `scripts/preflight.py` compared its state snapshot once, right after the first checks; the count pass
and the hostile pass ran afterwards with no comparison, so a write that only happens with ProgramFiles stripped
passed preflight. And the snapshot walked files only: an empty directory created under data/ - `data/fetched`,
which `oserve.startup()` makedirs for every test that boots the daemon without redirecting FETCHED_FILES_DIR -
was invisible (it was sitting in this worktree, left by the suite, when this was written).

The snapshot is taken once and compared after each of the three suite-running passes; it records directories
(reported with a trailing separator) as well as files; and `DCCoreTestCase` redirects `FETCHED_FILES_DIR` under
its own temp dir like the nine state files before it, without creating it - the code under test does that.
`tests/test_preflight_checks_every_pass_for_state_writes.py`.

### 🛫 preflight counts what was skipped, and keeps cmd.exe in the hostile pass (#642)

Audit M40. `scripts/preflight.py` parsed only "Ran N": a skipped test is one that ran nothing, and a pass that
skipped a hundred printed PASS. On Windows a good share of the launcher and OS-script tests skip - the hostile
pass strips PATH to the interpreter's directory, so cmd.exe (the operating system, not host tooling) was
unfindable and every .bat class skipped there; from PowerShell there is no bash either, so the POSIX launcher
classes skipped in the normal pass too. A launcher regression could pass local preflight with nothing having run
it.

The count pass now runs verbose and `skip_report()` reads the summary count and every per-test reason; preflight
prints them (`=== skipped: N of M (ceiling 60) ===` and a reason-by-count list, with a hint when the reason is a
missing POSIX shell) and refuses to pass above `MAX_SKIPPED = 60` - well above what any one platform legitimately
skips, low enough that a whole family going dark trips it. `hostile_env()` keeps `%SystemRoot%\System32` on the
Windows PATH so cmd.exe is found and the .bat launcher tests run in that pass; Program Files stays hidden.
CONVENTIONS.md says what the skip report means. `tests/test_preflight_counts_what_was_skipped.py`.

Running the real preflight for this found two more things. `irc.py::irc_loop` left `tests/uncovered_functions.txt`:
#633's tests drive it against a scripted socket, and the gate fails on an allowlisted function that has become
covered (CI does not run that step, which is why #789 was green). And
`test_the_fallback_trigger_still_fires` failed in the hostile pass with `['dave'] != ['someuser']`: every completed
send in the suite starts a `delayed_queue_trigger_fallback` thread that calls `dcc.check_queue_and_send` 3 s
later through the module attribute, so one from an earlier test landed in this test's recording stub in an order
the hostile pass produces. The stub now counts only its own user.

### ⏱️ The queue-progress receiver acks, so its three tests take 0.3 s instead of 60 (#641)

Audit M39. Since #526 the sender waits for the receiver's final ack to reach the file size before it closes.
`tests/test_queue_progress_is_recorded.py`'s receiver read to EOF and never sent the 4-byte ack, so each of its
three tests ended only when the client's 20 s recv timed out - a deterministic 60 s per full run, with the
sender logging "never acknowledged a single byte" and "Not counted ... ended short at N of N" on green tests,
and a slower runner one race away from the "flaky on Windows" failures the file's history already records.

The receiver now acks the running total after every read (`take()`), exactly as
`tests/test_dcc_resume_end_to_end.py`'s does, and `drain()` asserts the whole file arrived and that the sender
thread actually finished rather than trusting a join with a timeout. A silent receiver now fails the tests
outright (recv times out) instead of passing slowly. Test-only; nothing shipped changes.

### 🔢 HELLO carries the feed's minor version, and the guide says how to update a loaded script (#639)

Audit M37. The channel field went into seven structured lines with PROTOCOL_MAJOR left at 1 (#574: "major 1 takes
the extra field rather than a new number"), and the script only refuses `$2 != 1` - so an already-loaded older
dccore.mrc connected to the new bot without a word and read the channel as the position, the slot, the byte count
("slot #mp3/1", "at ##mp3"), and no document said how to replace a loaded script or what a mismatch looks like.

`adminchat.PROTOCOL_MINOR = 1`; HELLO is now `DCCORE HELLO 1.1 <botnick> <version>`. The minor goes up every time
a fixed field is inserted into a major-1 line. The script (1.1, `dccore.protominor` 1) reads major and minor apart:
an unknown major is plain mode as before; an unknown minor keeps the structured feed and says *"speaks feed 1.x
and this script was written for 1.y: some lines will show fields in the wrong place. Update whichever is older"*,
with the reload command. The pre-minor script's own `$2 != 1` refuses "1.1" and falls back to plain mode saying
"Update the script" - the message it was missing (mIRC compares numerically; the bot's constant must never read
as exactly 1 again, and a test pins that). ADMIN-CONSOLE.md documents major.minor, gains "Updating the script"
(save over the old file, `/reload -rs dccore.mrc`, `/dccore connect`; why not `/load`) and a troubleshooting entry
for channel names where numbers should be. `tests/test_the_feed_says_which_minor_it_speaks.py` holds the bot's
constant and the script's alias to each other. The mIRC side is not verified in mIRC.

### 📖 ADMIN-CONSOLE.md no longer credits configure.py with the hostmask step (#638)

Audit M36. Step 2 of the console guide said `configure.py` "does steps 2 and 3 together". It does step 2 only -
the password hash into admin_config.py - and never asks for ADMIN_HOSTMASKS (step 3); with no usable hostmask
pattern the console ignores every DCC CHAT in silence, by design. A novice who took the guide at its word skipped
step 3, got no reply, and was sent by "When it does not work" to check +x and typos instead of the step they had
been told was done.

The paragraph now says configure.py does this step for you, does NOT do step 3, and names both places to put the
hostmasks (admin_config.py by hand, or Settings → Admin console); the troubleshooting entry for "ignores you
completely" names the never-set case and why. `tests/test_the_console_guide_says_what_configure_does.py` reads
the guide and checks the premise against configure.py's own prompts, so a hostmask prompt added later flags the
wording the other way round.

### 🪟 WINDOWS.md names the file that actually turns the dashboard on (#637)

Audit M35. The guide's fix for `[WEBUI] Disabled via config.WEBUI_ENABLED = False.` was "set WEBUI_ENABLED = True
in admin_config.py or settings.conf". On a configure-made install that declined the dashboard, settings.conf holds
`WEBUI_ENABLED = false` - the setup writes the answer either way - and settings.conf is applied after
admin_config.py, so a True added to admin_config.py changed nothing; the operator restarted, saw the same line,
and concluded the bot was broken. (The sample-seeding half of the finding was fixed by #623 and #636.)

The passage now names settings.conf, says it is the file that wins where both set a name (and that the daemon
reports the shadowed line at startup), says the setup wrote the answer there, and spells the value the way that
file reads it (`true`). `configure.write_settings_conf()`'s docstring and a test docstring claimed the declined
dashboard is "deliberately absent" from what is written - it is written as `false`, on purpose (a re-run that says
no must switch off what an earlier run switched on); both now say so, and a test pins the explicit `false`.
`tests/test_the_windows_guide_names_the_file_that_wins.py` reads the passage.

### 📄 admin_config.py.sample carries the defaults it documents (#636)

Audit M34. INSTALL.md and WINDOWS.md say "copy admin_config.py.sample to admin_config.py and fill it in", and two
of the sample's live lines were not the defaults: `ADMIN_CHAT_MODE = "listen"` sat under a comment naming "auto"
the default and "right for most setups" (so a hand-made install never dialled the operator's client, and the
console guide's table sent them debugging a choice they never made), and `WEBUI_ENABLED = True` opened a listener
defaults.py keeps off, from a file the docs describe as opt-in. #623 stopped the setup seeding from the sample;
the hand-copy path still read it.

Both lines now carry defaults.py's value (`"auto"`, `False` with a line saying why), and
`tests/test_the_sample_admin_config_says_what_defaults_say.py` holds every live line of the sample against
defaults.py - the password hash and the hostmask list are placeholders and excepted - so the two cannot drift
apart again.

### 🪪 The bot's own nick follows the server, not the NICK it sent (#635)

Audit M33. The three paths that rename a registered bot - the reclaim of the main nick when its holder quits, the
background monitor that does the same on a timer, and a rehash that found NICKNAME changed in the file - all
assigned `config.NICKNAME` the moment they wrote the NICK command, and nothing reconciled it afterwards: the NICK
event handler had no branch for the bot itself, and 438 ("nick change too fast", Undernet's 30 s window) was
matched nowhere. A refused reclaim, a refused rehash rename or a rename services forced on the bot left the
server knowing it by one name and config saying another until the next reconnect - and every "is this for me"
test (DCC CHAT/SEND/RESUME offers, private messages, the self-message filter, a KICK of the bot) compared
against a nick the bot did not hold.

The senders no longer assign. `note_own_nick_change()` - called from the NICK event handler, matched on the old
nick against the name the bot holds - is now the one thing that renames a registered bot; a refused NICK
(433/437/438) after registration keeps the name and re-sends nothing (the old branch re-sent the alternate to a
server that already called us that). The rehash keeps `config.NICKNAME` at the live name and puts the file's
new name in ORIGINAL_NICK as the target, so the reclaim path and the next connect chase it and a refusal cannot
leave config wrong. Registration is unchanged: the ladder (#633) assigns while unregistered because the 001
settles it. `tests/test_the_bots_own_nick_follows_the_server.py` drives the real read loop past 001 with the
threads it would start stubbed: a forced rename is followed, a reclaim is not the bot's name until the server
says so, a reclaim refused as too fast or as taken leaves the name alone.

### 🤝 NICK and USER go out back to back; nothing is read before registration is sent (#634)

Audit M32. After NICK, the handshake waited for a line containing 001, 002, PING or NOTICE before it sent USER.
A server that says nothing until it has both lines (some ircds, most bouncers) never triggered it: the 70 s recv
timed out and the connect was retried every 80 s for ever, with a log that only said "timed out". And when the
trigger did arrive, the reader broke out of the chunk it was in - every line already decoded after it was dropped
and the partial line in its buffer with them - so a 433 sharing a recv() chunk with "NOTICE AUTH" was thrown away
and the bot waited for a registration the server had already refused. A PING before 001 was used as the cue and
never answered.

The pre-registration reader is gone. NICK and USER are sent back to back, as every client sends them, and the main
loop is the registration from the first byte: it answers PING, walks the nick ladder on a refusal (#633) and adopts
the name from the 001. `tests/test_nick_and_user_go_out_back_to_back.py` drives the real `irc_loop()` against a
scripted socket: a silent server still gets both lines, a 433 in the same chunk as the first NOTICE (or split
across two) is acted on, a pre-registration PING gets its PONG. Two source-reading guards that pinned the old
reader's shape now pin the new one.

### 🏷️ A third nick when both configured names are taken, and 437 is a refusal (#633)

Audit M31. The 433/432 handler only reacted while the bot was still asking for its main nick: a 433 for the
ALTERNATE did nothing, and 437 (ERR_UNAVAILRESOURCE - the nick delay Hybrid, ratbox and Solanum apply after a
split or a kill) was matched nowhere. On a split storm, with ghosts holding both names, every reconnect was NICK
main, 433, NICK alt, 433, silence until the server's registration timeout closed the link, ten seconds, and the
same again for as long as the ghosts lived; on an EFnet-style server the same happened with 437 for the whole
nick-delay window even when the alternate was free.

`parse_nick_refusal()` reads 432/433/437 by the numeric (a 437 naming a channel is not a nick refusal and is
returned as None) and both the handshake loop and the main loop go through it - the handshake used to match
`" 433 "` and the English for 432. `fallback_nick(main, attempt)` is the ladder: the configured alternate first,
then the alternate with a digit 1-9 (appended while the result fits every server's NICKLEN, replacing the last
character otherwise, never the main nick again), then nothing more this connection - the count is per connection,
because the ghosts may be gone by the next one. Once registered the old rule stands: a refused reclaim of the main
nick goes back to the alternate and nowhere else, so a server that answers 433 for the name we already hold cannot
walk us down the ladder. ALT_NICKNAME's help says so in all three languages; sample regenerated.
`tests/test_a_third_nick_when_both_are_taken.py` drives the real `irc_loop()` registration against a scripted
socket (no network) and reads the NICK lines it sends.

### 🔑 A channel that needs a services login (477) is a refusal, not silence (#632)

Audit M30. `JOIN_REFUSED_NUMERICS` held 405/471/473/474/475. Undernet answers a JOIN to a +r channel from a nick
not logged in to X with 477 (ERR_NEEDREGGEDNICK); 476 and 479 say the name is not a channel name at all.
`parse_join_refusal()` returned None for all three, so nothing was printed outside DEBUG_MODE, nothing was
counted, and a channel the watchdog had marked "never confirmed" stayed at zero refusals - which
`channels_to_rejoin()` reads as "worth another JOIN". The advert worker re-sent that JOIN every ANNOUNCE_INTERVAL
for the life of the process and got the same numeric back each time; the operator's only clue was the one-off
"never confirmed via NAMES" line with no reason in it.

The three join the set and are counted like a ban. 477 gets its own wording (`JOIN_REFUSED_NEEDS_A_LOGIN`), since
the answer is not "wait it out": the debug channel says the channel needs a registered nick, that the bot must log
in to services first, and to put the login in Settings > On connect (or check it goes out before the JOIN) - and
still shows the attempt count, so a login that never comes ends in "gave up" like any other refusal. 437
(temporarily unavailable) is deliberately left out: that is what a channel is during a netsplit, and a retry that
never gives up is right for a refusal that says it will pass. `tests/test_a_channel_that_needs_a_login_is_a_refusal.py`;
the two existing guards on the set and the handler text updated.

### 📣 The advert skips a channel the bot is not in (#631)

Audit M29. `announce_worker` walked `irc.configured_channels()` with no membership check, so a channel the bot
had been kicked from - including one past REJOIN_ATTEMPTS, which gets no more JOINs - and a channel that was +i
and never answered its JOIN each still received the advert PRIVMSG and the CTCP SLOTS line every
ANNOUNCE_INTERVAL. The server answers 404, nothing reads that, and each line costs a MSG_DELAY slot on the shared
pacer that the channels the bot IS in were waiting for: with 14 channels and MSG_DELAY=5 that is 10 s of the
outbound clock per cycle, for the life of the process.

`irc.channels_we_are_out_of()` is a pure read of `config.kicked_channels` - which is exactly that set: a kick or
an unanswered JOIN writes the entry, the 366 of a successful join removes it - and the worker reads it once per
cycle and skips what it names. The rejoin above the loop keeps asking for as long as it is allowed to, and the
channel is advertised again the moment a 366 puts it back. Same division as the rejoin itself: irc.py owns the
rule, the worker only acts on it. `tests/test_the_advert_skips_a_channel_we_are_not_in.py` drives one real cycle
of the worker with a kicked, a never-confirmed and a fine channel.

### 🚦 The outbound pump waits for the JOINs to land, and the stale VIP backlog is really cleared (#630)

Audit M28. `irc.py` publishes `oserve.irc_connection` straight after `connect()`, before NICK/USER go out and
seconds before the JOINs land, and `queue_mgr.queue_worker()`'s only gate was "is there a socket" - so whatever
the previous connection left queued (a "Sent:" notice, queue positions, a rejoin) drained into a window the server
answers with 451 and 404, and the lines vanished with no log line. The disconnect epilogue meant to drop stale
adverts emptied `send_queue["channel_announce"]`, a key `oserve.queue_message()` never writes (adverts go to
`vip_queue`), so it cleared nothing.

The pump now has the same second gate as the debug drain: it holds until `config.activation_triggered` - set once
every target channel has answered its JOIN or the watchdog gave up waiting, cleared by the epilogue. Deliberately
NOT `bot_joined_channel`: that stays False on a connection that never got into a channel, and the rejoin JOIN goes
through this very pump. The epilogue now empties `vip_queue` (and says how many lines it dropped), the same
decision the pump already takes on a failed send. `tests/test_the_pump_waits_for_the_joins_to_land.py`; the
three existing pump tests set the flag in their setUp.

### 👢 A kick of another user is a departure, and the rejoin's NAMES rebuilds the member list (#629)

Audit M27. The KICK handler only acted on a kick of the bot itself; anyone else kicked stayed in
`config.channel_users`, which `dcc.user_is_present_in_ram()` reads as proof of presence, and since the bot then
shared no channel with them their QUIT was invisible too - a kicked user who disconnected kept being dispatched
to, each attempt holding a slot for the accept timeout, the queue never frozen or reaped. The bot's own kick left
the list alone as well, and the rejoin's 353 only merged into it, so members who left while the bot was out stayed
"present" for the life of the connection.

`irc.note_user_kicked()` now removes the victim exactly as a PART does (and records the departure for #376);
`note_kicked_from()` marks the member list stale and `learn_channel_names()` - which the 353 handler now goes
through - replaces the list on the first NAMES line after the kick and merges the rest as before, so a large
channel's multi-line NAMES still adds up. A channel the bot is not going back to has its list dropped
(`forget_channel_members()`). The list of a channel the bot WILL rejoin is deliberately kept until then: dropping
it would start the five-minute freeze timer on every queue there while the rejoin waits for the next advert.
`tests/test_a_kick_of_another_user_is_a_departure.py`.

### 🩹 A damaged list_index.db is moved aside and rebuilt, and the log no longer promises a fetch will do it (#628)

`list_index._connect()` failed on a corrupt or non-database file ("file is not a database", "database disk image is
malformed"), printed that the cross-list filter was "off until the next fetch", and returned None. But every caller -
`index_bot_list()` from a fetch completing, `backfill_missing()` at startup, `search()` and `bots_with_a_match()` from
the filter bar - opens the file through that same function and failed the same way, so no fetch ever repaired it: the
List Browser filter answered nothing for the rest of the install's life, the line was printed twice per keystroke, and
the only recovery (deleting the file by hand) was documented in INSTALL.md and nowhere the operator would look. The
index is a cache of lists still on disk, so `_connect()` now treats a bare `sqlite3.DatabaseError` (the class sqlite3
raises only for SQLITE_NOTADB and SQLITE_CORRUPT - a locked file, a full disk or a build without FTS5 are
`OperationalError`, a subclass, and are still left alone) as damage: the file is renamed to
`<file>.corrupt-<timestamp>` (kept, never deleted), any `-wal`/`-shm` sidecar sqlite3 did not remove goes with it, a
fresh index is created in its place, and a `_rebuild_pending` flag is set. The two dashboard readers call
`_prepare_to_read()` first, which opens the index and, when the flag is set, runs `backfill_missing()` over
`config.fetched_bot_lists` before answering - so the first filter query after the repair answers from the rebuilt
index rather than reporting every held list as empty. The log line names the file it moved the index to; when the
rename fails it says the file has to be deleted by hand, and the environmental "Unavailable" line says "off until it
can be opened" rather than "until the next fetch". The open-and-create-schema block moved into `_open()`, which still
closes the lazy handle before raising - on Windows the rename would fail otherwise. `backfill_missing()`'s summary
line is now "Indexed N held list(s) the search index did not have", since it no longer runs only at upgrade.
`tests/test_a_damaged_list_index_is_moved_aside_and_rebuilt.py` drives a damaged file through a fetch, the two
readers, a held list rebuilt on the first query, the once-only rebuild, a locked/unwritable index left in place, and
a rename that fails; two cases in `tests/test_crosslist_search.py` that relied on a corrupt file being "no index" now
use an unwritable path.

### 💾 A stats.txt that cannot be read for a moment is no longer overwritten with zeros (#626)

`db._load_advanced_stats_unlocked()` caught any error from `open()`/`read()` and returned the all-zero row. Harmless
for a display, fatal for a writer: `update_stats_on_complete()` incremented the zeros and `_atomic_write` replaced
stats.txt with them, and unlike the malformed-column path no `.corrupt` copy was kept. One share-deny lock from an AV,
backup or indexer at the instant a transfer completed on Windows - the same class of interference
`replace_with_retry()` exists for - or an EIO or a network-share hiccup, and the lifetime file and byte totals, which
nothing recomputes, were gone; the advert and the Stats page then showed one file. The loader now lets the read error
propagate: `update_stats_on_complete()` and `check_and_rotate_day()` raise before writing anything (their callers
in dcc.py and irc.py already catch, log a `[DB ERROR]`/`[DB ROTATE ERROR]` line and carry on - the one transfer goes
uncounted, the totals survive), and the read-only entry points `load_advanced_stats()` and
`load_advanced_stats_rolled()` catch it through `_load_for_display_unlocked()` and keep showing zeros for that one
refresh, logged after the lock is released. Nothing is preserved as `.corrupt` on this path: the file was fine, it
just could not be opened right then, and renaming it away would leave the writers starting from zero too.
`tests/test_an_unreadable_stats_file_is_never_overwritten_with_zeros.py` refuses `open()` once for the real file and
checks the writers leave it byte-for-byte alone, that the next completion counts on top of the real totals, that the
midnight rotation does not write either, and that the readers still answer with a row.

### 🔁 Turning AUTO_REFETCH_LISTS on live starts the refresh worker (#625)

`list_fetch.auto_refetch_worker` was started in one place, `oserve.startup()`, and only when the setting was
already on at boot. A dashboard save that ticked it on wrote settings.conf and fired a rehash, the rehash body
never looked at the setting, and `AUTO_REFETCH_LISTS` was not in `webserver.SETTINGS_RESTART_ONLY` either - so
the operator got a green save, "rehash started", no restart notice, and no worker. Held lists went stale until
the next restart; the only live effect was the one-shot `refetch_due_lists()` sweep irc.py runs on a reconnect.
oserve.py's own comment said "the setting says so" about a restart the help never mentioned.

New `list_fetch.ensure_auto_refetch_worker()` starts the hourly loop if the setting is on and it is not already
running, and returns whether this call started it. `oserve.startup()` and the rehash body (after the reload, so
it reads the saved value) both call it. The "already running" state - `runtime.auto_refetch_guard` and
`runtime.auto_refetch_started` - lives in runtime.py, so a rehash cannot reset it and start one more worker per
Settings save. Turning the setting off needs no stop: `refetch_due_lists()` reads the flag on every pass and the
worker idles. Nothing is added to `SETTINGS_RESTART_ONLY` and the help text is unchanged, because both now tell
the truth. `tests/test_turning_auto_refetch_on_live_starts_the_worker.py` drives the real rehash body with the
reload, the transfer wait and the debug line stubbed and the thread starter injected: one start over two
rehashes, none with the setting off, no thread outliving the test.

### 📍 The options dialog has room to spare (#782)

#767 made every label fit by a Tahoma 8pt / 96 DPI character table; the operator's screenshot showed the real dialog
font rendering about 10 % wider, so a label with 10.8 % to spare touched its neighbour. Every label now has at least
20 % (worst 21.6 %), the left column is 170 dbu (gap 12 to the right column, was 4), three labels are shorter, and the
edits beside a widened label move with it; control ids unchanged.
`tests/test_the_options_dialog_labels_have_room_to_spare.py` requires the margin and the gap for every label.

### 🧾 A new admin_config.py carries only the password, and a line settings.conf overrides is reported at boot (#623)

`configure.write_admin_config_password()` seeded a missing admin_config.py from admin_config.py.sample, whose active
lines - `WEBUI_ENABLED = True`, `WEBUI_HOST`, `WEBUI_PORT`, `ADMIN_CHAT_MODE = "listen"`, the two `DEBUG_TO_*` flags -
then sat in the operator's own file from birth. Where the setup had just written the same name to settings.conf
(`WEBUI_ENABLED`, `WEBUI_HOST`) they were dead, since defaults.py applies settings.conf second; where it had not,
they silently diverged from defaults.py (every install ran `ADMIN_CHAT_MODE = "listen"` under a comment naming
"auto" as the default; an install that declined the dashboard carried `WEBUI_ENABLED = True`). And the only shadow
check, `settings_file.shadowed_by_admin_config()`, ran at dashboard-save time, so an operator who later followed the
file's own comment, set `WEBUI_HOST = "0.0.0.0"` there and restarted, got a dashboard that ignored the edit and a
console that said nothing. A new admin_config.py is now `configure.NEW_ADMIN_CONFIG_HEADER` (a comment saying where
the other settings went) plus the `ADMIN_PASSWORD_HASH` line; the `sample_path` parameter is gone with the seeding.
`settings_file.apply_to()` reports, in `report["shadowed"]` and as a `[CONFIG]` line, every name it applied that the
`admin_config` module this process imported had also set to a different value - the same value in both is silent.
The sample is unchanged: it is documentation for a hand setup, not a template any more.
`tests/test_a_shadowed_admin_config_line_is_reported_at_boot.py` drives both.

### 🌐 The setup page is translated whole, not only its field labels (#621)

The FR/ES switch on `/setup` translated the six field labels and their **?** help - the `settings.field.*` keys the
Settings page already had - and nothing else. `render_setup_page()` and `render_setup_saved_page()` looked up
`setup.title`, `setup.intro`, the password labels, the LAN box, the folder placeholder, the button, the note and the
Saved page under `setup.*` keys that no lang file defined, the channel placeholder was a bare literal, and
`validate_setup_form()` had no language at all, so a French operator got a mixed-language form and "A nickname is
needed." under "Pseudo". The 25 `setup.*` keys now exist in `web/lang/{en,fr,es}.json`, `validate_setup_form(form,
lang="en")` reads its messages from the lang file (`setup.error.*`, the nickname problem from
`settings_file.nick_problem()` staying English inside the translated sentence), and the route passes the language on,
including to the "Could not write the settings" error. The English literals in `webserver.py` stay as the fallbacks.
`tests/test_dashboard_translations_stay_complete.py` now counts the `"setup.*"` strings in `webserver.py` as
referenced, so the parity and no-orphan checks cover these keys too. Test: every English fallback is absent from the
fr/es page, every refusal's message is a `setup.error.*` value of the chosen language, the Saved page in both
versions, and a French POST through the Flask app comes back with French errors.

### 📍 The setup writes the password before settings.conf (#624)

`webserver.apply_setup()` and `configure.main()` wrote settings.conf first and admin_config.py second. The two writes
are not one transaction: once settings.conf carried NICKNAME, CHANNEL and ADMIN_NICK the REQUIRED gate was satisfied,
so a failed password write (admin_config.py held open by an editor or a scanner, a disk that filled between the two)
followed by a restart skipped the setup page, joined IRC and had `webserver.start()` refuse the dashboard for the
missing hash, with the browser form never offered again. Both now write admin_config.py first: a hash on disk without
settings.conf still trips the gate, the page comes back, and the next attempt replaces the line in place.
`tests/test_the_setup_writes_the_password_before_settings_conf.py` drives both paths with a refusing writer.

### 🔑 The console takes the password exactly as it was set (#622)

The setup page, `POST /api/settings/password` and the dashboard's login all hash and verify the password verbatim,
but `adminchat._check_password()` did `line.strip()` first - so a password with a leading or trailing space (a
password-manager entry that ends in one, or `secret ` typed and confirmed identically) opened the dashboard and never
the console: three refusals, a blocked address, nothing saying why. The console now verifies the line as it came
(the reader has already split off the newline); only an empty line is still not an attempt. A test drives
`_check_password` with `" swordfish "` stored and asserts that both doors say the same about it and its stripped form.

### 🧪 preflight reads its children as UTF-8 (#620)

`scripts/preflight.py` captured the test-count run and the hostile-environment probe with `capture_output=True,
text=True` and no encoding, so it decoded with the locale code page, strict - cp1253 on the operator's Greek Windows -
while the children write UTF-8 (oserve.py's console guard reconfigures the test child; `hostile_env()` sets
`PYTHONUTF8=1` for the probe). The first byte cp1253 leaves undefined, 0x9f in any emoji a test prints, killed the
reader thread inside `subprocess.run`: a UnicodeDecodeError traceback landed in preflight's output looking like a test
failure, and the captured stream was None - on a red run whose failure text carried such a character, the count came
out as "only 0 collected". A new `capture()` helper runs both with `encoding="utf-8", errors="replace"`, the way
commands.py, dcc.py and update_list.py already did. The sweep in `test_a_filename_your_code_page_cannot_spell.py`
over parents that capture output now includes preflight, and `test_preflight_reads_its_children_in_utf8.py` drives
`capture()` for real from a Python forced onto a narrow locale (C/ASCII on POSIX, the ANSI code page on Windows),
probing rather than assuming that the locale could be narrowed.

### 🛑 The Linux and macOS autostart installers no longer start the bot at once (#619)

`install-autostart.sh` ran `systemctl --user enable --now` and `install-autostart.command` ran `launchctl load -w`
on a plist with RunAtLoad, so both started the launcher at install time. INSTALL.md tells the operator to run the bot
by hand first, and nothing - oserve.py, the launcher, the setup check (which only fails when zero DCC ports bind, and
an idle bot binds none) - refuses a second instance: run while the hand-run bot was still up, the installer started a
twin on ALT_NICKNAME, in the same channels, writing the same data/ files, with the dashboard bind failure logged and
ignored. Both installers now do what the Windows one already did: register the start (`systemctl --user enable`;
the plist written and `launchctl enable gui/<uid>/com.dccore.bot` - `unload -w`, which the remover and the re-run
path use, marks the label disabled and `enable` clears that without loading it) and print how to start it now once
the hand-run bot is stopped (`systemctl --user start dccore`; `launchctl load -w <plist>`). A failing `launchctl
enable` (pre-10.10) is reported, not fatal: the plist is written either way. The `_Posix` tests run both scripts
with a recording fake and assert that no call starts anything. Not verified on a real systemd or launchd host.

### 🐧 The Linux autostart unit survives a folder name with a space, % or $ (#618)

`scripts/linux/install-autostart.sh` wrote `ExecStart=$ROOT/scripts/linux/start-dccore.sh` and `WorkingDirectory=$ROOT`
raw. systemd word-splits ExecStart=, so `~/My Files/dccore` became the executable `/home/me/My`; a `%` in the path
is a specifier (the unit fails to load) and `$VAR` is substituted from the environment. Because the unit is
`Restart=on-failure`, `enable --now` succeeded and the script said "Done" while the service looped on 203/EXEC every
ten seconds. ExecStart= is now the double-quoted form with `\`, `"`, `%` and `$` escaped (`\\`, `\"`, `%%`, `$$`);
WorkingDirectory= (not word-split, not $-expanded) gets `%%`. The macOS and Windows twins already escaped theirs.
The test moves the tree under `My Files %h $HOME` (plus `"q" \b` where the file system allows) and decodes the unit
by systemd's own rules - specifiers, quote removal, variable substitution - instead of looking for `%%`.

### 📍 A first run whose setup page cannot bind falls back to the terminal questions (#617)

With Flask importable `configure.py --setup-in-browser` answers 0 without asking, so the launchers started
`oserve.py` to serve the setup page; when `run_setup_until_configured()` could not bind WEBUI_PORT (another DCCore
in a minimised window, another program) it returned None and `startup()` fell to the refusal that says "copy the
sample files", exit 1 - and every later run took the identical road, with the terminal questions unreachable from
the launcher. The bind-failure line printed werkzeug's SystemExit as "(1)" and named no cause and no way out.
Now: `run_setup_until_configured()` says "the port is taken", asks about another DCCore, and names `WEBUI_PORT`
and `configure.py`; `startup()` exits `oserve.EXIT_SETUP_IN_THE_TERMINAL` (3) when the page was tried and did not
configure anything (the refusal without a page keeps its 1 and its sample advice); `start-dccore.bat` and
`start-dccore.sh` (which the .command execs) map a 3 on the browser path to the terminal questions, then the
setup check, the Flask offer and the start, exactly as the terminal path does. A 3 from a configured tree is
reported as before. Tests execute both launchers with stub scripts (the fallback, the check still running on it,
questions that do not finish, and a configured tree that exits 3), and drive `startup()` and a held loopback port.

### 📍 The options dialog's labels fit their controls (#616)

`dialog dccore.opt` uses `option dbu`: a horizontal unit is a quarter of the dialog font's average character width
(Tahoma 8pt, 1.5px at 96 DPI), and a check control spends ~17px on its box. A 118 dbu check therefore holds ~160px of
label, and "Side panel with slots, queue and today's totals" needs 223 - five checks and three small texts were cut off
("Side panel with slots, queue and"). The dialog is now 322 dbu wide (was 262): the left checks are 178 wide, the
whole right column moved 60 to the right, the small texts got the room they need, 403/404/405 the full width. Two
labels that still could not fit beside their edit box or the box edge were shortened: "Fixed-width font (Lucida
Console)" is "Fixed-width font, size" and "min (0 = never)" is "min, 0 = never".
`tests/test_the_options_dialog_labels_fit_their_controls.py` measures every label against its control from the source,
with a Tahoma width table on every platform and the real GDI font on Windows, and checks nothing runs out of its box.
Not verified in mIRC itself.

### 📍 A chat the bot never answers is closed and retried (#615)

`dccore.connect` issued `dcc chat` and waited for either a 401 or a CHATCLOSE; a bot that gets the offer and says
nothing (the address is blocked after three wrong passwords, the host is not in `ADMIN_HOSTMASKS`, the offer could
not be parsed, or its listen-back offer is firewalled) produces neither, and mIRC never times out its own outgoing
chat, so the =bot window sat at "Waiting for acknowledgement..." for ever, `$chat()` stayed true, and every later
connect - the on CONNECT and on JOIN ones included - answered "already open". `dccore.connect` now starts a one-shot
`.timerdccoreOpen` (75 s) whose alias `dccore.noanswer`, if the state is still `opening`, says so, closes the
window and goes through `dccore.retry` (guarded against CHATCLOSE retrying first). The first CHAT line, CHATCLOSE
and `dccore.timers.off` cancel it. Source-reading test; not verified in mIRC.

### 📍 The heartbeat no longer waits on queue_lock (#614)

The STATUS burst is the structured session's only heartbeat, and the writer computed it inline: `status_lines()` calls
`stats_mgr.live_speed()`, which takes `dcc.queue_lock`, and reads the stats DB. A lock held past the script's 90 s
(a slow `db.save_dcc_queue` under the lock, a locked DB) parked the writer with nothing at all going out - feed, LOG
or STATUS - so `dccore.mrc` called the link dead, reconnected, and the new session's writer parked at the same point:
a login line every ~100 s while the bot and its IRC loop were fine. `Session.send_status()` now computes the burst on
a helper thread and waits `STATUS_WAIT` (2 s) for it; past that it sends `DCCORE PING` and goes on draining the
outbox. The helper is left to finish, no second one is started while it runs, and its lines go out on the pass that
finds it done in time. `hello`'s first burst, sent on the reader thread, gets the same bound. `dccore.mrc` swallows
`PING` (the on CHAT handler resets the timer on any line already; an unknown type would have been echoed).
ADMIN-CONSOLE.md documents the line. Tests hold `dcc.queue_lock` around a session over a socketpair.

### 🔒 A refused token is not sent again, and the redial waits for the operator (#613)

On `Incorrect Password.` to the stored token `dccore.mrc` only changed its message; the bot closed the unanswered
session after 60 s, CHATCLOSE redialled in 5 s (the first line of every session resets the backoff), `dccore.connect`
reset `tokentried` and the revoked token went out again. `note_bad_ip` counts per address across sessions, so the
third session - about two minutes after the first, with the operator away - blocked the address for 15 minutes and
every login was refused with no word why. The script now sets a live `tokenbad` on that refusal: the prompt branch
does not send the token while it is set, `dccore.retry` returns before arming its timer (and says so), and the mark is
cleared by a login (which says the stored token is still the refused one), a new TOKEN, or `unpair`. `/dccore connect`
does not clear it: a dial the operator asks for prompts for the password rather than spending another attempt. The
INPUT handler now records `typed 1` (reset by `dccore.connect`) so a mistyped hand password, which also sits in state
`auth`, is not taken for the token - before, it drew the "refused the stored token" message. Not verified in mIRC;
`tests/test_a_refused_token_is_not_sent_again.py` reads the script and pins each branch.

### 📍 The queue position is the serving order, not the alphabet (#612)

`adminchat.status_lines()` numbered the `DCCORE QUEUE <pos>` rows from `sorted(queue, key=str.lower)` while
`dcc.check_queue_and_send()` walks `config.dcc_queue` in insertion order (first request first, kept across a
save/load), so the mIRC panel showed an alphabetical rank labelled as a position and, with more than 20 waiting,
the user actually next in line could fall off the burst. The rows now follow the queue's own order, as does the
console's `queue` listing, whose header says "in serving order". Tests use nicks that sort the other way round from
their arrival (the old ones used helen/Ivan, the same in both orders).

### 🔐 A foreign page cannot lock the operator out of the login (#609)

`POST /login` is the one route outside the session gate and its failed-attempt pool is keyed on `request.remote_addr`;
on the stock loopback install the operator's browser and any hostile page open in it both arrive as 127.0.0.1, so
three cross-site `fetch(..., {mode: 'no-cors'})` POSTs with a wrong password blocked the operator's own login for
`BAD_IP_BLOCK_SECONDS`, repeatable for ever. `_login_origin_ok(origin, referer, host)` now compares the `Origin`
header (a sandboxed frame's `null` counts as foreign), or `Referer` when there is no `Origin`, with the request's own
`Host` - lower-cased, default port stripped - the way `_setup_host_ok()` guards `/setup`; a mismatch is answered 403
before the password is read and is never counted. A request with neither header (curl, the test client) passes: the
guard is against the lockout, not a second password. ADMIN-CONSOLE.md says a reverse proxy must pass `Host` through.
`tests/test_a_foreign_page_cannot_lock_the_operator_out_of_the_login.py` runs the audit's reproduction and the
controls (own origin, case and `:80`, no headers, the operator's own failures still block).

### 📍 An automatic dial asks no stranger for the password (#608)

#585 gated the stored token behind `dccore.peerok`, but with no token to send (a password-only install, after
`unpair`, or while pairing with the bot away) the same automatic dial - retry timer, JOIN of the bot's nick, IRC
connect - still put "Type the admin password here" in @DCCore for whoever held the nick, and the typed password also
opens the dashboard. `dccore.connect byhand` (from `/dccore connect` and `pair`) sets `byhand` in `dccore.live`; the
timers dial without it. On `Enter Your Password:` with no token, a chat not opened by hand runs
`dccore.peerok password` first (same branches: known host must match, unknown learned once, refusal closes the chat
and stops the redial); a by-hand chat asks as before. The check is nested, not `&&`-joined: mIRC evaluates every
identifier on an if-line and peerok has side effects. `%what` in peerok names what is withheld.

### 📍 Two @find lines arriving together no longer both walk the list (#607)

`execute_search()` runs on a thread per @find, and its "one search at a time" guard read `config.search_inprogress`
at the top of the function and set it some twenty lines later, with `library.list_name_for_request()`'s trip to
lists.json in between and no lock. Two @find lines dispatched from one recv() buffer both passed, both scanned the
master list at once, and the first to finish cleared the flag while the other still ran, admitting a third - and an
`!update` arriving then passed its "no scan running" check with a scan in progress. The check and the set are now
one step under `runtime.list_update_gate`, the lock `!update` already takes for `update_inprogress` (#444); the
refused searcher returns before the `try`, so its `finally` cannot release somebody else's flag.
`handle_list_update_request()` checks and raises `search_inprogress` inside that same gate, and its `finally`
clears it only when this request raised it (with PAUSE_ON_UPDATE off the flag belonged to a running search). The
test forces the interleaving with a barrier inside the old window instead of betting on the scheduler.
### 🧪 The resume-reply test joins its helper before the next test

`test_the_reply_carries_the_latest_position` (#725) failed with `2 != 1`
on three CI runs of unrelated branches (#778). Each test in that module
parks its ACCEPT helper in a stubbed pacer until the test's cleanup
releases it, and every test registers the same `(USER, PORT)` offer in the
module-global `runtime.dcc_send_offers` - so on a slow runner the previous
test's helper woke up during the next one, popped `accept_pending` off the
new offer, and the third RESUME started a second helper. The harness now
counts helpers inside the stub and, at cleanup, releases them and waits
for the count to reach zero before the patch is removed. Test-only.

### 📍 The queue save no longer pops keys out of the live queue (#606)

`db.save_dcc_queue()` dropped every emptied user key from `config.dcc_queue` itself, and two of its callers
(`release_queue_entry` on every completion, the poisoned-entry branch of `check_queue_and_send`) run after their
`with queue_lock:` block has closed. That pop raced the lock-held live walks of `config.dcc_queue.items()` in
`next_waiting_pack_owner()` and in start_dcc_send's temp-archive cleanup and raised "dictionary changed size during
iteration" in their thread - and in the finally the `redispatch_waiting_pack()` call was the one unguarded step, so the
error skipped `user_processing_lock.discard()` and the fallback trigger: the user whose pack had just finished stayed
"already claimed elsewhere" until a rehash. The save now only reads the dict (the file never held empty keys either
way); an emptied queue leaves with its key inside `release_queue_entry`'s own lock block (and in the poisoned-entry
branch); the wake in the finally is wrapped like every other step there. db.py's "five of the six callers" comment was
wrong and is gone. Tests: a save during a live walk, the key pruned under the lock, an AST check of the guard, and a
loopback pack send whose wake raises.

### 🔒 The dispatch notice and the queue save run outside queue_lock (#605)

Section B of `check_queue_and_send` and both branches of `handle_download_request` called
`announce.send_dcc_sending_notice()` - which since #550 does an `os.path.getsize()` on the library path for the
console feed - and `db.save_dcc_queue()` (an fsync) from inside `with queue_lock:`. `FILE_DIRECTORY` may be an NFS
mount, and a stat on a hung one blocks forever: `queue_worker` samples `live_speed()` under the same lock once a
second, so every outbound line stopped, and the IRC read thread takes it on every NICK, so the PONGs stopped and the
server dropped the bot. The claim (`user_processing_lock.add` and the `active_transfers` append) is what needs the
lock; the notice, the thread spawn and the save now run after it is released, the way section A's plain-file branch
always has. A test drives all three paths and records whether the lock was held at the notice, the save and the
thread start. The freeze sweep's `save_dcc_queue()` (once per expired timer) still runs under the lock.
### 🔒 A rehash no longer rewrites the live runtime containers with no lock held (#604)

`commands.restore_preserved_runtime()` now skips a key whose preserved value *is* the live container (`value is
current`), which is every name in `PRESERVE_RUNTIME` since they are all runtime.py-bound and a reload never empties
them. Until now it merged anyway, which came out as `current.clear(); current.update(copy)` on the live dict and
`current[:] = copy` on the live list - on the rehash thread, outside queue_lock, the fetch lock and the offers lock
that every other writer holds. Three races, all microseconds wide: a transfer that completed and removed itself under
queue_lock between the copy and the write-back was put back as a phantom DCC slot; a dict was momentarily empty
between `clear()` and `update()`, which `count_active_fetches()` / `check_user_status()` could read as "nothing
active" or "nobody banned"; and `clear()` under the IRC read thread's iteration of `banned_users` / `muted_until` /
`user_requests` raised RuntimeError outside the per-command try, which closes the socket. The key still counts as
restored, so the `[REHASH RAM]` line is unchanged. The merge path is kept for a future key that is not
runtime.py-bound; it is the only path that writes, and it still runs with no lock. Tests: instrumented dict and list
containers must see zero writes when they are their own snapshot, and a fresh container must still be filled.
### 📍 The panel's Since box is the bot's start (#754)

`runtime.feed_counts` counts FAIL and SEARCH in `announce.feed_event` (runtime, so a rehash does not reset it; counted
before the console tickboxes can refuse a line). `adminchat.status_lines()` appends `<started_epoch> <failed>
<searches>` to the STATUS line (now minus the uptime; a minor, additive change: an older script reads $1-$8).
`dccore.status` stores them as `st.started`, `st.failed`, `st.searches` when present and the panel draws Since (with
the weekday when more than 20 hours ago), failed and searches from them, falling back to `opened` and its own
counters. ADMIN-CONSOLE.md documents the fields.

### 🧭 The setup page starts with the dashboard box ticked (#603)

`build_setup_fields()` read `WEBUI_ENABLED` straight from config, and defaults.py ships it `False` (convention 1),
so on every first run the "Enable web dashboard" box rendered unticked - while the intro, the folder placeholder and
the folder error all told the operator to choose the music folder "later on the dashboard's Settings page", and
INSTALL.md described the box as "left on". A novice who took the page's own advice and did not notice the box ended
with a bot that connected, served nothing and had no Settings page to fix it from; nothing on screen named
`settings.conf`. A fresh page (no values typed yet) now ticks the box - the page only exists because Flask is
installed, the launcher having just installed it for the dashboard, and the box still binds loopback unless the LAN
box is ticked too - and a redisplay after an error keeps what the operator chose (an unticked box is absent from the
POST, so config's `False` wins there). `validate_setup_form()` computes the dashboard choice first and, when the box
is off, the folder error says to set `FILE_DIRECTORY` in `settings.conf` or tick the box, instead of promising a
Settings page that will not exist. The shipped default is untouched. INSTALL.md says so.
Tests: `tests/test_the_setup_page_starts_with_the_dashboard_ticked.py`.

### 📍 The panel's Since box is the bot's start (#754)
### 📍 Every command is in the @DCCore menu (#550)

`menu @DCCore` now has submenus for every `/dccore` command and every console command that is worth a click. Prompts
are `$input` (mIRC 6.0+): `dccore.ask <command> <prompt>` (edit box, Cancel or empty sends nothing), `dccore.askraw`,
`dccore.confirm <command> <prompt>` (yes or no; used for `update` and `rehash`), `dccore.askfont`. Prompt texts
contain no commas. `ban`, `unban` and `clearqueue` are never sent without their argument. A test checks that every
command the script implements and every non-plumbing console command has a menu entry.

### 📍 The LISTFETCH line carries its text (#750)

`Session.event_sink(kind, fields, text)` passed only `fields` to `structured_line()`; LISTFETCH's payload is the
sentence, which travels as `text`, so the line ended after the action. The sink now passes `{'text': text, **fields}`;
the other kinds never read it and a field named text still wins. The tests that shipped with LISTFETCH built the
fields by hand with a text in them and never saw it: the new ones go through `announce.feed_event` and the session's
outbox.

### 📍 Lists and fetch in the console, LISTFETCH in the feed (#750)

`adminchat`: `lists` (from `webserver.build_fetched_bot_list_summaries()`, one line per bot: freshness, count, age,
online) and `fetch [bot]` (the changed ones oldest first, offline skipped, `FETCH_COMMAND_MAX` = 10, through
`build_list_fetch_enqueue_result()` - the dashboard's enqueue, so the slot limits, duplicate guard and queue ceiling
apply). New feed kind `LISTFETCH` (`DCCORE LISTFETCH <bot> <action> <text>`, actions auto / arrived / unusable), a
debug-channel category only with DEBUG_CHANNEL_FEED, tag [LISTS]. `list_fetch.refetch_due_lists` and
`process_fetched_list_zip` emit it through `_tell_the_console` (never raises, outside the fetch lock). `dccore.mrc`:
`/dccore lists`, `/dccore fetch`, and the LISTFETCH line. ADMIN-CONSOLE.md documents the commands and the line.

### 📍 A resumed send's SLOT speed counts what it sent (#746)

`adminchat.status_lines()` built the DCCORE SLOT speed as `bytes_sent / (now - started_at)`; a resumed send starts
with `bytes_sent` at the resume offset. `dcc.start_dcc_send` now also stores `resume_offset` on the row and the speed
is `(bytes_sent - resume_offset) / elapsed`, never negative. Progress (sent / total) is unchanged. Rows without an
offset behave exactly as before.

### 📍 The IRC ident and real name follow the nickname (#744)

`irc.py` sent `USER <getattr(config,'IDENT','dccore')> 0 * :<getattr(config,'REALNAME','dccore bot')>`; neither
setting existed, so every bot was dccore@host. `irc.registration_names()` now returns the ident and real name from the
configured nickname (`ORIGINAL_NICK`, not the temporary alternate) at each connection. `irc.ident_for_nick()`: lower
case (`scripts/capture_adverts.py` documents that Undernet answers an upper-case username with 468 and closes the
link), ASCII letters and digits only, cut to `IDENT_MAX_LENGTH` = 10, `dccore` if nothing is left. The real name is
the nickname unchanged. The old `IDENT`/`REALNAME` getattr side door is gone.

### 📍 The nick and 'in' have a space between them (#550)

`dccore.in` returned `$+($chr(32),in,$chr(32),$1)`; mIRC drops a leading space from an alias's return value, so `$2 $+
$dccore.in($3)` printed 'NICKin #chan'. The spaces are now `$chr(160)`, which it keeps and which the script already
uses for column padding.

### 📍 A fast big transfer reports its speed (#550)

`stats_mgr.speed_is_measurable(duration, size=None)` needed one second, because a file inside the 4 MB send buffer is
'sent' in one go and the clock measures memory. Since #526 the clock stops at the receiver's final acknowledgement,
and a file past the buffer cannot be a memory copy: `LARGE_TRANSFER_BYTES` (8 MB, twice the buffer) and
`MIN_LARGE_TRANSFER_SECONDS` (0.1 s) now let it through. `dcc.py` passes `file_size`. The speed RECORD keeps its own
one-second floor. `dccore.mrc` prints 'at n/a' for a SENT line whose speed is 0.

### 📍 The queue menu reads the panel row without a regex (#584, #550)

`dccore.sels`/`dccore.selq` took the nine-character nick field with `$regex(... /^>[ \xA0]+(.{9})/)`; on a real mIRC
the pattern matched (`$regex` returned 1) but `$regml` gave an empty group, so no menu item appeared. Both now use
`$sline`, `$mid`, `$remove(...,$chr(160))` and `$gettok`, which the operator's diagnostic showed to behave (row = 34
characters, `>`, space, then the nick). The tests run the same steps in Python on rows built like the panel builds
them, including a two-digit row number, a 13-character nick and every heading row.

### 📍 The mIRC window's background is a 128x128 tile (#550)

The background colour (#573) was a one-pixel `.bmp` tiled with `/background -t`. Tiled, that is one draw per pixel of
the window on every repaint, and the window froze on every new line and every options change. The picture is now
128x128 (`dccore-bg-<n>-128.bmp`, 48 KB, written the first time a colour is used), and the options save only calls
`dccore.background` when the colour was changed. The one-pixel files are no longer used.

### 📍 The token goes only to the bot that was paired (#585, #550)

`dccore.mrc` answered the first `Enter Your Password:` with the stored token, whoever held the bot's nick.
`dccore.peerok` compares `$address(<bot>,2)` (*!*@host) with `bothost`, stored when the token arrives: same host,
send; different, refuse and `dccore.abandon` (no automatic redial); none stored and the host known (a script paired
before this), learn it once (trust on first use) and send; none stored and unknown, wait for `/dccore trust`. `unpair`
forgets it. The bot's ADMIN_HOSTMASKS gate still bounds what a stolen token is worth.

### 📍 A taken-over console hears it, and does not take it back (#583, #597, #600)

`_promote()` queued the takeover notice with `send()` and closed the socket on the next statement, so the writer
thread never sent it (0/40 over loopback); in structured mode it would have been wrapped as `DCCORE OUT` and never
reached the script's `taken` check. It is now written inline through `close(announce_text=...)`: prose for a plain
session, and for a structured one a line of its own, `DCCORE TAKEN <ip>` (documented in the protocol table; a script
that does not know the type ignores it), which `dccore.mrc` turns into state `taken`, the state in which CHATCLOSE
does not retry.

### 📍 The window keeps its history and the queue menu names the nick (#582, #584, #550)

`dccore.rebuild` closed @DCCore and then read the lines of the new, empty window: the history was lost, the first
empty `/echo` halted the alias and `rebuilding` stayed 1, so the CLOSE handler ignored every close. The text is now
copied into a hash table first (an empty line as a non-breaking space), `rebuilding` is `$ticks` and CLOSE ignores it
after 5 s. `dccore.selq`/`dccore.sels` no longer read the nick off the panel text (cut or padded to nine characters):
a queue row's number is looked up in `queue.N` of the status, a sending row's nine characters are matched against
`slot.N`.

### 📍 Setup falls back to the terminal where a browser is out of reach (#595)

`configure.offer_setup_in_browser()` returned 0 whenever Flask imported, so the launcher served `/setup` on 127.0.0.1
and `run_setup_until_configured()` looped for ever (`webbrowser.open`'s False ignored, Ctrl-C a traceback, nothing
naming configure.py). `configure.over_ssh()` (SSH_CONNECTION / SSH_TTY / SSH_CLIENT) now makes it return 2 with an
explanation, unless `DCCORE_SETUP_IN_BROWSER=1` says a tunnel is up. `run_setup_until_configured()` logs the ssh -L
hint when the opener returned falsy, always names `python3 configure.py`, and catches KeyboardInterrupt (logs and
returns None, port freed). `docs/INSTALL.md` says so.

### 📍 The server's shortened nick is adopted (#594)

The 001-target adoption sat in the pre-USER loop, which breaks on the first NOTICE/PING and sends USER; 001 only comes
after USER, so it never ran and `config.NICKNAME` kept the long name (every `target_chan.lower() ==
config.NICKNAME.lower()` test failed for PMs to the real nick). The logic is `irc.adopt_registered_nick()`, called
from the main loop's 001 handling before `joined = True`; the unreachable copy is removed and a comment says why. The
005 explanation ('so it was shortened to X') now prints the adopted name.

### 📍 Nicknames are validated against what IRC allows (#591)

`settings_file.nick_problem()` / `nicks_problem()` (ASCII; a letter or one of [ ] \\ ` _ ^ { | } first, then those
plus digits and '-'; ADMIN_NICK is a comma-separated list) are applied in `coerce()` for NICKNAME, ALT_NICKNAME and
ADMIN_NICK, which every path into config goes through (settings.conf, the Settings page, `apply_settings_changes`); a
bad value is a 'bad' setting with the reason. `validate_setup_form()` uses the same rule instead of its own, and
`configure._ask(check=...)` asks again. Blank is unchanged. Length is not checked (NICKLEN is the server's, #594).

### 📍 The browser setup re-derives the list name (#590)

`webserver.apply_setup()` applies settings.conf through `settings_file.apply_to()`, which assigns NICKNAME but never
re-runs the derivation in `defaults.py`; `LIST_BASE_NAME` stayed 'DCCore' in the daemon while `update_list.py` (a new
process) derived it from the nick. The derivation is now `defaults.derive_list_base_name()`, still called once at
import, and `apply_setup()` calls it after `apply_to()`. A LIST_BASE_NAME the operator set is left alone.

### 📍 macOS autostart gets a PATH; the firewall script removes the Block rule Cancel made (#588, #589)

The launchd agent inherited `/usr/bin:/bin:/usr/sbin:/sbin`, so `start-dccore.sh` found only Apple's stub and
KeepAlive restarted it every ~10 s while the installer had said 'Done: DCCore is running now'. `install-
autostart.command` now writes `EnvironmentVariables/PATH` = the installer shell's PATH, then Homebrew / python.org
locations, then launchd's own (XML-escaped). `allow-firewall.bat` removed nothing but its own two rules, while Windows
evaluates Block before Allow and Cancel on the Security Alert creates an inbound Block rule for python.exe: it now
finds `sys.executable` of `%PY%`, and removes inbound Block rules whose `Program` is that path (PowerShell; a failure
is a warning). `docs/WINDOWS.md` says so.

### 📍 The Windows launchers run their Python, and autostart keeps running (#586, #587)

`where python` finds the App Execution Alias stub in WindowsApps, so `start-dccore.bat` and `allow-firewall.bat`
committed to it and never reached the install offer of #547. Each candidate (`py -3`, `python`) is now run once with
`call` (a shim such as pyenv-win's `python.bat` never returns otherwise) and silenced; only one that answers becomes
`%PY%`. `install-autostart.bat` follows `schtasks /create` with `New-ScheduledTaskSettingsSet` / `Set-ScheduledTask`:
no execution time limit, start and keep running on battery, priority 4, three restarts a minute apart; if PowerShell
cannot do it a warning says what to untick by hand and the task is still created.

### 📍 An unknown filename does not scan the library unbounded (#580)

The list-and-walk fallback in `handle_download_request()` is now behind `_library_scans` (a BoundedSemaphore of
`MAX_CONCURRENT_LIBRARY_SCANS` = 2, taken without blocking: the request that cannot get one is answered `busy` and
touches no disk) and a per-(list, lowercased name) miss memory (`LOOKUP_MISS_TTL_SECONDS` = 60, `LOOKUP_MISS_MEMORY` =
512 entries, oldest dropped). Only a real miss is remembered, never a busy refusal. The slot is released in a
`finally`. A file in the first folder's root never reaches either. The block itself is unchanged apart from its
indentation.

### 📍 A failed direct send tells the user (#599)

The direct-send fast path builds a synthetic row that is never in `dcc_queue`. `release_queue_entry()` classified it
as retryable and retained, so a failure sent no notice and logged 'kept for retry' for a retry nothing could perform.
A row that is in no queue (identity check under `queue_lock`, so a renamed key still counts as queued) is now settled
on its first failure, with a NOTICE that says 'Ask for it again' rather than 'Removed from your queue'.
`tests/test_queue_integrity.py::test_unknown_user_does_not_raise` asserted the old retained-orphan behaviour and now
asserts this.

### 📍 `pair` in the dashboard Console shows the token (#581)

`_cmd_pair` saved the token and then read `session.structured`, which the web shim (`_WebConsoleSession`) lacked, so
the Console said 'Command failed' and the token was never shown while any script paired under that name was silently
locked out. `hello` printed its DCCORE HELLO line and failed on `send_status`. The shim now has `structured = False`
and `client`, `hello` is in `_CONSOLE_UNSUPPORTED_COMMANDS` with a message, `pair` says when it replaced an existing
token, and a structural test fails if any supported handler reads a session attribute the shim lacks.

### 📍 A failed midnight rotation does not stop every command (#592)

`db.check_and_rotate_day()` is the first call in the per-message block and raises when the rollover cannot be
written; the exception unwound the whole block, so no command, admin command or download was dispatched until
the write worked. It still raises on purpose (no caller is handed un-rotated counters as current), so
`irc.rotate_the_day_without_stopping_the_bot()` catches it at that one call site, says so once a minute and
retries on that cadence (each failed attempt on Windows also costs the read thread a replace retry).

### 📍 The channel admin commands check the host (#579)

`commands.is_admin(user)` compared only the nick against `ADMIN_NICK`, while `irc.py` already parsed ident@host and
`adminchat.is_admin_host()` matched it for the console. `is_admin(user, host=None)` now also requires, when
`ADMIN_HOSTMASKS` is set, that `host` matches one of the patterns (the ident is not part of the proof; an all-wildcard
pattern admits no one; a caller that passes no host is refused). With `ADMIN_HOSTMASKS` empty it is the nick alone,
as before. The five handlers take `user_host=` and `irc.py` hands them the sender's, and the same gate covers
`!ping`/`!debugnames` (`diagnostics_are_for_the_admin`). The console passes `authorised=True` and is unchanged.
Help texts (en/fr/es), `defaults.py`, the sample and ADMIN-CONSOLE.md say so.

### 📍 A resume reply does not block the IRC read thread (#577, #602)

`handle_resume_request()` ran on the read thread and sent the ACCEPT through `outbound_pacer.wait_for_slot(MSG_DELAY)`:
up to MSG_DELAY per matching RESUME with no PING answered and no line parsed, and - since `is_flooding()` stamps a
request when it is handled - spaced out just enough that the sender was never muted. `background=True` (what
`irc.py` passes) keeps the lookup and the position on the read thread and sends the paced reply from `_send_resume_accept`
on a short thread: one at a time per offer (`accept_pending`), a RESUME that arrives meanwhile only moves the
position, and the slot is waited for before the offer is read, so the reply carries the latest. Without `background`
the behaviour is exactly as before.

### 📍 A UNC path is refused before it is touched (#578)

`os.path.join(base, r"\\host\share\x")` returns the UNC path unchanged, and `os.path.exists()` / `realpath()` ran
on it before the containment check: on Windows an SMB connection (NTLM, as the bot's account) to a host the
requester chose. `dcc.names_a_remote_or_absolute_path()` refuses, on the text and before any file system call:
UNC and device paths everywhere (either slash, `//host` included, checked BEFORE the leading-slash strip that
used to hide it), a drive or root-relative name on Windows, and a NUL byte. Applied to plain requests and, for
remote forms only (a heading is written `D:\\...`), to `!rar`. Tests record every `os.path` call the handler
makes and fail if any sees the remote name.

### 📍 A nick change mid-transfer keeps the slot and the queue (#598, #601)

`start_dcc_send()` found its `active_transfers` row and released its `user_processing_lock` entry by the nick the
send started as, while `irc.note_nick_change()` had already rewritten the row and moved the lock to the new nick.
After a `/nick` the row and the lock outlived the transfer: a slot lost until restart, the renamed user answered
"already transferring" for good, rehash waiting out `REHASH_TRANSFER_WAIT`, `bytes_sent` frozen. The send now
finds its row once (`_find_transfer_row`, by nick and file) and keeps it by identity; the lock is released under
the row's current nick.

`note_nick_change()` also never saved the queue it re-keyed, and left `user_raw` (the dispatcher's DCC target) as the
old nick. It now rewrites `user_raw` on the moved rows and calls `db.save_dcc_queue()`.

### 📍 The channel_users control test no longer bets on a race (#596)

`test_without_the_lock_the_same_workload_corrupts_state` churned an unlocked dict for three seconds and asserted
that the scheduler happened to interleave a writer inside an iteration. On a lightly loaded macOS runner it did not,
and main was reported red twice in one day for changes that touched nothing near `channel_users`. The control is now
deterministic: a reader holds an iteration open, a writer adds a channel key, the reader resumes - `RuntimeError`
every time unlocked, no error and the write lands afterwards when the lock is held.

### 📍 A rehash keeps the structured feed attached (#576)

`importlib.reload(announce)` resets `announce._event_sinks` to `[]` as well as `_debug_sinks`, but the rehash
snapshotted and reattached only the debug sinks. A structured console session (`dccore.mrc` after `hello`)
gets REQUEST/QUEUED/SENDING/SENT/FAIL/SEARCH/RESUMED and the event-driven STATUS burst through its event sink,
and drops those kinds from its debug sink, so after any rehash it heard neither - while LOG lines kept arriving
and made it look alive. `commands.reattach_event_sinks()` is the sibling of `reattach_debug_sinks()`; the
snapshot is taken under `_debug_sinks_lock` next to the debug one.

### 📍 The structured feed says which channel

From the operator's first real session with `dccore.mrc` (#550): the SEARCH
line said who searched and what, but not where; SENDING and SENT the same.

The structured lines carried no channel at all, so the script had nothing to
show. Every event line now has a `<channel>` token straight after the nick -
REQUEST, QUEUED, SENDING, RESUMED, SENT, FAIL and SEARCH - and it is always
exactly one token, `-` when the event has none (a request by private message,
a resume, a transfer that no longer knows where it was asked for), because it
sits among the fixed fields ahead of the free text and must not move what
follows. Anything that is not a channel name is `-`. Shown by the script as
`nick in #channel`, and nothing for `-`.

The protocol has not shipped in a release yet (it is for v1.13), so major 1
takes the extra field rather than a new number; the script and the bot have to
be updated together - an old script against a new bot would read the channel as
the next field.

Where the channel comes from: SEARCH from the channel the search was typed in
(`list.py`); REQUEST and QUEUED from the request's channel; SENDING from the
channel the send is started for, which is the point - a send picked up from the
queue later (three sites in `dcc.py`) hands on the same channel it passes to
`start_dcc_send`, so it still says where it was asked for; SENT from
`send_transfer_complete()`'s channel; FAIL through one wrapper inside
`start_dcc_send`, so all eight of its failure reports carry it. RESUMED has none
at hand and says `-`.

`tests/test_the_feed_says_which_channel.py` (10) and additions to
`test_a_structured_feed_for_the_admin_chat.py` and
`test_the_bots_window_in_mirc.py`: the token is one token and `-` for none,
every channel prefix counts and an odd spelling stays one token; each emitter
hands the channel on (read from a real event sink where the function can be
called, from the source where it cannot); the four SENDING notices and the two
QUEUED notices name a channel; `start_dcc_send` reports failures through one
wrapper. The script's `$N` positions are checked against the bot's own lines
for every kind, channel included. Verified by hand: dropping the channel from
one SENDING call site, and from SENT, each fail.

### 🔁 An automatic re-fetch remembers that it asked

Found while answering "how often are updated lists fetched": the log from the
night the bot froze showed one bot asked at 00:34, 01:34, 02:34 ... 06:34, in
spite of `AUTO_REFETCH_INTERVAL_HOURS = 24`.

`lists_worth_refetching()` measured that floor from `fetched_at` - the last
fetch that COMPLETED. A bot whose list never arrives (it is not answering, or
the daemon was offline or wedged when it did) keeps its old `fetched_at` for
ever, so it stayed permanently "stale enough": every hourly sweep asked it
again, and so did every restart, since the sweep after a restart runs at once.

The sweep now writes `last_attempt` on the entry when it actually asks (a 200
from the same enqueue the dashboard uses; a refusal is not an ask), through
`db.save_fetched_bot_lists()` so a restart keeps it, and the floor runs from
the later of `fetched_at` and `last_attempt`. A bot that never answers is asked
once per interval, not once per hour. Only an automatic ask is counted: the
operator's own Re-download list click is theirs, not the sweep's. A completed
fetch replaces the entry, so the mark goes with it. `AUTO_REFETCH_INTERVAL_HOURS
= 0` still means no floor. The setting's comment, plain help (en/fr/es) and
`settings.conf.sample` say what the interval is counted from.

`tests/test_an_auto_refetch_remembers_that_it_asked.py` (8): the reproduction (a
failed ask is not repeated at the next sweep, nor 23 hours on; it is asked again
at 24); written to disk with the entry; kept through a restart (the saved
registry loaded into an empty store, then the sweep the restart runs); a
refused ask is not counted; a newer completed fetch still sets the floor; a
damaged mark is ignored; and 0 means no floor. Verified by hand: measuring
from `fetched_at` alone fails two, never writing the mark fails three.

### 🎨 `dccore.mrc`: a background colour in the options

From the operator's first real session with the window (#550): the options
had a colour for every kind of event and none for the window itself.

mIRC has no per-window colour setting - `/color background` changes every
window at once, the operator's channels and queries included - only a
per-window PICTURE (`/background -t @window file`, `-x` to remove it). So the
option is a colour, and the script turns it into a one-pixel 24-bit `.bmp`
beside itself (`dccore-bg-<n>.bmp`, written the first time a colour is used,
one file per colour), tiled behind the window; "none" (the default) removes
the picture and leaves the window as mIRC has it. The combo in the Window
box lists none and mIRC's sixteen colours; the line selected is the colour
plus two, and the save stores line minus two, so a round trip cannot move it.
Applied when the window opens and when the options are saved.

`tests/test_the_mirc_window_has_a_background_colour.py` (13): the header the
script writes parses as a valid 1x1 24-bit bitmap (file size, data offset,
image size, one pixel as blue-green-red-pad right after it); the palette has
sixteen entries lined up with the names in the dialog; the combo is inside
the Window box and no dialog id is used twice; the line-to-colour mapping
agrees between fill and save; it defaults to none; `-x` and `-t` are what
`dccore.background` calls and `/color background` is never used. Mutants: a
wrong pixel order and an off-by-one in the save both fail.

The side panel's headings (Sending, Queue, Today, Since) are drawn in
`col.head`, which defaulted to navy and had no control in the dialog: on a
black window they could not be read and could not be changed (reported with a
screenshot). The Show box now has **Panel headings** beside the file-name and
console colours, filled and saved the same way, and the panel is redrawn when
the options are saved. The default is left alone - navy is right on mIRC's
own white background - and a saved choice is never overwritten.

**Not run in mIRC.** Written from mIRC's documentation (`/background`,
`/bset`, `/bwrite`); nothing here can execute the script, so the first
colour chosen is the real test.

### 📏 The mIRC window says MB

Seen on the first real send with `dccore.mrc` connected: `"…flac" to
mantaur98 (slot 1/3, 27.5)` and `27.5 in 0:25 at 1.06/s` - the size and
the speed with no unit. `$bytes(N,3).suffix`, which the script used, gives
no suffix in this operator's mIRC. `dccore.bytes` formats the unit itself
now (B/KB/MB/GB, two decimals under 10, one under 100, none above, so the
panel's columns stay columns) and `dccore.speed` is that plus `/s`; no
`$bytes()` left in the file. The same send also proved #563: `[SENDING]`
with the window connected, `[SENT]` 25 s later, the bot still there.

### 🧹 One suffix list for both sweeps

`tests/test_no_conflict_marker_is_left_behind.py` (#565) carried its own
copy of the identifier sweep's `TEXT_SUFFIXES`. A new file type has to be
added to that tuple or nobody scans it - `.mrc` and `.command` each
shipped unscanned once for that reason - and two copies were two places
to forget. The marker test now imports the sweep's tuple. Tests only.

### 🔄 A Re-download list button in the List Browser

#302's last open item, read wrongly the first time (as a button inside an
update notification, which does not exist) and corrected by the reporter: when
automatic re-fetching does not work, a button that fetches a bot's list again.

`AUTO_REFETCH_LISTS` re-asks a bot on a timer and ships off; with it off, or
not working, the only way was to type the nick into the fetch box. The List
Browser toolbar now has **Re-download list** beside Purge, for whichever bot's
list is open. Same visibility rule as Purge (`renderFilelistsPurge()` decides
both): only a list held from another bot - your own is the library, and a bot
only seen advertising is started from the fetch box as before. It asks for the
bot (`row.nick`), not the tab: the open one may be the bot's RAR or VIDEO list,
and the request is for the bot's list archive, exactly as the fetch box's is.
`POST /api/filelists/fetch` is unchanged and already answers 409 when a fetch
of that bot is running or the bot is not here; the button shows that sentence.
Three strings in en/fr/es.

`tests/test_redownload_a_bots_list_from_the_list_browser.py` (7), read from the
page source like the rest: the button is in the page and hidden, follows the
purge rule, asks for the bot rather than the tab, shows the server's refusal,
is wired, is never left disabled, and every language has all three strings.
Verified by hand: with the click handler removed the wiring test fails. Not
looked at in a browser.

### 🪧 A merge that leaves its markers behind now fails the suite

The changelogs take an entry from nearly every pull request, so branches
merged in turn conflict in `docs/UPDATES.md` and `docs/UPDATES-PUBLIC.md`
routinely, and each is resolved by hand. Twice in one day that went wrong
unnoticed: #543's merge left a bare separator line in the changelog with no
start or end marker beside it (found by reading the diff), and a resolution
script that handled the two changelogs left start, separator and end markers
in `docs/WINDOWS.md`, committed, with everything green.

`tests/test_no_conflict_marker_is_left_behind.py` (5) reads every tracked text
file - the changelogs included, since they are where it happens - for a line
that is exactly a start marker, an end marker or the separator. The separator
is checked on its own because that is what the first case left. The detector
is tested against all three, and against the ordinary rules and mid-line
uses of those characters, which it must not flag. Verified by hand: a bare
separator appended to `docs/WINDOWS.md` fails it; the tree as it is passes.

### 🗺️ The roadmap catches up

`docs/FUTURE.md`'s own rule is that a feature moves under *Implemented* in
the commit that builds it. Ten of them did not: the console in colour and
admin-only diagnostics (#553, #552), the structured feed, pairing and
`dccore.mrc` (#554–#556), the launchers, the browser setup, the Python
download and the OS small things (#551, #558, #559, #561), album sizes and
companion files (#543, #539), plain-language help and the translations
(#545). All under *Implemented* now, and the test count reads 5237 rather
than 4994. Docs only.

### 🧊 The STATUS burst never runs on the emitting thread

Seen live, 2026-09-19, the first night with `dccore.mrc` connected: at a
`SENDING` event the bot froze, the mIRC script saw no heartbeat and
declared the link dead 90 s later, and 24 minutes on the server ping-timed
the bot out. `stats_mgr.live_speed()` takes `dcc.queue_lock`, a plain
`Lock`; `status_lines()` calls it; and `SENDING` is emitted from inside
`with queue_lock:` in `dcc.check_queue_and_send()`. #555's `event_sink`
computed the burst on the emitting thread - taking a lock it already
held. That thread froze holding `queue_lock`; the writer's timer burst
blocked behind it (no heartbeat); the next request's `with queue_lock:`
in the IRC loop blocked too (no PONG).

- `Session.event_sink()` now only flags `_status_due` and wakes the
  writer (`request_status()`: no figure read, no lock taken); the writer
  thread, which holds nothing, computes and sends the burst on its next
  pass, ahead of the backlog. `hello`'s first burst stays synchronous - it
  runs on the reader thread, which holds no lock.
- Only a structured session was affected; a plain console or none at all
  never computed a burst on an event.
- `tests/test_status_slot_queue_and_pairing.py`: the five moving kinds
  set the flag and compute nothing on the caller's thread; the writer
  sends the burst after the event line (socketpair); and the regression
  itself - `event_sink("SENDING")` called while holding `dcc.queue_lock`
  returns at once (the old code hangs, the test's 3 s wait catches it) -
  with a source pin that `status_lines()` still calls `live_speed()` and
  `live_speed()` still takes the lock, so the test stays meaningful.

### 🌐 Set it up in the browser

#547, Proposal 4. With `settings_file.REQUIRED` still blank and Flask
present, `oserve.startup()` no longer exits 1: it serves one page on
127.0.0.1 until the form has written `settings.conf` and `admin_config.py`,
then carries on down the same line it always ran.

- **`oserve.startup(setup_page=None)`** - one `if`, not two phases: when
  unconfigured and `setup_page is not False`, resolve the page (the
  argument, else `webserver.run_setup_until_configured` when
  `webserver.setup_page_is_possible()`, else None), call it, re-ask
  `unconfigured_required()`. `False` is the old refusal (the three
  `test_startup` refusals pass it); a callable is a stand-in for tests.
- **`webserver.run_setup_until_configured(host, port, log, opener, wait,
  token)`** - `secrets.token_urlsafe(24)`, `werkzeug.serving.make_server`
  (stoppable, unlike `app.run`; **`except (OSError, SystemExit)`** because
  werkzeug calls `sys.exit(1)` on a bind failure and a taken port must not
  take the daemon down - it returns None and the old refusal follows),
  serve on a thread, print and open `http://127.0.0.1:<port>/setup?token=`,
  wait in a 0.5 s loop (a Windows console cannot deliver Ctrl-C into an
  indefinite `Event.wait()`), then half a second for the Saved page to
  land, `shutdown()`, `server_close()`, return the changes.
- **`create_setup_app(token, on_done, port)`** - its own small Flask app,
  no static folder, 64 KB body cap. `before_request`: the Host header must
  be a loopback spelling (DNS rebinding), and until the form is saved
  every request must carry the token (CSRF: any site open in the same
  browser can POST to 127.0.0.1, and with no password yet that POST would
  set its own). `GET /setup` renders; `POST /setup` validates, writes
  through `apply_setup()`, marks done, renders the Saved page; `/` →
  `/setup?token=`; `/login` answers 503 so the Saved page's poll gets a
  200 only once the real app holds the port.
- **`build_setup_fields(lang, values)`** - `SETUP_FIELDS` through the
  Settings page's `_settings_field()`, with `settings.field.<NAME>` and
  `.help` from `web/lang/<lang>.json` laid over for fr/es. So #545's
  plain help and the translations appear with no second copy.
  **`validate_setup_form(form)`** builds the same `{NAME: value}` dict
  `configure.collect_answers()` builds (blank folder absent, dashboard
  off writes no host, LAN → `0.0.0.0`) and hashes the password with
  `adminchat.make_password_hash`. **`apply_setup(changes, hash)`** writes
  through `configure.write_settings_conf()` and
  `write_admin_config_password()` - the terminal path's own writers, so
  the files are identical - then `settings_file.apply_to(vars(config))`
  and `config.ADMIN_PASSWORD_HASH = hash`, since `admin_config.py` was
  imported (or not) long before and is not re-imported.
- **`render_setup_page()` / `render_setup_saved_page()`** -
  server-rendered, `string.Template`, no script on the form (the Saved
  page has the poll), dark palette of the login page, EN/FR/ES links.
- **`configure.offer_setup_in_browser(ask, log)`** (`--setup-in-browser`):
  0 = Flask importable (installed just now on a yes; `pip` checked first),
  start the daemon; 2 = declined, no pip, EOF (nobody at the keyboard):
  ask in the terminal. **Both launchers** call it on a fresh tree; on 0
  they skip the setup check (it would refuse the blank tree the page
  exists to fill in) and the `--flask` offer, and start `oserve.py`.
  Proposal 1's stub now answers 2 so its tests keep their path; two new
  ones cover the 0 path.
- `tests/test_set_it_up_in_the_browser.py` - 41: the fields are the
  Settings page's own with #545's help (not the developer's), fr/es from
  the lang files, unknown lang → en, typed values redisplayed; the
  validator's dict equals configure's, blank folder absent, dashboard off
  → no host, LAN, eleven refusals, an existing folder taken, errors named
  per field; apply writes both files as configure does (the admin text
  byte-equal to the terminal writer's) and the process sees them; the
  Host check both ways; the app via Flask's test client - token on GET
  and POST, root redirect, foreign Host refused, every field/help/token in
  the page and no script, French, the CSRF POST refused with nothing
  written, a bad form 400 with its errors and values but never the
  password, a good form writes and polls, dashboard off does not poll,
  after saving the page is the Saved page and the poll gets 503, a second
  POST does not write again; **end to end over a real socket**: the URL
  printed and handed to the browser opener, GET, a token-less POST 403,
  the real POST Saved, the function returns with the changes and the port
  is free again; giving up returns None; a taken port is reported, not
  fatal, and never exits the process; `startup()` with a stand-in page
  serves then carries on, a page that returns without configuring still
  refuses, `setup_page=False` is the old refusal, the default is the real
  page; the launcher hook's three answers and its wiring; the docs.

### 🧰 The small things that are the OS, not DCCore

#547, Proposal 6 - four of them, each small.

- **The firewall.** `Platform.firewall_hint` in `scripts/setup_check.py`
  (a wording difference, so it belongs in the seam): printed after the
  port check with the range filled in - Windows names
  `allow-firewall.bat`, Linux names `ufw allow` and `firewall-cmd`.
  **`scripts/windows/allow-firewall.bat`** re-opens itself elevated when
  `net session` fails (`powershell Start-Process -Verb RunAs`), reads the
  ports through **`scripts/ports.py`** → `setup_check.ports_line(config)`
  (one place knows the ports; `test_the_checks_live_in_exactly_one_place`
  counts the copies and caught the first draft) into a temp file (for /f's
  command form and a quoted `%PY%` do not mix), deletes then adds `DCCore
  DCC sends` for the range and `DCCore dashboard` for `WEBUI_PORT` when
  `WEBUI_ENABLED`, no parenthesised block around an echo with a `)` in
  it. `remove-firewall.bat` deletes both.
- **Starting with the system**, each with a twin, each running the
  launcher rather than `oserve.py` (the launcher is what puts the working
  directory right), each refusing a tree with neither `settings.conf` nor
  `admin_config.py` (a service cannot answer the setup questions):
  `install-autostart.bat` → `schtasks /create /tn DCCore /sc onlogon /tr
  "<launcher>" /f`, no `/ru`, so no account or password stored and the
  bot's window opens at logon; `install-autostart.sh` → a systemd **user**
  unit in `$XDG_CONFIG_HOME/systemd/user/dccore.service`
  (`WorkingDirectory`, `ExecStart`, `Restart=on-failure`,
  `WantedBy=default.target`), `daemon-reload`, `enable --now`, and the
  `loginctl enable-linger` line for boot without login;
  `install-autostart.command` → `~/Library/LaunchAgents/com.dccore.bot.plist`
  (`RunAtLoad`, `KeepAlive.SuccessfulExit=false`, stdout/err to
  `~/Library/Logs/dccore.log`, the path XML-escaped), `launchctl unload
  -w` then `load -w`. All four POSIX files mode 100755.
- **The console window** - both launchers already say closing it stops
  the bot (#551); pinned here as the third thing.
- **Port forwarding** - what it means, where in the router, which range,
  why DCCore cannot do it (no UPnP in the stdlib), in `docs/WINDOWS.md`
  and `docs/INSTALL.md`, with the firewall and autostart sections.
- `tests/test_the_small_things_that_are_the_os.py` - 33, executed with
  the OS commands faked on PATH ahead of the real ones (`schtasks`,
  `net`, `netsh`, `powershell`, `systemctl`, `launchctl`, each appending
  its arguments to a file), so nothing real is created on the machine
  running the tests: the Windows helpers under cmd.exe (elevated adds the
  range and, with the dashboard on, its port; deletes before adding; not
  elevated asks Windows and never touches netsh; ports from the settings
  not the script; no Python says run the launcher; remove deletes both;
  install creates the on-logon task running the launcher with no `/ru`,
  refuses an unconfigured tree, reports a refusal; remove deletes, and
  nothing-to-remove is not an error); the Linux and macOS ones under the
  POSIX shell on PATH - Git Bash on the Windows runners, so all three
  families run on all three runners - with the unit parsed back by
  configparser and the plist by plistlib, including a folder named `Tom
  & Jerry`; plus `ports_line`, the hints, exec bits from `git ls-files
  -s`, CRLF and `call` on every bat, launcher-not-oserve, and the docs.

### 🔎 A search's header comes first, not last

Seen live: `Search Result: ON  Found: 3 Match(es) For ...` arrived at
07:22:47, under the three result rows from 07:22:07, 07:22:17 and 07:22:27,
with ten seconds between rows instead of five.

`announce.send_search_result_header()` queued the header as
`"channel_announce"` - the VIP lane the channel advert lines share - while
the rows that follow it are queued in the requester's own lane by `list.py`.
Since v1.12.2 `queue_mgr` takes strict turns between the two lanes (one VIP
line, one standard line, so `Sent:` is never more than two slots away), which
is right for what it was built for and wrong for a header that has to lead its
own rows: with an advert cycle's thirteen lines waiting in the VIP lane, the
header sat behind them while the rows went out on every other slot. The ten
seconds are the same alternation - an advert line takes each slot between two
rows.

The header is a private message to one user, so it now goes in that user's own
lane, ahead of the rows the caller queues next; FIFO within the lane does the
rest. The rows still share slots with an advert in progress, which is the
fairness the alternation exists for.

`tests/test_the_search_header_goes_first.py` (3), against the real
`oserve.queue_message()` and `queue_mgr.next_standard_line()`: the header is not
in the VIP lane; it is first in the requester's lane, before its rows; and
served the way `queue_mgr` serves - one VIP line, one standard line, in turn -
with thirteen advert lines already waiting, the requester's first line is the
header. Verified by hand: with the fix reverted all three fail.

### 🐍 Python missing: help, do not fail

#547, Proposal 2. The one wall left after Proposal 1: a Windows machine
with no Python got "Python was not found" and a URL. `start-dccore.bat`
now offers to install it.

- **The offer, then the download.** `choice /c YN` - a piped or closed
  stdin reads as no (`errorlevel` 255 ≥ 2), so an unattended run never
  downloads. `PROCESSOR_ARCHITECTURE`/`ARCHITEW6432` pick `amd64` or
  `arm64`; a 32-bit Windows gets the page. `call curl -L --fail
  --progress-bar` (curl ships with Windows 10 1803+; `call`, so a wrapper
  script returns instead of taking over - the fake in the tests found
  that). A failed download is reported and never hashed.
- **The pin.** `PY_VERSION=3.14.7`, `PY_SHA256_AMD64`, `PY_SHA256_ARM64` at
  the top of the file, copied from python.org's release page (which prints
  the SHA-256 in four groups of sixteen) and verified against the downloaded
  installers when pinned. `certutil -hashfile ... SHA256`, spaces removed
  for older certutils, compared case-insensitively; a mismatch prints
  expected/got, deletes the file and does not run it.
- **The run.** `start /wait "" installer /passive InstallAllUsers=0
  PrependPath=1 Include_launcher=1 Include_test=0` - python.org's documented
  unattended options; 0 and 3010 (reboot required) count as installed. Then
  `goto :find_python`: this window's PATH predates the install, so
  Proposal 1's `%LOCALAPPDATA%\Programs\Python` search is what finds it;
  `PY_INSTALL_TRIED` guards the loop to one pass. Any other outcome lands
  on the by-hand text with the download page, opened in the browser unless
  `DCCORE_NO_BROWSER` is set (the tests set it).
- Linux/macOS unchanged: they name `apt`/`dnf`/`brew` and stop, which is
  the whole of the proposal there; pinned by a test.
- `tests/test_python_missing_help_do_not_fail.py` - 20: the pin's shape,
  the URL from version and processor, the pinned minor is in the CI
  matrix, an opt-in network test that downloads both installers and
  checks the hashes (`DCCORE_VERIFY_PYTHON_PIN=1`; run once here, both
  match); the order of ask → download → hash → run on the source; and,
  executed on Windows with a PATH that has no Python, empty
  LOCALAPPDATA/ProgramFiles and a fake `curl.bat`: declining, no answer at
  all, a junk download refused by the real certutil and deleted, a failed
  download reported, a matching hash (fake certutil) leading to the run and
  the honest exit-code message when what was written is not a program, a
  space-separated hash still matching, and nothing of this appearing when
  Python is present. `docs/WINDOWS.md` is two steps now; `docs/INSTALL.md`
  says which platform does what.

### 🔤 The mIRC window in the operator's own size, and no `/echo` errors

Two things from the first real run of `dccore.mrc` (mIRC on a
high-resolution screen; the pairing itself worked end to end):

- **The font.** A fixed 9 pt Lucida Console was unreadable. `/dccore font
  <size>` (and a size field beside the font tickbox in the options) sets
  it and remembers it; until set, `$dccore.fontsize` takes the Status
  window's `$window().fontsize`, else 12. The fixed-width face stays,
  since the panel's columns need it.
- **`* /echo: insufficient parameters`** on every empty line the bot
  sends - the banner has two, `help` ends with one. `/echo` refuses an
  empty text; `dccore.echo`, `dccore.sys` and `dccore.out` now draw an
  empty line as `$chr(160)`.
- The title bar is refreshed on the first banner line, so it no longer
  says "opening" while the bot is already talking.

### 🪟 The bot's window in mIRC

#550, step 4 of 4 - `scripts/mirc/dccore.mrc`, the client the structured
feed was designed for. One file, `/load -rs dccore.mrc`, written for
**mIRC 6.10 and later** (every construct dates from 6.x: `/window -el`,
`/aline -l`, `/titlebar`, `on ^CHAT`, `on CHATCLOSE`, hash tables with
`/hsave`, `/timer -m`, dialog tables, `$bytes().suffix`, `$regex`; the
file is pure ASCII and CRLF - `.gitattributes` now says so for `*.mrc`).

- **`/dccore pair <bot>`** opens `@DCCore`, offers the chat, hides the
  chat window on the first line, asks for the password once by hand,
  sends `hello dccore.mrc 1.0` then `pair dccore.mrc 1.0`, and keeps the
  `DCCORE TOKEN` in `dccore.ini` beside the script. From then on it logs
  in with the token: on `/dccore connect`, on IRC connect, on the bot's
  JOIN; retries 5/15/60/120 s; a 90 s silence (no STATUS heartbeat)
  reopens the chat; a refused token drops to hand-typed password; a
  `Session taken over` does **not** auto-reconnect (two clients would
  trade the session for ever). `raw 401` on the bot's nick is caught.
- **Drawing**: every `DCCORE` line is parsed by `$N` position exactly as
  `structured_line()` renders it; the tag is bold in the group's colour,
  padded with `$chr(160)` (a `$1-` collapses spaces), the name in its own
  colour; `STATUS` sets the title bar and the panel is redrawn 250 ms
  after the last `SLOT`/`QUEUE` line of a burst (there is no end marker);
  `OUT` lines are `[CONSOLE]` or go to `@DCCore-console`; `DROPPED` is
  shown; an unknown type on the same major is shown as-is; `HELLO` with
  another major, or `Unknown command: hello`, or six seconds of no
  `HELLO`, means plain mode: the chat as it comes, no panel.
- **Options dialog** (`dialog dccore.opt`): show/colour per group
  (request, queued, sends, fail, search, joins, bans, info), name and
  console colours, status-line minutes, panel, title bar, separate
  console window, beep, font, bot nick, auto-reconnect, pair again,
  forget token. Panel toggling recreates the window and carries the text
  over line by line, guarded so `on CLOSE` does not read it as a
  disconnect.
- **Menus**: window (commands, the selected panel user's queue / clear,
  options, panel, connect/disconnect), nick list (queue / clear the queue
  of the nick), status and channel windows.
- Gotchas recorded in the header for whoever edits it next: timers run
  outside script scope, so every alias is global (namespaced `dccore.*`);
  `[ ]` are evaluation brackets; newer-mIRC features go behind
  `if ($version >= 7)` rather than raising the floor (7.x already decodes
  the chat as UTF-8, so non-ASCII names show correctly there).
- `tests/test_the_bots_window_in_mirc.py` - the file ships (ASCII, no
  tabs, braces balance, every block a header), the requirements header
  says 6.10, every line type the bot can send is handled by name, no
  `alias -l` (timers), the four timer targets are defined, the field
  positions the script reads match `structured_line()` and
  `status_lines()` for every kind, the docs reference it, `.gitattributes`
  carries the CRLF rule. Not run in mIRC by CI; the user verifies in
  mIRC 6.10 and 7.x by hand.

### 📊 STATUS, SLOT, QUEUE and pairing

#550, step 3 - the live picture a window is drawn from, and a credential
for a script that is not the admin password.

- **`adminchat.status_lines(now=None)`** - one burst: `DCCORE STATUS <used>
  <slots> <qfiles> <qusers> <sent_today> <bytes_today> <bps_now> <record_bps>`,
  then `DCCORE SLOT <nick> <sent> <total> <bps> <name>` per active transfer
  and `DCCORE QUEUE <pos> <nick> <files> <frozen_secs_left>` per queued user
  (the first 20, sorted). Today's figures are the rolled ones
  (`db.load_advanced_stats_rolled`); the record is `db.get_speed_record()`;
  `bps_now` is `stats_mgr.live_speed()`. A figure that fails to load reads 0
  and the log says why - a wrong number beats no title bar.
- **`Session.send_status()`** - after `hello`, after any `SENDING`, `SENT`,
  `FAIL`, `QUEUED` or `RESUMED` event, and every `STATUS_INTERVAL` (30 s)
  on the writer thread's own half-second wake - the heartbeat. The timer
  fills silence only: it speaks when the outbox is empty, never on top of a
  backlog it would only push lines off.
- **`dcc.start_dcc_send()` stamps `size` and `started_at`** on the
  `active_transfers` row once the file is open, so `SLOT` has a total and a
  clock of its own; a row without them reads 0.
- **`pair <client> <version>`** mints `secrets.token_urlsafe(32)`, stores
  `{"hash": make_password_hash(token), "created", "by"}` under the client's
  name in `ADMIN_TOKENS_FILE` (`db.load_admin_tokens` / `save_admin_tokens`,
  through `_disk_lock` and `_atomic_write`), and sends it back once as
  `DCCORE TOKEN <name> <token>` or a plain line. **`_check_password()`**
  accepts a token or the password (`_token_matches()` over the store with
  `verify_password`); everything around it - hostmask check, three attempts,
  IP block - is unchanged. **`webserver` never reads the store**: a token
  opens a chat, never the dashboard (pinned by a test). `unpair` lists;
  `unpair <name>` revokes. Both live in `COMMANDS`, so authenticated only.
- **`ADMIN_TOKENS_FILE`** (advanced; `./data/adminchat_tokens.json`) with
  label, plain help and fr/es.
- `tests/test_status_slot_queue_and_pairing.py` - 25: the burst's shapes
  and edge cases, when it is sent and when it is not, the silence-only
  timer over a socketpair, pairing end to end including the dashboard
  refusing a token, and password → `pair` → token login over a real
  loopback chat. Two step-2 tests widened for the burst.

### 📡 A structured feed for the admin chat

#550, step 2 - the protocol under `dccore.mrc`, usable by any client. A
console session that says `hello <client> <version>` after logging in gets
every feed event as one line, `DCCORE <TYPE> <fixed fields...> <free
text>`; one that never says it is byte-for-byte the console it was.

- **`announce.feed_event(kind, text, **fields)`** - the feed told twice:
  the prose to `send_debug()` under `kind`, exactly as before, and the
  fields to a second sink registry (`add_event_sink()`), so the `(text,
  category)` contract every existing sink relies on is untouched. The
  console tickboxes gate the fields as they gate the prose. All seven
  emitters go through it: SENDING and QUEUED in `announce.py` (the sending
  notice now takes an optional `path` and reports the size; its three
  dispatch callers pass the path they have), SENT in
  `send_transfer_complete()` (now takes `duration`), FAIL in
  `dcc._report_transfer_failure()` (now takes `acked`/`total`, which the
  five sites that know them pass), REQUEST at both request sites, RESUMED
  after the accept, SEARCH in `execute_search()`.
- **Found on the way**: `Sent:` went out as `category="INFO"` from the day
  it was written. The `[SENT]` tag the channel line has rendered since
  #526 never fired for the one line it was for, and #528's "sends" tickbox
  never governed it. It is `SENT` now; `test_announce_output` had pinned
  INFO and is corrected.
- **`adminchat.structured_line(kind, fields)`**, pure: the line shapes
  in `docs/ADMIN-CONSOLE.md`'s new table. Space-separated positional
  tokens, the one free-text field last (mIRC's `$N-`); numbers raw;
  control characters and tabs in any field become spaces; a token field
  (nick, kind, category) has its spaces replaced so it stays one `$N`; a
  ` :: ` inside a filename is broken so it cannot split a FAIL line. Every
  kind outside the seven is `DCCORE LOG <CATEGORY> <prose>` - nothing is
  lost by the typing.
- **The session**: `Session.structured`, set only by the `hello` command,
  which lives in `COMMANDS` and so is reachable only once authenticated
  (an unauthenticated `hello` is a wrong password). `debug_sink()` in
  structured mode skips the feed kinds (their fields arrive through the
  new `event_sink()`) and sends everything else as LOG; `send()` wraps a
  command reply as `DCCORE OUT <text>` unless it already is a DCCORE line,
  so the fourteen handlers need no change; the writer sends `DCCORE
  DROPPED <n>` ahead of the next line through when the bounded outbox had
  to drop for a slow client. Both sinks attach at authentication and
  detach at close.
- `docs/ADMIN-CONSOLE.md` gains "The structured feed, for a script" with
  the handshake, the rules and the line table.

Tests in `tests/test_a_structured_feed_for_the_admin_chat.py` (33): the
line format per kind, free text last with spaces, raw numbers, missing
numbers as zero, control characters and tabs to spaces, token fields with
no spaces, the FAIL marker unsplittable, HELLO's fields; `feed_event()`
tells both halves, the tickbox gates the fields, a raising sink is dropped
not fatal, every emitter goes through it and no `category="<kind>"` prose-
only send remains, SENT is finally SENT with its fields, SENDING reports
the size from a path and zero without one; the session - hello switches
and answers, an unauthenticated hello is a password guess, a plain session
unchanged, a structured one gets fields not prose for feed kinds and LOG
for the rest, OUT wrapping, no double wrapping, hello in the table; a slow
client is told `DROPPED n` before the next line, over a socketpair; and
login → hello → a SEARCH event over a real loopback DCC chat. Six mutants
- OUT wrapping gone, feed kinds sent as prose too, DROPPED never
reported, tickbox not applied to fields, token spaces kept, Sent still
INFO - each fail. Four #538 anchors re-pointed at `feed_event`.

### 🎨 The admin console in the bot's own colours

#550, step 1 - the one piece of the mIRC-window design that stands alone.
A DCC CHAT window is an IRC client and renders mIRC colour codes the way a
channel does; the chat sink stripped every code with the note "a console
is read as a log, not rendered by an IRC client" - true of the dashboard's
Console page, false of this one.

- **`announce.category_tag(category)`** - one table, `(label, colour)`,
  replacing the `elif` chain inside `send_debug()`. Both the channel
  line's tag block and the console now read it, so a `[SECURITY]` in the
  channel is a `[SECURITY]` in the console, in the same alert colour, in
  the operator's `THEME`. The labels that differ from the category are
  kept and their reasons recorded on the table: BAN → `[HARDBAN]` (an
  admin's `!ban`) and HARDBAN → `[SECURITY]` (a blocked path traversal)
  must not look alike; MUTE → `[MUTED]`, TBAN → `[TEMPBAN]`; the feed's
  own categories take the `[SENT]` colour.
- **`adminchat.console_line(text, category)`**, which the chat sink now
  calls: with `ADMIN_CHAT_COLOURS` on (default) it is
  `<colour>[LABEL]<reset> text`, and a caller's own bold round a nick is
  kept; off, it is the pre-#550 `[CATEGORY] text` with every code
  stripped - verbatim category, so a log parser on the old form sees
  nothing new. The dashboard's `_console_debug_sink()` keeps stripping.
- `ADMIN_CHAT_COLOURS: bool = True`, in Settings → Admin console, with its
  plain explanation and fr/es.

Tests in `tests/test_the_console_in_the_bots_own_colours.py` (20): the
table (feed categories take the value colour, the alerts, the two bans
distinct, mute/tempban labels, join/quit, unknown → grey INFO, case,
follows the theme); the channel line's tag is the table's for ten
categories and the old chain is gone; the console line on and off, the
label from the table, bold kept, the sink goes through it, the dashboard
sink still strips; the setting ships on as a bool with help. Two tests
updated with intent kept: `test_mirc_formatting_is_stripped_for_the_console`
now says "when colours are off" and has a coloured-by-default twin;
`test_announce_renders_the_fail_category` asserts FAIL's tag is an alert
through the table rather than grepping the chain it replaced.

### 🔒 `!ping` and `!debugnames` answer only the bot's own admin

Seen live by the user: another operator typed `!ping` in a shared channel to
check their own bot, and every DCCore in the channel ran a latency check -
each spending a paced server line (the slot the adverts share) and each
reporting into its own admin console, so a stranger's ping appeared in the
user's console as `[INFO] Latency Check triggered by <them>` as if the user
had asked. Both commands are in `irc.py`'s *unaddressed* user-command set
(`msg_lower in ("!list", "!debugnames", "!ping")`), so every bot present
handled them, and neither even answers the person who typed it: `!ping`
reports only through `send_debug()`, `!debugnames` is a `[RAM-CHECK]`
notice about the bot's own membership mirror. They are the operator's
tools.

- `commands.diagnostics_are_for_the_admin(user)` - one gate, `is_admin()`,
  the same rule the admin commands use, so it cannot drift from it.
- Both dispatch branches in `irc.py` check it first and `continue`
  silently - an answer or a log line per stranger is the noise this
  removes. The `elif` lines are untouched, since three source-anchored
  tests key on them.
- `handle_ping_request()` checks it too, so no other caller can make the
  bot ping on a stranger's behalf.
- **`!list` stays public on purpose**: it is the discovery command every
  serving bot answers with its trigger - that is how people find bots.

Tests in `tests/test_diagnostics_answer_only_the_admin.py` (9): the gate's
truth table (admin, case-insensitive, every listed admin, strangers, and
equal to `is_admin()` for both answers); a stranger's `!ping` sends
nothing to the server and starts no measurement while the admin's still
goes out; and, read from the source, both branches gate before doing
anything (before the thread for `!ping`, before the lock for
`!debugnames`) while `!list` does not. Three pacing tests in
`test_a_shared_outbound_pace.py` that pinged as `alice`/`bob` now list
them in `ADMIN_NICK` - what they measure is the pacing of a ping that IS
sent - and its `debugnames_block()` slice is bounded by the next branch
rather than 1500 characters, which the gate had pushed the `queue_message`
line past. `docs/ADMIN-CONSOLE.md` says which commands are whose.

### 🚀 The launcher is the install

#547, Proposal 1. A first-timer's install was eight steps, three of them in
a terminal, and the dashboard's one dependency was discovered at step 7
from a log line saying step 4 had been needed. Now: install Python,
extract, run the launcher for your system.

- **No config → `configure.py` runs right there.** Both launchers used to
  refuse an unconfigured tree with "copy the sample and fill it in"; they
  now run the setup questions themselves, then continue to the check and
  the start. A tree with no `configure.py` (a broken extract) is still
  refused - the one thing the branch must never do is start on the
  defaults - and a setup that does not finish stops the launcher rather
  than starting a half-configured bot. The legacy `local_config.py` branch
  keeps its place *before* this one: `configure.py` would otherwise
  happily write a fresh `settings.conf` beside the stranded file.
- **`configure.py --flask`**, run by both launchers just before the start:
  silent when the dashboard is off or Flask is present, otherwise the same
  `pip install -r requirements-web.txt` offer setup makes
  (`offer_flask_if_the_dashboard_is_on()`). Never a reason not to start.
  Runs after the setup check, so nothing is offered for a bot that is not
  going to start.
- **Windows finds Python when the PATH box was missed**: after `py` and
  `python`, the launcher probes `%LOCALAPPDATA%\Programs\Python\Python3*`
  and `%ProgramFiles%\Python3*`, where the python.org installer puts it.
  The "not found" message now names both installer boxes and the download
  page.
- **The Linux launcher takes the first Python that actually runs**, not the
  first that exists: macOS's `/usr/bin/python3` is an Xcode-installer stub
  and Windows (under Git Bash) has a Microsoft Store one; both pass
  `command -v`. Each candidate is asked to `import sys`. The not-found
  message names the package-manager command for the system it detects.
  `readlink -f` is gone (macOS before 12.3 had none) in favour of
  `cd "$(dirname "$0")" && pwd -P`.
- **`scripts/macos/start-dccore.command`** - new, executable, a wrapper
  that `exec`s the Linux launcher. Finder runs a `.command` in Terminal on
  a double-click, which is the whole reason it exists; Gatekeeper needs
  one right-click → Open the first time, and the docs say so.
- **One line in the banner**: closing the window stops the bot.
- `docs/WINDOWS.md`'s seven steps are three; `docs/INSTALL.md` gains "The
  short way" with the launcher for each platform; the README's intro says
  the same in one sentence.

Tests in `tests/test_the_launcher_is_the_install.py` (22): both launchers
**executed** in throwaway trees beside stub scripts that print a marker,
through six states each - fresh tree (configure → `--flask` → oserve, in
that order, with the welcome), setup that does not finish (no start), a
tree without `configure.py` (refused, no start), a configured tree (no
questions asked), a failing setup check (no offer, no start), check mode
(only checks) - plus the closing-window line; the macOS wrapper (exists,
execs the Linux launcher with `"$@"`, is `100755` in git, the Linux
launcher uses no `readlink -f` in a code line and probes candidates by
running them); and the `--flask` hook (silent off / silent with Flask /
the offer with Flask hidden, returning 0 on decline; wired in
`configure.py`'s main). `tests/test_legacy_config_upgrade.py`'s three
"fresh install gets the sample instruction" tests are updated with their
intent kept: legacy branch before the first-run branch; a fresh install is
configured, never started on the defaults.

### 🧪 The quiet-window test without a sleep

`test_a_late_channel_restarts_the_quiet_window` slept 60% of the debounce
window and asserted nothing had flushed yet - a bet on the scheduler that
macOS's GitHub runners lost at 0.05 s (#546), and again after it was
widened to 0.3 s (#548; the failure that reddened #556). The property is
that the second arrival cancels the timer the first armed and arms a new
one; the test now checks that on the timer objects (`finished.is_set()`,
identity), which cannot be late, then waits for the one flush and counts
both channels. A mutant that stops cancelling fails on the first
assertion. No sleep left in it.

## 🟩 v1.12.2 (2026-09-18) - "The Dashboard Speaks For Itself"

### ✍️ A bot that never advertises can be added by hand

#376, part 2. The List Browser's sidebar is built from adverts we have seen
(`runtime.known_bots`), so a bot that answers `@nick` perfectly well but
does not advertise on a channel we are in had no row, and there was no way
to fetch its list from the dashboard. Confirmed absent before this:
`webserver.py` had `/api/filelists`, `/fetch`, `/fetch-folder-rar`,
`/bots`, `/search` and `/bot/<nick>`, and nothing that adds a source.

- **`POST /api/filelists/sources`** (`build_add_source_result()`): the nick
  goes into the **same registry**, flagged `hand_entered`, rather than a
  second list - so every reader of the sidebar (rows, freshness, presence,
  the fetch button, the alt-nick display) works on it unchanged, and if the
  bot ever does advertise, `irc._record_bot()` merges the advert into the
  same entry and the flag survives. Validated as a nick (`_looks_like_a_nick`:
  RFC 2812's shape, no channel prefix, no whitespace, ≤ 64) on top of the
  IRC-line safety check; our own nick refused; a bot already seen is kept,
  not duplicated. Persisted at once (`_flush_known_bots(force=True)`) - the
  30 s flush timer is for adverts, not for an operator's own action.
- **`POST /api/filelists/sources/<nick>/remove`**
  (`build_remove_source_result()`): forgets a hand-entered bot. 409 for a
  bot that advertises (it would be back at its next advert), 409 for one
  with a held list (the purge routes know about requests in flight), 404
  for a stranger.
- **Pruning**: `irc._known_bot_is_stale()` never drops a hand-entered entry
  - it has no adverts to age on - and the size cap's eviction skips them
  (with `last_seen` 0 they would sort as the oldest of all). They stay until
  the operator forgets them.
- **The row** carries `hand_entered`; the page tags it "by hand" (tooltip:
  added by nick, not seen advertising), shows the grey "cannot tell"
  presence and `not_held` freshness like any advert-only row, and the
  count stays an em dash - no advert, no claim. A fetch from it goes through
  `bot_not_here_error()` like every other.
- **The sidebar** gains a small form under the purge button: a nick box,
  **Add** and **Forget**, with its own status line. Both refresh the list at
  once. Eleven dictionary keys in en/fr/es.

Not done, deliberately: presence for a hand-entered bot is not probed (no
WHOIS) - the row says "cannot tell" until NAMES or a JOIN says otherwise,
the same rule as everywhere else. Part 1 of #376 (alt-nick identity) is
untouched.

Tests in `tests/test_a_bot_named_by_hand.py` (26): adding (entry shape,
persisted at once, an advertising bot kept not duplicated with the
advertised spelling winning, trimming, blank/non-nick/own-nick refused,
nick punctuation allowed); the row (tagged, not-held, count None,
presence None, an advert later fills it in and the flag survives);
pruning (the week TTL, the confirmed-absent TTL and the size cap all leave
it alone while still applying to others); forgetting (removed and
persisted, 404 unknown, 409 advertising, 409 held, unsafe input refused
first); over HTTP behind the login (add → in `/api/filelists/bots` →
forget; a non-object body is 400 not 500 - `json_object()`, the guard
`test_no_post_route_still_coerces_with_or` insisted on); and the page
(form, the two routes, the tag only when hand-entered and not held, every
key in all three dictionaries). `docs/INSTALL.md` mentions the form.

### 🍎 macOS joins the CI matrix

From #69's macOS discussion: "the cheapest first step, by a distance" is to
add `macos-latest` to CI and let the answer be a fact rather than a guess.
`platform_compat.py`'s `IS_WINDOWS` switch already treats "not Windows" as
POSIX-like, so macOS was never expected to need its own branch - this
either confirms that cheaply or turns up the first real failure to fix.
README's platform line updated to name all three.

It turned up one, and it was CI's own timing, not the daemon (#548).
`tests.test_reconnect.ReconnectThawSummaryTests.test_a_late_channel_restarts_the_quiet_window`
failed on macOS 3 of 6 job-runs across two full CI runs, and never once on
Linux or Windows (0 of 12) - which Python version failed was different each
time, ruling out a version-specific cause and pointing at macOS's
GitHub-hosted runner scheduling specifically. The test's own `setUp()`
shrinks `irc._RECONNECT_THAW_QUIET_SECONDS` to 0.05s for speed, and this one
test additionally slept for 60% of that window before asserting nothing had
flushed yet - leaving only ~20ms of slack before the real
`threading.Timer(0.05, ...)` fired on its own, not enough headroom against
macOS's scheduling jitter. Widened to 0.3s for this one test only (same 60%
checkpoint, ~120ms of slack now), rather than touching the 0.05s every
other test in the class relies on for speed. Not a bug in the debounce
logic itself - every other test in the class, which polls via
`wait_for_flush()` rather than sleeping a fixed amount, passed reliably
throughout both runs.

### 💬 The "?" beside every setting now speaks the operator's language

The user, on #537's tooltips: "some things are too technical. Check for
example the nickname explanation. How is a simple user going to understand
it?" Fair - `NICKNAME`'s "?" read: *"NICKNAME, ADMIN_NICK and CHANNEL are
None - not a real value - because they ARE in settings_file.REQUIRED:
oserve.startup() refuses to boot while any of them is still blank..."*
That is the developer's record of why, which is exactly what belongs in
`defaults.py` and exactly what does not belong in a tooltip. The two
readers want different texts, and #537 gave both the same one.

- **`settings_help.PLAIN_HELP`** - one explanation per setting the page
  shows (113), written for the person running the bot: what it does, when
  they would change it, what a sensible value is. `NICKNAME` now reads
  *"The bot's name on IRC. People request files with it (for example
  @YourBot for the list), so pick something short and easy to type.
  Required - the bot will not start without it."*
- `help_text()` returns the plain text when there is one and the developer
  block otherwise (`developer_text()`, the old behaviour under its real
  name). The comment blocks in `defaults.py` are untouched.
- **`settings.conf.sample`** shows the plain text first, wrapped, then a
  blank comment line, then the developer's block - the order a reader
  wants them in. Regenerated.
- **French and Spanish**, all 113, under `settings.field.<NAME>.help` in
  `fr.json`/`es.json`. These **replace** the entries Neo added in #542 an
  hour earlier: those were faithful translations of the technical text this
  change retires, and a tooltip that is plain in English and technical in
  French would be worse than either. `en.json` carries none, as #542's
  design says - the page falls back to the server's text, which is the
  source, so there is nothing to drift.

Guards in `tests/test_every_setting_explains_itself.py`
(`ItIsWrittenForTheOperator`, 8): every setting on the page has a plain
explanation and no explanation is for a setting the page does not show;
plain wins over the developer block; **it speaks no code** - no `.py`
names (except `admin_config.py`, which the operator edits by hand), no
`function()` calls, no issue numbers, no module names - the rule that keeps
the next entry plain too; it is short (≤ 320 characters); the sample shows
the plain text first with the developer block after; fr and es carry a
translation of every one, and none is identical to the English; `en.json`
carries no `.help`. The three earlier paragraph-joining tests now name
`developer_text()`, which is what they were testing. The identifier sweep
(`test_no_personal_identifiers_ship`) passes on all three languages - it
caught a fragment in #542's Spanish once, so it was run on purpose.

### 📦 Each folder heading says how many files and how big

The size on each album from #69, wanted from the start and deferred twice.
The first time because a `!rar` row must stay verbatim - AutoQ copies it -
so that list gains nothing. The second time the plan became "put it on the
main list's folder heading, which is framed decoration". It is not: `list.py`'s
reader takes the whole heading line as the folder (`current_folder =
line_strip`), `dcc.py`'s request resolver does the same (`last_heading`),
and so does every older DCCore that fetches this list. A size appended to
the heading would resolve every request into a folder that does not exist,
on every one of them.

So it is **its own line, under the closing rule**, indented:

```
===================

D:\MEDIA\Artist\Album\
==========================
    14 files, 1.20GB
!Bot 01 - Track.flac  ::INFO:: 87.61MB
```

A line that is neither a rule nor a `!` row is dropped by every reader in
its resting state - `list.py`'s state machine skips it, `dcc.py` looks only
at prefix lines and `!` lines, AutoQ imports `!` rows, and `count_request_lines()`
counts `!` rows - which is what makes this safe to add to a format other
software already parses. `folder_summary_line()` carries that reasoning;
`folder_totals()` is one pass over the rows already in memory, taken before
the write loop because a heading is written before its rows. Both the music
and the film list get the line; the album list stays one line per album.
The stale "framed decoration" note beside the `!rar` writer is corrected.

Tests in `tests/test_each_folder_says_what_it_holds.py` (12): the line's
shape (count, singular, thousands, indented, not a rule, not a request, not
a prefix line); written under every heading in the music and film lists
with the rows following; the album list unchanged; and the claim checked
reader by reader - search still attributes rows to the right folder, the
heading still resolves to the real folder on disk, the request count is
unchanged, and our parser reads a list carrying the line the same way
(which is what an older peer's identical parser does too). Three mutants:
line dropped, line un-indented, and the size put on the heading - the last
also fails the existing resolver tests, which is the point.

### 🌍 Switching language while on the Settings page left the panel stuck in the old one

Found live, right after the settings-help translations shipped: switching
the dashboard's language while looking at Settings changed the sidebar nav
and the page header, and left the category rail and every field's label
and help text sitting in whatever language they were drawn in the moment
Settings was first opened.

`applyTranslations()` redoes every `data-i18n`-tagged element and the
active view's header on a language change - enough for content that is
either static markup or already gets rebuilt every change. It was not
enough for Settings: its rail and fields are built once from
`state.settingsCategories` (`state.settingsLoaded`) and never repainted on
a timer, unlike Downloads, Stats and the notice/message lists, which all
redraw from their own poll loop within a few seconds regardless of
language - so those self-heal almost immediately, and Settings, visited
once, never did on its own. Clicking a category "fixed" it by coincidence:
that handler calls the same two render functions the language change
should have called too.

Fixed by having `applyTranslations()` call `renderSettingsRail()` and
`renderSettingsCategory()` again, guarded on Settings being the current,
already-loaded view. Both read only from state already in hand - no
server request, so nothing here can race an edit in progress the way a
reload would; `renderSettingsCategory()` already restores a dirty field's
typed value from `state.settingsDirty` rather than `field.value`, the same
repaint an ordinary category switch already performs safely.

`tests/test_language_switch_repaints_the_active_view.py` (4, source
inspection like the rest of this page's tests): the repaint calls are
present, guarded on both the active view and `settingsLoaded`, and never
re-fetch from the server. Verified by hand: reverting the fix fails 3 of
the 4 tests.

### 🌐 The settings "?" tooltips speak French and Spanish now

#537 shipped the tooltip mechanism but no translations for it on purpose:
`fieldHelp()` looks up `settings.field.NAME.help` directly in the loaded
dictionary rather than through `t()`, specifically so a language missing
one falls back to the server's own English explanation rather than to a
raw key string - "no dictionary entries are added for it here... until
somebody translates" was always the plan.

All 113 overridable settings now have a French and a Spanish translation,
pulled from the exact text `settings_help.help_text()` currently sends so
nothing drifts from what the English tooltip says. Setting names, file
paths, backtick code spans, protocol keywords (DCC, IRC, CTCP...), product
names and file extensions are left untranslated throughout, matching how
every other settings string on this page already handles them.

`tests/test_dashboard_translations_stay_complete.py`'s cross-language
completeness check now treats a `.help` key as optional per language by
design - that is the whole point of the fallback, so requiring one in
`en.json` (which never needs it) or in every language at once would fight
the mechanism rather than guard it. A new `SettingsHelpKeysAreHonest` check
takes its place for what still has to hold: a `.help` key's stem must name
a real, referenced `settings.field` key, so a typo in a translated key
cannot silently just never match rather than being caught.

One translation needed rewording along the way: `test_no_personal_identifiers_ship.py`'s
scrub flagged "un rejoin instantáneo" in the Spanish text, because its
ASCII-only word-splitting cuts at the accented "á" and the leftover
fragment happened to match a real forbidden identifier's hash - a
coincidence, not a leak (the English source has no name in that sentence
at all), confirmed by checking `settings_help.help_text("REJOIN_ATTEMPTS")`
directly. Reworded to "un rejoin inmediato", same meaning, no collision.

### 🎬 A release travels whole: subtitles, .nfo and .sfv follow their film

Neo's #411, with numbers from a real install: a video-only list published a
"master" file of 3,638 rows - 3,637 `.srt` and one `.nfo` - beside a video
file holding the 3,494 films. `SEPARATE_VIDEO_LIST` decided per file by
extension, so only the `.mkv` was video and everything a release ships
beside it fell into the music list. `defaults.py` had called this "the one
rough edge of deciding per file rather than per folder" since the split was
written.

The rule the user proposed, built: **a scene release is the video plus its
companions**, and they go together. New `LIST_VIDEO_COMPANION_EXTENSIONS`
(`.srt .sub .idx .ass .ssa .vtt .smi .nfo .sfv`) names the companions;
`update_list.belongs_in_video_list(name, folder_has_video, ...)` routes a
companion to the video list **only when its folder holds a video**, so an
album's `.nfo` and `.sfv` stay with its tracks exactly as before.
`folder_has_video` is decided once per folder from the folder's own files
before the per-file loop - not derived as the walk goes, so the answer for
a companion cannot depend on which file came first, and not from
subfolders, so a series folder's own `.nfo` beside season subfolders stays
put and each release folder decides for itself. A companion in a folder
with no video - a stray `.srt` beside an album - stays with the music: the
rule is the folder's, not the extension's.

Chosen over the issue's other two shapes on purpose. Adding subtitles to
`LIST_VIDEO_EXTENSIONS` alone would have left `.nfo`/`.sfv` behind and
put a music release's `.nfo` in the film list. A third `-MISC-` bucket
would change the list-naming convention that other bots' fetchers already
key on (`_pick_list_file()` excludes known markers and takes what is
left) - a cross-bot protocol change for a problem the folder rule solves
without one. The video-only operator's "master" now holds whatever is not
part of a release, which for that install is nothing worth publishing;
whether an empty master should be skipped is #411's remaining question
and is unchanged here.

Registered like its sibling: `webserver.SETTINGS_CATEGORIES` (Your list,
beside `LIST_VIDEO_EXTENSIONS`), `SETTINGS_LABELS`, the
`SETTINGS_FIELD_LABEL_KEYS` table, en/fr/es, `settings.conf.sample`
regenerated, `docs/INSTALL.md`, and `test_commands`'s NOT_PRESERVED list
(a setting, re-read on a rehash, like the other extension sets).

Tests in `tests/test_master_list_generation.py`, `AReleaseTravelsWhole`
(9): a release's `.srt`/`.nfo`/`.sfv` follow its `.mkv`; an album's `.nfo`
and `.sfv` stay with the album; a stray subtitle with no video stays with
the music; only the folder's own files count (a season subfolder's video
does not pull the parent's `.nfo`); a mixed album-and-video folder sends
its companions with the video - stated so the choice is visible; with the
split off nothing moves; the operator can change the companion set; the
film list's header count includes the companions; and the predicate in
isolation, including case. The old "a film's subtitles land in the music
list" docstring is updated.

### 📺 The console feed: requests, queue, starts, resumes, searches - with tickboxes

Part one of #528. "dccore sends but I can't know until I look at the stats
in the browser" - an OmenServe operator sees every request, send and served
search live in mIRC, and the admin console here carried only the end of the
story: `Sent:` and, since #526, `Failed:`. A request, a slot being taken, a
resume and a served search were `print()` to the bot's own window and
nothing else.

**One line per event now**, through `send_debug()` with a category of its
own, so the two sinks render `[CATEGORY] text` exactly as they already do:

- `REQUEST` - `dave asked for "Song.flac"`, from `handle_download_request()`
  once the file is found and before the send-or-queue decision, so every
  accepted request reports once whichever way it goes; the `!rar` path's
  existing INFO line is re-tagged REQUEST.
- `QUEUED` - `Queued "file" for dave at #2 (3/3 slots busy)`, from
  `send_dcc_queue_notice()`, which both request paths already call.
- `SENDING` - `Sending "file" to dave (slot 2/3)`, from
  `send_dcc_sending_notice()`, which every dispatch path already calls the
  moment a slot is taken - one site, no copy per path.
- `RESUMED` - `Resumed "file" for dave at 1.0GB of 1.5GB`, after the accept
  in `handle_resume_request()`.
- `SEARCH` - `dave searched "metal" - 12 results`, from `execute_search()`,
  hits or none; `total_matches`, not the `MAX_SEARCH_RESULTS`-capped count.
- `SENT` and `FAIL` as before.

**Settings → Console feed** (new category `console-feed`): five `bool`
tickboxes - `CONSOLE_SHOW_REQUESTS`, `_QUEUE`, `_SENDS` (starting, resuming
and completing), `_FAILURES`, `_SEARCHES` - all on by default, plus
`DEBUG_CHANNEL_FEED`. The policy is two pure functions in `announce.py`:
`console_wants(category)` (that category's tickbox, or True for any
category the table does not name) and `channel_wants(category)` (the feed's
own five categories only under `DEBUG_CHANNEL_FEED`; everything else as
before). `send_debug()` applies both on top of `DEBUG_TO_CHANNEL` /
`DEBUG_TO_CONSOLE`.

Two decisions worth stating:

- **An unticked kind is declined, not undelivered.** The stdout floor ("no
  line is ever lost") fires only for a line the operator wanted and nobody
  took - an unticked feed line falls nowhere. Without that, unticking
  requests would have moved them from the console to the bot's window.
- **The issue's `_PRESENCE` and `_BANS` boxes are not here.** `PART`, `JOIN`
  and `QUIT` are used across dcc.py and irc.py as "bad news / good news"
  tags for config errors, pack failures and rejoins as much as for
  presence, so a presence tickbox over them would silence errors. Those
  categories, and the ban ones, are never muted. A clean-up of the category
  vocabulary would have to come first; separate.

The IRC debug channel: `Sent:`/`Failed:` keep going there under
`DEBUG_TO_CHANNEL`; the five new categories only with `DEBUG_CHANNEL_FEED`,
which ships off - a channel line costs a `MSG_DELAY` slot on the pacer the
adverts and resume replies share (the #527 lesson), and the console costs
nothing. On the channel the five render `[REQUEST]` etc. in the `[SENT]`
block colour.

Registered everywhere a setting has to be: `defaults.py` (with the
explanations the "?" of #537 will show), `webserver.SETTINGS_CATEGORIES`
and `SETTINGS_LABELS`, the `SETTINGS_CATEGORY_LABEL_KEYS` /
`SETTINGS_FIELD_LABEL_KEYS` tables from #533, en/fr/es dictionaries (7 keys
each), `settings.conf.sample` regenerated, `docs/ADMIN-CONSOLE.md` gains
"The transfer feed".

Tests in `tests/test_the_console_feed.py` (26): the policy on a bare config
(every feed category has a switch, the switches are real settings shipping
on, ticked/unticked, SENDS covers three categories, case, a category outside
the feed is never muted, a missing switch means on - a pre-#528
settings.conf must not go dark, new events off the channel by default and
on with the switch, SENT/FAIL/others unaffected); `send_debug()` end to end
with a sink, the channel queue and stdout all watched (console-not-channel
by default, both with the switch, unticked = nowhere and silent, unticked
SENT still on the channel, the floor still fires for a wanted line nobody
took, non-feed untouched, channel tag present); the SENDING and QUEUED lines
with slot figures; source-anchored placement of REQUEST (after the file is
found, before the decision), the folder request, RESUMED (after the accept)
and SEARCH (total not capped, before the hits check); and the Settings
payload's `console-feed` category with six `bool` fields. Five mutants of
the routing - tickbox ignored, channel switch ignored, declined line
floored, SENT treated as feed-only, missing switch meaning off - each fail.

### ❓ A "?" beside every setting

Part two of #528: "a lot of settings are not easy to understand". The
explanation for every one of them already existed - the comment block beside
it in `defaults.py`, which `scripts/gen_settings_sample.py` has parsed into
`settings.conf.sample` since the sample was first generated. It was one file
away from the page that needed it.

- The parser moved out of the generator into **`settings_help.py`**
  (`assignment_parts()`, `doc_lines()`, `parse_help()`, `help_lines()`,
  `help_text()`); the generator imports it, so the sample is byte-identical
  and there is exactly one copy of the rule. `help_lines()` caches on the
  file's mtime and size, so a rehash after editing `defaults.py` shows the
  new text and every other call is a dict lookup; an unreadable
  `defaults.py` means no "?" rather than a page that fails to load.
- **`/api/settings` sends `help` per field** - the comment lines joined into
  paragraphs (a blank comment line is a paragraph break). A setting with
  nothing to say gets no key rather than an empty string.
- **The page draws a "?" after the label** (`settingsHelpHtml()`), shown on
  hover and on keyboard focus (`tabindex="0"`, a visually-hidden "What this
  setting does" label in all three languages). The text is rendered as an
  element's text content, never a `title=`: `escapeHtml()` encodes text,
  not attributes, and a comment with a quote in it would otherwise close
  one. Capped at 420 px wide and 60 vh tall - the longest explanation
  (`LIST_IGNORED_EXTENSIONS`) is 2.2 KB.
- A dictionary may translate a setting's help under its label key plus
  `.help` (`settings.field.MAX_DCC_SLOTS.help`), resolved through Neo's
  `SETTINGS_FIELD_LABEL_KEYS` from #533 and looked up directly, since `t()`
  answers a missing key with the key; a missing translation shows the
  server's English, the same fallback `fieldLabel()` uses. No dictionary
  entries are added for it here - one source, until somebody translates.
- **Twenty settings had no comment at all** - `PORT`, `CHANNEL`,
  `ADMIN_NICK`, `ALT_NICKNAME`, `DEBUG_MODE`, `DEBUG_TO_CONSOLE`, the DCC
  port range, four data-file paths, `LIST_RAWBYTES_FILE`,
  `PRIVATE_MESSAGE_DECLINE_BURST_SECONDS`, `WEBUI_PORT` and all six
  `CUSTOM_THEME_*` roles. Each has an inline comment now, written from what
  the code does with it (`ADMIN_NICK` is the nick list `is_admin()` checks
  for `!ban`/`!rehash`/`!update`; the DCC console additionally checks
  `ADMIN_HOSTMASKS`), and `settings.conf.sample` is regenerated with them.

Tests in `tests/test_every_setting_explains_itself.py` (26): the parser's
rules on small sources (block, inline, both, a `#` inside a string value,
a section rule ending the block, annotated and plain assignments, a
multi-line value taking no inline comment); paragraph joining; against the
real file - **every overridable setting has an explanation** (the guard
that keeps the twenty from growing back), the cache follows the file, an
unreadable file is empty not fatal; the generator imports the shared
parser and has no copy; every field in `build_settings_payload()` carries
help matching `settings_help`; and the page's source - the mark follows the
label, the text is text content with no `title=`/`data-help=`, no mark
without help, translation before server text, keyboard reachable, the key
in all three dictionaries, CSS on both `:hover` and `:focus`. Exercised
through the real Flask app behind the login: 106 of 106 fields carry help.

### ⏱️ Auto re-fetch fired before the bot had joined anything

Follow-up to the previous entry, surfaced by fixing it: once the sweep
could actually get past its own input validation, it turned out to run far
too early.

`list_fetch.auto_refetch_worker()` is started from `oserve.startup()` and
calls `refetch_due_lists()` as its very first action, synchronously, with
no gate on connection or channel-join state at all. Seen live: the fetch
request was queued and dispatched (via `oserve.queue_message()` into
`queue_mgr.config.send_queue`, drained as soon as a raw socket exists)
several seconds before the server confirmed registration, and roughly 20+
seconds before any channel was actually joined.

`webserver.build_list_fetch_enqueue_result()`'s own presence check,
`bot_not_here_error()`, treats an empty `channel_users` as "unknown" rather
than "absent" - correct in general, since a bot mid-JOIN should not be
judged gone (see `test_we_do_not_ask_a_bot_that_is_not_there.py`'s own
docstring on that distinction) - so it never refused at cold startup, when
`channel_users` is *always* empty. The dispatcher then built its `PRIVMSG`
using `dcc.channel_containing_user(bot) or default_channel`, and since we
were not in any channel yet, fell back to whatever channel is first in
`config.CHANNEL` - one we had not joined either. The message most likely
never reached the target bot - dropped pre-registration by the server, or
landed in a channel we were not yet a member of - and the request just
timed out as "no response", indistinguishable in the log from the target
bot genuinely being unreachable.

Now: `refetch_due_lists()` refuses outright (returns `[]`) while
`config.bot_joined_channel` is not yet `True` - the same gate `dcc.py`'s
own presence decisions already use, and for the identical reason.
`irc.delayed_activate()` runs the sweep once, in its own thread, right
after that flag is claimed - mirroring `dcc.wake_restored_queues()`'s
activation hook from #531 - so a fresh start does not otherwise wait up to
an hour (the worker's own sweep interval) for its first real attempt.

A new test, `test_nothing_is_asked_before_the_bot_has_joined_anything`,
pins the guard; `AskingAgainWithoutBeingAsked`'s existing tests now set
`bot_joined_channel=True` in `setUp()` as the steady state they were always
meant to represent. Three source-inspection tests in a new
`ActivationWakesTheSweepToo` class check the activation hook the same way
the equivalent `dcc.wake_restored_queues` hook is already checked: it runs
after channel sync is claimed, only when the feature is switched on, and in
its own thread rather than blocking activation. Verified by hand: reverting
either the guard or the hook fails exactly the tests meant to catch it.

### 🔁 Auto re-fetch called its own validator with the wrong shape

Seen live, with `AUTO_REFETCH_LISTS` on: `[LIST-FETCH] Did not re-ask
SomeBot: 'bot' must be a string.` - for every single bot the sweep ever
tried, regardless of which one.

`list_fetch.refetch_due_lists()` called
`webserver.build_list_fetch_enqueue_result({"bot": bot})` - a dict - where
the function's own signature and docstring, and the real HTTP route that
calls it correctly (`build_list_fetch_enqueue_result(body.get("bot", ""))`),
both want the bot nick **itself**. `reject_if_unsafe_for_irc_line()`'s
`isinstance(value, str)` check failed on that dict every time, so the
feature never got past its own input validation - `bot_not_here_error()`,
the outstanding-request check and the actual enqueue were all unreachable.
Harmless (nothing crashed, nothing corrupted) but `AUTO_REFETCH_LISTS` has
likely never actually re-fetched a single list since this call site was
written.

Fixed by passing the nick directly. Two existing tests in
`tests/test_list_freshness.py` had asserted the broken `[{"bot": ...}]`
call shape as if it were correct, so neither ever caught this; both are
corrected to the real shape, and their `started` assertions (what the sweep
actually got the shared enqueue to accept) are corrected too - one had
`started == []` for the wrong reason (validation failure) rather than the
right one that fixture actually represents (presence unknown, which never
refuses - see `test_we_do_not_ask_a_bot_that_is_not_there.py`). A new test,
`test_a_reachable_bot_is_actually_re_fetched`, gives the sweep a bot
`channel_users` can actually see and asserts the fetch is accepted
end-to-end, which neither existing test's fixture could ever have shown
either way. Verified by hand: reverting the fix fails all three.

### 🚦 The VIP lane gets one slot per pass, not one in N+1

Reported by Neo, live (#527): "when you get spammed with requests, the
`Sent:` doesn't send to the channels before the queue is empty - while
channel announce works and debug messages."

`queue_mgr.queue_worker()` drained one VIP line per pass and then one line
for EVERY user with a backlog in `send_queue`. Each line costs a `MSG_DELAY`
slot on the shared pacer - five seconds by default - so with N users
spamming, a pass was 1 VIP line + N standard lines and the VIP lane, where
`Sent:`, the advert and `Sending:` live, got one slot in N+1: with ten
spammers, one line every 55 seconds. The debug drain has its own thread
and takes pacer slots independently, which is why debug lines kept coming.
This was #426 over-corrected - that fix removed the `continue` that let
VIP starve the standard lane completely, and what replaced it turned the
starvation round the other way under load.

Now strict alternation: **one VIP line, one standard line, per pass**. The
standard lane rotates through users across passes with a cursor
(`next_standard_line()`, a pure function: the user after the one served
last, wrapping round) instead of serving every user within one pass. VIP is
never more than two slots away whatever the load; per-user fairness in the
standard lane is kept over time. Total throughput is the pacer's either
way - only the share changes, and only while VIP has a backlog.

One thing the first draft got wrong, caught by its own rotation test: a
user whose last line went out was deleted on the spot, and the cursor -
their name - then had nowhere to stand, so the next pass started from the
top and served the first users twice before the last was served once. An
emptied user now keeps their (empty) entry until the cursor next passes
it, one rotation later, and is tidied away then.

Tests in `tests/test_the_vip_lane_gets_one_slot_per_pass.py` (17): the
cursor's rotation, wrap, gone-user and tidy rules; against the real worker
thread - a `Sent:` line arriving with ten users' backlogs in flight leaves
within two slots, the standard lane still runs under a 500-line VIP
backlog (#426's property, kept), every window of five sends reaches five
different users, and a user added mid-run takes the next turn. Three
mutants - the old every-user loop restored, the cursor never advancing, the
cursor always starting at the top - each fail. `test_a_shared_outbound_pace`
unchanged and green.

### 🔓 The admin console stays open until the operator closes it

Reported by the user from early versions: the DCC CHAT console dropped after
a while with nothing wrong - `IDLE_TIMEOUT` closed any authenticated session
that had been quiet for thirty minutes. But quiet is the console's normal
state: an operator opens it to *watch* - the `Sent:` and `Failed:` feed, the
joins and parts - and types something only when there is a reason to.
Thirty minutes of nothing to do meant a closed window and a fresh login.

`IDLE_TIMEOUT` is removed, not raised - a constant that exists gets tuned
back in. `Session.expired()` now only ever fires for a session that has
not authenticated within `AUTH_TIMEOUT` (60 s), which is the clock that
matters for security. An authenticated console ends when the operator
closes it, when a second login takes it over (`_promote()`), or when the
connection itself dies - `apply_keepalive(idle=60, interval=15, count=4)`
on the socket notices a dead peer within about two minutes, so nothing
lingers.

Tests: an authenticated session quiet for zero seconds, thirty-one minutes
and a week is not expired; the constant is gone (`hasattr` guard, so it
cannot come back quietly). Two comments and one test that cited the 1800 s
lifetime are reworded. `docs/ADMIN-CONSOLE.md`'s limits table updated.

### 🧊 The queue sweep could not see a PM requester, and never let one go

Found on the user's bot (#530): one nick with 65 files QUEUED, 0 of 3 slots
busy, for days - across a restart, while 38 files went to other people. The
head row had `send_fails: 2` and `"channel": "<the bot's own nick>"`.

That channel value is the whole story. A request made by private message
records the PRIVMSG target as the row's channel, and for a PM that is the bot.
`check_queue_and_send()` has two ways of deciding whether a waiting user is
present. The specific-user branch - a fresh request, a completion for that
user, a JOIN thaw - asks every channel the bot is in. The global sweep
(section B, the path every OTHER completion and every `!rehash` take) built
`channels_to_check` from the row and looked only there. `channel_users` has
no key for a nick, so a PM-originated head row was invisible to the sweep
whichever channel the user was sitting in.

On its own that would have meant "PM requests are only served by their own
trigger". Two more things made it permanent. The sweep answered "not
present" with a silent `continue`, where the specific-user branch freezes the
user and starts the five-minute countdown - so no freeze, no timer, no expiry,
no log line. And the JOIN handler wakes only users who are FROZEN
(`frozen_queues` is in-memory), so after the restart even a user who came and
went was never looked at again. Nothing was ever going to touch that queue.

- **The sweep asks every channel we are in**, via the same
  `user_is_present_in_ram()` the specific-user branch and the stale-freeze
  sweep already use. The row's channel is where to *announce*
  (`announce_channel_for()`), not where to *look*; the two questions had
  been separated once already (#272) and this is the half that was still
  conflated.
- **The sweep freezes an absent user** instead of skipping them. The
  countdown moved out of the specific-user branch into
  `freeze_absent_user()` so both paths apply one policy: not while the bot
  is unsynced, one countdown per user, announced to the debug channel as
  `QUIT`. Absent users are collected under `queue_lock` and frozen after it
  is released, because the freeze announces and `send_debug()` paces.
- **One sweep on activation.** `wake_restored_queues()` runs the global
  sweep once per slot when `delayed_activate` claims channel sync, so a
  queue restored from `dcc_queue.txt` is evaluated - served if present,
  frozen if not - without waiting for unrelated traffic. Once per slot
  because a single pass dispatches one user and breaks.
- **A nick is not a place to announce.** `announce_channel_for()` handed
  back whatever string the row carried, so the `Sent:` line for a PM request
  went out as `PRIVMSG <our nick> :Sent ...` - the bot telling itself. A
  value with no channel prefix (`is_channel_name()`, RFC 2812's four) now
  falls back to the default channel like a missing one does.

Tests in `tests/test_the_sweep_could_not_see_a_pm_requester.py` (23): a PM
row for a present user is dispatched by the sweep with the announce target a
channel; an absent user is frozen by it, the queue kept, no second countdown,
gone after five minutes via the existing reaper, nothing frozen while
unsynced, the freeze announced outside `queue_lock` (probed with a
non-blocking acquire); activation serves up to the slot count and respects
the quiesce gate; the specific-user branch still calls the shared helper.
Six mutants checked - each fix reverted in turn fails its tests, and moving
the freeze back under the lock deadlocks, which is the point of the probe.
`test_announce_target_is_one_channel.py`'s membership anchor is updated
with its intent kept: it guarded against narrowing presence to one channel,
and now guards that the row's channel is not consulted at all.

Also: the comment above `_ended` in `start_dcc_send()` still said
`transfer_finished_at` was "set the instant the last byte went out" - Neo's
review note on #529. It is the final ack now, and the comment says so.

Not in this change, filed on the issue: `MAX_SEND_FAILS` is per ROW. A user
whose client cannot accept DCC at all - as this one's evidently could not,
two accept timeouts on the head - would, now that the sweep can reach them,
burn three attempts per row on each of 65 rows, holding a slot for hours. A
per-user consecutive-failure limit would end that in minutes.

### 🟢 The dashboard speaks English, French or Spanish

Scope decided on issue #69: the dashboard translates, the Console and the
debug channel deliberately do not - both show the same lines the daemon's
own log does, and translating those means touching every `print()` call
site across the daemon rather than this one static page. Private replies to
a user (`-help`, `-que`, `-stats`) and everything the bot says in a channel
stay English unconditionally too, for the reason already recorded there:
other bots parse those lines as a de facto protocol.

**Client-only, like the dark/light theme.** Which language suits an
operator is a fact about the person looking at the dashboard, not about the
bot, so the choice lives in `localStorage` and nothing about it reaches
`settings.conf` or the server. A first visit reads the browser's own
language and falls back to English for anything the three dictionaries do
not cover.

**Three flat JSON dictionaries** - `web/lang/en.json`, `fr.json`, `es.json`
- fetched at load through Flask's existing static-file serving
(`create_app()`'s `static_folder="web"`), so no new route was needed and
the files sit behind the same login gate as everything else (verified: an
unauthenticated request 302s to `/login`, same as any other page).

**A key a language is missing falls back to English rather than showing
nothing** - the only failure mode a viewer in that language should ever
see. A key neither dictionary defines renders as the literal key string,
which is loud on purpose: `tests/test_dashboard_translations_stay_complete.py`
exists so that never has to happen silently. It checks, both directions:
every key the page actually references - `index.html`'s `data-i18n`,
`data-i18n-html`, `data-i18n-placeholder` and `data-i18n-title`
attributes, and every key-shaped string literal in `app.js` (a literal
`t("...")` call, an object literal's values looked up dynamically such as
`DOWNLOAD_STATE_LABELS`/`STATUS_LABELS`, or a key assigned to a local
variable through a ternary before being passed to `t(theVariable)`) -
exists in `en.json`; every key `en.json` defines is referenced by
something; and all three languages define exactly the same set of keys, so
nothing quietly stays English for one language and not the others.

**The nav label and the page heading share one translation key per view**,
not two coincidentally-equal strings - `tests/test_dashboard_nav_and_multiselect.py`'s
existing guard against the two drifting apart (written when both were still
literal English) is adapted rather than dropped, and now checks the
identifier match instead.

**Landed in two passes.** The first covered the navigation rail, every
page's heading, the sidebar chrome and the Search view's own controls - 29
keys. The second covers everything else the dashboard says: Downloads, List
Browser, Tools, Settings and Stats - 321 more keys, 350 in total, in all
three languages. The Console, private replies and channel output remain
English throughout, by design rather than by omission.

Two low-severity findings from review are fixed in the second pass:
`loadLanguage()` now guards every callback with a generation counter, so two
overlapping language changes - or a change back to English while another
language's fetch is still in flight - can no longer let a stale reply
overwrite a newer one; and `document.documentElement.lang` now follows the
picker, so a screen reader and the browser's own "translate this page"
prompt agree with what is actually on screen.

**Known seam, left for a follow-up:** content a `render*()` function builds
through `t()` - rather than walked by `applyTranslations()` via a
`data-i18n*` attribute - keeps its old language until the view that built
it reloads. Most of the dashboard self-heals within a few seconds because
it polls; the Settings pane and the List Browser cache once behind a
`*Loaded` flag and do not, so a language changed while looking at either
stays partly English until you navigate away and back.

### 🌐 The dashboard's translation had real gaps - Settings and Downloads

Found by testing the live French/Spanish dashboard: Settings showed nothing
but English - every category in the sidebar, every field label on the right
- and Downloads had two literal English strings the original sweep should
have caught.

**Two genuine misses.** "Redownload" and "Browse it in List Browser" in the
Downloads table were never wired to `t()` at all - plain oversights in the
first pass.

**Settings is a different case.** `category.label` and `field.label` arrive
from the server (`SETTINGS_CATEGORIES`/`SETTINGS_LABELS` in `webserver.py`),
because the setting schema lives there, not in this page - so unlike
everything else the dashboard translates, there was no `data-i18n` attribute
or `t()` call already sitting on this text at its source, and the "rest of
the page" pass never gave it one. That was a deliberate, documented scope
decision at the time; testing the actual page showed it left the single
largest visible area of the dashboard entirely English.

Two client-side lookup tables translate it anyway, on the same
resilient-fallback shape every other lookup on this page already uses:
`SETTINGS_CATEGORY_LABEL_KEYS` (13 entries, keyed by category id) and
`SETTINGS_FIELD_LABEL_KEYS` (106 entries, keyed by the exact setting NAME
the server uses - `SERVER`, `MAX_DCC_SLOTS`, and so on - copied verbatim
from `SETTINGS_LABELS` so the two stay directly cross-referenceable). A
category or setting this page does not recognise - one just added to
`config.py`, before a translator has caught up - simply keeps showing the
server's own English text. The one dynamic note (`WEBUI_CONSOLE_ENABLED`'s
"Currently ON/OFF") is recognised by its exact suffix and rebuilt from a
template, falling back to the server's literal text if that suffix ever
changes.

127 new keys per language (125 settings + the two Downloads strings), 432
total, still identical across en/fr/es and still passing the existing
coverage guard in both directions - which needed a fix of its own:
`JS_KEY_SHAPED_STRING` never allowed an underscore in a key segment, so it
silently stopped matching after the first dotted component containing one -
true of every field key with more than one word in its name
(`settings.field.MAX_DCC_SLOTS` and 100 of its 106 neighbours). Verified the
widened shape against the whole file on both sides before and after.

### 📬 Complete means the receiver acknowledged it

Found by a second operator running DCCore on Windows, reproduced by the user
with mIRC against that bot (#526). The channel announced a 2.7 MB list zip
as sent - counted, credited, `Speed: n/a (<1s)` - eleven seconds after the
receiver had reported it incomplete at 1.2 MB after 24 seconds. A resume of
the remaining 1.5 MB then counted as a second whole file. The same operator
reported transfers that start fast and stall, and that a 64 KB
`DCC_SEND_BUFFER` was "very unstable" while 4096 was better.

One cause. The DCC SEND protocol has the receiver send back a 4-byte
big-endian running total after every packet, and that total is the ONLY
signal of what arrived. `start_dcc_send()` never read it - not one `recv()`
on the data socket in the whole send path. "Complete" was
`bytes_sent >= file_size`, where `bytes_sent` counted what `sendall()` had
handed to the kernel, and `sendall()` returns the moment the kernel's send
buffer accepts the bytes. That buffer is 4 MB on Windows by default, bigger
than most files. So the bot declared success at t~0, stopped its clock
there (a memcpy's speed, hence `n/a (<1s)`), counted the file, slept 1.5
seconds and closed the socket - with megabytes still queued behind a link
doing 50 KB/s. The receiver was cut off at whatever the wire had managed.

And the operator's workaround explained: `DCC_SEND_BUFFER = 4096` "worked"
because a tiny buffer makes `sendall()` block on the actual wire, so the loop
tracked delivery by accident. The bigger the buffer, the earlier the bot hung
up.

Now:

- The receiver's acks are read throughout the transfer (`_AckTracker`, fed
  by a non-blocking `select()` drain after every write) and **completion is
  the final ack equalling the file size**. After the last write the bot
  waits for that ack, bounded by progress rather than a fixed clock: a slow
  link that is still advancing is allowed to finish; one that has not
  advanced for `ACK_STALL_SECONDS` (60) is dead whether or not the kernel
  still holds bytes. The 1.5-second "let mIRC close its file" sleep is gone,
  and so is the 0.5-second one in the finally "to let the final acknowledgement
  flush" - both were pauses standing in for the ack nothing read, and both
  only held a DCC slot.
- The clock stops at the final ack. That is the transfer, and it is what the
  speed record, the advert and the counters are a record of.
- `[DCC-SUCCESS]`, the totals, the download counter, the speed record and
  the channel announce all wait for it. A transfer whose final ack never
  comes is a `[DCC-FAIL]` and is not counted (#454's rule, on the signal
  that actually means delivery).
- Draining INSIDE the loop, not only after it, is load-bearing: acks are 4
  bytes per packet, and left unread a 4 GB file's worth fills the bot's
  receive buffer and then the receiver's send buffer, at which point mIRC
  blocks writing an ack, stops reading, and the transfer deadlocks about a
  gigabyte in. Pinned by a test that counts one drain per block.
- The 32-bit counter is tracked unwrapped with serial-number arithmetic: a
  drop of more than 2**31 is the receiver past 4 GB wrapping; a smaller one
  is a stale or duplicated word and is ignored, because a cumulative total
  never goes backwards. The first draft treated every drop as a wrap and
  would have added 4 GB to a duplicate ack - a mutation caught it.
- A resumed transfer completes on absolute acks, which is what mIRC sends
  after a resume, so the same comparison against `file_size` holds.

**Failures are now reported where successes are.** Every `[DCC-FAIL]` was a
plain `print()` - the console window and nothing else - while a completed
transfer went to the debug channel and the admin console as `Sent:`. An
operator watching either saw every success and no failure, and a cut-off
transfer the old code miscounted as a success was reported as one. One
reporter, `_report_transfer_failure()`, now prints the log line AND sends a
`FAIL`-category debug line (`Failed: "file" to nick - <reason>`), rendered in
the alert colour beside `[SENT]`. Every failure site goes through it,
including the two new ones (receiver hung up mid-transfer; receiver stopped
acknowledging) and the old socket timeout, whose message said "no data
acknowledged" for a check that never read acknowledgements - it now says
what it measures, the send blocking.

The end-to-end fixture in `tests/test_dcc_resume_end_to_end.py` read until
EOF and never acked - the same blind spot as the sender, which is how the
bug survived its own tests. It acks now, as a real client does, and its six
cases pass in 3 seconds instead of 103: completion is the ack rather than a
receiver timeout plus a sleep.

19 tests in `tests/test_complete_means_the_receiver_acked_it.py`: the tracker
in isolation (split words, backwards words, the 4 GB wrap, resume seeding,
stall clock), and the bot against a real loopback receiver that acks
normally, slowly, not at all, stops mid-file, hangs up early, and resumes.
Eleven mutations run, all caught: completion ignoring acks again, the final
wait returning at once, the settling sleep restored, in-loop draining
removed, stall detection removed, every drop treated as a wrap, wrap removed,
`received_any` never set, a partial word dropped between reads, failures
reported as INFO, and the FAIL tag unrendered.

Needs a real mIRC download against a real bot before release - this is the
transfer loop.

### 🔴 The console timestamp proxy took the dashboard down on the live server

Found live, immediately after updating: the daemon connected to IRC and
served files normally, but the web dashboard printed one line and stopped -
`[WEBUI] Dashboard stopped: a bytes-like object is required, not 'str'`.

The new console-timestamp proxy's `write()` answered `if not text: return 0`
for any falsy argument - a string OR bytes - before anything checked what
`text` actually was. Flask's CLI banner prints through `click.echo()`, and
click decides whether a stream takes `str` or `bytes` by probing it with
`stream.write(b"")`. Against this proxy that probe "succeeded" - it never
reached the code that would have noticed `b""` is not a string - so click
concluded the stream was binary, wrapped it in its own encoder, and fed
every later write here as encoded bytes instead of text. The first real
line printed after that (the Flask banner itself) failed inside the
proxy's own line-splitting.

A real text-mode stream raises `TypeError` for `stream.write(b"")` too, so
matching that exactly is both the fix and the property worth pinning -
"handle bytes gracefully" would have made the proxy correct in a way
click's probe still cannot see. `write()` now rejects anything that is not
a `str` before the empty-input check, the same way `io.TextIOWrapper`
would. Four new tests, including one that drives click's actual probe
function against the proxy directly, and a control that checks a real
`TextIOWrapper` agrees.

### 🧠 Resolving a request streams the list; it no longer loads it

Found on the same 5.4-million-file install as the count fix above, by looking
at the daemon's memory rather than its log: **1.5 GB resident, 5.9 GB peak**.

`dcc.handle_download_request()` turns a bare `!<nick> Some Track.flac` into a
path on disk by finding the row in the published list and reading the folder
heading above it. It did that by `readlines()`-ing every published list into
one Python list of strings and then, on a match, walking BACKWARDS through it
to the nearest heading. The whole list in memory existed for that backward
walk and nothing else. On this install that is 460 MB of text as ~5.9 GB of
`str` objects - and it ran on EVERY file request, because the direct check
before it is `<first folder>/<name>` and a track is never in a folder's root.
Freed afterwards, but the allocator keeps its arenas: 1.5 GB held for
nothing. Three busy slots could mean three of those at once.

Headings precede their rows, so "the nearest heading above the matching row"
is simply the last heading seen on the way down. The lookup now carries that
in one variable and holds nothing: one generator across every list in order,
so a `break` leaves the lookup exactly as it left the old single loop over
the concatenation, and the heading state carries across the file boundary the
way the concatenation carried it. The heading is still resolved lazily, on a
match only, so a miss costs what it cost before minus the memory. Which
folder, which spelling (#445), which copy under a size hint - all unchanged,
and `tests/test_download_resolution.py` pins every one of them already.

`tests/test_a_request_does_not_load_the_whole_list.py` pins the memory with
`tracemalloc`: a request against a 4 MB list with the wanted row LAST must
allocate less than a fifth of the file's size - generous for a stream, and
impossible for a copy, whose `str` overhead alone exceeds the file. Six
mutations run, all caught, including materialising the generator back into a
list (the old profile, and the tracemalloc test is what catches it), never
remembering a heading, the first heading winning instead of the nearest, a
bare request scanning on past its first match, and a size hint never winning.

### 📊 The file count is computed once per build, not once per caller

Found on a live install with 5.4 million files - a 460 MB list. The operator
opened the Stats page, saw every card sitting on a dash, and assumed it was
broken. It was counting.

`list.get_file_count_date_size_and_raw_bytes()` answers "how many files do I
share" by reading every published list end to end and counting the lines that
start with `!`. On that install: 2.4 seconds warm, considerably more cold
after a restart. And it is asked constantly - every advert cycle, every Stats
page load, every `-que` from somebody with nothing queued, the admin console's
`status` - each caller paying the full read again, for a number that cannot
change between one `!update` and the next. commands.py:140 had already noticed
the cost and dodged it in one branch rather than fixing it.

The counting loop is now `count_request_lines(paths)`, cached on each list
file's `(path, mtime_ns, size)`. `update_list.py` publishes with
`os.replace()`, which gives the new list a new mtime and (almost always) a new
size, so the first call after a rebuild misses, recounts once, and every call
until the next rebuild is free. Measured on the same install: first call
2.41s, second 0.04ms. Nothing is invalidated by hand - the file on disk is the
truth and the key is the file on disk - so `!rehash` needs no special case and
neither does a list that vanishes. A lock makes concurrent callers share one
read rather than each walking 460 MB.

Only a COMPLETE count is kept. The #433 case is a list that `stat()`s fine but
will not open - an AV scanner holding the VIDEO list on Windows - and that
leaves the signature unchanged when the scanner lets go. Caching the short
count under it would have served that number until the next `!update`; the
uncached code retried on the next call, and so does this.

The lock is `runtime.list_count_lock`, not constructed in list.py -
`tests/test_no_reloaded_module_owns_a_lock.py` caught the first draft doing
exactly that: list.py is reloaded by `!rehash`, and a `Lock()` built there is
a new object after every reload while a caller mid-count still holds the old
one. The cache dict itself stays in list.py on purpose; rebinding it on reload
costs one recount, which is harmless.

15 tests in `tests/test_the_list_is_counted_once_per_build.py`. Eight
mutations run, all caught, including keying on the path alone, on mtime
alone, and on size alone (a same-size rebuild with a different count - one
long filename gone, two short ones added - is what the mtime half of the key
is for, and it took a test that builds exactly that file to pin it), and
caching a short count.

### 🕒 Every console line says when

Reported live, in the same session as the setup-check fix. Four channels
failed to confirm at startup and the operator, reading the console window,
could not tell whether the scheduled retry had fired yet - nothing in the
window said when anything had happened. A log line with no time on it
answers "what" and never "when": whether the bot rejoined a channel on its
own, how long a rebuild took, whether the disconnect came before or after
that transfer.

Every line the daemon prints to its console - or to the file its output is
redirected to - now starts with the time it was written:

    [22:38:02] [CONNECT] Attempting to connect to irc.undernet.org:6667 ...
    [22:53:11] [REJOIN] Asking to rejoin #example.

`platform_compat.install_console_timestamps()` wraps stdout and stderr in a
proxy that stamps the start of every LINE - not every write. print() hands
the stream its text and its newline as separate calls, and a traceback or a
folder listing arrives as one write holding several lines, so the proxy
tracks whether the last character it saw ended a line and stamps every line
that begins on the stream however the text was split to reach it. Everything
else - encoding, isatty(), fileno(), reconfigure() - is delegated to the real
stream, so the console-encoding guard's reconfigure() still lands where it
did and code inspecting sys.stdout finds what it always found.

It is installed at the same moment as the encoding guard, before config has
loaded, so the two config-loading lines are stamped too; the operator's own
format is applied the line after config exists. The format is read per line
rather than captured at install for exactly that reason.

`CONSOLE_TIMESTAMP_FORMAT` (Debug & logging on the dashboard; strftime; empty
= off) defaults to `%H:%M:%S`, the mIRC convention and enough for a window
watched live. A log kept for days wants `%Y-%m-%d %H:%M:%S`. An invalid
format is refused with a message and the previous one kept, rather than
becoming a ValueError inside every print() for the life of the process.

Only the daemon installs it. `scripts/setup_check.py` and `configure.py` print
a report for a person to read once, not a log, and a stamp on every line of a
report is noise. The dashboard's Console page keeps its own `time` field and
is unaffected. `update_list.py` as a child of `!update` prints unstamped, and
the daemon stamps each relayed line once.

28 tests in `tests/test_every_console_line_says_when.py`. Ten mutations run,
all caught - never stamping, stamping every write rather than every line,
stamping only the first line of a multi-line write, dropping the attribute
delegation, accepting an invalid format, double-wrapping on a second install,
leaving stderr unstamped, installing after config loads, and never applying
the operator's setting.

Two existing tests changed. `tests/test_console_encoding.py`'s stderr
fidelity test imports oserve in a child and compared stderr exactly; it now
strips the stamp first and compares the rest exactly, rather than weakening
to a substring check. And `settings.conf.sample` is generated from
`defaults.py` by `scripts/gen_settings_sample.py` - the first draft hand-wrote
the entry, and `test_the_committed_sample_matches_what_the_generator_produces`
caught it, which is what it is for.

## 🟩 v1.12.1 (2026-09-14) - "The Setup Check Catches Up"

### 🗂️ The setup check asked FILE_DIRECTORY; the daemon had stopped asking it

Found on the first upgrade of a real multi-folder install to v1.12.0. The
operator runs twenty library folders across five drives from
`data/library_folders.json`, and `start-dccore.bat check` told them:

    WARN   FILE_DIRECTORY is not set yet - the daemon will start, but cannot
           search or serve anything until it is set ...

which was simply untrue - all twenty were reachable and the daemon served
from them the moment it started.

`scripts/setup_check.py` read `FILE_DIRECTORY` and nothing else. That setting
is only the fallback for an install with no folder list, and the daemon's own
startup check in `oserve.py` had already received exactly this correction -
its comment above `configured = library.folders()` describes the same
complaint word for word. The setup check kept the old rule, and it is the
thing the operator reads first.

The verdict is now `library.folders()`, the daemon's own resolution, with the
daemon's own three outcomes so the check cannot say "ready" for a library the
daemon will refuse, or refuse one it will serve: no folders at all is a WARN
(not chosen yet, not misconfigured); every folder gone is a FAIL (the daemon
exits on it, and so does the list build); some folders gone is a WARN (a
scan-time condition the build already skips). A multi-folder install now
sees each folder listed by name and path with its reachability, and the
"files that would be listed" count covers every folder rather than the one
`FILE_DIRECTORY` named.

Also fixed by the same change: a stale `FILE_DIRECTORY` pointing at a drive
that is no longer there used to make the check FAIL - and the launcher refuse
to start - while the folders the daemon actually serves from sat there
readable. The daemon stopped doing that in #26; the check now agrees.

An install with only `FILE_DIRECTORY` set reads exactly as it did before,
wording included - that path is untouched. The per-folder listing only appears
when a folder file is in use.

The block moved out of `main()` into
`library_report(config, ok, warn, fail, detail)` so every branch is exercised
with a fake config and recording reporters
(`tests/test_the_setup_check_asks_the_library.py`, 16 tests). Before, the only
test that ran the check did so in a child process against the developer's own
checkout, which could reach exactly one branch: whichever that checkout
happened to be in. `detail` - the indented per-folder sub-line - is a reporter
passed in like the other three rather than a `print()` in the function, because
the function sits above `main()`'s console-encoding guard in the file, and
`tests/test_a_filename_your_code_page_cannot_spell.py` rightly forbids a print
ahead of that guard; the first draft tripped it.

Nine mutations run, all caught, including the two that matter most: the
original `FILE_DIRECTORY`-only rule put back, and a stale `FILE_DIRECTORY`
allowed to veto a working folder list. One earlier candidate - walking every
configured folder for the count rather than only the reachable ones - turned
out to be behaviourally equivalent, since `os.walk` on a missing path yields
nothing; its test was removed rather than kept as a test that cannot fail.

One existing assertion changed: `tests/test_check_setup_numeric_sanity.py`
looked for the literal `WARN   FILE_DIRECTORY is not set yet`, which was the
half-truth being fixed. Its sibling - a set-but-missing `FILE_DIRECTORY` is
still a FAIL - passes unchanged, because that path kept its wording.

## 🟩 v1.12.0 (2026-09-14) - "The Several Lists Release"

### 🔴 The resume handshake takes its turn

Found by the pre-publication audit sweep. Closes #453.

Every outbound line waits for a slot on `runtime.outbound_pacer` - the shared
clock added after this bot was disconnected with **Excess Flood** in
production. `!ping` was one exception and was fixed in #425; the `DCC ACCEPT`
that answers a peer's resume request was the other, and went straight onto
the socket.

It is not queued behind the round-robin. A resume handshake is something a
peer is actively waiting on, so it stays immediate - what changes is that it
takes a slot on the same clock instead of ignoring it. The wait is only ever
paid when the bot has just sent something else, and is at most `MSG_DELAY`.

#### And a clock that outlived the test that wound it

`runtime.outbound_pacer` is a process-wide singleton holding "the earliest
moment the next line may leave". A test that sent anything left the next
test's first send blocked for up to `MSG_DELAY` - five seconds by default -
which is leaked state like any other, and wall-clock every suite run paid.
`tests/support.py` now hands each test a fresh clock, which
`test_a_shared_outbound_pace.py` was already doing by hand for itself.

Three mutation-checked properties, no survivors.

### 🟢 Four things the dashboard got wrong quietly

Found by the pre-publication audit sweep. Closes #459, #460, #461 and #462.

Three of the four are the same shape: **a CSS class that matches no rule does
not fail, it does nothing.** The page renders, the operator sees no error, and
the styling simply never arrives - which is why all three sat unnoticed.

  * `foldersSectionHtml()` emitted `served-served-folder-rows`, a doubled
    prefix, so the rule written for `served-folder-rows` never applied and the
    served-folders editor was unstyled.
  * `#import-status` had **no rule at all** - not even base styling - so
    marking a rejected `vars.ini` with `is-error` coloured nothing. The
    operator was told the file was rejected in the same colour as success.
  * The password confirmation was written onto the note and then painted over
    by `loadSettings(true)` on the very next line: the operator changed their
    password and saw nothing confirm it. It is now carried across the repaint
    and applied once the new panel exists.

The fourth is growth rather than silence. The Console pane appended forever -
three elements per log line, nothing ever removed - in the one view an
operator leaves open for hours. The server has capped its own buffer at 500
lines all along; the browser now trims from the front, above that, so it
bounds the pane without dropping history the server still holds.

The `is-error` fix is guarded as a property over every site rather than the
one that was wrong: any element app.js marks as an error must carry a class
that can actually show it. Four mutation-checked properties, no survivors.

### 🔴 Four faults in building a list

Found by the pre-publication audit sweep. Closes #441, #442, #443 and #444.

A rebuild over an 80TB library is the most expensive thing the daemon does and
the least often watched, so a fault here is paid for in hours and noticed
late.

**One unreadable folder aborted every rebuild, for ever.** Any error during
the walk kept the previous index rather than publishing a truncated one -
correct for a subtree that went away mid-scan, and wrong for a folder that
simply cannot be read. A Windows volume root's System Volume Information, a
POSIX lost+found, anything whose ACL excludes the account the daemon runs as:
those fail identically on every future scan, so the list could never be
rebuilt again on that install. Every `!update` ran the whole scan and threw
the result away.

The two are now told apart by whether the path is still there. Permission
denied on a directory that exists is a fact about the ACL; a directory that
has vanished means the library changed underneath the scan and the snapshot
is already wrong. The first is excluded and reported once, with the count and
the reason - an operator whose file count comes up short deserves to know it
is permissions rather than a broken scanner. The second still aborts.

**A failure after the swap said the opposite of what happened.** Everything
between `_publish_artifacts()` and the handler is tail work - side files,
cleanup, bookkeeping - and any of it can raise. The handler then printed "The
previous list was left untouched and is still in use", which is exactly
backwards: the swap had already happened, the new list was live and serving,
and only the tail failed. An operator told nothing changed reasonably
concludes the rebuild can simply be retried.

**The film and series list was never sorted.** `all_files_data` has been
sorted since the beginning; `video_files_data` is built by the same walk and
was only ever appended to, so it came out in whatever order the filesystem
handed the directories over. Both now sort by the same key - a different key
would put the same folder in a different place in each list.

**The `!update` guard read its flag 178 lines before setting it**, with the
`PAUSE_ON_UPDATE` wait for a running search in between. Two requests arriving
in that window both passed the guard and both started a rebuild: two
subprocesses writing the same `.new` temp paths. The check and the set are now
one step under `runtime.list_update_gate` - in `runtime.py`, because
`commands.py` is reloaded by `!rehash` and a lock built there is a fresh
object on the far side of every reload (#235). The one path that returns
between the gate and the work puts the flag back; leaving it raised would deny
every future update for the life of the process.

Five mutation-checked properties, no survivors.
### 🟢 Three documents that were wrong

Found by the pre-publication audit sweep. Closes #448, #449 and #466.

**docs/WINDOWS.md advised something that cannot work.** It told an operator
testing a download from their own machine to pin `MY_IP_OR_DOCK` to the PC's
LAN address. `dcc.is_offerable_to_strangers()` refuses every private,
loopback, link-local and reserved address - an offer carrying one is an offer
to nobody - so the daemon refuses the send outright rather than failing to
connect. The symptom is the bot declining to send at all, which reads as a
different fault entirely. The page now says what actually works, and why the
shortcut does not.

**.gitattributes ships a line that is wrong in the place it lands.** Here,
`docs/UPDATES.md` is the internal changelog and is deliberately
export-ignored. In the PUBLIC repository that same path IS the changelog -
extraction step 3 renames `UPDATES-PUBLIC.md` onto it. Shipped unchanged, the
line tells any `git archive` run in the public repository to drop its own
changelog, and nobody would find out until a release tarball came out without
one. Extraction step 2 now strips it, with the reason attached, and a guard
fails if that step is ever removed or stops saying why.

**The public changelog claimed more than the code does.** "Bold has been
removed from every message the bot sends" is false: `C_BOLD` is still used
thirty-one times across `announce.py`, `commands.py` and `dcc.py`. The
enumeration that followed the sentence was always the accurate part - the
advert, the "Sent:" notice, `@find` results, the private notices and the debug
channel are all bold-free. The claim now matches, and names the replies that
still use bold deliberately.

### 🟢 Three loaders that trusted the file

Found by the pre-publication audit sweep. Closes #450, #451 and #452.

State files are hand-editable by design - the documentation says so - and
every loader in `db.py` filters row by row for that reason: one bad entry
costs that entry and not the rest. Two did not.

`load_dcc_queue()` checked that the top level was a dict and then `update()`d
the file's contents wholesale. A value that was not a list, or a row that was
not a dict, reached `config.dcc_queue` intact and failed later - on a dispatch
thread, far from the file that caused it, with nothing naming the file. It now
filters like its neighbours, says how many entries it dropped, and counts what
it kept rather than what it read.

`load_notices()` kept any row that merely HAD an `id`, while the `seen_id`
marker two lines below was already coerced with a guarded `int()`. So a
hand-edited `"id": "first"` survived the loader and raised wherever ids are
compared - `unread_notices()`, the mark-read marker - taking the whole panel
out rather than the one bad row. Ids are now coerced or the row is dropped,
and `"3"` becomes `3` rather than being thrown away, because hand-edited JSON
quotes numbers all the time.

`save_dcc_queue()` walked `config.dcc_queue` live while holding only
`_disk_lock`, which guards the FILE rather than the dict. It now walks one
copy taken in a single step. `queue_lock` cannot be taken there - five of the
six callers in `dcc.py` are already inside it and it is not reentrant - which
is the same conclusion #432 reached for `get_total_queued_count()`.

#### What could not be shown

The #452 race did not reproduce. Sixty concurrent saves against a queue being
mutated by another thread raised nothing, with the fix and without it: the
prune already snapshotted its keys with `list()`, so only the final
comprehension was ever exposed, and that window is too narrow for a stress
test to land in reliably. The fix is right by inspection and costs nothing,
but its guard reads the source rather than claiming a reproduction nobody got.

### 🟢 What the scan does to every file

Closes #463 and #464.

**Three copies of the library where one is needed.** `generate_master_list()`
built a second full copy - one dict per file - and handed it to
`list.find_duplicate_filenames()`, whose answer was passed to `len()` and
dropped. Re-measured independently rather than taken on trust, and it
reproduces within ~5%: at 5.4M files, 1.25 GiB live after the scan, 2.32 GiB
at the sort's peak, and 3.54 GiB once the copy and the answer were both in
memory.

`count_duplicate_filenames()` answers the same question by the same
definition, keeping one entry per distinct NAME rather than one per row, and
the caller hands it a generator over the scan's own tuples - a materialised
list of pairs would have put back a smaller version of the same mistake.

The definition is the other function's deliberately, and the two are now
pinned against each other on the cases that could separate them: case,
repeated folders, a folderless row, a blank name, a third folder holding the
same name. The test that used to guard this checked that the string
`find_duplicate_filenames(` appeared in `update_list.py`, which is not
agreement.

**The same pass recomputed each directory's path once per file.** `rel_dir`
is a property of the directory; it sat inside the per-file loop, so a folder
of twenty tracks paid twenty `relpath()` calls and twenty `join()` calls for
one answer, and stored twenty separate equal strings. Hoisted, the rows share
one object: 149.9 B/file against 206.9 B/file measured, about 0.29 GiB at
5.4M files.

**And the txt artifact read each member whole.** `_write_text_artifact()` is
only reached when `LIST_FORMAT` is `txt` rather than the default `zip` -
which is exactly the operator most likely to have a list big enough for it to
matter. It copies in chunks now, carrying an overlap so the operator's banner
is still removed when it straddles a boundary, and still only the first
occurrence, which is what `replace(x, "", 1)` meant.

**A backslash in a folder name is not a separator on Linux.** It is an
ordinary filename character there, and unzipping a Windows-made archive
produces one routinely. The list writes headings with backslashes BETWEEN the
components and `list.list_heading_parts()` reads them back by splitting on
both separators - so a directory named `Rock\Metal` is written as
`D:\MEDIA\music\Rock\Metal\` and read back as three components. If
`music/Rock/Metal` exists, every file under the real folder - and its `!rar`
row - resolves into that unrelated album and the requester silently gets the
wrong thing. If it does not, nothing under it can ever be served.

Excluded rather than escaped, and the reason is compatibility: the list
format is read by other bots and by AutoQ, so an escape convention would have
to be understood by readers that already exist and never will be.
`library.problems()` already refuses a backslash in a folder LABEL for
exactly this reason - "the label travels further than the machine that made
it" - so this is the same rule applied to the components below it. The
folders are named in one line at the end of the scan, the way unreadable
folders already are: a file count that looks short deserves an explanation.

Windows cannot reach this bug at all, since the backslash is its separator -
which made it the kind of test that runs on one platform and leaves a hole on
the other. `has_backslash_component()` takes the separator as an argument,
the way `irc.resolve_dcc_address()` takes `lookup`, so both readings are
driven everywhere; the end-to-end exclusion test probes the filesystem and
skips where the name cannot exist.

Eight mutation-checked properties, no survivors. Two of them needed the test
strengthening first: a two-banner case only separates "remove the first" from
"remove them all" when the copies are in different chunks, and a mutant that
moved the relative path back into the per-file loop needed an assertion about
which loop it is in rather than that it happens at all.

### 🟢 What the operator types, and what they are told

Closes #486 and #465.

**The slash a client user actually types.** #474 taught `on_connect` that
`msg <target> <text>` is the client's spelling of `PRIVMSG <target> :<text>`,
because the dashboard promises these lines can be written "exactly as you
would type it into a client". But in a client you type `/msg`, and the slash
never reaches the wire - the client consumes it. The operator who reported
#474 happened to omit it; the X login instructions they were following tell
them to include it, one keystroke from the same `421 Unknown command`.

It was never a `msg` problem. EVERY on-connect line had it: `/mode`, `/join`
and `/nick` were all sent with the slash attached and all rejected.

One leading slash comes off, whatever follows it, before anything else looks
at the line - which fixes every command at once and makes the `msg` rewrite
catch `/msg` for nothing. That also settles `//` (an escaped literal slash in
most clients) by construction: it keeps one rather than losing both.

The case is left exactly as typed. RFC 1459 makes the command word
case-insensitive on the wire, so `mode` is a MODE, and rewriting it would be
a second change to a line the operator is entitled to see sent as they wrote
it.

One new refusal comes with the stripping: `/` on its own is not blank to
`_clean_command()`, but once the slash is off there is no command left and
what would reach the server is a bare CRLF it discards - indistinguishable,
from the operator's side, from a command that ran.

**Two messages naming a route that does not work.** `dcc.py` and `irc.py`
both told the operator to set `MY_IP_OR_DOCK` "in admin_config.py or
settings.conf". The second half is false on a fresh install:
`settings_file.apply_to()` only applies names already in the namespace, and
nothing declares this one, so the entry is discarded with a message about
spelling.

Declaring it would be the wrong fix, and `settings_file.py` already says why
in its own comment - this is the address DETECTED at startup, and a value in
the file would freeze one session's answer into every session after it, with
`apply_to()` dutifully applying it and the detection never running again. It
would also appear on the dashboard's Settings page pre-filled with the
detected address, where one Save pins it permanently.

So the messages name `admin_config.py` only, and say why the other route is
not offered. The operator who puts it in settings.conf anyway is no longer
told to check their spelling of a name they spelled perfectly: both names the
daemon assigns to itself while it runs - `MY_IP_OR_DOCK` and `ORIGINAL_NICK`
- now say what they actually are and where to set them instead. That is a
message, not a gate: neither becomes applicable, and `_check_writable()`
still refuses to write one.

Six mutation-checked properties, no survivors.

### 🟢 No socket write is a partial write

Closes #504, which was filed while checking that #456's fix was complete
rather than local. It was not: twenty more sites across five modules had the
same fault.

`socket.send()` returns how many bytes it managed to hand to the kernel, and
the caller has to loop on the rest. `sendall()` does that loop. Every one of
these dropped the return value, so on Linux - with the socket's send buffer
within a few hundred bytes of full - the line goes out truncated and the
server reads whatever arrived as a complete command.

What it looks like when it happens, which is the argument for converting all
of them rather than the ones that look risky: a truncated `PONG` is a ping
timeout and therefore a disconnect; a truncated `USER` is a failed
registration; a truncated DCC SEND handshake is a transfer that never starts
with the slot still held. None of those report themselves as a short write.

**No truncation has been observed.** A short write needs the buffer nearly
full, which needs a slow server read or a burst, and most of these lines are
short and sent one at a time. The reason to fix them anyway is that the cost
is one word per site, the failure is silent, and "this line is short so it
cannot be truncated" is exactly the reasoning that left `queue_mgr` broken:
the buffer being full is a property of the CONNECTION, not of the line.

Every site that encodes a string does it the way `announce.py`'s debug drain
always has, so a name the socket cannot spell costs a character rather than
raising on whichever thread is holding the socket - the reader thread, for a
PONG.

This changes nothing about WHICH path a line takes. Several of these bypass
`oserve.queue_message()` deliberately - the DCC negotiation lines do it for
latency and say so where it happens - and that is untouched.

#### The guard is the deliverable

A test listing the twenty known sites would pass forever while somebody added
a twenty-first, so it asks the opposite question: is there any `.send(` left
in a daemon module at all?

That works because this codebase has exactly one non-socket `send` -
`adminchat.Session.send()`, which writes a line to an admin's DCC CHAT
session. So the allowlist is three receiver names in one file, and anything
else is a finding rather than a judgement call. It reads the AST rather than
the text, so a `send(` inside a comment is not a finding (`webserver.py` has
three, describing this very thing) and a call split across lines still is.

Three of its six tests guard the guard: the allowlist must still describe
something real, `Session.send` must still be a method rather than a name the
exemption now excuses by accident, and the calls must not have been deleted
rather than converted. That last one is a coarse backstop and says so - what
actually owns "the line is still sent" is the rest of the suite, verified by
replacing the USER registration with `pass` and watching six tests in other
files go red.

Six mutation-checked properties, no survivors.

### 🟢 A file the bot advertises, refused for its spelling

Closes #445.

The list lookup case-folds on purpose. `list.find_duplicate_filenames()` says
why in its own docstring: "Matching is case-insensitive, because the resolver
compares lowercased and a requester typing a name back cannot be expected to
reproduce its case."

Having matched that way, it then rebuilt the path from the REQUESTER's
spelling - `os.path.join(target_folder, requested_file)` - which is the one
spelling known not to be the one on disk. The list was written FROM the disk;
the row that matched is the row that names the file.

On Linux that path does not exist, and the `os.walk` last resort is a
case-sensitive `if requested_file in files` which misses too, so the
requester is told "File not found" for a file the bot is publicly listing. On
Windows it resolves - and the file is then offered and received under the
requester's casing rather than the operator's, because the resolved path is
what goes out in the DCC SEND.

Both branches of the lookup carry the name now: the first copy the list names
(the bare request every existing caller makes) and the copy a size hint picks
out (a request built from a search result's exact line).

The `os.walk` fallback is deliberately unchanged. It answers for names the
list never matched at all, and widening it would be a different decision -
one about whether an unlisted file becomes reachable under any casing.

Four mutation-checked properties, and the tests are the interesting part. The
refusal only happens on a case-sensitive filesystem, so a test that depended
on one would cover nothing on Windows or macOS. It is emulated instead:
`os.path.exists` is replaced with one that checks each component against what
the directory actually lists, which is what a case-sensitive filesystem does.
The wrapper has to strip `platform_compat.long_path()`'s prefix before
walking the path - without that it answers about a path that does not parse,
which is a test that passes while testing nothing. There is an explicit
fixture-invariant test that the emulation tells the two names apart, and a
separate assertion that runs everywhere: whatever the filesystem does, the
path the daemon settles on must carry the list's spelling.

The first version of the emulation case-checked every component up to the
drive root, which quietly made it a question about the machine rather than
about the library. A GitHub Windows runner's temp directory sits under an 8.3
short name - `RUNNER~1` - which its own parent does not list, so every path
in the fixture "did not exist" and the whole class failed on CI while passing
on a developer box. It checks only the components below the fixture's own
tree now, and there is a test for that: the tree's parent is made to list
nothing, which is the same condition on any platform.

### 🟢 A setting that came back blank

Closes #511. Found while investigating #510 - different cause, same visible
symptom, which is why it is a separate fix.

`sync_channels()` protects the debug channel from being PARTed by a rehash.
It read `DEBUG_CHANNEL` **after** the reload, so the guard worked whenever the
value survived and did nothing at all in the one case it exists for: the
reload handing back a blank. The channel is then in the before-list, absent
from the after-list, and `chan != ""` is true - so the bot PARTed the one
channel whose whole purpose is telling the operator what it is doing.

The dashboard fires a rehash on **every** settings save, so a `settings.conf`
that is briefly unreadable, an `admin_config.py` that failed to import, or
the window `tests/test_audit_high_findings.py` already documents where a
reload has a value back at its literal default, is enough.

A blank is ambiguous, and the two readings want opposite things: the operator
may have cleared the setting deliberately, or the reload may simply not have
brought it back. **Staying is the recoverable answer.** Somebody who meant to
clear it loses nothing they can see - a blank `DEBUG_CHANNEL` already stops
`send_debug()` writing there - and the bot leaves on the next reconnect.
Somebody who did not mean it keeps the channel they are reading. It is said
out loud either way.

A *deliberate change* still parts, which is the half a blanket "never part
the debug channel" rule would have quietly broken: moving `DEBUG_CHANNEL`
from one channel to a different one is an edit nobody makes by accident, so
the old one is parted and the new one joined exactly as before.

This is deliberately **not** the shape of the REQUIRED-setting restore in
`reload_modules_in_order()`. That one puts the value BACK, which is only safe
because a blank `NICKNAME`/`CHANNEL`/`ADMIN_NICK` is never legitimate - its
own comment says so, and warns against becoming "a ratchet that prevents a
rehash from unsetting anything". A blank `DEBUG_CHANNEL` is perfectly
legitimate, so nothing here changes the setting; only whether a PART is sent
on the strength of it.

Six mutation-checked properties, no survivors. One needed the test
strengthening: a mutant that left the capture exactly where it is and made it
`old_debug_chan = ''` survived a check for the name and its position, so the
guard asserts the statement rather than the token.

### 🔴 A JOIN nobody checked

Closes #510. Reported live, and the numbers are the whole story: fourteen
configured channels, **eleven joined**. The three missing were the last three
in configured order, and the debug channel - sent as a separate command after
them - was missing too.

The connect path sent every channel as ONE `JOIN` line and then a second
command for the debug channel with no gap at all. The server takes the head
of an over-long JOIN and drops the tail, so what is lost is whatever is at
the end. The operator's own client was showing the network throttling joins
in the same window.

Three separate things then kept it quiet, and each of them is individually
defensible:

- **A refusal numeric arriving at connect time is deliberately not counted.**
  `note_join_refused()` only counts for a channel already being retried,
  because *"inventing a retry schedule for [anything else] would start the
  bot knocking on doors nobody asked it to."* Sound - and it means a refusal
  during the initial JOIN went nowhere.
- **The retry machinery is seeded by the KICK handler alone.**
  `config.kicked_channels` had exactly one writer, so the daemon had a
  bounded retry for a channel it was thrown out of and nothing at all for one
  it never got into.
- **The warning that did fire went to the debug channel.**
  `activation_watchdog()` had already computed the answer exactly -
  `missing = target_channels - channels_confirmed` - and sent it through
  `send_debug()`, which writes to the debug channel. When a truncated JOIN is
  the cause, that is routinely one of the channels that went missing. It
  carried no `notice=`, so it never reached the dashboard either.

So the bot activated, reported itself healthy, served files, and was absent
from three of the channels it is configured to serve.

#### What changed

**The channels go out a few at a time**, four per `JOIN` with a two-second
gap. Fourteen channels become four lines over about six seconds, against a
five-second settle the connect path already waits. The debug channel rides in
that batching rather than trailing it - it was the most exposed line in the
burst, and the one whose loss costs the operator the message saying anything
was lost.

**Whatever never answers is tried again.** The watchdog's `missing` set is
fed to `note_join_unconfirmed()`, which writes the same entry a kick does -
so `channels_to_rejoin()`, the attempt limit and the advert-timer retry all
work with no special case. The reason is recorded alongside, because "gave up
after 3 attempts" reads very differently for a channel that threw us out and
one that never let us in.

**The warning reaches the dashboard**, not only a channel that may be gone.

**`405 ERR_TOOMANYCHANNELS` is now a refusal**, with its own wording. It
belongs with the four permanent ones rather than with a throttle - it keeps
being true until the operator serves fewer channels - but "gave up after 3
attempts" would send somebody looking for a fault on the channel's side. It
says what is actually wrong instead. And a refusal that is *not* counted is
now printed rather than discarded in silence, which is most of why serving
eleven of fourteen channels looked like nothing had happened.

**One definition of where the bot belongs.** `irc.channels_we_should_be_in()`
is the single answer now; `commands._channels_to_sync()` delegates to it.
There were two hand-written answers to that question, in two modules that
both get reloaded - and this file already carries a comment about exactly
that hazard, for exactly this setting (#193), in the one place it decides
whether to PART a channel.

Eleven mutation-checked properties, no survivors. Six existing tests moved
with the mechanism: five windows in `test_on_connect.py` were anchored on
`JOIN {channels}`, which matters more than it sounds - `str.split()` returns
the WHOLE body when it finds nothing, so a stale marker silently widens a
window to the entire function and goes on passing. Two source-reading guards
in other files asserted the shape of the old fix rather than the behaviour
and are driven now. And one was broken by a COMMENT of mine quoting the
message it anchors on, which is the third time that has happened in this
codebase - that file strips comments before searching now.

### 🟢 The rehash's channel sync takes its turn

Closes #440.

A rehash compares the channel list it read before the reload against the one
after it, and JOINs what is new, PARTs what is gone and NAMESes the rest.
All three loops wrote straight to the socket: no pacer slot, no gap between
lines, one line per channel per verb. It was the only outbound path in the
daemon that both scaled with the channel count and ignored
`runtime.outbound_pacer`.

The dashboard fires a rehash on EVERY settings save - a theme change and a
password change included - so an operator with fourteen channels plus a debug
channel sent fifteen back-to-back lines for changing a colour. Measured at 15
lines and 222 bytes for that case, and 43 lines and 894 bytes for a full
channel-list swap.

Two things fix it together, and both were already precedent in this codebase.
The lines are comma-batched, the way `irc.py`'s connect path has always
joined every channel with ONE JOIN - which turns the realistic case into two
lines and the full-swap case into three. And they go through
`oserve.queue_message()` like every other line the bot says, so they take a
pacer slot rather than racing whatever else is being sent. A NAMES refresh is
latency-insensitive; the DCC negotiation lines that deliberately bypass the
queue are not, and are untouched.

The batching is capped at 510 bytes per line. Without that, comma-batching
would eventually build a line longer than a server will read - which is a
worse failure than the burst it replaces, because the tail is then read as a
command of its own.

Two smaller things came with it. The debug channel gets one line per verb
rather than one per channel: `send_debug()` goes to a CHANNEL through the VIP
lane, so a line per channel was a second burst sitting behind the first,
paced but still saying the same thing fifteen times. And the PART reason said
"Removed from DDCore" - a typo, in the one string every person left in the
channel sees.

`sync_channels()` is a function of its own now, for the reason
`irc.resolve_dcc_address()` is: while it was inline, reaching it meant
reloading every module in the daemon, so the only thing a test could do was
look for the word JOIN in `commands.py` - which passes with the loop behind
`if False:`.

#### The sync no longer reports the reload as failed

Making it a function is what found this. `tests/test_rehash_end_to_end.py`
runs one real rehash in a booted subprocess, and its `oserve` stand-in
carried only `irc_connection` - enough for three `send()` calls and not for
`queue_message()`. The AttributeError took the *whole rehash* down: the
modules had already reloaded, the live state had already been merged back,
and the operator was told "[REHASH CRITICAL ERROR] The files could not be
reloaded live".

The sync is tail work - everything the handler exists to do has happened
before it runs - so it now has its own handler that says what actually
failed and what it means (the channels are unchanged until the next rehash or
reconnect). Same reasoning as `update_list.py`'s point of no return (#442):
once the thing is done, the error path has to stop claiming it is not.

The fixture's `oserve` is complete now, and asserts through the real rehash
that the sync lines go to the queue and that both channels share one NAMES
line.

#### A test that was passing on the wrong occurrence

`test_the_pause_does_not_outlive_a_rehash_that_raised` read the 800
characters before the `[REHASH CRITICAL ERROR]` message and looked for
`resume_transfers()` in them. A comment explaining that message moved the
anchor; stripping comments fixed that and introduced something worse - the
window then reached far enough back to find the SUCCESS path's resume, and
passed with the failure path's own call deleted. Mutation-checked, caught,
and replaced: the window is now everything between entering the handler and
the message it prints, which is the span the resume actually has to be in. A
count of characters is not a property.

Nine mutation-checked properties, no survivors.

### 🟢 Two faults a fresh install meets

Found by the pre-publication audit sweep. Closes #446 and #447.

**A byte-order mark ate the first setting.** `settings.conf` was read as plain
`utf-8`, so a file saved with a BOM - three invisible bytes that Notepad
writes as a matter of course - kept the mark attached to its first line. The
first setting therefore parsed as `﻿MSG_DELAY`, landed in the report's
`unknown` list, and was silently ignored while the default stood. The operator
is looking at a file that plainly sets it.

Both sides now read `utf-8-sig`, which consumes a mark when present and is
identical to `utf-8` when not. The save side matters as much as the read one:
it decodes the same file to edit it in place, so a mark left there would
reappear inside the first line of the rewritten file and the bug would come
back on the next save.

**The pre-flight check reported a console that was switched off.**
`ADMIN_HOSTMASKS = [""]` is a truthy list of one, so counting it produced
"enabled for 1 host pattern(s)" while `adminchat.admin_host_patterns()`
returned `[]` and the daemon accepted nothing. `[""]` is exactly what a fresh
install can end up with, because `configure.py`'s password prompt is mandatory
while the hostmask is not.

The check now asks the daemon what it will actually accept. A pre-flight that
reports a feature as enabled when it is disabled is worse than one that says
nothing, because the operator stops looking - and it is guarded so the check
can never itself be what breaks the check.

Three mutation-checked properties, no survivors.

### 🔴 Two transfers counted wrong

Found by the pre-publication audit sweep. Closes #454 and #455.

**A send that ended short was counted as a completed one.** The check already
existed - `transfer_completed = bytes_sent >= file_size` - and a truncated
send already printed `[DCC-FAIL]`. But the `[DCC-SUCCESS] Sent the whole file`
line, the lifetime totals, the byte total, the speed record that feeds the
advert and the per-file download counter all ran regardless. So the one place
that told the truth was a log line, and every number an operator or another
bot would later read said the opposite.

Counting it also inflated the speed figure, because the elapsed time covers a
transfer that stopped early.

The skip is one branch rather than a condition repeated around each
statement, and it has its own exception type so it cannot be swallowed by the
broad handler that guards the database writes - a deliberate skip reported as
"Could not increment the sharing statistics" would send the next reader
hunting a database fault that never happened.

**A row was settled under the nick the send started as.** A transfer takes
minutes; `irc.note_nick_change()` carries the queue to the new nick the moment
the server says so. By the time the row was settled it sat under a key
`release_queue_entry()` had never heard of, so the keyed lookup found nothing,
the delivered row was never removed, and the same file went out again on the
next trigger.

This is the other half of #431: that fix is what moves the queue, which is
what leaves this lookup pointing at a key nobody uses. The row object is the
identity, not the key it happens to sit under - so the key is tried first,
and a miss falls back to finding the object itself. Matching on the filename
instead would take somebody else's row, since two people can queue the same
file.

Five mutation-checked properties, no survivors. One of the guards was
rewritten first: it used `str.index` and died with "substring not found",
which tells whoever hits it nothing about what broke.

### 🟢 Three small things in the plumbing

Found by the pre-publication audit sweep. Closes #456, #457 and #458.

**Two outbound lines could be cut in half.** `queue_mgr`'s VIP lane and its
standard lane both called `send()` and discarded the count it returns. On
Linux, with the socket's kernel send buffer within a few hundred bytes of
full, that truncates an IRC line mid-message - and the server reads whatever
arrived as a complete command. `announce.py`'s debug drain has used
`sendall()` for exactly this reason; these two were the last places that did
not. They encode the same way now too, so a filename the socket cannot spell
costs a character rather than raising on the worker thread.

**Every `-que` read the whole library index.** The four values it fetched are
used only by the layout shown when somebody has nothing queued, and nothing in
the other branch touches them - so the person who DOES have files queued paid
a full read of every published list file for numbers that were then thrown
away, on a command they are likely to repeat while they wait. The read now
happens only in the branch that shows it.

**A constant that described a behaviour the daemon does not have.**
`SEND_TIMEOUT = 30.0` sat next to a comment saying a send timeout "stops a
stalled peer wedging the writer thread forever". There was no second timeout:
it was never referenced, and the writer's `sendall()` has always run under the
same `sock.settimeout(1.0)` as the reader.

A real send deadline is not available cheaply here - the writer runs on its own
thread and shares the socket with the reader, and `settimeout()` is per SOCKET
rather than per direction, so raising it around a send would raise it for a
`recv` another thread is sitting in. `SO_SNDTIMEO` is direction-specific but
interacts badly with Python's own timeout handling.

One second is defensible for this workload rather than merely tolerated: the
console sends short lines, so `sendall()` only blocks if the buffer is full,
and that means the peer stopped reading long enough to fill it. Tearing the
session down then is the right answer. What was wrong was the constant
claiming otherwise - so it is gone, and the comment now says what happens and
why, which is what stops somebody adding it back.

Three mutation-checked properties, no survivors.

### 🔴 The search index's WAL log only ever grew

Reported live: an 845MB `list_index.db` next to a 128MB `.db-wal` file that
never shrank back down.

`index_bot_list()` deletes and re-inserts a whole bot's list as one
transaction, and the largest bot on the live index is 1.3 million rows - so
the WAL log grows to the size of that whole write before there is even a
transaction boundary for SQLite's own automatic checkpoint to attempt. That
default (PASSIVE, best-effort) can leave the checkpoint incomplete when a
concurrent read - the dashboard's filter bar, polled continuously - holds an
older snapshot at the moment it tries, and one incomplete checkpoint compounds
with the next big write.

Confirmed rather than assumed: a manual `PRAGMA wal_checkpoint(TRUNCATE)`
against the live file cleared it to zero in one call, and a check of the main
database found no orphaned rows from bots no longer held - all 3.88M rows
belonged to a bot still present, 13 for 13. The 845MB was real, current data,
not a leak.

`_checkpoint_locked()` now runs a full TRUNCATE checkpoint after the commit
in both `index_bot_list()` and `drop_bot()`, inside its own `try`/`except` so
a checkpoint failure - which costs disk space, never correctness - can never
be mistaken for the write itself failing.

### 🔴 A completed fetch drops its temporary id

Reported live: fetched files and fetched list zips both piling up with names
like `058c4cc8ee9a_Some Track.mp3` and
`ef31cd79d1f5_SomeBot-Default(2026-01-02)-OS.zip` forever.

The id is folded into the stored filename so two in-flight fetches racing
for the same cleaned name can never collide - but nothing about that needs
to survive once a fetch finishes. For a fetched list specifically, unzipping
it on an operator's own machine carried the id even further: a plain list
zip has no folder recorded inside it for an unzip tool to extract "into" the
way one packed with rar's `-ep1` does, so Windows named the unzipped result
after the zip file itself, id and all.

A completed `"file"` or `"folder"` fetch is now renamed from its id-prefixed
staging name to the plain one, never overwriting a file already there. A
completed list fetch has its raw zip removed entirely once it has been
safely extracted - the List Browser reads only from the extracted copy, and
nothing ever reopens the zip afterward - and the zip is left in place on
failure, as the only diagnostic evidence for why a peer's archive could not
be read. The Downloads tab's Download button, which used to appear
regardless of whether anything was actually left to download, now checks
for that first and offers "Browse it in List Browser" instead when there
isn't.

### 🔴 A dash-separated size was part of the filename

Reported live:

    [FETCH] Requested "A101. Donna Summer - I Feel Love (Original 12''
    Version).mp3 ---- 18.8Mb" from SomeBot (request 7c311f24a8a1).
    [FETCH] Rejected unsolicited DCC SEND from SomeBot: no matching pending
    request.

SomeBot runs bot software outside the OmenServe family, and its master list
carries no `::INFO::` marker at all - just a bare filename, two or more
hyphens, and a size. `strip_info_suffix()` only recognised `::INFO::`, so the
trailing `" ---- 18.8Mb"` stayed attached to what the dashboard then
requested, and the real DCC SEND that came back - bearing only the bare
filename - never matched it. The same failure mode `::INFO::` itself was
hardened against a while back, just via a different bot's marker convention.

A second pattern is now tried once the `::INFO::` search has failed: a
trailing `-{2,}<size>` shape anchored to the end of the line, only trusted
once what follows the dashes actually looks like a size (digits, optional
decimal point, optional K/M/G/T, then B) - a real title can contain a run of
hyphens, but essentially never one immediately followed by a size unit.

### 🟢 One test leaked a channel into every later one

Chased as a flake twice before it was read as a leak, which is the part worth
recording.

`test_a_bot_nowhere_we_know_of_falls_back_to_the_first_channel` failed inside
a preflight run with

    'PRIVMSG #one :' not found in 'PRIVMSG #dccore-test :...'

naming a channel from a test file it has nothing to do with - in a session
where three consecutive full suites had just passed, and where preflight's own
second pass passed too.

**`BROADCAST_SEARCH_CHANNEL` was never reset between tests.**
`tests/test_config_overrides.py` sets it while checking that `settings.conf`
overrides a module default, which is exactly what that file is for, and
nothing put it back. Every later test in the run then saw a channel it never
configured - and `dcc_fetch` reads that value as the fallback for a bot
presence cannot place, so the leak silently redirected fetch requests.

Reproduced deterministically before fixing anything: set the value, run the
one test, watch it fail with the identical message. That is the difference
between a flake and a leak, and it took two false starts to go looking for it.

`RUNTIME_FLAGS` already covered live state, and every container in
`runtime.py` is emptied between tests. **Ordinary config values with a
module-level default had nothing**, and they are the harder case: a test that
changes one is usually testing something else entirely, so nothing about it
looks like state management. `SETTINGS_DEFAULTS` now covers them, with a
fixture invariant that the list is not empty and that every name in it is a
real setting - a typo there would reset nothing and say nothing.

### 🔴 A rename carries the user's state

Found while checking #376's premise - that `config.channel_users` can serve as
a presence history for inferring alt-nick identity. It has a hole in it at
exactly the event that issue is trying to reason about.

`irc.py`'s NICK handler has always parsed the message correctly and moved
**one** store:

```python
if old_nick in config.send_queue:
    queue_mgr.config.send_queue[new_nick.lower()] = queue_mgr.config.send_queue.pop(old_nick)
```

Everything else keyed on the nick stayed where it was. Demonstrated against
the module rather than argued:

    BEFORE  someuser -> someuser_
      present as 'someuser'  : True
      present as 'someuser_' : False
      dcc_queue keys         : ['someuser']

    AFTER (handler moves send_queue only)
      present as 'someuser'  : True    <- nobody by that name now
      present as 'someuser_' : False   <- the name they actually use
      dcc_queue keys         : ['someuser']
      send_queue keys        : ['someuser_']   <- moved, correctly

**Two of those matter immediately.**

`channel_users` is what `dcc.user_is_present_in_ram()` reads, and dcc.py
treats it as proof somebody is there before dispatching to them and before
thawing a frozen queue. Stale, it answers **False for the name they now use
and True for one nobody has** - so a user who changes nick mid-session has
their transfers stop while they are sitting in the channel, and nothing
anywhere says why.

`dcc_queue` holds their files. Stale, `!que` under the new nick shows nothing
and the bot has nobody to send them to.

The server tells us this **once**. There is nothing to infer - a NICK names
the old nick and the new one - but nothing rebuilds those stores until an
unrelated NAMES or rejoin happens to.

#### What it deliberately does not carry

`muted_until` and `banned_users` are nick-keyed too, and moving them would be
a **change of policy rather than a fix**. DCCore already answers nick-hopping
with hard bans, which match a HOSTMASK pattern and are unaffected by any of
this - so a soft sanction being escapable by rename is a designed property
with a designed escalation, not an oversight to quietly close inside a bug
fix. There is a test pinning that it does not move, and a mutant that makes it
move is killed.

`whois_status` is left for a different reason: it caches what a WHO reply
said, and asserting the new nick is online because the old one was is
answering on the server's behalf.

#### One thing worth stealing for any future move-the-key code

It never overwrites an existing entry under the new name. Nicks get reused,
and somebody else may hold that name with a queue of their own - handing one
person another's files is a worse failure than the one being fixed. It logs
and leaves both alone instead.

The helper is `note_nick_change()`, beside the other `note_*` functions, so it
can be tested without running the read loop - which is why the original
one-third-of-the-job version was never caught. Two structural tests check the
handler actually calls it, and that the old inline `send_queue` line has not
come back.

#### It broke a security guard, which is the guard working

`test_the_nick_change_handler_is_gated_on_nick` anchors on a line inside the
NICK handler, walks outwards to the enclosing `if`, and **evaluates** that
condition against a genuine NICK line and against a forged
`:attacker!u@h PRIVMSG #c :hey NICK :victim`. It exists because
`" QUIT " in line` once matched an ordinary `@find QUIT PLAYING GAMES`.

Moving the body into `note_nick_change()` deleted the line it anchors on, and
the guard refused to guess - *"matched 0 lines in irc.py; it must match
exactly one"* - rather than silently checking nothing. Exactly right: a marker
matching zero lines and a marker matching two are both a test that has stopped
testing.

The gate is untouched; only the marker moved, onto the call itself rather than
the function name, which also appears at the definition and would match two
lines. Re-verified the guard still earns its place by un-anchoring the gate to
`if " NICK " in line:` - it fails, as it should.

Six mutants on the fix, all killed, including "presence is not updated" (the
original defect) and "only the first channel is updated".

---

### 🟢 My ceiling test raced its own fixture

Reported by the co-maintainer against `main`, while checking whether a failure
in their own branch was theirs - it was not, and checking rather than assuming
is what found this.

`TheCeilingIsOptional` failed on their machine and passes here. Not a timing
margin that wants widening: **the fixture races the thing under test.**

`FakeChild(ticks=99)` finishes after 99 no-op waits, and the ceiling under
test is 1ms. The watcher does no real waiting against a fake, so the test is
actually asking *"do 99 iterations of the watcher's loop take longer than a
millisecond?"* - which depends on how warm the filesystem cache is when
`last_progress_at()` reads the progress file. Lose that race and the fake
**finishes**, the watcher returns normally, and the test fails asking why no
timeout was raised.

Measured here: those 99 iterations take **11.2ms**, an 11x margin. That is why
it passed 45 consecutive runs locally, including 25 under six-way CPU load,
and still failed on somebody else's machine. A margin that large looks safe
and is not a guarantee.

`ticks=None` now means never finishes, and the four tests whose subject is the
watcher **giving up** use it - so the only way out of the loop is the thing
being tested. No margin to tune, no machine dependency, nothing to widen next
time somebody gets faster hardware.

Worth stating as a rule, because it is the third fixture problem this release:
**a fake that can finish on its own is racing any test about giving up.** The
fake must only end the way the code under test ends it.

---

### 🟢 Somebody messaged your bot

Asked by an operator making DCCore their primary server: *"what happens to
private messages the bot gets?"*

Checked rather than answered from memory, and the answer was **nothing at
all**. An unrecognised private message is dropped in the read loop: no reply,
and no record either. The whole PRIVMSG block contains three logging calls and
all three are error handlers; nothing writes to disk; the Console buffer only
carries `send_debug()` output. So somebody could message the bot every day and
the operator would never know anyone had tried.

    ignored   hello?
    ignored   are you there
    ignored   can you send me the new album please
    COMMAND   @<nick>-help
    COMMAND   @find ...

**The silence is right and it stays.** A bot that answers every stray line is
one that can be made to flood itself off the network, which is exactly why the
rate limiter upstream exists. But there is a real gap between *"do not reply to
strangers"* and *"the operator never finds out anyone spoke to it"* - and
somebody messaging a file server is usually somebody who wants something from
it and does not know the syntax. Only the record changes.

#### Kept apart from the notices, deliberately

A notice is something that went **wrong** and carries one of two severities. A
message is neither wrong nor right, and giving it a severity would mean
inventing a third that nobody can tell apart at a glance - which the notices
design says in as many words it will not do.

So they get their own store, their own file, and their own page. The unread
count sits on the **nav item**, not on the status badge: a notice wants you
now, a message is waiting whenever you next look, and mixing them makes one of
the two mean less.

#### Where the capture sits is the whole design

One point in the read loop, where four things are already known:

  * **not a command** - those are answered normally and are not this;
  * **sent privately** - a channel line is one the operator can already see,
    and recording those would be a log of other people's conversations rather
    than of anybody talking to us;
  * **not a CTCP** - that is a client talking to a client (VERSION, a DCC
    offer), not a person typing something they expect an answer to;
  * **past the ban check and the flood gate** - so a ban silences somebody in
    the panel too, and a flood cannot fill it.

There is a test for each, and one that asserts the capture sends nothing -
no notice, no queue, no socket write. The bot still says nothing.

#### And an off-switch that answers instead of recording

Asked for by the same operator: a way to turn the whole thing off, without
even the page showing in the menu - and, when it is off, to tell whoever
messaged where to go instead.

That is not the same feature with its panel hidden. They are two different
contracts with the person who typed:

    PRIVATE_MESSAGES_ENABLED = true    recorded, page shown, bot silent
    PRIVATE_MESSAGES_ENABLED = false   nothing kept, page gone, sender told once

The second is the only mode that tells them anything, which is what makes it
worth having rather than just a checkbox. Somebody messaging a file server is
usually somebody who wants something from it and does not know the syntax;
until now their two possible outcomes were "recorded and ignored" and
"dropped and ignored".

The page disappears the way the Console's does - its API answers **404** and
`web/app.js` hides the nav item on that status. 404 rather than an empty list
on purpose: an empty list means *nobody has messaged you*, which is a fact
about the world, and this is *there is no such page here*, which is a fact
about the bot. The section itself stays in the DOM and is only hidden,
because deleting it made `activateView()` throw on every view switch and took
the rest of the navigation with it - found on a real install, on the Console,
and not worth finding twice.

#### The reply is the only thing here that puts a line on the wire

An auto-reply to anyone who messages you is the classic way for a bot to be
flooded off a network by strangers, so it has four brakes and they cover
different things:

  * **A NOTICE, never a PRIVMSG.** RFC 1459 forbids a client auto-replying to
    a NOTICE; it is what every other user-facing answer here already uses;
    and - decisively - `irc.py`'s own parser matches `PRIVMSG` alone, so two
    bots both running this **cannot** answer each other into a loop. That one
    is structural rather than a check somebody has to remember to write.
  * **Once per sender per day**, persisted. RAM alone would mean the bot
    repeating itself to everybody every time the operator restarts it.
  * **A ceiling across every sender together**, which the per-sender rule
    cannot give: two hundred nicks messaging within a minute are two hundred
    *first* messages, each individually owed a reply. That would sit in the
    send queue for minutes and delay the transfer notices people are actually
    waiting on. Past the ceiling the replies are dropped silently - nobody is
    owed an explanation of why they did not get one, and a line saying "too
    busy to answer" would be the same flood.
  * **The ordinary send lane, never VIP**, so it waits behind real work and
    goes out one `outbound_pacer` slot apart like everything else the bot
    says. Ten people messaging at once become ten notices spread out, not ten
    lines at once - which is the same clock the Excess Flood work put in.

`%admin` in the text becomes `ADMIN_NICK`, or **"the bot's owner"** when none
is set: the bug worth naming is *"Please message None instead"*, sent to
somebody who now knows less than before they asked. Startup says so once when
the feature is off and no admin nick is configured.

Blanking the text is a real third position - no record, no page, and no line
on the wire either - for an operator who wants the bot completely silent to
strangers without editing code.

Thirteen mutation-checked properties, including the two that survived the
first pass: a blank text that still spoke, and an `activateView("search")`
asserted as a bare call rather than as the whole statement, which passed
happily on a page that stranded whoever was reading it.

#### One person repeating themselves is one person

Somebody typing four lines because the first got no answer is one person
trying to ask something, and four rows of it buries the next person who tries.
The first is kept and the rest dropped for `PRIVATE_MESSAGE_COOLDOWN_SECONDS`
(300 by default), **per sender** - a throttle that silenced everybody after one
message would hide exactly the person worth hearing from, and there is a test
that fails if it ever becomes global.

The cooldown lives in RAM only, on purpose: its job is to stop one person
filling the panel in one sitting, and an operator restarting the bot is
entitled to see that somebody is still trying.

#### The page says what it cannot do

Everything on that page looks like a conversation and is not one. So the
subtitle says *"the bot never replies to these"* **above** the list rather than
below it, there is no reply field, and the payload carries no action that
sends anything. A reply box would be a promise the daemon cannot keep -
there is no conversation path in the bot at all. Two tests pin that: the
warning comes before the list, and the section contains no text input.

The message text is kept, not just a count. *"Three people messaged you"* is
not something an operator can act on; *"can you send me the new album"* is.
Trimmed at 400 characters - somebody pasting is still somebody asking, and the
first part says what they wanted.

Six mutants, all killed, including a global cooldown, a case-sensitive one,
and the text being thrown away for a count.

One of my own guards had to be fixed first: it split the source on
`record_private_message(` to find the capture block, and the **comment** above
the call names the function too - so the extract stopped before any of the
conditions and three tests were asserting against prose. It splits on the call
with its arguments now, and strips comments. That is the third time this
session; the pattern is always the same, and always mine.
### 🟢 A thread that outlived its test wrote real state

Preflight's state-write guard kept failing with

    THE SUITE WROTE REAL STATE FILES:
      modified dataetch_history.json

and passing on the next run with nothing changed. Read as a flake twice, and
re-run past. It is not a flake.

`dcc_fetch`'s dispatcher persists the fetch history **every 2 seconds on a
daemon thread** that outlives the test that started it. `tests/support.py`
redirected `db.FETCH_HISTORY_FILE` into a temp directory for each test and
restored the **real path** in `tearDown` - so a tick landing between that
restore and the end of the run wrote the operator's own
`data/fetch_history.json`. It needs a 2-second tick to fall inside a teardown,
which is why it appeared perhaps one run in three.

The restore now points at a dead temp path instead of the real one. A late
tick then fails to write a file nobody reads, which costs nothing, and the
real file is **never a target at any point in the run** - there is no window
left to land in.

    after a test finishes, db.FETCH_HISTORY_FILE points at:
      ...\Temp\dccore-orphaned-test-writeetch_history.json
    is that the operator's real file? -> no

#### The same shape as the drain flake, found independently

The co-maintainer hit this class on #413 at nearly the same time, in a
different thread: `announce._ensure_debug_drain()` starts a drain that never
stops, re-reads `sys.modules['oserve']` every loop, and therefore writes stray
debug lines into whatever the CURRENT test's fake socket is.

Two threads, one shape: **a daemon thread that outlives its test and
re-resolves its target on every loop attaches itself to whatever is current.**
One of them contaminated assertions; this one wrote real operator data.

That is the useful generalisation, and it suggests where to look next rather
than only what to fix now: any module-level `_ensure_*` that starts a thread,
and any teardown that restores a path a live thread still holds.

---

### 🔴 A rebuild that is working is not hung

Reported from the live bot: **`Failed: timed out after 1800s`**, on a library
of **80 TB+** that takes hours to walk. Every rebuild died at the thirty-minute
mark, and the bot has been serving the same list ever since.

The guard was `subprocess.run(timeout=LIST_UPDATE_TIMEOUT)` with a flat 1800s
default, added in #162 to stop a hung mount wedging `search_inprogress` and
`update_inprogress` permanently. That problem was real. The instrument was
wrong.

**A wall clock cannot tell a rebuild that is working from one that is stuck**,
and the number cannot be fixed by making it bigger. Any value is either too
small for somebody's library or too large to be a safety net, and the value an
operator needs changes every time their library grows. The old default was
asking every operator to guess how long their own filesystem takes - and to
guess again next year.

**The child already answers the right question.** `update_list.py` writes
`LIST_PROGRESS_FILE` on every directory it enters, roughly twice a second. So
the question is not "how long has this taken" but "when did it last do
anything".

`run_watching_for_a_stall()` replaces the flat timeout with three rules:

    progress advancing   -> leave it alone, for as many hours as it needs
    silent for `stall`   -> wedged; kill it (default 900s)
    past `ceiling`       -> an absolute cap for anyone who wants one (default 0 = none)

**Strictly better at both ends.** A genuine wedge is now noticed in fifteen
minutes rather than thirty, and an honest eight-hour rebuild is never touched.

#### The rule that matters most is the one about not knowing

`last_progress_at()` answers `None` for "cannot tell" - no file, unreadable,
no timestamp in it - and a `None` **never** kills the child. A rebuild that
cannot write its progress file (a full disk, a read-only `data/`) is not
evidence of a rebuild that is stuck, and killing one for it would turn a
cosmetic failure into the loss of an eight-hour run. The mutant that treats
"cannot tell" as "silent forever" is the most dangerous one in this change and
three tests kill it.

#### A stall is not a timeout

They get separate endings and separate wording, because they point at
different things. Going quiet points at the **library** - a mount that went
away mid-walk - and says so: *"nothing reported for 20m 00s. The library it was
reading may have gone away."* Running past a ceiling points at a **limit the
operator chose**. Both raise an operator notice, and both record how long the
run lasted.

#### Two smaller pieces

`_write_zip_artifact()` now writes one `"packing"` heartbeat before it starts.
Deflating a several-hundred-megabyte list is the longest step in the run with
nothing else to say, and without it the stall watch has only the last
*scanning* write to go on while the largest libraries finish.

`subprocess.run` -> `Popen` moved the seam every test mocked. Four test sites
were updated, and two of them - in `test_webserver.py` - had **no cleanup at
all**. That was survivable while they patched a stdlib attribute; leaving
`commands.run_watching_for_a_stall` replaced would have silently disabled the
real rebuild for every test that ran after them. Both restore it now.

The test asserting *"the subprocess is given the configured timeout, not
None"* was #162's own guard, and this change deliberately retires the contract
it pins. It is replaced rather than deleted: the caller must still hand over
both limits, and a stall must still clear `search_inprogress` /
`update_inprogress` in the `finally`, which is the part #162 actually cared
about.

Five mutants, all killed, including a pure wall-clock regression and a ceiling
of 0 being enforced as zero seconds.

---

### 🟢 How long has this been running

The rebuild progress line said what it was DOING - *"Scanning folder 3 of 10 ·
4,211 files so far"* - and never how long it had been doing it. That leaves the
one question an operator actually has while watching a rebuild unanswered on
the page: **is this normal, or has it hung?**

Now: `Scanning folder 3 of 10 · Flac · 4,211 files so far · 1m 15s`, and
`Done in 3m 04s.` when it finishes.

**The page cannot work the elapsed time out for itself**, which is why this
touches three files rather than one. `update_list.py` runs as a SUBPROCESS, so
the progress file is the only thing it shares with the daemon; and the
dashboard may have been opened - or the daemon restarted - long after the
rebuild began. Deriving it from when the file first appeared fails for the same
reason: the file outlives the run that wrote it. So the child stamps
`started_at` in every write, once per process.

**Subtracted on the daemon's clock, at both ends.** A browser subtracting a
server timestamp against its own clock shows a negative elapsed, or an hour of
it, on any machine whose time is slightly off - and the first frame is exactly
when somebody is looking.

Two values that look alike and are not:

  * `progress.elapsed` - how long the SCAN has been going, live.
  * `seconds` - how long the whole OPERATION took, recorded when it ends. That
    covers spawning python, the two-second NFS sync pause after the child
    exits, and the re-count afterwards. On a slow mount those are not
    rounding, and reporting the scan's own figure as the total would quietly
    understate every rebuild.

**The clock starts inside the thread, not when the request arrives.** `!update`
can wait on the maintenance lock, and time spent queued is not time spent
rebuilding.

**Every ending records one** - success, failure, timeout, and the unexpected -
because a duration left over from the previous rebuild is worse than none: it
would be shown against a run it did not measure. There is a test that counts
the two against each other rather than naming the paths, so a fifth ending
added later cannot quietly skip it.

**On a failure it is the more useful half of the message.** A rebuild that died
after four seconds never reached the library; one that died after forty minutes
did - and that is the difference between a typo in a path and a mount that went
away mid-walk.

#### Two wordings for one number

`describe_duration()` exists in Python and again in JavaScript, because the
same rebuild is reported in the debug channel and on the page. Seconds under a
minute, `2m 04s` above it, `1h 12m` above an hour - a large library on a mapped
drive runs into the hours, and `4331s` is a number nobody converts in their
head. The smaller unit is zero-padded so consecutive rebuilds line up read one
under another in a log.

Both are pinned, including the boundaries: an off-by-one there shows `60m 00s`
or `0h 59m` to a real operator, and 3599/3600 are tested on both sides.

#### Missing, not zero

A progress file written by an older build, read mid-upgrade, has no
`started_at`. That is `None`, never `0` - the second renders as fifty-odd
years - and the page omits the clock entirely rather than showing `0s` forever,
which would read as a stalled rebuild rather than as a missing field. A clock
that steps backwards mid-run (ntp correcting a drift) clamps at zero rather
than reporting a rebuild that has not begun.

Eight mutants, seven killed. The eighth is **equivalent and recorded as such**:
dropping the explicit `started_at is not None` check changes nothing, because
`float(None)` raises and the surrounding `except` produces the same `None`. The
check stays anyway - an operator upgrading mid-rebuild is an expected case, and
expected cases should not be handled by catching an exception.

#### A test that was a bet on a function never growing

`test_the_writing_phase_is_indeterminate` read the renderer as
`source().split("function showUpdateListProgress(", 1)[1][:1600]` - a fixed
character budget. Adding the elapsed clock and its comment pushed
`is-indeterminate` past 1600, and a test that is not about the clock failed
against code it checks that was untouched and still correct.

Both slices now extract to the start of the next function, with a fixture
invariant asserting the split still matches - otherwise the "body" becomes the
whole rest of the file and every check passes on somebody else's code. The
invariant is mutation-checked, which is the only way to know a fixture guard
is load-bearing.

The `!update` completion line also loses its last ``, which the "no bold
anywhere" sweep had left behind in `commands.py`. **19 more remain in that
file** and are not touched here; they belong to a sweep of their own.

---

### 🟢 The list is not a download

Reported from the live bot: the master list was showing up in **Most
downloaded**.

It has been counted since those tables existed, because it goes out through
the same `start_dcc_send()` as everything else and `download_count_identity()`
had two branches - album, or file - with nothing in between. The list fell
into "file".

**It is the wrong thing to count, not merely an extra one.** The list is how
somebody finds out what the downloads ARE. It is sent to everyone who has ever
typed the nickname, which makes it the most-requested item on every bot,
forever, in a table whose entire job is to say which of the *files* people
want. One row that is always first tells you nothing you did not know.

And it is worse than one row. The artifact's name carries the build date:

    SomeBot-2026-07-01.zip    5
    SomeBot-2026-08-01.zip   91
    SomeBot-FULL-2026-09-01.txt    2

Every rebuild starts a **new key**, so the table slowly fills with dated
copies of the same list and pushes real files out of the top ten. On a bot
that rebuilds weekly the top of that table is eventually nothing else.

There is a third symptom underneath. The list lives in `LOCAL_LIST_DIR`, not
under any library folder, so `library_count_key()` has nothing to make it
relative to and falls back to the **absolute path** - the one form #151 made
these keys relative to avoid, because it breaks every counter the moment a
library moves.

**A None key is the "do not count this" signal.** `db.record_download()`
already returned early on a falsy key as a defensive check; that check is now
load-bearing and says so. The decision stays in the one place that already
decides what a send counts as, rather than becoming a second condition at the
call site - which is what `test_it_records_what_download_count_identity_
decides` exists to prevent.

**Checked AFTER the album branch, on purpose.** An album is identified by
*where it is* - `TMP_ZIP_DIR`, which this module wrote - and that is
unambiguous. A folder that happens to pack as `<base name>-<date>.rar` would
otherwise match the list naming rule and stop being counted. There is a test
for exactly that ordering.

#### The fix is invisible to everybody who already has the rows

Which is everybody who reported it. So `prune_list_artifact_download_counts()`
runs at startup, beside the migration that already tidies this same file, and
takes two rules because neither catches everything:

  * the **name** is a list artifact - `<base>-<date>.zip|.rar`,
    `<base>-FULL-<date>.txt` - which finds them wherever they were keyed.
  * the **key** is an absolute path inside `LOCAL_LIST_DIR`, which finds them
    after the bot has been renamed, since the name rule is anchored on the
    *current* `LIST_BASE_NAME` and an old row no longer matches it. That
    directory holds the further lists' subdirectories too, so one check covers
    every list a bot serves.

Nothing served can legitimately be keyed by an absolute path: a library file
is keyed by its label and the path beneath it, which is the whole point of
`library_count_key()`.

It runs on **every** boot rather than once, and is a no-op the moment there is
nothing to remove - an operator restoring an old `download_counts.json` should
not get the rows back permanently. Same posture as its neighbour otherwise: it
never raises, because these counters describe history nothing else reads, so
losing them is cosmetic and refusing to boot over them would not be.

**The totals are untouched.** "Files sent" and the byte counters still include
the list, because they are a record of what the bot actually sent and it
actually sent it. Only the "which of my files do people want" table stops
counting it.

Seven mutants, and one of them earned its keep: removing the **name** rule
from the sweep passed everything, because every fixture row was also keyed
inside the current `LOCAL_LIST_DIR` and the path rule caught them all. The
case that rule exists for - an operator who repoints `LOCAL_LIST_DIR`, leaving
rows keyed under a directory the daemon no longer knows about - had no test.
It does now.

---

### 🟢 A fetched list can be deleted

Nothing ever removed an entry from `config.fetched_bot_lists`: no TTL, no cap,
no route, no button (#385). A list fetched once stayed in the List Browser
forever. The asymmetry is with the *advert* registry, which has had exactly
this housekeeping for ages - `_prune_known_bots()` in `irc.py`, a TTL plus a
count cap, evicting oldest-`last_seen` first - while the store the browser
actually reads never got any.

**The manual action first, and possibly only.** It is exact, it needs no new
state, and it removes the row **today** rather than in N days. It also covers
what a timer structurally cannot: a bot that renamed, a channel you have left,
a list fetched by mistake, or one bot recorded twice because it reconnected on
its alt nick - which is what produced the screenshot on the issue, and which a
TTL would clear in N days and then let come back on the next collision.

**Three stores go together or the operation is a lie.** The entry, or the
sidebar offers a list whose files are gone. The extracted directory, or the
disk never comes back - which is the whole point for anyone whose fetched
directory has outgrown what they meant to keep. And the search index rows,
which are **not** a correctness problem (`search_index()` already restricts
its answer to lists currently held, because *"returning a row for a list we no
longer have would offer a file that cannot be requested"*) but are roughly as
large again as the lists they describe. A purge that skipped them would
quietly keep the largest part of what it claimed to remove.

**The whole bot, not one list.** `fetched_bot_lists` is keyed by nick and a
single entry carries every list that bot's archive held - and they came out of
one zip into one directory, so there is no per-list thing to remove even if
the store were shaped for it. A `<nick>/<marker>` source is therefore resolved
to its nick rather than refused: the row an operator clicks is a list, the
thing that can be deleted is the bot.

**The extra markers are read from the INDEX, not from the entry** - which has
already been deleted by then, and which can drift from the index anyway: a
re-fetch that returned fewer lists than the one before it leaves the old
markers behind, and those are exactly the rows a purge exists to remove.

#### Refusals, and one that is deliberately unlike its neighbour

`__own__` and `__own__:<name>` are refused: those are not fetched from
anywhere and their files are the library. Reaching the function at all would
be a UI bug, so it answers rather than assuming it cannot happen.

A fetch **in flight** is refused, including a `pending` one - and that is the
interesting case, because `build_fetch_delete_result()` right next door
*allows* a pending row to be deleted. They are different questions. Deleting a
pending row removes the thing that would have started; purging leaves it
queued and pointed at a directory that has just gone, so it would recreate
what was purged a moment later. That reads as the purge having silently
failed, which is worse than refusing.

**409 rather than 400** for that case: the request is well formed and would be
valid in a moment, which is what that code means, and what lets the page say
"try again" rather than "that was wrong".

**A directory that will not delete does not block the entry's removal.** The
row is what the operator asked to be rid of, and leaving it would leave them
with a row they cannot remove at all. The detail string says what was left
behind rather than claiming the whole thing worked.

#### Two things worth writing down

The path is derived by `list_extract_dir()`, which sanitises the nick and
`is_safe_path()`-checks the result - so nothing attacker-reachable builds it.
It is checked **again** before the `rmtree` anyway, for the reason
`build_fetch_delete_result()` gives about its own delete: download was
protected twice and delete, the destructive one, only once.

A test patched `shutil.rmtree` to make the delete fail, and took the whole
fixture down with it - `shutil` is shared, `tearDown` runs *before*
`addCleanup`, and the harness cleans its temp trees with that same function.
The stub replaces `list_fetch`'s own reference instead. Worth remembering the
ordering: a cleanup registered in the test body cannot restore something
`tearDown` is going to use.

#### It sits on #388's primitives, and fixes a leak in one of them

#388 landed the **bulk** purge - "every bot showing the red dot" - first, and
with it two primitives this one now calls instead of repeating:
`list_fetch.forget_bot()` and `dcc_fetch.has_any_outstanding_request()`. The
latter is the better-placed of the two versions we each wrote: it lives in
`dcc_fetch` where the queue does, and it counts plain `file` rows, which the
private one here reasoned about without naming.

What is left here is only what a **per-list** purge needs and a bulk one does
not: resolving a clicked `<nick>/<marker>` row to its bot, refusing
`__own__`/`__own__:<name>`, and the HTTP shape (404/409/400).

**`forget_bot()` leaked the extra lists' index rows.** It dropped
`list_index.drop_bot(bot)` - the bare nick - but `_measure_extra_list()`
indexes each further list under `index_key(bot, marker)`, `"<nick>/<marker>"`.
So a bot whose archive held a films or series list kept those rows forever,
pointing at files the same call had just deleted:

    indexed before : ['somebot', 'somebot/films']
    indexed after  : ['somebot/films']

Not a correctness problem - `search_index()` already restricts its answer to
lists currently held - but the index runs roughly as large again as the lists
it describes, so on a multi-list bot the leak is **most of the space the purge
had just claimed to free**, which is the entire point of the operation.

Its own tests could not see it, and that is the part worth keeping: they seed
a single bare-nick index entry, so `assertNotIn("otherbot", indexed_bots())`
passes while `"otherbot/films"` survives - a *different string*. A fixture
without a marker list in it cannot ask the question. Two tests here do, and
the shipped behaviour is one of the mutants they kill.

`| {""}` on the marker set, because an entry written before an archive could
hold more than one list has no `lists` key at all and the bare nick must still
go. And the `rmtree` stays wrapped **at the call site** rather than in a
hoisted variable: `tests/test_list_fetch.py` walks the AST of every
`rmtree()` in the module to check exactly that, and a local caught it
immediately.

**`ignore_errors=True` now reports what it hid.** A directory that will not go
- a file held open on Windows, a permission problem on a share - left the
operator with "purged" and the files still there, which is the one outcome
nobody can act on. The entry still goes either way, because leaving a row
nobody can remove is worse than leaving files somebody can delete by hand.

#### A collision with #388 that git would have merged cleanly

#388 lands a **bulk** purge - "every bot showing the red dot" - from its own
toolbar button. The two are complementary and the ids differ, but both
branches reached for the same obvious name in `app.js`'s element map:
`filelistsPurgeBtn`.

A duplicate key in a JavaScript object literal is **not an error**. The last
one silently wins. So the merge is textually clean, nothing conflicts, no
console error appears, and one of the two buttons simply does nothing - with
nothing in any diff to look at. Whichever PR merged second would have broken
the other.

Renamed here to `filelistsPurgeListBtn`, and a guard added
(`TheElementMapHasNoDuplicateKeys`) so the next pair of branches that both
want `el.somethingBtn` fails a test instead of shipping a dead control. That
guard immediately found a second symptom of the same mutation: the duplicate
also pointed at an element id the page does not define, which
`test_every_element_the_script_looks_up_exists` catches independently.

Eleven mutants killed, including the two that matter most - the index rows
left behind, and the extra markers left behind while the main list goes.
### 🟢 The one-listener test raced with itself

`test_ten_concurrent_offers_open_one_listener` failed on **ubuntu/3.10 alone**
while the other five legs passed, on a commit whose only changes were a
changelog, a JavaScript rename and a test guard - nothing within reach of a
socket or a thread. The runner was reporting `[Errno 24] Too many open files`
a few lines above it.

The defect was in the test, and it is worth writing down because the shape is
common. It started ten threads, **slept 0.2s**, then released the one that had
won the lock:

    for thread in threads: thread.start()
    time.sleep(0.2)
    release.set()

`_listening` is cleared in `_listen_and_serve()`'s `finally`, so the instant
the winner returns the gate is open again. The sleep is a bet that all ten
threads get scheduled inside 200ms. They do on an idle machine. On a runner
that was out of file descriptors and contending for CPU, one straggler had not
reached the check yet - so it arrived after the winner had already released,
passed the gate honestly, and made the count 2. **The code under test was
correct the whole time.**

It now waits for the condition instead of for the clock: nine threads having
*finished* is exactly "every loser has already been turned away", since a
refused thread returns immediately.

    while time.time() < deadline:
        if sum(1 for t in threads if t.is_alive()) <= 1: break
        time.sleep(0.01)

**With a guard on the guard.** The wait has a deadline, and a machine slow
enough to blow it would otherwise reach the original assertion with the losers
still pending and pass for the wrong reason - which is the exact failure this
test exists to catch. So it asserts the wait *succeeded* first.

Verified both directions: removing the `_listening` check in `adminchat.py`
still fails both tests in the file, and thirty consecutive runs under
four-way CPU load pass, where the old shape is what CI tripped over.

Worth remembering as a rule: **a sleep before an assertion is a bet on the
scheduler, and CI is the machine that collects.** Wait for the state that
makes the assertion meaningful, and assert that the wait worked.

---

### 🟢 Tell me what I missed

The Console, the debug channel and the admin chat all carry the same fan-out
from `announce.send_debug()`. That is a **log**: everything, in order, which is
the right shape for reading back what happened and the wrong shape for "did
anything go wrong while I was asleep". Everything is in it, so nothing stands
out.

This is the other shape, beside it rather than instead of it. A badge in the
status panel, hidden entirely when there is nothing unread, opening a panel
that lists what happened newest first.

**Raised explicitly, never inferred.** The handful of call sites that KNOW
they are one of these events pass `notice="warning"` or `notice="error"` to the
same `send_debug()` call they already make. Inferring from the log category
would be wrong in both directions at once: `BAN` and `MUTE` are DCCore banning
USERS, which is routine and would flood the badge into uselessness, while the
three things actually worth surfacing all sit under `INFO` with a hundred
ordinary lines. Taking it as an argument to `send_debug()` rather than as a
second call beside it means a caller cannot log an event and forget to raise
it, or raise one worded differently from the log line next to it - one string,
used twice.

**Two severities, and no third.** `warning` happened and is over (kicked, and
a rejoin is already scheduled); `error` is still true (gave up on a channel;
the list will not build). A third would be a level nobody can tell apart from
the other two at a glance, and a glance is the only moment a badge is read.

**The read marker is an id, not a count.** The page remembers the highest id it
has been shown and asks "anything above this". A count breaks the moment two
events arrive between two polls. Ids are monotonic and never reused, which
means they come from the last entry rather than from `len()` - the same number
until the 200-entry cap starts discarding, and different forever afterwards.
That distinction is not theoretical: a mutant replacing it with `len()`
**survived the first run of these tests**, because the test recorded three
notices where the count and the highest id were both 3. Under that mutant the
badge would never clear no matter how many times it was clicked. The test now
asks where the two differ.

**`notice_state` is a dict holding one integer, and the dict is the point.**
Only mutable objects can be bound onto `defaults.py` by reference. A plain int
there would be a copy, so the dashboard marking notices read would update a
number the daemon never sees, and the badge would come back on the next poll.

**Both containers satisfy all four runtime contracts** - in `runtime.py`, bound
onto `defaults.py`, in `commands.PRESERVE_RUNTIME` (a rehash is not an
acknowledgement), and reset by `tests/support.py`. The clean-interpreter check
added after the `kicked_channels` crash walks every container in `runtime.py`,
so it covered these two the moment they existed, without a line being added
to it.

**Persisted on every write, not on a timer.** The events worth a badge include
the ones immediately before the process dies. The notices and the read marker
go into one file because they are one fact: written apart, a crash between the
two writes could leave a `seen_id` higher than any surviving notice, which
silently swallows everything in between - and the operator is never told what
they were never told about. A file that will not parse costs an empty panel,
never a refusal to start, and rows are filtered individually so one
hand-edited entry does not take the other hundred and ninety-nine with it.

#### Three defects the existing tests found before a reviewer could

1. **`hidden` did not hide.** `.status-row` sets `display`, and `hidden` is an
   attribute the UA stylesheet gives `display: none` at the lowest specificity
   there is - so the class rule outranks it and the badge would have been on
   screen from first paint, permanently, saying nothing. `HiddenActuallyHides`
   in `tests/test_web_assets.py` has been sweeping every element carrying that
   attribute since `.filelists-filter-actions` did the same thing.

2. **The badge opened a view that could not exist.** The handler called
   `showView()`, which is not a function in this codebase - the router is
   `activateView()` - and the `views` table had no `notices` entry, so
   `views[name].title` would have thrown on undefined and taken the whole
   router down with it, not just that one view.

3. **The startup hunk sat at column 0**, which ends `startup()` early and
   leaves the line after it an `IndentationError`.

#### One test relaxed, and made stricter in the same edit

`test_every_section_is_reachable` asserted that every view section has a nav
button, because a section nobody can reach is dead markup that still costs a
lookup on every switch. "What happened" is deliberately not in the nav rail: it
is somewhere you are SENT when something happens, not somewhere you go looking,
and a permanent nav entry for a page that is almost always empty is a permanent
reminder of nothing.

So reachability now means *a nav button **or** an `activateView("name")` call
somewhere in the script* - and the new `test_the_openers_are_real_views` checks
the other direction, that any name passed to `activateView()` is a real `views`
key. Net, that is one more invariant than before, not one fewer: defect 2 above
would now be caught by the tests rather than by reading the diff.

---

### 🟢 The scan asks the filesystem once, not twice

`os.walk` is built on `os.scandir`, which gets each entry's size from the
directory enumeration - and then throws it away, because os.walk's contract is
names only. The scan then asked `os.path.getsize()` for a number the
filesystem had just finished telling us: **one redundant syscall per file**,
and on a network share one redundant round trip per file.

Measured, same tree, same answer from both:

    the walk alone   0.356s -> 0.095s   3.8x   (20,000 files, local SSD)
    a whole rebuild  2.51s  -> 1.56s    1.61x  (30,000 files)

**Both figures are recorded on purpose.** Quoting the 3.8x alone would
overstate it: writing and packing are the rest of the job and this does not
touch them. The walk's share is what grows on a network drive, where the
second ask is a round trip rather than a cached answer - and the library this
was written for is 799,438 files on a mapped drive, where a rebuild takes
fifteen and a half minutes.

**The risk was never the speed.** This is the loop that decides what the bot
hands out, so the tests that matter are the ones asserting the new walk answers
*identically* to the old one - same files, same sizes - across an ordinary
library, an empty directory, a deep tree, a zero-byte file and awkward names,
with a guard on the guard because two empty dicts compare equal.

Traversal order differs and cannot matter: `all_files_data` is sorted by
(folder, filename) before anything is written.

**Symlinks are three decisions, not two, and collapsing two of them was a real
bug that CI caught.** `entry.stat()` **follows**, exactly as
`os.path.getsize()` did, so a symlinked track still reports its target's size.
The other two look like one question and are not:

    is_dir()       FOLLOW  - to CLASSIFY. A symlink to a directory IS a
                             directory; os.walk puts it in `dirs`, where a
                             caller never sees it as a file.
    is_symlink()           - and then do not DESCEND, os.walk's followlinks=
                             False default. A link back up the tree would
                             otherwise walk forever.

The first version asked `is_dir(follow_symlinks=False)` and used the one
answer for both. That answers **False** for a symlinked directory - so the
walk classified it as a *file*, stat'd it, and would have published a
directory as a downloadable entry in the list. Not a crash, not a hang: a
wrong list, quietly.

**Why the local run did not catch it.** The test that proves this needs a real
symlink, and creating one on Windows needs Developer Mode or elevation, so it
is written to skip where it cannot. That is the right call - the alternative
is a suite that fails on an ordinary developer machine for a reason that is
not a defect - but a skip is a hole, and this machine is Windows. The bug
survived the full suite, a preflight and a seven-mutant run here, and failed
on all six Linux CI legs.

So the skipping test now has a companion that runs **everywhere**. A
`DirEntry` is a small enough surface to stand in for: the walk asks it exactly
`is_dir()`, `is_symlink()` and `stat()`, and a fake that answers those three
the way a symlinked directory does covers the same decision on the platform
most likely to get it wrong again. The fake's `stat()` raises rather than
returning a size, so "it was treated as a file" is an explicit failure rather
than a number that looks plausible.

One further trap, for the third time this release: the source-level guard that
pins these decisions reads `walk_with_sizes`'s body, and both the docstring
and the comments there **name the wrong call in order to explain why it is
wrong**. A search over them matches the explanation instead of the code - this
guard passed on the docstring first, then failed on a comment. It strips both
now and reads only the code.

**Four test stubs had to move**, and that is the honest cost of the change:
they injected failures at `os.path.getsize` and `os.walk`, which the scan no
longer calls, so they would have sat on functions nothing reaches and passed
while proving nothing. They hook `walk_with_sizes()` now - narrower than
patching a stdlib function globally, which had been reaching `shutil.rmtree`
and the harness's own cleanup. The flattening stub got simpler: it yields name
and size together, so the `getsize` shim it needed for names that cannot exist
as real files is gone.

Seven mutants, all caught. Three survived first, and each was a different
lesson:

- the unreadable-subtree test passed **by luck of traversal order** - with the
  bad directory visited last, abandoning the walk on an error changed nothing.
  Two failing directories and a count is order-independent.
- both symlink decisions were only covered by a test that **skips** where
  symlinks need elevation, which is the machine most likely to run it.
- and the source-level guard added to cover that matched the **docstring**,
  which quotes the decision verbatim to explain it. It reads the code with the
  docstring stripped now - the same trap this project has recorded before.

### 🔴 "No background" was not what it did

From the beta, against the new colour picker: *"something is wrong with color
preview in website"*, then the sharper version - *"why half message is shown in
red and other half in blue"*.

**Nothing was wrong with the preview.** It was showing exactly what the channel
would see, which is what it is for. What was wrong was the menu that produced
the setting.

The background dropdown offered **"No background"**, and that is not what
choosing it does. A colour code carrying only a foreground leaves the
background as the previous segment set it - the IRC formatting spec, not an
implementation quirk:

> If only the foreground color is set, the background color stays the same.

Checked against the spec before touching anything, because the alternative
was that the renderer was wrong, and a preview that lies is worse than no
preview.

**Why the message split in three**, which is the part worth writing down. The
advert opens

    {BORDER} {SEPARATOR} {TEXTBOX} Type: ...

and every later section is

    {SEPARATOR} {BORDER} {TEXTBOX} Slots: ...

with the two blocks **in the opposite order**. So the first field inherits the
separator's colour and every later field inherits the border's - the blue half
and the red half. The stretch between them lands on the client default,
because a reset clears both and a foreground-only text box never puts one
back. Decoded from the real line rather than guessed:

    royal blue     Type:  @DCCore
    default        For My List Of:  719,041  Files (5.48 TB) created  Sep 7th
    red            Slots: 3/3 - Queued: 0 - Speed - Total Sent - Search: ON

**The fix is the label, and a warning where it matters.** The option now says
**"Keep previous"**, which is what it does. And three of the six roles are a
surface rather than a text colour - `theme.py` already says so: border is *"the
outer block that frames a section"*, separator *"the block between fields"*,
textbox *"the plate the text sits on"*. A block with no background is not a
block. Those three warn when a foreground is set without one; `value`, `alert`
and `accent` colour figures and secondary text, where a foreground alone is
exactly right and a warning would be telling the operator off for using the
feature correctly.

**Every shipped preset gives all three surfaces a background** - which is why
no preset has ever shown this, and why the picker letting one through was the
defect. That is now a test, so a preset cannot quietly acquire the same
problem.

The test file reads the rendered line with a small mIRC parser rather than
asserting on the codes, because the claim under test is about what a CLIENT
does with them; asserting the codes would only restate the template.

Seven mutants, all caught. One survived first, and it was the recurring shape:
the warning's guard was replaced with `if (true)` and every assertion about
the function's contents still passed, because they were all still there as
dead code. The guard is named now.

### 🔴 The advert owns the trigger; the sender owns the identity

Seen in a live console:

    [ADVERT] SomeBot- advertised as 'SomeBot' - ignoring; the sender is the authority
    on who a bot is.

The log line is right about the principle and the code was applying it to the
wrong field. **SomeBot's RAR list was never learned at all.**

**What the advert actually says:**

    <+SomeBot-> Type @SomeBot^ to get my list of 39,454 (5.48 TB) RAR folders

Sender `SomeBot-`, trigger `SomeBot^`. `_parse_rar_folder_advert()` took the text
inside the trigger and returned it as `nick`, the caller compared that against
the sender, they differed, and the whole advert was discarded.

That worked for the three bots it was written against, whose triggers happen
to be their nick with a `^` on the end. Its docstring said so outright: *"PackBot
sends this, 'PackBot^' does not exist."* True for PackBot. Not a rule.

**The trigger is configurable and is not a name.** From the operator who
reported it: mx.rarserver's default is `@<nick>^` *"but he could have w/e"*.
So it cannot be derived from the nick, and the nick cannot be derived from it,
in either direction. Two facts were being carried in one field.

- The **sender** is the identity, exactly as the log line claims. The parser
  returns `nick: None` now - "this advert makes no claim about who sent it" -
  and the caller skips a comparison it has nothing to compare.
- The **advert** is the authority on the trigger, captured verbatim as a
  token. The regex no longer requires a `^`, because matching on that was
  matching on somebody's default.

**The other two wordings still have their name checked.** OmenServe and SPQR
put the bot's own nick in the text, so a mismatch there really is one bot
advertising as another, and that check is what refuses it. Relaxing it for one
family must not relax it for those, which is a test.

**Why relaxing it is safe here**, checked rather than assumed: a forged trigger
would be recorded under the *forger's* key, and
`dcc_fetch._claim_matching_offer_locked()` matches an incoming DCC offer
against the bot the request went to. An offer from anyone else is already
rejected as unsolicited. The residue is that DCCore could be made to type an
odd `@string` into a channel it is already in - so the trigger is validated for
shape before it is kept, and a trigger that could not be sent safely is
dropped while the bot is still recorded as publishing a RAR list. Only the
shortcut is lost.

**What this does not do yet.** A list fetch still sends `@<nick>`, so the
stored trigger is not used to *ask* for a RAR list - it is recorded, and
`bot_publishes_a_rar_list()` now answers correctly for bots like SomeBot that were
previously invisible. Asking by trigger is a separate change with its own
request type.

Seven mutants, all caught. One survived first and the fix was not to delete the
check it exposed: a trigger with a space cannot reach `_TRIGGER_RE` through the
parser, because the advert regex captures `\S+`. The validator guards a value
that ends up in a PRIVMSG, and what may reach it is the caller's business today
and not necessarily tomorrow - so it is exercised directly instead, on its own
terms.

### 🔴 A list that cannot produce an album no longer ships an album section

Issue #383, a follow-up to #288 which is otherwise done. A video-only list was
building and delivering a `-RAR-` section that could never hold a row.

**Verified rather than taken:** `LIST_VIDEO_EXTENSIONS` and `RAR_EXTENSIONS`
share not one entry, so since #288 - which made a folder earn its `!rar` row by
holding a packable file - a video-only list's album section is not *usually*
empty. It cannot hold a row for any library under any configuration.

**And it did not sit there inertly.** The masthead makes the file non-empty, so
the `getsize > 0` test that decides what goes into the downloadable archive let
it through: four dead lines and a heading with nothing behind it, inside every
copy every user downloads.

**Decided after the scan, not from the extension sets.** #383 suggested asking
whether the list's configured extensions admit packable content. What actually
matters is whether this list *produced* a packable folder, and the scan has
just finished answering exactly that - where reasoning from the extensions
would be predicting it, and would be wrong for a list whose folders merely hold
no albums today and might tomorrow.

**And it reuses `serve_albums` rather than adding a flag**, which is what makes
it two lines. Every consequence is already written for `RAR_ENABLED` being off:
the masthead is skipped, no rows are written, the empty temp file fails the
archive's size test, and the `if not serve_albums:` branch removes it instead
of publishing it. "This list has no albums" wants all four and nothing else.

The two paths still say different things in the log - *"RAR_ENABLED is off"*
and *"No folder in this list can be packed"* - because they call for different
actions from whoever reads them.

**An existing test asserted the weaker version** and has been moved to the
stronger one: `test_an_empty_packable_set_makes_nothing_packable` checked that
the album list was written with no rows. It now checks that it is not written.
Both say nothing is packable; the file's absence says it better.

Four mutants, all caught. And the first version of the new test failed for a
reason worth recording: `TempTree` seeds two baseline `.flac` tracks, so a
"video-only" library that merely *adds* films to it still holds two albums -
the album list was correctly published and the test was wrong. It starts from
`use_empty_library()` now.

### 🔴 The list failure reported the one line that never explains anything

Reported from the same install as the code-page fix above, and only visible
because that fix landed first: `!update` failed with

    Failed: --- ERROR: could not generate the list. ---

which says the run failed - something the exit code already said - and never
why.

**"Take the last line" was right when it was written, and quietly stopped
being right.** `commands.subprocess_failure_message()` reaches for the last
line of the child's output because `update_list.py` reports through plain
`print()` to stdout, and its own summary used to land there. Its `__main__`
now prints a generic banner **after** everything else:

    [LIST-GEN ERROR] 'Music' failed: [WinError 3] The system cannot find the path specified
    [LIST-GEN ERROR] These lists were not rebuilt and are still serving what they last built: 'Music'
    --- ERROR: could not generate the list. ---      <- always last

So the reason was captured and thrown away, every single time, and the one
line guaranteed to be useless was the one shown.

**The first tagged line, not the last.** `[LIST-GEN ERROR]`, `[CRITICAL]`,
`[LIST ERROR]` and `[UPDATE ERROR]` mark a real explanation; the first of them
is the root cause and the ones after it are consequences - "These lists were
not rebuilt" is true, follows from whatever broke the first, and names
nothing. With no tagged line at all - a traceback, say - the last line is
still the best guess, minus the banner that would otherwise always win. And a
banner on its own still beats returning nothing, which would read as a run
that produced no output.

**The old test asserted the defect.** It passed a `Permission denied` through
and expected the trailing status line instead:

```python
self.assertEqual(
    msg, "[LIST-GEN] The previous list was left untouched and is still in use.")
```

Written to prove the stdout fallback worked, which it did, it also wrote down
"the last line is the answer" as the expectation - so the behaviour could not
change without the test objecting, and the test was wrong.

Five mutants, all caught. One survived first: the `[CRITICAL]` case passed a
stdout whose only line was the `[CRITICAL]` one, so the untagged fallback
returned it whether the tag was recognised or not. It has a line after it now,
which is what makes the tag decide anything.

### 🟢 No bold anywhere, and the channel need not be told

Two things asked for together:

> Should be able to hide that message if you don't want to send public
> messages. In settings. Also theme shouldn't have bold in any location of the
> message. That's for everywhere bot advertisement answers to find requests
> etc. No bolds

**One public message, and now it is optional.** A send produces four messages
and only one of them is public:

| message | to | |
|---|---|---|
| queue position | the requester | private NOTICE |
| "Sending" | the requester | private NOTICE |
| `DCC SEND` | the requester | private CTCP |
| **"Sent: ... To: ..."** | **the channel** | **public PRIVMSG** |

`ANNOUNCE_TRANSFERS` gates the last one and nothing else. Turning it off does
not make a request go unanswered: whoever asked is told exactly what they were
told before. It is the channel that stops being told afterwards.

**And it does not take the operator's own log with it.**
`send_transfer_complete()` also writes the debug line that records the send,
and the obvious implementation - an early return - would have silenced both.
Somebody who does not want the channel told is not somebody who wants to stop
seeing their own transfers, so the gate is around the channel send alone.
Turning it off still prints a line saying the send happened and that the
notice is off, because silence in the operator's own console is how a setting
gets blamed for a bug.

**No bold, anywhere.** Fifty markers across eight outbound paths: the advert,
the completion notice, `@find` results, the private notices, the pack error,
the debug channel. `theme.BOLD` stays defined - theme.py's own note is that
bold and reset are IRC control characters with fixed meanings, and that is
still true - and `blocks()` still returns eight values, because renaming an
unpacking eight call sites share would have been churn to say nothing.

**The golden fixture moved, and how it moved is the point.** `tests/_golden_
palette.py` pins every outbound path to exact bytes, so removing bold failed
six subtests, which is the fixture doing its job. Regenerating it by
re-capturing would have been the easy way and the wrong one: a fresh capture
absorbs anything ELSE that has drifted since, which is precisely what the file
exists to prevent.

So the difference was proved first - drive every path, strip `\x02` from each
stored line, and check that this reproduces the new line **exactly**, for all
eight:

    ok    send_transfer_complete       bold removed: 14
    ok    send_dcc_sending_notice      bold removed: 4
    ok    send_search_result_header    bold removed: 12
    ok    list.execute_search          bold removed: 12
    ...
    every path differs by bold alone

and only then was that same transformation applied to the fixture. Its
docstring now records that the baseline moved once, deliberately, and what has
to be proved before it moves again.

Eight mutants, all caught - including the one that matters most here: gating
the debug line along with the channel notice, which every test that only
checks "the channel went quiet" would have passed.

### 🔴 Kicked from a channel, and nothing noticed

Reported from a live channel:

> a slight bug there, dccore doesn't appear to rejoin a chan if kicked or
> banned, maybe add an option that it can try to rejoin when the advert timer
> triggers

**It was worse than not rejoining.** `KICK` was not parsed anywhere, so DCCore
did not know it had left. It went on advertising into a channel it was not in
- the server answers those with 404 and nothing reads it - and never asked to
come back. `474 ERR_BANNEDFROMCHAN` was not parsed either, so a refused join
was equally invisible: the bot could be locked out of a channel for weeks with
no sign anywhere.

**The retry rides on the advert timer**, exactly as suggested, and that is the
right cadence for a reason beyond convenience: an instant rejoin reads as a
fight with whoever kicked us, and is how a kick becomes a ban. The advert
interval is already the bot's own rhythm, and it is the moment it was about to
speak there anyway - so no new clock, and nothing to tune.

**Giving up is the point, not retrying.** A channel answering "you are banned"
will answer that way for as long as the ban stands, and a bot that keeps asking
is a bot that earns a longer one. After `REJOIN_ATTEMPTS` refusals (3, or 0 to
never rejoin at all) DCCore stops trying that channel and says so.

Four numerics count as a refusal - `471` full, `473` invite-only, `474`
banned, `475` bad key - because each will keep being a refusal until somebody
changes something on their side, which is what makes a bounded retry the right
shape rather than a backoff.

**What it will not do:**

- chase a channel that is not in `CHANNEL`. Somebody invited the bot
  somewhere, or the operator has removed it since; rejoining would be the bot
  deciding where it belongs.
- count a refusal for a channel it was not already trying to return to. That
  would turn an ordinary failed JOIN into the start of a retry schedule
  nobody asked for.
- treat a later kick as a continuation. The count is CONSECUTIVE refusals, so
  a channel that let us back in and threw us out months later starts again.

**Where the rule lives.** `irc.py` counts and decides; `announce.py`'s worker
asks it what to send. The read thread must never block on a socket write, and
the advert worker must not hold a lock or know what a kick is - so the worker
carries no limit, no count and no comparison of its own. A test asserts that,
because two copies of the rule could disagree about when to stop.

**A mutation run found dead code**, which is worth recording because the two
guards look identical and only one does anything. `channels_to_rejoin()` had
an early `if limit <= 0: return []` above a comparison of `refusals < limit` -
false for every entry that can exist when the limit is 0, so the guard was
unreachable. In `gave_up_on()` the same line is load-bearing: there the
comparison is `refusals >= limit`, TRUE for everything, so without it an
operator who turned rejoining off would be told the bot had "given up" on a
channel it had never tried. The dead one is gone, the live one is documented,
and the asymmetry is a test.

Eleven mutants, all caught.

### 🔴 One accented filename stopped the list rebuilding

Reported from a live install on a Greek Windows box:

    External update_list.py failed (Exit Code 1): Unknown script error
    ...
    File "C:\\Python314\\Lib\\encodings\\cp1253.py", line 23, in decode
    UnicodeDecodeError: 'charmap' codec can't decode byte 0x8d in position 563

**Two failures, one cause, and they hid each other.**

**The child.** `update_list.py` runs as its own process - the daemon starts it
with `subprocess.run()`, `configure.py` runs it directly - so `oserve.py`'s
console guard, which has protected the daemon since it was written, did nothing
for it. Every line it prints is a path off somebody's disk. On a console whose
code page cannot represent a character in one of those paths, `print()` raises
`UnicodeEncodeError` and the scan dies where it stood. One accented filename is
enough, which is to say: most music libraries.

**The parent.** `subprocess.run(..., text=True)` with no `encoding` decodes the
child using the same locale code page. So the bytes that did escape killed
`subprocess`'s own reader thread, and the run reported *"Unknown script error"*
- because **the output that would have explained it is exactly what could not
be read**.

The operator got a failure with no cause, for a library that was fine.

**The fix is the one that already existed, applied where it was missing.**
`platform_compat.install_console_encoding_guard()` has been the answer since
`oserve.py` called it. `update_list.py`, `configure.py` and `adminchat.py` are
entry points too and never did - three of the four. Verified rather than
reasoned about: the same print, in a child with the code page forced, exits 1
with `UnicodeEncodeError` without the guard and 0 with it.

The parents now decode `utf-8` with `errors="replace"`, so a child that does
not guard itself still cannot take the daemon's report away with it. That
matters most for `rar`, which is not Python and cannot be guarded at all - a
pack that failed for a nameable reason must not become a pack that failed
silently.

**The class guard is the part that keeps this fixed.** A test walks every
module carrying a `__main__` block and fails if it does not install the guard,
so a new entry point that forgets is a failing test rather than a bug report
from somebody's channel. A second sweep parses each captured `subprocess.run()`
and fails on any that does not name both an encoding and an error handler -
`text=True` alone means "decode with whatever the console uses", which is the
whole bug.

**This is the code-page hazard for the third time** (after the loopback probe
and MAX_PATH), and the first time it has been caught by a rule rather than by
an operator.

Nine mutants, all caught.

### 🟢 A flaky test asserted a number nothing promised

`test_missing_rar_charges_the_row_rather_than_looping` failed once on
ubuntu/3.12 with `3 != 1` - the first failure in twenty-five CI runs that day,
green on a re-run of the same commit, and unrelated to the branch it appeared
on.

**The exact count was never a contract.** A failed send reaches
`release_queue_entry()` by more than one path, and they do not all charge the
same amount. `start_dcc_send()`'s missing-file branch deliberately charges,
sleeps three seconds and calls `check_queue_and_send()` again - its own comment
says why: *"charging the budget is what stops it being re-selected every three
seconds forever"*. One request down that path walks a row to the budget on its
own. Which path a failure takes depends on how far the pack got before it died,
which is timing, which is why a loaded runner sees a different number from a
laptop.

The test now asserts what it is named for and what every path guarantees: the
failure lands on the retry budget, and **the budget is a ceiling**. A second
test covers the half nothing asserted - that the charges stop and the row
leaves the queue, because charging is only a bound if something ends. A row
re-selected forever at the cost of a queue slot is the same failure wearing a
counter.

**This is a strengthening, not a loosening**, and that was measured rather than
claimed. The same four mutants run against the old test and the new one:

| mutant | old | new |
|---|---|---|
| the failure is never charged at all | caught | caught |
| the budget is not a ceiling - it climbs forever | **survived** | caught |
| a row that used up its budget stays in the queue | **survived** | caught |
| the charge is one higher than the attempt | caught | caught |

Two of the three things this test exists to prevent were unguarded by it.

**What is not claimed:** the exact sequence that produced 3 on that runner.
Thirty isolated runs and a full local suite would not reproduce it, and two
theories were built and discarded on the evidence - a stale retry thread left
by an earlier test (disproved: this path spawns no such thread, which a
deliberately falsifiable guard reported as `suppressed == 0` rather than
passing quietly), and cross-test interference through the shared fixture nick
(not reproducible under a full-suite trace). The over-specified assertion is a
defect on its own terms whichever path fired, so it is fixed on its own terms -
and the discarded guesses are not written into the code as though they had been
findings.

### 🔴 A list is bound to a channel by picking it, not by retyping it

Issue #368. Identity & network already holds the operator's channel list, and
the Served lists editor asked for each list's channels again as free text -
so the same names were typed a second time with nothing connecting the two.

**It is not the typing that makes this worth fixing**, and the report was right
to say so - but it is worse than it says. `library.list_for_request()` matches
exactly, and its third rule is that once the primary binds ANY channels at all,
a channel with nothing bound to it gets nothing:

    bound (with a one-character typo) : #somechannnel
    list_for_request("#somechannel")  : None
    spelled correctly                 : the list
    nothing bound at all              : the list

So a single mistyped character does not bind one channel wrongly. **It silences
the bot in the real channel** - no advert, no requests answered - with no error
raised anywhere and nothing on the page saying why. That is reproduced against
the real routing as the first class in the new test file, because the whole
justification rests on it and a fix argued from a premise nobody checked is a
fix that outlives its reason.

**The field is now the configured channels, as checkboxes.** They come from
`CHANNEL`'s own settings field, taking a pending edit over the saved value -
the same source and the same precedence the theme preview reads, for the same
reason: a channel typed into Identity & network and not yet saved is still one
the operator means to be in, and offering the stale list would offer to bind a
list to a channel they have just renamed.

**A binding the picker cannot offer is kept, ticked, and marked in red**, with
a line saying nothing arrives from it and what to do about it. That is the part
that matters most for anyone already caught: their channel is silent, the log
says nothing, and `lists.json` looks perfectly well-formed. **That row is the
only place the mistake is visible.** Dropping it silently to make the picker
tidy would have removed the diagnosis along with the symptom - the same rule
the colour picker follows for a code its menus cannot say.

With no channels configured at all, the section says where to add them and that
the list serves everywhere meanwhile - rather than an empty box that reads as
broken. Nothing ticked still means every channel, which is what an empty field
has always meant and what every install today has.

The channel name is assigned as a property and its label set as `textContent`,
never concatenated into the markup: it is operator input and `escapeHtml()`
does not encode a double quote, which is the rule every other row in that file
follows.

The server is unchanged, as #368 says it should be. `lists.json` can still be
hand-edited and is still accepted as written - the picker narrows what can be
CHOSEN, it does not become the rule.

Fourteen mutants, all caught. One survived first time and it was the same shape
as the last three: `assertIn("not in your join list", body)` was satisfied with
the condition replaced by `false`, because the message still sat there in a
branch nothing could reach. The assertion is on the guard now.

### 🔴 The theme preview showed the colour code instead of the colour

Issue #367, reported with a screenshot: picking a custom colour from the new
dropdown put a literal, unstyled `\x0309,07` at the front of the advert
sample and pushed the box off to the right. Every role still on "Theme default"
rendered correctly, which is what isolated it to values that had been through
the picker.

**A colour has two forms and they are one character apart.** It crosses the
wire as the escape TEXT - nine characters - because a browser text input cannot
hold the 0x03 control byte and `settings.conf` should not carry one either.
`theme.palette()` deals in the decoded BYTE, which is what `theme.CLASSIC` and
every other preset holds.

The save path already bridged the two: `settings_file.coerce()` decodes on the
way in. **The preview path did not.** It handed nine literal characters to a
builder expecting a colour code, and `renderIrcLine()` - which looks for a real
0x03 - found none and drew them as text.

**The report's second claim does not hold**, and checking it was the first
thing done rather than the last. It said a saved value would carry the same
text into every outbound line and reach real channels. It would not:
`_check_writable()` coerces before writing, so `settings.conf` gets the
readable `\x0309,07` and config gets the real byte. A test now pins that
difference down, and it is the one test in the new file that passes against
the unfixed code - which is exactly what makes it worth having.

**The fix is one line, and it is which function does the decoding.** The
preview whitelist now coerces through `settings_file.coerce()` rather than a
private copy of the rule: the preview's whole claim is that it shows what
saving would produce, and two functions that merely happen to agree today are
two functions that can stop agreeing. That is what happened here.

**Not** by changing the page to emit a raw control byte, which is what the
report proposed. That would have fixed the preview by undoing the reason the
picker exists - a text input cannot display 0x03, which is what made the
settings box read `13` for a value of `\x0313` in the first place. Two tests
assert the page still speaks the readable form.

**Why the existing tests missed it**, which the report got right: the picker's
own round trip is `parseIrcColour(formatIrcColour(...))`, and both sides speak
the escape text. It is perfectly self-consistent and can never surface the
mismatch. What was missing is an assertion against the REAL byte - against what
`theme.CLASSIC` actually contains - and against the two paths agreeing with
each other rather than each with itself.

The bug arrived with the preview, not with the picker. Anyone who had typed a
code by hand into the box would have seen it; the dropdown simply made custom
colours something people would actually set.

Five mutants, all caught, including the report's own proposed fix.

### 🔴 Served folders, multiple lists and On connect had vanished from the page

From the beta, and the only way this could have been found:
*"uhm, what happend with multi folder / channel? cant see it in settings at
all"*

**Three editors were being drawn on a category that no longer existed.**

They are not settings and cannot be. Served folders and "Serve more than one
list" are ORDERED lists of `{label, path}` validated as a set - two folders can
each be fine and still conflict with each other - and a served list carries its
own `channels` as well as its own `folders`, which is exactly the "multi folder
/ channel" of the report. On connect is a list of commands whose ORDER is the
point. All three live in their own JSON files behind their own endpoints, and
each is then stitched into a settings category so an operator finds it where
they would look.

That stitching is a string comparison against `category.id`. Regrouping the
Settings page renamed the categories, `paths` stopped existing, and all nine
comparisons against it went quietly false.

**Nothing failed.** No error, no empty panel, no console warning. The
categories all rendered; every one of the 94 settings still appeared, and a
sweep for settings that had fallen off the page would have come back clean - it
was run, and it did. The only symptom was three features being reachable from
nowhere, on a dashboard that is the only place they can be reached from.

They are back where they belong rather than where they were: the folder and
multi-list editors on **Your list**, beside the Music directory that is
labelled *"used only when no folders are set"* - the thing that sets them
should not be on another screen from the label that defers to it. On connect
goes to **Identity & network**, beside the server and the channels: those
commands run after registration and BEFORE the JOIN, which on Undernet is the
difference between `+x` hiding your host and everyone in the channel seeing it.

**The two ids are named constants now**, which is worth doing and does not by
itself fix anything - a renamed category still leaves a constant holding a
string nothing matches. The fix is
`tests/test_every_settings_category_the_page_asks_for_exists.py`, which lifts
every category id `app.js` compares against and asserts each is one
`SETTINGS_CATEGORIES` actually defines. It fails on the shipped code, naming
the id and listing the twelve that exist. Neither side can check this alone:
`webserver.py` owns the category list, `app.js` owns the attachment, and
neither imports the other.

It also asserts each editor is drawn AND wired up - rows drawn without their
listeners is an editor that looks like it works right up until Save - and that
the settings themselves have not fallen off, which is the half that was already
fine and is now checked with the half that was not.

Every mutant caught. One survived first time, and it was the failure mode this
project keeps meeting: `assertIn("onConnectSectionHtml()", source)` is
satisfied by the function's own DEFINITION, so deleting the call that draws it
changed nothing. The assertion is now on the call, inside the branch that makes
it.

**And the same shape was swept for everywhere else it occurs**, which is what
was asked for once this was found. Every `getElementById` against the ids in
`index.html`; every `el.*` name used against the ones the map declares; every
view switched to against the sections that exist; every literal API path
against the routes `webserver.py` serves; and every settings category against
what the server sends.

**It found no second instance.** Two things looked like hits and were not: two
CSS classes with no rule of their own turned out to be selector hooks
(`querySelectorAll(".broadcast-check:checked")`), and two API routes that
looked uncalled are built by concatenation
(`"/api/filelists/bot/" + encodeURIComponent(nick)`) so a literal-only scan
cannot see them.

Five of those sweeps are now permanent tests, mutation-checked. The CSS-class
one is not: it cannot tell a missing style from a selector hook, and a guard
that has to be argued with every time it fires is one that gets deleted.

### 🟢 A list with nothing matching leaves the sidebar, and says it has

From the beta: *"names get hidden as you type something that you search lists
for. only the names with result are shown. and you can still select/deselect
results from certain bots if you click on their names and then reclick on their
names (toggle/select)."*

The second half of that **already existed** - clicking a name while filtering
toggles its results, and "Show all lists" / "Show none" do it wholesale - which
makes that half a discoverability problem rather than a missing feature. What
changed is the first half.

**The old behaviour was not simply wrong**, and the argument for it was written
into the stylesheet: the rows were dimmed rather than hidden because *"the
sidebar is also the answer to 'who has this', and a row that vanished would
take that answer with it."* That is right about what matters and wrong about
what to do. Dimming asks the operator to scan thirty rows and judge opacity; a
count states the same fact outright - **"Show 12 with no match"** - and the
button that says it puts them back for anyone who wants to look. The answer is
kept, in a form nobody has to count.

That note has been rewritten rather than deleted, and a test fails if it goes
back to claiming the rows stay. A comment describing a decision the file no
longer makes is worse than no comment: it reads as current.

**Four rows are never hidden**, and none of them is a special case for its own
sake:

- **our own list**, which this filter does not search - it covers lists fetched
  from other bots;
- **the list currently open**, or the table would be showing a list with no row
  for it;
- **the row holding keyboard focus**, because hiding the focused element drops
  focus to the body and loses the operator's place in a list they were moving
  through;
- **all of them**, once the operator has asked to see them.

A row that cannot be hidden is still dimmed - it is on screen, and the fact
that it has nothing is still the thing worth knowing about it. `display: none`
rather than `visibility` or `opacity`, so a hidden row leaves no gap and is not
a stop on the way through the list with the keyboard.

The reveal is a **toggle**, resets on every new term like the per-bot
exclusions already do, and redraws from the answer already in the browser
rather than re-asking the server for rows it has just returned.

Nineteen mutants, all caught.

**A stylesheet tidy-up on the way, and it was mine.** Three comments had been
orphaned from their rules across the two previous changes in this release: each
patch anchored on a RULE and inserted before it, which put the new block
between an existing rule and its own comment. Three ended up stacked with no
rules between them, each sitting above somebody else's - the filter-dimming
note above `mark.filter-hit`, and so on. Cosmetic, but a comment next to the
wrong rule reads as though it describes that rule. All three are back where
they belong.

### 🔴 The settings box could not show the colour it held

The other half of the same beta request - *"select colors/symbols from drop
down menu"* - and it turned out not to be only about convenience.

config holds the DECODED bytes. A role of `\x0313` is four characters, two of
them the 0x03 control code, and the settings page was being handed them raw. A
browser text input cannot display 0x03, so **the field for that accent read
`13`**. The code was there, invisible, and what the operator could see was not
what was set. Retyping what they read would have put a literal `13` into every
advert.

The same value went the other way too. Saving from the dashboard wrote the raw
control byte into `settings.conf` - a file people edit by hand, where a 0x03 is
invisible in an editor and is exactly the kind of thing an editor strips on
save.

`decode_irc_escapes()` had been half a round trip since #170's follow-up: it
turns typed `\x0313` into the byte mIRC reads, and **nothing turned it back**.
`encode_irc_escapes()` is the missing half, applied at both ends - the value
the page is given, and the text that lands on disk. Every control character,
not just the ones a theme uses today, because picking a shorter set means
deciding now which codes a future theme may want. Lowercase hex, so a value has
one spelling: the decoder takes either case, and two spellings of the same
colour would each read as an edit of the other against a baseline string.

On the way out, the escape happens **before** the line-break check rather than
after, so that check weighs what will actually be written - an escaped value
holds no line break at all.

**Then the menus.** Each role gets a foreground list, a background list and a
swatch of what the two make. The background is disabled while the foreground is
"theme default", because there is no such code as `\x03,05`: offering it would
be offering a value that cannot be saved. Codes are written with both digits,
matching every preset in `theme.py` - mIRC reads `\x034` and `\x0304` alike,
but one value should have one spelling for the same reason the hex is
lowercase.

**What the menus will not do** is rewrite a value they cannot say. A code with
bold in it, a number past fifteen, anything hand-written - the field stays a
text box and says why. Replacing an operator's own value with the nearest thing
a dropdown can offer, to make the page tidier, is the one behaviour a picker
must never have.

**The parse rule is tested by being run, not by being read.** It is one regular
expression, and it is the whole decision, so the test lifts it out of `app.js`
and executes it against a table - Python and JavaScript agree on anchors, `\d`
with a counted repeat, and an optional non-capturing group. That found a trap
in the test itself: `re.match()` anchors at position 0 whatever the pattern
says, so a rule that had lost its `^` passed every assertion. JavaScript's
`.exec()` is an unanchored search and `re.search()` is the honest counterpart.

Twenty mutants, all caught. Three needed the tests tightened first - the
anchoring one above, `pad2(` present anywhere being satisfied by the background
while the foreground went out unpadded, and the fallback branch being checked
for its contents rather than for the guard that reaches it.

One extraction on the way: `recordSettingChange()`, because the picker is two
controls that mean one setting and cannot go through the ordinary change
handler. Everything after the value is computed is identical for both,
including the part that is easy to get subtly wrong - a value put back exactly
as it was found is not an edit, and must be removed from the dirty set rather
than stored.

### 🟢 You can see the theme before the channel does

From the beta, on the settings that colour the advert:
*"bot font settings shows become easier. select colors/symbols from drop down
menu and show preview of the messages"*

This is the preview half. The six `CUSTOM_THEME_*` settings hold raw mIRC
colour codes, typed into text boxes. Finding out what a change did meant
saving it, rehashing, and waiting for the next advert - up to
`ANNOUNCE_INTERVAL` away, on a bot other people are using. **The channel was
the preview.** Now the Appearance category draws two sample lines above the
fields and redraws them on every keystroke.

**Two samples, and that is not a presentation choice.** Between them the
advert and the completion notice use all six roles, and neither uses all six
alone: the advert never touches `accent`. One sample would leave one setting
looking as though it did nothing, which is exactly the confusion a preview
exists to end. That was measured rather than assumed, and there is a test that
fails if a role ever stops appearing in both.

**Built by the real builders, not by a copy of their templates.** Two builders
were lifted out of `announce.py` for this - `build_advert_line()` and
`build_transfer_complete_line()` - and the advert loop and the notice now go
through them. A preview assembled from its own copy of those f-strings would
drift the first time one changed and would then lie with a straight face. The
extraction was proved byte-identical against the pre-change file across all
five themes before anything was built on it, and a test counts the templates
so a third copy is a failure rather than a surprise.

**And it never touches config.** The point of a preview is the colour you have
just typed and not saved, so the pending edits travel with the request and
`theme.palette(settings)` reads them in place of config for exactly the keys
they hold. A live daemon is serving a channel while somebody is choosing
colours; the preview must not write to what every other thread is reading.
What may steer it is a whitelist - `THEME` and the six roles, with an unknown
theme name dropped rather than silently falling back to the configured one,
which would preview the wrong thing under a name the operator did not pick.

**Rendering mIRC in a browser** is a small parser: `\x03` with one or two
digits and an optional `,background`, `\x02` bold, `\x0f` reset, everything
else through as text. A comma with no digits after it is a literal comma, not
an empty background - which is how `\x0304,text` is meant to read. Text goes
in through `escapeHtml()` and colours come from a fixed table of mIRC's
sixteen, indexed by a number from `parseInt` - so nothing from a settings box
reaches a style attribute as text.

**The one place a literal colour is correct**, and the stylesheet guard now
says why rather than carrying a name on an ignore list. The preview shows what
the CHANNEL looks like: those sixteen colours are a property of IRC, identical
whether the dashboard is in light mode or dark, because a channel does not
have a dashboard theme. Painting them from the palette would misrepresent the
one thing the panel exists to show. So the exemption is narrow - one selector
prefix and the renderer's own table - and it is a positive statement rather
than a hole: every literal inside it must BE one of the mIRC sixteen, the
panel still takes its FRAME from the palette, the renderer must map all
sixteen, and a guard-on-the-guard fails if the exempt region ever disappears
and leaves the checks passing on nothing.

Fifteen mutants, all caught. Three needed the tests tightened first, and each
was the same shape of mistake: counting occurrences instead of asserting a
property. `refreshThemePreview()` twice was satisfied by the definition plus
one call, so deleting the call that runs on every edit changed nothing;
`IRC_COLOURS[` present somewhere was satisfied by the background branch while
the foreground put a raw number in a style attribute; and a pending change was
only ever checked on the notice, which the advert builder could ignore
entirely. They now name the call site, check each colour push, and override a
role the advert actually uses.

### 🔴 The colour on a bot's name is the one the legend promises

From the beta, looking at a sidebar where every not-downloaded bot was grey:
*"shouldnt names be red of those not downloaded yet?"*

They should. The name had just taken over carrying the list's freshness, and a
rule written for the older design was still there:

    .bot-row[data-held="no"] .bot-row-name { color: var(--text-dim); }

That is specificity **(0,3,0)** against the modifier's **(0,2,0)**, on EXACTLY
the rows the modifier is about. So every not-downloaded bot stayed dim while the
legend beside it promised red - and nothing failed: the class was applied, the
rule existed, the colour was simply outranked.

The override is gone. `.bot-row-name.is-not-held` says the same thing in the
colour the legend names, and one rule saying it beats two disagreeing. What that
rule was really for is kept: a bot with nothing held is not emphasised, so it
sets the WEIGHT and leaves the colour alone.

**A test file rather than a one-line fix**, because this is a class of bug
rather than an incident. A specificity collision is invisible in review - both
rules are correct on their own, and the losing one is present and spelled right
- and silent at runtime. Nothing here executes CSS, so the cascade is computed:
every rule that sets a colour on `.bot-row-name` is weighed, and none may
outrank the four freshness modifiers. The calculator has its own guard, since
one that returned the same number for everything would pass every assertion
using it.

### 🟢 The dot says who is here; the name says what we hold

Two requests from the beta, and they are the same subject.

**We asked a nick that was not on the network.** The server answered the
operator's own WHOIS with "No such nick", and the fetch sat in the queue
holding a slot until it timed out, then reported "no response". True, and
useless: nobody was there to respond, and that was knowable before a line went
out.

The daemon already knew. `config.channel_users` is synced from 353/JOIN/PART
for every channel it is in, and `dcc.py` has read it as proof of presence
before dispatching a send since long before this. A request is a PRIVMSG into a
channel - a nick that is not in one of ours cannot see it - so refusing is not a
guess about whether they would answer, it is the observation that they were
never asked.

All three entry points check it: list, folder and file. The file route checks
PER ITEM, because a bulk paste is routinely several bots and one of them having
signed off is no reason to refuse the rest.

**Nothing known is not the same as nobody there.** While the bot is still
joining, the membership mirror is empty and every nick would read as gone - so
that state is its own, and it never refuses a fetch.

**And the sidebar shows both things now.** The dot carried the list's
freshness, which left presence - the thing that decides whether asking is worth
anything at all - shown nowhere. A list can be perfectly current from a bot that
signed off an hour ago.

    dot   green  in a channel with us     name  green   list current
          red    not in one                     orange  theirs changed
          grey   still joining                  red     not downloaded
                                                grey    cannot tell

Colour on the name rather than a second dot: the row is already a nick, a count
and a dot, and a fourth mark to decode is worse than the two it replaces. Each
carries its own title, because colour alone is invisible to roughly one man in
twelve - and the legend names every state of both, which an existing guard
insisted on when the third presence state had no entry.

Membership is read ONCE per payload. This route is polled every few seconds and
a busy channel has dozens of advertisers; asking per row would rescan every
channel's membership per row, per poll.

A mutation run earned its keep on the wiring rather than the pieces: both
helpers can be perfect and still be plugged in the wrong way round, and
swapping the dot back to freshness passed every test until the wiring itself
was asserted.

### 🔤 The filter bar searches what was typed, in the order it was typed

From the beta: *"when i type amon a i want it to search 'amon a' only. if i
type amon amar i want it to search 'amon amar'. if i want the 2nd word to be in
any place then i search for amon*amar. also, it it possible to highlight the
matched characters?"*

**What it replaced.** Every word was ANDed and could appear anywhere, so a
two-word query was WIDER than a one-word query in every way that mattered - the
second word is usually short, and a short word is in half the library. Typing an
artist and the first letters of an album returned every track by that artist
whose title happened to contain a standalone "a".

    typed          old                        new
    amon a         amon AND a  (anywhere)     "amon a" as a phrase
    amon amar      amon AND amar (anywhere)   "amon amar" as a phrase
    amon*amar      - no meaning -             amon AND amar, anywhere

**The prefix rule is why it works.** The last phrase's last word is still
matched as a prefix, because it is the word being typed - so "amon a" reaches
"Amon Amarth". The old length floor refusing a wildcard on a single character is
right for a bare "a", which is every row in the index, and wrong inside a
phrase, where "amon" has already anchored it: without that exception "amon a"
asks for the standalone word "a" straight after "amon", which is not what
anybody types it for.

The `*` never reaches FTS5 - it is a separator, consumed before the expression
is built. Reaching it, it would be syntax, which is the same reason every term
is quoted rather than interpolated.

**The highlight marks what was TYPED**, not what FTS5 matched: the last phrase
is a prefix, so "amar" matches "Amarth", and marking the four characters the
operator put in is the honest reading of the request. The segments are parsed
ONCE, on the server, and returned in the payload - two parses of "amon*amar"
could disagree about where the boundary is, and the page would then mark
something the search did not use. Overlapping ranges are merged, because a
nested `<mark>` renders as a darker patch that reads like a third kind of match.

Every piece is escaped with the same `escapeHtml()` the title always had:
marking part of a foreign bot's filename must not turn the rest into markup. A
first version of that test counted escapes and a mutation walked straight
through it - dropping the escape from one of five pieces still left four - so it
asserts the property instead: no slice of the title reaches the output raw.

This changes the DASHBOARD FILTER only. `@find` and the own-list search go
through `list.find_matching_entries()`, which keeps its AND-across-words rule;
changing what channel users experience was not what was asked for.

### 🔍 The cross-list filter searches every list, and reports on every list

From the beta, filtering with two lists held from one bot: *"why flacme - rar
shows like it has a result for amon a but i dont see any"*.

Because it was never asked. A bot's archive can hold several lists, and since
they started being kept each is indexed under its own name - `<nick>` for the
main one and `<nick>/<marker>` for the rest. The filter built its list of
sources from the NICKS alone.

Two consequences, and the reported one is the second:

- the other lists were **never searched**, so a match inside one could not be
  found by the filter at all; and
- they came back in neither `matched` nor `empty` - and the sidebar only dims
  what it is TOLD is empty, so a list with no matches was left bright, reading
  as the one list that had them.

The keys now come from the same place the sidebar's rows do, which is what makes
the two line up. One enumeration used by both; two would drift, and drifting is
what produced the report. An entry written before archives were kept whole has
no `lists` key at all and is still enumerated by its bare nick, so nothing needs
migrating.

**And a second thing from the same screenshot** - *"why on folders i see only
track 01 and track 06"* - which is the filter working correctly. `amon a` means
"contains `amon` AND contains `a`", which is `build_match_query()`'s own rule
with the prefix wildcard reserved for terms of two characters or more, and every
result had a standalone "A" in its title.

But the folder heading said "2 files", which means the folder's SIZE when
browsing and the number of MATCHES when filtering - in identical words. So a
nine-track album whose title matched twice read as an album with two tracks in
it. It says "matches" when that is what it is.

### 🖥 The List Browser is two columns

Asked for during the beta with the two regions drawn on a screenshot: the
sources should be a narrow column on the left, and the table should sit beside
them rather than under them.

Everything was stacked full-width, so a column of nicks - which needs perhaps
300px - took the whole width, and the file table began some 600px down the page
and then scrolled inside whatever height was left. On the screen it was
reported from, six file rows were visible under a source list showing five.

    BEFORE                              AFTER
    +--- fetch ---------------+         +--------+------------------+
    +--- filter --------------+         | fetch  | TITLE  SIZE  FMT |
    +--- bot list ------------+         | filter |------------------|
    +--- file table ----------+         | FlacMe |  [ ] 01 Bleed... |
                                        | ...    |  [ ] 02 The L... |

**The `minmax(0, 1fr)` is load-bearing**, and looks like a redundant zero. A
grid track sized `1fr` takes `min-width: auto`, so a wide table inside it pushes
the TRACK wider rather than scrolling within it - and the whole page gains a
horizontal scrollbar, which is worse than what was there before. A floor of 0
lets the column be narrower than its content, handing the overflow back to
`.table-wrap` where it is already handled. `min-width: 0` on the two columns
themselves is the other half: a floor on the track is undone by an item that
will not shrink.

The source list gets its height back. It was capped at 216px because it sat
ABOVE the table and every pixel it took came off the files; side by side it
costs the table nothing, so it takes 60vh - and the cap returns under the
narrow-screen fallback, where it costs again. Below 900px the two columns are
narrower than either wants, so it stacks, which is the arrangement that was
already there.

A test reads the media query by MATCHING BRACES. The first version split on
`@media` and took everything after the next `{`, which is the rest of the file -
so it found a rule inside the first media query in the stylesheet and reported
on that. It has a guard on itself now: a block reader that returns the whole
file makes every assertion using it meaningless.

### 🧮 A rate nobody should act on is not stated as a fact

From the beta, on a list zip:

    Sent: "<list>.zip" to <user> [138.63MB/s]
    "this seems to high to be true"

It was. `sendall()` returns once the bytes are in the KERNEL, not once the peer
has them. For a file LARGER than the socket send buffer the two converge - the
kernel blocks once the buffer is full, so the send paces itself against the
network and the clock measures something real. For a file that fits INSIDE the
buffer they do not converge at all: the whole thing is handed over in one go
and the clock measures a memory copy.

**Caused by a change in this release, and predicted by it.** Raising the default
send buffer to 4 MB - which took a real transfer from 3.0 to 24.7 MB/s - also
widened the window of files this applies to from under 1 MB to under 4 MB. That
is most list archives and many single tracks. The change said so at the time and
pinned the guard on the speed RECORD; it did not pin the guard on what gets
SAID, and there was not one.

`MIN_RECORD_SECONDS` is the floor the record has always applied, for exactly this
reason. A number too unreliable to keep is too unreliable to say, so the same
judgement now decides both - and this line goes into the CHANNEL, where a figure
nobody should act on is worse published than absent. Such a transfer reports
`n/a (<1s)` rather than a guess, and rather than `0k/s`, which would be a
different false claim rather than an absence of one.

Nothing about the transfers themselves changed. They really are fast now; only
the ones too short to time stop claiming a number.

### ☑ A folder can be selected whole

Asked for during the beta, looking at a nine-track album: an album is the unit
people actually want, and ticking nine boxes one at a time to get one is the
kind of work a page should be doing for them.

The folder heading carries a checkbox now, **in the same column as the file
checkboxes it commands** - which is why the heading's cell is split into a
check column and a colspan of four instead of spanning all five. The
relationship is visible rather than something to work out.

**Three states, not two.** Checked, unchecked, and INDETERMINATE for
some-but-not-all. The third is the honest one: a box reading "unchecked" while
four of nine rows are selected describes a selection that is not the one in
force. Ticking a file updates its folder's box; a shift-range can span several
folders, so every heading is re-read rather than only the one clicked in.

**A collapsed folder selects too.** Its rows are in the document already -
collapsing hides them rather than removing them - so a folder can be selected
without being opened, which is most of the point when a list has hundreds of
them.

**What it does not claim.** It selects the rows that are RENDERED. A folder
past the page's row ceiling arrives cut short and says so in its own row, and a
box that silently claimed the rest would be claiming to have queued files
nobody has seen.

The gate deciding whether any of this appears is now written ONCE, in
`rowsAreFetchable()`. It existed twice before - the file checkbox and the
folder heading's old button - with the same defect fixed in each at different
times. Two copies of a rule cannot disagree if there is only one, and a test
refuses a second.

### 📥 The server is read in useful-sized bites

The socket was read **2048 bytes at a time** for as long as `irc.py` has
existed. That is one or two IRC lines - RFC 1459 caps a line at 512 bytes,
IRCv3 tags raise it to 8703 - which is fine while the server is trickling
channel chatter, and wrong when it is not.

Joining channels is when it is not. Every JOIN is answered with the whole NAMES
list, one 353 per few hundred nicks and then a 366, so adding several channels
at once produces tens of kilobytes in a burst - taken two kilobytes at a time.
An ircd bounds what it will hold for a client that is not keeping up and closes
the link when that fills; to us that arrives as `ECONNRESET` with nothing to
say why. A beta reported exactly that shape - *"[WinError 10054] ... Dropping
the link to reconnect"* right after several channels were added - and it would
not reproduce.

**This is not proof that was the cause.** The disconnect report that shipped
alongside it will say so directly the next time it happens, by printing the
server's own `ERROR :Closing Link:` line. This change stands on its own: a byte
stream read two kilobytes at a time has no argument for it. `recv()` returns
whatever is there up to the size asked for and never waits to fill the buffer,
so a larger one costs an allocation and saves syscalls exactly when there is a
backlog to clear.

Both loops, since the registration loop reads the same stream through the same
helper and the server's 001-005 and MOTD arrive there in a burst of their own.

**What makes it safe was already true** and is now pinned at this scale:
`take_complete_lines()` accumulates BYTES and returns only whole CRLF-terminated
lines, so the read size cannot split a line or a UTF-8 character however the
boundary lands, and `MAX_PENDING_LINE_BYTES` still bounds a peer that never
sends CRLF at all. A bigger read makes a mid-line boundary MORE likely, not
less, so there are tests for that case rather than fewer.

### 🔎 Checked, and not a defect: what a bare @nick hands over

Raised during the beta, when an operator whose whole library is film found
their master list empty and their video list holding everything: does a plain
`@<nick>` then hand somebody an empty file, while the advert - which sums every
list - promises hundreds?

**No, in either format**, verified against exactly that shape rather than by
reading the code:

    LIST_FORMAT=txt  ->  <base>-FULL-<date>.txt      contains the film
    LIST_FORMAT=zip  ->  <base>-<date>.zip           master + VIDEO + RAR

The `.txt` artifact is not the master index - it is a separate combined file
(`list.FULL_LIST_MARKER`), written precisely so that choosing `.txt` is a
choice about packaging rather than a request to hand out less. So the count in
the advert and the contents of the download agree.

The half of that question which WAS a real defect - a peer FETCHING such an
archive, and keeping only its empty master - is fixed above.

### 🗃️ The Settings page is grouped the way an operator looks for things

From the beta: *"Settings pages at webpage are a mess. Need better grouping,
hiding some that are never used like folder locations under advanced settings
etc. Also why is packet size on different page than buffer size."*

All three, and a fourth they did not name.

The old grouping is how any grouping ends up - by accretion. Each feature put
its settings wherever there was room, so of 94 settings:

| category | | |
|---|---|---|
| Slots & queue | **25** | DCC slots, queue limits, message delays, DCC ports, fetch slots, fetch history, fetch sizes, the send buffer, the rehash wait, auto-refetch, four fetch timeouts |
| Paths & storage | **31** | list generation, `.rar` packing, the **packet size**, and seventeen file paths |

Two thirds of the page in two categories - and `DCC_BLOCK_SIZE` and
`DCC_SEND_BUFFER`, the pair anyone tuning a transfer reads together and which
the 3 MB/s investigation needed both of, sat a category apart.

**The fourth:** the colour theme was under "Advertising & search", because
`announce.py` is what draws it. A fact about the code rather than about what an
operator came looking for. It has its own section now.

**Three rules**, applied and pinned by tests rather than left as an
arrangement:

- settings read together live together - the transfer pair, the fetch timeouts,
  a timeout beside the one it qualifies, the `.rar` settings with the list they
  shape;
- a category is named for what an operator came looking for;
- what is set once at install goes last, in **Advanced: file locations**.
  Seventeen file paths at the same level as `MAX_DCC_SLOTS` is seventeen
  chances to wonder whether you should be changing one.

The tests assert the other half of that last rule too: **nothing changed weekly
may be hidden there**, since burying something an operator does reach for would
be worse than the mess it replaced.

Largest ordinary category is now 15 rather than 31, and a test refuses any above
16 - so the next dumping ground fails a build instead of forming quietly.

**Nothing moves on disk.** `settings.conf` is flat; a category is a grouping for
this page and nothing else. Every stored value and every setting NAME is
untouched - only what an operator reads changed. The existing completeness guard
proved nothing was lost in the move: it passed unchanged.

### ⏱ A folder request to a bot with no sign of packing fails fast

`FETCH_FOLDER_OFFER_TIMEOUT` is 1800 seconds, and rightly so: the other bot has
to run its own packing pipeline before it can even begin the DCC SEND, and a
real album takes real time.

That allowance is wrong for a bot that never packs anything. There a non-answer
is the EXPECTED outcome rather than a slow one, and the wait costs one of
`MAX_FETCH_SLOTS` - three by default. Half an hour of a third of the fetch
capacity, spent discovering what that bot's own list already said.

**Two signals, either one enough.** A RAR list of theirs among the lists we hold
- their own statement of the folders they will pack, in our hands, and kept only
since the change above stopped discarding it - or the `@<nick>^ ... RAR folders`
advert `irc.py` has parsed into `known_bots` since #133 and which nothing
outside the registry had ever read.

**Used to decide how long to WAIT, never whether to ask.** A bot can pack
folders with neither signal: we may simply never have caught the advert, and may
hold only its main list. Refusing on this would take away something that works.
Waiting less costs nothing when the guess is wrong, and half an hour of a slot
when it is right.

A failure deciding waits LONGER rather than less. This runs inside the queue
lock on the sweep every tick, and the long wait is the safe way to be wrong: it
only ever waits, where the short one could give up on a pack still coming.

`FETCH_FOLDER_OFFER_TIMEOUT_UNADVERTISED` sits on the Settings page beside the
timeout it qualifies, because reading one without the other tells half a story -
and a test says so.

### 🗜 A folder is offered as .rar because that bot's list says so

"Get folder as .rar" sat on every folder heading of every fetched list, gated
only on "this is not our own list". That could never be right: it is a claim
about what a FOREIGN bot will do, made from nothing.

Measured against one live registry, **2 of 51** known bots publish a RAR folder
list at all. The button was wrong for the other 49 - and a click sent
`!<nick> !rar <folder>` into the channel and then held one of `MAX_DCC_SLOTS`
worth of fetch slots for `FETCH_FOLDER_OFFER_TIMEOUT`, half an hour, waiting
for a reply that was never coming. Three clicks and cross-bot fetching was dead
for the afternoon.

**And per-bot would still have been wrong.** From the operator: *"rar file list
can be different than normal filelist. i can offer 1 folder as normal file list
and 1 other folder as rar filelist only."* `update_list.py` does exactly that
here too - a folder earns its `!rar` row from holding a *packable* file, so the
two lists are different sets even in our own output.

**What the list already says.** A bot that packs albums publishes a separate
list whose every row is the line to type:

    !SomeBot !rar D:\MUSIC\Amon Amarth - 1999 - The Avenger
A per-folder statement of what that bot will pack, published by the bot itself.
Nothing inferred from an advert, nothing guessed from a filename convention
only our own lists follow. So the button goes on the ROW carrying the request
line, and the folder it sends is the one that row asks for - never the heading
the row sits under, which for a RAR list is not the folder being requested.

That list was being discarded on the way in until the change above kept it,
which is what makes this answerable at all.

The title is left exactly as the list wrote it: those rows exist to be copied
verbatim - the header of every such list says so - so this adds a field beside
the title rather than reformatting it. And the button shares the checkbox's own
gate, in the same function, since packing a folder makes no sense against our
own list.

A mutation run earned its keep twice: `\s*` in place of `\s+` let a file whose
name merely BEGINS "!rar" read as a request - `!rarely used.flac` becoming a
request to pack `ely used.flac` - and the single-file branch in the archive
picker turned out to be dead code.

### 📦 Every list in a fetched archive is kept, not just the largest

A peer's archive routinely holds more than one list, and until now exactly one
survived: `_pick_list_file()` skipped anything matching the "-rar-"/"-video-"
naming conventions and took the largest of what remained.

    @SomeBot  ->  SomeBot-Default(2026-01-02)-OS.txt   kept
                  SomeBot-rar(2026-01-02)-OS.txt       thrown away

So a bot offering its albums as a separate RAR list, or its films as a separate
video list, had that half silently discarded on the way in - and an operator
whose peer keeps everything in the second file saw an empty catalogue for a bot
that plainly advertises thousands.

**Keyed on a marker, never the filename.** A peer's list file carries a date, so
a filename key would make every re-fetch a NEW list: the old one orphaned, the
sidebar growing forever, the freshness LED with nothing stable to compare. The
marker is what remains once the shared prefix, the date and a trailing "-OS"
come off - `rar`, `VIDEO`, `Default`.

The shared prefix is derived from the FILES rather than assumed from the nick.
A peer names its lists after its own `LIST_BASE_NAME`, which need not be the
nick we asked, and a nick containing a hyphen would be cut in half by splitting
on the first separator. It is trimmed back to a separator so it cannot end
mid-word: with `-RAR-` and `-README-` the raw common prefix ends at `-R`, and
the markers would come out `AR` and `EADME`.

**The main list is unchanged.** It keeps the empty marker, it is still mirrored
in `list_path` and `entry_count` at the top of the entry, and it is still what a
bare `@<nick>` is understood to be offering - so nothing migrates, and an entry
written before this has no `lists` key at all and still renders and browses.

**Bounded, and not silently.** "Keep exactly one" was what bounded this; without
a ceiling an archive of hundreds of small `.txt` files is hundreds of parses,
sidebar rows and index writes, all under the existing byte cap. The cap names
what it dropped - three of them and a count, because a cap that says nothing
reads as "we covered everything" and one that names thirty-two files is a wall
nobody finishes.

A second list's failure is not the fetch's failure: the main list is parsed,
counted and indexed before the others are touched, so an oversized or unreadable
sibling costs that list alone. A `.txt` with no request lines is a readme or a
banner rather than a catalogue and gets no row - the main list exempt, because
an empty main list is a fact about that bot worth seeing.

Each list is indexed under its own name - the bare nick for the main one, so an
index written before this still resolves - so re-fetching replaces one list's
rows rather than the whole bot's.

**Two things the tests corrected.** The main list is now decided ONCE and passed
down: the first version asked `_pick_list_file()` a second time, which repeated
its log line and re-entered a function `tests/test_list_fetch.py` deliberately
hooks to block on its first call. And a mutation run showed the single-file
special case was redundant - `marker = "" if path == main` already answers it -
so it went, rather than staying as dead code that looks load-bearing.

### 🗂️ Every list this bot serves is browsable

From the beta. An operator built a second list **through the dashboard**, put
every film in it, and then could not find it: *"in list browser i dont see all
my lists / only music list / video list isnt there"*.

The list was served correctly and advertised correctly in its own channel -
Stage 4 made adverts per-channel, so that channel reported its 9 films
accurately. Only the page that BROWSES lists stopped at the primary, for two
independent reasons:

- `build_filelists_payload()` called `find_matching_entries()` with
  `name=None`, which resolves to the PRIMARY served list, so everything under
  another list's own directory was never opened;
- the sidebar hard-coded one row - `{bot: "__own__", label: "Our own list"}` -
  so there was nowhere to click even once the backend could answer.

Multi-list is this release's headline feature and the dashboard is where the
operator created the list. A page that offers to make a thing and then will not
show it is a round trip that does not close.

**What did not change.** `__own__` alone still means the primary, and
`GET /api/filelists` with no `?list=` still returns it - the meaning that route
had before lists had names. Every install serving one list sees exactly what it
saw before, down to the row still reading "Our own list" rather than "Main", a
name the operator never chose and has no reason to recognise.

`?list=` is validated against the lists we actually serve, and an unknown name
resolves to the primary rather than erroring: the sidebar is polled every few
seconds, and a list renamed between two polls would otherwise turn the table
into an error message on its own. It also keeps an arbitrary string away from
`list.find_latest_list()`, which joins the name into a directory path.

**Composed in the route**, not inside either builder.
`build_fetched_bot_list_summaries()` answers the question its own name states
and should not start answering ours - the first attempt put the composition
inside it, and eleven existing tests said so.

`isOwnSource()` replaces every bare `=== "__own__"`. Missing one would leave a
second list looking like a foreign bot: fetchable, refetchable, and offered a
Download button for a list we wrote ourselves. A test refuses any bare
comparison outside the helper itself.

### 🚀 Send speed was capped at 3 MB/s, and it was arithmetic

From the beta. Same friend, same machine, same link:

    OmenServe : 30.4 MB/s
    DCCore    : 2.95 / 2.98 / 3.00 / 3.01 MB/s   (four files, different sizes)

Identical every time - congestion varies, a ceiling does not.

TCP cannot have more bytes in flight than the send buffer holds, so throughput
is bounded by `SO_SNDBUF / round-trip-time`. The measured default buffer on
that machine was exactly 65,536 bytes. Setting the packet size to 4 KB made it
WORSE - 1.6 MB/s - and fitting both measurements gives the whole picture:

    effective ceiling  : 3.19 MB/s
    fixed cost / block : 1.27 ms
    implied RTT        : 20.6 ms      64 KB / 20.6 ms = 3.19 MB/s

An ordinary internet round trip, derived from the two speeds rather than
assumed. Raising `DCC_SEND_BUFFER` to 1 MB took the same transfer to **23.9 and
24.7 MB/s**, confirmed by the same friend.

**Why 4 MB and not the 1 MB that fixed the report.** The measured link was
20.6 ms away, where 1 MB is already far more than enough - but the ceiling is a
function of DISTANCE, and this bot serves a channel rather than one friend:

    RTT  20 ms   1MB ->  52.4 MB/s     4MB -> 209.7 MB/s
    RTT 120 ms   1MB ->   8.7 MB/s     4MB ->  35.0 MB/s
    RTT 200 ms   1MB ->   5.2 MB/s     4MB ->  21.0 MB/s
    RTT 300 ms   1MB ->   3.5 MB/s     4MB ->  14.0 MB/s

At 200 ms - an ordinary Australia-to-Europe hop - 1 MB lands back at the same
few megabytes a second this change exists to escape. Fixing the nearby case and
leaving the distant one is not a fix, it is a shorter list of people who are
still capped. The cost is bounded: `SO_SNDBUF` is a ceiling the kernel MAY
buffer, not an allocation, and it only fills when the network is slow enough to
make it worth having.

**And the guard this makes load-bearing.** `sendall()` returns once the bytes
are in the kernel, not once the peer has them, so a file that fits entirely
inside the send buffer "sends" in almost no time and its computed speed means
nothing. The window of affected files is exactly the buffer size - it grew from
under 1 MB to under 4 MB, which is most single tracks.
`stats_mgr.MIN_RECORD_SECONDS` already refuses a sample measured over less than
a second and predates all of this; it now has tests tied to THIS reason, so the
buffer can be changed again without anyone re-deriving why the floor matters.
The record is what the channel advert publishes, so a bogus one is not a
private mistake.

**Per-platform, not simply a new default.** The old behaviour was "never set it
unless asked", justified by `SO_SNDBUF` disabling the OS's own auto-tuning.
That is sound on Linux, where `tcp_wmem` grows the buffer to fit the connection
and pinning it would be a downgrade on exactly the long-haul links that need it
most. It does not hold on Windows, where "leave it alone" means a fixed 64 KB.
Neither platform's answer is the other's mistake, so `0` now means "the default
for your platform" - and an explicit value still wins on both, including a
deliberately small one.

### 🔇 The dashboard's own heartbeat no longer buries the console

From the beta, pasting a screen of this:

    127.0.0.1 - - [...] "GET /api/fetch/status HTTP/1.1" 200 -
    127.0.0.1 - - [...] "GET /api/filelists/bots HTTP/1.1" 200 -
    127.0.0.1 - - [...] "GET /api/console/log?since=33 HTTP/1.1" 200 -

*"maybe those lines shouldnt be visible on cmd.exe except you run dccore on
something like debug mode. you miss the important lines like search results
etc"*.

Exactly that. The dashboard polls five endpoints every couple of seconds, so an
idle bot with one page open writes on the order of a hundred lines a minute.
Every one says the same thing - the dashboard is still open - and together they
push a search result, a transfer or a disconnect off the screen faster than
anyone can read them. The console is the operator's only view on a daemon with
no window, and this was spending it on the daemon talking to itself.

**Silenced, not redirected.** Every request werkzeug reports is one this
process just served, so nothing is lost that was ever news. ERROR is left
through, so a genuine failure inside the dashboard still reaches the console -
and `DEBUG_MODE` turns the whole thing back on for anyone who wants it.

### 🔎 A dropped link reports what the server last said

From the same beta, after several channels were added:

    [DISCONNECT FIX] TCP keepalive detected a dead network ([WinError 10054]
    ...). Dropping the link to reconnect.

By the time it was asked about the surrounding lines were gone and it would not
reproduce - so there was nothing to diagnose from, and no way to ask for more.

An ircd states its reason before it hangs up: `ERROR :Closing Link: <nick> (Max
SendQ exceeded)` and the like. **That line was being read, matched by nothing,
and dropped** - the only mention of `ERROR` in the read loop was a condition
inside a `DEBUG_MODE` filter, which is off on every install that has not
already gone looking for trouble. It is logged on arrival now, and the last
fifteen inbound lines are printed on every one of the four ways out of the read
loop.

**WinError 10054 is not a dead network.** It is `ECONNRESET` - the server
hanging up on US. Reporting every socket error as "TCP keepalive detected a
dead network" sent an operator to look at their connection when the answer had
been on the wire a moment earlier.

The ring is bounded and each line truncated: the point is the handful of lines
around a drop, not a transcript, on a bot that may run for months without one.

### 🔽 A dropdown on the Settings page shows what is actually stored

From the beta: *"i set packet size to 64kb and when i press save and rehash i
see it back to 4kb"*.

The save had worked. `4096` is simply the FIRST `DCC_BLOCK_SIZE` choice, and no
`<option>` was ever marked `selected` - nor did anything assign `select.value`
after the markup was inserted - so every dropdown on the page rendered showing
its first choice, whatever the daemon was actually using.

All four were affected, and the packet size was the least of them:

| setting | always displayed |
|---|---|
| `LIST_FORMAT` | `txt` |
| `THEME` | `classic` |
| `ADMIN_CHAT_MODE` | `auto` |
| `DCC_BLOCK_SIZE` | `4096` - "4 KB" |

**The worse half is not the one that was noticed.** A page stating a value the
daemon is not using invites an operator to read it as correct and leave it
alone - agreeing to something they were never shown. It cuts the other way
too: someone who wants the first choice sees the first choice, changes
nothing, and keeps whatever was really there.

**Why it survived.** `field.value` arrives as JSON, so it is an int for
`DCC_BLOCK_SIZE` and a string for `LIST_FORMAT`, while an `<option>` value is
always text. Any strict comparison between the two is false for every numeric
choice. The fix compares as strings, and the tests carry a case of each kind.

A pending edit wins over the stored value, like the checkbox beside it: a
re-render while the save bar is dirty must not silently discard what the
operator picked.

### ⏭️ DCC RESUME: a partial download can be resumed

From the beta, in mIRC: a transfer sat at **"Requesting resume"** and never
moved. Not a failure and not an error - a client keeping its side of a bargain
this bot had never been able to answer. DCC RESUME was not implemented at all;
there was not one mention of the verb anywhere in the tree.

The exchange is three lines. We offer `DCC SEND <name> <ip> <port> <size>`; a
receiver holding a partial file answers `DCC RESUME <name> <port> <position>`;
and the sender MUST answer `DCC ACCEPT <name> <port> <position>` before
anything else happens. Only then does the receiver connect. Without the ACCEPT
it waits, which is exactly what was seen.

**Matched by port, never by filename.** The port is ours, unique per offer and
unambiguous. The offered name has already been through a space-to-underscore
pass and `announce.fit_irc_filename()` may have SHORTENED it to fit the IRC
line - so the name we sent is not always the name we hold, and matching on it
would fail on exactly the long-titled files most likely to need resuming. The
name in our ACCEPT is read back out of the handshake that actually went out,
so it is what the receiver is matching against, and no text off the wire is
ever interpolated into an outbound line.

**The ordering is the race.** The offset is stored before the ACCEPT is sent,
because a receiver connects only once it has seen the ACCEPT. Sending first
and storing afterwards would leave a window in which a prompt client connects
and is sent the file from byte zero, appended onto what it already had.

**Clamped, not trusted.** The position decides where we seek in a file of ours
and it arrives from the network. Past the end is answered as "you already have
all of it", which completes the receiver's transfer honestly rather than
leaving it hanging - the failure this whole feature exists to end.

**Honest numbers.** `bytes_sent` still counts what the receiver ends up
holding, so the completeness check compares against the whole file. What this
transfer actually put on the wire is tracked separately: a resumed send that
skipped 4 GB did not move 4 GB, and must not claim it in the totals or in the
speed record the channel advert publishes.

The registry lives in `runtime.py`, is bound onto config, is reset by the test
harness, and is in `commands.PRESERVE_RUNTIME` - all four demanded by existing
guards. The last matters most: losing it to a rehash does not merely forget an
offer, it hands the waiting sender an offset of zero, so it sends from the
start to a receiver that has been told to append from the middle. A silently
corrupted download rather than a failed one.

DCC RESUME is in the flood-checked set too, for the reason #219 gives for DCC
SEND: it answers with an outbound PRIVMSG, and an unthrottled responder is a
standard way to make a bot flood itself off the network.

`tests/test_dcc_resume_end_to_end.py` runs the whole thing over a real
loopback socket and checks the arriving bytes against the file - the test that
catches an off-by-one in the seek, or an ACCEPT whose position and whose seek
disagree. It probes bind-and-connect first and skips if the runner cannot host
it.

Two existing guards earned their keep: the runtime-container contract, four
failures until the registry was bound and reset; and the preflight state
guard, which caught the end-to-end test writing the developer's real
`data/download_counts.json` - `db.record_download()` is only reached on the
success path, and no test had ever taken one.

### 📡 A channel name is not restricted to word characters

Also from the beta. An operator added a channel with an **"&"** in its name.
The bot joined it, sat in it, advertised into it on schedule - and answered
nothing at all. `@<nick>`, `@find`, `-help`, `-que`: every one silently
dropped, in that channel only.

Joining and advertising are OUTBOUND. Neither parses a line, which is why both
looked perfectly healthy.

`parse_privmsg()` matched its target as `[#\w\-]+` - "#", letters, digits,
underscore, hyphen. RFC 2812 says a channel is a "#", "&", "+" or "!" prefix
followed by any octet except NUL, BEL, CR, LF, space and comma. "&" is not
exotic, and neither is "^", which is just as common in music-channel names. A
message from such a channel failed to match at all, and a PRIVMSG that does
not parse is a PRIVMSG that never happened.

The same character class sat in five more parsers, every failure silent:

- **NOTICE**, which is how another bot's advert reaches us
- **353 (NAMES)**, which populates `config.channel_users` - `dcc.py` treats
  that as proof a user is present before it dispatches, so sends into such a
  channel were refused too. This one also stopped skipping to the first "#"
  and takes the RFC's own `:<server> 353 <nick> <symbol> <channel> :<names>`.
- **366 (End of NAMES)**, so the channel never counted as confirmed at startup
- **JOIN** and **PART** tracking, which freeze and thaw a user's queue

**Widening alone would have been wrong.** `\S+` on its own lets a hostile
server hand us a "channel" containing `\x01` or a bare `\r`, and the target is
interpolated straight back into our own outbound lines. So the regexes take
`\S+` - which is what the protocol means, since a space is the field separator
- and `is_valid_irc_target()` refuses NUL, BEL, CR, LF, `\x01` and the comma
that separates a target list. Validating at parse time means every caller
inherits it rather than each one remembering, the same reasoning
`dcc_fetch.contains_unsafe_ctcp_bytes()` is applied where an offer is parsed
and not where it is echoed.

### 🏷️ A row we asked for says "Requested", not "Offered"

From a maintainer, on two list rows sitting at OFFERED in the Downloads
table: *"should be REQUESTED, not offered"*.

Exactly right, and the word was backwards on the SCREEN rather than in the
queue. `check_fetch_queue()` flips a row to `offered` at the moment it
dispatches OUR OWN request line - `@bot` for a list, `!bot <file>` otherwise -
and stamps `offered_at` with the time we sent it. Its own log line for that
moment already said "Requested". So the state means "we have asked and are
waiting for their DCC SEND"; nothing has been offered to us. The name reads
from inside `dcc_fetch.py`, where the row IS the offer being waited on, and
that reading does not survive being printed on a pill.

**Only the label moved.** The internal name stays: it is written into the
fetch queue file, so renaming it would strand every row in flight across a
restart, and it is matched by name in a dozen places. The CSS class is built
from the state name, so the pill keeps its colour.

The two fetch timeout settings measure this same state and had the same
backwards word - "Fetch offer timeout" sounds like a limit on an offer
somebody made us, when it is how long we wait for a reply after asking.
Fixing the pill and leaving those would have left an operator reading two
different accounts of one thing. Setting KEYS are untouched, so `settings.conf`
is unaffected.

### 💡 The freshness LED says what it is comparing

From the same maintainer: *"redownloaded [a bot's] list, its yellow, but it
does not update to green."*

The tooltip said only "Their list has changed since you downloaded it" - the
verdict with the evidence removed. Three quite different situations produce
it: the re-download never landed, it landed and was refused, or it landed fine
and the bot advertised something newer again afterwards. From outside they are
identical, and telling them apart meant reading the daemon log.

The payload has carried `advert_then` and `advert_now` since the LED was built
- the exact two values `webserver._freshness()` decides on - and the LED was
dropping them. It now reads "They advertised 100 files, built 2026-08-01 when
you downloaded it, and now advertise 120 files, built 2026-09-05", which
answers the question by hovering. The green and "not downloaded" states say
what they matched too: "Current" alone cannot be told from a bot that simply
stopped advertising.

**Reusing the formatter, not writing a second one.** `describeAdvert()`
already existed for the freshness BANNER, which spells the same two adverts
out for whichever bot is SELECTED in the List Browser. The LED is what you
read while scanning the bot list itself, which is where the question actually
gets asked. A second function of that name would not have been a duplicate so
much as a silent override - JavaScript hoists both declarations and the later
one wins - so the two are now guaranteed to tell one story.

The verdict itself was correct throughout, and a test now says so end to end:
green, then yellow after the bot advertises a newer list, then green again
once a re-fetch rewrites the stored snapshot.

### 🧯 A guard that the dashboard's JavaScript still parses

Found by breaking it. While writing the tooltip above, a `\n` written into a
string through a shell heredoc arrived as a REAL newline, so the string ran
off the end of its line. `web/app.js` is one script: a single unterminated
string anywhere in it takes down every tab at once, silently, with a blank
page and one line in a console nobody has open. The same shape as the empty
Settings page that reached the beta.

Nothing in the suite noticed, and the diff looked right.

There is no JS engine here, so this is not a parser and does not pretend to
be one. It walks each script in `web/` once, in the order a lexer would -
block comment, line comment, string, code - and reports a string left open at
the end of its line, or brackets that do not balance. A quote inside a
comment, an apostrophe inside a double-quoted string and an escaped quote are
all ordinary in this file and all defeat simple counting, which is why it
scans rather than greps.

Template literals would legitimately span lines. `app.js` has no backtick in
CODE - all 26 are prose inside comments - so one appearing is itself reported,
rather than quietly widening what the check will accept.

### 📏 Sizes are read and typed in MB, not in bytes

From the beta, looking at the Settings page:

    Largest folder !rar will pack (bytes, 0 = no limit)   10737418240

Counting the zeros to check that says ten gigabytes is not work anybody should
be doing. Seven settings now carry a unit: the five large ones in **MB**, and
`DCC_SEND_BUFFER` and `LIST_HEADER_MAX_BYTES` in **KB**, because MB would print
`0.0078` for an 8 KB banner limit and that is less readable than the bytes it
replaced. `DCC_BLOCK_SIZE` is a menu rather than a number to type, so its
options read `64 KB` while still storing `65536`.

**Stored in bytes, unchanged.** settings.conf, admin_config.py and every
reader in the daemon keep the number they have always had - nothing migrates,
and a hand-edited file looks exactly as it did. The division happens when the
field is displayed and the multiplication when it is typed into; the baseline,
the dirty set and the save body are all still bytes, so the dirty marker, the
save bar and the request body needed no unit knowledge at all.

The unit is fixed per setting rather than chosen from the magnitude. A field
that switched unit as its value grew would move under an operator mid-edit,
and `0` - which several of these use for "no limit" - has no magnitude to read.
An empty or half-typed box is left alone rather than converting to 0, since 0
means exactly that here.

**One existing guard had to be re-anchored.** It sliced forward from
`input.value =` to prove the assignment reads the dirty map - and the value is
now chosen first and converted second, so the slice stopped covering the
choice. It anchors on the choice instead and still fails when the dirty branch
is deleted.

### 📊 The list rebuild says what it is doing

From the beta: "running update list in tools is too silent." It was. The page
said "Rebuilding the master list..." and nothing else for as long as the scan
took, which on a large library is minutes and is indistinguishable from a hung
process.

It now reports the folder it is in, how far through the folders it is, and how
many files it has indexed - with a bar. The file counter is the part that
matters most: it moves even while the bar does not, which is what tells an
operator the thing is alive.

**Through a file, because the rebuild is a SUBPROCESS.** `update_list.py` is
started with `subprocess`, so it has no shared memory with the daemon to
report into. It writes `LIST_PROGRESS_FILE` and the status endpoint reads it -
written whole and renamed into place, since the dashboard polls it while it is
being written and half a JSON object is a parse error every two seconds rather
than a progress bar. The alternative was parsing the child's stdout, which
turns prose written for an operator into a wire format.

**What is honest to report.** The folder COUNT is known before the walk starts,
so "folder 2 of 5" is a real fraction; a file total is not knowable without a
full pass, which is the work being measured. The percentage counts folders
COMPLETED rather than the one in hand, because a bar that reaches 100% as the
last folder starts is claiming to have finished while it is still walking. Once
the walk is done the folder count has nothing left to say, so the writing phase
renders as indeterminate rather than freezing at its last value during the
phase that is genuinely slowest.

**It can never cost the rebuild.** Every write is wrapped and throttled to
twice a second: a full disk, a read-only `data/` or a permissions problem costs
the operator the bar, not the list they asked for. The file is dropped in
`generate_all_lists()`'s own `finally`, so a crash mid-scan leaves nothing
behind - a leftover would read as a rebuild still in progress and show a bar
that never moves.

Two things the existing guards caught on the way in: the clearing first lived
only in `__main__`, which no test runs, so a mutation removing it passed until
it moved somewhere reachable; and the bar's colours were written as literal
fallbacks, which `tests/test_web_theme.py` refuses - every colour comes from
the palette.

### 🐍 Python 3.14 emptied the entire Settings page

**Second RC1 beta finding, and the more serious of the two.** The dashboard
came up and every Settings category rendered its heading with no fields under
it. No JavaScript error, no failed request, no log line - the API returned all
8 categories and 92 fields when asked from a shell.

The difference was the interpreter. `start-dccore.bat` runs the daemon with
`py -3`, which on that machine is 3.14; the shell probe used `python`, which
is 3.13.

**PEP 649, new in 3.14, made annotations lazy.** A module now carries an
`__annotate__` function and builds `__annotations__` the first time the
ATTRIBUTE is read. `vars(module)` hands back the raw `__dict__`, which does
not contain it until then:

    Python 3.13:  vars(config)["__annotations__"]      -> 94 entries
    Python 3.14:  vars(config).get("__annotations__")  -> None

`declared_types()` reads exactly that, so on 3.14 it reported a configuration
with **no declared settings at all**. Nothing raised, which is why this
reached a beta rather than a stack trace. Every caller simply saw nothing:
the Settings page rendered zero fields, and both the settings reader and the
writer fell back to the default value's runtime type - so
`WEBUI_CONSOLE_ENABLED: bool = None` typed as `NoneType` and came through as
raw text rather than a bool.

It reads the attribute now when the dict has nothing, resolving the module by
its own `__name__` so every existing caller keeps passing `vars(config)`
unchanged. 94 settings on both interpreters.

**CI covered 3.10 and 3.12, and the README promises "3.10+".** Nothing in the
matrix was wrong; the matrix was behind. It has 3.14 in it now, and the whole
suite passes there - 3290 tests, no failures - so this was the only 3.14
incompatibility, not the first of many.

The unit-level guard does not depend on which interpreter runs it: it builds a
module whose annotations are reachable only as an attribute, which is the
shape 3.14 gives every module.

### 🧩 The dashboard was silently absent, and the check said "Ready to start"

**First finding from the RC1 beta.** The daemon was started from a real
install and the web dashboard was not there. The only evidence was one line,
after the bot had already connected and joined its channels:

    [WEBUI] Flask not installed; dashboard disabled.

Flask *was* installed. The machine had two Pythons - one reached by the `py`
launcher, another first on `PATH` - and the two halves of the instructions
pointed at different ones:

- `start-dccore.bat` runs the daemon with `py -3`, falling back to `python`
- the documented `pip install -r requirements-web.txt` follows `python`

So the package went into one interpreter and the daemon started under the
other. `check-setup` reported "Ready to start" and was not wrong; it had
simply never been asked this question.

**The check asks it now**, and asks it the only way that can be right: by
importing Flask itself. The launcher invokes the check through the same
`%PY%` / `$PY` it uses for the daemon, so the answer is about the interpreter
that will actually run the bot. Anything that shelled out to `pip list` or
read a requirements file would have agreed with the documentation and been
just as wrong. The warning names the interpreter it looked in, which is what
makes the two-Python case diagnosable rather than baffling.

**Both guides now say `py -3 -m pip` / `python3 -m pip`** rather than a bare
`pip`, and say why. `docs/WINDOWS.md` is the one that matters most: its step 3
has the operator run `where python` and `where py` precisely to discover they
have two, and then step 4 sent them to the wrong one.

### 🧪 Cut as a release candidate, to be run before it is published

Versioned `-RC1` rather than `v1.12.0` because it is going to be run as a beta
first. `SCRIPT_VERSION` is reported over CTCP VERSION, printed in the advert
and stamped on every generated list, so a build under test that calls itself
`v1.12.0` puts a version that does not exist yet into three places at once -
and a problem found during the beta cannot be pinned to a build.

`docs/PUBLIC-REPO-WORKFLOW.md` now documents the process. The project had cut
four release candidates already - `v1.10.0-RC1` through `RC4` - and never
written down how, so each one was reconstructed from the last.

### ⏱️ A concurrency test's backstop was set above its own timeout

Found by CI on the release branch, on windows-latest, reporting a possible
deadlock. There was none.

`ConcurrentReadDuringSameBotRefetch` caps the writer at `MAX_FETCH_ROUNDS`
re-fetches as a backstop against a stuck reader, and joins each thread with a
60-second timeout. The cap was 2000, and a re-fetch of that fixture measures
about 25ms - so reaching the backstop takes roughly fifty seconds on a
developer machine and more than sixty on the CI runner. The safety net was
hung below the floor.

**Measured rather than guessed:** a healthy run uses **41** rounds, because
the readers finishing is what actually stops the writer. The cap was never
reached at 2000 and is not reached at 300 either; all that changes is the
worst case, from about fifty seconds to about seven.

The re-fetch rollback added in this release costs ~7% per round (23.1ms ->
24.8ms) and narrowed an already-thin margin, but it did not create it. The
test had been one slow runner away from this since it was written.

### 🪞 The guard against shipping names was shipping them

Caught by running the guard's own question against the export it had just
been added to. It held a denylist of the forbidden strings written out in
full - and it ships, like everything else in `tests/`, so the one file
guaranteed to contain every identifier was published along with them. It even
had to skip itself to pass, which meant the file most certain to contain them
was the one file never checked.

`docs/PUBLIC-REPO-WORKFLOW.md` already records the principle, learnt from the
licence check: **"Assert what should be true, not a list of what shouldn't."**
No positive property distinguishes a person's handle from any other word, so
the next best thing is a denylist nobody can read: the entries are SHA-256
hashes of the lowercased word, and the scanner hashes each word it finds.

That removed the leak and the self-exemption in one change - the guard now
checks itself like every other file, and passes.

### 🕵️ Every identifying name out of the shipped tree, and a guard that keeps it that way

`.gitattributes` export-ignores exactly two files. Everything else reaches the
public repository, `tests/` included - and the co-maintainer's handle was on
18 lines across 12 shipping files, in comments attributing an observation to
the person who made it.

That was ordinary practice and done in good faith, but the handle is the same
one on the issue tracker and on IRC, and none of the comments need it: "an
operator reported" carries the same weight and dates better. The attributions
were rewritten rather than deleted, so every piece of reasoning survives
without the name.

**The README named the private repository and linked to files that do not
ship.** Its document table listed `docs/UPDATES.md` (export-ignored),
`docs/UPDATES-PUBLIC.md` (renamed at release step 3) and
`docs/PUBLIC-REPO-WORKFLOW.md` - so in the public tree two of those rows were
dead links, and the third described the `dccore-dev` / `dccore` split to
strangers who cannot read one of them. The table now lists what the public
tree actually ends up with. PUBLIC-REPO-WORKFLOW.md records why, so the rows
do not come back.

**And a guard, because hand-written sweeps keep missing things.** They are
written to find what is already known about. `tests/test_no_personal_
identifiers_ship.py` asks the shipped file list instead: no forbidden
identifier, no address outside the loopback, private and documentation
ranges, the two internal documents genuinely export-ignored, and `tests/`
genuinely shipping - which is the premise the whole rule rests on.

It reads the WORKING TREE via `git ls-files` and `git check-attr`, not
`git archive HEAD`. An archive-based version reads the last commit, so it
would have passed the very change that introduced an identifier and failed on
everything before it - backwards for something meant to stop one being
committed.

### 📣 The public changelog carries the whole release

`docs/UPDATES-PUBLIC.md` had stopped at the on-connect commands work and was
missing everything the audit produced. Twenty operator-facing entries added,
in the file's own voice - what changed, what it means for the person running
the bot, and which of them changes a number they will notice.

Two are worth reading before upgrading: **rotating the admin password with
`configure.py` could silently do nothing**, and **live speed is now the total
across slots rather than their average**, which changes the figure in the
channel advert.

### 🔁 A real `!rehash`, executed end to end for the first time

The audit's completeness critic named this as the single highest-value check
still not done, and it was right: `_handle_rehash_request()` is about seven
hundred lines - quiesce transfers, reload eight modules, merge runtime state
back, re-baseline the nick, restore the advert token, reattach debug sinks,
sync channels - and across 120-odd test files **nothing ran it**. Every test
stubbed it, reloaded one harmless module instead, or read its source as text.
`test_commands.py` says why: running the real reload "would risk the identical
thing happening to test state".

Correct for a unit suite, and the wrong place to leave it for a release. This
is not a rare admin command - the dashboard fires one on EVERY settings save,
and the console and IRC are two more entry points. The daemon's least-tested
path is the one an operator triggers with a checkbox, and three of the audit's
confirmed findings lived inside it.

**It runs in a subprocess.** The objection was only ever about reloading
modules the test runner itself holds; a separate interpreter has no such
problem. The child seeds the live state a rehash exists to carry across,
changes `settings.conf` underneath itself the way a dashboard save does, runs
the real thing, and reports what survived. Eleven assertions: the changed
setting takes effect, a user's queue survives, the channel lists survive, a
timed ban is not released, a freeze timer survives, the advert token survives,
the bot is not left paused, adverts resume, the queue is woken, and it says
`[REHASH SUCCESS]` rather than failing quietly.

**Two things it taught immediately.**

The first fixture assigned `config.dcc_queue = {...}` and the queue came back
empty - which reads exactly like the rehash losing it. It is not: `dcc_queue`
is a `runtime.py`-bound container and runtime is not reloaded, which is
precisely why it survives. Rebinding on `config` detaches that alias.
`defaults.py` says so in as many words - "Mutate them in place. Never rebind
them" - so the fixture was breaking the documented invariant, not finding a
defect.

And the preserved runtime state has **two independent mechanisms** keeping it
alive: the `runtime.py` binding, and the rehash's own PRESERVE_RUNTIME
snapshot. Breaking either alone leaves the test passing; only breaking both
fails it. That is what belt-and-braces should look like, and it is worth
knowing before anybody decides one of them is redundant - which, from reading
either one alone, it looks like.

### 🔚 The last four audit findings: two fixed, two deliberately not

**A busy neighbour could hide a real match.** `bot:"Dude"` is an FTS5 PHRASE
over a tokenised column, not equality - unicode61 splits on punctuation, so it
also matches `Dude|away`. The equality that compensated for that ran in Python
over a `LIMIT 25` window, and a neighbour holding fifty matching files fills
the whole window: the bot underneath was reported as having NO match, so the
sidebar dimmed a list that the filter would then happily list rows from.

Whatever number is picked there, a busy neighbour can exceed it. The equality
is a plain `bot = ?` column filter inside the query now, which is exact, and
one row is proof again - which is what that function's docstring had claimed
all along.

**The advert worker could retire during a rehash.**
`announce.current_worker_id` is how a running worker knows it is still the
current one, and `importlib.reload()` resets it to 0. The restore sat seventy
lines below the reload, and the worker wakes every five seconds: a wake in
that window read 0, concluded it had been replaced, and stopped - leaving no
advert worker and the channels silent until the next reconnect. The restore
now happens immediately after the reload, still refusing to overwrite a newer
worker's token.

**Two findings were real but are not being changed**, and the reasoning is
pinned as tests rather than left in a changelog:

- A bot-alone `list` row can claim an offer that was a near-miss for a `file`
  row from the same bot. The exact-match branch runs first, so reaching the
  bot-alone one means the name genuinely is not the file we asked for - which,
  for a bare `@bot` request whose answer cannot be predicted, is exactly what
  a real list reply looks like. Refusing the fall-through would reject
  legitimate list answers whenever a file request to that bot was outstanding.
- The passive DCC reply goes through the 5s-per-message pacer while the 60s
  accept clock runs. Sending it unpaced is what `queue_mgr` exists to prevent,
  and `irc.py`'s 513/PONG line is on record as what a raw unpaced write cost.
  Raising `PASSIVE_LISTEN_TIMEOUT` is the change with no such risk, and that
  is a setting rather than a code decision.

**That closes all 26 confirmed findings from the six-lens audit** - 24 fixed,
2 recorded as considered.

### 🖥️ The Settings page said the Console was off while it was on

`WEBUI_CONSOLE_ENABLED` is declared `bool = None`, and None does not mean
False: `console_is_enabled()` reads it as "on while the dashboard is
loopback-only". A stock install therefore has the Console - ban, unban,
clearqueue, rehash, update, behind the dashboard password alone - switched ON.

The page renders a bool from `!!value`, so None drew an unchecked box. The
operator was told the remote admin console was disabled while it was live.

The field now carries a note saying what unset resolves to right now. A note
rather than a corrected value on purpose: sending the effective value would
make the checkbox truthful and then have the next save write an explicit
`True`, so an operator who later moved the dashboard onto the LAN would keep a
Console that should have switched itself off.

### 🎨 A custom theme colour could not be expressed at all

`settings.conf.sample` documents each `CUSTOM_THEME_*` override as "a raw mIRC
code string like `\x0306,06`", and `defaults.py` says the same. Neither was
true: `coerce()` returned the text verbatim, so those nine literal characters
went into every advert, every "Sent:" notice and every search header -
broadcast on a five-minute cycle, with nothing reporting a problem.

There was no way round it either. A colour code starts with `0x03`, a control
character: `settings.conf` is edited in a text editor and the dashboard field
is a browser text input, and neither can produce that byte. Typing the escape
was the only route there was, and it was the one route that did not work.

`\xHH` now decodes, which covers every code mIRC uses - colour, bold,
underline, reset. Only `\xHH`, and only for the theme settings: a decoder
that ate every backslash would quietly mangle a Windows path in some future
string setting.

**Neither of these files had an audit lens pointed at it.** `theme.py`,
`stats_mgr.py`, `on_connect.py`, `omenserve_import.py`, `web/index.html`,
`web/style.css` and the whole of `scripts/` were covered by nobody, and the
console defect in particular is only visible across two of them - the tri-state
lives in `webserver.py` and the rendering that misreports it in `app.js`.

### 📦 A running RAR pack is no longer invisible

`config.active_transfers` is the SEND side, and a folder pack has no row there
while it runs: `check_queue_and_send()` claims `rar_inprogress`, runs `rar` for
up to `RAR_TIMEOUT` - half an hour by default - and only appends once the
archive exists. Two things read that list and drew the wrong conclusion.

**The quiesce saw an idle bot.** `wait_for_transfers_to_finish()` returned True
immediately with a pack mid-flight. The reload then re-executed `defaults.py`,
whose body sets `rar_inprogress = False`, and the rehash rebound
`user_processing_lock` to a fresh empty set - both while the packer was still
running. The next `!rar` read both interlocks as free and started a SECOND
`rar` process. Two packs of the same album target the same archive path, and
the second one removes the file the first is still writing.

**The capacity check was minutes stale.** The RAR branch tests `MAX_DCC_SLOTS`
before starting `rar` and takes its slot after. For a large album those are
minutes apart - long enough for every slot to fill with plain file sends, each
of which re-checked correctly on its own way through. This was the one append
that did not, so a finished pack could push the count past the ceiling.

Re-checked under the lock now, and a pack that finds no slot leaves its
archive queued for the next trigger, releases `rar_inprogress` and lets a
waiting pack start - the same outcome, and the same message, the two sibling
dispatch paths already use.

### 🧯 Three quiet failures from the audit's low findings

None is dramatic alone. What they share is silence: memory that grows with
nothing logged, a truncated file recorded as a completed send, and a record
replaced by a worse one.

**The IRC read buffer had no ceiling.** `buffer += chunk` grew for as long as
the peer withheld CRLF - an on-path attacker, or a `PORT` pointed at something
that is not an ircd, streaming bytes until the OOM reaper takes the daemon.
The read loop cannot notice on its own: with no complete lines, no per-line
handler ever runs. Bounded at 64 KB, far above RFC 1459's 512 bytes plus
IRCv3's 8191 of tags, so nothing a real server sends can reach it.

**A truncated send was recorded as complete.** The send loop ends on local
EOF, which says the file stopped giving bytes - not that it gave as many as
the handshake promised. A file replaced by a shorter one mid-send is the
ordinary way that happens: a re-encode, a library tidy-up, an NFS mount going
away under the read. The short send was then counted in the totals, credited
to the download counter, and its queue row deleted, while the receiver waited
for bytes that were never coming.

**The speed record could be replaced by a slower one.** The read and the write
were two separate lock acquisitions with the comparison between them, so two
transfers finishing together both read the old record, both decided they had
beaten it, and the slower one saved last. `db.raise_speed_record_to()` now
does the compare and the write under one lock, the way `record_download()`
already does. Losing a record is not corruption, but it is the one number in
the advert an operator cannot get back.

**A ninth state file was leaking**, found the same way as the previous five -
`data/speed_record.txt`, written for real by the new tests. Redirected in the
harness. The guard added this morning has now caught nine.

### 🗺️ Two more places the list name was dropped

The audit's critic grouped these under one cause: the `name` argument reaches
most helpers and is dropped at a few, and each drop silently means "the
primary list" rather than failing.

**A second list's advert published the primary's size.**
`get_file_count_date_size_and_raw_bytes(name)` passes `name` to
`find_latest_list()` and `all_list_paths()` a few lines up, then calls
`size_file_path()` and `rawbytes_file_path()` bare. So a channel bound to a
second list advertised its own file count and list date beside the PRIMARY
library's size and byte total - a plausible-looking number, which is why it
would have survived a glance.

**Renaming the bot orphaned every list but the primary.**
`migrate_list_base_name()` only ever looked in `LOCAL_LIST_DIR`. A list's
files live in its own directory and the marker recording what they are called
is already per-directory, so the design supported this - the function simply
never looked. After a rename, every other list's artifacts kept the old base
name, nothing on the next startup knew to look for it, and those channels
advertised a library they no longer had a list for.

**One thing deliberately NOT changed**, and one guard deleted:

- `db.migrate_legacy_side_files()` has the same primary-only shape and was
  flagged with them, but the consequence does not follow. The legacy names it
  migrates existed only in installs from before that rename, which predates
  multi-list entirely - so those installs had one list, and a second list's
  directory is created afterwards and can only ever hold the new names.
- The new migration briefly had a de-duplication guard for two lists sharing
  a directory. `list_dir()` does answer `LOCAL_LIST_DIR` for any list marked
  primary, so two primaries would collide - but `load_lists()` normalises a
  hand-edited file down to exactly one primary first. Unreachable, so it was
  deleted rather than kept with a test that could only ever pass. A test now
  pins the invariant it depended on.

### 📊 Live speed is the sum of the slots, not their average

The operator's call on the question the audit raised: **"Isn't live speed the
sum of all slot speeds?"** Yes - and both contracts already said so. The
implementation was the odd one out.

`stats_mgr.live_speed()` summed the per-transfer rates and then divided by the
number of contributors. Its own docstring, and `runtime.live_speed_bps`, both
described an aggregate "across every sending transfer". So a bot with three
slots each moving 2 MB/s published `Speed: 2.0MB/s` in its channel advert
against 6 MB/s of real outbound traffic - understating itself by a factor of
the slots in use, and worst exactly when it was busiest and had most to show.

The divisor had a defence written next to it: a transfer with no sample window
yet must not drag the mean toward zero. Under a sum that concern disappears on
its own - a skipped transfer contributes nothing, which is exactly right.
`contributors` is kept only to tell "nothing moved" apart from "nothing was
measured".

This changes what every advert publishes, which is why it waited for a
decision rather than going in with the docstring fix.

### 🔑 Rotating the admin password could silently do nothing

`defaults.py` applies `admin_config.py` first and `settings.conf` second, and
says so in as many words: settings.conf wins. The dashboard's own change-
password control writes to settings.conf.

So on any install whose password had ever been changed from the dashboard,
running `configure.py` to rotate the credential wrote a new hash into
`admin_config.py` where nothing would read it - and the operator went on
believing they had replaced a password that still worked. That is the recovery
path for a lost or shared credential, so it is the worst possible place for a
silent no-op.

`settings_file.shadowed_by_admin_config()` already warns about this exact
collision from the other direction. The reverse direction now has its half:
configure.py parses settings.conf with the daemon's own parser and says
plainly that the change will not take effect, and where to make it instead.

### 🧱 A half-written `admin_config.py` bricked the install

`open(path, "w")` truncates before it writes, so a full disk, a killed process
or a power cut during a routine password change left a partial Python file.

A partial Python file is a `SyntaxError`, and `defaults.py`'s
`except ImportError` around `from admin_config import *` does not catch that -
verified: `import defaults` fails outright. `configure.py` imports `defaults`
itself, so the one tool that could repair the file will not start either. The
install is unbootable AND un-reconfigurable.

`settings_file._atomic_write()` already does this correctly for the other
config file; this one is no longer the exception.

### 🪟 `start-dccore.bat check` said FAIL and exited 0

`cmd.exe` expands `%errorlevel%` when it PARSES a parenthesised block, before
anything inside it has run - so `if ... ( ... exit /b %errorlevel% )` returned
whatever the value was beforehand, which is 0. Verified on Windows 11: the
block form exits 0 where the goto form exits the real code.

It matters because `start-dccore.bat check` is the documented Windows
pre-flight in `README.md`, `docs/INSTALL.md` and `docs/WINDOWS.md`. It printed
"FAIL ..." and "1 problem(s) - fix these before starting", then reported
success, so any wrapper, scheduled task or CI step gating on the exit code
treated a broken config as verified.

The Linux twin was always right (`"$PY" ... ; exit $?`, outside any block), so
the two launchers had drifted on the one thing this shim layer exists to keep
identical.

### 🔢 An on-connect error named the wrong line

`problems()` reports a fault by POSITION, deliberately: the text may be a
password and must never be echoed back, so the number is the only handle the
operator has. `save()` stripped blank lines and then numbered the FILTERED
list, while the dashboard sends a textarea split with `splitlines()` and no
filtering at all.

Paste an X login, a blank separator, a `MODE %nick% +x` and an over-long
line - the ordinary shape of the block this feature exists for - and you were
told "command 3" for what you typed on line 4, then edited the wrong command.

Validation now runs against what the operator typed; blanks are still stripped
before storing, and are no longer reported as faults when it is `save()` doing
the asking.

### ⚖️ live_speed() contradicted itself, and the number is a question for the operator

Not changed - documented, because changing it changes what every advert says.

`stats_mgr.live_speed()` sums the per-transfer rates and then DIVIDES by the
number of contributors. Its inline comment explains that as a mean; its own
docstring, and `runtime.live_speed_bps`, both called it an aggregate "across
every sending transfer". Somebody changed one and not the others.

With three slots each moving 2 MB/s, the channel advert publishes
`Speed: 2.0MB/s` and the dashboard shows the same, against 6 MB/s of real
outbound traffic. That figure is the bot's shop window, so which of the two
readings is wanted is the operator's call. Both docstrings now say what the
code actually does, and name the open question.

**These four files had no audit lens pointed at them at all** - `theme.py`,
`stats_mgr.py`, `on_connect.py` and `omenserve_import.py`, along with the
whole of `scripts/` and `web/index.html`. The launchers in particular are
exactly what a Python-shaped audit walks past.

### 🔌 A failed listener no longer costs a DCC slot for ever

The caller appends a transfer to `config.active_transfers` BEFORE calling
`start_dcc_send()`, and only the `finally:` of one try inside that function
removes it again. `settimeout()` and `listen()` sat ABOVE that try, so an
`OSError` from either - EMFILE when the host is out of file descriptors, or
the kernel refusing the backlog - killed the dispatch thread with the row
still in the list and the listener still open.

Nothing ever revisits such a row. It names a transfer that is not happening
and no completion will fire to remove it, so it is one permanent slot out of
`MAX_DCC_SLOTS`, cumulative, until the daemon restarts - and the conditions
that make `listen()` fail are exactly the ones where losing serving capacity
hurts most.

`listen()` could not simply move down past the handshake: the handshake is
what tells the peer to connect, and a peer dialling before we listen gets a
refusal. The whole block moved inside the try instead, and a test pins that
ordering as well as the release.

### 🧷 A rejected re-fetch no longer destroys the list you already had

`_extract_and_locate_list_file()` wipes the extraction directory as its FIRST
action - and that directory is not scratch space. It is
`<FETCHED_FILES_DIR>/lists/<bot>/`, where the list being served from lives.
Every validation runs after the wipe: the byte cap, `is_zipfile()`, the member
checks, the plausible-list sniff, the line ceiling.

So a re-fetch that turned out to be a RAR, an oversized archive or a peer's
error page destroyed a good list on the way to rejecting the replacement. And
`refetch_due_lists()` runs unattended on a timer, so nobody is watching: the
operator finds a list they had yesterday gone today, with a rejection line in
the log as the only trace.

The held copy is now set aside first and put back on any rejection - the same
shape `update_list.py` already uses to publish our own list through
`final + ".new"`, applied to the receiving side.

### 🧪 A reload was throwing the test harness's redirects away

The state guard added earlier today caught this on a run where nothing else
had changed, which is exactly the intermittent shape it exists to make loud.

`db.py` derives its paths once at import - `FETCH_HISTORY_FILE =
getattr(config, "FETCH_HISTORY_FILE", "data/fetch_history.json")` - and a
`!rehash` reloads `db`, re-running that line. The harness patched only the
module constant, so any test exercising a reload threw the redirect away
mid-run, and every later write in that process landed in the developer's real
`data/` directory. `defaults.py` does not define these names, so the fallback
is the real path.

The harness now sets the CONFIG values too, so a reload re-derives the temp
path rather than the real one.

### 🔁 Three fixes that had only ever been applied to one site each

The audit's completeness critic named the pattern underneath a third of the
findings, and it is not a knowledge gap. In each case somebody found a real
problem, wrote the right fix, wrote a comment explaining it - and applied it
only where the bug had been reported from.

**`db` loaders.** `load_known_bots()` filters malformed rows out per ENTRY,
with a comment explaining that every reader treats a row as a mapping. Its two
siblings check only that the whole file is a dict, though both docstrings say
"same posture as `load_known_bots()`". For the fetch history that was not a
degraded view: `oserve` loads it straight into `config.fetch_queue`, and the
dispatcher walks that dict every two seconds - so one string value raised
`AttributeError` on every tick and killed cross-bot fetching for the life of
the process.

**The latency PONG.** `is_server_numeric()` exists because unanchored
substring tests let a user forge server messages by typing them in a channel;
its docstring cites this very PONG line as the bug it was written for. It was
applied to the 513 handler and not to the PONG handler three lines above, so
typing `oops PONG OSERVE_LATENCY_CHECK` forged the operator's latency reading -
and the `continue` after it meant the speaker's own line was never processed.

**The address in an inbound offer.** The only check was that the integer fits
in 32 bits, so whoever held the offering nick chose an address this daemon
would connect to - `DCC SEND x 0 22 1` decodes to 0.0.0.0, which `connect()`
reads as localhost.

**That third one is where the pattern stops being a rule.** The obvious fix -
call `dcc.is_offerable_to_strangers()` at the missing site - is wrong, and
proved it by breaking 32 existing tests. That predicate asks whether an
address is reachable from the public internet, which is the right question
about OUR OWN advertised address and the wrong one about a peer's: two bots on
one LAN, or a machine talking to itself, are ordinary. The dial side gets the
narrower question instead - can this name a peer at all - so 0.0.0.0,
multicast and reserved are refused while loopback and private ranges stay
allowed. Mutation-checked in both directions: too loose fails, and so does too
tight.

### ⏸️ The rehash quiesce now actually pauses anything

`wait_for_transfers_to_finish()` sets `config.transfers_paused`, waits for the
in-flight transfers to end, and logs **"No new sends will start."** That was
not true. The flag had exactly one reader, in `handle_download_request()`,
which turns away a NEW request from a user. Nothing stopped the DISPATCHER -
the function that claims a slot and starts the send - from promoting rows that
were already queued.

**So the wait could not converge on a busy bot.** Every completing transfer
re-arms a fallback that calls straight back into the dispatcher, so the freed
slot was refilled inside the very wait that was draining it. `active_transfers`
never emptied; the wait burned its full 120 seconds refusing every user
request with "the bot is reloading", and then reloaded under live transfers
anyway - the exact outcome the quiesce was added to prevent. Every dashboard
Settings save triggers this path.

The rehash wake already assumed the gate existed: it resumes BEFORE waking the
queue, commented "waking it while still paused would have every dispatch
refused by the gate the wait put up". That gate is now real.

**The fetch dispatcher is gated too.** A cross-bot fetch never appears in
`active_transfers` - it has its own queue - so the wait saw a quiet bot while
that dispatcher was still putting fresh `@bot` requests into the channel, each
bringing an inbound DCC SEND into the reload window.

**One existing test had to be rescoped, and that is worth recording.** It read
`dcc.py` as text and split on the first `if transfers_are_paused():` - so the
second gate silently moved its anchor onto the wrong branch. It now scopes to
`handle_download_request` via `inspect.getsource` and then to the branch,
because that function legitimately carries the list-rebuilding message for a
different condition.

**And the first version of the new test passed with the gate removed.** It
queued a row the dispatcher rejected for unrelated reasons, so it proved
nothing. The fixture now queues a row that really dispatches, and a control
test asserts exactly that - if the fixture ever stops dispatching, the control
fails rather than the pause test passing for free.

### 🧪 The suite was overwriting the developer's - and the server's - real state

Found by a guard written for a smaller problem, which is the useful part of
the story.

A new test called `library.save_lists()` and wrote **`data/lists.json` for
real**. The folders in it were that test's temp directory, deleted the moment
it finished, so every later test read a list definition pointing at nothing:
**147 failures and 27 errors**, none of them in tests that mention lists.
`git status` stayed clean throughout, because `data/` is gitignored.

That was the third time - `settings.conf`, then `data/on_connect.json` with a
plaintext X password in it, now this. So rather than fix it a third time and
wait for a fourth, `scripts/preflight.py` now snapshots `settings.conf` and
everything under `data/` before the suite and compares afterwards, naming any
file the suite created or touched.

**It fired immediately, on five files nobody knew about**: `bans.txt`,
`dcc_queue.txt`, `fetched_bot_lists.json`, `list_index.db` and `stats.txt`.

That is not untidiness. The daemon runs from its own directory on the
production LXC, so running the suite there overwrote **the live bot's queue,
its ban list and its accumulated totals** - the very numbers the OmenServe
import exists to carry across - with test fixtures. All eight files are
redirected now, and the guard fails the build on the ninth.

### 🗂️ Three defects from the six-lens audit

**A `!rar` from any list but the primary was destroyed at pack time.**
`library.folders()` defaults to the primary list's folders, which is right on
the request path - a request is routed to a list first, and checking it
against that list is the stronger test. But two callers ask the opposite
question, "is this path one of ours at all", and both run after routing, when
the list name is gone.

So a `!rar` arriving in a channel bound to a second list was validated against
that list's folders, accepted, queued, packed - and then, at the end, checked
against the PRIMARY's folders, logged as a poisoned queue entry, and deleted
along with the user's queue row. The download counter had the same bug more
quietly: files served from a second list fell through to an absolute-path key,
putting a real drive path into a stats table the dashboard renders.

`library.every_folder()` is the accessor for that second question. The default
`folders()` is untouched.

**A NAMES sync could drop the link it was sent to recover.** The 353 handler
thaws every frozen user still in the channel and starts a dispatch thread for
each. That thread's freeze sweep deletes every user it finds present in
`channel_users` - which the same handler populated with all those names two
lines earlier. So the first iteration's thread routinely deleted keys the
later iterations were about to `del`, and the `KeyError` landed on the IRC
READ THREAD, where the message loop answers it by closing the socket. The JOIN
handler had the same check-then-act shape. Both are one `pop()` now.

**One Swedish log line** (`dcc.py`, the queue-clean message) - the suite ships
publicly and the rule is English only.

**On the tests.** The first versions of these reimplemented the checks inline
instead of calling them, and passed against the broken code: three of five
mutations survived. That is the "guard that reads rather than executes"
failure this project has already been bitten by three times. The checks are
now named functions - `dcc.path_is_in_our_library()`,
`irc.thaw_frozen_users()` - so the tests can call the real thing, and the race
is driven by a dict whose membership test removes the key, which makes the
interleaving deterministic rather than hoping threads collide. All six
mutations fail now.

### 🔐 Every dashboard route, proven to be behind the login

From the pre-release audit. Two findings, one file.

**Nothing tested the authentication gate as a whole.** The dashboard has 37
registered rules and not one `@login_required` decorator - everything rests on
a single `before_request` hook that denies by default and exempts exactly one
endpoint. That design is right, because a per-route decorator is a thing
somebody can forget to add and a global hook is not. But it puts the whole
story on one function, and no test walked the routes to check it.

Probed all 37 rules unauthenticated: **none reachable**. The gate holds. What
was missing was anything that would notice if it stopped holding - the
exemption widening, the check going back to matching a path prefix, or a
future blueprint whose routes this hook never sees. The new test walks
`url_map` itself, so a route added tomorrow is covered without anybody
remembering the test exists.

Four mutations fail, including making the exemption path-based again and
quietly adding a second exempt endpoint.

**The "nine untested routes" figure was stale.** `docs/FUTURE.md` carried it
with "that count has not been re-measured since". Re-measured: four, and their
BUILDERS were well covered all along - six to twelve tests each. What had no
test was the wiring: that the path resolves, that a POST-only route really
refuses a GET, that the JSON envelope comes back. They have it now.

One of those tests documents a real quirk rather than papering over it: this
app answers a wrong-method request with **404, not 405**, so "is it POST-only"
is asserted as a refusal plus a separate `url_map` check. The status alone is
ambiguous - 404 is also what a deleted route returns.

### 🚫 Adapting the packet size mid-transfer is not planned

Asked for after the 64 KB work: could the bot raise the packet size when it
sees a transfer going well, and keep it low for slow receivers?

**Measured rather than argued.** `scripts/send_benchmark.py` reaches roughly
950 MB/s at 64 KB on loopback, which is far above any real link - so there is
no headroom for a larger write to win back. What bounds a fast link to a
distant peer is the socket send buffer against the round-trip time, and
`DCC_SEND_BUFFER` already exists for exactly that.

The slow-receiver half is already handled too, and better than this would
handle it: TCP flow control blocks the send when the far end stops reading.
Adapting the write size downward would be reimplementing that badly.

Recorded in the roadmap's "Not planned" section with the reasoning, rather
than dropped silently - it is a reasonable thing to ask twice.

### 📋 Three backlog items that were already built

Reported to the operator as outstanding, and none of them were. Corrected
here because the wrong list was acted on before it was checked.

- **Most-requested files and folders.** Complete end to end: `db.record_download()`
  writes them, `db.top_downloads()` reads them split by kind, `commands.py`
  answers on IRC, the stats payload carries them and the dashboard renders
  them. 31 tests.
- **Download history persistence.** `_persist_fetch_history_locked()` writes
  terminal rows every tick with retention, and `oserve.py` loads them at
  startup. In-flight rows are excluded deliberately - the socket that would
  have finished them is gone with the old process.
- **Files counted separately from RAR albums.** That is the `kind` argument
  above, and the dashboard hides the album table when `RAR_ENABLED` is off.

**The cause is worth keeping.** The backlog was read out of issue #133, which
was written before any of it was built, instead of out of the code. The
greps that were supposed to catch that searched for `most_requested` and
`top_files` - names this project never used - so an absent match read as an
absent feature. `docs/FUTURE.md` had it right all along: per-file download
counts are listed under Implemented.

### 🌐 The test suite was opening the operator's browser

Reported from the desktop: `http://127.0.0.1:8420/` popping up every few
minutes, on a machine where the bot was not running.

It was the test suite. `webserver.start()` opens the dashboard in the default
browser, and two tests in `test_webserver.py` drive `start()` directly to check
the `WEBUI_ENABLED` and `WEBUI_HOST` gates. So every full-suite run - every
`scripts/preflight.py` - launched a browser tab.

**The same class of side effect was already guarded one line lower.** That test
class replaces `create_app()` so a regressed gate cannot bind a real socket,
and says why: "a test must not be able to start a live listener because the
code it is testing broke". The browser call sits immediately above the bind and
was missed.

**The guard is in `tests/__init__.py`.** `tests/support.py` was the obvious
place and is the wrong one - only 90 of the 121 test files import it, and the
class that tripped this is a plain `unittest.TestCase` that does not. Patching
each test that calls `start()` fixes today's two and nothing about the next.
Importing the package is the one thing every run does.

Calls are RECORDED, not dropped, so opening the dashboard is still assertable -
which matters, because it is a feature somebody asked for. The new tests check
that `start()` still would open it, that `WEBUI_OPEN_BROWSER = False` still
stops it, and that a LAN-bound dashboard still opens nothing.

Verified by re-running the whole of preflight with a `sitecustomize` trap
installed at interpreter start, below the guard: zero calls reached the real
opener across both passes. Six mutations, including removing the guard and
guarding only `open()` while leaving `open_new()` exposed, all fail.

### 🗑️ The "what's new" list is off the roadmap

Dropped at the operator's call - not wanted at the moment.

**Nothing was built, so nothing was removed but the line.** The design got as
far as a marker excluded from `find_latest_list()` and a `SEPARATE_NEW_LIST`
default that was off, and it was never applied: no branch, no stash, and
`git log --all -S` finds neither identifier in any commit. Verified before
deleting the roadmap entry rather than assumed.

Recorded here so it is not proposed again as though it were new. The design
question that stalled it is the part worth keeping if it ever comes back:
what counts as "new" is the difference against the PREVIOUS PUBLISHED LIST,
not a file timestamp - mtime is rewritten by copying a library, restoring a
backup or moving between drives, and any of those would report the whole
library as new.

### 🕵️ A serving bot's nick, out of the test suite

Found while writing the queue tests below. `tests/` SHIPS - `.gitattributes`
export-ignores exactly two docs, and neither is a test file - and six
occurrences of another network's real serving bot nick were sitting in two
test files, copied in from the log excerpts the tests were written against.

Renamed to an invented one. The nick was never what either test was about.

The standing rule is to invent the identifier BEFORE writing the test, and
this is the second time it has been caught afterwards instead. A log excerpt
pasted in as a docstring is the specific way it gets in.

### 📥 The fetch queue's pacing, pinned end to end

> i download a bot's list, added 10+ into the queue on the webdashboard. it
> didnt queue them, it spit out every line at 1 go. should be queued and sent
> 3 at the time to the channel.

**This could not be reproduced on `main`.** Driven the way it really runs, ten
queued rows put three lines in the channel, hold the other seven across
repeated ticks, and release the next three only as the first three complete.
That is the reported-correct behaviour, and it is what the code does.

What was missing was the test. The SLOT CAP had one - two promoted, two left
pending - but nothing covered the thing an operator actually watches: that a
long selection reaches the channel a few lines at a time, and that each
COMPLETION is what releases the next. Both halves could have regressed in
silence. Nine tests now hold them, including the hundred-row shape driven
dispatch-complete-dispatch rather than by setting states and hoping.

**A real hazard turned up next to it.** `MAX_FETCH_SLOTS` is editable on the
settings page, which is the one way the active count can come out HIGHER than
the limit - lower it while transfers are running and `free_slots` goes
negative. It reaches `pending_ids[:free_slots]` as a slice FROM THE END, and
dispatches every pending row but the last few. A hundred-row queue would empty
into the channel the moment somebody saved a setting, which is a fair
description of the symptom reported above.

The `free_slots <= 0` guard has always stopped this, so it has never been
reachable in a shipped build - but nothing tested the guard, and deleting it
as redundant would have looked safe. It is pinned now. Holding still until the
extra transfers finish is the right response: the ones in flight are already
paid for, and the new limit governs from the next promotion on.

No behaviour changed. The investigation and the guard are the deliverable.

### 🔌 Commands sent on connect, before the join

> On connect commands to the server like undernet authentication and user
> modes etc. Maybe a text box in settings where you add all the commands you
> want to be sent to server on connect and a delay box with up and down arrows
> to change seconds between commands.

**The moment matters more than the commands.** These run after registration
and BEFORE the JOIN, and on Undernet that ordering is not cosmetic: logging in
to X takes `+x`, and `+x` replaces the host every person in the channel sees.
Join first and your real host is in front of everybody already sitting there -
and no later mode change takes it back. Auth, mode, then join.

A failure there does not stop the join. A bot that will not join because one
optional line was refused is worse off than one that joined without its
usermode.

**Its own file, not a setting.** `settings.conf` is one `NAME = value` per line
and deliberately refuses a value spanning lines - an indented continuation used
to join itself onto the setting above, silently. A list of commands is exactly
the shape that breaks on, so `data/on_connect.json` follows
`library_folders.json` and `lists.json`.

**The file holds a password**, and that shapes three decisions:

- **The command text is never logged.** `send_debug()` writes to a CHANNEL, and
  printing an X login there hands the password to everybody watching. The log
  line is built from `redacted()`, which keeps the command word and drops
  everything after it: `PRIVMSG ...`.
- **A validation message names the position, not the text.** "command 1" is
  enough to find the line; quoting it would put the password in the error.
- **The dashboard shows them in full anyway.** A box an operator can overwrite
  but never read means never fixing a typo without retyping the lot - and
  anyone with the dashboard login can read the file off the disk. The rule is
  about logs and channels, which is where it is enforced.

`%nick%` expands to the nick the SERVER settled on, not the configured one: a
433 collision rebinds it, and `MODE %nick% +x` written with the configured nick
would send a mode for somebody else.

A newline inside one entry becomes a space rather than a second command. That
is how one line would turn into two on the wire, and an operator pasting a
block with a stray newline means one command.

The gap between commands is a number input - arrows, keyboard, or typed - and
it is taken BETWEEN them, not before the first: sending an X login, a usermode
and whatever else back to back is how a bot meets Excess Flood on its own first
line, and a gap before the first one would delay the join for nothing.

Twenty-two tests, nine mutations, all caught. One of them - that the gap sits
inside the loop rather than before it - needed asserting as a sequence, because
a sleep in either place looks identical to a check for "is there a sleep".

**And a test wrote the real `data/on_connect.json`**, which would have left an
X password in the working directory. The harness now redirects
`ON_CONNECT_FILE` per test, the same way it redirects `DCCORE_SETTINGS_FILE` -
the second time today that a gitignored file was the thing that hid a test
writing somewhere real.

### ⏱️ The transfer was never slow. The number was.

> with mirc 65536 packet size i got 46 mb/sec max transfer speed and i get over
> 30 constantly with high speed line people [...] i feel it slower than mirc
> omenserve even with 64kb

**The send loop was never the limit**, and the first thing to do was stop
guessing about it. `scripts/send_benchmark.py` runs the exact shape of that
loop - same read size, same per-chunk bookkeeping, same socket options - over
loopback, where there is no link to blame. On this machine:

```
     block       MB/s   MB/s (no bookkeeping)
      4096       57.1                    65.0
     16384      234.8                   250.8
     65536      952.7                  1020.7
    131072      765.6                  1101.4
```

At 64 KB the loop reaches about **950 MB/s**, twenty times the fastest real
transfer being compared against. Worth keeping: at 4 KB it manages 57 MB/s, so
a small packet size genuinely would cap somebody at these speeds - the menu
added alongside this is not decoration.

**So the loop was exonerated, and the reported figure was not.**
`acute_duration` was measured at the very END of `start_dcc_send()` - after
`time.sleep(1.5)` ("gives mIRC 1.5 seconds to close the file calmly") and
another `time.sleep(0.5)` before the statistics write. Two seconds of settling,
counted as transfer time, against files that mostly take less than that:

| file | real at 46 MB/s | reported |
|---|---|---|
| 10 MB | 0.22s | **4.5 MB/s** |
| 50 MB | 1.09s | 16.2 MB/s |
| 100 MB | 2.17s | 24.0 MB/s |
| 700 MB | 15.2s | 40.7 MB/s |

A tenth of the real rate on an ordinary music file. And it fed the speed
RECORD and the channel advert, so the figure everyone else saw was wrong in the
same direction.

The clock now stops the instant the last byte goes out. The pauses still
happen - the receiver still gets its 1.5 seconds - they are simply not counted
as time spent transferring.

Three tests, two mutations, all caught. One of them is not a test of the code
but of the claim: it pins the size of the old error, so the arithmetic in this
entry cannot quietly stop being true.

### 🖥️ The Console is on where nobody else can reach it, and the dashboard opens itself

> i think the website console should be on by default on windows machines and
> when it is enabled i think the website should auto open on the default
> browser when you start up the program

**Keyed on exposure, not on the platform**, and that is the one place this
departs from the request. `WEBUI_CONSOLE_ENABLED`'s own comment explains why it
shipped off:

> an operator who enabled it months ago would have gained a remote admin
> console on upgrade with no setting changed and nothing recording that their
> exposure had widened

Defaulting it on for Windows would do exactly that. But look at what the gate
is protecting against: the Console reaches ban, unban, clearqueue, rehash and
update behind the dashboard password ALONE - one factor, over plain HTTP. That
is a real widening when the dashboard is on the LAN, and no widening at all
when it is on loopback, where whoever can reach it already has the served files
and `admin_config.py` sitting on the same disk.

So the default is now "on when the dashboard is loopback-only". On a Windows
desktop, which binds `127.0.0.1`, that is on - the outcome asked for - without
handing a remote admin console to anyone whose dashboard faces the LAN. **An
explicit True or False always wins**, so nobody who has already made this
choice has it made again for them.

A host that cannot be parsed counts as exposed. A host this cannot read is one
it cannot vouch for, and the safe answer to "is this reachable from outside" is
yes.

**The browser opens itself**, loopback only - and that half is not about
security but about what the machine probably is. A dashboard bound to the LAN
is as likely to be a headless box as a desktop, and a daemon that spawns a
browser there is doing something nobody asked for and nobody will see. A
missing browser or display is not a reason to stop the bot: the address was
printed a line above either way.

One thing worth noting for whoever reads this next: the routes gate on
`console_is_enabled()` now rather than reading the setting. Two of them check
independently on purpose - they are the whole attack surface - and leaving them
reading the raw setting would have kept the old flat default alive on the only
paths that actually gate the feature.

Twelve tests, seven mutations, all caught.

### 🎛️ Packet size is a menu, and the thing that is probably slowing you down is not it

> can we make dcc packet size choosable like mirc? 4 kb -> 8 -> 16 -> 32 -> 64
> and 128 [...] i feel it slower than mirc omenserve even with 64kb

**The menu:** `DCC_BLOCK_SIZE` is now a dropdown of exactly those six values.
That needed the choice mechanism to learn about numbers - every `CHOICES`
entry had been a string, and returning `"65536"` for a setting declared `int`
would hand the send loop a `str` to `read()` with. `bool` had to be told apart
from `int` first, because in Python a bool IS an int and a future `("yes",
"no")` choice would otherwise come back as 1 and 0.

**Now the part that matters more.** At 64 KB the packet size is very unlikely
to be what an operator is feeling. TCP_NODELAY is already set on every
transfer, and DCCore has never waited for per-block acknowledgements - so the
two things mIRC's "fast send" does are both already on.

What bounds a transfer on a fast, distant link is the **bandwidth-delay
product**: bytes in flight = bandwidth x round-trip time. At 100 Mbps and
100 ms RTT that is about 1.25 MB, and a 64 KB socket send buffer caps the
transfer at roughly 5 Mbps no matter how large each write is - the writer waits
for the far end to acknowledge before it can put more on the wire. Raising the
packet size does not change that; raising the send buffer does.

So `DCC_SEND_BUFFER` is here too, and **it defaults to 0, meaning "leave it
alone"**. That is not timidity: both Windows and Linux auto-tune this buffer,
and setting it explicitly TURNS THAT OFF - a value chosen for one link can be
worse than the default on every other. It is a knob to experiment with on a
link you know, not one to set hopefully. A kernel that refuses or rounds the
value does not fail the transfer.

Twelve tests, seven mutations, all caught. One of them was unfalsifiable at
first - nothing has a boolean choice today - so rather than delete a guard
against a real language footgun, the test adds a temporary one. A guard nothing
can falsify is a guard nobody can trust.

### 📦 The DCC packet size is a setting, and it was already large

> mirc has packet size option that makes sends much faster if you raise the
> packet size and you have a good connection speed. is there something like
> this in dccore?

There is, and it has been on the whole time. mIRC defaults its packet size to
4 KB, which is why raising it there is so noticeable. **DCCore has always used
64 KB** - sixteen times that - and it does not wait for the receiver to
acknowledge each block before sending the next, which is the other half of what
mIRC's fast send does. So the thing an operator comes looking for is already
there; what was missing is the number being visible.

`DCC_BLOCK_SIZE` now exposes it, and the honest part is the documentation
around it: **raising it further usually changes nothing.** Past a few tens of
kilobytes the limit is TCP's own window and the link, not how much this loop
hands the kernel at a time - the bytes are already in flight while the next
read happens. Where it can help is a very fast, very high-latency link. Where
it can hurt is memory, because this number is multiplied by the number of
concurrent transfers.

**Clamped rather than trusted**, 4 KB to 1 MB. A mistyped 500000000 would hold
half a gigabyte per slot, and 0 would turn the send loop into a syscall storm -
neither worth a startup error when the honest thing is to use the nearest
usable number and get on with the transfer.

Resolved once per transfer rather than once per pass: a `getattr` in the inner
loop of a 4 GB send is a million lookups for one answer that cannot change
mid-file. A test pins that too, since the obvious way to write it is the wrong
one.

Seven tests, four mutations, all caught.

### ⏸️ A rehash waits for transfers to finish (#310)

Neo, reviewing the last rehash fix:

> When rehash is requested check if dcc send is currently sending, pause dcc
> after a complete send. Do the rehash and when it's finished restart queue.

Right, and a level above what the last change did: that one stopped a rehash
COSTING a queued user their place. This one stops the reload landing inside
somebody's download at all. A reload swaps the modules a running transfer is
executing inside.

The order is the whole feature: **stop starting new sends -> let the ones in
flight finish -> reload -> start again.** A test asserts the wait precedes
`reload_modules_in_order()`, because waiting afterwards would mean the reload
already happened inside the transfer it was waiting for.

**Its own pause flag, not `update_inprogress`.** That one means "the list is
being rebuilt" and its notice says so. Telling somebody the list is rebuilding
when it is not is the kind of small untruth that makes every other message less
believable, so a held request gets its own: *"The bot is reloading its
configuration. Your request is not lost - try again in a moment."*

**Bounded, and it goes ahead anyway when the bound is reached.** A transfer can
sit idle for as long as the far end keeps its socket open. An admin who types
`!rehash` and gets silence is worse off than one whose transfer was
interrupted, because at least the second one knows what happened -
`REHASH_TRANSFER_WAIT` is 120 seconds, and the log says which of the two
occurred.

**The pause is lifted on the failure path too.** If the reload raised anywhere
after the quiesce, the bot would sit refusing every send for ever, with the
only clue a notice telling users to try again in a moment.

Ten tests, six mutations, all caught - and two of them taught something.

The order guard failed while the order was perfectly correct: a whole-file
`index()` found a COMMENT mentioning `reload_modules_in_order()` five hundred
lines above the call, so it was comparing the wait against a sentence about the
reload. Comments stripped, and the search scoped to the rehash body.

And the timeout mutation did not fail the suite, it HUNG it: Disabling the timeout did not fail the suite, it HUNG
it: from outside, "caught" and "never returns" look identical. The two wait
tests now bound their own loop by counting sleeps, so a broken timeout fails
them instead of stopping the run.
### 🔁 Changing the server or port now says a restart is needed (#302)

> when a user changes ports or other core configurations that affect `irc.py`
> and `dcc.py`, the changes do not take effect immediately

**Most of this already existed**, which is the useful part of the finding. The
save already returns `restart_required` and the Settings page already turns it
into a sentence - `SETTINGS_RESTART_ONLY` just did not contain `SERVER` or
`PORT`. So the two settings the issue actually names were the two saved in
silence, leaving an operator to work out for themselves why the bot was still
on the old server.

**What is deliberately NOT in that set matters as much as what is.** Most
network settings ARE live: the DCC port range is read per send, so a rehash
applies it to the very next transfer, and the channel list is compared and
JOIN/PARTed by the rehash itself. Listing those would train an operator to
ignore the notice, and a notice nobody reads is worse than none - so a test
pins their absence as well as SERVER and PORT's presence.

**Prompted, not performed.** #302 offered either, and restarting a process from
inside itself while it holds live transfers is a different order of risk from
telling the operator what to do next.

One thing I did and undid: I wrote a second set, `SETTINGS_NEEDING_RESTART`,
before finding the one that was already there. Two answers to "does this need a
restart" is precisely how they drift, so it went - and a test now asserts there
is only one.

Five tests, three mutations, all caught.

### 🧊 A rehash was spending the queue's retries

Neo:

> Also noticed that if the bit had some queues from a user. And admin made a
> rehash. It cancels the queue.

Reproduced, and it is not Windows-only.

**Every rehash ends by waking the queue** - `[REHASH-WAKE] Letting queued
users into the free slots...` - which calls `check_queue_and_send()` and
attempts a send for each queued user. If that attempt fails, the failure is
charged to the user's retry budget. `MAX_SEND_FAILS` is 3. **Three rehashes
deleted the row.**

**The cause of the failure is what makes it wrong.** That branch covered two
unrelated things under one message - *"file missing, empty, or public IP
unknown"*:

- A file that is missing or empty is a DEAD ROW. No amount of retrying brings
  it back, and charging the budget is exactly what stops it being re-selected
  every three seconds for ever - the reason the charge was added.
- **"No usable public address" is not that.** It is the bot's own
  configuration, it affects every queued user identically, and it is fixed by
  the operator setting `MY_IP_OR_DOCK` - not by the user waiting. Charging it
  meant three attempts silently discarded a perfectly good queue, and the
  fastest way to make three attempts happen is three `!rehash` runs.

So the address case returns without charging anyone, and says which of the two
it was: *"the address, not the file, is what is missing"*. The hot loop the
budget exists to bound is still bounded - with no public address there is
nothing to select, and the retry that would re-enter the loop is not scheduled.

**What it is NOT:** the module reload is innocent. `dcc_queue` is bound from
`runtime.py` and a reload rebinds the name to the same object, which a direct
test confirms; `channel_users` is backed up and restored in place around the
reload. Both were checked before the wake was suspected.

Four tests, three mutations, all caught. One needed its precondition asserted
rather than assumed: `is_offerable_to_strangers()` correctly refuses the
documentation ranges, so a fixture using `203.0.113.9` was testing the address
branch while claiming to test the file branch.

### 🔁 Held lists can keep themselves up to date (#302)

> Implement auto-downloading of lists from bots to keep them automatically up
> to date.

**The advert decides, not a timer.** #286 already worked out what "moved on"
means and why - their advert THEN against their advert NOW, date first and
count second, because bots count differently and an off-by-a-few would mark a
list permanently stale. A timer alone would re-ask every bot for a list we
already have, every interval, for ever: that is other people's bandwidth and
other people's transfer slots.

**"Unknown" is not "changed."** A bot that publishes no date, or one whose
advert we have not seen since starting, gives no evidence either way - and
acting on no evidence is exactly what makes an automatic feature untrustworthy.

**Off by default.** It spends somebody else's resources, which is a decision to
make rather than one to inherit.

Three bounds, each with a case behind it:

`AUTO_REFETCH_INTERVAL_HOURS` is the floor on how often ONE bot is asked, not
how often the sweep runs. A bot rebuilding its list hourly would otherwise be
re-fetched hourly, however loudly its advert changed.

`AUTO_REFETCH_MAX_PER_RUN` caps a sweep. A bot back after a month offline comes
back to a lot of stale lists, and asking for all of them at once is a burst of
outbound requests nobody asked for. The rest go next time, **oldest first**.

And every request goes through `build_list_fetch_enqueue_result()` - the same
enqueue the dashboard's own Refresh uses - so the slot limits, the duplicate
guard and the queue ceiling apply exactly as they do to a fetch an operator
started by hand.

Two tests were wrong first time and the fix is the interesting part. They
asserted the sweep REPORTED a bot as started, but `started` is what the
enqueue accepted, and in a fixture with no live connection it refuses. That is
the right answer - a sweep reports what was actually queued, not what it
decided to try - so the tests now assert what the sweep controls: which bots
it asks for, and how many.

The coverage gate refused this before it could be pushed: `auto_refetch_worker`
is an endless loop and no test entered it. It takes its sleep as an argument
precisely so one can - watch a pass, then leave - and allow-listing a loop that
was built to be drivable would have been the wrong answer. It also now has a
test for surviving a sweep that raises, because a background thread that dies
on one bad pass stops refreshing anything, silently, until a restart.

Eleven tests, nine mutations, all caught. The last needed a source-reading guard
for the worker's own start, asserted as a sequence: a thread started
unconditionally and a guard with nothing behind it are both wrong, and either
alone passes a check for the other.

### 🪟 The Windows install is seven numbered steps (#302)

Neo's format, with the two things that actually go wrong written in rather
than left to be discovered:

**A new Command Prompt.** An already-open one still has the old PATH, so
`python --version` fails immediately after an install that worked perfectly.

**What `where python` printing only a `WindowsApps` path means.** That is the
App Execution Alias, not Python - the installer's *Add Python to PATH* box was
not ticked, and the fix is Modify rather than a reinstall. The guide already
explained why `python3` opens the Microsoft Store; this is the same trap one
step earlier.

`pip install -r requirements-web.txt` is marked as the dashboard-only step it
is: the daemon starts and serves files without Flask, and an operator who does
not want a web page should not be running a pip command at all.

### 📍 The bot refused the answer to its own question

From Neo's log, requesting from the dashboard:

```
[FETCH] Requested 'BBCRadio - Under Milk Wood - Richard Burton.mp3
        ::INFO:: 79.53MB' from FlacMeDCC (request f96ba6b77dff).
[FETCH] Rejected unsolicited DCC SEND from FlacMeDCC
        ('BBCRadio_-_Under_Milk_Wood_-_Richard_Burton.mp3'):
        no matching pending request.
```

We asked. They answered. We threw it away as unsolicited.

**The `::INFO::` suffix was part of the stored filename.** The dashboard sends
what the operator clicked, and what they clicked is a row out of another bot's
list - `<filename>  ::INFO:: <size>`. `dcc.py` has stripped that on the SERVING
side since #234, where a request arrives; the fetching side never learned to,
so the size went into the row as part of the name and out onto the wire with
it.

**The normaliser was not the gap, which is worth saying because it looks like
it should have been.** `_claim_matching_offer_locked()` already compares
underscore/space-normalised, exactly to survive the one transformation every
DCC client applies - the peer's underscores were never the problem. A size that
only one side is carrying is not whitespace, and a test pins that the
normaliser alone could not have bridged it.

**Stripped at row creation, not at match time**, because that value is also
what goes out on the wire. The peer above coped with the suffix; a stricter one
would not have.

**"file" rows only.** A folder row's `requested_filename` is the literal
`!rar <path>` request text, which `new_fetch_row()`'s own docstring depends on
being preserved. A mutation showed that restriction was not falsifiable at
first - a `!rar` path has no `::INFO::` in it - so the test now uses a folder
path that does. It is odd, but the path is built from a heading in ANOTHER
bot's list and is not ours to predict; stripping it would turn a request they
could have answered into one they cannot.

Five tests, three mutations, all caught.

### 🔓 The fetch size caps can be turned off (#302)

Neo asked for two of them to be removed outright:

> Remove "Max fetched master-list zip size (bytes)". Master lists should not
> have a size limit. Remove "Max fetch file size (bytes)" limitations. Files
> should never be rejected based on size.

**Switchable off rather than deleted.** 0 means no limit, which is the same
outcome for the operator who wants it and no change for the operator who does
not know these exist. Deleting them would take the choice away from everyone.

**Defensible here in a way it would not be on the serving side.** A fetch is
SOLICITED: `handle_incoming_offer()` only accepts an offer matching a row this
operator created, so what arrives is the thing they asked for, onto their own
disk. The cap guards against a peer answering a request with something
enormous - worth a default - not against a stranger pushing files at us, which
is refused earlier and for a different reason.

**And `MAX_FETCH_LIST_FILE_SIZE` was low for the same reason the text ceiling
was.** 10MB, described as "generous over any real master-list zip", on the
evidence of one 4MB list. Real lists in that channel run to 31MB of TEXT, and
an archive of one is several MB - close enough to the old cap that the next
library along lands on it. 64MB now.

The refusal message names the setting and says 0 turns it off, because the
operator who hits one of these has no other way to tell it is theirs to change.

Five tests, four mutations, all caught. One of the tests was wrong first time
in a way worth keeping: with the cap off the row still FAILS - it goes on to
connect to a peer that is not there - so "not failed" was asserting that a
fixture socket connects. What the cap decides is whether it failed FOR ITS
SIZE, and that is what is asserted now.

### 🗜️ FILE_DIRECTORY is the fallback, and three places thought it was the truth

Neo:

> under Paths & Storage, this is not needed anymore

Half right, and the half that is not was hiding two real bugs.

**It IS still needed.** It is what an install with no folder list serves from,
which is most of them, and `library.folders()` falls back to it deliberately.
Removing it would take that fallback with it.

**But nothing should consult it directly**, and three things did:

**The daemon refused to boot on a stale one.** `oserve.startup()` checked
`config.FILE_DIRECTORY` alone - so an operator who had configured folders and
left it pointing at a drive that is no longer there got
`[CRITICAL] Missing directory` and an exit, while the folders it actually
serves from sat there perfectly readable. A setting nothing reads any more was
able to stop the bot starting.

The same check told an operator with folders configured and the setting blank
that the daemon "cannot search or serve anything" at every single start, which
was simply untrue. It asks `library.folders()` now, and refuses only when
EVERY configured folder is missing - a single unavailable one is a scan-time
condition the build already skips with a warning, which is the rule
`update_list`'s own entry point already applies.

**The build read it for nothing.** A single-folder leftover: `scan_root` built
from `config.FILE_DIRECTORY` and then immediately overwritten inside the
per-folder loop. Dead since #164 - and exactly the kind of leftover that makes
a superseded setting look load-bearing. `update_list.py` now contains no
reference to it at all, which a test pins.

**And the Settings page called it "Music directory."** Beside a folder editor
that overrides it, that reads as the setting that matters. It says "used only
when no folders are set" now - which is true in both states and needs no
conditional UI.

Six tests, three mutations, all caught.

### 🔄 Downloads reads newest first, and a failed fetch can be asked for again

Both Neo's, both about the view you open when something has gone wrong.

**Newest on top.** The reason to open Downloads is almost always the most
recent thing that happened, and oldest-first meant scrolling past every
completed fetch to reach it. Still ordered by time, so it is no less stable
between polls - the rows are counted from the other end.

**Redownload, next to Delete, on a row that failed or was rejected.** That is
the row an operator most wants to retry, and the only way to do it was to go
back to the List Browser and retype the nick.

Three things it is careful about:

**A list and a file are asked for the way they were asked for.** They always
differed - a list is `@<bot>`, because we cannot know what the bot will call
its archive, while a file is named outright. Retrying through the wrong route
would create a row of a different KIND from the one being retried.

**Neither the bot nor the filename goes into an attribute.** `escapeHtml()` is
textContent -> innerHTML: it encodes `&` `<` `>` and leaves a double quote
alone, and both of those values come off the wire. The rows are kept in state
and looked up by the row's own id, which is ours and is hex. Same rule the
file lists and the folder rows already follow.

**The row being retried is not deleted.** It is the record of what happened,
and throwing it away as a side effect of retrying would remove the reason the
retry was needed.

Not offered on a row that succeeded: there is a Download button there, and
re-fetching a list already held is what the List Browser's own refresh is for.

Six tests, four mutations, all caught.

### 📥 Two reasons a real list was thrown away at the last step

Both from one screenshot of the Downloads view: five fetches, four refused.

**A list that arrives as plain text was refused for not being a zip.**
*"extraction aborted: File is not a zip file"* - on a fetch that completed at
100%. `update_list.py` has published `.txt` as a `LIST_FORMAT` since #201, so
a bot sending one is doing an ordinary thing; there was never a reason to
expect only archives back.

Detected by CONTENT, not by the offered filename. That name comes from the
sending bot, and a peer calling an archive `list.txt` must not skip the member
and traversal guards by renaming it - `zipfile.is_zipfile()` reads the file's
own end-of-archive record.

**It still has to look like a list.** The zip route gets its plausibility from
the archive guards and `_pick_list_file()`; this route has neither, so without
a check any file at all that is not a zip would be stored as a bot's list -
parsing to zero rows, reported as a successful fetch, and answering every
filter with nothing. The property checked is the one the parser needs, a
request line, read from a bounded prefix because the file may be 128MB and the
answer is in the first few lines.

**And the size ceiling was set from a sample of one.** Three lists in that
same channel - 25.7MB, 26.8MB, 31.5MB - were all refused by a 20MB cap. The
comment justifying that number said it plainly: *"5x headroom over the largest
real list anyone here has actually seen"*, that list being this operator's own
4MB one from a 1.21TB/47,420-file library.

A FLAC library with long filenames produces a far bigger text list than a
similar MP3 one, and this ceiling has to hold for libraries this operator will
never see. The default is 128MB - four times the largest actually observed,
and at roughly 80 bytes a row about 1.6M rows, inside the four million the
cross-list index is measured against.

**It is a setting now, which reverses the earlier reasoning deliberately.**
"An internal safety bound, not an operator-facing knob" holds when the right
value is knowable here. It is not: it depends on the libraries of bots in
somebody else's channel. The refusal message names the setting too, because
the operator who hit this had three real lists refused with no indication it
was adjustable.

The test that pinned the old default has been rewritten to pin the new one
against the largest list actually offered to this bot, rather than against the
one that happened to be nearest to hand.

Nine tests, five mutations, all caught.

### 🗄️ The list says MEDIA, not MUSIC

Neo, reading a real film list:

> we have a virtual name in the list, so it works with both linux paths and
> windows paths. i opened up the VIDEO list -
> `D:\MUSIC\TV\Spider-Noir (2026) {imdb-tt30460310}\Season 01\` -
> maybe we should change this `D:\MUSIC\` to `D:\MEDIA\`

He is right, and the heading contradicts itself: the second component is the
operator's own FOLDER LABEL, so a fixed "MUSIC" in front of "TV" says
something about the path that is not true. Since the lists stopped being
music-only, the prefix stopped being accurate.

**Safe to change**, and `list.py` had already established why: QuickList made
the written path an OPTION, so lists in the wild carry full drive paths,
stripped relative ones and bare folder names, and OmenServe consumed all of
them. There is no canonical prefix. AutoQ does not read it either - its
dequeue match takes `$nopath()` of the folder, the last component only.

**What is NOT safe is ceasing to understand the old one.** Every list already
in somebody's hands says `D:\MUSIC\`, and a row pasted back out of one has to
keep working. So `LIST_FOLDER_PREFIXES` is what the read side uses: one prefix
is written, all of them are understood.

That distinction was not academic. `dcc.py` uses the prefix to decide whether
a line IS a folder heading, and with only the new one it stopped seeing the
headings in every list already downloaded - a bare request against one then
resolved nothing at all. The test that counts resolutions caught it
immediately.

**And the builder had four copies of the literal**, none of them the
constant - so changing `LIST_FOLDER_PREFIX` alone would have changed nothing
about what gets written. All four go through it now, and a test asserts the
builder holds no prefix of its own.

The test sweep needed care in both directions. Assertions about what the
builder WRITES moved to the new prefix; fixture INPUT deliberately kept the
old one, because that is exactly what a list in somebody's hands says and
changing it too would have quietly removed the coverage that the legacy prefix
still resolves. Four assertions were changed and then changed back for that
reason: they echo their fixture rather than the builder.

Five tests, four mutations, all caught.

### 🧰 Lists are defined from the Settings page (#26, stage 5 - complete)

The last stage. A list is a name, the channels it serves, its folders, and
whether it is primary, edited in the Paths category and written through
`POST /api/lists`.

**One editor, never two.** With more than one list the folders live INSIDE the
lists, so leaving the single-folder editor on screen would be a second place
to edit folders that quietly does nothing. With one list the familiar editor
stays exactly where it was, and a button moves an operator to the other -
seeded from the folders already configured, so "serve more than one list" does
not begin by throwing away what is already there. Nothing is written until
they save.

**The whole set is validated, not each row**, for the reason the folder
endpoint already gives and one of its own: two lists can each be perfectly
good and still be an invalid pair. The same channel bound to both, or the same
name twice, is only visible in the set. Every fault comes back at once, each
naming the specific other entry it conflicts with - "invalid list
configuration" tells an operator with four lists nothing about which two to
look at.

The rules: a name is required, because a list is bound to channels and picked
by name; names are unique case-insensitively, because `list_by_name()` matches
that way and on Windows two directories differing only in case are one
directory; a channel belongs to one list; a channel name starts with `#` or
`&`; and one list must be primary, since a private message carries no channel.
Folder rules are NOT repeated - `problems()` already owns them, and each
list's folders go through it so a bad label reads the same whichever screen it
was typed on, prefixed with the list it belongs to.

**An empty set removes the file rather than writing `[]`**, because
`load_lists()` already falls back on an empty file - so `[]` would leave
something on disk that does nothing and the next operator to read it would
have to work that out.

One thing a guard caught: `saveLists()` read `res.body`, and `postJson()`
returns `res.data`. `tests/test_web_assets.py` has asserted that exact
convention since the last time somebody got it wrong.

And one branch was written and then deleted. `list_problems()` checked whether
two names would land in the same directory - but `list_slug()` is one-to-one
by construction, so it can never fire, and a rule no test can reach is the
same dead guard removed twice already today. The invariant is asserted where
it lives, in `list_slug()`'s own tests.

Twelve tests, ten mutations, all caught. **#26 is complete**: a list is a
thing, it has its own directory, requests route to it by channel, each channel
advertises its own, and they can be defined without hand-editing JSON.

### 📢 Each channel advertises its own list (#26, stage 4)

The advert loop already read the figures once per channel. It just read the
same ones every time - so this stage is the loop asking which list the channel
has before reading them.

A count and a size from another channel's library is a claim nobody there can
act on: they would see a number, ask for something in it, and be told no.

**A channel with no list bound gets no advert at all**, which is where #26's
rule is most visible. The alternative is a bot announcing a library it will
then refuse to send from - worse than a bot that is simply quiet there.

The lookup goes BEFORE the figures rather than after: reading them first and
discarding them would be equally correct and would make every unserved channel
pay for a list read it was never going to use.

One mutation was not caught first time, and the reason was worth the extra
test. `find_latest_list(name)` inside the counter feeds only the DATE and the
"is there a list at all" check - the count itself comes through
`all_list_paths(name)` - so a test asserting counts alone passed while that
call read the primary's. The sharper property: a list that has never been
built must report "No List" even when another list has one. It is the sentinel
the advert skips on, so with it wrong the channel would advertise a library
that does not exist.

Nine tests, five mutations, all caught.

### 🔀 A request is answered from the channel's own list (#26, stage 3)

The first stage that changes behaviour. `library.list_for_request()` is the
whole rule, and each of its three parts stops a different thing going wrong:

1. **A channel bound to a list gets that list.**
2. **Otherwise the PRIMARY answers - but only if it binds no channels of its
   own.** That is every install today: one list, no bindings, answering
   everywhere. Without this part, adding routing would be an upgrade that
   silenced every bot in existence.
3. **Otherwise nothing.** Once the primary names its own channels the operator
   has said where each list belongs, so a channel they did not name is one this
   bot does not serve. Without this part, binding a channel would mean nothing.

A private message is always the primary, and that is not the same as part 2: a
PM is not "a channel with nothing bound", it is not a channel at all.

**A channel serving nothing is answered with silence, not an error.** An error
implies something went wrong; nothing did. The bot does not serve there.

Three entry points route: `send_file_list()`, `execute_search()` and
`dcc.handle_download_request()`. The last resolves the list ONCE at the top,
because two things below need the same answer - the folders a bare filename is
looked for in, and the list the name is resolved against - and they must not
disagree. A heading is resolved against its own list's folders too: a label
names a folder OF a list, and reading one list's heading against another's
folders would send a request into the wrong library.

**A real defect fell out of writing the tests.** `dcc.py` looked for a list
ARCHIVE in `config.LOCAL_LIST_DIR` directly. Every list writes its archive
under the same name in its own directory, so any list but the primary would
have been offered its archive by `send_file_list()` and then told "file not
found" by the very next step. A source-reading test could not have found it;
driving the real path did, immediately.

**And three of my own tests were too weak to fail.** A mutation run broke the
routing in `send_file_list()` three separate ways and all three assertions
passed: one checked that a `return` appeared somewhere in the next 400
characters, one never named the call it cared about, and one asserted
`library.folders(wanted_list)` was *present* while two such calls exist and
only one was mutated. The first two are behavioural now - `send_file_list()` is
driven for real - and the third COUNTS: every `library.folders(` in that
function must be the scoped form.

Getting that behavioural test running took three attempts, and the reason is
worth recording: `list.py` binds `oserve` at import time, so the fake the test
harness installs into `sys.modules` is not the object it calls. Every notice
was going to the real module while the test asserted on an empty list.

Fifteen tests, eleven mutations, all caught.

### 📁 A list gets its own directory, and the build takes a name (#26, stage 2)

**The list is in the PATH, not the filename.** That is the whole design
decision here and it was made against the alternative. The names a build
writes already carry three markers - `-RAR-`, `-VIDEO-`, `-FULL-` - and the
code reading them has been wrong about a base name containing one of those
before, twice. Putting the list in the name would have made every one of those
parsers grow a dimension; putting it in the directory means every filename
stays exactly as it is.

**The primary keeps `LOCAL_LIST_DIR` itself.** Nothing moves, no upgrade
migrates anything, and a single-list operator cannot tell this landed. Only a
second list creates a subdirectory.

`list_slug()` is one-to-one, which took a second attempt. Replacing unsafe
characters is not injective - "A/B" and "A B" both flatten to "A_B" - and two
lists landing in one directory would overwrite each other's index, archive and
side files with no error anywhere. A name that had to be changed now carries a
short digest of the original; a name that needed no changing keeps exactly
itself, so "Films" is still the Films directory. `hashlib`, not `hash()`, which
is randomised per process and would rename every directory on each start.

`generate_master_list()` takes a list name and resolves ONE directory from it;
every path in it comes from that. `generate_all_lists()` builds every list, and
on a single-list install is one call with no name - byte for byte what running
the script has always done.

**Each list is built independently.** A list whose folder is on an unavailable
mount must not take down the list whose folder is on a local disk, so a failure
is recorded and the loop continues. The failures are NAMED rather than counted:
"1 of 3 failed" sends somebody to read a log they already have open.

Three things the mutation run corrected:

**A guard that could not fail.** `list_dir()` had an early return for
`name is None`, and `list_by_name(None)` is None, so the branch below already
answered `root`. Deleting it changed nothing. It went, rather than being
propped up with a test that could only ever pass.

**A test that could not see what it was checking.** The prune test built two
lists and asserted both survived - but every list writes the SAME filenames,
same base name and same date, differing only in directory. So a prune scanning
the root would find another list's index under a name its own keep set also
held, and delete nothing. The test now watches the SCOPE: a stale file in each
place, one build, and only the built list's own may go.

**And two of my own assumptions were wrong,** which the suite said before I
did. A `.mkv` in a second list's folder is not in that list's master index -
it is in its film list, because the split is on by default - so the test was
asking the wrong file. And a list with no folders is not a failed build: it
publishes an empty list, which is exactly what was configured, and reporting
that as a failure would send an operator looking for a fault they do not have.

Twelve tests, thirteen mutations, all caught.

### 🗂️ A list is now a thing, not a folder set (#26, stage 1)

The first piece of multiple lists, and deliberately the piece that changes
nothing. `library.ServedList` is a name, the folders it is built from, the
channels it answers in, and whether it is primary; `data/lists.json` holds
them; `lists()`, `primary_list()`, `list_for_channel()` and `list_by_name()`
answer for them.

**With no `lists.json` - every install - there is ONE list, over exactly the
folders that install already served.** Not a copy of that resolution, the same
one: `_configured_folders()` is what both `folders()` and the implicit list
read, because two answers to "which folders" is how they drift. A test asserts
the implicit list's folders and `folders()` are equal, which is the property
this whole stage rests on.

**`folders()` gained an optional list name and kept its meaning without one.**
That is FUTURE.md's own argument for building the accessor before the feature:
the folder set moving inside a list rebinds one function instead of touching
thirteen call sites a second time. All thirteen are untouched here.

Three decisions worth naming, because each has a wrong answer that looks
reasonable:

**Exactly one primary, settled on load.** A private message carries no channel,
so the primary is the only answer to "which list does this mean" - and "none of
them" and "two of them" are both answers somebody would otherwise handle at
every call site. A file with no primary promotes the first; a file with several
keeps the first and demotes the rest.

**A channel bound to nothing gets `None`, not the primary.** #26's rule is that
such a channel gets no advert and no requests answered. Quietly serving the
primary instead would be the opposite of that, and it would look like it was
working.

**An unknown list name gets `[]`, not the primary.** A caller asking for a list
that is not configured has a bug, and serving it somebody else's folders hides
that behind output nobody would question.

An install that has chosen no folder at all still gets one list with no
folders, rather than no lists: "which list is this request for" has to stay
answerable on a fresh install, where `FILE_DIRECTORY` is deliberately blank
until the dashboard sets it.

Twenty-one tests, ten mutations, all caught.

**Still to come:** per-list naming for the files a build writes, routing a
request to the list bound to the channel it arrived in, per-channel adverts,
and the Settings page. Each its own change.

### 🚪 The OmenServe import is offered during setup

The Stats page has imported these totals since #69. The gap was never what it
imports - it was WHEN it asks. Somebody migrating from OmenServe is looking at
`configure.py`, not at a dashboard they have not enabled yet, on a feature they
have no reason to know exists. The roadmap called this "an OmenServe migration
path offered on first run"; this is that.

**Nothing here re-implements the import.** `omenserve_import` reads the file,
and webserver's preview and apply do the rest. A second copy of "which
variables, what counts as a sane number, what actually landed" is precisely how
two answers to one question drift apart - so a test asserts `configure.py`
calls both, and that it does not name a single `%mx.` variable of its own.

**It shows the figures before it writes them.** A number read out of somebody's
file is not a number they have agreed to, so the rows and the notes are printed
and a second yes is asked for. Saying no leaves everything alone.

**It never ends the run.** A setup script that dies on a mistyped path has cost
the operator every answer they already gave. An unreadable file, a path that
does not exist, a file with nothing in it: each says so and carries on.

**And it reads a file mIRC actually wrote.** mIRC writes `vars.ini` in the
machine's ANSI code page, so a nickname with an accent in it is enough to make
the file invalid UTF-8 - `errors="replace"` rather than a crash. The numbers
are ASCII either way, and a mangled character in a variable NAME simply stops
that line matching a field, which is the same outcome as the variable being
absent.

A quoted path works too, because dragging a file onto a terminal hands you one
on both platforms and an operator who does that is doing the sensible thing.

### 🔎 The build says when two files share a name

A request names a FILE, not a path - a bare filename is all the list gives a
requester to copy. `dcc.handle_download_request()` resolves that name against
the list and serves the FIRST folder it finds it under, so every later copy is
listed, looks requestable, and can never be sent. The requester does not even
get an error: they get the other file, at the other size, silently.

The dashboard has answered this since #164 - Tools > Verify list, computed on
demand from the list on disk. What was missing is that it only answers when
somebody goes and looks, and the operators most likely to have collisions are
the ones running several folders without watching a web page. So the build
says it too.

**It costs nothing.** The scan is already holding every (folder, filename,
size) it wrote; the count is a pass over data in memory, with no second walk
and no side file to keep in step.

**Both lists, in the order the resolver reads them.** `all_list_paths()` hands
it the music list and then the film list, so a name in both resolves to the
music copy and the film row can never be sent. Counting the two separately
would miss exactly the collisions the split introduced - which a test pins,
because it is the easy thing to get wrong.

**The count only.** The detail belongs to the view that already presents it
properly, with headings resolved to paths this machine has. Printing them here
would be a second presentation to keep in step with the first, and one answer
to this question is the point - `find_duplicate_filenames()` is asked by both,
which a test also pins.

Nothing is said on a clean library. A warning on every build is a warning
nobody reads.

**The roadmap asked for this as "a list validator run at build time" and was
stale by half:** the validator has existed since #164, and its own Implemented
section three lines up said so while the wanted-list asked for it again. The
bullet is gone. That section also still called the tab "File Lists", which
#285 renamed to List Browser.

### 📦 A ceiling on what `!rar` will pack

Nothing bounded a pack. A request packed whatever the folder held, and the only
thing that ever stopped one was `RAR_TIMEOUT` - by which point the archive is
already on disk in `TMP_ZIP_DIR`, the single pack slot has been held for half an
hour, and the requester has had no answer at all.

The film-and-series split is what made it reachable rather than theoretical.
The film list publishes folder headings inside the archive every user
downloads, and `list_heading_parts()` strips the prefix, so a heading pastes
straight back as a request - which means a folder deliberately kept out of the
album list is nameable by anyone in the channel. `dcc.py` already said so in a
comment: *"There is no size cap anywhere, so that is an unbounded pack behind a
line anybody in the channel can send."*

`MAX_RAR_FOLDER_SIZE`, 10 GB by default, 0 for no limit.

**Checked BEFORE the request is queued.** Accepting it and discovering the size
at pack time costs a pack slot, half an hour of timeout, a part-written archive,
and still ends with the requester told nothing useful. The size is knowable at
request time, so it is known then.

**Measured the way the pack measures.** `rar a <dir>` takes the directory and
everything under it, so the walk is recursive - a top-level-only check would
measure something other than what gets packed, and the difference is exactly
where a huge folder hides.

**And it stops as soon as the cap is passed.** The answer wanted is a yes or a
no, not a total, and the folder this is most useful on is the enormous one - so
walking all of it to produce a number nobody reads is the one cost worth
avoiding. A folder a hundred times over is refused after a few thousand entries
rather than after all of them. With no cap set it does not touch the disk at
all, which a test asserts by watching `os.walk`.

**Not done here: filtering the album list by size.** The scan has per-file
sizes and could skip writing a row for an over-cap folder, so the bot would not
advertise what it will refuse. But those sizes are the MUSIC files, and the
pack takes everything in the folder - so a list-side check would under-measure
exactly where it matters. The gate is the enforcement; doing both properly
needs the scan to carry a second total, which is its own change.

The default is chosen to refuse the pathological case without refusing anything
real: a FLAC album is a few hundred megabytes and a large box set a few
gigabytes, while the folders this exists for are tens or hundreds. A test pins
both ends of that, because a default that refused ordinary albums would break a
working feature on upgrade and one that passed everything would not be a cap.

### 🧹 A real bot's nick out of the fixtures, and a line nobody could act on

**`Vibessono` is a real bot**, one of the 32 observed advertising in the
channel #133 was written from - the issue discusses it by name, as the only one
of the 32 publishing no list date. It was being used as an ordinary fixture
nick in three test files, 33 times, and `tests/` ships. Renamed to `ReelBot`.

The distinction that matters, since this project deliberately does the
opposite elsewhere: `tests/test_advert_listener.py` says **"THE FIXTURES ARE
REAL"** and reproduces captured adverts untidied, because no two bots format
alike and a parser written against a cleaned-up sample is a parser written
against a bot nobody runs. That is a reasoned choice and it stands. The
difference is that there the nick is inseparable from the sample being parsed,
while here it was a name for "a bot we hold a list from" - where an invented
one does the identical job. Real data where it is the point; invented data
where it is not.

**And a roadmap line nobody could act on.** "Roughly forty further verified
findings" was written on 2 Sep when the README was split, with no list behind
it anywhere in the repository - `git log -S` finds it arriving already
summarised. One of its two named examples, the queued `!rar` pack never
re-dispatched, is fixed and wired at three call sites; the other cannot be
checked without knowing what the other thirty-eight were. It now says that
plainly instead of implying a backlog that can be picked up, and asks the next
audit to leave a list that outlives it.

### 🔍 Thirteen audit findings, and the worst made the filter match nothing

From the multi-agent audit of the same day's work, plus the refutation pass
over what it had not tested.

**The cross-list filter never matched anything in production.**
`list_index.index_bot_list()` read `row["filename"]`;
`list.entries_to_filelist_rows()` writes `row["title"]`, and has never written
`"filename"`. Every row went into the index with an empty name.

Forty-nine tests passed over it, because the fixture built its own dicts with
a `"filename"` key — the only caller in the codebase that did not go through
the producer. It goes through it now, so a key renamed on either side fails
here.

**Bot identity: one cause wearing two hats.**

The `DELETE` that makes a refetch REPLACE a list used SQLite's binary `=` on
whatever the operator typed, while every reader case-folds —
`fetched_bot_lists` is keyed lower-case, `indexed_bots()` lowers, FTS5 MATCH
folds. Fetch from `Dude`, refetch from `DUDE` (the ordinary case: the sidebar
prefills the nick and a refetch is retyped by hand) and both copies stayed.
The stale one answered searches beside the new one, `indexed_bots()` reported
a single bot so nothing could see it, and nothing could free it. The key is
normalised on both sides now, and the payload maps back through
`fetched_bot_lists` so the operator still sees the nick as that bot spells it.

And `bot:"name"` is an FTS5 **phrase** over a tokenised column, not equality:
unicode61 splits on punctuation, so `Bot` matches `Bot-2`, `Bot_away` and
`Bot|gone`. Holding both with only `Bot-2` matching, the sidebar left `Bot`
undimmed and the status line said "1 match in 2 lists". The MATCH is a cheap
pre-filter now, with the bot column compared for equality on the rows that
come back. Every fixture was token-disjoint, which is why they passed.

**A stale reply repainted the table.** `runFilelistsFilter()` had a token and
its comment claimed *"only the newest is allowed to render"* — but it was
compared inside the debounce callback, before `loadFilelists()` was called. It
decided which request to SEND; nothing decided which reply to DRAW. At 120ms
of debounce a broad term is slow and one more character is fast, so the narrow
reply arrives first and the broad one repaints under the later term — and
caches itself, so every later re-render keeps serving it. The load carries its
own token now, checked in both the `then` and the `catch`.

**The four-second bot poll undid the filter.** The sidebar is rebuilt from
scratch every `FILELISTS_BOTS_POLL_MS`, and everything the filter puts on it
lives in classes on those rows — so the greying, the crossed-out names and the
operator's own switched-off choices vanished four seconds after appearing,
repeatedly, while they were still typing. The scroll position and keyboard
focus went with them, which on a thirty-two-advertiser channel means the list
jumps back to the top while being read. The rebuild restores all three.

**Nothing backfilled the index.** `index_bot_list()` has one caller — a fetch
completing — while held lists survive restarts. So an operator upgrading with
lists already fetched had a full `fetched_bot_lists` and an empty index, and
this module's "we cannot tell" guard did not cover it: `_connect()` creates
the database on demand, so the connection is not None, every per-bot query
simply misses, and the page states **positively** that no list holds a match.
A filter that reads as working and answers wrongly. `oserve` now indexes what
is missing at startup, through the same parse the fetch uses.

Two notes on the tests themselves. A guard matched this session's own prose
for the fifth time — a comment explaining the attribute-injection rule
contained the literal it warns about, so the XSS scan fired on the
explanation. And a source-reading test passed against a dead branch: the
mutation moved the call behind `if (false)` and the test only checked the call
existed. It asserts the call and its guard as one sequence now, which is as
close to "reachable" as reading source gets.

**Seven more, from the same audit.** Six of them share a shape worth naming:
each is a place where "we could not tell" was reported as a definite answer.

**A list that could not be checked was greyed out as holding no match.** One
`except Exception: continue` in the per-bot loop, and that bot fell out of
`matched` and straight into `empty` - which the page renders as a POSITIVE
statement, greying the list and counting it in "no match in N lists". The same
false claim the "no index" branch three lines above already refuses to make,
reached by a different route. A failed query now leaves the list unmarked and
says so in the log.

**The fetch queue was read without its lock.** `fetch_marks_by_bot()`
iterates `config.fetch_queue` on Flask's thread while `enqueue_fetch()`
inserts into it from the transfer thread - "dictionary changed size during
iteration", which here is a 500 on the List Browser at the exact moment
somebody starts a fetch, which is when they are most likely to be looking at
it. `build_fetch_status_payload()` was already written this way, with a
comment explaining why; this one simply had not been.

**A count too large to carry was repeated as though exact.** For a bot we
have not fetched from, `count` is THEIR advert text parsed with
`irc._as_int()`, which builds a Python int of any size. JSON has no limit and
JavaScript does: `JSON.parse()` rounds anything past 2^53 to the nearest float
before the page sees it, so a bot advertising twenty-three digits had a
DIFFERENT twenty-three digit number rendered beside its nick, in thousands
separators, looking precise. Dropped rather than clamped, because the rule
here throughout is that an absent field means "they did not say" - which is
the truthful reading of a number we cannot carry.

**`hidden` did not hide.** `.filelists-filter-actions { display: flex }`
outranks the UA stylesheet's `display: none` for the attribute, so the row of
buttons showed from first paint while the markup, the script and the reviewer
all said it was hidden. The guard for it is general: every class in
`index.html` that carries `hidden` and sets `display` must also carry a
`[hidden]` rule, and the test fails if it finds nothing in scope to examine.

**No ceiling on the request body.** Flask reads one into memory before any
route decides what to do with it, and this daemon shares a machine with
transfers it must not starve. 8MB - far above the largest legitimate body
here, a pasted `vars.ini` - and it applies before authentication, which is
where the daemon has the least reason to trust what it is handed.

**The index write no longer holds a second copy of the list in memory.** The
rows stream into `executemany` rather than being materialised into a list
first; at the four million rows this index is measured against that was
hundreds of megabytes of tuples held for the length of the write. The LOCK
stays held for the whole write, deliberately, and now says so: it is the
transaction boundary as well as the connection guard, and releasing it between
the delete and the insert would let a search read a list that had been emptied
and not yet refilled - the same false "empty" as the first finding above.

**A connection that could not be set up was dropped without being closed** -
and I had read this finding, decided it was already handled, and moved on. The
suite disagreed: a `ResourceWarning` out of preflight pointed straight at it.
`sqlite3.connect()` is LAZY, succeeding on a corrupt file, a text file, on
anything openable at all, so the failure lands on the first `execute()` with a
real open handle already in hand. Returning None without closing it leaked one
per call, and `_connect()` is called on every search: an operator with a
damaged index leaked one for every keystroke in the filter bar. On Windows the
file also stays locked until the object is collected, so the next attempt
fails for a NEW reason and the log stops describing the original one.

The test harness closes that connection after every test too. It is cached at
module level and kept for the life of the process, which is right for the
daemon and wrong for a run of 2,799 tests: one opened indirectly - by a fetch
completing, or a dashboard route - stayed open pointing at a temp directory the
teardown was about to delete, and surfaced at interpreter shutdown with no test
name attached to it. That is how the leak above was found.

**One finding the audit reported was genuinely already handled**, and now has
a guard so it stays that way: a `!rehash` moving `LIST_INDEX_FILE` takes
effect, because `_index_path()` reads config on every call and `_connect()`
compares it against the cached one. Capturing the path at import is the
obvious tidy-up and would send every write to a file nothing reads.

### 📌 The List Browser says what you have already asked for (#133)

The last unbuilt item in the issue, and it wants two states rather than one:
**asked** while a request is still in flight, **have it** once it has arrived,
and **nothing at all** for a failed one - a failure is not a thing you have,
and marking it would discourage the one useful action left, which is to ask
again.

The three map onto states `dcc_fetch` already keeps. "Asked" is exactly its
`_UNRESOLVED_FETCH_STATES` tuple rather than a second list of the same names,
which is what stops this drifting the day a state is added there. Only file
requests mark anything: a "list" or "folder" row asks for the whole list or a
packed archive, not for any row in the table.

A mark belongs to one bot. Two bots can hold a file of the same name, and
asking one says nothing about the other. "Have it" beats "asked" for the same
file, because having it is the more useful of the two answers.

**A defect in the cross-list filter, found while wiring this up.** A row is
only fetchable when it belongs to someone else's list - browsing our own is
filesystem access already. That decision asked whether the SELECTED SOURCE is
another bot's list, and the source defaults to our own. So filtering before
picking a bot rendered every result with **no checkbox and no way to queue any
of it**, which is the entire point of finding them. The folder's `!rar` button
had the same defect at its own call site, suppressed on every group in a
filter result.

Both are fixed, and the guard is derived rather than sliced from the first
occurrence it finds - the first version of it checked one site and reported on
the other, failing while pointing at code that was already correct.

**One row shape, still.** `mark` is declared in
`list.entries_to_filelist_rows()` rather than added by whichever payload
happens to know about it. Our own list and a fetched one go through that
function precisely so the frontend sees one shape, and a key present in one
and absent in the other is how that stops being true - it is always `""` for
our own list, which is correct rather than a placeholder, since nothing is
ever requested from ourselves.

Two mutations corrected tests. The row-shape test listed its five keys by name
and went stale the moment a sixth was added, failing on a change that kept the
property it exists to protect; it derives the shape from that one function
now. And the "have it beats asked" test happened to put the completed row
last, so "last row wins" gave the right answer for the wrong reason and the
guard could be deleted without failing it.

### 🔎 One filter across every list you hold (#133)

The last of #133's slices. Type in the List Browser and matches from every
fetched list appear together; bots with nothing matching grey out in the
sidebar.

**Why there is an index at all.** `list_fetch` re-parses a list fresh on every
call - deliberately, since #76 removed unbounded retention - so searching every
held list by re-reading them is arithmetically out of reach rather than merely
slow. #133 measured one 719k-file list at 2.0s and ten held lists at about
11s. No amount of debouncing turns eleven seconds into typing.

**#133 named SQLite and it was right, but its numbers were not.** The issue
proposed an ordinary indexed table and called it "milliseconds across millions
of rows". That is true of one of the two queries this feature needs and false
of the other, and the false half is the headline behaviour. Measured here
against 4,000,000 rows in ten lists, the sizes #133's own channel capture
recorded:

| | page of results | which bots have nothing |
|---|---|---|
| plain table, `LIKE '%term%'` | 1-41 ms | **1150-1400 ms** |
| FTS5, per-bot `LIMIT 1` | 1-4 ms | **2-4 ms** |

Paging is fast either way, because `LIMIT` stops the scan early. "Which bots
have nothing" - the question that greys the sidebar - must prove a negative
for every bot and has no such escape. Asked as one `DISTINCT` over every match
it is a full scan per keystroke; asked once per bot with `LIMIT 1`, each stops
at its first hit. That one change is the difference between a filter bar and a
search button.

**Written from a parse that was already happening.** The fetch path walked the
whole list to count it and threw the rows away; it indexes them on the way
past now. A second walk of a 719k-line file would not have been free.

**What it costs, said plainly.** Two things:

- **FTS5 tokenises where `find_matching_entries()` does substring**, so a
  mid-word fragment no longer matches - "andma" does not find "Sandman". Whole
  words and prefixes both do, and the last word typed gets a prefix wildcard so
  results appear before it is finished. Substring matching over four million
  rows IS the 1.4-second query above, so this is what buys the feature.
  `@find` over our own list is untouched.
- **The index is roughly the size of the lists again.** 4,000,000 rows
  measured at 452MB. An operator holding ten large lists should know that
  before it appears, rather than after.

**Best-effort throughout.** A missing, locked or corrupt index costs the
filter bar and nothing else: the lists are on disk, the browser still pages
them, `@find` still works, and the next fetch rebuilds it. Nothing in the
serving path reads the file, so it is never a reason to refuse to start or to
fail a fetch. "No index" is also not "no bot matches" - claiming every list is
empty would cross out the whole sidebar and read as a definite answer, and the
wrong one.

**The index is not the record of what is held.** `fetched_bot_lists` is, and
the two can drift - a list file removed by hand, a reset store. Every query is
scoped to the lists currently held, because a row for a list we no longer have
offers a file that cannot be requested.

The results reuse the browse view's own payload shape, so the folder
rendering, the checkboxes and "Download selected" all worked unchanged - each
row's `source` is its bot, which is what makes selecting across four lists at
once need no new plumbing. Two adjustments were needed: groups are keyed by
**bot and folder** rather than folder alone, since two bots can both have
`D:\MUSIC\Metallica\` and merging them would put one bot's files under
another's heading; and the folder's `!rar` button now takes its bot from its
own group rather than from whichever list the sidebar has selected.

Four mutations corrected tests rather than code, and three shared one cause:
`search()` swallows its own failures by design, so "it did not raise" passes
whether the query was built correctly or not. The syntax test now asserts the
ANSWER for a term like `AC-DC`; the empty-term test asserts that no query is
built at all; and the limit clamp is tested against more rows than the cap,
because with five rows in the table every limit returns five. The fourth was
this file's own recurring failure: a guard for `LIMIT 1` matched the docstring
that explains why `LIMIT 1` is there, and passed with the clause deleted.

**Clicking a bot toggles its results in or out** while a term is set, with
"Show all lists" and "Show none" beside the box - the rest of what #133 asks
of the filter. Done over the answer already held rather than by asking again:
the issue calls the toggling trivial precisely because the rows are in the
browser, and a round trip per click would be slower than the search that
fetched them. A bot switched off by the operator is marked differently from
one with nothing to show; one is a choice and reversible, the other is an
answer. A new term clears the choices, because it is a new question.

While a term is set the sidebar answers a different question, so the click
does a different thing - there is no "switch to this list" when the table is
showing all of them at once.

One behaviour was corrected on the way: a non-positive `limit` returned a
single row, where `webserver.parse_pagination_params()` already states the
house rule that non-positive means "omitted".

### 🟢 The List Browser picks a source from a list of dots, not a dropdown (#133)

The last of #133's slices bar the cross-list filter. The previous entry closed
with *"not in this change: the sidebar of bots with coloured dots that the
design mockup shows"* - this is it.

**Why a `<select>` could not do the job.** It holds a nick and a count and
nothing else, so the freshness verdict shipped last time had to go in the
option text. Worse, a dropdown lists things you can switch **to**, and one of
#133's three colours is *not downloaded* - a bot we have seen advertising but
hold nothing from. There is nowhere in a dropdown to put a row that is not a
destination.

So the rows now come from two places: the lists we hold, and
`runtime.known_bots`. A bot in both is one row, the held one - it carries a
real verdict and a count we parsed ourselves, where the advert row would
replace both with a claim. The two piles sort together, case-insensitively, on
the reasoning that an operator is looking for a nick and does not know in
advance which pile it is in.

**Four states, and grey is the default arm.** Current, their list changed, not
downloaded, cannot tell. A freshness the page does not recognise renders grey,
never green - green is the one colour that tells an operator to stop thinking
about a list, and a state we have never heard of has not earned it. A count
the bot never published renders as an em dash rather than 0, which is the
"did not say is not zero" rule the freshness comparison already follows.

**The colour is never the only carrier.** Every dot has a title, the legend
names all four in words, and the row for a list we do not hold is dimmed.
Roughly one man in twelve would otherwise be reading an undifferentiated
column of grey circles.

**Clicking a bot we hold nothing from does not switch to it** - there is no
list behind it, and the table would come back empty with nothing saying why.
It puts the nick in the fetch box, focuses it and says so.

The rows are built with `createElement`/`textContent`/`dataset` throughout. A
nick is whatever that bot called itself in a channel, `escapeHtml()` does not
encode a quote, and this file has been bitten by exactly that once already.

Two mutations worth recording. The remembered source is now checked against
`row.held` rather than mere presence: every advertising bot is in the rows now,
so "is it still in the list" stopped being the question a deleted list fails.
And the first version of the not-held guard sliced from the check down to the
source switch and looked for a `return` - a span that also contains the
"already showing this one" early return, so it passed against code with the
guard deleted. It reads the brace-matched arm now.

### 🗃️ One malformed line in the bot registry took the whole List Browser down

Found by the change above, in the way these usually are. The sidebar reads
`runtime.known_bots`, and the moment it started doing so, three tests in
`test_webserver.py` began failing in the full run and passing alone - one of
them with a 500.

The cause was a test, and the defect behind it was not. `test_runtime_state.py`
seeds every container `runtime.py` exposes with `{"probe": 1}` to prove they
survive a `!rehash`; the containers are shared, module-level and reload-proof
on purpose, so nothing took the probe back out, and it sat in the bot registry
for the rest of the run. That test cleans up after itself now, and says why.

What it exposed is real. `data/known_bots.json` is a plain JSON file an
operator can open, and `db.load_known_bots()` checked only that the **top
level** was a dict. Every reader treats an entry as a mapping - `irc.py`
copies it with `dict()`, the dashboard reads fields off it - so
`{"somebot": 5}` loaded cleanly and then raised in all of them. That file's
own docstring already promised the opposite: *"a corrupt or hand-edited file
costs an empty sidebar until then and nothing else"*.

Three places now hold that line rather than one, because the loader is not the
only way an entry gets in - the advert listener writes to the registry at
runtime. The loader drops malformed entries, the summary builder skips them,
and `_advert_now()` uses `isinstance` rather than `or {}`, which does not help
when the value is a `5` that `dict()` refuses. A bot whose own entry is
unreadable reads as "cannot tell", not as current: we read nothing, so we know
nothing.

**And the suite was reading this machine's own registry.** `oserve.start()`
loads it at boot, so every test that boots the daemon was pulling in whatever
bots this bot has actually met. The file is gitignored, so it exists on a
developer's machine and not on CI, and the suite behaved differently in the
two places - which is how a view that reads the registry came to fail on one
machine only, with a real nick nobody had put in a fixture sitting in the
assertion diff. `DCCoreTestCase` redirects `db.KNOWN_BOTS_FILE` to a throwaway
path now, exactly as it already did for `db.FETCH_HISTORY_FILE` and for the
same reason.

`known_bots` was also the one container `runtime.py` exposes that the harness
never reset between tests. Both gaps are derived rather than listed now: one
test asserts every container `runtime.py` exposes appears in the harness's
reset list, and another that no test is pointed at the real registry file - so
the next container added does not have to be found this way.

One more, found while running the suite twice at once to save time.
`test_dispatch_threads_are_daemons.py` scans every `.py` in the repository
root, and its own control test writes a `tmp*.py` into that very directory and
removes it again - so two runs against one checkout fail each other with a
`FileNotFoundError` naming a file neither of them ships, which reads as a real
defect and is not one. A name that is gone by the time we open it cannot be a
source file anyone ships, so the scan skips it.
### 🔍 What a multi-agent audit found in the same day's work

Thirteen agents over the four unmerged branches, then a refutation pass over
everything the first pass had not tested. Of 41 unique findings, 30 survived
an attempt to refute them. Seven of the ones in this branch are fixed here,
and one of them reframed what the audit called its most severe finding.

The four below were found after the first three, and they share a shape: the
film-and-series split introduced a SECOND generated list, and every place that
had one name to reason about now has two.

**`!update` reported "0 files, added 0" on every rebuild.**
`commands.count_from_master_list()` kept its own glob of the lists directory
rather than calling `list.find_latest_list()`, and every time that function
learned to exclude another name this one did not - #234 was that story with
the delivered `-FULL-` copy. The film list was it again, and worse:
`<base>-VIDEO-<date>.txt` sorts after a plain date ("V" > "2"), so this picked
the film list, whose header reads "List of N Films & Series" and never matched
the "List of N Files" pattern it was looking for.

Worse than a wrong number. The #230 shrink guard fires on `added_files < 0`,
and `0 - 0` is never negative - so the one warning that catches **a partial
mount failure publishing a truncated index** could not fire again.

**Which reframes the audit's own P1.** It reported that an unmounted music
share beside a local Films folder now publishes an empty index over a working
one, and called it a regression the zero-files guard should have caught. It is
not: `update_list.py`'s folder skip documents the opposite decision
deliberately - *"an unplugged drive or an unmounted share should cost its own
contents, not take the whole list - and the bot - off the air"* - and
`commands.py` names the real net for exactly that case, *"a partial mount
failure that still returns SOME files"*. `main` refused that scan only by
accident, because film was not indexed at all so the count reached zero. The
branch's behaviour matches the design; what actually broke was the warning
underneath it, and fixing the counter restores it.

The counter now calls `list.get_file_count_date_size_and_raw_bytes()`, which
counts request rows across every published list - so `!update` and the advert
cannot report different totals for one library by construction, rather than by
two copies of the filtering being kept in step by hand.

**A same-day rebuild kept a stale film list.** The keep set asked whether a
file existed at `video_path`, and that path carries today's date - so on a
second rebuild it was the earlier run's own output. A run that found no film
kept it: films that are gone, still searchable, still counted by the advert,
and absent from the archive users download. It self-corrects across a date
boundary, which is why the single-build test never saw it. One flag now
records whether THIS run published a film list, and all three sites ask it.

**`RAR_EXTENSIONS` was not a gate.** It decided whether a `!rar` row was
WRITTEN; it never decided whether one would be honoured. `dcc.py`'s whole gate
was `RAR_ENABLED`, containment, and "not an artist root", so a folder kept
deliberately out of the album list was packed happily by anyone who named it.

Harmless while nobody could name one - and this branch is what changed that.
The film list publishes folder headings inside the archive every user
downloads, and `list_heading_parts()` strips the prefix, so a heading pastes
straight back as a request. With no size cap anywhere, that is an unbounded
pack behind a line anybody in the channel can send. `defaults.py`,
`INSTALL.md`, the public changelog and a test all asserted this was the
defence while it was not implemented. It is now, on the request path, where
the claim is made - and the refusal says the files in that folder are still
requestable by name, because refusing a pack is not refusing the content.

**Ten tests failed on the counter fix, and were right to.** They pinned "the
counter reads the same FILE as everything else" from #234, which was exactly
right while there was one list. With two, the invariant is that the counter
and the advert give the same ANSWER - strictly stronger, and what #234 was
really protecting. Every case they encoded is preserved, because both sides
now go through `find_latest_list()`, which is where the exclusions live. Their
fixtures gained real request rows: a header with no rows is a file no
generator produces, and it made those tests turn on the counting mechanism
rather than on the behaviour.

**A library file starting with the bot's own name was looked for among the
lists.** `is_list_artifact()` matched "starts with LIST_BASE_NAME, ends in
.rar" - and its docstring already explained why the extension alone was not
enough, having been written for exactly this class. The prefix half is the
likelier of the two: LIST_BASE_NAME comes from the nickname, so it is an
ordinary word, and a shared "Muzik-Collection.rar" was routed to the lists
directory, found missing there, and refused for ever, while the file sat in
the library being advertised and counted. A real artifact carries the date the
builder writes into it, and that is what the test reads now - parsed with
`strptime` against the builder's own format string, so a change to one breaks
loudly rather than drifting from the other.

**A base name containing `-VIDEO-` hid the bot's entire list from itself.**
The markers are the builder's and they sit after the base name, but they were
matched against the whole path - so `Bot-VIDEO-Archive` excluded the master
list from its own search. @find answered "No MasterList found" and the advert
published 0 files, permanently, with the list sitting right there. `-RAR-` and
`-FULL-` did the same, and a LOCAL_LIST_DIR with any of the three somewhere in
its path did it to every list underneath. The name is now read from after the
base, which is the only part the builder owns.

**A peer's film list could be taken for their master.** This bot's own archive
now carries two `.txt` files, so a bot running DCCore is the ORDINARY case for
`list_fetch._pick_list_file()`, not an exotic one - and its tiebreak picks the
largest. From a bot whose films outweigh its music we would have indexed the
films, shown them as that bot's whole catalogue, and reported its music as
absent. `-VIDEO-` is excluded alongside `-RAR-` now, which drops those films
from the fetched copy rather than merging them in; reading both into one
fetched list changes what that function returns and the ceiling that guards
it, so it is recorded on the roadmap rather than smuggled in here.

**A killed run left staging files behind for ever.** A run that FAILS discards
its own `.new` temporaries; a run killed mid-scan - minutes, on a large
library - cannot, and they carry the date they were staged on, so the next
day's run stages different names and never looks at them again. Nothing ever
served or counted them, which is precisely why nothing removed them either.
Swept at the start of the next build, where this run has staged nothing yet
and the only file the sweep can reach belongs to a run that is no longer
alive.

One test of mine was wrong and the suite said so: it asserted an old
`DCCore-<date>.txt` survived the temp sweep, when the prune correctly removes
a superseded list at the end of every build.

### 🎬 The list held two file types out of every library on earth

`update_list.py`'s walk asked `file.lower().endswith(('.mp3', '.flac'))`, and
that was the only gate on what goes into the list. A video library walked past
every file it owned and published nothing.

The failure was silent from the operator's side, which is the part worth
recording: the scan reported success, the list was built, the advert went out.
It was simply empty. That reads as "the bot cannot see my files" with nothing
anywhere saying why - no warning, no count, no mention of extensions.

**Every file is listed now, and `LIST_IGNORED_EXTENSIONS` names the
exceptions.**

The first attempt at this widened the hardcoded pair into a
`LIST_EXTENSIONS` include-list, defaulting to audio plus the common video
formats. That was the wrong shape and it is worth writing down why, because it
looked right: **the set of things people serve is open-ended.** Every format
left out of an include-list is invisible in exactly the same way `.mkv` was,
so an include-list does not fix the defect - it moves it to whichever format
nobody thought of. `.m4b`, `.ape`, `.chd`, a scanned booklet, a text file of
liner notes.

An exclude-list is a short, closed list, and its failure mode is the mild one:
a file listed that need not have been, rather than a library that does not
appear. OmenServe has the same shape (`Exclude = .mpu,.db`), so it is also the
form operators coming from it already know.

**The default is only what is never a served file** - `.db`, `.ini`, `.lnk`,
`.url` for the Windows and shell droppings, and `.tmp`, `.part`,
`.crdownload`, `.!ut` for downloads still in flight. That last group is the
one case where listing a file is actively wrong: the bytes are not all there,
so the transfer can only ever hand over something broken.

Covers, `.nfo`, `.cue` and playlists are **not** skipped. People do serve
them, and an operator who would rather not can say so. `tests/
test_master_list_generation.py` had a test called
`test_other_files_are_ignored` asserting the opposite, with the docstring "a
music library is full of covers, cue sheets and notes" - it now asserts they
are listed, and says that the reversal is the point.

**Every spelling of the setting means the same thing.** Dots optional, spaces
around the commas optional, case irrelevant: `db,ini,tmp`, `db, ini, tmp` and
` .DB , ini ,  .TMP ` all resolve to the same three. Normalised where the
value is READ rather than where it is written, because it arrives from three
directions - `settings.conf`, `admin_config.py` and the dashboard's Settings
page - and only one of those goes anywhere near a validator.

The added dot is not cosmetic, and a mutation had to prove it.
`"Thumbs.db".endswith("db")` is already true, so the first test of the
normalisation passed with the dot dropped. What the dot prevents is an
extension matching the END OF A NAME - and under an exclude-list that is worse
than it was under an include-list: ignoring `ts` without the dot makes every
file called `credits` or `highlights` vanish from the library with nothing
said.

**An empty setting skips nothing**, and needs no fallback. That is the other
advantage of the inversion: an empty include-list scanned a full library to
zero files, the zero-files guard then refused to publish, and the bot sat on a
stale list reporting an empty library it did not have - so it needed a guess
to recover from. An empty exclude-list just lists the library.

**Resolved once per scan, not once per file.** The first version asked the
predicate inside the walk, and that function reads the setting, normalises it
and builds a tuple - so the largest library this project has measured, at 719k
files, would have done that 719,000 times. The scan resolves it once and
passes it down, and prints what it is skipping (`Indexing every file, except
8 type(s): .db, .ini, ...`). The reported symptom was "my files are not in the
list" with nothing saying why; that line is the answer to it, which is why it
has a test rather than being decoration.

`scripts/setup_check.py` had its own copy of the hardcoded pair for its "how
many files can I see" line. It calls `update_list.is_listed_file()` now: a
count that disagreed with what the build indexes would report a healthy
library and then publish a list that does not match it.

Two of this repository's own completeness guards caught things on the way,
which is what they are for. The setting is a list literal in config.py, so
both the "config defines no containers of its own" scan and the "every runtime
container is preserved or explicitly excluded" scan flagged it. It is
allow-listed in each with the reason `ADMIN_HOSTMASKS` already carries: it is
a **setting**, not runtime state, and re-reading it on a rehash is the point.

A mutation then showed those two lists could disagree without anything
noticing - the second guard is satisfied by a name being preserved OR
excluded, so a name in *both* passed while the code did the opposite of what
its written reason said. Adding the setting to `PRESERVE_RUNTIME` broke
nothing, and would have meant an operator's edit doing nothing until a full
restart. There is a guard for that now.

**Two consequences worth stating.** The `!rar` album list is built from folders
holding listed files, so film folders now appear in it, and there is no size
cap anywhere on packing a folder for `!rar`. That was always true - it simply
never mattered when the largest thing anyone could ask for was a lossless
album. A request for a 40GB folder will be attempted. `RAR_ENABLED` is the
existing off switch; a size cap is noted in the roadmap rather than invented
here, because the right number is the operator's.

And **"every file" means exactly that**: anything sitting under
`FILE_DIRECTORY` is now offered to anyone who asks - a stray backup, a
document, a private note dropped into the tree by accident. That was already
the rule for `.mp3` and `.flac`; it is now the rule for everything. The
setting's own comment says so, and so does INSTALL.md.

**Left for the operator to check:** the list is read by scripts as well as
people. Every row is `!<nick> <filename>  ::INFO:: <size>`, and this project's
own parser splits on the `::INFO::` marker regardless of extension, as do the
OmenServe bots the convention came from. The note at the row write reports
that AutoQ.mrc strips that tail only for `.mp3` and `.flac`, which if true
means any other row may reach it with the size still attached. No regression
is possible either way - there were no such rows at all before this - but an
operator serving to AutoQ users should confirm it.

### 🎬 Film and series get their own list, and only albums are packable

Both from the review of the change above.

**`!rar` eligibility had no format gate at all.** A folder earned a `!rar`
row from holding any file the scan indexed, with `RAR_ENABLED` the only
switch. That read as "album folders" while the scan took `.mp3` and `.flac`
and nothing else. The moment it took everything, every folder in the library
became packable - a season of a series, a folder holding one text note - and
there is no size cap anywhere on packing, so an unbounded amount of CPU, disk
and one transfer slot sat behind a line anybody in the channel can paste.

`RAR_EXTENSIONS` decides it now, as its own set rather than "whatever the scan
indexed". An album is a genuine multi-file collection - tracks, a cover, a cue
sheet - which is what makes packing it useful; a film is one large file that
can simply be requested by name, and still can.

**Film and series publish as their own list**, `<base>-VIDEO-<date>.txt`,
built from the same walk. No new trigger and nothing for anyone to learn:
`@<botnick>` already hands out one archive containing the master list and the
`!rar` album list, so this is a third member of the same download. The pattern
is not new here either - the album list has been a separate file from the same
scan since long before this.

`SEPARATE_VIDEO_LIST` turns it off, and off is a real answer rather than a
fallback. There are two ways to end up with a music list and a film list: this
switch, when the two are mixed in the same folders and only the file says
which is which; or several lists over their own folder sets, which is the
roadmap's multi-list feature and the better route for a library already sorted
that way on disk. The video list is only published when there is video to put
in it, so a music-only library never gains an empty file.

**The regression this could so easily have been.** A file request
(`dcc.py:1316`), an `@find` (`list.py:328`) and the advert's count
(`list.py:119`) all read `find_latest_list()` alone. Moving film into a second
file without touching those would have left every video in the library listed,
advertised and impossible to get - a feature that reads as working right up
until somebody asks for something. `all_list_paths()` is the seam; all three
go through it, and `dcc.py` concatenates the lists rather than searching them
one at a time, so the folder-heading state machine below it is untouched.

`find_latest_list()` needed a guard of its own: it globs `<base>-*.txt` and
takes the LAST, and `-VIDEO-` sorts after a bare date, so the film list would
have quietly become "the" master list. That is the failure its own comment
already records for `-RAR-`. The test fixture had the identical blind spot -
its `list_path()` helper globbed the same way - which is how the hazard was
confirmed rather than merely reasoned about.

Two more, found by the same walk:

- **The music list's header claimed the whole library's size**, films
  included: a number no reader can reconcile with the file in front of them.
  The side files and the advert still report the library total, which is what
  they are for.
- **An all-film library refused to REBUILD.** The zero-files guard counts the
  music list, so such a library looks empty to it. On a first run it finds no
  previous index and publishes anyway, so nothing looks wrong; it is the
  second run that fails with "scan found 0 files but an index already exists
  (mount unavailable?)" - one working list, then every `!update` refused for
  ever, blaming a mount that is fine.

Three mutations corrected tests here rather than code. `"1.00KB"` is a
substring of `"41.00KB"`, so the header assertion passed against exactly the
combined figure it existed to reject. The all-film test ran the scan once and
so never reached the guard. And the `!rar` gate turned out to be untested by
the film case at all: with the split on, a film is not even in the data the
`!rar` rows are built from, so the split alone keeps it out and deleting the
gate broke nothing. The gate is now tested where it does the work - with
`SEPARATE_VIDEO_LIST` off, which is the configuration an operator who keeps
film in its own folders will run.

### 📋 The roadmap said a closed defect class was still open

Release-checklist work, done early because that checklist says it is: *"check
`docs/FUTURE.md` for anything the release implements that is still listed as
planned or not-yet-done. A roadmap calling a shipped thing missing makes the
changelog look like it is overclaiming - this drifted once and reached the
public repo before it was caught."*

`!rehash` rebinding module-level locks was listed under "from the audits, not
yet done". It is neither: every lock in a module `!rehash` reloads is
allocated in `runtime.py` and bound by name, and
`tests/test_no_reloaded_module_owns_a_lock.py` fails if a new one appears -
so the class is closed rather than the four original instances merely fixed.
Moved to Quality, where the other structural guards are recorded.

Two more found by asking the same question of the rest of the file, which the
first pass did not: it read the bullet list and not the prose around it.

- The coverage section still said **"21 daemon functions have no behavioural
  coverage at all"**. `scripts/function_coverage.py` reports 2 of 257, both
  allowlisted with a written reason, and it fails the build on a third. What
  genuinely remains there is narrower and does not show up in that number -
  source-reading guards that do not execute what they check, which this file
  has three recorded instances of passing against deliberately broken code.
- The multi-list section called the multi-folder **Settings page** the thing
  still to do. It shipped: `GET`/`POST /api/folders`, `GET
  /api/folders/browse`, and a reorderable editor with a folder picker.
  `data/library_folders.json` has not needed hand-editing for some time.

Checked rather than assumed, and the neighbouring entry survived the same
check: **timed bans really do still grow without bound.** `banned_users` is
pruned only when that same nick is looked at again, and the flood sweep covers
`user_requests` and `muted_until` but not it - so a timed ban on a nick that
never comes back stays for ever, which is the normal case, because the ban is
what made them leave.

An operator upgrade note also landed in `docs/INSTALL.md`, for the same
reason: this change alters what the list CONTAINS, and that is exactly the
class of thing the checklist asks whether an existing operator has to act on.
They do - the list does not change until they run `!update`, everything under
`FILE_DIRECTORY` is offered once it does, and folders that used to be
`!rar`-packable may no longer be.

### 🔧 The mutation runner scored some mutations against code that was never on disk

Two mutations of the same byte length, applied to one file inside the same
mtime granularity, let Python reuse the FIRST one's cached bytecode for the
second run. Two of this batch reported "not caught" against tests that catch
them perfectly well when run by hand.

A false pass is as available that way as a false failure, so every mutation
batch in flight - this change's and the List Browser work's alike - was re-run
with the caches cleared and `-B` before either was believed. The runners clear
`__pycache__` and pass `-B` as a matter of course now, rather than relying on
two same-length edits never landing in the same second.
### 📦 Bringing an OmenServe operator's history across (#69)

The single biggest barrier to trying this bot is not features - it is
abandoning years of totals. `omenserve_import.py` could already read those
numbers; this is the half that puts them somewhere.

**Three routes in, one parser.** Choose `vars.ini`, paste its contents, or -
for an install nobody's parser understands - the figures are shown so they can
be checked by eye. All of it goes through `read_install()`, so the fallbacks
are not a second code path that can behave differently.

**The page filters before it uploads, and that is not tidiness.** The live
install #69 was written from held **280 variables** - nicks, channels, paths,
add-on settings and passwords among them. Only the counter lines are sent, and
the page asks the server which variables those are rather than hard-coding
them: a field added to `FIELDS` is then kept without the JavaScript changing,
where a hard-coded list would have silently stripped it out and told the
operator their file has nothing in it.

**Overwritten, not combined - said only when it matters.** On a fresh install
nobody reads that sentence and on a used one it is the only thing that does,
so the preview names which figures already have a value and the warning
appears exactly then.

**Nothing is taken on trust.** A `vars.ini` is a plain text file people
hand-edit, and the operator confirming a number is not the same as the number
being sane. Refused at the endpoint and not only in the page: negatives,
non-numbers, and magnitudes past anything a real link could have produced - a
stray digit looks exactly like that, and importing one silently is worse than
refusing it, because the operator can retype a number but cannot tell that a
total is wrong once it looks plausible. `True` is refused explicitly, since
`int(True)` is 1 and a JSON `true` would otherwise import as a file count of
one.

**An absent figure is left alone, never zeroed - and so is a zero.** That rule runs from the
parser to the write: the counters come from ADD-ONS rather than from OmenServe
itself, so an operator running one and not another imports what they have and
keeps the rest. Writing a zero over a real total would be the worst thing this
feature could do.

**The day columns are not touched.** They belong to the daemon's own rotation,
and importing a lifetime total must not reset what the bot did today. They
could not have been imported anyway: `%OSL.Today` is `Friday` and
`%mx.rarday` is `Wednesday` - a weekday NAME, so "today or six days ago" is
not answerable from the file at all.

The write goes through `db.save_advanced_stats()` and `db.save_speed_record()`
- a caller, not a second implementation of either - and the response carries
the before and after, so the page reports what actually happened rather than
what it asked for.

Two mutations corrected tests. One targeted the "reject the whole import"
guard at a case where every field was invalid, so the "nothing to import"
check answered it and the guard could be deleted; it belongs on the
half-valid case. And the fixture wrote an empty date in the stats row's
seventh column, which makes it six columns, which the loader correctly treats
as corrupt - so every "an existing figure is preserved" test failed against
code that was preserving it perfectly well.

### 🔍 Four audit findings in the import, three of them in reading one number

From the multi-agent audit of the same day's work. Every one of them is in the
few lines that turn text somebody hand-edited into an integer, which is worth
noting on its own: that is the whole attack surface of this feature and it is
about twenty lines long.

**A present zero overwrote a real lifetime total.** The rule this feature was
built on - never write a zero over somebody's history - was enforced on an
ABSENT variable and not on a zero, because the check read `number is not
None`. A fresh OmenServe install writes `%mx.rartsent 0`, and importing from
one replaced a real DCCore total with nothing, while the note beside it
promised "the total size will stay as it is". A zero carries no history
across, which is the entire point of the feature, so it can only destroy. It
is now read, shown in the preview marked as not imported, and explained -
silently dropping the row would have read as a parse failure to an operator
looking at that number in their own file.

**`1e999` reached the route as a 500.** It parses as a float perfectly well
and becomes `inf`, and `int(round(inf))` raises `OverflowError` - not
`ValueError`, which is what the conversion caught. NaN arrives the same way.
Both mean "not a number I can use", which is what `None` already says here.

**A thousands separator truncated the number by three orders of magnitude.**
`split(",")[0]` is what `%SDmaxspeed`'s `<bytes>,<nick>` shape needs, and the
`.replace(",", "")` written after it could therefore never see a comma - dead
code that looked like the handling for the case it was not handling. So a
value written `45,902` imported as `45`: silently, plausibly, and wrongly. The
two shapes are told apart by what follows the comma - a digit is a separator,
a nick is a field boundary.

**A half-written import reported success.** Both writers swallow their own
errors and return `None`, which is right for them - a failed stats write must
not take the daemon down - and meant this route answered 200 with
`imported: [...]` for figures that never reached the disk. The operator would
have been told their history came across and found half of it, with nothing to
say which half. The response is now built from what is actually on disk
afterwards, compared against what was asked for, and names the ones that did
not land.

One of the four fixes was written twice. The first version guarded `inf` and
NaN explicitly and then caught `OverflowError` underneath, and the mutation run
showed the guard was dead: deleting it changed nothing, because the catch
below already answered both. The catch stayed and the guard went.
### 📥 The list request answers AutoQ's own menu item

Read out of `AutoQ.mrc`, the queue script these channels actually use. Its
"Get Listfile" item sends:

```
msg $chan $+(@,$snick($chan,%i))  ::^C4,0Auto^C12,0^BQ^B^C1,0::
```

`@<nick>` with two spaces and a colour-coded tag. OmenServe answers that;
`irc.py` compared for equality, so DCCore was **silent** for every user of
that item - no reply, no error, and nothing in the log to say a request had
been made and ignored. The failure looks exactly like a bot that is down.

`is_list_request()` now takes `@<nick>` exactly, or `@<nick>` followed by a
space and anything at all. **The space is what makes a suffix safe**, and it
is the whole argument: none of the names this has to stay distinct from
contains one. `@<nick>-que`, `-remove`, `-help`, `-stats` and `-top` are this
bot's own commands, each still matched exactly by its own branch below.
`@<nick>2` and `@<nick>_away` are a DIFFERENT bot whose nick merely starts
with ours, and answering those would send our list to somebody asking
somebody else for theirs.

`@find` and `@locator` are excluded explicitly, for the bot whose nickname IS
"find": a search there must keep meaning a search. The cost is stated rather
than hidden - for that one bot, AutoQ's menu item searches for "::AutoQ::"
instead of sending the list.

**The gate and the dispatch are one function.** They were two copies of the
same comparison, and the flood gate's own test class exists because a gate
narrower than the dispatch is an unmetered command path. Widening one and not
the other would have re-created exactly that, so both call
`is_list_request()`, and `tests/test_irc_dispatch.py` binds the real function
into the namespace it evaluates the lifted gate expression in - the way it
already binds `bot_aliases`.

**One test was rewritten rather than deleted.** It asserted the request "stays
an exact match", and gave its reason: `@DCCore-que` must not be swallowed.
That reason still holds and is now met by the space; the exactness itself was
the defect. Every case it pinned is still there, with `@DCCore please` moved
to the other side and AutoQ's real payload added.

**And two source-reading guards were sliced wrong.** `test_help_command` and
`test_stats_top_commands` read the flood gate with
`block[:block.index(")
")]`, which stopped at the first closing paren in the
text - fine until the gate called a function, at which point the block ended
after one line and both failed while the metering they check was perfectly
intact. They walk to the gate's own closing paren now.

**Not changed: "Que Status".** AutoQ sends `<nick>-que` with no leading `@`,
which DCCore does not answer. Left alone deliberately - a bare word as a
channel trigger is a wider change than a suffix on a request already
addressed to us. AutoQ's own non-buffer branch for it is broken anyway: it
sends a literal `+(Nick,-que)`, missing the `$`. Written up in INSTALL.md so
an operator knows to type `@<nick>-que` instead.

**Also documented: rows AutoQ drops.** Its file branch truncates a line at the
end of the first accepted extension it finds - which is how `::INFO::` and the
size get stripped - but that `aline` sits INSIDE the extension test. A row
whose type is not in mIRC's `[text] accept` list is queued nowhere and
reported nowhere. AutoQ adds only `*.mp3` and `*.rar` on load, so now that
DCCore lists every file type, a `.flac` or `.mkv` row silently vanishes for
any user who has not added it. Nothing to fix here - the row is correct and
the client discards it - but operators need to know, so INSTALL.md says which
mIRC setting to change.
### 🔒 A timed ban on somebody who never came back stayed for ever

The last of the audit's flood-tracking findings, and the one the roadmap has
been carrying as outstanding.

`banned_users` was expired **lazily**: the check that reads it deletes an
expired row, but only on the next request FROM THAT SAME NICK. A banned nick
not coming back is the normal case - the ban is what made them leave - so the
entry stayed in memory and in `bans.txt` for good, and the file grew one
permanent row per flooder.

The flood sweep already ran every sixty seconds and already cleaned
`user_requests` and `muted_until`. This is the third structure it was always
meant to include; it is the same expiry rule applied to the nicks that never
return, not a new policy.

Three things it has to get right, each with a test:

- **The same reading of an expiry as the per-request check**, from one
  function. Two copies of that coercion could drift into disagreeing about
  when a ban ends, which means either a ban enforced but never cleared or one
  cleared while still being enforced. A row that will not parse as a number is
  swept, exactly as the lazy path would have deleted it - a row nothing can
  read is not a ban anyone is serving.
- **The file is rewritten.** Unlike the other two structures this one is
  persisted, so clearing memory alone would let every swept ban return at the
  next restart and the file would go on growing regardless.
- **Only when something actually expired.** A sweep runs every minute for the
  life of the daemon; writing the ban file each time would be a disk write a
  minute, for ever, to record no change at all.

`docs/FUTURE.md`'s "from the audits, not yet done" section is now down to the
one line about the remaining verified findings.

### 🟡 The List Browser says when a bot's list has moved on (#133)
The third of #133's remaining slices. A list you hold can be months out of date - from the channel capture the issue records: `Deepdiver` Apr 29th, `Hiroshima` Feb 20th, `FlacMe` Jan 2nd - and nothing said so.

**Their advert then against their advert now.** Not their advert against our own parsed row count, which #133 settled explicitly: bots count differently, some including header lines and some counting album rows apart, so an off-by-a-few would leave a list permanently marked stale with nothing actually wrong. A bot compared against its own earlier claim has no such problem. `list_fetch` records what that bot was advertising at the moment we took the copy; `webserver` compares it with what they advertise now.

**Date first, count second.** A count can coincidentally match after an edit; a date cannot - and 31 of the 32 bots in that capture publish one.

**"Unknown" is a real answer, and renders as nothing at all.** A missing field means "this bot did not say", never zero - the rule `irc.parse_channel_advert()` already follows. We can fetch from a bot whose advert has not come round yet, and a bot that publishes no date should show no freshness claim rather than an invented one. Every list already on disk was fetched without a snapshot, so all of them read "unknown" until re-fetched, which is the honest state rather than a wrong one.

The banner names the numbers, because a verdict alone does not tell an operator whether to act: *"they advertised 7,902 files built 10 Aug, and now advertise 8,110 built 28 Aug"*.

**Not in this change:** the sidebar of bots with coloured dots that the design mockup shows. The source picker is a `<select>`, which cannot carry one, so the marker is in the option text for now and the layout is its own change.

Two things a mutation run corrected here. An early `if not then or not now: return "unknown"` was redundant - the field loop already reaches that answer, and deleting the guard changed nothing any test could see, so it went rather than being propped up. And every test built `fetched_bot_lists` directly, so none of them could see whether the fetch path stored the snapshot at all: replacing that call with `{}` passed all of them until a guard for the wiring was added.

### 🗃️ The dashboard's nav says what each view is for, and a run of files can be selected at once (#133)
Two of #133's three remaining slices; the freshness indicators follow separately, because they need something stored that is not stored yet.

**Queue is folded into Stats.** #133's own words: *"everything the bot knows about itself, with the queue table keeping its place in the lower half of the view"*. The table and its three counters moved as they were - `renderQueueStats()` and `renderQueueTable()` still find the same ids - and the poll that refreshes them followed, keeping the rule it already had: refresh the visible table only when it is the one showing, so a background poll never clobbers what the operator is reading.

**Renamed and reordered.** `Download` is `Downloads`, `File Lists` is `List Browser`, and the order is `Search · Downloads · List Browser · Tools · Stats · Settings · Console` - by how often a view is used rather than by how central its information feels. Settings and Console sit after Stats because they are admin surfaces reached occasionally; #133 does not place them, and the same principle does.

That change touched three lists at once - `views` in app.js, the nav buttons, and the view sections - and a half-applied rename would have been invisible until somebody clicked. `tests/test_web_assets.py` now asserts the three name the same set, which is the #267 class stated generally: `activateView()` looks up `view-<key>` for every key in `views`, so a missing section is a null dereference on EVERY view switch rather than just that one.

**Shift-click extends a selection** in the List Browser. Ctrl needed no code - a checkbox already toggles exactly one box - and there is a test saying so, in case somebody later adds a `ctrlKey` branch that fights the browser.

Shift is handled on **click** rather than change, for two reasons worth recording: the range has to be computed against the state BEFORE the browser toggles the clicked box, and shift-clicking across rows also selects the text between them, which reads as the page having broken. The anchor is the last box the operator actually touched, and it is dropped whenever the table is rebuilt - it is a live DOM node, and a stale one would leave `indexOf()` never finding it, so the next shift-click would silently do nothing.

The button carries a `title` explaining it, because an interaction nobody can see is one nobody uses.

### 🔌 A handful of CTCPs could take every DCC port for a minute
The last finding of the full-program audit.

`handle_dcc_chat()`'s passive branch spawns `_listen_and_serve()` immediately, and **every other limit on that path runs after `accept()`**: `is_bad_ip()` is consulted on the connecting address, which does not exist until somebody connects; the single-`_pending` rule inside `_serve()` applies to a session that already exists; and `irc.py` deliberately leaves DCC CHAT out of the set `security.is_flooding()` meters, so the CTCPs that start them are not rate-limited either.

Nothing bounded how many listeners could be OPEN at once. So a handful of passive DCC CHAT offers took every port in `DCC_PORT_START..DCC_PORT_END` and held them for `LISTEN_TIMEOUT` - **the same range DCC SEND needs**. The bot stops being able to send files at all, and recovers only when the listeners time out.

One listener at a time now, which is the same shape as the `_pending` rule one step further on - "at most one connected-but-unauthenticated session" - applied to the step before it. A refused offer costs the sender nothing but another CTCP once the current one resolves, and a real operator makes one at a time.

The flag is cleared in `reset_state_for_tests()` alongside `_session` and `_pending`, because it is module state exactly like them: a test whose listener thread outlived it refused every passive offer in every test that ran afterwards, which is how the omission was found - the suite went from green to four failures that all passed in isolation.
### 🔄 A rebuild that failed partway published half of itself, and said it had not
`generate_master_list()` published the master index, the album index and the download artifact as three independent `os.replace` calls, then wrote the two size side files and the base-name marker, then pruned. **No rollback anywhere.**

The ordinary way to reach it, on Windows: `os.replace` onto a file another handle has open raises `PermissionError`, and `dcc.py` holds the published artifact open for the whole duration of a DCC send. `PAUSE_ON_UPDATE` only refuses NEW requests, so a transfer already in flight keeps that handle - and somebody downloading the list when the scheduled rebuild lands is not an edge case on a bot with several slots.

What that produced: the master index replaced, so `@find`, the advert count and `commands.count_from_master_list()` all reported the **new** scan - while the archive users actually received was the **previous** one, the size side files still carried the previous numbers, the base-name marker was not updated, and `_prune_superseded_lists` never ran. Nothing recovered it; the artifact stayed stale until some later rebuild happened to run with no transfer in progress. And the failure branch printed

```
[LIST-GEN] The previous list was left untouched and is still in use.
```

which by then was false.

`_publish_artifacts()` moves each destination **aside** before its replacement lands, so a failure can put back exactly what was there. That also makes the locked case fail at the safest possible moment: renaming a file another process holds open fails on Windows too, so the lock is discovered while moving the old file out of the way - **before anything observable has changed**. The download artifact goes first, because it is the one a DCC send holds open.

A mutation run also corrected a comment here: the rollback walks its list in reverse because that is the convention for undoing a sequence, not because it is required. Each swap touches only its own destination and that destination's `.previous`, so no two of them can collide - flipping the order broke nothing, and the comment that claimed otherwise now says so.

### 🧹 The last four dashboard findings of the audit
**A fetched file with a long remote-chosen name could not be downloaded.** `dcc_fetch` fits a stored name to `MAX_NAME_BYTES` (255) and touches every path through `platform_compat.long_path()`. The dashboard's download route was the one that did not, so on Windows the file was written, marked "complete", listed in the UI - and its Download button answered **404 for ever**, while the file sat there the whole time.

Handing `send_from_directory()` the *wrapped* directory does not fix it either, which is worth recording because it looks like it should: werkzeug's `safe_join()` joins with a **forward slash**, and a `\?\` path is the one kind Windows will not accept those in. The path is built here now, with a backslash, and wrapped after - and the containment `safe_join()` was providing is kept explicitly with `dcc.is_safe_path()`, which additionally resolves symlinks.

**A served-folder path had no length cap.** `apply_folder_changes()` bounded the NUMBER of folders and not the length of any one path, and `library.problems()` embeds each offending path verbatim in up to two messages per entry - so the 400 response was roughly twice the request that caused it. Every other web input in the module was already bounded.

**"Up" from a lowercase drive root left the machine root.** `browse_roots()` builds its entries from the uppercase letters A-Z and `os.path.abspath()` preserves whatever case the caller sent, so `c:\` missed the root list and the parent fell through to `ntpath.dirname("c:")` - which is `"c:"`, a *drive-relative* path meaning "the current directory on C:". Clicking Up from a lowercase drive root browsed the daemon's own working directory.

**A dead "Get folder as .rar" button.** Rendered for every group of a foreign bot's list including the unnamed one, while `requestFolderRar()` drops the click on `if (!bot || !folder)` - so it was there, clickable, and did nothing at all: no request, no message.
### 🧮 Four ways a configuration change did not mean what it said
All found by the full-program audit.

**An indented line was swallowed into the setting above it.** configparser treats an indented line as a CONTINUATION, so

```
NICKNAME = MyBot
    a stray indented line
```

gave `NICKNAME` the value `"MyBot
a stray indented line"` - newline and all - which then went out on the wire, with nothing reporting it. `settings.conf` is one setting per line (`save()` refuses to write a value containing a line break for exactly that reason), so an indented line is always a mistake. `parse()` names the line and refuses now. Indented COMMENTS are still fine - a comment is a comment wherever it sits.

**`ADMIN_CHAT_MODE` was a fixed-choice setting that `CHOICES` did not cover.** adminchat tests for `"listen"` and `"connect"` and treats everything else as `"auto"`, so a typo did not fail - it silently selected the default, and the operator who wrote `lisen` got automatic mode with no indication their setting had not taken. There is also a test that reads adminchat's own branches, so the two lists cannot drift.

**Renaming a folder label split every counter and grew the key.** The counter migration decided a key was "already labelled" by testing its first component against the CURRENT labels, so renaming `Flac` to `Lossless` made every existing key stop matching and get prefixed again - `Flac/Artist/Song.flac` became `Lossless/Flac/Artist/Song.flac`, one component longer each time, with the history split from what new downloads write.

**An absolute key re-migrated on every boot, for ever.** A file counted while it was under no configured folder keeps its absolute path. Its first component is not a label, so it was "migrated" every time - and `os.path.join(label, absolute)` returns the absolute path unchanged, so the file was rewritten and a migration logged on every single start while nothing actually changed.

The last two share a root: **"has this already run?" was being inferred from the shape of the data.** It is recorded now, once, in a marker beside the counters - so a restored backup of the data directory carries its own answer with it. Absolute keys are skipped outright, and a marker that cannot be written is reported rather than fatal, because this runs at startup and refusing to boot over it would be the thing the wrapper exists to prevent.

### 🔒 A host-shaped hard ban was confirmed, listed, and never enforced
`check_user_status()` decided a pattern was "hostmask-shaped" only if it contained `!` or `@`. Anything else was matched against the bare NICK - and an IRC nick can never contain a dot, so

```
!ban *.dialup.example.com
```

could not match anything, ever. But `!ban` accepted it, reported success, and `db.load_hard_bans()` listed it among the active bans. The admin believed a host was banned; the host walked straight in. The same shape as #225, where the confirmation and the enforcement disagreed silently.

**Three shapes now, matched against three different things.** A pattern containing `!` or `@` is a full hostmask and matches the mask; one containing `.` or `:` is a host or IP and matches **the host portion**; anything else is a nick and matches the nick. RFC 2812 allows a nick letters, digits and the specials `[]\`_^{|}` - so what a pattern contains says what it is about.

Matching the host and not the whole mask is the difference between working and appearing to: a full mask is `nick!ident@host`, so `192.168.1.*` anchored over the whole of it cannot match anything. The first attempt at this fix matched the full mask and passed the `*.dialup.example.com` case purely because that pattern's leading star swallowed the `nick!ident@` part - the IP test is what caught it.

### 🗄️ A bot nick containing "|" broke every cross-bot fetch from that bot
`_sanitize_bot_dir_name()` claimed in its own docstring to apply "the same discipline" as `dcc_fetch._sanitize_offer_filename()`, which is a **whitelist**. It was a blacklist: NUL, the two separators, `..` and surrounding dots.

`|` is an ordinary IRC nick character - RFC 2812's specials are `[]\`_^{|}`, and `Bot|Away` is one of the commonest nick shapes on the network - and is illegal in a Windows path. So `os.makedirs()` on the extraction directory failed with `WinError 123` **after the zip had already come over DCC**. The transfer worked, the bytes were on disk, and the fetch failed at the last step, every time, for that bot. `*`, `"`, `<`, `>`, `?` and `:` do the same.

A whitelist now, as the docstring always claimed. Legal nick specials that are also legal in a path (`[]{}^\`_-.`) are kept, so the directory is still recognisably that bot's.
### 📐 The master index has a grammar, and three things wrote into it without one
All three found by the full-program audit.

**The prune step ate the side files it had just written.** `_prune_superseded_lists()` matched artifacts with a bare `item.startswith(config.LIST_BASE_NAME)`. Every generated list is `f"{LIST_BASE_NAME}-{today}.txt"`, so the separator is always there - but the size side files live in the same directory, and with `LIST_BASE_NAME` derived from a nickname like `dccore` or `dcc` they matched too. Every rebuild wrote them and then deleted them, so **the library's total size disappeared from every public surface permanently**: the advert published `Files (0B)` and the CTCP SLOTS payload published 0 raw bytes, on every interval, for ever. The log line was `[LIST-CLEAN] Removed 2 superseded list(s)`, which reads like housekeeping working correctly.

The match requires the separator now. The side files are also excluded by name, which is not redundant: those names are *settings*, so an operator can set one to something the separator rule would match.

**A banner line of only `=` shifted every folder heading by one.** The operator's banner is written into the body of the index, and a line that is entirely `=` is a folder RULE to every reader. An odd number of them leaves `find_matching_entries()`'s rule/heading/rule machine mid-block, so the next banner line is taken as a folder heading and the REAL heading after it is swallowed. Measured: a file in `D:\MUSIC\Flac\Artist\` was reported as living in `MY BOT - EST 2009`. Not cosmetic - the dashboard's duplicate-finder resolves that value, and the File Lists view groups by it. Rewritten to the same width in `-`, so the box still looks like a box.

**A banner line beginning with `!` was served as a file.** A request row is identified everywhere in the project as "the line starts with `!`", so `!!! NEW RELEASES WEEKLY !!!` was counted in the advert's file count and in the CTCP SLOTS payload, and `@find` returned it as a genuine match - a user could request it and receive nothing. Dropped rather than rewritten: there is no edit that keeps such a line looking like itself while stopping it being read as a row. Both are reported with the offending line so the operator can fix the banner rather than wonder what happened to it.

Neutralised inside `read_operator_header()` rather than at the point of writing, because `_split_master_list()` removes the banner from each split part by matching the exact text that function returned - both sides have to be looking at the same text.

### 📣 The second announce target really did send a list - the note saying otherwise was wrong
#272 fixed a `PRIVMSG` target that defaulted to a LIST of channels, and recorded that the sibling site in the global-queue scan "deliberately" kept one, on the grounds that it was only a membership test. **It is not.** `g_chan` is passed to `start_dcc_send()` at the thread spawn a few lines below, which hands it to `announce.send_transfer_complete()`, which builds `f"PRIVMSG {channel} :"`. The reading stopped at the `isinstance` block and never followed the value to the spawn - and the test written then to protect that shape was protecting the identical defect. Found by the full-program audit.

There are two questions and they need two values. *"Which channels prove this user is present"* wants a LIST: the entry may name one, and an entry naming none must be checked against every configured channel. *"Where do we announce the finished transfer"* wants exactly ONE. The membership check keeps its list; the target now comes from `announce_channel_for()`.

**And that helper had the same hole one level down.** It read

```python
named = next_file.get('channel')
if named:
    return named
```

so a LIST stored in the entry was returned unchanged - it fixed the default and let a stored one straight through. `announce_channel_for({"channel": ["#one", "#two"]})` returned the list, and the wire line was `PRIVMSG ['#one', '#two'] :Sent`. It requires a non-empty string now, strips it (a space ends a PRIVMSG target), and falls back to the configured channel for anything else, because a queue entry whose channel is a list is malformed either way and a predictable fallback beats guessing which entry was meant.

### 🗂️ A folder picker for the Settings page (#164 step 5 - the last one)

**Four defects in this feature were found by the full-program audit before it merged**, which is why it was held back:

- **The editor's CSS class names collided with the File Lists table.** `app.js` has built `<tr class="folder-row">` with a `<span class="folder-name">` for that table since long before the folder editor existed, and `.folder-row { display: flex }` is a global rule - so styling the editor turned the File Lists folder headings into flex boxes. The editor's own classes are prefixed `served-` now; the older markup keeps the names it had first.
- **The picker wrote the chosen path into whichever row now sat at the index it opened on.** The panel stays open while the rows behind it can be added to, removed and reordered, and every one of those renumbers the draft. It holds the row OBJECT now, with a check that the row is still in the draft before writing - a reference survives reordering but not removal, and writing into an orphaned object would look like it worked while changing nothing.
- **Every browse error reached the operator as "HTTP 400".** `fetchJson()` turns a non-2xx into `throw new Error("HTTP " + status)`, discarding the JSON body - so the sentence the route is built to produce ("... is not a folder on this machine") could never be displayed. `fetchJsonAllowingError()` takes the same posture `postJson()` already did.
- And one the fix itself introduced: renaming the markup attribute to `data-served-folder-index` did not rename the three readers, which said `dataset.folderIndex` - a literal the rename could not match. `parseInt(undefined)` is `NaN`, and every row button silently stopped working. **A file-wide pairing check passes straight through that**, because the File Lists table legitimately emits `data-folder-index` and reads `dataset.folderIndex`; only a check scoped to the editor sees it. There is one now.

Three of the four are invisible to every existing test, because nothing in this project executes JavaScript.

The issue puts this last and on its own *"given the exposure"*, and that is the whole design question: the listing itself is twenty lines.

**What it grants that nothing else did.** An authenticated dashboard session can list the NAMES of directories on the machine the daemon runs on, anywhere it can read - not only under the served folders, because the point is to find a folder that is not served yet. Never files, never contents, never sizes or timestamps: a name, and whether it can be opened, is the whole of what a picker needs.

Without it the same session can already **probe** a path - saving a folder answers "not a folder on this machine" - which tells you about one path you already guessed. **Enumeration is different in kind**, so `WEBUI_FOLDER_BROWSER_ENABLED` ships **off** and is an explicit yes.

**Why its own switch and not the console's.** Suggested during review: gate it on `WEBUI_CONSOLE_ENABLED`, so turning the web console on also turns the picker on and nobody grows a second setting. Rejected, because the console is strictly the more dangerous of the two - it runs `ban`, `clearqueue`, `rehash` and `update`. Gating the weaker feature behind the stronger one means an operator who wants a folder picker, and specifically does *not* want a web admin console, has to enable the console to get it: **more risk accepted to obtain less capability**. `defaults.py` states the rule three lines above the console's own switch - "an admin surface reachable from a weaker path gets its own switch and a written reason for its default" - and this is that.

The cost of leaving it off is typing a path instead of clicking one; the folder rows from step 4 work either way, and the page says so rather than leaving an operator to wonder where the button is.

**404 rather than 403 when it is off**, for the reason the console routes already give: 403 confirms the route exists and is merely disabled, which tells anyone probing that this build has one worth returning for.

**No path is ever put in an HTML attribute.** A directory name on Linux may contain a double quote, and `escapeHtml()` is `textContent -> innerHTML`, which does not encode one. Browse entries are addressed by their **index** into `state.browse.entries` and the handler looks the path up from there - the same rule the folder rows and the file lists already follow, and there is a test that keeps it that way.

Listings are capped at `FOLDER_BROWSE_MAX_ENTRIES` and **say they were capped**: a library's top level can hold thousands of artist folders, and silently showing the first few hundred reads as "that folder is missing". Every entry is tested individually so one item the daemon cannot stat - a system folder, a dead symlink, a disconnected mount - drops that entry rather than the whole listing.

A mutation found one weak test, again: dropping the `isdir()` check still fails, because `scandir()` raises and the handler catches it. What the check buys is the **message** - "not a folder on this machine" rather than "[WinError 267] The directory name is invalid", which is an OS string and localised into whatever language the machine runs in. The test pins the sentence now.

**#164 is complete with this.** All five steps of its order of work have landed.
### 🚨 A served folder that is a drive root refused every file under it
`dcc.is_safe_path()` and `library.is_inside()` both compared with `path.startswith(base + os.sep)` - and `os.path.realpath("D:\\")` is `"D:\\"`, which already ends in a separator. So that built `"D:\\\\"`, a doubled separator no real path can start with, and **every file on a drive served whole was refused**.

Refused, not admitted: the direction is safe, which is exactly why it could sit there unnoticed. The master list advertised every file on the drive, every request for one came back as a path violation, and the refusal was logged as a **security event** rather than the configuration problem it actually was.

Latent until #164 step 4 shipped, and reachable the moment it did - the folder editor accepts a drive root with a 200. The separator is now appended only when the base does not already end in one. The sibling-prefix trap the boundary exists for (`/srv/library-backup` is not inside `/srv/library`) is unchanged, and tested alongside it.

Both functions, because the two disagreeing is its own bug: `is_inside()` decides whether a folder set may be SAVED and `is_safe_path()` decides whether a file may be SENT.

### 🚨 Two overlapping rehashes made one of them PART every channel
`handle_rehash_request()` reloads the modules, then compares the channel list it reads **afterwards** against the one it read before to decide what to JOIN and what to PART. A second rehash's reload puts `config.CHANNEL` back to its literal `None` for about a millisecond (see `runtime.config_reload_lock`), and a first rehash reading its "new" list inside that window saw **no channels at all** - so every channel the bot was in fell into the PART branch.

What the audit observed, with nothing patched, in 4 of 60 overlapping runs:

```
[REHASH SYNC] Channel sync completed successfully.
   'PART #one :Removed from DDCore'
   'PART #two :Removed from DDCore'
   'PART #dbg :Removed from DDCore'
CHANNEL after everything: '#one,#two'
channel_users after everything: {}
```

No JOIN, no NAMES, no exception, no error line - and `dcc.py` treats `channel_users` as proof a user is present, so every queue then froze. The bot sat in no channels, believing it was in two.

Reachable without trying: `webserver.py` fires a rehash on **every** Settings save and every password change - two separate endpoints - and `irc.py` and `adminchat.py` each spawn an unguarded thread per `!rehash`.

`runtime.rehash_lock` serialises them. **Waits rather than dropping the second one**: a dashboard save writes `settings.conf` and *then* triggers the rehash, and the one already running may have read the file before that write landed - so a dropped second rehash would silently lose the operator's change. The lock lives in `runtime.py` because `commands.py` is one of the modules a rehash reloads, so a lock allocated there would be a new lock every time.

`handle_rehash_request()` is now a wrapper around `_handle_rehash_request()` rather than the body being re-indented under a `with`, which keeps the diff to the header and leaves the 300-line body untouched for review.

### 🛑 The counter migration can no longer stop the daemon booting
Found by the full-program audit, in code merged the same day. `migrate_download_counts_to_labels()` merged a legacy row onto its labelled key with

```python
if new_key in counts:
    counts[new_key]["count"] = int(counts[new_key].get("count", 0)) + ...
```

which is an `AttributeError` the moment the value already sitting under that key is not a dict - a hand-edit, a half-restored backup, a truncated write. It runs from `oserve.startup()`, so **the whole daemon refused to boot** over one bad line in a file whose own loader is written to shrug off corruption. `load_download_counts()` states the posture two functions up: losing these counters "is a cosmetic failure - which is exactly why it must not be a loud one". A migration that raises is a louder failure than the thing it was migrating.

Two changes. The merge now checks `isinstance(existing, dict)` and, where it is not, keeps the legacy row - that one came through the type check above and is real data; whatever was there did not. And the whole migration is wrapped, so anything a future edit gets wrong in it leaves the counters untouched and the daemon running.

### 📊 The download counters name their folder (#164's last cost-table row)
The one place #164's own cost table listed that steps 1-4 never came back to:

```python
key = os.path.relpath(file_path, config.FILE_DIRECTORY)
```

One root. With several folders configured that goes wrong two ways, neither loudly:

* **`FILE_DIRECTORY` unset** - ordinary now that the dashboard writes `data/library_folders.json` and need never touch `FILE_DIRECTORY` at all. `os.path.relpath(path, None)` measures from the **current working directory**, so the key described where the daemon was started from rather than where the library is.
* **`FILE_DIRECTORY` set to the first folder** - a file in the second keys as `..\\Second\\Artist\\Album\\track.flac`, and on a different drive `relpath` raises `ValueError` outright and the old code fell back to the **absolute** path. That is precisely what #151 made these keys relative to avoid: every counter breaks the moment the library moves.

The key is now `<label>/<path beneath that folder>`. Keyed on the **label** and not the folder's path for that same reason - an operator who moves `D:\\Flac` to `E:\\Flac` and updates the folder list keeps their history, because the label did not change. And it settles the identity ambiguity #164 named: two roots can both hold `Artist/Album/track.flac`, and before this one credited the other.

`library.is_inside()` rather than `dcc.is_safe_path()`: this is attribution, running after a transfer has already completed, not the request-time security gate. That gate is unchanged.

**With a migration, because the alternative is a silent reset.** Changing the key leaves every accumulated row under a key nothing will increment again, while the "most downloaded" table starts from nothing and still shows the old entries. #164 settled the principle for exactly this shape of change - one break at the moment the operator upgrades beats a quiet second one weeks later - and a migration means there is no break at all. Every install with counters today is single-folder, because multi-folder was unreachable until the dashboard could write the list, so the old bare key is unambiguously a file in the first configured folder. `db.migrate_download_counts_to_labels()` runs from `oserve.startup()` next to the side-file migration.

Idempotent by inspection: a key whose first component is already a configured label is left alone. **One honest gap** - a library whose own top-level subfolder happens to share the library's label (`D:\\Flac` containing a folder also called `Flac`) has legacy keys that already start with `Flac`, so they are skipped and stay unlabelled, splitting that folder's counters between two keys. Rare and cosmetic, and documented rather than solved with a schema marker in a file whose every other key is a real row.

An existing test asserted `"MUSIC" not in key` as a proxy for "no absolute path in here". That proxy stopped meaning what it said the moment the library's own folder name became the label, so it now asserts the intent directly: the key must not carry the library's location, whatever that location is called.

### 📁 The served folders are editable from the dashboard (#164 step 4)
Steps 1-3 shipped in v1.11.0 and made the daemon serve from a *list* of folders: `library.py` as the single accessor, the list built from every folder in the operator's order, `!rar` and search resolving a request back to the right one by its label. **The only way to write that list was to create `data/library_folders.json` by hand**, so the feature existed and no operator could reach it. The Settings page offered one "Music directory" box - which is the single-folder FALLBACK - and nothing said so.

Found by an operator looking at that page and asking why there was only one box.

**Its own endpoint pair, not a setting.** Every other field on that page is one scalar typed into one box, and `settings_file.save()` writes exactly that. A folder list is ordered, validated as a SET, and stored as JSON. Bending it into the settings shape would mean either one comma-joined string (which cannot carry labels, and breaks the moment a path contains a comma) or one setting per folder (which cannot be reordered). So: `GET/POST /api/folders`.

`library` is imported inside each handler rather than at module scope - `tests/test_import_graph.py` holds `webserver.py` to a short list of imports that must work with no daemon running.

**Validated as a set, because that is the only way these faults exist.** Two folders can each be perfectly good and still be an invalid pair: one nested inside the other lists every file under it twice, and two sharing a label make the label useless for telling them apart. `library.problems()` already took the whole set and returned every fault at once; the page renders one line per fault rather than "invalid folder list", which tells an operator with eight folders nothing about which two to look at.

**`source` is reported, not just the folders.** `"file"`, `"file_directory"` or `"none"` - because a page showing one folder cannot otherwise say whether that is a one-entry list or the fallback, and editing the list is exactly what switches between them.

**An empty list is allowed** and means "go back to the single Music directory". The file is REMOVED rather than written as `[]`, because `library.load_folders()` already returns `None` for an empty list and falls back - so a written `[]` would be a file on disk that does nothing.

**No rehash, but a rebuild.** `library.folders()` re-reads the file on every call, so a running daemon picks a saved list up immediately. What it does not do is rebuild the *published* list, and a folder nobody can see in the list is not really being served - so the response carries `rebuild_required` and the page turns it into a sentence instead of leaving the operator to find out.

**Deliberately not in this pass: the server-side folder browser.** Letting the dashboard enumerate the filesystem over HTTP is a separate surface and deserves its own design rather than being tacked onto this. Paths are typed and validated server-side, and "not a folder on this machine" is a clear enough answer to a typo.

#### Two defects in this change, found before it shipped
The first was mine and would have broken the page on its first successful save: `postJson()` resolves with `{ ok, status, data }`, and the editor was written against `res.body` - `undefined` on every one of those objects, so the save would have thrown a `TypeError` inside a promise with no catch and the page would have gone quiet. **Exactly the shape of #267**, and every test in the suite passed straight through it, because nothing here executes JavaScript. Found by checking the helper's contract instead of assuming it, and there is now an assertion in `tests/test_web_assets.py` that no caller reads `res.body`, plus one that `postJson` still returns that shape so the rule cannot quietly become wrong.

The second came out of the mutation run: deleting the `library.problems()` call from the handler changed nothing a test could see, because `library.save_folders()` validates again and raises. The refusal still happened - as one error string rather than the list the page renders line by line. The test now pins the STRUCTURE rather than the fact of refusal.

### 🧭 One odd folder name made every file after it invisible
`find_matching_entries()` walks the master list with a three-state machine, because the format wraps each folder heading in a pair of rule lines. A rule line is "every character is `=`" - so a folder whose NAME is also all `=` reads as a *second* rule line. The machine treats that as a malformed doubled rule, keeps waiting for a heading, and takes the next line it gets - a FILE line - as the heading instead.

That loses the file **and** shifts every heading after it by one, so the rest of the list is mis-attributed or swallowed too. Measured on a three-folder list with one bad name in the middle: searching found **one of the three files**, and the one it missed last was in a perfectly ordinary folder.

Nothing failed. The list on disk was complete and the files were still served correctly when requested by name; only search and the dashboard's File Lists view were wrong.

**It is not reachable through our own list** - update_list.py writes every heading as `D:\MUSIC\<folder>\`, which is neither all `=` nor starts with `!`. It is reachable through a **fetched** one: `list_fetch.py` runs this same parser over a list another bot wrote and sent us, and that bot's folder names are not ours to choose.

A line starting with `!` is a file line, whatever the state machine was expecting. Checking that before consuming it as a heading lets the parser resynchronise, so a malformed heading now costs only its own folder's attribution instead of the remainder of the list.

### 📏 A fetched file's name is cut to fit one path component
The offering bot chooses the filename in a DCC SEND. `_sanitize_offer_filename()` strips separators, `..`, nulls and everything outside the charset whitelist - but it does not truncate, and the comment at the `open()` call said that was covered:

```python
# _sanitize_offer_filename() does not truncate, so the length of this
# name is entirely the offering bot's choice - wrap it like dcc.py
# wraps every path it touches.
handle = open(platform_compat.long_path(dest_path), "wb")
```

**There are two different limits and `long_path()` only lifts one of them.** The `\\?\` prefix removes the 260-character limit on the TOTAL PATH - which is exactly why dcc.py wraps every path it touches. The limit on a single path COMPONENT (255 characters on NTFS, 255 bytes on ext4) is a filesystem rule underneath that prefix and does not move. Measured by binary search against a real filesystem, with the wrap in place: **255**.

So an over-long offer failed at `open()` with `[Errno 22] Invalid argument`, caught by the transfer's own `except Exception` and reported as `transfer error: ...` - which names neither the length nor the name. Nothing crashed. That file simply never fetched, every time.

**And the prefix counts.** The stored name is `<request-id>_<cleaned name>`, and the request id is `uuid4().hex[:12]` plus an underscore - so the real budget for the offered name was 242 characters, not 255. Short enough to reach with a genuine filename (a long classical or live-recording title), not only with a hostile one, which is what makes this worth fixing rather than only worth refusing.

`_fit_name_component()` shrinks the STEM and keeps the extension, the same choice `announce.fit_irc_filename()` makes and for the same reason: the extension is how the file is recognised and opened, and a name trimmed the other way arrives as "Symphony No 9 in D mino" with nothing on the end. The budget is counted in **UTF-8 bytes**, because ext4's limit is bytes and an accented title costs two per character; `_truncate_utf8()` cuts on a character boundary, since a blind byte slice lands inside one.

Fitting happens BEFORE the `is_safe_path()` containment check, so there is a test that a truncated name still cannot escape - a name cut down to `.` or `..` would be exactly that.

### 🔒 A setting the daemon has no blank behaviour for can no longer be saved blank
From an audit of the dashboard's settings surface: every string field the page offers was saved empty, and the accepted ones read back. **Twenty-eight were accepted.** Four of them are used verbatim on the wire or on disk.

| Setting | What empty means |
|---|---|
| `SERVER` | `s.connect((config.SERVER, config.PORT))` with no host |
| `ALT_NICKNAME` | the literal line `NICK ` after a 433, answered with 431 |
| `LIST_BASE_NAME` | the master list's filename |
| `WEBUI_HOST` | the interface the dashboard binds - empty is EVERY interface, where the default is careful to say loopback |

`settings_file.REQUIRED` was the wrong list to extend. It means "an operator must supply this", which is why it holds exactly three names; the question here is the different one of whether a setting that already works may be emptied.

**The shipped default answers it, with no list to maintain.** `None` means "unset unless you say otherwise" and blank is how an operator says it again - `RAR_BINARY` cleared is "look on PATH", `DEBUG_CHANNEL` ships blank because an install with no debug channel is not misconfigured. A non-empty shipped default is the opposite: there is no code path for empty anywhere in the daemon. `defaults.SHIPPED_VALUES` snapshots every setting at the same moment `SHIPPED_DEFAULTS` already snapshotted the REQUIRED three - before either override mechanism runs - because the CURRENT value cannot answer this: after one blank save it is itself empty, and a rule reading that would wave through every save after it.

`FILE_DIRECTORY` stays blankable and that is correct: it ships as `None`, and since #260 an install serving from `library_folders.json` legitimately has none. `library.folders()` returns `[]` for it rather than raising.

### 🧮 SCRIPT_VERSION is no longer an editable setting
It was a field on the Settings page, labelled "Script version". Saving it wrote the number into `settings.conf` - and `settings.conf` is applied AFTER the shipped defaults, so from that moment the file won, permanently.

The next upgrade would ship a new version and the daemon would go on reporting the old one: in the channel advert, the list masthead, the CTCP VERSION reply and the dashboard's own header, with nothing anywhere to say why. Measured before the fix: shipped `DCCore v99.0.0`, reported `DCCore v1.0.0-STALE`. "What version are you running?" is the first question asked about any install, and this made the answer unreliable in the one direction nobody thinks to check.

`settings_file.NOT_SETTINGS` now holds it - uppercase names that describe the CODE rather than the installation. A version left in an existing `settings.conf` is ignored rather than applied, and does not stop the rest of the file being read. `PROJECT_URL` is deliberately NOT excluded: a fork pointing at its own repository is a real configuration, and it is only stopped from being blanked.

A second guard came out of the mutation run: `SETTINGS_CATEGORIES` is not what decides whether a field renders - `build_settings_payload()` filters by `declared_types` + `is_overridable` - so a name left in a category after it stops being a setting silently vanishes from the page. There is now an assertion that the category list names only real settings.

### 🏷️ The 433 fallback nickname actually runs
Both 433 handlers read

```python
alt_nick = getattr(config, 'ALT_NICKNAME', f"{main_nick}`")
```

which reads as a defence against a missing or empty alt nick and is not one. `getattr`'s default fires only when the attribute is **absent**, and `defaults.py` declares `ALT_NICKNAME: str = "DCCore_"`, so it never is. The fallback was unreachable in both places - dead code that looked exactly like a safety net.

It only mattered while the setting could be blanked, which is the entry above; but a file written before that fix still exists, and `admin_config.py` can still set it to anything. `irc.resolve_alt_nick()` now does what both call sites always looked like they were doing. Reachable only on a 433 - reconnecting after a split, with the old session still holding the name - which is the one moment the fallback exists for.

### 📢 A PRIVMSG target is one channel, and the default was a list of them
The channel a finished transfer is announced in was picked like this:

```python
target_chan = next_file.get('channel', config.CHANNEL.split(','))
```

`.split(',')` returns a LIST. A queue entry with no `'channel'` key therefore handed a list to `start_dcc_send()`, which passes it to `announce.send_transfer_complete()`, which builds `f"PRIVMSG {channel} :"` - and the line that went out was

```
PRIVMSG ['#one', '#two'] :Sent Song.flac to nick
```

A malformed target, answered with a numeric nothing here reads, so the announcement was lost while the transfer it announced had already succeeded. Nothing raised and nothing logged.

Latent rather than live - every entry the request path builds does set `'channel'` - but the default was the wrong SHAPE either way, and one that is only correct by accident of nothing using it is worth removing. `dcc.announce_channel_for()` is lifted out for the same reason `resolve_dcc_address()` and `take_complete_lines()` were lifted out of `irc_loop()`: the caller needs a socket, a live queue and a channel-user map, and the rule itself is four lines. It also now treats an entry carrying an EMPTY channel the same as one carrying none, which `get()`'s default could not.

**The sibling site is deliberately unchanged.** The global-queue scan uses the same expression as a list of channels to test membership in, and type-switches on str vs list explicitly. There a list is the right answer, and there is now an assertion saying so, because the obvious "tidy-up" would silently narrow that scan to one channel.

`announce_worker()`'s own `config.CHANNEL.split(",")` went through `irc.configured_channels()` at the same time - it is an `AttributeError` on the advert thread while a rehash has `CHANNEL` set to `None`, and `is_ready` cannot be relied on to gate it because `is_ready` is a module global that same reload rebinds.

The first version of this file's tests reimplemented the expression instead of calling it, and passed happily while the module was mutated back to the list default. The mutation run is what caught that; the test calls `dcc.announce_channel_for()` now.

### 🔎 What the audit checked and found sound
Recorded so the next audit starts further along rather than repeating it. Every dashboard route (25) refuses to answer without a session; ten path-traversal shapes served nothing; no route returns a 500 under a sweep of hostile query and body input; `app.js` references 72 element ids and every one exists in `index.html`, with no unguarded dereference and no unescaped value in an attribute (the quote-in-attribute class this file already carries `BroadcastRenderingXssRegressionTests` for); every pure parser in `irc.py` survives a fuzz sweep of malformed lines; and no lock or container in any reloaded module loses its identity across a rehash, which is `runtime.py` doing its job.

### 🖥️ A rehash no longer shows - or leaves - a configuration the daemon does not have
One operator saved one setting from the dashboard. Four defects fell out of it, and every one of them was silent.

**The Settings page came back with Nickname, Admin nick(s) and Channels EMPTY.** Nothing was lost - `settings.conf` had all three the entire time, and a refresh showed them - but the page an operator uses to check their configuration told them their configuration was gone.

`importlib.reload(defaults)` re-executes `defaults.py` from the top. That file is a long list of literal assignments (`NICKNAME = None`, `CHANNEL = None`, `ADMIN_NICK = None`) with `settings_file.apply_to(globals())` only at the very END of it, so for the whole of a reload every configured setting is transiently back to its shipped default. Measured against the reporting install's own file: **a reader looping on `config.NICKNAME` during a rehash saw it blank for 52% of the window.** Not a narrow race - half of it.

Saving from the dashboard starts the rehash on a background thread and returns at once, and the browser re-fetches `/api/settings` immediately, so it lands inside that window nearly every time. Only the three `REQUIRED` settings *looked* wrong, which is why it took a real install to find: every other setting on that page has a shipped default that happens to match what most operators run (`SERVER`, `WEBUI_HOST`), so it renders identically whether or not `settings.conf` has been applied yet.

`runtime.config_reload_lock` is the fix, and it lives in `runtime.py` for that module's founding reason - a lock allocated in a module `!rehash` reloads is a new lock every rehash, and this one is held *by* the rehash. `RLock`, because the rehash thread holds it across the reload and calls `announce.send_debug()` inside it, which fans out to the web console sink - webserver code, same thread, same lock.

**And a backstop, for when the window does not close.** `settings_file.apply_to()` is deliberately forgiving about an unreadable `settings.conf`: it logs and keeps the built-in defaults. That is right at startup, where `oserve.startup()`'s `REQUIRED` gate then refuses to boot and says why. It is wrong at rehash, where there is no gate - the daemon is already connected and would simply carry on with no nickname, no channels and no admin. A file readable a moment ago and not now (antivirus holding it open, a network share blinking, an editor mid-save) would silently de-configure a live bot. `reload_modules_in_order()` now snapshots `settings_file.REQUIRED` before the reload and puts back anything that came back blank, loudly. Narrow on purpose: `REQUIRED` only, and only a value that *was* set - anything wider would be a ratchet no rehash could ever unset.

### 📡 The debug channel is joined by a rehash, not only by a reconnect
Same report. The operator typed a debug channel into the dashboard, pressed Save, was told "Rehash started", and the bot never joined it.

`irc.py` joins `DEBUG_CHANNEL` once, at connect, right after the main channels. The rehash's channel sync built its list from `CHANNEL` alone and mentioned `DEBUG_CHANNEL` in exactly one place - the test that stops it being PARTed. So setting a debug channel on a running bot reported success and did nothing until the next restart, with no way to tell from any log that anything had been skipped.

`_channels_to_sync()` now supplies **both** sides of the JOIN/PART comparison, and the symmetry is the point: put the debug channel only in `new_chans` and the reported bug is fixed at the cost of re-JOINing that channel, with a *"due to new configuration layout!"* line, on every rehash for ever.

It also replaces a bare `config.CHANNEL.split(",")` on both sides - an `AttributeError` on `None`, thrown on the rehash thread between the reload and the end of the channel sync.

### 📝 settings.conf stops growing a new header block, and ends with a newline
The same save, seen from the file. `_rewrite()`'s append branch asked whether a SETTING was missing and never whether the EXPLANATION was, so the three-line *"Added by DCCore because these settings were not already in this file"* block was written again underneath the copy already there - twice in the reporting install's file, and once more per save after that. It also appended past the empty string `split("\n")` leaves behind, so every `settings.conf` that had ever had a setting added to it ended mid-line.

Neither breaks `parse()`. Both are wrong in a file an operator opens and reads, and both compound - the blank lines came in pairs, one per save.

The header is now a single `_ADDED_HEADER` tuple, consulted rather than retyped, so "is it already there?" and "what do we write?" cannot drift into two different answers - which is how it came to be there twice.

### 🧹 One operator's nickname and channels are out of the published tree
Found while writing the tests above. `.gitattributes` export-ignores `docs/UPDATES.md` and `docs/PUBLIC-REPO-WORKFLOW.md` and **nothing else** - so `tests/` and `irc.py` ship to the public repository verbatim, and three test files plus two `irc.py` comments carried a real operator's nickname and their six real channel names, written in as "the reported case".

Replaced with invented ones that preserve the property each test rests on: the long nick is still 13 characters and its truncation still 12, because that arithmetic *is* the NICKLEN test; the six-channel list is still six; the mixed-case channel still has a capital in it.

The `flac-serv-*` hits elsewhere in the tree are deliberate and stay: they are the legacy side-file names `db.migrate_legacy_side_files()` matches byte-for-byte, and `defaults.py` already says so.

### 🚪 A space after a comma no longer costs you every channel but the first
Reported from a real install: six channels configured, one joined, and a log line saying *"Activating the advert despite 5 unconfirmed channel(s)"* that never connected the two.

`config.CHANNEL` was handed to `JOIN` verbatim. RFC 2812 is `JOIN <channel>{,<channel>} [<key>{,<key>}]` - **space-separated parameters** - so

```
JOIN #one, #two, #three
```

joins `#one` and hands the server `#two,` as a channel *key*. The rest is discarded, and nothing about that is an error, so nothing reported one.

The format that breaks it is the one that reads naturally, and the one `configure.py` echoes back at the operator when it shows their current value - so the tool actively encouraged it.

The advert loop was unaffected, because `announce.py` strips each entry as it goes. Only the JOIN did not, which is why the bot looked half-working rather than broken: it adverted in channels it had never joined.

`join_target_list()` normalises before sending, so what an operator typed works as written. 10 tests, mutation-verified - putting `config.CHANNEL` back into the JOIN fails at once - and including a control that a list with no spaces passes through untouched, so this is a repair and not a change of behaviour.

### 📛 The bot takes its nickname from the server, not from config
Found on a real install. A nickname longer than the server's `NICKLEN` is not refused - it is silently **shortened**. Undernet allows 12, so `Merlin-DCCore` registered as `Merlin-DCCor` and the daemon never noticed: it logged *"CURRENT_NICK settled as: Merlin-DCCore"*, advertised `@Merlin-DCCore` - a nick nobody could PM or DCC - and went on believing a name it had never had. `433` was handled because it announces itself; truncation does not, so nothing failed and nothing caught it.

**The nick now comes from the numeric's target field.** Every numeric reply is `:<prefix> <code> <target> ...` and for a registered client `<target>` *is* its nick - RFC 1459/2812 message structure rather than a courtesy, so every IRCd does it. What varies is RPL_WELCOME's *text*, which each network writes as it likes, so this reads the field and never the sentence.

**And `005` says why.** Nothing read RPL_ISUPPORT before, so the limit the server publishes was there and unused. `isupport_nicklen()` reads it, and the daemon can now name the reason instead of leaving an operator to notice their bot is called something else.

The shortened name goes into `NICKNAME` only - `ORIGINAL_NICK` keeps the configured value. That is what keeps the master list working: it is built by a subprocess importing fresh config, so its request lines carry the configured name, and `get_bot_aliases()` already answers to both because it was written for the same divergence after a `433`.

13 tests, against real `001` lines from three networks. The negatives matter more than the positives and are mutation-verified: a forged `PRIVMSG` carrying `001` must not rename the bot (unanchored matching once read `!DCCore 001 - Enter Sandman.flac` as a numeric), the pre-registration `*` target is not a nick, `NICKLEN` in the trailing prose is not a limit, and `MAXNICKLEN=` is not `NICKLEN=`.
### 🧭 Turning the Console off no longer disables the whole dashboard
Reported from a real install, running the shipped default. `WEBUI_CONSOLE_ENABLED` is off, so `/api/console/log` answers 404, so `disableConsoleUi()` ran - and it **removed** `#view-console` from the page.

`activateView()` walks every key in `views` and calls `getElementById("view-" + key).classList` on each. With the section gone that is a null dereference, and it threw on **every view switch**, before the per-view loaders at the bottom of the function. So the symptom was not a missing Console: it was Settings stuck on "Loading" for ever, and Queue, Stats and Downloads quietly refusing to refresh. One disabled feature disabling the entire dashboard.

The nav button is hidden now and the section stays in the DOM. `activateView()` also checks for null before dereferencing - one absent section must not take the router with it, whatever removes it next time.

Two structural guards in `tests/test_web_assets.py`, both mutation-verified against the exact defect. Neither can prove the navigation works - nothing here executes JavaScript - but they refuse the move that broke it: deleting a section the router still looks up, and dereferencing that lookup without a check.
### 🪟 The Windows instructions name a command Windows has
Reported from a real install: someone following the setup on Windows was told to run `python3 configure.py`. A python.org install gives you `py` and `python` and **not** `python3` - and Windows 10 and 11 ship an App Execution Alias for that exact name, so typing it opens the Microsoft Store or prints "Python was not found" on a machine where Python is installed and working perfectly. A confusing failure at the very first step, from a document written on Linux.

`scripts/windows/start-dccore.bat` never had this problem - it probes `py -3`, then `python`, and says what to install if it finds neither. Only the prose was wrong, which is why nothing caught it: the code was right.

`docs/WINDOWS.md` now uses `py` throughout, with a note on why. `README.md`'s Quick start gains the Windows block - it said "Windows is the same with `start-dccore.bat`", which covered the second and third lines and not the first, the only one that actually breaks. `docs/INSTALL.md` says it once where the command first appears.

### 🖥️ A Console page in the dashboard - the DCC CHAT admin console, in the browser
Requested directly: an operator who wants neither a second IRC client open just to reach the admin console, nor a debug channel broadcasting the daemon's internals to whoever joins it. The dashboard's new Console page is both, over HTTP, behind the same login as everything else there.

Nothing about `adminchat.py` was duplicated. `webserver.build_console_command_result()` dispatches straight into `adminchat.COMMANDS`/`handle_command()` - the exact command set the DCC CHAT console runs, so a change to one cannot silently stop matching the other. `webserver._console_debug_sink()` registers with `announce.add_debug_sink()`, the same fan-out `adminchat.Session.debug_sink()` already uses - which is also why `DEBUG_TO_CONSOLE` (on by default, independent of `DEBUG_TO_CHANNEL`) is the only switch this needs: an operator with no debug channel at all still gets the log for free, and nothing downstream of `announce.send_debug()`'s 44 call sites had to learn a new destination exists.

**The log and a command's reply are two different things, and the page treats them that way.** A command is request/response - `POST /api/console/command` returns exactly the lines that one command produced. `GET /api/console/log?since=<cursor>` is the ambient stream, polled. This matters for `ban`, `unban`, `clearqueue`, `rehash` and `update`: each runs on a background thread (`adminchat._run_detached`) and replies immediately with only an acknowledgement, the same as a DCC CHAT session sees - the actual result arrives a moment later through the log, once the handler underneath calls `send_debug()` on completion. Verified live rather than assumed: running `ban` returns `["Banning ... ..."]` alone, and the log's next poll carries the real `[BAN]` line from `db.py`.

`quit` is the one command intercepted before it ever reaches `handle_command()`: it means `session.close()` in `adminchat.py`, which requires a socket - meaningless, and an `AttributeError` on `_WebConsoleSession`, without the special case (mutation-tested: removing it turns the crash into a caught-and-reported "Command failed" line instead of the friendly one).

`announce` is imported lazily, inside the sink and the log builder, not at module scope - `tests/test_import_graph.py` already enforces that `webserver.py` pulls in nothing beyond a fixed allow-list, precisely so a route handler cannot quietly drag the daemon's modules into a page that is tested without one running.

29 new tests (25 in `tests/test_web_console.py`, 4 of route wiring in `tests/test_dashboard_routes.py`), full suite green, verified end to end against a real running instance (login, every command, the async ban→log follow-up, an unauthenticated 401).

**It ships OFF, behind `WEBUI_CONSOLE_ENABLED`.** The two ways to reach the admin command set are not equally protected: the DCC CHAT console needs the operator's services host (`ADMIN_HOSTMASKS`) *and* the password, while the dashboard needs the password alone, over HTTP with no TLS. So this puts `ban`, `unban`, `clearqueue`, `rehash` and `update` behind the weaker door - a fine trade for an operator who wants it, but it has to be a trade they chose. Without the switch, anyone who had turned the dashboard on for Search and Queue would have gained a remote admin console on upgrade: no setting changed, nothing recording that their exposure had widened. `WEBUI_ENABLED` was deliberately made to fail closed (#116); this keeps what saying yes to it grants from quietly growing. Same shape as `ADMIN_CHANNEL_COMMANDS`, which exists for the same reason.

Both routes check the switch **independently** rather than sharing a decorator - they are the whole attack surface, and a decorator applied to one and forgotten on the other is a silent hole. Mutation-verified: leaving the *mutating* route ungated fails. They answer 404 rather than 403, because 403 confirms the routes exist and are merely switched off, which tells anyone probing that this build has an admin console worth coming back for.

**A bug the gate would otherwise have caused.** `pollConsoleLog()` runs on a timer whatever view is open, and its `catch` calls `markConnection(false)` - so with the Console off, every poll would 404 and the dashboard would report the whole daemon as unreachable, once every two seconds, for ever. The 404 is now its own case: it removes the nav item and the view, stops the timer, and leaves the connection indicator alone.

## 🟩 v1.11.0 (2026-09-04) - "The Several Folders Release"

Serving from more than one directory, which is the largest single gap against OmenServe and the one most asked about. **Configurable today by editing `data/library_folders.json`; the Settings page for it is still to come** (#164) - so this ships the capability, not yet the convenience.

Alongside it, the bot can finally say what it is: a CTCP VERSION reply, a masthead on every generated list, and a banner the operator writes themselves.

### 🪪 The bot can say what it is, without saying it in the channel
Two surfaces wanted the same missing fact. There was no project URL anywhere in the tree - no `github.com/...` in any `.py` or `.md` - so a list that reached a stranger carried nothing about what produced it, and a CTCP VERSION query got silence. `PROJECT_URL` is now defined once beside `SCRIPT_VERSION` and both consumers read it (from #69).

**CTCP VERSION is answered**, by NOTICE straight back to whoever asked. `irc.py` already parsed incoming CTCP but only acted on `QUE`, `REMOVE` and `DCC SEND`. `VERSION` joins the flood-gated set, because an unthrottled CTCP responder is a standard way to make a bot flood *itself* off the network - a few hundred queries and its own replies trip excess-flood. `CTCP_VERSION_REPLY` turns it off for operators who would rather not advertise a build.

Deliberately **not** a `!version` command. The bug report template (#251) told reporters to use one, and none exists: in this codebase a bang means IRC, and `version` lives in the admin console without one. Building it to make the document true was backwards and was rejected - a `!` command answers in channel every time anyone tries it, whereas a CTCP reply is private. The template now points at the admin console command that already exists.

**Every generated list carries a masthead** - `Served by <nick> - <version> - <url>` - and an operator can put their own banner below it by creating `data/list_header.txt` (`LIST_HEADER_FILE`, bounded by `LIST_HEADER_MAX_BYTES`). Both the `.txt` and the `!rar` list get it; the `.zip`/`.rar` downloads inherit it, since they derive from the same files.

Three constraints shaped the placement, each with a test:

* **Not line 1.** `commands.count_from_master_list()` does one `readline()` and regexes `List of N Files` out of it. Anything above that line stops the regex matching and fails silently - no exception, no empty file, just a count of zero feeding `!update` and the channel advert. The guard asserts the count is *identical* with and without a banner rather than equal to a literal, so it still fails if placement moves back up.
* **Above the operator's banner, not below.** The banner is free-form and any height, so the other order buries the attribution on exactly the installs that decorate the most. A test writes a 200-line banner and asserts the masthead is still within the first few lines.
* **The banner bypasses `_one_line()`.** That flattens control characters, which is right for folder lines and fatal for the ASCII art this exists to carry. Line endings are normalised too: the list is opened without `newline=""`, so a CRLF banner passed through untouched would come out `\r\r\n` on Windows.

`tests/test_config_fallbacks.py` caught the identity line reading `getattr(config, 'SCRIPT_VERSION', 'DCCore')` - a non-empty fallback is a second opinion about a value `config.py` already declares. It is now `update_list.list_identity_line()`, shared by both writers so they cannot drift.

### 🖼️ The delivered list shows the operator's banner once, not once per section
The `-FULL-` text download is the master index and the `!rar` album list concatenated, and each carries its own banner. That is right when they are handed out separately - `.zip` and `.rar` do exactly that, and the `!rar` list is served on its own - but joined, it put the operator's ASCII art halfway down the file as well as at the top, which reads as a bug rather than a design. Introduced by the banner itself, caught by generating a real list rather than by a test.

Removed at concatenation rather than at the writer, so the standalone `!rar` copy keeps its branding, and by matching the exact text `read_operator_header()` returned rather than by recognising a banner in the output - only the first is knowable, since a free-form banner could otherwise contain anything, including something shaped like a folder heading.

The identity line still repeats per section, deliberately: the album half should say what is serving it too, and one line is a section header where a banner of arbitrary height is not.

### 📥 Reading an OmenServe operator's history (#69, step 1)
The parser and the mapping. No UI and no endpoint yet, so nothing is reachable — this is the piece that can be tested without a browser.

**There is no OmenServe stats file.** Mapped from a live install: OmenServe keeps nothing of its own, and the counters are mIRC's persistent `%variables` in `scripts/vars.ini`, written by whichever add-ons the operator runs — `%mx.*` (mxrarserver) for files and bytes sent, `%SD*` for the speed record, `%OSL.*` for the day buckets. Two operators running different add-ons keep their history in different variables, which is why nothing here is required and why **a missing variable produces an absent field rather than a zero**: writing a zero over somebody's real lifetime total is the worst thing this feature could do. Mutation-verified — making a missing field default to 0 fails 8 tests.

Three numbers import: `%mx.rarsent` → total files, `%mx.rartsent` → total bytes, `%SDmaxspeed` → the speed record. **No unit conversion**: `%SDmaxspeed` is already bytes/s, exactly what `speed_record.txt` stores, so the units warning in the design was right in principle and does not apply. Its `<bytes>,<nick>` second half is dropped.

The day buckets are **shown in the preview and deliberately not imported**. `%OSL.Today` reads `Friday` — a weekday name, not a date — so nothing can tell whether "today" means today or six days ago, and `db._rotate_day_unlocked()` would rotate an imported "today" out of existence on the next new day. Shown rather than silently dropped, because an operator can see them in their own file.

**A correction to the design while building it.** The design put the parsing in the browser, so an operator's other ~268 variables never cross the network. But this repository never executes JavaScript in a test — `tests/test_web_assets.py` checks that `app.js` *parses*, explicitly "not what the script does", and CI has no node. A parser handling hand-edited text whose output overwrites cumulative totals is the last thing that should live where it cannot be tested. So the split moved: the page **filters** to the allowlisted lines (five lines of JavaScript), and this **parses**. Privacy intact, logic tested. `variable_names()` is served to the page rather than written into `app.js`, so the filter and the parser cannot drift.

### 🗂️ One list, built from several folders (#164, step 3)
The scan walks every configured folder in the operator's order, and every heading now leads with its folder's label: `D:\MUSIC\Artist\Album\` becomes `D:\MUSIC\Flac\Artist\Album\`. **The first step where published output changes.**

**Labelled for a single folder too.** Labelling only at two or more looks gentler and is not: an operator who serves for weeks and then adds a second folder would change every path anyone already saved - a second break, landing on people who had no idea anything changed. Once, at the upgrade, is one.

**What does not change** is most of it. A file request carries a filename, not a path (`dcc.py:1132`), so every saved file request and every AutoQ entry built from a file row is untouched. Only `!rar` folder requests carry a path, and step 2's resolution still accepts the unlabelled form by trying each folder in order - so an old saved `!rar` row keeps working.

**A missing folder costs only itself.** An unplugged drive or unmounted share is skipped with a warning naming it, and the list is built from the rest. Deliberately different from a subtree failing *during* a walk of a folder that was present: that keeps the previous index rather than publishing a truncated one, because it is a systemic failure of a library we are meant to be reading. Every folder missing is still caught by the zero-files guard.

**A bug caught before it was written.** The `!rar` multi-disc truncation guards on `truncate_at >= 2`, meaning "leave at least two segments below the folder" - artist and album. `rel_dir` now begins with the label, so every index shifted by one, and left at 2 a library shaped `<label>/<artist>/<disc 1>` would truncate to `<label>/<artist>`: the artist root `dcc.py` refuses outright, leaving that album with no requestable row at all. That is the exact failure the threshold was added to prevent. It is 3 now, with a test for the shape.

Of 2303 existing tests only one needed changing, and it was a hardcoded expectation rather than a behaviour: it now derives the label instead of spelling it, so it says "the album under its folder" rather than "the folder happens to be called music".

### 🧭 Resolution reads a heading's folder before anything writes one (#164, step 2)
The design put the scan before resolution. That order does not work: the moment the scan writes `D:\MUSIC\Flac\Artist\Album\`, `resolve_list_folder()` joins the label onto `FILE_DIRECTORY` and every `!rar` request breaks. So the consumer learns labels first, while nothing produces them - which is also what makes this step a verified no-op in production.

`resolve_list_folder_with_root()` returns **which folder** a heading landed in, and that is the point. `dcc.py`'s `!rar` path runs `is_safe_path()` and then an artist-root check; with several folders configured, asking about `FILE_DIRECTORY` asks about the wrong one, and asking "inside ANY configured folder" would be a weaker test than the one that line has always had. Resolving to a single root keeps it exactly as strong.

A labelled heading resolves inside its folder. An unlabelled one - anything from a list saved before this, still sitting in someone's AutoQ queue - tries each folder in the operator's order and takes the first that exists. **Existence decides between the two**, so a label that shares a name with a real subfolder resolves to whichever is really there.

The file-request path gains `search_roots`: the walk and the final containment check cover every folder, while list artifacts stay pinned to `LOCAL_LIST_DIR`, which is one place however many folders the library spans. The queued-pack re-check asks "inside one of the served folders", which is the honest question there - it has a real path and no heading to resolve.

**A bug this found in its own change.** One of the four edits to the `!rar` path matched twice and failed; the queued re-check got fixed and the traversal guard did not. The result resolved a heading into the right folder and then checked containment against a different one, so every album outside the first folder would have been refused as a traversal attempt. Caught because the test drives the real handler with two folders and asserts the *reason* for a refusal, not just that one happened. Mutation-verified: reverting the guard to the global root fails.

18 tests. `test_path_security` and `test_download_resolution` pass unchanged.

### 📚 Groundwork for serving several folders (#164, step 1)
`library.py` is now the one place that answers "which folders, in what order". **Nothing changes yet**: with no folder file on disk - every install today - `library.folders()` returns a single entry built from `FILE_DIRECTORY`, so every caller sees exactly what it saw before.

The point of doing it this way round is the 54 `FILE_DIRECTORY` references across ten modules, concentrated in `dcc.py` (16) and `update_list.py` (9). Teaching each of them about a list of folders would mean touching all 54 again when multi-list arrives and the folder set moves inside a list; funnelled through one accessor, that later change rebinds the accessor instead.

`LIBRARY_FOLDERS_FILE` (`./data/library_folders.json`) is an **override, not a replacement** - absent, `FILE_DIRECTORY` is the single folder - so an upgrade migrates nothing and the file appears the first time an operator saves a folder set. JSON rather than a `settings.conf` list because `settings_file.py` refuses any list entry containing a comma, and music paths routinely have one (`D:\Rock, Metal`).

Validation is written now because it is what an operator meets the first time they configure two folders: no folder inside another (checked in **both** directions - a rule checking one would let the same overlap through depending on the order they were added), no duplicate paths, unique labels, and a label that is a single path component since it becomes part of paths users copy back. Every problem is reported at once rather than the first, and each names the specific entry it conflicts with.

31 tests. Mutation-verified: replacing the separator-boundary containment test with a plain `startswith` fails (`/srv/library-backup` is not inside `/srv/library`), and dropping either nesting direction fails.

### 🤖 The list says, where it is written, that a script reads it back
The generated list is not only read by people. **AutoQ.mrc** copies request lines out of it and sends them verbatim, so `!DCCore !rar D:\MUSIC\Artist\Album\` is a command rather than a display row, and appending anything to it stops AutoQ matching. That is what ruled out a folder size on each album row - wanted, and parked in #69 until there is a list format carrying structure separately from the request line.

Nothing in the tree said so. `dcc.py` cites AutoQ compatibility for **archive and filename** shape (around lines 93, 125, 640, 1078-1091, 1215) and `TheRequestTriggerIsStable` pins the trigger against nick drift, but neither covers the request line's *format* - so the objection had to be rediscovered rather than read. The constraint now lives at the write site.

The masthead makes it worth testing rather than only documenting, since it put new lines into a file a script parses: no line above the listing may begin with the request trigger, in either list; the `!rar` row must end at the folder; and a control that request rows are still emitted at all, without which the first three would pass on a list that had stopped producing them. Appending `::SIZE:: 1.2GB` to the `!rar` row fails the trailing-field test, and a masthead starting with the trigger fails three.

`INSTALL.md` tells operators not to start a banner line with the bot's own trigger - documented rather than sanitised, since stripping it would break the verbatim promise the banner exists for.

### 🧪 The port-ordering tests stop depending on the machine
`FetchListenerPortOrderingTests` bound real sockets and then asserted which port came back, which quietly required 55000-55010 to be free. `_open_fetch_listener()` falls through to the next port whenever one is taken, so a listener still open from an earlier test - or a `TIME_WAIT` left by one - silently changed the answer. Both tests failed that way during a full-suite run, on a branch that touched none of this code; holding port 55005 on a clean checkout reproduces it exactly.

These tests are about the probe *order*, which needs no network, so the socket layer is faked - `setsockopt`, `bind`, `listen` and `close` are the only four things the function touches. Occupancy can now be stated rather than hoped for, which makes three previously unreachable behaviours testable: the full scan order rather than just its first element, a busy midpoint falling through to the next port, and a completely full range returning `(None, None)` instead of raising. Reverting the function to its original downward-from-`DCC_PORT_END` bug kills 4 of the 6, and all 6 pass with every port in the range held by another process. Real binding is still covered end to end by `PassiveOfferEndToEndTests` in the same file.

Fourth instance of an environment-dependent precondition being asserted rather than probed, after the loopback address, the console code page and `MAX_PATH`.

### 🎨 THEME is a choice on the settings page, not a text box
The dashboard offered `THEME` as free text: an operator had to already know and correctly spell one of the five preset names (`classic`, `midnight`, `forest`, `orchid`, `plain`), with nothing on the page to discover them. `LIST_FORMAT` solved the identical problem for `"txt"`/`"zip"`/`"rar"` via `settings_file.CHOICES`, which the page renders as a dropdown instead of a text input - `THEME` was simply never added to it. A typo also used to save successfully and only surface later as a console print from `theme.theme_name()`'s own fallback; it is now refused at save time, with the reason, the same as an unrecognised `LIST_FORMAT` already was.

`theme.THEMES` is not imported to build the new tuple: `theme.py` imports `defaults`, and `defaults.py` imports `settings_file` at module scope, so `import theme` here closes that into a cycle - verified by trying it, and it fails depending on which of the two modules a test or entry point happens to import first, not consistently. Named directly instead, same as `LIST_FORMAT`'s own tuple; a test pins it against `theme.THEMES` so the two cannot drift silently if a preset is ever added or renamed.

## 🟩 v1.10.0 (2026-09-01) - "General Availability"
RC4 shipped a full changelog entry below; everything after it did not. **66 PRs merged into `beta` over the five weeks since RC4** without a single one getting its own entry here - this release closes that gap with one condensed summary, grouped by theme rather than narrated PR-by-PR. See each PR's own description on GitHub for the full story behind any one line below.

No code changed to make this GA - it is RC4 plus everything below, judged stable. `SCRIPT_VERSION` drops the `-RC4` suffix. 1838 tests, all green on Linux and Windows CI.

### 🔒 The #162 security & reliability audit
An end-to-end adversarial review of the daemon found 32 issues, worked through in clusters across #166, #168, #169, #171, #172, #173, #174, #175, #176, #179, #180, #182 and #183: `!rehash`'s restore path detaching `config` from `runtime.py`; unanchored PRIVMSG/NOTICE parsing and hostmask bans that never actually matched anything; every runtime fallback that could disagree with `config.py`; the `flac-serv-*` side files renamed with a migration so no existing deployment lost its published size/byte-count; the `update_list.py` and `dcc.py` clusters (findings #4, #6, #7, #8, #15, #16, #21, #23, #32); config/docs drift (#18, #19, #20, #25); a ban pattern (`*!*@*`) that, after #168 made hostmask bans work at all, could ban an entire channel; an unbounded bulk fetch-enqueue with no way to cancel a pending row; and non-ASCII (Greek/Cyrillic/CJK) filenames that could be silently corrupted on read or overflow the DCC SEND handshake on send. #177 tracks the one finding (#30 - unbounded `user_requests`/`muted_until` growth) still open after this release.

### 🌐 Web dashboard
Grew from the RC4 feature line into a real second interface: a Settings page (#141, #142, #144) that writes `settings.conf` (#136) instead of only reading it, a Stats page (#147, #151, #159), a light theme (#137), login required on every route (#131), per-file downloads and folder-as-`.rar` fetch from the File Lists view (#146, #149, #150), the fetched-lists registry surviving a restart (#145), and the nav/title/columns staying visible while scrolling (#163). `WEBUI_ENABLED` now fails closed rather than open when unset (#116), and a test pins that importing `webserver.py` alone can never drag in the running daemon (#132).

### 📡 List distribution & the wider network
The master list can now be handed out as `.txt`, `.zip` or `.rar` (#155); live transfer speed is readable outside the advert loop, feeding the dashboard (#134); the daemon parses other bots' channel adverts, so it knows who else is serving files (#135); an operator can turn `!rar` off entirely (#140); and Verify List's overstated duplicate-count claim, plus a `resolve_list_folder()` bug that could drop the library's own base path, are both fixed (#130).

### 🛡️ Correctness & concurrency
Several shared-state races closed: `config.channel_users` against concurrent JOIN/PART/QUIT/353 (#123), `config.fetch_queue` read under the same lock its writers use (#124), and a wrong-lock bug in `announce.py` alongside `dcc_fetch._fetch_lock()`'s per-call fallback (#125). Elsewhere: temp-archive cleanup keys off the configured directory instead of a literal string (#122), the master-list folder-prefix logic was consolidated into one place instead of several that could disagree (#121), a duplicate filename now resolves using a search result's own `::INFO::` size hint (#128), square brackets survive into packed `.rar` names so AutoQ can match completion (#129), a missing ban file no longer fails silently under the wrong working directory (#120), and two stranger-sent channel lines that could hang the daemon in a reconnect loop are refused (#161).

### 🧹 Setup, tooling & smaller fixes
One shared setup-check module replaces two hand-maintained copies that had already drifted twice (#117), CI now installs Flask so the dashboard's own HTTP routes actually run under test (#126), two numeric settings that failed silently are now caught by `check-setup.py` (#127), the advert's Record figure (stuck at `0k/s` since the feature shipped) and its day-boundary figures are both fixed (#119, #157), an unclosed CSS rule that silently disabled the whole File Lists folder view is fixed (#118), `@<nick>-help` answers a stranger asking what the bot is (#154), `@<nick>-stats`/`@<nick>-top` answer in private (#159), the mIRC colour palette is defined once instead of eight times (#160), the last Swedish log lines - and the gap in the language guard that let them stay (a docstring the word-scan couldn't see into) - are fixed (#148, #158), and `README.md` no longer points at Windows scripts that were not actually on `beta` yet (#115).

### 🧰 Groundwork for #170
`settings_file.REQUIRED` and the `CUSTOM_THEME` flattening landed (#178) - the first phase of #170's RFC (`defaults.py`/`admin_config.py`, a guided first-run `setup.py`, and the rest of the mandatory-settings design) is still in progress on a separate branch and is not part of this release.

## 🟦 v1.10.0-RC4 (2026-08-29) - "The Web Dashboard Release"
The `beta-web` feature line - a full web dashboard plus a new cross-bot fetch capability, developed as its own branch since RC3 - is merged into `beta`. Everything below either shipped as part of that branch before the merge, or was built directly on `beta` afterward. 995 tests total, all green on Linux and Windows CI.

### 🚀 New features, merged from `beta-web`
- **Web dashboard (`webserver.py`, `web/`):** A dashboard served alongside the IRC daemon in the same process - Search, Queue, and File Lists views over a small Flask app. It is **not** read-only: three routes mutate state (broadcast search, file fetch, list fetch), and **no route has authentication** - LAN-only by design, see the warning blocks in `webserver.py`. Flask is an **optional** dependency; the daemon and CI never require it, and the dashboard silently disables itself with one log line if it's missing. `WEBUI_ENABLED` ships **off** by default (matching `adminchat.py`'s own `ADMIN_HOSTMASKS = []` opt-in pattern) - real values live in `local_config.py`, never as a tracked default.
- **Cross-bot search broadcast:** The dashboard can send a real `@find <term>` into a channel and collect PM/NOTICE replies from *other* bots for a 30-second window - something the daemon had never done before (it only ever answered its own `@find` requests). A best-effort `!<bot> <filename>` extraction turns a recognisable reply into a one-click download.
- **Cross-bot file fetch (`dcc_fetch.py`):** The daemon can now **receive** files, not just send them - a genuinely new capability. Handles both active (dial out) and passive/reverse (`port 0` + token, mirroring `adminchat.py`'s passive DCC CHAT) DCC SEND, with admission control that only ever acts on a fetch the daemon itself requested, a size cap enforced before connecting, and a dedicated storage directory kept separate from the served library.
- **Cross-bot list fetch (`list_fetch.py`):** Requests another bot's *entire* file list via the network's own `@<botnick>` convention, receives it as a zip (the same DCC-receive pipeline above), and extracts it under a strict safety boundary - zip-slip and zip-bomb protection, entry-count and declared-size caps, and (found afterwards, in production) a Windows-specific fix for all-dots path components that Win32's path parser silently collapses. Multiple bots' lists are kept and switchable in the dashboard, parsed on demand rather than held in memory, and paginated so an arbitrarily large fetched list can't be served in one unbounded response.
- **Download tab:** Bulk-paste any number of `!<bot> <filename>` lines at once; each becomes a tracked fetch-queue row (pending → offered → listening/receiving → complete/failed), throttled through its own `MAX_FETCH_SLOTS` cap, deliberately separate from `MAX_DCC_SLOTS` (an unrelated resource in the opposite direction).

### 🚀 New features, built on `beta` after the merge
- **Broadcast results grouped by bot, under a parsed heading (#106):** A broadcast for one album could draw 30+ interleaved replies from ten bots as a single flat list. Results now group under a heading built from each bot's own header line - match count, free/in-use slots, queue depth, server version - recognising two real bot families seen in production (OmenServe and the older SPQR script, which reports slots in the opposite sense). Depends on #105 below, which had to land first so a header line stopped being mistaken for a result.
- **File Lists grouped by folder (#107):** One collapsed heading per folder instead of one row per track - on the operator's own 36,208-file library, the difference between a scrollable wall of tracks and a browsable list of albums. Paging now advances by folder, never splitting one across a page boundary, with a row-count safety valve for a foreign bot's list that has no folder structure at all.
- **Verify List tool (#111):** A Tools view in the dashboard and a `verify` admin-console command, both reading the master list to find filenames that appear under more than one folder - the case where a request only ever reaches the first copy listed, and every later copy is unrequestable without anyone knowing.
- **Linux launcher and setup check (#112), `scripts/linux/`:** `start-dccore.sh` anchors the working directory before starting the daemon (every data path in `config.py` is relative, so a cron job or a systemd unit with no `WorkingDirectory` set would otherwise create an empty `data/` folder wherever it happened to run from) and refuses to start on a failed setup check. `check-setup.py` verifies the config without ever connecting to IRC - a missing music directory, or a nick/channel still carrying the upstream defaults (which would put a near-identical second bot into the production bot's live channels), both refuse to start; sharing a channel under the operator's own nick only warns, since that's an ordinary participant, not a clone. Mirrors the equivalent Windows tooling in `scripts/windows/`.

### 🐛 Bugs found and fixed before the merge, on `beta-web`
- **CRLF/CTCP injection, found and closed three separate times** in three different input channels feeding an outbound IRC line - the web-enqueue routes, the passive-DCC-offer reply, and (in a near-identical form using `\x01` rather than `\r`/`\n`) a gap between two independently-maintained validators. The third fix unified the two into one canonical check, on the theory that a bug recurring three times in one feature is a structural problem, not three unrelated slips.
- **Stored/attribute-injection XSS** in the dashboard's broadcast-results rendering, from building HTML attributes via string-concatenated `innerHTML` with attacker-reachable text - rebuilt using DOM APIs, which encode correctly by construction.
- **`!rehash` silently emptied the fetch queue and every fetched bot list**, because the runtime-state preservation list predates the feature that added those two containers. Fixed by deriving the check from `config.py` itself, so the next container someone adds fails a test instead of failing silently in production.
- **A `::INFO::` marker parser tuned only for this project's own two-space format** mis-captured the size and branding text other bots append after it as part of the "filename," breaking every cross-bot fetch of a name that came from a search-result line. Made whitespace-tolerant and marker-anchored, verified against four real, differently-formatted bots' production output.
- **Two list fetches for the same bot shared one extraction directory (#85).** `list_extract_dir()` keys on the bot nick alone and extraction opens by `rmtree`-ing that directory, so two fetches completing together deleted each other's files midway - and both still returned success, so nothing reported it. The stored record ended up labelled with one archive's name while holding the other archive's rows. The module lock now covers the whole extract -> parse -> store sequence rather than only the final write, which also bounds peak parse memory to one list instead of one per fetch slot.
- **Three exception classes escaped the zip-extraction guard (#86).** It caught `BadZipFile`, `ValueError` and `OSError`; a hand-crafted archive also raises `zlib.error` (corrupt deflate stream), `NotImplementedError` (a compression method with no decompressor) and `RuntimeError` (a member flagged encrypted), none of which subclass those. Each such archive has valid headers, so it passes the member-count, path-containment and size guards and fails only on read - reaching the fetch thread past a function documented "never raises".
- **A non-object JSON body returned HTTP 500 on two POST routes (#87).** `request.get_json(silent=True) or {}` only substitutes for a *falsy* result, so a truthy non-dict - an array, a string, a number - passed straight through to `.get()` and raised `AttributeError`. It is now coerced to an empty object, which is exactly what a missing body already produces, so it lands on the same 400 that every other bad input to those routes gets.
- **Windows: the new write paths ignored the 260-character path limit (#88).** `dcc.py` wraps every path it touches in `platform_compat.long_path()`; the two write paths added for cross-bot fetch did not - and neither name is ours, since a zip member name and an offered filename are both chosen by the remote bot and never truncated. A perfectly legal 240-character name refused the entire archive with "No such file or directory", for a file the daemon was itself trying to create. The cleanup paths are wrapped too: unwrapped, `os.path.exists()` answers False for such a path, so the debris from a failed long-named transfer was never removed and poisoned the next fetch from that bot.
- **A refused list archive displayed as "Complete", with a working Download button (#89).** The rejection reason was recorded on the row explicitly "for the dashboard" and served by `/api/fetch/status` - and the frontend never read it. An archive refused for path traversal was therefore indistinguishable from a list that fetched perfectly, and the only record of the attempt was a single line on stdout. This is the one that hid the others: a failed extraction, an escaped exception and a rejected archive all reached the operator as a cheerful "Complete".
- **A setting derived from another setting ignored `local_config.py` (#91).** Overrides are applied at the bottom of `config.py` on purpose, but `BROADCAST_SEARCH_CHANNEL` was derived from `CHANNEL` 264 lines above that. Its own comment promised it "defaults to the first entry of CHANNEL" - and it did, to the first entry of the *shipped* default. So an operator running on their own channel had a dashboard broadcast search send its `@find` into the shipped default channel rather than the operator's own. Derived values are now computed after overrides land, with a guard that fails on any future setting derived above that point.

### 🐛 Bugs found and fixed after the merge, on `beta`
- **A broadcast reply's fetch token had to start the line (#105).** The extraction searched anywhere in a captured reply, so a bot's own header line - which tells the user what to type, and so contains a `!` token too - matched as if it were a result, offering to fetch a file literally named "FILENAME To The Channel To Request...". Worse, ordinary chatter landing inside the listening window ("Thank You !!! I have now received...") matched at the `!!!` and offered a fetch from a bot that doesn't exist. Both would have sent a nonsense line into a live public channel on click. The token is now anchored to the start of the line - a real result always begins with it, a sentence mentioning one never does.
- **Pagination could hide most of a library (#107, found while building the folder-grouping feature above).** A page bounded by two limits (a folder count and a row ceiling) could return fewer folders than requested, and the pager advanced by the number it *asked for* rather than the number it *received* - silently skipping every folder in between. On a synthetic 1,000-folder library, this hid 795 of them. The payload now reports how many folders it actually returned, and the pager advances by that.
- **The dashboard's fetch routes didn't honour the fetch-disabled flag (#109).** `oserve.startup()` sets `fetch_feature_disabled` when the fetch storage directory can't be created, and the background dispatcher already checked it - but the two HTTP routes that *create* a fetch request never did, so the dashboard would accept a request, report it queued, and leave it `pending` forever with nothing said about why. Both routes now refuse with a clear 503 instead. The flag's default reading also changed from fail-open to fail-closed (a missing flag now means "disabled," not "enabled") - relevant to issue #100's design, since starting the dashboard earlier in boot (a goal of that work) turns "the flag is always set before anything can read it" into an ordinary race.
- **The master-list folder lookup was dead code, so duplicate filenames resolved by filesystem order (#110).** A `str.split()` call took the piece *before* the separator instead of after - every line in the master list starts with that separator, so the comparison was comparing an empty string against everything and never matched. The list-order lookup this was meant to be therefore never ran, and every request fell through to a raw filesystem walk: for a filename that exists under two folders, whichever one the filesystem happens to return first (not the one the published list names first) is what gets served. Fixed to read the right half of the split; a duplicate now resolves to the copy the operator's own list names first, matching what the requester actually saw.
- **A third round of Swedish comments a word-based scan still couldn't see (#98).** Sixteen more lines across six modules - two of which had already reached the generated `settings.conf.sample`, meaning an operator was shown Swedish in a file they're told to copy. The guard test's own word list is extended again, with its docstring now saying plainly what three rounds have shown: a list-based check raises the cost of reintroducing the problem, it does not prove there is none.

### 🧪 Tooling, in preparation for issue #100
Issue #100 (an operator's own filing) proposes making identity-critical settings (`NICKNAME`, `SERVER`, `CHANNEL`, `FILE_DIRECTORY`) mandatory rather than shipping a working default that lets a fresh install silently connect using someone else's identity. None of that behaviour has landed yet - this release only lays the groundwork two other changes would otherwise have broken silently:
- **A module named as a string has to be a module that exists (#103).** `!rehash`'s reload list, and every `sys.modules.get("name")` lookup elsewhere, refer to a module by a bare string - a name that stops resolving after a rename fails *silently*: `!rehash` skips it and reports success anyway, a lookup returns `None` past an `if mod:` guard. A new scan checks all ten such names across 61 sites actually resolve to real modules, specifically so a future rename (the eventual `config.py` → `defaults.py` this issue also proposes) fails loudly instead of quietly breaking `!rehash`.
- **Settings now declare their own type (#108).** A setting's default value used to be its type declaration - `MAX_DCC_SLOTS = 3` reads as an int because `3` is one - which stops working the moment a setting is legitimately unset (`None` carries no type to infer from). Every setting in `config.py` is now annotated (`MAX_DCC_SLOTS: int = 3`), and `settings_file.coerce()` reads the declared type directly. No value changed and no behaviour changed - this only removes the trap issue #100's mandatory-settings design would otherwise have hit.
- **The AST tooling that reads `config.py` was blind to the annotated form (#101, #102):** three checks (the settings-sample generator, and two guards asserting nothing rebinds a runtime container or derives a setting above its override point) matched the older, unannotated assignment shape only. Two of the three would have failed *silently* - reporting a clean, guarded file while no longer checking anything - the moment `#108`'s annotations landed. All three now handle both forms, verified against synthetic sources so the check itself can't quietly stop matching again.

---
## 🟦 v1.10.0-RC3 (2026-08-28) - "The English & Reliability Release"
### 🌍 Full English translation
Every module's comments and log strings are now English, closing the loop on a translation effort that started as an accent scan and finished as a permanent guard:
- **Nine modules translated outright** (`stats_mgr.py`, `list.py`, `queue_mgr.py`, `security.py`, `db.py`, `config.py`, `oserve.py`, `announce.py`, `update_list.py`), followed by the three largest and busiest (`dcc.py`, `irc.py`, `commands.py`) - 273 lines of comments, docstrings and log text, none of it control flow, identifiers, or anything a test or the protocol actually parses.
- **27 lines an accent scan cannot see, found and translated anyway.** Searching for `å`/`ä`/`ö` misses Swedish spelled without them entirely - `"Kunde inte skicka JOIN"`, `"Startar tidtagaruret"`. Eighteen were found by searching for Swedish *words* instead; the other nine were found by a new guard test written *after* the author was convinced the list was complete - which is the whole argument for having it.
- **Two permanent guards added** (`tests/test_source_language.py`): one asserts zero non-ASCII characters anywhere in the daemon's own source, the other asserts zero Swedish words, so a future feature branch (as happened once already, mid-effort) can't reintroduce either without failing CI. `tests/` is deliberately exempt - its fixtures include non-ASCII filenames on purpose, to prove those work.

### 🐛 Bug fixes
- **Non-Western console code pages could kill the daemon:** `print()` encodes with whatever code page the attached stream has - cp1253, cp1251, and cp932 can't represent `å`/`ä`/`ö`, and an uncaught `UnicodeEncodeError` took down the thread that printed. `platform_compat.install_console_encoding_guard()` now pins `encoding="utf-8", errors="replace"` on every stream, so a character can never take the process down while the underlying strings finish becoming English.
- **Redirected logs lost everything buffered when the process died:** Python block-buffers stdout whenever it isn't a real console - a log file, a pipe, a service host. Measured while deploying on Windows: 75 seconds of startup logging produced *one* line on disk, and force-killing the process lost the rest, including the channel JOIN and the advert. The same guard now also sets `line_buffering=True`, so a printed line is on disk before the next one starts.
- **Concurrent transfers could silently lose stats, and midnight could double-rotate:** `stats.txt`'s read-modify-write wasn't atomic under concurrent completions, so simultaneous transfers could overwrite each other's counts, and a rotation racing the clock across midnight could wipe yesterday's already-rotated totals. Now one lock covers the whole read-modify-write and the rotation check together.
- **Windows library paths over 260 characters were silently unservable:** `platform_compat.long_path()` existed for exactly this and was called by nothing. Measured against the operator's real 47,420-file library: the longest path is 259 characters - one character of headroom - and addressing the share by UNC (as any unattended Windows service or scheduled task must, since mapped drives are per-session) adds 17 more, putting 49 real files over the limit. The failure was an ordinary `FileNotFoundError` on a file plainly present. Wired into every file-access site in `dcc.py`, `update_list.py`, and the library scanner.
- **The master list's folder-header rule never matched the folder it framed:** the `====` rule above and below each header was a fixed 53 characters; measured against the real library, all 4,107 headers ran 54-136 characters, so the framing was always ragged. Now drawn to the exact width of the line it wraps.
- **A memory leak in flood/ban notification tracking:** `security._ban_notified` was a plain `set()` that only ever grew - its two removal paths (a timed ban expiring, or the nick later seen clean) are both unreachable for a nick matched by a *wildcard* pattern in `hard_bans.txt`, so an operator using one, plus anyone cycling nicks against it, grew the set without limit for the life of the process. Fixed with a time-expiring structure rather than a size-capped one: a naive LRU would let an attacker cycling more nicks than the cap evict a recently-notified nick and earn it a fresh notice - and every notice costs 0.5 seconds of read-thread stall, turning a slow leak into the exact network freeze the notification exists to prevent.
- **The operator's own address had been quoted into code, docs and tests:** three field reports were pasted verbatim while the DCC transport bugs were being fixed, and two of the log lines carried a real, routable address - one of them in `adminchat.py` itself rather than only in documentation, because a DCC offer encodes an address as a long and it was copied straight out of the report. A third value, invented for a test, turned out to be routable and to belong to somebody. All of them now sit in TEST-NET-3 (`203.0.113.0/24`), reserved by RFC 5737 for documentation and guaranteed never to be routed - which is the convention `tests/support.py` already followed.

### 🧪 Test suite & tooling
- **A CI test was measuring machine speed, not the code:** the log-buffering regression test killed a child process after a fixed 1.2 seconds and asserted a minimum line count - which went red on a loaded Windows runner that simply hadn't gotten as far yet. Replaced with a signal file the child writes when done printing, so the parent only ever kills it after every line is confirmed on disk; the assertion is now exact, not a guess about timing.
- **`requirements.txt` and `requirements-web.txt` added**, and the "no third-party dependencies" claim is checked rather than assumed - the daemon's only optional dependency is Flask, for the (opt-in) web dashboard.
- **CI now runs on every pull request:** the `pull_request` trigger named its branches explicitly, and GitHub evaluates that trigger from the workflow file on the *base* branch - so a PR against a branch not yet in the list could not run CI, **including the PR that would add it**. One PR hit exactly that and had to be verified by hand. Now `branches: ['**']`. The same change stopped two checks from reporting the environment they ran in as a defect in the code.
- **The boot sequence became callable, so CI can cover it:** `oserve.py`'s startup lived inline under `if __name__ == "__main__":`, so it could only be executed by starting the real bot - which connects to Undernet and joins live channels. Every module was imported and unit tested while the boot path itself had no automated coverage on either platform. Split into `startup()` and `run_forever()`, with no logic changed.
- **One import list, derived from the filesystem instead of written twice:** the "every module imports cleanly" check was a hand-written module list in both the CI workflow and `scripts/preflight.py`, and the two had drifted from each other and from the project - 11 modules in one, 12 in the other, 14 actually present.
- 528 tests total, all green on Linux and Windows CI.

---
## 🟦 v1.10.0-RC2 (2026-08-26) - "The Admin Console Release"
### 🚀 New features
- **🔐 Authenticated admin console over DCC CHAT (`adminchat.py`):** Closes the "known open item" from v1.10.0-RC1 - `is_admin()`'s nick-based gate, which anyone could inherit by taking the admin nick while the real operator was offline. The console instead requires two independent factors: the operator's Undernet services login, proved by the `+x` host only the IRC server can issue, and a PBKDF2-SHA256-hashed password compared in constant time. An unrecognised host gets no reply at all - not even a banner - so a stranger learns nothing about whether their guess was close. Full setup guide in `docs/ADMIN-CONSOLE.md`.
- **Connection fallback (`ADMIN_CHAT_MODE`):** The console normally dials the connecting client the way iroffer's non-passive DCC does, opening no new inbound port. When that dial can't succeed - a VPN exit address with nothing forwarded, a router that drops rather than rejects - it now falls back to listening on the bot's own DCC port range instead, the same range every DCC SEND already uses. `"auto"` (default) tries both; `"listen"` or `"connect"` pin one behaviour.
- **Passive DCC CHAT support:** The client-initiated passive form (`DCC CHAT chat <ip> 0 <token>`) is now parsed and answered correctly, token included - previously misread as a malformed active offer and discarded.
- **Full command surface (phase 2):** Read-only (`status`, `queue [nick]`, `slots`, `bans`, `uptime`, `version`) and action commands (`ban`, `unban`, `clearqueue`, `rehash`, `update`) all run from the authenticated session, reaching the same handlers channel commands always used - a console session satisfies a strictly stronger authorisation check than the nick comparison, never a weaker one. `ADMIN_CHANNEL_COMMANDS` (default on) keeps the old `!`-prefixed channel commands working side by side until the console has proved itself.
- **Debug routing (phase 3):** `DEBUG_TO_CHANNEL` and `DEBUG_TO_CONSOLE` independently control whether the daemon's runtime commentary goes to the public debug channel, an attached console, or both (both on by default). Neither switch can silently lose a line - if nothing is listening on either destination, `send_debug` falls back to stdout, so the LXC console and the journal always have it.
- **`local_config.py.sample`:** A checked-in template listing every setting actually meant to live in the gitignored `local_config.py` (`ADMIN_HOSTMASKS`, `ADMIN_PASSWORD_HASH`, `ADMIN_CHAT_MODE`, `ADMIN_CHANNEL_COMMANDS`, `DEBUG_TO_CHANNEL`, `DEBUG_TO_CONSOLE`), so a fresh deployment doesn't have to reverse-engineer it from `config.py`'s comments.

### 🐛 Bug fixes
- **`0.0.0.0` dial-to-self:** On Linux, `connect()` to `0.0.0.0` means "this host" - so when a client's DCC offer carried an unresolved `0.0.0.0` (a common mIRC symptom when its IP lookup fails), the daemon dialled its *own* port, found nothing listening, and logged the refusal as though the client had rejected it. An unspecified, multicast, or reserved address now triggers the listen-instead fallback rather than a dead end.
- **List side-files didn't roll back with the list:** `generate_master_list()` already kept the previous master list on a failed scan (an NFS mount going away, not an empty library) rather than publishing zero files - but the human-readable size and raw byte count published alongside it were written to their final names *before* that guard ran, so a vanished mount kept the file count while overwriting the advert's size with `0B`. Both are now published atomically together with the lists. A truncated one of the two no longer takes the other down with it - each is read in its own `try`, and an empty size file falls back to `"0B"` instead of erroring the whole read.
- **Control characters could corrupt the master list:** POSIX filenames may legally contain a newline (Windows cannot create such a file, which is why cross-platform CI caught this on the Linux jobs only) - written straight into the list, it split one request entry into two lines, corrupting the file from that point down. Control characters are now flattened to spaces; such a file was already unrequestable anyway, since the request parser also splits on whitespace.

### 🧪 Test suite
439 tests total, all green on both Linux and Windows. Coverage added this release: the admin console's entire authentication and transport path (real loopback-socket end-to-end tests, not mocks) and the master-list scanner (`generate_master_list()`), previously the last untested path in a module that deletes files.

---
## 🟦 v1.10.0-RC1 (2026-08-25) - "The Platform & Forgery-Hardening Release"
### 🚀 New features
- **🪟 Windows support (`platform_compat.py`):** Isolated every genuine Linux/Windows difference into one new module - the DCC listener's socket option, the rar binary lookup, long-path handling, and TCP keepalive tuning. Every function is a no-op or identity on Linux, so production behaviour on Linux is unchanged; the daemon now also runs on Windows, verified by CI on both platforms.
- **⚙️ Per-machine config overrides (`local_config.py`):** `config.py` now optionally imports a gitignored `local_config.py` so one machine can override paths, nickname, or channels without editing a tracked file or showing up as a deployment diff.
- **🧪 Preflight script (`scripts/preflight.py`):** Mirrors the CI workflow locally - imports, compileall, the full suite, then a second run with host tooling hidden (PATH stripped) and a floor on the collected test count, so a host-dependent test or a silently-emptied test file gets caught before pushing instead of by CI.

### 🐛 Bug fixes & security hardening
- **🛡️ DCC listener hijack risk (Linux vs. Windows `SO_REUSEADDR`):** The socket option that lets Linux quickly reuse a port in `TIME_WAIT` means the opposite on Windows - it lets a *different process* bind the same port and steal the incoming connection. `platform_compat.prepare_listener()` now picks the correct option per platform.
- **🔒 Server-numeric forgery (513/353/352/001/376):** Four `irc.py` handlers matched server numerics with a bare substring test on the raw line, so ordinary channel text could trigger them - including an unpaced raw `PONG` from a forged `513`, enough to flood-disconnect the bot from a single pasted message. Replaced with anchored matching (`is_server_numeric`) that requires the code to sit in the actual command position.
- **🔒 User-event forgery (JOIN/PART/QUIT/NICK/433):** Same defect on the membership side - a search like `@find QUIT PLAYING GAMES` matched the QUIT handler's substring test and froze that user's queue for no reason. Replaced with anchored matching (`is_user_event` / `event_source_nick`) so the source nick can only come from the line's prefix, never its body.
- **🗑️ `@<nick>-remove` orphaned temp archives:** Of the four code paths that clear a user's queue, the one users actually type was the only one that didn't delete the temporary `.rar` files those queue rows pointed at. Now routed through `dcc.discard_orphaned_temp_archives()`, shared with `!clearqueue`.
- **🔓 `!unban` could truncate `hard_bans.txt` and fail open:** It rewrote the file in place; a crash or full disk mid-write could leave it truncated, and a truncated-but-readable file is indistinguishable from an empty one - so every hard-banned user would be silently admitted. Now an atomic read-modify-write under a shared disk lock via `db.add_hard_ban()` / `db.remove_hard_ban()`.
- **🔗 `!ban` could glue two ban patterns together:** A bare append with no trailing-newline check could weld a new pattern onto the previous line on a hand-edited file, silently unbanning both. Fixed by the same atomic helper.
- **📉 `db.get_speed_record()` used a hardcoded path** instead of the `SPEED_RECORD_FILE` constant `save_speed_record()` already used - harmless from the repo root, but silently stale if the daemon is ever started from elsewhere.
- **🚦 Section A of `check_queue_and_send()` ignored `MAX_DCC_SLOTS`:** only Section B checked capacity before dispatching; repeated JOIN/353 thaws could push `active_transfers` past the configured slot limit.
- **🌊 Flood gate (`is_bot_command`) missed most of the dispatch chain:** only 4 of roughly 11 real command paths were metered - `-que`/`-remove`, both CTCP variants, `!list`, `!debugnames`, and `!ping` had no rate limit at all.

### 🧪 Test suite
Went from 168 to 250+ tests across this release, all evaluating the actual guard conditions read out of the source rather than grepping for text - the pattern established for exactly this class of forgeable-handler bug. Every fix above shipped with mutation testing proving the old, broken condition turns the suite red.

### ⚠️ Known open item
`is_admin()` is still nick-based with no `ident@host` verification - an Undernet nick isn't owned without services auth, so anyone taking the admin nick while the real admin is offline gains every admin command, including `!clearqueue`. Closing this needs `irc.py`'s PRIVMSG regex to capture the hostmask (currently discarded) plus a config-format decision. Tracked as follow-up work, not fixed in this release.

---
## 🟦 v1.9.0-RC1 (2026-08-15) - "The Gold & Audio Handshake Release"
### 🚀 New features
- **🛡️ Apostrophes survive packing (`Single Quote Filter`):** `inline_rar_packer` in `dcc.py` now derives the archive name with `os.path.basename`. A source folder containing an apostrophe (`'`) keeps it in the finished `.rar` name (e.g. `A_Winter's_Tale_(1995).rar`) instead of having it replaced by an underscore, so the packed file is named the same as the folder that was requested.
- **💽 Multidisc sets are listed differently in each list (`update_list.py`):** The list generator now treats the two text lists differently. The plain list (`.txt`) shows full subfolders such as `\Digital Media 1\` and `\CD2\` to preserve the track structure, while the album list (`-RAR-.txt`) is stripped as it is written: multidisc suffixes are cut so a whole box set appears on a single line (e.g. `Mission Underground (2026)\`).
- **🗜️ Whole box sets pack automatically:** When the parent line of a multidisc set is requested with `!rar`, the send path in `dcc.py` pulls every subfolder into one `.rar` archive on the local cache disk.
- **⚙️ Cold start and auto-wake:** A thread-safe trigger at the end of the startup chain in `oserve.py`. Once the bot has settled (five seconds after joining), `dcc_queue.txt` is scanned automatically and transfers resume with no operator command needed.

### 🐛 Bug fixes & optimisations
- **🧬 One announcement per send, not two:** All end-of-send work - disk cleanup, popping the queue, and the channel advert - moved into an isolated `finally:` block in `dcc.py`. The channel announcement now runs exactly once per send, instead of being duplicated by a second thread.
- **🏎️ `Complete` instead of `incomplete` in mIRC:** Restored the 1.5-second pause at the end of a send. It gives the receiving client the window it needs to flush its network buffer, so a transfer reports **Success/Complete** rather than a spurious `incomplete` on a fast link.
- **🌈 Case-insensitive queue bypass:** The channel check is no longer case-sensitive (`#channel` vs `#Channel`). System triggers (`system_next_trigger_fallback`) now also open the bypass paths, so the queue cannot stall silently at startup or after a reload.
- **🧼 Cleaned-up database counter:** The statistics block no longer carries a local `import db` that shadowed the module-level one and raised `UnboundLocalError`. The terminal output prints one summary line - files sent, written live to disk - instead of dumping a raw array.
- **🎛️ Lock-clearing rehash (`!rehash`):** The `!rehash` handler in `commands.py` takes the live network socket from the running instance via `sys.modules`. It also clears stale packer and per-user locks (`config.rar_inprogress = False`) out of memory, so a pack that died no longer leaves the queue wedged.
- **📊 Calibrated channel advert:** The slots figure in `announce.py` now divides total throughput by the number of active downloads, giving a real per-slot average instead of the implausible speeds (tens of millions of MB/s) it used to advertise.

---
## 🟦 v1.5.0-BETA (2026-08-10) - "The RAM Dictionary Queue & Inline RAR Packer Update"
### 🚀 New features
- **📦 In-memory dictionary queue (`dcc_queue.txt`):** The queue was rewritten from flat text strings to a structured JSON/dictionary form held in memory. Each entry now carries real metadata: nick, channel, absolute path, file type, and the `is_unpacked_rar_folder` and `is_temporary_zip` flags.
- **⚡ Inline RAR packer (`inline_rar_packer`):** A thread-safe compression step in `dcc.py` built on `subprocess.run(["rar", "a", ...])`. The bot recognises a requested album folder, builds a temporary `.rar` archive in the local cache directory (`data/tmp_zips/`), and streams that.
- **🛡️ The shared library is never written to (`RO-Protection`):** The RAR process is given a working directory (`-w`) on the cache disk, so it never attempts to write temporary data into the music folders - which are typically mounted read-only.

### 🐛 Bug fixes & optimisations
- **🔒 In-memory locks against thread collisions (`rar_inprogress`):** Added the global locks `config.rar_inprogress` and `config.user_processing_lock`. The queue packs and sends one folder at a time, which removed the CPU and disk contention that concurrent packs were causing.
- **🧹 Automatic cache cleanup:** The end of a send checks whether a temporary `.rar` is still needed by another active slot or queued request. If nothing else refers to it, it is removed with `os.remove()`.

---

## 🟦 v1.4.5-BETA (2026-07-31) - "The Multi-Character Regex Sanitizer Update"
### 🚀 New features
- **🧹 Search terms are sanitised (`@find`):** `re.sub(r'[-*_.]', ' ', search_term)` in `list.py`'s search function. Asterisks (`*`), underscores (`_`), dots (`.`) and hyphens (`-`) become spaces before the search terms are split, so a query typed with separators (e.g. `metallica*red*alert`) still matches.

---
## 🟦 v1.4.4-BETA (2026-07-30) - "The External Indexer & Micro-Read Update"
### 🚀 New features
- **🎛️ List rebuild from IRC (`!update`):** An admin command in `commands.py` that runs the external `update_list.py` script in a background thread via `subprocess.run`, so the library can be re-indexed without shell access to the host.
- **⚡ Micro-read optimisation:** `get_count_from_list` reads only the first line of the master list (`f.readline()`) and matches it against the pattern `List of X Files` to recover the file count, without reading or walking the rest of the file.
- **🧮 Before-and-after comparison:** The bot records the file count before and after the script runs, so it can report exactly how many files were added since the previous scan.

### 🐛 Bug fixes & optimisations
- **🧟 No more zombie processes:** Moving from asynchronous `Popen` to `subprocess.run` means the kernel reaps the child as soon as the scan finishes, instead of leaving a `defunct` entry behind in the process table.
- **🧬 Circular name-conflict fix:** The import of `list` was moved inside the function and taken from `sys.modules.get('list')`, which removed a silent failure caused by the module `list.py` shadowing Python's built-in `list`.

---

## 🟦 v1.4.3-BETA (2026-07-30) - "The Clean Config & Security Sync Update"
### 🚀 New features
- **🧼 Import-free `config.py`:** The central configuration file was cleared of functional code - hidden `import os` statements and dynamic `BASE_DIR` computation. The paths to `stats.txt`, `bans.txt` and `hard_bans.txt` are now plain, normalised strings.
- **🛡️ Live anti-flood and mute tracking:** `announce.send_debug` is now called from `is_flooding` in `security.py`. A colour-coded purple **`[TEMPBAN]`** notice goes out as soon as a user exceeds the rate limit; their queue is cleared, and an escalation to a day-long ban until midnight is logged.
- **🚨 Central security reporting:** `check_user_status` in `security.py` uses the same direct socket send. Dark red **`[HARDBAN]`** notices are posted to the debug channel the moment a nick matching a permanent wildcard starts hammering the search commands.

### 🐛 Bug fixes & optimisations
- **⛓️ Thread-safe path handling:** The admin commands `!ban` and `!unban` in `commands.py` read their paths from the config strings, which removed a `NameError` at boot and makes the threads resolve correctly into the `data/` subdirectory.

---

## 🟦 v1.4.2-BETA (2026-07-30) - "The Hard Ban & Admin Category Update"
### 🚀 New features
- **🛡️ Permanent wildcard blocks (`hard_bans.txt`):** A separate file under `data/` for fixed spambot patterns (e.g. `spammer_*`). It is exempt from the automatic midnight clearing that applies to ordinary flood bans.
- **🛠️ Admin commands (`!ban` / `!unban`):** Two commands in `commands.py` that write to and clean up the permanent ban file from IRC, with no shell access and no manual `!rehash` needed.
- **🎨 Dedicated security colour blocks:** `announce.py` gained two mIRC labels of its own: dark red **`[HARDBAN]`** for permanent wildcards and purple **`[TEMPBAN]`** for temporary day bans, so the two are distinguishable at a glance on the debug line.
- **🧭 Path and case handling:** `os.path.normpath` and absolute paths derived from `BASE_DIR` make the threaded file commands resolve into `data/` wherever the bot is started from, and lowercase normalisation (`.lower()`) is applied through the whole chain to close the case-sensitivity gaps that let a spambot slip past a ban.

---

## 🟦 v1.4.1-BETA (2026-07-30) - "The Intelligent Wildcard Search Update"
### 🚀 New features
- **🔍 Word-by-word wildcard search (`@find`):** `execute_search` in `list.py` was rewritten as a word-by-word scan. The search string is split into individual words with loose hyphens removed, and matching is order-independent: every word has to appear on the line, in any order (so `metallica red alert` and `red alert metallica` both match).

### 🐛 Bug fixes & optimisations
- **🧹 Disk space on the host:** A manual clean-out of cached package index files (`/var/cache/apt/`), freeing over 270 MB on the system disk ahead of setting the repository up. Host maintenance rather than a code change, recorded here because it is what the release day actually consisted of.

---

## 🟦 v1.4.0-BETA (2026-07-30) - "The Live Rehash & Channel Sync Update"
### 🚀 New features
- **🔄 Live module rehash (`!rehash`):** `importlib.reload()` in `commands.py` reloads every core module in place, with no need to stop or kill the process.
- **🌐 Automatic channel sync:** A rehash compares the configured channel list against the joined one, sending `JOIN` for channels added to `config.py` and `PART` for channels removed from it.
- **⚡ Readable latency measurement (`!ping`):** An admin command in `commands.py` that reports the round-trip time to the IRC server in seconds to three decimal places (e.g. `0.129 sec`), with the colour codes stripped out so nothing distorts the reply.
- **🎯 Colour and thread handling:** Raw mIRC colour codes were removed from the strings in `commands.py` to stop them bleeding into each other on screen, and the advert timer was synchronised so a rehash makes it wait its full five minutes instead of starting a second, competing thread.

### 🐛 Bug fixes & optimisations
- **🎛️ Direct socket send:** `send_debug` in `announce.py` was rewritten to use `irc_sock.send` directly, so logs, latency replies and rehash confirmations bypass the internal 15-second message queue and appear immediately.
- **🧬 `PONG` handled before the PRIVMSG filters:** `irc.py` gained a dedicated `PONG` branch at the top of the main loop, so the latency reply is caught before anything else can consume it.

---

## 🟥 v1.3.0-BETA (2026-07-28) - "The Debug & Theme Sync Update"
### 🚀 New features
- **🛠️ Debug channel:** An automatic gateway that sends timestamped, colour-coded CLI logs live to a dedicated debug channel on IRC.
- **🏎️ Express logging:** `send_debug` was switched to `is_vip=True` so system logs go out immediately, without waiting behind the normal queue.
- **🏷️ Category tags in the debug log:** Colour-coded labels down the left-hand side: `[SENT]` (green), `[PART]` (red), `[QUIT]` (purple) and `[JOIN]` (cyan), framed by solid colour blocks.

### 🐛 Bug fixes & optimisations
- **📦 No more black boxes:** The spacing in `send_debug` was restructured and a fixed `{BG_TEXT_BOX}` (white background) baked in, which stops mIRC drawing black cache boxes around the text.
- **📋 Text formatting:** `announce.py` casts values with `str()` so integers coming out of the database are not read as raw mIRC colour numbers.
- **🧩 Name-conflict fix:** `isinstance(stats, list)` was replaced with a plain `type()` check, removing the conflict with the file-sharing module `list.py`.

---

## 🟨 v1.2.0-BETA (2026-07-27) - "The Database & Index Sync"
### 🚀 New features
- **📉 Seven-column live statistics:** Total files sent, total bytes sent, and today's and yesterday's counters are all incremented on every completed transfer.
- **💾 Forced disk flush (`fsync`):** `db.save_advanced_stats` calls `f.flush()` and `os.fsync()` so the change reaches the disk rather than sitting in the operating system's write buffer.

### 🐛 Bug fixes & optimisations
- **🔢 Index synchronisation:** The database indices for yesterday and today in `announce.py` were corrected to match the seven-column format of `stats.txt`. They had been reading the list date and crashing.
- **🧮 Date-safe arithmetic:** A `ValueError` in `dcc.py` was fixed by isolating the list date (index 6) as a plain string, so the arithmetic loop no longer tries to turn a hyphenated date into an integer.

---

## 🟩 v1.1.0-BETA (2026-07-26) - "The VIP Express & Architecture Update"
### 🚀 New features
- **🚅 Isolated VIP send path:** A new `is_vip=False` flag in `oserve.queue_message`. Commands passing `is_vip=True` go straight past the normal flood-protection queue.
- **⛓️ Chained command parser:** The command parser in `irc.py` was rebuilt as a closed `if / elif` chain, and `continue` was changed to `return` in the CTCP filter, which removed the duplicated replies appearing in channels.

### 🐛 Bug fixes & optimisations
- **🧬 Circular import fix:** The top-level import in `commands.py` was replaced with a live lookup via `sys.modules.get('oserve')`, which stops the bot deadlocking at boot.
- **🧼 Cache cleanup:** Old duplicate definitions of `def queue_message` were removed from `oserve.py`, where they had been overriding the current one at startup.
