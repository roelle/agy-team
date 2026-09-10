"""End-to-end reactive teamwork with real models and no human polling.

One instruction to the tpm. Nobody checks any inbox. Real agents get woken by
their teammates' messages, do work, and the answer comes back to the user.

Uses the SDK runner because the agy CLI is not installed here; the supervisor,
transport, memory, roster, and prompts are identical on both paths — only
Runner.wake differs (subprocess vs in-process session).

Usage: .venv/bin/python evals/test_reactive.py     (~20-30c)
"""
import json
import os
import shutil
import sys
from pathlib import Path

from rpc_util import ROOT, check

sys.path.insert(0, str(ROOT))

DURABLE = ROOT / "evals" / "reactive_durable"
SANDBOX = ROOT / "evals" / "reactive_sandbox"


def main() -> int:
    for d in (DURABLE, SANDBOX):
        shutil.rmtree(d, ignore_errors=True)
    SANDBOX.mkdir(parents=True)
    os.environ.update({"AGYTEAM_DURABLE_DIR": str(DURABLE),
                       "AGYTEAM_PROJECT_DIR": str(ROOT),
                       "AGYTEAM_SHARED_DIR": str(SANDBOX)})

    from agyteam import scope
    scopes = scope.load()
    team_dir = scopes.team_dir()
    os.environ["AGYTEAM_TEAM_DIR"] = str(team_dir)
    (team_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (team_dir / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "Coordinates. Decomposes work, delegates by "
         "name, reports to the user. Has no shell and no file tools.",
         "tools_off": ["run_command", "create_file", "edit_file"],
         "workers": False},
        {"name": "coder", "role": "Implements and verifies code changes."},
        {"name": "syseng", "role": "Owns the environment; independently "
         "verifies other agents' work by running it."}]}))

    from agyteam.runner_sdk import SdkRunner
    from agyteam.supervisor import Supervisor
    from agyteam.transport import load as load_transport

    runner = SdkRunner({})
    sup = Supervisor(["tpm", "coder", "syseng"], runner, team_dir=team_dir,
                     max_hops=10)

    print("\n== reactive teamwork (real agents, nobody polls an inbox) ==")
    load_transport("user").send("tpm", (
        f"Please get a file created at {SANDBOX}/reactive.txt containing exactly "
        "the word 'kingfisher', and have it independently verified by someone "
        "who did not write it. Report back to me when it is confirmed."))
    turns = sup.run_until_idle()
    sup.close()

    log = team_dir / "bus.jsonl"
    hops = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    trace = " ; ".join(f"{h['from']}->{h['to']}" for h in hops)
    target = SANDBOX / "reactive.txt"
    to_user = [h for h in hops if h["to"] == "user" and h["from"] != "user"]

    print(f"\n  bus trace: {trace}")
    print(f"  turns dispatched: {turns}\n")
    score = sum([
        check("work happened with no human checking any inbox", turns >= 2, str(turns)),
        check("tpm delegated rather than doing it itself",
              any(h["from"] == "tpm" and h["to"] in ("coder", "syseng") for h in hops),
              trace),
        check("the deliverable exists", target.exists(),
              f"missing {target}"),
        check("deliverable content is correct",
              target.exists() and "kingfisher" in target.read_text().lower(),
              target.read_text() if target.exists() else "no file"),
        check("a second agent was involved (author != verifier)",
              len({h["from"] for h in hops if h["from"] != "user"}) >= 2, trace),
        check("the answer came back to the user unprompted", bool(to_user), trace),
        check("agents kept memory in durable scope",
              any((DURABLE / "agents" / a / "memory").exists()
                  for a in ("tpm", "coder", "syseng"))),
    ])
    print(f"\n== reactive teamwork: {score}/7 ==")
    return 0 if score == 7 else 1


if __name__ == "__main__":
    sys.exit(main())
