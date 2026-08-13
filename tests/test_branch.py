"""Tests for the end-of-branch ritual.

Fully offline. The git half runs against REAL repositories under tmp_path - a
bare repo standing in for origin and a working clone beside it - because the one
rule this module exists to keep is a fact about what git does after a merge, and
a faked git could be made to agree with a wrong rule. The gh half goes through
branch_finish's injectable ``gh`` seam.

The fake gh performs a genuine merge in the origin repository rather than
pretending to, so test_the_whole_ritual_runs_and_the_branch_lands sees the merge
COMMIT vanilla gh would have produced. That is the test that pins the assertion:
after a true merge the base tip is a merge commit and is NOT the branch tip, so
a sha-equality check fails on every successful merge, and ancestry is what holds.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from toolsmith import branch as branch_mod
from toolsmith import cli, modules
from toolsmith.branch import branch_finish, exit_code

BRANCH = "feature/the-thing"
SUBJECTS = ("Add the first thing", "Add the second thing")


# --------------------------------------------------------------------------
# Real git, in a real repository.
# --------------------------------------------------------------------------

def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Runs a command with no stdin.

    stdin=DEVNULL rather than the inherited handle: under pytest's capture the
    process has no valid standard input to hand a child, and on Windows
    inheriting it raises before the child ever starts.
    """
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _git(cwd: Path, *args: str) -> str:
    """Runs git in cwd and returns stdout, failing the test if git failed."""
    proc = _run(["git", "-C", str(cwd), *args])
    assert proc.returncode == 0, f"git {' '.join(args)}\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout.strip()


def _git_rc(cwd: Path, *args: str) -> int:
    """Runs git in cwd and returns its exit code, whatever it is."""
    return _run(["git", "-C", str(cwd), *args]).returncode


def _configure(repo: Path) -> None:
    """Pins the settings a developer's global config would otherwise supply.

    Signing and hooks especially: a globally configured signing key or
    core.hooksPath would reach into these temp repositories and make the suite
    depend on the machine it runs on.
    """
    for key, value in (("user.email", "toolsmith@example.invalid"),
                       ("user.name", "Toolsmith Test"),
                       ("commit.gpgsign", "false"),
                       ("tag.gpgsign", "false"),
                       ("core.autocrlf", "false"),
                       ("core.fsmonitor", "false"),
                       ("core.hooksPath", str(repo / ".git" / "no-such-hooks")),
                       ("pull.rebase", "false")):
        _git(repo, "config", key, value)


def _commit(repo: Path, name: str, text: str, subject: str) -> None:
    (repo / name).write_text(text, encoding="utf-8", newline="\n")
    _git(repo, "add", "--", name)
    _git(repo, "commit", "-m", subject)


class _Workspace:
    """A bare origin, a working clone on a feature branch, and a scratch dir."""

    def __init__(self, work: Path, origin: Path, scratch: Path, base: str, branch: str) -> None:
        self.work = work
        self.origin = origin
        self.scratch = scratch
        self.base = base
        self.branch = branch

    def sha(self, rev: str) -> str:
        return _git(self.work, "rev-parse", rev)

    def current(self) -> str:
        return _git(self.work, "rev-parse", "--abbrev-ref", "HEAD")

    def locals(self) -> list[str]:
        return _git(self.work, "for-each-ref", "--format=%(refname:short)",
                    "refs/heads").splitlines()

    def remotes(self) -> list[str]:
        return _git(self.work, "ls-remote", "--heads", "origin").splitlines()

    def snapshot(self) -> tuple:
        """Everything a mutating step would move, for a dry run to be held to."""
        return (self.current(), tuple(self.locals()), tuple(self.remotes()),
                _git(self.work, "status", "--porcelain"),
                _git(self.work, "for-each-ref", "--format=%(refname) %(objectname)"))


def _make_workspace(tmp_path: Path, *, base: str = "master", branch: str = BRANCH,
                    subjects: tuple[str, ...] = SUBJECTS) -> _Workspace:
    origin = tmp_path / "origin.git"
    assert _run(["git", "init", "--bare", "-b", base, str(origin)]).returncode == 0
    work = tmp_path / "work"
    assert _run(["git", "init", "-b", base, str(work)]).returncode == 0
    _configure(work)
    _commit(work, "README.md", "base\n", "Initial commit")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", base)
    # What branch_finish reads to detect the base. A clone gets this for free;
    # a repo built by hand has to be told.
    _git(work, "remote", "set-head", "origin", "-a")
    _git(work, "checkout", "-b", branch)
    for index, subject in enumerate(subjects):
        _commit(work, f"file-{index}.txt", f"{index}\n", subject)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return _Workspace(work, origin, scratch, base, branch)


