"""Closing a task carries evidence, waits for collaborators, and respects review.

Also the other half of waiting: a task that is waiting on something external
is checked by the sweep itself -- a file, a process -- and the owner is woken
once, when it has happened or the deadline has passed. Twenty wakes in
seventy minutes to learn "still running" twenty times was the measurement.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import mcp_tasks, persona, tasks  # noqa: E402

PAST = "2000-01-01T00:00:00Z"


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "gates"},
        {"name": "coder", "role": "implements"},
        {"name": "qa", "role": "reviews"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AUDIT_LOG", raising=False)
    tasks._quiet.clear()
    return td


def worked(td, agent):
    with (td / "bus.jsonl").open("a") as f:
        f.write(json.dumps({"ts": "2099-01-01 00:00:00", "from": agent,
                            "to": "manager", "content": "done"}) + "\n")


# --- closing -----------------------------------------------------------------------

def test_a_closing_note_is_required_and_replaces_the_old_one(team):
    t = tasks.create(team, "p", "x", owner="coder", note="actively triaging")
    out = mcp_tasks._complete_task("coder", team, {"task_id": t["id"]})
    assert out.startswith("[error:") and "closing note" in out
    assert "closing note" in out and tasks.get(team, t["id"])["status"] == "queued"
    mcp_tasks._complete_task("coder", team, {"task_id": t["id"], "note": "fixed in 3 files",
                                             "evidence": "/repo/tests/test_x.py"})
    got = tasks.get(team, t["id"])
    assert (got["status"], got["note"], got["evidence"]) == \
        ("done", "fixed in 3 files", "/repo/tests/test_x.py")


def test_a_joint_task_waits_for_every_collaborator(team):
    worked(team, "coder")               # the record is readable; qa is not in it
    t = tasks.create(team, "p", "x", owner="coder", collaborators=["qa"])
    with pytest.raises(tasks.TaskError, match="qa .*no recorded activity"):
        tasks.complete(team, t["id"], "coder", "my half is done")
    worked(team, "qa")
    assert tasks.complete(team, t["id"], "coder", "both halves landed")["status"] == "done"


def test_a_collaborator_can_be_dropped(team):
    t = tasks.create(team, "p", "x", owner="coder", collaborators=["qa"])
    tasks.update(team, t["id"], "coder", collaborators=[])
    assert tasks.complete(team, t["id"], "coder", "solo after all")["status"] == "done"


def test_requires_review_closes_only_on_a_review_naming_it(team):
    t = tasks.create(team, "p", "x", owner="coder", requires_review=True)
    with pytest.raises(tasks.TaskError, match="requires an approved review"):
        tasks.complete(team, t["id"], "coder", "done")
    with (team / "reviews.jsonl").open("a") as f:
        f.write(json.dumps({"kind": "work", "verdict": "approved", "reviewer": "qa",
                            "author": "coder", "task_id": "task_other"}) + "\n")
    with pytest.raises(tasks.TaskError):
        tasks.complete(team, t["id"], "coder", "done")       # names another task
    with (team / "reviews.jsonl").open("a") as f:
        f.write(json.dumps({"kind": "work", "verdict": "approved", "reviewer": "qa",
                            "author": "coder", "task_id": t["id"]}) + "\n")
    assert tasks.complete(team, t["id"], "coder", "done")["status"] == "done"


def test_the_operator_can_waive_a_review_and_it_is_never_counted_as_one(team):
    t = tasks.create(team, "p", "x", owner="coder", requires_review=True)
    with pytest.raises(tasks.TaskError):
        tasks.waive_review(team, t["id"], by="")
    entry = tasks.waive_review(team, t["id"], by="user", reason="demo deadline")
    assert entry["kind"] == "waiver" and entry["verdict"] == "waived"
    assert tasks.complete(team, t["id"], "coder", "shipped")["status"] == "done"
    from agyteam.supervisor import generate_report
    rep = generate_report(team)
    assert all(r["kind"] != "work" for r in rep.get("reviews", [])), rep.get("reviews")


def test_the_waiver_is_a_command_only_the_operator_runs(team):
    t = tasks.create(team, "p", "x", owner="coder", requires_review=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "AGYTEAM_TEAM_DIR": str(team)}
    r = subprocess.run([sys.executable, "-m", "agyteam.supervisor", "--waive-review",
                        t["id"], "--by", "user", "--reason", "known good"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0 and "review waived" in r.stdout, r.stderr
    assert tasks.approved_review_for(team, t["id"])["kind"] == "waiver"
    r = subprocess.run([sys.executable, "-m", "agyteam.tasks", "waive", "task_nope"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 1 and "no task" in r.stderr


def test_a_norm_adoption_is_not_a_review_of_a_task(team):
    t = tasks.create(team, "p", "x", owner="coder", requires_review=True)
    with (team / "reviews.jsonl").open("a") as f:
        f.write(json.dumps({"kind": "norm", "verdict": "approved", "reviewer": "manager",
                            "task_id": t["id"]}) + "\n")
    with pytest.raises(tasks.TaskError):
        tasks.complete(team, t["id"], "coder", "done")


def test_status_aliases_are_accepted(team):
    t = tasks.create(team, "p", "x", owner="coder")
    for alias, canonical in (("in_progress", "running"), ("waiting", "blocked"),
                             ("completed", "done")):
        if canonical == "blocked":
            tasks.update(team, t["id"], "coder", status=alias, check_after="1h")
        else:
            tasks.update(team, t["id"], "coder", status=alias)
        assert tasks.get(team, t["id"])["status"] == canonical
    assert [p for p in mcp_tasks.TOOLS if p["name"] == "update_task"][0] \
        ["inputSchema"]["properties"]["status"]["enum"] == sorted(tasks.STATUSES)


# --- waiting on something external --------------------------------------------------

def sweep(team, sent, now=None):
    return tasks.sweep(team, lambda to, c: sent.append((to, c)) or "[delivered]", now=now)


def test_a_file_condition_wakes_nobody_until_the_file_appears(team, tmp_path):
    out = tmp_path / "sim.done"
    t = tasks.create(team, "sims", "trial 9", owner="coder",
                     until={"type": "file_exists", "path": str(out)}, poll_every="1m")
    sent = []
    for _ in range(5):
        assert sweep(team, sent, now="2099-01-01T00:00:00Z") == {}
    assert sent == []
    assert tasks.get(team, t["id"])["check_after"] > "2000"   # advanced, not cleared
    out.write_text("done")
    assert list(sweep(team, sent, now="2099-01-01T00:00:00Z")) == ["coder"]
    assert len(sent) == 1 and "condition met" in sent[0][1] and "sim.done exists" in sent[0][1]
    assert sweep(team, sent, now="2099-01-01T00:00:00Z") == {}, "woken once, not twice"


def test_a_deadline_wakes_the_owner_even_if_the_condition_never_holds(team, tmp_path):
    t = tasks.create(team, "sims", "trial 9", owner="coder",
                     until={"type": "file_exists", "path": str(tmp_path / "never")},
                     deadline="2050-01-01T00:00:00Z")
    sent = []
    assert sweep(team, sent, now="2049-12-31T00:00:00Z") == {}
    assert list(sweep(team, sent, now="2050-01-01T00:00:00Z")) == ["coder"]
    assert "deadline" in sent[0][1] and "NOT met" in sent[0][1]


def test_file_contains_and_pid_exited(team, tmp_path):
    log = tmp_path / "run.log"
    log.write_text("starting\n")
    t1 = tasks.create(team, "sims", "log", owner="coder",
                      until={"type": "file_contains", "path": str(log), "text": "FINISHED"})
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    t2 = tasks.create(team, "sims", "proc", owner="coder",
                      until={"type": "pid_exited", "pid": sleeper.pid})
    sent = []
    try:
        assert sweep(team, sent, now="2099-01-01T00:00:00Z") == {}
        log.write_text("starting\nFINISHED\n")
        sleeper.kill()
        sleeper.wait()
        assert list(sweep(team, sent, now="2099-01-01T00:00:00Z")) == ["coder"]
    finally:
        if sleeper.poll() is None:
            sleeper.kill()
    assert "2 reminders" in sent[0][1]
    assert "contains 'FINISHED'" in sent[0][1] and f"process {sleeper.pid} exited" in sent[0][1]


def test_the_tool_parses_the_condition_and_says_what_it_costs(team, tmp_path):
    out = mcp_tasks._create_task("coder", team, {
        "project": "sims", "title": "trial", "until": f"file_exists:{tmp_path}/x",
        "poll_every": "10m", "deadline": "2050-01-01T00:00:00Z"})
    assert "[task created" in out and "no turn spent" in out and "every 10m" in out
    out = mcp_tasks._create_task("coder", team, {"project": "sims", "title": "t",
                                                 "until": "cmd_succeeds:rm -rf /"})
    assert out.startswith("[error:") and "not a condition this can check" in out


# --- a team's own words in the brief ------------------------------------------------------

def test_a_team_can_replace_a_section_of_the_brief_and_add_to_an_agent(team):
    agents = json.loads((team / "roster.json").read_text())["agents"]
    before = persona.brief("coder", agents, team_dir=team)
    assert "## How we work together" in before or "Teammates are persistent peers" in before
    (team / "persona").mkdir()
    (team / "persona" / "teamwork.md").write_text("## How this team works\nShip small.")
    (team / "persona" / "coder.md").write_text("## For coder\nYou own the sims.")
    after = persona.brief("coder", agents, team_dir=team)
    assert "Ship small." in after and "You own the sims." in after
    assert "Teammates are persistent peers" not in after
    qa = persona.brief("qa", agents, team_dir=team)
    assert "Ship small." in qa and "You own the sims." not in qa


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
