"""Changing which model an agent runs on, on purpose and on the record.

The operator's problem: a scarce strong model and an unlimited weaker one,
and a need to move agents between them without editing the roster and
restarting. Two ways that went wrong before this existed: editing the
roster's model on the SDK runner ended the agent's session with nothing
distilled, and nothing said how much of the scarce model a week had used.
"""
import json
import subprocess
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import models  # noqa: E402
from agyteam.runner import Runner  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["manager", "coder", "qa"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "gates", "model": "pro"},
        {"name": "coder", "role": "implements", "model": "flash", "effort": "high"},
        {"name": "qa", "role": "reviews", "model": "pro"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.setenv("AGYTEAM_DURABLE_DIR", str(tmp_path / "durable"))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    return td


# --- the record ---------------------------------------------------------------

def test_an_override_wins_over_the_roster_and_drops_its_effort(team):
    models.set_model(team, "coder", "opus", reason="hard week")
    assert models.resolve(team, "coder", "flash", "high")[:2] == ("opus", None)
    assert models.resolve(team, "qa", "pro")[:2] == ("pro", None)


def test_a_named_agent_beats_star(team):
    models.set_model(team, "*", "flash")
    models.set_model(team, "qa", "opus")
    assert models.resolve(team, "manager", "pro")[0] == "flash"
    assert models.resolve(team, "qa", "pro")[0] == "opus"


def test_an_override_expires_back_to_the_roster(team):
    t0 = time.time()
    models.set_model(team, "coder", "opus", until="1h", now=t0)
    assert models.resolve(team, "coder", "flash", now=t0 + 60)[0] == "opus"
    assert models.resolve(team, "coder", "flash", now=t0 + 3700)[0] == "flash"


def test_clear_returns_to_the_roster(team):
    models.set_model(team, "coder", "opus")
    models.clear(team, "coder")
    assert models.resolve(team, "coder", "flash")[0] == "flash"


def test_every_change_is_kept(team):
    models.set_model(team, "coder", "opus", reason="hard week")
    models.clear(team, "coder", reason="done")
    rows = [json.loads(l) for l in (team / "models.jsonl").read_text().splitlines()]
    assert [(r["agent"], r["model"], r["reason"]) for r in rows] == \
        [("coder", "opus", "hard week"), ("coder", None, "done")]


def test_it_is_the_operators_not_an_agents(team, monkeypatch):
    monkeypatch.setenv("AGYTEAM_AGENT", "coder")
    with pytest.raises(models.ModelError, match="coder's session"):
        models.set_model(team, "coder", "opus")


def test_unknown_agents_and_unreadable_times_are_refused(team):
    with pytest.raises(models.ModelError, match="no agent"):
        models.set_model(team, "ghost", "opus")
    with pytest.raises(models.ModelError, match="until"):
        models.set_model(team, "coder", "opus", until="next friday")


def test_the_command_line(team):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "AGYTEAM_TEAM_DIR": str(team)}
    env.pop("AGYTEAM_AGENT", None)
    run = lambda *a: subprocess.run([sys.executable, "-m", "agyteam.models", *a],  # noqa: E731
                                    capture_output=True, text=True, env=env, timeout=60)
    assert run("set", "coder", "opus", "--until", "2d").returncode == 0
    out = run("list").stdout
    assert "coder" in out and "opus" in out and "override" in out
    assert "pro" in out and "[roster]" in out


# --- every runner asks the same question ----------------------------------------

def test_runners_resolve_through_the_override(team):
    class R(Runner):
        def wake(self, agent, message):
            return ""
    models.set_model(team, "coder", "opus")
    assert R().resolved_model("coder", "flash", "high") == ("opus", None)


def test_the_host_runner_starts_conversations_on_the_override(team):
    from agyteam.runner_host import HostRunner
    r = HostRunner({"start": ["x"], "deliver": ["y"], "signal_dir": str(team)})
    assert r.model_for("coder") == "flash"
    models.set_model(team, "coder", "opus")
    assert r.model_for("coder") == "opus"


def test_the_cli_runner_passes_the_override(team, monkeypatch):
    from agyteam.runner_agy import AgyRunner
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "{}", "")
    monkeypatch.setattr(subprocess, "run", fake_run)
    r = AgyRunner({"binary": "agy"})
    r._resolved = "agy"
    models.set_model(team, "coder", "claude-opus-4-6-thinking")
    r._run("coder", "hi", None)
    i = seen["cmd"].index("--model")
    assert seen["cmd"][i + 1] == "claude-opus-4-6-thinking", "roster effort was glued on"


# --- applying a change ------------------------------------------------------------

class Distilling(ScriptedRunner):
    def __init__(self):
        super().__init__({})
        self.distills, self.resets = [], []

    def wake(self, agent, message):
        if "distill" in message.lower() or "memory" in message.lower():
            self.distills.append(agent)
        return "ok"

    def reset(self, agent=None):
        self.resets.append(agent)
        super().reset(agent)


