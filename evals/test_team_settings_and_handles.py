"""Two things an integrator reported, each with the failure it caused.

- A team's supervisor started by a client (agy-chat's Start button) ran with
  the client's environment, not the team's: the default runner instead of the
  team's, so wakes failed and conversations were lost, and a guessed manager.
  The roster's "supervisor" block now says how the team is run.
- "@Ada" did not wake "at-Ada": mentions matched exact names only, so a
  team-prefixed handle could never be reached by the name people use.
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import channels, mcp_bus, supervisor  # noqa: E402
from agyteam.roster import resolve_name  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from agyteam.transport_file import FileTransport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

SEEN: list[dict] = []
THIS = "test_team_settings_and_handles"


class TeamRunner(ScriptedRunner):
    """Records the config it was built with and the heartbeat it ran under."""
    label = "team-runner"

    def wake(self, agent, message):
        beat = json.loads((Path(os.environ["AGYTEAM_TEAM_DIR"]) / "supervisor.json").read_text())
        SEEN.append({"config": dict(self.config), "beat": beat, "agent": agent})
        return "ok"


class OtherRunner(TeamRunner):
    label = "other-runner"


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    # main() writes these; set-then-delete so teardown restores the originals.
    for var in ("AGYTEAM_RUNNER", "AGYTEAM_RUNNER_CONFIG"):
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    SEEN.clear()
    return td


def roster(td, agents, block=None):
    doc = {"agents": agents}
    if block is not None:
        doc["supervisor"] = block
    (td / "roster.json").write_text(json.dumps(doc))


AGENTS = [{"name": "at-Boss", "role": "manager: gates the work"},
          {"name": "at-Ada", "role": "implements"},
          {"name": "at-Grace", "role": "reviews"}]


def test_a_client_started_supervisor_runs_the_team_as_its_roster_says(team):
    roster(team, AGENTS, {"runner": f"{THIS}:TeamRunner", "runner_config": {"tag": "team"},
                          "manager": "at-Grace"})
    supervisor.main(["--team-dir", str(team), "--quiet", "--say", "at-Ada: hi"])
    assert SEEN, "nobody was woken"
    first = SEEN[0]
    assert first["config"].get("tag") == "team"
    assert first["beat"]["runner"] == "team-runner"
    assert first["beat"]["runner_class"] == f"{THIS}:TeamRunner"
    assert first["beat"]["manager"] == "at-Grace", "the manager was guessed, not the team's"
    assert first["beat"]["source"] == {"runner": "roster", "manager": "roster"}


def test_the_roster_beats_the_launchers_environment_and_drops_its_config(team, monkeypatch):
    monkeypatch.setenv("AGYTEAM_RUNNER", f"{THIS}:OtherRunner")
    monkeypatch.setenv("AGYTEAM_RUNNER_CONFIG", json.dumps({"tag": "launcher"}))
    roster(team, AGENTS, {"runner": f"{THIS}:TeamRunner"})
    supervisor.main(["--team-dir", str(team), "--quiet", "--say", "at-Ada: hi"])
    assert SEEN[0]["beat"]["runner"] == "team-runner"
    assert "tag" not in SEEN[0]["config"], "another runner's config was handed to this one"


def test_the_flag_beats_the_roster(team):
    roster(team, AGENTS, {"runner": f"{THIS}:TeamRunner"})
    supervisor.main(["--team-dir", str(team), "--quiet", "--runner", f"{THIS}:OtherRunner",
                     "--say", "at-Ada: hi"])
    assert SEEN[0]["beat"]["runner"] == "other-runner"
    assert SEEN[0]["beat"]["source"]["runner"] == "--runner"


def test_without_a_block_the_environment_still_decides(team, monkeypatch):
    monkeypatch.setenv("AGYTEAM_RUNNER", f"{THIS}:OtherRunner")
    monkeypatch.setenv("AGYTEAM_RUNNER_CONFIG", json.dumps({"tag": "launcher"}))
    roster(team, AGENTS)
    supervisor.main(["--team-dir", str(team), "--quiet", "--say", "at-Ada: hi"])
    assert SEEN[0]["beat"]["runner"] == "other-runner"
    assert SEEN[0]["config"].get("tag") == "launcher"
    assert SEEN[0]["beat"]["source"] == {"runner": "AGYTEAM_RUNNER"}


def test_cycle_uses_the_teams_runner_too(team):
    """A cycle run on the wrong runner resets a conversation that runner never had."""
    roster(team, AGENTS, {"runner": f"{THIS}:TeamRunner"})
    r = supervisor.load_team_runner(team)
    try:
        assert isinstance(r, TeamRunner)
    finally:
        r.close()


# --- handles ---------------------------------------------------------------------------

NAMES = ["at-Ada", "at-Grace", "bt-Grace", "lin", "Lin_ops"]


@pytest.mark.parametrize("handle,agent", [
    ("at-Ada", "at-Ada"),       # exact
    ("Ada", "at-Ada"),          # team prefix dropped
    ("ada", "at-Ada"),          # and case
    ("AT-ADA", "at-Ada"),
    ("Grace", None),            # at-Grace or bt-Grace: neither
    ("lin", "lin"),             # exact beats a suffix match
    ("ops", "Lin_ops"),
    ("da", None),               # a suffix must follow a separator
    ("", None),
])
def test_a_handle_resolves_to_exactly_one_agent_or_none(handle, agent):
    assert resolve_name(handle, NAMES) == agent


def test_a_mention_without_the_team_prefix_wakes_the_agent(team):
    roster(team, AGENTS)
    channels.create(team, "build", [a["name"] for a in AGENTS])
    out = channels.post(load_transport("user"), team, "build", "@Ada can you take this?")
    assert out["woke"] == ["at-Ada"]
    assert channels.mentioned("@ada and @grace", ["at-Ada"], [a["name"] for a in AGENTS]) \
        == {"at-Ada", "at-Grace"}


def test_a_direct_message_without_the_team_prefix_is_delivered(team):
    roster(team, AGENTS)
    out = mcp_bus._send(FileTransport("at-Boss"), {"to": "Ada", "content": "take the parser"})
    assert out.startswith("[queued for at-Ada")
    assert [m.content for m in load_transport("at-Ada").peek()] == ["take the parser"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
