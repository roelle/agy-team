"""Default runner: wake an agent as a headless `agy` CLI turn.

    agy --agent <name> -p "<message>" --output-format text

This is the path that makes a team work inside Antigravity proper: agents are
agy custom agents (installed by plugin/install.sh), so they get the harness's
tools, policies, and subagents, and the same sessions are visible to Remote
Control. The supervisor turns bus traffic into these invocations, which is what
makes teammates reactive instead of waiting to be checked on.

Config (AGYTEAM_RUNNER_CONFIG), all optional:
    {"binary": "agy",            # or an absolute path
     "timeout": 900,             # seconds per turn
     "extra_args": ["--effort", "high"],
     "auto_approve": false}      # adds --dangerously-skip-permissions

auto_approve exists because an unattended agent cannot answer a permission
prompt — but it is off by default, since "unattended" and "approves everything"
should be a decision you make explicitly rather than inherit.
"""
import os
import shutil
import subprocess

from .runner import Runner


class AgyRunner(Runner):
    label = "agy-cli"

    def __init__(self, config=None):
        super().__init__(config)
        self.binary = self.config.get("binary", "agy")
        self.timeout = int(self.config.get("timeout", 900))
        self.extra_args = list(self.config.get("extra_args", []))
        if self.config.get("auto_approve"):
            self.extra_args.append("--dangerously-skip-permissions")

    def available(self) -> str | None:
        """Return the resolved binary path, or None if agy isn't installed."""
        return shutil.which(self.binary) or (
            self.binary if os.path.isfile(self.binary) else None)

    def wake(self, agent: str, message: str) -> str:
        resolved = self.available()
        if not resolved:
            return (f"[error: '{self.binary}' not found on PATH. Install the "
                    f"Antigravity CLI, or set AGYTEAM_RUNNER_CONFIG "
                    f'\'{{"binary": "/path/to/agy"}}\']')
        cmd = [resolved, "--agent", agent, "-p", message,
               "--output-format", "text", *self.extra_args]
        # AGYTEAM_AGENT is what the plugin's MCP servers use to decide whose
        # memory and mail this process is touching, so it must match --agent.
        env = {**os.environ, "AGYTEAM_AGENT": agent}
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=self.timeout, env=env)
        except subprocess.TimeoutExpired:
            return f"[error: {agent} timed out after {self.timeout}s]"
        except OSError as e:
            return f"[error: could not run {resolved}: {e}]"
        out = (r.stdout or "").strip()
        if r.returncode != 0:
            return f"[error: {agent} exited {r.returncode}] {(r.stderr or '')[:500]}"
        return out or "[no output]"
