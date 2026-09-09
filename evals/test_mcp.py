"""Memory-server and file-scope tests (no API cost).

A2A/bus behavior lives in test_transport.py, which runs the same contract
against every transport rather than just the file one.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rpc_util import PY, ROOT, check, rpc, text_of


def test_memory() -> tuple[int, int]:
    print("\n== memory MCP server ==")
    ws = ROOT / "evals" / "ws_mcp_unit"
    shutil.rmtree(ws, ignore_errors=True)
    r = rpc("clawagy.mcp_memory", [str(ws)], [
        ("save_memory", {"name": "deploy-host", "description": "where we deploy",
                         "content": "horta, ssh port 2222"}),
        ("memory_index", {}),
        ("read_memory", {"name": "deploy-host"}),
        ("read_memory", {"name": "no-such-memory"}),
        ("delete_memory", {"name": "deploy-host"}),
        ("memory_index", {}),
    ])
    init, tools, results = r[0], r[1], r[2:]
    return sum([
        check("initialize handshake",
              init["result"]["serverInfo"]["name"] == "clawagy-memory"),
        check("tools/list exposes 4 tools", len(tools["result"]["tools"]) == 4),
        check("save returns refreshed index", "deploy-host" in text_of(results[0])),
        check("index lists the memory", "deploy-host" in text_of(results[1])),
        check("read returns content", "2222" in text_of(results[2])),
        check("missing memory is honest, not invented",
              "no memory named" in text_of(results[3]).lower(), text_of(results[3])),
        check("delete works", "deleted" in text_of(results[4])),
        check("index empty after delete", "deploy-host" not in text_of(results[5])),
        check("memory file persisted to disk", (ws / "MEMORY.md").exists()),
    ]), 9


def test_roster_file() -> tuple[int, int]:
    """File-transport specifics: does roster.json survive edits intact?"""
    print("\n== roster.json persistence ==")
    td = ROOT / "evals" / "team_admin_unit"
    shutil.rmtree(td, ignore_errors=True)
    td.mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps(
        {"mission": "test", "agents": [{"name": "tpm", "role": "coordinates"}]}))
    env = {"CLAWAGY_AGENT": "tpm", "CLAWAGY_TEAM_DIR": str(td),
           "CLAWAGY_ROSTER_ADMIN": "1"}
    rpc("clawagy.mcp_bus", [], [("roster_add", {"name": "qa", "role": "reviews"})],
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
            if k not in ("CLAWAGY_PROJECT_DIR", "CLAWAGY_DURABLE_DIR")}

    def run_scope(cwd, env=None):
        return subprocess.run([str(PY), "-m", "clawagy.scope"], cwd=cwd,
                              env={**base, "PYTHONPATH": str(ROOT), **(env or {})},
                              capture_output=True, text=True, timeout=30).stdout

    out = run_scope(ROOT, {"CLAWAGY_DURABLE_DIR": "/tmp/clawagy-durable-test",
                           "CLAWAGY_PROJECT_DIR": str(ROOT)})

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
    plain = Path(tempfile.mkdtemp(prefix="clawagy-nogit-")).resolve()
    from_plain = run_scope(plain)

    return sum([
        check("durable honors env override", "/tmp/clawagy-durable-test" in out, out),
        check("project honors env override", str(ROOT) in out, out),
        check("shared defaults under project", ".clawagy-team" in out, out),
        check("git root found from nested subdir", f"{repo} [git root]" in from_sub,
              from_sub),
        check("worktree .git file treated as root", f"{wt} [git root]" in from_wt,
              from_wt),
        check("non-repo falls back to cwd", f"{plain} [not a git repo]" in from_plain,
              from_plain),
        check("shared follows the discovered git root",
              str(repo / ".clawagy-team") in from_sub, from_sub),
    ]), 7


if __name__ == "__main__":
    totals = [test_memory(), test_roster_file(), test_scopes()]
    got, want = sum(s for s, _ in totals), sum(t for _, t in totals)
    print(f"\n== memory/scope tests: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
