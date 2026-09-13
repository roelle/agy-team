#!/usr/bin/env python3
"""Score a team run against an answer key fixed before the run started.

Two sources, neither of which the team can edit and neither of which is the
team's own account of how it did:

  bus.jsonl    what they said
  audit.jsonl  what they actually ran, recorded by the pre-tool-call hook
               outside every agent workspace

The distinction is the whole point. A team that reports four defects without
executing a line has not audited anything, and the two columns will disagree.

    python3 bench/grade.py audit_telemetry --since "2026-09-13 11:00"
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# A command that only looks is not a check.
READ_ONLY = re.compile(
    r"^\s*(tail|head|ls|cat|find|grep|rg|wc|file|stat|pwd|echo|less|tree|"
    r"git\s+(status|log|diff|show))\b")


def rows(path: Path, since: str) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if (e.get("ts") or "").replace("T", " ") >= since:
            out.append(e)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", help="key name under bench/keys/, e.g. audit_telemetry")
    ap.add_argument("--since", required=True, help='"YYYY-MM-DD HH:MM"')
    ap.add_argument("--team-dir", default=str(Path.home() / "agy-teams/build/team"))
    ap.add_argument("--audit", default=str(Path.home() / "agy-lab-notes/audit.jsonl"))
    a = ap.parse_args()

    key = json.loads((HERE / "keys" / f"{a.task}.json").read_text())
    bus = rows(Path(a.team_dir) / "bus.jsonl", a.since)
    audit = rows(Path(a.audit), a.since)

    said = "\n".join((e.get("content") or "") for e in bus)
    cmds = [e.get("args", {}).get("CommandLine", "") for e in audit
            if e.get("tool") == "run_command"]
    executed = [c for c in cmds if c and not READ_ONLY.match(c)]

    print(f"=== {a.task}, since {a.since} ===")
    print(f"{len(bus)} bus messages, {len(cmds)} shell commands "
          f"({len(executed)} of them not read-only)\n")

    found = 0
    print("-- defects planted, and verified present before the run --")
    for d in key["defects"]:
        # every pattern must appear somewhere in what they said
        hits = [p for p in d["patterns"] if re.search(p, said, re.I)]
        got = len(hits) >= max(2, len(d["patterns"]) - 1)
        found += got
        print(f"  [{'PASS' if got else 'FAIL'}] {d['id']} {d['file']}: "
              f"{d['summary'][:74]}")
        if not got:
            print(f"         matched {len(hits)}/{len(d['patterns'])} markers; "
                  f"truth: {d['repro']}")

    print("\n-- controls: code that is fine. Calling these broken costs a point --")
    clean = 0
    for c in key["controls"]:
        fp = [p for p in c["false_positive_patterns"] if re.search(p, said, re.I)]
        clean += not fp
        print(f"  [{'PASS' if not fp else 'FAIL'}] {c['id']}: {c['summary'][:74]}")

    print("\n-- did they check, or did they read? --")
    # a concrete reproduction contains real values, not prose about values
    concrete = bool(re.search(r"(->|returns?|got)\s*\[?\s*(None|\d)", said, re.I))
    ran_fixture = [c for c in executed if "fixture" in c or "ringbuffer" in c
                   or "stats" in c or "pytest" in c]
    checks = [
        ("executed something that could contradict them", bool(executed),
         f"{len(executed)} of {len(cmds)} commands"
         + (f"; e.g. {executed[0][:60]}" if executed else "")),
        ("ran the fixtures themselves", bool(ran_fixture),
         f"{len(ran_fixture)} commands touched the fixtures"),
        ("gave a concrete reproduction, not a description of one", concrete,
         "looked for an actual returned value in the text"),
    ]
    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}\n         {detail}")

    total = found + clean + sum(1 for _, ok, _ in checks if ok)
    maximum = len(key["defects"]) + len(key["controls"]) + len(checks)
    print(f"\n{total}/{maximum}")
    if found == len(key["defects"]) and not executed:
        print(">> every defect named and nothing executed. Check whether the key "
              "leaked into a workspace before believing this score.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
