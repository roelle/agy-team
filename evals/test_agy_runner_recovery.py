"""The CLI runner's recovery from a failed resume must not strip the agent.

When resuming an agent's conversation failed, the runner retired it and
started a fresh one -- with the message it had prepared for *resuming*, which
carries no brief. The new conversation had no identity, no tool signatures and
no rules, and every later wake resumed it. It also did this on any failure, so
a provider's 503 discarded the agent's whole context.

A fake `agy` stands in for the CLI: it records the prompt it was given and
fails a resume the way it is told to.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam.runner_agy import AgyRunner  # noqa: E402

FAKE_AGY = """#!{python}
import json, os, sys
args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
resuming = "--conversation" in args
with open(os.environ["FAKE_AGY_LOG"], "a") as f:
    f.write(json.dumps({{"resuming": resuming, "prompt": prompt}}) + "\\n")
if resuming and os.environ.get("FAKE_AGY_RESUME_ERROR"):
    sys.stderr.write(os.environ["FAKE_AGY_RESUME_ERROR"])
    sys.exit(1)
print(json.dumps({{"conversation_id": "conv-new", "response": "ok",
                  "status": "SUCCESS"}}))
"""


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "coder", "role": "implements"}]}))
    (td / "conversations.json").write_text(json.dumps({"coder": "conv-old"}))
    binary = tmp_path / "agy"
    binary.write_text(FAKE_AGY.format(python=sys.executable))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.setenv("FAKE_AGY_LOG", str(log))
    return td, binary, log


def calls(log):
    return [json.loads(l) for l in log.read_text().splitlines()]


def test_a_fresh_conversation_after_a_dead_one_gets_the_brief(team, monkeypatch):
    td, binary, log = team
    monkeypatch.setenv("FAKE_AGY_RESUME_ERROR", "conversation conv-old not found")
    runner = AgyRunner({"binary": str(binary)})
    assert runner.wake("coder", "do the thing") == "ok"

    made = calls(log)
    assert [c["resuming"] for c in made] == [True, False]
    assert "You are 'coder'" in made[1]["prompt"], "the new conversation has no brief"
    assert "do the thing" in made[1]["prompt"]
    assert runner.conversation_id("coder") == "conv-new"


def test_a_transient_failure_keeps_the_conversation(team, monkeypatch):
    td, binary, log = team
    monkeypatch.setenv("FAKE_AGY_RESUME_ERROR", "503 UNAVAILABLE: high demand")
    runner = AgyRunner({"binary": str(binary)})
    reply = runner.wake("coder", "do the thing")

    assert reply.startswith("[error:")          # the supervisor requeues the mail
    assert [c["resuming"] for c in calls(log)] == [True]
    assert runner.conversation_id("coder") == "conv-old"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
