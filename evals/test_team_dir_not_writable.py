"""The team directory is the record, and no agent may be granted write access.

bus.jsonl, reviews.jsonl, retro_inbox.jsonl, NORMS.md, roster.json and
conversations.json are all written by an agyteam process and read back as the
account of what the team did. Agents reach them through the MCP servers, which
are separate processes with their own filesystem access, so no agent needs a
grant there to do anything it is supposed to do.

A grant that reaches it therefore buys nothing and costs every gate built on
those files at once. The review gate runs a proof file and refuses a
self-review -- and an agent that can append a line to reviews.jsonl has skipped
all of it. The same line closes retro-inbox forgery by construction rather than
by validation: you cannot write a retrospective answer in a teammate's name if
you cannot write the file.

This is the "answer keys are not in the workspace" rule pointed at our own
bookkeeping, and it is the cheaper half, because unlike the audit log it costs
nothing to enforce.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import doctor, lifecycle  # noqa: E402


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "durable" / "team"
    td.mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "gates"},
        {"name": "coder", "role": "implements"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    return td


def test_the_team_dir_itself_cannot_be_granted(team):
    with pytest.raises(ValueError) as e:
        lifecycle.grant_workspace(team, agent="coder", team_dir=team)
    assert "record" in str(e.value)


def test_an_ancestor_cannot_be_granted_either(team):
    """The realistic version: nobody grants the team directory on purpose,
    they grant the directory above it."""
    with pytest.raises(ValueError):
        lifecycle.grant_workspace(team.parent, agent="coder", team_dir=team)
    with pytest.raises(ValueError):
        lifecycle.grant_workspace(team.parent.parent, team_dir=team)


def test_a_working_tree_beside_it_is_fine(team, tmp_path):
    """The rule must not make ordinary work impossible, or it gets removed."""
    tree = tmp_path / "repo"
    tree.mkdir()
    res = lifecycle.grant_workspace(tree, agent="coder", team_dir=team)
    assert res["status"] == "ok"


def test_the_refusal_says_what_to_do_instead(team):
    with pytest.raises(ValueError) as e:
        lifecycle.grant_workspace(team.parent, team_dir=team)
    msg = str(e.value)
    assert "separate process" in msg, "explain why no grant is needed"
    assert "AGYTEAM_DURABLE_DIR" in msg, "and what to do if it is co-located"


def test_a_refused_grant_is_not_half_applied(team):
    before = (team / "roster.json").read_text()
    with pytest.raises(ValueError):
        lifecycle.grant_workspace(team.parent, agent="coder", team_dir=team)
    assert (team / "roster.json").read_text() == before


# --- and an existing roster gets told ----------------------------------------

class FakeReport(doctor.Report):
    def __init__(self):
        super().__init__()
        self.lines = []

    def line(self, mark, text):
        self.lines.append((mark.strip(), text))
        super().line(mark, text)

    def detail(self, text):
        pass


def test_the_preflight_flags_a_grant_that_predates_the_rule(team):
    """Refusing new grants does nothing about the roster already on disk."""
    doc = json.loads((team / "roster.json").read_text())
    doc["agents"][1]["workspaces"] = [str(team.parent)]
    (team / "roster.json").write_text(json.dumps(doc))

    r = FakeReport()
    doctor.check_workspace_grants(r, team, {"supports_containment": True})
    assert r.failed
    assert any("contain the team directory" in t for _, t in r.lines)


def test_a_clean_roster_passes(team):
    r = FakeReport()
    doctor.check_workspace_grants(r, team, {"supports_containment": True})
    assert not r.failed


def test_a_runner_with_no_containment_says_the_list_is_not_the_boundary(team):
    """Otherwise a clean grant list reads as a guarantee on a runner that
    enforces nothing at all."""
    doc = json.loads((team / "roster.json").read_text())
    doc["workspaces"] = [str(team.parent.parent / "repo")]
    (team / "roster.json").write_text(json.dumps(doc))

    r = FakeReport()
    doctor.check_workspace_grants(r, team, {"supports_containment": False})
    assert not r.failed
    assert any("enforces no workspace boundary" in t for _, t in r.lines)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
