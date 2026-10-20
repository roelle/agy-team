"""Mail pulled mid-turn with check_inbox, then the turn fails (--inbox-pull).

Reported downstream: an agent runs for minutes, sends an early progress
note, and calls check_inbox before finishing, as it must. A teammate's mail
that arrived while the turn ran is marked read by that call. If the turn
then crashed, that mail was gone: the supervisor restored only its own wake
batch, and only when the turn had sent nothing.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import config, mcp_bus, receipts  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from agyteam.transport_file import FileTransport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["tpm", "coder", "qa"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "coordinates"}, {"name": "coder", "role": "implements"},
        {"name": "qa", "role": "reviews"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    monkeypatch.setattr(config, "WAKE_BACKOFF_BASE_S", 0.0)
    monkeypatch.setattr(config, "WAKE_BACKOFF_MAX_S", 0.0)
    return td


def inbox(name):
    return load_transport(name).peek() or []


class LongTurn(ScriptedRunner):
    """Sends progress, gets new mail mid-turn, pulls it, then (first time) dies."""
    inbox_pull = True

    def __init__(self, fail_first=True):
        super().__init__({})
        self.fail_first, self.turns, self.pulled = fail_first, 0, []

    def wake(self, agent, message):
        self.turns += 1
        me = FileTransport(agent)
        if self.turns == 1:
            me.send("tpm", "on it", kind="status")                     # early progress
            load_transport("qa").send("coder", "also: the date parser")  # arrives mid-turn
        self.pulled.append(mcp_bus._check_inbox(me))
        if self.fail_first and self.turns == 1:
            return "[error: agy exited -9]"
        return "ok"


def test_mail_pulled_mid_turn_comes_back_when_the_turn_fails(team):
    load_transport("tpm").send("coder", "fix the parser")
    runner = LongTurn()
    sup = Supervisor(AGENTS, runner, quiet=True, require_review=False)
    try:
        sup.step()
        back = inbox("coder")
        assert sorted(m.content for m in back) == ["also: the date parser", "fix the parser"], \
            "pulled mail was lost with the turn"
        assert all(m.redelivered and "1 status" in m.redelivered for m in back), \
            "redelivered mail should say what the failed turn had already sent"
        assert not receipts.path(team, "coder").exists()
        sup._backoff.clear()
        sup.step()
    finally:
        sup.close()
    assert "[redelivered:" in runner.pulled[1] and "check what you already did" in runner.pulled[1]
    assert inbox("coder") == [], "a successful turn's mail is done with"
    assert not receipts.path(team, "coder").exists()


def test_a_successful_turn_restores_nothing(team):
    load_transport("tpm").send("coder", "fix the parser")
    sup = Supervisor(AGENTS, LongTurn(fail_first=False), quiet=True, require_review=False)
    try:
        sup.step()
    finally:
        sup.close()
    assert inbox("coder") == []
    assert not receipts.path(team, "coder").exists()


def test_without_a_supervised_turn_check_inbox_keeps_no_receipt(team):
    load_transport("tpm").send("coder", "hello")
    assert "hello" in mcp_bus._check_inbox(FileTransport("coder"))
    assert not receipts.path(team, "coder").exists()
    assert inbox("coder") == []


def test_restored_mail_is_given_back_once(team):
    """Two receipts for one message (read, failed, read again, failed) put
    back one copy."""
    load_transport("tpm").send("coder", "fix the parser")

    class AlwaysDies(ScriptedRunner):
        inbox_pull = True

        def wake(self, agent, message):
            mcp_bus._check_inbox(FileTransport(agent))
            return "[error: 503]"

    sup = Supervisor(AGENTS, AlwaysDies({}), quiet=True, require_review=False)
    try:
        for _ in range(2):
            sup._backoff.clear()
            sup.step()
    finally:
        sup.close()
    assert [m.content for m in inbox("coder")] == ["fix the parser"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
