"""CLI runner: wake an agent as an `agy` CLI turn, in a session that lasts.

    agy --agent <name> --conversation <id> -p "<message>" --output-format json

This is the path that makes a team work inside Antigravity proper: agents are
agy custom agents (installed by plugin/install.sh), so they get the harness's
tools, policies, and subagents, and the same conversations are visible to Remote
Control and the IDE. The supervisor turns bus traffic into these invocations,
which is what makes teammates reactive instead of waiting to be checked on.

Each agent keeps ONE conversation. The first wake starts it and records the id
in <team_dir>/conversations.json; later wakes resume it with --conversation, so
an agent woken five times has one continuous context instead of five cold
starts. That matters twice over: the agent remembers what it was just doing, and
the resumed turns hit the prompt cache instead of re-paying for the persona and
memory index every time. It also means you can join the very conversation your
agent is working in (`python -m agyteam.session <name>`) rather than talking to
a fresh copy of it.

Config (AGYTEAM_RUNNER_CONFIG), all optional:
    {"binary": "agy",            # or an absolute path
     "timeout": 900,             # seconds per turn
     "extra_args": ["--effort", "high"],
     "persist": true,            # keep one conversation per agent
     "auto_approve": false}      # adds --dangerously-skip-permissions

auto_approve exists because an unattended agent cannot answer a permission
prompt — but it is off by default, since "unattended" and "approves everything"
should be a decision you make explicitly rather than inherit. Prefer setting
"toolPermission": "always-proceed" in ~/.gemini/antigravity-cli/settings.json.
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .runner import Runner

PERMISSION_HINT = (
    'called a tool that needs approval, which headless mode cannot prompt for. '
    'Set "toolPermission": "always-proceed" in '
    '~/.gemini/antigravity-cli/settings.json, or set auto_approve in '
    'AGYTEAM_RUNNER_CONFIG')


class AgyRunner(Runner):
    label = "agy-cli"

    def __init__(self, config=None, observer=None):
        super().__init__(config, observer=observer)
        self.binary = self.config.get("binary", "agy")
        self.timeout = int(self.config.get("timeout", 900))
        self.extra_args = list(self.config.get("extra_args", []))
        self.persist = self.config.get("persist", True)
        if self.config.get("auto_approve"):
            self.extra_args.append("--dangerously-skip-permissions")

    def _roster_spec(self, agent: str) -> dict:
        from . import roster as roster_lib
        try:
            for a in roster_lib.load(self.team_dir / "roster.json")["agents"]:
                if a["name"] == agent:
                    return a
        except Exception:
            pass
        return {}

    def _brief(self, agent: str) -> str:
        """The opening brief, from the same source the SDK runner uses."""
        from . import persona
        from . import roster as roster_lib
        try:
            agents = roster_lib.load(self.team_dir / "roster.json")["agents"]
        except Exception:
            agents = [{"name": agent, "role": ""}]
        shared = None
        try:
            from . import scope
            shared = scope.load().shared_dir()
        except Exception:
            pass
        return persona.brief(agent, agents, shared)

    def available(self) -> str | None:
        """Return the resolved binary path, or None if agy isn't installed."""
        return shutil.which(self.binary) or (
            self.binary if os.path.isfile(self.binary) else None)

    def _record_usage(self, agent: str, payload: dict, conv_id: str | None = None,
                      duration_s: float | None = None) -> None:
        usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        dur = payload.get("duration_seconds") if isinstance(payload, dict) else None
        if dur is None:
            dur = duration_s or 0.0
        cid = payload.get("conversation_id") if isinstance(payload, dict) else None
        cid = cid or conv_id or ""
        try:
            self.observer.record_turn(
                agent=agent,
                conversation=cid,
                duration_s=dur,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cache_read_tokens=usage.get("cache_read_tokens"),
                total_tokens=usage.get("total_tokens"),
                model=payload.get("model") if isinstance(payload, dict) else None,
            )
        except Exception:
            pass  # accounting must never break a turn

    def _record_failure(self, agent: str, conv_id: str, error: str,
                        duration_s: float | None = None) -> None:
        try:
            self.observer.record_failure(agent, conv_id, error, duration_s=duration_s)
        except Exception:
            pass

    # --- the turn ---------------------------------------------------------

    def _run(self, agent: str, message: str, conv_id: str | None):
        # agy's own print-mode wait defaults to 5m and silently returns partial
        # output when it expires, which looks like an agent that did nothing.
        # Keep it just inside our subprocess timeout so one clock governs.
        # Deliberately NOT --agent: agy's custom-agent mechanism is undocumented
        # and strips every builtin tool, so those agents cannot write a file.
        # The default agent has full tools; identity comes from AGYTEAM_AGENT
        # (which the MCP servers read) and the role from the opening brief.
        cmd = [self._resolved, "-p", message,
               "--output-format", "json",
               "--print-timeout", f"{max(self.timeout - 10, 30)}s"]
        # Per-agent model from the roster. The CLI reaches models the SDK cannot
        # (claude-*, gemini-*-pro), and encodes reasoning effort in the name, so
        # "model" and "effort" are combined back into a single CLI alias here.
        spec = self._roster_spec(agent)
        model = spec.get("model") or self.config.get("model")
        effort = spec.get("effort") or self.config.get("effort")
        if model:
            if effort and not model.endswith(f"-{effort}"):
                model = f"{model}-{effort}"
            cmd += ["--model", model]
        if conv_id:
            cmd[1:1] = ["--conversation", conv_id]
        cmd.extend(self.extra_args)
        # AGYTEAM_AGENT is what the plugin's MCP servers use to decide whose
        # memory and mail this process is touching, so it must match --agent.
        env = {**os.environ, "AGYTEAM_AGENT": agent}
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=self.timeout, env=env)

    def wake(self, agent: str, message: str) -> str:
        t0 = time.monotonic()
        resolved = self.available()
        if not resolved:
            err = (f"[error: '{self.binary}' not found on PATH. Install the "
                   f"Antigravity CLI, or set AGYTEAM_RUNNER_CONFIG "
                   f'\'{{"binary": "/path/to/agy"}}\']')
            self._record_failure(agent, "", err, duration_s=time.monotonic() - t0)
            return err
        self._resolved = resolved
        conv_id = self.conversation_id(agent) if self.persist else None
        # The brief goes in exactly once, on the turn that creates the
        # conversation; every later wake resumes a session that already has it.
        if not conv_id:
            message = f"{self._brief(agent)}\n\n---\n\n{message}"
        try:
            r = self._run(agent, message, conv_id)
            # A conversation can vanish (pruned, or a different team dir). One
            # cold retry beats stranding the agent forever on a dead id.
            if conv_id and r.returncode != 0:
                self.reset(agent)
                r = self._run(agent, message, None)
        except subprocess.TimeoutExpired:
            err = f"[error: {agent} timed out after {self.timeout}s]"
            self._record_failure(agent, conv_id or "", err, duration_s=time.monotonic() - t0)
            return err
        except OSError as e:
            err = f"[error: could not run {resolved}: {e}]"
            self._record_failure(agent, conv_id or "", err, duration_s=time.monotonic() - t0)
            return err

        out, err = (r.stdout or "").strip(), (r.stderr or "").strip()
        dur = time.monotonic() - t0
        if r.returncode != 0:
            err_msg = f"[error: {agent} exited {r.returncode}] {err[:500]}"
            self._record_failure(agent, conv_id or "", err_msg, duration_s=dur)
            return err_msg

        try:
            payload = json.loads(out) if out else {}
        except ValueError:
            payload = {}
        if payload:
            cid = payload.get("conversation_id", "")
            self.remember_conversation(agent, cid)
            self._record_usage(agent, payload, conv_id=cid or conv_id, duration_s=dur)
            reply = (payload.get("response") or "").strip()
            status = payload.get("status", "")
            if status and status != "SUCCESS" and not reply:
                err_msg = f"[error: {agent} reported {status}] {err[:300]}"
                self._record_failure(agent, cid or conv_id or "", err_msg, duration_s=dur)
                return err_msg
            if reply:
                return reply

        # A clean exit with nothing to say is how headless agy reports a tool it
        # auto-denied: the reason goes to stderr. Dropping it turns the one
        # actionable message in the whole run into a silent no-op.
        if "cannot prompt for" in err or "auto-denied" in err:
            err_msg = f"[error: {agent} {PERMISSION_HINT}] {err[:300]}"
            self._record_failure(agent, conv_id or "", err_msg, duration_s=dur)
            return err_msg
        return f"[no output] {err[:300]}".strip() if err else "[no output]"
