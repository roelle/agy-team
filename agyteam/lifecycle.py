"""Team lifecycle management: dynamic workspace grants, agent additions/removals,
and team stop/start controls. Pure standard library.

Provides Python API, CLI entrypoint (python -m agyteam.lifecycle), and admin primitives.
"""
import argparse
import datetime
import io
import json
import os
import shutil
import sys
import tarfile
import time
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
    status = {
        "team_dir": str(td),
        "stopped": stopped,
        "stop_reason": stop_reason,
        "workspaces": doc.get("workspaces", []),
        "agents": doc.get("agents", []),
    }
    try:
        from .git_hygiene import check_git_hygiene
        status["git_hygiene"] = check_git_hygiene()
    except Exception as e:
        status["git_hygiene"] = {
            "is_git": False,
            "clean": False,
            "error": str(e),
            "warnings": [f"Git hygiene check failed: {e}"],
        }
    return status


def _validate_tar_member(member: tarfile.TarInfo, dest_dir: Path) -> None:
    """Tar-slip and security validation for archive member before extraction.

    Raises ValueError on any traversal, absolute path, device file, or escaping link.
    """
    name = member.name
    if not name or "\x00" in name:
        raise ValueError(f"Tar-slip security violation: invalid member name: {name!r}")

    norm_name = name.replace("\\", "/")

    # 1. Absolute path check (Unix and Windows style)
    if os.path.isabs(name) or norm_name.startswith("/") or (len(name) >= 2 and name[1] == ":" and name[0].isalpha()):
        raise ValueError(f"Tar-slip security violation: member has absolute path: {name}")

    # 2. Path traversal '..' check
    parts = Path(norm_name).parts
    if ".." in parts:
        raise ValueError(f"Tar-slip security violation: member contains traversal '..': {name}")

    # 3. Path escaping destination directory
    try:
        dest_resolved = dest_dir.resolve()
        resolved_path = (dest_dir / norm_name).resolve()
        if not resolved_path.is_relative_to(dest_resolved):
            raise ValueError(f"Tar-slip security violation: path escapes destination: {name}")
    except Exception as e:
        raise ValueError(f"Tar-slip security violation in member path {name}: {e}")

    # 4. Device and FIFO files
    if member.isdev() or member.ischr() or member.isblk() or member.isfifo():
        raise ValueError(f"Tar-slip security violation: special device or fifo member: {name}")

    # 5. Symlinks and hardlinks
    if member.issym() or member.islnk():
        linkname = member.linkname
        if not linkname or "\x00" in linkname:
            raise ValueError(f"Tar-slip security violation: empty or invalid link target in {name}")
        norm_link = linkname.replace("\\", "/")
        if os.path.isabs(linkname) or norm_link.startswith("/") or (len(linkname) >= 2 and linkname[1] == ":" and linkname[0].isalpha()):
            raise ValueError(f"Tar-slip security violation: absolute link target {linkname} in {name}")
        link_parent = (dest_dir / norm_name).parent
        resolved_target = (link_parent / norm_link).resolve()
        if not resolved_target.is_relative_to(dest_resolved):
            raise ValueError(f"Tar-slip security violation: link target {linkname} escapes destination")


