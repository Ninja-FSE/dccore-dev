### 🧪 The identifier denylist is not tracked; CI writes it from a secret (#518)

Security advisory GHSA-g7x3-f4mf-w873 (medium), fixed in its private fork and merged with this release.

`tests/forbidden_identifiers.py`, the hashed denylist `tests/test_no_personal_identifiers_ship.py` reads, is no longer
part of the repository. Each maintainer keeps a local copy, and CI writes one before the suite runs.

- **Untracked, and in `.gitignore`.** Its `export-ignore` line in `.gitattributes` is gone with it: there is no
  tracked file left for it to apply to.
- **CI gets it from the `FORBIDDEN_IDENTIFIERS` secret.** A step in `.github/workflows/tests.yml`, under bash on all
  three runners, base64-decodes the secret into the file and checks that it imports and holds entries; a bad value
  fails the step naming only the kind of error. The value reaches Python through the environment and is never
  echoed. With no secret the step does nothing and the sweep skips - unless the repository variable
  `REQUIRE_IDENTIFIER_SWEEP` is set, where it fails instead; a pull request from a fork, which gets no secrets,
  still skips.
- **Absent still skips, and now says the sweep did not run.** The denylist test's skip reason says the file is
  absent or defines no `FORBIDDEN`, and where it comes from. `scripts/preflight.py` prints the same under its skip
  report (`identifier_sweep_note()`), without failing. It asks the guard rather than checking the path itself: a
  child process imports the guard and prints whether its `FORBIDDEN` is None, plus the guard's `DENYLIST` path, so
  a file without `FORBIDDEN` is reported too. If that import fails, the note says the sweep will skip and prints
  nothing the import raised.
- **Every tracked file is swept, not only what ships.** New `NothingIdentifyingIsTracked` runs the same denylist
  over every tracked text file, export-ignored ones included (the internal changelog, the workflow and
  project-board notes, the changelog fragments), since the development repository is public too. In export-ignored
  files only, the repository's own name and the maintainers' public account names are allowed
  (`ALLOWED_OUTSIDE_THE_RELEASE`, held as word hashes). It skips where git cannot list the tracked files, as in the
  extracted public tree. `shipped_paths()` now derives from the new `tracked_paths()`.
- **`export-ignore` is described as what it is.** `.gitattributes`, the guard's comments and
  `docs/PUBLIC-REPO-WORKFLOW.md` say it keeps a file out of the release tree and nothing more: every file tracked in
  `dccore-dev` can be read by anyone. The workflow document has the secret and variable setup, how new entries
  reach each copy, why the reference copy lives outside the work tree (checking out an older commit overwrites the
  ignored copy, and switching back deletes it), and the one-time step that keeps a local copy across the pull that
  stops tracking it.
- **Older entries in this changelog** describe a bot by its role rather than by its nick, or use the
  placeholder nick the public changelog already uses for the same story.
- **Tests:** the denylist test now reports a hit on GitHub Actions (`GITHUB_ACTIONS` is `true`) by file:line and "a
  listed identifier" alone, ungrouped, because the CI logs are public; elsewhere it still names the entry's label.
  It fails with `self.fail()`, since `assertEqual()` would print the labels with the dict. New,
  `tests/test_the_identifier_denylist_is_a_local_file.py`, every value a planted placeholder:
  - the file is not in the index and `git check-ignore --no-index` matches it;
  - imported with no denylist, the guard reads `FORBIDDEN` as None and its denylist test skips saying the sweep did
    not run, with no failure or error; given a denylist, the same copy runs;
  - a planted word in a temp tree, found by the guard's own test: no label with `GITHUB_ACTIONS=true`, the label
    without it or with `false`;
  - the CI step comes before the suite, runs under bash, and takes the secret only in `env:`, cross-checked with
    PyYAML where it is installed. Its own `run:` text, run with bash: no secret, empty or blank passes and writes
    nothing; valid, line-wrapped and CRLF-wrapped base64 write the exact bytes; bad base64, a stray character in
    good base64, a syntax error, a file whose error message would quote it, and an empty or non-dict `FORBIDDEN`
    fail, naming only the error type. No output holds the secret or a line of what it decodes. With no secret, the
    secret (missing, empty, blank) × variable (missing, empty, blank, set) × fork test (missing, true, false, other casings) matrix
    fails exactly when the variable is set and the run is not from a fork; values set on the host change nothing.
    The step's whole `env:` mapping is checked, from the text and, where installed, with PyYAML.

  In `tests/test_preflight_counts_what_was_skipped.py`, `identifier_sweep_note()` is given each kind of answer
  (ready, absent with the guard's path, a non-zero exit, empty or unexpected output, an `OSError`), and a marker
  planted in the probe's stderr never reaches the note. The real probe runs in a temp tree with the file absent,
  without `FORBIDDEN`, as a folder, valid, and with a syntax error on the marker's line; run as a child, preflight
  prints the marker on neither stream. `main()`, with every pass stubbed and the probe's answer faked, prints the note in the skip report.
  In `tests/test_every_shipped_text_file_is_swept.py`, the tracked sweep on a temp tree: an allowed word passes in
  an export-ignored file and fails in one that ships, any other entry fails anywhere, no labels on GitHub Actions,
  and it skips when git cannot run or lists nothing; every allowed hash is a key of the real denylist. In an
  extracted public tree the new tests pass or skip, and the denylist test skips.
- **Mutation checks, 31/31 caught:** the `.gitignore` line deleted; the skip turned into a failure; the ImportError
  fallback removed; the note's print dropped; `echo "$FORBIDDEN_IDENTIFIERS"` added; `validate=False`; `err`
  printed instead of its type; a non-zero exit on an empty secret; the label printed in CI; `assertEqual(found, {})`
  restored; `shell: bash` removed; the secret pasted into `run:`; the file tracked again; the step moved after the
  suite; the step ignoring `REQUIRE_IDENTIFIER_SWEEP`, ignoring `FROM_A_FORK`, passing when
  `FROM_A_FORK` is missing, or counting a blank variable as set; the variable's or the fork test's `env:` line
  dropped or changed; preflight putting the probe's stderr in the note, or letting it through to its own stderr;
  the probe checking the path, so a file without `FORBIDDEN` reads as ready; the note printing a path of its own
  rather than the guard's; the probe's exit code or an `OSError` ignored; `tracked_hits()` applying the allowance to
  files that ship, or ignoring it; the tracked sweep using `assertEqual(found, {})`, or running on an empty file
  list; the guard's `DENYLIST` naming another path than the one it imports.
