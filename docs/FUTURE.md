# Roadmap

What DCCore does today, and what it does not do yet.

**How to use this file:** everything under *Implemented* is in `main` and working. Everything under *Planned* is not, whatever a comment or an old changelog entry might imply. When something is built it moves up, in the same commit that builds it — a roadmap that is edited afterwards is a roadmap nobody trusts.

---

## Implemented

### Serving files

- **DCC SEND over IRC**, with a per-user and global queue, configurable slot limits, and a DCC port range you control.
- **Album packing** — `!rar <folder>` builds an archive on demand and cleans it up afterwards.
- **Freeze box** — a user who parts or quits keeps their queue for five minutes; rejoining thaws it instantly rather than losing their place.
- **Anti-flood** — a rolling request window, temporary mutes, and escalation to a day-ban for anyone who keeps going while muted.
- **Ban list** — hard bans by hostmask pattern, timed bans, and a guard that refuses a pattern matching everyone.
- **Long paths and non-ASCII filenames** work on both platforms: Windows `MAX_PATH` is handled throughout, and Greek, Cyrillic and CJK filenames survive both ends of the IRC connection.

### The file list

- **One master list**, rebuilt by `!update` or on a schedule, published atomically so a failed scan never overwrites a good index.
- **Three formats** — `.txt`, `.zip` and `.rar`, all built every time; `LIST_FORMAT` picks which one is offered.
- **Search** — `@find <words>` against the master list, with results fitted to the IRC line limit.
- **A partially unreadable library fails the rebuild** rather than silently publishing a truncated list.

### Receiving files from other bots

- **Cross-bot fetch** — request a file with `!<bot> <filename>` or a whole list with `@<botnick>`, and track it from the dashboard.
- **Broadcast search** — send one `@find` to a channel and collect every bot's reply, grouped under each bot's own parsed header.
- **Both DCC directions** — active and passive/reverse SEND, since bots behind NAT use the latter.
- **Hostile-input handling** — every other bot is treated as untrusted: admission control, size caps, and zip-slip / zip-bomb guards on any archive received.

### Operating it

