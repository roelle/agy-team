"""What an operator's admin screen does to a team, through agyteam.actions.

Agents, norms and memories were edited by hand in files, under the
supervisor's roster guard, with no check that the result still parsed. A
client (agy-chat's admin page) now does it through these, and a channel can
follow the roster so a team where everyone hears everything needs no boxes
ticked when an agent joins.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import actions, channels, roster  # noqa: E402


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "teams" / "demo" / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "coordinates"}, {"name": "coder", "role": "implements"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    monkeypatch.delenv("AGYTEAM_MEMORY_STORE", raising=False)
    monkeypatch.delenv("AGYTEAM_MEMORY_CONFIG", raising=False)
    return td


def names(td):
    return [a["name"] for a in roster.load(td / "roster.json")["agents"]]


def test_an_everyone_channel_follows_the_roster(team):
    actions.create_channel(team, "all-hands", everyone=True)
    actions.create_channel(team, "pair", ["coder"])
    actions.add_agent(team, "qa", role="reviews", channels=None)
    assert channels.members(team, "all-hands") == ["coder", "qa", "tpm"]
    assert channels.members(team, "pair") == ["coder"]
    actions.set_members(team, "all-hands", remove=["tpm"])
    assert channels.members(team, "all-hands") == ["coder", "qa"]
    actions.add_agent(team, "ops", channels=None)
    assert "ops" not in channels.members(team, "all-hands"), "removing someone makes it explicit"
    actions.set_members(team, "all-hands", everyone=True)
    assert "ops" in channels.members(team, "all-hands")


def test_a_new_agent_joins_every_open_channel_by_default(team):
    actions.create_channel(team, "billing", ["coder"])
    actions.create_channel(team, "old", ["coder"])
    actions.archive_channel(team, "old")
    out = actions.add_agent(team, "qa", role="reviews", model="pro")
    assert "billing" in out["channels"] and "old" not in out["channels"]
    entry = next(a for a in roster.load(team / "roster.json")["agents"] if a["name"] == "qa")
    assert entry["model"] == "pro" and entry["role"] == "reviews"


def test_agent_names_and_fields_are_checked(team):
    for bad in ("user", "all", "has space", "", "-x"):
        with pytest.raises(actions.OperatorError):
            actions.add_agent(team, bad)
    with pytest.raises(actions.OperatorError, match="already on the roster"):
        actions.add_agent(team, "coder")
    with pytest.raises(actions.OperatorError, match="cannot set workspaces"):
        actions.update_agent(team, "coder", workspaces=["/"])


def test_update_and_remove_go_through_the_roster_history(team):
    actions.update_agent(team, "coder", role="implements and tests", tools_off="run_command, x",
                         is_principal=False)
    entry = next(a for a in roster.load(team / "roster.json")["agents"] if a["name"] == "coder")
    assert entry["role"] == "implements and tests" and entry["tools_off"] == ["run_command", "x"]
    actions.update_agent(team, "coder", tools_off="")
    entry = next(a for a in roster.load(team / "roster.json")["agents"] if a["name"] == "coder")
    assert "tools_off" not in entry
    assert roster.history_size(team / "roster.json") >= 2, \
        "an operator's edit not in the history is put back by a running supervisor"
    actions.create_channel(team, "pair", ["coder", "tpm"])
    actions.remove_agent(team, "coder")
    assert names(team) == ["tpm"] and channels.members(team, "pair") == ["tpm"]


def test_norms_are_replaced_and_the_edit_is_recorded(team):
    actions.set_norms(team, "# Norms\r\n\r\n- Review before shipping.")
    assert actions.norms(team) == "# Norms\n\n- Review before shipping.\n"
    events = [json.loads(l) for l in (team / "events.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "norms_edited"


def test_an_agents_memories_can_be_read_written_and_deleted(team):
    out = actions.save_memory(team, "coder", "Deploy Host", "deploys go to box-2", "where deploys go")
    assert out["name"] == "deploy-host" and out["created"]
    mems = actions.memories(team, "coder")
    assert [m["name"] for m in mems] == ["deploy-host"]
    assert "box-2" in mems[0]["content"] and "operator" in mems[0]["content"]
    assert (team.parent / "agents" / "coder" / "memory" / "deploy-host.md").exists(), \
        "written somewhere the agent will not read it"
    actions.delete_memory(team, "coder", "deploy-host")
    assert actions.memories(team, "coder") == []
    with pytest.raises(actions.OperatorError):
        actions.memories(team, "nobody")


def test_admin_actions_are_refused_inside_an_agent(team, monkeypatch):
    monkeypatch.setenv("AGYTEAM_AGENT", "coder")
    for call in (lambda: actions.add_agent(team, "qa"), lambda: actions.set_norms(team, "x"),
                 lambda: actions.save_memory(team, "tpm", "a", "b"),
                 lambda: actions.remove_agent(team, "tpm")):
        with pytest.raises(actions.OperatorError):
            call()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
