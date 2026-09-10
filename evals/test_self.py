"""Tests for agyteam.mcp_self (agent introspection server).

Exercises the server over real MCP JSON-RPC stdio.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rpc_util import PY, ROOT, check, rpc, text_of, tool_names


def test_self_server() -> tuple[int, int]:
    print("\n== self server: tool functionality and robustness ==")
    td = Path(tempfile.mkdtemp(prefix="agyteam-self-test-"))
    durable_dir = td / "alpha"
    team_dir = durable_dir / "team"
    team_dir.mkdir(parents=True)
    shared_dir = td / "shared"
    shared_dir.mkdir(parents=True)

    roster_data = {
        "mission": "Introspection test",
        "agents": [
            {"name": "alice", "role": "Architect", "tools_off": ["write_to_file"], "workers": False},
            {"name": "bob", "role": "Developer"}
        ]
    }
    (team_dir / "roster.json").write_text(json.dumps(roster_data))

    env = {
        "AGYTEAM_AGENT": "alice",
        "AGYTEAM_TEAM_DIR": str(team_dir),
        "AGYTEAM_DURABLE_DIR": str(durable_dir),
        "AGYTEAM_SHARED_DIR": str(shared_dir),
        "AGYTEAM_TEAM": "alpha",
    }

    # 1. Test when bus.jsonl and usage.jsonl are missing
    r_empty = rpc("agyteam.mcp_self", [], [
        ("whoami", {}),
        ("my_capabilities", {}),
        ("my_activity", {}),
    ], env=env)
    init, tools, res_whoami, res_cap, res_act_empty = r_empty[0], r_empty[1], r_empty[2], r_empty[3], r_empty[4]
    s_name = init["result"]["serverInfo"]["name"]
    t_list = tool_names(tools)
    t_whoami = text_of(res_whoami)
    t_cap = text_of(res_cap)
    t_act_empty = text_of(res_act_empty)

    # 2. Now populate bus.jsonl and usage.jsonl
    bus_entries = [
        {"ts": "2026-09-10 10:00:00", "from": "bob", "to": "alice", "content": "ping"},
        {"ts": "2026-09-10 10:01:00", "from": "alice", "to": "bob", "content": "pong"},
        {"ts": "2026-09-10 10:02:00", "from": "charlie", "to": "david", "content": "unrelated"},
        {"ts": "2026-09-10 10:03:00", "from": "bob", "to": "all", "content": "broadcast notice"},
    ]
    with (team_dir / "bus.jsonl").open("w") as f:
        for b in bus_entries:
            f.write(json.dumps(b) + "\n")

    usage_entries = [
        {"ts": "2026-09-10 10:00:30", "agent": "alice", "duration_s": 2.5, "input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        {"ts": "2026-09-10 10:01:30", "agent": "alice", "duration_s": 3.0, "input_tokens": 200, "output_tokens": 80, "total_tokens": 280},
        {"ts": "2026-09-10 10:02:30", "agent": "bob", "duration_s": 5.0, "input_tokens": 500, "output_tokens": 200, "total_tokens": 700},
    ]
    with (team_dir / "usage.jsonl").open("w") as f:
        for u in usage_entries:
            f.write(json.dumps(u) + "\n")

    r_populated = rpc("agyteam.mcp_self", [], [
        ("my_activity", {}),
    ], env=env)
    t_act_pop = text_of(r_populated[2])

    # 3. Test with positional CLI args (<team_dir> <agent_name>)
    r_cli = rpc("agyteam.mcp_self", [str(team_dir), "bob"], [
        ("whoami", {}),
        ("my_capabilities", {}),
    ], env={"AGYTEAM_DURABLE_DIR": str(durable_dir), "AGYTEAM_SHARED_DIR": str(shared_dir)})
    t_cli_whoami = text_of(r_cli[2])
    t_cli_cap = text_of(r_cli[3])

    checks = [
        check("server name matches agy-team-self:<agent>",
              s_name == "agy-team-self:alice", s_name),
        check("exposes exactly 3 tools",
              sorted(t_list) == ["my_activity", "my_capabilities", "whoami"], str(t_list)),
        check("whoami reports agent name and role",
              "alice" in t_whoami and "Architect" in t_whoami, t_whoami),
        check("whoami reports teammates excluding self",
              "bob" in t_whoami and "user" in t_whoami, t_whoami),
        check("whoami reports team name",
              "alpha" in t_whoami, t_whoami),
        check("whoami reports resolved paths",
              str(durable_dir) in t_whoami and str(team_dir) in t_whoami, t_whoami),
        check("my_capabilities reports disabled tools",
              "write_to_file" in t_cap, t_cap),
        check("my_capabilities reports worker setting",
              "False" in t_cap or "false" in t_cap or "disallowed" in t_cap.lower(), t_cap),
        check("my_capabilities states its limits honestly",
              "here is what your roster declares; I cannot see the harness's builtins from here" in t_cap, t_cap),
        check("my_activity survives missing/empty bus.jsonl and usage.jsonl",
              "missing or empty" in t_act_empty or "no" in t_act_empty.lower(), t_act_empty),
        check("my_activity includes messages addressed to or from agent or broadcast",
              "ping" in t_act_pop and "pong" in t_act_pop and "broadcast notice" in t_act_pop, t_act_pop),
        check("my_activity excludes messages between third parties",
              "unrelated" not in t_act_pop, t_act_pop),
        check("my_activity summarizes turns, duration, and token usage",
              "Turns:         2" in t_act_pop and "Total tokens:  430" in t_act_pop, t_act_pop),
        check("positional CLI args resolve identity correctly",
              "bob" in t_cli_whoami and "Developer" in t_cli_whoami, t_cli_whoami),
        check("unspecified capabilities report default",
              "none" in t_cli_cap.lower(), t_cli_cap),
    ]
    return sum(checks), len(checks)


def test_team_dir_override() -> tuple[int, int]:
    print("\n== self server: AGYTEAM_TEAM_DIR resolution ==")
    td = Path(tempfile.mkdtemp(prefix="agyteam-custom-team-"))
    custom_team_dir = td / "custom-team" / "team"
    custom_team_dir.mkdir(parents=True)
    custom_roster = {
        "mission": "Custom team mission",
        "agents": [
            {"name": "lead", "role": "Directs custom team"},
            {"name": "worker", "role": "Executes custom tasks"}
        ]
    }
    (custom_team_dir / "roster.json").write_text(json.dumps(custom_roster))

    base = {k: v for k, v in os.environ.items()
            if k not in ("AGYTEAM_AGENT", "AGYTEAM_TEAM_DIR", "AGYTEAM_TEAM", "AGYTEAM_DURABLE_DIR")}

    # Test with ONLY AGYTEAM_TEAM_DIR and AGYTEAM_AGENT set (no AGYTEAM_TEAM)
    env = {
        **base,
        "AGYTEAM_AGENT": "lead",
        "AGYTEAM_TEAM_DIR": str(custom_team_dir),
    }

    r = rpc("agyteam.mcp_self", [], [
        ("whoami", {}),
    ], env=env)
    t_whoami = text_of(r[2])

    # Also test CLI positional invocation: <team_dir> <agent_name>
    r_cli = rpc("agyteam.mcp_self", [str(custom_team_dir), "worker"], [
        ("whoami", {}),
    ], env=base)
    t_cli_whoami = text_of(r_cli[2])

    checks = [
        check("AGYTEAM_TEAM_DIR alone selects custom team name",
              "Team:     custom-team" in t_whoami, t_whoami),
        check("AGYTEAM_TEAM_DIR alone loads custom roster",
              "Directs custom team" in t_whoami and "worker" in t_whoami, t_whoami),
        check("paths reflect custom team directory",
              str(custom_team_dir) in t_whoami and str(custom_team_dir.parent) in t_whoami, t_whoami),
        check("CLI positional args select custom team name",
              "Team:     custom-team" in t_cli_whoami, t_cli_whoami),
        check("CLI positional args load custom roster for agent",
              "Executes custom tasks" in t_cli_whoami and "lead" in t_cli_whoami, t_cli_whoami),
    ]
    return sum(checks), len(checks)


def test_failure_modes() -> tuple[int, int]:
    print("\n== self server: failure modes ==")
    base = {k: v for k, v in os.environ.items()
            if k not in ("AGYTEAM_AGENT", "AGYTEAM_TEAM_DIR")}

    def run(args, env):
        p = subprocess.run([str(PY), "-m", "agyteam.mcp_self", *args], cwd=ROOT,
                           input="", capture_output=True, text=True, timeout=30,
                           env={**base, "PYTHONPATH": str(ROOT), **env})
        return p.returncode, (p.stderr or "") + (p.stdout or "")

    rc1, o1 = run([], {"AGYTEAM_AGENT": ""})
    rc2, o2 = run([], {})

    checks = [
        check("empty AGYTEAM_AGENT fails loudly",
              rc1 != 0 and "no agent identity" in o1, o1),
        check("missing AGYTEAM_AGENT and args fails loudly",
              rc2 != 0 and "no agent identity" in o2, o2),
    ]
    return sum(checks), len(checks)


if __name__ == "__main__":
    runs = [test_self_server(), test_team_dir_override(), test_failure_modes()]
    got, want = sum(s for s, _ in runs), sum(t for _, t in runs)
    print(f"\n== introspection tests: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
