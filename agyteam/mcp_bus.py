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
import json
import os
import sys
import time
from pathlib import Path

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
    tool("record_review",
         "Record a verification review of a feature, change, or task. "
         "Must specify what was reviewed, verdict ('approved' or 'changes_requested'), "
         "specific cases tried (cannot be empty), and findings.",
         {"what": string("What was reviewed (feature, branch, PR, or task)"),
          "verdict": string("Verdict: 'approved' or 'changes_requested'"),
          "cases_tried": string("Specific test cases and scenarios constructed and executed (required)"),
          "findings": string("Observations, issues found, or confirmation of behavior")},
         ["what", "verdict", "cases_tried"]),
    tool("list_reviews",
         "List durable verification reviews recorded for this team.",
         {}),
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


def _reviews_path(t: Transport) -> Path:
    if hasattr(t, "dir") and t.dir:
        return Path(t.dir) / "reviews.jsonl"
    env = os.environ.get("AGYTEAM_TEAM_DIR")
    if env:
        return Path(env) / "reviews.jsonl"
    from . import scope
    return scope.load().team_dir() / "reviews.jsonl"


def _record_review(t: Transport, a: dict) -> str:
    what = a.get("what", "")
    if not isinstance(what, str) or not what.strip():
        return "[error: what is required and cannot be empty]"
    what = what.strip()

    verdict = a.get("verdict", "")
    if verdict not in ("approved", "changes_requested"):
        return f"[error: verdict must be 'approved' or 'changes_requested', got {verdict!r}]"

    cases_tried = a.get("cases_tried")
    if cases_tried is None:
        return "[error: cases_tried is required and cannot be empty. A review must state what was attempted.]"
    if isinstance(cases_tried, str):
        if not cases_tried.strip():
            return "[error: cases_tried is required and cannot be empty. A review must state what was attempted.]"
        cases = cases_tried.strip()
    elif isinstance(cases_tried, list):
        cleaned = [str(c).strip() for c in cases_tried if str(c).strip()]
        if not cleaned:
            return "[error: cases_tried is required and cannot be empty. A review must state what was attempted.]"
        cases = cleaned
    else:
        return "[error: cases_tried is required and cannot be empty. A review must state what was attempted.]"

    findings = a.get("findings", "")
    findings_str = findings.strip() if isinstance(findings, str) else str(findings)

    rev_path = _reviews_path(t)
    rev_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "reviewer": t.me,
        "what": what,
        "verdict": verdict,
        "cases_tried": cases,
        "findings": findings_str,
    }
    with rev_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")

    return f"[review recorded: {verdict} for '{what}']"


def _list_reviews(t: Transport) -> str:
    rev_path = _reviews_path(t)
    if not rev_path.exists():
        return "[no reviews recorded]"
    try:
        content = rev_path.read_text().strip()
    except OSError as e:
        return f"[error reading reviews: {e}]"
    if not content:
        return "[no reviews recorded]"

    reviews = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            reviews.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not reviews:
        return "[no reviews recorded]"

    lines = ["# Review records", ""]
    for r in reviews:
        ts = r.get("ts", "unknown")
        reviewer = r.get("reviewer", "unknown")
        what = r.get("what", "unknown")
        verdict = r.get("verdict", "unknown")
        cases = r.get("cases_tried", [])
        findings = r.get("findings", "")

        lines.append(f"- [{ts}] {reviewer} -> {verdict}: {what}")
        if isinstance(cases, list):
            lines.append("  Cases tried:")
            for c in cases:
                lines.append(f"  * {c}")
        elif cases:
            lines.append(f"  Cases tried: {cases}")
        if findings:
            lines.append(f"  Findings: {findings}")
        lines.append("")

    return "\n".join(lines).rstrip()


def main(transport: Transport, admin: bool = False):
    handlers = {
        "send_to_teammate": lambda a: transport.send(a["to"], a["content"]),
        "broadcast": lambda a: transport.broadcast(a["content"]),
        "check_inbox": lambda a: _check_inbox(transport),
        "list_teammates": lambda a: _list_teammates(transport),
        "record_review": lambda a: _record_review(transport, a),
        "list_reviews": lambda a: _list_reviews(transport),
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
