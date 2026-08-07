"""JitPack build status, trigger-and-wait, and workspace pin drift.

Replaces the round that has been hand-authored roughly once a day and never
the same way twice - thirty distinct command shapes for one operation:

    curl -s "https://jitpack.io/api/builds/com.github.<org>/<artifact>"
    curl -s -o /dev/null -w "%{http_code} %{time_total}" --max-time 900 \\
        "https://jitpack.io/com/github/<org>/<artifact>/<sha>/<artifact>-<sha>.pom"
    curl -s "https://jitpack.io/com/github/<org>/<artifact>/<sha>/build.log" | tail -60

The happy path really is one request; what costs time is the dozen rules
guarding it, all of them measured against the live service and none of them
discoverable from JitPack's documentation:

  * ``/api/builds/<group>/<artifact>/<version>`` - the per-version endpoint -
    SILENTLY TRIGGERS A BUILD, and its records go stale for months. **No code
    path in this module may ever request it.** The versionless *list* endpoint
    is the only ``/api/`` URL used here: it is fast, fresh, reports in-flight
    builds, and does not trigger.
  * A build is triggered AND waited on by a single blocking GET of the ``.pom``.
    There is no poll loop and no retry loop - every retry is another real build
    request against a third-party service. The list endpoint is the sole
    exception and the sole request here that may be retried: it triggers
    nothing, so a re-read costs a pause rather than a build.
  * A list read that did not ANSWER is not an artifact with no builds. Reading
    the two alike is how ``pins`` reported healthy pins as unbuilt - a different
    handful on each run, because one list call per artifact back to back draws
    throttling, and a throttled read decoded as zero records.
  * The list reports ``ok`` for artifacts that answer 404, so a record is never
    a green verdict on its own: ``build`` goes green on an HTTP 200 from the
    ``.pom`` and on nothing less. For an already-built sha that is a cache hit.
  * JitPack cannot distinguish a bogus sha from an unbuilt one, so ref validity
    is established locally (``git rev-parse``) or not at all.
  * A cached failure never rebuilds for the same sha; only a new commit does.
  * ``private``, ``message``, ``isTag`` and ``status:"none"`` are zero-values or
    outright wrong on this API and are never read.

Failure is data: every public function returns a dict and nothing raises across
the boundary. Human formatting belongs to the CLI adapter, not here.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from . import __version__
from .modules import get_modules, resolve_module, workspace_root

_BASE = "https://jitpack.io"

# Cloudflare challenges bare/absent agents, so identify properly.
_UA = f"toolsmith/{__version__} (+jitpack)"

# Timeout budget. No build ceiling has ever been measured; the longest observed
# build is 74 s and JitPack's FAQ claims 15 minutes, so the artifact wait sits
# above the claimed ceiling with slack. MCP callers should pass
# _MCP_BUILD_TIMEOUT instead - deliberately below a typical 10-minute harness
# cap, so the tool returns a clean status="timeout" dict rather than being
# killed mid-call. A timeout is inconclusive, never red: the build continues
# server-side and a later request attaches to it instead of starting a second.
_BUILD_TIMEOUT = 900.0
_MCP_BUILD_TIMEOUT = 480.0
_LIST_TIMEOUT = 20.0
_LOG_TIMEOUT = 120.0

# Re-reads of the list endpoint, and the pause before each. Bounded and small:
# this is the one request that triggers nothing, so retrying it is safe, but it
# is still a third-party service and a burst is what provoked the throttling in
# the first place.
_LIST_ATTEMPTS = 3
_LIST_BACKOFF = 0.5
_WATCH_INTERVAL = 12.0
_WATCH_TIMEOUT = 10.0
_GIT_TIMEOUT = 15.0

# Each distinct sha prefix length is a SEPARATE JitPack build, so the prefix is
# always derived here and never taken from the caller.
_SHA_LEN = 7

# The 39-byte body a failed build answers with on the artifact path. It is the
# PRIMARY discriminator on a 404 - a build either produced this failure or it
# did not - and latency is only the tiebreaker for a body neither seen nor empty.
_BUILD_FAILED_BODY = "Build failed. See the log at jitpack.io"

# Tiebreaker only: below this a 404 with an unrecognised body came from a cached
# negative rather than a build that ran. Wall-clock across the public internet is
# not evidence on its own - a cold TLS handshake alone can cost a second.
_FAST_404 = 1.0

_MAX_BODY = 2_000_000
_LOG_TAIL_CHARS = 8000

_XTEST_NOTE = "jitpack builds with -xtest; this is a compile check, not a test run"
_CACHED_FAILURE_NOTE = ("cached failure - jitpack never rebuilds the same sha; "
                        "push a new commit and re-run")
_NO_LOG_NOTE = ("(no build log - jitpack answers 404 'File not found. Build Error' "
                "for some failures)")
_TIMEOUT_NOTE = ("build still running server-side - re-run to attach to it, "
                 "a late request never starts a second build")
_COMPOSITE_NOTE = ("verify standalone from the module dir, not from the workspace root - "
                   "includeBuild substitution makes a green consumer build prove nothing")
_MISMATCH_NOTE = ("jitpack's build list says 'ok' but serves no artifact for this version "
                  "(jitpack issue #7711) - the pin would not resolve; push a new commit "
                  "and re-run")
_UNREACHABLE_NOTE = ("jitpack's build list did not answer for some artifacts - inconclusive, "
                     "NOT a missing build; re-run, or narrow the scan with an artifact filter")
_CONFLICT_NOTE = ("strictly() is gradle's hardest constraint, so two different pins for one "
                  "artifact in a single graph is a resolution failure, not 'newest wins'")
_GIT_REMEDY = "git remote add origin <url> && git push -u origin master"

# Status codes meaning the list did not answer, as opposed to answering "none".
# 429 is the one that actually bites - `pins` issues one list call per artifact
# back to back - but a 5xx is just as much a non-answer. A 404 is deliberately
# absent: that IS an answer, and it means jitpack has never seen the artifact.
_LIST_RETRY_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

# Sha paths never redirect, so any of these means a symbolic ref reached the
# artifact GET. A bare status-code check reads it as failure.
_REDIRECTS = (301, 302, 303, 307, 308)

# The subset of a resolved coordinate that every public return carries.
_COORD_KEYS = ("module", "group", "artifact", "org", "repo", "repo_dir")

# Statuses where nothing was requested and the caller mis-invoked us, mapping to
# the exit code argparse itself reserves for a usage error.
_PRECONDITION_STATUSES = ("precondition", "symbolic")

# Caller-facing defaults, named so the CLI and the MCP tool can share them.
# They differ deliberately: see the timeout budget above.
BUILD_TIMEOUT = _BUILD_TIMEOUT
MCP_BUILD_TIMEOUT = _MCP_BUILD_TIMEOUT
LIST_TIMEOUT = _LIST_TIMEOUT

# Windows only: keep a child git off the MCP server's console (see gradle.py).
_NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

_REMOTE_RE = re.compile(
    r"^(?:(?:https?|ssh|git)://)?(?:[^@/]+@)?(?P<host>[^/:]+)[/:]"
    r"(?P<org>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)

_COORD_RE = re.compile(
    r"""["'](?P<group>(?:com|io)\.github\.[\w.-]+):(?P<artifact>[\w.-]+)"""
    r"""(?::(?P<version>[^"':]+))?["']"""
)
_STRICTLY_RE = re.compile(r"""strictly\(\s*["']([^"']+)["']\s*\)""")
_GROUP_DECL_RE = re.compile(r"""^\s*group\s*=\s*["'](io\.github\.[\w.-]+)["']""", re.M)

