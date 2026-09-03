# The dccore-dev / dccore split

Two repositories, one project:

- **`dccore-dev`** (this repo, private, full history) — where all development
  happens. Every fix, every test, every PR.
- **`dccore`** (public, no shared history with this repo) — "the released,
  ready-to-run distribution." Only ever gets a fresh tree of the current
  state, extracted and pushed as a release. Nobody develops against it.

Neither repo's issue tracker or PR history means anything to the other —
`dccore-dev#162` and `dccore#162` are unrelated numbers in unrelated repos.

## Handling an issue reported on `dccore`

A user files an issue on the public repo. It stays there — do not transfer
it. Transferring moves it into this private repo, and the reporter (who is
not a collaborator here) loses all visibility into their own report.

1. **Triage on `dccore`.** Decide whether it needs a fix, same as any
   issue.
2. **Do the actual work here, in `dccore-dev`,** on a normal branch/PR.
   Reference the public issue in the commit or PR description as
   `Ninja-FSE/dccore#N` for traceability — it's just text across a private
   repo boundary (GitHub won't cross-link it, since the target isn't
   visible from here), but it tells the next reader why the change exists.
3. **Ship it** through the normal extraction/release process (below).
4. **Go back to the `dccore` issue** once the release is out: comment with
   what was done and a link to the relevant `docs/UPDATES.md` entry, then
   close it. This is the step that actually closes the loop for the
   reporter — nothing automatic does it.

No GitHub feature does steps 2-4 for you. It's a checklist, not tooling.

## Releasing to `dccore`

1. On `main` here, `git archive --format=tar HEAD | tar -x` into a scratch
   tree — no `.git` history carried over.
2. Strip dev-only tooling that has no shipped consumer: `scripts/preflight.py`,
   `scripts/capture_adverts.py`, `scripts/function_coverage.py`,
   `tests/uncovered_functions.txt`. Keep anything a shipped file's tests
   depend on (`scripts/gen_settings_sample.py`, `docs/CONVENTIONS.md`).
3. Swap the changelog: delete `docs/UPDATES.md` (internal, references
   internal PR numbers and pre-release history that means nothing outside
   this repo), rename `docs/UPDATES-PUBLIC.md` → `docs/UPDATES.md`. Keep
   `UPDATES-PUBLIC.md` current here as work lands, the same way `UPDATES.md`
   is kept current — write the public-facing entry in the same PR as the
   fix, not as an afterthought at release time.
4. `git init`, push, PR into `dccore`'s `main`. Branch protection there
   requires the CI matrix green before merge.

## Identity scrubbing — what actually failed before

Every general sweep ("grep the code and docs for names") missed things
that later sweeps caught by design, not luck. Specifically:

- **A denylist of identities cannot live inside the file it's protecting.**
  `tests/test_licence_is_stated.py` used to assert the README did *not*
  contain a list of real handles — which meant the test itself, since
  `tests/` ships, spelled those handles into the public repo. Fixed by
  asserting the positive property instead (`Copyright (C) YYYY The DCCore
  contributors`), which needs no denylist. Assert what should be true, not
  a list of what shouldn't.
- **`.gitattributes export-ignore` is the only mechanism `git archive`
  actually honours.** A manual `rm` step in the extraction script works
  until someone extracts a different way. Anything that must never ship —
  `docs/UPDATES.md` (internal) — should carry `export-ignore`, verified by
  a test that runs a real `git archive` and checks the output, not by
  trusting the rule is there.
- Read the scrubbing/test code itself during a sweep, not just the
  production code and docs. The scrubber is shipped too.

## Access

Both repos: `Ninja-FSE` (owner) and `chchatzop` (collaborator, both repos).
No one else has merge rights on either. `dccore`'s branch protection
requires a PR with green CI to merge into `main` — including for releases
pushed by either of us — but does not require a second approver.
