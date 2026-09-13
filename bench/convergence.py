#!/usr/bin/env python3
"""Measure whether a team converges from a cold start. Spends real money.

This replaces a 523-line eval that never constructed an agent and whose
verdicts were string literals. The lesson of that file is baked into this
one: there is no offline mode, no mock runner, no way to make this pass
without five real agents doing real turns. If you cannot afford the run,
the honest state is "unmeasured", and this script refuses to pretend
otherwise.

What it does, end to end:

  1. Creates a team with NOTHING but a roster: no memories, no norms, no
     bus history, no identities.
  2. Stages the audit_telemetry fixtures in a directory outside any repo
     and outside every other team's workspace, and points the cold team's
     workspaces at it — the keys stay unreachable.
  3. Run A: the task from bench/tasks/audit_telemetry.md, verbatim.
  4. Grades run A from the bus and the audit log.
  5. Retro + cycle-all: the system's own convergence loop. Team learnings
     land in NORMS.md, individual learnings distil to memory.
  6. Run B: the sibling task (audit_sessions) on fresh fixtures. Same
     defect classes, code the team has never seen.
  7. Grades run B and prints the comparison.

Convergence is the DERIVATIVE, not the level: did run B beat run A? When
run A saturates the content scale (a strong cold team can), the signal
moves to process — delegation, review-before-answer — and cost. All three
are printed; judge on what has headroom.

    .venv/bin/python bench/convergence.py --team cold-$(date +%m%d) \
        --audit ~/agy-lab-notes/audit.jsonl

Requires: GEMINI_API_KEY, the venv, and a budget. Expect one to a few
dollars depending on models in the roster.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

# The roster is the only thing a cold team starts with. Copied from the
# build team's shape: capability-scoped roles, oversight on a different
# model family from implementation. Workspaces are filled in at stage time.
COLD_ROSTER = {
    "mission": ("Audit code you are given and report, for each file, whether "
                "it is correct - with a concrete reproduction for anything "
                "you call broken."),
    "agents": [
        {"name": "manager",
         "role": ("Faces outward. Decides what good means, gates what reaches "
                  "the user, and is accountable for it. Nothing reaches the "
                  "user claiming to be done without an approved review. Has a "
                  "shell to check claims against reality. Does not write the "
                  "work: implementation goes through tpm."),
         "tools_off": ["create_file", "edit_file"], "workers": False,
         "model": "gemini-3.1-pro-preview"},
        {"name": "tpm",
         "role": ("Faces inward. Decomposes work into pieces one agent can "
                  "finish, delegates by name with complete context, tracks "
                  "what is outstanding. Does not write code or run commands."),
         "tools_off": ["run_command", "create_file", "edit_file"],
         "workers": False},
        {"name": "coder",
         "role": ("Implements, with tests, and runs them before handing off."),
         "model": "gemini-3.8-flash", "effort": "high"},
        {"name": "syseng",
         "role": ("Owns the environment. Reproduces problems and reports the "
                  "command and output that produced them."),
         "model": "gemini-3.8-flash", "effort": "high"},
        {"name": "qa",
         "role": ("Reviews. Constructs cases the author did not write and "
                  "records the verdict with record_review. Cannot edit "
                  "source: findings go back to the implementer."),
         "tools_off": ["create_file", "edit_file"], "workers": False,
         "model": "gemini-3.1-pro-preview"},
    ],
}

TASKS = [("audit_telemetry", "fixtures"), ("audit_sessions", "fixtures2")]


def task_prompt(name: str, staged_dir: Path) -> str:
    """The team-facing text from bench/tasks/<name>.md, path rewritten."""
    md = (HERE / "tasks" / f"{name}.md").read_text()
    body = md.split("---")[1].strip()
    body = re.sub(r"`bench/fixtures2?/`", str(staged_dir), body)
    return (body + "\n\nReport to user when done. Do not send status updates "
            "and do not ask me to confirm a plan.")


def sup(team: str, audit: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ,
               AGYTEAM_TEAM=team,
               AGYTEAM_RUNNER="agyteam.runner_sdk:SdkRunner",
               AGYTEAM_AUDIT_LOG=audit)
    return subprocess.run(
        [sys.executable, "-m", "agyteam.supervisor", *args],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=3600)


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


def process_metrics(team_dir: Path, since: str) -> dict:
    bus = rows(team_dir / "bus.jsonl", since)
    reviews = rows(team_dir / "reviews.jsonl", since)
    to_user = [e for e in bus if e.get("to") == "user"]
    frm = lambda e: e.get("frm") or e.get("from")
    delegated = sorted({e.get("to") for e in bus
                        if frm(e) == "manager"
                        and e.get("to") not in ("user", None)})
    lateral = [e for e in bus
               if frm(e) not in ("manager", "tpm", "user", "supervisor")
               and e.get("to") not in ("manager", "tpm", "user")]
    approved = [e for e in reviews if e.get("verdict") == "approved"]
    # Self-review was invisible until the record gained an "author" field
    # (the first cold run's manager approved her own audit and nothing
    # structural could see it). record_review now refuses reviewer==author
    # outright; this metric exists to catch records from older code and any
    # future regression of that refusal.
    reviewed_first = bool(approved and to_user and
                          approved[0]["ts"] <= to_user[0]["ts"])
    self_approved = [e for e in approved
                     if e.get("author") in (None, e.get("reviewer"))]
    return {
        "self_or_authorless_approvals": len(self_approved),
        "messages": len(bus),
        "manager_delegated_to": delegated,
        "lateral_messages": len(lateral),
        "reviews": len(reviews),
        "approved_review_before_answer": reviewed_first,
        "answered_user": bool(to_user),
    }


def grade(task: str, since: str, team_dir: Path, audit: str, team: str) -> str:
    p = subprocess.run(
        [sys.executable, str(HERE / "grade.py"), task,
         "--since", since, "--team-dir", str(team_dir),
         "--audit", audit, "--team", team],
        capture_output=True, text=True)
    return p.stdout + p.stderr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True,
                    help="name for the throwaway cold team; must not exist")
    ap.add_argument("--audit", required=True,
                    help="audit log path OUTSIDE every workspace")
    ap.add_argument("--teams-root",
                    default=str(Path.home() / "agy-teams"))
    a = ap.parse_args()

    team_root = Path(a.teams_root) / a.team
    if team_root.exists():
        sys.exit(f"{team_root} already exists. A convergence run must start "
                 "cold; pick a fresh name rather than reusing a team that "
                 "may have memories.")

    stage = Path(tempfile.mkdtemp(prefix="bench-stage-"))
    print(f"cold team: {team_root}\nstaging:   {stage}\n")

    verify = subprocess.run([sys.executable, str(HERE / "verify_fixtures.py")],
                            capture_output=True, text=True)
    if verify.returncode != 0:
        sys.exit("bench fixtures are not intact; refusing to measure with a "
                 "rotted instrument:\n" + verify.stdout + verify.stderr)

    reports = []
    for i, (task, fixdir) in enumerate(TASKS):
        run_stage = stage / task
        shutil.copytree(HERE / fixdir, run_stage)

        roster = json.loads(json.dumps(COLD_ROSTER))     # deep copy
        for agent in roster["agents"]:
            agent["workspaces"] = [str(run_stage)]
        team_dir = team_root / "team"
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "roster.json").write_text(json.dumps(roster, indent=2))

        since = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        label = "A (cold)" if i == 0 else "B (after retro + cycle)"
        print(f"=== run {label}: {task}, since {since} ===")
        p = sup(a.team, a.audit, "--say",
                f"manager: {task_prompt(task, run_stage)}")
        if p.returncode != 0:
            print(p.stdout[-2000:], p.stderr[-2000:], sep="\n")
            sys.exit(f"run {label} failed; not grading a run that did not "
                     "happen")

        print(grade(task, since, team_dir, a.audit, a.team))
        metrics = process_metrics(team_dir, since)
        reports.append((label, task, metrics))
        print("process:", json.dumps(metrics, indent=2), "\n")

        if i == 0:
            print("=== retro + cycle-all: the convergence loop itself ===")
            sup(a.team, a.audit, "--retro", "--retro-force")
            sup(a.team, a.audit, "--cycle-all")
            norms = team_dir / "NORMS.md"
            print("NORMS.md:",
                  f"{len(norms.read_text().splitlines())} lines"
                  if norms.exists() else
                  "NOT WRITTEN - the loop produced no team learning; "
                  "expect run B to look like run A")

    print("=== the question that matters: did B beat A? ===")
    for label, task, m in reports:
        print(f"  run {label}: delegated to {m['manager_delegated_to'] or 'nobody'}, "
              f"{m['lateral_messages']} lateral, "
              f"review-before-answer={m['approved_review_before_answer']}")
    print("\nContent scores are above, printed per run. If run A saturated "
          "the scale, judge on process and cost; the level tells you the "
          "team is good, only the derivative tells you the system is "
          "convergent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
