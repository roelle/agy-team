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

## Message kinds

Every message carries a `kind`. Most kinds wake the recipient; a few do not:
an `ack`, an `fyi` or a `status` note is written to the inbox and read on the
recipient's next wake, whatever causes it. One review cost six model turns
before this existed -- request, forward, verdict, cc, user, cc -- and the
polite half of those woke someone to read nothing. A transport that stores
only (sender, recipient, content) still works: the base class folds the kind
into the text, so it is visible rather than lost, and such a message wakes.
"""
import importlib
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

DEFAULT_TRANSPORT = "agyteam.transport_file:FileTransport"

#: Kinds that cause a wake, and kinds that wait for one.
WAKING_KINDS = ("work", "deliverable", "question", "blocker", "review", "reminder")
QUIET_KINDS = ("ack", "fyi", "status")
KINDS = WAKING_KINDS + QUIET_KINDS


def wakes(kind: str | None) -> bool:
    return (kind or "work") not in QUIET_KINDS


@dataclass
class Message:
    ts: str
    sender: str
    to: str
    content: str
    id: str | int | None = None
    kind: str = "work"
    task_id: str | None = None

    def render(self) -> str:
        tag = "" if self.kind == "work" else f" ({self.kind})"
        task = f" [task {self.task_id}]" if self.task_id else ""
        return f"[{self.ts}] from {self.sender}{tag}{task}:\n{self.content}"

    def as_dict(self) -> dict:
        d = {"ts": self.ts, "from": self.sender, "to": self.to,
             "content": self.content, "kind": self.kind}
        if self.id is not None:
            d["id"] = self.id
        if self.task_id:
            d["task_id"] = self.task_id
        return d


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

        A transport that stores kinds accepts `kind=` and `task_id=` keyword
        arguments as well; see send_kind.
        """

    @abstractmethod
    def fetch(self) -> list[Message]:
        """Return messages addressed to me.

        In at-least-once delivery, messages remain in the queue until explicitly
        acknowledged via acknowledge().
        """

    def acknowledge(self, msgs: list[Message] | None = None) -> None:
        """Acknowledge receipt and successful processing of messages.

        Removes acknowledged messages from the durable queue. If msgs is None,
        acknowledges all currently unacknowledged messages.
        Default is a no-op; subclasses should override.
        """

    @abstractmethod
    def teammates(self) -> list[dict]:
        """Peers as [{"name": str, "role": str}], excluding me and 'user'.

        A native transport with its own agent directory should return that
        directory here rather than reading a roster file.
        """

    # --- optional ---------------------------------------------------------

    def send_kind(self, to: str, content: str, kind: str = "work",
                  task_id: str | None = None) -> str:
        """send() with a kind, on any transport.

        A transport whose send() takes the keywords stores them. One that
        does not gets the kind and task written into the text instead -- the
        recipient still sees it, at the cost of such a message waking them.
        """
        if kind == "work" and not task_id:
            return self.send(to, content)
        try:
            return self.send(to, content, kind=kind, task_id=task_id)
        except TypeError:
            tag = f"[{kind}]" if kind != "work" else ""
            task = f"[task {task_id}]" if task_id else ""
            return self.send(to, f"{tag}{task} {content}".strip())

    def peek(self) -> list[Message] | None:
        """Messages waiting for me, WITHOUT consuming them. None = unsupported.

        Used by status displays and supervisor scheduling. Implement it if you
        can: a status command built on fetch() would destroy the very work it
        was asked to report on.
        """
        return None

    def requeue(self, msgs_or_role, msgs=None) -> None:
        """Return unconsumed messages to the inbox (e.g. after a failed turn).

        Accepts either requeue(msgs) or requeue(role, msgs).
        Default is a no-op; subclasses should override.
        """

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
