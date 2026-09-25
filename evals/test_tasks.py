"""tasks.py: the record long-running work is tracked against.

Covers the properties the module's own docstring claims: an append-only log
folds to current state per task id, `due()` finds only what has actually
come due, and re-reading the file from a fresh process reproduces the same
state a long-running one would have -- which is the whole of what "survives
a power cycle" means here, since there is no other state to lose.
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import tasks  # noqa: E402


@pytest.fixture
def team(tmp_path):
    td = tmp_path / "team"
    td.mkdir()
    return td


def test_create_returns_current_state(team):
    t = tasks.create(team, project="sim-sweep", title="run trial 3",
                     owner="coder", created_by="coder", note="pid 4821")
    assert t["project"] == "sim-sweep"
    assert t["title"] == "run trial 3"
    assert t["owner"] == "coder"
    assert t["status"] == "queued"
    assert t["note"] == "pid 4821"
    assert t["check_after"] is None
    assert t["id"].startswith("task_")


def test_create_requires_project_and_title(team):
    with pytest.raises(ValueError):
        tasks.create(team, project="", title="x")
    with pytest.raises(ValueError):
        tasks.create(team, project="x", title="")


def test_update_folds_onto_prior_state(team):
    t = tasks.create(team, project="p", title="run it", owner="coder")
    tasks.update(team, t["id"], "coder", status="running")
    got = tasks.get(team, t["id"])
    assert got["status"] == "running"
    # fields not touched by the update survive the fold
    assert got["project"] == "p"
    assert got["title"] == "run it"
    assert got["owner"] == "coder"


def test_update_on_unknown_task_returns_none(team):
    assert tasks.update(team, "task_doesnotexist", "coder", status="running") is None


def test_update_rejects_unknown_status(team):
    t = tasks.create(team, project="p", title="x")
    with pytest.raises(ValueError):
        tasks.update(team, t["id"], "coder", status="orbiting")


def test_check_after_round_trips(team):
    t = tasks.create(team, project="p", title="x", owner="coder")
    tasks.update(team, t["id"], "coder", status="blocked",
                check_after="2099-01-01T00:00:00Z")
    got = tasks.get(team, t["id"])
    assert got["check_after"] == "2099-01-01T00:00:00Z"


def test_clear_check_after_nulls_it_explicitly(team):
    t = tasks.create(team, project="p", title="x", owner="coder")
    tasks.update(team, t["id"], "coder", status="blocked",
                check_after="2099-01-01T00:00:00Z")
    tasks.update(team, t["id"], "coder", status="running",
                clear_check_after=True)
    assert tasks.get(team, t["id"])["check_after"] is None


def test_omitting_check_after_leaves_it_alone(team):
    """A caller that only wants to change the note must not silently clear
    the reminder they set on a previous call."""
    t = tasks.create(team, project="p", title="x", owner="coder")
    tasks.update(team, t["id"], "coder", status="blocked",
                check_after="2099-01-01T00:00:00Z")
    tasks.update(team, t["id"], "coder", note="still waiting")
    got = tasks.get(team, t["id"])
    assert got["check_after"] == "2099-01-01T00:00:00Z"
    assert got["note"] == "still waiting"


def test_due_finds_only_blocked_tasks_past_their_time(team):
    ready = tasks.create(team, project="p", title="ready", owner="coder")
    tasks.update(team, ready["id"], "coder", status="blocked",
                check_after="2000-01-01T00:00:00Z")  # long past

    not_yet = tasks.create(team, project="p", title="not yet", owner="coder")
    tasks.update(team, not_yet["id"], "coder", status="blocked",
                check_after="2099-01-01T00:00:00Z")  # far future

    running = tasks.create(team, project="p", title="running", owner="coder")
    tasks.update(team, running["id"], "coder", status="running")

    due_ids = {t["id"] for t in tasks.due(team)}
    assert due_ids == {ready["id"]}


def test_mark_swept_clears_check_after_so_it_is_not_resent(team):
    t = tasks.create(team, project="p", title="x", owner="coder")
    tasks.update(team, t["id"], "coder", status="blocked",
                check_after="2000-01-01T00:00:00Z")
    assert tasks.due(team)
    tasks.mark_swept(team, t["id"])
    assert tasks.due(team) == []
    # the task itself is not otherwise touched -- still blocked, just not due
    assert tasks.get(team, t["id"])["status"] == "blocked"


def test_list_tasks_filters(team):
    tasks.create(team, project="a", title="one", owner="coder")
    tasks.create(team, project="a", title="two", owner="qa")
    tasks.create(team, project="b", title="three", owner="coder")

    assert len(tasks.list_tasks(team)) == 3
    assert len(tasks.list_tasks(team, project="a")) == 2
    assert len(tasks.list_tasks(team, owner="coder")) == 2
    assert {t["title"] for t in tasks.list_tasks(team, project="a", owner="qa")} == {"two"}


def test_an_update_naming_a_task_never_created_is_dropped(team):
    """The fold replays forward only; a mutation with nothing to mutate must
    not conjure a task the create-time validation never ran against."""
    tasks.update(team, "task_neverexisted", "coder", status="running")
    assert tasks.snapshot(team) == {}


def test_unparseable_line_is_skipped_not_fatal(team):
    t = tasks.create(team, project="p", title="x")
    with tasks.path(team).open("a", encoding="utf-8") as f:
        f.write("not json at all\n")
    tasks.create(team, project="p", title="y")
    assert len(tasks.snapshot(team)) == 2


def test_power_cycle_is_just_reopening_the_file(team):
    """There is no in-memory state to lose. A 'restart' is reading the same
    log a second time, from a value that behaves like a fresh process would --
    a plain re-call of the module's own functions, since tasks.py holds
    nothing between calls."""
    t = tasks.create(team, project="sim-sweep", title="trial 9", owner="coder",
                     note="started at t0")
    tasks.update(team, t["id"], "coder", status="blocked",
                check_after="2000-01-01T00:00:00Z")

    # Simulate "the process restarted": read everything back from scratch,
    # exactly as a new Supervisor.__init__ / a new mcp_tasks process would.
    resumed = tasks.get(team, t["id"])
    assert resumed["status"] == "blocked"
    assert resumed["note"] == "started at t0"
    assert resumed["check_after"] == "2000-01-01T00:00:00Z"
    assert tasks.due(team)[0]["id"] == t["id"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
