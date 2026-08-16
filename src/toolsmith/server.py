"""FastMCP stdio server exposing the toolsmith tools.

This is a thin typed veneer: every tool forwards to the sibling library module
that does the real work. The point is to make the deterministic, high-frequency
operations a single cheap typed call instead of a re-derived shell incantation,
loaded ONLY under the Java workspace via a project-scoped .mcp.json.
"""
from __future__ import annotations

import contextlib
import io

from fastmcp import FastMCP

from . import gradle as _gradle
from . import imports as _imports
from . import javadoc as _javadoc
from . import jitpack as _jitpack
from . import json_diff as _json_diff
from . import modules as _modules
from . import tally as _tally

mcp = FastMCP("toolsmith")


@mcp.tool()
def gradle_verify(
    module: str,
    tasks: list[str] | None = None,
    tail: int = 25,
    compile_only: bool = False,
    rerun: bool = False,
) -> dict:
    """Run the module-scoped gradle gate and return a structured pass/fail result.

    Replaces the hand-written shape
    ``cd MODULE && ./gradlew compileJava test -q 2>&1 | grep -vE noise | tail -N``.

    Args:
        module: module alias (e.g. "ar"), name ("asset-renderer"), or path.
        tasks: gradle tasks to run. Defaults to compileJava+test.
        tail: how many signal / de-noised trailing lines to return.
        compile_only: use compileJava+compileTestJava as the default task set.
        rerun: force re-execution past up-to-date checks and the build cache
            (--rerun-tasks) so tests actually run instead of restoring
            FROM-CACHE. Note: clean does not do this.

    Returns:
        module, tasks, root, exit_code, ok, first_failure, lines, lines_kind.
        On timeout, ok is False, exit_code is None, and timed_out is True.

        Check lines_kind before reading anything into `lines`:
          "signal" - lines matched as failure diagnostics. Meaningful.
          "tail"   - no diagnostics matched, so this is just the last few
                     non-noise lines the build happened to print. It is NOT a
                     summary or a verdict, and on a passing run it is usually
                     unrelated chatter from whatever the build shelled out to.
          "empty"  - the build printed nothing after noise stripping. Normal
                     for a clean run under -q; not evidence the build no-opped.

        In particular `lines` is never a test tally. Counts printed there come
        from whatever the build spawned, not from gradle_verify - use
        gradle_tally or the JUnit XML for real numbers.
    """
    return _gradle.gradle_verify(module, tasks=tasks, tail=tail,
                                 compile_only=compile_only, rerun=rerun)


@mcp.tool()
def gradle_tally(module: str, subdir: str = "", fails: int = 15) -> dict:
    """Parse a gradle module's JUnit XML and return counts plus the names of failing tests.

    Needs a gradle module that has already run its tests. Replaces the recurring
    grep/awk/python one-liners over build/test-results/test/*.xml.

    Args:
        module: module alias, name, or path whose test-results to tally.
        subdir: optional sub-path holding a nested build dir.
        fails: cap on the number of failing testcase names returned.
    """
    return _tally.tally(module, subdir=subdir, fails_cap=fails)


@mcp.tool()
def java_reorder_imports(paths: list[str], check: bool = False) -> dict:
    """Reorder the imports of Java source files to the IntelliJ Default layout.

    IDE-independent - it acts on the `.java` files it is handed and needs no
    project model.

    Faithful to Optimize Imports: group 1 other, group 2 javax.* then java.*,
    group 3 static; ASCII sort; wildcards and CRLF/LF preserved; idempotent.

    Args:
        paths: .java files, directories, or globs.
        check: report what WOULD change without writing (a gate; sets no files).
    """
    return _imports.run(paths, mode="check" if check else "write")


@mcp.tool()
def java_docs_normalize(
    paths: list[str],
    fix: bool = False,
    scope: str = "all",
    prefix: list[str] | None = None,
) -> dict:
    """Audit (or --fix) the javadocs of Java source files against the project conventions.

    Args:
        paths: .java files, directories, or globs to process.
        fix: apply safe auto-fixes in place (otherwise audit-only).
        scope: one of class | method | field | all.
        prefix: extra FQN top-level prefixes to auto-import (additive).

    When fix is True, treat as destructive - re-Read any file you had already
    Read before editing it further.
    """
    argv: list[str] = ["--fix"] if fix else []
    argv += ["--scope", scope]
    for p in prefix or []:
        argv += ["--prefix", p]
    argv += list(paths)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = _javadoc.main(argv)
    return {"fixed": fix, "exit_code": code, "output": buf.getvalue()[-8000:]}


