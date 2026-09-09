"""MCP stdio server exposing one agent's durable memory.

Any MCP host can mount the same workspace memory the CLI/team agents use — the
agy CLI (plugin mcp_config.json), the Antigravity hub
(~/.gemini/antigravity/mcp_config.json), the google-antigravity SDK
(McpStdioServer), Claude Code, etc.

Workspace: `python -m clawagy.mcp_memory <workspace_dir>` (explicit), or omit it
and set CLAWAGY_AGENT — the workspace is then resolved to the agent's *durable*
scope (see clawagy.scope), so memory follows the agent between projects instead
of being stranded in whichever repo it happened to be working in.
"""
import os
import sys
from pathlib import Path

from .mcp_base import serve, string, tool
from .tools import Toolbox

TOOLS = [
    tool("save_memory",
         "Save a durable memory so future sessions of you know it: user "
         "preferences, corrections, facts about systems/projects, lessons "
         "learned. Call it the moment you learn something, not at session end. "
         "Saving under an existing name overwrites it — read first and merge if "
         "unsure. Returns the refreshed memory index.",
         {"name": string("Short kebab-case topic name, e.g. 'user-preferences'"),
          "description": string("One line for your index"),
          "content": string("The memory content in markdown")},
         ["name", "description", "content"]),
    tool("read_memory",
         "Read one of your memory files by name; lists available names on a miss.",
         {"name": string("Memory name from your index")}, ["name"]),
    tool("delete_memory", "Delete a memory that is wrong or obsolete.",
         {"name": string("Memory name")}, ["name"]),
    tool("memory_index",
         "List everything you remember (name + one-line description each). "
         "Check this before claiming you don't know something.", {}),
]


def main(workspace: str):
    box = Toolbox(Path(workspace))
    index = lambda: (box.workspace / "MEMORY.md").read_text()
    handlers = {
        "save_memory": lambda a: f"{box.save_memory(**a)}\nCurrent memory index:\n{index()}",
        "read_memory": lambda a: box.read_memory(**a),
        "delete_memory": lambda a: f"{box.delete_memory(**a)}\nCurrent memory index:\n{index()}",
        "memory_index": lambda a: index(),
    }

    def dispatch(name, args):
        fn = handlers.get(name)
        return fn(args) if fn else f"[error: unknown tool '{name}']"

    serve("clawagy-memory", TOOLS, dispatch)


if __name__ == "__main__":
    if len(sys.argv) == 2:
        main(sys.argv[1])
    else:
        from . import scope
        agent = os.environ.get("CLAWAGY_AGENT", "")
        if not agent:
            sys.exit("clawagy.mcp_memory: pass <workspace_dir> or set CLAWAGY_AGENT")
        main(str(scope.load().agent_workspace(agent)))