def export_team(output_path: str | Path,
                team_dir: str | Path | None = None,
                include_memory: bool = True,
                include_inbox: bool = True,
                include_observer: bool = True) -> dict:
    """Export team configuration, state, and agent memories to a portable tar bundle."""
    td = resolve_team_dir(team_dir)
    out_p = Path(output_path).expanduser().resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    roster_path = td / "roster.json"
    agents = []
    if roster_path.exists():
        try:
            doc = roster_lib.load(roster_path)
            agents = [a["name"] for a in doc.get("agents", []) if "name" in a]
        except Exception:
            pass

    files_to_add: dict[str, Path] = {}

    # Team root files and subdirectories (excluding agents memory and excluded components)
    if td.exists():
        for root, dirs, filenames in os.walk(td):
            root_p = Path(root)
            rel_dir = root_p.relative_to(td)
            # Skip agents directory from td walk (handled separately under include_memory)
            if "agents" in rel_dir.parts:
                continue
            # Skip inbox if excluded
            if not include_inbox and "inbox" in rel_dir.parts:
                continue
            for fn in filenames:
                fp = root_p / fn
                arcname = str(fp.relative_to(td))
                if arcname == "manifest.json":
                    continue
                if arcname == "bus.jsonl" and not include_inbox:
                    continue
                if arcname in ("observer.db", "observer.jsonl", "events.jsonl", "usage.jsonl") and not include_observer:
                    continue
                files_to_add[arcname] = fp

    # Agent memories
    if include_memory:
        candidate_agents = list(agents)
        # Also discover any agent folders under td / "agents" or td.parent / "agents"
        for search_base in (td / "agents", td.parent / "agents"):
            if search_base.is_dir():
                for entry in search_base.iterdir():
                    if entry.is_dir() and entry.name not in candidate_agents:
                        candidate_agents.append(entry.name)

        from . import scope
        try:
            current_scope = scope.load()
        except Exception:
            current_scope = None

        for agent in candidate_agents:
            agent_dir = None
            if (td / "agents" / agent).is_dir():
                agent_dir = td / "agents" / agent
            elif (td.parent / "agents" / agent).is_dir():
                agent_dir = td.parent / "agents" / agent
            elif current_scope:
                try:
                    ws = current_scope.agent_workspace(agent)
                    if ws.is_dir():
                        agent_dir = ws
                except Exception:
                    pass

            if agent_dir and agent_dir.is_dir():
                for root, _, filenames in os.walk(agent_dir):
                    for fn in filenames:
                        fp = Path(root) / fn
                        rel = fp.relative_to(agent_dir)
                        arcname = f"agents/{agent}/{rel}"
                        files_to_add[arcname] = fp

    components = ["roster"]
    if (td / "NORMS.md").exists():
        components.append("norms")
    if (td / "reviews.jsonl").exists():
        components.append("reviews")
    if include_inbox:
        components.append("inbox")
    if include_observer:
        components.append("observer")
    if include_memory:
        components.append("memory")

    # Build manifest
    manifest = {
        "format_version": "1.0",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "team_name": td.name,
        "agents": agents,
        "components": components,
        "file_count": len(files_to_add) + 1,
    }

    tar_mode = "w:gz" if str(out_p).endswith(".gz") or str(out_p).endswith(".tgz") else "w"
    with tarfile.open(out_p, tar_mode) as tar:
        # Add manifest.json
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
        tarinfo = tarfile.TarInfo(name="manifest.json")
        tarinfo.size = len(manifest_bytes)
        tarinfo.mtime = int(time.time())
        tarinfo.mode = 0o644
        tar.addfile(tarinfo, io.BytesIO(manifest_bytes))

        # Add all files sorted by arcname
        for arcname, src_path in sorted(files_to_add.items()):
            tar.add(src_path, arcname=arcname, recursive=False)

    return {
        "status": "ok",
        "action": "export_team",
        "output_path": str(out_p),
        "team_dir": str(td),
        "agents": manifest.get("agents", []),
        "components": manifest.get("components", []),
        "manifest": manifest,
        "file_count": manifest["file_count"],
    }