@mcp.tool()
def java_json_diff(
    json_path: str,
    src: str,
    root: str,
    node: str = "",
    union: str = "",
    section: str | None = None,
    show_mapped: bool = False,
    show_unresolved: bool = False,
    max_depth: int = 12,
    cap: int = 200,
    opaque: list[str] | None = None,
    map_types: list[str] | None = None,
    seq_types: list[str] | None = None,
    wrapper_types: list[str] | None = None,
    strict: list[str] | None = None,
    phantom: bool = False,
    fail_on_phantom: bool = False,
) -> dict:
    """Diff a JSON capture against the Java DTO tree that binds it and report unmapped keys.

    Needs a Java source root holding the classes and a JSON file captured from
    the API they bind. Needs no gradle build, no network and no IDE - it parses
    the Java with regexes and walks the JSON, so it runs against a checkout
    alone. Replaces reading a multi-megabyte capture by hand to find out which
    of its keys the DTO tree does not bind.

    Traps this encodes:
      - What it understands is a VOCABULARY rather than a language:
        @SerializedName, @SerializedPath, @Extract, @Capture, @Collapse, @Key
        and @Flatten. @Lenient, @Split and @Fallback are read by nothing, and a
        field declaration split across two lines matches nothing at all - which
        is what empty_classes reports.
      - A key optional in every individual sample reports unmapped from a
        single sample and mapped from a union of them. Pass union to merge the
        samples into one template first.
      - The strictness switches all default OFF. Each corrects one annotation's
        handling and each can change the verdict on a capture the walk calls
        clean, so each is asked for by name.
      - Key lists carry `cap` rows and the counts beside them do not, so
        `truncated` is what says a list is short.
      - phantom answers the OPPOSITE question - fields no JSON key reaches -
        and most of that answer is a subtree the account simply did not send.
        It also puts both whole projections in the return uncapped, which is
        the one place bulk is allowed in, so ask for it deliberately.

    Args:
        json_path: path to the JSON capture to audit. Spelled with the suffix
            because a parameter named `json` shadows an attribute of the model
            the tool schema is built from.
        src: Java source root whose classes are parsed.
        root: the class the walk starts from, by simple or nested name.
        node: dotted path narrowing the document to one subtree. Empty audits
            the document itself.
        union: path expression whose matched nodes merge into one template
            before the walk. `[]` is every array element, `{}` every object
            value.
        section: keep only this top-level key of the node. It narrows the
            capture alone, so it is refused together with phantom.
        show_mapped: also return the keys a field does map (large - use cap).
        show_unresolved: also return fields whose Java type did not parse.
        max_depth: how deep the parallel walk descends.
        cap: rows per returned list. 0 is uncapped.
        opaque: type names to add to the never-descend set. An entry written
            "-Name" is removed from the built-in set instead.
        map_types: collection types whose LAST type argument describes a JSON
            object's values. Added and subtracted like opaque.
        seq_types: collection types whose FIRST type argument describes an
            element. Added and subtracted like opaque.
        wrapper_types: types unwrapped to the type inside them. Added and
            subtracted like opaque.
        strict: strictness switches to turn on - names, extract, capture,
            collapse, flatten - or "all".
        phantom: also project both sides and report the fields no JSON key
            reaches. It projects from root, which section does not narrow.
        fail_on_phantom: let that second direction decide ok / the exit code.

    Returns:
        The inputs echoed, then ok, the counts unmapped / mapped / unresolved /
        classes, sections (each: section, count, a capped keys list of
        path/kind), shape_mismatches, and the diagnostics empty_classes,
        ambiguous_types and unreadable_files - none of which is a finding about
        the JSON.

        Under phantom, three more: phantoms, the capped list of paths the class
        graph can bind and the document never fed; phantom_total beside it; and
        projection, holding the two whole sorted line lists uncapped.

        ok is True only when no key was unmapped AND no shape mismatched; a
        phantom never moves it unless fail_on_phantom asked. A precondition
        failure - unreadable capture, source root that parsed no classes,
        unknown root class, path expression that matches nothing - sets status
        "precondition" and a top-level error, and reports no counts at all.
    """
    return _json_diff.json_diff(
        json_path, src, root=root, node=node, union=union, section=section,
        show_mapped=show_mapped, show_unresolved=show_unresolved,
        max_depth=max_depth, cap=cap, opaque=opaque or (), map_types=map_types or (),
        seq_types=seq_types or (), wrapper_types=wrapper_types or (), strict=strict or (),
        phantom=phantom, fail_on_phantom=fail_on_phantom)


# The editor hand-off (`toolsmith java json_diff --open`) is deliberately not a
# parameter above: it opens a diff viewer on somebody's screen and an agent has
# none. Nothing here can reach json_diff.open_diff, since json_diff() never
# calls it and no argument of this tool selects it.


