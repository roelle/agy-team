"""Memories over time: their history, a review that consolidates them, and
lessons several agents learned separately.

A store kept one fact about an entry's history, its last save. Distill only
added and updated; nothing re-read what was stored, and in a six-week team
more than half the memories had never been read back. review_memory could
flag but not merge, and a delete was gone for good.
"""
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import actions, distill_queue, mcp_memory, memory_log  # noqa: E402
from agyteam import memory as memory_lib  # noqa: E402
from agyteam.record import Record  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["tpm", "coder", "qa"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "teams" / "demo" / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": a, "role": "x"} for a in AGENTS]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.setenv("AGYTEAM_DURABLE_DIR", str(td.parent))    # this test's own agents
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    for var in ("AGYTEAM_WORKSPACE", "AGYTEAM_MEMORY_STORE", "AGYTEAM_MEMORY_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    return td


def tools(team, agent):
    return mcp_memory.handlers(memory_log.open_store(team, agent), team)


def save(t, name, content, description=None):
    return t["save_memory"]({"name": name, "description": description or name,
                             "content": content, "why": "test"})


def test_saves_reads_and_merges_are_kept_and_a_merge_keeps_its_history(team):
    t = tools(team, "coder")
    save(t, "build-cmd", "make all builds everything")
    save(t, "build-cmd", "make all; make test runs the suite")
    save(t, "test-cmd", "pytest -q in evals")
    for _ in range(3):
        t["read_memory"]({"name": "build-cmd"})
    t["read_memory"]({"name": "test-cmd"})
    out = t["merge_memories"]({"names": "build-cmd, test-cmd", "into": "build-and-test",
                               "description": "how to build and test",
                               "content": "make all; make test; pytest -q in evals"})
    assert "archived" in out
    st = Record(team).memory_stats("coder")
    [m] = st
    assert m["name"] == "build-and-test"
    assert m["reads"] == 4 and m["revisions"] == 4, "the merged memory lost its sources' history"
    assert m["created"] is not None
    gone = sorted(d["name"] for d in memory_log.archived(team, "coder"))
    assert gone == ["build-cmd", "test-cmd"]


def test_a_merge_needs_real_sources_and_content(team):
    t = tools(team, "coder")
    save(t, "a", "one")
    assert "no memory named" in t["merge_memories"]({"names": "a, nope", "into": "a",
                                                     "description": "d", "content": "x"})
    save(t, "b", "two")
    assert "write the merged content" in t["merge_memories"]({"names": "a,b", "into": "a",
                                                              "description": "d", "content": " "})
    assert sorted(e.name for e in memory_log.open_store(team, "coder").index()) == ["a", "b"]


def test_a_deleted_memory_is_archived_and_the_operator_can_restore_it(team):
    t = tools(team, "qa")
    save(t, "flaky-test", "test_x is flaky under load", "test_x flakes")
    assert "archived" in t["delete_memory"]({"name": "flaky-test"})
    [doc] = actions.archived_memories(team, "qa")
    assert doc["name"] == "flaky-test" and doc["description"] == "test_x flakes"
    actions.restore_memory(team, "qa", doc["id"])
    [m] = actions.memories(team, "qa")
    assert m["name"] == "flaky-test" and "flaky under load" in m["content"]
    assert actions.archived_memories(team, "qa") == []


def test_reads_from_before_the_log_come_from_the_event_log(team):
    t = tools(team, "tpm")
    store = memory_log.open_store(team, "tpm")
    store.save("old-lesson", "old", "from before", why="w", when="2026-09-01 10:00:00")
    with (team / "events.jsonl").open("w") as f:
        for _ in range(2):
            f.write(json.dumps({"ts": "2026-09-02 10:00:00", "event": "tool_call",
                                "agent": "tpm", "tool": "read_memory",
                                "args": {"Arguments": {"name": "old-lesson"}}}) + "\n")
    t["read_memory"]({"name": "old-lesson"})
    [m] = Record(team).memory_stats("tpm")
    assert m["reads"] == 3 and m["known_since"] == "2026-09-01 10:00:00" and not m["created"]


# --- the review turn ----------------------------------------------------------------

class Reviewer(ScriptedRunner):
    """On a review prompt, merges two of its memories the way an agent would."""

    def __init__(self, team):
        super().__init__({})
        self.team, self.prompts = team, []

    def wake(self, agent, message):
        self.prompts.append(message)
        if "Review your own memories" in message:
            tools(self.team, agent)["merge_memories"]({
                "names": "m0,m1", "into": "m0", "description": "merged",
                "content": "both details: alpha and beta"})
            return "merged m0 and m1"
        return "ok"


def fill(team, agent, n):
    t = tools(team, agent)
    for i in range(n):
        save(t, f"m{i}", f"lesson {i} about topic {i}")


def test_a_review_turn_shows_the_history_and_records_what_changed(team):
    fill(team, "coder", 12)
    tools(team, "coder")["read_memory"]({"name": "m3"})
    runner = Reviewer(team)
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        out = sup.review_memory("coder")
    finally:
        sup.close()
    prompt = runner.prompts[0]
    assert "[m3]" in prompt and "reads 1" in prompt and "last read never" in prompt
    assert out["before"] == 12 and out["after"] == 11 and out["removed"] == ["m1"]
    events = [json.loads(l) for l in (team / "events.jsonl").read_text().splitlines()]
    assert any(e.get("event") == "memory_review" and e["agent"] == "coder" for e in events)
    assert json.loads((team / "memory_review.json").read_text())["coder"]["count"] == 11


def test_reviews_come_due_at_milestones_first_time_and_weekly(team):
    fill(team, "coder", 26)
    fill(team, "qa", 5)
    sup = Supervisor(AGENTS, ScriptedRunner({}), quiet=True)
    try:
        due = sup.memory_reviews_due()
        assert set(due) == {"coder"} and "passed 25" in due["coder"]
        state = {"coder": {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "count": 26, "milestone": 25}}
        (team / "memory_review.json").write_text(json.dumps(state))
        assert sup.memory_reviews_due() == {}
        state["coder"]["ts"] = "2026-01-01 00:00:00"
        (team / "memory_review.json").write_text(json.dumps(state))
        assert "days since" in sup.memory_reviews_due()["coder"]
        doc = json.loads((team / "roster.json").read_text())
        doc["memory_review"] = False
        (team / "roster.json").write_text(json.dumps(doc))
        assert sup.memory_reviews_due() == {}
    finally:
        sup.close()


def test_a_due_review_is_queued_by_the_daemon_and_run_between_turns(team):
    fill(team, "coder", 12)
    runner = Reviewer(team)
    sup = Supervisor(AGENTS, runner, quiet=True)
    assert sup._claim_team()
    try:
        sup._schedule_memory_reviews()
        assert distill_queue.pending(team) == [], "only a daemon schedules reviews"
        sup._daemon = True
        sup.inflight["coder"] = {"handle": {}, "msgs": [], "ts": "", "hop": 0,
                                 "trigger": {}, "started": time.monotonic()}
        sup._schedule_memory_reviews()
        assert [r["action"] for r in distill_queue.pending(team)] == ["review"]
        sup._serve_distill_requests()
        assert runner.prompts == [], "a review ran into a turn in flight"
        del sup.inflight["coder"]
        sup._serve_distill_requests()
        assert len(runner.prompts) == 1 and distill_queue.pending(team) == []
    finally:
        sup.close()


# --- the same lesson, learned separately -----------------------------------------------

LESSON = ("Drive the real code path in review: a test against a static mock passed while "
          "the deployed binary failed, so run the actual binary end to end before approving")


def test_a_lesson_three_agents_learned_is_a_norm_candidate(team):
    for agent in AGENTS:
        save(tools(team, agent), f"{agent}-review-real-path", LESSON + f" ({agent})",
             "review by driving the real path, not mocks")
    save(tools(team, "coder"), "unrelated", "the billing database uses UTC timestamps")
    groups = Record(team).norm_candidates()
    assert len(groups) == 1 and groups[0]["agents"] == sorted(AGENTS)
    assert "binary" in groups[0]["terms"] and "2026" not in groups[0]["terms"]
    sup = Supervisor(AGENTS, ScriptedRunner({}), quiet=True)
    try:
        assert "learned separately" in sup._norm_candidates_text()
    finally:
        sup.close()


def test_two_agents_are_not_a_norm(team):
    for agent in AGENTS[:2]:
        save(tools(team, agent), f"{agent}-lesson", LESSON)
    assert Record(team).norm_candidates() == []


def test_review_requests_need_a_running_supervisor(team):
    fill(team, "coder", 1)
    with pytest.raises(actions.OperatorError, match="no supervisor"):
        actions.review_memory(team, "coder")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))


