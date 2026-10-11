# Changelog entries waiting for a release

Every PR writes its changelog entry here, in a file of its own, instead of
editing `docs/UPDATES.md` and `docs/UPDATES-PUBLIC.md` (#1307). Two PRs never
touch the same file, so they cannot conflict in the changelogs, however many
are open at once.

## The files

- **`<issue>.md`**: the internal entry, exactly as it would have gone into
  `docs/UPDATES.md`. It starts with its `### ` heading, and has no `#` or `##`
  heading of its own; those belong to the changelog.
- **`<issue>.public.md`**: the public bullet, when the change has one. One
  line, starting `- **`, as it would have gone into `docs/UPDATES-PUBLIC.md`.
- **Two entries for one issue** (a phase 1 and a phase 2) take a suffix:
  `<issue>-<suffix>.md`, lower case, digits and dashes (`1288-phase2.md`).

Between releases this folder *is* "Unreleased". `docs/UPDATES.md` and
`docs/UPDATES-PUBLIC.md` change only when a release is rolled.

## At release time

```
python scripts/roll_changelog.py v1.17.0 "The Title"
```

writes the version heading into both changelogs with the entries under it,
newest issue first, and deletes the fragments. `--date YYYY-MM-DD` sets the
date (today otherwise), and `--check` only validates. A malformed fragment
stops the roll with nothing changed, and says which file and why.
`docs/PUBLIC-REPO-WORKFLOW.md` has the rest of the release steps.

This folder is export-ignored: if a roll is ever forgotten, the internal
entries still stay out of the public tree.