@mcp.tool()
def jitpack_status(module: str, refs: list[str] | None = None) -> dict:
    """Report whether a module's commits are built on JitPack. Never triggers a build.

    Replaces `curl -s https://jitpack.io/api/builds/com.github.<org>/<artifact>`
    plus the local git work needed to know which sha to look for. Reads exactly
    one URL - the versionless build list - so it is safe to call freely. Run it
    BEFORE jitpack_build: an already-ok sha costs no build there, only a cached
    artifact read that confirms the record.

    Args:
        module: module alias (e.g. "d4j"), name, or path.
        refs: git refs or short shas to report on. Empty asks about
            origin/HEAD (never the local HEAD), which is the only ref worth
            pinning.

    Returns:
        module, group, artifact, org, repo, repo_dir, records, counts
        (total/ok/error/in-flight/unknown) and refs - one entry per requested
        ref with ref (7-char sha), full, source, pushed, status, state, ok.
        ok is True only when every requested ref is built.

        A precondition failure - module unresolved, no git remote, ref not
        resolvable/pushed/ambiguous, or a module that publishes to Maven
        Central - sets a top-level error instead.
    """
    return _jitpack.jitpack_status(module, refs=refs or ())


@mcp.tool()
def jitpack_build(
    module: str,
    ref: str = "",
    timeout: float = _jitpack.MCP_BUILD_TIMEOUT,
    force: bool = False,
) -> dict:
    """Precheck, then trigger and wait for ONE JitPack build of a module's commit.

    Replaces the hand-authored
    `curl -o /dev/null -w "%{http_code}" --max-time N <jitpack>/<...>.pom` round.
    The ref is validated from local git before anything is requested, one list
    read prechecks it, then a single blocking GET both triggers the build and
    waits for it.

    Traps this encodes, none of them discoverable from JitPack's docs:
      - Never call the per-version /api/builds/<group>/<artifact>/<version>
        endpoint yourself: it silently STARTS A BUILD and its records go stale
        for months.
      - status "timeout" is INCONCLUSIVE, not a failure. The build continues
        server-side; call this tool again and it attaches to the same build
        rather than starting a second one. resume is True in that case.
      - status "cached-failure" never changes for the same sha - not with
        force, not on retry. It needs a NEW COMMIT.
      - A list record saying "ok" is never the verdict on its own: the list
        reports ok for artifacts that answer 404, so ok means an HTTP 200 on
        the .pom and nothing less. status "already-built" carries that 200.
      - A green build is a COMPILE check: JitPack builds with -xtest, and
        composite includeBuild substitution means a green consumer build proves
        nothing. Use gradle_verify from the module dir for a real gate.

    Args:
        module: module alias, name, or path.
        ref: git ref or short sha. Empty resolves origin/HEAD, never local HEAD.
        timeout: seconds to hold the blocking request. The default sits below a
            typical harness cap so a slow build returns a clean "timeout" dict
            instead of killing the call.
        force: re-request a sha the precheck already reported.

    Returns:
        module, group, artifact, org, repo, ref, full_sha, source, precheck,
        action, url, http_code, elapsed, log_tail (capped), hints, ok and
        status - one of built, already-built, failed, cached-failure, timeout,
        in-flight, symbolic, precondition, error. A success also carries pin
        (the ready-to-paste strictly line); a failure carries error and the
        build.log tail.
    """
    return _jitpack.jitpack_build(module, ref=ref or None, timeout=timeout, force=force)


