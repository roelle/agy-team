"""Two integrator reports against 3.3.0.

- `--status` wiped inflight.json. It builds a Supervisor to peek at the
  queues, and Supervisor.__init__ loaded the ledger -- with a runner that
  resumes nothing -- before anything asked whether this process drives the
  team: every live turn was recorded as died in a restart, and an empty
  ledger was saved over the live supervisor's.
- Asking which models the team's runner offers (the chat app's picker,
  Record.models(), doctor, `agyteam.models`) read AGYTEAM_RUNNER and
  AGYTEAM_RUNNER_CONFIG and never the roster's "supervisor" block, so from
  any process without the launcher's exports the answer was the default
  runner's, or nothing.
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import runner as runner_lib, supervisor  # noqa: E402
from agyteam.record import Record  # noqa: E402
from agyteam.runner import Runner  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["manager", "coder"]
THIS = "test_status_and_team_runner"


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "coordinates"},
        {"name": "coder", "role": "implements", "model": "team-model"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    for var in ("AGYTEAM_RUNNER", "AGYTEAM_RUNNER_CONFIG", "AGYTEAM_MODEL_CHECK"):
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)
    runner_lib._MODEL_CACHE.clear()
    return td


def live_ledger(team):
    """What a HostRunner supervisor leaves while coder's turn runs on the host."""
    text = json.dumps({"coder": {
        "handle": {"conversation": "c-live", "started": 0},
        "ts": "2026-10-20 09:00:00", "hop": 1, "trigger": {},
        "msgs": [{"ts": "2026-10-20 08:59:59", "from": "manager", "to": "coder",
                  "content": "build it", "kind": "work"}]}}, indent=2) + "\n"
    (team / "inflight.json").write_text(text)
    return text


def failures(team):
    path = team / "events.jsonl"
    if not path.exists():
        return []
    return [e for e in map(json.loads, path.read_text().splitlines())
            if e.get("event") == "failure"]


# --- the ledger ----------------------------------------------------------------

def test_status_leaves_the_ledger_alone(team, capsys):
    before = live_ledger(team)
    supervisor.main(["--team-dir", str(team), "--status"])
    assert (team / "inflight.json").read_text() == before
    assert failures(team) == [], "a live turn was recorded as died in a restart"
    out = capsys.readouterr().out
    assert "coder: busy, turn running since 2026-10-20 09:00:00" in out, out


HOLD = textwrap.dedent("""
    import sys, time, os
    sys.path.insert(0, {root!r}); sys.path.insert(0, {evals!r})
    os.environ["AGYTEAM_TEAM_DIR"] = {td!r}
    from agyteam.supervisor import Supervisor
    from fixture_runner import ScriptedRunner
    sup = Supervisor(["manager", "coder"], ScriptedRunner({{}}), quiet=True)
    assert sup._claim_team()
    print("held", flush=True)
    time.sleep(30)
""")


def test_a_supervisor_built_beside_a_live_one_leaves_its_ledger_alone(team):
    """--distill, a retro, or a supervisor about to be refused the team."""
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD.format(root=str(ROOT), evals=str(ROOT / "evals"),
                                           td=str(team))],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        before = live_ledger(team)          # as the live one has it now
        sup = Supervisor(AGENTS, ScriptedRunner({}), quiet=True)
        try:
            assert sup.inflight == {}
            assert sup.run_until_idle() == 0 and "already driving" in sup.stopped
        finally:
            sup.close()
        assert (team / "inflight.json").read_text() == before
        assert failures(team) == []
    finally:
        holder.kill()
        holder.wait()


class Resumable(ScriptedRunner):
    resumable = True

    def poll(self, handle):
        return None                    # still running on the host

    def cancel(self, handle):
        pass


def test_the_one_that_claims_the_team_takes_the_ledger_up(team):
    live_ledger(team)
    sup = Supervisor(AGENTS, Resumable({}), quiet=True, inspect_only=True)
    try:
        assert sup.inflight == {}
    finally:
        sup.close()
    sup = Supervisor(AGENTS, Resumable({}), quiet=True)
    try:
        assert "coder" in sup.inflight, "the restart forgot a turn the host is running"
    finally:
        sup.close()


# --- the team's runner ---------------------------------------------------------

class Listing(Runner):
    """A runner that offers what its config says it does."""
    label = "listing"

    @classmethod
    def list_models(cls, config=None):
        return [{"id": m, "label": m} for m in (config or {}).get("offers", [])]

    def wake(self, agent, message):
        return ""


def block(team, **sup):
    doc = json.loads((team / "roster.json").read_text())
    doc["supervisor"] = sup
    (team / "roster.json").write_text(json.dumps(doc))


def test_the_roster_names_the_runner_whose_models_are_listed(team):
    block(team, runner=f"{THIS}:Listing", runner_config={"offers": ["team-model"]})
    assert runner_lib.list_models() is None or runner_lib.runner_class() is not Listing
    assert runner_lib.list_models(team_dir=team) == [{"id": "team-model", "label": "team-model"}]
    assert runner_lib.runner_class(team_dir=team) is Listing
    assert runner_lib.capabilities(team_dir=team)["spec"] == f"{THIS}:Listing"


def test_record_models_judges_against_the_teams_runner(team):
    block(team, runner=f"{THIS}:Listing", runner_config={"offers": ["team-model"]})
    models = Record(team).models()
    assert models["coder"]["available"] is True, models["coder"]


def test_a_model_the_teams_runner_does_not_offer_is_flagged(team):
    block(team, runner=f"{THIS}:Listing", runner_config={"offers": ["something-else"]})
    assert Record(team).models()["coder"]["available"] is False


def test_the_environment_still_answers_for_a_team_with_no_block(team, monkeypatch):
    monkeypatch.setenv("AGYTEAM_RUNNER", f"{THIS}:Listing")
    monkeypatch.setenv("AGYTEAM_RUNNER_CONFIG", json.dumps({"offers": ["team-model"]}))
    assert runner_lib.team_runner(team) == (
        f"{THIS}:Listing", {"offers": ["team-model"]}, "AGYTEAM_RUNNER")
    assert Record(team).models()["coder"]["available"] is True


def test_the_roster_beats_the_environment_and_brings_its_own_config(team, monkeypatch):
    monkeypatch.setenv("AGYTEAM_RUNNER", "agyteam.runner_agy:AgyRunner")
    monkeypatch.setenv("AGYTEAM_RUNNER_CONFIG", json.dumps({"binary": "/opt/agy"}))
    block(team, runner=f"{THIS}:Listing")
    assert runner_lib.team_runner(team) == (f"{THIS}:Listing", {}, "roster"), \
        "another runner's config must not travel to the team's"
    assert runner_lib.team_runner(team, runner_flag="x:Y") == ("x:Y", {}, "--runner")


def test_setting_a_model_is_checked_against_the_teams_runner(team, monkeypatch):
    from agyteam import models
    monkeypatch.setenv("USER", "operator")
    block(team, runner=f"{THIS}:Listing", runner_config={"offers": ["team-model"]})
    models.set_model(team, "coder", "team-model")
    with pytest.raises(models.ModelError):
        models.set_model(team, "coder", "not-offered")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
