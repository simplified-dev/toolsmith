"""The end-of-branch ritual: push, open a pull request, merge, and clean up.

Eight steps hand-run at the end of every branch, in an order where a mistake at
step seven is the expensive one:

    git push -u origin <branch>
    <write the PR body to a file>
    gh pr create --base <base> --head <branch> --title ... --body-file ...
    gh pr merge <n> --merge
    git checkout <base>
    git pull --ff-only origin <base>
    <check that the base really contains the branch>
    git branch -d <branch>

The rules below are what the hand-run version keeps getting wrong:

  * A true merge leaves a MERGE COMMIT at the base tip, so the base sha is
    never equal to the branch sha. Sha equality is the natural way to write the
    validation and it is wrong on every successful merge; ancestry is the
    assertion that holds. The branch tip is read BEFORE the checkout, because
    the branch is about to be deleted.
  * The merge method is ``--merge`` and nothing else. Commits here are often
    independently gated units, and a squash destroys the per-commit revert
    granularity that gating produced. A caller asking for a squash or a rebase
    is refused rather than quietly honoured.
  * The deletion is ``git branch -d``, never ``-D``, and it runs only once the
    ancestry check has passed. ``-d`` refuses an unmerged branch, so it is the
    backstop if that check is ever wrong.
  * The base branch is DETECTED, from origin's head or ``gh repo view``. This
    workspace uses ``master`` and the next repo will not.
  * The PR body goes to a FILE. Bodies carry backticks, ``$`` and apostrophes,
    and a heredoc parse-errors on the long ones, so nothing here ever builds a
    body as a shell string.
  * The ritual dies between steps, so each step detects its own already-done
    state from the repository and the pull request rather than from a marker,
    and is reported as skipped instead of being re-run.

Every precondition is checked before anything mutates, so a refusal changes
nothing. Failure is data: every public function returns a dict and nothing
raises across the boundary. Human formatting belongs to the CLI adapter.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

# Git is local and fast; gh talks to GitHub, and a merge of a large PR is the
# slowest thing here.
_GIT_TIMEOUT = 120.0
_GH_TIMEOUT = 300.0

# The one merge method. Not a default a flag can move - see the module docstring.
MERGE_METHOD = "merge"

_MERGE_REASON = ("commits here are often independently gated units, and a squash or a "
                 "rebase destroys the per-commit revert granularity that gating produced")

_TTY_NOTE = ("merging is outward-facing and effectively irreversible, so an unattended run "
             "has to say so: pass --yes, or --no-merge to stop after the pull request")

_STALE_BASE_NOTE = ("the branch tip is not in the base after the merge - nothing was deleted; "
                    "check the pull request before re-running")

# Windows only: keep a child git or gh off the MCP server's console (see gradle.py).
_NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

# A push that needs credentials must fail rather than block on a prompt nobody
# is watching - this call can be several steps into a ritual that already
# mutated the remote.
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0"}

# The number in a pull request URL, which is what `gh pr create` prints.
_PR_URL_RE = re.compile(r"https://\S*?/pull/(\d+)")

# Which pull request a branch's finish is about, when the branch carries more
# than one. An OPEN one is the one to merge; a MERGED one means the ritual
# already got that far; a CLOSED one is neither and is only a fallback.
_PR_STATE_ORDER = {"OPEN": 0, "MERGED": 1, "CLOSED": 2}


def _run(program: str, args: Sequence[str], timeout: float, cwd: Path | str | None = None,
         env: dict[str, str] | None = None) -> dict:
    """Runs a child process and returns its outcome as data; never raises.

    Args:
        program: executable name, resolved on PATH.
        args: arguments after the program name.
        timeout: seconds before the call is abandoned.
        cwd: directory to run in.
        env: extra environment variables layered over this process's own.

    Returns:
        dict with command, code (int, or None when the process never ran), out,
        err and ok. A missing executable and a timeout both yield code None,
        which is how a missing gh is told from a gh that answered unhappily.
    """
    command = " ".join([program, *args])
    try:
        proc = subprocess.run(
            [program, *args],
            cwd=None if cwd is None else str(cwd),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_NO_CONSOLE,
            env={**os.environ, **(env or {})},
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"command": command, "code": None, "out": "", "err": str(exc), "ok": False}
    return {"command": command, "code": proc.returncode, "out": proc.stdout.strip(),
            "err": proc.stderr.strip(), "ok": proc.returncode == 0}


def _git_run(repo_dir: Path | str, *args: str, timeout: float = _GIT_TIMEOUT) -> dict:
    """Runs git in repo_dir and returns the whole outcome, stderr included."""
    return _run("git", ["-C", str(repo_dir), *args], timeout, env=_GIT_ENV)


def _git(repo_dir: Path | str, *args: str, timeout: float = _GIT_TIMEOUT) -> str | None:
    """Runs git in repo_dir and returns stripped stdout, or None if it failed.

    "" and None are DIFFERENT answers and callers must keep them apart, as they
    are in jitpack.py. "" is a command that ran and printed nothing - a branch
    origin does not carry, a range with no commits in it. None is a command that
    never answered: a non-zero exit, a timeout, a stale index.lock, git missing
    from PATH. Reading the two alike turns a failed query into a confident
    negative, and here that negative would skip a step rather than run it.

    Args:
        repo_dir: directory to run in (git's -C argument).
        args: git arguments after -C.
        timeout: seconds before the call is abandoned.

    Returns:
        Stripped stdout, or None for a non-zero exit, a timeout, or a missing
        git - never "" for those.
    """
    outcome = _git_run(repo_dir, *args, timeout=timeout)
    return outcome["out"] if outcome["ok"] else None


def _gh(repo_dir: Path | str, *args: str, timeout: float = _GH_TIMEOUT) -> dict:
    """Runs gh against repo_dir and returns the outcome as data.

    The one door to GitHub in this module, and the seam the tests replace: every
    push, pull request and merge either goes through here or does not happen.

    gh carries no -C flag: it reads the repository from the working directory,
    so repo_dir is the child's cwd rather than an argument.

    Args:
        repo_dir: repository the command runs against.
        args: gh arguments.
        timeout: seconds before the call is abandoned.

    Returns:
        The dict _run returns.
    """
    # No prompts and no pager: this runs unattended as often as not, and gh
    # paging its own json output would hang the step.
    return _run("gh", list(args), timeout, cwd=repo_dir,
                env={"GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat", "GH_NO_UPDATE_NOTIFIER": "1"})


def _walk_up_git(start: Path) -> Path | None:
    """Walks up from start to the nearest directory holding a .git entry.

    Mirrors modules.find_gradle_root. .git is a file rather than a directory in
    a worktree, so existence is the test.
    """
    try:
        current = start.resolve()
    except OSError:
        return None
    while True:
        if (current / ".git").exists():
            return current
        if current == current.parent:
            return None
        current = current.parent


def _remote_head(repo_dir: Path, branch: str) -> str | None:
    """The sha origin holds for branch.

    Returns:
        The 40-char sha, "" when origin answered and does not carry the branch,
        or None when the query did not answer at all. A caller may skip work on
        "" and must not skip it on None: an unanswered query that read as "the
        branch is gone" would silently drop the deletion it was gating.
    """
    out = _git(repo_dir, "ls-remote", "--heads", "origin", branch)
    if out is None:
        return None
    fields = out.split()
    return fields[0] if fields else ""


def _stdin_is_tty() -> bool:
    """Whether a human is available to answer a prompt."""
    try:
        return bool(sys.stdin is not None and sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def _tty_confirm(question: str) -> bool:
    """Asks on the terminal, defaulting to no on anything but an explicit yes."""
    try:
        answer = input(question)
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def _short(sha: str | None) -> str:
    """The 7-char form of a sha, for a human-facing line."""
    return (sha or "")[:7] or "-"


def exit_code(result: dict) -> int:
    """Maps any result from this module onto the toolsmith 0/1/2 convention.

    Args:
        result: a dict returned by branch_finish.

    Returns:
        0 when the ritual finished, stopped where it was asked to stop, or only
        printed a plan; 1 for an ordinary failed verdict (a step that ran and
        failed, a declined confirmation, a base that does not contain the
        branch); 2 for a precondition failure, where nothing was attempted.
    """
    if result.get("status") == "precondition":
        return 2
    return 0 if result.get("ok") else 1


def _detect_base(repo_dir: Path, gh: Callable[..., dict], explicit: str | None) -> dict:
    """Detects the repository's base branch. Never assumes master or main.

    Origin's own head is the cheap answer and the local one; ``gh repo view``
    is asked only when the repository has no remote head recorded, which is
    normal for a clone that has never run ``git remote set-head``.

    Args:
        repo_dir: the git repository.
        gh: the gh runner.
        explicit: a caller-supplied base, which wins over any detection.

    Returns:
        dict with base and source, or a dict carrying error naming the remedy.
    """
    if explicit:
        return {"base": explicit, "source": "--base"}

    head = _git(repo_dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head:
        # "origin/master" -> "master". The remote is known to be origin by now.
        return {"base": head.split("/", 1)[1] if "/" in head else head,
                "source": "refs/remotes/origin/HEAD"}

    view = gh(repo_dir, "repo", "view", "--json", "defaultBranchRef",
              "-q", ".defaultBranchRef.name")
    if view["ok"] and view["out"].strip():
        return {"base": view["out"].strip().splitlines()[0].strip(), "source": "gh repo view"}

    return {"error": "cannot detect the base branch - origin records no head and gh repo "
                     "view did not answer; run 'git remote set-head origin -a', or pass "
                     "--base"}


def _base_ref(repo_dir: Path, base: str) -> str | None:
    """The ref the base is compared against, preferring origin's copy.

    origin/<base> is what the pull request will merge into, so it is the honest
    thing to count commits against; a repository that has never fetched the base
    falls back to its local branch.
    """
    for candidate in (f"refs/remotes/origin/{base}", f"refs/heads/{base}"):
        if _git(repo_dir, "rev-parse", "--verify", "--quiet", candidate):
            return candidate
    return None


def _existing_pr(repo_dir: Path, gh: Callable[..., dict], branch: str) -> dict:
    """Finds the pull request already open or merged for this head branch.

    This is what makes a re-run a resume rather than a second pull request: a
    ``gh pr create`` for a head that already has one fails, and the failure says
    nothing about which number to merge.

    Args:
        repo_dir: the git repository.
        gh: the gh runner.
        branch: the head branch.

    Returns:
        dict with pr (a dict carrying number, state, url and title, or None), or
        a dict carrying error when gh did not answer with usable json.
    """
    listing = gh(repo_dir, "pr", "list", "--head", branch, "--state", "all",
                 "--json", "number,state,url,title", "--limit", "20")
    if not listing["ok"]:
        return {"error": f"gh pr list failed for head '{branch}': "
                         f"{listing['err'] or listing['out'] or 'no output'}"}
    try:
        rows = json.loads(listing["out"] or "[]")
    except (json.JSONDecodeError, ValueError):
        return {"error": f"gh pr list did not answer with json: {listing['out'][:200]}"}
    if not isinstance(rows, list):
        return {"error": "gh pr list did not answer with a list of pull requests"}

    usable = [row for row in rows if isinstance(row, dict) and row.get("number")]
    if not usable:
        return {"pr": None}
    usable.sort(key=lambda row: (_PR_STATE_ORDER.get(str(row.get("state", "")).upper(), 3),
                                 -int(row["number"])))
    return {"pr": usable[0]}


def _commit_subjects(repo_dir: Path, base_ref: str, branch: str) -> list[str]:
    """The branch's commit subjects, oldest first."""
    out = _git(repo_dir, "log", "--reverse", "--format=%s", f"{base_ref}..{branch}")
    return [line.strip() for line in (out or "").splitlines() if line.strip()]


def _compose_body(subjects: Sequence[str], branch: str) -> str:
    """Builds a pull request body from the branch's commit subjects.

    A file rather than a string is the whole point: subjects carry backticks,
    ``$`` and apostrophes, so a body assembled for a shell is a parse error
    waiting for the first commit message that uses one.
    """
    lines = [f"- {subject}" for subject in subjects]
    return "\n".join(lines or [f"- {branch}"]) + "\n"


def _write_body(text: str) -> Path:
    """Writes a composed body to a temp file and returns its path.

    newline="\\n" because the default rewrites every newline on Windows, and a
    markdown body is read back by gh rather than by a Windows editor.
    """
    handle, name = tempfile.mkstemp(prefix="toolsmith-pr-", suffix=".md", text=True)
    os.close(handle)
    path = Path(name)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _pr_from_output(text: str) -> dict | None:
    """Reads the pull request `gh pr create` printed the URL of, or None."""
    match = _PR_URL_RE.search(text or "")
    if match is None:
        return None
    return {"number": int(match.group(1)), "state": "OPEN", "url": match.group(0)}


def _failure_text(outcome: dict) -> str:
    """One line saying how a command failed, preferring what it said itself."""
    said = outcome.get("err") or outcome.get("out") or ""
    first = next((line for line in said.splitlines() if line.strip()), "")
    code = outcome.get("code")
    where = f"exit {code}" if code is not None else "did not run"
    return f"{where}: {first}" if first else where


def branch_finish(repo: str | Path | None = None, *, title: str | None = None,
                  body_file: str | Path | None = None, base: str | None = None,
                  merge_method: str = MERGE_METHOD, delete_remote: bool = False,
                  no_merge: bool = False, dry_run: bool = False, assume_yes: bool = False,
                  gh: Callable[..., dict] | None = None,
                  confirm: Callable[[str], bool] | None = None) -> dict:
    """Runs the end-of-branch ritual, resuming whatever part of it already ran.

    Every precondition is established before the first push, so a refusal leaves
    the repository, the remote and the pull request exactly as they were. After
    that each step decides for itself whether it has already happened - origin
    already carrying the branch tip, a pull request already open for this head,
    that pull request already merged - and a step that has is reported as
    skipped rather than re-run.

    The merge is ``gh pr merge --merge``. A squash or a rebase is refused rather
    than honoured, because commits here are often independently gated units and
    flattening them destroys the per-commit revert granularity that gating
    produced.

    Args:
        repo: any path inside the repository, or None for the current directory.
        title: pull request title. None takes the branch's last commit subject,
            falling back to the branch name.
        body_file: an existing file to use as the pull request body. None
            composes one from the branch's commit subjects and writes it to a
            temp file, which is what gets passed to gh - never a shell string.
        base: base branch, overriding detection. Detection reads origin's head
            and then gh, and nothing here assumes master or main.
        merge_method: the only accepted value is "merge".
        delete_remote: also delete the remote branch. Off by default, since that
            is a separate decision from cleaning up locally.
        no_merge: push and open the pull request, then stop, for when review is
            wanted before the merge.
        dry_run: print the ordered plan and mutate nothing.
        assume_yes: merge without asking. Without it a merge needs a terminal to
            ask at, and an unattended run is refused before it mutates anything.
        gh: the gh runner, for tests. Defaults to the real one.
        confirm: asks the caller's question and returns their answer. Defaults
            to a terminal prompt, and its absence on a non-terminal is what
            makes an unattended merge a precondition failure.

    Returns:
        dict with repo_dir, branch, base, base_source, branch_sha, title, ahead,
        pr, steps, skipped, ok and status, where status is one of:

          "finished"     - the whole ritual ran or was already done; ok
          "opened"       - --no-merge, so it stopped with the pull request open; ok
          "planned"      - --dry-run; steps carry the plan and nothing ran; ok
          "declined"     - the merge confirmation was answered no
          "failed"       - a step ran and failed; error says which
          "precondition" - nothing was attempted; error says why

        Each step carries name, state (done, skipped, planned or failed),
        command and detail. "precondition" is exit-code-2 territory for the
        caller; every other non-ok status is exit code 1.
    """
    runner = gh or _gh
    info: dict = {"repo_dir": None, "branch": None, "base": None, "base_source": None,
                  "branch_sha": None, "title": None, "ahead": None, "pr": None,
                  "merge_method": merge_method, "dry_run": dry_run}

    def refuse(message: str, **extra) -> dict:
        return {**info, "ok": False, "status": "precondition", "steps": [], "skipped": [],
                "error": message, **extra}

    # ---- A. Pre-flight. Every failure here changes nothing. ----
    if merge_method != MERGE_METHOD:
        return refuse(f"merge method '{merge_method}' is refused - finish merges with "
                      f"'gh pr merge --merge' and nothing else, because {_MERGE_REASON}")

    start = Path(repo) if repo is not None else Path.cwd()
    repo_dir = _walk_up_git(start)
    if repo_dir is None:
        return refuse(f"'{start}' is not inside a git repository")
    info["repo_dir"] = str(repo_dir)

    dirty = _git(repo_dir, "status", "--porcelain")
    if dirty is None:
        return refuse(f"git failed in '{repo_dir}' - could not read the working tree status "
                      f"(is git on PATH?)")
    if dirty:
        return refuse(f"working tree is not clean - {len(dirty.splitlines())} path(s) modified "
                      f"or untracked; commit or stash them first")

    branch = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
    if not branch:
        return refuse(f"git failed in '{repo_dir}' - could not read the current branch")
    if branch == "HEAD":
        return refuse("HEAD is detached - finish runs from the branch it is finishing")
    info["branch"] = branch

    remotes = _git(repo_dir, "remote")
    if remotes is None:
        return refuse(f"git failed in '{repo_dir}' - could not list remotes")
    if "origin" not in remotes.split():
        return refuse(f"'{repo_dir}' has no 'origin' remote to push to")

    detected = _detect_base(repo_dir, runner, base)
    if detected.get("error"):
        return refuse(detected["error"])
    base_name = detected["base"]
    info["base"] = base_name
    info["base_source"] = detected["source"]
    if branch == base_name:
        return refuse(f"already on the base branch '{base_name}' - finish runs from the branch "
                      f"it is finishing")
    base_ref = _base_ref(repo_dir, base_name)
    if base_ref is None:
        return refuse(f"base branch '{base_name}' exists neither locally nor on origin")

    auth = runner(repo_dir, "auth", "status")
    if not auth["ok"]:
        return refuse(f"gh is missing or not authenticated ({_failure_text(auth)}) - "
                      f"run 'gh auth login'")

    given_body = Path(body_file) if body_file else None
    if given_body is not None and not given_body.is_file():
        return refuse(f"--body-file '{given_body}' does not exist")

    # Read BEFORE anything checks out or deletes anything: this sha is what the
    # post-merge validation asserts against, and the branch that carries it is
    # about to be deleted.
    branch_sha = _git(repo_dir, "rev-parse", "HEAD")
    if not branch_sha:
        return refuse(f"git failed in '{repo_dir}' - could not read the branch tip")
    info["branch_sha"] = branch_sha

    found = _existing_pr(repo_dir, runner, branch)
    if found.get("error"):
        return refuse(found["error"])
    pr = found["pr"]
    info["pr"] = pr
    already_merged = str((pr or {}).get("state", "")).upper() == "MERGED"

    counted = _git(repo_dir, "rev-list", "--count", f"{base_ref}..{branch}")
    try:
        ahead = int((counted or "").strip())
    except ValueError:
        return refuse(f"could not count what '{branch}' carries over '{base_name}'")
    info["ahead"] = ahead
    if ahead == 0 and not already_merged:
        return refuse(f"'{branch}' carries no commit '{base_name}' does not already have")

    subjects = _commit_subjects(repo_dir, base_ref, branch)
    resolved_title = title or (subjects[-1] if subjects else "") or branch
    info["title"] = resolved_title

    # The merge is the outward-facing, effectively irreversible step, so it is
    # the one that needs an answer. An unattended run has nowhere to ask, and is
    # refused here rather than after it has already pushed and opened a PR.
    merging = not no_merge and not already_merged
    confirmer = confirm
    if merging and not dry_run and not assume_yes and confirmer is None:
        if not _stdin_is_tty():
            return refuse(f"a merge needs a confirmation this run cannot ask for - stdin is "
                          f"not a terminal; {_TTY_NOTE}")
        confirmer = _tty_confirm

    # ---- B. The ritual. From here on things change. ----
    steps: list[dict] = []

    def record(name: str, state: str, command: str = "", detail: str = "") -> None:
        steps.append({"name": name, "state": state, "command": command, "detail": detail})

    def done(status: str) -> dict:
        return {**info, "ok": status in ("finished", "opened", "planned"), "status": status,
                "steps": steps,
                "skipped": [step["name"] for step in steps if step["state"] == "skipped"]}

    def failed(message: str, **extra) -> dict:
        return {**done("failed"), "error": message, **extra}

    def git_step(name: str, args: Sequence[str], detail: str = "") -> dict | None:
        """Runs one git step and records it; returns a failure result or None."""
        command = "git " + " ".join(args)
        if dry_run:
            record(name, "planned", command, detail)
            return None
        outcome = _git_run(repo_dir, *args)
        if not outcome["ok"]:
            record(name, "failed", command, _failure_text(outcome))
            return failed(f"{command} failed - {_failure_text(outcome)}")
        record(name, "done", command, detail)
        return None

    # ---- push ----
    push_command = f"git push -u origin {branch}"
    if _remote_head(repo_dir, branch) == branch_sha:
        record("push", "skipped", push_command,
               f"origin/{branch} is already at {_short(branch_sha)}")
    else:
        outcome = git_step("push", ["push", "-u", "origin", branch],
                           f"{_short(branch_sha)} to origin/{branch}")
        if outcome is not None:
            return outcome

    # ---- body ----
    body_path = given_body
    if pr is not None:
        record("body", "skipped", "write the pull request body",
               f"pull request #{pr['number']} already exists")
    elif given_body is not None:
        record("body", "planned" if dry_run else "done", f"read {given_body}",
               "--body-file, used verbatim")
    elif dry_run:
        record("body", "planned", "write the pull request body to a temp file",
               f"{len(subjects)} commit subject(s)")
    else:
        body_path = _write_body(_compose_body(subjects, branch))
        record("body", "done", "write the pull request body to a temp file",
               f"{len(subjects)} commit subject(s) -> {body_path}")

    # ---- pr ----
    pr_command = f"gh pr create --base {base_name} --head {branch} --title ... --body-file ..."
    if pr is not None:
        record("pr", "skipped", pr_command,
               f"#{pr['number']} {str(pr.get('state', '')).lower()} {pr.get('url', '')}".rstrip())
    elif dry_run:
        record("pr", "planned", pr_command, f"title: {resolved_title}")
    else:
        created = runner(repo_dir, "pr", "create", "--base", base_name, "--head", branch,
                         "--title", resolved_title, "--body-file", str(body_path))
        if not created["ok"]:
            record("pr", "failed", pr_command, _failure_text(created))
            return failed(f"gh pr create failed - {_failure_text(created)}")
        # gh prints the new pull request's URL; a re-query is the fallback for a
        # gh whose output shape is not that.
        pr = _pr_from_output(created["out"]) or _existing_pr(repo_dir, runner, branch).get("pr")
        if pr is None:
            record("pr", "failed", pr_command, "the pull request number could not be read")
            return failed("the pull request was created and its number could not be read - "
                          "re-run to pick it up")
        info["pr"] = pr
        record("pr", "done", pr_command, f"#{pr['number']} {pr.get('url', '')}".rstrip())

    number = (pr or {}).get("number")

    # ---- merge ----
    merge_command = f"gh pr merge {number if number else '<n>'} --merge"
    if no_merge:
        record("merge", "skipped", merge_command,
               "--no-merge: stopping with the pull request open")
        return done("planned" if dry_run else "opened")
    if already_merged:
        record("merge", "skipped", merge_command, f"pull request #{number} is already merged")
    elif dry_run:
        record("merge", "planned", merge_command,
               "a merge commit, never a squash and never a rebase")
    elif number is None:
        record("merge", "failed", merge_command, "no pull request number to merge")
        return failed("there is no pull request to merge")
    else:
        question = (f"merge pull request #{number} into '{base_name}' with a merge commit, "
                    f"then delete '{branch}' locally? [y/N] ")
        if not assume_yes and not confirmer(question):
            record("merge", "skipped", merge_command, "declined at the prompt")
            return {**done("declined"),
                    "error": "the merge was declined - the pull request is open and nothing "
                             "was deleted"}
        merged = runner(repo_dir, "pr", "merge", str(number), "--merge")
        if not merged["ok"]:
            record("merge", "failed", merge_command, _failure_text(merged))
            return failed(f"gh pr merge failed - {_failure_text(merged)}")
        info["pr"] = {**pr, "state": "MERGED"}
        record("merge", "done", merge_command, f"#{number} merged into {base_name}")

    # ---- checkout ----
    checkout_command = f"git checkout {base_name}"
    if _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD") == base_name:
        record("checkout", "skipped", checkout_command, f"already on '{base_name}'")
    else:
        outcome = git_step("checkout", ["checkout", base_name], f"{branch} -> {base_name}")
        if outcome is not None:
            return outcome

    # ---- pull ----
    outcome = git_step("pull", ["pull", "--ff-only", "origin", base_name],
                       f"fast-forward '{base_name}' onto the merge")
    if outcome is not None:
        return outcome

    # ---- validate ----
    validate_command = f"git merge-base --is-ancestor {_short(branch_sha)} {base_name}"
    if dry_run:
        record("validate", "planned", validate_command, "ancestry, never sha equality")
    else:
        # ANCESTRY, not equality. A true merge leaves a MERGE COMMIT at the base
        # tip, so `git rev-parse <base>` and `git rev-parse <branch>` differ on
        # every successful merge and comparing them would fail the ritual it is
        # supposed to be confirming. What actually has to hold is that the
        # branch tip is reachable from the base tip, which is what --is-ancestor
        # answers. The sha it is asked about was captured before the checkout,
        # since the branch is about to be deleted.
        ancestry = _git_run(repo_dir, "merge-base", "--is-ancestor", branch_sha, base_name)
        if not ancestry["ok"]:
            record("validate", "failed", validate_command,
                   f"'{base_name}' does not contain {_short(branch_sha)}")
            return failed(f"'{base_name}' does not contain '{branch}' at "
                          f"{_short(branch_sha)} - the merge did not land",
                          note=_STALE_BASE_NOTE)
        record("validate", "done", validate_command,
               f"'{base_name}' contains {_short(branch_sha)}")

    # ---- delete-local ----
    delete_command = f"git branch -d {branch}"
    if _git(repo_dir, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") is None:
        record("delete-local", "skipped", delete_command, "the local branch is already gone")
    else:
        # -d and never -D: it refuses a branch the base does not contain, which
        # is the backstop for the check above ever being wrong.
        outcome = git_step("delete-local", ["branch", "-d", branch], f"deleted '{branch}'")
        if outcome is not None:
            return outcome

    # ---- delete-remote ----
    remote_command = f"git push origin --delete {branch}"
    if not delete_remote:
        record("delete-remote", "skipped", remote_command,
               "--delete-remote not given - the remote branch is its own decision")
    elif _remote_head(repo_dir, branch) == "":
        record("delete-remote", "skipped", remote_command, f"origin/{branch} is already gone")
    else:
        outcome = git_step("delete-remote", ["push", "origin", "--delete", branch],
                           f"deleted origin/{branch}")
        if outcome is not None:
            return outcome

    return done("planned" if dry_run else "finished")
