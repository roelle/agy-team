"""MCP stdio server giving one agent self-introspection.

Exposes tools for an agent to answer:
  - what am I and where do I live? (whoami)
  - what can I do, and what are the limits of this observation? (my_capabilities)
  - what have I recently done on the bus and what is my usage? (my_activity)

Like agyteam/mcp_bus.py and agyteam/mcp_memory.py, this is pure Python standard
library, built on agyteam.mcp_base.serve.

Identity: `python -m agyteam.mcp_self <team_dir> <agent_name>` (explicit), or
set AGYTEAM_AGENT (agy CLI path). Refuses to start nameless rather than guessing.
"""
import json
import os
import sys
from pathlib import Path

from .mcp_base import serve, tool
from . import roster, scope

TOOLS = [
    tool("whoami",
         "Who you are and where you live: your name, role, team, teammates, and "
         "resolved paths for durable scope, workspace/memory, and shared files.",
         {}),
    tool("my_capabilities",
         "What you can and cannot do according to your team roster, including "
         "disabled tools, worker permissions, and the limits of what this server can observe.",
         {}),
    tool("my_activity",
         "Recent activity for this agent: recent messages sent and received on "
         "the team bus, and turn count and token usage.",
         {}),
]


def _resolve_team_dir() -> Path:
    env = os.environ.get("AGYTEAM_TEAM_DIR")
    if env:
        return Path(env).resolve()
    return scope.load().team_dir()


def _resolve_context(team_dir: Path | None = None) -> tuple[scope.Scopes, Path]:
    env = os.environ.get("AGYTEAM_TEAM_DIR")
    if env or team_dir is not None:
        td = team_dir or Path(env).resolve()
        scopes = scope.load(durable=td.parent, team=td.parent.name)
        return scopes, td
    scopes = scope.load()
    return scopes, scopes.team_dir()


def whoami(agent: str, scopes: scope.Scopes | None = None, team_dir: Path | None = None) -> str:
    if scopes is None or team_dir is None:
        scopes, team_dir = _resolve_context(team_dir)
    roster_path = team_dir / "roster.json"
    doc = roster.load(roster_path)
    agents = doc.get("agents", [])
    entry = next((a for a in agents if a["name"] == agent), None)
    role = entry.get("role", "") if entry else "[not listed on roster]"

    teammates = [
        f"- {a['name']}: {a.get('role', '')}"
        for a in agents if a["name"] != agent
    ]
    teammates.append("- user: the human you work for")

    lines = [
        f"Agent:    {agent}",
        f"Role:     {role}",
        f"Team:     {scopes.team}",
        "",
        "Teammates:",
        "\n".join(teammates),
        "",
        "Paths:",
        f"- durable:   {scopes.durable}",
        f"- workspace: {scopes.agent_workspace(agent)}",
        f"- memory:    {scopes.agent_workspace(agent) / 'memory'}",
        f"- shared:    {scopes.shared_dir()}",
        f"- team:      {team_dir}",
    ]
    return "\n".join(lines)


def my_capabilities(agent: str, scopes: scope.Scopes | None = None, team_dir: Path | None = None) -> str:
    if scopes is None or team_dir is None:
        scopes, team_dir = _resolve_context(team_dir)
    roster_path = team_dir / "roster.json"
    doc = roster.load(roster_path)
    agents = doc.get("agents", [])
    entry = next((a for a in agents if a["name"] == agent), None)

    lines = [
        f"Capabilities for '{agent}':",
        "",
        "here is what your roster declares; I cannot see the harness's builtins from here",
        "",
    ]
    if entry is None:
        lines.append(f"Roster declaration: agent '{agent}' not found in roster.json")
        return "\n".join(lines)

    tools_off = entry.get("tools_off")
    if tools_off:
        tools_str = ", ".join(tools_off) if isinstance(tools_off, list) else str(tools_off)
        lines.append(f"- disabled tools (tools_off): {tools_str}")
    else:
        lines.append("- disabled tools (tools_off): none (no tools disabled in roster)")

    workers = entry.get("workers")
    if workers is not None:
        lines.append(f"- subagent workers: {workers}")
    else:
        lines.append("- subagent workers: not declared in roster (default permitted)")

    extras = {k: v for k, v in entry.items() if k not in ("name", "role", "tools_off", "workers")}
    if extras:
        lines.append("- other roster attributes:")
        for k, v in sorted(extras.items()):
            lines.append(f"  {k}: {v}")

    lines.append("")
    lines.append("Note: The MCP introspection server can only report settings declared in your team roster; host-level builtin tools and harness configuration cannot be observed from here.")
    return "\n".join(lines)