# A version or branch is server-controlled text (maven-metadata.xml, a --ref)
# that gets spliced into a URL path, and urllib transmits dot segments verbatim.
# A segment must start alphanumeric, which alone rules out "." and "..", and may
# hold nothing that can leave its path component - no "/", "%", "?" or "#". This
# is what keeps a corrupt metadata document from reaching /api/builds/<g>/<a>/<v>,
# the endpoint that silently triggers a build.
_SEGMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,99}")

# Keys the list endpoint carries on a NO-RECORD response. They are not versions:
# {"version": null, "status": "ok", "message": "Not found", "private": true} is
# a repo with zero builds, and reading it as a records map invents two - one of
# them green. `private`, `message` and `isTag` are never signals here either.
_NON_RECORD_KEYS = frozenset({
    "version", "status", "message", "private", "time", "commit", "ci",
    "buildUrl", "modules", "isTag",
})

# Log signatures worth a hint. Both have cost real time.
_HINTS = (
    ("/sdkman/candidates/java/21.0.2-open",
     "delete 'jitpack.yml'; jitpack installs OpenJDK 21.0.2 by itself"),
    ("Gradle 'publishToMavenLocal' task not found",
     "benign - jitpack injects maven-publish"),
)


def _git(repo_dir: Path | str, *args: str, timeout: float = _GIT_TIMEOUT) -> str | None:
    """Runs git in repo_dir and returns stripped stdout, or None if it failed.

    Borrows gradle.py's subprocess hygiene: a fresh console on Windows so no
    window flashes and the child cannot wedge on the inherited one, and
    stdin=DEVNULL because under the MCP server our stdin is the JSON-RPC pipe
    and a child must never read protocol bytes off it.

    "" and None are DIFFERENT answers and callers must keep them apart. "" is a
    command that ran and printed nothing - a repo with no origin remote, a
    commit on no remote branch. None is a command that never answered: a
    non-zero exit, a timeout on a large repo, a stale index.lock, git missing
    from PATH. Conflating them made a pushed sha report as unpushed, with a
    'git push' remedy naming the wrong branch.

    Args:
        repo_dir: directory to run in (git's -C argument).
        args: git arguments after -C.
        timeout: seconds before the call is abandoned.

    Returns:
        Stripped stdout, or None for a non-zero exit, a timeout, or a missing
        git - never "" for those.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_NO_CONSOLE,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _git_lines(repo_dir: Path | str, *args: str,
               timeout: float = _GIT_TIMEOUT) -> list[str] | None:
    """Runs git and returns its non-empty output lines, or None if it failed.

    [] means the command answered with nothing, None that it did not answer -
    the distinction _git draws, carried through so a caller cannot read a failed
    query as an empty result.
    """
    out = _git(repo_dir, *args, timeout=timeout)
    if out is None:
        return None
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _is_segment(value: str | None) -> bool:
    """Whether a version or branch is safe to splice into a URL path."""
    return bool(value) and _SEGMENT_RE.fullmatch(value) is not None


def _require_segment(value: str, kind: str) -> str:
    """Returns value, or raises before it can be spliced into a URL path.

    The structural guard behind the single highest-value rule in this design:
    no request may ever reach /api/builds/<group>/<artifact>/<version>. Callers
    validate first and turn a bad segment into a verdict, so this only fires if
    a future path forgets to - which must be a loud failure, not a traversal.
    """
    if not _is_segment(value):
        raise ValueError(f"{kind} '{value}' is not a valid url path segment")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirect handler that surfaces a 3xx as data instead of following it.

    Returning None from redirect_request makes urllib raise the redirect as an
    HTTPError, which _http catches - so a 302 stays observable. That matters
    because sha paths never redirect: a 302 means a symbolic ref reached the
    artifact GET, and a bare status-code check would read it as failure.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # None means "do not redirect", which leaves the 3xx to the default
        # error handler - i.e. it surfaces as an HTTPError _http turns into data.
        return None


def _http(url: str, timeout: float, follow: bool = False) -> dict:
    """Fetches url and returns the response as data; never raises.

    Args:
        url: absolute URL to GET.
        timeout: seconds before the request is abandoned.
        follow: whether redirects are followed. Off by default so a 302 is
            observable rather than silently resolved.

    Returns:
        dict with url, code (int or None), body, elapsed, location, timed_out,
        error. A timeout yields code None and timed_out True; a transport
        failure yields code None and error set - including one that breaks after
        the status line, because a response whose body never finished is not a
        verdict. Neither is an exception.
    """
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    code: int | None = None
    body = ""
    location = ""
    timed_out = False
    error: str | None = None

    start = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            code = response.status
            location = response.headers.get("Location", "") or ""
            body = response.read(_MAX_BODY).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # 4xx/5xx and (with follow=False) 3xx arrive here. They are verdicts,
        # not errors: 200 built, 401 repo-not-found, 404 failed-or-cached.
        # HTTPError IS the response, so it owns a socket until it is closed.
        code = exc.code
        if exc.headers is not None:
            location = exc.headers.get("Location", "") or ""
        try:
            body = exc.read(_MAX_BODY).decode("utf-8", "replace")
        except (OSError, http.client.HTTPException):
            body = ""
        finally:
            exc.close()
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            timed_out = True
        else:
            error = str(exc.reason)
    except (socket.timeout, TimeoutError):
        timed_out = True
    except http.client.HTTPException as exc:
        # IncompleteRead and BadStatusLine are not OSErrors and would otherwise
        # escape as a traceback. JitPack holds the .pom connection open chunked
        # for the whole build, so a mid-stream truncation is a live risk, and
        # nothing may raise across this module's boundary.
        error = f"{type(exc).__name__}: {exc}"
    except OSError as exc:
        error = str(exc)

    if timed_out or error is not None:
        # The status line may already have arrived when the stream broke, and a
        # 200 whose body never finished is not a verdict. One rule for every
        # caller: a failed request carries no code and no body.
        code, body = None, ""

    return {
        "url": url,
        "code": code,
        "body": body,
        "elapsed": round(time.monotonic() - start, 2),
        "location": location,
        "timed_out": timed_out,
        "error": error,
    }


def _walk_up_git(start: Path) -> Path | None:
    """Walks up from start to the nearest directory holding a .git entry.

    Mirrors modules.find_gradle_root. A module nested inside a repo inherits
    the parent's remote, and .git is a file rather than a directory in a
    worktree, so existence is the test.
    """
    current = start.resolve()
    while True:
        if (current / ".git").exists():
            return current
        if current == current.parent:
            return None
        current = current.parent


def _parse_remote(url: str) -> tuple[str, str] | None:
    """Splits a git remote URL into (org, repo), stripping any .git suffix.

    Handles both dialects in this workspace (https and scp-style ssh) and the
    inconsistent .git suffix - asset-renderer's remote has none. The repo
    segment is the JitPack artifact id and is NEVER the module or directory
    name: the minecraft-text module lives at github.com/minecraft-library/text.
    """
    match = _REMOTE_RE.match(url.strip())
    if not match:
        return None
    org, repo = match.group("org"), match.group("repo")
    return (org, repo) if org and repo else None


def _central_group(mod_dir: Path) -> str | None:
    """Returns the io.github.* group a module declares, or None.

    A module publishing to Maven Central has no JitPack builds at all, so
    asking JitPack about it produces a confusing empty answer rather than a
    useful one.
    """
    for name in ("build.gradle.kts", "build.gradle"):
        path = mod_dir / name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _GROUP_DECL_RE.search(text)
        if match:
            return match.group(1)
    return None


def _coordinate(module: str) -> dict:
    """Resolves a module token to its JitPack coordinate. No network.

    Args:
        module: module alias, name, or path (see toolsmith.modules).

    Returns:
        dict with module, dir, repo_dir, remote, org, artifact, repo, group
        (the dotted /api/ form) and group_path (the artifact-path form); or a
        dict carrying error when the module, its repo or its remote is missing.
    """
    mod_dir = resolve_module(module)
    if mod_dir is None:
        return {"module": module, "ok": False,
                "error": f"module '{module}' not resolved (run 'toolsmith setup'); "
                         f"root={workspace_root()}"}

    central = _central_group(mod_dir)
    if central:
        return {"module": module, "ok": False, "dir": str(mod_dir), "central": central,
                "error": f"module '{module}' publishes to maven central as '{central}', "
                         f"not jitpack"}

    repo_dir = _walk_up_git(mod_dir)
    if repo_dir is None:
        return {"module": module, "ok": False, "dir": str(mod_dir),
                "error": f"no git repo above '{mod_dir}'"}

    # Presence is asked of `git remote`, not of `config --get`. `config --get`
    # exits 1 when the key is unset, which is indistinguishable from git being
    # broken - reading the absent origin off it reported every remote-less repo
    # as a PATH problem and buried the remedy. `git remote` exits 0 and prints
    # nothing when a repo simply has none.
    remotes = _git(repo_dir, "remote")
    if remotes is None:
        return {"module": module, "ok": False, "dir": str(mod_dir),
                "repo_dir": str(repo_dir),
                "error": f"git failed in '{repo_dir}' - could not list remotes "
                         f"(is git on PATH?)"}
    if "origin" not in remotes.split():
        return {"module": module, "ok": False, "dir": str(mod_dir),
                "repo_dir": str(repo_dir),
                "error": f"module '{module}' has no git remote "
                         f"(run '{_GIT_REMEDY}')"}

    remote = _git(repo_dir, "config", "--get", "remote.origin.url")
    if not remote:
        # origin is listed but carries no url, or git stopped answering between
        # the two calls.
        return {"module": module, "ok": False, "dir": str(mod_dir),
                "repo_dir": str(repo_dir),
                "error": f"git failed in '{repo_dir}' - could not read "
                         f"remote.origin.url (is git on PATH?)"}

    parsed = _parse_remote(remote)
    if parsed is None:
        return {"module": module, "ok": False, "dir": str(mod_dir),
                "repo_dir": str(repo_dir), "remote": remote,
                "error": f"remote '{remote}' is not a recognisable github url"}

    org, artifact = parsed
    return {
        "module": module,
        "ok": True,
        "dir": str(mod_dir),
        "repo_dir": str(repo_dir),
        "remote": remote,
        "org": org,
        "artifact": artifact,
        "repo": f"{org}/{artifact}",
        "group": f"com.github.{org}",
        "group_path": f"com/github/{org}",
    }


def _resolve_ref(repo_dir: Path, ref: str | None = None, allow_symbolic: bool = False) -> dict:
    """Resolves and locally validates a ref. No network.

    JitPack answers byte-identically for a typo'd sha and a real unbuilt one,
    so validity is established here or not at all. Everything below is a local
    git query; nothing reaches the network, and an invalid ref never costs a
    build.

    Args:
        repo_dir: the git repo the ref belongs to.
        ref: git ref or short sha. None resolves origin/HEAD - never the local
            HEAD, which is off the default branch in several repos right now.
        allow_symbolic: permit a <branch>-SNAPSHOT ref instead of a sha.

    Returns:
        dict with ref (the 7-char sha), full, source, symbolic, pushed and
        unambiguous; or a dict carrying error for an unresolvable, ambiguous,
        unpushed or (unless allowed) symbolic ref. A git query that fails
        outright is reported as inconclusive (pushed None), never as a negative
        answer - "not pushed" is a claim, and a claim needs an answer to rest on.
    """
    if ref is None:
        # origin/HEAD, never HEAD: the pin must come from the pushed default
        # branch, and 3 of 16 repos sit on a feature branch at any given time.
        source = _git(repo_dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        source = source or "origin/master"
    else:
        source = ref

    if source.endswith("-SNAPSHOT"):
        if not allow_symbolic:
            return {"source": source, "symbolic": True,
                    "error": f"symbolic ref '{source}' - pin by sha, "
                             f"or pass --allow-symbolic"}
        branch = source[:-len("-SNAPSHOT")]
        if not _is_segment(branch):
            # The branch is spliced into a maven-metadata URL path.
            return {"source": source, "symbolic": True,
                    "error": f"branch '{branch}' is not a usable version segment "
                             f"(letters, digits and . _ + - only)"}
        return {"source": source, "symbolic": True, "branch": branch,
                "ref": None, "full": None, "pushed": True, "unambiguous": True}

    full = _git(repo_dir, "rev-parse", "--verify", f"{source}^{{commit}}") or ""
    if not re.fullmatch(r"[0-9a-f]{40}", full):
        return {"source": source, "symbolic": False,
                "error": f"ref '{source}' not resolvable in '{repo_dir}'"}

    # CONSTANT 7. Each distinct prefix length is a separate JitPack build, so a
    # caller-supplied prefix length would silently cost an extra one.
    sha = full[:_SHA_LEN]

    candidates = _git_lines(repo_dir, "rev-parse", f"--disambiguate={sha}")
    if candidates is not None and len(candidates) > 1:
        return {"source": source, "symbolic": False, "ref": sha, "full": full,
                "error": f"sha prefix '{sha}' is ambiguous in this repo "
                         f"({len(candidates)} objects)"}

    contains = _git_lines(repo_dir, "branch", "-r", "--contains", full)
    if contains is None:
        # Inconclusive, not negative: 'branch -r --contains' can exceed its
        # timeout on a large repo or lose to a stale index.lock. Claiming "not
        # pushed" here printed a 'git push' remedy for an already-pushed sha,
        # naming the local branch rather than the one holding the commit.
        return {"source": source, "symbolic": False, "ref": sha, "full": full, "pushed": None,
                "error": f"cannot tell whether '{sha}' is pushed - "
                         f"'git branch -r --contains' failed in '{repo_dir}'"}
    remote_branches = [ln for ln in contains if ln.startswith("origin/")]
    if not remote_branches:
        local = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD") or "<branch>"
        return {"source": source, "symbolic": False, "ref": sha, "full": full, "pushed": False,
                "error": f"ref '{sha}' is not pushed (run 'git push origin {local}')"}

    return {"source": source, "symbolic": False, "ref": sha, "full": full,
            "pushed": True, "unambiguous": True,
            "branches": [b for b in remote_branches if "->" not in b][:5]}


def _decode_records(body: str, group: str, artifact: str) -> dict:
    """Turns a list-endpoint body into a flat {version: status} mapping.

    Args:
        body: the response body.
        group: dotted group the records were asked for.
        artifact: repo name the records were asked for.

    Returns:
        The records, or {} for malformed JSON or a body that is not a records
        map. {} here means "answered with no records" - the caller has already
        established that it answered at all.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    envelope = data.get(group)
    if isinstance(envelope, dict):
        # The documented shape: {group: {artifact: {version: status}}}.
        inner = envelope.get(artifact)
        versions = inner if isinstance(inner, dict) else envelope
        return _records_from(versions)

    # No group envelope. A repo with zero builds answers with the metadata shape
    # {"version": null, "status": "ok", "message": "Not found", "private": true},
    # and reading that as records invented two builds, one of them green, and
    # reported an unresolvable pin as fine.
    return _records_from(data)


