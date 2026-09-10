"""Does a wake resume the agent's conversation, or only reload its memory?

Two wakes, one fact, no memory writes allowed. If the second wake recalls what
the first was told, the runner keeps a live session; if it does not, every wake
is a cold start and durable memory is the only continuity that exists.

This is the difference between runner_agy (a fresh `agy -p` conversation per
wake) and runner_sdk (one persistent session per agent), measured rather than
assumed. Runs each runner that is actually available.

Usage: .venv/bin/python evals/test_continuity.py       (~10-20c)
"""
import json
import os
import shutil
import sys
from pathlib import Path

from rpc_util import ROOT, check

sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / "evals" / "continuity_scratch"
SECRET = "4173"
AGENT = "coder"

PLANT = (f"Remember this number for the rest of our work together: {SECRET}. "
         "Do NOT save it to memory, do not write it to any file, and do not "
         "message anyone. Just reply with the single word OK.")
RECALL = ("What number did I ask you to remember earlier? Reply with just the "
          "number. If you have no record of any such number, reply NONE. Do "
          "not guess, and do not message anyone.")


def setup() -> Path:
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    os.environ.update({"AGYTEAM_DURABLE_DIR": str(SCRATCH),
                       "AGYTEAM_PROJECT_DIR": str(ROOT),
                       "AGYTEAM_SHARED_DIR": str(SCRATCH)})
    from agyteam import scope
    team_dir = scope.load().team_dir()
    (team_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (team_dir / "roster.json").write_text(json.dumps({"agents": [
        {"name": AGENT, "role": "Implements and verifies code changes."}]}))
    os.environ["AGYTEAM_TEAM_DIR"] = str(team_dir)
    return team_dir


def probe(runner, label: str) -> tuple[int, int]:
    print(f"\n== {label}: does the second wake remember the first? ==")
    first = runner.wake(AGENT, PLANT)
    second = runner.wake(AGENT, RECALL)
    runner.close()
    print(f"  wake 1: {first.strip()[:100]}")
    print(f"  wake 2: {second.strip()[:100]}")

    remembered = SECRET in second
    errored = second.startswith("[error:")
    return sum([
        check(f"{label}: both wakes ran without error", not errored, second[:200]),
        check(f"{label}: recall == {'session kept' if remembered else 'cold start'}",
              True, ""),   # reported, not judged — this is the measurement
    ]), 2, remembered


def main() -> int:
    results = {}
    scores = 0
    total = 0

    setup()
    from agyteam.runner_sdk import SdkRunner
    s, t, remembered = probe(SdkRunner({}), "sdk")
    scores, total, results["sdk"] = scores + s, total + t, remembered

    setup()
    from agyteam.runner_agy import AgyRunner
    agy = AgyRunner({})
    if agy.available():
        s, t, remembered = probe(agy, "agy-cli")
        scores, total, results["agy-cli"] = scores + s, total + t, remembered
    else:
        print("\n== agy-cli: skipped, binary not on PATH ==")

    print("\n== continuity ==")
    for label, remembered in results.items():
        print(f"  {label:8} : {'remembers across wakes (persistent session)' if remembered else 'forgets across wakes (memory is the only continuity)'}")
    print(f"\n== continuity: {scores}/{total} ==")
    return 0 if scores == total else 1


if __name__ == "__main__":
    sys.exit(main())
