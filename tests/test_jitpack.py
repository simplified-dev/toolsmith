"""Tests for the JitPack status / build / pins core.

Fully hermetic. Two seams stand in for the outside world: jitpack._http, which
is the only door to the network, and jitpack._git, which is the only door to a
real repository's history. Everything else is real code over a tmp_path
workspace with a real discovered inventory.

The single most important test here is
test_never_requests_the_per_version_api_endpoint. The per-version endpoint
``/api/builds/<group>/<artifact>/<version>`` silently TRIGGERS a build on a
third-party service and answers from months-stale records; the recording fake
below refuses to serve it at all, so every test in the file enforces that rule
and one test proves the guard is not vacuous.
"""
from __future__ import annotations

import http.client
import io
import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from toolsmith import discovery, jitpack, modules
from toolsmith.jitpack import exit_code, jitpack_build, jitpack_pins, jitpack_status

MODULE = "discord4j-fauxrig"
ORG = "simplified-dev"
ARTIFACT = "discord4j-fauxrig"
GROUP = f"com.github.{ORG}"
GROUP_PATH = f"com/github/{ORG}"
REMOTE = f"https://github.com/{ORG}/{ARTIFACT}.git"
LIST_URL = f"https://jitpack.io/api/builds/{GROUP}/{ARTIFACT}"

# Two real, already-built fauxrig shas, so nothing here reads as a sha that
# would be worth requesting if a route ever escaped the fake.
FULL = "bd238422975aef8c1d3f2b9a0e6d4c7185ab90ff"
SHA = FULL[:7]
OTHER_FULL = "e977ad3c0b41d2e5f6a7b8c9d0e1f2a3b4c5d6e7"
OTHER_SHA = OTHER_FULL[:7]

# The versionless list endpoint - group and artifact, and nothing after them.
_LIST_URL_RE = re.compile(r"^https://jitpack\.io/api/builds/[^/]+/[^/]+$")

_BUILD_FAILED_BODY = "Build failed. See the log at jitpack.io"
_SNAPSHOT_VERSION = "master-bd23842297-1"
_METADATA_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    f"<metadata><groupId>{GROUP}</groupId><artifactId>{ARTIFACT}</artifactId>"
    f"<versioning><release>{_SNAPSHOT_VERSION}</release>"
    f"<versions><version>{_SNAPSHOT_VERSION}</version></versions></versioning></metadata>"
)
_FAILING_LOG = (
    "> Task :compileJava\n"
    "Widget.java:12: error: cannot find symbol\n"
    "22 errors\n"
    "BUILD FAILED in 3s\n"
    "Exit code: 1\n"
)


# --------------------------------------------------------------------------
# The HTTP seam.
# --------------------------------------------------------------------------

def _response(code: int = 200, body: str = "", elapsed: float = 2.0, **extra) -> dict:
    """One _http return, defaulting to a slow 200 (a build that actually ran)."""
    return {"code": code, "body": body, "elapsed": elapsed, "location": "",
            "timed_out": False, "error": None, **extra}


# An unrouted URL answers like a cold miss rather than blowing up, so a test
# only has to declare the responses it cares about.
_MISS = _response(404, elapsed=0.05)


class _FakeHttp:
    """Recording stand-in for jitpack._http.

    Routes match on a URL substring, first match wins, and a route may queue
    several responses so a re-request (a followed redirect) can differ from the
    first. A response may also be a callable taking the URL, which is how the
    list endpoint answers per artifact.

    Requesting the per-version API endpoint is a hard failure rather than a
    canned answer: no code path may ever reach it, so the fake will not model
    one.
    """

    def __init__(self) -> None:
        self.routes: list[tuple[str, list]] = []
        self.calls: list[tuple[str, bool, float]] = []

    def route(self, needle: str, *responses) -> "_FakeHttp":
        self.routes.append((needle, list(responses)))
        return self

    @property
    def urls(self) -> list[str]:
        return [call[0] for call in self.calls]

    @property
    def timeouts(self) -> list[float]:
        """Every request's budget, so a caller cannot silently spend it twice."""
        return [call[2] for call in self.calls]

    def __call__(self, url: str, timeout: float, follow: bool = False) -> dict:
        assert "/api/" not in url or _LIST_URL_RE.fullmatch(url), (
            f"per-version api request '{url}' - that endpoint triggers a build")
        self.calls.append((url, follow, timeout))
        for needle, queued in self.routes:
            if needle not in url:
                continue
            response = queued[0] if len(queued) == 1 else queued.pop(0)
            return {**_MISS, **(response(url) if callable(response) else response), "url": url}
        return {**_MISS, "url": url}


def _list_responder(by_artifact: dict[str, dict]) -> Callable[[str], dict]:
    """Answers the list endpoint with the records held for that URL's artifact."""
    def respond(url: str) -> dict:
        group, artifact = url.rsplit("/", 2)[-2:]
        return _response(body=json.dumps({group: {artifact: by_artifact.get(artifact, {})}}),
                         elapsed=0.12)
    return respond


def _install_http(monkeypatch, *, records: dict | None = None,
                  by_artifact: dict[str, dict] | None = None,
                  pom=None, log=None, metadata=None) -> _FakeHttp:
    """Installs a recording _http and returns it.

    Args:
        records: {version: status} for the default artifact's list endpoint.
        by_artifact: {artifact: {version: status}} when more than one is in play.
        pom: response (or list of responses) for the artifact request.
        log: response for build.log.
        metadata: response for maven-metadata.xml.
    """
    http = _FakeHttp()
    http.route("/api/builds/", _list_responder(by_artifact or {ARTIFACT: records or {}}))
    for needle, canned in (("maven-metadata.xml", metadata), ("build.log", log), (".pom", pom)):
        if canned is not None:
            http.route(needle, *(canned if isinstance(canned, list) else [canned]))
    monkeypatch.setattr(jitpack, "_http", http)
    return http


# --------------------------------------------------------------------------
# The git seam.
# --------------------------------------------------------------------------

# A table value meaning "git did not answer at all" - a non-zero exit, a
# timeout on a large repo, a stale index.lock, or git missing from PATH. It is
# NOT the same as "" (the command ran and printed nothing), and the whole point
# of the sentinel is that no caller may treat the two alike.
GIT_FAILED = object()


class _FakeGit:
    """Recording stand-in for jitpack._git, answering from an args table.

    Lookup is exact first, then longest-standing prefix, so a table can hold
    both "rev-parse --verify origin/master^{commit}" and a catch-all
    "rev-parse --verify" that makes every other ref unresolvable. A table value
    may be a callable taking the repo directory, which is how each repo in a
    multi-repo workspace reports its own remote, or GIT_FAILED for a command
    that never answered.

    An unknown call returns "" - a command that ran and printed nothing.
    """

    def __init__(self, table: dict) -> None:
        self.table = dict(table)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, repo_dir, *args: str, timeout: float = 15.0) -> str | None:
        key = " ".join(args)
        self.calls.append((Path(repo_dir).name, key))
        value = self.table.get(key)
        if value is None:
            value = next((v for k, v in self.table.items() if key.startswith(k)), "")
        if value is GIT_FAILED:
            return None
        return value(Path(repo_dir)) if callable(value) else value

    def args(self) -> list[str]:
        return [key for _repo, key in self.calls]


def _git_table() -> dict:
    """The default table: a healthy repo whose local HEAD is off the default branch."""
    return {
        # Remote PRESENCE is read from `git remote`; `config --get` supplies the
        # url only once origin is known to exist. `config --get` exits 1 on an
        # unset key, so it can never be the presence check.
        "remote": "origin",
        "config --get remote.origin.url": REMOTE,
        "symbolic-ref --short refs/remotes/origin/HEAD": "origin/master",
        "rev-parse --verify origin/master^{commit}": FULL,
        f"rev-parse --disambiguate={SHA}": FULL,
        "branch -r --contains": "origin/master\n  origin/HEAD -> origin/master",
        "rev-parse --abbrev-ref HEAD": "feature/offline-harness",
        # Anything else is genuinely unresolvable, which is the honest default.
        "rev-parse --verify": "",
    }


def _install_git(monkeypatch, overrides: dict | None = None) -> _FakeGit:
    """Installs a fake _git over the default healthy table plus any overrides."""
    table = _git_table()
    table.update(overrides or {})
    git = _FakeGit(table)
    monkeypatch.setattr(jitpack, "_git", git)
    return git


# --------------------------------------------------------------------------
# The workspace.
# --------------------------------------------------------------------------

def _make_module(root: Path, rel: str, *, git: bool = True, build: str = "") -> Path:
    """Creates a gradle module under root, with its own .git unless told not to."""
    directory = root / rel
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "build.gradle.kts").write_text(build, encoding="utf-8")
    if git:
        (directory / ".git").mkdir(exist_ok=True)
    return directory


def _install_inventory(root: Path, monkeypatch) -> Path:
    """Scans root and points toolsmith.modules at the result for this test only."""
    monkeypatch.setattr(discovery, "REGISTRY", root / "registry.json")
    discovery.run_setup(root)
    monkeypatch.setenv("TOOLSMITH_ROOT", str(root))
    modules.reload()
    return root