def my_activity(agent: str, scopes: scope.Scopes | None = None, team_dir: Path | None = None, limit: int = 10) -> str:
    if scopes is None or team_dir is None:
        scopes, team_dir = _resolve_context(team_dir)
    bus_path = team_dir / "bus.jsonl"
    usage_path = team_dir / "usage.jsonl"

    sections = [f"Recent activity for '{agent}':", ""]

    # 1. Bus messages
    sections.append("## Recent bus messages")
    if not bus_path.exists() or not bus_path.read_text().strip():
        sections.append("(bus.jsonl is missing or empty — no bus messages recorded yet)")
    else:
        messages = []
        try:
            for line in bus_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                    sender = m.get("from")
                    recipient = m.get("to")
                    if sender == agent or recipient == agent or recipient == "all":
                        messages.append(m)
                except json.JSONDecodeError:
                    continue
        except OSError as e:
            sections.append(f"[error reading bus.jsonl: {e}]")
            messages = []

        if not messages:
            sections.append(f"(no messages sent or received by '{agent}')")
        else:
            # An uncapped history would flood the context window of the calling agent,
            # which is the exact problem this tool is designed to protect against.
            # We cap at the 10 most recent messages and truncate snippet lines at 77 chars.
            recent = messages[-limit:]
            for m in recent:
                ts = m.get("ts", "unknown time")
                sender = m.get("from", "?")
                recipient = m.get("to", "?")
                content = m.get("content", "").replace("\n", " ").strip()
                if len(content) > 80:
                    content = content[:77] + "..."
                sections.append(f"- [{ts}] {sender} -> {recipient}: {content}")
            if len(messages) > len(recent):
                sections.append(f"  (showing last {len(recent)} of {len(messages)} messages)")

    sections.append("")

    # 2. Usage summary
    sections.append("## Usage summary")
    if not usage_path.exists() or not usage_path.read_text().strip():
        sections.append("(usage.jsonl is missing or empty — no usage recorded yet)")
    else:
        agent_entries = []
        try:
            for line in usage_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("agent") == agent:
                        agent_entries.append(entry)
                except json.JSONDecodeError:
                    continue
        except OSError as e:
            sections.append(f"[error reading usage.jsonl: {e}]")
            agent_entries = []

        if not agent_entries:
            sections.append(f"(no usage entries recorded for '{agent}')")
        else:
            turns = len(agent_entries)
            total_duration = sum(e.get("duration_s", 0) for e in agent_entries)
            input_tokens = sum(e.get("input_tokens", 0) for e in agent_entries)
            output_tokens = sum(e.get("output_tokens", 0) for e in agent_entries)
            total_tokens = sum(e.get("total_tokens", 0) for e in agent_entries)
            cache_read_tokens = sum(e.get("cache_read_tokens", 0) for e in agent_entries)

            sections.append(f"- Turns:         {turns}")
            sections.append(f"- Total duration: {total_duration:.2f}s")
            sections.append(f"- Input tokens:  {input_tokens}")
            sections.append(f"- Output tokens: {output_tokens}")
            if cache_read_tokens:
                sections.append(f"- Cache read tokens: {cache_read_tokens}")
            sections.append(f"- Total tokens:  {total_tokens}")

    return "\n".join(sections)


def main(agent: str, team_dir: Path | None = None):
    scopes, td = _resolve_context(team_dir)
    handlers = {
        "whoami": lambda a: whoami(agent, scopes, td),
        "my_capabilities": lambda a: my_capabilities(agent, scopes, td),
        "my_activity": lambda a: my_activity(agent, scopes, td),
    }

    def dispatch(name, args):
        fn = handlers.get(name)
        return fn(args) if fn else f"[error: unknown tool '{name}']"

    serve(f"agy-team-self:{agent}", TOOLS, dispatch)


if __name__ == "__main__":
    td_arg = None
    if len(sys.argv) == 3:                      # <team_dir> <agent_name>
        td_arg = Path(sys.argv[1]).resolve()
        os.environ["AGYTEAM_TEAM_DIR"] = str(td_arg)
        me = sys.argv[2]
    elif len(sys.argv) == 2:                    # <agent_name>
        me = sys.argv[1]
    else:
        me = os.environ.get("AGYTEAM_AGENT", "")
    if not me:
        sys.exit("agyteam.mcp_self: no agent identity. Pass <team_dir> <agent_name> "
                 "or set AGYTEAM_AGENT (and optionally AGYTEAM_TEAM_DIR).")
    main(me, team_dir=td_arg)
