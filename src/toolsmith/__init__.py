"""toolsmith - a project-scoped MCP server for the Simplified Java workspace.

Exposes the deterministic, high-frequency dev operations that an AI agent
otherwise hand-authors on every turn (the gradle noise-strip gate, the JUnit
result tally, the IntelliJ-faithful import reorder, the javadoc normalizer) as
typed MCP tools, so they become one cheap call instead of a re-derived shell
incantation.

The tool logic lives in sibling modules and is fully usable from the command
line as well - the MCP server is a thin typed veneer over them.
"""

__version__ = "0.1.0"