@pytest.fixture(autouse=True)
def _drop_inventory():
    """Never let a tmp inventory leak into another test."""
    yield
    modules.reload()


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A one-module workspace, the module being its own git repo."""
    _make_module(tmp_path, MODULE)
    return _install_inventory(tmp_path, monkeypatch)


# --------------------------------------------------------------------------
# Coordinate resolution. All local. A wrong coordinate asks JitPack about the
# wrong repository, so none of it may be inferred from the module or directory
# name - only from the remote.
# --------------------------------------------------------------------------

def test_artifact_comes_from_the_remote_not_the_directory(tmp_path, monkeypatch):
    """The minecraft-text module publishes github.com/minecraft-library/text."""
    _make_module(tmp_path, "minecraft-text")
    _install_inventory(tmp_path, monkeypatch)
    _install_git(monkeypatch, {"config --get remote.origin.url":
                               "https://github.com/minecraft-library/text.git"})

    coord = jitpack._coordinate("minecraft-text")

    assert coord["ok"] is True
    assert coord["artifact"] == "text"
    assert coord["org"] == "minecraft-library"
    assert coord["repo"] == "minecraft-library/text"
    assert coord["group"] == "com.github.minecraft-library"
    assert coord["group_path"] == "com/github/minecraft-library"


def test_scp_style_remote_without_a_git_suffix(tmp_path, monkeypatch):
    """The .git suffix is inconsistent across these remotes; asset-renderer has none."""
    _make_module(tmp_path, "asset-renderer")
    _install_inventory(tmp_path, monkeypatch)
    _install_git(monkeypatch, {"config --get remote.origin.url":
                               "git@github.com:simplified-dev/asset-renderer"})

    coord = jitpack._coordinate("asset-renderer")

    assert coord["ok"] is True
    assert coord["org"] == "simplified-dev"
    assert coord["artifact"] == "asset-renderer"


def test_nested_module_walks_up_to_the_enclosing_repo(tmp_path, monkeypatch):
    """A module inside a repo inherits that repo's remote, not one of its own."""
    (tmp_path / "outer" / ".git").mkdir(parents=True)
    _make_module(tmp_path, "outer/inner", git=False)
    _install_inventory(tmp_path, monkeypatch)
    _install_git(monkeypatch)

    coord = jitpack._coordinate("inner")

    assert coord["ok"] is True
    assert Path(coord["repo_dir"]).resolve() == (tmp_path / "outer").resolve()
    assert coord["artifact"] == ARTIFACT


def test_no_remote_degrades_without_touching_the_network(repo, monkeypatch):
    """Two modules have no remote at all; that is an answer, not a crash."""
    _install_git(monkeypatch, {"remote": "",
                               "config --get remote.origin.url": GIT_FAILED})
    http = _install_http(monkeypatch)

    result = jitpack_status(MODULE)

    assert result["ok"] is False
    assert "has no git remote" in result["error"]
    assert "git remote add origin" in result["error"]
    assert result["refs"] == []
    assert http.urls == []
    assert exit_code(result) == 2


def test_no_git_repo_above_the_module(tmp_path, monkeypatch):
    _make_module(tmp_path, "loose", git=False)
    _install_inventory(tmp_path, monkeypatch)
    _install_git(monkeypatch)
    http = _install_http(monkeypatch)

    result = jitpack_status("loose")

    assert result["ok"] is False
    assert "no git repo above" in result["error"]
    assert http.urls == []
    assert exit_code(result) == 2


def test_unrecognisable_remote_is_an_error(repo, monkeypatch):
    _install_git(monkeypatch, {"config --get remote.origin.url": "/srv/git/fauxrig.git"})

    coord = jitpack._coordinate(MODULE)

    assert coord["ok"] is False
    assert "not a recognisable github url" in coord["error"]


def test_unresolved_module_is_a_precondition(repo, monkeypatch):
    http = _install_http(monkeypatch)

    result = jitpack_status("no-such-module")

    assert result["ok"] is False
    assert "not resolved" in result["error"]
    assert http.urls == []
    assert exit_code(result) == 2


def test_maven_central_module_refuses_before_any_http(tmp_path, monkeypatch):
    """annotations publishes to Central and has zero JitPack builds, ever."""
    _make_module(tmp_path, "annotations", build='group = "io.github.simplified-dev"\n')
    _install_inventory(tmp_path, monkeypatch)
    _install_git(monkeypatch)
    http = _install_http(monkeypatch)

    result = jitpack_build("annotations")

    assert result["ok"] is False
    assert result["status"] == "precondition"
    assert result["central"] == "io.github.simplified-dev"
    assert "publishes to maven central" in result["error"]
    assert http.urls == []
    assert exit_code(result) == 2


# --------------------------------------------------------------------------
# Ref resolution. Also all local: JitPack answers byte-identically for a typo'd
# sha and a real unbuilt one, so validity is established here or not at all -
# and an invalid ref must never cost a build.
# --------------------------------------------------------------------------

def test_default_ref_is_origin_head_not_local_head(repo, monkeypatch):
    """Several repos sit on a feature branch; the pin comes from the pushed default."""
    git = _install_git(monkeypatch, {"rev-parse --verify HEAD^{commit}": OTHER_FULL})
    _install_http(monkeypatch, records={SHA: "ok"})

    entry = jitpack_status(MODULE)["refs"][0]

    assert entry["source"] == "origin/master"
    assert entry["ref"] == SHA
    assert entry["full"] == FULL
    assert entry["ok"] is True
    assert "rev-parse --verify HEAD^{commit}" not in git.args()


def test_default_ref_falls_back_to_origin_master(repo, monkeypatch):
    """A clone whose origin/HEAD was never fetched still pins the default branch."""
    _install_git(monkeypatch, {"symbolic-ref --short refs/remotes/origin/HEAD": ""})
    _install_http(monkeypatch, records={SHA: "ok"})

    entry = jitpack_status(MODULE)["refs"][0]

    assert entry["source"] == "origin/master"
    assert entry["ref"] == SHA


def test_sha_is_always_truncated_to_seven(repo, monkeypatch):
    """Each distinct prefix length is a separate build, so the length is never the caller's."""
    _install_git(monkeypatch, {f"rev-parse --verify {FULL}^{{commit}}": FULL})
    http = _install_http(monkeypatch, pom=_response(200, body="<project/>", elapsed=74.2))

    result = jitpack_build(MODULE, ref=FULL)

    assert result["ref"] == SHA
    assert len(result["ref"]) == 7
    assert result["full_sha"] == FULL
    assert result["url"] == (f"https://jitpack.io/{GROUP_PATH}/{ARTIFACT}/{SHA}"
                             f"/{ARTIFACT}-{SHA}.pom")
    assert http.urls[-1] == result["url"]


def test_unresolvable_ref_makes_no_request(repo, monkeypatch):
    _install_git(monkeypatch)
    http = _install_http(monkeypatch)

    result = jitpack_build(MODULE, ref="deadbee")

    assert result["ok"] is False
    assert result["status"] == "precondition"
    assert "ref 'deadbee' not resolvable" in result["error"]
    assert http.urls == []
    assert exit_code(result) == 2


def test_ambiguous_prefix_is_refused_before_any_request(repo, monkeypatch):
    """Two objects share the prefix, so JitPack could build the wrong commit."""
    _install_git(monkeypatch, {f"rev-parse --disambiguate={SHA}": f"{FULL}\n{OTHER_FULL}"})
    http = _install_http(monkeypatch)

    result = jitpack_build(MODULE)

    assert result["ok"] is False
    assert result["status"] == "precondition"
    assert f"sha prefix '{SHA}' is ambiguous in this repo (2 objects)" in result["error"]
    assert http.urls == []
    assert exit_code(result) == 2


def test_unpushed_ref_makes_no_request(repo, monkeypatch):
    """Resolvable locally, on no origin/* branch: asking would cost a build for nothing."""
    _install_git(monkeypatch, {"branch -r --contains": "upstream/master"})
    http = _install_http(monkeypatch)

    result = jitpack_build(MODULE)

    assert result["ok"] is False
    assert result["status"] == "precondition"
    assert result["error"] == (f"ref '{SHA}' is not pushed "
                               f"(run 'git push origin feature/offline-harness')")
    assert http.urls == []
    assert exit_code(result) == 2


def test_a_failing_contains_query_is_inconclusive_not_unpushed(repo, monkeypatch):
    """'branch -r --contains' can exceed its timeout on a large repo.

    Declaring a pushed sha unpushed sends the reader off to push a branch that
    is already pushed - and the branch named is the LOCAL one, not the branch
    holding the commit, so following the advice pushes the wrong thing.
    """
    _install_git(monkeypatch, {"branch -r --contains": GIT_FAILED})
    http = _install_http(monkeypatch)

    result = jitpack_build(MODULE)

    assert result["ok"] is False
    assert result["status"] == "precondition"
    assert "cannot tell whether" in result["error"]
    assert "git push origin" not in result["error"]
    assert "feature/offline-harness" not in result["error"]
    assert http.urls == []
    assert exit_code(result) == 2


def test_an_empty_contains_query_really_is_unpushed(repo, monkeypatch):
    """The other half of the distinction: git answered, and the answer was nothing."""
    _install_git(monkeypatch, {"branch -r --contains": ""})
    http = _install_http(monkeypatch)

    result = jitpack_build(MODULE)

    assert result["error"] == (f"ref '{SHA}' is not pushed "
                               f"(run 'git push origin feature/offline-harness')")
    assert http.urls == []


