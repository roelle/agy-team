"""Scripted runner for testing the supervisor without spending tokens.

Records every wake and optionally replies over the bus, so the reactive cascade
(A messages B → B is woken → B replies → A is woken) can be verified
deterministically. Written against only the public Runner interface.

Config: {"script": {"agent": [["to", "content"], ...]}, "log": "/path.jsonl",
         "fail": ["agent"]}
"""
import json
import os

from agyteam.runner import Runner
from agyteam.transport import load as load_transport


class ScriptedRunner(Runner):
    label = "scripted"

    def __init__(self, config=None, observer=None):
        super().__init__(config, observer=observer)
        self.script = self.config.get("script", {})
        self.fail = set(self.config.get("fail", []))
        self.log_path = self.config.get("log") or os.environ.get("AGYTEAM_RUNNER_LOG")
        self.woken: list[str] = []

    def wake(self, agent: str, message: str) -> str:
        self.woken.append(agent)
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"agent": agent, "message": message}) + "\n")
        if agent in self.fail:
            try:
                self.observer.record_failure(agent, self.conversation_id(agent) or "scripted",
                                             f"{agent} blew up on purpose", duration_s=0.01)
            except Exception:
                pass
            raise RuntimeError(f"{agent} blew up on purpose")
        # Each scripted reply is sent once; a runner that re-sent on every wake
        # would manufacture the very loop the hop budget exists to catch.
        sends = self.script.pop(agent, [])
        if sends:
            bus = load_transport(agent)
            for to, content in sends:
                bus.send(to, content)
            bus.close()
        try:
            self.observer.record_turn(agent, self.conversation_id(agent) or "scripted",
                                      duration_s=0.01)
        except Exception:
            pass
        return f"{agent} handled {len(message)} chars, sent {len(sends)}"
