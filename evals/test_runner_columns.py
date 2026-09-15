"""Run the core flows twice: once with a transcript, once without.

## The gap this closes

This repo was never short of test doubles. ScriptedRunner, MockRunner,
RetroMockRunner, DaisyChainRunner, FanoutRunner, RecoveryRunner, PingPong,
AckLoop, FlakyRunner, BusyRunner, AnomalyRunner, PriorityRunner,
MultiPassDaisyRunner, MidTurnBlockRunner. A lot of coverage.

Every one of them returned prose from `wake()`.

So every double satisfied the strongest reading of the Runner contract, and no
test could detect a dependency on the part of the contract that is optional.
The retro tests passed because the mock handed the parser retro-shaped prose;
meanwhile a real runner honouring the contract exactly produced a
retrospective whose every section read "(missing or invalid)" and never wrote
NORMS.md. The double was more capable than the thing it stood in for -- which
is the substitution pattern this project exists to catch, aimed squarely at
its own test suite.

## The diagnostic

Two columns over the same flows:

  rich     ScriptedRunner as before -- returns prose
  minimal  the same runner returning "", declaring no capabilities

**A test that passes in `rich` and fails in `minimal` is an undeclared
capability requirement.** No host, no key and no network needed to run it.
Applied to the code as it stood, this column produces U3, U5, U6, U7 and U11
directly and makes U2 obvious on inspection.

It also closes the loop on the template we hand integrators: until now nothing
proved the file actually works, because nothing ran it.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import retro_store  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.supervisor import retro as run_retro  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import COLUMNS  # noqa: E402

AGENTS = ["manager", "coder", "qa"]

RETRO_ANSWERS = {
    "manager": ["The gate held: nothing reached the user unreviewed.",
                "Two delegations failed on argument shape first.",
                "Add to NORMS.md: a delegation that errors twice escalates."],
    "coder": ["Tests ran before handing off, every time.",
              "I reported a fix I had not executed.",
              "No handoff without the command and its output."],
    "qa": ["Reviews named an author who had actually worked.",
           "One review was recorded after the answer went out.",
           "No approval recorded after the user has been answered."],
}


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "gates what reaches the user"},
        {"name": "coder", "role": "implements"},
        {"name": "qa", "role": "reviews"}]}))
    (td / "events.jsonl").write_text(json.dumps(
        {"ts": "2026-09-14 09:00:00", "event": "turn", "agent": "coder",
         "conversation": "c1", "duration_s": 5.0}) + "\n")
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.setenv("AGYTEAM_DURABLE_DIR", str(tmp_path / "durable"))
    return td


@pytest.fixture(params=sorted(COLUMNS), ids=sorted(COLUMNS))
def column(request):
    return COLUMNS[request.param]


# --- the cascade: one instruction, unattended ---------------------------------

def test_delegation_cascade_runs_in_both_columns(team, column):
    """The load-bearing behaviour: A messages B, B is woken, B replies."""
    runner = column({"script": {
        "manager": [["coder", "please audit the buffer"]],
        "coder": [["qa", "audited; please review"]],
        "qa": [["user", "reviewed: read_all returns [None]"]]}})
    sup = Supervisor(AGENTS, runner, max_hops=32, quiet=True)
    load_transport("user").send("manager", "audit the buffer")
    turns = sup.run_until_idle()
    sup.close()

    # The gate bounces the unreviewed answer back to the manager afterwards,
    # which is the point of the gate; the chain itself must run in order.
    assert runner.woken[:3] == ["manager", "coder", "qa"], runner.woken
    assert turns >= 3
    bus = [json.loads(x) for x in (team / "bus.jsonl").read_text().splitlines()]
    assert any(e["to"] == "user" for e in bus), "the user was never answered"


def test_the_supervisor_does_not_treat_silence_as_failure(team, column):
    """A quiet success and an error must not look alike.

    The minimal column returns "" for a completed turn. Counted as a failure,
    the retry wrapper re-runs turns that already succeeded and the episode
    accounting is wrong for every agent on that host.
    """
    runner = column({"script": {"manager": [["user", "done"]]}})
    sup = Supervisor(AGENTS, runner, max_hops=8, quiet=True)
    load_transport("user").send("manager", "do the thing")
    sup.run_until_idle()
    sup.close()

    events = [json.loads(x) for x in
              (team / "events.jsonl").read_text().splitlines()]
    assert not [e for e in events if e.get("event") == "failure"], \
        "a turn that ended cleanly was recorded as a failure"


# --- the convergence loop ------------------------------------------------------

def test_retro_produces_a_report_in_both_columns(team, column):
    """The measured failure: five agents woken, every section "(missing or
    invalid)", NORMS.md never written."""
    runner = column({"retro": RETRO_ANSWERS})
    res = run_retro(leader="manager", runner=runner, team_dir=team, force=True)

    assert res.success, str(res)
    assert "(missing or invalid)" not in str(res)
    assert res.outcome != "invalid"


