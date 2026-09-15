"""Runners must declare what they cannot do, and tools must honour it.

The rule these pin down: a check that could not run must never be reported as
a check that found nothing. It already governs test files that collect no
tests and a bench that cannot be verified; these apply it to the runner seam,
where three documented "invariants" are actually runner-provided.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam.runner import Runner  # noqa: E402
from agyteam.runner_agy import AgyRunner  # noqa: E402
from agyteam.runner_template import MyRunner  # noqa: E402


def test_base_class_defaults_to_no_capability():
    """The honest default for "can you do this?" is no."""
    assert Runner.supports_audit is False
    assert Runner.supports_containment is False


def test_template_declares_nothing_it_has_not_implemented():
    assert MyRunner.supports_audit is False
    assert MyRunner.supports_containment is False


def test_external_host_runner_declares_both_false():
    """It has no workspace mechanism and installs no audit hook."""
    assert AgyRunner.supports_audit is False
    assert AgyRunner.supports_containment is False


def test_sdk_runner_declares_both_true_if_importable():
    try:
        from agyteam.runner_sdk import SdkRunner
    except Exception:
        return          # dependency absent; nothing to assert
    assert SdkRunner.supports_audit is True
    assert SdkRunner.supports_containment is True


def test_status_reports_capabilities():
    from agyteam import lifecycle
    caps = lifecycle.runner_capabilities()
    assert "supports_audit" in caps and "supports_containment" in caps
    assert "spec" in caps


def test_capability_scoping_is_declared_by_every_runner():
    """The third runner-provided guarantee, previously undeclared.

    `tools_off` is enforced in exactly one place -- the SDK runner's
    disabled_tools -- while the introspection server told every agent its
    tools_off list as settled fact, on every runner. Measured: a manager whose
    roster entry read tools_off: [create_file, edit_file] and "Does not write
    the work" ran `sed -i` on the fixture it was auditing.
    """
    assert Runner.supports_capability_scoping is False
    assert AgyRunner.supports_capability_scoping is False
    assert MyRunner.supports_capability_scoping is False
    try:
        from agyteam.runner_sdk import SdkRunner
    except Exception:
        return
    assert SdkRunner.supports_capability_scoping is True


def test_introspection_says_whether_tools_off_is_enforced(tmp_path, monkeypatch):
    """The one reader that acts on the answer must be told which it is."""
    from agyteam import mcp_self

    td = tmp_path / "team"
    td.mkdir()
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "gates",
         "tools_off": ["create_file", "edit_file"]}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))

    monkeypatch.setenv("AGYTEAM_RUNNER", "agyteam.runner_agy:AgyRunner")
    out = mcp_self.my_capabilities("manager", team_dir=td)
    assert "create_file" in out
    assert "NOT enforced" in out, out

    monkeypatch.setenv("AGYTEAM_RUNNER", "agyteam.runner_sdk:SdkRunner")
    out = mcp_self.my_capabilities("manager", team_dir=td)
    assert "never reaches you" in out, out


def test_a_cycled_conversation_is_retired_not_discarded(tmp_path, monkeypatch):
    """--cycle-all emptied conversations.json and nothing re-registered.

    Observed shrinking live across one run: five agents, then two, then one.
    Afterwards `agyteam.session <agent>` found nobody -- the exact failure the
    base class exists to prevent -- and anything reverse-mapping a
    conversation id back to an agent failed open, silently.
    """
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(tmp_path))

    class R(Runner):
        label = "t"

        def wake(self, agent, message):
            return ""

    r = R()
    for agent, cid in [("manager", "conv-m"), ("coder", "conv-c")]:
        r.remember_conversation(agent, cid)

    r.reset("coder")
    assert r.conversation_id("coder") is None, "reset must start fresh"
    assert r.agent_for_conversation("conv-c") == "coder", \
        "attribution must survive a cycle"
    assert r.agent_for_conversation("conv-m") == "manager"

    r.reset()
    assert r.conversations() == {}
    assert r.agent_for_conversation("conv-m") == "manager"
    assert {x["agent"] for x in r.retired_conversations()} == {"manager", "coder"}
    assert r.agent_for_conversation("never-existed") is None


def _team_that_said_something(tmp_path):
    """A team with a real transcript.

    These tests used to grade /nonexistent, which happens to have no bus
    either, so they passed for a second reason as well as the intended one.
    That stopped being true once the grader learned to refuse a window with no
    bus activity at all -- a run that produced nothing is not a run whose
    evidence is unobservable. Give it a transcript so it tests the blindness
    path alone.
    """
    td = tmp_path / "team"
    td.mkdir()
    (td / "bus.jsonl").write_text(json.dumps({
        "ts": "2026-09-14 10:00:00", "from": "manager", "to": "user",
        "content": "read_all returns [None] for unwritten slots"}) + "\n")
    return td


def _grade(audit_path, env_extra=None, team_dir="/nonexistent"):
    import os
    env = {k: v for k, v in os.environ.items() if k != "AGYTEAM_AUDIT_LOG"}
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(ROOT / "bench/grade.py"), "audit_telemetry",
         "--since", "2000-01-01 00:00", "--team-dir", str(team_dir),
         "--audit", str(audit_path)],
        capture_output=True, text=True, env=env).stdout


def test_grader_refuses_to_score_evidence_it_cannot_see(tmp_path):
    """The whole point: N/A, not zero, and it says which.

    Scoring zero here once produced "every defect named and nothing executed,
    check whether the key leaked" about a team that had run 33 commands.
    """
    out = _grade("/nonexistent/audit.jsonl",
                 team_dir=_team_that_said_something(tmp_path))
    assert "N/A" in out, out
    assert "not scored" in out, out
    assert "NOT the same as" in out, out
    # and the denominator must shrink rather than carry unscoreable points
    assert "/9" not in out, out


def test_grader_does_not_blank_checks_that_read_the_bus(tmp_path):
    """Overstating blindness is the same error pointed the other way."""
    out = _grade("/nonexistent/audit.jsonl",
                 team_dir=_team_that_said_something(tmp_path))
    reproduction = [ln for ln in out.splitlines() if "concrete reproduction" in ln]
    assert reproduction, out
    assert "N/A" not in reproduction[0], reproduction[0]


def test_grader_survives_a_runner_it_cannot_construct(tmp_path):
    """Unknown must not become "refuse to grade".

    load() raises SystemExit on every misconfiguration, and SystemExit is a
    BaseException. Catching Exception around it meant one bad AGYTEAM_RUNNER
    exited the grader with no score at all -- a regression introduced by the
    commit that added the N/A handling in the first place.
    """
    out = _grade("/nonexistent/audit.jsonl",
                 env_extra={"AGYTEAM_RUNNER": "no.such.module:Nope"},
                 team_dir=_team_that_said_something(tmp_path))
    assert out.strip(), "the grader printed nothing at all"
    assert "audit_telemetry" in out
    assert "N/A" in out, out


def test_grader_says_so_when_the_user_was_never_answered(tmp_path):
    """One rep scored 9/9 with an empty to_user; its bus was tpm -> manager."""
    td = tmp_path / "team"
    td.mkdir()
    (td / "bus.jsonl").write_text(json.dumps({
        "ts": "2026-09-14 10:00:00", "from": "tpm", "to": "manager",
        "content": "read_all returns [None] for unwritten slots"}) + "\n")
    out = _grade("/nonexistent/audit.jsonl", team_dir=td)
    assert "THE USER WAS NEVER ANSWERED" in out, out


def test_grader_refuses_a_window_with_no_bus_activity(tmp_path):
    """Not a low score: a refusal. Nothing happened, or it happened elsewhere."""
    td = tmp_path / "team"
    td.mkdir()
    out = _grade("/nonexistent/audit.jsonl", team_dir=td)
    assert "NO BUS ACTIVITY" in out, out
    assert "different team directory" in out


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
