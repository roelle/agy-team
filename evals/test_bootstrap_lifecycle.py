"""Verification suite for Priority 1 BOOTSTRAP:
Dynamic team roster, workspace management, supervisor lifecycle controls,
and runner session recycling.

Pure standard library lifecycle management, dynamic reload, and explicit
session recycling with visible log banners.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "evals") not in sys.path:
    sys.path.insert(0, str(ROOT / "evals"))

# Isolate from any ambient AGYTEAM_* environment variables
for _k in ("AGYTEAM_TEAM_DIR", "AGYTEAM_SHARED_DIR", "AGYTEAM_AGENT", "AGYTEAM_ROSTER_ADMIN"):
    os.environ.pop(_k, None)

from rpc_util import rpc, text_of, tool_names
from agyteam import lifecycle, roster as roster_lib
from agyteam.runner_sdk import SdkRunner
from agyteam.supervisor import Supervisor
from agyteam.transport import load as load_transport
from fixture_runner import ScriptedRunner


def test_lifecycle_api():
    """Test Python lifecycle API: grant, revoke, add, remove, stop, start, status."""
    with tempfile.TemporaryDirectory(prefix="agy-test-api-") as td_raw:
        td = Path(td_raw)
        roster_path = td / "roster.json"
        ws1 = str(Path(td / "default_ws").resolve())
        ws_coder = str(Path(td / "coder_ws").resolve())
        roster_path.write_text(json.dumps({
            "mission": "test dynamic lifecycle",
            "workspaces": [ws1],
            "agents": [{"name": "coder", "role": "dev", "workspaces": [ws_coder]}],
        }))

        # 1. team_status initial
        st = lifecycle.team_status(team_dir=td)
        assert st["stopped"] is False
        assert st["stop_reason"] is None
        assert len(st["agents"]) == 1
        assert len(st["workspaces"]) == 1
        assert st["workspaces"][0] == ws1

        # 2. grant_workspace top-level
        ws_shared = str(Path(td / "shared_ws").resolve())
        g_res = lifecycle.grant_workspace(ws_shared, team_dir=td)
        assert g_res["status"] == "ok"
        assert g_res["workspace"] == ws_shared
        assert g_res["agent"] is None
        doc = roster_lib.load(roster_path)
        assert ws_shared in doc["workspaces"]

        # Idempotent: grant again does not duplicate
        lifecycle.grant_workspace(ws_shared, team_dir=td)
        doc2 = roster_lib.load(roster_path)
        assert doc2["workspaces"].count(ws_shared) == 1

        # 3. grant_workspace agent-level
        ws_coder_extra = str(Path(td / "coder_extra").resolve())
        g_agent = lifecycle.grant_workspace(ws_coder_extra, agent="coder", team_dir=td)
        assert g_agent["status"] == "ok"
        assert g_agent["agent"] == "coder"
        doc = roster_lib.load(roster_path)
        coder_entry = next(a for a in doc["agents"] if a["name"] == "coder")
        assert ws_coder_extra in coder_entry["workspaces"]

        # Idempotent: grant again to agent does not duplicate
        lifecycle.grant_workspace(ws_coder_extra, agent="coder", team_dir=td)
        doc2 = roster_lib.load(roster_path)
        coder_entry2 = next(a for a in doc2["agents"] if a["name"] == "coder")
        assert coder_entry2["workspaces"].count(ws_coder_extra) == 1

        # Grant to non-existent agent raises ValueError
        try:
            lifecycle.grant_workspace(ws_coder_extra, agent="nonexistent", team_dir=td)
            assert False, "Expected ValueError for missing agent"
        except ValueError as e:
            assert "not found on roster" in str(e)

        # 4. revoke_workspace agent-level
        r_res = lifecycle.revoke_workspace(ws_coder_extra, agent="coder", team_dir=td)
        assert r_res["status"] == "ok"
        doc = roster_lib.load(roster_path)
        coder_entry = next(a for a in doc["agents"] if a["name"] == "coder")
        assert ws_coder_extra not in coder_entry["workspaces"]

        # Revoke non-existent agent raises ValueError
        try:
            lifecycle.revoke_workspace(ws_coder_extra, agent="nonexistent", team_dir=td)
            assert False, "Expected ValueError for missing agent"
        except ValueError as e:
            assert "not found on roster" in str(e)

        # 5. revoke_workspace top-level
        r_top = lifecycle.revoke_workspace(ws_shared, team_dir=td)
        assert r_top["status"] == "ok"
        doc = roster_lib.load(roster_path)
        assert ws_shared not in doc["workspaces"]

        # 6. add_agent
        ws_qa = str(Path(td / "qa_ws").resolve())
        a_res = lifecycle.add_agent("qa", role="verifies", workspaces=[ws_qa], team_dir=td)
        assert a_res["status"] == "ok"
        assert a_res["name"] == "qa"
        doc = roster_lib.load(roster_path)
        qa_entry = next((a for a in doc["agents"] if a["name"] == "qa"), None)
        assert qa_entry is not None
        assert qa_entry["role"] == "verifies"
        assert ws_qa in qa_entry["workspaces"]

        # Duplicate add raises ValueError
        try:
            lifecycle.add_agent("qa", team_dir=td)
            assert False, "Expected ValueError for duplicate agent"
        except ValueError as e:
            assert "already on the roster" in str(e)

        # 7. remove_agent
        rm_res = lifecycle.remove_agent("coder", team_dir=td)
        assert rm_res["status"] == "ok"
        doc = roster_lib.load(roster_path)
        assert not any(a["name"] == "coder" for a in doc["agents"])

        # Removing missing agent raises ValueError
        try:
            lifecycle.remove_agent("ghost", team_dir=td)
            assert False, "Expected ValueError for missing agent"
        except ValueError as e:
            assert "no teammate named" in str(e)

        # 8. stop_team and start_team
        s_res = lifecycle.stop_team(reason="maintenance halt", team_dir=td)
        assert s_res["status"] == "ok"
        assert s_res["reason"] == "maintenance halt"
        assert (td / ".stop").exists()
        assert (td / ".stop").read_text(encoding="utf-8").strip() == "maintenance halt"

        st_stop = lifecycle.team_status(team_dir=td)
        assert st_stop["stopped"] is True
        assert st_stop["stop_reason"] == "maintenance halt"

        un_res = lifecycle.start_team(team_dir=td)
        assert un_res["status"] == "ok"
        assert not (td / ".stop").exists()

        st_start = lifecycle.team_status(team_dir=td)
        assert st_start["stopped"] is False
        assert st_start["stop_reason"] is None


def test_lifecycle_cli():
    """Test CLI subcommands via python -m agyteam.lifecycle."""
    with tempfile.TemporaryDirectory(prefix="agy-test-cli-") as td_raw:
        td = Path(td_raw)
        roster_path = td / "roster.json"
        roster_path.write_text(json.dumps({
            "mission": "test cli",
            "agents": [{"name": "tpm", "role": "lead"}],
        }))
        env = {**os.environ, "PYTHONPATH": str(ROOT)}

        def run_cli(*args):
            return subprocess.run(
                [sys.executable, "-m", "agyteam.lifecycle", "--team-dir", str(td), *args],
                cwd=ROOT, env=env, capture_output=True, text=True, check=True
            )

        # status --json
        res = run_cli("status", "--json")
        data = json.loads(res.stdout)
        assert data["stopped"] is False
        assert any(a["name"] == "tpm" for a in data["agents"])

        # grant-workspace
        ws_cli = str(Path(td / "cli_ws").resolve())
        res = run_cli("grant-workspace", ws_cli)
        assert "[granted workspace" in res.stdout
        assert ws_cli in roster_lib.load(roster_path)["workspaces"]

        # grant-workspace --agent
        ws_agent = str(Path(td / "tpm_ws").resolve())
        res = run_cli("grant-workspace", ws_agent, "--agent", "tpm")
        assert "[granted workspace" in res.stdout
        assert "to tpm" in res.stdout

        # add-agent
        res = run_cli("add-agent", "reviewer", "--role", "reviews changes")
        assert "[added agent reviewer]" in res.stdout
        assert any(a["name"] == "reviewer" for a in roster_lib.load(roster_path)["agents"])

        # stop
        res = run_cli("stop", "--reason", "cli pause requested")
        assert "[team stopped: cli pause requested]" in res.stdout
        assert (td / ".stop").exists()

        # status shows stopped
        res = run_cli("status")
        assert "STOPPED (cli pause requested)" in res.stdout

        # start
        res = run_cli("start")
        assert "[team started/unpaused]" in res.stdout
        assert not (td / ".stop").exists()

        # revoke-workspace
        res = run_cli("revoke-workspace", ws_cli)
        assert "[revoked workspace" in res.stdout
        assert ws_cli not in roster_lib.load(roster_path)["workspaces"]

        # remove-agent
        res = run_cli("remove-agent", "reviewer")
        assert "[removed agent reviewer]" in res.stdout
        assert not any(a["name"] == "reviewer" for a in roster_lib.load(roster_path)["agents"])


def test_mcp_admin_tools():
    """Test MCP bus tools exposure and admin execution gated by AGYTEAM_ROSTER_ADMIN."""
    with tempfile.TemporaryDirectory(prefix="agy-test-mcp-") as td_raw:
        td = Path(td_raw)
        roster_path = td / "roster.json"
        roster_path.write_text(json.dumps({
            "mission": "mcp admin test",
            "agents": [{"name": "tpm", "role": "coordinator"}],
        }))

        # 1. When AGYTEAM_ROSTER_ADMIN is not enabled, admin tools are omitted
        resp_no_admin = rpc(
            "agyteam.mcp_bus", [str(td), "tpm"], [("tools/list", {})],
            env={"AGYTEAM_ROSTER_ADMIN": "0", "AGYTEAM_TEAM_DIR": str(td), "AGYTEAM_AGENT": "tpm"}
        )
        tools_no_admin = tool_names(resp_no_admin[1])
        assert "grant_workspace" not in tools_no_admin
        assert "revoke_workspace" not in tools_no_admin
        assert "stop_team" not in tools_no_admin
        assert "start_team" not in tools_no_admin
        assert "team_status" not in tools_no_admin

        # 2. When AGYTEAM_ROSTER_ADMIN=1, admin tools are listed and callable
        ws_mcp = str(Path(td / "mcp_ws").resolve())
        calls = [
            ("grant_workspace", {"workspace": ws_mcp}),
            ("team_status", {}),
            ("stop_team", {"reason": "halted from mcp"}),
            ("team_status", {}),
            ("start_team", {}),
            ("revoke_workspace", {"workspace": ws_mcp}),
        ]
        resp_admin = rpc(
            "agyteam.mcp_bus", [str(td), "tpm"], calls,
            env={"AGYTEAM_ROSTER_ADMIN": "1", "AGYTEAM_TEAM_DIR": str(td), "AGYTEAM_AGENT": "tpm"}
        )
        tools_admin = tool_names(resp_admin[1])
        assert "grant_workspace" in tools_admin
        assert "revoke_workspace" in tools_admin
        assert "stop_team" in tools_admin
        assert "start_team" in tools_admin
        assert "team_status" in tools_admin

        # Validate grant_workspace response
        grant_out = json.loads(text_of(resp_admin[2]))
        assert grant_out["status"] == "ok"
        assert grant_out["workspace"] == ws_mcp

        # Validate team_status after grant
        status1 = json.loads(text_of(resp_admin[3]))
        assert ws_mcp in status1["workspaces"]
        assert status1["stopped"] is False

        # Validate stop_team response
        stop_out = json.loads(text_of(resp_admin[4]))
        assert stop_out["status"] == "ok"
        assert stop_out["reason"] == "halted from mcp"

        # Validate team_status after stop
        status2 = json.loads(text_of(resp_admin[5]))
        assert status2["stopped"] is True
        assert status2["stop_reason"] == "halted from mcp"

        # Validate start_team and revoke_workspace responses
        start_out = json.loads(text_of(resp_admin[6]))
        assert start_out["status"] == "ok"
        revoke_out = json.loads(text_of(resp_admin[7]))
        assert revoke_out["status"] == "ok"


def test_supervisor_stop_and_resume_mail_preserved():
    """Supervisor honors .stop sentinel without consuming or dropping unread mail,
    and resumes cleanly when .stop is removed."""
    with tempfile.TemporaryDirectory(prefix="agy-test-sup-stop-") as td_raw:
        td = Path(td_raw)
        roster_path = td / "roster.json"
        roster_path.write_text(json.dumps({
            "agents": [
                {"name": "tpm", "role": "coord"},
                {"name": "coder", "role": "dev"},
            ]
        }))
        old_env = os.environ.get("AGYTEAM_TEAM_DIR")
        os.environ["AGYTEAM_TEAM_DIR"] = str(td)
        try:
            # Send message to tpm
            load_transport("user").send("tpm", "critical message before pause")

            # Place .stop sentinel
            lifecycle.stop_team(reason="planned outage", team_dir=td)

            runner = ScriptedRunner({"script": {
                "tpm": [["user", "working on critical message"]],
            }})
            sup = Supervisor(["tpm", "coder"], runner, max_hops=10, quiet=True)

            # Step while stopped: must return 0 turns and record reason
            turns = sup.step()
            assert turns == 0
            assert "team stopped via lifecycle: planned outage" in sup.stopped

            # Crucial invariant: tpm's inbox mail was NOT fetched or lost!
            tpm_t = load_transport("tpm")
            msgs = tpm_t.fetch()
            assert len(msgs) == 1, "Mail must not be lost or drained when stopped"
            assert msgs[0].content == "critical message before pause"
            tpm_t.requeue(msgs)

            # Remove .stop sentinel
            lifecycle.start_team(team_dir=td)
            sup.stopped = ""

            # Next step resumes execution normally
            turns2 = sup.step()
            assert turns2 == 1
            sup.close()
        finally:
            if old_env is not None:
                os.environ["AGYTEAM_TEAM_DIR"] = old_env
            else:
                os.environ.pop("AGYTEAM_TEAM_DIR", None)


def test_supervisor_dynamic_roster_reload():
    """Supervisor dynamically reloads roster.json on step(), adding and removing transports."""
    with tempfile.TemporaryDirectory(prefix="agy-test-sup-reload-") as td_raw:
        td = Path(td_raw)
        roster_path = td / "roster.json"
        roster_path.write_text(json.dumps({
            "agents": [
                {"name": "tpm", "role": "coordinator"},
                {"name": "coder", "role": "developer"},
            ]
        }))
        old_env = os.environ.get("AGYTEAM_TEAM_DIR")
        os.environ["AGYTEAM_TEAM_DIR"] = str(td)
        try:
            sup = Supervisor(["tpm", "coder"], ScriptedRunner({}), quiet=True)
            assert set(sup.agents) == {"tpm", "coder"}
            assert set(sup.transports.keys()) == {"tpm", "coder"}

            # Dynamically add agent 'qa'
            lifecycle.add_agent("qa", role="reviewer", team_dir=td)
            sup.step()
            assert set(sup.agents) == {"tpm", "coder", "qa"}
            assert "qa" in sup.transports

            # Dynamically remove agent 'coder'
            lifecycle.remove_agent("coder", team_dir=td)
            sup.step()
            assert set(sup.agents) == {"tpm", "qa"}
            assert "coder" not in sup.transports

            sup.close()
        finally:
            if old_env is not None:
                os.environ["AGYTEAM_TEAM_DIR"] = old_env
            else:
                os.environ.pop("AGYTEAM_TEAM_DIR", None)


def test_runner_sdk_session_recycling_and_banner():
    """Runner prints exact RECYCLE banner and resets in-memory sessions on sync_roster diffs."""
    runner = SdkRunner()
    try:
        # 1. Direct recycle_agent banner formatting check
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            runner.recycle_agent("coder", reason="workspaces changed")
        output = buf.getvalue().strip()
        expected = (
            "[runner_sdk] RECYCLE: terminating session for 'coder' due to workspaces changed. "
            "Active session terminated; in-memory context lost."
        )
        assert output == expected

        # 2. sync_roster change detection and granular recycling
        runner._agents["coder"] = "mock_coder_session"
        runner._agents["tpm"] = "mock_tpm_session"
        runner._top_workspaces = ["/top/base"]
        runner._agent_workspaces = {"coder": ["/old/coder/ws"], "tpm": []}
        runner._specs = {
            "coder": {"name": "coder", "model": "m1", "workspaces": ["/old/coder/ws"]},
            "tpm": {"name": "tpm", "model": "m1"},
        }

        # Case A: Coder's workspace changes -> Coder is recycled, TPM is preserved
        buf = io.StringIO()
        roster_update = {
            "workspaces": ["/top/base"],
            "agents": [
                {"name": "coder", "model": "m1", "workspaces": ["/new/coder/ws"]},
                {"name": "tpm", "model": "m1"},
            ]
        }
        with contextlib.redirect_stdout(buf):
            runner.sync_roster(roster_update)
        captured = buf.getvalue()
        assert "terminating session for 'coder' due to workspaces changed" in captured
        assert "terminating session for 'tpm'" not in captured
        assert "coder" not in runner._agents
        assert "tpm" in runner._agents

        # Case B: Top-level workspace changes -> All active agents recycled
        runner._agents["coder"] = "mock_coder_session"
        buf = io.StringIO()
        roster_top_update = {
            "workspaces": ["/top/base", "/top/new_shared"],
            "agents": [
                {"name": "coder", "model": "m1", "workspaces": ["/new/coder/ws"]},
                {"name": "tpm", "model": "m1"},
            ]
        }
        with contextlib.redirect_stdout(buf):
            runner.sync_roster(roster_top_update)
        captured_top = buf.getvalue()
        assert "terminating session for 'coder' due to workspaces changed" in captured_top
        assert "terminating session for 'tpm' due to workspaces changed" in captured_top
        assert len(runner._agents) == 0

        # Case C: Agent removed from roster -> Agent recycled with removal reason
        runner._agents["coder"] = "mock_coder_session"
        buf = io.StringIO()
        roster_remove = {
            "workspaces": ["/top/base", "/top/new_shared"],
            "agents": [{"name": "tpm", "model": "m1"}],
        }
        with contextlib.redirect_stdout(buf):
            runner.sync_roster(roster_remove)
        captured_rm = buf.getvalue()
        assert "terminating session for 'coder' due to agent removed from roster" in captured_rm
        assert "coder" not in runner._agents
    finally:
        runner.close()


def test_end_to_end_workspace_grant_and_effective_workspaces():
    """Dynamically granted workspaces propagate to runner._effective_workspaces."""
    with tempfile.TemporaryDirectory(prefix="agy-test-ws-e2e-") as td_raw:
        td = Path(td_raw)
        top_ws = str(Path(td / "global_ws").resolve())
        coder_ws = str(Path(td / "coder_initial_ws").resolve())
        roster_path = td / "roster.json"
        roster_path.write_text(json.dumps({
            "workspaces": [top_ws],
            "agents": [{"name": "coder", "workspaces": [coder_ws]}],
        }))
        old_env = os.environ.get("AGYTEAM_TEAM_DIR")
        os.environ["AGYTEAM_TEAM_DIR"] = str(td)
        runner = None
        try:
            runner = SdkRunner()
            initial_effective = runner._effective_workspaces("coder")
            assert top_ws in initial_effective
            assert coder_ws in initial_effective

            # 1. Dynamically grant workspace to coder
            extra_coder_ws = str(Path(td / "coder_extra_dynamic").resolve())
            lifecycle.grant_workspace(extra_coder_ws, agent="coder", team_dir=td)
            runner.sync_roster()
            updated_coder = runner._effective_workspaces("coder")
            assert extra_coder_ws in updated_coder

            # 2. Dynamically grant global workspace to team
            extra_global_ws = str(Path(td / "global_extra_dynamic").resolve())
            lifecycle.grant_workspace(extra_global_ws, team_dir=td)
            runner.sync_roster()
            updated_all = runner._effective_workspaces("coder")
            assert extra_global_ws in updated_all

            # 3. Revoke coder workspace
            lifecycle.revoke_workspace(extra_coder_ws, agent="coder", team_dir=td)
            runner.sync_roster()
            revoked_coder = runner._effective_workspaces("coder")
            assert extra_coder_ws not in revoked_coder
            assert extra_global_ws in revoked_coder
        finally:
            if runner is not None:
                runner.close()
            if old_env is not None:
                os.environ["AGYTEAM_TEAM_DIR"] = old_env
            else:
                os.environ.pop("AGYTEAM_TEAM_DIR", None)


if __name__ == "__main__":
    tests = [
        ("lifecycle_api", test_lifecycle_api),
        ("lifecycle_cli", test_lifecycle_cli),
        ("mcp_admin_tools", test_mcp_admin_tools),
        ("supervisor_stop_and_resume", test_supervisor_stop_and_resume_mail_preserved),
        ("supervisor_dynamic_roster_reload", test_supervisor_dynamic_roster_reload),
        ("runner_sdk_session_recycling", test_runner_sdk_session_recycling_and_banner),
        ("end_to_end_workspace_grant", test_end_to_end_workspace_grant_and_effective_workspaces),
    ]
    passed = 0
    total = len(tests)
    print("\n== bootstrap lifecycle verification suite ==")
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n== bootstrap lifecycle: {passed}/{total} ==")
    sys.exit(0 if passed == total else 1)
