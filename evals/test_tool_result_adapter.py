"""Tests for SdkRunner ToolCall and ToolResult adapter.

Verifies the live-wire contract captured in fixtures/tool_result_fixture.json:
- ToolCall contains guaranteed parsed dictionary args, id, step_id.
- ToolResult does NOT contain args or duration_s; duration and args must be
  correlated from pre_trace via id/step_id or by-name FIFO queue.
- No speculative getattr fallback chains or blanket try/except blocks.
- Deserialization is executed directly against the committed wire fixture.
"""
import ast
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from google.antigravity.types import BuiltinTools, ToolCall, ToolResult

from agyteam.observer_file import FileObserver
from agyteam.runner_sdk import SdkRunner

FIXTURE_PATH = ROOT / "fixtures" / "tool_result_fixture.json"



def _setup_runner():
    td = Path(tempfile.mkdtemp(prefix="test-adapter-helper-")) / "team"
    td.mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [{"name": "coder", "role": "implements"}]}))
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)
    obs = FileObserver({"team_dir": str(td)})
    runner = SdkRunner(observer=obs)
    agent = runner._submit(runner._ensure("coder"))
    pre_hook = next(h for h in agent._config.hooks if getattr(h, "__name__", None) == "pre_trace_tools")
    post_hook = next(h for h in agent._config.hooks if getattr(h, "__name__", None) == "trace_tools")
    return td, runner, obs, pre_hook, post_hook


def test_committed_fixture_schema_and_direct_attributes():
    """Verify the committed fixture exists and validates ToolCall/ToolResult schemas."""
    assert FIXTURE_PATH.is_file(), f"Committed fixture missing at {FIXTURE_PATH}"

    with open(FIXTURE_PATH) as f:
        data = json.load(f)

    assert "samples" in data
    assert len(data["samples"]) == 4

    for sample in data["samples"]:
        tc = ToolCall(**sample["tool_call"])
        tr = ToolResult(**sample["tool_result"])

        # ToolCall contract assertions
        assert isinstance(tc.args, dict), "ToolCall.args must be guaranteed dict[str, Any]"
        assert hasattr(tc, "name")
        assert hasattr(tc, "id")
        assert hasattr(tc, "step_id")

        # ToolResult contract assertions: args, duration_s, duration DO NOT exist
        assert "args" not in ToolResult.model_fields
        assert "duration_s" not in ToolResult.model_fields
        assert "duration" not in ToolResult.model_fields
        with pytest.raises(AttributeError):
            _ = tr.args
        with pytest.raises(AttributeError):
            _ = tr.duration_s
        with pytest.raises(AttributeError):
            _ = tr.duration


def test_sdk_runner_adapter_all_committed_samples():
    """Verify SdkRunner hooks correctly correlate and record all committed fixture samples."""
    with open(FIXTURE_PATH) as f:
        data = json.load(f)

    td = Path(tempfile.mkdtemp(prefix="test-adapter-fixture-")) / "team"
    td.mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [{"name": "coder", "role": "implements"}]}))

    saved_team_dir = os.environ.get("AGYTEAM_TEAM_DIR")
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)

    try:
        obs = FileObserver({"team_dir": str(td)})
        runner = SdkRunner(observer=obs)
        agent = runner._submit(runner._ensure("coder"))

        pre_hook = next(h for h in agent._config.hooks if getattr(h, "__name__", None) == "pre_trace_tools")
        post_hook = next(h for h in agent._config.hooks if getattr(h, "__name__", None) == "trace_tools")

        for sample in data["samples"]:
            tc = ToolCall(**sample["tool_call"])
            tr = ToolResult(**sample["tool_result"])

            hook_res = pre_hook.f(tc)
            assert getattr(hook_res, "allow", None) is True
            time.sleep(0.005)
            post_hook.f(tr)

        runner.close()
        evs = obs.events("tool_call")
        assert len(evs) == 4, f"Expected 4 recorded tool_call events, got {len(evs)}"

        for idx, (sample, ev) in enumerate(zip(data["samples"], evs)):
            tc_expected = sample["tool_call"]
            tr_expected = sample["tool_result"]

            assert ev["event"] == "tool_call"
            assert ev["agent"] == "coder"
            assert ev["tool"] == tc_expected["name"]
            assert ev["args"] == tc_expected["args"], f"Sample {idx}: args mismatch"
            assert ev["result"] == tr_expected["result"], f"Sample {idx}: result mismatch"
            assert ev["error"] == tr_expected["error"], f"Sample {idx}: error mismatch"
            assert isinstance(ev["duration_s"], float)
            assert ev["duration_s"] >= 0.0, f"Sample {idx}: duration_s must be non-negative"

    finally:
        if saved_team_dir is not None:
            os.environ["AGYTEAM_TEAM_DIR"] = saved_team_dir
        else:
            os.environ.pop("AGYTEAM_TEAM_DIR", None)
        shutil.rmtree(td.parent, ignore_errors=True)


