# DCCore Version Updates & Project Log 📝

All version changes, optimizations, and bug fixes made over time in the DCCore project are logged here.

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