# --- memory turns on a stronger model ---------------------------------------------------

class ModelSeen(ScriptedRunner):
    """Records the model each turn runs on, as a CLI-runner command would."""
    switches_model_in_conversation = True

    def __init__(self):
        super().__init__({})
        self.seen = []

    def wake(self, agent, message):
        self.seen.append((agent, self.resolved_model(agent, "task-model")[0],
                          "Review" if "Review your own" in message else
                          "distill" if "save_memory" in message else "work"))
        return "ok"


def set_roster(team, **top):
    doc = json.loads((team / "roster.json").read_text())
    doc.update(top)
    (team / "roster.json").write_text(json.dumps(doc))


def test_memory_turns_run_on_the_memory_model_and_work_turns_do_not(team):
    set_roster(team, memory_model="strong-model", memory_effort="high")
    fill(team, "coder", 3)
    runner = ModelSeen()
    sup = Supervisor(AGENTS, runner, quiet=True, require_review=False)
    try:
        sup.distill("coder")
        sup.review_memory("coder")
        from agyteam.transport import load as load_transport
        load_transport("user").send("coder", "do the work")
        sup.step()
    finally:
        sup.close()
    assert [(m, k) for _, m, k in runner.seen] == [
        ("strong-model", "distill"), ("strong-model", "Review"), ("task-model", "work")]


