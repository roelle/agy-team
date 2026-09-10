"""Pluggable agent runner — *how* an agent is woken to do work.

Third seam, same pattern as agyteam/transport.py (how messages move) and
agyteam/memory.py (where knowledge lives). These are orthogonal: you can run
agents through the agy CLI while messages travel over a custom bus and memory
lives in a shared database, changing any one without touching the others.

    AGYTEAM_RUNNER=agyteam.runner_agy:AgyRunner        # default
    AGYTEAM_RUNNER_CONFIG='{"binary": "agy"}'          # optional, JSON

Why this exists: without it, delivery is passive — mail sits in an inbox until
somebody thinks to check. A runner lets the supervisor *wake* an agent when work
arrives, so a message from a teammate causes work the same way a message from
the user does. That is the difference between a team and a shared mailbox.

Implement `wake`. See runner_template docs in the class below.
"""
import importlib
import json
import os
from abc import ABC, abstractmethod

DEFAULT_RUNNER = "agyteam.runner_agy:AgyRunner"


class Runner(ABC):
    """Starts or resumes one agent turn with a message it must act on."""

    #: shown in supervisor output so you can tell which runner is live
    label = "runner"

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    # --- conversation continuity, shared by every runner -------------------
    #
    # An agent keeps one conversation, and which one is recorded in
    # <team_dir>/conversations.json so a human can walk into it
    # (`python -m agyteam.session <agent>`). This lives on the base class
    # because both runners need it and two copies would drift: the SDK runner
    # once wrote its sessions into the CLI's conversation store without
    # recording their ids, so they existed but nobody could find them.

    @property
    def team_dir(self):
        from pathlib import Path
        env = os.environ.get("AGYTEAM_TEAM_DIR")
        if env:
            return Path(env)
        from . import scope
        return scope.load().team_dir()

    @property
    def conversations_path(self):
        return self.team_dir / "conversations.json"

    def conversations(self) -> dict:
        try:
            return json.loads(self.conversations_path.read_text())
        except (OSError, ValueError):
            return {}

    def conversation_id(self, agent: str) -> str | None:
        return self.conversations().get(agent)

    def remember_conversation(self, agent: str, conv_id: str) -> None:
        if not conv_id:
            return
        convs = self.conversations()
        if convs.get(agent) == conv_id:
            return
        convs[agent] = conv_id
        self.conversations_path.parent.mkdir(parents=True, exist_ok=True)
        self.conversations_path.write_text(
            json.dumps(convs, indent=2, sort_keys=True))

    def reset(self, agent: str | None = None) -> None:
        """Forget stored conversations so the next wake starts fresh."""
        if agent is None:
            convs = {}
        else:
            convs = self.conversations()
            convs.pop(agent, None)
        self.conversations_path.parent.mkdir(parents=True, exist_ok=True)
        self.conversations_path.write_text(
            json.dumps(convs, indent=2, sort_keys=True))

    @abstractmethod
    def wake(self, agent: str, message: str) -> str:
        """Run `agent` against `message` and return whatever it said.

        Blocks until the turn finishes. The reply text is for logging only —
        anything the agent wants a teammate or the user to see, it sends over
        the bus itself, which is what lets one wake cascade into the next.

        Errors should be returned as text starting with "[error:", not raised;
        a supervisor must survive one agent failing.
        """

    def close(self) -> None:
        """Release resources (sessions, subprocesses). Default is a no-op."""


def load(spec: str | None = None, config: dict | None = None) -> Runner:
    """Instantiate the configured runner. Fails loudly on misconfiguration."""
    spec = spec or os.environ.get("AGYTEAM_RUNNER") or DEFAULT_RUNNER
    if config is None:
        raw = os.environ.get("AGYTEAM_RUNNER_CONFIG", "")
        try:
            config = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise SystemExit(f"AGYTEAM_RUNNER_CONFIG is not valid JSON: {e}")
    if ":" not in spec:
        raise SystemExit(f"AGYTEAM_RUNNER must be 'module:Class', got {spec!r}")
    mod_name, _, cls_name = spec.partition(":")
    try:
        cls = getattr(importlib.import_module(mod_name), cls_name)
    except (ImportError, AttributeError) as e:
        raise SystemExit(f"cannot load runner {spec!r}: {e}")
    if not issubclass(cls, Runner):
        raise SystemExit(f"{spec} is not an agyteam.runner.Runner subclass")
    return cls(config)