def test_a_failing_git_is_not_reported_as_a_missing_remote(repo, monkeypatch):
    """With git off PATH the old answer was "module has no git remote" - a lie."""
    _install_git(monkeypatch, {"config --get remote.origin.url": GIT_FAILED})
    http = _install_http(monkeypatch)

    result = jitpack_status(MODULE)

    assert result["ok"] is False
    assert "git failed" in result["error"]
    assert "no git remote" not in result["error"]
    assert http.urls == []
    assert exit_code(result) == 2


def test_a_missing_remote_carries_the_whole_remedy(repo, monkeypatch):
    """Matrix row 11: 'gh repo create' left the remote unattached; both halves are needed.

    Absence is expressed the way real git expresses it: `git remote` prints
    nothing, and `config --get remote.origin.url` EXITS 1 on the unset key. An
    earlier version of this test handed `config --get` an empty string, which
    real git never returns, and so passed while the production path it claimed to
    cover reported a PATH problem and printed no remedy at all.
    """
    _install_git(monkeypatch, {
        "remote": "",
        "config --get remote.origin.url": GIT_FAILED,
    })

    result = jitpack_status(MODULE)

    assert "has no git remote" in result["error"]
    assert "git remote add origin <url> && git push -u origin master" in result["error"]
    assert exit_code(result) == 2


def test_a_repo_whose_git_cannot_list_remotes_is_not_called_remote_less(repo, monkeypatch):
    """git failing to answer is not the same as a repo having no origin."""
    _install_git(monkeypatch, {"remote": GIT_FAILED})

    result = jitpack_status(MODULE)

    assert "could not list remotes" in result["error"]
    assert "has no git remote" not in result["error"]
    assert exit_code(result) == 2


def test_a_failing_disambiguate_never_invents_an_ambiguous_prefix(repo, monkeypatch):
    """No output is not two objects; a failed query must not become a refusal."""
    _install_git(monkeypatch, {f"rev-parse --disambiguate={SHA}": GIT_FAILED})
    _install_http(monkeypatch, records={SHA: "ok"},
                  pom=_response(200, body="<project/>", elapsed=0.2))

    result = jitpack_build(MODULE)

    assert result["status"] == "already-built"


def test_status_reports_an_unresolved_ref_per_entry(repo, monkeypatch):
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={SHA: "ok"})

    result = jitpack_status(MODULE, refs=["origin/master", "deadbee"])

    assert [entry["ok"] for entry in result["refs"]] == [True, False]
    assert result["refs"][1]["state"] == "unresolved"
    assert "not resolvable" in result["refs"][1]["error"]
    assert result["ok"] is False
    assert result["error"] == result["refs"][1]["error"]
    assert exit_code(result) == 2


def test_status_asks_nothing_when_no_ref_survived_local_validation(repo, monkeypatch):
    """Matrix row 7 puts the local gate BEFORE any network call, and build obeys it.

    There is no question left to ask JitPack once every ref is unresolvable, and
    the two commands may not disagree about their own precondition ordering.
    """
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={SHA: "ok"})

    result = jitpack_status(MODULE, refs=["deadbee", "beefdea"])

    assert result["ok"] is False
    assert result["records"] == 0
    assert [entry["state"] for entry in result["refs"]] == ["unresolved", "unresolved"]
    assert http.urls == []
    assert exit_code(result) == 2


def test_status_still_reads_the_list_when_one_ref_resolves(repo, monkeypatch):
    """Only a total wash-out skips the read; one answerable ref is still a question."""
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={SHA: "ok"})

    result = jitpack_status(MODULE, refs=["origin/master", "deadbee"])

    assert http.urls == [LIST_URL]
    assert result["records"] == 1


def test_symbolic_ref_is_refused_by_default(repo, monkeypatch):
    _install_git(monkeypatch)
    http = _install_http(monkeypatch)

    result = jitpack_build(MODULE, ref="master-SNAPSHOT")

    assert result["ok"] is False
    assert result["status"] == "precondition"
    assert "pin by sha" in result["error"]
    assert http.urls == []
    assert exit_code(result) == 2


# --------------------------------------------------------------------------
# The list-endpoint precheck. One free, fast, fresh read that separates "built"
# from "cached failure" from "never requested" before anything is triggered -
# which is what makes the historical 20-minute 404 ambiguity impossible.
# --------------------------------------------------------------------------

def test_precheck_ok_short_circuits_the_wait_but_still_confirms_the_artifact(repo, monkeypatch):
    """An already-ok sha costs one list read plus one cache-hit .pom, and no build."""
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={SHA: "ok"},
                         pom=_response(200, body="<project/>", elapsed=0.2))

    result = jitpack_build(MODULE)

    assert result["ok"] is True
    assert result["status"] == "already-built"
    assert result["action"] == "verify"
    assert result["precheck"] == "ok"
    assert result["http_code"] == 200
    assert result["pin"] == f'{GROUP}:{ARTIFACT} -> version {{ strictly("{SHA}") }}'
    assert "-xtest" in result["note"]
    assert http.urls == [LIST_URL, f"https://jitpack.io/{GROUP_PATH}/{ARTIFACT}/{SHA}"
                                   f"/{ARTIFACT}-{SHA}.pom"]
    assert exit_code(result) == 0


def test_a_record_saying_ok_never_alone_produces_a_green_verdict(repo, monkeypatch):
    """Matrix row 16 / jitpack issue #7711: the list says ok, the artifact tree 404s.

    Trusting the record cost ~20 minutes once: the pin was pasted, and the
    downstream resolve is where the truth arrived. The artifact GET is the gate.
    """
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={SHA: "ok"},
                         pom=_response(404, elapsed=0.18),
                         log=_response(200, body=_FAILING_LOG))

    result = jitpack_build(MODULE)

    assert result["ok"] is False
    assert result["precheck"] == "ok"
    assert result["action"] == "verify"
    assert result["status"] == "failed"
    assert result["http_code"] == 404
    assert "#7711" in result["error"]
    assert "pin" not in result  # nothing paste-ready may leave this branch
    assert any(url.endswith(".pom") for url in http.urls)
    assert exit_code(result) == 1


def test_the_verify_request_gets_the_callers_whole_budget(repo, monkeypatch):
    """Confirming an 'ok' record must be allowed to outlast a real build.

    An earlier version capped this request at 60s on the reasoning that
    confirmation is only ever a cache hit. It is not: the case the confirmation
    exists to catch (jitpack issue #7711 - the record says ok, the artifact tree
    404s) STARTS a genuine build, and the longest build on record took 74.2s. The
    cap turned that into exit 1 status=timeout no matter what --timeout said.
    A cache hit answers in milliseconds regardless of the budget, so the budget
    costs nothing when the artifact really is there.
    """
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={SHA: "ok"},
                         pom=_response(200, body="<project/>", elapsed=0.2))

    jitpack_build(MODULE, timeout=900.0)

    assert http.timeouts[-1] == 900.0


def test_precheck_only_mode_will_not_call_an_ok_record_green(repo, monkeypatch):
    """timeout<=0 opts out of the one request a green verdict has to rest on."""
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={SHA: "ok"})

    result = jitpack_build(MODULE, timeout=0)

    assert result["ok"] is False
    assert result["status"] == "precondition"
    assert result["precheck"] == "ok"
    assert "requests nothing" in result["error"]
    assert http.urls == [LIST_URL]
    assert exit_code(result) == 2


def test_precheck_error_is_a_cached_failure(repo, monkeypatch):
    """JitPack never rebuilds a sha it has already failed, so nothing is requested."""
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={SHA: "Error"},
                         log=_response(200, body=_FAILING_LOG))

    result = jitpack_build(MODULE)

    assert result["ok"] is False
    assert result["status"] == "cached-failure"
    assert result["action"] == "refuse"
    assert "push a new commit" in result["error"]
    assert result["log_code"] == 200
    assert "22 errors" in result["log_tail"]
    assert http.urls == [LIST_URL,
                         f"https://jitpack.io/{GROUP_PATH}/{ARTIFACT}/{SHA}/build.log"]
    assert exit_code(result) == 1


def test_git_error_status_is_red_too(repo, monkeypatch):
    """Casing is inconsistent on this endpoint; any status containing 'error' is red."""
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={SHA: "gitError"}, log=_response(404))

    result = jitpack_build(MODULE)

    assert result["precheck"] == "error"
    assert result["status"] == "cached-failure"
    assert result["log_tail"] == ""
    assert "no build log" in result["log_note"]
    assert exit_code(result) == 1


def test_absent_record_proceeds_to_a_trigger(repo, monkeypatch):
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={},
                         pom=_response(200, body="<project/>", elapsed=74.2))

    result = jitpack_build(MODULE)

    assert result["precheck"] == "absent"
    assert result["action"] == "trigger"
    assert result["status"] == "built"
    assert result["ok"] is True
    assert result["bytes"] == len("<project/>")
    assert result["elapsed"] == 74.2
    assert http.urls[-1].endswith(f"/{SHA}/{ARTIFACT}-{SHA}.pom")
    assert exit_code(result) == 0


