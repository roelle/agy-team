"""Messages have a kind; quiet kinds wait for the next wake instead of causing one.

One review cost six messages and six model turns: request, forward, verdict,
cc, user, cc. The verdict now reaches the author as one message of kind
"review"; an ack, an fyi or a status note is left in the inbox and wakes
nobody; send_to_teammate says "queued", and whether anyone is running to
deliver it, instead of "delivered".
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import mcp_bus, mcp_tasks, tasks  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from agyteam.transport_file import FileTransport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["manager", "coder", "qa"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "gates", "is_principal": True},
        {"name": "coder", "role": "implements"},
        {"name": "qa", "role": "reviews"}]}))
    (td / "proof.py").write_text("def test_ok():\n    assert 1 == 1\n")
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AUDIT_LOG", raising=False)
    return td


def events(td, kind):
    return [json.loads(l) for l in (td / "events.jsonl").read_text().splitlines()
            if l.strip() and json.loads(l).get("event") == kind]


def test_a_quiet_message_wakes_nobody(team):
    load_transport("manager").send_kind("coder", "seen, thanks", kind="ack")
    load_transport("manager").send_kind("coder", "heads up", kind="fyi")
    runner = ScriptedRunner({})
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        assert sup.step() == 0
    finally:
        sup.close()
    assert runner.woken == []
    assert [m.kind for m in load_transport("coder").peek()] == ["ack", "fyi"]


def test_quiet_mail_is_read_on_the_next_wake(team):
    seen = {}

    class Reader(ScriptedRunner):
        def wake(self, agent, message):
            seen["prompt"] = message
            return super().wake(agent, message)

    load_transport("manager").send_kind("coder", "fyi: the build is green", kind="fyi")
    load_transport("manager").send_kind("coder", "please do X", kind="deliverable")
    sup = Supervisor(AGENTS, Reader({}), quiet=True)
    try:
        assert sup.step() == 1
    finally:
        sup.close()
    assert "the build is green" in seen["prompt"] and "please do X" in seen["prompt"]
    assert "(fyi)" in seen["prompt"] and "(deliverable)" in seen["prompt"]
    assert load_transport("coder").peek() == []
    disp = events(team, "dispatch")[-1]
    assert sorted(disp["trigger"]["kinds"]) == ["deliverable", "fyi"]
    turn = events(team, "turn")[-1]
    assert turn["trigger"]["senders"] == ["manager"] and turn["hop"] == 1


def test_a_review_round_trip_is_two_wakes_not_six(team):
    """coder asks qa; qa records the verdict; the verdict is the only
    message the author gets, and nobody forwards or acks anything."""
    class Team(ScriptedRunner):
        def wake(self, agent, message):
            self.woken.append(agent)
            if agent == "coder" and "build the buffer" in message:
                load_transport("coder").send_kind("qa", "please review the buffer",
                                                  kind="review")
            elif agent == "qa":
                out = mcp_bus._record_review(FileTransport("qa"), {
                    "what": "the buffer", "author": "coder", "verdict": "approved",
                    "proof_file": str(Path(team) / "proof.py"),
                    "findings": "clean"})
                assert out.startswith("[review recorded:"), out
            return "ok"

    load_transport("user").send("coder", "build the buffer")
    runner = Team({})
    sup = Supervisor(AGENTS, runner, quiet=True, require_review=False)
    try:
        sup.run_until_idle()
    finally:
        sup.close()
    assert runner.woken == ["coder", "qa", "coder"], runner.woken
    verdicts = [m for m in load_transport("coder").peek()]
    # the verdict was delivered and consumed by coder's third wake
    assert verdicts == []
    bus = [json.loads(l) for l in (team / "bus.jsonl").read_text().splitlines()]
    notice = [e for e in bus if e["to"] == "coder" and e.get("kind") == "review"]
    assert len(notice) == 1 and "approved" in notice[0]["content"]


def test_the_review_notice_reaches_the_task_creator_too(team):
    t = tasks.create(team, "p", "the buffer", owner="coder", created_by="manager")
    load_transport("coder").send("qa", "done")          # coder has worked
    out = mcp_bus._record_review(FileTransport("qa"), {
        "what": "the buffer", "author": "coder", "verdict": "approved",
        "proof_file": str(team / "proof.py"), "task_id": t["id"]})
    assert "[coder, manager notified]" in out
    mgr = load_transport("manager").peek()
    assert mgr and mgr[0].kind == "review" and mgr[0].task_id == t["id"]
    rows = [json.loads(l) for l in (team / "reviews.jsonl").read_text().splitlines()]
    assert rows[-1]["task_id"] == t["id"]


def test_completing_a_task_tells_its_creator(team):
    t = tasks.create(team, "p", "the buffer", owner="coder", created_by="manager")
    out = mcp_tasks._complete_task("coder", team, {"task_id": t["id"], "note": "shipped to /x"})
    assert "manager, who created it, has been told" in out
    mgr = load_transport("manager").peek()
    assert mgr[0].kind == "deliverable" and "shipped to /x" in mgr[0].content


def test_send_reports_queued_and_whether_anyone_will_deliver(team):
    out = mcp_bus._send(FileTransport("coder"), {"to": "qa", "content": "hi"})
    assert out.startswith("[queued for qa;") and "no supervisor is running" in out
    out = mcp_bus._send(FileTransport("coder"), {"to": "qa", "content": "hi", "kind": "ack"})
    assert "as ack" in out and "does not cause" in out
    out = mcp_bus._send(FileTransport("coder"), {"to": "qa", "content": "hi", "kind": "shout"})
    assert out.startswith("[error: kind must be one of")


def test_a_deliverable_for_the_user_waits_for_its_review(team):
    t = tasks.create(team, "p", "the answer", owner="coder", created_by="manager",
                     requires_review=True)
    args = {"to": "user", "content": "here it is", "task_id": t["id"]}
    out = mcp_bus._send(FileTransport("coder"), args)
    assert out.startswith("[refused:") and "requires an approved review" in out
    assert load_transport("user").peek() == []

    load_transport("coder").send("qa", "done")
    mcp_bus._record_review(FileTransport("qa"), {
        "what": "the answer", "author": "coder", "verdict": "approved",
        "proof_file": str(team / "proof.py"), "task_id": t["id"]})
    assert mcp_bus._send(FileTransport("coder"), args).startswith("[queued for the user")


def test_list_retros_reads_back_what_record_retro_wrote(team):
    from agyteam import retro_store
    assert "No recorded answers" in mcp_bus._list_retros(FileTransport("manager"))
    retro_store.record(team, "coder", "tests ran before every handoff",
                       "I reported a fix I had not executed", "no handoff without output")
    out = mcp_bus._list_retros(FileTransport("manager"))
    assert "## coder" in out and "had not executed" in out


def test_a_transport_without_kinds_still_shows_the_kind(team):
    class Bare(FileTransport):
        def send(self, to, content):          # the old two-argument signature
            return super().send(to, content)

    Bare("manager").send_kind("coder", "seen", kind="ack", task_id="task_1")
    got = load_transport("coder").peek()
    assert got[0].kind == "work" and got[0].content.startswith("[ack][task task_1] seen")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