def _read_reason(response: dict) -> str:
    """Says why a list read produced no answer, in the caller's words."""
    if response["timed_out"]:
        return f"build list timed out after {response['elapsed']}s"
    if response["error"] is not None:
        return f"build list request failed: {response['error']}"
    return f"build list answered http {response['code']}"


def _read_records(group: str, artifact: str, timeout: float = _LIST_TIMEOUT,
                  attempts: int = _LIST_ATTEMPTS) -> dict:
    """Reads the versionless build list. The only /api/ call in this codebase.

    THE URL IS VERSIONLESS AND MUST STAY THAT WAY. Appending a version segment
    reaches /api/builds/<group>/<artifact>/<version>, which silently TRIGGERS A
    BUILD and answers from records that go stale for months. The list endpoint
    triggers nothing, answers in ~0.1 s, reports Building while a build is in
    flight, and is fresh within seconds of one finishing.

    Because it triggers nothing it is also the one request in this module that
    may be re-issued: elsewhere a retry is another real build. That matters at
    the scale `pins` works at - one call per artifact, back to back - where a
    throttled read used to decode as zero records and turn a healthy pin red.

    An answer carrying no records and no answer at all are kept apart, because
    only the first of them is evidence. A 404 is an ANSWER: it means jitpack has
    never seen the artifact.

    Args:
        group: dotted group, e.g. com.github.simplified-dev.
        artifact: repo name.
        timeout: seconds before one read is abandoned.
        attempts: how many times a read that did not answer is re-issued.

    Returns:
        dict with records ({version: status}), answered, http_code, error and
        attempts. answered False means the service did not answer - never that
        the artifact has no builds, and no caller may score it as one.
    """
    outcome: dict = {}
    for attempt in range(1, max(1, attempts) + 1):
        response = _http(f"{_BASE}/api/builds/{group}/{artifact}", timeout=timeout)
        code = response["code"]
        if code == 200 and response["body"]:
            return {"records": _decode_records(response["body"], group, artifact),
                    "answered": True, "http_code": code, "error": None, "attempts": attempt}
        if code is not None and code not in _LIST_RETRY_CODES:
            # An answer, just not one carrying records - a 404 for an artifact
            # jitpack has never seen, or a 200 with an empty body. Neither
            # improves on a re-read.
            return {"records": {}, "answered": True, "http_code": code, "error": None,
                    "attempts": attempt}
        outcome = {"records": {}, "answered": False, "http_code": code,
                   "error": _read_reason(response), "attempts": attempt}
        if attempt < attempts:
            time.sleep(_LIST_BACKOFF * attempt)
    return outcome


