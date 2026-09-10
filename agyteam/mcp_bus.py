"""MCP stdio server giving one agent peer-to-peer (A2A) messaging.

Antigravity exposes no native peer messaging publicly — Teamwork coordinates
through workspace artifacts, and the SDK offers only subagents. This server
supplies the channel, and because it is MCP it works identically in the agy CLI
(plugin mcp_config.json), the desktop hub, and SDK agents.

The wire is pluggable (see agyteam/transport.py): the default file bus works
anywhere agents share a filesystem, and AGYTEAM_BUS_TRANSPORT swaps in a native
implementation without changing the agent-facing tools.

Delivery is inbox-based rather than push: when the harness owns the agent loop
nobody can force a peer to take a turn, so peers leave mail and agents read it.
The teammate/worker distinction stays structural — teammates are *messaged*
through this server; workers are *spawned* by the harness and never appear here.

Identity: `python -m agyteam.mcp_bus <team_dir> <agent_name>` (SDK path), or
omit the arguments and set AGYTEAM_AGENT (agy CLI path, where mcp_config.json is
static and the session supplies identity). Refuses to start nameless rather than
guessing, so messages can never be filed under the wrong agent.
"""
import os
import sys

from .mcp_base import serve, string, tool
from .transport import Transport, load

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

# Roster mutation is off unless AGYTEAM_ROSTER_ADMIN=1 *and* the transport
# supports it. Team composition is the operator's call, not something an agent
# should do to itself mid-task.
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


def _list_teammates(t: Transport) -> str:
    rows = [f"- {a['name']}: {a.get('role', '')}" for a in t.teammates()]
    return "\n".join(rows + ["- user: the human you work for"])


def _check_inbox(t: Transport) -> str:
    msgs = t.fetch()
    return "\n\n".join(m.render() for m in msgs) if msgs else "[inbox empty]"


def main(transport: Transport, admin: bool = False):
    handlers = {
        "send_to_teammate": lambda a: transport.send(a["to"], a["content"]),
        "broadcast": lambda a: transport.broadcast(a["content"]),
        "check_inbox": lambda a: _check_inbox(transport),
        "list_teammates": lambda a: _list_teammates(transport),
    }
    tools = list(TOOLS)
    if admin and transport.supports_roster_admin:
        tools += ADMIN_TOOLS
        handlers["roster_add"] = lambda a: transport.roster_add(a["name"], a["role"])
        handlers["roster_remove"] = lambda a: transport.roster_remove(a["name"])

    def dispatch(name, args):
        fn = handlers.get(name)
        return fn(args) if fn else f"[error: unknown tool '{name}']"

    try:
        serve(f"agy-team-bus:{transport.me}", tools, dispatch)
    finally:
        transport.close()


if __name__ == "__main__":
    if len(sys.argv) == 3:                      # <team_dir> <agent_name>
        os.environ.setdefault("AGYTEAM_TEAM_DIR", sys.argv[1])
        me = sys.argv[2]
    else:
        me = os.environ.get("AGYTEAM_AGENT", "")
    if not me:
        sys.exit("agyteam.mcp_bus: no agent identity. Pass <team_dir> <agent_name> "
                 "or set AGYTEAM_AGENT (and optionally AGYTEAM_TEAM_DIR).")
    main(load(me), admin=os.environ.get("AGYTEAM_ROSTER_ADMIN") == "1")
