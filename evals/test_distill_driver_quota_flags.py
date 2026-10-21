"""Three things reported from use, each with the failure it caused.

- `--distill coder` while a daemon drove the team woke coder in a second
  process, in the middle of the daemon's turn: it checked only its own
  process's turns, and it had none.
- A distill needs the model it distills on. Left until the allowance or the
  account ran out, it failed exactly when it was needed.
- create_task's requires_review was declared a string and read as one: a JSON
  `true` and a typo like "ttur" were both a quiet false.
"""
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import config, mcp_base, mcp_tasks, models  # noqa: E402
from agyteam.supervisor import DISTILL_PROMPT, Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["tpm", "coder", "qa"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "coordinates"}, {"name": "coder", "role": "implements"},
        {"name": "qa", "role": "reviews"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    monkeypatch.setattr(config, "WAKE_BACKOFF_BASE_S", 0.0)
    monkeypatch.setattr(config, "WAKE_BACKOFF_MAX_S", 0.0)
    return td


# --- a distill asked from outside goes through the driver -----------------------

DRIVER = textwrap.dedent("""
    import json, os, sys, time
    sys.path.insert(0, {root!r}); sys.path.insert(0, {evals!r})
    os.environ["AGYTEAM_TEAM_DIR"] = {td!r}
    from agyteam.supervisor import Supervisor
    from fixture_runner import ScriptedRunner

    class Slow(ScriptedRunner):
        def wake(self, agent, message):
            what = "distill" if "memory" in message.lower() else "turn"
            start = time.time()
            if what == "turn":
                time.sleep(2.0)
            with open({log!r}, "a") as f:
                f.write(json.dumps({{"agent": agent, "what": what, "start": start,
                                     "end": time.time(), "pid": os.getpid()}}) + "\\n")
            return "ok"

    sup = Supervisor(["tpm", "coder", "qa"], Slow({{}}), quiet=True, poll=0.2,
                     require_review=False)
    sup.run_forever()
""")


def test_a_distill_from_outside_waits_for_the_drivers_turn_and_runs_there(team, tmp_path):
    log = tmp_path / "wakes.jsonl"
    driver = subprocess.Popen([sys.executable, "-c", DRIVER.format(
        root=str(ROOT), evals=str(ROOT / "evals"), td=str(team), log=str(log))])
    try:
        for _ in range(100):                                     # the driver is up
            if (team / "supervisor.json").exists():
                break
            time.sleep(0.1)
        load_transport("user").send("coder", "start the parser")
        for _ in range(100):                                     # coder's turn is running
            if "coder" in json.loads((team / "inflight.json").read_text() or "{}") \
                    if (team / "inflight.json").exists() else False:
                break
            time.sleep(0.05)
        outside = Supervisor(AGENTS, ScriptedRunner({}), quiet=True)
        try:
            outside.distill("coder")
            assert outside.runner.woken == [], "the outside process woke coder itself"
            assert not outside._last_distill_failed
        finally:
            outside.close()
        rows = [json.loads(l) for l in log.read_text().splitlines()]
        turn = next(r for r in rows if r["what"] == "turn")
        distill = next(r for r in rows if r["what"] == "distill")
        assert distill["pid"] == driver.pid, "the distill ran outside the driving supervisor"
        assert distill["start"] >= turn["end"], "the distill ran into coder's turn"
    finally:
        driver.kill()
        driver.wait()


def test_with_no_driver_a_distill_runs_here_and_never_into_a_turn_in_flight(team):
    runner = ScriptedRunner({})
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        sup.inflight["coder"] = {"handle": {}, "msgs": [], "ts": "", "hop": 0,
                                 "trigger": {}, "started": time.monotonic()}
        sup.distill("coder")
        assert runner.woken == [] and sup._last_distill_failed
        del sup.inflight["coder"]
        sup.distill("coder")
        assert runner.woken == ["coder"]
    finally:
        sup.close()


# --- distill while there is still quota ----------------------------------------

class Records(ScriptedRunner):
    def __init__(self):
        super().__init__({})
        self.prompts = []

    def wake(self, agent, message):
        self.prompts.append((agent, message))
        return "ok"

    def reset(self, agent=None):
        raise AssertionError("a distill ahead of the allowance must keep the conversation")


def turn_on(team, agent, model, tokens):
    with (team / "events.jsonl").open("a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "turn",
                            "agent": agent, "conversation": "c1", "model": model,
                            "input_tokens": tokens, "output_tokens": 0}) + "\n")


