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


def rows(path: Path, since: str, from_line: int | None = None,
         to_line: int | None = None) -> list[dict]:
    """Parsed JSONL at or after `since`, optionally restricted to a line range.

    The line range is how a run is separated from the run before it in a
    shared append-only log; timestamps alone are too coarse.
    """
    if not path.exists():
        return []
    lines = path.read_text(errors="replace").splitlines()
    lines = lines[(from_line or 0):(to_line if to_line is not None else len(lines))]
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if (e.get("ts") or "").replace("T", " ") >= since:
            out.append(e)
    return out


def line_count(path: Path) -> int:
    """Lines in an append-only file; 0 when it does not exist yet."""
    try:
        with Path(path).open("rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", help="key name under bench/keys/, e.g. audit_telemetry")
    ap.add_argument("--since", required=True, help='"YYYY-MM-DD HH:MM"')
    ap.add_argument("--team-dir", default=str(Path.home() / "agy-teams/build/team"))
    ap.add_argument("--audit", default=str(Path.home() / "agy-lab-notes/audit.jsonl"))
    ap.add_argument("--team", help="only count audit rows tagged with this team "
                    "(rows written before the tag existed are kept)")
    # --since is minute-granular and the audit log is one global file, so two
    # runs started inside the same minute grade each other's commands.
    # Measured over 8 back-to-back reps: every rep's window contained foreign
    # stage-directory references, one of them 31 own rows against 3 borrowed.
    # It was harmless there only because the affected checks were saturated;
    # for any A/B use of the bench it is a cross-arm leak in the scoring path.
    # Line bounds are exact, and an append-only file makes them stable.
    ap.add_argument("--audit-from-line", type=int, default=None,
                    help="ignore audit rows before this 0-based line")
    ap.add_argument("--audit-to-line", type=int, default=None,
                    help="ignore audit rows at or after this 0-based line")
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
            # Read the declaration off the class. Constructing the runner
            # inside `except Exception` was a regression introduced by the
            # commit that added this very handling: agyteam.runner.load()
            # signals every misconfiguration with SystemExit, a BaseException,
            # which passes straight through. One bad AGYTEAM_RUNNER turned the
            # grader from "reports blindness honestly" into "exit 1, no score
            # at all" -- a worse failure than the one it was written to fix.
            from agyteam.runner import capabilities
            caps = capabilities(runner_spec)
            if caps["supports_audit"] is False:
                blind = (f"runner {runner_spec} declares supports_audit = "
                         "False: it cannot record tool calls")
            elif caps["supports_audit"] is None:
                blind = (f"could not read the capabilities of runner "
                         f"{runner_spec} ({caps['error']}); unknown is not "
                         f"the same as audited")

    audit = rows(audit_path, a.since, a.audit_from_line, a.audit_to_line)
    if a.team:
        # Two teams can share the audit file and their agent names can
        # collide; without this filter one team's shell activity is graded
        # as the other's diligence.
        audit = [e for e in audit if e.get("team", a.team) == a.team]

    said = "\n".join((e.get("content") or "") for e in bus)
    # An audit nobody was told about is not an audit. One rep scored 9/9 with
    # an empty to_user -- its whole bus was tpm -> manager -- because the
    # grader scores text and shell commands and never asked whether an answer
    # was delivered. Same species as grading a run that did not happen.
    answered_user = any(e.get("to") == "user" for e in bus)
    cmds = [e.get("args", {}).get("CommandLine", "") for e in audit
            if e.get("tool") == "run_command"]
    executed = [c for c in cmds if c and not READ_ONLY.match(c)]

    print(f"=== {a.task}, since {a.since} ===")
    if blind:
        print(f"{len(bus)} bus messages; TOOL CALLS NOT OBSERVABLE ({blind})\n")
    else:
        print(f"{len(bus)} bus messages, {len(cmds)} shell commands "
              f"({len(executed)} of them not read-only)\n")

    if not bus:
        print(">> NO BUS ACTIVITY in this window. Either the run did not "
              "happen, or it wrote to a different team directory than the one "
              f"being graded ({a.team_dir}). Refusing to score it.")
        return 2

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
    if not answered_user:
        print(">> THE USER WAS NEVER ANSWERED. Nothing in this window is "
              "addressed to 'user', so whatever the team found, it did not "
              "deliver. Treat the score above as a measure of internal "
              "activity only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
