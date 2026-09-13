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
import collections
import json
import sys
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
        self.roster = roster_lib.load(self.team_dir / "roster.json")
        self._specs = {a["name"]: a for a in self.roster.get("agents", [])}
        self._top_workspaces: list[str] = list(self.roster.get("workspaces", []))
        self._agent_workspaces: dict[str, list[str]] = {
            a["name"]: list(a.get("workspaces", [])) for a in self.roster.get("agents", [])
        }
        self._agents: dict[str, Agent] = {}
        self._cms: dict[str, Agent] = {}
        self._tool_call_starts: dict[tuple[str, str], tuple[float, any]] = {}
        self._tool_call_starts_by_name: dict[tuple[str, str], collections.deque] = collections.defaultdict(collections.deque)
        # One private event loop on its own thread: the supervisor API is
        # synchronous, and asyncio.run() per wake would tear down the sessions
        # we are specifically trying to keep alive.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _effective_workspaces(self, agent: str) -> list[str]:
        ws_list: list[str] = []
        seen: set[str] = set()
        for w in self._top_workspaces:
            if w not in seen:
                seen.add(w)
                ws_list.append(w)
        agent_ws = self._agent_workspaces.get(agent, [])
        if not agent_ws and agent in self._specs:
            agent_ws = self._specs[agent].get("workspaces", [])
        for w in agent_ws:
            if w not in seen:
                seen.add(w)
                ws_list.append(w)
        return ws_list

    def _submit(self, coro, timeout: float | None = None):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout=timeout)
        except BaseException:
            # BaseException, not Exception: a wait with no timeout is exactly
            # where Ctrl-C arrives, and KeyboardInterrupt/SystemExit are not
            # Exceptions. Catching only Exception left the coroutine running
            # on the background loop after the interrupt, so the process would
            # not die and the user's only escape was SIGKILL.
            fut.cancel()
            raise

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

        def _pre_trace(call):
            tool_name = call.name.value if hasattr(call.name, "value") else str(call.name)
            args = call.args  # Guaranteed dict[str, Any]
            cid = call.id
            sid = call.step_id
            entry = (time.monotonic(), args, cid, sid)
            if cid:
                self._tool_call_starts[(agent, str(cid))] = entry
            if sid:
                self._tool_call_starts[(agent, str(sid))] = entry
            self._tool_call_starts_by_name[(agent, tool_name)].append(entry)

        def _trace_tool_call(res):
            tool_name = res.name.value if hasattr(res.name, "value") else str(res.name)
            cid = res.id
            sid = res.step_id
            captured_entry = None
            if cid and (agent, str(cid)) in self._tool_call_starts:
                captured_entry = self._tool_call_starts.pop((agent, str(cid)))
            elif sid and (agent, str(sid)) in self._tool_call_starts:
                captured_entry = self._tool_call_starts.pop((agent, str(sid)))
            elif self._tool_call_starts_by_name.get((agent, tool_name)):
                captured_entry = self._tool_call_starts_by_name[(agent, tool_name)].popleft()

            if captured_entry is not None:
                captured_t0, args, e_cid, e_sid = captured_entry
                if e_cid and (agent, str(e_cid)) in self._tool_call_starts:
                    self._tool_call_starts.pop((agent, str(e_cid)), None)
                if e_sid and (agent, str(e_sid)) in self._tool_call_starts:
                    self._tool_call_starts.pop((agent, str(e_sid)), None)
                q = self._tool_call_starts_by_name.get((agent, tool_name))
                if q:
                    if captured_entry in q:
                        q.remove(captured_entry)
                    if not q:
                        self._tool_call_starts_by_name.pop((agent, tool_name), None)
            else:
                captured_t0, args = None, None

            result_str = str(res.result) if res.result is not None else None
            error_val = str(res.error) if res.error is not None else (str(res.exception) if res.exception is not None else None)
            duration_s = max(0.0, time.monotonic() - captured_t0) if captured_t0 is not None else None

            conv_id = self.conversation_id(agent) or ""
            self.observer.record_tool_call(
                agent=agent,
                conversation=conv_id,
                tool=tool_name,
                args=args,
                result=result_str,
                error=error_val,
                duration_s=duration_s,
            )

        conf = build_config(
            self.scopes.agent_workspace(agent), model=spec.get("model", self.model),
            name=agent, interactive=False, use_mcp=True, with_bus=True,
            effort=spec.get("effort", self.effort),
            extra_sections=[section],
            subagents=[WORKER] if (self.use_workers and spec.get("workers", True))
            else None,
            disabled_tools=spec.get("tools_off"),
            trace=_trace_tool_call,
            pre_trace=_pre_trace,
            workspaces=self._effective_workspaces(agent))
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
            return with_retry(
                lambda: self._submit(self._wake(agent, message),
                                     timeout=cfg.TURN_TIMEOUT_S),
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

        if agent is None:
            self._tool_call_starts.clear()
            self._tool_call_starts_by_name.clear()
        else:
            self._tool_call_starts = {k: v for k, v in self._tool_call_starts.items() if k[0] != agent}
            self._tool_call_starts_by_name = collections.defaultdict(
                collections.deque,
                {k: v for k, v in self._tool_call_starts_by_name.items() if k[0] != agent}
            )

        async def _close_sessions():
            for a in targets:
                cm = self._cms.pop(a, None)
                self._agents.pop(a, None)
                if cm is not None:
                    try:
                        await asyncio.wait_for(cm.__aexit__(None, None, None), timeout=2.0)
                    except Exception:
                        pass

        try:
            if self._loop and self._loop.is_running():
                self._submit(_close_sessions(), timeout=5.0)
            else:
                for a in targets:
                    self._cms.pop(a, None)
                    self._agents.pop(a, None)
        except Exception:
            pass

    def recycle_agent(self, agent: str, reason: str = "") -> None:
        """Explicitly terminate/recycle an agent session and log the required banner."""
        reason_str = reason or "workspace or configuration change"
        print(
            f"[runner_sdk] RECYCLE: terminating session for '{agent}' due to {reason_str}. "
            f"Active session terminated; in-memory context lost.",
            flush=True,
        )
        self.reset(agent)

    def sync_roster(self, roster_doc: dict | None = None) -> None:
        """Sync running agent configurations with an updated roster document."""
        if roster_doc is None:
            try:
                roster_doc = roster_lib.load(self.team_dir / "roster.json")
            except Exception:
                return
        try:
            roster_doc = roster_lib.normalize(roster_doc)
        except Exception:
            pass

        new_top = list(roster_doc.get("workspaces", []))
        new_specs = {a["name"]: a for a in roster_doc.get("agents", [])}
        new_agent_ws = {a["name"]: list(a.get("workspaces", [])) for a in roster_doc.get("agents", [])}

        top_changed = set(self._top_workspaces) != set(new_top)

        active_agents = set(self._agents.keys()) | set(self._cms.keys())
        for agent in list(active_agents):
            if agent not in new_specs:
                self.recycle_agent(agent, reason="agent removed from roster")
            elif top_changed:
                self.recycle_agent(agent, reason="workspaces changed")
            else:
                old_ws = set(self._agent_workspaces.get(agent, []))
                new_ws = set(new_agent_ws.get(agent, []))
                old_spec = self._specs.get(agent, {})
                new_spec = new_specs.get(agent, {})
                reasons = []
                if old_ws != new_ws:
                    reasons.append("workspaces changed")
                if (old_spec.get("model") or "") != (new_spec.get("model") or ""):
                    reasons.append("model changed")
                if old_spec.get("tools_off") != new_spec.get("tools_off"):
                    reasons.append("tools_off changed")
                if (old_spec.get("role") or "") != (new_spec.get("role") or ""):
                    reasons.append("role changed")
                if reasons:
                    self.recycle_agent(agent, reason=", ".join(reasons))

        self.roster = roster_doc
        self._specs = new_specs
        self._top_workspaces = new_top
        self._agent_workspaces = new_agent_ws

    def close(self):
        self._tool_call_starts.clear()
        self._tool_call_starts_by_name.clear()
        async def shutdown():
            for cm in list(self._cms.values()):
                try:
                    await asyncio.wait_for(cm.__aexit__(None, None, None), timeout=2.0)
                except Exception:
                    pass
        try:
            if self._loop and self._loop.is_running():
                self._submit(shutdown(), timeout=5.0)
        except Exception:
            pass
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            # Base close() releases the observer. Skipping it is harmless for
            # the file observer but leaks a networked one, which is exactly the
            # swap the seam exists to allow.
            super().close()
