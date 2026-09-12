import sys
sys.path.insert(0, '/mnt/data/agy-exp')

import asyncio
import tempfile
import pathlib
import time
from agyteam.transport_file import FileTransport
from agyteam.runner_sdk import SdkRunner
import pytest

def test_qa_defect2_mail_not_lost():
    with tempfile.TemporaryDirectory() as td:
        t = FileTransport("coder", config={"team_dir": str(td)})
        t.send("coder", "critical message from user")
        msgs = t.fetch()
        assert len(msgs) == 1
        t.requeue(msgs)
        msgs_again = t.fetch()
        assert len(msgs_again) == 1, "Mail was lost despite requeue!"
        assert msgs_again[0].content == "critical message from user"

def test_qa_defect3_runner_waitfor():
    class MaliciousSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *args):
            await asyncio.sleep(100) # hang
    
    runner = SdkRunner()
    try:
        runner._cms["bad"] = MaliciousSession()
        runner._agents["bad"] = "agent"
        t0 = time.time()
        runner.close()
        duration = time.time() - t0
        assert duration < 5.0, "Runner close took too long or hung!"
    finally:
        runner.close()