def test_force_re_requests_an_already_built_sha(repo, monkeypatch):
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={SHA: "ok"},
                         pom=_response(200, body="<project/>", elapsed=0.4))

    result = jitpack_build(MODULE, force=True)

    assert result["precheck"] == "ok"
    assert result["action"] == "trigger"
    assert result["status"] == "built"
    assert http.urls[-1].endswith(f"/{SHA}/{ARTIFACT}-{SHA}.pom")


def test_in_flight_precheck_attaches_to_the_running_build(repo, monkeypatch):
    """A late request attaches to a running build; it never starts a second one."""
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={SHA: "Building"},
                         pom=_response(200, body="<project/>", elapsed=31.0))

    result = jitpack_build(MODULE)

    assert result["precheck"] == "in-flight"
    assert result["action"] == "attach"
    assert result["status"] == "built"
    assert len([url for url in http.urls if url.endswith(".pom")]) == 1


def test_precheck_only_mode_reports_in_flight(repo, monkeypatch):
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={SHA: "Queued"})

    result = jitpack_build(MODULE, timeout=0)

    assert result["status"] == "in-flight"
    assert result["action"] == "none"
    assert result["ok"] is False
    assert http.urls == [LIST_URL]
    assert exit_code(result) == 1


def test_precheck_only_mode_on_an_absent_record_requests_nothing(repo, monkeypatch):
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={})

    result = jitpack_build(MODULE, timeout=0)

    assert result["status"] == "precondition"
    assert "requests nothing" in result["error"]
    assert http.urls == [LIST_URL]
    assert exit_code(result) == 2


def test_list_endpoint_404_means_no_records_not_unbuilt(repo, monkeypatch):
    """An outage or a 404 is 'no records', never a claim that the sha is unbuilt."""
    _install_git(monkeypatch)
    http = _FakeHttp()
    http.route("/api/builds/", _response(404, elapsed=0.1))
    http.route(".pom", _response(200, body="<project/>", elapsed=61.0))
    monkeypatch.setattr(jitpack, "_http", http)

    result = jitpack_build(MODULE)

    assert result["precheck"] == "absent"
    assert result["status"] == "built"


def test_malformed_list_json_is_tolerated(repo, monkeypatch):
    _install_git(monkeypatch)
    http = _FakeHttp()
    http.route("/api/builds/", _response(200, body="<html>challenge</html>"))
    monkeypatch.setattr(jitpack, "_http", http)

    result = jitpack_status(MODULE)

    assert result["records"] == 0
    assert result["counts"]["total"] == 0
    assert result["refs"][0]["state"] == "absent"
    assert result["refs"][0]["status"] is None
    assert result["refs"][0]["ok"] is False


def test_private_and_message_fields_are_never_read(repo, monkeypatch):
    """Both were misread as auth problems twice; they are zero-values on this API."""
    _install_git(monkeypatch)
    http = _FakeHttp()
    http.route("/api/builds/", _response(body=json.dumps(
        {GROUP: {ARTIFACT: {SHA: "ok", OTHER_SHA: "Error"}},
         "private": True, "message": "Not found"})))
    monkeypatch.setattr(jitpack, "_http", http)

    result = jitpack_status(MODULE)

    assert result["refs"][0]["ok"] is True
    assert result["records"] == 2
    assert result["counts"] == {"total": 2, "ok": 1, "error": 1,
                                "in-flight": 0, "unknown": 0}
    assert "private" not in result
    assert "message" not in result


# The body a repo with ZERO builds answers with - misread as an auth problem
# twice, 8 days apart, and reproducible on JitPack's own demo repo.
_NO_RECORD_BODY = json.dumps({"version": None, "status": "ok",
                              "message": "Not found", "private": True})


def test_a_no_record_body_is_zero_builds_not_two(repo, monkeypatch):
    """`status` and `message` are metadata fields, never versions.

    Read as a records map they become two builds - one of them GREEN - so a repo
    JitPack has never built reports `records=2 ok=1`.
    """
    _install_git(monkeypatch)
    http = _FakeHttp()
    http.route("/api/builds/", _response(body=_NO_RECORD_BODY, elapsed=0.11))
    monkeypatch.setattr(jitpack, "_http", http)

    result = jitpack_status(MODULE)

    assert result["records"] == 0
    assert result["counts"] == {"total": 0, "ok": 0, "error": 0,
                                "in-flight": 0, "unknown": 0}
    assert result["refs"][0]["state"] == "absent"
    assert result["ok"] is False


def test_a_no_record_body_never_makes_a_snapshot_pin_look_healthy(tmp_path, monkeypatch):
    """The end of the same bug: an unbuildable master-SNAPSHOT reported green.

    `pins` reads the artifact's summary for a SNAPSHOT row, so a fake `ok`
    record turns "a coordinate that has never resolved" into exit 0.
    """
    _pins_workspace(tmp_path, monkeypatch, consumers={"apps/bot": _snapshot_line("client")})
    _install_git(monkeypatch, _PINS_GIT)
    http = _FakeHttp()
    http.route("/api/builds/", _response(body=_NO_RECORD_BODY, elapsed=0.11))
    monkeypatch.setattr(jitpack, "_http", http)

    result = jitpack_pins()

    assert result["pins"][0]["state"] == "absent"
    assert result["unbuilt"] == 1
    assert result["ok"] is False
    assert exit_code(result) == 1


def test_a_records_map_without_the_group_envelope_is_still_read(repo, monkeypatch):
    """The positive shape test must not throw away a body that really is records."""
    _install_git(monkeypatch)
    http = _FakeHttp()
    http.route("/api/builds/", _response(body=json.dumps({SHA: "ok", OTHER_SHA: "Error"})))
    monkeypatch.setattr(jitpack, "_http", http)

    result = jitpack_status(MODULE)

    assert result["records"] == 2
    assert result["refs"][0]["state"] == "ok"


@pytest.mark.parametrize("payload", [
    {"version": None, "status": "ok", "message": "Not found", "private": True},
    {"status": "ok", "message": "Not found"},          # the string-only subset
    {"private": True, "isTag": False},
    {"modules": [], "time": 1783751764456},
    {"buildUrl": "https://jitpack.io/build/1", "commit": "bd23842", "ci": "gradle"},
    {"com.github.other": {"other": {"abc1234": "ok"}}},
    [{"version": "abc1234"}],
    {},
])
def test_only_a_real_records_map_becomes_records(monkeypatch, payload):
    """Every known metadata shape reads as zero records, never as builds."""
    http = _FakeHttp()
    http.route("/api/builds/", _response(body=json.dumps(payload)))
    monkeypatch.setattr(jitpack, "_http", http)

    assert jitpack._list_records(GROUP, ARTIFACT) == {}


@pytest.mark.parametrize("enveloped", [
    {GROUP: {ARTIFACT: {"version": None, "status": "ok",
                        "message": "Not found", "private": True}}},
    {GROUP: {"version": None, "status": "ok",
             "message": "Not found", "private": True}},
])
def test_the_group_envelope_is_screened_too(monkeypatch, enveloped):
    """Position inside the envelope is not evidence of being a build record.

    The first remediation screened only the bare-body path, so the identical
    metadata arriving inside the documented envelope still decoded to
    {'status': 'ok', 'message': 'Not found'} - two invented builds, one green,
    byte-identical to the original defect.
    """
    http = _FakeHttp()
    http.route("/api/builds/", _response(body=json.dumps(enveloped)))
    monkeypatch.setattr(jitpack, "_http", http)

    assert jitpack._list_records(GROUP, ARTIFACT) == {}


def test_one_odd_version_key_costs_only_that_key(monkeypatch):
    """Screening is per entry: a repo with real builds is never reported as having none."""
    http = _FakeHttp()
    http.route("/api/builds/", _response(body=json.dumps(
        {SHA: "ok", "feature/x-SNAPSHOT": "Error", OTHER_SHA: "Building"})))
    monkeypatch.setattr(jitpack, "_http", http)

    records = jitpack._list_records(GROUP, ARTIFACT)

    assert records == {SHA: "ok", OTHER_SHA: "Building"}


# --------------------------------------------------------------------------
# Verdict resolution. The blocking artifact GET is both the trigger and the
# wait, and its status code is the verdict - with latency as a tiebreaker on
# 404 and the body as the stronger discriminator.
# --------------------------------------------------------------------------

def test_a_green_build_states_what_it_does_not_prove(repo, monkeypatch):
    """JitPack runs -xtest and consumers substitute local sources; both caveats ship."""
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={},
                  pom=_response(200, body="<project/>", elapsed=74.2))

    result = jitpack_build(MODULE)

    assert result["ok"] is True
    assert "-xtest" in result["note"]
    assert "compile check, not a test run" in result["note"]
    assert any("includeBuild substitution" in hint for hint in result["hints"])
    assert result["pin"] == f'{GROUP}:{ARTIFACT} -> version {{ strictly("{SHA}") }}'


def test_slow_404_is_a_failed_build_with_the_log_tail(repo, monkeypatch):
    """The build ran and lost; the tail is real compiler output."""
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={},
                  pom=_response(404, body=_BUILD_FAILED_BODY, elapsed=42.0),
                  log=_response(200, body=_FAILING_LOG))

    result = jitpack_build(MODULE)

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["http_code"] == 404
    assert result["http_body"] == _BUILD_FAILED_BODY
    assert result["error"] == f"build failed for '{SHA}'"
    assert "Widget.java:12: error: cannot find symbol" in result["log_tail"]
    assert result["log_note"] is None
    assert exit_code(result) == 1


