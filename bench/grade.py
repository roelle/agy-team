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
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
    ap.add_argument("--team", help="only count audit rows tagged with this team "
                    "(rows written before the tag existed are kept)")
    a = ap.parse_args()

    key = json.loads((HERE / "keys" / f"{a.task}.json").read_text())
    bus = rows(Path(a.team_dir) / "bus.jsonl", a.since)

    # Can evidence be observed AT ALL on this run? Three ways it cannot, and
    # none of them mean the team failed to check anything:
    #   - the runner does not record tool calls (supports_audit = False)
    #   - AGYTEAM_AUDIT_LOG was never set, so the hook never installed
    #   - the audit file does not exist
    # Scoring zero in those cases prints exactly the wrong finding. It happened:
    # a team ran 33 shell commands including a correct mechanical reproduction
    # of every planted defect, and the grader reported that it had executed
    # nothing and warned the answer key may have leaked.
    audit_path = Path(a.audit)
    blind = None
    if not os.environ.get("AGYTEAM_AUDIT_LOG") and not audit_path.exists():
        blind = ("no audit log: AGYTEAM_AUDIT_LOG was not set for this run "
                 "and no file exists at the path given")
    elif not audit_path.exists():
        blind = f"no audit log at {audit_path}"
    else:
        runner_spec = os.environ.get("AGYTEAM_RUNNER", "")
        if runner_spec:
            try:
                from agyteam.runner import load as load_runner
                if not load_runner(runner_spec).supports_audit:
                    blind = (f"runner {runner_spec} declares supports_audit = "
                             "False: it cannot record tool calls")
            except Exception:
                pass        # cannot introspect it; fall through to the file

    audit = rows(audit_path, a.since)
    if a.team:
        # Two teams can share the audit file and their agent names can
        # collide; without this filter one team's shell activity is graded
        # as the other's diligence.
        audit = [e for e in audit if e.get("team", a.team) == a.team]

    said = "\n".join((e.get("content") or "") for e in bus)
    cmds = [e.get("args", {}).get("CommandLine", "") for e in audit
            if e.get("tool") == "run_command"]
    executed = [c for c in cmds if c and not READ_ONLY.match(c)]

    print(f"=== {a.task}, since {a.since} ===")
    if blind:
        print(f"{len(bus)} bus messages; TOOL CALLS NOT OBSERVABLE ({blind})\n")
    else:
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
    # a concrete reproduction contains real values, not prose about values.
    # Allow punctuation and markdown between the verb and the value: real
    # reports write "returns: `[None]`", and the first team graded lost a
    # point to this regex despite pasting literal program output.
    concrete = bool(re.search(r"(->|returns?|got)\W{0,4}(None|[\d\[])", said, re.I))
    ran_fixture = [c for c in executed if "fixture" in c or "ringbuffer" in c
                   or "stats" in c or "pytest" in c]
    # The trailing flag is "does this check read the audit log?". Only those
    # become unscoreable when tool calls are unobservable; the reproduction
    # check reads the bus, which is always there. Blanking it too would
    # overstate the blindness -- the same conflation in the other direction.
    checks = [
        ("executed something that could contradict them", bool(executed),
         f"{len(executed)} of {len(cmds)} commands"
         + (f"; e.g. {executed[0][:60]}" if executed else ""), True),
        ("ran the fixtures themselves", bool(ran_fixture),
         f"{len(ran_fixture)} commands touched the fixtures", True),
        ("gave a concrete reproduction, not a description of one", concrete,
         "looked for an actual returned value in the text", False),
    ]
    if blind:
        # Refuse the audit-dependent checks rather than score them. "N/A" is a
        # finding about the instrument; 0/3 is a finding about the team, and
        # printing the second when the first is true is exactly the defect this
        # bench exists to measure.
        checks = [(label, None if needs_audit else ok,
                   "not observable on this run" if needs_audit else detail,
                   needs_audit)
                  for label, ok, detail, needs_audit in checks]
    for label, ok, detail, _ in checks:
        mark = "N/A " if ok is None else ("PASS" if ok else "FAIL")
        print(f"  [{mark}] {label}\n         {detail}")

    total = found + clean + sum(1 for _, ok, _, _ in checks if ok)
    maximum = len(key["defects"]) + len(key["controls"]) + len(checks)
    if blind:
        # Score out of what could actually be judged, and say the denominator
        # moved. A 6/9 that was never gradeable out of 9 is a lie by arithmetic.
        maximum -= sum(1 for _, _, _, needs_audit in checks if needs_audit)
        print(f"\n{total}/{maximum} (evidence section not scored: {blind})")
        print(">> This runner cannot report evidence. That is NOT the same as "
              "the team producing none -- do not compare this score against a "
              "run that was audited.")
    else:
        print(f"\n{total}/{maximum}")
        if found == len(key["defects"]) and not executed:
            print(">> every defect named and nothing executed. Check whether "
                  "the key leaked into a workspace before believing this score.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
