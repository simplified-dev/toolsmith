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

# The one thing a cascade may not claim. Published gradle module metadata for
# these artifacts records {"requires": "<sha>"} - NOT strictly - so an inherited
# pin is a soft constraint and a consumer's own strictly() overrides it. Nothing
# forces the far end of a chain to move; converging on one sha per artifact is
# this workspace's convention, and `order` says so rather than implying gradle
# demands it.
_SOFT_PIN_NOTE = ("an inherited pin is published as 'requires', not 'strictly', so a consumer's "
                  "own strictly() overrides it - a full cascade is this workspace's "
                  "one-sha-per-artifact convention, not a resolution requirement")
_CASCADE_NOTE = ("each step lands downstream only once that module is committed, built on "
                 "jitpack and re-pinned by its own consumers")
_SNAPSHOT_SKIP_NOTE = ("a -SNAPSHOT coordinate floats onto the new commit by itself, so it is "
                       "left alone; pass --include-snapshots to nail it to this sha instead")
_UNVERIFIED_NOTE = ("nothing was written - jitpack could not be asked whether the sha is built; "
                    "re-run, or pass --no-verify to pin it anyway")
_STALE_SCAN_NOTE = "the file changed between the scan and the write - re-run"

# Forms whose pin text can be rewritten in place. "unpinned" is deliberately
# absent: a coordinate naming no version has no pin text to replace, and
# inventing one would change the declaration's shape rather than its sha.
_REWRITABLE_FORMS = frozenset({"strictly", "version", "snapshot"})

