"""Listing limits, task-scoped in-flight notes, policy reach, and the two host hooks.

- Listing tools stay under the inline-output ceiling. The agy CLI spills a
  tool result over ~4,000 bytes to a file; a confined agent is then refused
  the read, so a long inbox never arrived. check_inbox marks read only what
  it returned.
- list_tasks says which tasks a running turn is working on, by task id --
  never by owner, which marked every task an agent owned as in flight.
- tools_off holds through a host's generic MCP-call tool.
- refuse_paths covers shell commands that name the path, not only file tools.
- The generic pre-tool hook writes the audit line the review gate reads.
- The generic stop hook writes the signal line HostRunner waits for.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import activity, mcp_base, mcp_bus, mcp_tasks, policy, tasks  # noqa: E402
from agyteam.transport_file import FileTransport  # noqa: E402


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "gates"},
        {"name": "coder", "role": "implements"},
        {"name": "qa", "role": "reviews"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    for k in ("AGYTEAM_AGENT", "AGYTEAM_AUDIT_LOG", "AGYTEAM_SIGNAL_DIR",
              "AGYTEAM_RUNNER_CONFIG", "AGYTEAM_STOP_IDLE_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(mcp_base, "OUTPUT_BYTES", 3800)
    tasks._quiet.clear()
    return td


def size(text):
    return len(text.encode("utf-8"))


# --- the ceiling ------------------------------------------------------------------

def test_fit_keeps_the_footer_inside_the_limit():
    blocks = [f"item {i} " + "x" * 90 for i in range(100)]
    text, n = mcp_base.fit(blocks, "[{n} more]", limit=1000)
    assert size(text) <= 1000 and 0 < n < 100 and text.endswith(f"[{100 - n} more]")


def test_fit_returns_an_oversized_first_item_whole():
    text, n = mcp_base.fit(["y" * 5000, "z"], "[{n} more]", limit=1000)
    assert n == 1 and "y" * 5000 in text and text.endswith("[1 more]")


def test_a_ceiling_of_zero_means_none():
    text, n = mcp_base.fit(["a" * 3000] * 5, "[{n} more]", limit=0)
    assert n == 5 and "more" not in text


def test_a_long_inbox_arrives_in_parts_and_nothing_is_lost(team):
    sender = FileTransport("manager")
    for i in range(40):
        sender.send("coder", f"message {i:02d} " + "é" * 150)
    me = FileTransport("coder")
    seen, calls = [], 0
    while True:
        out = mcp_bus._check_inbox(me)
        calls += 1
        assert size(out) <= 3800
        seen += [l for l in out.splitlines() if l.startswith("message ")]
        if "more unread" not in out:
            break
    assert calls > 1, "40 long messages fit in one result; the test is vacuous"
    assert [s[:10] for s in seen] == [f"message {i:02d}" for i in range(40)]
    assert mcp_bus._check_inbox(me) == "[inbox empty]"


def review(td, i, task_id=None):
    with (td / "reviews.jsonl").open("a") as f:
        f.write(json.dumps({"ts": f"2026-10-08 09:{i:02d}:00", "kind": "work",
                            "reviewer": "qa", "author": "coder", "verdict": "approved",
                            "what": f"change {i} " + "w" * 120, "findings": "f" * 120,
                            "task_id": task_id}) + "\n")


def test_list_reviews_is_newest_first_capped_and_filterable(team):
    for i in range(50):
        review(team, i, task_id="task_target" if i == 3 else None)
    out = mcp_bus._list_reviews(FileTransport("qa"))
    assert size(out) <= 3800 and "older reviews not shown" in out
    assert out.index("change 49") < out.index("change 48")
    one = mcp_bus._list_reviews(FileTransport("qa"), task_id="task_target")
    assert "change 3 " in one and "change 4 " not in one and "not shown" not in one


def test_list_tasks_is_newest_first_capped_and_filterable(team):
    made = [tasks.create(team, "p", f"task number {i} " + "t" * 150, owner="coder",
                         created_by="coder") for i in range(40)]
    out = mcp_tasks._list_tasks(team, {})
    assert size(out) <= 3800 and "less recently updated tasks not shown" in out
    assert out.index("task number 39") < out.index("task number 38")
    one = mcp_tasks._list_tasks(team, {"task_id": made[5]["id"]})
    assert "task number 5 " in one and "number 6 " not in one


# --- in flight, by task --------------------------------------------------------------

def test_in_flight_is_scoped_to_the_task_that_woke_the_turn(team, monkeypatch):
    a = tasks.create(team, "p", "the one being worked", owner="coder", created_by="coder")
    b = tasks.create(team, "p", "also coder's, idle", owner="coder", created_by="coder")
    (team / "inflight.json").write_text(json.dumps({"coder": {
        "ts": "2026-10-08 09:00:00", "trigger": {"task_ids": [a["id"]]}}}))
    monkeypatch.setattr(mcp_tasks.heartbeat, "running", lambda td: {"pid": 1})
    out = mcp_tasks._list_tasks(team, {})
    line_a = next(l for l in out.splitlines() if a["id"] in l)
    line_b = next(l for l in out.splitlines() if b["id"] in l)
    assert "in flight with coder since 2026-10-08 09:00:00" in line_a
    assert "in flight" not in line_b


def test_a_dead_supervisors_ledger_marks_nothing_in_flight(team, monkeypatch):
    a = tasks.create(team, "p", "x", owner="coder", created_by="coder")
    (team / "inflight.json").write_text(json.dumps({"coder": {
        "ts": "t", "trigger": {"task_ids": [a["id"]]}}}))
    monkeypatch.setattr(mcp_tasks.heartbeat, "running", lambda td: None)
    assert "in flight" not in mcp_tasks._list_tasks(team, {})


# --- policy ---------------------------------------------------------------------

def set_roster(td, **coder):
    doc = json.loads((td / "roster.json").read_text())
    doc["agents"][1].update(coder)
    (td / "roster.json").write_text(json.dumps(doc))


def test_tools_off_holds_through_a_generic_mcp_call(team):
    set_roster(team, tools_off=["read_resource"])
    out = policy.check_tool_policy("coder", "call_mcp_tool",
                                   {"ServerName": "docs", "ToolName": "read_resource"},
                                   team_dir=team)
    assert out and "read_resource is not available to coder" in out
    assert policy.check_tool_policy("coder", "call_mcp_tool",
                                    {"ServerName": "docs", "ToolName": "search"},
                                    team_dir=team) is None


@pytest.mark.parametrize("cmd", [
    "cat ~/answers/key.json", "cat $HOME/answers/key.json",
    "cat ${HOME}/answers", "cat '~/ans'\"wers\"/key.json",
    "grep -r x ~/answers;echo done"])
def test_a_shell_command_naming_a_refused_path_is_refused(team, cmd):
    set_roster(team, refuse_paths=["~/answers"])
    out = policy.check_tool_policy("coder", "run_command", {"CommandLine": cmd},
                                   team_dir=team)
    assert out and "refuse_paths" in out, cmd


@pytest.mark.parametrize("cmd", [
    "cat ~/answers-old/key.json", "cat ~/answersheet", "ls /x/home/answers",
    "echo ~other/answers"])
def test_a_command_that_only_resembles_it_is_not(team, cmd):
    set_roster(team, refuse_paths=["~/answers"])
    assert policy.check_tool_policy("coder", "run_command", {"CommandLine": cmd},
                                    team_dir=team) is None, cmd


# --- the hooks ------------------------------------------------------------------

def hook(module, payload, env):
    return subprocess.run([sys.executable, "-m", f"agyteam.{module}"],
                          input=json.dumps(payload), capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT), **env}, timeout=60)


def test_the_pre_tool_hook_writes_the_audit_line_the_gate_reads(team, tmp_path, monkeypatch):
    set_roster(team, tools_off=["run_command"])
    log = tmp_path / "audit.jsonl"
    env = {"AGYTEAM_TEAM_DIR": str(team), "AGYTEAM_AUDIT_LOG": str(log),
           "AGYTEAM_AGENT": "coder", "AGYTEAM_TEAM": "t1"}
    assert hook("hook_pre_tool_use", {"tool_name": "view_file",
                                      "tool_input": {"path": "/x", "blob": "b" * 5000}},
                env).returncode == 0
    assert hook("hook_pre_tool_use", {"tool_name": "run_command",
                                      "tool_input": {"command": "ls"}}, env).returncode == 2
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert [(r["agent"], r["tool"], r["team"]) for r in rows] == \
        [("coder", "view_file", "t1"), ("coder", "run_command", "t1")]
    assert len(rows[0]["args"]["blob"]) == 2000 and "refused" not in rows[0]
    assert "not available" in rows[1]["refused"]

    monkeypatch.setenv("AGYTEAM_AUDIT_LOG", str(log))
    monkeypatch.setenv("AGYTEAM_TEAM", "t1")
    worked, evidence = activity.author_activity(team, "coder", "2000-01-01 00:00:00")
    assert worked and "1 tool call(s)" in evidence, "a refused call counted as work"


def test_the_stop_hook_writes_the_line_host_runner_counts(team, tmp_path):
    from agyteam.runner_host import HostRunner
    sig = tmp_path / "signals"
    config = {"start": ["x"], "deliver": ["y"], "signal_dir": str(sig)}
    env = {"AGYTEAM_RUNNER_CONFIG": json.dumps(config)}
    r = hook("hook_stop", {"conversationId": "conv-9",
                           "usage": {"inputTokens": 120, "outputTokens": 7}}, env)
    assert r.returncode == 0, r.stderr
    path = HostRunner(config).signal_path("conv-9")
    [line] = [json.loads(l) for l in path.read_text().splitlines()]
    assert line["input_tokens"] == 120 and line["output_tokens"] == 7 and "error" not in line

    hook("hook_stop", {"session_id": "conv-9", "error": {"message": "quota"}}, env)
    assert json.loads(path.read_text().splitlines()[-1])["error"] == "quota"


def test_the_stop_hook_skips_a_pause_that_is_not_a_turn_end(team, tmp_path):
    sig = tmp_path / "signals"
    env = {"AGYTEAM_SIGNAL_DIR": str(sig), "AGYTEAM_STOP_IDLE_KEY": "fullyIdle"}
    hook("hook_stop", {"conversationId": "c1", "fullyIdle": False}, env)
    assert not (sig / "c1.jsonl").exists()
    hook("hook_stop", {"conversationId": "c1", "fullyIdle": False, "error": "boom"}, env)
    hook("hook_stop", {"conversationId": "c1", "fullyIdle": True}, env)
    assert len((sig / "c1.jsonl").read_text().splitlines()) == 2


def test_the_stop_hook_never_fails_the_host(team, tmp_path):
    for payload, env in [({"conversationId": "c"}, {}),               # nowhere to write
                         ({}, {"AGYTEAM_SIGNAL_DIR": str(tmp_path)}),  # no id
                         ({"conversationId": "../escape"}, {"AGYTEAM_SIGNAL_DIR": str(tmp_path)})]:
        r = hook("hook_stop", payload, env)
        assert r.returncode == 0 and r.stderr
    assert not (tmp_path.parent / "escape.jsonl").exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
