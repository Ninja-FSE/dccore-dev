# The dccore-dev / dccore split

Two repositories, one project:

- **`dccore-dev`** (this repo, public, full history) — where all development
  happens. Every fix, every test, every PR. Public means everything pushed
  here - branches, commits, issues, PRs, comments - can be read by anyone the
  moment it is pushed; see "Security fixes" for the one kind of work that
  never starts here.
- **`dccore`** (public, no shared history with this repo) — "the released,
  ready-to-run distribution." Only ever gets a fresh tree of the current
  state, extracted and pushed as a release. Nobody develops against it.

Neither repo's issue tracker or PR history means anything to the other —
`dccore-dev#162` and `dccore#162` are unrelated numbers in unrelated repos.

## Handling an issue reported on `dccore`

A user files an issue on the public repo. It stays there — do not transfer
it. Transferring moves it into this repo, out of the tracker the reporter
filed it in and is watching: it gets a new number, and the release repo's
issue list loses it.

1. **Triage on `dccore`.** Decide whether it needs a fix, same as any
   issue.
2. **Do the actual work here, in `dccore-dev`,** on a normal branch/PR.
   Reference the public issue in the commit or PR description as
   `Ninja-FSE/dccore#N` for traceability. Both repos are public, so GitHub
   links it both ways: the public issue's timeline shows the PR here. That
   is fine for an ordinary fix - and never done for a security fix, which
   does not appear here at all until it ships ("Security fixes" below).
3. **Ship it** through the normal extraction/release process (below).
4. **Go back to the `dccore` issue** once the release is out: comment with
   what was done and a link to the relevant `docs/UPDATES.md` entry, then
   close it. This is the step that actually closes the loop for the
   reporter — nothing automatic does it.

No GitHub feature does steps 2-4 for you. It's a checklist, not tooling.

