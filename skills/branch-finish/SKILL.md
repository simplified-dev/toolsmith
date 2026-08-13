---
name: branch-finish
description: Finish a branch - push, open a pull request, merge it with a merge commit, check out and pull the base, verify the base really contains the branch, and delete the local branch. Auto-invoked when the USER asks to "finish this branch", "open a PR and merge it", "land this branch", "merge and clean up", or before Claude hand-runs that sequence of git and gh commands. Routes to `toolsmith branch finish`, which detects the base branch, resumes a ritual that died half way, asserts ANCESTRY rather than sha equality after the merge, and deletes with `git branch -d`. CLI only and never on Claude's own initiative - it pushes, opens and merges, so the user decides when it runs.
auto_invoke: true
tags: [git, github, gh, pull-request, merge, branch, cleanup]
---

# branch-finish

The end-of-branch ritual as one command instead of eight hand-run steps.

```bash
toolsmith branch finish            # the whole thing, prompting before the merge
toolsmith branch finish --dry-run  # the ordered plan, mutating nothing
toolsmith branch finish ar         # name the repository rather than standing in it
```

The repository is named like every other command names one - a module shorthand,
a module name, or a path - and with no argument it reads the current directory.
A token matching neither a known module nor a directory is refused rather than
falling back to the current directory. A repository that is not a discovered
gradle module is named by path.

## The user decides when this runs

Invoke it when the user asks for it, and not otherwise. It pushes, opens a pull
request and merges it: all three are outward-facing, and the merge is
effectively irreversible. There is deliberately no MCP tool for it, so it stays
something a human asks for rather than something an agent can reach for on its
own initiative.

Read the plan back to the user before running it for the first time on a
repository:

```bash
toolsmith branch finish --dry-run
```

## What it does, in order

| Step | Command |
|---|---|
| push | `git push -u origin <branch>` |
| body | compose the pull request body from the commit subjects, into a file |
| pr | `gh pr create --base <base> --head <branch> --title ... --body-file ...` |
| merge | `gh pr merge <n> --merge` |
| checkout | `git checkout <base>` |
| pull | `git pull --ff-only origin <base>` |
| validate | `git merge-base --is-ancestor <branch-sha> <base>` |
| delete-local | `git branch -d <branch>` |
| delete-remote | `git push origin --delete <branch>`, only with `--delete-remote` |

## Rules worth knowing before you edit or replace this

- **The merge method is `--merge`.** Not squash, not rebase. Commits here are
  often independently gated units, and flattening them destroys the per-commit
  revert granularity that gating produced. `--squash` and `--rebase` exist as
  flags only so that asking for one gets the reason rather than an argparse
  usage error.
- **The post-merge check is ancestry, never equality.** A true merge leaves a
  merge commit at the base tip, so `git rev-parse <base>` and
  `git rev-parse <branch>` differ on every successful merge and comparing them
  fails the ritual it is meant to confirm. The branch tip is captured before the
  checkout, since the branch is about to be deleted.
- **The delete is `git branch -d`.** `-d` refuses a branch the base does not
  contain, which is the backstop if the check above is ever wrong. Never `-D`.
- **The base branch is detected**, from origin's head and then `gh repo view`.
  Do not pass `--base master` out of habit; the next repository will not use it.
- **The pull request body goes to a file.** Bodies carry backticks, `$` and
  apostrophes, so a heredoc parse-errors on the long ones.

## Flags

| Flag | What it does |
|---|---|
| `MODULE` | positional: module shorthand, module name, or a path inside the repository (default: the current directory) |
| `--title` | pull request title (default: the branch's last commit subject) |
| `--body-file FILE` | use this body instead of one composed from commit subjects |
| `--base BRANCH` | override base detection |
| `--delete-remote` | also delete the remote branch (off by default) |
| `--no-merge` | push and open the pull request, then stop, for when review is wanted |
| `--dry-run` | print the ordered plan and mutate nothing |
| `--yes` | merge without asking |

## Confirming the merge

With a terminal, it asks before merging. Without one - a piped run, a hook, an
agent - it refuses before it mutates anything unless `--yes` is passed. So an
unattended run either says `--yes` outright or says `--no-merge` and leaves the
merge to a human.

## Resuming

The ritual dies between steps often enough that re-running it is the normal
recovery, and a re-run continues rather than repeating:

- a branch origin already carries at the same sha skips the push
- a pull request already open for this head is reused, never opened twice
- a pull request already merged skips the merge and goes on to the checkout,
  pull, validation and delete

The skipped steps are named in the output, so a resumed run says what it found
already done.

## Preconditions

All checked before anything mutates, and any failure exits 2 having changed
nothing: inside a git repository, a clean working tree, not already on the base
branch, an `origin` remote, `gh` present and authenticated, and at least one
commit the base does not have.

## Exit codes

- `0` finished, stopped where `--no-merge` asked it to, or printed a plan
- `1` a step ran and failed, the confirmation was declined, or the base does not
  contain the branch after the merge
- `2` a precondition failed and nothing was attempted

## Cross-reference

Gate the branch before finishing it: `gradle-verify-gate` for a Java change,
then `toolsmith branch finish`. For a module whose sha other modules pin, the
next step after the merge is `toolsmith jitpack build <module>` and the
`jitpack order` / `jitpack set` cascade.
