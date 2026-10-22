"""What happens to each agent's memories over time, kept by the team.

A memory store keeps one thing about an entry's history: when it was last
saved. How old it is, how often it was rewritten, whether anyone ever reads
it -- all gone. So the memory server writes every save, read, delete and
merge to <team_dir>/memory_log.jsonl, whatever store is behind it, and
stats() turns that (and, for history from before this file, the event log's
read_memory calls) into what a review needs.

Nothing an agent deletes or merges away is destroyed: it goes to
<team_dir>/memory_archive/<agent>/ first, and restore() brings it back. A
review that consolidates good lessons into vague ones is the failure to
guard against, and an archive is the guard.

norm_candidates() finds the same lesson learned separately by several
agents -- a candidate for NORMS.md, which the operator decides on.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

LOG = "memory_log.jsonl"
ARCHIVE = "memory_archive"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def team_dir_here() -> Path | None:
    """The team this process serves, or None if it cannot tell."""
    env = os.environ.get("AGYTEAM_TEAM_DIR")
    if env:
        return Path(env)
    try:
        from . import scope
        return scope.load().team_dir()
    except Exception:
        return None


def record(team_dir, agent: str, op: str, name: str, **fields) -> None:
    """Append one event. Never raises: the log is about the memory, and a
    failure to log must not fail the memory operation."""
    if team_dir is None:
        return
    try:
        row = {"ts": _now(), "agent": agent, "op": op, "name": name, **fields}
        with (Path(team_dir) / LOG).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def _rows(team_dir) -> list[dict]:
    try:
        lines = (Path(team_dir) / LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def open_store(team_dir, agent: str):
    """An agent's memory store, opened for this team whatever team the
    process is pointed at: a client showing several teams has no one
    AGYTEAM_TEAM_DIR. Raises LookupError for an agent not on the roster."""
    from . import memory as memory_lib
    from . import roster as roster_lib
    td = Path(team_dir)
    names = [a["name"] for a in roster_lib.load(td / "roster.json").get("agents", [])]
    if agent not in names:
        raise LookupError(f"no teammate named {agent!r}")
    try:
        config = json.loads(os.environ.get("AGYTEAM_MEMORY_CONFIG") or "{}")
    except ValueError:
        config = {}
    if "workspace" not in config:
        # The team this process is configured for keeps its agents where
        # its scope says (AGYTEAM_DURABLE_DIR can put them elsewhere);
        # another team, where the layout puts them.
        try:
            from . import scope
            sc = scope.load()
            here = sc.team_dir().resolve() == td.resolve()
        except Exception:
            here = False
        durable = td.parent if td.name == "team" else td
        config["workspace"] = str(sc.agent_workspace(agent) if here
                                  else durable / "agents" / agent)
    return memory_lib.load(agent, config=config)


def entries(store) -> list:
    """The store's index, with each entry's last save time filled in from
    its provenance when the store's index leaves it out."""
    from .memory import extract_provenance
    out = []
    for e in store.index():
        if not getattr(e, "when", ""):
            when, why, _ = extract_provenance(store.read(e.name) or "")
            try:
                e = type(e)(e.name, e.description, why or "", when or "")
            except TypeError:
                pass
        out.append(e)
    return out


def everything(team_dir) -> dict[str, list[dict]]:
    """{agent: [{name, description, content}]} for every agent on the roster,
    the content without its provenance header."""
    from .memory import extract_provenance
    from . import roster as roster_lib
    out = {}
    for a in roster_lib.load(Path(team_dir) / "roster.json").get("agents", []):
        try:
            store = open_store(team_dir, a["name"])
        except Exception:
            continue
        try:
            out[a["name"]] = [{"name": e.name, "description": e.description,
                               "content": extract_provenance(store.read(e.name) or "")[2]}
                              for e in store.index()]
        finally:
            store.close()
    return out


# --- the archive ------------------------------------------------------------------

def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def archive(team_dir, agent: str, name: str, description: str, content: str,
            reason: str, by: str) -> str | None:
    """Keep a copy of a memory about to be deleted or merged away."""
    if team_dir is None:
        return None
    try:
        d = Path(team_dir) / ARCHIVE / _safe(agent)
        d.mkdir(parents=True, exist_ok=True)
        aid = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}-{_safe(name)}"
        (d / f"{aid}.json").write_text(json.dumps({
            "id": aid, "agent": agent, "name": name, "description": description,
            "content": content, "archived": _now(), "reason": reason, "by": by},
            indent=2) + "\n", encoding="utf-8")
        return aid
    except Exception:
        return None


def archived(team_dir, agent: str) -> list[dict]:
    """Newest first: [{id, name, description, content, archived, reason, by}]."""
    d = Path(team_dir) / ARCHIVE / _safe(agent)
    out = []
    for f in sorted(d.glob("*.json"), reverse=True) if d.is_dir() else []:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def take_archived(team_dir, agent: str, aid: str) -> dict:
    """Remove one entry from the archive and return it (for a restore)."""
    f = Path(team_dir) / ARCHIVE / _safe(agent) / f"{_safe(aid)}.json"
    doc = json.loads(f.read_text(encoding="utf-8"))
    f.unlink()
    return doc


