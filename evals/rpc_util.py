"""Shared MCP stdio client helpers for the eval suites."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Prefer the repo venv, but fall back to whatever interpreter is running these
# tests: the offline contract suites are stdlib-only by design, so system
# python or a foreign virtualenv must not turn into a FileNotFoundError.
PY = ROOT / ".venv" / "bin" / "python"
if not PY.exists():
    PY = Path(sys.executable)


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
        # PREPEND, never replace. Replacing meant the conformance suite could
        # only ever import a transport or store that lives in this repo, so
        # the one deployment that matters -- a bundle installed elsewhere by
        # plugin/install.sh, or an internal implementation on the operator's
        # own PYTHONPATH -- was unreachable by the very suite that exists to
        # certify it. The repo still comes first so our fixtures win ties.
        env={**os.environ,
             "PYTHONPATH": os.pathsep.join(
                 p for p in (str(ROOT), str(ROOT / "evals"),
                             os.environ.get("PYTHONPATH", "")) if p),
             **(env or {})},
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