def _list_records(group: str, artifact: str, timeout: float = _LIST_TIMEOUT) -> dict:
    """The records alone, for the one caller that cannot act on the difference.

    Only the cosmetic watchdog reads this: it prints a progress line, where a
    poll that failed is indistinguishable from a build that has not started.
    Any caller whose VERDICT rests on the answer must use _read_records instead,
    since {} here still conflates "no records" with "no answer".
    """
    return _read_records(group, artifact, timeout=timeout, attempts=1)["records"]


def _records_from(data: dict) -> dict:
    """Keeps only the entries of a decoded body that really are {version: status}.

    Screens per entry rather than accepting or rejecting the whole object. Both
    decode paths run through here: position inside the group envelope is not
    enough on its own, because a repo with zero builds carries the same metadata
    keys there as it does bare, and dropping only non-string values left
    {"status": "ok", "message": "Not found"} behind - two invented builds, one of
    them green.

    Per entry rather than all-or-nothing because one unrecognised version key
    should cost that key, not the whole map: failing closed on a single odd tag
    would report a repo with real builds as having none.

    Args:
        data: the decoded object to screen.

    Returns:
        The subset whose keys look like versions and whose values are plain
        status strings, with the known metadata keys refused outright - "status"
        and "message" are legal version charsets and would otherwise slip through.
    """
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str)
        and key not in _NON_RECORD_KEYS and _is_segment(key)
    }


def _classify(status: str | None) -> str:
    """Normalises a list-endpoint status into absent/ok/error/in-flight/unknown.

    Casing is inconsistent on this endpoint (`ok` lowercase, `Error` and
    `Building` capitalised), and `gitError` is a failure too - so the web UI's
    own rule applies: any status containing "error" is red. This is the one
    normalisation point.
    """
    if status is None:
        return "absent"
    lowered = status.strip().lower()
    if lowered == "ok":
        return "ok"
    if "error" in lowered:
        return "error"
    if lowered in ("building", "queued"):
        return "in-flight"
    return "unknown"


def _summarise(records: dict) -> dict:
    """Counts the records by classification."""
    counts = {"ok": 0, "error": 0, "in-flight": 0, "unknown": 0}
    for status in records.values():
        counts[_classify(status)] += 1
    return {"total": len(records), **counts}


def _hints(log_text: str) -> list[str]:
    """Matches known landmines in a build log tail."""
    return [hint for signature, hint in _HINTS if signature in log_text]


def _pom_url(coord: dict, version: str) -> str:
    """The artifact URL whose blocking GET both triggers and waits on a build.

    The version is validated before it is spliced in: it can come from a
    maven-metadata document, and urllib sends dot segments unnormalised, so
    "../../api/builds/..." would otherwise reach the one endpoint no code path
    here may ever request.
    """
    artifact = coord["artifact"]
    _require_segment(version, "version")
    return f"{_BASE}/{coord['group_path']}/{artifact}/{version}/{artifact}-{version}.pom"


def _fetch_log(coord: dict, version: str, log_lines: int) -> dict:
    """Fetches build.log after a terminal non-200 verdict. Never a probe.

    build.log triggers a build for an unbuilt sha and blocks and streams for an
    in-flight one, so it is only ever read once the artifact GET has already
    returned a terminal non-200.

    Args:
        coord: a resolved coordinate (see _coordinate).
        version: the sha or concrete version the build ran for.
        log_lines: how many trailing log lines to keep.

    Returns:
        dict with log_url, log_code, log_tail, log_note and hints. An absent or
        empty log yields a note and an empty tail - never a success signal.
    """
    url = (f"{_BASE}/{coord['group_path']}/{coord['artifact']}"
           f"/{_require_segment(version, 'version')}/build.log")
    response = _http(url, timeout=_LOG_TIMEOUT, follow=True)
    body = response["body"]
    if response["code"] == 200 and body.strip():
        tail = "\n".join(body.splitlines()[-log_lines:])[-_LOG_TAIL_CHARS:]
        return {"log_url": url, "log_code": 200, "log_tail": tail,
                "log_note": None, "hints": _hints(body)}
    return {"log_url": url, "log_code": response["code"], "log_tail": "",
            "log_note": _NO_LOG_NOTE, "hints": []}


