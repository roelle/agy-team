"""Default A2A transport: append-only log plus a per-agent inbox file.

No daemon, no ports, no dependencies — it works anywhere the agents share a
filesystem, which includes every environment agyteam currently runs in. Swap it
out via AGYTEAM_BUS_TRANSPORT if you have something better.

## An inbox is a read-modify-write, so it is locked

Appending to an inbox is safe on its own. Acknowledging is not: it reads the
inbox, drops what was handled, and writes the rest back, and a message a
teammate appended in between was overwritten. Measured: 10-14 of 400 messages
lost to one sender and one reader. Every change to an inbox, append included,
now happens under agyteam/filelock.py, and a rewrite replaces the file
atomically so a reader never sees it half-written.

A line that still cannot be parsed (a torn write, a hand edit) is skipped on
read instead of raising. Raising made every read of that inbox fail forever,
and the supervisor's loop with it.
"""
import json
import os
import tempfile
import time
from pathlib import Path

from . import filelock, roster
from .transport import Message, Transport


class FileTransport(Transport):
    label = "file"
    supports_roster_admin = True

    def __init__(self, me: str, config: dict | None = None):
        super().__init__(me, config)
        team_dir = (self.config.get("team_dir")
                    or __import__("os").environ.get("AGYTEAM_TEAM_DIR"))
        if not team_dir:
            from . import scope
            team_dir = scope.load().team_dir()
        self.dir = Path(team_dir)
        self.inbox_dir = self.dir / "inbox"
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.log = self.dir / "bus.jsonl"
        self.roster_path = self.dir / "roster.json"

    # --- roster ----------------------------------------------------------

    def _roster_doc(self) -> dict:
        # Accepts list or mapping shapes and raises RosterError with a readable
        # message on anything malformed — the MCP layer surfaces that as
        # "[error: ...]" rather than an AttributeError from deep in the stack.
        return roster.load(self.roster_path)

    def _agents(self) -> list[dict]:
        return self._roster_doc()["agents"]

    def teammates(self) -> list[dict]:
        return [a for a in self._agents() if a["name"] != self.me]

    def _write_agents(self, agents: list[dict]) -> None:
        doc = self._roster_doc()
        doc["agents"] = agents
        roster.save(self.roster_path, doc)      # always canonical list form

    def roster_add(self, name: str, role: str) -> str:
        agents = self._agents()
        if any(a["name"] == name for a in agents):
            return f"[error: '{name}' is already on the roster]"
        agents.append({"name": name, "role": role})
        self._write_agents(agents)
        return f"[added '{name}'. Roster: {', '.join(a['name'] for a in agents)}]"

    def roster_remove(self, name: str) -> str:
        agents = self._agents()
        if not any(a["name"] == name for a in agents):
            return f"[error: no teammate named '{name}']"
        self._write_agents([a for a in agents if a["name"] != name])
        left = ", ".join(a["name"] for a in self._agents()) or "nobody"
        return f"[removed '{name}'. Roster: {left}]"

    # --- messaging -------------------------------------------------------

    def send(self, to: str, content: str) -> str:
        known = [a["name"] for a in self._agents()]
        if to != "user" and known and to not in known:
            return (f"[error: no teammate named '{to}'. Teammates: "
                    f"{', '.join(known)}, user]")
        entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "from": self.me,
                 "to": to, "content": content}
        line = json.dumps(entry) + "\n"
        with self.log.open("a") as f:
            f.write(line)
        # The user gets a real mailbox like anyone else. Logging their messages
        # only to bus.jsonl made answers unreadable without grepping the log,
        # and left nothing that could tell whether the ask had been answered.
        inbox = self._inbox(to)
        with filelock.locked(inbox):
            with inbox.open("a") as f:
                f.write(line)
        return (f"[delivered to {to}]" if to != "user"
                else "[delivered to the user]")

    def _inbox(self, name: str) -> Path:
        return self.inbox_dir / f"{name}.jsonl"

    @staticmethod
    def _rewrite(path: Path, text: str) -> None:
        """Replace `path` whole, so a concurrent reader sees old or new, never half."""
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _read_inbox(self) -> list[Message]:
        path = self._inbox(self.me)
        try:
            text = path.read_text()
        except OSError:
            return []
        msgs = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                m = json.loads(line)
                msgs.append(Message(ts=m["ts"], sender=m["from"], to=m["to"],
                                    content=m["content"]))
            except (ValueError, KeyError, TypeError):
                continue            # skipped, never fatal; see module docstring
        return sorted(msgs, key=lambda m: 0 if m.sender == "user" else 1)

    def peek(self) -> list[Message]:
        return self._read_inbox()

    def fetch(self) -> list[Message]:
        return self._read_inbox()

    def acknowledge(self, msgs: list[Message] | None = None) -> None:
        """Remove acknowledged messages from the inbox file.

        `msgs=None` means "everything that was fetched", but it is read as
        "everything in the file" -- which, without the lock, also swallowed
        anything that had arrived since the fetch. Pass the messages.
        """
        if msgs is not None and not msgs:
            return
        path = self._inbox(self.me)
        with filelock.locked(path):
            try:
                content = path.read_text()
            except OSError:
                return
            if not content.strip():
                return
            if msgs is None:
                self._rewrite(path, "")
                return

            targets = []
            for m in msgs:
                sender = getattr(m, "sender", None) or getattr(m, "from", "")
                targets.append((getattr(m, "ts", ""), sender,
                                getattr(m, "to", self.me), getattr(m, "content", "")))

            remaining = []
            for line in content.splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    key = (entry.get("ts", ""),
                           entry.get("from") or entry.get("sender", ""),
                           entry.get("to", ""), entry.get("content", ""))
                except Exception:
                    remaining.append(line)
                    continue
                if key in targets:
                    targets.remove(key)
                else:
                    remaining.append(line)

            self._rewrite(path, "".join(l + "\n" for l in remaining))

    def requeue(self, msgs_or_role, msgs=None) -> None:
        """Prepend unconsumed messages back to the inbox."""
        if msgs is None:
            if isinstance(msgs_or_role, str):
                return
            messages = list(msgs_or_role or [])
            explicit_role = None
        else:
            explicit_role = msgs_or_role
            messages = list(msgs or [])
        if not messages:
            return

        by_recipient: dict[str, list[dict]] = {}
        for m in messages:
            recip = explicit_role or getattr(m, "to", None) or self.me
            if isinstance(m, Message):
                entry = {"ts": m.ts, "from": m.sender, "to": m.to, "content": m.content}
            elif isinstance(m, dict):
                entry = {
                    "ts": m.get("ts", time.strftime("%Y-%m-%d %H:%M:%S")),
                    "from": m.get("from") or m.get("sender", "unknown"),
                    "to": m.get("to", recip),
                    "content": m.get("content", ""),
                }
            else:
                entry = {
                    "ts": getattr(m, "ts", time.strftime("%Y-%m-%d %H:%M:%S")),
                    "from": getattr(m, "sender", getattr(m, "from", "unknown")),
                    "to": getattr(m, "to", recip),
                    "content": getattr(m, "content", ""),
                }
            by_recipient.setdefault(recip, []).append(entry)

        for recip, entries in by_recipient.items():
            path = self._inbox(recip)
            with filelock.locked(path):
                existing = path.read_text() if path.exists() else ""
                existing_entries = []
                for l in existing.splitlines():
                    if l.strip():
                        try:
                            existing_entries.append(json.loads(l))
                        except Exception:
                            pass
                entries_to_add = [e for e in entries if e not in existing_entries]
                if entries_to_add:
                    prefix = "".join(json.dumps(e) + "\n" for e in entries_to_add)
                    if existing and not existing.endswith("\n"):
                        existing += "\n"
                    self._rewrite(path, prefix + existing)
