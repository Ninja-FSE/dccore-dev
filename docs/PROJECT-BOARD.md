# The project board

The board tracks every issue and pull request in `Ninja-FSE/dccore-dev`, from the day it is filed to the release
that ships it: <https://github.com/users/Ninja-FSE/projects/1>. It is private, and both of us have write access.

This file is for the two of us. It names this repository's board, so `.gitattributes` keeps it out of the public
tree (`export-ignore`), and `tests/test_internal_files_do_not_ship.py` checks that it stays out.

## The four columns

| Status | Means | Gets there |
|---|---|---|
| **Todo** (yellow) | Filed. Nobody is working on it yet. | Automatically, for every new issue **and** every new PR. |
| **In Progress** (orange) | Someone has taken it, or its PR is open and not merged yet. | Mostly by hand. |
| **Ready for release** (green) | The PR is merged into `main` of dccore-dev, but is not in a release yet. | Automatically, when a PR is merged. |
| **Done** (purple) | Closed issues, and PRs that have shipped in a release. | Automatically, when an issue closes. PRs are moved here at the release. |

The **PR** carries the release state. When a PR with `Fixes #N` is merged, the issue closes and goes straight to
Done. The PR waits in Ready for release until the release ships it.

## What moves by itself, and what does not

The board's built-in workflows:

| When | The item goes to |
|---|---|
| An issue or PR is opened in dccore-dev (auto-add) | Todo, PRs included |
| A PR whose body says `Fixes #N` is opened | Issue #N goes to In Progress |
| A PR is merged | The PR goes to Ready for release |
| An issue or PR is closed | Done |

Two gaps you have to fill by hand:

- **A "Taken." comment moves nothing.** The issue stays in Todo until you set its Status yourself, or until your PR
  with `Fixes #N` is open.
- **A new PR lands in Todo,** not In Progress, because the workflow cannot tell PRs from issues. Set your own PR to
  In Progress when you open it.

The workflows can only be changed in the web UI (the board's `...` menu, then **Workflows**). The API has no way
to change them.

## Your workflow, start to finish

1. **Pick an issue in Todo.** Check its comments first: if someone has written "Taken.", it is theirs. Leave issues
   labelled `release` alone; they are done at release time.
2. **Claim it with a comment:** `Taken.`, or "Taken:" plus one line on your plan. Claiming first is what keeps two
   people from building the same fix.
   ```bash
   gh issue comment 1234 -R Ninja-FSE/dccore-dev -b "Taken."
   ```
3. **By hand: set the issue's Status to In Progress.** On the issue page, open the right-hand sidebar and go to
   **Projects → DCCore → Status**. You can also drag the card on the board.
4. **Build it on a branch from `main`.** Write a changelog fragment, `docs/changes/1234.md`, plus `1234.public.md`
   when users see the change (`docs/changes/README.md`). Run `python scripts/preflight.py` before you push.
5. **Open the PR.** Start the body with `Fixes #1234`, so the issue is linked and closes on merge. Add a type label
   and an area label (see below).
6. **By hand: set the PR's Status to In Progress.** Auto-add has just put it in Todo.
7. **Merge when CI is green and the review is done.** CI runs nine jobs: Linux, Windows and macOS, each with
   Python 3.10, 3.12 and 3.14. On merge, the PR goes to Ready for release, and the issue closes and goes to Done.
8. **By hand: put the merged PR in the "Next release" milestone.** That milestone is the list of what the next release
   carries, and the release PR's map is built from it.
   ```bash
   gh api -X PATCH repos/Ninja-FSE/dccore-dev/issues/1235 -F milestone=1
   ```

## Labels

| Label | Use it for |
|---|---|
| `bug` · `enhancement` · `documentation` | What kind of change it is. Every issue and PR gets one. |
| `area: bot` · `area: dashboard` · `area: mirc` · `area: site` | Which part it touches. Use more than one when it spans several. |
| `release` | Something to do or decide at the next release, such as a version bump. |
| `audit` · `severity: low` / `medium` / `high` | Findings from an audit, graded by severity. |
| `needs decision` | The operator has to choose before anyone builds it. |

One gotcha with gh 2.4.0: `-f "labels[]=bug"` silently sets no labels, both when an issue is created and on
`POST .../labels`. Send JSON instead, then read the labels back:

```bash
echo '{"labels":["bug","area: bot"]}' \
  | gh api repos/Ninja-FSE/dccore-dev/issues/1235/labels --input -
```

## Stacked PRs

When your fix builds on a PR that is not merged yet:

1. Branch from that PR's branch.
2. Open your PR with **that branch as its base**. The diff then shows only your change.
3. Say so in the first line of the body: "Stacked on #1234, merge that first".

Once the base PR is merged and its branch is deleted, GitHub retargets your PR to `main` by itself. CI runs on every
PR, whatever its base (`.github/workflows/tests.yml`).

## At a release

The operator does this. It is listed here so you know what the columns mean afterwards.

- Every PR in Ready for release moves to Done.
- The "Next release" milestone is renamed to the version (`v1.18.0`, say) and closed, and a new "Next release" is
  opened.
- Issues labelled `release` are done or decided as part of the release.

## Security fixes never go on the board

dccore-dev is public, and the board items link to public issues and PRs. A vulnerability goes through a **draft
GitHub security advisory** and its private fork. Never open an issue or PR for it, and never describe it in a
commit message or changelog fragment before it ships. The steps are in `docs/PUBLIC-REPO-WORKFLOW.md`, under
"Security fixes".

## For scripts: the IDs

Setting a Status from the command line goes through GraphQL. gh 2.4.0 has no `gh project` command.

| What | ID |
|---|---|
| Project | `PVT_kwHOAPs-PM4Bnc-9` |
| Status field | `PVTSSF_lAHOAPs-PM4Bnc-9zhmBi4c` |
| Todo | `d0ae6f00` |
| In Progress | `359fad9a` |
| Ready for release | `bcc7b117` |
| Done | `ed189220` |

Find the item's ID, then set its Status. This moves PR #1235 to In Progress. For an issue, use `issue(number:...)`
instead of `pullRequest`.

```bash
ITEM=$(gh api graphql -f query='query{repository(owner:"Ninja-FSE",name:"dccore-dev"){
  pullRequest(number:1235){projectItems(first:5){nodes{id project{id}}}}}}' \
  --jq '.data.repository.pullRequest.projectItems.nodes[]
        | select(.project.id=="PVT_kwHOAPs-PM4Bnc-9").id')

gh api graphql -f query="mutation{updateProjectV2ItemFieldValue(input:{
  projectId:\"PVT_kwHOAPs-PM4Bnc-9\", itemId:\"$ITEM\",
  fieldId:\"PVTSSF_lAHOAPs-PM4Bnc-9zhmBi4c\",
  value:{singleSelectOptionId:\"359fad9a\"}}){projectV2Item{id}}}"
```

**Never rewrite the Status options themselves** (names, colours) through the API without passing each option's
`id`. Without the ids, every option is created anew: the items lose their Status, and the built-in workflows break
and have to be set up again in the web UI.
