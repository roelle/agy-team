"""Runner that wakes agents as persistent google-antigravity SDK sessions.

Alternative to runner_agy for when you're driving the team from Python rather
than the agy CLI. Sessions are kept alive between wakes, so an agent woken five
times has one continuous context rather than five cold starts — cheaper and it
remembers the conversation, not just its memory files.

Requires google-antigravity (unlike runner_agy, which only shells out), so this
module is deliberately excluded from the plugin's dependency-free module set.

Config (AGYTEAM_RUNNER_CONFIG), all optional:
    {"model": "gemini-3.8-flash", "workers": true, "quiet": true}
"""
import asyncio
import threading

from google.antigravity import Agent, types

from . import config as cfg
from . import roster as roster_lib
from . import scope
from .runner import Runner
from .sdk_agent import build_config

WORKER = types.SubagentConfig(
    name="worker",
    description=(
        "Ephemeral worker for one bounded task. No memory, no teammates, gone "
        "when done — brief it completely. Use send_to_teammate for anything "
        "needing a peer's role, memory, or judgment."))


class SdkRunner(Runner):
    label = "sdk"

    def __init__(self, config=None):
        super().__init__(config)
        self.model = self.config.get("model", cfg.DEFAULT_MODEL)
        self.use_workers = self.config.get("workers", True)
        self.scopes = scope.load()
        self.roster = roster_lib.load(self.scopes.team_dir() / "roster.json")
        self._specs = {a["name"]: a for a in self.roster["agents"]}
        self._agents: dict[str, Agent] = {}
        self._cms: dict[str, Agent] = {}
        # One private event loop on its own thread: the supervisor API is
        # synchronous, and asyncio.run() per wake would tear down the sessions
        # we are specifically trying to keep alive.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _ensure(self, agent: str) -> Agent:
        if agent in self._agents:
            return self._agents[agent]
        spec = self._specs.get(agent, {"name": agent, "role": ""})
        peers = "\n".join(f"- {a['name']}: {a.get('role','')}"
                          for a in self.roster["agents"] if a["name"] != agent)
        section = types.SystemInstructionSection(
            title="team_contract",
            content=(f"You are '{agent}'. Role: {spec.get('role','')}\n\n"
                     f"TEAMMATES (persistent peers with their own memory — reach "
                     f"them with send_to_teammate):\n{peers}\n"
                     "- user: the human. Send final results and questions to 'user'.\n\n"
                     "You are woken when a teammate writes to you, so reply when "
                     "you finish — silence stalls whoever is waiting. Workers you "
                     "spawn are not teammates: no memory, no bus, gone when done. "
                     "Never spawn a worker to do a teammate's job.\n\n"
                     f"Shared files belong under {self.scopes.shared_dir()}"))
        conf = build_config(
            self.scopes.agent_workspace(agent), model=spec.get("model", self.model),
            name=agent, interactive=False, use_mcp=True, with_bus=True,
            extra_sections=[section],
            subagents=[WORKER] if (self.use_workers and spec.get("workers", True))
            else None,
            disabled_tools=spec.get("tools_off"))
        cm = Agent(conf)
        self._agents[agent] = await cm.__aenter__()
        self._cms[agent] = cm
        return self._agents[agent]

    async def _wake(self, agent: str, message: str) -> str:
        a = await self._ensure(agent)
        return await (await a.chat(message)).text()

    def wake(self, agent: str, message: str) -> str:
        try:
            return self._submit(self._wake(agent, message))
        except Exception as e:
            return f"[error: {agent} failed: {type(e).__name__}: {e}]"

    def close(self):
        async def shutdown():
            for cm in self._cms.values():
                try:
                    await cm.__aexit__(None, None, None)
                except Exception:
                    pass
        try:
            self._submit(shutdown())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
