"""A missing API key must fail a wake at once, not hang it.

config.api_key() exits when GEMINI_API_KEY is unset and is reached from a
coroutine on SdkRunner's private loop. SystemExit escaped the loop instead of
landing on the future, the loop thread died, and every wake after that waited
its full timeout (ten minutes per agent for a turn) to report a bare
TimeoutError. evals/conftest.py sets a placeholder key precisely so the suite
does not hang, which is also why this was never seen under pytest; here the
key is removed on purpose.
"""
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("google.antigravity")

from agyteam.runner_sdk import SdkRunner  # noqa: E402


@pytest.fixture
def runner(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "coder", "role": "implements"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = SdkRunner()
    yield r
    r.close()


def test_a_missing_key_fails_fast_and_says_why(runner):
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        runner._submit(runner._ensure("coder"), timeout=30)
    assert time.monotonic() - t0 < 10


def test_the_loop_survives_to_serve_the_next_call(runner):
    with pytest.raises(RuntimeError):
        runner._submit(runner._ensure("coder"), timeout=30)
    assert runner._thread.is_alive()

    async def ping():
        return "alive"

    assert runner._submit(ping(), timeout=5) == "alive"


def test_a_wake_reports_the_reason_as_an_error_string(runner):
    reply = runner.wake("coder", "hello")
    assert reply.startswith("[error:") and "GEMINI_API_KEY" in reply


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
