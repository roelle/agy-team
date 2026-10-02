"""Turns run concurrently, failures back off and leave a trace, restarts resume.

Measured before this: in an 11-message exercise eight turns finished one at a
time over 35 minutes, one twelve-minute turn holding everyone else; a wake
path that failed instantly was counted as a dispatched turn, so the daemon
never slept -- two process spawns a second, for days -- and events.jsonl said
nothing about it, so a broken team looked like an idle one.
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import config  # noqa: E402
from agyteam.runner import Runner  # noqa: E402
from agyteam.supervisor import Supervisor, supervisor_running  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["tpm", "coder", "qa", "syseng"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": a, "role": "works", **({"is_principal": True} if a == "tpm" else {})}
        for a in AGENTS]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.setattr(config, "WAKE_BACKOFF_BASE_S", 0.2)
    monkeypatch.setattr(config, "WAKE_BACKOFF_MAX_S", 0.5)
    return td


def events(td, kind=None):
    out = []
    for line in (td / "events.jsonl").read_text().splitlines():
        if line.strip():
            e = json.loads(line)
            if kind is None or e.get("event") == kind:
                out.append(e)
    return out


class SlowRunner(ScriptedRunner):
    def __init__(self, seconds):
        super().__init__({})
        self.seconds = seconds

    def wake(self, agent, message):
        self.woken.append(agent)
        time.sleep(self.seconds)
        return "ok"


def test_four_turns_take_one_turns_time_not_four(team):
    for a in AGENTS:
        load_transport("user").send(a, "go")
    runner = SlowRunner(0.5)
    sup = Supervisor(AGENTS, runner, require_review=False, quiet=True, poll=0.05)
    t0 = time.monotonic()
    try:
        turns = sup.run_until_idle()
    finally:
        sup.close()
    wall = time.monotonic() - t0
    assert turns == 4 and sorted(runner.woken) == sorted(AGENTS)
    assert wall < 1.5, f"four 0.5s turns took {wall:.1f}s: they ran one at a time"


def test_a_long_turn_does_not_hold_up_a_new_message_for_someone_else(team):
    """The failure as measured: one slow agent, everyone else waits."""
    done = {}

    class Mixed(ScriptedRunner):
        def wake(self, agent, message):
            self.woken.append(agent)
            time.sleep(1.0 if agent == "coder" else 0.05)
            done[agent] = time.monotonic()
            return "ok"

    runner = Mixed({})
    load_transport("user").send("coder", "a long job")
    sup = Supervisor(AGENTS, runner, require_review=False, quiet=True, poll=0.05)
    stop = threading.Event()
    th = threading.Thread(target=sup.run_forever, args=(stop,), daemon=True)
    t0 = time.monotonic()
    th.start()
    time.sleep(0.2)                              # coder is mid-turn
    load_transport("user").send("qa", "a quick question")
    try:
        deadline = time.monotonic() + 5
        while "qa" not in done and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        stop.set()
        th.join(timeout=5)
        sup.close()
    assert "qa" in done, "qa was never woken"
    assert done["qa"] - t0 < 0.8, "qa waited for coder's turn to finish"


def test_the_pass_is_still_synchronous_for_callers_of_step(team):
    load_transport("user").send("coder", "go")
    runner = SlowRunner(0.2)
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        assert sup.step() == 1
        assert not sup.inflight, "step() returned with the turn still running"
    finally:
        sup.close()


# --- failures ------------------------------------------------------------------

class Broken(ScriptedRunner):
    """A wake path that fails instantly, as a misconfigured host does."""

    def wake(self, agent, message):
        self.woken.append(agent)
        return "[error: deliver exited 1: no such session]"


def test_a_failing_wake_path_backs_off_instead_of_spinning(team):
    load_transport("user").send("coder", "go")
    runner = Broken({})
    sup = Supervisor(AGENTS, runner, require_review=False, quiet=True, poll=0.02)
    stop = threading.Event()
    th = threading.Thread(target=sup.run_forever, args=(stop,), daemon=True)
    th.start()
    time.sleep(1.5)
    stop.set()
    th.join(timeout=5)
    sup.close()
    # 0.2 + 0.4 + 0.5 + 0.5 ... : a handful of attempts in 1.5s, not hundreds.
    # (tpm is woken too, by the escalation, and fails the same way.)
    attempts = runner.woken.count("coder")
    assert 2 <= attempts <= 6, f"{attempts} attempts in 1.5s"


def test_failures_are_on_the_record_even_when_the_runner_recorded_nothing(team):
    load_transport("user").send("coder", "go")
    sup = Supervisor(AGENTS, Broken({}), quiet=True)
    try:
        sup.step()
    finally:
        sup.close()
    fails = events(team, "failure")
    assert len(fails) == 1 and fails[0]["agent"] == "coder"
    assert "no such session" in fails[0]["error"]
    disp = events(team, "dispatch")
    assert disp and disp[0]["failed"] is True and disp[0]["trigger"]["senders"] == ["user"]


def test_a_runner_that_records_its_own_failure_is_not_recorded_twice(team):
    class SelfReporting(ScriptedRunner):
        def wake(self, agent, message):
            self.woken.append(agent)
            self.observer.record_failure(agent, "c1", "[error: boom]", duration_s=0.1)
            return "[error: boom]"

    load_transport("user").send("coder", "go")
    sup = Supervisor(AGENTS, SelfReporting({}), quiet=True)
    try:
        sup.step()
    finally:
        sup.close()
    assert len(events(team, "failure")) == 1


def test_repeated_failures_escalate_once(team, monkeypatch):
    monkeypatch.setattr(config, "ESCALATE_AFTER_FAILURES", 3)
    load_transport("user").send("coder", "go")
    sup = Supervisor(AGENTS, Broken({}), quiet=True)
    try:
        for _ in range(6):
            sup.step()
    finally:
        sup.close()
    tpm = load_transport("tpm").peek()
    escalations = [m for m in tpm if "failed 3 turns in a row" in m.content]
    assert len(escalations) == 1, [m.content for m in tpm]
    assert escalations[0].kind == "blocker"


def test_a_crash_in_begin_is_a_failed_turn_not_a_dead_supervisor(team):
    class CrashOnBegin(ScriptedRunner):
        def begin(self, agent, message):
            raise OSError("spawn failed")

    load_transport("user").send("coder", "go")
    load_transport("user").send("qa", "go")
    runner = CrashOnBegin({})
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        sup.step()
    finally:
        sup.close()
    assert len(events(team, "failure")) == 2
    assert len(load_transport("coder").peek()) == 1, "mail must survive a failed begin"


# --- restarts --------------------------------------------------------------------

class HostLike(Runner):
    """A runner whose turns run out of process: the handle is a file the
    "host" appends to when the turn ends, so another process can poll it."""
    label = "hostlike"
    resumable = True

    def __init__(self, config=None, observer=None):
        super().__init__(config, observer=observer)
        self.begun = []

    def wake(self, agent, message):
        raise AssertionError("the supervisor must use begin/poll here")

    def begin(self, agent, message):
        self.begun.append(agent)
        signal = Path(os.environ["AGYTEAM_TEAM_DIR"]) / f"signal-{agent}"
        return {"agent": agent, "signal": str(signal)}

    def poll(self, handle):
        return "" if Path(handle["signal"]).exists() else None

    def wait_turn(self, timeout):
        time.sleep(min(timeout, 0.05))


def test_a_resumable_turn_survives_a_supervisor_restart(team):
    load_transport("user").send("coder", "go")
    first = HostLike()
    sup = Supervisor(AGENTS, first, quiet=True)
    begun, _ = sup._pass()
    assert begun == ["coder"] and (team / "inflight.json").exists()
    del sup                                 # gone without close(): a kill

    second = HostLike()
    sup2 = Supervisor(AGENTS, second, quiet=True, poll=0.05)
    try:
        assert "coder" in sup2.inflight, "the restart forgot a turn the host is still running"
        begun, _ = sup2._pass()
        assert begun == [], "the restart began a second turn on top of the first"
        (team / "signal-coder").write_text("done\n")
        sup2._drain()
        assert sup2.inflight == {}
    finally:
        sup2.close()
    assert second.begun == [], "no double wake"
    assert load_transport("coder").peek() == [], "the turn's mail was acknowledged once it ended"
    assert json.loads((team / "inflight.json").read_text()) == {}


def test_a_thread_turn_does_not_survive_a_restart_and_its_mail_is_redelivered(team):
    load_transport("user").send("coder", "go")
    sup = Supervisor(AGENTS, SlowRunner(30), quiet=True)
    begun, _ = sup._pass()
    assert begun == ["coder"]
    # The process dies here. The ledger still names coder's turn.
    del sup

    runner = ScriptedRunner({})
    sup2 = Supervisor(AGENTS, runner, quiet=True)
    try:
        assert sup2.inflight == {}
        assert sup2.step() == 1 and runner.woken == ["coder"]
    finally:
        sup2.close()
    fails = events(team, "failure")
    assert any("restarted mid-turn" in f["error"] for f in fails)


# --- what the agent is woken with ----------------------------------------------------

def test_inbox_pull_wakes_with_a_summary_and_the_agent_reads_the_mail(team):
    seen = {}

    class Puller(ScriptedRunner):
        inbox_pull = True

        def wake(self, agent, message):
            self.woken.append(agent)
            seen["prompt"] = message
            inbox = load_transport(agent)
            seen["mail"] = inbox.fetch()
            inbox.acknowledge(seen["mail"])
            return "ok"

    load_transport("user").send("coder", "the actual body of the request")
    load_transport("tpm").send_kind("coder", "fyi only", kind="fyi", task_id="task_1")
    sup = Supervisor(AGENTS, Puller({}), quiet=True)
    try:
        sup.step()
    finally:
        sup.close()
    assert "actual body" not in seen["prompt"]
    assert "[wake] 2 message(s)" in seen["prompt"]
    assert "from=user kind=work" in seen["prompt"] and "task_id=task_1" in seen["prompt"]
    assert sorted(m.content for m in seen["mail"]) == ["fyi only", "the actual body of the request"]
    assert load_transport("coder").peek() == []


def test_inbox_pull_does_not_redeliver_what_the_agent_already_took(team):
    """A turn that pulled its mail and then timed out had processed it; the
    snapshot was requeued anyway, and the next wake did the work twice."""
    calls = []

    class PullThenDie(ScriptedRunner):
        inbox_pull = True

        def wake(self, agent, message):
            calls.append(agent)
            inbox = load_transport(agent)
            got = inbox.fetch()
            inbox.acknowledge(got)        # read it, then the host times out
            if len(calls) == 1:
                return "[error: timed out]"
            return "ok"

    load_transport("user").send("coder", "please")
    sup = Supervisor(AGENTS, PullThenDie({}), quiet=True)
    try:
        sup.step()
        assert load_transport("coder").peek() == [], "acknowledged mail came back"
        assert sup.step() == 0
    finally:
        sup.close()
    assert calls == ["coder"]


def test_inbox_pull_still_redelivers_what_was_never_taken(team):
    """The other half: a turn that died before reading leaves the mail where
    it was, and the next wake gets it."""
    calls = []

    class DieBeforeReading(ScriptedRunner):
        inbox_pull = True

        def wake(self, agent, message):
            calls.append(agent)
            if len(calls) == 1:
                return "[error: deliver failed]"
            inbox = load_transport(agent)
            got = inbox.fetch()
            inbox.acknowledge(got)
            return "ok"

    load_transport("user").send("coder", "please")
    sup = Supervisor(AGENTS, DieBeforeReading({}), quiet=True)
    try:
        sup.step()
        assert [m.content for m in load_transport("coder").peek()] == ["please"]
        sup.step()
        assert load_transport("coder").peek() == []
    finally:
        sup.close()
    assert calls == ["coder", "coder"]


def test_a_running_supervisor_leaves_a_heartbeat(team):
    assert supervisor_running(team) is None
    sup = Supervisor(AGENTS, ScriptedRunner({}), quiet=True)
    try:
        sup.step()
        beat = supervisor_running(team)
        assert beat and beat["pid"] == os.getpid()
    finally:
        sup.close()
    assert supervisor_running(team) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