def test_at_the_first_allowance_warning_its_agents_distill_and_keep_their_conversations(team):
    doc = json.loads((team / "roster.json").read_text())
    doc["policy"] = {"model_allowance": {"pro": 1000}}
    (team / "roster.json").write_text(json.dumps(doc))
    turn_on(team, "coder", "pro", 850)                    # 85%
    turn_on(team, "qa", "flash", 50)                      # another model
    runner = Records()
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        sup._model_allowance_alerts()
    finally:
        sup.close()
    assert [a for a, m in runner.prompts if m == DISTILL_PROMPT] == ["coder"]
    told = [m.content for m in load_transport("user").peek() if m.sender == "supervisor"]
    assert len(told) == 1 and "Distilled coder" in told[0]


def test_the_distill_ahead_can_be_turned_off(team):
    doc = json.loads((team / "roster.json").read_text())
    doc["policy"] = {"model_allowance": {"pro": 1000}, "distill_before_allowance": False}
    (team / "roster.json").write_text(json.dumps(doc))
    turn_on(team, "coder", "pro", 850)
    runner = Records()
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        sup._model_allowance_alerts()
    finally:
        sup.close()
    assert runner.prompts == []


def test_a_distill_that_fails_for_quota_says_so_and_keeps_the_conversation(team, capsys):
    class OutOfQuota(Records):
        def wake(self, agent, message):
            return "[error: 429 insufficient_quota]"

    runner = OutOfQuota()
    runner.remember_conversation("coder", "c-live")
    sup = Supervisor(AGENTS, runner, quiet=False)
    try:
        sup.cycle("coder")
    finally:
        sup.close()
    assert runner.conversation_id("coder") == "c-live"
    out = capsys.readouterr()
    assert "out of quota" in out.out + out.err and "nothing it learned is lost" in out.out + out.err
    events = [json.loads(l) for l in (team / "events.jsonl").read_text().splitlines()]
    assert any(e.get("event") == "distill_deferred" for e in events)


# --- booleans are booleans --------------------------------------------------------

def test_a_flag_takes_true_false_and_their_words_and_refuses_the_rest():
    f = lambda v: mcp_base.flag({"x": v}, "x")       # noqa: E731
    assert [f(True), f("true"), f("Yes"), f(1), f("on")] == [True] * 5
    assert [f(False), f("false"), f("no"), f(0), f("")] == [False] * 5
    assert mcp_base.flag({}, "x") is False
    with pytest.raises(mcp_base.ArgError, match="must be true or false"):
        f("ttur")


def test_arguments_are_held_to_their_declared_types():
    tools = [mcp_base.tool("t", "", {"on": mcp_base.boolean(""), "id": mcp_base.string("")})]
    assert mcp_base.check_args(tools, "t", {"on": "true", "id": 12}) == {"on": True, "id": "12"}
    assert mcp_base.check_args(tools, "t", {"on": None, "id": True}) == {"id": "true"}
    with pytest.raises(mcp_base.ArgError):
        mcp_base.check_args(tools, "t", {"on": "ttur"})


def call_server(td, agent, name, arguments):
    """One tools/call through the real stdio server."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": name, "arguments": arguments}}]
    out = subprocess.run([sys.executable, "-m", "agyteam.mcp_tasks", str(td), agent],
                         input="".join(json.dumps(m) + "\n" for m in msgs),
                         capture_output=True, text=True, cwd=ROOT, timeout=30)
    return json.loads(out.stdout.splitlines()[-1])["result"]


def test_create_task_takes_a_json_true_and_refuses_a_typo(team):
    ok = call_server(team, "tpm", "create_task", {"project": "p", "title": "gated",
                                                  "requires_review": True})
    assert not ok["isError"], ok
    rows = [json.loads(l) for l in (team / "tasks.jsonl").read_text().splitlines()]
    assert rows[-1].get("requires_review") is True, "a JSON true was read as false"
    bad = call_server(team, "tpm", "create_task", {"project": "p", "title": "typo",
                                                   "requires_review": "ttur"})
    assert bad["isError"] and "must be true or false" in bad["content"][0]["text"]
    assert len((team / "tasks.jsonl").read_text().splitlines()) == len(rows)


def test_the_schema_declares_requires_review_a_boolean():
    tool = next(t for t in mcp_tasks.TOOLS if t["name"] == "create_task")
    assert tool["inputSchema"]["properties"]["requires_review"]["type"] == "boolean"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