def test_norms_are_written_in_both_columns(team, column):
    """retro -> NORMS.md -> brief. Without the file the loop does not close."""
    runner = column({"retro": RETRO_ANSWERS})
    run_retro(leader="manager", runner=runner, team_dir=team, force=True)
    norms = team / "NORMS.md"
    assert norms.exists(), "NORMS.md NOT WRITTEN — the loop is inert"
    assert "escalates" in norms.read_text()


def test_every_participant_is_heard_in_both_columns(team, column):
    runner = column({"retro": RETRO_ANSWERS})
    res = run_retro(leader="manager", runner=runner, team_dir=team, force=True)
    assert set(retro_store.latest_by_agent(team)) == set(AGENTS)
    assert "I reported a fix I had not executed" in str(res)


def test_cycle_distils_and_retires_in_both_columns(team, column):
    """Distillation writes memory; the conversation id is retired, not lost."""
    runner = column({})
    runner.remember_conversation("coder", "conv-coder")
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        sup.cycle("coder")
    finally:
        sup.close()

    assert runner.conversation_id("coder") is None
    assert runner.agent_for_conversation("conv-coder") == "coder", \
        "attribution must survive a cycle on every runner"


# --- the gate ------------------------------------------------------------------

def test_the_review_gate_behaves_the_same_in_both_columns(team, column):
    """Recording a review must not depend on anything the runner returns."""
    from agyteam.mcp_bus import _record_review
    from agyteam.transport_file import FileTransport

    runner = column({"script": {"coder": [["qa", "built it"]]}})
    sup = Supervisor(AGENTS, runner, max_hops=8, quiet=True)
    load_transport("user").send("coder", "build it")
    sup.run_until_idle()
    sup.close()

    proof = team / "proof.py"
    proof.write_text("def test_ok():\n    assert 1 == 1\n")
    args = {"what": "the build", "author": "coder", "verdict": "approved",
            "proof_file": str(proof)}
    assert _record_review(FileTransport("qa"), args).startswith(
        "[review recorded:")

    # and the forgery is refused identically: manager never ran this episode
    assert "manager" not in runner.woken, runner.woken
    args["author"] = "manager"
    assert _record_review(FileTransport("qa"), args).startswith("[error:")


# --- the columns must actually differ -----------------------------------------

def test_the_minimal_column_is_genuinely_weaker():
    """A guard against the columns quietly converging.

    If someone gives MinimalRunner a reply string to make a test pass, the
    matrix stops being a matrix and this file becomes decoration.
    """
    rich, minimal = COLUMNS["rich"], COLUMNS["minimal"]
    assert minimal.supports_audit is False
    assert minimal.supports_containment is False
    assert minimal.supports_capability_scoping is False
    assert rich is not minimal

    import inspect
    src = inspect.getsource(minimal.wake)
    assert 'return ""' in src, "the minimal column must return no transcript"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
