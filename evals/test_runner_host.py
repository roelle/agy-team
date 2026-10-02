"""runner_host against a fake host: the surface every hosted runtime has.

start, deliver, cancel, and a stop hook that appends to a signal file. Checks
the two-phase start registers the conversation before anything is delivered,
that completion is detected by line count, that a timed-out turn is
cancelled, and that the whole thing drives a supervisor cascade.
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam.runner_host import HostRunner  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402

HOST = str(ROOT / "evals" / "fixture_host.py")


@pytest.fixture
def host(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "coordinates", "is_principal": True},
        {"name": "coder", "role": "implements", "model": "roster-model"}]}))
    signals = tmp_path / "signals"
    signals.mkdir()
    log = tmp_path / "host.jsonl"
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.setenv("FAKE_HOST_LOG", str(log))
    monkeypatch.setenv("FAKE_HOST_SIGNALS", str(signals))
    monkeypatch.setenv("FAKE_HOST_DELAY", "0.15")
    for k in ("FAKE_HOST_HANG", "FAKE_HOST_FAIL"):
        monkeypatch.delenv(k, raising=False)
    config = {
        "start": [sys.executable, HOST, "start", "--agent", "{agent}",
                  "--model", "{model}", "--prompt", "{prompt}"],
        "deliver": [sys.executable, HOST, "send", "--conversation", "{conversation}",
                    "--message", "{message}"],
        "cancel": [sys.executable, HOST, "cancel", "{conversation}"],
        "signal_dir": str(signals), "timeout": 2, "poll_seconds": 0.05,
        "model_map": {"tpm": "mapped-model", "*": "default-model"},
    }
    return td, log, config


def calls(log):
    return [json.loads(l) for l in log.read_text().splitlines() if l.strip()]


def test_two_phase_start_registers_before_it_delivers(host):
    td, log, config = host
    runner = HostRunner(config)
    assert runner.wake("coder", "build it") == ""
    made = calls(log)
    assert [c["call"] for c in made] == ["start", "send"]
    assert made[0]["prompt"].startswith("This session was created"), "the primer must be content-free"
    assert made[1]["registered"] is True, "delivered before conversations.json knew the id"
    assert "You are 'coder'" in made[1]["message"] and "build it" in made[1]["message"]
    assert runner.conversation_id("coder") == "conv-1"


def test_a_second_wake_resumes_without_the_brief(host):
    td, log, config = host
    runner = HostRunner(config)
    runner.wake("coder", "first")
    runner.wake("coder", "second")
    sends = [c for c in calls(log) if c["call"] == "send"]
    assert len(sends) == 2 and "You are 'coder'" not in sends[1]["message"]
    assert sends[1]["conversation"] == "conv-1"


def test_the_model_comes_from_the_roster_then_the_map_then_the_default(host):
    td, log, config = host
    runner = HostRunner(config)
    assert runner.model_for("coder") == "roster-model"
    assert runner.model_for("tpm") == "mapped-model"
    assert runner.model_for("nobody") == "default-model"
    runner.wake("tpm", "hi")
    assert calls(log)[0]["model"] == "mapped-model"


def test_identity_reaches_the_host_process(host):
    td, log, config = host
    HostRunner(config).wake("coder", "hi")
    assert all(c["agent_env"] == "coder" for c in calls(log))


def test_a_turn_that_never_ends_is_cancelled_and_reported(host, monkeypatch):
    td, log, config = host
    monkeypatch.setenv("FAKE_HOST_HANG", "1")
    config["start_timeout"] = 1
    runner = HostRunner(config)
    t0 = time.monotonic()
    reply = runner.wake("coder", "hi")
    assert reply.startswith("[error:") and "primer turn never ended" in reply
    assert time.monotonic() - t0 < 5


def test_a_hung_delivered_turn_is_cancelled(host, monkeypatch):
    td, log, config = host
    runner = HostRunner(config)
    runner.wake("coder", "warm up")           # conversation exists now
    monkeypatch.setenv("FAKE_HOST_HANG", "1")
    reply = runner.wake("coder", "hang")
    assert "timed out" in reply and "cancel sent" in reply
    assert [c["call"] for c in calls(log)][-1] == "cancel"
    fails = [json.loads(l) for l in (td / "events.jsonl").read_text().splitlines()
             if '"failure"' in l]
    assert fails and fails[-1]["agent"] == "coder"


def test_a_failed_deliver_is_an_error_not_an_exception(host, monkeypatch):
    td, log, config = host
    monkeypatch.setenv("FAKE_HOST_FAIL", "1")
    reply = HostRunner(config).wake("coder", "hi")
    assert reply.startswith("[error:") and "session not found" in reply


def test_token_counts_from_the_stop_hook_are_recorded(host):
    td, log, config = host
    HostRunner(config).wake("coder", "hi")
    turns = [json.loads(l) for l in (td / "events.jsonl").read_text().splitlines()
             if '"turn"' in l]
    assert turns[-1]["input_tokens"] == 120 and turns[-1]["model"] == "roster-model"


def test_the_message_can_travel_on_stdin(host):
    td, log, config = host
    config["deliver"] = [sys.executable, HOST, "send", "--conversation", "{conversation}"]
    config["deliver_stdin"] = True
    HostRunner(config).wake("coder", "a message with\nnewlines and `backticks`")
    send = [c for c in calls(log) if c["call"] == "send"][0]
    assert "newlines and `backticks`" in send["message"]


def test_a_cascade_runs_through_the_supervisor(host):
    """Non-blocking turns, several in flight, driven by begin/poll."""
    td, log, config = host
    runner = HostRunner(config)
    sup = Supervisor(["tpm", "coder"], runner, quiet=True, poll=0.05,
                     require_review=False)
    load_transport("user").send("tpm", "go")
    load_transport("user").send("coder", "go")
    t0 = time.monotonic()
    try:
        turns = sup.run_until_idle()
    finally:
        sup.close()
    assert turns == 2
    starts = [c for c in calls(log) if c["call"] == "start"]
    assert sorted(c["agent"] for c in starts) == ["coder", "tpm"]
    assert time.monotonic() - t0 < 2.5, "the two primer+turn pairs did not overlap"


def test_turns_the_operator_takes_in_the_host_ui_reach_the_record(host):
    """The stop hook fires for every turn; the ones this runner did not
    start used to leave no trace."""
    td, log, config = host
    runner = HostRunner(config)
    runner.wake("coder", "first")
    signal = Path(config["signal_dir"]) / "conv-1.jsonl"
    with signal.open("a") as f:                     # two turns in the host UI
        f.write(json.dumps({"turn": "ended"}) + "\n")
        f.write(json.dumps({"turn": "ended"}) + "\n")
    runner.wake("coder", "second")
    turns = [json.loads(l) for l in (td / "events.jsonl").read_text().splitlines()
             if '"turn"' in l]
    by_host = [t for t in turns if t.get("started_by") == "host"]
    assert len(by_host) == 2 and all(t["agent"] == "coder" for t in by_host)
    assert len([t for t in turns if not t.get("started_by")]) == 2


def test_a_restart_does_not_replay_history_as_operator_turns(host):
    """Measured: 442 phantom started_by-host turn events after one restart,
    one per historical signal line, all stamped with the current time."""
    td, log, config = host
    first = HostRunner(config)
    for _ in range(3):
        first.wake("coder", "work")
    before = (td / "events.jsonl").read_text().count('"turn"')

    second = HostRunner(config)                   # the supervisor restarted
    second.wake("coder", "more work")
    turns = [json.loads(l) for l in (td / "events.jsonl").read_text().splitlines()
             if '"turn"' in l]
    assert not [t for t in turns if t.get("started_by") == "host"], \
        "history before the restart was replayed as the operator's turns"
    assert (td / "events.jsonl").read_text().count('"turn"') == before + 1


def test_operator_turns_between_restarts_are_still_recorded(host):
    td, log, config = host
    first = HostRunner(config)
    first.wake("coder", "work")
    signal = Path(config["signal_dir"]) / "conv-1.jsonl"
    with signal.open("a") as f:                      # one turn in the host UI
        f.write(json.dumps({"turn": "ended"}) + "\n")
    second = HostRunner(config)                      # watermark survived the restart
    second.wake("coder", "more")
    turns = [json.loads(l) for l in (td / "events.jsonl").read_text().splitlines()
             if '"turn"' in l]
    assert len([t for t in turns if t.get("started_by") == "host"]) == 1


def test_a_conversation_first_seen_with_history_is_not_a_burst(host):
    """No watermark at all -- the team predates this code -- seeds at the
    file's current length."""
    td, log, config = host
    runner = HostRunner(config)
    runner.remember_conversation("coder", "conv-old")
    signal = Path(config["signal_dir"]) / "conv-old.jsonl"
    signal.write_text("".join(json.dumps({"turn": "ended"}) + "\n" for _ in range(50)))
    runner.wake("coder", "hello again")
    turns = [json.loads(l) for l in (td / "events.jsonl").read_text().splitlines()
             if '"turn"' in l]
    assert not [t for t in turns if t.get("started_by") == "host"]
    assert json.loads((td / "host_signals.json").read_text())["conv-old"] == 51


def test_the_primer_turn_gets_the_turn_timeout_not_the_command_timeout(host, monkeypatch):
    td, log, config = host
    monkeypatch.setenv("FAKE_HOST_DELAY", "0.8")
    config["start_timeout"] = 0.3                    # bounds the command only
    config["timeout"] = 5
    assert HostRunner(config).wake("coder", "hi") == ""


def test_missing_configuration_fails_at_load(host):
    with pytest.raises(SystemExit, match="signal_dir"):
        HostRunner({"start": ["x"], "deliver": ["y"]})


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
