"""Can the team actually deliver a verified artifact through the agy CLI?

Everything else measures plumbing. This measures the product: one instruction,
no human polling, a real file on disk, written by one agent and independently
checked by another. It runs on its own team namespace so it never inherits
memories or conversations from your working team.

This is the test that would have caught the two worst bugs of the port: agents
defined via the plugin's agents/ directory have no builtin tools and silently
cannot write files, and a team told to always reply will ack itself until the
hop budget stops it.

Usage: AGYTEAM_TEAM=trial .venv/bin/python evals/test_delivery.py   (~15-20c)
"""
import json
import os
import shutil
import sys
from pathlib import Path

from rpc_util import ROOT, check

sys.path.insert(0, str(ROOT))

# Deliberately an assignment, not setdefault. This eval wipes its team directory
# to start clean, and inheriting AGYTEAM_TEAM from the environment meant it
# would wipe *yours* — it once deleted a running team's roster and bus log
# mid-flight because an agent ran the test board with AGYTEAM_TEAM set.
TEAM = "delivery-eval"
os.environ["AGYTEAM_TEAM"] = TEAM
os.environ.pop("AGYTEAM_TEAM_DIR", None)
os.environ.pop("AGYTEAM_DURABLE_DIR", None)
MARKER = "delivery-eval team, safe to wipe"
SHARED = ROOT / "evals" / "delivery_shared"
TARGET = SHARED / "kingfisher.txt"

ROSTER = {"mission": MARKER, "agents": [
    {"name": "tpm", "role": "Coordinates. Decomposes the request, delegates to "
     "teammates by name, and reports the consolidated result to the user. Does "
     "not write files or run commands.",
     "tools_off": ["run_command", "create_file", "edit_file"], "workers": False},
    {"name": "coder", "role": "Implements. Writes files and runs commands to "
     "prove the work before handing off."},
    {"name": "syseng", "role": "Owns the environment and independently verifies "
     "other agents' work by running it, never by reading a claim about it."}]}

ASK = (f"get a file created at {TARGET} containing exactly the word kingfisher, "
       "and have it independently verified by someone who did not write it. "
       "Report to me when confirmed.")


def main() -> int:
    shutil.rmtree(SHARED, ignore_errors=True)
    SHARED.mkdir(parents=True)
    os.environ["AGYTEAM_SHARED_DIR"] = str(SHARED)

    from agyteam import scope
    team_dir = scope.load().team_dir()
    # Belt and braces: never delete a directory this eval did not create. If a
    # roster is there without our marker, it belongs to somebody real.
    existing = team_dir / "roster.json"
    if existing.exists():
        try:
            mission = json.loads(existing.read_text()).get("mission", "")
        except ValueError:
            mission = ""
        if mission != MARKER:
            sys.exit(f"refusing to wipe {team_dir}: roster is not this eval's "
                     f"(mission={mission!r}). Move it aside or pick another team.")
    shutil.rmtree(team_dir, ignore_errors=True)
    (team_dir / "inbox").mkdir(parents=True)
    (team_dir / "roster.json").write_text(json.dumps(ROSTER, indent=2))
    os.environ["AGYTEAM_TEAM_DIR"] = str(team_dir)

    from agyteam.runner_agy import AgyRunner
    from agyteam.supervisor import Supervisor
    from agyteam.transport import load as load_transport

    runner = AgyRunner({})
    if not runner.available():
        print("agy not on PATH — skipping")
        return 0

    print(f"\n== delivery: one instruction, verified artifact ({TEAM}) ==")
    sup = Supervisor([a["name"] for a in ROSTER["agents"]], runner,
                     team_dir=team_dir, max_hops=16)
    load_transport("user").send("tpm", ASK)
    turns = sup.run_until_idle()
    answer = load_transport("user").fetch()
    sup.close()

    hops = [json.loads(l) for l in (team_dir / "bus.jsonl").read_text().splitlines()]
    trace = " ; ".join(f"{h['from']}->{h['to']}" for h in hops)
    writers = {h["from"] for h in hops if h["from"] != "user"}
    convs = json.loads((team_dir / "conversations.json").read_text())
    content = TARGET.read_text() if TARGET.exists() else ""

    print(f"\n  trace: {trace}\n  turns: {turns}\n")
    score = sum([
        check("the deliverable exists on disk", TARGET.exists(), str(TARGET)),
        check("its content is exactly right", content.strip() == "kingfisher",
              repr(content)),
        check("tpm delegated instead of doing it itself",
              any(h["from"] == "tpm" and h["to"] in ("coder", "syseng")
                  for h in hops), trace),
        check("a second agent was involved", len(writers) >= 2, str(writers)),
        check("the user was answered unprompted", bool(answer),
              str([m.content[:60] for m in answer])),
        check("the episode terminated on the answer, not the hop budget",
              sup.stopped == "the user was answered", sup.stopped),
        check("no ack spiral (well under the budget)", turns <= 8, str(turns)),
        check("each agent kept one persistent conversation",
              len(convs) >= 2, str(convs)),
    ])
    print(f"\n== delivery: {score}/8 ==")
    return 0 if score == 8 else 1


if __name__ == "__main__":
    sys.exit(main())
