"""What a team running for days needs from the supervisor, each with its failure.

- An exhausted account stops the team once, rather than every agent failing,
  backing off and escalating in turn.
- A conversation that fails the same non-transient way twice is retired;
  a transient failure never retires one.
- Escalations survive a restart instead of firing again.
- A ledger turn orphaned long before a restart is cancelled, not resumed.
- distill_all runs the turns at once; cycle_all resets only who distilled.
- The scheduled sweep: once a day, only agents that worked, never into a
  turn in flight, not on excluded models.
- auto_cycle modes: distill_only keeps the conversation and distills once
  per crossing; off does nothing; context_tokens is preferred.
"""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import config  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["tpm", "coder", "qa"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "coordinates", "is_principal": True},
        {"name": "coder", "role": "implements", "model": "flash"},
        {"name": "qa", "role": "reviews", "model": "scarce-pro"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.setenv("AGYTEAM_DURABLE_DIR", str(tmp_path / "durable"))
    monkeypatch.setattr(config, "WAKE_BACKOFF_BASE_S", 0.0)
    monkeypatch.setattr(config, "WAKE_BACKOFF_MAX_S", 0.0)
    return td


def events(td, kind):
    p = td / "events.jsonl"
    if not p.exists():
        return []
    return [e for e in map(json.loads, p.read_text().splitlines()) if e.get("event") == kind]


def mail_for(name):
    return [m.content for m in load_transport(name).peek() or []]


class Failing(ScriptedRunner):
    def __init__(self, reply):
        super().__init__({})
        self.reply = reply

    def wake(self, agent, message):
        self.woken.append(agent)
        return self.reply


# --- the budget ----------------------------------------------------------------

def test_an_exhausted_account_stops_the_team_once(team):
    for a in AGENTS:
        load_transport("user").send(a, "go")
    runner = Failing("[error: coder failed: HTTP 402 insufficient_quota]")
    sup = Supervisor(AGENTS, runner, quiet=True, poll=0.01)
    try:
        sup.run_until_idle()
    finally:
        sup.close()
    assert (team / ".stop").exists() and "budget exhausted" in (team / ".stop").read_text()
    told = [m for m in load_transport("user").peek() or [] if m.sender == "supervisor"]
    assert len(told) == 1 and "lifecycle start" in told[0].content
    assert len(events(team, "budget_halt")) == 1
    assert all(mail_for(a) == ["go"] for a in AGENTS), "mail was lost to the halt"
    assert "team stopped" in sup.stopped


def test_lifecycle_start_resumes_it(team):
    from agyteam import lifecycle
    load_transport("user").send("coder", "go")
    sup = Supervisor(AGENTS, Failing("[error: credit balance is too low]"), quiet=True)
    try:
        sup.step()
    finally:
        sup.close()
    lifecycle.start_team(team_dir=team)
    ok = ScriptedRunner({})
    sup = Supervisor(AGENTS, ok, quiet=True, require_review=False)
    try:
        sup.step()
    finally:
        sup.close()
    assert ok.woken == ["coder"]


def test_an_ordinary_failure_does_not_stop_the_team(team):
    load_transport("user").send("coder", "go")
    sup = Supervisor(AGENTS, Failing("[error: 503 UNAVAILABLE]"), quiet=True)
    try:
        sup.step()
    finally:
        sup.close()
    assert not (team / ".stop").exists()


def test_agent_prose_about_quotas_is_not_a_budget_failure(team):
    load_transport("user").send("coder", "go")
    sup = Supervisor(AGENTS, Failing("I fixed the insufficient_quota handler"),
                     quiet=True, require_review=False)
    try:
        sup.step()
    finally:
        sup.close()
    assert not (team / ".stop").exists()


# --- broken conversations --------------------------------------------------------

def test_a_conversation_that_fails_the_same_way_twice_is_retired(team):
    runner = Failing("[error: coder failed: session not found]")
    runner.remember_conversation("coder", "conv-dead")
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        for _ in range(2):
            load_transport("user").send("coder", "go")
            sup._backoff.clear()
            sup.step()
    finally:
        sup.close()
    assert runner.conversation_id("coder") is None
    assert [r["conversation"] for r in runner.retired_conversations()] == ["conv-dead"]
    assert len(events(team, "reset")) == 1


def test_transient_failures_never_retire_a_conversation(team):
    runner = Failing("[error: 503 UNAVAILABLE high demand]")
    runner.remember_conversation("coder", "conv-live")
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        for _ in range(4):
            sup._backoff.clear()
            sup.step()
            if not mail_for("coder"):
                load_transport("user").send("coder", "go")
    finally:
        sup.close()
    assert runner.conversation_id("coder") == "conv-live"


# --- escalations across a restart ------------------------------------------------

def test_an_escalation_is_not_sent_again_after_a_restart(team, monkeypatch):
    monkeypatch.setattr(config, "ESCALATE_AFTER_FAILURES", 1)
    monkeypatch.setattr(config, "RESET_AFTER_FAILURES", 0)

    def blockers():
        return [m for m in (load_transport("tpm").peek() or [])
                if m.sender == "supervisor" and m.kind == "blocker"]

    for restart in range(2):
        load_transport("user").send("coder", "go")
        sup = Supervisor(AGENTS, Failing("[error: boom]"), quiet=True)
        try:
            sup.step()
        finally:
            sup.close()
    assert len(blockers()) == 1, "the restarted supervisor escalated again"
    assert "coder" in json.loads((team / "escalations.json").read_text())

    ok = ScriptedRunner({})
    sup = Supervisor(AGENTS, ok, quiet=True, require_review=False)
    try:
        sup.step()
    finally:
        sup.close()
    assert not (team / "escalations.json").exists(), "recovery did not clear it"


# --- the ledger -------------------------------------------------------------------

class Resumable(ScriptedRunner):
    resumable = True
    timeout = 60

    def __init__(self):
        super().__init__({})
        self.cancelled, self.polled = [], []

    def poll(self, handle):
        self.polled.append(handle.get("conversation"))
        return None

    def cancel(self, handle):
        self.cancelled.append(handle.get("conversation"))


def ledger(team, started):
    (team / "inflight.json").write_text(json.dumps({"coder": {
        "handle": {"conversation": "c1", "started": started},
        "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "hop": 1, "trigger": {}, "msgs": []}}))


def test_a_turn_orphaned_long_before_the_restart_is_cancelled(team):
    ledger(team, time.time() - 3 * 3600)
    runner = Resumable()
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        assert "coder" not in sup.inflight and runner.cancelled == ["c1"]
    finally:
        sup.close()


def test_a_recent_turn_is_still_resumed(team):
    ledger(team, time.time() - 30)
    runner = Resumable()
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        assert "coder" in sup.inflight and runner.cancelled == []
    finally:
        sup.close()


# --- distill_all / cycle_all --------------------------------------------------------

class SlowDistill(ScriptedRunner):
    def __init__(self, seconds=0.4, fail=()):
        super().__init__({})
        self.seconds, self.failing = seconds, set(fail)
        self.active = self.peak = 0
        self.lock = threading.Lock()

    def wake(self, agent, message):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(self.seconds)
        with self.lock:
            self.active -= 1
        return "[error: nope]" if agent in self.failing else "saved nothing new"


def worked(sup, *agents):
    for a in agents:
        sup.observer.record_turn(a, f"conv-{a}", 1.0)


def test_distill_all_runs_the_turns_at_once(team):
    runner = SlowDistill()
    sup = Supervisor(AGENTS, runner, quiet=True, poll=0.02)
    worked(sup, *AGENTS)
    t0 = time.monotonic()
    try:
        out = sup.distill_all()
    finally:
        sup.close()
    assert set(out) == set(AGENTS) and runner.peak == 3
    assert time.monotonic() - t0 < 1.0, "three 0.4s distills ran one after another"


def test_cycle_all_resets_only_the_agents_that_distilled(team):
    runner = SlowDistill(0.05, fail={"qa"})
    for a in AGENTS:
        runner.remember_conversation(a, f"conv-{a}")
    sup = Supervisor(AGENTS, runner, quiet=True, poll=0.02)
    worked(sup, *AGENTS)
    try:
        sup.cycle_all()
    finally:
        sup.close()
    assert runner.conversation_id("qa") == "conv-qa"
    assert runner.conversation_id("coder") is None and runner.conversation_id("tpm") is None


def test_distill_all_skips_an_agent_with_a_turn_in_flight(team):
    runner = SlowDistill(0.05)
    sup = Supervisor(AGENTS, runner, quiet=True, poll=0.02)
    worked(sup, *AGENTS)
    try:
        sup.inflight["coder"] = {"handle": {}}
        out = sup.distill_all()
        sup.inflight.clear()
    finally:
        sup.close()
    assert "coder" not in out and set(out) == {"tpm", "qa"}


def test_an_agent_with_nothing_of_its_own_is_not_distilled(team):
    """Asked anyway, it read everyone else's records for 2.1M tokens."""
    runner = SlowDistill(0.0)
    sup = Supervisor(AGENTS, runner, quiet=True, poll=0.01)
    worked(sup, "qa")
    runner.remember_conversation("tpm", "conv-tpm")
    worked(sup, "tpm")
    runner.reset("tpm")                      # cycled since its only turn
    try:
        out = sup.distill_all()
    finally:
        sup.close()
    assert set(out) == {"qa"}


# --- the scheduled sweep --------------------------------------------------------------

def turns(sup, agent, n):
    for _ in range(n):
        sup.observer.record_turn(agent, "c", 1.0)


def schedule(team, **cfg):
    doc = json.loads((team / "roster.json").read_text())
    doc["nightly_distill"] = {"hour_utc": 0, **cfg}
    (team / "roster.json").write_text(json.dumps(doc))


def test_the_sweep_takes_only_agents_that_worked_and_runs_once_a_day(team):
    schedule(team, exclude_models=["scarce-pro"])
    runner = SlowDistill(0.01)
    sup = Supervisor(AGENTS, runner, quiet=True, poll=0.01)
    try:
        turns(sup, "coder", 3)
        turns(sup, "qa", 5)            # worked, but on an excluded model
        turns(sup, "tpm", 1)           # under min_turns_24h
        first = sup.distill_sweep(scheduled=True)
        again = sup.distill_sweep(scheduled=True)
        forced = sup.distill_sweep(force=True)
    finally:
        sup.close()
    assert first["ran"] and first["agents"] == ["coder"]
    assert "excluded" in first["skipped"]["qa"] and "1 turn" in first["skipped"]["tpm"]
    assert not again["ran"] and "ago" in again["reason"]
    assert forced["ran"]


def test_the_sweep_survives_a_restart(team):
    schedule(team)
    for _ in range(2):
        sup = Supervisor(AGENTS, SlowDistill(0.01), quiet=True, poll=0.01)
        try:
            turns(sup, "coder", 2)
            out = sup.distill_sweep(scheduled=True)
        finally:
            sup.close()
    assert not out["ran"], "a restarted supervisor swept a second time"
    assert len(events(team, "distill_sweep")) == 1


def test_the_sweep_waits_for_its_hour_and_needs_a_schedule(team):
    sup = Supervisor(AGENTS, SlowDistill(0.01), quiet=True)
    try:
        assert sup.distill_sweep(scheduled=True)["reason"] == "no nightly_distill in the roster"
        schedule(team, hour_utc=24)                     # never comes
        assert sup.distill_sweep(scheduled=True)["reason"] == "not yet the hour"
    finally:
        sup.close()


def test_a_dry_run_records_nothing(team):
    schedule(team)
    sup = Supervisor(AGENTS, SlowDistill(0.01), quiet=True)
    try:
        turns(sup, "coder", 2)
        out = sup.distill_sweep(dry_run=True)
    finally:
        sup.close()
    assert out["agents"] == ["coder"] and not events(team, "distill_sweep")


# --- auto_cycle modes ----------------------------------------------------------------------

def big_turn(sup, agent, conv, **tokens):
    sup.observer.record_turn(agent, conv, 1.0, **tokens)


def test_distill_only_keeps_the_conversation_and_distills_once_per_crossing(team, monkeypatch):
    monkeypatch.setattr(config, "AUTO_CYCLE_MODE", "distill_only")
    runner = SlowDistill(0.0)
    runner.remember_conversation("coder", "conv-host")
    distilled = []
    sup = Supervisor(AGENTS, runner, quiet=True)
    sup.distill = lambda a: distilled.append(a) or (0, 0)
    try:
        big_turn(sup, "coder", "conv-host", input_tokens=10, context_tokens=150_000)
        sup.auto_cycle()
        big_turn(sup, "coder", "conv-host", context_tokens=160_000)
        sup.auto_cycle()                                    # still above: not again
        big_turn(sup, "coder", "conv-host", context_tokens=40_000)
        sup.auto_cycle()                                    # host compacted: re-arm
        big_turn(sup, "coder", "conv-host", context_tokens=120_000)
        sup.auto_cycle()
    finally:
        sup.close()
    assert distilled == ["coder", "coder"]
    assert runner.conversation_id("coder") == "conv-host"


def test_off_does_nothing_and_a_runner_can_choose_its_mode(team, monkeypatch):
    runner = SlowDistill(0.0)
    runner.auto_cycle_mode = "off"
    runner.remember_conversation("coder", "conv")
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        big_turn(sup, "coder", "conv", input_tokens=500_000)
        assert sup.auto_cycle() == []
    finally:
        sup.close()
    assert runner.conversation_id("coder") == "conv"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
