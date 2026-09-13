#!/usr/bin/env python3
"""Can the team read the answer key? Answer from the real grant list.

This replaces a documented one-liner that could not work:

    python -m agyteam.lifecycle status --json | grep -c bench   # want 0

`status --json` reports the TEAM-level workspace grants and, for each agent,
only a name and a role. Per-agent `workspaces` from roster.json never appear,
`AGYTEAM_EXTRA_WORKSPACES` never appears, and neither does the working
directory -- which `sdk_agent._workspaces()` grants unconditionally. So the
count was zero whether or not the keys were reachable: a check that passes
because it is blind, which is the exact defect class the bench measures.

    python3 bench/check_leak.py                    # check the default team
    python3 bench/check_leak.py --team build       # a named team
    python3 bench/check_leak.py --cwd /path/to/run # where you will launch from

Exit 0 if the keys are unreachable, 1 if any agent can read them, 2 if the
check could not be evaluated (never silently "clean").
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEYS = HERE / "keys"


def covers(grant: Path, target: Path) -> bool:
    try:
        grant = grant.expanduser().resolve()
    except OSError:
        return False
    return target == grant or grant in target.parents


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default=os.environ.get("AGYTEAM_TEAM", "default"))
    ap.add_argument("--teams-root",
                    default=os.environ.get("AGYTEAM_TEAMS_ROOT",
                                           str(Path.home() / "agy-teams")))
    ap.add_argument("--cwd", default=os.getcwd(),
                    help="directory the supervisor will be launched from; "
                         "granted to every agent unconditionally")
    a = ap.parse_args()

    if not KEYS.is_dir():
        print(f"CANNOT CHECK: no key directory at {KEYS}", file=sys.stderr)
        return 2
    keys = KEYS.resolve()

    roster_path = Path(a.teams_root) / a.team / "team" / "roster.json"
    if not roster_path.is_file():
        print(f"CANNOT CHECK: no roster at {roster_path}. Name the team with "
              "--team, or point --teams-root at where it lives.",
              file=sys.stderr)
        return 2
    try:
        roster = json.loads(roster_path.read_text())
    except (OSError, ValueError) as e:
        print(f"CANNOT CHECK: unreadable roster {roster_path}: {e}",
              file=sys.stderr)
        return 2

    agents = roster.get("agents") or []
    if isinstance(agents, dict):
        agents = [{"name": k, **(v if isinstance(v, dict) else {})}
                  for k, v in agents.items()]
    if not agents:
        print(f"CANNOT CHECK: roster {roster_path} lists no agents",
              file=sys.stderr)
        return 2

    env_extra = [p for p in os.environ.get("AGYTEAM_EXTRA_WORKSPACES", "")
                 .split(os.pathsep) if p.strip()]

    # Every grant an agent actually receives, mirroring _workspaces():
    # its own workspace, the CWD, the team dir, the env grant, and the
    # roster's per-agent list. The CWD is the one that bites -- it is granted
    # to everyone and is usually the repo, which is where these keys live.
    shared = [("working directory", a.cwd),
              ("team directory", str(Path(a.teams_root) / a.team))]
    shared += [("AGYTEAM_EXTRA_WORKSPACES", p) for p in env_extra]

    leaks = []
    for agent in agents:
        name = agent.get("name", "?") if isinstance(agent, dict) else str(agent)
        grants = list(shared)
        if isinstance(agent, dict):
            grants += [("roster workspaces", str(w))
                       for w in (agent.get("workspaces") or [])]
        for source, path in grants:
            if covers(Path(path), keys):
                leaks.append((name, source, path))

    print(f"answer keys: {keys}")
    print(f"roster:      {roster_path}")
    print(f"agents:      {', '.join(str(x.get('name', x)) for x in agents)}\n")

    if not leaks:
        print("CLEAN: no agent grant covers the answer keys.")
        print("Note this checks GRANTS, not the filesystem. An agent with a "
              "shell can still read what the OS allows; the audit log is the "
              "check on that path.")
        return 0

    print(f"LEAKED: {len(leaks)} grant(s) put the answer keys inside an "
          "agent's workspace.\n")
    for name, source, path in leaks:
        print(f"  {name:10s} via {source}: {path}")
    print("\nAny score from this team is worthless -- it may have read the "
          "answers. Move the keys outside the grant, or run the team from a "
          "directory that is not this repo.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
