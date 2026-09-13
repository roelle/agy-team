import json
import os
import shutil
import tempfile
import time

from google.antigravity.types import BuiltinTools, ToolCall, ToolResult

from agyteam.observer_file import FileObserver
from agyteam.runner_sdk import SdkRunner

def test_correlation_map_memory_leak():
    td = tempfile.mkdtemp(prefix="test-adapter-qa3-")
    team_dir = os.path.join(td, "team")
    os.makedirs(team_dir)
    with open(os.path.join(team_dir, "roster.json"), "w") as f:
        json.dump({"agents": [{"name": "coder", "role": "implements"}]}, f)

    saved_team_dir = os.environ.get("AGYTEAM_TEAM_DIR")
    os.environ["AGYTEAM_TEAM_DIR"] = team_dir

    try:
        obs = FileObserver({"team_dir": team_dir})
        runner = SdkRunner(observer=obs)
        agent = runner._submit(runner._ensure("coder"))

        pre_hook = next(h for h in agent._config.hooks if getattr(h, "__name__", None) == "pre_trace_tools")
        post_hook = next(h for h in agent._config.hooks if getattr(h, "__name__", None) == "trace_tools")
        
        # Fire 5 calls with an ID and Step ID
        for i in range(5):
            tc = ToolCall(name="view_file", id=f"id-{i}", step_id=f"step-{i}", args={})
            pre_hook.f(tc)
            tr = ToolResult(name="view_file", id=f"id-{i}", step_id=f"step-{i}", result="ok")
            post_hook.f(tr)

        # Let's inspect the runner's state
        queue_len = len(runner._tool_call_starts_by_name.get(("coder", "view_file"), []))
        # The deque shouldn't grow continuously!
        assert queue_len == 0, f"Memory leak: deque length is {queue_len} instead of 0"

    finally:
        if saved_team_dir is not None:
            os.environ["AGYTEAM_TEAM_DIR"] = saved_team_dir
        else:
            os.environ.pop("AGYTEAM_TEAM_DIR", None)
        shutil.rmtree(td, ignore_errors=True)
