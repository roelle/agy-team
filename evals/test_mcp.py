"""Memory-server and file-scope tests (no API cost).

A2A/bus behavior lives in test_transport.py, which runs the same contract
against every transport rather than just the file one.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Prevent outer agent process environment from bleeding into unit test scope and isolation tests
for _k in ("AGYTEAM_TEAM_DIR", "AGYTEAM_SHARED_DIR"):
    os.environ.pop(_k, None)

from rpc_util import PY, ROOT, check, rpc, text_of


def test_memory_invocation() -> tuple[int, int]:
    """Server wiring only — memory *semantics* live in test_memory.py, which
    runs the same contract against every store."""
    print("\n== memory server invocation paths ==")
    ws = ROOT / "evals" / "ws_mcp_unit"
    shutil.rmtree(ws, ignore_errors=True)

    # positional workspace (how the SDK path launches it)
    r = rpc("agyteam.mcp_memory", [str(ws)], [
        ("save_memory", {"name": "deploy-host", "description": "where we deploy",
                         "content": "horta, ssh port 2222"})])
    init, tools, saved = r[0], r[1], r[2]

    # env-derived workspace (how the plugin launches it)
    ws2 = ROOT / "evals" / "ws_mcp_env"
    shutil.rmtree(ws2, ignore_errors=True)
    ws2.mkdir(parents=True)
    env_run = rpc("agyteam.mcp_memory", [], [("memory_index", {})],
                  env={"AGYTEAM_AGENT": "alice", "AGYTEAM_WORKSPACE": str(ws2)})

    return sum([
        check("initialize handshake names the agent",
              init["result"]["serverInfo"]["name"].startswith("agy-team-memory"),
              init["result"]["serverInfo"]["name"]),
        check("tools/list exposes 5 tools", len(tools["result"]["tools"]) == 5),
        check("positional workspace writes there", (ws / "MEMORY.md").exists()),
        check("save returns the refreshed index", "deploy-host" in text_of(saved)),
        check("env-derived workspace starts and answers",
              "Memory index" in text_of(env_run[2]), text_of(env_run[2])),
    ]), 5


def test_roster_file() -> tuple[int, int]:
    """File-transport specifics: does roster.json survive edits intact?"""
    print("\n== roster.json persistence ==")
    td = ROOT / "evals" / "team_admin_unit"
    shutil.rmtree(td, ignore_errors=True)
    td.mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps(
        {"mission": "test", "agents": [{"name": "tpm", "role": "coordinates"}]}))
    env = {"AGYTEAM_AGENT": "tpm", "AGYTEAM_TEAM_DIR": str(td),
           "AGYTEAM_ROSTER_ADMIN": "1"}
    rpc("agyteam.mcp_bus", [], [("roster_add", {"name": "qa", "role": "reviews"})],
        env=env)
    doc = json.loads((td / "roster.json").read_text())
    return sum([
        check("addition persisted to roster.json",
              any(a["name"] == "qa" for a in doc["agents"]), str(doc)),
        check("unrelated keys (mission) preserved", doc.get("mission") == "test"),
        check("existing agents preserved",
              any(a["name"] == "tpm" for a in doc["agents"])),
    ]), 3


def test_scopes() -> tuple[int, int]:
    print("\n== file scope resolution ==")
    import os
    base = {k: v for k, v in os.environ.items()
            if k not in ("AGYTEAM_PROJECT_DIR", "AGYTEAM_DURABLE_DIR")}

    def run_scope(cwd, env=None):
        return subprocess.run([str(PY), "-m", "agyteam.scope"], cwd=cwd,
                              env={**base, "PYTHONPATH": str(ROOT), **(env or {})},
                              capture_output=True, text=True, timeout=30).stdout

    out = run_scope(ROOT, {"AGYTEAM_DURABLE_DIR": "/tmp/agyteam-durable-test",
                           "AGYTEAM_PROJECT_DIR": str(ROOT)})

    repo = ROOT / "evals" / "gitroot_unit"
    shutil.rmtree(repo, ignore_errors=True)
    (repo / "sub" / "deeper").mkdir(parents=True)
    (repo / ".git").mkdir()
    from_sub = run_scope(repo / "sub" / "deeper")

    wt = ROOT / "evals" / "gitfile_unit"
    shutil.rmtree(wt, ignore_errors=True)
    (wt / "sub").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
    from_wt = run_scope(wt / "sub")

    # Must live outside this repo, else walking up correctly finds *our* root
    # and the test proves nothing.
    plain = Path(tempfile.mkdtemp(prefix="agyteam-nogit-")).resolve()
    from_plain = run_scope(plain)

    return sum([
        check("durable honors env override", "/tmp/agyteam-durable-test" in out, out),
        check("project honors env override", str(ROOT) in out, out),
        check("shared defaults under project", ".agy-team-shared" in out, out),
        check("git root found from nested subdir", f"{repo} [git root]" in from_sub,
              from_sub),
        check("worktree .git file treated as root", f"{wt} [git root]" in from_wt,
              from_wt),
        check("non-repo falls back to cwd", f"{plain} [not a git repo]" in from_plain,
              from_plain),
        check("shared follows the discovered git root",
              str(repo / ".agy-team-shared") in from_sub, from_sub),
    ]), 7


def test_roster_shapes() -> tuple[int, int]:
    """Hand-written rosters come in several reasonable shapes; accept them all,
    and fail readably on the ones that are genuinely ambiguous."""
    print("\n== roster shapes and migration ==")
    sys.path.insert(0, str(ROOT))
    from agyteam.roster import RosterError, normalize_agents

    def names(raw):
        return [a["name"] for a in normalize_agents(raw)]

    def rejects(raw, fragment):
        try:
            normalize_agents(raw)
            return False, "accepted something malformed"
        except RosterError as e:
            return fragment in str(e), str(e)

    # the exact shape that crashed: mapping with redundant "name" keys
    old_style = {"tpm": {"name": "tpm", "role": "coordinates"},
                 "coder": {"name": "coder", "role": "implements"}}
    ok_dup, msg_dup = rejects([{"name": "tpm"}, {"name": "tpm"}], "duplicate")
    ok_conflict, msg_con = rejects({"tpm": {"name": "other"}}, "disagrees")
    ok_noname, msg_non = rejects([{"role": "x"}], 'no "name"')
    ok_type, msg_type = rejects("tpm,coder", "must be a list or an object")
    ok_reserved, msg_res = rejects([{"name": "user"}], "reserved")

    # end-to-end: the crashing roster now works through the live MCP server
    td = ROOT / "evals" / "team_shapes"
    shutil.rmtree(td, ignore_errors=True)
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"mission": "m", "agents": old_style}))
    env = {"AGYTEAM_AGENT": "tpm", "AGYTEAM_TEAM_DIR": str(td)}
    listed = text_of(rpc("agyteam.mcp_bus", [], [("list_teammates", {})], env=env)[2])
    rpc("agyteam.mcp_bus", [], [("roster_add", {"name": "qa", "role": "reviews"})],
        env={**env, "AGYTEAM_ROSTER_ADMIN": "1"})
    migrated = json.loads((td / "roster.json").read_text())

    return sum([
        check("mapping of name -> object (the shape that crashed)",
              names(old_style) == ["tpm", "coder"]),
        check("mapping of name -> role string",
              names({"tpm": "coordinates"}) == ["tpm"]),
        check("canonical list of objects",
              names([{"name": "tpm", "role": "r"}]) == ["tpm"]),
        check("list of bare name strings", names(["tpm", "coder"]) == ["tpm", "coder"]),
        check("missing agents key is empty, not an error", names(None) == []),
        check("per-agent extras (model, tools_off) survive normalisation",
              normalize_agents({"tpm": {"role": "r", "model": "m",
                                        "tools_off": ["run_command"]}})[0]["model"] == "m"),
        check("duplicate names rejected", ok_dup, msg_dup),
        check("key/name conflict rejected as ambiguous", ok_conflict, msg_con),
        check("list entry without a name rejected", ok_noname, msg_non),
        check("wrong type rejected with a readable message", ok_type, msg_type),
        check("'user' rejected as a roster name", ok_reserved, msg_res),
        check("live server reads the old mapping roster",
              "coder" in listed and "[error:" not in listed, listed),
        check("editing migrates the file to canonical list form",
              isinstance(migrated["agents"], list)
              and {a["name"] for a in migrated["agents"]} == {"tpm", "coder", "qa"},
              str(migrated)),
        check("migration preserves unrelated keys", migrated.get("mission") == "m"),
    ]), 14


def test_stdlib_purity() -> tuple[int, int]:
    """The MCP servers must run under a bare system python, no venv.

    That property is what makes the agy plugin installable without vendoring
    third-party packages, so it is worth a test rather than a comment.
    """
    print("\n== stdlib purity (system python, no virtualenv) ==")
    sys_py = "/usr/bin/python3"
    if not Path(sys_py).exists():
        return check("system python present", False, f"{sys_py} missing"), 1

    def imports(mod, path):
        p = subprocess.run([sys_py, "-c", f"import {mod}"], cwd="/",
                           env={"PYTHONPATH": path}, capture_output=True,
                           text=True, timeout=60)
        return p.returncode == 0, p.stderr

    checks = []
    for mod in ("agyteam.mcp_memory", "agyteam.mcp_bus", "agyteam.mcp_self", "agyteam.scope",
                "agyteam.transport_file", "agyteam.store"):
        ok, err = imports(mod, str(ROOT))
        checks.append(check(f"{mod} imports with no third-party deps", ok, err))

    # the venv-only modules SHOULD fail here — proves the test has teeth
    ok, _ = imports("agyteam.sdk_agent", str(ROOT))
    checks.append(check("control: agyteam.sdk_agent (needs google-antigravity) does NOT import",
                        not ok, "sdk_agent.py imported on bare python — purity test is "
                                "not actually discriminating"))
    return sum(checks), len(checks)


def test_team_isolation() -> tuple[int, int]:
    """Separate teams must share no roster, no bus, and no memory."""
    print("\n== team namespacing ==")
    root = Path(tempfile.mkdtemp(prefix="agyteam-teams-"))
    base = {"AGYTEAM_TEAMS_ROOT": str(root), "PYTHONPATH": str(ROOT),
            "AGYTEAM_TEAM_DIR": "", "AGYTEAM_DURABLE_DIR": ""}

    for team in ("alpha", "beta"):
        d = root / team / "team"
        (d / "inbox").mkdir(parents=True)
        (d / "roster.json").write_text(json.dumps({"agents": [
            {"name": "tpm", "role": "coordinates"},
            {"name": "coder", "role": "implements"}]}))

    def send(team, to, content):
        return rpc("agyteam.mcp_bus", [], [("send_to_teammate",
                                            {"to": to, "content": content})],
                   env={**base, "AGYTEAM_TEAM": team, "AGYTEAM_AGENT": "tpm"})[2:]

    def inbox(team, agent):
        return text_of(rpc("agyteam.mcp_bus", [], [("check_inbox", {})],
                           env={**base, "AGYTEAM_TEAM": team,
                                "AGYTEAM_AGENT": agent})[2])

    send("alpha", "coder", "alpha-only-secret")
    beta_inbox = inbox("beta", "coder")
    alpha_inbox = inbox("alpha", "coder")

    # explicit durable path should override the root/team composition entirely
    exact = Path(tempfile.mkdtemp(prefix="agyteam-exact-"))
    out = subprocess.run([str(PY), "-m", "agyteam.scope"], cwd=ROOT,
                         env={**base, "AGYTEAM_TEAM": "alpha",
                              "AGYTEAM_DURABLE_DIR": str(exact)},
                         capture_output=True, text=True, timeout=30).stdout
    bad_name = subprocess.run([str(PY), "-m", "agyteam.scope"], cwd=ROOT,
                              env={**base, "AGYTEAM_TEAM": "../escape"},
                              capture_output=True, text=True, timeout=30).stdout

    return sum([
        check("message delivered within its own team",
              "alpha-only-secret" in alpha_inbox, alpha_inbox),
        check("other team cannot see it", beta_inbox == "[inbox empty]", beta_inbox),
        check("each team gets its own durable dir",
              (root / "alpha").exists() and (root / "beta").exists()),
        check("explicit durable path overrides team composition",
              str(exact) in out and str(root / "alpha") not in out, out),
        check("malicious team name cannot escape the teams root",
              f"{root}/escape" in bad_name and ".." not in bad_name, bad_name),
    ]), 5


if __name__ == "__main__":
    totals = [test_memory_invocation(), test_roster_file(), test_roster_shapes(), test_scopes(),
              test_stdlib_purity(), test_team_isolation()]
    got, want = sum(s for s, _ in totals), sum(t for _, t in totals)
    print(f"\n== memory/scope tests: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
