### 🩹 A `!rar` archive holds what the list holds, and a pack waits while there is no room for it (#1376 follow-up)

Security advisory GHSA-84jr-495f-w93p (medium), fixed in its private fork and merged with this release.

Since #1376 a file type the list leaves out (`LIST_IGNORED_EXTENSIONS`, or a list's own file types to leave out,
#1295) is not served by name. A pack now follows the same rule, and waits rather than filling the disk.

- **One rule for the row and the gate.** `update_list.folder_holds_packable()` takes the list's left-out types
  (`ignored`, `ignored_extensions(<list>)`) and counts a file only where the scan gives its folder a `!rar` row:
  `is_listed_file()` and `is_packable_file()` both say yes, its size can be read, and no disc folder on the way
  has a backslash in its name (#464). `handle_download_request()` passes the left-out types of the list the
  request was answered from. A folder whose only packable files are left out has no row and is refused, the same
  notice as a folder with nothing packable.
- **The archive holds the files the list lists.** `update_list.pack_members()` walks the folder by the scan's own
  rules (`walk_with_sizes()`: no links or junctions followed; a folder with a backslash in its name left out; a
  file whose size cannot be read left out; `is_listed_file()` for the rest). A file that is a link to somewhere
  outside the library folder the album is in is left out too, and logged (`_members_inside_the_library()`): rar
  packs what a link points at. rar is run as it always was, `rar a -ep1 -w<dir> <archive> <folder>`, with `-n@<list>`: a list file of those files'
  absolute paths, which keeps only them (`_rar_list_file_bytes()`): UTF-16 with `-scul` on Windows, UTF-8 with
  `-scfl` elsewhere - rar 6.23 on Linux leaves out a name with a character above U+FFFF (an emoji) read from a
  UTF-16 list, and packs it, and U+2028, from a UTF-8 one. rar walks the folder by its own absolute path, so a
  folder deeper than Windows' 260 characters packs as before (starting rar in the folder's parent was refused by
  Windows for such a folder, `WinError 267`, with or without `\\?\`). Checked with RAR 7.22 on Windows, on a
  320-character folder too, and with RAR 6.23 on Linux (a review run); macOS was not checked. The UTF-8
  charset switches (`-scfl` for the list off Windows, `-scfr` for the listing on Windows) need RAR 5.50 or later
  ("Version 5.50: 'f' charset value in -sc", rar's whatsnew.txt): an older rar refuses them, and every pack would
  fail where the folder form worked. The archive's file names are the old form's exactly, Unicode, hidden and leading-space names included, less what the list leaves out; folders are no
  longer stored as entries of their own (they come back with their files). Named rather than excluded with `-x`
  masks, because a mask that fails to match lets the file in, and rar's case rule is its platform's, not the
  list's.
- **Only paths rar reads as that one exact file are named** (`_rar_list_file_lines()`). rar 7.22 does not read
  every `-n@` line as an exact name: it trims the end of a line (`keep.flac ` took `keep.flac`, a file `Sub ` the
  whole folder `Sub`, and on Windows `keep.flac.` took `keep.flac`), a `*` or `?` in a name also matches files
  in every folder below, and a folder named takes everything in it. So a path is left out of the archive, and the
  log counts it, when any part has a `*` or `?`, ends in a space or a tab (or a dot, on Windows), or holds a
  control character, a backslash where `/` is the separator, or a name the list file cannot hold; only files are ever
  named. A name holding U+0085, U+2028 or U+2029 is kept: rar 7.22 takes it as named and lists it as one name.
- **The archive is checked before it is sent**, both ways. `rar lb` lists it (`_archive_names()`, through
  `_run_rar()` like the pack, with `-scfr` on Windows so rar writes UTF-8 to the pipe - checked with rar 7.22,
  Unicode names intact - and in a UTF-8 locale elsewhere, `_rar_environment()`: one already in force is kept,
  else `C.UTF-8` on Linux and `en_US.UTF-8` on macOS, the rest of the environment kept, for the pack too). The
  listing is read at line ends alone (`_listed_names()`): `str.splitlines()` also splits at U+2028 and the like,
  and made every pack of a folder holding such a name fail. `_what_the_archive_got_wrong()` compares it with the
  names asked for. A name nobody asked for, or none of what was asked for (an empty listing), and the archive is
  deleted and the pack refused once, the names logged; a pack made again would make the same archive and hold
  every other pack meanwhile. Short of some asked-for files and holding nothing else, it is sent, with a loud
  `[LINEAR RAR ERROR]` line naming what it lacks: it holds nothing the list leaves out, and what it lacks (a file
  gone since the walk) can still be asked for by name. A folder above an asked-for file is not counted against
  it: rar off Windows may store one, with no data. An archive that cannot be listed (rar's exit code, its stderr
  in the log, an OSError or the timeout) is deleted and the pack tried again, with its retries.
- **The row records its list** (`PACK_LIST_KEY`, `served_list`); `pack_leaves_out()` follows that list's rule. A
  row with none, queued before this, or naming a list that is gone, follows every list's left-out types together.
- **A waiting archive is handed on only under the same rule.** A packed archive's row records what it was packed
  without (`PACKED_WITHOUT_KEY`). The packer reuses an archive another request is waiting on (#1268) only when that
  covers this row's left-out types; otherwise it packs beside it, at a path named with a digest of the types
  (`_archive_path_leaving_out()`). An archive no row says that of is never handed to another request.
- **An archive packed before this is packed again.** A packed archive's row with no `PACKED_WITHOUT_KEY` and a
  `source_path` (restored from `dcc_queue.txt`, say) is turned back into its folder's row: every such row, at any
  place in its queue, once the queue is loaded at start-up (`pack_again_every_row_packed_before()`, from
  `oserve.py`), and a head the dispatcher or the sweep meets later (`_pack_again_if_packed_before()`); its archive
  goes unless another row or a send still needs it. A row being sent is left alone; a row with no folder on it
  (from before #1077) is not turned back.
- **Refused once.** A pack that cannot be made however often it is tried - nothing in the folder is listed any
  more, the archive's paths are both wanted under other rules, the folder is bigger than the whole disk, or the
  archive is not what was asked for - raises `PackRefused`: the wrapper drops the row without using up its
  retries (`release_queue_entry(..., retry=False)`).
- **What the user is told.** One fixed line for any folder that could not be packed, `PACK_FAILED_TO_USER`
  ("the folder could not be packed"), once, in release_queue_entry()'s notice (`told`); the reason - which can
  name `TMP_ZIP_DIR`, a size or a path, and for an error once the retries are spent an OSError's path - goes to
  the log alone. The packer's failures no longer send `send_pack_error_notice()`, whose text is about artist
  root folders.
- **Upper bounds now:** `MAX_RAR_FOLDER_SIZE` (`pack_size_over()`) and the dashboard's total for a running pack
  still count every file in the folder, the left-out ones too, so both are at least what is packed.
- **A pack waits while its files do not fit.** Before rar runs, the listed files must fit in what is free on
  `TMP_ZIP_DIR`'s disk with `dcc_fetch.MIN_FREE_BYTES` to spare, the rule fetching waits by
  (`_has_room_to_pack()`, through `dcc_fetch.has_room()`, public now). Short, that row waits, keeping its place (`runtime.pack_space_holds`, one entry per
  row), and a pack that fits goes ahead; the log and the debug channel say `[RAR-HOLD]`.
- **No more packed archives wait than `MAX_DCC_SLOTS`** (`waiting_archive_count()`, by path, an archive being sent
  not counted, nor one behind a folder pack in its own nick's queue - counted, it held that very pack at the cap
  for ever, with a pack and its own archive behind it in one queue, as a moved row or a restored queue can leave
  them; `waiting_archive_cap()`, at least one). Since #1435 a finished archive can wait for a slot or a
  large send while the packer moves on; at the cap every pack waits in the queue like one behind a running pack.
- **One answer to "can a pack start".** `packs_can_start(row)` (no pack running; `pack_hold_reason(row)`: the
  cap, or that row waiting for room) is what the dispatcher's gate, `claim_next_in_line()`,
  `redispatch_waiting_pack()` (through `next_waiting_pack_owner()`, which passes a row waiting for room over) and
  the fairness rule (#1032, `others_wait_for_a_slot()`) ask, so a held pack is not woken into a refusal over and
  over.
- **A held pack is woken.** A send's end wakes the queue as before. And a hold starts `_watch_held_packs()` (one at
  a time, `runtime.pack_hold_watch`, unmarked again if its thread cannot start), which looks every
  `PACK_HOLD_POLL_SECONDS` (30 s): a row stops waiting once its files fit or it has left the queue, and is woken
  at once even while others still wait; the queue is woken again when nothing is held, and also when a look
  fails. The packer interlocks are released once, by the wrapper (#714). No new setting.
- **The `[LINJAR RAR ERROR]` log tag is `[LINEAR RAR ERROR]`** in the packer's two lines that had it.
- **Left as they are:** a pack waiting for disk room has no ageing - under steady small packs it is looked at
  again only every 30 s by the watcher. An archive already queued is not looked at again when a list's exclusions
  tighten, as a queued single file is not.
- **Stale list files:** a `pack-*.lst` a crashed run left in `TMP_ZIP_DIR` goes when the next pack writes its own.
- **Docs:** `docs/INSTALL.md` (the list is what the bot serves; `RAR_EXTENSIONS` and `MAX_RAR_FOLDER_SIZE`).
- **Tests:** `tests/test_a_pack_holds_what_the_list_holds.py` (59) and
  `tests/test_a_pack_waits_for_room_where_archives_are_built.py` (24). The list build and the real request
  handler agree, folder by folder, with types left out for every list, by the primary alone and by a second list
  bound to its own channel; the archive a stand-in rar packs - one that reads `-n@` the way rar 7.22 does, trims
  and wildcards included, and refuses a start folder longer than 260 characters on every platform, as Windows
  does - holds the listed files only, upper-case extensions and subfolders included; names that end in a space
  or a dot, or hold a wildcard or a control character, take nothing left out, and one holding U+2028 is packed;
  an archive holding a name nobody asked for, or listed empty, is refused once, and one
  that cannot be listed is tried again; the listing is read at line ends alone, both rar runs get the
  environment, and `LC_ALL=C.UTF-8` off Windows; the user is told one fixed line, once, with no path, size or
  `TMP_ZIP_DIR` in it, for refusals and for an error that names a path; with a real rar where there is one: the
  listed files with a Unicode and a U+2028 name, a folder deeper than 260 characters, names rar would trim, and a
  folder line caught by the check; reuse under the same rule, not under another; an archive packed before this
  is packed again, in the dispatcher and the sweep, but not while it is being sent; the room hold, its margin, a
  pack that fits going ahead, a folder bigger than the disk refused once, the wake of one row while another still
  waits, and a failed look still waking the queue; the cap, at and below it, and its wake when an archive is
  sent; the interlocks released once (#714's model). Also: the list file's bytes and switch on both platforms, and
  a pack read from a UTF-8 list on any platform with a name above U+FFFF; an archive short of an asked-for
  file sent with the log line, and refused when it also holds a stray name; a file link out of the library
  left out (a real link where links can be made, the decision with fakes everywhere); every earlier archive
  row of a loaded queue turned back, and a start-up (`oserve.startup()`) that loads a saved queue turning back
  both the head and one behind a pack, their archives gone; an archive behind its own nick's
  pack not counted - the pack starts - also after move_queued_file() puts it there, and one behind a plain
  file still counted. A rename does not merge two queues (note_nick_change() leaves the new nick's alone),
  so it cannot put an archive behind a pack. `support.fake_rar_runs()` answers the listing with the
  names the pack's `-n@` list asked for, and the real-process fake rars in
  `tests/test_a_running_pack_can_be_seen_and_cancelled.py` and
  `tests/test_every_transfer_ending_is_recorded_with_how_it_ended.py` list what they packed, so every stand-in
  pack goes through the check; `tests/support.py` resets `runtime.pack_space_holds` and `runtime.pack_hold_watch`
  between tests. The waiting archives in `tests/test_serving_keeps_shared_archives_and_claims_straight.py` and
  `tests/test_a_finished_archive_follows_the_large_file_rule.py` now say what they were packed without, and
  `tests/test_path_security.py`'s control checks that rar is given the `-n@` list.
- **Mutation checks, 59/59 caught**, among them: the gate without the list's types; `folder_holds_packable()`
  or `pack_members()` ignoring them; rar given the folder without the `-n@` list; rar started in the folder's
  parent again (the long-path tests fail, the real rar with `WinError 267`); the wildcard guard removed or reduced
  to the entries beside a part; the trailing space and dot guard removed or limited to the last part; control
  characters kept; the check after the pack removed, accepting names nobody asked for, missing names or an empty
  listing, refusing the folders rar off Windows stores, retrying what it refuses, reading the listing with
  `splitlines()`, run without `-scfr`, or sending what cannot be listed; `LC_ALL` not set or the environment not
  passed; the refusal's reason sent to the user; the room hold back to one for every pack; a folder bigger than
  the disk left to wait; a refusal using up retries; an earlier archive sent as it is, or only the head of a loaded
  queue turned back, or the start-up call removed or put behind `if False:`; a UTF-16 list file off Windows; an archive short of a listed file refused again; the count
  taking archives behind their own pack; a file link out of the library packed; the room check removed or
  without its margin; the cap removed or off by one either way; a held pack never woken (six ways); and the whole
  earlier code.
