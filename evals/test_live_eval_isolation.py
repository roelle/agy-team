"""A live eval must build its own team, whatever the shell exports.

The README said each live eval "uses a team or workspace of its own and never
touches yours". Three of them resolved their team through scope.load() with
AGYTEAM_TEAM_DIR still set, and AGYTEAM_TEAM_DIR outranks everything else:
run with a team exported, they wrote their roster over that team's. They run
as scripts, where conftest.py's sandbox never loads.

Also here: test_a2a.py could not start at all, because it read agent
identities from a plugin directory that no longer ships. None of these evals
runs under pytest -- they cost money -- so nothing noticed.
"""
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

import rpc_util  # noqa: E402
from agyteam import scope  # noqa: E402

LIVE = ["test_delivery.py", "test_reactive.py", "test_continuity.py", "test_a2a.py"]


def test_forgetting_the_ambient_team_beats_an_exported_team_dir(tmp_path, monkeypatch):
    theirs = tmp_path / "their-team"
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(theirs))
    monkeypatch.setenv("AGYTEAM_TEAM", "theirs")
    monkeypatch.setenv("AGYTEAM_DURABLE_DIR", str(tmp_path / "eval"))
    assert scope.load().team_dir() == theirs          # the fault
    rpc_util.forget_ambient_team()
    monkeypatch.setenv("AGYTEAM_DURABLE_DIR", str(tmp_path / "eval"))
    assert scope.load().team_dir() == tmp_path / "eval" / "team"


@pytest.mark.parametrize("name", LIVE)
def test_every_live_eval_forgets_it_before_resolving_a_team(name):
    src = (ROOT / "evals" / name).read_text()
    forget = src.find("forget_ambient_team()")
    resolve = min(i for i in (src.find("scope.load("), src.find(".team_dir()")) if i >= 0)
    assert 0 <= forget < resolve, f"{name} resolves its team before forgetting yours"


def test_every_eval_that_writes_a_roster_is_listed():
    """The guard: a new live eval that seeds a roster must be added above."""
    writers = {p.name for p in (ROOT / "evals").glob("*.py")
               if re.search(r'"roster\.json"\)\.write_text', p.read_text())
               and "def main(" in p.read_text()
               and "tmp_path" not in p.read_text() and "tempfile" not in p.read_text()}
    assert writers <= set(LIVE), writers - set(LIVE)


def test_the_a2a_eval_can_build_its_agents(tmp_path, monkeypatch):
    pytest.importorskip("google.antigravity")
    import test_a2a
    td = tmp_path / "team"
    td.mkdir()
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "Coordinates and delegates; no shell."}]}))
    conf = test_a2a.agent_config("tpm", tmp_path / "durable", td)
    assert "Coordinates and delegates" in conf.system_instructions.identity
    assert {s.name for s in conf.mcp_servers} == {"agyteam_memory", "agyteam_bus"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
