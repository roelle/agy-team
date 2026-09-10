"""Skeleton for a custom memory store. Copy, rename, fill in.

This is the only file you should need to write to move agent memory off local
markdown files and into a shared database, a knowledge service, or an internal
store. Nothing else in agyteam changes, and the agents' tools keep the same
names and semantics.

    cp agyteam/memory_template.py example_memory.py
    # implement the four methods
    export AGYTEAM_MEMORY_STORE=example_memory:MyStore
    export AGYTEAM_MEMORY_CONFIG='{"dsn": "..."}'    # optional
    .venv/bin/python evals/test_memory.py            # must pass 14/14

Run that suite before trusting it. It is store-agnostic and checks the contract
the rest of the system relies on — round-tripping, overwrite-not-duplicate,
index accuracy, and the honest miss (see `read`), which is what stops an agent
from inventing an answer when memory has nothing.

Note on scope: one store instance serves one agent. If your backend is shared
between agents, namespace records by `self.agent` so teammates cannot read or
overwrite each other's memories unless you intend them to.
"""
from .memory import MemoryEntry, MemoryStore


class MyStore(MemoryStore):
    label = "custom"

    def __init__(self, agent, config=None):
        super().__init__(agent, config)
        # self.config holds the parsed AGYTEAM_MEMORY_CONFIG JSON.
        # Open your client/connection here, e.g.:
        # self.db = YourClient(dsn=self.config["dsn"], namespace=agent)
        raise NotImplementedError("implement MyStore before using it")

    def save(self, name: str, description: str, content: str,
             why: str = "", when: str | None = None) -> bool:
        """Create or overwrite. Return True if new, False if it already existed.

        Names arrive normalised (lowercase, hyphenated), so you can use them as
        keys directly. Overwriting is intended: agents are told to update
        memories rather than pile up near-duplicates.

        why: why this was learned (provenance context), so future sessions can judge
        if the lesson remains valid.
        when: timestamp string of when the memory was written (defaults to now).
        """
        raise NotImplementedError

    def read(self, name: str) -> str | None:
        """Return content, or None if absent.

        Return None — do not raise, and never synthesise a plausible answer. The
        server turns None into an explicit "I don't have that in memory" reply
        listing what does exist, which is load-bearing for grounding.
        """
        raise NotImplementedError

    def delete(self, name: str) -> bool:
        """Remove it. Return False if there was nothing to remove."""
        raise NotImplementedError

    def index(self) -> list[MemoryEntry]:
        """Every memory as MemoryEntry(name, description).

        This is read on every save and shown to the agent, so keep it cheap. It
        must reflect writes immediately — an agent that saves something and then
        cannot see it in its own index will save it again.
        """
        raise NotImplementedError

    # def close(self) -> None:
    #     self.db.disconnect()