# --- stats ---------------------------------------------------------------------------

def _event_reads(team_dir, agent: str, before: str | None) -> list[tuple[str, str]]:
    """(ts, name) of read_memory calls in events.jsonl, from before the log."""
    out = []
    try:
        with (Path(team_dir) / "events.jsonl").open(encoding="utf-8") as f:
            for line in f:
                if '"read_memory"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("agent") != agent or e.get("tool") != "read_memory":
                    continue
                if before and (e.get("ts") or "") >= before:
                    continue
                args = e.get("args") or {}
                args = args.get("Arguments", args) if isinstance(args, dict) else {}
                name = args.get("name") if isinstance(args, dict) else None
                if name:
                    out.append((e.get("ts") or "", str(name)))
    except OSError:
        pass
    return out


def stats(team_dir, agent: str, entries=()) -> dict[str, dict]:
    """{name: {created, saved, revisions, reads, last_read, known_since}}.

    `entries` (the store's index) supplies what the log cannot: for a memory
    saved before the log existed, its last save time, which is then the
    earliest we know of it ("known_since" says the history starts there).
    """
    rows = [r for r in _rows(team_dir) if r.get("agent") == agent]
    first_logged = rows[0]["ts"] if rows else None
    out: dict[str, dict] = {}

    def s(name):
        return out.setdefault(name, {"created": None, "saved": None, "revisions": 0,
                                     "reads": 0, "last_read": None, "known_since": None})

    for ts, name in _event_reads(team_dir, agent, first_logged):
        st = s(name)
        st["reads"] += 1
        st["last_read"] = max(st["last_read"] or "", ts) or None
    for r in rows:
        name, op, ts = r.get("name"), r.get("op"), r.get("ts")
        if op == "save":
            st = s(name)
            if r.get("new") and not st["created"]:
                st["created"] = ts
            st["revisions"] += 1
            st["saved"] = ts
        elif op == "read":
            st = s(name)
            st["reads"] += 1
            st["last_read"] = ts
        elif op == "merge":
            # The merged entry carries its sources' history.
            st = s(name)
            st["revisions"] += 1
            st["saved"] = ts
            for src in r.get("sources") or []:
                if src == name or src not in out:
                    continue
                o = out.pop(src)
                st["reads"] += o["reads"]
                st["revisions"] += o["revisions"]
                st["created"] = min(filter(None, [st["created"], o["created"]]), default=None)
                st["last_read"] = max(filter(None, [st["last_read"], o["last_read"]]), default=None)
        elif op == "delete":
            out.pop(name, None)
    for e in entries:
        st = s(e.name)
        if not st["created"]:
            st["known_since"] = min(filter(None, [e.when or None, st["saved"]]), default=None)
        st["saved"] = st["saved"] or (e.when or None)
    names = {e.name for e in entries}
    return {k: v for k, v in out.items() if not names or k in names}


def saves_since(team_dir, agent: str, since: str | None) -> int:
    return sum(1 for r in _rows(team_dir)
               if r.get("agent") == agent and r.get("op") in ("save", "merge", "delete")
               and (not since or (r.get("ts") or "") > since))


# --- the same lesson, learned separately ---------------------------------------------

def _terms(text: str) -> set[str]:
    from .memory import extract_keywords
    return {w for w in extract_keywords(text) if len(w) > 3}


def norm_candidates(team_dir, memories: dict[str, list[dict]], min_agents: int = 3,
                    threshold: float = 0.35) -> list[dict]:
    """Groups of memories, from at least `min_agents` different agents, that
    say much the same thing: [{agents, memories: [{agent, name,
    description}], terms}], strongest first.

    Lexical, on purpose: it costs nothing and runs anywhere, and it finds
    lessons worded alike. Two agents who learned one lesson in different
    words are missed; a retro or the operator still catches those.
    """
    items = []
    for agent, mems in memories.items():
        for m in mems:
            t = _terms(f"{m.get('name', '')} {m.get('description', '')} "
                       f"{m.get('description', '')} {m.get('content', '')[:1500]}")
            if t:
                items.append((agent, m, t))
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i][0] == items[j][0]:
                continue
            a, b = items[i][2], items[j][2]
            if len(a & b) / max(1, min(len(a), len(b))) >= threshold \
                    and len(a & b) / len(a | b) >= threshold / 2:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for idx in groups.values():
        agents = sorted({items[i][0] for i in idx})
        if len(agents) < min_agents:
            continue
        shared = set.intersection(*(items[i][2] for i in idx))
        out.append({"agents": agents,
                    "memories": [{"agent": items[i][0], "name": items[i][1].get("name"),
                                  "description": items[i][1].get("description", "")}
                                 for i in idx],
                    "terms": sorted(shared)[:12]})
    return sorted(out, key=lambda g: (-len(g["agents"]), -len(g["memories"])))
