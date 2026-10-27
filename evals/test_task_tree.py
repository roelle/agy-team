"""Tasks form a tree: a project is a task with tasks under it.

A "project" was a label on a task. With hundreds of them, nothing could be
finished or archived, and the board's project filter was a list of
hundreds. Now a task may have a parent (one, no cycles), a level (set, or
worked out from what is under it), and the work rolls up.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import actions, mcp_tasks, tasks  # noqa: E402
from agyteam.record import Record  # noqa: E402
from agyteam.transport import Message  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402

AGENTS = ["tpm", "coder", "qa"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "teams" / "demo" / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [{"name": a} for a in AGENTS]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    return td


def new(team, title, **kw):
    return tasks.create(team, kw.pop("project", ""), title, created_by="user", **kw)


def inbox(name):
    return load_transport(name).peek() or []


def test_a_task_has_one_parent_and_no_cycles(team):
    a = new(team, "Platform reliability")
    b = new(team, "Postgres 18", parent=a["id"])
    c = new(team, "Rotate certs", parent=b["id"])
    with pytest.raises(tasks.TaskError, match="inside itself"):
        tasks.update(team, a["id"], "user", parent=c["id"])
    with pytest.raises(tasks.TaskError, match="no task"):
        new(team, "orphan", parent="task_nope")
    tasks.update(team, c["id"], "user", parent="")                   # to the top
    assert tasks.get(team, c["id"])["parent"] is None


def test_levels_are_worked_out_unless_set(team):
    proj = new(team, "Billing revamp", level=1)          # a project before it has tasks
    shape = tasks.tree(team)
    assert shape[proj["id"]]["level"] == 1
    leaf = new(team, "fix rounding", parent=proj["id"])
    mid = new(team, "invoices", parent=proj["id"])
    new(team, "dedupe", parent=mid["id"])                 # an extra layer under it
    shape = tasks.tree(team)
    assert shape[leaf["id"]]["level"] == 0 and shape[mid["id"]]["level"] == 1
    assert shape[proj["id"]]["level"] == 1, "a set level stays where it was set"
    top = new(team, "unset parent")
    new(team, "child", parent=top["id"])
    assert tasks.tree(team)[top["id"]]["level"] == 1


def test_work_rolls_up_and_archiving_hides_a_branch(team):
    p = new(team, "Launch")
    a = new(team, "copy", parent=p["id"], owner="coder")
    b = new(team, "site", parent=p["id"])
    c = new(team, "deploy", parent=b["id"])
    tasks.update(team, a["id"], "coder", status="done")
    shape = tasks.tree(team)
    assert (shape[p["id"]]["done"], shape[p["id"]]["total"]) == (1, 3)
    assert shape[c["id"]]["path"] == ["Launch", "site"]
    tasks.update(team, b["id"], "user", archived=True)
    shape = tasks.tree(team)
    assert shape[c["id"]]["archived_by"] == b["id"] and shape[a["id"]]["archived_by"] is None
    with pytest.raises(tasks.TaskError, match="operator"):
        tasks.update(team, p["id"], "coder", archived=True)


def test_the_last_child_closing_nudges_the_owner(team):
    p = new(team, "Q4 report", owner="tpm")
    kids = [new(team, f"part {i}", parent=p["id"], owner="coder") for i in range(2)]
    tasks.update(team, kids[0]["id"], "coder", status="done")
    assert inbox("tpm") == []
    tasks.update(team, kids[1]["id"], "coder", status="done")
    [m] = inbox("tpm")
    assert m.kind == "reminder" and m.task_id == p["id"] and "Every task under" in m.content
    assert tasks.get(team, p["id"])["status"] == "queued", "nudged, not closed"


def test_auto_close_closes_the_parent_and_climbs(team):
    top = new(team, "Program", auto_close=True)
    mid = new(team, "Project", parent=top["id"], auto_close=True)
    leaf = new(team, "Task", parent=mid["id"], owner="coder")
    tasks.update(team, leaf["id"], "coder", status="done")
    assert tasks.get(team, mid["id"])["status"] == "done"
    assert tasks.get(team, top["id"])["status"] == "done"


def test_a_failed_child_is_not_auto_closed_over(team):
    p = new(team, "Migration", auto_close=True)
    k = new(team, "copy rows", parent=p["id"], owner="coder")
    tasks.update(team, k["id"], "coder", status="failed")
    assert tasks.get(team, p["id"])["status"] == "queued"
    [m] = inbox("user")
    assert "did not finish" in m.content


def test_an_agent_s_new_task_goes_under_the_one_it_is_working_on(team):
    work = new(team, "Investigate slow queries", owner="coder")
    tasks.update(team, work["id"], "coder", status="running")
    out = mcp_tasks._create_task("coder", team, {"title": "add an index"})
    assert f"under {work['id']}" in out
    child = next(t for t in tasks.snapshot(team).values() if t["title"] == "add an index")
    assert child["parent"] == work["id"]
    mcp_tasks._create_task("coder", team, {"title": "unrelated", "parent": "none"})
    top = next(t for t in tasks.snapshot(team).values() if t["title"] == "unrelated")
    assert top["parent"] is None


def test_list_tasks_shows_a_branch_as_a_tree_and_hides_archived_ones(team):
    p = new(team, "Launch", level=1)
    a = new(team, "copy", parent=p["id"])
    new(team, "headline", parent=a["id"])
    out = mcp_tasks._list_tasks(team, {"under": p["id"]})
    lines = [l for l in out.splitlines() if l.lstrip().startswith("- task_")]
    assert [len(l) - len(l.lstrip()) for l in lines] == [0, 2, 4]
    assert "a project" in out and "0/2 under it closed" in out
    tasks.update(team, p["id"], "user", archived=True)
    assert "Launch" not in mcp_tasks._list_tasks(team, {})
    assert "Launch" in mcp_tasks._list_tasks(team, {"archived": True})


def test_a_wake_about_a_task_says_what_it_is_part_of(team):
    from agyteam.supervisor import Supervisor
    from fixture_runner import ScriptedRunner
    p = new(team, "Billing revamp", note="Customers are double-billed; stop it by Q4.")
    t = new(team, "dedupe invoices", parent=p["id"])
    sup = Supervisor(AGENTS, ScriptedRunner({}), quiet=True)
    try:
        prompt = sup.wake_prompt([Message("2026-10-27 10:00:00", "tpm", "coder", "please",
                                          task_id=t["id"])])
    finally:
        sup.close()
    assert "Where these tasks fit" in prompt and "Billing revamp" in prompt
    assert "double-billed" in prompt


def test_project_labels_become_parent_tasks(team):
    for i in range(2):
        new(team, f"billing {i}", project="billing")
    done = new(team, "old thing", project="legacy", owner="coder")
    tasks.update(team, done["id"], "coder", status="done")
    plan = actions.migrate_project_labels(team, dry_run=True)
    assert {r["project"]: r["tasks"] for r in plan} == {"billing": 2, "legacy": 1}
    actions.migrate_project_labels(team)
    state = tasks.snapshot(team)
    billing = next(t for t in state.values() if t["title"] == "billing")
    legacy = next(t for t in state.values() if t["title"] == "legacy")
    assert billing["level"] == 1 and billing["status"] == "queued"
    assert sum(t.get("parent") == billing["id"] for t in state.values()) == 2
    assert legacy["status"] == "done" and legacy["archived"]
    assert actions.migrate_project_labels(team, dry_run=True) == [], "run twice, nothing moves twice"


def test_a_team_names_its_levels_and_tokens_roll_up(team):
    doc = json.loads((team / "roster.json").read_text())
    doc["task_levels"] = ["task", "epic", "initiative"]
    (team / "roster.json").write_text(json.dumps(doc))
    assert tasks.level_names(team) == ["task", "epic", "initiative"]
    p = new(team, "Epic")
    k = new(team, "work", parent=p["id"])
    with (team / "events.jsonl").open("w") as f:
        f.write(json.dumps({"ts": "2026-10-27 10:00:00", "event": "turn", "agent": "coder",
                            "input_tokens": 300, "output_tokens": 0,
                            "trigger": {"task_ids": [k["id"]]}}) + "\n")
    tree = Record(team).task_tree()
    assert tree["levels"][1] == "epic" and tree["nodes"][p["id"]]["tokens"] == 300


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