def _resolve_symbolic(coord: dict, branch: str, timeout: float) -> dict:
    """Resolves a <branch>-SNAPSHOT to a concrete version via maven-metadata.

    Deliberately not through /api/builds/<group>/<artifact>/<branch>-SNAPSHOT,
    which answers a frozen tagNotFound for months on repos whose default branch
    JitPack never registered. maven-metadata.xml is redirect-free and truthful.

    Args:
        coord: a resolved coordinate.
        branch: branch name without the -SNAPSHOT suffix.
        timeout: seconds before the read is abandoned.

    Returns:
        dict with version and metadata_url, or a dict carrying error.
    """
    url = (f"{_BASE}/{coord['group_path']}/{coord['artifact']}"
           f"/{_require_segment(branch, 'branch')}-SNAPSHOT/maven-metadata.xml")
    response = _http(url, timeout=timeout, follow=True)
    if response["code"] != 200 or not response["body"].strip():
        return {"metadata_url": url,
                "error": f"no maven-metadata for '{branch}-SNAPSHOT' "
                         f"(http {response['code']})"}
    try:
        root = ET.fromstring(response["body"])
    except ET.ParseError:
        return {"metadata_url": url,
                "error": f"malformed maven-metadata for '{branch}-SNAPSHOT'"}

    for path in ("versioning/release", "versioning/latest", "version"):
        node = root.find(path)
        if node is not None and (node.text or "").strip():
            return {"metadata_url": url, "version": node.text.strip()}
    versions = [n.text.strip() for n in root.iter("version") if (n.text or "").strip()]
    if versions:
        return {"metadata_url": url, "version": versions[-1]}
    return {"metadata_url": url,
            "error": f"maven-metadata for '{branch}-SNAPSHOT' names no version"}


def _watchdog(coord: dict, sha: str, progress: Callable[[str], None],
              stop: threading.Event, started: float) -> None:
    """Cosmetic progress poller against the list endpoint. Never the gate.

    The blocking artifact GET is the wait; this only exists so a human watching
    a terminal sees something. It polls the free list endpoint at a 12 s cadence
    (not the 5 s the docs suggest, since nothing depends on it) and a failure to
    poll is never fatal.
    """
    while not stop.wait(_WATCH_INTERVAL):
        try:
            records = _list_records(coord["group"], coord["artifact"], timeout=_WATCH_TIMEOUT)
            progress(f"  {sha}: {records.get(sha, '...')} ({time.monotonic() - started:.0f}s)")
        except Exception:  # a cosmetic poller must never break the wait it decorates
            return


def _public(coord: dict) -> dict:
    """The coordinate fields every public return carries."""
    return {key: coord[key] for key in _COORD_KEYS if key in coord}


def exit_code(result: dict) -> int:
    """Maps any result from this module onto the toolsmith 0/1/2 convention.

    Lives here rather than in the CLI because the mapping is not the usual
    "error means 2": a failed build and a cached failure both carry an error
    message yet are ordinary red verdicts. What earns a 2 is that nothing was
    requested at all.

    Args:
        result: a dict returned by jitpack_status, jitpack_build or jitpack_pins.

    Returns:
        0 when ok, 1 for a red verdict (build failed, pin unbuilt, timeout),
        2 for a precondition failure (module unresolved, no remote, ref not
        resolvable/pushed/unambiguous, symbolic ref, no inventory).
    """
    status = result.get("status")
    if status in _PRECONDITION_STATUSES:
        return 2
    if status is None and result.get("error"):
        return 2
    return 0 if result.get("ok") else 1


def jitpack_status(module: str, refs: Sequence[str] = (), timeout: float = _LIST_TIMEOUT) -> dict:
    """Reports whether refs are built, without ever triggering a build.

    Reads exactly one URL - the versionless build list - and answers "is this
    sha built, and is it safe to pin". Run this before jitpack_build: an
    already-ok sha costs no build there, only the cached artifact read that
    confirms the record. This report is the list's own view and stays that way;
    it is jitpack_build's verdict that has to be backed by an artifact.

    Args:
        module: module alias, name, or path.
        refs: git refs or short shas to report on. Empty asks about
            origin/HEAD, which is the only ref worth pinning.
        timeout: seconds before the list read is abandoned.

    Returns:
        dict with module, group, artifact, org, repo, repo_dir, records (the
        record count), counts (total/ok/error/in-flight/unknown), list_ok and
        refs - one entry per requested ref carrying ref, full, source, pushed,
        status, state and ok. ok is True only when every requested ref is built.

        A list read that did not answer sets list_ok False and leaves every ref
        in state "unreachable" - inconclusive, and deliberately never "absent",
        which would read as a ref that needs building.

        A precondition failure (module unresolved, no remote, ref not
        resolvable, unpushed, ambiguous, symbolic) sets a top-level error and
        is exit-code-2 territory for the caller.
    """
    coord = _coordinate(module)
    if not coord.get("ok"):
        return {**coord, "refs": []}

    repo_dir = Path(coord["repo_dir"])
    resolutions = [_resolve_ref(repo_dir, ref) for ref in (list(refs) or [None])]
    # Local validation comes first and it is a gate, not a filter: with nothing
    # answerable left there is no question to ask jitpack, and build already
    # returns here rather than requesting anything.
    answerable = any(not resolution.get("error") for resolution in resolutions)
    read = (_read_records(coord["group"], coord["artifact"], timeout=timeout) if answerable
            else {"records": {}, "answered": True, "error": None})
    records = read["records"]

    entries: list[dict] = []
    for resolution in resolutions:
        if resolution.get("error"):
            entries.append({"ref": resolution.get("ref"), "source": resolution.get("source"),
                            "ok": False, "state": "unresolved",
                            "error": resolution["error"]})
            continue
        status = records.get(resolution["ref"])
        state = _classify(status) if read["answered"] else "unreachable"
        entries.append({
            "ref": resolution["ref"],
            "full": resolution["full"],
            "source": resolution["source"],
            "pushed": resolution["pushed"],
            "unambiguous": resolution.get("unambiguous", True),
            "status": status,
            "state": state,
            "ok": state == "ok",
        })

    failed = next((entry["error"] for entry in entries if entry.get("error")), None)
    result = {
        **_public(coord),
        "ok": all(entry["ok"] for entry in entries),
        "records": len(records),
        "counts": _summarise(records),
        "list_ok": read["answered"],
        "refs": entries,
    }
    if not read["answered"]:
        # A note rather than an error: the caller invoked this correctly and the
        # service did not answer, which is exit code 1's territory, not 2's.
        result["note"] = read["error"]
    if failed:
        result["error"] = failed
    return result


