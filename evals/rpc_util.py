"""Shared MCP stdio client helpers for the eval suites."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "bin" / "python"


def rpc(module: str, args: list[str], calls: list[tuple[str, dict]],
        env: dict | None = None) -> list[dict]:
    """Start a server, do the handshake, issue calls, return parsed responses.

    Responses are [initialize, tools/list, *call results] in order.
    """
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    for i, (name, a) in enumerate(calls):
        msgs.append({"jsonrpc": "2.0", "id": 10 + i, "method": "tools/call",
                     "params": {"name": name, "arguments": a}})
    proc = subprocess.run(
        [str(PY), "-m", module, *args], cwd=ROOT, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'evals'}", **(env or {})},
        input="\n".join(json.dumps(m) for m in msgs) + "\n", capture_output=True)
    if proc.returncode != 0:
        raise AssertionError(f"{module} exited {proc.returncode}: {proc.stderr}")
    return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]


def text_of(resp) -> str:
    return resp["result"]["content"][0]["text"]


def tool_names(resp) -> list[str]:
    return [t["name"] for t in resp["result"]["tools"]]


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'} {label}")
    if not ok and detail:
        print(f"       {detail[:400]}")
    return bool(ok)
