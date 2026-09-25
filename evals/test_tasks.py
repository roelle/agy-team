"""tasks.py: the record long-running work is tracked against.

Covers the properties the module's own docstring claims: an append-only log
folds to current state per task id; a reminder is parsed when it is set or
refused; it fires on any unfinished task and is delivered once; a claim has
exactly one winner however many agents make it at once; and re-reading the
file reproduces the state a long-running process would have held -- which is
the whole of what "survives a power cycle" means here.
"""
import calendar
import json
import multiprocessing
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import doctor, mcp_tasks, tasks  # noqa: E402

NOON = calendar.timegm((2026, 9, 24, 12, 0, 0))    # 2026-09-24T12:00:00Z
PAST = "2000-01-01T00:00:00Z"
FUTURE = "2099-01-01T00:00:00Z"


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    td.mkdir()
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "coder", "role": "implements"},
        {"name": "qa", "role": "reviews"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    tasks._quiet.clear()
    return td


def remind(team, owner="coder", when=PAST, status="blocked", title="x"):
    t = tasks.create(team, project="p", title=title, owner=owner, created_by=owner)
    tasks.update(team, t["id"], owner, status=status, check_after=when)
    return t


# --- the record ---------------------------------------------------------------

def test_create_returns_current_state(team):
    t = tasks.create(team, project="sim-sweep", title="run trial 3",
                     owner="coder", created_by="coder", note="pid 4821")
    assert (t["project"], t["title"], t["owner"], t["status"], t["note"]) == \
        ("sim-sweep", "run trial 3", "coder", "queued", "pid 4821")
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
    assert (got["status"], got["project"], got["title"], got["owner"]) == \
        ("running", "p", "run it", "coder")


def test_update_on_unknown_task_returns_none(team):
    assert tasks.update(team, "task_doesnotexist", "coder", status="running") is None


def test_update_rejects_unknown_status(team):
    t = tasks.create(team, project="p", title="x")
    with pytest.raises(ValueError):
        tasks.update(team, t["id"], "coder", status="orbiting")


def test_an_update_naming_a_task_never_created_is_dropped(team):
    tasks._append(team, {"id": "task_ghost", "kind": "updated", "status": "running"})
    assert tasks.snapshot(team) == {}


def test_unparseable_line_is_skipped_not_fatal(team):
    tasks.create(team, project="p", title="x")
    with tasks.path(team).open("a", encoding="utf-8") as f:
        f.write("not json at all\n")
    tasks.create(team, project="p", title="y")
    assert len(tasks.snapshot(team)) == 2


def test_list_tasks_filters(team):
    tasks.create(team, project="a", title="one", owner="coder")
    tasks.create(team, project="a", title="two", owner="qa")
    tasks.create(team, project="b", title="three", owner="coder")
    assert len(tasks.list_tasks(team)) == 3
    assert len(tasks.list_tasks(team, project="a")) == 2
    assert len(tasks.list_tasks(team, owner="coder")) == 2
    assert {t["title"] for t in tasks.list_tasks(team, project="a", owner="qa")} == {"two"}


def test_power_cycle_is_just_reopening_the_file(team):
    t = remind(team)
    tasks._quiet.clear()        # a new process starts with nothing cached
    resumed = tasks.get(team, t["id"])
    assert (resumed["status"], resumed["check_after"]) == ("blocked", PAST)
    assert tasks.due(team)[0]["id"] == t["id"]


# --- ownership ------------------------------------------------------------------

def test_only_the_owner_may_change_an_owned_task(team):
    t = tasks.create(team, project="p", title="x", owner="coder")
    with pytest.raises(tasks.TaskError, match="owned by 'coder'"):
        tasks.update(team, t["id"], "qa", status="done")


def test_the_owner_can_hand_a_task_off(team):
    """claim_task refuses an owned task and tells the claimant to ask for a
    hand-off; that advice is only honest if a hand-off exists."""
    t = tasks.create(team, project="p", title="x", owner="coder")
    tasks.update(team, t["id"], "coder", owner="qa")
    assert tasks.get(team, t["id"])["owner"] == "qa"
    tasks.update(team, t["id"], "qa", status="running")


def test_claim_refuses_a_task_someone_else_owns(team):
    t = tasks.create(team, project="p", title="x", owner="coder")
    with pytest.raises(tasks.TaskError, match="update_task\\(owner='qa'\\)"):
        tasks.claim(team, t["id"], "qa")


def _claimer(team_dir, task_id, name, barrier, results):
    barrier.wait()
    try:
        won = tasks.claim(team_dir, task_id, name) is not None
    except tasks.TaskError:
        won = False
    results.put(won)


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(),
                    reason="needs fork to race processes cheaply")
