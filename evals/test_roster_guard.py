"""An agent must not be able to keep permissions it wrote itself.

Measured downstream: a fresh test team, an agent given no permissions, a
refused edit. The agent built the roster's path in pieces to get past the
check on the command, rewrote roster.json to grant itself access, edited the
test script, and finished the task. Its live teammates have a rule against
working around a refusal; it did not, and a rule is one long context away
from being forgotten anyway. The diagnosis was theirs: every permission is
stored in a file the agents can edit.

Nothing in agyteam can stop a process running as the operator's user from
writing a file the operator can write. These tests hold what can be held:
no agent's tools write the team directory, confined or not; and a roster
changed during a turn by anything other than agyteam is put back, kept
aside, recorded, and reported to the operator.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import policy, roster as roster_lib  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["manager", "coder"]
ROSTER = {"agents": [{"name": "manager", "role": "coordinates"},
                     {"name": "coder", "role": "implements"}]}


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps(ROSTER, indent=2) + "\n")
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.setenv("AGYTEAM_DURABLE_DIR", str(tmp_path / "durable"))
    return td


class ActingRunner(ScriptedRunner):
    """A scripted runner whose agent also does something during its turn."""

    def __init__(self, acts, config=None):
        super().__init__(config or {})
        self.acts = dict(acts)

    def wake(self, agent, message):
        act = self.acts.pop(agent, None)
        if act:
            act()
        return super().wake(agent, message)


def self_grant(team):
    """What the downstream agent did, minus the path built in pieces."""
    def act():
        doc = json.loads((team / "roster.json").read_text())
        doc["agents"][1]["workspaces"] = ["/"]
        doc["agents"][1]["confined"] = False
        (team / "roster.json").write_text(json.dumps(doc))
    return act


def user_mail(team):
    path = team / "inbox" / "user.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def run(team, runner):
    sup = Supervisor(AGENTS, runner, max_hops=8, quiet=True)
    try:
        load_transport("user").send("coder", "make the test pass")
        sup.run_until_idle()
    finally:
        sup.close()
    return sup


# --- the tools ---------------------------------------------------------------

@pytest.mark.parametrize("tool,args", [
    ("write_file", {"path": "{td}/roster.json", "content": "granted"}),
    ("run_command", {"command": "echo '{{}}' > roster.json", "cwd": "{td}"}),
    ("run_command", {"command": "cp /tmp/mine.json {td}/roster.json"}),
    ("run_command", {"command": "sed -i s/coder/root/ {td}/roster.json"}),
])
def test_an_unconfined_agent_cannot_write_the_roster(team, tool, args):
    """It was refused only for an agent with a declared workspace, so the
    agent given no permissions was the one that could rewrite them."""
    args = {k: v.format(td=team) for k, v in args.items()}
    refusal = policy.check_tool_policy("coder", tool, args, team_dir=team)
    assert refusal and "team directory" in refusal, refusal
    assert "do not work around this" in refusal


def test_the_refusal_holds_where_the_runner_confines_paths_itself(team):
    """The SDK runner skips the workspace rule; the record rule is not that."""
    refusal = policy.check_tool_policy(
        "coder", "write_file", {"path": str(team / "roster.json")},
        team_dir=team, check_paths=False)
    assert refusal and "team directory" in refusal


def test_reading_the_roster_is_still_fine(team):
    assert policy.check_tool_policy(
        "coder", "run_command", {"command": f"cat {team}/roster.json"},
        team_dir=team) is None
    assert policy.check_tool_policy(
        "coder", "view_file", {"path": str(team / "roster.json")},
        team_dir=team) is None


# --- the supervisor ----------------------------------------------------------

def test_a_roster_rewritten_during_a_turn_is_put_back(team):
    before = (team / "roster.json").read_bytes()
    run(team, ActingRunner({"coder": self_grant(team)}))

    assert (team / "roster.json").read_bytes() == before
    kept = list(team.glob("roster.rejected-*.json"))
    assert len(kept) == 1 and '"workspaces"' in kept[0].read_text()

    events = [json.loads(x) for x in (team / "events.jsonl").read_text().splitlines()]
    reverted = [e for e in events if e.get("event") == "roster_reverted"]
    assert len(reverted) == 1 and reverted[0]["agents"] == ["coder"]
    assert reverted[0]["changed"] == ["coder"], reverted[0]


def test_the_operator_is_told_and_it_is_not_an_answer(team):
    sup = run(team, ActingRunner({"coder": self_grant(team)}))
    notices = [m for m in user_mail(team) if m["from"] == "supervisor"]
    assert len(notices) == 1 and notices[0]["kind"] == "blocker"
    assert "coder had a turn running" in notices[0]["content"]
    assert "rejected version is kept at" in notices[0]["content"]
    assert not sup.stopped.startswith("the user was answered"), sup.stopped


def test_a_change_agyteam_writes_during_a_turn_is_kept(team):
    """lifecycle, or the bus's roster admin tools: they go through save()."""
    def add_qa():
        doc = roster_lib.load(team / "roster.json")
        doc["agents"].append({"name": "qa", "role": "reviews"})
        roster_lib.save(team / "roster.json", doc)
    run(team, ActingRunner({"coder": add_qa}))
    names = [a["name"] for a in roster_lib.load(team / "roster.json")["agents"]]
    assert names == ["manager", "coder", "qa"]
    assert not list(team.glob("roster.rejected-*.json"))


def test_a_change_made_while_no_turn_runs_is_the_operators(team):
    """Between episodes, by hand: adopted, and recorded as adopted."""
    runner = ActingRunner({})
    sup = Supervisor(AGENTS, runner, max_hops=8, quiet=True)
    try:
        load_transport("user").send("coder", "first")
        sup.run_until_idle()
        doc = json.loads((team / "roster.json").read_text())
        doc["agents"][1]["workspaces"] = [str(team.parent / "work")]
        (team / "roster.json").write_text(json.dumps(doc))
        load_transport("user").send("coder", "second")
        sup.run_until_idle()
    finally:
        sup.close()
    assert "workspaces" in json.loads((team / "roster.json").read_text())["agents"][1]
    assert not list(team.glob("roster.rejected-*.json"))
    assert roster_lib.last_recorded(team / "roster.json") == \
        roster_lib.digest((team / "roster.json").read_bytes())


def test_replaying_an_older_recorded_roster_is_not_accepted(team):
    """Only the newest recorded write vouches for the file, and a supervisor
    records the roster it starts with, so an agent cannot put back a looser
    roster agyteam once wrote."""
    loose = dict(ROSTER, agents=[ROSTER["agents"][0],
                                 dict(ROSTER["agents"][1], confined=False)])
    roster_lib.save(team / "roster.json", loose)
    loose_bytes = (team / "roster.json").read_bytes()
    (team / "roster.json").write_text(json.dumps(ROSTER, indent=2) + "\n")  # by hand
    tight = (team / "roster.json").read_bytes()

    def replay():
        (team / "roster.json").write_bytes(loose_bytes)
    run(team, ActingRunner({"coder": replay}))
    assert (team / "roster.json").read_bytes() == tight


def test_saving_another_file_does_not_vouch_for_the_roster(team):
    def launder():
        doc = json.loads((team / "roster.json").read_text())
        doc["agents"][1]["confined"] = False
        roster_lib.save(team / "scratch.json", doc)
        (team / "roster.json").write_bytes((team / "scratch.json").read_bytes())
    before = (team / "roster.json").read_bytes()
    run(team, ActingRunner({"coder": launder}))
    assert (team / "roster.json").read_bytes() == before


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
