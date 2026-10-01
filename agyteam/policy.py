"""One answer to "may this agent make this tool call?", asked from three places.

Role policy used to be enforced in exactly one of them: the SDK runner, which
withholds a tool's schema for `tools_off`. Everywhere else the roster entry
was a description. On a host runtime the manager had every native tool,
assigned tasks to implementers directly, and could message anyone; an
integrator wrote six hundred lines of host hook to get back what the roster
already said. So the rule lives here, once, and is asked:

- by the SDK session builder, as a pre-tool-call hook;
- by `python -m agyteam.hook_pre_tool_use`, a generic hook for any host that
  can run a command before a tool call and read its exit code;
- inside the bus and task servers themselves, so what those servers do --
  who an agent may message, who may hand work to whom -- holds on a host
  with no hooks at all.

## What the roster can say

Per agent:

    "tools_off":        ["run_command", ...]   tool names this agent may not call
    "allowed_send_to":  ["manager"]            who it may message ("user" included
                                               only if listed); absent = anyone
    "assigns_tasks":    false                  may not create tasks for others or
                                               hand its own tasks to others
    "workspaces":       [...]                  with the team's, the only paths its
                                               file tools may touch; see below
    "confined":         false                  opt out of the path rule (a role
                                               that maintains the platform itself)

    "allowed_mcp_servers": ["agyteam_bus"]     on a host whose generic MCP call
                                               names the server in its arguments,
                                               the only servers it may call
    "refuse_paths":     ["/srv/checkout"]      paths its file tools may never touch,
                                               whatever the workspaces say

Team-wide, under "policy" (each also accepted per agent):

    "detach_commands":  ["make bench", "run_sim.*"]   regular expressions; a
                        shell command matching one is refused unless it is
                        already detached (nohup, setsid, or a trailing &). On
                        a hosted UI a foreground multi-hour command hangs the
                        turn to its timeout; sixteen such timeouts in one
                        episode.
    "forbidden_commands": [{"pattern": "launch_sim", "message": "the operator
                        starts sims"}]   refused outright, with that message
                        and no suggestion to detach. A plain string is a
                        pattern with a default message.
    "refuse_paths":     [...]                  as above, for everyone

## Paths

A native file tool on a path outside the agent's workspaces raises an
interactive permission dialog on a hosted UI, which nobody can click in a
background conversation, and the turn hangs until its timeout. The path rule
exists for that, and only applies where a workspace is declared for the agent
or the team: an agent with none declared is not confined by this file.
Allowed roots are the declared workspaces, the agent's own durable workspace,
and the team's shared directory. The team directory is never allowed -- it is
the record, and agents reach it through the servers.

`refuse_paths` is the other form of the same rule, for teams whose agents
legitimately roam: nothing is confined, but a named path -- a source checkout
the UI treats as out-of-workspace, a directory with answer keys -- is refused
wherever it is reached from. Both forms are checked on the same arguments.

The rule is applied to any tool whose name looks like a file operation, on
any string argument that resolves to an absolute path. That is a heuristic,
and a deliberate one: hosts name their file tools differently, and a false
refusal costs one tool call with a clear message, where a false allow costs a
hung turn.
"""
import re
from pathlib import Path

from . import roster as roster_lib

FILE_TOOL = re.compile(r"file|dir|path|read|write|edit|view|glob|grep|list|"
                       r"search|replace|create|delete|remove|move|rename|append|"
                       r"open|save|cat|ls|find", re.I)
SHELL_TOOL = re.compile(r"command|shell|bash|exec|terminal|run", re.I)
DETACHED = re.compile(r"\bnohup\b|\bsetsid\b|&\s*$|\bdisown\b")

#: Our own servers' tools: policy is applied to their *arguments* inside the
#: server, never to their names as file operations.
TEAM_TOOLS = {"send_to_teammate", "broadcast", "check_inbox", "list_teammates",
              "record_review", "list_reviews", "record_retro", "list_retros",
              "create_task", "claim_task", "update_task", "complete_task",
              "list_tasks", "save_memory", "read_memory", "delete_memory",
              "memory_index", "review_memory", "whoami", "my_capabilities",
              "my_activity"}


def _entry(roster: dict, agent: str) -> dict:
    for a in roster.get("agents", []):
        if a.get("name") == agent:
            return a
    return {}


def load_roster(team_dir: Path | str | None) -> dict:
    if team_dir is None:
        return {"agents": []}
    try:
        return roster_lib.load(Path(team_dir) / "roster.json")
    except Exception:
        return {"agents": []}


def allowed_roots(agent: str, roster: dict, team_dir: Path | str | None) -> list[Path] | None:
    """Where this agent's file tools may go, or None if it is not confined."""
    entry = _entry(roster, agent)
    if entry.get("confined") is False:
        return None
    declared = list(roster.get("workspaces", [])) + list(entry.get("workspaces", []))
    if not declared:
        return None
    roots = [Path(p).expanduser().resolve() for p in declared]
    try:
        from . import scope
        scopes = scope.load()
        roots.append(scopes.agent_workspace(agent).resolve())
        roots.append(scopes.shared_dir().resolve())
    except Exception:
        pass
    return roots


def _patterns(roster: dict, entry: dict, key: str) -> list:
    team = (roster.get("policy") or {}).get(key) or []
    mine = entry.get(key) or (entry.get("policy") or {}).get(key) or []
    return list(team) + list(mine)


