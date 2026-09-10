"""Pluggable A2A transport.

The agent-facing tool surface (send_to_teammate, check_inbox, ...) is fixed;
*how* messages actually move is not. Today the default is a file-backed bus,
because Antigravity exposes no peer-to-peer messaging publicly. If a native
(or internal, or proprietary) A2A mechanism is available to you, implement this
interface against it and point AGYTEAM_BUS_TRANSPORT at your class — no agyteam
source changes, and agents never notice the difference.

    AGYTEAM_BUS_TRANSPORT=example_transport:MyTransport
    AGYTEAM_BUS_CONFIG='{"endpoint": "...", "timeout": 5}'   # optional, JSON

Implement `send`, `fetch`, and `teammates`; everything else has a working
default. See agyteam/transport_template.py for a commented skeleton.
"""
import importlib
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

DEFAULT_TRANSPORT = "agyteam.transport_file:FileTransport"


@dataclass
class Message:
    ts: str
    sender: str
    to: str
    content: str

    def render(self) -> str:
        return f"[{self.ts}] from {self.sender}:\n{self.content}"


class Transport(ABC):
    """One agent's view of the team channel. Constructed per agent process."""

    #: shown in the MCP server name, so you can tell which transport is live
    label = "transport"

    def __init__(self, me: str, config: dict | None = None):
        self.me = me
        self.config = config or {}

    # --- required ---------------------------------------------------------

    @abstractmethod
    def send(self, to: str, content: str) -> str:
        """Deliver a message to a peer (or 'user'). Return a status string.

        Return a string starting with '[error:' for unknown recipients rather
        than raising — the model reads this and can correct itself.
        """

    @abstractmethod
    def fetch(self) -> list[Message]:
        """Return messages addressed to me, and mark them consumed.

        Must not redeliver: an agent that keeps seeing the same message will
        loop. If the underlying system is push-based or has no ack, buffer in
        the transport and drain here.
        """

    @abstractmethod
    def teammates(self) -> list[dict]:
        """Peers as [{"name": str, "role": str}], excluding me and 'user'.

        A native transport with its own agent directory should return that
        directory here rather than reading a roster file.
        """

    # --- optional ---------------------------------------------------------

    def broadcast(self, content: str) -> str:
        names = [t["name"] for t in self.teammates()]
        for n in names:
            self.send(n, content)
        return f"[broadcast to {', '.join(names) or 'nobody'}]"

    supports_roster_admin = False

    def roster_add(self, name: str, role: str) -> str:
        return f"[error: {self.label} transport cannot modify the roster]"

    def roster_remove(self, name: str) -> str:
        return f"[error: {self.label} transport cannot modify the roster]"

    def close(self) -> None:
        """Release resources. Called on server shutdown; default is a no-op."""


def load(me: str, spec: str | None = None, config: dict | None = None) -> Transport:
    """Instantiate the configured transport.

    Failures are loud on purpose: silently falling back to the file bus when you
    meant to use a native one would split the team across two channels, and the
    symptom (messages that vanish) is miserable to debug.
    """
    spec = spec or os.environ.get("AGYTEAM_BUS_TRANSPORT") or DEFAULT_TRANSPORT
    if config is None:
        raw = os.environ.get("AGYTEAM_BUS_CONFIG", "")
        try:
            config = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise SystemExit(f"AGYTEAM_BUS_CONFIG is not valid JSON: {e}")
    if ":" not in spec:
        raise SystemExit(f"AGYTEAM_BUS_TRANSPORT must be 'module:Class', got {spec!r}")
    mod_name, _, cls_name = spec.partition(":")
    try:
        cls = getattr(importlib.import_module(mod_name), cls_name)
    except (ImportError, AttributeError) as e:
        raise SystemExit(f"cannot load transport {spec!r}: {e}")
    if not issubclass(cls, Transport):
        raise SystemExit(f"{spec} is not a agyteam.transport.Transport subclass")
    return cls(me, config)
