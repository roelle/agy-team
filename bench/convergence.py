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


DEFAULT_RUNNER = "agyteam.runner_sdk:SdkRunner"


def runner_spec() -> str:
    """Honour AGYTEAM_RUNNER, defaulting to the SDK runner.

    It was hardcoded here, in the same commit that made runners pluggable, so
    the one script that measures whether the system learns could only ever
    measure one runtime.
    """
    return os.environ.get("AGYTEAM_RUNNER") or DEFAULT_RUNNER


def sup(team: str, audit: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ,
               AGYTEAM_TEAM=team,
               AGYTEAM_RUNNER=runner_spec(),
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


def line_count(path: Path) -> int:
    """Lines in the audit log, used to bound one run's slice of it exactly."""
    try:
        with Path(path).open("rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


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
    # Governance records (a retro adopting a norm) are not work reviews and
    # must not count toward "was this work checked".
    approved = [e for e in reviews if e.get("verdict") == "approved"
                and e.get("kind", "work") == "work"]
    # Self-review was invisible until the record gained an "author" field
    # (the first cold run's manager approved her own audit and nothing
    # structural could see it). record_review now refuses reviewer==author
    # outright; this metric exists to catch records from older code and any
    # future regression of that refusal.
    self_approved = [e for e in approved
                     if e.get("author") in (None, e.get("reviewer"))]
    unverified = [e for e in approved if e.get("author_verified") is False]

    # One boolean used to carry two different questions, and answered neither.
    # `approved[0].ts <= to_user[0].ts` compares against the FIRST message to
    # the user, so a team that answers early, gets bounced by the supervisor,
    # deliberates, earns a review and re-answers scores identically to a team
    # that answers once and is never reviewed at all. Measured over 8
    # controlled reps: False 8/8 in both arms. A metric that cannot separate
    # the arms is not measuring anything.
    #
    # Split them. "Did the manager jump the gate" is about the first answer;
    # "did the answer the user kept carry an approval" is about the last.
    first_answer = to_user[0]["ts"] if to_user else None
    last_answer = to_user[-1]["ts"] if to_user else None
    return {
        "self_or_authorless_approvals": len(self_approved),
        "unverified_author_approvals": len(unverified),
        "messages": len(bus),
        "manager_delegated_to": delegated,
        "lateral_messages": len(lateral),
        "reviews": len(reviews),
        # the gate held on the first attempt
        "answered_before_any_review": bool(
            first_answer and not any(e["ts"] <= first_answer for e in approved)),
        # the answer the user actually kept was backed by a review
        "final_answer_was_reviewed": bool(
            last_answer and any(e["ts"] <= last_answer for e in approved)),
        "answered_user": bool(to_user),
        "answers_to_user": len(to_user),
    }


def grade(task: str, since: str, team_dir: Path, audit: str, team: str,
          audit_from: int, audit_to: int) -> str:
    p = subprocess.run(
        [sys.executable, str(HERE / "grade.py"), task,
         "--since", since, "--team-dir", str(team_dir),
         "--audit", audit, "--team", team,
         "--audit-from-line", str(audit_from),
         "--audit-to-line", str(audit_to)],
        capture_output=True, text=True)
    return p.stdout + p.stderr


def episode_summary(team_dir: Path, since: str) -> dict:
    """Turns and seconds for the episodes in this window.

    "If run A saturated the scale, judge on process and cost" was printed
    beside no cost at all.
    """
    events = rows(team_dir / "events.jsonl", since)
    eps = [e for e in events if e.get("event") == "episode"]
    turns = [e for e in events if e.get("event") == "turn"]
    return {
        "turns": sum(int(e.get("turns") or 0) for e in eps) or len(turns),
        "model_seconds": round(sum(float(e.get("duration_s") or 0)
                                   for e in turns), 1),
        "episode_seconds": round(sum(float(e.get("duration_s") or 0)
                                     for e in eps), 1),
    }


def check_containment(stage: Path) -> None:
    """Refuse to stage an answer key behind a guarantee the runner disclaims.

    The staging step sets each agent's `workspaces` to the fixture directory
    and the docstring says "the keys stay unreachable" -- which is exactly
    what supports_containment = False declares does not work. On such a runner
    the keys sit in this repo, readable, and the run produces a perfect score
    that means nothing. verify_fixtures.py already refuses to measure with a
    rotted instrument; this is the same refusal one step earlier.
    """
    sys.path.insert(0, str(REPO))
    from agyteam.runner import capabilities
    caps = capabilities(runner_spec())
    if caps["supports_containment"] is True:
        return
    why = ("declares supports_containment = False"
           if caps["supports_containment"] is False
           else f"could not be inspected ({caps['error']})")
    sys.exit(
        f"runner {caps['spec']} {why}.\n"
        f"Staging the fixtures at {stage} does not make bench/keys/ "
        f"unreachable on this runner: roster `workspaces` are advisory here "
        f"and the keys are readable in the repo.\n"
        f"A benchmark the subject can read is not a benchmark. Either run "
        f"this under a runner that enforces containment, or enforce it where "
        f"the process lives -- filesystem permissions, a container, a "
        f"separate account -- and copy the fixtures somewhere the keys are "
        f"not.")


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
    print(f"cold team: {team_root}\nstaging:   {stage}\n"
          f"runner:    {runner_spec()}\n")

    verify = subprocess.run([sys.executable, str(HERE / "verify_fixtures.py")],
                            capture_output=True, text=True)
    if verify.returncode != 0:
        sys.exit("bench fixtures are not intact; refusing to measure with a "
                 "rotted instrument:\n" + verify.stdout + verify.stderr)
    check_containment(stage)

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
        audit_from = line_count(Path(a.audit))
        p = sup(a.team, a.audit, "--say",
                f"manager: {task_prompt(task, run_stage)}")
        audit_to = line_count(Path(a.audit))
        if p.returncode != 0:
            print(p.stdout[-2000:], p.stderr[-2000:], sep="\n")
            sys.exit(f"run {label} failed; not grading a run that did not "
                     "happen")

        metrics = process_metrics(team_dir, since)
        episode = episode_summary(team_dir, since)
        metrics.update(episode)
        # returncode != 0 does not mean the run happened. The supervisor exits
        # 0 on "team went idle", which is what an episode looks like when the
        # agents' bus servers are bound to a different team directory: every
        # run ends "[done after 1 turns - team went idle]" and eight of them
        # completed in sixteen minutes, producing a full set of meaningless
        # scores. The script already computed both facts below and read
        # neither.
        if episode["turns"] <= 1 and not metrics["answered_user"]:
            print(p.stdout[-2000:], p.stderr[-2000:], sep="\n")
            sys.exit(
                f"run {label} exited 0 but did not happen: "
                f"{episode['turns']} turn(s), {metrics['messages']} bus "
                f"message(s), nothing addressed to the user.\n"
                f"A supervisor exits 0 when the team goes idle, so this is "
                f"not a model failure. Check that the agents' bus is bound to "
                f"{team_dir} -- a server process started against a previous "
                f"team keeps its environment and writes to the old team's "
                f"bus, where this run will never see it.")

        print(grade(task, since, team_dir, a.audit, a.team,
                    audit_from, audit_to))
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
              f"gate-jumped={m['answered_before_any_review']}, "
              f"final-answer-reviewed={m['final_answer_was_reviewed']}, "
              f"{m['turns']} turns, {m['model_seconds']}s model, "
              f"{m['episode_seconds']}s wall")

    # Print the delta, because the derivative is the claim. Saying "judge on
    # cost" and then printing no cost leaves the reader to do arithmetic the
    # script already has the numbers for.
    if len(reports) == 2:
        (_, _, a_m), (_, _, b_m) = reports
        print("\n  B - A:")
        for field in ("turns", "model_seconds", "episode_seconds",
                      "lateral_messages", "messages"):
            delta = b_m[field] - a_m[field]
            better = "cheaper" if field.endswith(("turns", "seconds")) else "more"
            direction = "" if delta == 0 else (
                f" ({better})" if (delta < 0) == field.endswith(("turns", "seconds"))
                else "")
            print(f"    {field}: {a_m[field]} -> {b_m[field]} "
                  f"({delta:+g}){direction}")

    print("\nContent scores are above, printed per run. If run A saturated "
          "the scale, judge on process and cost; the level tells you the "
          "team is good, only the derivative tells you the system is "
          "convergent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