def test_of_eight_simultaneous_claims_exactly_one_wins(team):
    """Before the lock, eight agents claiming one task produced more than one
    winner in every one of twenty trials -- a duplicate sim launch each time."""
    ctx = multiprocessing.get_context("fork")
    for trial in range(10):
        t = tasks.create(team, project="p", title=f"trial {trial}")
        barrier, results = ctx.Barrier(8), ctx.Queue()
        procs = [ctx.Process(target=_claimer,
                             args=(team, t["id"], f"agent{i}", barrier, results))
                 for i in range(8)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)
        wins = sum(results.get(timeout=5) for _ in procs)
        assert wins == 1, f"trial {trial}: {wins} agents were told they won"


# --- reminder times -------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("90m", "2026-09-24T13:30:00Z"),
    ("2H", "2026-09-24T14:00:00Z"),
    ("1.5h", "2026-09-24T13:30:00Z"),
    ("1 hour", "2026-09-24T13:00:00Z"),
    ("90 minutes", "2026-09-24T13:30:00Z"),
    ("1d", "2026-09-25T12:00:00Z"),
    ("2026-09-26T05:00:00Z", "2026-09-26T05:00:00Z"),
    ("2026-09-26T05:00:00-07:00", "2026-09-26T12:00:00Z"),
])
def test_reminder_times_are_normalised(raw, expected):
    assert tasks.parse_when(raw, now=NOON) == expected


@pytest.mark.parametrize("raw", [
    "tomorrow",                 # stored verbatim, it sorted after every year
    "2026-9-25T06:00:00Z",      # unpadded: would have waited until 2027
    "2026-09-26 05:00",         # no zone: would have been read as UTC
    "",
    "-5m",
])
def test_reminder_times_that_cannot_be_read_are_refused(raw):
    with pytest.raises(tasks.TaskError):
        tasks.parse_when(raw, now=NOON)


def test_a_bad_reminder_is_refused_before_anything_is_written(team):
    t = tasks.create(team, project="p", title="x", owner="coder")
    before = tasks.path(team).read_text()
    with pytest.raises(tasks.TaskError):
        tasks.update(team, t["id"], "coder", status="blocked", check_after="tomorrow")
    assert tasks.path(team).read_text() == before


# --- which reminders are due ------------------------------------------------------

def test_a_reminder_fires_on_any_unfinished_task(team):
    """A reminder used to fire only on 'blocked'. An agent that has just
    started a sim marks it 'running', and that reminder was accepted and then
    never delivered."""
    running = remind(team, status="running")
    assert [t["id"] for t in tasks.due(team)] == [running["id"]]


def test_a_reminder_not_yet_due_is_not_due(team):
    remind(team, when=FUTURE)
    assert tasks.due(team) == []


def test_finishing_a_task_removes_its_reminder(team):
    t = remind(team, when=FUTURE)
    tasks.update(team, t["id"], "coder", status="done")
    assert tasks.get(team, t["id"])["check_after"] is None
    assert tasks.reminders(team) == []


def test_a_reminder_on_finished_work_is_refused(team):
    t = tasks.create(team, project="p", title="x", owner="coder")
    tasks.update(team, t["id"], "coder", status="done")
    with pytest.raises(tasks.TaskError, match="would never fire"):
        tasks.update(team, t["id"], "coder", check_after="1h")


def test_omitting_check_after_leaves_it_alone(team):
    t = remind(team, when=FUTURE)
    tasks.update(team, t["id"], "coder", note="still waiting")
    assert tasks.get(team, t["id"])["check_after"] == FUTURE


def test_an_unreadable_stored_reminder_fires_rather_than_never(team):
    """What an older version stored verbatim. Early and noisy beats silent."""
    t = tasks.create(team, project="p", title="x", owner="coder")
    tasks._append(team, {"id": t["id"], "kind": "updated", "status": "blocked",
                         "check_after": "tomorrow"})
    sent = []
    tasks.sweep(team, lambda to, c: sent.append((to, c)) or "[delivered]")
    assert len(sent) == 1 and "could not be read" in sent[0][1]


# --- delivery ---------------------------------------------------------------------

def test_sweep_delivers_each_reminder_once(team):
    remind(team)
    sent = []
    send = lambda to, c: sent.append(to) or f"[delivered to {to}]"
    assert list(tasks.sweep(team, send)) == ["coder"]
    assert tasks.sweep(team, send) == {}
    assert sent == ["coder"]


def test_reminders_for_one_owner_arrive_as_one_message(team):
    remind(team, title="a")
    remind(team, title="b")
    sent = []
    tasks.sweep(team, lambda to, c: sent.append(c) or "[delivered]")
    assert len(sent) == 1 and "2 reminders" in sent[0]


@pytest.mark.parametrize("failure", ["error-string", "exception"])
def test_a_reminder_that_could_not_be_sent_stays_armed(team, failure):
    remind(team)

    def send(to, content):
        if failure == "exception":
            raise OSError("disk full")
        return "[error: no teammate named 'coder']"

    assert tasks.sweep(team, send) == {}
    assert len(tasks.due(team)) == 1
    assert list(tasks.sweep(team, lambda to, c: "[delivered]")) == ["coder"]