def test_a_runtime_that_cannot_switch_models_keeps_the_agents_own(team, capsys):
    set_roster(team, memory_model="strong-model")
    runner = ModelSeen()
    runner.switches_model_in_conversation = False
    sup = Supervisor(AGENTS, runner, quiet=False)
    try:
        sup.distill("coder")
    finally:
        sup.close()
    assert runner.seen[0][1] == "task-model"
    assert "cannot switch models inside a conversation" in capsys.readouterr().out


def test_an_agents_own_memory_model_beats_the_teams(team):
    doc = json.loads((team / "roster.json").read_text())
    doc["memory_model"] = "team-strong"
    doc["agents"][1]["memory_model"] = "coder-strong"
    (team / "roster.json").write_text(json.dumps(doc))
    sup = Supervisor(AGENTS, ScriptedRunner({}), quiet=True)
    try:
        assert sup.memory_model("coder") == ("coder-strong", None)
        assert sup.memory_model("qa") == ("team-strong", None)
    finally:
        sup.close()


# --- reviews see what happened --------------------------------------------------------------

def test_a_review_sees_requested_changes_failed_tasks_and_what_the_operator_said(team):
    fill(team, "coder", 2)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with (team / "reviews.jsonl").open("w") as f:
        f.write(json.dumps({"ts": now, "reviewer": "qa", "author": "coder", "what": "the parser fix",
                            "verdict": "changes_requested", "findings": "tested a mock, not the binary"}) + "\n")
        f.write(json.dumps({"ts": now, "reviewer": "qa", "author": "tpm", "what": "not mine",
                            "verdict": "changes_requested", "findings": "someone else's"}) + "\n")
    from agyteam import tasks as tasks_lib
    t = tasks_lib.create(team, "p", "flaky importer", owner="coder", created_by="tpm")
    tasks_lib.update(team, t["id"], by="coder", status="failed", note="never reproduced the bug")
    with (team / "bus.jsonl").open("w") as f:
        f.write(json.dumps({"ts": now, "from": "user", "to": "coder", "content": "stop guessing paths"}) + "\n")
        f.write(json.dumps({"ts": now, "from": "user", "to": "#general", "channel": "general",
                            "content": "@coder please check the logs first"}) + "\n")
        f.write(json.dumps({"ts": now, "from": "user", "to": "qa", "content": "not for coder"}) + "\n")
    with (team / "events.jsonl").open("a") as f:
        f.write(json.dumps({"ts": now, "event": "failure", "agent": "coder",
                            "error": "[error: coder timed out after 900s]"}) + "\n")
    sup = Supervisor(AGENTS, ScriptedRunner({}), quiet=True)
    try:
        prompt = sup._memory_review_prompt("coder")
    finally:
        sup.close()
    assert "tested a mock, not the binary" in prompt and "someone else's" not in prompt
    assert "flaky importer" in prompt and "never reproduced the bug" in prompt
    assert "stop guessing paths" in prompt and "check the logs first" in prompt
    assert "not for coder" not in prompt
    assert "timed out" not in prompt, "a runtime failure is not a lesson about the work"


def test_the_distill_prompt_asks_for_lessons_that_transfer_and_can_be_found():
    from agyteam.supervisor import DISTILL_PROMPT
    assert "next task" in DISTILL_PROMPT and "says when it applies" in DISTILL_PROMPT
