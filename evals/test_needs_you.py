"""What is waiting on a person.

Asked for after reading another harness's panel, which lists first whatever
needs a human: here, a question from an agent looked like any other message,
and scrolled away.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import actions  # noqa: E402
from agyteam import tasks as tl  # noqa: E402
from agyteam.record import Record  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({
        "operator": {"name": "matt", "handles": ["mattr"]},
        "agents": [{"name": "coder", "role": "builds"}, {"name": "qa", "role": "reviews"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    return td


def send(sender, to, text, **kw):
    bus = load_transport(sender)
    try:
        bus.send(to, text, **kw)
    finally:
        bus.close()


def kinds(team):
    return [r["kind"] for r in Record(team).needs_you()]


def test_nothing_is_waiting_on_a_quiet_team(team):
    assert Record(team).needs_you() == []


def test_a_question_waits_until_it_is_answered(team):
    send("coder", "user", "Which database?", kind="question")
    row = Record(team).needs_you()[0]
    assert row["kind"] == "question" and row["from"] == "coder" and row["dm"] == "coder"
    assert row["content"] == "Which database?"
    send("user", "coder", "Postgres.")
    assert kinds(team) == []


def test_a_reply_to_someone_else_does_not_answer_it(team):
    send("coder", "user", "Blocked on a key.", kind="blocker")
    send("user", "qa", "Unrelated.")
    assert kinds(team) == ["blocker"]


def test_an_ordinary_report_is_not_a_question(team):
    send("coder", "user", "Should I also fix the other one?")
    send("coder", "user", "Here it is.", kind="deliverable")
    send("coder", "qa", "Which one?", kind="question")
    assert kinds(team) == []


def test_a_later_question_is_not_answered_by_an_earlier_reply(team):
    send("user", "coder", "Hello.")
    send("coder", "user", "Which database?", kind="question")
    assert kinds(team) == ["question"]


def post(sender, text, **kw):
    bus = load_transport(sender)
    try:
        bus.post("dev", ["coder", "qa", "user"], text, **kw)
    finally:
        bus.close()


def test_a_question_in_a_channel_counts_when_it_names_the_person(team):
    post("coder", "@user which one?", kind="question", mentioned={"user"})
    post("coder", "@qa which one?", kind="question", mentioned={"qa"})
    rows = Record(team).needs_you()
    assert [r["channel"] for r in rows] == ["dev"]
    post("user", "The first.")
    assert kinds(team) == []


def test_tasks_the_person_owns_and_tasks_that_are_stuck(team):
    mine = tl.create(team, "", "sign the form", owner="user", created_by="coder")["id"]
    alias = tl.create(team, "", "named", owner="matt", created_by="coder")["id"]
    stuck = tl.create(team, "", "needs a key", owner="coder", created_by="coder")["id"]
    tl.update(team, stuck, "coder", status="blocked")
    tl.create(team, "", "parked", owner="user", created_by="user", status="icebox")
    closed = tl.create(team, "", "closed", owner="user", created_by="user")["id"]
    tl.update(team, closed, "user", status="done")
    rows = {r["id"]: r for r in Record(team).needs_you()}
    assert set(rows) == {mine, alias, stuck}
    assert rows[mine]["kind"] == "task" and rows[stuck]["kind"] == "stuck"
    assert rows[stuck]["owner"] == "coder"


def test_a_stopped_team_comes_first(team):
    send("coder", "user", "Which?", kind="question")
    (team / ".stop").write_text("the operator stopped it")
    rows = Record(team).needs_you()
    assert rows[0]["kind"] == "stopped" and rows[0]["reason"] == "the operator stopped it"


def event(team, name, agent):
    with (team / "events.jsonl").open("a") as f:
        f.write(json.dumps({"ts": "2026-11-02 12:00:00", "event": name, "agent": agent}) + "\n")


def test_a_notice_can_be_cleared_though_nothing_can_answer_it(team):
    """Reported: notices piled up, with no one to reply to and no other way."""
    send("supervisor", "user", "Moved a roster change back.", kind="blocker")
    send("coder", "user", "Which database?", kind="question")
    rows = Record(team).needs_you()
    notice = next(r for r in rows if r["from"] == "supervisor")
    assert actions.clear_waiting(team, ids=[notice["id"]], by="matt") == 1
    assert [r["from"] for r in Record(team).needs_you()] == ["coder"]
    assert "Moved a roster" in Record(team).messages(dm=("supervisor", "user"), include_plumbing=True)[0]["content"]


def test_all_notices_clear_at_once_and_questions_stay(team):
    for n in range(3):
        send("supervisor", "user", f"Notice {n}", kind="blocker")
    send("coder", "user", "Which database?", kind="question")
    assert actions.clear_waiting(team, sender="supervisor") == 3
    assert [r["from"] for r in Record(team).needs_you()] == ["coder"]
    assert actions.clear_waiting(team, sender="supervisor") == 0


def test_a_question_can_be_cleared_without_waking_the_agent(team):
    send("coder", "user", "Still want this?", kind="question")
    before = (team / "inbox" / "coder.jsonl").exists()
    actions.clear_waiting(team, ids=[Record(team).needs_you()[0]["id"]])
    assert kinds(team) == []
    assert (team / "inbox" / "coder.jsonl").exists() == before


def test_clearing_only_touches_what_is_waiting(team):
    assert actions.clear_waiting(team, ids=["nope"]) == 0
    assert not (team / "cleared.jsonl").read_text()


def test_a_failure_notice_goes_when_the_agent_recovers(team):
    event(team, "failure", "coder")
    bus = load_transport("supervisor")
    bus.send_kind("user", "coder has failed 3 turns in a row.", kind="blocker", failing="coder")
    bus.close()
    assert kinds(team) == ["blocker"]
    event(team, "failure", "coder")
    assert kinds(team) == ["blocker"]
    event(team, "turn", "qa")
    assert kinds(team) == ["blocker"], "someone else's turn says nothing about coder"
    event(team, "turn", "coder")
    assert kinds(team) == []


def test_rows_from_both_clocks_sort_together(team):
    tl.create(team, "", "mine", owner="user", created_by="coder")
    send("coder", "user", "Which?", kind="question")
    assert len(kinds(team)) == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