def _transport_verdict(info: dict, response: dict, started: float) -> dict | None:
    """Turns a timeout or transport failure into a verdict, or None to continue."""
    if response["timed_out"]:
        # Inconclusive, not red: the build is still running server-side.
        return {**info, "ok": False, "status": "timeout", "resume": True, "http_code": None,
                "elapsed": round(time.monotonic() - started, 2), "log_tail": "", "hints": [],
                "note": _TIMEOUT_NOTE}
    if response["error"] is not None:
        return {**info, "ok": False, "status": "error", "http_code": None,
                "elapsed": response["elapsed"], "log_tail": "", "hints": [],
                "error": f"request to jitpack failed: {response['error']}"}
    return None


def jitpack_build(module: str, ref: str | None = None, timeout: float = _BUILD_TIMEOUT,
                  force: bool = False, allow_symbolic: bool = False, log_lines: int = 60,
                  progress: Callable[[str], None] | None = None) -> dict:
    """Prechecks, then triggers and waits for one JitPack build.

    The ref is resolved and validated entirely from local git before anything
    is requested, then one list read prechecks the sha, then ONE blocking GET of
    the .pom triggers the build and waits for it. That single request is the
    whole waiter: there is no poll loop and no retry loop, because every request
    is a real build on a third-party service.

    The precheck short-circuits the WAITING, never the verdict. A record saying
    "ok" is confirmed against the .pom before this returns green, because the
    list endpoint reports ok for artifacts that answer 404 (jitpack issue
    #7711) and a pin nobody can resolve is the expensive failure this exists to
    prevent. For an already-built sha that confirmation is a cache hit.

    Args:
        module: module alias, name, or path.
        ref: git ref or short sha. None resolves origin/HEAD, never local HEAD.
        timeout: seconds to hold the blocking artifact request. Pass
            _MCP_BUILD_TIMEOUT (480) from a harness-capped caller. A
            non-positive value prechecks only and never requests anything.
        force: re-request a sha the precheck already reported. Note that it
            cannot change a cached failure - only a new commit does.
        allow_symbolic: permit a <branch>-SNAPSHOT ref, resolved to a concrete
            version through maven-metadata.xml.
        log_lines: trailing build.log lines to return on a failure.
        progress: optional callback receiving a cosmetic progress line every
            12 s while the request blocks. None disables the watchdog thread.

    Returns:
        dict with module, group, artifact, org, repo, repo_dir, ref (the
        7-char sha, or the concrete version for a symbolic ref), full_sha,
        source, symbolic, precheck, action, url, http_code, elapsed, log_tail,
        hints, ok and status, where status is one of:

          "built"          - 200 from the artifact request; ok
          "already-built"  - the precheck said ok AND the .pom confirmed it
                             with a 200; ok
          "cached-failure" - refused, or a 404 no build produced. NEW COMMIT
          "failed"         - the build ran and failed, or 401, or the record
                             said ok while the artifact 404s. log_tail is real
          "timeout"        - INCONCLUSIVE. resume is True; re-running attaches
                             to the same build rather than starting a second
          "in-flight"      - a build is already running and no wait was asked
          "symbolic"       - a redirect reached the artifact request
          "precondition"   - nothing was requested; error says why
          "error"          - the request itself failed (DNS, TLS, outage)

        A success also carries pin (the ready-to-paste strictly line) and note
        (jitpack builds with -xtest, so a green build is a compile check only).
        "precondition" and "symbolic" are exit-code-2 territory for the caller;
        every other non-ok status is exit code 1.
    """
    coord = _coordinate(module)
    if not coord.get("ok"):
        return {**coord, "status": "precondition", "hints": []}

    # ---- B. Ref resolution and out-of-band validation. Still no network. ----
    repo_dir = Path(coord["repo_dir"])
    resolution = _resolve_ref(repo_dir, ref, allow_symbolic=allow_symbolic)
    if resolution.get("error"):
        return {**_public(coord), "ok": False, "status": "precondition", "hints": [],
                "ref": resolution.get("ref"), "source": resolution.get("source"),
                "error": resolution["error"]}

    symbolic = bool(resolution.get("symbolic"))
    source = resolution["source"]
    if symbolic:
        metadata = _resolve_symbolic(coord, resolution["branch"], _LIST_TIMEOUT)
        if metadata.get("error"):
            return {**_public(coord), "ok": False, "status": "precondition", "hints": [],
                    "source": source, "error": metadata["error"]}
        version = metadata["version"]
    else:
        version = resolution["ref"]

    if not _is_segment(version):
        # Server-controlled text that is about to become a URL path segment.
        return {**_public(coord), "ok": False, "status": "precondition", "hints": [],
                "source": source, "ref": version,
                "error": f"version '{version}' is not a usable url path segment"}

    # ---- C. Precheck. ONE list read; it never triggers a build. ----
    # A read that did not answer leaves the precheck "unknown" rather than
    # "absent". Both still reach the trigger below - the artifact GET is the
    # verdict either way - but only one of them is a claim about the sha.
    read = _read_records(coord["group"], coord["artifact"], timeout=_LIST_TIMEOUT)
    records = read["records"]
    precheck = _classify(records.get(version)) if read["answered"] else "unknown"
    info = {**_public(coord), "ref": version, "full_sha": resolution.get("full"),
            "source": source, "symbolic": symbolic, "precheck": precheck}
    if not read["answered"]:
        info["precheck_error"] = read["error"]
    pin = f'{coord["group"]}:{coord["artifact"]} -> version {{ strictly("{version}") }}'

    if precheck == "error" and not force:
        # Requesting the artifact here would change nothing: jitpack never
        # rebuilds a sha it has already failed.
        return {**info, "ok": False, "status": "cached-failure", "action": "refuse",
                "http_code": None, "elapsed": 0.0, "error": _CACHED_FAILURE_NOTE,
                **_fetch_log(coord, version, log_lines)}

    if timeout <= 0:
        # Precheck-only mode: report what the list said and request nothing.
        # Not even an "ok" record graduates to a green verdict here, because the
        # artifact GET that would back it is exactly what was opted out of.
        if precheck == "in-flight":
            return {**info, "ok": False, "status": "in-flight", "action": "none",
                    "http_code": None, "elapsed": 0.0, "log_tail": "", "hints": [],
                    "note": "a build is already in flight - re-run with a timeout to wait"}
        return {**info, "ok": False, "status": "precondition", "action": "none",
                "http_code": None, "elapsed": 0.0, "log_tail": "", "hints": [],
                "error": f"timeout={timeout:g} requests nothing - precheck says "
                         f"'{precheck}', pass a positive timeout to request it"}

    if precheck == "ok" and not force:
        # The record is a hint, not the verdict: issue #7711 has the list
        # reporting ok while the artifact tree 404s, and that is the pin that
        # costs a downstream resolve failure. The .pom is already cached, so
        # this confirms in ~0.2 s and never waits out a build budget.
        action = "verify"
    elif precheck == "in-flight":
        action = "attach"
    else:
        action = "trigger"
    info["action"] = action
    # Every action gets the caller's whole budget. Confirming a record that
    # already says "ok" is normally a cache hit answering in milliseconds, and a
    # 404 answers just as fast, so a shorter budget bought nothing - but when the
    # artifact really is missing (jitpack issue #7711) that confirmation starts a
    # genuine build, and a 60s cap timed out a build measured at 74.2s.
    budget = timeout

    # ---- D. Trigger AND wait. ONE blocking artifact GET. ----
    url = _pom_url(coord, version)
    info["url"] = url
    stop = threading.Event()
    watcher: threading.Thread | None = None
    started = time.monotonic()
    if progress is not None:
        watcher = threading.Thread(target=_watchdog, daemon=True,
                                   args=(coord, version, progress, stop, started))
        watcher.start()
    try:
        response = _http(url, timeout=budget, follow=False)
    finally:
        stop.set()
        if watcher is not None:
            watcher.join(timeout=_WATCH_TIMEOUT)

    # ---- E. Verdict. ----
    verdict = _transport_verdict(info, response, started)
    if verdict is not None:
        return verdict
    code, elapsed = response["code"], response["elapsed"]

    if code in _REDIRECTS:
        location = response["location"]
        if not allow_symbolic:
            return {**info, "ok": False, "status": "symbolic", "http_code": code,
                    "elapsed": elapsed, "location": location, "log_tail": "", "hints": [],
                    "error": f"'{version}' redirected to a symbolic version - "
                             f"pin by sha, or pass --allow-symbolic"}
        parts = location.rstrip("/").split("/")
        info["resolved_version"] = parts[-2] if len(parts) >= 2 else location
        info["location"] = location
        # The re-issue shares the caller's budget rather than starting a second
        # one: --timeout is what the caller agreed to wait in total, and this is
        # another real build request, not a retry that costs nothing.
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
            return {**info, "ok": False, "status": "timeout", "resume": True,
                    "http_code": code, "elapsed": elapsed, "log_tail": "", "hints": [],
                    "note": _TIMEOUT_NOTE}
        response = _http(url, timeout=remaining, follow=True)
        verdict = _transport_verdict(info, response, started)
        if verdict is not None:
            return verdict
        code = response["code"]
        elapsed = round(elapsed + response["elapsed"], 2)

    if code == 200:
        # A verified record is reported as what it is - already built, not built
        # by this call - but either way the green verdict rests on this 200.
        return {**info, "ok": True, "http_code": 200, "elapsed": elapsed,
                "status": "already-built" if action == "verify" else "built",
                "bytes": len(response["body"]), "log_tail": "", "pin": pin,
                "note": _XTEST_NOTE, "hints": [_COMPOSITE_NOTE]}

    if code == 401:
        # Never sourced from the API's `private` field, which is a zero-value.
        return {**info, "ok": False, "status": "failed", "http_code": 401, "elapsed": elapsed,
                "log_tail": "", "hints": [],
                "error": f"repo '{coord['repo']}' is not resolvable by jitpack, or its "
                         f"1-hour visibility cache has not expired since it went public"}

    if code == 404:
        body = response["body"].strip()
        if action == "verify":
            # The list said ok and the tree serves nothing: jitpack issue #7711.
            # No build ran here, so there is no fresh log to dump and the sha is
            # simply not servable.
            return {**info, "ok": False, "status": "failed", "http_code": 404,
                    "elapsed": elapsed, "http_body": body[:200], "discriminator": "precheck",
                    "log_tail": "", "hints": [], "error": _MISMATCH_NOTE}
        # The BODY is the discriminator: a build that ran and lost answers with
        # the 39-byte 'Build failed. See the log at jitpack.io', and a coordinate
        # jitpack never served answers with nothing at all. Latency only breaks
        # a tie on a body that is neither, because a cold TLS handshake alone can
        # make a cached negative look like a build that just failed.
        if _BUILD_FAILED_BODY in body:
            classification, discriminator = "failed", "body"
        elif not body:
            classification, discriminator = "cached-failure", "body"
        else:
            classification = "cached-failure" if elapsed < _FAST_404 else "failed"
            discriminator = "latency"
        return {**info, "ok": False, "status": classification, "http_code": 404,
                "elapsed": elapsed, "http_body": body[:200], "discriminator": discriminator,
                "error": _CACHED_FAILURE_NOTE if classification == "cached-failure"
                         else f"build failed for '{version}'",
                **_fetch_log(coord, version, log_lines)}

    return {**info, "ok": False, "status": "failed", "http_code": code, "elapsed": elapsed,
            "log_tail": "", "hints": [], "http_body": response["body"].strip()[:200],
            "error": f"unexpected http {code} from '{url}'"}


