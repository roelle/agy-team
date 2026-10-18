"""A turn that times out on the SDK runner must stop, not just stop being waited for.

Measured: a manager's turn hit AGYTEAM_TURN_TIMEOUT, the supervisor recorded
the failure and queued its mail to be delivered again -- and 23 seconds later
the same turn, still running in the harness, answered the user. Cancelling
the asyncio task stopped the wait; only ChatResponse.cancel() stops the turn.
"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("google.antigravity")


class SlowResponse:
    def __init__(self, log):
        self.log = log

    async def text(self):
        await asyncio.sleep(30)
        return "too late"

    async def cancel(self):
        self.log.append("turn cancelled")


class FakeSession:
    conversation_id = "conv-x"

    def __init__(self, log):
        self.log = log

    async def chat(self, message):
        return SlowResponse(self.log)


def test_a_timed_out_turn_is_cancelled_where_it_runs(tmp_path, monkeypatch):
    from agyteam import config as cfg
    from agyteam import runner_sdk
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(tmp_path / "team"))
    monkeypatch.setattr(cfg, "TURN_TIMEOUT_S", 0.5)
    monkeypatch.setattr(runner_sdk.cfg, "TURN_TIMEOUT_S", 0.5)
    log = []
    r = runner_sdk.SdkRunner()

    async def fake_ensure(agent):
        return FakeSession(log)
    monkeypatch.setattr(r, "_ensure", fake_ensure)
    try:
        reply = r.wake("coder", "work")
        import time
        for _ in range(50):                    # the cancel runs on the runner's loop
            if log:
                break
            time.sleep(0.05)
    finally:
        r.close()
    assert reply.startswith("[error:") and "Timeout" in reply
    assert log == ["turn cancelled"], "the turn was abandoned but left running"