def test_fast_404_is_a_cached_negative(repo, monkeypatch):
    """Nothing served and nothing waited for - a cached failure past the precheck."""
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={}, pom=_response(404, elapsed=0.28),
                  log=_response(404))

    result = jitpack_build(MODULE)

    assert result["ok"] is False
    assert result["status"] == "cached-failure"
    assert "push a new commit" in result["error"]
    assert exit_code(result) == 1


def test_a_slow_empty_404_is_still_a_cached_negative(repo, monkeypatch):
    """Latency alone is not evidence: a cold TLS handshake costs about a second.

    JitPack served no failure body, so no build produced this 404 - and calling
    it a fresh failure prints the PREVIOUS build's log tail as if it were
    current, while withholding the one line that resolves it.
    """
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={}, pom=_response(404, body="", elapsed=1.4),
                  log=_response(200, body=_FAILING_LOG))

    result = jitpack_build(MODULE)

    assert result["status"] == "cached-failure"
    assert result["discriminator"] == "body"
    assert "jitpack never rebuilds the same sha" in result["error"]
    assert exit_code(result) == 1


def test_a_fast_404_carrying_the_failure_body_is_a_real_failure(repo, monkeypatch):
    """The converse: the 39-byte body says a build ran, whatever the clock says."""
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={},
                  pom=_response(404, body=_BUILD_FAILED_BODY, elapsed=0.6),
                  log=_response(200, body=_FAILING_LOG))

    result = jitpack_build(MODULE)

    assert result["status"] == "failed"
    assert result["discriminator"] == "body"
    assert result["error"] == f"build failed for '{SHA}'"
    assert "22 errors" in result["log_tail"]


def test_latency_only_breaks_a_tie_on_an_unrecognised_body(repo, monkeypatch):
    """A body that is neither the failure marker nor empty falls back to the clock."""
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={},
                  pom=_response(404, body="<html>cloudflare</html>", elapsed=42.0),
                  log=_response(404))

    result = jitpack_build(MODULE)

    assert result["status"] == "failed"
    assert result["discriminator"] == "latency"


def test_empty_log_is_never_read_as_success(repo, monkeypatch):
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={},
                  pom=_response(404, body=_BUILD_FAILED_BODY, elapsed=12.0),
                  log=_response(200, body="   \n"))

    result = jitpack_build(MODULE)

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["log_tail"] == ""
    assert "no build log" in result["log_note"]
    assert result["hints"] == []


def test_log_tail_is_capped_to_log_lines(repo, monkeypatch):
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={},
                  pom=_response(404, body=_BUILD_FAILED_BODY, elapsed=12.0),
                  log=_response(200, body="".join(f"line {i}\n" for i in range(200))))

    result = jitpack_build(MODULE, log_lines=5)

    assert result["log_tail"].splitlines() == [f"line {i}" for i in range(195, 200)]


def test_known_landmines_become_hints(repo, monkeypatch):
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={},
                  pom=_response(404, body=_BUILD_FAILED_BODY, elapsed=8.0),
                  log=_response(200, body=(
                      "/sdkman/candidates/java/21.0.2-open/bin/java: not found\n"
                      "Gradle 'publishToMavenLocal' task not found\n")))

    result = jitpack_build(MODULE)

    assert "delete 'jitpack.yml'; jitpack installs OpenJDK 21.0.2 by itself" in result["hints"]
    assert "benign - jitpack injects maven-publish" in result["hints"]


def test_401_is_failed_and_never_claims_private(repo, monkeypatch):
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={}, pom=_response(401, elapsed=0.3))

    result = jitpack_build(MODULE)

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["http_code"] == 401
    assert "1-hour visibility cache" in result["error"]
    assert "private" not in result
    assert exit_code(result) == 1


def test_unexpected_status_code_is_a_failure(repo, monkeypatch):
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={},
                  pom=_response(503, body="upstream unavailable", elapsed=1.5))

    result = jitpack_build(MODULE)

    assert result["status"] == "failed"
    assert result["http_code"] == 503
    assert "unexpected http 503" in result["error"]
    assert exit_code(result) == 1


def test_client_timeout_is_inconclusive_not_failed(repo, monkeypatch):
    """The build continues server-side; re-running attaches rather than rebuilding."""
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={},
                  pom=_response(None, elapsed=480.0, timed_out=True))

    result = jitpack_build(MODULE, timeout=480.0)

    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert result["resume"] is True
    assert result["http_code"] is None
    assert "re-run to attach" in result["note"]
    assert "error" not in result
    assert exit_code(result) == 1


def test_transport_failure_is_an_error_verdict(repo, monkeypatch):
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={},
                  pom=_response(None, elapsed=0.05,
                                error="[Errno 11001] getaddrinfo failed"))

    result = jitpack_build(MODULE)

    assert result["ok"] is False
    assert result["status"] == "error"
    assert "request to jitpack failed" in result["error"]
    assert exit_code(result) == 1


def test_build_log_is_never_fetched_before_a_terminal_verdict(repo, monkeypatch):
    """It triggers a build for an unbuilt sha and blocks and streams for a live one."""
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={},
                         pom=_response(200, body="<project/>", elapsed=50.0),
                         log=_response(200, body=_FAILING_LOG))

    assert jitpack_build(MODULE)["status"] == "built"
    assert not any("build.log" in url for url in http.urls)

    warm = _install_http(monkeypatch, records={SHA: "ok"}, log=_response(200, body=_FAILING_LOG),
                         pom=_response(200, body="<project/>", elapsed=0.2))
    assert jitpack_build(MODULE)["status"] == "already-built"
    assert not any("build.log" in url for url in warm.urls)


# --------------------------------------------------------------------------
# Symbolic refs. A sha path never redirects, so a 3xx means a symbolic version
# reached the artifact request - which a bare status-code check reads as a
# failure. The concrete version is resolved through maven-metadata.xml, never
# through the poisoned per-version API record.
# --------------------------------------------------------------------------

def _snapshot_pom_url() -> str:
    return (f"https://jitpack.io/{GROUP_PATH}/{ARTIFACT}/{_SNAPSHOT_VERSION}"
            f"/{ARTIFACT}-{_SNAPSHOT_VERSION}.pom")


def test_302_without_allow_symbolic_is_a_precondition(repo, monkeypatch):
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={},
                  pom=_response(302, elapsed=0.2, location=_snapshot_pom_url()))

    result = jitpack_build(MODULE)

    assert result["ok"] is False
    assert result["status"] == "symbolic"
    assert result["http_code"] == 302
    assert result["location"] == _snapshot_pom_url()
    assert "pin by sha" in result["error"]
    assert exit_code(result) == 2


def test_302_with_allow_symbolic_reports_the_resolved_version(repo, monkeypatch):
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={},
                         pom=[_response(302, elapsed=0.2, location=_snapshot_pom_url()),
                              _response(200, body="<project/>", elapsed=18.0)])

    result = jitpack_build(MODULE, allow_symbolic=True)

    assert result["ok"] is True
    assert result["status"] == "built"
    assert result["resolved_version"] == _SNAPSHOT_VERSION
    assert result["elapsed"] == 18.2
    assert http.calls[-1][1] is True  # the re-issue is the only followed request
    assert exit_code(result) == 0


class _Clock:
    """A monotonic clock that advances a fixed step on every read."""

    def __init__(self, step: float) -> None:
        self.now, self.step = 0.0, step

    def monotonic(self) -> float:
        self.now += self.step
        return self.now


def test_the_redirect_re_issue_shares_the_budget_it_does_not_double_it(repo, monkeypatch):
    """--timeout is what the caller agreed to wait in TOTAL.

    The watchdog is already stopped by then, so a second full-length wait is
    silent - and it is a second real build request, not a free retry.
    """
    _install_git(monkeypatch)
    monkeypatch.setattr(jitpack, "time", _Clock(30.0))
    http = _install_http(monkeypatch, records={},
                         pom=[_response(302, elapsed=0.2, location=_snapshot_pom_url()),
                              _response(200, body="<project/>", elapsed=18.0)])

    result = jitpack_build(MODULE, allow_symbolic=True, timeout=300.0)

    pom_budgets = [t for url, _follow, t in http.calls if url.endswith(".pom")]
    assert result["status"] == "built"
    assert pom_budgets == [300.0, 270.0]  # 300 minus the 30 s already spent
    assert sum(pom_budgets) < 2 * 300.0


def test_an_exhausted_budget_refuses_the_second_request(repo, monkeypatch):
    """Nothing left to wait with is a timeout, not another build request."""
    _install_git(monkeypatch)
    monkeypatch.setattr(jitpack, "time", _Clock(400.0))
    http = _install_http(monkeypatch, records={},
                         pom=[_response(302, elapsed=0.2, location=_snapshot_pom_url()),
                              _response(200, body="<project/>", elapsed=18.0)])

    result = jitpack_build(MODULE, allow_symbolic=True, timeout=300.0)

    assert result["status"] == "timeout"
    assert result["resume"] is True
    assert len([url for url in http.urls if url.endswith(".pom")]) == 1
    assert exit_code(result) == 1


