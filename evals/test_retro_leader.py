"""Who leads the retrospective, and why the roster gets to say.

`--retro-leader` defaulted to the literal string "tpm", and the roster's
`is_retro_leader` flag was consulted only when that default named nobody on the
roster -- the one case where it was not needed. Any team with a tpm ignored the
flag entirely, including the seeded one, whose tpm role said "Leads retros".

Two separate teams proposed, unasked, that the accountable role should lead
instead. The reasoning is the same one the code already encodes in
TENSION_ACCOUNTABLE: the norms a retro produces bind the team, and a level-1
manager facilitating a retro on work she delegated is grading her own
instructions. That has a real cost -- a retro led by the person accountable for
the outcome is a weaker retro -- and the report says so out loud rather than
choosing the facilitator who does not have to own the result.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam.supervisor import (TENSION_ACCOUNTABLE,  # noqa: E402
                                format_retro_report, resolve_retro_leader)


def roster(tmp_path, agents):
    td = tmp_path / "team"
    td.mkdir(exist_ok=True)
    (td / "roster.json").write_text(json.dumps({"agents": agents}))
    return td


def test_the_roster_flag_beats_the_presence_of_a_tpm(tmp_path):
    """The measured regression: a team with a tpm could not move the retro."""
    td = roster(tmp_path, [
        {"name": "manager", "role": "gates", "is_retro_leader": True},
        {"name": "tpm", "role": "coordinates"},
        {"name": "coder", "role": "implements"}])
    assert resolve_retro_leader(None, ["manager", "tpm", "coder"], td) == "manager"


def test_an_explicit_choice_still_wins(tmp_path):
    """Whoever is running the retro can override the roster for one run."""
    td = roster(tmp_path, [
        {"name": "manager", "role": "gates", "is_retro_leader": True},
        {"name": "tpm", "role": "coordinates"}])
    assert resolve_retro_leader("tpm", ["manager", "tpm"], td) == "tpm"


def test_the_accountable_role_leads_when_nothing_is_marked(tmp_path):
    td = roster(tmp_path, [
        {"name": "tpm", "role": "coordinates"},
        {"name": "director", "role": "gates", "is_principal": True},
        {"name": "coder", "role": "implements"}])
    assert resolve_retro_leader(None, ["tpm", "director", "coder"], td) == "director"


def test_a_gatekeeper_counts_as_accountable(tmp_path):
    td = roster(tmp_path, [
        {"name": "tpm", "role": "coordinates"},
        {"name": "auditor", "role": "gates", "is_gatekeeper": True}])
    assert resolve_retro_leader(None, ["tpm", "auditor"], td) == "auditor"


def test_a_team_with_no_manager_at_all_still_gets_a_leader(tmp_path):
    """A retro that refuses to run because nobody is marked is worse than a
    retro led by the coordinator."""
    td = roster(tmp_path, [
        {"name": "tpm", "role": "coordinates"},
        {"name": "coder", "role": "implements"}])
    assert resolve_retro_leader(None, ["tpm", "coder"], td) == "tpm"


def test_an_unreadable_roster_does_not_take_the_retro_down(tmp_path):
    td = tmp_path / "team"
    td.mkdir()
    (td / "roster.json").write_text("{ not json")
    assert resolve_retro_leader(None, ["manager", "coder"], td) == "manager"


def test_the_leader_must_be_on_the_roster(tmp_path):
    """A flag naming somebody who has left is not a reason to wake nobody."""
    td = roster(tmp_path, [
        {"name": "gone", "role": "left the team", "is_retro_leader": True},
        {"name": "tpm", "role": "coordinates"}])
    assert resolve_retro_leader(None, ["tpm"], td) == "tpm"


# --- the cost of the choice has to stay visible ------------------------------

def test_an_accountable_leader_is_named_as_a_tension_in_the_report():
    """If this ever stops appearing, the default has quietly become a claim
    that the accountable role is the *better* facilitator, which it is not."""
    out = format_retro_report("manager", ["coder", "qa"], True,
                              "went well", "did not", "change this", "ok")
    assert TENSION_ACCOUNTABLE in out


def test_the_seeded_roster_and_the_resolver_agree(tmp_path):
    """The seed lives in a shell heredoc in plugin/install.sh, where no import
    reaches it -- so it drifted from the code once already."""
    text = (ROOT / "plugin" / "install.sh").read_text()
    manager_line = next(ln for ln in text.splitlines()
                        if '"name": "manager"' in ln)
    start = text.index(manager_line)
    block = text[start:text.index('{"name": "tpm"', start)]
    assert '"is_retro_leader": true' in block, \
        "the seeded manager must be marked, or a fresh team leaves tpm leading"
    tpm_line = next(ln for ln in text.splitlines() if '"name": "tpm"' in ln)
    assert "Leads retros" not in tpm_line, \
        "two roles claiming the retro is worse than either claiming it"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