def test_sdk_runner_adapter_builtin_enum_and_exception():
    """Verify BuiltinTools enum unwrapping and Exception extraction in SdkRunner."""
    td = Path(tempfile.mkdtemp(prefix="test-adapter-enum-")) / "team"
    td.mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [{"name": "coder", "role": "implements"}]}))

    saved_team_dir = os.environ.get("AGYTEAM_TEAM_DIR")
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)

    try:
        obs = FileObserver({"team_dir": str(td)})
        runner = SdkRunner(observer=obs)
        agent = runner._submit(runner._ensure("coder"))

        pre_hook = next(h for h in agent._config.hooks if getattr(h, "__name__", None) == "pre_trace_tools")
        post_hook = next(h for h in agent._config.hooks if getattr(h, "__name__", None) == "trace_tools")

        # Use BuiltinTools enum directly
        tc = ToolCall(name=BuiltinTools.RUN_COMMAND, args={"command": "false"}, id="enum-call-1", step_id="s1")
        pre_hook.f(tc)
        time.sleep(0.005)
        tr = ToolResult(
            name=BuiltinTools.RUN_COMMAND,
            id="enum-call-1",
            step_id="s1",
            exception=RuntimeError("Subprocess execution failed"),
        )
        post_hook.f(tr)

        runner.close()
        evs = obs.events("tool_call")
        assert len(evs) == 1
        assert evs[0]["tool"] == "run_command"
        assert evs[0]["args"] == {"command": "false"}
        assert evs[0]["result"] is None
        assert evs[0]["error"] == "Subprocess execution failed"
        assert evs[0]["duration_s"] >= 0.0

    finally:
        if saved_team_dir is not None:
            os.environ["AGYTEAM_TEAM_DIR"] = saved_team_dir
        else:
            os.environ.pop("AGYTEAM_TEAM_DIR", None)
        shutil.rmtree(td.parent, ignore_errors=True)


def test_sdk_runner_adapter_fifo_name_fallback():
    """Verify FIFO queue correlation when harness id and step_id are absent."""
    td = Path(tempfile.mkdtemp(prefix="test-adapter-fifo-")) / "team"
    td.mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [{"name": "coder", "role": "implements"}]}))

    saved_team_dir = os.environ.get("AGYTEAM_TEAM_DIR")
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)

    try:
        obs = FileObserver({"team_dir": str(td)})
        runner = SdkRunner(observer=obs)
        agent = runner._submit(runner._ensure("coder"))

        pre_hook = next(h for h in agent._config.hooks if getattr(h, "__name__", None) == "pre_trace_tools")
        post_hook = next(h for h in agent._config.hooks if getattr(h, "__name__", None) == "trace_tools")

        # Two calls without id/step_id
        tc1 = ToolCall(name="view_file", args={"path": "file1.txt"})
        tc2 = ToolCall(name="view_file", args={"path": "file2.txt"})

        pre_hook.f(tc1)
        time.sleep(0.005)
        pre_hook.f(tc2)
        time.sleep(0.005)

        tr1 = ToolResult(name="view_file", result="content 1")
        tr2 = ToolResult(name="view_file", result="content 2")

        post_hook.f(tr1)
        post_hook.f(tr2)

        runner.close()
        evs = obs.events("tool_call")
        assert len(evs) == 2
        assert evs[0]["args"] == {"path": "file1.txt"}
        assert evs[0]["result"] == "content 1"
        assert evs[1]["args"] == {"path": "file2.txt"}
        assert evs[1]["result"] == "content 2"

    finally:
        if saved_team_dir is not None:
            os.environ["AGYTEAM_TEAM_DIR"] = saved_team_dir
        else:
            os.environ.pop("AGYTEAM_TEAM_DIR", None)
        shutil.rmtree(td.parent, ignore_errors=True)