def test_sweep_only_delivers_to_the_owners_it_is_given(team):
    remind(team, owner="ex-teammate")
    assert tasks.sweep(team, lambda to, c: "[delivered]", owners={"coder"}) == {}
    assert len(tasks.due(team)) == 1


def test_an_idle_sweep_still_notices_a_reminder_coming_due(team):
    """The cache that lets an idle poll skip the read must not skip a
    reminder whose time arrives while the file is unchanged."""
    remind(team, when="2026-09-24T13:00:00Z")
    send = lambda to, c: "[delivered]"
    assert tasks.sweep(team, send, now="2026-09-24T12:00:00Z") == {}
    assert tasks.sweep(team, send, now="2026-09-24T12:59:59Z") == {}
    assert list(tasks.sweep(team, send, now="2026-09-24T13:00:00Z")) == ["coder"]


def test_two_sweepers_at_once_deliver_each_reminder_once(team):
    for i in range(5):
        remind(team, title=f"t{i}")
    sent, gate = [], threading.Barrier(2)

    def send(to, content):
        sent.append(content)
        return "[delivered]"

    def run():
        gate.wait()
        tasks.sweep(team, send)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(sent) == 1 and "5 reminders" in sent[0]


def test_the_sweep_runs_from_the_command_line(team):
    """A host that does not run the supervisor calls this from its own
    scheduler; the reminder must land in the owner's inbox."""
    remind(team)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "AGYTEAM_TEAM_DIR": str(team)}
    out = subprocess.run([sys.executable, "-m", "agyteam.tasks", "sweep",
                          "--team-dir", str(team)],
                         capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "reminded coder" in out.stdout
    inbox = (team / "inbox" / "coder.jsonl").read_text()
    assert "come due" in inbox


# --- the agent-facing tools ---------------------------------------------------------

def dispatcher(agent, team, monkeypatch):
    """The server's dispatch function, as the stdio loop would call it."""
    captured = {}
    monkeypatch.setattr(mcp_tasks, "serve",
                        lambda name, tools, dispatch: captured.update(d=dispatch))
    mcp_tasks.main(agent, team_dir=team)
    return captured["d"]


def test_tool_refusals_are_sentences_not_exceptions(team, monkeypatch):
    t = tasks.create(team, project="p", title="x", owner="coder")
    coder = dispatcher("coder", team, monkeypatch)
    reply = coder("update_task", {"task_id": t["id"], "check_after": "tomorrow"})
    assert reply.startswith("[error:") and "not a time this can read" in reply
    assert coder("claim_task", {"task_id": t["id"]}).startswith("[claimed]")

    qa = dispatcher("qa", team, monkeypatch)
    reply = qa("claim_task", {"task_id": t["id"]})
    assert reply.startswith("[error:") and "update_task(owner='qa')" in reply
    reply = qa("update_task", {"task_id": t["id"], "status": "done"})
    assert reply.startswith("[error:") and "owned by 'coder'" in reply


def test_setting_a_reminder_says_what_delivers_it(team):
    out = mcp_tasks._create_task("coder", team, {"project": "p", "title": "sim",
                                                 "check_after": "1h"})
    assert "tasks sweep" in out and "supervisor" in out


def test_handing_off_to_someone_not_on_the_roster_is_refused(team):
    t = tasks.create(team, project="p", title="x", owner="coder")
    out = mcp_tasks._update_task("coder", team, {"task_id": t["id"], "owner": "nobody"})
    assert out.startswith("[error:") and "not on this roster" in out


def test_complete_task_clears_the_reminder(team):
    t = remind(team, when=FUTURE)
    mcp_tasks._complete_task("coder", team, {"task_id": t["id"]})
    got = tasks.get(team, t["id"])
    assert (got["status"], got["check_after"]) == ("done", None)


# --- the preflight -------------------------------------------------------------------

class FakeReport(doctor.Report):
    def __init__(self):
        super().__init__()
        self.lines = []

    def line(self, mark, text):
        self.lines.append((mark.strip(), text))
        super().line(mark, text)

    def detail(self, text):
        pass


def test_doctor_says_when_nothing_is_sweeping(team):
    remind(team)                    # due since 2000: nobody delivered it
    r = FakeReport()
    doctor.check_tasks_log(r, team)
    assert any(m == "warn" and "nothing is sweeping" in t for m, t in r.lines)


def test_doctor_is_quiet_about_reminders_not_yet_due(team):
    remind(team, when=FUTURE)
    r = FakeReport()
    doctor.check_tasks_log(r, team)
    assert not any("overdue" in t for _, t in r.lines)
    assert not r.failed


def test_doctor_fails_on_a_task_owned_by_someone_gone(team):
    tasks.create(team, project="p", title="x", owner="ex-teammate")
    r = FakeReport()
    doctor.check_tasks_log(r, team)
    assert r.failed


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
