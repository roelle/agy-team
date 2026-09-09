"""MCP stdio server giving one agent peer-to-peer (A2A) messaging.

Antigravity has no native peer messaging — Teamwork coordinates through
workspace artifacts, and the SDK only offers subagents. This server supplies
the missing channel, and because it is MCP it works identically in the agy CLI
(plugin mcp_config.json), the desktop hub, and SDK agents.

Delivery is inbox-based rather than push: when the harness owns the agent loop
nobody can force a peer to take a turn, so peers leave mail and agents read it.
The teammate/worker distinction stays structural — teammates are *messaged*
through this server; workers are *spawned* by the harness and never appear here.

Identity: `python -m clawagy.mcp_bus <team_dir> <agent_name>` (SDK path, one
server per agent), or omit the arguments and set CLAWAGY_TEAM_DIR /
CLAWAGY_AGENT in the environment (agy CLI path, where mcp_config.json is static
and the session supplies identity). Refuses to start nameless rather than
guessing, so messages can never be filed under the wrong agent.
"""
import json
import os
import sys
import time
from pathlib import Path

from .mcp_base import serve, string, tool

TOOLS = [
    tool("send_to_teammate",
         "Send a message to a persistent teammate (a peer agent with its own "
         "memory and role) or to 'user'. Delivery is asynchronous: it lands in "
         "their inbox and they act on it when they next check. Include full "
         "context and a concrete ask — they cannot see your conversation.",
         {"to": string("Teammate name from list_teammates, or 'user'"),
          "content": string("The message: context, the ask, where to put results")},
         ["to", "content"]),
    tool("broadcast",
         "Send one message to every teammate at once. Use sparingly — for "
         "announcements, not for delegating work (delegate by name instead).",
         {"content": string("The announcement")}, ["content"]),
    tool("check_inbox",
         "Read and clear messages other agents have sent you. Check at the "
         "start of a task and again before reporting a task finished — a "
         "teammate may have answered your question while you were working.",
         {}),
    tool("list_teammates",
         "List your teammates (persistent peers you can message) and their "
         "roles. Workers you spawn yourself are not teammates and are not "
         "listed here.", {}),
]

# Roster mutation is off unless CLAWAGY_ROSTER_ADMIN=1. Team composition is the
# operator's call, not something an agent should do to itself mid-task.
ADMIN_TOOLS = [
    tool("roster_add",
         "Add a teammate to the roster. Takes effect for sessions started "
         "afterwards; existing sessions learn of them on their next start.",
         {"name": string("Short agent name, e.g. 'qa'"),
          "role": string("One-line description of what this agent owns")},
         ["name", "role"]),
    tool("roster_remove", "Remove a teammate from the roster.",
         {"name": string("Agent name to remove")}, ["name"]),
]


class Bus:
    """File-backed message bus: append-only log + per-agent inbox."""

    def __init__(self, team_dir: Path, me: str):
        self.dir = Path(team_dir)
        self.me = me
        self.inbox_dir = self.dir / "inbox"
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.log = self.dir / "bus.jsonl"
        self.roster_path = self.dir / "roster.json"

    def roster(self) -> list[dict]:
        if not self.roster_path.exists():
            return []
        try:
            return json.loads(self.roster_path.read_text()).get("agents", [])
        except json.JSONDecodeError:
            return []

    def _names(self) -> list[str]:
        return [a["name"] for a in self.roster()]

    def send(self, to: str, content: str) -> str:
        known = self._names()
        if to != "user" and known and to not in known:
            return (f"[error: no teammate named '{to}'. Teammates: "
                    f"{', '.join(known)}, user]")
        entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "from": self.me,
                 "to": to, "content": content}
        with self.log.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        if to != "user":
            with (self.inbox_dir / f"{to}.jsonl").open("a") as f:
                f.write(json.dumps(entry) + "\n")
        return (f"[delivered to {to}]" if to != "user" else
                "[delivered to the user — they will see it in the team log]")

    def broadcast(self, content: str) -> str:
        others = [n for n in self._names() if n != self.me]
        for n in others:
            self.send(n, content)
        return f"[broadcast to {', '.join(others) or 'nobody'}]"

    def check_inbox(self) -> str:
        path = self.inbox_dir / f"{self.me}.jsonl"
        if not path.exists() or not path.read_text().strip():
            return "[inbox empty]"
        lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        path.write_text("")          # messages are consumed on read
        return "\n\n".join(f"[{m['ts']}] from {m['from']}:\n{m['content']}"
                           for m in lines)

    def list_teammates(self) -> str:
        rows = [f"- {a['name']}: {a.get('role', '')}"
                for a in self.roster() if a["name"] != self.me]
        return "\n".join(rows + ["- user: the human you work for"]) \
            if rows else "- user: the human you work for"

    def _write_roster(self, agents: list[dict]) -> None:
        doc = {}
        if self.roster_path.exists():
            try:
                doc = json.loads(self.roster_path.read_text())
            except json.JSONDecodeError:
                doc = {}
        doc["agents"] = agents
        self.roster_path.write_text(json.dumps(doc, indent=2))

    def roster_add(self, name: str, role: str) -> str:
        agents = self.roster()
        if any(a["name"] == name for a in agents):
            return f"[error: '{name}' is already on the roster]"
        agents.append({"name": name, "role": role})
        self._write_roster(agents)
        return f"[added '{name}'. Roster: {', '.join(a['name'] for a in agents)}]"

    def roster_remove(self, name: str) -> str:
        agents = self.roster()
        if not any(a["name"] == name for a in agents):
            return f"[error: no teammate named '{name}']"
        self._write_roster([a for a in agents if a["name"] != name])
        left = ", ".join(a["name"] for a in self.roster()) or "nobody"
        return f"[removed '{name}'. Roster: {left}]"


def main(team_dir: str, me: str, admin: bool = False):
    bus = Bus(Path(team_dir), me)
    handlers = {"send_to_teammate": lambda a: bus.send(a["to"], a["content"]),
                "broadcast": lambda a: bus.broadcast(a["content"]),
                "check_inbox": lambda a: bus.check_inbox(),
                "list_teammates": lambda a: bus.list_teammates()}
    tools = list(TOOLS)
    if admin:
        tools += ADMIN_TOOLS
        handlers["roster_add"] = lambda a: bus.roster_add(a["name"], a["role"])
        handlers["roster_remove"] = lambda a: bus.roster_remove(a["name"])

    def dispatch(name, args):
        fn = handlers.get(name)
        return fn(args) if fn else f"[error: unknown tool '{name}']"

    serve(f"clawagy-bus:{me}", tools, dispatch)


if __name__ == "__main__":
    if len(sys.argv) == 3:
        team_dir, me = sys.argv[1], sys.argv[2]
    else:
        from . import scope
        team_dir = os.environ.get("CLAWAGY_TEAM_DIR") or str(scope.load().team_dir())
        me = os.environ.get("CLAWAGY_AGENT", "")
    if not me:
        sys.exit("clawagy.mcp_bus: no agent identity. Pass <team_dir> <agent_name> "
                 "or set CLAWAGY_AGENT (and optionally CLAWAGY_TEAM_DIR).")
    main(team_dir, me, admin=os.environ.get("CLAWAGY_ROSTER_ADMIN") == "1")
