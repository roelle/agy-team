"""MCP stdio server exposing one agent's durable memory.

Any MCP host can mount it — the agy CLI (plugin mcp_config.json), the
Antigravity hub (~/.gemini/antigravity/mcp_config.json), the google-antigravity
SDK (McpStdioServer), Claude Code.

Storage is pluggable (see agyteam/memory.py): the default keeps markdown files
in the agent's durable scope, and AGYTEAM_MEMORY_STORE swaps in any backend
without changing the agent-facing tools.

This module owns *presentation*; stores own storage. Every backend therefore
produces identical wording — including the honest miss, where a read of an
absent memory returns what does exist rather than letting the model guess.

Workspace: `python -m agyteam.mcp_memory <workspace_dir>` (explicit), or omit it
and set AGYTEAM_AGENT — the workspace resolves to that agent's durable scope, so
memory follows the agent between projects instead of being stranded in whichever
repo it happened to be working in.
"""
import os
import sys

from .mcp_base import serve, string, tool
from .memory import MemoryStore, normalize_name
from .memory import load as load_store

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


def render_index(store: MemoryStore) -> str:
    entries = store.index()
    if not entries:
        return "# Memory index\n(empty — you have not saved anything yet)"
    return "# Memory index\n" + "\n".join(
        f"- [{e.name}] {e.description}" for e in entries)


def main(store: MemoryStore):
    def save(a):
        name = normalize_name(a["name"])
        created = store.save(name, a["description"], a["content"])
        verb = "saved" if created else "updated"
        return f"[memory '{name}' {verb}]\nCurrent memory index:\n{render_index(store)}"

    def read(a):
        name = normalize_name(a["name"])
        content = store.read(name)
        if content is not None:
            return content
        have = ", ".join(e.name for e in store.index()) or "none"
        # Never fabricate on a miss: say so, and show what does exist.
        return f"[no memory named '{name}'. Existing memories: {have}]"

    def delete(a):
        name = normalize_name(a["name"])
        if not store.delete(name):
            return f"[no memory named '{name}']"
        return f"[memory '{name}' deleted]\nCurrent memory index:\n{render_index(store)}"

    handlers = {"save_memory": save, "read_memory": read,
                "delete_memory": delete,
                "memory_index": lambda a: render_index(store)}

    def dispatch(name, args):
        fn = handlers.get(name)
        return fn(args) if fn else f"[error: unknown tool '{name}']"

    try:
        serve(f"agy-team-memory:{store.agent}", TOOLS, dispatch)
    finally:
        store.close()


if __name__ == "__main__":
    if len(sys.argv) == 2:                 # explicit workspace (SDK path)
        os.environ.setdefault("AGYTEAM_WORKSPACE", sys.argv[1])
        agent = os.environ.get("AGYTEAM_AGENT") or "agent"
    else:
        agent = os.environ.get("AGYTEAM_AGENT", "")
        if not agent:
            sys.exit("agyteam.mcp_memory: pass <workspace_dir> or set AGYTEAM_AGENT")
    main(load_store(agent))