def test_symbolic_ref_resolves_through_maven_metadata(repo, monkeypatch):
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, records={},
                         metadata=_response(200, body=_METADATA_XML),
                         pom=_response(200, body="<project/>", elapsed=9.0))

    result = jitpack_build(MODULE, ref="master-SNAPSHOT", allow_symbolic=True)

    assert result["ok"] is True
    assert result["status"] == "built"
    assert result["symbolic"] is True
    assert result["source"] == "master-SNAPSHOT"
    assert result["ref"] == _SNAPSHOT_VERSION
    assert result["full_sha"] is None
    assert (f"https://jitpack.io/{GROUP_PATH}/{ARTIFACT}/master-SNAPSHOT/maven-metadata.xml"
            in http.urls)
    assert http.urls[-1] == _snapshot_pom_url()
    assert [url for url in http.urls if "/api/" in url] == [LIST_URL]


def test_symbolic_without_metadata_is_a_precondition(repo, monkeypatch):
    """The branch JitPack never registered answers a frozen tagNotFound on the API."""
    _install_git(monkeypatch)
    http = _install_http(monkeypatch, metadata=_response(404))

    result = jitpack_build(MODULE, ref="master-SNAPSHOT", allow_symbolic=True)

    assert result["ok"] is False
    assert result["status"] == "precondition"
    assert "no maven-metadata for 'master-SNAPSHOT'" in result["error"]
    assert not any(url.endswith(".pom") for url in http.urls)
    assert exit_code(result) == 2


# --------------------------------------------------------------------------
# The poisoned endpoint. /api/builds/<group>/<artifact>/<version> silently
# TRIGGERS a build and answers from records up to ~122 days stale: one aborted
# 3-second request against it once started a 74-second build, and the natural
# hand-rolled shape of `pins` would fire one per pin. No code path may reach
# it. This is the regression guard on the most expensive finding in the record.
# --------------------------------------------------------------------------

def test_never_requests_the_per_version_api_endpoint(tmp_path, monkeypatch):
    _make_module(tmp_path, MODULE, build=(
        "dependencies {\n"
        f'    api("{GROUP}:collections") {{ version {{ strictly("c741e14") }} }}\n'
        "}\n"))
    _make_module(tmp_path, "libs/collections")
    _install_inventory(tmp_path, monkeypatch)
    _install_git(monkeypatch, {
        f"rev-parse --verify {SHA}^{{commit}}": FULL,
        f"rev-parse --verify {OTHER_SHA}^{{commit}}": OTHER_FULL,
        f"rev-parse --disambiguate={OTHER_SHA}": OTHER_FULL,
    })
    http = _install_http(
        monkeypatch,
        by_artifact={ARTIFACT: {SHA: "ok", OTHER_SHA: "Error"},
                     "collections": {"c741e14": "ok"}},
        pom=_response(200, body="<project/>", elapsed=40.0),
        log=_response(200, body=_FAILING_LOG),
        metadata=_response(200, body=_METADATA_XML),
    )

    # Every branch that touches the network, in one recording.
    assert jitpack_status(MODULE, refs=[SHA, OTHER_SHA])["records"] == 2
    assert jitpack_build(MODULE)["status"] == "already-built"
    assert jitpack_build(MODULE, force=True)["status"] == "built"
    assert jitpack_build(MODULE, ref=OTHER_SHA)["status"] == "cached-failure"
    assert jitpack_build(MODULE, ref="master-SNAPSHOT",
                         allow_symbolic=True)["status"] == "built"
    assert jitpack_build(MODULE, ref="deadbee")["status"] == "precondition"
    assert jitpack_pins()["total"] == 1

    api_urls = [url for url in http.urls if "/api/" in url]
    assert api_urls, "the versionless list endpoint should have been read"
    assert LIST_URL in api_urls
    assert all(_LIST_URL_RE.fullmatch(url) for url in api_urls)
    assert not any(re.search(r"/api/builds/[^/]+/[^/]+/", url) for url in http.urls)

    # ... and the guard that enforces it in every other test is not vacuous.
    with pytest.raises(AssertionError):
        http(f"{LIST_URL}/{SHA}", timeout=1.0)


@pytest.mark.parametrize("version", [
    "..", ".", "../../api/builds/com.github.o/a/x", "a/b", "%2e%2e", "-lead", "", "a b",
    "a?b", "a#b", "a%2fb",
])
def test_a_version_that_could_leave_its_path_segment_is_refused(version):
    """urllib sends dot segments verbatim, and any proxy normalises them server-side.

    A version is server-controlled text out of maven-metadata.xml, so the one
    endpoint no code path may request is a "../.." away from _pom_url unless the
    charset is enforced in production code rather than in a test fake.
    """
    assert jitpack._is_segment(version) is False
    with pytest.raises(ValueError):
        jitpack._pom_url({"group_path": GROUP_PATH, "artifact": ARTIFACT}, version)


@pytest.mark.parametrize("version", [SHA, _SNAPSHOT_VERSION, "1.4.0", "v2.3.1_beta+7"])
def test_a_real_version_still_builds_its_url(version):
    assert jitpack._is_segment(version) is True
    assert jitpack._pom_url({"group_path": GROUP_PATH, "artifact": ARTIFACT},
                            version).endswith(f"/{version}/{ARTIFACT}-{version}.pom")


