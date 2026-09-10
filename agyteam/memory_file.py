"""Default memory store: one markdown file per memory, plus a MEMORY.md index.

Human-readable and diffable on purpose — you can inspect, edit, or hand-seed an
agent's memory with a text editor, and the index doubles as the summary loaded
into the agent's system prompt at boot. Swap it via AGYTEAM_MEMORY_STORE.

Layout (per agent, in durable scope):
    <workspace>/MEMORY.md          "- [name] description" per memory
    <workspace>/memory/<name>.md   the content
"""
import os
import time
from pathlib import Path

from .memory import MemoryEntry, MemoryStore, extract_provenance


class FileMemory(MemoryStore):
    label = "file"

    def __init__(self, agent: str, config: dict | None = None):
        super().__init__(agent, config)
        ws = self.config.get("workspace") or os.environ.get("AGYTEAM_WORKSPACE")
        if not ws:
            from . import scope
            ws = scope.load().agent_workspace(agent)
        self.workspace = Path(ws)
        self.memory_dir = self.workspace / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.workspace / "MEMORY.md"

    def _path(self, name: str) -> Path:
        return self.memory_dir / f"{name}.md"

    def save(self, name: str, description: str, content: str,
             why: str = "", when: str | None = None) -> bool:
        path = self._path(name)
        is_new = not path.exists()
        if when is None:
            when = time.strftime("%Y-%m-%d %H:%M:%S")

        # Provenance unpacking for read-modify-save workflows:
        # If incoming content already carries headers (title, - When:, - Why:),
        # unpack clean_body to avoid accumulating nested headings or duplicate
        # metadata blocks on re-save.
        #
        # Rationale for why precedence: An incoming non-empty why represents a
        # fresh reason/trigger for this update and supersedes old_why. If incoming
        # why is omitted or empty, preserve old_why so existing reasoning and
        # context are not lost during read-modify-save cycles.
        old_when, old_why, clean_body = extract_provenance(content)
        first_line = content.lstrip().splitlines()[0] if content.strip() else ""
        has_name_heading = (first_line.startswith("#") and
                            first_line.lstrip("#").strip().replace("_", "-") == name.replace("_", "-"))
        if old_when or old_why or has_name_heading:
            content = clean_body
            if not why and old_why:
                why = old_why

        # Provenance: record when and why this memory was learned so future
        # sessions can judge whether the context and reasoning remain true.
        lines = [f"# {name}", ""]
        if when:
            lines.append(f"- When: {when}")
        if why:
            lines.append(f"- Why: {why}")
        if when or why:
            lines.append("")
        lines.append(content.strip())
        lines.append("")

        path.write_text("\n".join(lines))
        self._write_index(name, description)
        return is_new

    def read(self, name: str) -> str | None:
        path = self._path(name)
        return path.read_text() if path.exists() else None

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.exists():
            return False
        path.unlink()
        self._write_index(name, None)
        return True

    def index(self) -> list[MemoryEntry]:
        entries = {}
        if self.index_path.exists():
            for line in self.index_path.read_text().splitlines():
                if line.startswith("- [") and "]" in line:
                    name, _, desc = line[3:].partition("]")
                    entries[name] = desc.strip()
        # Files are the source of truth; a hand-edited or stale index should
        # never make a memory that exists on disk invisible to the agent.
        for f in sorted(self.memory_dir.glob("*.md")):
            entries.setdefault(f.stem, "")
        return [MemoryEntry(name=n, description=d) for n, d in sorted(entries.items())]

    def _write_index(self, name: str, description: str | None) -> None:
        lines = ["# Memory index", ""]
        current = {e.name: e.description for e in self.index()}
        if description is None:
            current.pop(name, None)
        else:
            current[name] = description
        lines += [f"- [{n}] {d}" for n, d in sorted(current.items())]
        self.index_path.write_text("\n".join(lines) + "\n")
