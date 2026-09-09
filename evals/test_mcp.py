"""Protocol + semantics tests for the memory and bus MCP servers (no API cost)."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "bin" / "python"


def rpc(module: str, args: list[str], calls: list[tuple[str, dict]],
        env: dict | None = None) -> list[dict]:
    """Run a server, issue initialize + tools/list + tool calls, return results."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    for i, (name, a) in enumerate(calls):
        msgs.append({"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call",
                     "params": {"name": name, "arguments": a}})
    import os
    proc = subprocess.run(
        [str(PY), "-m", module, *args], cwd=ROOT, text=True, timeout=60,
        env={**os.environ, **(env or {})},
        input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True)
    if proc.returncode != 0:
        raise AssertionError(f"{module} exited {proc.returncode}: {proc.stderr}")
    return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]


def text_of(resp) -> str:
    return resp["result"]["content"][0]["text"]


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'} {label}")
    if not ok and detail:
        print(f"       {detail[:300]}")
    return ok


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
    init, tools = r[0], r[1]
    results = r[2:]
    score = sum([
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
    ])
    return score, 9


def test_bus() -> tuple[int, int]:
    print("\n== bus MCP server (A2A) ==")
    td = ROOT / "evals" / "team_mcp_unit"
    shutil.rmtree(td, ignore_errors=True)
    td.mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "coordinates"},
        {"name": "coder", "role": "implements"},
        {"name": "syseng", "role": "verifies"}]}))

    # tpm sends to coder and to a name that doesn't exist
    a = rpc("clawagy.mcp_bus", [str(td), "tpm"], [
        ("list_teammates", {}),
        ("send_to_teammate", {"to": "coder", "content": "please build X"}),
        ("send_to_teammate", {"to": "ghost", "content": "hello?"}),
        ("check_inbox", {}),
    ])[2:]
    # coder reads mail in a *separate process* — proves cross-process delivery
    b = rpc("clawagy.mcp_bus", [str(td), "coder"], [
        ("check_inbox", {}), ("check_inbox", {})])[2:]
    # broadcast reaches everyone but the sender
    c = rpc("clawagy.mcp_bus", [str(td), "syseng"], [
        ("broadcast", {"content": "standup in 5"})])[2:]
    d = rpc("clawagy.mcp_bus", [str(td), "tpm"], [("check_inbox", {})])[2:]

    score = sum([
        check("list_teammates excludes self, includes user",
              "coder" in text_of(a[0]) and "tpm" not in text_of(a[0])
              and "user" in text_of(a[0]), text_of(a[0])),
        check("send to real teammate delivers", "delivered to coder" in text_of(a[1])),
        check("send to unknown name errors, not silently drops",
              "no teammate named" in text_of(a[2]), text_of(a[2])),
        check("sender's own inbox unaffected", "[inbox empty]" == text_of(a[3])),
        check("recipient receives across processes",
              "please build X" in text_of(b[0]) and "from tpm" in text_of(b[0])),
        check("messages consumed once (no infinite redelivery)",
              text_of(b[1]) == "[inbox empty]", text_of(b[1])),
        check("broadcast names its recipients",
              "tpm" in text_of(c[0]) and "coder" in text_of(c[0])),
        check("broadcast excludes sender", "syseng" not in text_of(c[0])),
        check("broadcast lands in inbox", "standup in 5" in text_of(d[0])),
        check("bus.jsonl audit log written", (td / "bus.jsonl").exists()),
    ])
    return score, 10


def test_roster_admin() -> tuple[int, int]:
    print("\n== roster admin gating ==")
    td = ROOT / "evals" / "team_admin_unit"
    shutil.rmtree(td, ignore_errors=True)
    td.mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps(
        {"mission": "test", "agents": [{"name": "tpm", "role": "coordinates"}]}))

    # default (no admin env): mutation tools must not be offered or honored
    plain = rpc("clawagy.mcp_bus", [str(td), "tpm"],
                [("roster_add", {"name": "sneaky", "role": "self-added"})])
    names = [t["name"] for t in plain[1]["result"]["tools"]]
    after_plain = json.loads((td / "roster.json").read_text())["agents"]

    # admin session: mutation works and persists
    adm = rpc("clawagy.mcp_bus", [str(td), "tpm"], [
        ("roster_add", {"name": "qa", "role": "reviews deliverables"}),
        ("roster_add", {"name": "qa", "role": "duplicate attempt"}),
        ("roster_remove", {"name": "nobody"}),
        ("roster_remove", {"name": "qa"}),
    ], env={"CLAWAGY_ROSTER_ADMIN": "1"})
    adm_names = [t["name"] for t in adm[1]["result"]["tools"]]
    r = adm[2:]
    final = json.loads((td / "roster.json").read_text())

    score = sum([
        check("roster tools hidden without admin flag", "roster_add" not in names, str(names)),
        check("roster unchanged when agent tries anyway", len(after_plain) == 1),
        check("roster tools exposed with admin flag", "roster_add" in adm_names),
        check("add works", "added 'qa'" in text_of(r[0]), text_of(r[0])),
        check("duplicate add rejected", "already on the roster" in text_of(r[1])),
        check("removing unknown agent errors", "no teammate named" in text_of(r[2])),
        check("remove works", "removed 'qa'" in text_of(r[3])),
        check("mission preserved through edits", final.get("mission") == "test"),
    ])
    return score, 8


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

    # git root discovery: a repo with a nested subdir, invoked from the subdir
    repo = ROOT / "evals" / "gitroot_unit"
    shutil.rmtree(repo, ignore_errors=True)
    (repo / "sub" / "deeper").mkdir(parents=True)
    (repo / ".git").mkdir()
    from_sub = run_scope(repo / "sub" / "deeper")

    # worktree-style .git *file* rather than directory
    wt = ROOT / "evals" / "gitfile_unit"
    shutil.rmtree(wt, ignore_errors=True)
    (wt / "sub").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
    from_wt = run_scope(wt / "sub")

    # not a repo at all → falls back to cwd. Must live outside this repo,
    # otherwise walking up correctly finds *our* root and the test is bogus.
    plain = Path(tempfile.mkdtemp(prefix="clawagy-nogit-")).resolve()
    from_plain = run_scope(plain)

    score = sum([
        check("durable honors env override", "/tmp/clawagy-durable-test" in out, out),
        check("project honors env override", str(ROOT) in out, out),
        check("shared defaults under project", ".clawagy-team" in out, out),
        check("git root found from nested subdir",
              f"project (the repo/drive you are working in): {repo} [git root]"
              in from_sub, from_sub),
        check("worktree .git file treated as root",
              f"{wt} [git root]" in from_wt, from_wt),
        check("non-repo falls back to cwd",
              f"{plain} [not a git repo]" in from_plain, from_plain),
        check("shared follows the discovered git root",
              str(repo / ".clawagy-team") in from_sub, from_sub),
    ])
    return score, 7


if __name__ == "__main__":
    totals = [test_memory(), test_bus(), test_roster_admin(), test_scopes()]
    got, want = sum(s for s, _ in totals), sum(t for _, t in totals)
    print(f"\n== MCP/scope unit tests: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
