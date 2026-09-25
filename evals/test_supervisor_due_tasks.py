"""A due task wakes its owner through the ordinary mail path, not a new one.

Supervisor._sweep_due_tasks() turns a task whose check_after has passed into
a bus message and lets step()'s existing per-agent fetch loop deliver it --
see agyteam/tasks.py's module docstring for why that beats a second wake
mechanism. These tests exercise the sweep the same way step() actually calls
it, not the tasks.due() query in isolation (that is evals/test_tasks.py's job).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import tasks  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["tpm", "coder"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "coordinates"},
        {"name": "coder", "role": "implements"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    return td


def make_supervisor(team_dir, script=None):
    runner = ScriptedRunner({"script": script or {}})
    sup = Supervisor(AGENTS, runner, team_dir=team_dir, max_hops=16, quiet=True)
    return sup, runner


def test_a_due_task_wakes_its_owner_with_no_other_mail(team):
    t = tasks.create(team, project="sim-sweep", title="check trial run",
                     owner="coder", created_by="coder")
    tasks.update(team, t["id"], "coder", status="blocked",
                check_after="2000-01-01T00:00:00Z")

    sup, runner = make_supervisor(team)
    try:
        dispatched = sup.step()
    finally:
        sup.close()

    assert dispatched == 1
    assert runner.woken == ["coder"]


def test_a_task_not_yet_due_wakes_nobody(team):
    t = tasks.create(team, project="sim-sweep", title="check trial run",
                     owner="coder", created_by="coder")
    tasks.update(team, t["id"], "coder", status="blocked",
                check_after="2099-01-01T00:00:00Z")

    sup, runner = make_supervisor(team)
    try:
        dispatched = sup.step()
    finally:
        sup.close()

    assert dispatched == 0
    assert runner.woken == []
    # and the task is still waiting, unmolested
    assert tasks.get(team, t["id"])["check_after"] == "2099-01-01T00:00:00Z"


def test_a_swept_task_is_not_resent_on_the_next_pass(team):
    t = tasks.create(team, project="p", title="x", owner="coder")
    tasks.update(team, t["id"], "coder", status="blocked",
                check_after="2000-01-01T00:00:00Z")

    sup, runner = make_supervisor(team)
    try:
        first = sup.step()
        second = sup.step()
    finally:
        sup.close()

    assert first == 1
    assert second == 0
    assert runner.woken == ["coder"]


def test_two_due_tasks_for_the_same_owner_arrive_as_one_wake(team):
    a = tasks.create(team, project="p", title="task a", owner="coder")
    b = tasks.create(team, project="p", title="task b", owner="coder")
    for t in (a, b):
        tasks.update(team, t["id"], "coder", status="blocked",
                    check_after="2000-01-01T00:00:00Z")

    sup, runner = make_supervisor(team)
    try:
        dispatched = sup.step()
    finally:
        sup.close()

    assert dispatched == 1
    assert runner.woken == ["coder"]
    assert tasks.due(team) == []


def test_a_due_task_owned_by_nobody_on_the_roster_is_skipped(team):
    """An owner who is not a live transport gets no message -- there is
    nowhere to send it -- and the task is left due rather than silently
    swept, so `agyteam.doctor` can still find it."""
    t = tasks.create(team, project="p", title="x", owner="ex-teammate",
                     created_by="coder")
    tasks.update(team, t["id"], "ex-teammate", status="blocked",
                check_after="2000-01-01T00:00:00Z")

    sup, runner = make_supervisor(team)
    try:
        dispatched = sup.step()
    finally:
        sup.close()

    assert dispatched == 0
    assert runner.woken == []
    assert tasks.due(team)  # still due; nobody swept it


def test_going_idle_with_reminders_set_says_so(team, capsys):
    """A --say run exits when the team idles. A reminder due tomorrow is then
    delivered by nothing, and "team went idle" alone reads as "nothing left"."""
    t = tasks.create(team, project="p", title="x", owner="coder")
    tasks.update(team, t["id"], "coder", status="running",
                 check_after="2099-01-01T00:00:00Z")
    runner = ScriptedRunner({})
    sup = Supervisor(AGENTS, runner, team_dir=team, quiet=False)
    try:
        sup.run_until_idle()
    finally:
        sup.close()
    out = capsys.readouterr().out
    assert "1 task reminder(s) still set" in out
    assert "python -m agyteam.tasks sweep" in out


def test_run_until_idle_completes_after_delivering_one_due_task(team):
    t = tasks.create(team, project="p", title="x", owner="coder")
    tasks.update(team, t["id"], "coder", status="blocked",
                check_after="2000-01-01T00:00:00Z")

    sup, runner = make_supervisor(team)
    try:
        turns = sup.run_until_idle()
    finally:
        sup.close()

    assert turns == 1
    assert sup.stopped == "team went idle"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
