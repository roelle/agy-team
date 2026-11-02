"""Read the team's record, and follow it as it grows.

The team directory is the record: bus.jsonl (every message), tasks.jsonl,
reviews.jsonl, events.jsonl, channels.json, roster.json, inflight.json and
the supervisor's heartbeat. This is the one place that knows how to read
them, for any client -- a chat UI, a dashboard, a script -- so none of them
parses the files itself and none of them drifts when a format changes.

    rec = Record(team_dir)
    rec.messages(channel="billing")         # a channel's posts, oldest first
    rec.messages(dm=("user", "coder"))       # a direct conversation
    rec.thread("a1b2c3d4e5f6")               # a message and its replies
    rec.presence()                           # who is running, idle, failing
    feed = Feed(team_dir); feed.poll()       # what was appended since last poll

Everything here reads. Changing the team goes through agyteam.actions.
"""
import json
import os
import time
from pathlib import Path

from . import channels as channels_lib

#: Supervisor plumbing: messages a person does not need in the stream.
PLUMBING_SENDERS = {"supervisor"}

#: Messages a person took off Waiting on you without replying, one per line.
CLEARED = "cleared.jsonl"


def _jsonl(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _epoch(ts) -> float | None:
    """A record timestamp ("%Y-%m-%d %H:%M:%S", local time) as epoch seconds."""
    try:
        return time.mktime(time.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return None


def _lines(path: Path) -> list[str]:
    try:
        return [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def instant(ts) -> float | None:
    """Seconds since the epoch for a record's timestamp, whichever kind it
    is. Two are written: UTC ISO ("2026-10-27T19:01:18Z": tasks, model
    overrides, the heartbeat) and local time with a space ("2026-10-27
    12:01:18": the bus, events, reviews, the memory log). Compared as text
    they disagree by the UTC offset, so compare these instead."""
    s = str(ts or "").strip()
    try:
        if "T" in s[:19]:
            import calendar
            return float(calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")))
        return time.mktime(time.strptime(s[:19], "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


class Record:
    def __init__(self, team_dir):
        self.team_dir = Path(team_dir)

    # --- who -------------------------------------------------------------------

    def roster(self) -> dict:
        return _json(self.team_dir / "roster.json", {"agents": []})

    def agents(self) -> list[str]:
        return [a["name"] for a in self.roster().get("agents", []) if a.get("name")]

    def channels(self) -> dict[str, dict]:
        """Every channel, archived and deleted ones included and flagged as
        such ("archived", "deleted"); a client decides what to show."""
        return channels_lib.load(self.team_dir)

    def operator(self) -> dict:
        """{"name", "handles"}: the human this team works for, if named."""
        from . import roster as roster_lib
        return roster_lib.operator(self.roster())

    # --- what was said -----------------------------------------------------------

    def bus(self) -> list[dict]:
        """Every message, with each reply's `thread` set to the first message
        of its thread. Replies used to be stored under whatever message they
        answered, often another reply, where no client looked for them."""
        rows = _jsonl(self.team_dir / "bus.jsonl")
        parents = {r["id"]: r["thread"] for r in rows if r.get("id") and r.get("thread")}
        if not parents:
            return rows
        out = []
        for r in rows:
            t = r.get("thread")
            if t in parents:
                seen = {t}
                while t in parents and parents[t] not in seen:
                    t = parents[t]
                    seen.add(t)
                r = {**r, "thread": t}
            out.append(r)
        return out

    def messages(self, channel: str | None = None, dm: tuple[str, str] | None = None,
                 include_plumbing: bool = False, limit: int | None = None) -> list[dict]:
        """A channel's posts, or the direct messages between two parties.

        Thread replies are included; a client groups them under their root
        by `thread`.
        """
        rows = self.bus()
        if not include_plumbing:
            rows = [r for r in rows if r.get("from") not in PLUMBING_SENDERS]
        if channel:
            rows = [r for r in rows if r.get("channel") == channel]
        elif dm:
            a, b = dm
            rows = [r for r in rows if not r.get("channel")
                    and {r.get("from"), r.get("to")} == {a, b}]
        return rows[-limit:] if limit else rows

    def conversations(self) -> list[tuple[str, str]]:
        """Every pair that has exchanged direct messages, the user's first."""
        pairs = {tuple(sorted((r.get("from"), r.get("to")))) for r in self.bus()
                 if not r.get("channel") and r.get("from") and r.get("to")
                 and r.get("from") not in PLUMBING_SENDERS}
        return sorted(pairs, key=lambda p: (0 if "user" in p else 1, p))

    def thread(self, root_id: str) -> list[dict]:
        rows = self.bus()
        return [r for r in rows if r.get("id") == root_id or r.get("thread") == root_id]

    def reply_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.bus():
            if r.get("thread"):
                out[r["thread"]] = out.get(r["thread"], 0) + 1
        return out

    # --- the work ------------------------------------------------------------------

    def tasks(self) -> list[dict]:
        from . import tasks as tasks_lib
        try:
            return sorted(tasks_lib.snapshot(self.team_dir).values(),
                          key=lambda t: t.get("updated_at") or "", reverse=True)
        except Exception:
            return []

    def reviews(self) -> list[dict]:
        return _jsonl(self.team_dir / "reviews.jsonl")

    def events(self, kind: str | None = None) -> list[dict]:
        rows = _jsonl(self.team_dir / "events.jsonl")
        return [r for r in rows if kind is None or r.get("event") == kind]

    def norms(self) -> str:
        try:
            return (self.team_dir / "NORMS.md").read_text(encoding="utf-8")
        except OSError:
            return ""

    def task_usage(self) -> dict[str, dict]:
        """Tokens spent on each task, estimated from the turns: {task_id:
        {turns, input_tokens, output_tokens, tokens, direct, inferred,
        agents: {agent: tokens}}}.

        A turn woken by a message naming tasks counts toward them ("direct",
        split evenly). Any other turn counts toward the task its agent owned
        and was working (claimed, running, review) when the turn ended
        ("inferred", split evenly if it had several). Turns with neither are
        nobody's. An estimate, and labelled as one.
        """
        from . import tasks as tasks_lib
        active_states = {"claimed", "running", "review"}
        spans: dict[str, list[tuple[float, float, str]]] = {}  # agent -> [(start, end, task)]
        state: dict[str, dict] = {}
        open_since: dict[str, tuple[str, float]] = {}            # task -> (agent, start)
        for rec in tasks_lib.read(self.team_dir):
            tid, ts = rec["id"], instant(rec.get("ts"))
            if ts is None:
                continue
            cur = state.setdefault(tid, {})
            for k in ("owner", "status"):
                if k in rec:
                    cur[k] = rec[k]
            was = open_since.pop(tid, None)
            if was:
                spans.setdefault(was[0], []).append((was[1], ts, tid))
            if cur.get("owner") and cur.get("status") in active_states:
                open_since[tid] = (cur["owner"], ts)
        for tid, (agent, start) in open_since.items():
            spans.setdefault(agent, []).append((start, float("inf"), tid))
        out: dict[str, dict] = {}

        def add(tid, agent, ev, share, how):
            u = out.setdefault(tid, {"turns": 0, "input_tokens": 0, "output_tokens": 0,
                                     "tokens": 0, "direct": 0, "inferred": 0, "agents": {}})
            inp = int(ev.get("input_tokens") or 0) * share
            outp = int(ev.get("output_tokens") or 0) * share
            u["turns"] += 1
            u["input_tokens"] += round(inp)
            u["output_tokens"] += round(outp)
            u["tokens"] += round(inp + outp)
            u[how] += 1
            u["agents"][agent] = u["agents"].get(agent, 0) + round(inp + outp)

        for ev in self.events("turn"):
            agent, ts = ev.get("agent"), instant(ev.get("ts"))
            if ts is None:
                continue
            named = [t for t in ((ev.get("trigger") or {}).get("task_ids") or []) if t]
            if named:
                for t in named:
                    add(t, agent, ev, 1 / len(named), "direct")
                continue
            working = [tid for start, end, tid in spans.get(agent, []) if start <= ts <= end]
            for t in working:
                add(t, agent, ev, 1 / len(working), "inferred")
        return out

    def task_tree(self) -> dict:
        """{"levels": [names], "nodes": {id: {children, level, depth, path,
        done, total, archived_by, tokens}}}: the shape of the work, with each
        task's tokens summed over everything under it."""
        from . import tasks as tasks_lib
        shape = tasks_lib.tree(self.team_dir)
        usage = self.task_usage()
        memo: dict[str, int] = {}

        def tokens(tid, stack=()):
            if tid in memo:
                return memo[tid]
            n = (usage.get(tid) or {}).get("tokens", 0)
            for c in shape.get(tid, {}).get("children", []):
                if c not in stack:
                    n += tokens(c, stack + (tid,))
            memo[tid] = n
            return n
        for tid, node in shape.items():
            node["tokens"] = tokens(tid)
        return {"levels": tasks_lib.level_names(self.team_dir), "nodes": shape}

    def avatars(self) -> dict[str, str]:
        from . import actions
        return actions.avatars(self.team_dir)

    def memory_stats(self, agent: str) -> list[dict]:
        """One agent's memories with their history: [{name, description,
        created, saved, revisions, reads, last_read, known_since}]."""
        from . import memory_log
        store = memory_log.open_store(self.team_dir, agent)
        try:
            entries = memory_log.entries(store)
        finally:
            store.close()
        st = memory_log.stats(self.team_dir, agent, entries)
        return [{"name": e.name, "description": e.description, **st.get(e.name, {})}
                for e in entries]

    def recall_stats(self, agent: str, days: int = 14) -> dict:
        """How recall has gone for one agent, from its `recall` and
        `recall_miss` events: {"enabled", "days": [{"day", "wakes", "offered",
        "memories", "hints", "errors", "misses"}] for the last `days` days
        (oldest first, empty days included), "memories": {name: {"offered",
        "hinted", "missed", "last_offered"}} over all time, and "misses": the
        last ten misses, newest first}."""
        import datetime
        from . import recall as recall_lib
        today = datetime.date.today()
        span = [(today - datetime.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        per_day = {d: {"day": d, "wakes": 0, "offered": 0, "memories": 0, "hints": 0,
                       "errors": 0, "misses": 0} for d in span}
        per_mem: dict[str, dict] = {}
        misses = []

        def mem(name):
            return per_mem.setdefault(name, {"offered": 0, "hinted": 0, "missed": 0,
                                             "last_offered": None})
        for e in self.events():
            if e.get("agent") != agent or e.get("event") not in ("recall", "recall_miss"):
                continue
            d = per_day.get(str(e.get("ts") or "")[:10])
            if e["event"] == "recall_miss":
                misses.append(e)
                if e.get("memory"):
                    mem(e["memory"])["missed"] += 1
                if d:
                    d["misses"] += 1
                continue
            picked, hints = e.get("picked") or [], e.get("hints") or []
            for name in picked:
                m = mem(name)
                m["offered"] += 1
                m["last_offered"] = e.get("ts")
            for name in hints:
                mem(name)["hinted"] += 1
            if d:
                d["wakes"] += 1
                d["offered"] += bool(picked)
                d["memories"] += len(picked)
                d["hints"] += bool(hints) and not picked
                d["errors"] += bool(e.get("error"))
        return {"enabled": recall_lib.config(self.team_dir) is not None,
                "days": list(per_day.values()), "memories": per_mem,
                "misses": [{k: e.get(k) for k in ("ts", "memory", "correction", "learned")}
                           for e in reversed(misses[-10:])]}

    def memory_archive(self, agent: str) -> list[dict]:
        from . import memory_log
        return memory_log.archived(self.team_dir, agent)

    def norm_candidates(self) -> list[dict]:
        """The same lesson in several agents' memories: candidates for NORMS.md."""
        from . import memory_log
        return memory_log.norm_candidates(self.team_dir, memory_log.everything(self.team_dir))

    # --- who is doing what now ------------------------------------------------------------

    def supervisor(self) -> dict | None:
        from . import heartbeat
        try:
            return heartbeat.running(self.team_dir)
        except Exception:
            return None

    def stopped(self) -> str | None:
        try:
            return (self.team_dir / ".stop").read_text(encoding="utf-8").strip() or "stopped"
        except OSError:
            return None

    def needs_you(self) -> list[dict]:
        """What is waiting on a person, oldest first; empty when nothing is.

        Each row has "kind" and "ts", and points at where to deal with it:

            stopped   the team is stopped: "reason"
            question, blocker
                      a message to the operator with no reply yet: "id",
                      "from", "content", "channel" or "dm", and "task_id"
            task      an open task the operator owns: "id", "title", "status"
            stuck     a task an agent marked blocked: the same, plus "owner"

        Only those kinds of message count, not ordinary mail: whether a
        report is really a question is not something to guess from its text.
        A reply from the operator in the same conversation or thread clears
        a message, and so does actions.clear_waiting. A notice that an agent
        is failing (its "failing" field) clears once that agent's turn works.
        """
        from . import tasks as tasks_lib
        rows = []
        reason = self.stopped()
        if reason:
            rows.append({"kind": "stopped", "reason": reason, "ts": ""})
        operator = self.operator()
        names = {"user", operator.get("name"), *operator.get("handles", [])} - {"", None}
        replied: dict[tuple, int] = {}          # by place in the log: ts is to the second
        cleared = {r.get("id") for r in _jsonl(self.team_dir / CLEARED)}
        last_turn = self._last_turns()
        waiting = []
        for n, m in enumerate(self.bus()):
            sender = m.get("from")
            where = (("channel", m["channel"]) if m.get("channel")
                     else ("dm", m.get("to") if sender == "user" else sender))
            if sender == "user":
                replied[where] = n
                if m.get("thread"):
                    replied[("thread", m["thread"])] = n
            elif m.get("kind") in ("question", "blocker") and (
                    m.get("to") in names or names & set(m.get("mentions") or [])):
                waiting.append((n, m, where))
        for n, m, where in waiting:
            keys = [("thread", m["thread"])] if m.get("thread") else [where]
            if m.get("id"):
                keys.append(("thread", m["id"]))
            if any(replied.get(k, -1) > n for k in keys) or m.get("id") in cleared:
                continue
            if last_turn.get(m.get("failing"), {}).get("event") == "turn":
                continue
            rows.append({"kind": m["kind"], "ts": m.get("ts") or "", "id": m.get("id"),
                         "from": m.get("from"), "content": m.get("content"),
                         "task_id": m.get("task_id"), where[0]: where[1],
                         "thread": m.get("thread")})
        for t in self.tasks():
            status = t.get("status")
            if status in tasks_lib.TERMINAL or t.get("archived"):
                continue
            row = {"id": t["id"], "title": t.get("title"), "status": status,
                   "ts": t.get("updated_at") or ""}
            if t.get("owner") in names and status not in tasks_lib.PAUSED:
                rows.append({"kind": "task", **row})
            elif status == "blocked":
                rows.append({"kind": "stuck", "owner": t.get("owner"), **row})
        return sorted(rows, key=lambda r: _epoch(r["ts"]) or 0)

    def _last_turns(self) -> dict[str, dict]:
        """{agent: its latest "turn" or "failure" event}."""
        last: dict[str, dict] = {}
        for e in self.events():
            if e.get("event") in ("turn", "failure") and e.get("agent"):
                last[e["agent"]] = e
        return last

    def presence(self) -> dict[str, dict]:
        """{agent: {"state", "since", "detail"}}.

        state is one of: running (a turn in flight), held (mail is waiting
        and the hop budget is spent; the user's messages still wake it),
        failing (its last turn failed), idle (supervisor up, nothing
        running), offline (no supervisor is running, so nothing will wake it).
        """
        running = self.supervisor()
        held = set((running or {}).get("held") or [])
        hops = (running or {}).get("hops") or {}
        inflight = _json(self.team_dir / "inflight.json", {})
        last = self._last_turns()
        out = {}
        for agent in self.agents():
            if isinstance(inflight, dict) and agent in inflight:
                t = inflight[agent]
                out[agent] = {"state": "running", "since": t.get("ts"),
                              "detail": ", ".join((t.get("trigger") or {}).get("senders", []))}
            elif agent in held:
                window = hops.get("window_s")
                out[agent] = {"state": "held", "since": None,
                              "detail": f"hop budget: {hops.get('used')} of {hops.get('max')} wakes"
                                        + (f" in the last {window / 60:g} min" if window else "")}
            elif last.get(agent, {}).get("event") == "failure":
                out[agent] = {"state": "failing", "since": last[agent].get("ts"),
                              "detail": (last[agent].get("error") or "")[:200]}
            elif running:
                out[agent] = {"state": "idle", "since": last.get(agent, {}).get("ts"),
                              "detail": ""}
            else:
                out[agent] = {"state": "offline", "since": None, "detail": ""}
        return out

    def quiet_mail(self, days: float = 7, now: float | None = None) -> dict:
        """What channel membership costs: mail a wake carried that did not
        ask for the agent, from the `quiet` each turn's trigger records.

        {"days", "turns", "turns_with_quiet", "msgs", "bytes", "deferred",
         "est_tokens", "input_tokens", "share",
         "channels": {name: {"msgs", "bytes"}} (bytes only, largest first),
         "agents": {name: {"msgs", "bytes", "turns"}}}

        est_tokens is bytes / 4. share is est_tokens over the uncached input
        tokens of the same turns, the first read only: the mail then stays
        in the agent's context, re-read at the cached rate until compaction.
        """
        now = time.time() if now is None else now
        out = {"days": days, "turns": 0, "turns_with_quiet": 0, "msgs": 0, "bytes": 0,
               "deferred": 0, "input_tokens": 0}
        channels: dict[str, int] = {}
        agents: dict[str, dict] = {}
        for e in self.events():
            if e.get("event") != "turn":
                continue
            at = _epoch(e.get("ts"))
            if at is None or at < now - days * 86400:
                continue
            out["turns"] += 1
            out["input_tokens"] += e.get("input_tokens") or 0
            q = (e.get("trigger") or {}).get("quiet")
            if not q:
                continue
            out["turns_with_quiet"] += 1
            for k in ("msgs", "bytes", "deferred"):
                out[k] += q.get(k) or 0
            for name, size in (q.get("channels") or {}).items():
                channels[name] = channels.get(name, 0) + size
            a = agents.setdefault(e.get("agent") or "unknown", {"msgs": 0, "bytes": 0, "turns": 0})
            a["msgs"] += q.get("msgs") or 0
            a["bytes"] += q.get("bytes") or 0
            a["turns"] += 1
        out["est_tokens"] = out["bytes"] // 4
        out["share"] = round(out["est_tokens"] / out["input_tokens"], 4) if out["input_tokens"] else None
        out["channels"] = dict(sorted(channels.items(), key=lambda kv: -kv[1]))
        out["agents"] = agents
        return out

    def load(self, now: float | None = None) -> dict[str, dict]:
        """How busy each agent is, so work can go where there is room.

        {agent: {"working": bool, "since": ts of the turn in flight or None,
                 "waiting": waking messages not yet being worked on,
                 "busy": {"hour": share, "day": share},
                 "busy_s": seconds in turns, all time, "turns": all time}}

        A share is the part of the window spent in turns, the turn in flight
        included. Turns are timed from the record, so work an agent did
        outside the supervisor (asked directly in the IDE, say) is not
        counted.
        """
        from .transport import Message
        now = time.time() if now is None else now
        windows = {"hour": 3600.0, "day": 86400.0}
        spans: dict[str, list[tuple[float, float]]] = {}
        for e in self.events():
            if e.get("event") not in ("turn", "failure") or not e.get("agent"):
                continue
            end, dur = _epoch(e.get("ts")), e.get("duration_s")
            if end is None or not isinstance(dur, (int, float)) or dur <= 0:
                continue
            spans.setdefault(e["agent"], []).append((end - dur, end))
        inflight = _json(self.team_dir / "inflight.json", {})
        inflight = inflight if isinstance(inflight, dict) else {}
        out = {}
        for agent in self.agents():
            done = spans.get(agent, [])
            turn = inflight.get(agent) if isinstance(inflight.get(agent), dict) else None
            since = turn.get("ts") if turn else None
            start = _epoch(since)
            live = [(start, now)] if start is not None and start < now else []
            busy = {}
            for name, w in windows.items():
                lo = now - w
                spent = sum(max(0.0, min(b, now) - max(a, lo)) for a, b in done + live)
                busy[name] = round(min(1.0, spent / w), 3)
            waiting = 0
            for line in _lines(self.team_dir / "inbox" / f"{agent}.jsonl"):
                try:
                    m = Message.from_dict(json.loads(line))
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                # Mail older than the turn in flight is what it is working on.
                if m.wakes and not (since and (m.ts or "") <= since):
                    waiting += 1
            out[agent] = {"working": bool(turn), "since": since, "waiting": waiting,
                          "busy": busy,
                          "busy_s": round(sum(b - a for a, b in done + live)),
                          "turns": len(done)}
        return out

    def models(self) -> dict[str, dict]:
        """{agent: {"model", "effort", "source"}}."""
        from . import models as models_lib
        from . import runner as runner_lib
        out = {}
        for a in self.roster().get("agents", []):
            m, e, src = models_lib.resolve(self.team_dir, a["name"], a.get("model"), a.get("effort"))
            # available: False means the configured runner will refuse it,
            # None that it cannot say (or that the runner's default is used).
            out[a["name"]] = {"model": m, "effort": e, "source": src,
                              "available": runner_lib.model_available(
                                  m, e, team_dir=self.team_dir),
                              # what an override falls back to when it expires
                              "roster_model": a.get("model"),
                              "roster_available": runner_lib.model_available(
                                  a.get("model"), a.get("effort"), team_dir=self.team_dir)}
        return out

    # --- search ------------------------------------------------------------------------------

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Case-insensitive substring search over messages, tasks and norms."""
        q = (query or "").strip().lower()
        if not q:
            return []
        hits = []
        gone = {n for n, c in self.channels().items() if c.get("deleted")}
        for r in reversed(self.bus()):
            if r.get("from") in PLUMBING_SENDERS or r.get("channel") in gone:
                continue
            if q in (r.get("content") or "").lower():
                hits.append({"type": "message", "item": r})
        for t in self.tasks():
            text = " ".join(str(t.get(k) or "") for k in ("title", "note", "project", "owner"))
            if q in text.lower():
                hits.append({"type": "task", "item": t})
        for line in self.norms().splitlines():
            if q in line.lower():
                hits.append({"type": "norm", "item": {"text": line}})
        return hits[:limit]


class Feed:
    """What has been appended to the record since the last poll.

    Follows the append-only logs by byte offset (bus, tasks, reviews,
    events) and notices when the snapshot files change (inflight,
    channels, roster, the heartbeat). A log that shrinks -- rotated or
    rewritten -- is read again from the start.
    """
    LOGS = ("bus.jsonl", "tasks.jsonl", "reviews.jsonl", "events.jsonl")
    SNAPSHOTS = ("inflight.json", "channels.json", "roster.json", "supervisor.json",
                 "models.jsonl", ".stop")

    def __init__(self, team_dir, from_start: bool = False):
        self.team_dir = Path(team_dir)
        self.offsets: dict[str, int] = {}
        self.stamps: dict[str, tuple] = {}
        if not from_start:
            for name in self.LOGS:
                self.offsets[name] = self._size(name)
            for name in self.SNAPSHOTS:
                self.stamps[name] = self._stamp(name)

    def _size(self, name) -> int:
        try:
            return (self.team_dir / name).stat().st_size
        except OSError:
            return 0

    def _stamp(self, name) -> tuple:
        try:
            st = (self.team_dir / name).stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return ()

    def poll(self) -> list[dict]:
        """[{"stream": "bus"|"tasks"|..., "item": row}] and
        [{"stream": "changed", "item": {"file": name}}] for snapshot files."""
        out = []
        for name in self.LOGS:
            p = self.team_dir / name
            size = self._size(name)
            start = self.offsets.get(name, 0)
            if size < start:
                start = 0
            if size > start:
                with p.open("rb") as f:
                    f.seek(start)
                    chunk = f.read(size - start)
                # Only whole lines: a writer may be mid-line.
                cut = chunk.rfind(b"\n") + 1
                for line in chunk[:cut].splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    out.append({"stream": name.split(".")[0], "item": row})
                self.offsets[name] = start + cut
            else:
                self.offsets[name] = start
        for name in self.SNAPSHOTS:
            stamp = self._stamp(name)
            if stamp != self.stamps.get(name, ()):
                self.stamps[name] = stamp
                out.append({"stream": "changed", "item": {"file": name}})
        return out