def _matches(pattern: str, text: str) -> bool:
    """A regular expression, or a plain substring if it is not a valid one."""
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return pattern in text


def refused_paths(agent: str, roster: dict) -> list[Path]:
    entry = _entry(roster, agent)
    out = []
    for raw in _patterns(roster, entry, "refuse_paths"):
        try:
            out.append(Path(raw).expanduser().resolve())
        except OSError:
            continue
    return out


def _paths_in(args: dict) -> list[Path]:
    cwd = str((args or {}).get("Cwd") or (args or {}).get("cwd") or "")
    out = []
    for v in (args or {}).values():
        if not isinstance(v, str) or not v or len(v) > 4096 or "\n" in v:
            continue
        p = Path(v).expanduser()
        if not p.is_absolute():
            if not cwd:
                continue
            p = Path(cwd) / p
        try:
            out.append(p.resolve())
        except OSError:
            continue
    return out


def check_tool_policy(agent: str, tool: str, args: dict | None,
                      team_dir: Path | str | None = None,
                      roster: dict | None = None,
                      check_paths: bool = True) -> str | None:
    """A refusal the agent can act on, or None if the call is allowed.

    `check_paths=False` skips the workspace rule, for a runner that confines
    agents itself (the SDK) and whose idea of the allowed roots is wider.
    """
    roster = roster if roster is not None else load_roster(team_dir)
    entry = _entry(roster, agent)
    args = args or {}
    name = str(tool or "")

    if name in (entry.get("tools_off") or []):
        return (f"[refused: {name} is not available to {agent} on this team "
                f"(roster tools_off). Delegate it to a teammate who has it]")

    if name in ("send_to_teammate", "broadcast"):
        allowed = entry.get("allowed_send_to")
        if allowed is not None:
            to = args.get("to") if name == "send_to_teammate" else None
            if name == "broadcast":
                return (f"[refused: {agent} may only message "
                        f"{', '.join(allowed) or 'nobody'} (roster allowed_send_to); "
                        f"broadcast reaches everyone]")
            if to and to not in allowed:
                return (f"[refused: {agent} may only message "
                        f"{', '.join(allowed) or 'nobody'} (roster allowed_send_to), "
                        f"not '{to}'. Route it through them]")

    if name in ("create_task", "update_task", "claim_task") and entry.get("assigns_tasks") is False:
        owner = args.get("owner")
        if name == "create_task" and owner and owner != agent:
            return (f"[refused: {agent} does not assign tasks on this team "
                    f"(roster assigns_tasks: false). Create it for yourself, or "
                    f"ask the coordinator to assign it]")
        if name == "update_task" and owner and owner != agent:
            return (f"[refused: {agent} does not hand tasks to others on this "
                    f"team (roster assigns_tasks: false)]")

    allowed_servers = entry.get("allowed_mcp_servers")
    if allowed_servers is not None and name not in TEAM_TOOLS:
        # A host with one generic MCP-call tool names the server in the
        # arguments; that is the only place "which server" is visible.
        server = next((str(args[k]) for k in
                       ("ServerName", "server_name", "serverName", "server",
                        "mcp_server", "mcpServer") if args.get(k)), None)
        if server and server not in allowed_servers:
            return (f"[refused: {agent} may call only these MCP servers: "
                    f"{', '.join(allowed_servers) or 'none'} (roster "
                    f"allowed_mcp_servers), not '{server}']")

    if SHELL_TOOL.search(name) and name not in TEAM_TOOLS:
        cmd = str(args.get("command") or args.get("cmd") or args.get("CommandLine") or "")
        for rule in _patterns(roster, entry, "forbidden_commands"):
            pattern = rule.get("pattern", "") if isinstance(rule, dict) else str(rule)
            if pattern and _matches(pattern, cmd):
                why = (rule.get("message") if isinstance(rule, dict) else None) or \
                    "this team does not let agents run it"
                return f"[refused: commands matching '{pattern}' are not for you to run: {why}]"
        hit = next((p for p in _patterns(roster, entry, "detach_commands")
                    if p and _matches(p, cmd)), None)
        if hit and not DETACHED.search(cmd):
            return (f"[refused: commands matching '{hit}' run for hours and a "
                    f"foreground run hangs this turn until it times out. Run it "
                    f"detached (nohup ... > log 2>&1 &), then track it with a task "
                    f"reminder (update_task with check_after, or until=...)]")

    if check_paths and name not in TEAM_TOOLS and FILE_TOOL.search(name):
        paths = _paths_in(args)
        denied = refused_paths(agent, roster)
        for p in paths:
            hit = next((d for d in denied if p == d or d in p.parents), None)
            if hit:
                return (f"[refused: {p} is under {hit}, which this team's file "
                        f"tools may not touch (policy refuse_paths). Reach it "
                        f"another way or leave it alone]")
        roots = allowed_roots(agent, roster, team_dir)
        if roots is not None:
            td = Path(team_dir).expanduser().resolve() if team_dir else None
            for p in paths:
                inside = any(p == r or r in p.parents for r in roots)
                in_record = td is not None and (p == td or td in p.parents)
                if in_record or not inside:
                    return (f"[refused: {p} is outside {agent}'s workspaces "
                            f"({', '.join(str(r) for r in roots[:3])}"
                            f"{', ...' if len(roots) > 3 else ''}). On this host a "
                            f"file tool outside your workspace hangs the turn. Use "
                            f"the shell, or stage the file under a workspace]")
    return None
