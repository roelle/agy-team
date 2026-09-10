"""Default A2A transport: append-only log plus a per-agent inbox file.

No daemon, no ports, no dependencies — it works anywhere the agents share a
filesystem, which includes every environment agyteam currently runs in. Swap it
out via AGYTEAM_BUS_TRANSPORT if you have something better.
"""
import json
import time
from pathlib import Path

from . import roster
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
        with self.log.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        if to != "user":
            with (self.inbox_dir / f"{to}.jsonl").open("a") as f:
                f.write(json.dumps(entry) + "\n")
            return f"[delivered to {to}]"
        return "[delivered to the user — they will see it in the team log]"

    def fetch(self) -> list[Message]:
        path = self.inbox_dir / f"{self.me}.jsonl"
        if not path.exists() or not path.read_text().strip():
            return []
        raw = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        path.write_text("")          # consumed on read; never redelivered
        return [Message(ts=m["ts"], sender=m["from"], to=m["to"],
                        content=m["content"]) for m in raw]
