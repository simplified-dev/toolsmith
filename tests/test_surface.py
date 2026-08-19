"""Tests for the command surface - the umbrellas, the spellings, and the tool names.

A prefix marks WHO CAN USE a command rather than what it parses, so the surface
is itself a contract: `toolsmith java <cmd>` needs Java source, `toolsmith
gradle <cmd>` needs a gradle build, and the MCP tools carry the same two
prefixes. What is pinned here is that a grouped subcommand is reachable under
its group and nowhere else, that a command's result reaches stdout with nothing
on stderr beside it (a piped stdout carries results, not commentary), and that
each MCP tool answers to exactly one name - nothing types those by hand, so a
second name is only ambiguity.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from toolsmith import cli, modules, server

JSON_DIFF_REST = ["--json", "capture.json", "--src", "src", "--root", "SkyBlockMember"]

# One row per grouped subcommand: the argv that reaches it, arguments it parses,
# and the handler it must land on. The handler is named here rather than read
# back out of _GROUPED_COMMANDS, which would only assert the table against
# itself.
GROUPED = [
    (["gradle", "modules"], [], cli._cmd_modules),
    (["gradle", "verify"], ["ar", "test"], cli._cmd_verify),
    (["gradle", "tally"], ["d4j"], cli._cmd_tally),
    (["java", "locate"], ["TypeRegistrar"], cli._cmd_locate),
    (["java", "reorder"], ["src"], cli._cmd_reorder),
    (["java", "docs"], ["src"], cli._cmd_docs),
    (["java", "json_diff"], JSON_DIFF_REST, cli._cmd_json_diff),
]

# The flat spellings nobody is offered: every grouped subcommand's bare name,
# plus `javadoc` for the one whose group name differs from it. A misread of the
# table registers a top-level parser under one of these, which is a spelling the
# help does not mention and no documentation names.
FLAT = ["modules", "verify", "tally", "locate", "reorder", "javadoc", "docs", "json_diff"]

INVENTORY = [{"shorthand": "ar", "name": "asset-renderer", "buildable": True,
              "kind": "gradle", "repo": True,
              "path": "Minecraft-Library/asset-renderer", "package": "lib.minecraft"}]

CURRENT_TOOLS = {
    "gradle_modules", "gradle_verify", "gradle_tally",
    "java_reorder_imports", "java_docs_normalize", "java_json_diff",
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
# The spellings. A group is where a command lives, and the only way in.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("argv, rest, handler", GROUPED)
def test_a_grouped_subcommand_reaches_its_handler(argv, rest, handler):
    assert cli.build_parser().parse_args(argv + rest).func is handler


@pytest.mark.parametrize("flat", FLAT)
def test_a_flat_spelling_is_a_usage_error(flat, capsys):
    """A subcommand is reachable under its group and nowhere else.

    Registering a second top-level parser is how a subcommand acquires a
    spelling nobody was promised: one the help does not mention, that no
    documentation names, and that quietly becomes a compatibility burden.
    """
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args([flat])

    assert excinfo.value.code == 2
    capsys.readouterr()


def test_a_grouped_subcommand_takes_the_arguments_its_shape_declares():
    """The argument shape reaches the parser the table registered it against."""
    args = cli.build_parser().parse_args(["java", "docs", "--fix", "--scope", "class", "src"])

    assert (args.fix, args.scope, args.paths) == (True, "class", ["src"])


def test_a_result_lands_on_stdout_with_nothing_beside_it(capsys, monkeypatch):
    """Commentary on stderr is what keeps a piped stdout readable as data."""
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
    assert re.search(r"^\s+json_diff\s", java, re.M)

    gradle = _help_for(["gradle", "--help"], capsys)
    assert re.search(r"^\s+modules\s", gradle, re.M)
    assert re.search(r"^\s+verify\s", gradle, re.M)
    assert re.search(r"^\s+tally\s", gradle, re.M)


def test_the_top_level_help_offers_only_the_grouped_surface(capsys):
    """The choice list argparse derives is the surface, since nothing sets a metavar.

    A hand-written metavar would assert only that a string was typed correctly;
    this asserts that these six are what is registered.
    """
    text = _help_for(["--help"], capsys)

    assert "{setup,serve,java,gradle,jitpack,branch}" in text
    for flat in FLAT:
        assert not re.search(rf"^\s+{flat}\s", text, re.M)


# --------------------------------------------------------------------------
# The MCP surface.
# --------------------------------------------------------------------------

def test_the_server_exposes_the_grouped_tool_names():
    assert _tool_names() == CURRENT_TOOLS


def test_no_tool_answers_to_a_second_name():
    """Nothing types an MCP tool name by hand, so one tool earns exactly one name."""
    assert _tool_names().isdisjoint(
        {"list_modules", "test_tally", "reorder_imports", "javadoc_normalize"})