def _scan_pins(artifact: str | None = None) -> list[dict]:
    """Scans the workspace build files for com.github/io.github pins.

    Both dialects in use are recognised: the library form
    ``api("com.github.<org>:<artifact>") { version { strictly("<sha>") } }`` and
    the app form ``api("com.github.<org>:<artifact>:master-SNAPSHOT")``. Neither
    is rewritten - picking a side is a human decision about the ecosystem.

    Args:
        artifact: case-insensitive substring filter on the artifact id.

    Returns:
        One dict per occurrence with group, org, artifact, pin, form, central,
        file and line.
    """
    root = workspace_root()
    if root is None:
        return []

    occurrences: list[dict] = []
    seen: set[str] = set()
    for directory in [root, *(root / m["path"] for m in get_modules())]:
        for name in ("build.gradle.kts", "build.gradle"):
            path = directory / name
            if str(path) in seen or not path.is_file():
                continue
            seen.add(str(path))
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            try:
                label = path.relative_to(root).as_posix()
            except ValueError:
                label = path.as_posix()

            for index, line in enumerate(lines):
                match = _COORD_RE.search(line)
                if match is None:
                    continue
                found = match.group("artifact")
                if artifact and artifact.lower() not in found.lower():
                    continue
                version = match.group("version")
                if version:
                    pin = version
                    form = "snapshot" if version.endswith("-SNAPSHOT") else "version"
                else:
                    # The strictly block usually sits on the same line, but a
                    # wrapped declaration puts it on the next few.
                    window = "\n".join(lines[index:index + 4])
                    strict = _STRICTLY_RE.search(window)
                    pin, form = (strict.group(1), "strictly") if strict else ("", "unpinned")
                group = match.group("group")
                occurrences.append({
                    "group": group,
                    "org": group.split(".", 2)[-1],
                    "artifact": found,
                    "pin": pin,
                    "form": form,
                    "central": group.startswith("io.github."),
                    "file": label,
                    "line": index + 1,
                })
    return occurrences


def _repo_dirs_by_artifact(wanted: Iterable[str]) -> dict[str, Path]:
    """Maps each wanted artifact id to the module repo that publishes it.

    Keyed by the remote's repo segment, not the module name - the two differ
    (the minecraft-text module publishes github.com/minecraft-library/text).
    """
    root = workspace_root()
    if root is None:
        return {}
    remaining = set(wanted)
    mapping: dict[str, Path] = {}
    seen: set[str] = set()
    for module in get_modules():
        if not remaining:
            break
        repo_dir = _walk_up_git(root / module["path"])
        if repo_dir is None or str(repo_dir) in seen:
            continue
        seen.add(str(repo_dir))
        parsed = _parse_remote(_git(repo_dir, "config", "--get", "remote.origin.url") or "")
        if parsed is None:
            continue
        name = parsed[1]
        if name in remaining:
            mapping[name] = repo_dir
            remaining.discard(name)
    return mapping