- **Authenticated admin console over DCC CHAT**, gated on the operator's services host *and* a PBKDF2-hashed password. Read-only commands (`status`, `queue`, `slots`, `bans`, `uptime`, `version`) and action commands (`ban`, `unban`, `clearqueue`, `rehash`, `update`) — see [ADMIN-CONSOLE.md](ADMIN-CONSOLE.md).
- **Optional web dashboard** — Search, Queue, File Lists grouped by folder, Downloads, a duplicate-filename verifier, a list rebuilder, a Settings page, and a Console (the DCC CHAT admin console's commands and live log, in the browser — for an operator who wants neither a second IRC client nor a debug channel). Off by default, loopback by default, behind the same password as the DCC CHAT console.
- **Guided first-run setup** — `python3 configure.py` asks a short series of questions and writes a working configuration.
- **Pre-flight check** — `start-dccore.sh check` verifies the setup without opening a socket.
- **Two configuration mechanisms** — `admin_config.py` for Python, `settings.conf` for plain text; the dashboard and console both write to the latter.
- **`!rehash`** reloads code and settings live, preserving queues and transfer state.
- **Channel adverts** on a timer, with a per-bot theme (five presets, or your own colours).
- **Statistics** — totals, today and yesterday, per-file download counts, a speed record, and a live rate.

### Quality

- **2903 tests**, on Linux and Windows, Python 3.10 and 3.12, in CI on every push and pull request.
- **Stdlib-only** — the daemon and its test suite need no third-party packages; Flask is required only for the optional dashboard.
- **No reloaded module owns a lock** — `!rehash` re-executes a module body, so a module-level `threading.Lock()` is rebound while a thread is still inside it. Every lock in a reloaded module is allocated in `runtime.py` and bound by name, and `tests/test_no_reloaded_module_owns_a_lock.py` fails if a new one appears — the class, not the four instances that prompted it.
- **A cross-list search index** — SQLite FTS5, built as each bot list is fetched, so the dashboard can filter every held list live rather than re-reading them at 2-11 seconds a keystroke.
- **Two adversarial audits** — an internal audit (32 defects, all fixed) and a pre-publication sweep before the first public release.

---

## Planned

Ordered by what unblocks what, not by preference.

### Multiple lists, and multiple folders per list

The largest gap against OmenServe, which has had both since long before this project started. DCCore now serves **several** directories into **one** list; more than one list is still to come.

`SEPARATE_VIDEO_LIST` is not that feature and does not pre-empt it: it splits one scan's output by content type, where this splits by folder set and binds each list to a channel. An operator whose film and music already live in separate folders wants this one, and turns that switch off.

The design is settled:

- One trigger, unchanged. `@<botnick>` everywhere; no new syntax for anybody to learn.
- A list has an operator-facing name and a set of directories. Names are never typed in a channel.
- A channel is bound to exactly one list; a list may serve several channels. A request uses the list bound to the channel it arrived in.
- A private message uses the list marked primary, since a PM carries no channel.
- A channel with no list bound gets nothing: no advert, no requests answered.

**Multi-folder is done.** `library.py` answers which folders and in what order, resolution reads a folder's label out of a heading, and the scan builds one list from all of them. The Settings page landed with it — a reorderable list with a validated add and a folder browser (`GET`/`POST /api/folders`, `GET /api/folders/browse`), so `data/library_folders.json` no longer needs hand-editing.

Multi-list then follows: allow more than one list object, with per-channel adverts falling out nearly free. The folder set moves inside a list at that point, which is why every caller goes through one accessor rather than reading a setting directly — the move rebinds the accessor instead of touching 54 call sites a second time.

Two pieces were worth doing carefully rather than quickly, and one of them turned out the opposite way to what this section used to predict:

- **Containment.** This said `is_safe_path()` would become "inside *any* configured root", and called that the one place a mistake is a security bug rather than an inconvenience. Right about the risk, wrong about the answer: widening it that way is a strictly weaker test. Because a heading names its own folder, resolution returns *which* folder it landed in and the check runs against that one — the same strength as when there was only ever one. `is_safe_path()` itself was never touched.
- **Index identity.** Two folders can hold the same relative path — the same album in flac and in mp3 is the ordinary case — so an entry has to record which folder it came from. That is the label leading every path, and it is what makes the containment answer above possible.

### Test coverage where it is thinnest

The pre-publication audit found **21 daemon functions with no behavioural coverage at all** — `!rehash`, `@<nick>-que`, the advert worker, the IRC read loop, `configure.py`'s entry point. That gap is closed: `scripts/function_coverage.py` reports **2 of 257 uncovered, both on the allowlist with a written reason**, and it fails the build on a third.

What remains is narrower and does not show up in that number. Several "the wiring is in place" guards read the source as text rather than executing it, so a call moved behind a disabled branch would still not be noticed — this file's own history has three such guards that passed against deliberately broken code. Nine dashboard routes were never requested by any test; that count has not been re-measured since.

### From the audits, not yet done

- Roughly forty further verified findings, from a false "MasterList missing" during a concurrent search to a queued `!rar` pack that is never re-dispatched.

### Knowing which bots are out there — open questions

The List Browser's source list is built from two things: lists we have
fetched, and `runtime.known_bots`, which is filled from the periodic advert
every serving bot sends in the channel. Both of those assumptions have holes,
and neither has been decided yet.

**A bot's nick is not a stable identity.** When a bot loses its connection and
comes back on its alt nick it advertises under the alt nick, so it appears in
the registry — and in the sidebar — as a second bot. The freshness comparison
then has nothing to compare: the advert we recorded at fetch time is filed
under one nick and the advert arriving now under another, so a list that is
perfectly current reads as "cannot tell", and re-fetching under the alt nick
gives a second copy of the same library.

The list *contents* do not follow the nick either. A request line inside a
fetched list is `!nickname <track>`, written by that bot when it built the
list, so it still names whichever nick was current at build time. Copying that
line into the channel sends the request to a nick that may not be there.

We have the same problem in the other direction, and it is not hypothetical:
`update_list.py` writes `!{config.NICKNAME}` into every line of our own list at
build time, and `irc.py` rebinds `config.NICKNAME` to the alt nick on a 433.
A rebuild while we are on the alt nick therefore ships a list whose every line
names the alt nick.

Open: what is the stable identity — the services account, the host, an operator
mapping in settings? And is the fix to normalise at fetch time, to rewrite the
request lines on the way out, or only to merge the two rows in the sidebar?

**Some bots we want are not advertising, and some advertising bots are not
reachable.** Three cases, none of which the registry covers today:

- A bot that answers CTCP but never advertises in the channel. It never
  reaches `known_bots`, so it does not appear at all — even though `@botnick`
  and `!botnick <track>` would both work.
- The opposite: a bot that advertises but does not answer. It appears, and
  clicking it starts a fetch that goes nowhere.
- A bot that does neither, where the operator simply knows that `@botname`
  gets the list and `!botname <track>` gets files, and wants to add it by
  hand.

So the source list probably needs a manual entry — an operator-added bot,
persisted separately from the advert-built registry so a prune cannot remove
it, and marked in the sidebar as added rather than seen. Open: whether a
manual entry should be probed to confirm it answers, and what the sidebar
should say about one that has never replied.

### Smaller things worth having

- **PER-LIST file exclusions** (`Exclude = .mpu,.db`) — OmenServe has them per list. `LIST_IGNORED_EXTENSIONS` does this globally; scoping it to one folder is the part still missing.
- **A size cap on `!rar` packing.** There is none: a request packs whatever the folder holds, however large. `RAR_EXTENSIONS` now keeps film folders out of the album list entirely, which removes the worst case, but a genuinely enormous album is still a request nobody sized before accepting.
- **A fetched list keeps only the peer's master.** Since the film-and-series split, a DCCore bot's archive carries two `.txt` files, and `list_fetch` picks one - now the master rather than whichever is larger. The films in the other are dropped from the fetched copy. Reading both into one fetched list changes what `_pick_list_file()` returns and the size ceiling that guards it, so it is a change of its own rather than part of the fix.
- **A "what's new" list** — files added in the last N days, generated alongside the main one.
- **A list validator** run at build time, reporting anything a requester will not actually be able to get — duplicate filenames across folders being the common case.
- **An OmenServe migration path offered on FIRST RUN.** The Stats page can import an operator's totals from `vars.ini` today; what is missing is `configure.py` offering it during setup, when somebody migrating is actually looking.
- **Stealth channels** — serve a channel while advertising nothing in it.
- **Multi-network** — real in OmenServe, and it would touch every socket path here.

---

## Not planned

- **Multi-network before multi-list.** It touches more and is wanted less.
- **Rewriting the mIRC theme engine.** The colour blocks are what makes a DCCore bot recognisable in a channel.
- **TLS for the dashboard.** It is a loopback tool by design; anyone needing it across a network should put a reverse proxy in front rather than have the daemon grow a certificate story.