def test_a_traversing_metadata_version_never_reaches_a_request(repo, monkeypatch):
    """The document is JitPack's, not ours; a corrupt one must not steer our path."""
    _install_git(monkeypatch)
    traversal = "../../api/builds/com.github.simplified-dev/discord4j-fauxrig/master-SNAPSHOT"
    http = _install_http(monkeypatch, records={}, metadata=_response(200, body=(
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<metadata><versioning><release>{traversal}</release></versioning></metadata>")))

    result = jitpack_build(MODULE, ref="master-SNAPSHOT", allow_symbolic=True)

    assert result["ok"] is False
    assert result["status"] == "precondition"
    assert "not a usable url path segment" in result["error"]
    assert [url for url in http.urls if "/api/" in url] == []
    assert not any(url.endswith(".pom") for url in http.urls)
    assert exit_code(result) == 2


def test_a_branch_that_is_not_a_path_segment_is_refused_before_any_request(repo, monkeypatch):
    """A slash in the ref would split the maven-metadata path in two."""
    _install_git(monkeypatch)
    http = _install_http(monkeypatch)

    result = jitpack_build(MODULE, ref="feature/nested-SNAPSHOT", allow_symbolic=True)

    assert result["status"] == "precondition"
    assert "not a usable version segment" in result["error"]
    assert http.urls == []
    assert exit_code(result) == 2


# --------------------------------------------------------------------------
# Pin drift. Read only, one list call per distinct artifact, and no rewriting:
# the same artifact really is pinned to three different shas by three families,
# and picking a winner is a human decision.
# --------------------------------------------------------------------------

# Each repo reports a remote named after its own directory, so a library module
# maps to the artifact it publishes.
_PINS_GIT = {
    "config --get remote.origin.url": lambda d: f"https://github.com/{ORG}/{d.name}.git",
    "symbolic-ref --short refs/remotes/origin/HEAD": "origin/master",
    "rev-list --count c741e14..origin/master": "0",
    "rev-list --count 2f2aa58..origin/master": "80",
    "rev-list --count 4140130..origin/master": "107",
}


def _pin_line(artifact: str, sha: str) -> str:
    return f'    api("{GROUP}:{artifact}") {{ version {{ strictly("{sha}") }} }}\n'


def _snapshot_line(artifact: str) -> str:
    return f'    api("{GROUP}:{artifact}:master-SNAPSHOT")\n'


def _pins_workspace(tmp_path, monkeypatch, consumers: dict[str, str],
                    libs: tuple[str, ...] = ()) -> Path:
    """A workspace of library repos plus consumer modules carrying pin lines."""
    for lib in libs:
        _make_module(tmp_path, f"libs/{lib}")
    for rel, body in consumers.items():
        _make_module(tmp_path, rel, build=body)
    return _install_inventory(tmp_path, monkeypatch)


def test_pins_counts_behind_and_calls_the_list_once_per_artifact(tmp_path, monkeypatch):
    """One artifact, three shas, four consumer lines - and exactly one request."""
    _pins_workspace(tmp_path, monkeypatch, libs=("collections",), consumers={
        "apps/bot": _pin_line("collections", "c741e14") + _pin_line("collections", "2f2aa58"),
        "apps/tool": _pin_line("collections", "c741e14") + _pin_line("collections", "4140130"),
    })
    _install_git(monkeypatch, _PINS_GIT)
    http = _install_http(monkeypatch, by_artifact={
        "collections": {"c741e14": "ok", "2f2aa58": "ok", "4140130": "ok"}})

    result = jitpack_pins()
    rows = {row["pin"]: row for row in result["pins"]}

    assert result["ok"] is True
    assert result["total"] == 3
    assert result["artifacts"] == 1
    assert result["list_calls"] == 1
    assert len([url for url in http.urls if "/api/builds/" in url]) == 1
    assert rows["c741e14"]["behind"] == 0
    assert rows["2f2aa58"]["behind"] == 80
    assert rows["4140130"]["behind"] == 107
    assert rows["c741e14"]["consumers"] == 2
    assert sorted(rows["c741e14"]["consumer_paths"]) == ["apps/bot/build.gradle.kts:1",
                                                         "apps/tool/build.gradle.kts:1"]
    assert result["stale"] == 2  # behind=0 is current, not stale
    assert result["median_behind"] == 80
    assert exit_code(result) == 0


def test_pins_unresolvable_pin_reports_no_behind_count(tmp_path, monkeypatch):
    """A sha never fetched into this clone cannot be counted, and is not "up to date"."""
    _pins_workspace(tmp_path, monkeypatch, libs=("collections",),
                    consumers={"apps/bot": _pin_line("collections", "6586657")})
    _install_git(monkeypatch, _PINS_GIT)
    _install_http(monkeypatch, by_artifact={"collections": {"6586657": "ok"}})

    result = jitpack_pins()
    row = result["pins"][0]

    assert row["form"] == "strictly"
    assert row["state"] == "ok"
    assert row["jitpack"] == "ok"
    assert row["behind"] is None
    assert result["stale"] == 0
    assert result["median_behind"] is None
    assert result["ok"] is True


def test_pins_unbuilt_or_failed_pin_flips_the_verdict(tmp_path, monkeypatch):
    _pins_workspace(tmp_path, monkeypatch, libs=("collections",), consumers={
        "apps/bot": _pin_line("collections", "2f2aa58") + _pin_line("collections", "6586657")})
    _install_git(monkeypatch, _PINS_GIT)
    _install_http(monkeypatch, by_artifact={"collections": {"2f2aa58": "Error"}})

    result = jitpack_pins()
    rows = {row["pin"]: row for row in result["pins"]}

    assert rows["2f2aa58"]["state"] == "error"
    assert rows["2f2aa58"]["jitpack"] == "Error"
    assert rows["6586657"]["state"] == "absent"
    assert rows["6586657"]["jitpack"] == "absent"
    assert result["errors"] == 1
    assert result["unbuilt"] == 1
    assert result["ok"] is False
    assert exit_code(result) == 1


def test_pins_max_behind_is_the_opt_in_gate(tmp_path, monkeypatch):
    """Staleness alone is not red - most pins are stale and a default gate is noise."""
    _pins_workspace(tmp_path, monkeypatch, libs=("collections",),
                    consumers={"apps/bot": _pin_line("collections", "2f2aa58")})
    _install_git(monkeypatch, _PINS_GIT)
    _install_http(monkeypatch, by_artifact={"collections": {"2f2aa58": "ok"}})

    assert jitpack_pins()["ok"] is True

    result = jitpack_pins(max_behind=5)

    assert result["ok"] is False
    assert result["max_behind"] == 5
    assert result["over_max_behind"] == 1
    assert exit_code(result) == 1


def test_pins_snapshot_form_reports_artifact_health_without_drift(tmp_path, monkeypatch):
    """A SNAPSHOT names no concrete version, so only the artifact's health is answerable."""
    _pins_workspace(tmp_path, monkeypatch, consumers={
        "apps/bot": _snapshot_line("client") + _snapshot_line("scheduler")})
    _install_git(monkeypatch, _PINS_GIT)
    _install_http(monkeypatch, by_artifact={"client": {"abc1234": "ok"},
                                            "scheduler": {"abc1234": "Error"}})

    result = jitpack_pins()
    rows = {row["artifact"]: row for row in result["pins"]}

    assert rows["client"]["form"] == "snapshot"
    assert rows["client"]["pin"] == "master-SNAPSHOT"
    assert rows["client"]["state"] == "ok"
    assert rows["client"]["behind"] is None
    assert rows["scheduler"]["state"] == "error"
    assert result["list_calls"] == 2
    assert result["ok"] is False


def test_pins_central_artifact_is_reported_but_not_gated(tmp_path, monkeypatch):
    """annotations publishes to Maven Central, so having no JitPack record is not a fault."""
    _pins_workspace(tmp_path, monkeypatch, libs=("collections",), consumers={
        "apps/bot": f'    api("io.github.{ORG}:annotations:1.4.0")\n'
                    + _pin_line("collections", "c741e14")})
    _install_git(monkeypatch, _PINS_GIT)
    http = _install_http(monkeypatch, by_artifact={"collections": {"c741e14": "ok"}})

    result = jitpack_pins()
    rows = {row["artifact"]: row for row in result["pins"]}

    assert rows["annotations"]["central"] is True
    assert rows["annotations"]["jitpack"] == "n/a (central)"
    assert rows["annotations"]["state"] == "central"
    assert rows["annotations"]["behind"] is None
    assert result["ok"] is True
    assert result["list_calls"] == 1
    assert not any("annotations" in url for url in http.urls)


def test_pins_artifact_filter_narrows_the_scan(tmp_path, monkeypatch):
    _pins_workspace(tmp_path, monkeypatch, libs=("collections",), consumers={
        "apps/bot": _pin_line("collections", "c741e14") + _snapshot_line("client")})
    _install_git(monkeypatch, _PINS_GIT)
    http = _install_http(monkeypatch, by_artifact={"collections": {"c741e14": "ok"}})

    result = jitpack_pins("coll")

    assert [row["artifact"] for row in result["pins"]] == ["collections"]
    assert result["list_calls"] == 1
    assert not any("client" in url for url in http.urls)


def test_pins_without_an_inventory_is_a_precondition(monkeypatch):
    monkeypatch.setattr(jitpack, "workspace_root", lambda: None)

    result = jitpack_pins()

    assert result["ok"] is False
    assert result["pins"] == []
    assert result["total"] == 0
    assert "no inventory" in result["error"]
    assert exit_code(result) == 2


def test_pins_with_nothing_to_report_says_so(repo, monkeypatch):
    _install_git(monkeypatch, _PINS_GIT)
    http = _install_http(monkeypatch)

    result = jitpack_pins()

    assert result["ok"] is True
    assert result["total"] == 0
    assert "no jitpack pins found" in result["note"]
    assert http.urls == []


# --------------------------------------------------------------------------
# Normalisation, the shared timeout budget, and the exit-code mapping - which
# is deliberately not the usual "an error means 2", since a failed build and a
# cached failure both carry an error yet are ordinary red verdicts.
# --------------------------------------------------------------------------

def test_status_reads_exactly_one_url(repo, monkeypatch):
    """status is the safe way to ask, so it may never request an artifact."""
    _install_git(monkeypatch, {
        f"rev-parse --verify {SHA}^{{commit}}": FULL,
        f"rev-parse --verify {OTHER_SHA}^{{commit}}": OTHER_FULL,
        f"rev-parse --disambiguate={OTHER_SHA}": OTHER_FULL,
    })
    http = _install_http(monkeypatch, records={SHA: "ok", OTHER_SHA: "Error"})

    result = jitpack_status(MODULE, refs=[SHA, OTHER_SHA])

    assert http.urls == [LIST_URL]
    assert result["records"] == 2
    assert result["counts"]["error"] == 1
    assert [entry["state"] for entry in result["refs"]] == ["ok", "error"]
    assert [entry["ok"] for entry in result["refs"]] == [True, False]
    assert result["ok"] is False
    assert exit_code(result) == 1


def test_classify_normalises_inconsistent_casing():
    """`ok` is lowercase, `Error`/`Building` are capitalised, and gitError is red."""
    assert jitpack._classify(None) == "absent"
    assert jitpack._classify("ok") == "ok"
    assert jitpack._classify("OK") == "ok"
    assert jitpack._classify("Error") == "error"
    assert jitpack._classify("gitError") == "error"
    assert jitpack._classify("Building") == "in-flight"
    assert jitpack._classify("Queued") == "in-flight"
    assert jitpack._classify("none") == "unknown"  # never a signal in its own right


def test_progress_callback_never_gates_the_verdict(repo, monkeypatch):
    """The watchdog is cosmetic; the blocking request is the whole waiter."""
    _install_git(monkeypatch)
    _install_http(monkeypatch, records={},
                  pom=_response(200, body="<project/>", elapsed=74.2))
    lines: list[str] = []

    result = jitpack_build(MODULE, progress=lines.append)

    assert result["status"] == "built"
    assert lines == []  # nothing to report before the first 12 s poll


def test_timeout_constants_are_shared_not_duplicated():
    assert jitpack.BUILD_TIMEOUT == 900.0
    assert jitpack.MCP_BUILD_TIMEOUT == 480.0  # under a typical 10-minute harness cap
    assert jitpack.MCP_BUILD_TIMEOUT < jitpack.BUILD_TIMEOUT
    assert jitpack.LIST_TIMEOUT == 20.0


def test_exit_code_treats_a_red_verdict_as_one_not_two():
    assert exit_code({"ok": True, "status": "built"}) == 0
    assert exit_code({"ok": True, "status": "already-built"}) == 0
    assert exit_code({"ok": False, "status": "failed", "error": "build failed"}) == 1
    assert exit_code({"ok": False, "status": "cached-failure", "error": "cached"}) == 1
    assert exit_code({"ok": False, "status": "timeout", "resume": True}) == 1
    assert exit_code({"ok": False, "status": "error", "error": "dns"}) == 1
    assert exit_code({"ok": False, "status": "in-flight"}) == 1
    assert exit_code({"ok": False, "status": "precondition", "error": "no remote"}) == 2
    assert exit_code({"ok": False, "status": "symbolic", "error": "pin by sha"}) == 2
    assert exit_code({"ok": False, "error": "no inventory"}) == 2


# --------------------------------------------------------------------------
# The transport itself. Everything above replaces jitpack._http, so the layer
# every verdict rests on - the redirect-as-data handler, the HTTPError-to-code
# mapping, the timeout mapping, a stream that breaks mid-body - would otherwise
# never execute. These are the only tests in the file that open a socket, and it
# is a loopback server they own: no external network, and they skip themselves
# if a socket cannot be bound at all.
# --------------------------------------------------------------------------

_SERVED: list[str] = []


class _LoopbackHandler(BaseHTTPRequestHandler):
    """A stand-in for jitpack.io that answers each measured shape on its own path."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the pytest output clean
        pass

    def _send(self, code: int, body: bytes = b"", **headers) -> None:
        self.send_response(code)
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's own naming
        _SERVED.append(self.path)
        if self.path == "/ok":
            self._send(200, b"<project/>", Content_Type="text/xml")
        elif self.path == "/redirect":
            self._send(302, Location="/ok")
        elif self.path == "/unauthorized":
            self._send(401)
        elif self.path == "/failed":
            self._send(404, _BUILD_FAILED_BODY.encode())
        elif self.path == "/slow":
            time.sleep(1.5)
            self._send(200, b"<project/>")
        elif self.path == "/truncated":
            # A chunk header claiming 16 bytes, 5 bytes sent, then the
            # connection drops - jitpack holds the .pom open chunked for the
            # whole build, so this is the shape a mid-build drop arrives in.
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"10\r\n12345")
            self.close_connection = True
        elif self.path.startswith("/api/builds/"):
            self._send(200, json.dumps({GROUP: {ARTIFACT: {SHA: "Building"}}}).encode())
        elif self.path.endswith(".pom"):
            self._send(200, b"<project/>", Content_Type="text/xml")
        else:
            self._send(404)


@pytest.fixture(scope="module")
def loopback():
    """A real HTTP server on 127.0.0.1, or a skip when sockets are unavailable."""
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _LoopbackHandler)
    except OSError:  # pragma: no cover - a socket-less sandbox stays green
        pytest.skip("no loopback socket available")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_real_http_maps_a_200(loopback):
    result = jitpack._http(f"{loopback}/ok", timeout=5.0)

    assert result["code"] == 200
    assert result["body"] == "<project/>"
    assert result["timed_out"] is False
    assert result["error"] is None
    assert result["elapsed"] >= 0.0


def test_real_http_surfaces_a_302_as_data(loopback):
    """_NoRedirect exists so a 302 stays observable - a sha path never redirects."""
    result = jitpack._http(f"{loopback}/redirect", timeout=5.0)

    assert result["code"] == 302
    assert result["location"] == "/ok"
    assert result["error"] is None


def test_real_http_follows_a_redirect_only_when_asked(loopback):
    result = jitpack._http(f"{loopback}/redirect", timeout=5.0, follow=True)

    assert result["code"] == 200
    assert result["body"] == "<project/>"


def test_real_http_maps_a_401(loopback):
    assert jitpack._http(f"{loopback}/unauthorized", timeout=5.0)["code"] == 401


def test_real_http_maps_a_404_and_keeps_its_body(loopback):
    """The 39-byte body is the discriminator, so it has to survive the transport."""
    result = jitpack._http(f"{loopback}/failed", timeout=5.0)

    assert result["code"] == 404
    assert result["body"] == _BUILD_FAILED_BODY


def test_real_http_maps_a_timeout_to_data_not_an_exception(loopback):
    result = jitpack._http(f"{loopback}/slow", timeout=0.25)

    assert result["timed_out"] is True
    assert result["code"] is None
    assert result["error"] is None


def test_real_http_survives_a_stream_that_breaks_mid_body(loopback):
    """IncompleteRead is not an OSError; unhandled it is a traceback, not a verdict."""
    result = jitpack._http(f"{loopback}/truncated", timeout=5.0)

    assert isinstance(result, dict)
    assert result["error"] is not None
    assert "IncompleteRead" in result["error"]
    # The status line said 200 before the body stopped arriving; a half-read
    # response is not a green verdict, so the code does not survive the break.
    assert result["code"] is None
    assert result["body"] == ""


def test_real_http_maps_an_unreachable_host_to_an_error(loopback):
    result = jitpack._http("http://127.0.0.1:1/nothing", timeout=5.0)

    assert result["code"] is None
    assert result["error"] is not None
    assert result["timed_out"] is False


def test_a_whole_build_over_the_real_transport(repo, monkeypatch, loopback):
    """One end-to-end pass with nothing faked below jitpack itself."""
    _install_git(monkeypatch)
    monkeypatch.setattr(jitpack, "_BASE", loopback)
    _SERVED.clear()

    result = jitpack_build(MODULE)

    assert result["ok"] is True
    assert result["status"] == "built"
    assert result["precheck"] == "in-flight"  # the loopback list reports Building
    assert result["action"] == "attach"
    assert result["http_code"] == 200
    assert _SERVED == [f"/api/builds/{GROUP}/{ARTIFACT}",
                       f"/{GROUP_PATH}/{ARTIFACT}/{SHA}/{ARTIFACT}-{SHA}.pom"]
    assert not any(re.search(r"/api/builds/[^/]+/[^/]+/", path) for path in _SERVED)
    assert exit_code(result) == 0


class _RaisingOpener:
    """An opener whose open() always raises, standing in for a broken connection."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def open(self, request, timeout=None):
        raise self.error


@pytest.mark.parametrize("error", [
    http.client.IncompleteRead(b"12345"),
    http.client.BadStatusLine("garbage"),
    http.client.LineTooLong("header line"),
    http.client.ResponseNotReady(),
])
def test_http_turns_a_broken_stream_into_a_verdict_not_a_traceback(monkeypatch, error):
    """http.client's exceptions are not OSErrors and were escaping the module."""
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: _RaisingOpener(error))

    result = jitpack._http("https://jitpack.io/x.pom", timeout=1.0)

    assert result["code"] is None
    assert result["timed_out"] is False
    assert type(error).__name__ in result["error"]


def test_a_broken_stream_reaches_the_caller_as_a_status_not_a_stack_trace(repo, monkeypatch):
    """The module's contract is that nothing raises across its boundary."""
    _install_git(monkeypatch)
    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *a: _RaisingOpener(http.client.IncompleteRead(b"12345")))

    result = jitpack_build(MODULE)

    assert result["ok"] is False
    assert result["status"] == "error"
    assert "IncompleteRead" in result["error"]
    assert exit_code(result) == 1


def test_an_http_error_response_is_closed(monkeypatch):
    """HTTPError IS the response object, so it holds a socket until it is closed."""
    closed: list[bool] = []

    class _ClosingError(urllib.error.HTTPError):
        def close(self):
            closed.append(True)
            super().close()

    error = _ClosingError("https://jitpack.io/x.pom", 404, "Not Found", {},
                          io.BytesIO(_BUILD_FAILED_BODY.encode()))
    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: _RaisingOpener(error))

    result = jitpack._http("https://jitpack.io/x.pom", timeout=1.0)

    assert result["code"] == 404
    assert result["body"] == _BUILD_FAILED_BODY
    assert closed == [True]


