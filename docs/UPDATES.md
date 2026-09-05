# DCCore Version Updates & Project Log 📝

All version changes, optimizations, and bug fixes made over time in the DCCore project are logged here.

## 🟨 Unreleased

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
Found on a real install. A nickname longer than the server's `NICKLEN` is not refused - it is silently **shortened**. Undernet allows 12, so `Samoth-DCCore` registered as `Samoth-DCCor` and the daemon never noticed: it logged *"CURRENT_NICK settled as: Samoth-DCCore"*, advertised `@Samoth-DCCore` - a nick nobody could PM or DCC - and went on believing a name it had never had. `433` was handled because it announces itself; truncation does not, so nothing failed and nothing caught it.

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