def start(team, runner):
    sup = Supervisor(AGENTS, runner, quiet=True, require_review=False)
    sup._apply_model_changes()                 # the baseline
    return sup


def worked(sup, runner, agent):
    runner.remember_conversation(agent, f"conv-{agent}")
    sup.observer.record_turn(agent, f"conv-{agent}", 1.0)


def test_a_change_is_applied_as_a_cycle_when_the_agent_is_idle(team):
    runner = Distilling()
    sup = start(team, runner)
    try:
        worked(sup, runner, "coder")
        models.set_model(team, "coder", "opus")
        sup._apply_model_changes()
    finally:
        sup.close()
    assert runner.distills == ["coder"], "the conversation was not distilled first"
    assert runner.resets == ["coder"] and runner.conversation_id("coder") is None
    ev = [json.loads(l) for l in (team / "events.jsonl").read_text().splitlines()
          if '"model_change"' in l]
    assert ev and ev[0]["before"] == ["flash", "high"] and ev[0]["after"] == ["opus", None]


def test_a_roster_edit_is_applied_the_same_way(team):
    runner = Distilling()
    sup = start(team, runner)
    try:
        worked(sup, runner, "qa")
        doc = json.loads((team / "roster.json").read_text())
        doc["agents"][2]["model"] = "flash"
        (team / "roster.json").write_text(json.dumps(doc))
        sup._apply_model_changes()
    finally:
        sup.close()
    assert runner.distills == ["qa"] and runner.resets == ["qa"]


def test_an_agent_with_a_turn_in_flight_waits(team):
    runner = Distilling()
    sup = start(team, runner)
    try:
        worked(sup, runner, "coder")
        models.set_model(team, "coder", "opus")
        sup.inflight["coder"] = {"handle": {}}
        sup._apply_model_changes()
        assert runner.resets == []
        del sup.inflight["coder"]
        sup._apply_model_changes()
    finally:
        sup.close()
    assert runner.resets == ["coder"]


def test_an_agent_that_never_worked_is_not_woken_to_distill(team):
    runner = Distilling()
    sup = start(team, runner)
    try:
        models.set_model(team, "qa", "flash")
        sup._apply_model_changes()
    finally:
        sup.close()
    assert runner.distills == [] and runner.resets == []


def test_a_restart_does_not_cycle_anyone_again(team):
    runner = Distilling()
    sup = start(team, runner)
    worked(sup, runner, "coder")
    models.set_model(team, "coder", "opus")
    sup._apply_model_changes()
    sup.close()
    again = Distilling()
    again.remember_conversation("coder", "conv-new")
    sup = Supervisor(AGENTS, again, quiet=True, require_review=False)
    try:
        sup._apply_model_changes()
    finally:
        sup.close()
    assert again.resets == []


def test_a_change_made_while_the_supervisor_was_down_is_still_applied(team):
    runner = Distilling()
    sup = start(team, runner)
    sup.close()
    runner = Distilling()
    sup = Supervisor(AGENTS, runner, quiet=True, require_review=False)
    worked(sup, runner, "coder")
    models.set_model(team, "coder", "opus")         # while "down"
    try:
        sup._apply_model_changes()
    finally:
        sup.close()
    assert runner.resets == ["coder"]


# --- usage and allowances --------------------------------------------------------

def turns(team, model, n, tokens):
    with (team / "events.jsonl").open("a") as f:
        for _ in range(n):
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "turn",
                                "agent": "qa", "model": model,
                                "input_tokens": tokens, "output_tokens": 0}) + "\n")


def allowance(team, **limits):
    doc = json.loads((team / "roster.json").read_text())
    doc["policy"] = {"model_allowance": limits}
    (team / "roster.json").write_text(json.dumps(doc))


def test_usage_is_totalled_per_model(team):
    turns(team, "pro", 3, 1000)
    turns(team, "flash", 1, 50)
    u = models.usage(team)
    assert u["pro"]["turns"] == 3 and u["pro"]["tokens"] == 3000
    assert u["flash"]["tokens"] == 50


def test_each_threshold_is_reported_once(team):
    allowance(team, pro=10_000)
    turns(team, "pro", 1, 8_500)
    first = models.due_alerts(team)
    assert [a["threshold"] for a in first] == [80]
    assert models.due_alerts(team) == []
    turns(team, "pro", 1, 2_000)
    assert [a["threshold"] for a in models.due_alerts(team)] == [100]


def test_the_supervisor_tells_the_user_and_switches_nothing(team):
    allowance(team, pro=1_000)
    turns(team, "pro", 1, 900)
    sup = Supervisor(AGENTS, Distilling(), quiet=True, require_review=False)
    try:
        sup._model_allowance_alerts()
    finally:
        sup.close()
    told = [m for m in load_transport("user").peek() or [] if m.sender == "supervisor"]
    assert len(told) == 1 and "90%" in told[0].content and "Nothing has been switched" in told[0].content
    assert models.overrides(team) == {}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
