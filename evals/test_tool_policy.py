"""One policy, three askers: the SDK hook, the generic hook, and the servers.

Role policy used to hold only inside the SDK runner. On a host runtime the
manager had every native tool, assigned tasks to implementers directly, and
could message anyone. The same roster fields now produce the same refusal
whether the call comes through a host's pre-tool hook, the SDK session's
hook, or straight at the bus and task servers.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import hook_pre_tool_use, mcp_bus, mcp_tasks, policy, tasks  # noqa: E402
from agyteam.transport_file import FileTransport  # noqa: E402


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    (td / "roster.json").write_text(json.dumps({
        "policy": {"detach_commands": ["run_sim", "make bench"]},
        "agents": [
            {"name": "manager", "role": "gates", "is_principal": True,
             "tools_off": ["run_command"], "allowed_send_to": ["tpm", "user"]},
            {"name": "tpm", "role": "coordinates", "allowed_send_to": ["manager", "coder"]},
            {"name": "coder", "role": "implements", "assigns_tasks": False,
             "workspaces": [str(repo)]},
            {"name": "ops", "role": "maintains the platform", "confined": False,
             "workspaces": [str(repo)]},
            {"name": "qa", "role": "reviews"}]}))
    (td / "conversations.json").write_text(json.dumps({"coder": "conv-coder"}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.setenv("AGYTEAM_DURABLE_DIR", str(tmp_path / "durable"))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    return td, repo


def check(agent, tool, args, td):
    return policy.check_tool_policy(agent, tool, args, team_dir=td)


# --- the rules ------------------------------------------------------------------

def test_tools_off_refuses_by_name(team):
    td, _ = team
    assert "not available to manager" in check("manager", "run_command", {"command": "ls"}, td)
    assert check("coder", "run_command", {"command": "ls"}, td) is None


def test_allowed_send_to_limits_recipients(team):
    td, _ = team
    assert check("tpm", "send_to_teammate", {"to": "coder"}, td) is None
    out = check("tpm", "send_to_teammate", {"to": "user"}, td)
    assert out and "may only message manager, coder" in out
    assert "reaches everyone" in check("tpm", "broadcast", {}, td)
    assert check("qa", "send_to_teammate", {"to": "user"}, td) is None   # unrestricted


def test_assigns_tasks_false_keeps_work_with_the_agent(team):
    td, _ = team
    assert check("coder", "create_task", {"owner": "coder"}, td) is None
    assert "does not assign tasks" in check("coder", "create_task", {"owner": "qa"}, td)
    assert "does not hand tasks" in check("coder", "update_task", {"owner": "qa"}, td)
    assert check("qa", "create_task", {"owner": "coder"}, td) is None


def test_file_tools_outside_the_workspace_are_refused(team):
    td, repo = team
    assert check("coder", "read_file", {"path": str(repo / "a.py")}, td) is None
    out = check("coder", "read_file", {"path": "/etc/passwd"}, td)
    assert out and "outside coder's workspaces" in out and "hangs the turn" in out
    assert "outside" in check("coder", "write_file", {"path": str(td / "reviews.jsonl")}, td), \
        "the team directory is the record"
    assert check("coder", "edit_file", {"path": "b.py", "Cwd": str(repo)}, td) is None


def test_an_agent_with_no_workspaces_is_not_confined_by_this(team):
    td, _ = team
    assert check("qa", "read_file", {"path": "/etc/passwd"}, td) is None


def test_confined_false_opts_a_role_out(team):
    td, _ = team
    assert check("ops", "read_file", {"path": "/etc/passwd"}, td) is None


def test_long_commands_must_run_detached(team):
    td, _ = team
    out = check("coder", "run_command", {"command": "run_sim --trials 400"}, td)
    assert out and "detached" in out and "check_after" in out
    assert check("coder", "run_command", {"command": "nohup run_sim > log 2>&1 &"}, td) is None
    assert check("coder", "run_command", {"command": "ls"}, td) is None


def test_our_own_tools_are_never_treated_as_file_tools(team):
    td, _ = team
    assert check("coder", "send_to_teammate",
                 {"to": "qa", "content": "see /etc/passwd"}, td) is None


# --- the three askers -------------------------------------------------------------

def test_the_bus_server_applies_it(team):
    td, _ = team
    out = mcp_bus._send(FileTransport("tpm"), {"to": "user", "content": "hi"})
    assert out.startswith("[refused:") and "may only message" in out


def test_the_task_server_applies_it(team):
    td, _ = team
    out = mcp_tasks._create_task("coder", td, {"project": "p", "title": "x", "owner": "qa"})
    assert out.startswith("[refused:") and "does not assign tasks" in out
    t = tasks.create(td, "p", "x", owner="coder", created_by="coder")
    out = mcp_tasks._update_task("coder", td, {"task_id": t["id"], "owner": "qa"})
    assert out.startswith("[refused:")


def run_hook(payload, env):
    return subprocess.run([sys.executable, "-m", "agyteam.hook_pre_tool_use"],
                          input=json.dumps(payload), capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT), **env}, timeout=60)


def test_the_generic_hook_blocks_with_exit_2_and_a_reason(team):
    td, _ = team
    env = {"AGYTEAM_TEAM_DIR": str(td), "AGYTEAM_AGENT": "manager"}
    r = run_hook({"tool_name": "run_command", "tool_input": {"command": "ls"}}, env)
    assert r.returncode == 2
    verdict = json.loads(r.stdout)
    assert verdict["decision"] == "deny" and verdict["block"] is True
    assert "not available to manager" in r.stderr
    r = run_hook({"tool_name": "read_file", "tool_input": {"path": "/x"}}, env)
    assert r.returncode == 0 and json.loads(r.stdout) == {"decision": "allow"}


def test_the_generic_hook_reads_a_nested_camel_case_payload(team):
    """One host sends {"conversationId", "toolCall": {"name", "args"}} and
    expects {"decision": "deny"|"allow", "reason"}; that shape needed a
    wrapper before."""
    td, _ = team
    r = run_hook({"conversationId": "conv-coder",
                  "toolCall": {"name": "read_file", "args": {"path": "/etc/passwd"}}},
                 {"AGYTEAM_TEAM_DIR": str(td)})
    assert r.returncode == 2
    assert json.loads(r.stdout)["reason"].startswith("[refused:")


def test_allowed_mcp_servers_on_a_generic_mcp_call(team):
    td, _ = team
    doc = json.loads((td / "roster.json").read_text())
    doc["agents"][2]["allowed_mcp_servers"] = ["agyteam_bus", "agyteam_tasks"]
    (td / "roster.json").write_text(json.dumps(doc))
    assert check("coder", "call_mcp_tool", {"ServerName": "agyteam_bus", "ToolName": "check_inbox"}, td) is None
    out = check("coder", "call_mcp_tool", {"ServerName": "browser", "ToolName": "navigate"}, td)
    assert out and "may call only these MCP servers" in out
    assert check("qa", "call_mcp_tool", {"ServerName": "browser"}, td) is None


def test_refuse_paths_is_the_deny_form_of_the_path_rule(team, tmp_path):
    td, repo = team
    checkout = tmp_path / "upstream-checkout"
    checkout.mkdir()
    doc = json.loads((td / "roster.json").read_text())
    doc["policy"]["refuse_paths"] = [str(checkout)]
    doc["agents"][4]["refuse_paths"] = ["/etc"]          # qa, who roams freely
    (td / "roster.json").write_text(json.dumps(doc))
    out = check("qa", "read_file", {"path": str(checkout / "x.py")}, td)
    assert out and "refuse_paths" in out
    assert "refuse_paths" in check("qa", "read_file", {"path": "/etc/passwd"}, td)
    assert check("qa", "read_file", {"path": "/tmp/other"}, td) is None
    # it applies before the allow-list, for confined agents too
    assert "refuse_paths" in check("coder", "read_file", {"path": str(checkout / "x")}, td)


def test_detach_patterns_are_regular_expressions_and_forbidden_commands_say_why(team):
    td, _ = team
    doc = json.loads((td / "roster.json").read_text())
    doc["policy"]["detach_commands"] = [r"make (bench|all)"]
    doc["policy"]["forbidden_commands"] = [
        {"pattern": r"launch_sim\b", "message": "the operator starts sims"},
        "rm -rf /"]
    (td / "roster.json").write_text(json.dumps(doc))
    assert "detached" in check("coder", "run_command", {"command": "make all"}, td)
    assert check("coder", "run_command", {"command": "make test"}, td) is None
    out = check("coder", "run_command", {"command": "nohup launch_sim --trials 4 &"}, td)
    assert out and "the operator starts sims" in out and "detached" not in out
    assert "not for you to run" in check("coder", "run_command", {"command": "rm -rf /"}, td)


def test_the_generic_hook_finds_the_agent_by_conversation_id(team):
    td, _ = team
    env = {"AGYTEAM_TEAM_DIR": str(td)}
    r = run_hook({"name": "read_file", "args": {"path": "/etc/passwd"},
                  "conversation_id": "conv-coder"}, env)
    assert r.returncode == 2 and "outside coder's workspaces" in r.stderr
    r = run_hook({"name": "read_file", "args": {"path": "/etc/passwd"},
                  "conversation_id": "conv-unknown"}, env)
    assert r.returncode == 0 and "no agent identified" in r.stderr


def test_the_same_refusal_from_every_asker(team):
    """The point of one function: the SDK wrapper, the hook, and the server
    must disagree with an agent in exactly the same words."""
    td, _ = team
    direct = check("tpm", "send_to_teammate", {"to": "user"}, td)
    server = mcp_bus._send(FileTransport("tpm"), {"to": "user", "content": "hi"})
    code, hook = hook_pre_tool_use.decide(
        {"tool_name": "send_to_teammate", "tool_input": {"to": "user"}, "agent": "tpm"}, td)
    assert direct == server == hook and code == 2


def test_the_sdk_session_asks_it_too(team, monkeypatch):
    pytest.importorskip("google.antigravity")
    monkeypatch.setenv("GEMINI_API_KEY", "offline-tests-never-call-a-model")
    from agyteam.sdk_agent import build_config
    td, repo = team
    conf = build_config(repo / "ws", name="coder", use_mcp=True, with_bus=True)
    hooks = [h for h in conf.hooks if getattr(h, "__name__", "") == "team_policy"]
    assert hooks, [getattr(h, "__name__", h) for h in conf.hooks]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
