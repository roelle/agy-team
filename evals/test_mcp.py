"""Protocol + semantics tests for the memory and bus MCP servers (no API cost)."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "bin" / "python"


def rpc(module: str, args: list[str], calls: list[tuple[str, dict]]) -> list[dict]:
    """Run a server, issue initialize + tools/list + tool calls, return results."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    for i, (name, a) in enumerate(calls):
        msgs.append({"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call",
                     "params": {"name": name, "arguments": a}})
    proc = subprocess.run(
        [str(PY), "-m", module, *args], cwd=ROOT, text=True, timeout=60,
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


def test_scopes() -> tuple[int, int]:
    print("\n== file scope resolution ==")
    import os
    env = {**os.environ,
           "CLAWAGY_DURABLE_DIR": "/tmp/clawagy-durable-test",
           "CLAWAGY_PROJECT_DIR": str(ROOT)}
    out = subprocess.run([str(PY), "-m", "clawagy.scope"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=30).stdout
    score = sum([
        check("durable honors env override", "/tmp/clawagy-durable-test" in out, out),
        check("project honors env override", str(ROOT) in out, out),
        check("shared defaults under project", ".clawagy-team" in out, out),
    ])
    return score, 3


if __name__ == "__main__":
    totals = [test_memory(), test_bus(), test_scopes()]
    got, want = sum(s for s, _ in totals), sum(t for _, t in totals)
    print(f"\n== MCP/scope unit tests: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