**Never develop directly against `dccore`, not even for a small doc file.**
`SECURITY.md` was added there directly (#2, before this doc existed) and
did not exist here — so the next release's extraction would have silently
deleted it, since the release tree only ever contains what `dccore-dev`
has. chchatzop caught it (`dccore#3`) before a release shipped. Anything
meant to persist across releases has to originate here and flow through
extraction like every other file; `dccore` gets content only as a release,
never as its own line of development.

## Release candidates

The project has cut four of these — `v1.10.0-RC1` through `RC4` — and never
wrote down how, which is why this section exists.

**What an RC is for:** running a finished release yourself before anyone else
gets it. Nothing about the code changes; what changes is that the build stops
claiming to be the final version while it is still being proven.

**Cut one by versioning it, in the same three places a release uses:**

- `SCRIPT_VERSION` in `defaults.py` → `DCCore vX.Y.Z-RCn`
- the version line in `README.md`
- the heading in **both** changelogs, `docs/UPDATES.md` marked 🟦 rather than
  🟩, `docs/UPDATES-PUBLIC.md` in its own plain form

All three, and matching. `SCRIPT_VERSION` is reported over CTCP VERSION,
printed in the advert and stamped on every generated list, so an RC that says
`v1.12.0` puts a version that does not exist yet into three places at once —
and a bug reported against it cannot be placed. That is the same mismatch this
file already warns about for tags.

**An RC stays here.** It is not extracted to `dccore`: the point is to run it
before it is published, and publishing it defeats that. The v1.10.0 RCs each
got a public changelog entry because they *were* released; an RC used only as
a beta does not need one until it is promoted.

**Promoting it** is one edit in each of those three places — `-RCn` comes off,
the internal marker goes 🟦 → 🟩, and the date is updated if it has moved.
The RC heading is not kept as a separate section unless the RC actually
shipped to `dccore`: two headings for one set of changes reads as two releases.
Then run the checklist below.

**If the beta finds something**, fix it on a branch as usual and cut `-RC2`.
Do not fix it silently under the same RC number: the whole value of the
version is that the person running it can say which build they saw the
problem on.

## Releasing to `dccore`

### Before you extract anything

Every PR is supposed to carry its own documentation, so this is a check that
nothing slipped through rather than a writing session. In practice it is where
the omissions surface, because it is the last point at which they are cheap.

- **Roll the changelog**: `python scripts/roll_changelog.py vX.Y.Z "Title"`
  (#1307). Every PR writes its entry to `docs/changes/` instead of the two
  changelogs (`docs/changes/README.md`); this writes the version heading, with
  the date, into both `docs/UPDATES.md` and `docs/UPDATES-PUBLIC.md`, puts the
  entries under it newest issue first, and deletes the fragments. It refuses,
  changing nothing, when a fragment is malformed. If commits have landed since
  the last release and `docs/changes/` is empty, something shipped without an
  entry — write it now from the merged PRs.
- **Bump `SCRIPT_VERSION`** in `defaults.py`, **the version line in
  `README.md`**, **and the version on the project page** (`site/index.html`,
  beside "Download the latest release"; so is `dccore.ver` beside the mIRC
  script - `tests/test_the_site_page.py` fails until both match, #1430). `SCRIPT_VERSION` is what the bot reports over CTCP VERSION,
  prints in the advert and stamps on every generated list, so a stale one
  misreports itself in three places at once; `README.md`'s is the first thing
  a visitor to the public repo reads. This checklist named only the first of
  them until v1.11.0, when the second was found by grepping for the old
  number rather than by following the list.
- **Update the test count in `docs/FUTURE.md`** - the one place it lives
  since #698; `README.md`'s Tests section points there rather than carrying a
  second copy, because two sentences in two files that say the same number
  drifted independently (4994 against 5237 on one commit, neither true of the
  tree). Measure it here, at release time, from the merged tree, not in the
  branches: it is one line that every branch is tempted to touch, so three
  concurrent PRs mean a three-way conflict and a number that was never true
  of any tree that existed - whichever merged last wins with a count measured
  before the other two landed. Measured once, on what actually shipped, it is
  right by construction. `tests/test_the_test_count_is_kept_in_one_place.py`
  refuses a figure more than a tenth off what the loader discovers, so a
  roll that forgets this line fails the suite. `README.md`'s sentence used to
  also name `scripts/preflight.py` by file, which is dev-only tooling stripped
  at extraction (step 2 below) - worth a fresh read rather than a straight
  number swap, since a sentence naming a file that will not exist for the
  reader is its own kind of stale.
- **Check the Python pin in `scripts/windows/start-dccore.bat`** - `PY_VERSION`
  and its two SHA-256 lines (#711). The launcher installs exactly that
  python.org build for a first-timer with no Python, and it fails safe (a
  hash mismatch refuses to run the file) but never fresh: a 3.x.y security
  release lands and the launcher keeps installing the old one until somebody
  remembers. So remember here, once per release: look at python.org's
  current release in the pinned minor, and if it moved, bump the three lines
  and run `DCCORE_VERIFY_PYTHON_PIN=1 python -m unittest
  tests.test_python_missing_help_do_not_fail` (it fetches the hashes and
  checks them). WINDOWS.md's sample transcript carries the same number, and
  `tests/test_the_python_pin_has_one_home.py` fails the moment the two
  disagree - so bump both, and the test says which one you forgot.
- **Tag the release** on `dccore` after the merge, matching the changelog
  heading exactly. `v1.10.0` in the changelog and `v1.9.0-RC1` on the tag is
  the kind of mismatch nobody notices until someone reports a bug against a
  version that does not exist.
- **Create the GitHub Release for the tag, with the mIRC zip** (#1412, #1404).
  Notes: the release's section of the public changelog. Build the asset from
  the revision you extracted and attach it under its fixed name, so
  `releases/latest/download/dccore-mirc.zip` (the docs and the project page
  link it) is the new script. It is built from git at `--rev`, never from the
  working tree (#1442), so a stray or uncommitted file under `scripts/mirc/`
  cannot ship. The release names that revision explicitly: left out, it is
  this checkout's `HEAD`, which need not be what was extracted. A name is
  not enough either - `origin/main` moves with any fetch between the
  extraction and this step. So give it, as `<sha>`, the sha the extraction
  printed beside the revision it read ("The extraction", below):

      python3 scripts/build_mirc_zip.py --rev <sha> --out dist/dccore-mirc.zip
      gh release create vX.Y.Z -R Ninja-FSE/dccore --title "vX.Y.Z - Title" \
          --notes-file <notes> --latest dist/dccore-mirc.zip

  The sha `build_mirc_zip.py` prints must be that `<sha>`; any other is a
  zip of a tree that was not extracted - do not attach it. Then check
  `gh api repos/Ninja-FSE/dccore/releases/latest` names the new
  tag: a tag with no Release is invisible to the update check (#572), which
  asks for the latest release, and v1.16.2 shipped that way.
- **Check `docs/FUTURE.md`** for anything the release implements that is still
  listed as planned or not-yet-done. A roadmap calling a shipped thing missing
  makes the changelog look like it is overclaiming — this drifted once and
  reached the public repo before it was caught.
- **Check `docs/INSTALL.md`'s Upgrading section** covers anything an existing
  operator has to *do*. New settings never appear in an existing
  `settings.conf` (it is gitignored), and a change to what the generated list
  contains does nothing until the operator runs `!update` — neither is
  self-evident from the outside.

### The extraction

**Steps 1-3 are one command** (#1418), and `--test` runs step 4's suite in the
result:

    python3 scripts/extract_public_tree.py origin/main /tmp/dccore-public --test

It refuses, leaving no tree at all, when a step does not find exactly what it
expects - a dev-only file that is gone, the `.gitattributes` line or a link
moved - and names it. The list of what is stripped is `DEV_ONLY` in the script,
and only there; the steps below say why each kind goes. Its last line is the
`git update-index --chmod=+x ...` command step 4 runs.

1. On `main` here, `git archive --format=tar HEAD | tar -x` into a scratch
   tree — no `.git` history carried over.
2. Strip dev-only tooling that has no shipped consumer (`DEV_ONLY` in
   `scripts/extract_public_tree.py`: the preflight, the advert capture, the
   coverage pass, the extraction script itself). Keep anything a shipped file's tests
   depend on (`scripts/gen_settings_sample.py`, `docs/CONVENTIONS.md`), and
   keep `scripts/run_tests_in_parallel.py` with `tests/module_durations.json`:
   the public tree's own CI runs the suite through them (#1146).

   **Also strip the tests that exist only to cover `scripts/preflight.py`**
   (the `tests/test_preflight*.py` files, all in `DEV_ONLY`, and a test fails
   when one is added that it does not name). Removing the
   script and not these left four test files importing a module that no
   longer exists - invisible until step "run the full suite in the scratch
   tree itself" (below) actually caught it, at the v1.13.0 release, the
   first time that step was followed to the letter. A test file that covers
   both a dev-only tool and a shipped one (`tests/test_a_filename_your_code_page_cannot_spell.py`
   covers `preflight.py` alongside `commands.py`/`dcc.py`/`update_list.py`)
   is not on this list - it stays, and filters the dev-only entry out of its
   own list at runtime instead, by checking the file still exists.

   **Also strip the one internal-only line from `.gitattributes` that is
   wrong in the public repo:**

       docs/UPDATES.md    export-ignore

   Correct here, actively wrong there. Step 3 renames `UPDATES-PUBLIC.md`
   onto `docs/UPDATES.md`, so in the public repo that path **is** the
   changelog — and shipping this line unchanged tells any `git archive` run
   in the public repo to drop it. Nobody would see that until a release
   tarball came out without its changelog.

   **Leave the `docs/PUBLIC-REPO-WORKFLOW.md    export-ignore` line in
   place.** It names a file that does not exist in the public repo at all,
   which is harmless there either way — and its continued presence in the
   shipped `.gitattributes` is what lets `tests/exported_tree.py` tell the
   two repositories apart at all: `docs/UPDATES.md`'s own presence cannot be
   that signal (this same step makes it exist in *both*, with different
   content), so every test that needs to know which tree it is in checks for
   `docs/PUBLIC-REPO-WORKFLOW.md` being absent *with* its export-ignore rule
   still explaining why, instead. Stripping this line was tried once — it
   does not corrupt anything a person reads, but it made ten tests in the
   shipped suite fail on the very first clone of the public repo, for a
   reason that had nothing to do with their own content.
3. Swap the changelog: rename `docs/UPDATES-PUBLIC.md` → `docs/UPDATES.md`
   in the scratch tree. The internal `docs/UPDATES.md` needs no removal
   step — it carries `export-ignore`, so step 1 never extracted it (#246),
   and `tests/test_internal_files_do_not_ship.py` fails if that ever stops
   being true. Do not add an `rm` back: it would be a no-op that sends the
   next reader looking for a file step 1 was right not to produce. Keep
   `UPDATES-PUBLIC.md` current here as work lands, the same way `UPDATES.md`
   is kept current — write the public-facing entry in the same PR as the
   fix, not as an afterthought at release time.

   **Also rewrite the two links to it.** `README.md`'s document table and
   `docs/INSTALL.md`'s step 5 both link to `docs/UPDATES-PUBLIC.md` /
   `UPDATES-PUBLIC.md`, correctly, for a reader of *this* repository. Once
   the rename above has run, that target no longer exists in the scratch
   tree — rewrite both links to point at `UPDATES.md` (`docs/UPDATES.md` /
   `UPDATES.md` respectively) before extraction moves on.
   `tests/test_docs_links.py` fails on the next step if either is missed.
4. `git init`, `git add -A`, then the `git update-index --chmod=+x ...`
   line the extraction printed last, then commit, push, PR into `dccore`'s
   `main`. Branch protection there requires the CI matrix green before
   merge.

   **Do not skip the `update-index` line, on any OS** (#1441). The
   launchers are mode 100755, and NTFS has no executable bit: extracted on
   Windows, they come out plain, and `git add` there (`core.filemode`
   false) commits every one of them as 100644. Pushed like that, macOS
   refuses to open `start-dccore.command` and `./start-dccore.sh` says
   Permission denied - on every clone, and nothing on the Windows side
   shows it. The line names the files git carries as executable in the
   revision extracted, so it does not go stale as launchers come and go;
   where the filesystem kept the bit it changes nothing. Before the commit,
   check that `git ls-files -s | grep ^100755` (PowerShell:
   `git ls-files -s | Select-String '^100755'`) lists exactly the paths the
   line named.

   **Run the full test suite in the scratch tree itself before pushing**,
   not only in `dccore-dev`. Nothing in this checklist has run one until a
   real release needed it, and the first time surfaced ten failures that
   were invisible from here — every one of them a test whose assumptions
   only held on this side of the extraction. `dccore-dev`'s own green CI
   does not prove the tree steps 1-3 produce is green; only running it does.
5. **The website** (`site/`, #1309): the project page at
   `https://ninja-fse.github.io/dccore/`. GitHub Pages serves it from the
   orphan **`gh-pages`** branch of `dccore`, "Deploy from a branch", root.
   That branch is the one exception to "nothing but release trees reaches
   `dccore`": it shares no history with `main`, so a clone of `dccore`
   holds no website, and `site/` itself is export-ignored here. Publish it
   when a release changes what the page says, and only with the operator's
   go-ahead, like any push to `dccore`:

   - In `site/`, take the **Next release** mark off every feature the
     release carries (the overview's versions were already bumped with the roll), and check the pages against the release's README,
     INSTALL and ADMIN-CONSOLE. Merge that here first, as a normal PR.
   - Copy `site/` onto `gh-pages` and push:

     ```bash
     git clone --branch gh-pages --single-branch https://github.com/Ninja-FSE/dccore.git /tmp/dccore-pages
     cd /tmp/dccore-pages
     git rm -rq . && cp -a <dccore-dev>/site/. . && git add -A
     git commit -m "The project page for vX.Y.Z"
     git push origin gh-pages
     ```

   - The push starts a Pages build (a minute or so); `gh api
     repos/Ninja-FSE/dccore/pages/builds/latest` says when it is `built`.

   The pages are scanned by the identifier sweep like any other file, but
   only here: run the suite before copying. A custom domain is the
   **Custom domain** box in `dccore`'s Settings > Pages plus one DNS record
   (a `CNAME` for a subdomain, pointing at `ninja-fse.github.io`); that
   setting writes a `CNAME` file to `gh-pages`, which the `git rm` above
   removes: put it back with `git checkout HEAD -- CNAME` before the commit.

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
- **`dccore-dev` is public as well.** A name in a commit, an issue, a PR
  or a comment here is published the moment it is pushed; the sweep of the
  extracted tree at release time does not reach this repository's history,
  and nothing takes it back out. Scrub before pushing, not before releasing.
- **`README.md`'s document table deliberately does not list this file or
  `UPDATES-PUBLIC.md`.** README ships, and both of those are internal: one
  is export-ignored and the other is renamed to `UPDATES.md` at step 3, so
  listing either produced a link that resolves to nothing in the public
  tree — and the row naming the `dccore-dev` / `dccore` split pointed
  readers of the release at the development repository, which is not where
  they should look. It lists `UPDATES.md`, which
  is what the public tree ends up with. Do not add the rows back; the two
  of us do not need a table to find a file in `docs/`.

## Security fixes

A fix for a vulnerability never starts as a branch, an issue or a PR in
`dccore-dev`. Both repos are public: a branch named for the hole, a test
that reproduces it, or a commit message that explains it publishes the
attack to everyone running a released bot before the fix reaches them.

It goes through a **draft GitHub security advisory** on `dccore-dev` and the
advisory's **temporary private fork** instead. Only the owner, the
collaborators the advisory names and GitHub can see either.

1. **Open a draft advisory** (`dccore-dev` → Security → Advisories → New
   draft, or `POST repos/Ninja-FSE/dccore-dev/security-advisories`). Name the
   other maintainer as a collaborator on it. A report that arrives through
   private vulnerability reporting (`docs/SECURITY.md`) is an advisory
   waiting for triage: accept it, and continue from step 2.
2. **Create its temporary private fork** (the advisory's page, or
   `POST .../security-advisories/{ghsa_id}/forks`) and push the fix branch
   there - never to `origin`, not even for a moment. The first push can be
   refused for about 20 seconds while the fork is set up; push again.
3. **Review and test it there, as any PR.** Open the PR inside the fork; the
   other maintainer reviews it there. CI does not run in an advisory's fork,
   so run the full suite and `scripts/preflight.py` locally and say in the PR
   what ran and what it gave, with the mutants that prove the old code fails
   (`docs/CONVENTIONS.md`, rule 5).
4. **Keep the words neutral until it ships.** The commit message and the
   changelog fragment describe the behaviour ("copes better with ...", "no
   longer ..."), not the attack; the advisory itself holds the details.
5. **Ship it.** Merge the PR from the advisory page when the release that
   carries it is ready, so the fix and the release go out together, then
   publish the advisory. Only then may an issue, a release note or a comment
   here describe what it was.

Nothing in this repository is a safe place to park a security fix while it
waits - a pushed branch is public, a draft PR is public, and a force-push
does not unpublish what was already fetched.

## Access

Both repos: `Ninja-FSE` (owner) and `chchatzop` (collaborator, both repos).
No one else has merge rights on either. `dccore`'s branch protection
requires a PR with green CI to merge into `main` — including for releases
pushed by either of us — but does not require a second approver.