@mcp.tool()
def jitpack_set(
    artifact: str,
    sha: str,
    modules: list[str] | None = None,
    check: bool = False,
    no_verify: bool = False,
    include_snapshots: bool = False,
) -> dict:
    """Rewrite one artifact's JitPack pin across every workspace build file that declares it.

    Replaces the hand-rolled
    `sed -i -E "s#(:$a\\") \\{ version \\{ strictly\\(\\")[a-f0-9]+...#..#" build.gradle.kts`
    that a multi-module re-pin gets driven by. Use it instead of Edit when the
    same sha has to land in several build files.

    Traps this encodes:
      - A sed that matches nothing exits 0 and prints nothing, so a typo'd
        artifact is only discovered days later when a build resolves the old
        sha. Here zero matches is an ERROR carrying the artifact ids that were
        found. Treat a non-zero exit as "the edit did not happen".
      - Matching is EXACT, not the substring filter `jitpack pins` audits with.
        A rewrite that over-matches edits the wrong artifact.
      - Both dialects are handled - the library `strictly(...)` form and the
        app `group:artifact:version` form, including a `strictly` wrapped onto
        the next line - and each site keeps whichever it already used.
      - `-SNAPSHOT` coordinates are SKIPPED by default: they float onto the new
        commit by themselves, so nailing one to a sha is a semantic change.
        Pass include_snapshots to do it anyway.
      - The sha is checked to be resolvable, pushed, and built on JitPack
        before anything is written (one read-only list call, never a build).
        no_verify skips that; the pin is then unchecked.
      - What gets written is the 7-char sha verification resolved, not the
        spelling you passed. Every prefix length is a separate JitPack build,
        so pinning 8 or 40 chars asks for an artifact nobody produced. This is
        also why `sha` may be any ref local git resolves - a branch, a tag,
        origin/master - as long as verification is on. Read `sha` back off the
        result rather than assuming your input was written.
      - Idempotent: a site already holding the sha reports as unchanged and is
        not rewritten, and a file with nothing to change is not written at all.

    Args:
        artifact: artifact id (e.g. "collections"), or "<group>:<artifact>"
            when one id is pinned under two groups.
        sha: the pin to write, cut to 7 chars. With verification on this may
            be any ref local git resolves; the sha it resolves to is written.
        modules: module aliases, names or paths to narrow the edit to. Empty
            means every module that pins the artifact.
        check: report what would change and write nothing.
        no_verify: skip the JitPack precheck.
        include_snapshots: also nail `<branch>-SNAPSHOT` coordinates to the sha.

    Returns:
        artifact, group, sha, check, changed, unchanged, skipped, files (per
        matched site: file, line, before, after, changed, module, form),
        skipped_sites (each with a reason), verification, ok and status - one
        of written, checked, unchanged, unbuilt, unverified, write-failed,
        precondition.

        When check is False and status is "written", treat as destructive:
        re-Read any build file you had already Read before editing it further.
    """
    return _jitpack.jitpack_set(artifact, sha, modules=modules or (), check=check,
                                verify=not no_verify, include_snapshots=include_snapshots)


@mcp.tool()
def jitpack_order(artifact: str) -> dict:
    """List the modules needing a re-pin after an artifact's sha changes, in dependency order.

    Replaces reading every build.gradle.kts in the workspace and working the
    cascade out by hand. Read-only and offline - it walks the pin graph, not
    JitPack. Run it BEFORE jitpack_set to know how far the change travels, and
    walk the chain one module at a time: commit, jitpack_build, then
    jitpack_set the next module along.

    Traps this encodes:
      - `strictly` is NOT transitive here. Published gradle module metadata for
        these artifacts records {"requires": "<sha>"}, so an inherited pin is a
        SOFT constraint and a consumer's own strictly() overrides it. Do not
        tell the user gradle forces the whole chain to move.
      - Hence the two reasons: "direct" means the module pins this artifact
        itself, and leaving it stale keeps it building against the OLD code.
        "cascade" means it only pins things that get a new sha as a result -
        re-pinning is this workspace's one-sha-per-artifact convention.
      - `-SNAPSHOT` consumers appear under "floating", not in the order: they
        resolve the new commit with no edit at all, so they earn no commit of
        their own and do not propagate the cascade.
      - The order is deterministic (depth, then alphabetical) but not unique;
        any topological order of the same graph is equally valid.

    Args:
        artifact: the artifact whose sha is changing.

    Returns:
        artifact, chain (the flat order, source first), order (per artifact:
        artifact, modules, depth, reason, repins - the concrete declaration
        sites to edit), floating, cycles, total, direct, cascade, note, ok.
    """
    return _jitpack.jitpack_order(artifact)


# Workspace pin drift (`toolsmith jitpack pins`) is deliberately CLI-only, like
# setup and java locate: it is a wide table for a human, not a call in an agent
# loop.


@mcp.tool()
def gradle_modules() -> dict:
    """List the workspace's discovered projects.

    Each module: name, path (workspace-relative), kind ("gradle", "maven",
    "python", or null for a repository declaring no build system), repo (whether
    it is a git repository root), package (base Java package), shorthand (short
    alias), buildable. Reads the cache written by `toolsmith setup`. Use this to
    look up a module's real base package or alias instead of guessing paths -
    several package roots do not match the directory name.

    kind and repo vary independently: a gradle module can sit inside a
    repository it does not own, and a repository can carry no build file at all.
    """
    root = _modules.workspace_root()
    mods = _modules.get_modules()
    if not mods:
        return {"root": root.as_posix() if root else None, "modules": [],
                "note": "no inventory - run `toolsmith setup` in the workspace"}
    return {"root": root.as_posix(), "count": len(mods), "modules": mods}


def main() -> None:
    """Entry point: run the stdio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
