"""Tests for the command surface - the umbrellas, the aliases, and the tool names.

A prefix marks WHO CAN USE a command rather than what it parses, so the surface
is itself a contract: `toolsmith java <cmd>` needs Java source, `toolsmith
gradle <cmd>` needs a gradle build, and the MCP tools carry the same two
prefixes. What is pinned here is that both spellings of a moved subcommand reach
one handler, that the deprecation notice never reaches stdout (a piped stdout
carries results, not commentary), and that each MCP tool answers to exactly one
name - nothing types those by hand, so a second name is only ambiguity.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from toolsmith import cli, modules, server

# One row per moved subcommand: the deprecated argv, the current argv, and
# arguments valid under both.
MOVED = [
    (["modules"], ["gradle", "modules"], []),
    (["verify"], ["gradle", "verify"], ["ar", "test"]),
    (["tally"], ["gradle", "tally"], ["d4j"]),
    (["locate"], ["java", "locate"], ["TypeRegistrar"]),
    (["reorder"], ["java", "reorder"], ["src"]),
    (["javadoc"], ["java", "docs"], ["src"]),
]

INVENTORY = [{"shorthand": "ar", "name": "asset-renderer", "buildable": True,
              "path": "Minecraft-Library/asset-renderer", "package": "lib.minecraft"}]

CURRENT_TOOLS = {
    "gradle_modules", "gradle_verify", "gradle_tally",
    "java_reorder_imports", "java_docs_normalize",
    "jitpack_status", "jitpack_build", "jitpack_set", "jitpack_order",
}


def _help_for(argv: list[str], capsys) -> str:
    """Returns the help text argparse prints for an invocation.

    Args:
        argv: the arguments to parse, ending in --help
        capsys: the pytest capture fixture to read the text back from

    Returns:
        everything the parser wrote to stdout
    """
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args(argv)
    assert excinfo.value.code == 0
    return capsys.readouterr().out


def _tool_names() -> set[str]:
    """Returns the names the MCP server actually advertises."""
    return {tool.name for tool in asyncio.run(server.mcp.list_tools())}


# --------------------------------------------------------------------------
# The aliases. One handler per subcommand, reachable under either spelling.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("deprecated, current, rest", MOVED)
def test_an_alias_runs_the_handler_of_its_current_spelling(deprecated, current, rest):
    parser = cli.build_parser()

    assert parser.parse_args(deprecated + rest).func is parser.parse_args(current + rest).func


@pytest.mark.parametrize("deprecated, current, rest", MOVED)
def test_only_the_alias_is_marked_for_a_notice(deprecated, current, rest):
    parser = cli.build_parser()

    assert parser.parse_args(deprecated + rest).moved_from == (deprecated[0], " ".join(current))
    assert getattr(parser.parse_args(current + rest), "moved_from", None) is None


def test_an_alias_takes_the_arguments_its_current_spelling_takes():
    """The two spellings share one argument shape, so neither can drift."""
    parser = cli.build_parser()

    deprecated = parser.parse_args(["javadoc", "--fix", "--scope", "class", "src"])
    current = parser.parse_args(["java", "docs", "--fix", "--scope", "class", "src"])

    assert (deprecated.fix, deprecated.scope, deprecated.paths) == (True, "class", ["src"])
    assert (deprecated.fix, deprecated.scope, deprecated.paths) == (current.fix, current.scope,
                                                                    current.paths)


def test_the_notice_lands_on_stderr_and_the_command_output_on_stdout(capsys, monkeypatch):
    """Commentary on stderr is what keeps a piped stdout readable as data."""
    monkeypatch.setattr(modules, "get_modules", lambda: INVENTORY)

    assert cli.main(["modules"]) == 0

    captured = capsys.readouterr()
    assert "asset-renderer" in captured.out
    assert "deprecated" not in captured.out
    assert "`toolsmith modules` is deprecated" in captured.err
    assert "toolsmith gradle modules" in captured.err


def test_the_current_spelling_prints_no_notice(capsys, monkeypatch):
    monkeypatch.setattr(modules, "get_modules", lambda: INVENTORY)

    assert cli.main(["gradle", "modules"]) == 0

    captured = capsys.readouterr()
    assert "asset-renderer" in captured.out
    assert captured.err == ""


# --------------------------------------------------------------------------
# The help, which is the surface a reader is offered.
# --------------------------------------------------------------------------

def test_the_group_help_lists_the_subcommands_it_holds(capsys):
    java = _help_for(["java", "--help"], capsys)
    assert re.search(r"^\s+locate\s", java, re.M)
    assert re.search(r"^\s+reorder\s", java, re.M)
    assert re.search(r"^\s+docs\s", java, re.M)

    gradle = _help_for(["gradle", "--help"], capsys)
    assert re.search(r"^\s+modules\s", gradle, re.M)
    assert re.search(r"^\s+verify\s", gradle, re.M)
    assert re.search(r"^\s+tally\s", gradle, re.M)


def test_the_top_level_help_offers_only_the_grouped_surface(capsys):
    text = _help_for(["--help"], capsys)

    assert "{setup,serve,java,gradle,jitpack,branch}" in text
    # An alias is registered with no help at all: argparse renders a SUPPRESS
    # help as the literal "==SUPPRESS==" for a subparser rather than hiding it.
    assert "SUPPRESS" not in text
    for deprecated, _, _ in MOVED:
        assert not re.search(rf"^\s+{deprecated[0]}\s", text, re.M)


# --------------------------------------------------------------------------
# The MCP surface.
# --------------------------------------------------------------------------

def test_the_server_exposes_the_grouped_tool_names():
    assert _tool_names() == CURRENT_TOOLS


def test_no_tool_answers_to_a_second_name():
    """Nothing types an MCP tool name by hand, so one tool earns exactly one name."""
    assert _tool_names().isdisjoint(
        {"list_modules", "test_tally", "reorder_imports", "javadoc_normalize"})