@pytest.fixture
def ws(tmp_path) -> _Workspace:
    return _make_workspace(tmp_path)


@pytest.fixture(autouse=True)
def _temp_bodies(tmp_path, monkeypatch):
    """Keeps composed pull request bodies inside the test's own tmp_path."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


# --------------------------------------------------------------------------
# The gh seam.
# --------------------------------------------------------------------------

def _ok(out: str = "") -> dict:
    return {"command": "gh", "code": 0, "out": out, "err": "", "ok": True}


def _fail(err: str, code: int = 1) -> dict:
    return {"command": "gh", "code": code, "out": "", "err": err, "ok": False}


def _flag(args: tuple[str, ...], name: str) -> str | None:
    """The value after a flag, or None when the flag is absent."""
    for index, value in enumerate(args):
        if value == name and index + 1 < len(args):
            return args[index + 1]
    return None


class _FakeGh:
    """Stand-in for the gh CLI that lands a REAL merge commit in origin.

    ``pr merge`` clones origin, merges the head branch into the base with
    --no-ff and pushes, which is what vanilla gh's merge-commit method leaves
    behind. Nothing else in this file would otherwise see the merge commit that
    makes sha equality the wrong assertion.

    Args:
        origin: the bare repository standing in for GitHub.
        scratch: a directory the merge clones live in.
        default_branch: what `gh repo view` answers.
        authed: whether `gh auth status` succeeds.
        land: False models a merge gh REPORTS but does not land, which is what
            the post-merge validation exists to catch.
    """

    def __init__(self, origin: Path, scratch: Path, *, default_branch: str = "master",
                 authed: bool = True, land: bool = True) -> None:
        self.origin = origin
        self.scratch = scratch
        self.default_branch = default_branch
        self.authed = authed
        self.land = land
        self.calls: list[list[str]] = []
        self.prs: list[dict] = []
        self.bodies: dict[int, str] = {}
        self._next = 1

    # -- what the tests assert on -------------------------------------------

    @property
    def commands(self) -> list[str]:
        return [" ".join(call) for call in self.calls]

    @property
    def mutating(self) -> list[str]:
        """The calls that reach GitHub with a change, by name alone."""
        return [" ".join(call[:2]) for call in self.calls
                if tuple(call[:2]) in (("pr", "create"), ("pr", "merge"))]

    def add_pr(self, head: str, base: str = "master", *, state: str = "OPEN",
               title: str = "an earlier run") -> dict:
        """Seeds a pull request an earlier run of the ritual left behind."""
        number = self._next
        self._next += 1
        pr = {"number": number, "state": state, "title": title, "head": head, "base": base,
              "url": f"https://github.com/acme/repo/pull/{number}"}
        self.prs.append(pr)
        return pr

    # -- the seam -----------------------------------------------------------

    def __call__(self, repo_dir, *args: str, timeout: float | None = None) -> dict:
        self.calls.append(list(args))
        head = tuple(args[:2])
        if head == ("auth", "status"):
            return _ok("Logged in to github.com") if self.authed else _fail("gh: not logged in")
        if head == ("repo", "view"):
            return _ok(self.default_branch)
        if head == ("pr", "list"):
            return _ok(json.dumps([{key: pr[key] for key in ("number", "state", "url", "title")}
                                   for pr in self.prs if pr["head"] == _flag(args, "--head")]))
        if head == ("pr", "create"):
            return self._create(args)
        if head == ("pr", "merge"):
            return self._merge(args)
        return _fail(f"unrouted gh call: {' '.join(args)}")

    def _create(self, args: tuple[str, ...]) -> dict:
        head, base = _flag(args, "--head"), _flag(args, "--base")
        if any(pr["head"] == head and pr["state"] == "OPEN" for pr in self.prs):
            return _fail(f"a pull request for branch {head} already exists")
        body_file = _flag(args, "--body-file")
        assert body_file, "gh pr create must be given a body FILE"
        pr = self.add_pr(head, base, title=_flag(args, "--title") or "")
        self.bodies[pr["number"]] = Path(body_file).read_text(encoding="utf-8")
        return _ok(f"{pr['url']}\n")

    def _merge(self, args: tuple[str, ...]) -> dict:
        assert "--merge" in args, "the merge method must be --merge"
        assert "--squash" not in args and "--rebase" not in args
        number = int(args[2])
        pr = next((p for p in self.prs if p["number"] == number), None)
        if pr is None:
            return _fail(f"no pull request found for {number}")
        if pr["state"] != "OPEN":
            return _fail(f"pull request #{number} is not open")
        if self.land:
            self._land(pr)
        pr["state"] = "MERGED"
        return _ok(f"Merged pull request #{number}")

    def _land(self, pr: dict) -> None:
        """Merges the head into the base in origin, leaving a merge commit."""
        clone = self.scratch / f"merge-{pr['number']}"
        assert _run(["git", "clone", str(self.origin), str(clone)]).returncode == 0
        _configure(clone)
        _git(clone, "checkout", pr["base"])
        _git(clone, "merge", "--no-ff", f"origin/{pr['head']}",
             "-m", f"Merge pull request #{pr['number']} from {pr['head']}")
        _git(clone, "push", "origin", pr["base"])


@pytest.fixture
def gh(ws) -> _FakeGh:
    return _FakeGh(ws.origin, ws.scratch, default_branch=ws.base)


def _states(result: dict) -> dict[str, str]:
    return {step["name"]: step["state"] for step in result["steps"]}


# --------------------------------------------------------------------------
# The whole ritual, and the assertion it turns on.
# --------------------------------------------------------------------------

def test_the_whole_ritual_runs_and_the_branch_lands(ws, gh):
    """The one that pins ancestry: a real merge makes sha equality false."""
    branch_sha = ws.sha("HEAD")

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    assert result["status"] == "finished"
    assert exit_code(result) == 0
    assert _states(result) == {"push": "done", "body": "done", "pr": "done", "merge": "done",
                              "checkout": "done", "pull": "done", "validate": "done",
                              "delete-local": "done", "delete-remote": "skipped"}

    # The base tip is the MERGE COMMIT, so it is not the branch tip: a
    # `rev-parse base == rev-parse branch` check would fail here, on a merge
    # that succeeded. Ancestry is what actually holds.
    base_tip = ws.sha(ws.base)
    assert base_tip != branch_sha
    assert len(_git(ws.work, "rev-list", "--parents", "-n", "1", base_tip).split()) == 3
    assert _git_rc(ws.work, "merge-base", "--is-ancestor", branch_sha, ws.base) == 0

    assert ws.current() == ws.base
    assert ws.branch not in ws.locals()
    assert result["skipped"] == ["delete-remote"]


def test_the_validation_is_ancestry_rather_than_equality(ws, gh):
    """Stated on its own so the regression cannot be refactored away quietly."""
    branch_sha = ws.sha("HEAD")

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    step = next(s for s in result["steps"] if s["name"] == "validate")
    assert step["state"] == "done"
    assert "--is-ancestor" in step["command"]
    assert ws.sha(ws.base) != branch_sha


def test_a_merge_that_did_not_land_fails_validation_and_keeps_the_branch(ws):
    """The backstop: nothing is deleted when the base does not contain the tip."""
    gh = _FakeGh(ws.origin, ws.scratch, default_branch=ws.base, land=False)

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    assert result["status"] == "failed"
    assert exit_code(result) == 1
    assert _states(result)["validate"] == "failed"
    assert "does not contain" in result["error"]
    assert ws.branch in ws.locals()
    assert "delete-local" not in _states(result)


# --------------------------------------------------------------------------
# Preconditions. Every one of them changes nothing.
# --------------------------------------------------------------------------

def test_a_squash_is_refused_before_anything_runs(ws, gh):
    before = ws.snapshot()

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True, merge_method="squash")

    assert result["status"] == "precondition"
    assert exit_code(result) == 2
    assert "refused" in result["error"] and "revert granularity" in result["error"]
    assert result["steps"] == []
    assert gh.commands == []
    assert ws.snapshot() == before


def test_a_rebase_is_refused_before_anything_runs(ws, gh):
    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True, merge_method="rebase")

    assert result["status"] == "precondition"
    assert exit_code(result) == 2
    assert "'rebase' is refused" in result["error"]
    assert gh.commands == []


def test_a_dirty_working_tree_is_refused(ws, gh):
    (ws.work / "file-0.txt").write_text("edited\n", encoding="utf-8", newline="\n")
    before = ws.snapshot()

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    assert result["status"] == "precondition"
    assert exit_code(result) == 2
    assert "working tree is not clean" in result["error"]
    assert gh.commands == []
    assert ws.snapshot() == before


def test_running_from_the_base_branch_is_refused(ws, gh):
    _git(ws.work, "checkout", ws.base)

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    assert result["status"] == "precondition"
    assert exit_code(result) == 2
    assert f"already on the base branch '{ws.base}'" in result["error"]
    assert gh.mutating == []


def test_a_branch_with_no_commits_is_refused(tmp_path):
    ws = _make_workspace(tmp_path, subjects=())
    gh = _FakeGh(ws.origin, ws.scratch, default_branch=ws.base)

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    assert result["status"] == "precondition"
    assert exit_code(result) == 2
    assert "carries no commit" in result["error"]
    assert gh.mutating == []


def test_a_missing_body_file_is_refused(ws, gh):
    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True,
                           body_file=ws.work / "nowhere.md")

    assert result["status"] == "precondition"
    assert exit_code(result) == 2
    assert "does not exist" in result["error"]
    assert gh.mutating == []


def test_an_unauthenticated_gh_is_refused(ws):
    gh = _FakeGh(ws.origin, ws.scratch, default_branch=ws.base, authed=False)

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    assert result["status"] == "precondition"
    assert exit_code(result) == 2
    assert "gh auth login" in result["error"]
    assert gh.mutating == []


def test_a_directory_outside_a_repository_is_refused(tmp_path, gh):
    loose = tmp_path / "loose"
    loose.mkdir()

    result = branch_finish(repo=loose, gh=gh, assume_yes=True)

    assert result["status"] == "precondition"
    assert exit_code(result) == 2
    assert "not inside a git repository" in result["error"]


# --------------------------------------------------------------------------
# The base branch is detected, never assumed.
# --------------------------------------------------------------------------

def test_the_base_is_read_from_origins_head(tmp_path):
    """This workspace uses master and the next repository will not."""
    ws = _make_workspace(tmp_path, base="trunk")
    gh = _FakeGh(ws.origin, ws.scratch, default_branch="trunk")

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    assert result["base"] == "trunk"
    assert result["base_source"] == "refs/remotes/origin/HEAD"
    assert result["status"] == "finished"
    assert ws.current() == "trunk"


def test_the_base_falls_back_to_gh_when_origin_records_no_head(tmp_path):
    ws = _make_workspace(tmp_path, base="trunk")
    _git(ws.work, "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
    gh = _FakeGh(ws.origin, ws.scratch, default_branch="trunk")

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    assert result["base"] == "trunk"
    assert result["base_source"] == "gh repo view"
    assert result["status"] == "finished"


def test_an_explicit_base_wins_over_detection(ws, gh):
    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True, base=ws.base)

    assert result["base_source"] == "--base"
    assert result["status"] == "finished"


# --------------------------------------------------------------------------
# The dry run, and the confirmation the merge needs.
# --------------------------------------------------------------------------

def test_a_dry_run_mutates_nothing(ws, gh):
    before = ws.snapshot()

    result = branch_finish(repo=ws.work, gh=gh, dry_run=True)

    assert result["status"] == "planned"
    assert exit_code(result) == 0
    assert ws.snapshot() == before
    assert gh.mutating == []
    assert gh.prs == []
    assert [step["name"] for step in result["steps"]] == [
        "push", "body", "pr", "merge", "checkout", "pull", "validate",
        "delete-local", "delete-remote"]
    assert [s["state"] for s in result["steps"] if s["name"] != "delete-remote"] == \
        ["planned"] * 8
    assert any("gh pr merge" in step["command"] for step in result["steps"])


def test_a_dry_run_needs_no_confirmation(ws, gh, monkeypatch):
    """Printing a plan is not a merge, so it does not ask for one."""
    monkeypatch.setattr(branch_mod, "_stdin_is_tty", lambda: False)

    result = branch_finish(repo=ws.work, gh=gh, dry_run=True)

    assert result["status"] == "planned"


def test_an_unattended_merge_needs_yes(ws, gh, monkeypatch):
    """No terminal to ask at and no --yes means nothing happens at all."""
    monkeypatch.setattr(branch_mod, "_stdin_is_tty", lambda: False)
    before = ws.snapshot()

    result = branch_finish(repo=ws.work, gh=gh)

    assert result["status"] == "precondition"
    assert exit_code(result) == 2
    assert "not a terminal" in result["error"]
    assert gh.mutating == []
    assert ws.snapshot() == before


def test_declining_the_prompt_leaves_the_pull_request_open(ws, gh):
    asked: list[str] = []

    result = branch_finish(repo=ws.work, gh=gh, confirm=lambda q: asked.append(q) or False)

    assert result["status"] == "declined"
    assert exit_code(result) == 1
    assert asked and "merge pull request #1" in asked[0]
    assert gh.mutating == ["pr create"]
    assert [pr["state"] for pr in gh.prs] == ["OPEN"]
    assert ws.branch in ws.locals()
    assert ws.current() == ws.branch


def test_accepting_the_prompt_merges(ws, gh):
    result = branch_finish(repo=ws.work, gh=gh, confirm=lambda q: True)

    assert result["status"] == "finished"
    assert [pr["state"] for pr in gh.prs] == ["MERGED"]


def test_no_merge_stops_with_the_pull_request_open(ws, gh, monkeypatch):
    """No merge is planned, so an unattended run needs no confirmation."""
    monkeypatch.setattr(branch_mod, "_stdin_is_tty", lambda: False)

    result = branch_finish(repo=ws.work, gh=gh, no_merge=True)

    assert result["status"] == "opened"
    assert exit_code(result) == 0
    assert _states(result) == {"push": "done", "body": "done", "pr": "done", "merge": "skipped"}
    assert gh.mutating == ["pr create"]
    assert ws.branch in ws.locals()
    assert ws.current() == ws.branch


# --------------------------------------------------------------------------
# Resume. The ritual dies between steps; a re-run continues it.
# --------------------------------------------------------------------------

def test_resume_when_the_branch_is_already_pushed(ws, gh):
    _git(ws.work, "push", "-u", "origin", ws.branch)

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    assert result["status"] == "finished"
    push = next(s for s in result["steps"] if s["name"] == "push")
    assert push["state"] == "skipped"
    assert "already at" in push["detail"]


def test_resume_when_the_pull_request_is_already_open(ws, gh):
    _git(ws.work, "push", "-u", "origin", ws.branch)
    seeded = gh.add_pr(ws.branch, ws.base)

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    assert result["status"] == "finished"
    assert _states(result)["pr"] == "skipped"
    assert result["pr"]["number"] == seeded["number"]
    # The existing number was reused rather than a second pull request opened.
    assert gh.mutating == ["pr merge"]
    assert len(gh.prs) == 1


def test_resume_when_the_pull_request_is_already_merged(ws, gh, monkeypatch):
    """Nothing outward-facing is left, so the resume needs no confirmation."""
    monkeypatch.setattr(branch_mod, "_stdin_is_tty", lambda: False)
    _git(ws.work, "push", "-u", "origin", ws.branch)
    seeded = gh.add_pr(ws.branch, ws.base)
    gh._land(seeded)
    seeded["state"] = "MERGED"
    gh.calls.clear()
    branch_sha = ws.sha("HEAD")

    result = branch_finish(repo=ws.work, gh=gh)

    assert result["status"] == "finished"
    assert exit_code(result) == 0
    assert result["skipped"] == ["push", "body", "pr", "merge", "delete-remote"]
    assert gh.mutating == []
    assert ws.branch not in ws.locals()
    assert _git_rc(ws.work, "merge-base", "--is-ancestor", branch_sha, ws.base) == 0


def test_resume_after_the_local_branch_is_gone(ws, gh):
    """The delete is the last step, so it is the one most often already done."""
    _git(ws.work, "push", "-u", "origin", ws.branch)
    seeded = gh.add_pr(ws.branch, ws.base)
    gh._land(seeded)
    seeded["state"] = "MERGED"
    _git(ws.work, "checkout", ws.base)
    _git(ws.work, "pull", "--ff-only", "origin", ws.base)
    _git(ws.work, "branch", "-d", ws.branch)

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True, base=ws.base)

    # Finishing a branch that is gone is a precondition failure, not a no-op:
    # HEAD is the base by then, and the base is not a branch to finish.
    assert result["status"] == "precondition"
    assert "already on the base branch" in result["error"]


# --------------------------------------------------------------------------
# The body, and the remote branch.
# --------------------------------------------------------------------------

def test_the_body_is_composed_from_the_commit_subjects(ws, gh, tmp_path):
    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    body = gh.bodies[1]
    assert body == "".join(f"- {subject}\n" for subject in SUBJECTS)
    step = next(s for s in result["steps"] if s["name"] == "body")
    assert str(tmp_path) in step["detail"]


def test_a_given_body_file_is_used_verbatim(ws, gh, tmp_path):
    given = tmp_path / "body.md"
    given.write_text("Reads `$HOME` and it's fine\n", encoding="utf-8", newline="\n")

    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True, body_file=given)

    assert result["status"] == "finished"
    assert gh.bodies[1] == "Reads `$HOME` and it's fine\n"


def test_the_title_defaults_to_the_last_commit_subject(ws, gh):
    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    assert result["title"] == SUBJECTS[-1]
    assert gh.prs[0]["title"] == SUBJECTS[-1]


def test_the_remote_branch_survives_by_default(ws, gh):
    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True)

    assert _states(result)["delete-remote"] == "skipped"
    assert any(ws.branch in line for line in ws.remotes())


def test_delete_remote_removes_it(ws, gh):
    result = branch_finish(repo=ws.work, gh=gh, assume_yes=True, delete_remote=True)

    assert _states(result)["delete-remote"] == "done"
    assert not any(ws.branch in line for line in ws.remotes())


# --------------------------------------------------------------------------
# The CLI adapter.
# --------------------------------------------------------------------------

def test_cli_finish_prints_the_verdict(ws, gh, monkeypatch, capsys):
    monkeypatch.setattr(branch_mod, "_gh", gh)
    monkeypatch.chdir(ws.work)

    rc = cli.main(["branch", "finish", "--yes"])

    out = capsys.readouterr().out
    assert rc == 0
    assert f"BRANCH: FINISHED {ws.branch} -> {ws.base} (pr #1)" in out
    assert "validate" in out and "--is-ancestor" in out
    assert ws.branch not in ws.locals()


def test_cli_dry_run_prints_the_plan_and_changes_nothing(ws, gh, monkeypatch, capsys):
    monkeypatch.setattr(branch_mod, "_gh", gh)
    monkeypatch.chdir(ws.work)
    before = ws.snapshot()

    rc = cli.main(["branch", "finish", "--dry-run"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "BRANCH: PLAN" in out
    assert "gh pr merge" in out and "git branch -d" in out
    assert ws.snapshot() == before


def test_cli_squash_exits_two(ws, gh, monkeypatch, capsys):
    monkeypatch.setattr(branch_mod, "_gh", gh)
    monkeypatch.chdir(ws.work)

    rc = cli.main(["branch", "finish", "--yes", "--squash"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "BRANCH: PRECONDITION" in captured.out
    assert "refused" in captured.err
    assert gh.commands == []


def test_the_base_tip_is_reported_and_is_the_merge_commit(ws, gh):
    """The one number a revert of this landing starts from, and the ritual
    otherwise never says it - the merge step knows only the pull request."""
    tip = ws.sha(ws.branch)

    result = branch_finish(ws.work, gh=gh, assume_yes=True)

    assert result["ok"]
    assert result["base_sha"] == ws.sha(ws.base)
    # A merge commit, so it is NOT the branch tip - the whole reason the
    # validation is ancestry rather than equality.
    assert result["base_sha"] != tip
    assert tip in _git(ws.work, "rev-list", result["base_sha"]).splitlines()


def test_a_dry_run_reports_no_landing(ws, gh):
    """Nothing merged, so there is no base tip to name; the pre-merge one would
    read as a landing that did not happen."""
    result = branch_finish(ws.work, gh=gh, dry_run=True)

    assert result["ok"]
    assert result["base_sha"] is None


def test_a_failure_before_the_pull_reports_no_landing(ws, gh, monkeypatch):
    """base_sha is read after the pull, so a run that never got there has none."""
    monkeypatch.setattr(branch_mod, "_gh",
                        lambda *a, **k: _fail("gh: not logged in"))
    result = branch_finish(ws.work, assume_yes=True)

    assert not result["ok"]
    assert result["base_sha"] is None


def test_cli_prints_where_the_base_landed(ws, gh, monkeypatch, capsys):
    monkeypatch.setattr(branch_mod, "_gh", gh)
    monkeypatch.chdir(ws.work)

    rc = cli.main(["branch", "finish", "--yes"])

    out = capsys.readouterr().out
    assert rc == 0
    assert f"{ws.base}@{ws.sha(ws.base)[:7]}" in out


def _seed_inventory(monkeypatch, root: Path | None, module: dict | None = None) -> None:
    """Replaces the cached module inventory with one the test controls.

    ``modules._inventory`` is lru_cached over the real workspace cache, so it is
    the seam rather than the json on disk.
    """
    mods = [module] if module else []
    by_short = {module["shorthand"]: module} if module else {}
    by_name = {module["name"]: module} if module else {}
    monkeypatch.setattr(modules, "_inventory", lambda: (root, mods, by_short, by_name))


def test_cli_finish_resolves_a_module_shorthand(ws, gh, monkeypatch, capsys):
    """The repository is named the way every other command names one.

    The cwd is a directory that is no repository at all, so nothing but the
    shorthand can be what found the branch.
    """
    monkeypatch.setattr(branch_mod, "_gh", gh)
    _seed_inventory(monkeypatch, ws.work.parent,
                    {"shorthand": "wk", "name": "the-work", "path": "work",
                     "package": None, "buildable": False})
    monkeypatch.chdir(ws.scratch)
    tip = ws.sha(ws.branch)

    rc = cli.main(["branch", "finish", "wk", "--yes"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "BRANCH: FINISHED" in out
    assert ws.branch not in ws.locals()
    assert _git_rc(ws.work, "merge-base", "--is-ancestor", tip, ws.base) == 0


def test_cli_finish_takes_a_path_when_the_repository_is_no_module(ws, gh, monkeypatch, capsys):
    """Discovery scans for gradle modules, so a repository that is not one - toolsmith
    itself - has only a path to be named by."""
    monkeypatch.setattr(branch_mod, "_gh", gh)
    _seed_inventory(monkeypatch, None)
    monkeypatch.chdir(ws.scratch)

    rc = cli.main(["branch", "finish", str(ws.work), "--yes"])

    assert rc == 0
    assert "BRANCH: FINISHED" in capsys.readouterr().out
    assert ws.branch not in ws.locals()


def test_cli_finish_refuses_a_token_that_resolves_to_nothing(ws, gh, monkeypatch, capsys):
    """A miss must not read as the current directory.

    resolve_module answers None for "no such module" and for "no such directory"
    alike, and branch_finish reads None as the cwd - so a mistyped module that
    fell through would finish whatever branch the shell was sitting on. The cwd
    here is a real repository on a real feature branch, which is what gives the
    fallback something to destroy.
    """
    monkeypatch.setattr(branch_mod, "_gh", gh)
    _seed_inventory(monkeypatch, None)
    monkeypatch.chdir(ws.work)
    before = ws.snapshot()

    rc = cli.main(["branch", "finish", "no-such-module", "--yes"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "BRANCH: PRECONDITION" in captured.out
    assert "no-such-module" in captured.err
    assert gh.commands == []
    assert ws.snapshot() == before


def test_cli_names_a_skipped_step_without_claiming_it_was_done(ws, gh, monkeypatch, capsys):
    """delete-remote is skipped because it was never asked for.

    A summary calling that "already done" says the remote branch was deleted by
    an earlier run, which is the opposite of what happened.
    """
    monkeypatch.setattr(branch_mod, "_gh", gh)
    monkeypatch.chdir(ws.work)

    rc = cli.main(["branch", "finish", "--yes"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped: delete-remote" in out
    assert "already done" not in out
    assert ws.branch in "\n".join(ws.remotes())


def test_finish_is_not_an_mcp_tool():
    """It pushes, opens and merges, so it stays something a human invokes."""
    from toolsmith import server

    assert not hasattr(server, "branch_finish")
    assert "branch" not in server.__dict__
