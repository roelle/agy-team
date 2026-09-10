"""Run different teammates on different runtimes, chosen per agent.

The two runtimes are not ranked — they trade against each other, and which
trade is right depends on the role:

- The SDK reaches only the models your Gemini API key can reach, but it
  enforces `tools_off` for real (an agent without a tool cannot talk itself
  into using it) and supports hooks.
- The agy CLI routes through Antigravity's backend, so it reaches models the
  API key cannot — claude-opus-4-6-thinking, claude-sonnet-4-6,
  gemini-3.1-pro-high — at the cost of tool restrictions being advisory.

That asymmetry is the point. A reviewer benefits far more from a stronger model
than an implementer does, and it runs once per feature rather than many times,
so paying for capability exactly there is cheap. Meanwhile implementers keep
capability-enforced roles, which is what stops them doing each other's jobs.

Per agent in roster.json:

    {"name": "qa", "runner": "agyteam.runner_agy:AgyRunner",
     "model": "claude-opus-4-6-thinking"}

Agents with no "runner" use the default (AGYTEAM_MIXED_DEFAULT, or the SDK).
Sub-runners are built lazily and cached, so a runtime nobody uses is never
started — which matters because SdkRunner spins up an event loop thread.
"""
import os

from . import roster as roster_lib
from .runner import Runner
from .runner import load as load_runner

DEFAULT_SUB = "agyteam.runner_sdk:SdkRunner"


class MixedRunner(Runner):
    label = "mixed"

    def __init__(self, config=None, observer=None):
        super().__init__(config, observer=observer)
        self.default_spec = (self.config.get("default")
                             or os.environ.get("AGYTEAM_MIXED_DEFAULT")
                             or DEFAULT_SUB)
        roster = roster_lib.load(self.team_dir / "roster.json")
        self._specs = {a["name"]: a for a in roster["agents"]}
        self._subs: dict[str, Runner] = {}

    def runner_spec(self, agent: str) -> str:
        return self._specs.get(agent, {}).get("runner") or self.default_spec

    def _sub(self, agent: str) -> Runner:
        spec = self.runner_spec(agent)
        if spec not in self._subs:
            # load() fails loudly on a bad spec, which is what we want: a typo
            # in a roster should not silently downgrade an agent's runtime.
            sub = load_runner(spec)
            sub.observer = self.observer      # one accounting stream, not two
            self._subs[spec] = sub
        return self._subs[spec]

    def wake(self, agent: str, message: str) -> str:
        return self._sub(agent).wake(agent, message)

    def close(self) -> None:
        for sub in self._subs.values():
            try:
                sub.close()
            except Exception:
                pass
        self._subs.clear()
        super().close()
