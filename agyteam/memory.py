"""Pluggable durable memory.

Mirror of agyteam/transport.py, for the same reason: the agent-facing tools are
fixed, the storage behind them is not. The default keeps memory in markdown
files, which is right for a single machine and readable by humans. Point
AGYTEAM_MEMORY_STORE at your own class to put it somewhere else — a shared
database, a team knowledge service, an internal store — without touching
agyteam source or changing what agents see.

    AGYTEAM_MEMORY_STORE=example_memory:MyStore
    AGYTEAM_MEMORY_CONFIG='{"dsn": "..."}'          # optional, JSON

Implement `save`, `read`, `delete`, and `index`. See memory_template.py.

Division of labour: the store does storage, the MCP server does presentation.
Stores return plain data (or None for "not found") and never format user-facing
strings, so every backend produces identical wording — including the honest
"no memory named X, here is what exists" reply, which is load-bearing for
grounding and must not vary by backend.
"""
import importlib
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

DEFAULT_STORE = "agyteam.memory_file:FileMemory"


class MemoryError_(Exception):
    """Storage failure. The message is meant to be shown to a human."""


@dataclass
class MemoryEntry:
    name: str
    description: str


def normalize_name(name: str) -> str:
    """Canonical memory name, applied *before* the store sees it.

    Normalising centrally means every backend agrees on identity: saving
    "Deploy Host" and later reading "deploy-host" must hit the same record
    whether the backend is files or a database.
    """
    slug = name.strip().replace(" ", "-").lower()
    slug = "".join(c for c in slug if c.isalnum() or c in "-_")
    if not slug:
        raise MemoryError_(f"{name!r} is not a usable memory name")
    return slug


class MemoryStore(ABC):
    """One agent's durable memory. Constructed per agent process."""

    #: shown in the MCP server name, so you can tell which store is live
    label = "store"

    def __init__(self, agent: str, config: dict | None = None):
        self.agent = agent
        self.config = config or {}

    @abstractmethod
    def save(self, name: str, description: str, content: str) -> bool:
        """Create or overwrite a memory. Return True if new, False if updated.

        Names arrive already normalised. Overwriting is intentional — agents are
        told to update rather than accumulate near-duplicates.
        """

    @abstractmethod
    def read(self, name: str) -> str | None:
        """Return the content, or None if there is no such memory.

        Return None rather than raising or inventing: the server turns None into
        an explicit "I don't have that" reply listing what does exist, which is
        what keeps an agent from filling the gap with a guess.
        """

    @abstractmethod
    def delete(self, name: str) -> bool:
        """Remove a memory. Return False if it wasn't there."""

    @abstractmethod
    def index(self) -> list[MemoryEntry]:
        """Every memory as (name, description). Ordering is up to you."""

    def close(self) -> None:
        """Release resources. Called on server shutdown; default is a no-op."""


def load(agent: str, spec: str | None = None,
         config: dict | None = None) -> MemoryStore:
    """Instantiate the configured store.

    Failures are loud on purpose: silently falling back to file storage when you
    meant to use a shared backend would strand an agent's learnings somewhere
    nobody looks, and the symptom (an agent that forgets) is the exact failure
    this project exists to prevent.
    """
    spec = spec or os.environ.get("AGYTEAM_MEMORY_STORE") or DEFAULT_STORE
    if config is None:
        raw = os.environ.get("AGYTEAM_MEMORY_CONFIG", "")
        try:
            config = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise SystemExit(f"AGYTEAM_MEMORY_CONFIG is not valid JSON: {e}")
    if ":" not in spec:
        raise SystemExit(f"AGYTEAM_MEMORY_STORE must be 'module:Class', got {spec!r}")
    mod_name, _, cls_name = spec.partition(":")
    try:
        cls = getattr(importlib.import_module(mod_name), cls_name)
    except (ImportError, AttributeError) as e:
        raise SystemExit(f"cannot load memory store {spec!r}: {e}")
    if not issubclass(cls, MemoryStore):
        raise SystemExit(f"{spec} is not an agyteam.memory.MemoryStore subclass")
    return cls(agent, config)