def _behind(repo_dir: Path, pin: str) -> int | None:
    """Counts commits between a pinned sha and the origin default branch.

    Returns None when the count cannot be established - most often because the
    pinned sha was never fetched into this clone, which is not the same as
    being up to date.
    """
    base = _git(repo_dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD") or "origin/master"
    count = _git(repo_dir, "rev-list", "--count", f"{pin}..{base}") or ""
    return int(count) if count.isdigit() else None


def jitpack_pins(artifact: str | None = None, max_behind: int | None = None,
                 timeout: float = _LIST_TIMEOUT) -> dict:
    """Audits every workspace pin for drift, one list call per artifact.

    Read only, and deliberately structured to stay that way: the natural
    hand-rolled shape loops the pins through the per-version API endpoint,
    which would trigger one build per pin. This makes ONE versionless list call
    per distinct artifact instead, and never rewrites a coordinate.

    Args:
        artifact: case-insensitive substring filter on the artifact id.
        max_behind: opt-in gate. When set, a pin more than this many commits
            behind its default branch flips ok to False. Off by default,
            because most pins are stale and a default-red gate is noise.
        timeout: seconds before a list read is abandoned.

    Returns:
        dict with root, pins, artifacts, total (distinct rows), occurrences
        (declaration sites, which is the larger number), stale, unbuilt,
        errors, unreachable, unpinned, conflicts, conflicting, median_behind,
        list_calls and ok. Each pin row carries group, org, artifact, pin, form
        (strictly/snapshot/version/unpinned), central, jitpack (the raw status
        label), state, behind (None when it cannot be counted), conflict (how
        many distinct pins that artifact carries workspace-wide), consumers and
        consumer_paths.

        state is one of ok/error/in-flight/unknown/absent for a pin jitpack
        answered about, plus three that are not verdicts on a build at all:
        "central" (Maven Central publishes it), "unpinned" (the coordinate
        names no version, so there is nothing to ask about) and "unreachable"
        (the list read did not answer).

        ok is False when any gated pin is absent from jitpack, in error or
        unreachable, or when max_behind is exceeded. Staleness alone never
        flips it, and neither central nor unpinned rows are gated at all.
        A missing workspace root sets error and is exit-code-2 territory.
    """
    root = workspace_root()
    if root is None:
        return {"ok": False, "pins": [], "total": 0,
                "error": "no inventory - run 'toolsmith setup' first"}

    occurrences = _scan_pins(artifact)
    grouped: dict[tuple[str, str, str], dict] = {}
    for occurrence in occurrences:
        key = (occurrence["group"], occurrence["artifact"], occurrence["pin"])
        row = grouped.setdefault(key, {
            "group": occurrence["group"], "org": occurrence["org"],
            "artifact": occurrence["artifact"], "pin": occurrence["pin"],
            "form": occurrence["form"], "central": occurrence["central"],
            "consumers": 0, "consumer_paths": [],
        })
        row["consumers"] += 1
        if len(row["consumer_paths"]) < 10:
            row["consumer_paths"].append(f"{occurrence['file']}:{occurrence['line']}")

    rows = sorted(grouped.values(), key=lambda r: (r["artifact"], r["pin"]))

    # Every distinct version declared for an artifact, so a row can say whether
    # it is one of several. Central rows count too: two versions of one artifact
    # is the same resolution problem wherever it is published from.
    declared: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        if row["pin"]:
            declared.setdefault((row["group"], row["artifact"]), set()).add(row["pin"])

    # ONE list call per distinct artifact - never one per pin, and never the
    # per-version endpoint that would build each of them. An artifact whose rows
    # are all central or unpinned names no version to ask about, so it costs no
    # call either.
    reads: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["group"], row["artifact"])
        if row["central"] or row["form"] == "unpinned" or key in reads:
            continue
        reads[key] = _read_records(row["group"], row["artifact"], timeout=timeout)

    repo_dirs = _repo_dirs_by_artifact(
        {r["artifact"] for r in rows if r["form"] in ("strictly", "version") and r["pin"]})

    for row in rows:
        key = (row["group"], row["artifact"])
        row["conflict"] = len(declared.get(key, ()))
        repo_dir = repo_dirs.get(row["artifact"])
        row["behind"] = _behind(repo_dir, row["pin"]) if repo_dir and row["pin"] else None
        if row["central"]:
            # Maven Central publishes it; jitpack has no records at all and the
            # absence is not a fault.
            row["jitpack"], row["state"], row["behind"] = "n/a (central)", "central", None
            continue
        if row["form"] == "unpinned":
            # The coordinate names no version, so there is no build to ask
            # about. Scoring this "absent" counted a missing PIN as a missing
            # BUILD, and put a coordinate that resolves fine in the unbuilt
            # column.
            row["jitpack"], row["state"], row["behind"] = "n/a (unpinned)", "unpinned", None
            continue
        read = reads.get(key) or {}
        if not read.get("answered"):
            # The service did not answer, so nothing is known about this pin.
            # Deliberately not "absent": that is a claim the read cannot support,
            # and making it is what reported healthy pins as unbuilt.
            row["jitpack"], row["state"] = "unreachable", "unreachable"
            continue
        artifact_records = read["records"]
        if row["form"] == "snapshot":
            # A SNAPSHOT pin names no concrete version, so the artifact's own
            # health is the only answerable question and drift is unmeasurable.
            summary = _summarise(artifact_records)
            row["state"] = "ok" if summary["ok"] else ("error" if summary["error"] else "absent")
            row["jitpack"] = row["state"]
            row["behind"] = None
            continue
        status = artifact_records.get(row["pin"])
        row["state"] = _classify(status)
        row["jitpack"] = status or "absent"

    # Central and unpinned rows are reported but never gated - neither is a
    # claim about a jitpack build, so neither can be red.
    gated = [row for row in rows if not row["central"] and row["form"] != "unpinned"]
    behinds = sorted(row["behind"] for row in rows if row["behind"] is not None)
    over = [row for row in gated
            if max_behind is not None and row["behind"] is not None and row["behind"] > max_behind]
    conflicting = sorted({artifact for (_group, artifact), pins in declared.items()
                          if len(pins) > 1})
    result = {
        "ok": all(row["state"] == "ok" for row in gated) and not over,
        "root": str(root),
        "pins": rows,
        # total counts distinct (artifact, pin) ROWS - one per table line -
        # while occurrences counts the declaration sites they were folded from.
        # The two differ by a factor of ~1.5 here, so neither may be labelled
        # with a bare "pins".
        "total": len(rows),
        "occurrences": len(occurrences),
        "artifacts": len({(row["group"], row["artifact"]) for row in rows}),
        "stale": sum(1 for row in rows if row["behind"]),
        "unbuilt": sum(1 for row in gated if row["state"] == "absent"),
        "errors": sum(1 for row in gated if row["state"] == "error"),
        "unreachable": sum(1 for row in gated if row["state"] == "unreachable"),
        "unpinned": sum(1 for row in rows if row["form"] == "unpinned"),
        "conflicts": len(conflicting),
        "conflicting": conflicting,
        "median_behind": behinds[len(behinds) // 2] if behinds else None,
        "over_max_behind": len(over),
        "max_behind": max_behind,
        "list_calls": len(reads),
    }
    if result["unreachable"]:
        result["note"] = _UNREACHABLE_NOTE
    if not rows:
        result["note"] = (f"no jitpack pins found under {root}"
                          + (f" matching '{artifact}'" if artifact else ""))
    return result
