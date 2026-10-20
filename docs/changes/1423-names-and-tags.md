### 📁 Longer directory tags, and plain names at the top of the fetch folder keep their id (#1423)

Security advisory GHSA-92w2-hwhv-qr3v (low), fixed in its private fork and merged with this release.

- **Plain names at the top of `FETCHED_FILES_DIR`:** a fetched file is promoted to its plain name there only when that
  name has a "." in it. A name without one keeps its request id, because that is where each bot's own folder goes
  (`FETCHED_FILES_PER_BOT`, #1423), and a folder named after a nick never has a dot. This also covers `lists`, which
  stays in `_RESERVED_FETCHED_NAMES`. Inside a bot's own folder nothing changes.
- **No dot in a bot's folder name:** `bot_folder_name()` turns a "." left in the cleaned nick into "_", so the rule above holds
  for any nick, not only for valid ones. That name is not the nick, so it carries the tag a cleaned name gets (#1438):
  `a.b` gives `a_b-<digest>` and never shares the folder of the nick `a_b`.
- **A file where a bot's folder goes:** when something other than a folder is already at a fetch folder's name - a
  file kept at the top from an earlier version - `_run_transfer()` fails the fetch with a reason that names it and
  asks the operator to move or rename it, instead of a bare `could not create destination dir: [Errno 17]`. The
  file is left as it is, and no other folder is used instead.
- **A nick that cleans to nothing** (`[]`, `` ^|` ``, or `fetched_file` in any case) now gets a folder of its own,
  `bot (<digest>)`, instead of the top of `FETCHED_FILES_DIR`. The space and parentheses are not nick characters, so
  no other nick's folder can take that name. A nick of nothing but control codes still has no folder.
- **Longer digest:** the tag `list_fetch._name_digest()` puts on a cleaned or reserved directory name is now 16 hex
  digits (64 bits) instead of 6, for a bot's list directory and a secondary channel's. A name that looks tagged in
  either the new or the old six-digit form is tagged again, so no nick spells out a directory under either name.
- **Held lists move once at startup:** `migrate_held_list_directories()` moves a held list, and its secondary
  channels' lists, from the six-digit name to the longer one, under the same safety rules as before. The old
  `lists/_channels/<bot>/` folder is removed once the last channel has moved out and it is empty.
- **Tests:** on the real code, in temp trees:
  - `tests/test_a_plain_name_at_the_top_keeps_its_id.py`: through `handle_incoming_offer()` and a real loopback
    transfer, a no-extension file `alfa` fetched with `FETCHED_FILES_PER_BOT` off keeps its id at the top, and a
    later fetch from bot alfa with the setting on completes in `alfa/`; the same when the first file comes from the
    nick `[]` with the setting on. `_promote_clean_filename()` on its own: `alfa`, `alfa.`, `alfa ` keep their id at
    the top, `a.b` is promoted there, a no-dot name is promoted inside a bot's folder, and a relative
    `FETCHED_FILES_DIR` or a trailing separator is still the top. A real transfer from bot alfa with a file
    already at `alfa` fails with a reason naming that file and asking for it to be moved or renamed, not an errno,
    and leaves the file as it was; an existing `alfa/` folder is used as before.
  - `tests/test_each_bot_gets_a_fetch_folder_of_its_own.py`: `[]`, `` ^|` ``, `fetched_file` and `FETCHED_FILE` give
    `bot (<16 hex>)`, one folder for both cases and one per nick; `""`, None, blanks and control codes alone give
    None; `fetched_dir_of()` and `_bot_folder_for_locked()` accept the name; no nick-shaped string gives it. No
    folder name has a "." for 17 dotted nicks (dots inside, leading, trailing, `a.` repeated 200 times, `aux.txt`,
    `lists.`, `fetched.file`, with control codes), and each is still one component of at most 255 bytes and not
    `lists`.
  - `tests/test_a_cleaned_name_carries_a_longer_digest.py`: the digest is 16 hex digits on a bot's and a channel's
    directory; a name tagged in the 6- or 16-digit form is tagged again, 15 digits is not; two names that clean
    alike, with digests standing in that agree in their first six digits, get two list directories, two channel
    directories and two fetch folders. The migration moves a main list and two channels from 6 to 16 digits,
    rewrites and saves their paths, removes the emptied old `_channels/<bot>/`, keeps one with anything else in
    it and a bot's own channels folder, moves nothing on a second run, still refuses a shared directory or an
    existing target, and renames or removes nothing outside `lists/` (nor `_channels/` itself).
- **Mutants:** 24 of 27 caught: the dot rule removed, applied in every folder, with the root comparison inverted
  or not normalised, or without `rstrip(" .")`; `_name_digest()` back to six digits; `_DIR_TAG_RE` without the
  16 or without the 6 form; `bot_folder_name()` giving None for `[]`, `bot-<digest>`, or no casefold; the rmdir
  guard dropped, without its "not the new parent" clause, cut to its "not `_channels/` itself" clause alone,
  widened to `lists/`, or aimed at `_channels/`; rmdir on the new parent; the "." to "_" in `bot_folder_name()`
  removed, or only for the first dot; the file-in-the-way check removed, made to fire on any existing path (a
  folder too), aimed at `FETCHED_FILES_DIR` instead of the bot's folder, or without its `return` (so `makedirs()`
  overwrote the reason); and `src/` reverted to `main`. The three that live are equivalent: the old parent is only
  ever `lists/` (excluded as the new parent) or `lists/_channels/<bot>/`, so dropping the "not `_channels/` itself"
  clause, or widening the guard to any folder under `lists/` but `lists/`, changes nothing; and the "." to "_"
  moved after the length fit still gives a name with no dot, of the same length, only cut at another place when a
  long nick has a dot near its end.