def test_the_watchdog_polls_the_list_endpoint_and_stops_when_told(monkeypatch):
    """Cosmetic output only - and never a second thread left running after the wait."""
    monkeypatch.setattr(jitpack, "_WATCH_INTERVAL", 0.01)
    monkeypatch.setattr(jitpack, "_list_records",
                        lambda group, artifact, timeout: {SHA: "Building"})
    stop = threading.Event()
    lines: list[str] = []

    def progress(line: str) -> None:
        lines.append(line)
        if len(lines) == 3:
            stop.set()

    jitpack._watchdog({"group": GROUP, "artifact": ARTIFACT}, SHA, progress, stop,
                      time.monotonic())

    assert len(lines) == 3
    assert all(f"{SHA}: Building" in line for line in lines)


def test_the_watchdog_never_breaks_the_wait_it_decorates(monkeypatch):
    """A failed poll is not a failed build; the blocking GET is the whole waiter."""
    monkeypatch.setattr(jitpack, "_WATCH_INTERVAL", 0.01)

    def boom(group, artifact, timeout):
        raise RuntimeError("list endpoint down")

    monkeypatch.setattr(jitpack, "_list_records", boom)
    lines: list[str] = []

    jitpack._watchdog({"group": GROUP, "artifact": ARTIFACT}, SHA, lines.append,
                      threading.Event(), time.monotonic())

    assert lines == []


# --------------------------------------------------------------------------
# Edge-case matrix rows deliberately without a test of their own:
#
#   11 (a `gh repo create` that did not attach the remote) reaches the code as
#      either no remote or an unpushed ref, both covered above.
#   14/15/16 (`private`, `message`, `status:"ok"` with empty modules) are
#      never-read fields: proven negatively by
#      test_private_and_message_fields_are_never_read and by the fact that only
#      200 on the .pom produces a green build verdict.
#   19/20 (`/api/latest`, `/api/search`, `/api/downloads`) are covered by
#      test_never_requests_the_per_version_api_endpoint, which asserts every
#      /api/ URL is the versionless list and nothing else.
#   29 (two callers racing on one sha) needs no handling - a late request
#      attaches to the running build - so there is no behaviour to assert.
#   30/31 hint signatures are matched in test_known_landmines_become_hints; the
#      jitpack.yml files themselves are a chore, not tool behaviour.
# --------------------------------------------------------------------------