def import_team(bundle_path: str | Path,
                team_dir: str | Path | None = None,
                overwrite: bool = False) -> dict:
    """Import team configuration, state, and memories from a portable tar bundle."""
    td = resolve_team_dir(team_dir)
    bp = Path(bundle_path).expanduser().resolve()
    if not bp.is_file():
        raise FileNotFoundError(f"Bundle file not found: {bp}")

    if td.exists() and any(td.iterdir()):
        if not overwrite:
            raise FileExistsError(f"Destination team directory '{td}' exists and is not empty. Use overwrite=True to overwrite.")

    with tarfile.open(bp, "r:*") as tar:
        # Validate manifest presence
        try:
            manifest_info = tar.getmember("manifest.json")
        except KeyError:
            raise ValueError("Bundle archive is missing required manifest.json")

        f = tar.extractfile(manifest_info)
        if f is None:
            raise ValueError("Unable to read manifest.json from bundle archive")
        try:
            manifest = json.loads(f.read().decode("utf-8"))
        except Exception as e:
            raise ValueError(f"Malformed manifest.json in bundle archive: {e}")

        if not isinstance(manifest, dict) or "format_version" not in manifest:
            raise ValueError("Invalid manifest.json schema in bundle archive")

        total_members = len(tar.getmembers())

        # Tar-slip pre-extraction check
        for member in tar.getmembers():
            _validate_tar_member(member, td)

        td.mkdir(parents=True, exist_ok=True)
        if hasattr(tarfile, "data_filter"):
            tar.extractall(path=td, filter="data")
        else:
            tar.extractall(path=td)

    # If within standard hierarchy where td.name == 'team', mirror memories to td.parent / 'agents'
    extracted_agents = td / "agents"
    if extracted_agents.is_dir() and td.name == "team":
        durable_agents = td.parent / "agents"
        if durable_agents.resolve() != extracted_agents.resolve():
            def _safe_copy(src: str, dst: str) -> None:
                if not overwrite and os.path.lexists(dst):
                    return
                shutil.copy2(src, dst)

            shutil.copytree(extracted_agents, durable_agents, dirs_exist_ok=True, copy_function=_safe_copy)


    return {
        "status": "ok",
        "action": "import_team",
        "bundle_path": str(bp),
        "team_dir": str(td),
        "agents": manifest.get("agents", []),
        "components": manifest.get("components", []),
        "manifest": manifest,
        "file_count": manifest.get("file_count", total_members),
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
    p_add.add_argument("--principal", action="store_true", help="Mark agent as team principal")
    p_add.add_argument("--gatekeeper", action="store_true", help="Mark agent as review gatekeeper / QA")
    p_add.add_argument("--retro-leader", action="store_true", help="Mark agent as retrospective / TPM leader")

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

    # export
    p_export = subparsers.add_parser("export", help="Export team configuration, state, and memory")
    p_export.add_argument("output", help="Output bundle path (.tar.gz)")
    p_export.add_argument("--no-memory", action="store_true", help="Exclude agent memories")
    p_export.add_argument("--no-inbox", action="store_true", help="Exclude message bus and inboxes")
    p_export.add_argument("--no-observer", action="store_true", help="Exclude observer telemetry")

    # import
    p_import = subparsers.add_parser("import", help="Import team bundle")
    p_import.add_argument("bundle", help="Path to bundle archive (.tar.gz)")
    p_import.add_argument("--overwrite", action="store_true", help="Overwrite existing non-empty directory")

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
            extra = {}
            if getattr(args, "principal", False):
                extra["is_principal"] = True
            if getattr(args, "gatekeeper", False):
                extra["is_gatekeeper"] = True
            if getattr(args, "retro_leader", False):
                extra["is_retro_leader"] = True
            res = add_agent(args.name, role=args.role, workspaces=args.workspaces, team_dir=td, **extra)
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
                gh = res.get("git_hygiene")
                if gh and (gh.get("is_git") or gh.get("error")):
                    from .git_hygiene import format_hygiene_report
                    print()
                    print(format_hygiene_report(gh))
        elif args.command == "export":
            res = export_team(
                args.output,
                team_dir=td,
                include_memory=not args.no_memory,
                include_inbox=not args.no_inbox,
                include_observer=not args.no_observer,
            )
            print(f"[exported team to {res['output_path']} ({res['file_count']} files)]")
        elif args.command == "import":
            res = import_team(
                args.bundle,
                team_dir=td,
                overwrite=args.overwrite,
            )
            print(f"[imported team from {res['bundle_path']} ({res['file_count']} files)]")
        return 0
    except Exception as e:
        print(f"[error: {e}]", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
