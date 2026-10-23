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

from . import memory_log
from .mcp_base import serve, string, tool
from .memory import (MemoryStore, detect_conflicts, extract_provenance,
                     normalize_name, review_memories)
from .memory import load as load_store

TOOLS = [
    tool("save_memory",
         "Save a durable memory so future sessions of you know it: user "
         "preferences, corrections, facts about systems/projects, lessons "
         "learned. Call it the moment you learn something, not at session end. "
         "Saving under an existing name overwrites it — read first and merge if "
         "unsure. Returns the refreshed memory index, and notifies if the memory "
         "appears to overlap or conflict with an existing one.",
         {"name": string("Short kebab-case topic name, e.g. 'user-preferences'"),
          "description": string("One line for your index saying when this applies, e.g. "
                                "'Before approving a fix: run the real binary, not the "
                                "mocked test'. It is all you see when deciding what to read"),
          "content": string("The memory content in markdown"),
          "why": string("Why this was learned: the situation or trigger that produced "
                        "the lesson, so future sessions can judge if it is still true"),
          "when": string("When this was learned (timestamp, optional, defaults to now)")},
         ["name", "description", "content"]),
    tool("read_memory",
         "Read one of your memory files by name; lists available names on a miss.",
         {"name": string("Memory name from your index")}, ["name"]),
    tool("delete_memory", "Delete a memory that is wrong or obsolete. A copy is "
         "kept in the team's memory archive, so the operator can restore it.",
         {"name": string("Memory name")}, ["name"]),
    tool("merge_memories",
         "Replace several memories that say overlapping things with one. Write the "
         "merged content yourself, keeping every concrete detail of the sources "
         "(commands, names, numbers, the reason each was learned): a merge that "
         "turns specific lessons into a general one loses what made them useful. "
         "The sources are archived, and the merged memory keeps their history.",
         {"names": string("Comma-separated names of the memories to merge"),
          "into": string("Name of the merged memory: one of them, or a new name"),
          "description": string("One line for your index saying when this applies"),
          "content": string("The merged content in markdown"),
          "why": string("Why these were merged, and what each came from")},
         ["names", "into", "description", "content"]),
    tool("memory_index",
         "List everything you remember (name + one-line description each). "
         "Check this before claiming you don't know something.", {}),
    tool("review_memory",
         "Examines stored memories and reports items that deserve a second look: "
         "mutual contradictions, unknown vintage, and conditional lessons whose situation "
         "may have passed. Does not change or delete anything — reports findings so the "
         "agent can decide.",
         {}),
]


def render_index(store: MemoryStore) -> str:
    entries = store.index()
    if not entries:
        return "# Memory index\n(empty — you have not saved anything yet)"
    return "# Memory index\n" + "\n".join(
        f"- [{e.name}] {e.description}" for e in entries)


def handlers(store: MemoryStore, team=None) -> dict:
    """{tool name: handler(args) -> str} for one agent's store."""
    team = team if team is not None else memory_log.team_dir_here()

    def describe(name):
        return next((e.description for e in store.index() if e.name == name), "")

    def save(a):
        name = normalize_name(a["name"])
        why = a.get("why", "")
        when = a.get("when")

        # Check for contradictions or overlap against existing memories
        existing = []
        for e in store.index():
            if e.name != name:
                raw = store.read(e.name) or ""
                _, _, body = extract_provenance(raw)
                existing.append((e.name, e.description, body))
        conflicts = detect_conflicts(name, a["description"], a["content"], why, existing)

        created = store.save(name, a["description"], a["content"],
                             why=why, when=when)
        memory_log.record(team, store.agent, "save", name, new=bool(created))
        verb = "saved" if created else "updated"
        msg = f"[memory '{name}' {verb}]"
        if conflicts:
            conflict_lines = "\n".join(f"- '{c['name']}': {c['reason']}" for c in conflicts)
            msg += (
                f"\n\n[conflict warning: this memory appears to overlap or conflict with "
                f"existing memories]\n{conflict_lines}\n"
                f"Please review and reconcile if appropriate."
            )
        return f"{msg}\nCurrent memory index:\n{render_index(store)}"

    def read(a):
        name = normalize_name(a["name"])
        content = store.read(name)
        if content is not None:
            memory_log.record(team, store.agent, "read", name)
            return content
        have = ", ".join(e.name for e in store.index()) or "none"
        # Never fabricate on a miss: say so, and show what does exist.
        return f"[no memory named '{name}'. Existing memories: {have}]"

    def delete(a):
        name = normalize_name(a["name"])
        content = store.read(name)
        if content is None:
            return f"[no memory named '{name}']"
        memory_log.archive(team, store.agent, name, describe(name), content,
                           reason="deleted", by=store.agent)
        store.delete(name)
        memory_log.record(team, store.agent, "delete", name)
        return f"[memory '{name}' deleted (archived)]\nCurrent memory index:\n{render_index(store)}"

    def merge(a):
        names = [normalize_name(n) for n in str(a.get("names") or "").split(",") if n.strip()]
        into = normalize_name(a["into"])
        missing = [n for n in names if store.read(n) is None]
        if len(set(names)) < 2:
            return "[error: name at least two memories to merge]"
        if missing:
            return (f"[error: no memory named {', '.join(missing)}. Existing: "
                    f"{', '.join(e.name for e in store.index()) or 'none'}]")
        if not (a.get("content") or "").strip():
            return "[error: write the merged content; a merge never drops its sources unread]"
        for n in names:
            memory_log.archive(team, store.agent, n, describe(n), store.read(n) or "",
                               reason=f"merged into {into}", by=store.agent)
        store.save(into, a["description"], a["content"], why=a.get("why", "") or
                   f"merged from {', '.join(names)}")
        for n in names:
            if n != into:
                store.delete(n)
        memory_log.record(team, store.agent, "merge", into, sources=names)
        return (f"[merged {', '.join(names)} into '{into}'; the sources are archived]\n"
                f"Current memory index:\n{render_index(store)}")

    return {"save_memory": save, "read_memory": read,
            "delete_memory": delete, "merge_memories": merge,
            "memory_index": lambda a: render_index(store),
            "review_memory": lambda a: review_memories(store)}


def main(store: MemoryStore):
    table = handlers(store)

    def dispatch(name, args):
        fn = table.get(name)
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