def test_banned_defensive_guessing_and_speculative_getattrs():
    """Verify agyteam/runner_sdk.py contains no speculative getattr fallback chains or blanket try/excepts."""
    src_path = Path(__file__).resolve().parent.parent / "agyteam" / "runner_sdk.py"
    src_content = src_path.read_text()

    assert 'getattr(res, "args"' not in src_content
    assert 'getattr(res, "duration"' not in src_content
    assert 'getattr(res, "duration_s"' not in src_content
    assert 'getattr(call, "args"' not in src_content
    assert 'json.dumps(res.result' not in src_content

    # Parse AST to ensure _pre_trace and _trace_tool_call have no blanket except Exception: pass
    tree = ast.parse(src_content)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("_pre_trace", "_trace_tool_call"):
            for subnode in ast.walk(node):
                if isinstance(subnode, ast.ExceptHandler):
                    pytest.fail(f"Found forbidden except handler in {node.name}: line {subnode.lineno}")


def test_correlation_maps_cleared_on_trace():
    """Verify that both _tool_call_starts and _tool_call_starts_by_name are cleared after tracing."""
    td, runner, obs, pre_hook, post_hook = _setup_runner()
    saved_team_dir = os.environ.get("AGYTEAM_TEAM_DIR")
    try:
        # Case 1: Calls with both ID and Step ID
        for i in range(10):
            tc = ToolCall(name="view_file", id=f"call-{i}", step_id=f"step-{i}", args={"path": f"file_{i}.txt"})
            pre_hook.f(tc)
            tr = ToolResult(name="view_file", id=f"call-{i}", step_id=f"step-{i}", result=f"content_{i}")
            post_hook.f(tr)

        assert len(runner._tool_call_starts) == 0, f"_tool_call_starts leaked: {runner._tool_call_starts}"
        assert len(runner._tool_call_starts_by_name.get(("coder", "view_file"), [])) == 0

        # Case 2: Calls without ID (FIFO fallback)
        for i in range(10):
            tc = ToolCall(name="list_dir", args={"path": f"dir_{i}"})
            pre_hook.f(tc)
            tr = ToolResult(name="list_dir", result=f"files_{i}")
            post_hook.f(tr)

        assert len(runner._tool_call_starts) == 0
        assert len(runner._tool_call_starts_by_name.get(("coder", "list_dir"), [])) == 0

        # Case 3: Mixed calls
        tc_id = ToolCall(name="run_command", id="cmd-1", args={"cmd": "ls"})
        tc_noid = ToolCall(name="run_command", args={"cmd": "pwd"})
        pre_hook.f(tc_id)
        pre_hook.f(tc_noid)

        tr_id = ToolResult(name="run_command", id="cmd-1", result="output 1")
        tr_noid = ToolResult(name="run_command", result="output 2")
        post_hook.f(tr_id)
        post_hook.f(tr_noid)

        assert len(runner._tool_call_starts) == 0
        assert len(runner._tool_call_starts_by_name.get(("coder", "run_command"), [])) == 0

        runner.close()
    finally:
        if saved_team_dir is not None:
            os.environ["AGYTEAM_TEAM_DIR"] = saved_team_dir
        else:
            os.environ.pop("AGYTEAM_TEAM_DIR", None)
        shutil.rmtree(td.parent, ignore_errors=True)