# A hex sha is cut to the same CONSTANT 7 every other path here uses. Each
# distinct prefix length is a SEPARATE jitpack build, so pinning 8 or 40 chars
# asks for an artifact nobody produced - and _resolve_ref reports on the 7-char
# form regardless, so an unnormalised pin would be verified at a length it was
# never going to be written at.
_ABBREVIABLE_SHA_RE = re.compile(r"[0-9a-fA-F]{7,40}")

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
    the app form ``api("com.github.<org>:<artifact>:master-SNAPSHOT")``. Which
    one a site uses is a human decision about the ecosystem, so jitpack_set
    edits the pin inside whichever form it finds and never converts between them.

    Each occurrence carries the exact character span of its pin text, which is
    what makes a rewrite an offset splice rather than a regex substitution: the
    artifact id never reaches a pattern, so an id holding a regex metacharacter
    (``gson-extras``, and ``.`` is legal here too) cannot alter what matches, and
    a declaration this function did not find cannot be silently edited by one.

    Args:
        artifact: case-insensitive substring filter on the artifact id.

    Returns:
        One dict per occurrence with group, org, artifact, pin, form, central,
        file, line, module (the discovered module the file belongs to, None for
        the workspace root), path (the absolute file path) and span (the
        character offsets of the pin text within that file, None when the
        coordinate names no version at all).
    """
    root = workspace_root()
    if root is None:
        return []

    occurrences: list[dict] = []
    seen: set[str] = set()
    sources = [(None, root), *((m["name"], root / m["path"]) for m in get_modules())]
    for module, directory in sources:
        for name in ("build.gradle.kts", "build.gradle"):
            path = directory / name
            if str(path) in seen or not path.is_file():
                continue
            seen.add(str(path))
            try:
                # newline="" and keepends=True together are what make an offset
                # here mean the same thing on disk. Universal-newline decoding
                # collapses \r\n to \n, so every span in a CRLF file would point
                # one byte earlier per preceding line, and writing such a text
                # back would silently re-encode the whole file to LF.
                text = path.read_text(encoding="utf-8", errors="replace", newline="")
            except OSError:
                continue
            lines = text.splitlines(keepends=True)
            try:
                label = path.relative_to(root).as_posix()
            except ValueError:
                label = path.as_posix()

            offset = 0
            for index, line in enumerate(lines):
                start = offset
                offset += len(line)
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
                    span = (start + match.start("version"), start + match.end("version"))
                else:
                    # The strictly block usually sits on the same line, but a
                    # wrapped declaration puts it on the next few. Joining with
                    # nothing keeps the window's offsets equal to the file's,
                    # since keepends already carried each terminator along.
                    window = "".join(lines[index:index + 4])
                    strict = _STRICTLY_RE.search(window)
                    if strict is None:
                        pin, form, span = "", "unpinned", None
                    else:
                        pin, form = strict.group(1), "strictly"
                        span = (start + strict.start(1), start + strict.end(1))
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
                    "module": module,
                    "path": str(path),
                    "span": span,
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


def _line_at(text: str, offset: int) -> tuple[int, int, int]:
    """Locates the line holding a character offset.

    Args:
        text: the whole file, decoded with newline translation OFF.
        offset: a character offset into it.

    Returns:
        (1-based line number, start offset, end offset), where the end excludes
        the terminator. A CRLF file leaves the \\r inside the slice, so a caller
        rendering the line strips it rather than assuming \\n.
    """
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    return text.count("\n", 0, start) + 1, start, len(text) if end < 0 else end


def _site_ref(site: dict) -> dict:
    """The identifying half of an occurrence, for a report that changes nothing."""
    return {"file": site["file"], "line": site["line"], "module": site["module"],
            "form": site["form"], "pin": site["pin"]}


def _rewrite_file(path: Path, sites: list[dict], sha: str,
                  write: bool) -> tuple[list[dict], str | None]:
    """Renders, and optionally applies, every planned edit in one file.

    The file is read AGAIN here rather than trusted from the scan, and each
    recorded span is checked to still hold the pin the scan saw. A splice at a
    stale offset would corrupt a coordinate rather than fail, so a file that
    moved between the two reads is refused instead of edited. Decoding is
    strict for the same reason: errors="replace" is fine for reporting, but
    writing a replaced text back would burn U+FFFD into the file.

    Edits are applied right to left so each earlier span keeps its offsets, and
    the whole file is written once - a file with nothing to change is not
    rewritten at all.

    Args:
        path: the build file to edit.
        sites: occurrences in this file, each carrying a span.
        sha: the pin text to write.
        write: whether to write; False renders the same entries and touches
            nothing.

    Returns:
        (entries, error). Each entry carries file, module, line (the line the
        pin is on), decl_line (the coordinate's own line, which differs for a
        wrapped declaration), form, pin, before, after and changed. An error
        yields no entries, because nothing about the file could be established.
    """
    try:
        text = path.read_text(encoding="utf-8", newline="")
    except (OSError, UnicodeDecodeError) as exc:
        return [], f"cannot read '{path}': {exc}"

    entries: list[dict] = []
    edits: list[tuple[int, int, str]] = []
    for site in sites:
        start, end = site["span"]
        if text[start:end] != site["pin"]:
            return [], f"{_STALE_SCAN_NOTE} ('{site['file']}' line {site['line']})"
        number, line_start, line_end = _line_at(text, start)
        if end > line_end:
            # A pin literal spanning a line break is not something any dialect
            # here produces, and splicing one would rewrite the wrong slice.
            return [], (f"pin at '{site['file']}' line {number} spans a line break - "
                        f"edit it by hand")
        changed = site["pin"] != sha
        entries.append({
            "file": site["file"], "module": site["module"], "line": number,
            "decl_line": site["line"], "form": site["form"], "pin": site["pin"],
            "before": text[line_start:line_end].rstrip("\r"),
            "after": (text[line_start:start] + sha + text[end:line_end]).rstrip("\r"),
            "changed": changed,
        })
        if changed:
            edits.append((start, end, sha))

    if write and edits:
        for start, end, replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[end:]
        try:
            path.write_text(text, encoding="utf-8", newline="")
        except OSError as exc:
            return entries, f"cannot write '{path}': {exc}"
    return entries, None


def _verify_pin(artifact: str, sha: str, timeout: float) -> dict:
    """Asks whether a sha is built BEFORE it is written into a build file.

    Read-only twice over: jitpack_status reads the versionless build list and
    triggers nothing, and it validates the ref against the publishing repo's
    local git first - which is what catches the sha that was never pushed, the
    failure a hand-rolled sed cannot see at all.

    The module token is the repo DIRECTORY rather than a name, because the
    artifact id is the git remote's repo segment and need not match any module
    name (minecraft-text publishes minecraft-library/text).

    Args:
        artifact: the artifact id being pinned.
        sha: the pin about to be written.
        timeout: seconds before the list read is abandoned.

    Returns:
        dict with ok, ref (the 7-char sha the request actually resolved to, or
        None), state, status, repo, list_ok and error. state is "unpublished"
        when no workspace repo publishes the artifact, so the question cannot be
        asked here at all - which is a reason to stop, not a reason to write.
    """
    repo_dir = _repo_dirs_by_artifact({artifact}).get(artifact)
    if repo_dir is None:
        return {"ok": False, "ref": None, "state": "unpublished", "list_ok": False,
                "error": f"no repo in this workspace publishes '{artifact}', so its sha "
                         f"cannot be checked here - pass --no-verify to pin it unchecked"}
    report = jitpack_status(str(repo_dir), refs=[sha], timeout=timeout)
    entry = (report.get("refs") or [{}])[0]
    return {
        "ok": bool(entry.get("ok")),
        "ref": entry.get("ref"),
        "state": entry.get("state") or "unresolved",
        "status": entry.get("status"),
        "repo": report.get("repo"),
        "list_ok": report.get("list_ok", False),
        "error": report.get("error") or entry.get("error"),
    }


def jitpack_set(artifact: str, sha: str, modules: Sequence[str] = (), check: bool = False,
                verify: bool = True, include_snapshots: bool = False,
                timeout: float = _LIST_TIMEOUT) -> dict:
    """Rewrites one artifact's pin across the workspace build files.

    The write counterpart of jitpack_pins, and the replacement for the
    throwaway ``sed -i -E "s#(:$a\\") \\{ version \\{ strictly\\(\\")[a-f0-9]+...``
    that a nine-module cascade was last driven by. Every defect of that script
    is a rule here:

      * It could not miss silently. A typo'd artifact matched nothing, printed
        nothing and exited 0, and the news arrived days later as a build
        resolving the old sha. Zero matches is an error carrying the artifact
        ids that WERE found.
      * It knew one dialect. The scan recognises the library
        ``strictly(...)`` form, the app ``group:artifact:version`` form and a
        ``strictly`` that wrapped onto a following line, and edits the pin
        inside whichever it finds - never converting one into the other.
      * It validated nothing. The sha is resolved in the publishing repo's
        local git, checked to be pushed, and checked to be built on jitpack
        before a byte is written.
      * The artifact id was interpolated into a regex. Here it is compared with
        ``==`` and the edit is an offset splice, so a metacharacter in an id
        cannot widen what matches.

    Matching is EXACT, unlike the substring filter jitpack_pins audits with: a
    filter that over-matches shows you extra rows, while a rewrite that
    over-matches edits the wrong artifact.

    Args:
        artifact: artifact id, optionally qualified as "<group>:<artifact>".
            The qualified form is required when one id is pinned under more
            than one group.
        sha: the pin to write. Any hex sha is cut to 7 chars, the constant
            every other path here uses, because each distinct prefix length is
            a separate jitpack build. Under verification this may be any ref
            local git can resolve - a branch, a tag, origin/master - and what
            gets written is the 7-char sha it resolved to, so the version
            checked and the version pinned are always the same one.
        modules: module aliases, names or paths to narrow the edit to. Empty
            means every module that pins the artifact.
        check: report what would change and write nothing.
        verify: confirm the sha is built on jitpack before writing.
        include_snapshots: also nail ``-SNAPSHOT`` coordinates to the sha.
            Off by default - they float onto the new commit by themselves, and
            fixing one silently is a semantic change nobody asked for.
        timeout: seconds before the verification list read is abandoned.

    Returns:
        dict with artifact, group, sha, check, changed, unchanged, skipped,
        files (one entry per matched site: file, line, before, after, changed,
        and the module/form/pin it belongs to), skipped_sites (every site NOT
        edited, each with a reason), verification, ok and status - one of:

          "written"     - the files were edited; ok
          "checked"     - --check, so nothing was written; ok
          "unchanged"   - every matched pin already held the sha; ok
          "unbuilt"     - jitpack has no green build for the sha; nothing written
          "unverified"  - jitpack could not be asked; nothing written
          "write-failed"- a file could not be read back or written
          "precondition"- nothing matched, or the request was unanswerable

        "precondition" is exit-code-2 territory; every other non-ok status is
        exit code 1.
    """
    base: dict = {"artifact": artifact, "sha": sha, "check": check, "changed": 0,
                  "unchanged": 0, "skipped": 0, "files": [], "skipped_sites": []}

    def stop(status: str, message: str, **extra) -> dict:
        return {**base, **extra, "ok": False, "status": status, "error": message}

    if workspace_root() is None:
        return stop("precondition", "no inventory - run 'toolsmith setup' first")

    # An unqualified id is the normal case; the group form exists so a name
    # pinned under two orgs can be addressed at all.
    qualifier, _sep, wanted = artifact.rpartition(":")
    wanted, qualifier = wanted.strip(), (qualifier.strip() or None)
    if not wanted:
        return stop("precondition", f"'{artifact}' names no artifact")

    target = sha.strip()
    if _ABBREVIABLE_SHA_RE.fullmatch(target):
        # Lowercased because jitpack keys its build records on the exact string.
        target = target.lower()
    truncated = len(target) > _SHA_LEN and _ABBREVIABLE_SHA_RE.fullmatch(target) is not None
    if truncated:
        target = target[:_SHA_LEN]
    base["sha"] = target

    sites = _scan_pins()
    matches = [site for site in sites
               if site["artifact"].lower() == wanted.lower()
               and (qualifier is None or site["group"] == qualifier)]
    if not matches:
        # The dangerous failure of the script this replaces, turned into the
        # loudest one available: the ids that DO exist are printed, so a typo
        # is visible in the same breath.
        found = sorted({site["artifact"] for site in sites})
        near = sorted({name for name in found
                       if wanted.lower() in name.lower() or name.lower() in wanted.lower()})
        message = f"no workspace build file pins '{artifact}'"
        if qualifier is not None and any(s["artifact"].lower() == wanted.lower() for s in sites):
            message += f" under group '{qualifier}'"
        if near:
            message += f" - did you mean {', '.join(near)}?"
        return stop("precondition", message, found=found, near=near)

    groups = sorted({site["group"] for site in matches})
    if len(groups) > 1:
        return stop("precondition",
                    f"'{wanted}' is pinned under {len(groups)} groups - qualify it as "
                    f"{' or '.join(f'{g}:{wanted}' for g in groups)}", groups=groups)
    group = groups[0]
    base["group"] = group

    if matches[0]["central"]:
        # Maven Central publishes it, so it carries a released version and has
        # no jitpack build to point a sha at.
        return stop("precondition",
                    f"'{wanted}' publishes to maven central as '{group}:{wanted}' - "
                    f"a commit sha is not a version there")

    if modules:
        targets: dict[str, Path] = {}
        unknown: list[str] = []
        for token in modules:
            directory = resolve_module(token)
            if directory is None:
                unknown.append(token)
            else:
                targets[token] = directory.resolve()
        if unknown:
            return stop("precondition",
                        f"module(s) not resolved: {', '.join(unknown)} "
                        f"(run 'toolsmith setup')")
        narrowed = [site for site in matches
                    if Path(site["path"]).parent.resolve() in set(targets.values())]
        if not narrowed:
            # Narrowing to nothing is the same silent no-op as a typo'd
            # artifact, so it is the same error - naming who does pin it.
            pinning = sorted({site["module"] or "<workspace root>" for site in matches})
            return stop("precondition",
                        f"none of the requested modules pin '{wanted}' - it is pinned by "
                        f"{', '.join(pinning)}", pinned_by=pinning)
        matches = narrowed

    editable: list[dict] = []
    skipped: list[dict] = []
    for site in matches:
        if site["span"] is None or site["form"] not in _REWRITABLE_FORMS:
            skipped.append({**_site_ref(site),
                            "reason": "the coordinate names no version to replace"})
        elif site["form"] == "snapshot" and not include_snapshots:
            skipped.append({**_site_ref(site), "reason": _SNAPSHOT_SKIP_NOTE})
        else:
            editable.append(site)
    base["skipped_sites"] = skipped
    base["skipped"] = len(skipped)
    if not editable:
        return stop("precondition",
                    f"'{wanted}' matched {len(skipped)} declaration(s), none of them "
                    f"rewritable - see skipped_sites")

    verification = _verify_pin(wanted, target, timeout) if verify else None
    resolved = None
    if verification is not None and verification.get("ref") and verification["ref"] != target:
        # What gets written is what was CHECKED. _resolve_ref reports on the
        # 7-char form of whatever it was given, so writing the caller's spelling
        # instead would verify one version and pin another - and it is what lets
        # "origin/master" or a tag be passed here at all.
        resolved, target = target, verification["ref"]
    base["sha"] = target
    blocked = verification is not None and not verification["ok"]

    if not _is_segment(target):
        # A pin becomes a url path segment downstream, so the charset is enforced
        # on what would actually be WRITTEN, and only after a ref has had its
        # chance to resolve: "origin/master" is a legal ref and an illegal pin,
        # and gating the argument instead would refuse the useful half.
        detail = f" ({verification['error']})" if blocked and verification.get("error") else ""
        return stop("precondition",
                    f"'{sha}' is not a usable version - letters, digits and . _ + - only"
                    + detail, verification=verification)
    if blocked:
        state = verification["state"]
        # An unresolvable or unpushed sha, and an artifact this workspace does
        # not publish, are all the caller's question to answer - exit 2. A list
        # that would not answer is jitpack's, and inconclusive is not a licence
        # to write the pin either.
        status = {"unresolved": "precondition", "unpublished": "precondition",
                  "unreachable": "unverified"}.get(state, "unbuilt")
    else:
        status = "checked" if check else "written"

    # The diff is rendered either way: under --check, and under a refusal, what
    # WOULD change is the most useful thing left to report.
    write = not check and not blocked
    by_file: dict[str, list[dict]] = {}
    for site in editable:
        by_file.setdefault(site["path"], []).append(site)

    files: list[dict] = []
    errors: list[str] = []
    for path_str, file_sites in sorted(by_file.items()):
        entries, error = _rewrite_file(Path(path_str), file_sites, target, write)
        files.extend(entries)
        if error is None:
            continue
        errors.append(error)
        # A file that could not be established never just disappears from the
        # report; its sites move to the skipped list carrying the reason.
        for site in file_sites:
            if not any(entry["file"] == site["file"] and entry["decl_line"] == site["line"]
                       for entry in entries):
                skipped.append({**_site_ref(site), "reason": error})

    changed = sum(1 for entry in files if entry["changed"])
    if errors:
        status, blocked = "write-failed", True
    elif not blocked and changed == 0 and not check:
        status = "unchanged"

    notes = []
    if truncated:
        notes.append(f"sha cut to {_SHA_LEN} chars - each prefix length is a "
                     f"separate jitpack build")
    if resolved is not None:
        notes.append(f"'{resolved}' resolved to '{target}', which is the version checked "
                     f"and written")
    if changed and not blocked:
        notes.append(_CASCADE_NOTE)
    if any(entry["form"] == "snapshot" for entry in files):
        notes.append("a -SNAPSHOT coordinate was nailed to this sha and no longer floats")

    result = {
        **base,
        "ok": not blocked,
        "status": status,
        "group": group,
        "changed": changed,
        "unchanged": len(files) - changed,
        "skipped": len(skipped),
        "files": files,
        "files_touched": len({entry["file"] for entry in files if entry["changed"]}),
        "skipped_sites": skipped,
        "verification": verification,
    }
    if errors:
        result["error"] = errors[0]
        result["errors"] = errors
    elif blocked:
        result["error"] = verification["error"] or (
            f"jitpack reports '{target}' as {verification['state']} for '{wanted}'")
        if status == "unverified":
            notes.insert(0, _UNVERIFIED_NOTE)
    if notes:
        result["note"] = "; ".join(notes)
    return result


def _publishers() -> dict[str, str]:
    """Maps each discovered module NAME to the artifact its repo publishes.

    The artifact comes from the git remote's repo segment and never from the
    directory name - minecraft-text publishes minecraft-library/text. A module
    nested inside another module's repo maps to THAT repo's artifact, because
    its build file is committed and released as part of it, so a pin declared
    there moves the enclosing artifact's sha.

    Kept separate from _repo_dirs_by_artifact, which answers a narrow question
    and stops early once it has: this one needs every module, and pays one git
    call per repo rather than per module to get it.
    """
    root = workspace_root()
    if root is None:
        return {}
    by_repo: dict[str, str | None] = {}
    mapping: dict[str, str] = {}
    for module in get_modules():
        repo_dir = _walk_up_git(root / module["path"])
        if repo_dir is None:
            continue
        key = str(repo_dir)
        if key not in by_repo:
            parsed = _parse_remote(_git(repo_dir, "config", "--get", "remote.origin.url") or "")
            by_repo[key] = parsed[1] if parsed else None
        if by_repo[key]:
            mapping[module["name"]] = by_repo[key]
    return mapping


def _pin_graph() -> tuple[dict[str, set[str]], dict[str, list[dict]], dict[str, set[str]]]:
    """Builds the artifact dependency graph from the workspace build files.

    An edge runs from a pinned artifact to the artifact whose repo declares the
    pin, so following edges is following the blast radius of a new sha.

    Only FIXED pins carry an edge. A -SNAPSHOT consumer resolves the new commit
    with no edit, so it never earns a commit of its own, so its own consumers
    are not moved by this change - propagating through it would invent work.
    Maven Central coordinates carry no edge either: they are not jitpack builds.

    Returns:
        (consumers by pinned artifact, fixed pin sites by consuming artifact,
        floating consumers by pinned artifact).
    """
    publishes = _publishers()
    consumers: dict[str, set[str]] = {}
    sites_by: dict[str, list[dict]] = {}
    floating: dict[str, set[str]] = {}
    for site in _scan_pins():
        consumer = publishes.get(site["module"])
        if consumer is None or site["central"] or consumer == site["artifact"]:
            continue
        if site["form"] in ("strictly", "version"):
            consumers.setdefault(site["artifact"], set()).add(consumer)
            sites_by.setdefault(consumer, []).append(site)
        elif site["form"] == "snapshot":
            floating.setdefault(site["artifact"], set()).add(consumer)
    return consumers, sites_by, floating


def jitpack_order(artifact: str) -> dict:
    """Lists what has to be re-pinned after an artifact's sha changes, in order.

    The nine-module cascade this replaces was worked out by hand from ten
    build.gradle.kts files. It is a graph walk: every artifact whose repo
    declares a fixed pin on something already in the set joins it, and the
    result is levelled so everything at depth N can be re-pinned as soon as
    depth N-1 is built.

    The two reasons a module appears are NOT the same strength, and the
    difference rests on a measured fact: published gradle module metadata for
    these artifacts records ``{"requires": "<sha>"}``, not ``strictly``. An
    inherited pin is therefore SOFT and a consumer's own strictly() overrides
    it, so nothing in gradle forces the far end of a chain to move.

      "direct"   - the module declares a pin on <artifact> itself. Its own
                   strictly() is the binding constraint, so leaving it stale
                   means this module keeps building against the OLD code no
                   matter what anything downstream says.
      "cascade"  - the module only pins things that get a new sha because of
                   this change. Re-pinning keeps the workspace on one sha per
                   artifact, which is a convention here, not a requirement.

    Ordering is deterministic (depth, then alphabetical) but not unique - any
    topological order of the same graph is equally valid.

    Args:
        artifact: the artifact whose sha is changing.

    Returns:
        dict with artifact, order (one row per artifact to re-pin, each with
        artifact, modules, depth, reason and repins - the concrete declaration
        sites to edit), chain (the order as a flat list, the source first),
        floating (consumers that track it with -SNAPSHOT and need no edit),
        cycles, total, direct, cascade, note and ok. A source nobody pins and
        nobody publishes sets a top-level error and is exit-code-2 territory.
    """
    if workspace_root() is None:
        return {"ok": False, "status": "precondition", "artifact": artifact, "order": [],
                "total": 0, "error": "no inventory - run 'toolsmith setup' first"}

    source = artifact.rpartition(":")[2].strip()
    consumers, sites_by, floating = _pin_graph()
    publishes = _publishers()
    modules_by: dict[str, list[str]] = {}
    for module, published in sorted(publishes.items()):
        modules_by.setdefault(published, []).append(module)

    if source not in consumers and source not in modules_by:
        known = sorted(set(consumers) | set(modules_by))
        near = sorted(name for name in known
                      if source.lower() in name.lower() or name.lower() in source.lower())
        message = f"'{artifact}' is neither published nor pinned anywhere in this workspace"
        if near:
            message += f" - did you mean {', '.join(near)}?"
        return {"ok": False, "status": "precondition", "artifact": source, "order": [],
                "total": 0, "error": message, "known": known}

    # The blast radius: everything whose repo declares a fixed pin on something
    # already in the set.
    reached = {source}
    frontier = [source]
    while frontier:
        for consumer in sorted(consumers.get(frontier.pop(), ())):
            if consumer not in reached:
                reached.add(consumer)
                frontier.append(consumer)

    edges = {node: sorted(consumers.get(node, set()) & reached) for node in reached}
    indegree = {node: 0 for node in reached}
    for node, outgoing in edges.items():
        for consumer in outgoing:
            indegree[consumer] += 1

    depth = dict.fromkeys(reached, 0)
    ready = sorted(node for node, degree in indegree.items() if degree == 0)
    chain: list[str] = []
    while ready:
        node = ready.pop(0)
        chain.append(node)
        for consumer in edges[node]:
            depth[consumer] = max(depth[consumer], depth[node] + 1)
            indegree[consumer] -= 1
            if indegree[consumer] == 0:
                ready.append(consumer)
        # Depth first, then name: everything at one depth can be re-pinned as
        # soon as the depth before it is built, and the name only breaks a tie
        # so two runs agree.
        ready.sort(key=lambda pending: (depth[pending], pending))
    # A cycle leaves nodes with a residual indegree. Reporting them beats both
    # looping and dropping them, since a pin cycle is a real thing to go fix.
    cycles = sorted(reached - set(chain))

    rows: list[dict] = []
    for node in [*chain, *cycles]:
        repins = sorted(
            ({"artifact": site["artifact"], "pin": site["pin"], "form": site["form"],
              "file": site["file"], "line": site["line"]}
             for site in sites_by.get(node, []) if site["artifact"] in reached),
            key=lambda repin: (repin["artifact"], repin["file"], repin["line"]))
        direct = any(repin["artifact"] == source for repin in repins)
        rows.append({
            "artifact": node,
            "modules": modules_by.get(node, []),
            "depth": depth[node],
            "reason": "source" if node == source else ("direct" if direct else "cascade"),
            "repins": repins,
            "cyclic": node in cycles,
        })

    downstream = [row for row in rows if row["artifact"] != source]
    result = {
        "ok": True,
        "artifact": source,
        "order": rows,
        "chain": [row["artifact"] for row in rows],
        "total": len(downstream),
        "direct": sum(1 for row in downstream if row["reason"] == "direct"),
        "cascade": sum(1 for row in downstream if row["reason"] == "cascade"),
        "floating": sorted({consumer for pinned in reached
                            for consumer in floating.get(pinned, set())} - reached),
        "cycles": cycles,
        "note": _SOFT_PIN_NOTE,
    }
    if not downstream:
        result["note"] = f"nothing in this workspace pins '{source}'"
    return result
