"""Default runner: wake an agent as an `agy` CLI turn, in a session that lasts.

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

    def __init__(self, config=None):
        super().__init__(config)
        self.binary = self.config.get("binary", "agy")
        self.timeout = int(self.config.get("timeout", 900))
        self.extra_args = list(self.config.get("extra_args", []))
        self.persist = self.config.get("persist", True)
        if self.config.get("auto_approve"):
            self.extra_args.append("--dangerously-skip-permissions")
        self._team_dir = self._resolve_team_dir()
        self._conv_path = self._team_dir / "conversations.json"
        self._usage_path = self._team_dir / "usage.jsonl"

    @staticmethod
    def _resolve_team_dir() -> Path:
        env = os.environ.get("AGYTEAM_TEAM_DIR")
        if env:
            return Path(env)
        from . import scope
        return scope.load().team_dir()

    def available(self) -> str | None:
        """Return the resolved binary path, or None if agy isn't installed."""
        return shutil.which(self.binary) or (
            self.binary if os.path.isfile(self.binary) else None)

    # --- conversation continuity -----------------------------------------

    def _conversations(self) -> dict:
        try:
            return json.loads(self._conv_path.read_text())
        except (OSError, ValueError):
            return {}

    def conversation_id(self, agent: str) -> str | None:
        return self._conversations().get(agent) if self.persist else None

    def _remember(self, agent: str, conv_id: str) -> None:
        if not (self.persist and conv_id):
            return
        convs = self._conversations()
        if convs.get(agent) == conv_id:
            return
        convs[agent] = conv_id
        self._conv_path.parent.mkdir(parents=True, exist_ok=True)
        self._conv_path.write_text(json.dumps(convs, indent=2, sort_keys=True))

    def reset(self, agent: str | None = None) -> None:
        """Forget stored conversations so the next wake starts fresh."""
        if agent is None:
            convs = {}
        else:
            convs = self._conversations()
            convs.pop(agent, None)
        self._conv_path.parent.mkdir(parents=True, exist_ok=True)
        self._conv_path.write_text(json.dumps(convs, indent=2, sort_keys=True))

    def _record_usage(self, agent: str, payload: dict) -> None:
        usage = payload.get("usage") or {}
        if not usage:
            return
        entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "agent": agent,
                 "conversation": payload.get("conversation_id", ""),
                 "duration_s": round(payload.get("duration_seconds", 0), 2),
                 **{k: usage.get(k, 0) for k in
                    ("input_tokens", "output_tokens", "cache_read_tokens",
                     "total_tokens")}}
        try:
            with self._usage_path.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass        # accounting must never break a turn

    # --- the turn ---------------------------------------------------------

    def _run(self, agent: str, message: str, conv_id: str | None):
        # agy's own print-mode wait defaults to 5m and silently returns partial
        # output when it expires, which looks like an agent that did nothing.
        # Keep it just inside our subprocess timeout so one clock governs.
        cmd = [self._resolved, "--agent", agent, "-p", message,
               "--output-format", "json",
               "--print-timeout", f"{max(self.timeout - 10, 30)}s"]
        if conv_id:
            cmd[1:1] = ["--conversation", conv_id]
        cmd.extend(self.extra_args)
        # AGYTEAM_AGENT is what the plugin's MCP servers use to decide whose
        # memory and mail this process is touching, so it must match --agent.
        env = {**os.environ, "AGYTEAM_AGENT": agent}
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=self.timeout, env=env)

    def wake(self, agent: str, message: str) -> str:
        resolved = self.available()
        if not resolved:
            return (f"[error: '{self.binary}' not found on PATH. Install the "
                    f"Antigravity CLI, or set AGYTEAM_RUNNER_CONFIG "
                    f'\'{{"binary": "/path/to/agy"}}\']')
        self._resolved = resolved
        conv_id = self.conversation_id(agent)
        try:
            r = self._run(agent, message, conv_id)
            # A conversation can vanish (pruned, or a different team dir). One
            # cold retry beats stranding the agent forever on a dead id.
            if conv_id and r.returncode != 0:
                self.reset(agent)
                r = self._run(agent, message, None)
        except subprocess.TimeoutExpired:
            return f"[error: {agent} timed out after {self.timeout}s]"
        except OSError as e:
            return f"[error: could not run {resolved}: {e}]"

        out, err = (r.stdout or "").strip(), (r.stderr or "").strip()
        if r.returncode != 0:
            return f"[error: {agent} exited {r.returncode}] {err[:500]}"

        try:
            payload = json.loads(out) if out else {}
        except ValueError:
            payload = {}
        if payload:
            self._remember(agent, payload.get("conversation_id", ""))
            self._record_usage(agent, payload)
            reply = (payload.get("response") or "").strip()
            status = payload.get("status", "")
            if status and status != "SUCCESS" and not reply:
                return f"[error: {agent} reported {status}] {err[:300]}"
            if reply:
                return reply

        # A clean exit with nothing to say is how headless agy reports a tool it
        # auto-denied: the reason goes to stderr. Dropping it turns the one
        # actionable message in the whole run into a silent no-op.
        if "cannot prompt for" in err or "auto-denied" in err:
            return f"[error: {agent} {PERMISSION_HINT}] {err[:300]}"
        return f"[no output] {err[:300]}".strip() if err else "[no output]"
