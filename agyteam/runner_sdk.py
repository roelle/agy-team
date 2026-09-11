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
import time

from google.antigravity import Agent, types

from . import config as cfg
from . import persona
from . import roster as roster_lib
from . import scope
from .runner import Runner, with_retry
from .sdk_agent import build_config

WORKER = types.SubagentConfig(
    name="worker",
    description=(
        "Ephemeral worker for one bounded task. No memory, no teammates, gone "
        "when done — brief it completely. Use send_to_teammate for anything "
        "needing a peer's role, memory, or judgment."))


class SdkRunner(Runner):
    label = "sdk"

    def __init__(self, config=None, observer=None):
        super().__init__(config, observer=observer)
        self.model = self.config.get("model", cfg.DEFAULT_MODEL)
        self.use_workers = self.config.get("workers", True)
        self.effort = self.config.get("effort", "")
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
        # Same brief the CLI runner sends as an opening message, so an agent has
        # one character regardless of which runtime started it.
        section = types.SystemInstructionSection(
            title="team_contract",
            content=persona.brief(agent, self.roster["agents"],
                                  self.scopes.shared_dir()))
        conf = build_config(
            self.scopes.agent_workspace(agent), model=spec.get("model", self.model),
            name=agent, interactive=False, use_mcp=True, with_bus=True,
            effort=spec.get("effort", self.effort),
            extra_sections=[section],
            subagents=[WORKER] if (self.use_workers and spec.get("workers", True))
            else None,
            disabled_tools=spec.get("tools_off"))
        cm = Agent(conf)
        live = await cm.__aenter__()
        self._agents[agent] = live
        self._cms[agent] = cm
        return live

    async def _wake(self, agent: str, message: str) -> str:
        t0 = time.monotonic()
        a = await self._ensure(agent)
        chat_resp = await a.chat(message)
        reply = await chat_resp.text()
        dur = time.monotonic() - t0
        # build_config points save_dir at the CLI's conversation store, so this
        # session is a real, joinable conversation — but only once somebody
        # records which id belongs to which agent. The id is not assigned until
        # the first exchange, so this has to happen after chat, not at start-up.
        conv_id = a.conversation_id or ""
        self.remember_conversation(agent, conv_id)

        # Extract usage safely from ChatResponse or active conversation
        usage = getattr(chat_resp, "usage_metadata", None)
        if usage is None:
            try:
                usage = a.conversation.last_turn_usage
            except Exception:
                usage = None

        in_tok = getattr(usage, "prompt_token_count", None) if usage else None
        out_tok = getattr(usage, "candidates_token_count", None) if usage else None
        cache_tok = getattr(usage, "cached_content_token_count", None) if usage else None
        tot_tok = getattr(usage, "total_token_count", None) if usage else None
        thoughts = getattr(usage, "thoughts_token_count", None) if usage else None
        if out_tok is not None and thoughts:
            out_tok += thoughts

        spec = self._specs.get(agent, {})
        model = spec.get("model", self.model)

        try:
            self.observer.record_turn(
                agent=agent,
                conversation=conv_id,
                duration_s=dur,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_tokens=cache_tok,
                total_tokens=tot_tok,
                model=model,
            )
        except Exception:
            pass

        return reply

    def wake(self, agent: str, message: str) -> str:
        t0 = time.monotonic()

        def announce(attempt, delay, err):
            print(f"    {agent}: provider unavailable, retrying in {delay:.0f}s "
                  f"(attempt {attempt})", flush=True)

        try:
            return with_retry(lambda: self._submit(self._wake(agent, message)),
                              on_wait=announce)
        except Exception as e:
            dur = time.monotonic() - t0
            err = f"[error: {agent} failed: {type(e).__name__}: {e}]"
            try:
                self.observer.record_failure(
                    agent, self.conversation_id(agent) or "", err, duration_s=dur
                )
            except Exception:
                pass
            return err

    def reset(self, agent: str | None = None) -> None:
        """Forget stored conversations and close/evict live SDK sessions."""
        super().reset(agent)
        targets = [agent] if agent is not None else list(set(self._agents.keys()) | set(self._cms.keys()))

        async def _close_sessions():
            for a in targets:
                cm = self._cms.pop(a, None)
                self._agents.pop(a, None)
                if cm is not None:
                    try:
                        await cm.__aexit__(None, None, None)
                    except Exception:
                        pass

        try:
            if self._loop and self._loop.is_running():
                self._submit(_close_sessions())
            else:
                for a in targets:
                    self._cms.pop(a, None)
                    self._agents.pop(a, None)
        except Exception:
            pass

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
            # Base close() releases the observer. Skipping it is harmless for
            # the file observer but leaks a networked one, which is exactly the
            # swap the seam exists to allow.
            super().close()
