"""Team lifecycle management: dynamic workspace grants, agent additions/removals,
and team stop/start controls. Pure standard library.

Provides Python API, CLI entrypoint (python -m agyteam.lifecycle), and admin primitives.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import roster as roster_lib


def resolve_team_dir(team_dir: str | Path | None = None) -> Path:
    if team_dir:
        return Path(team_dir).expanduser().resolve()
    env = os.environ.get("AGYTEAM_TEAM_DIR")
    if env:
        return Path(env).expanduser().resolve()
    from . import scope
    return scope.load().team_dir()


def grant_workspace(workspace: str | Path, agent: str | None = None,
                    team_dir: str | Path | None = None) -> dict:
    """Grant a workspace directory path to the team or a specific agent."""
    td = resolve_team_dir(team_dir)
    td.mkdir(parents=True, exist_ok=True)
    roster_path = td / "roster.json"
    doc = roster_lib.load(roster_path)
    res_ws = str(Path(workspace).expanduser().resolve())
    if agent:
        found = False
        for a in doc.get("agents", []):
            if a["name"] == agent:
                ws_list = a.setdefault("workspaces", [])
                if res_ws not in ws_list:
                    ws_list.append(res_ws)
                found = True
                break
        if not found:
            raise ValueError(f"agent '{agent}' not found on roster")
    else:
        top_ws = doc.setdefault("workspaces", [])
        if res_ws not in top_ws:
            top_ws.append(res_ws)
    roster_lib.save(roster_path, doc)
    return {
        "status": "ok",
        "action": "grant_workspace",
        "workspace": res_ws,
        "agent": agent,
        "team_dir": str(td),
    }


def revoke_workspace(workspace: str | Path, agent: str | None = None,
                     team_dir: str | Path | None = None) -> dict:
    """Revoke a workspace directory path from the team or a specific agent."""
    td = resolve_team_dir(team_dir)
    roster_path = td / "roster.json"
    doc = roster_lib.load(roster_path)
    res_ws = str(Path(workspace).expanduser().resolve())
    raw_ws = str(workspace)
    if agent:
        found = False
        for a in doc.get("agents", []):
            if a["name"] == agent:
                if "workspaces" in a:
                    a["workspaces"] = [w for w in a["workspaces"] if w != res_ws and w != raw_ws]
                found = True
                break
        if not found:
            raise ValueError(f"agent '{agent}' not found on roster")
    else:
        if "workspaces" in doc:
            doc["workspaces"] = [w for w in doc["workspaces"] if w != res_ws and w != raw_ws]
    roster_lib.save(roster_path, doc)
    return {
        "status": "ok",
        "action": "revoke_workspace",
        "workspace": res_ws,
        "agent": agent,
        "team_dir": str(td),
    }


def add_agent(name: str, role: str = "", workspaces: list[str | Path] | None = None,
              team_dir: str | Path | None = None, **extra) -> dict:
    """Add an agent to the team roster."""
    td = resolve_team_dir(team_dir)
    td.mkdir(parents=True, exist_ok=True)
    roster_path = td / "roster.json"
    doc = roster_lib.load(roster_path)
    agents = doc.get("agents", [])
    if any(a["name"] == name for a in agents):
        raise ValueError(f"agent '{name}' is already on the roster")
    entry = {"name": name, "role": role}
    if workspaces:
        entry["workspaces"] = [str(Path(w).expanduser().resolve()) for w in workspaces]
    if extra:
        entry.update(extra)
    agents.append(entry)
    doc["agents"] = agents
    roster_lib.save(roster_path, doc)
    return {
        "status": "ok",
        "action": "add_agent",
        "name": name,
        "role": role,
        "team_dir": str(td),
    }


def remove_agent(name: str, team_dir: str | Path | None = None) -> dict:
    """Remove an agent from the team roster."""
    td = resolve_team_dir(team_dir)
    roster_path = td / "roster.json"
    doc = roster_lib.load(roster_path)
    agents = doc.get("agents", [])
    if not any(a["name"] == name for a in agents):
        raise ValueError(f"no teammate named '{name}'")
    doc["agents"] = [a for a in agents if a["name"] != name]
    roster_lib.save(roster_path, doc)
    return {
        "status": "ok",
        "action": "remove_agent",
        "name": name,
        "team_dir": str(td),
    }


def stop_team(reason: str = "", team_dir: str | Path | None = None) -> dict:
    """Stop the running team by writing the .stop sentinel."""
    td = resolve_team_dir(team_dir)
    td.mkdir(parents=True, exist_ok=True)
    stop_file = td / ".stop"
    reason_str = (reason or "stop requested via lifecycle").strip()
    stop_file.write_text(reason_str + "\n", encoding="utf-8")
    return {
        "status": "ok",
        "action": "stop_team",
        "reason": reason_str,
        "team_dir": str(td),
    }


def start_team(team_dir: str | Path | None = None) -> dict:
    """Clear .stop sentinel to allow team to run."""
    td = resolve_team_dir(team_dir)
    stop_file = td / ".stop"
    if stop_file.exists():
        try:
            stop_file.unlink()
        except OSError:
            pass
    return {
        "status": "ok",
        "action": "start_team",
        "team_dir": str(td),
    }


def team_status(team_dir: str | Path | None = None) -> dict:
    """Get current team configuration, agents, workspaces, and stop state."""
    td = resolve_team_dir(team_dir)
    stop_file = td / ".stop"
    stopped = stop_file.exists()
    stop_reason = None
    if stopped:
        try:
            stop_reason = stop_file.read_text(encoding="utf-8").strip()
        except OSError:
            stop_reason = ""
    roster_path = td / "roster.json"
    doc = roster_lib.load(roster_path)
    return {
        "team_dir": str(td),
        "stopped": stopped,
        "stop_reason": stop_reason,
        "workspaces": doc.get("workspaces", []),
        "agents": doc.get("agents", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agyteam.lifecycle",
        description="Dynamic team lifecycle and configuration management.",
    )
    parser.add_argument("--team-dir", help="Path to team directory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # grant-workspace
    p_grant = subparsers.add_parser("grant-workspace", help="Grant workspace to team or agent")
    p_grant.add_argument("workspace", help="Path to workspace directory")
    p_grant.add_argument("--agent", help="Optional agent name")

    # revoke-workspace
    p_revoke = subparsers.add_parser("revoke-workspace", help="Revoke workspace from team or agent")
    p_revoke.add_argument("workspace", help="Path to workspace directory")
    p_revoke.add_argument("--agent", help="Optional agent name")

    # add-agent
    p_add = subparsers.add_parser("add-agent", help="Add agent to roster")
    p_add.add_argument("name", help="Agent name")
    p_add.add_argument("--role", default="", help="Agent role")
    p_add.add_argument("--workspaces", nargs="*", default=None, help="Workspace paths")

    # remove-agent
    p_rm = subparsers.add_parser("remove-agent", help="Remove agent from roster")
    p_rm.add_argument("name", help="Agent name")

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop team execution")
    p_stop.add_argument("--reason", default="", help="Reason for stopping")

    # start
    subparsers.add_parser("start", help="Resume/start team execution")

    # status
    p_status = subparsers.add_parser("status", help="Show team status")
    p_status.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args(argv)
    td = args.team_dir

    try:
        if args.command == "grant-workspace":
            res = grant_workspace(args.workspace, agent=args.agent, team_dir=td)
            print(f"[granted workspace {res['workspace']}" + (f" to {res['agent']}]" if res.get('agent') else "]"))
        elif args.command == "revoke-workspace":
            res = revoke_workspace(args.workspace, agent=args.agent, team_dir=td)
            print(f"[revoked workspace {res['workspace']}" + (f" from {res['agent']}]" if res.get('agent') else "]"))
        elif args.command == "add-agent":
            res = add_agent(args.name, role=args.role, workspaces=args.workspaces, team_dir=td)
            print(f"[added agent {res['name']}]")
        elif args.command == "remove-agent":
            res = remove_agent(args.name, team_dir=td)
            print(f"[removed agent {res['name']}]")
        elif args.command == "stop":
            res = stop_team(reason=args.reason, team_dir=td)
            print(f"[team stopped: {res['reason']}]")
        elif args.command == "start":
            res = start_team(team_dir=td)
            print("[team started/unpaused]")
        elif args.command == "status":
            res = team_status(team_dir=td)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                st = "STOPPED" if res["stopped"] else "RUNNING"
                reason_extra = f" ({res['stop_reason']})" if res["stop_reason"] else ""
                print(f"Team: {res['team_dir']} [{st}{reason_extra}]")
                print(f"Workspaces: {', '.join(res['workspaces']) or 'none'}")
                print("Agents:")
                for a in res["agents"]:
                    aws = f" [workspaces: {', '.join(a.get('workspaces', []))}]" if a.get("workspaces") else ""
                    print(f"  - {a['name']}: {a.get('role', '')}{aws}")
        return 0
    except Exception as e:
        print(f"[error: {e}]", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
