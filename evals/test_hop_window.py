"""A running team's hop budget is a rate, and it never holds the user up.

Reported: a busy team reached its 32 hops while one agent was mid-turn. The
budget reset only once no turn was in flight, so while that turn ran nobody
else woke -- including an agent the user had just @mentioned in agy-chat --
and nothing on screen said why.
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
from agyteam.record import Record  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["tpm", "coder", "qa"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": a, "role": "works", **({"is_principal": True} if a == "tpm" else {})}
        for a in AGENTS]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    return td


class LongTpm(ScriptedRunner):
    """tpm's turn lasts until released; everyone else's ends at once."""

    def __init__(self):
        super().__init__({})
        self.release = threading.Event()

    def wake(self, agent, message):
        self.woken.append(agent)
        if agent == "tpm":
            self.release.wait(10)
        return "ok"


def send(sender, to, text):
    bus = load_transport(sender)
    try:
        bus.send(to, text)
    finally:
        bus.close()


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def daemon(team):
    runner = LongTpm()
    sup = Supervisor(AGENTS, runner, require_review=False, max_hops=2,
                     quiet=True, poll=0.02)
    stop = threading.Event()
    th = threading.Thread(target=sup.run_forever, args=(stop,), daemon=True)
    th.start()
    # Spend the budget: tpm's long turn, then one quick turn for coder.
    send("user", "tpm", "plan it")
    assert wait_for(lambda: "tpm" in sup.inflight)
    send("qa", "coder", "first")
    assert wait_for(lambda: runner.woken.count("coder") == 1)
    yield sup, runner
    runner.release.set()
    stop.set()
    th.join(5)
    sup.close()


def test_a_long_turn_does_not_hold_the_budget_past_its_window(daemon, monkeypatch):
    sup, runner = daemon
    monkeypatch.setattr(config, "HOP_WINDOW_S", 600)
    send("qa", "coder", "second")
    assert wait_for(lambda: "coder" in sup._held), "agent mail should wait once the budget is spent"
    assert runner.woken.count("coder") == 1
    # The window passes while tpm is still mid-turn: coder wakes anyway.
    monkeypatch.setattr(config, "HOP_WINDOW_S", 0.0)
    assert wait_for(lambda: runner.woken.count("coder") == 2), \
        "the budget only came back once no turn was in flight"
    assert "tpm" in sup.inflight


def test_the_users_message_wakes_an_agent_when_the_budget_is_spent(daemon, monkeypatch):
    sup, runner = daemon
    monkeypatch.setattr(config, "HOP_WINDOW_S", 600)
    send("qa", "coder", "second")
    assert wait_for(lambda: "coder" in sup._held)
    send("user", "coder", "@coder are you there?")
    assert wait_for(lambda: runner.woken.count("coder") == 2), \
        "the user's message waited on a budget meant for agents waking agents"


def test_a_held_agent_shows_as_held(daemon, monkeypatch, team):
    sup, runner = daemon
    monkeypatch.setattr(config, "HOP_WINDOW_S", 600)
    send("qa", "coder", "second")
    assert wait_for(lambda: "coder" in sup._held)
    sup._heartbeat_at = 0.0             # write the heartbeat on the next pass
    assert wait_for(lambda: Record(team).presence()["coder"]["state"] == "held")
    p = Record(team).presence()
    assert "2 of 2 wakes in the last 10 min" in p["coder"]["detail"], p["coder"]
    assert p["tpm"]["state"] == "running"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
