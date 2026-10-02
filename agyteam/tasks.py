"""Where long-running work is tracked, so an agent never has to hold it in its head.

## Why this file exists

The team was running simulations that take hours to finish, and the pattern
that emerged around that was an agent scheduling an external cron job to poke
itself later. That works, but it puts the schedule outside every record this
project keeps: `agyteam.doctor` cannot see it, a restart does not know it
existed, and a second agent asking "is anything still running?" has nowhere to
look but the first agent's memory of having set it up.

`retro_store.py` solved the identical problem for retrospective answers: write
to an append-only file instead of holding state where only one process knows
about it, and let everything else -- including a process that starts back up
after a power cycle -- read the same file. A task here is one more append-only
record, folded to current state the same way.

## Reminders, and who delivers them

Any unfinished task may carry `check_after`. `sweep()` finds the reminders
whose time has come and hands each owner one ordinary message through whatever
transport the team uses -- the same mechanism a teammate's message rides, so a
reminder wakes an agent on any host that can wake an agent for mail.

What sweep() cannot do is call itself. `Supervisor.step()` calls it on every
pass, which covers a team run with `--daemon`. A host that schedules agents
some other way -- a push transport, agents driven directly in a CLI, a
`--say` run that went idle and exited -- must call it too, and
`python -m agyteam.tasks sweep` exists so that any scheduler it already has
can. A reminder nobody sweeps is silent, which is the failure this feature was
built to remove, so it is made loud in three places: `run_until_idle` says how
many reminders it is leaving behind, `agyteam.doctor` warns when reminders are
overdue, and the tool result that sets one says what delivers it.

## Reminders that cannot fire are refused, not stored

The first version stored `check_after` verbatim and compared strings. "1 hour"
fired at once; "2H" and a timestamp without zero-padding waited until the next
year; a time without a zone was quietly read as UTC. Each of those is a
reminder that looks set and is not. Now a reminder is parsed when it is set --
a duration, or an ISO-8601 time that names its zone -- and stored normalised,
or the call fails with a sentence saying what would have worked. A value that
still cannot be read (written by an older version, or by hand) fires at the
next sweep rather than never: early and noisy beats silent.

## Why no separate project file

A project is a grouping string on a task, not a second record with its own
lifecycle. Two append-only logs that must agree with each other is a
consistency problem this does not need to have.
"""
import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import filelock

FILENAME = "tasks.jsonl"

STATUSES = {"queued", "claimed", "running", "blocked", "done", "failed", "cancelled"}
TERMINAL = {"done", "failed", "cancelled"}

_KIND_CREATED = "created"
_KIND_UPDATED = "updated"
_KIND_SWEPT = "swept"

# Fields an "updated" or "swept" entry may carry into the folded state. `id`,
# `kind`, `ts` and `by` describe the log entry, not the task.
_MUTABLE_FIELDS = ("project", "title", "owner", "status", "note", "check_after",
                   "collaborators", "requires_review", "evidence", "until",
                   "poll_every", "deadline")

# What an agent may call a status and what it is here. The allowed set was
# discoverable only by trial -- update_task(status="in_progress") was refused
# with a list -- so the obvious spellings are accepted and normalised.
STATUS_ALIASES = {"in_progress": "running", "in-progress": "running",
                  "started": "running", "active": "running",
                  "waiting": "blocked", "paused": "blocked",
                  "complete": "done", "completed": "done", "finished": "done",
                  "closed": "done", "canceled": "cancelled", "todo": "queued",
                  "open": "queued", "pending": "queued"}


def normalize_status(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    return STATUS_ALIASES.get(s, s)

_FMT = "%Y-%m-%dT%H:%M:%SZ"
_CANONICAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)\s*([a-z]+)$", re.IGNORECASE)
_UNIT_SECONDS = {}
for _names, _secs in ((("s", "sec", "secs", "second", "seconds"), 1),
                      (("m", "min", "mins", "minute", "minutes"), 60),
                      (("h", "hr", "hrs", "hour", "hours"), 3600),
                      (("d", "day", "days"), 86400),
                      (("w", "wk", "wks", "week", "weeks"), 604800)):
    _UNIT_SECONDS.update(dict.fromkeys(_names, _secs))

WHEN_HELP = ("a duration like '90m', '2h', '1.5 hours' or '1d', or an "
             "ISO-8601 time that names its zone, like '2026-09-26T05:00:00Z' "
             "or '2026-09-26T05:00:00-07:00'")


class TaskError(ValueError):
    """A refusal the caller can act on, phrased as a sentence."""


def path(team_dir: Path | str) -> Path:
    return Path(team_dir) / FILENAME


def _now() -> str:
    return time.strftime(_FMT, time.gmtime())


def parse_when(raw: str, now: float | None = None) -> str:
    """A reminder time, normalised to UTC `YYYY-MM-DDTHH:MM:SSZ`, or TaskError.

    Durations are relative to `now` (epoch seconds, default the present).
    A timestamp without a zone is refused rather than guessed: "05:00" means
    something different on every host that reads it, and whichever guess is
    wrong moves the reminder by hours without anyone noticing.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise TaskError(f"check_after is empty; give {WHEN_HELP}")
    s = raw.strip()
    m = _DURATION.match(s)
    if m and m.group(2).lower() in _UNIT_SECONDS:
        secs = float(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]
        base = time.time() if now is None else now
        return time.strftime(_FMT, time.gmtime(base + secs))
    iso = s[:-1] + "+00:00" if s[-1:] in ("z", "Z") else s
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        raise TaskError(f"check_after {raw!r} is not a time this can read; "
                        f"give {WHEN_HELP}") from None
    if dt.tzinfo is None:
        raise TaskError(f"check_after {raw!r} has no timezone, so it would fire "
                        f"at a different moment on every host; add 'Z' for UTC "
                        f"or an offset like '-07:00'")
    return dt.astimezone(timezone.utc).strftime(_FMT)


def parse_duration(raw: str) -> str:
    """A duration shorthand, validated and returned as written ('90m')."""
    if not isinstance(raw, str) or not raw.strip():
        raise TaskError("poll_every is empty; give a duration like '10m' or '1h'")
    m = _DURATION.match(raw.strip())
    if not m or m.group(2).lower() not in _UNIT_SECONDS:
        raise TaskError(f"poll_every {raw!r} is not a duration; give e.g. '10m' or '1h'")
    return raw.strip()


def duration_seconds(raw: str) -> float:
    m = _DURATION.match(raw.strip())
    return float(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]


# --- waiting on something external ------------------------------------------
#
# Twenty wakes in seventy minutes to learn "still running" twenty times.
# A task may instead carry a condition the sweep checks itself, in-process,
# and the owner is woken once: when it holds, or when the deadline passes.
# Conditions read files and process tables; none runs a command, because
# the sweep runs with the supervisor's privileges and a command an agent
# wrote would run with them too.

UNTIL_KINDS = ("file_exists", "file_contains", "pid_exited")


def parse_until(raw: str) -> dict:
    """'file_exists:/path', 'file_contains:/path:text', 'pid_exited:1234'."""
    kind, _, rest = (raw or "").strip().partition(":")
    kind = kind.strip().lower()
    if kind == "file_exists" and rest:
        return {"type": kind, "path": rest.strip()}
    if kind == "file_contains" and rest:
        path, _, text = rest.partition(":")
        if path.strip() and text:
            return {"type": kind, "path": path.strip(), "text": text}
    if kind == "pid_exited" and rest.strip().isdigit():
        return {"type": kind, "pid": int(rest.strip())}
    raise TaskError(f"until {raw!r} is not a condition this can check; give "
                    f"'file_exists:/path', 'file_contains:/path:text' or "
                    f"'pid_exited:1234'")


def validate_until(until: dict) -> None:
    if not isinstance(until, dict) or until.get("type") not in UNTIL_KINDS:
        raise TaskError(f"until must be one of {', '.join(UNTIL_KINDS)}")


def satisfied(until: dict) -> bool:
    """Does the condition hold right now? Any error reads as 'not yet'."""
    try:
        t = until.get("type")
        if t == "file_exists":
            return Path(until["path"]).expanduser().exists()
        if t == "file_contains":
            return until["text"] in Path(until["path"]).expanduser().read_text(
                encoding="utf-8", errors="replace")
        if t == "pid_exited":
            try:
                os.kill(int(until["pid"]), 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            return False
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return False


# --- the log --------------------------------------------------------------

def _append(team_dir: Path | str, entry: dict) -> None:
    p = path(team_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read(team_dir: Path | str) -> list[dict]:
    """Every log entry in file order. Unparseable lines are skipped, not fatal --
    a task log a person can still read after a bad line is more useful than one
    that refuses to load. `agyteam.doctor` reports them."""
    try:
        text = path(team_dir).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("id"):
            out.append(rec)
    return out


def snapshot(team_dir: Path | str) -> dict[str, dict]:
    """Current state per task id, folded from the log in order.

    This is the whole state. A process that just started, reading a log
    written by one that no longer exists, arrives at the same answer a process
    that had been running the entire time would have -- which is what
    "return to a project after a power cycle" means here.
    """
    state: dict[str, dict] = {}
    for rec in read(team_dir):
        tid = rec["id"]
        if rec.get("kind", _KIND_CREATED) == _KIND_CREATED:
            state[tid] = {k: rec.get(k) for k in
                          ("id", "project", "title", "owner", "status",
                           "created_by", "note", "check_after", "collaborators",
                           "requires_review", "evidence", "until", "poll_every",
                           "deadline")}
            state[tid]["created_at"] = rec.get("ts")
            state[tid]["updated_at"] = rec.get("ts")
        elif tid in state:
            # A mutation naming a task never created is dropped, not invented.
            for k in _MUTABLE_FIELDS:
                if k in rec:
                    state[tid][k] = rec[k]
            state[tid]["updated_at"] = rec.get("ts")
    return state


def get(team_dir: Path | str, task_id: str) -> dict | None:
    return snapshot(team_dir).get(task_id)


# --- mutations: each one decides and writes under the same lock -------------

def create(team_dir: Path | str, project: str, title: str,
           owner: str | None = None, created_by: str = "",
           note: str = "", check_after: str | None = None,
           collaborators: list[str] | None = None, requires_review: bool = False,
           until: dict | None = None, poll_every: str | None = None,
           deadline: str | None = None) -> dict:
    """Append a new task and return its current state."""
    if not project or not project.strip():
        raise TaskError("project is required")
    if not title or not title.strip():
        raise TaskError("title is required")
    if until:
        validate_until(until)
    entry = {
        "id": "task_" + uuid.uuid4().hex[:8],
        "kind": _KIND_CREATED,
        "ts": _now(),
        "project": project.strip(),
        "title": title.strip(),
        "owner": owner,
        "status": "queued",
        "created_by": created_by,
        "note": note.strip() if note else "",
        "check_after": parse_when(check_after) if check_after else None,
        "collaborators": [c for c in (collaborators or []) if c and c != owner],
        "requires_review": bool(requires_review),
        "evidence": None,
        "until": until or None,
        "poll_every": parse_duration(poll_every) if poll_every else None,
        "deadline": parse_when(deadline) if deadline else None,
    }
    if until and not entry["check_after"]:
        # A condition is checked from the next sweep on, not left to wait
        # for a reminder nobody set.
        entry["check_after"] = _now()
    with filelock.locked(path(team_dir)):
        _append(team_dir, entry)
    return get(team_dir, entry["id"])


def claim(team_dir: Path | str, task_id: str, agent: str) -> dict | None:
    """Take ownership. None if no such task; TaskError if someone else owns it.

    The check and the write happen under one lock, so of any number of agents
    claiming at once, exactly one is told it won.
    """
    with filelock.locked(path(team_dir)):
        current = snapshot(team_dir).get(task_id)
        if current is None:
            return None
        if current.get("owner") and current["owner"] != agent:
            raise TaskError(f"task {task_id} is already owned by "
                            f"'{current['owner']}'; they can hand it to you "
                            f"with update_task(owner='{agent}')")
        _append(team_dir, {"id": task_id, "kind": _KIND_UPDATED, "ts": _now(),
                           "by": agent, "owner": agent, "status": "claimed"})
    return get(team_dir, task_id)


def update(team_dir: Path | str, task_id: str, by: str, *,
           status: str | None = None, owner: str | None = None,
           check_after: str | None = None, clear_check_after: bool = False,
           note: str | None = None, evidence: str | None = None,
           collaborators: list[str] | None = None,
           until: dict | None = None, poll_every: str | None = None,
           deadline: str | None = None) -> dict | None:
    """Append a mutation by `by`. None if task_id is unknown.

    Only the owner may change an owned task, and passing `owner` is how they
    hand it to someone else. `check_after=None` leaves any reminder alone;
    `clear_check_after=True` removes it. Finishing a task removes its reminder,
    because a reminder on finished work would only ever wake someone to read
    that it was finished.
    """
    status = normalize_status(status)
    if status is not None and status not in STATUSES:
        raise TaskError(f"unknown status {status!r}; use one of "
                        f"{', '.join(sorted(STATUSES))}")
    when = parse_when(check_after) if check_after is not None else None
    if until:
        validate_until(until)
    every = parse_duration(poll_every) if poll_every else None
    due_by = parse_when(deadline) if deadline else None
    with filelock.locked(path(team_dir)):
        current = snapshot(team_dir).get(task_id)
        if current is None:
            return None
        if current.get("owner") and current["owner"] != by:
            raise TaskError(f"task {task_id} is owned by '{current['owner']}', "
                            f"not '{by}'")
        final_status = status or current.get("status")
        if when and final_status in TERMINAL:
            raise TaskError(f"task {task_id} is {final_status}; a reminder on "
                            f"it would never fire")
        entry = {"id": task_id, "kind": _KIND_UPDATED, "ts": _now(), "by": by}
        if status is not None:
            entry["status"] = status
        if owner is not None:
            entry["owner"] = owner
        if final_status in TERMINAL or clear_check_after:
            entry["check_after"] = None
        elif when:
            entry["check_after"] = when
        if note is not None:
            entry["note"] = note.strip()
        if evidence is not None:
            entry["evidence"] = evidence.strip()
        if collaborators is not None:
            entry["collaborators"] = [c for c in collaborators if c]
        if until:
            entry["until"] = until
            if not when and not current.get("check_after"):
                entry["check_after"] = _now()
        if every:
            entry["poll_every"] = every
        if due_by:
            entry["deadline"] = due_by
        _append(team_dir, entry)
    return get(team_dir, task_id)


def complete(team_dir: Path | str, task_id: str, by: str, note: str,
             evidence: str | None = None) -> dict | None:
    """Close a task, with the closing note it must carry.

    Three closed tasks still read "actively triaging..." because closing did
    not ask for a note and the fold kept the old one. A joint task was closed
    by one owner ten minutes before the other's result landed. A deliverable
    reached the user before its review existed. So: a note, every
    collaborator seen working since the task was created, and -- where the
    task says so -- an approved review that names it.
    """
    if not note or not note.strip():
        raise TaskError("a closing note is required: what was done and where "
                        "the result is")
    current = get(team_dir, task_id)
    if current is None:
        return None
    if current.get("owner") and current["owner"] != by:
        raise TaskError(f"task {task_id} is owned by '{current['owner']}', not '{by}'")
    from .activity import author_activity
    idle = []
    for c in current.get("collaborators") or []:
        verifiable, evidence_of = author_activity(Path(team_dir), c, current.get("created_at") or "")
        if verifiable and not evidence_of:
            idle.append(c)
    if idle:
        raise TaskError(f"task {task_id} lists {', '.join(idle)} as collaborator(s) "
                        f"with no recorded activity since it was created; their "
                        f"part has not landed. Wait for it, or remove them with "
                        f"update_task(collaborators=...)")
    if current.get("requires_review") and not approved_review_for(team_dir, task_id):
        raise TaskError(f"task {task_id} requires an approved review before it "
                        f"closes, and none names it. Have a teammate "
                        f"record_review(..., task_id='{task_id}') first")
    return update(team_dir, task_id, by, status="done", note=note, evidence=evidence)


def approved_review_for(team_dir: Path | str, task_id: str) -> dict | None:
    """The most recent approved work review naming this task, or the
    operator's waiver of one, or None."""
    from .activity import jsonl
    found = None
    for r in jsonl(Path(team_dir) / "reviews.jsonl"):
        if r.get("task_id") != task_id:
            continue
        if (r.get("verdict") == "approved" and r.get("kind", "work") == "work") \
                or r.get("kind") == "waiver":
            found = r
    return found


def waive_review(team_dir: Path | str, task_id: str, by: str = "user",
                 reason: str = "") -> dict:
    """The operator's "ship it without review", on the record.

    Written as its own kind in reviews.jsonl, so it satisfies the
    requires_review gate without ever being counted as a review: the report
    and the retrospective read kind == "work" and never see it. It is a
    command and not a tool argument so that no agent is offered it.

    How far "only the operator" holds depends on the runtime. Where agents
    are contained they cannot run this or write the team directory. Where
    they have an unconfined shell (the agy CLI), nothing can authenticate the
    caller: this refuses a waiver signed by a teammate and one issued from
    inside an agent's session (the runners export AGYTEAM_AGENT to everything
    an agent starts), which stops an agent that reaches for the command and
    not one that sets out to forge it. Add the command to
    policy.forbidden_commands where a pre-tool hook runs.
    """
    current = get(team_dir, task_id)
    if current is None:
        raise TaskError(f"no task with id '{task_id}'")
    inside = os.environ.get("AGYTEAM_AGENT", "").strip()
    if inside:
        raise TaskError(f"a waiver is the operator's, and this is {inside}'s "
                        f"session; ask the operator to waive it")
    try:
        doc = json.loads((Path(team_dir) / "roster.json").read_text(encoding="utf-8"))
        teammates = {a.get("name") for a in doc.get("agents", []) if isinstance(a, dict)}
    except (OSError, ValueError, AttributeError):
        teammates = set()
    teammates.add(current.get("owner"))
    if not by or not by.strip() or by.strip() in teammates:
        raise TaskError("a waiver is the operator's, not a teammate's; pass "
                        "--by with who is waiving")
    entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "waiver",
             "verdict": "waived", "reviewer": by.strip(), "author": current.get("owner"),
             "task_id": task_id, "what": current.get("title"),
             "findings": reason.strip() or "review waived by the operator"}
    path_ = Path(team_dir) / "reviews.jsonl"
    with filelock.locked(path_):
        with path_.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    return entry


def list_tasks(team_dir: Path | str, project: str | None = None,
               owner: str | None = None, status: str | None = None) -> list[dict]:
    rows = list(snapshot(team_dir).values())
    if project:
        rows = [r for r in rows if r.get("project") == project]
    if owner:
        rows = [r for r in rows if r.get("owner") == owner]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: r.get("updated_at") or "")
    return rows


# --- reminders --------------------------------------------------------------

def reminders(team_dir: Path | str) -> list[dict]:
    """Unfinished tasks that carry a reminder, due or not."""
    return [r for r in snapshot(team_dir).values()
            if r.get("status") not in TERMINAL and r.get("check_after")]


def unreadable(task: dict) -> bool:
    ca = task.get("check_after")
    return bool(ca) and not (isinstance(ca, str) and _CANONICAL.match(ca))


def _due_in(state: dict[str, dict], now: str) -> list[dict]:
    # Canonical UTC timestamps sort lexically in time order, so this is a
    # string comparison. Anything not canonical fires now rather than never.
    return [r for r in state.values()
            if r.get("status") not in TERMINAL and r.get("check_after")
            and (unreadable(r) or r["check_after"] <= now)]


def due(team_dir: Path | str, now: str | None = None) -> list[dict]:
    """Unfinished tasks whose reminder has come due."""
    return _due_in(snapshot(team_dir), now or _now())


def _describe(until: dict) -> str:
    t = until.get("type")
    if t == "file_exists":
        return f"{until.get('path')} exists"
    if t == "file_contains":
        return f"{until.get('path')} contains {until.get('text')!r}"
    if t == "pid_exited":
        return f"process {until.get('pid')} exited"
    return str(until)


def reminder_text(owed: list[dict]) -> str:
    lines = []
    for t in owed:
        line = f"- {t['id']} [{t.get('status')}] ({t.get('project')}): {t.get('title')}"
        if t.get("note"):
            line += f" — {t['note']}"
        if unreadable(t):
            line += (f" (its reminder time {t['check_after']!r} could not be "
                     f"read, so it fired now; set a new one if you still "
                     f"need it)")
        lines.append(line)
    plural = "reminder has" if len(owed) == 1 else "reminders have"
    return (f"{len(owed)} {plural} come due on tasks you own:\n"
            + "\n".join(lines)
            + "\n\nCheck on them now. Set a new check_after to be reminded "
              "again, or complete_task when one is finished.")


# (path, mtime_ns, size) -> earliest reminder in that version of the file.
# Lets an idle poll skip the read entirely: the log is only re-read when it
# has changed or when a reminder it already knew about has come due.
_quiet: dict[str, tuple] = {}


def sweep(team_dir: Path | str, send, owners=None,
          now: str | None = None) -> dict[str, list[dict]]:
    """Deliver every due reminder once, through `send(to, content) -> str`.

    `send` is a transport's send; a result starting with "[error" or an
    exception leaves that owner's reminders armed for the next sweep, so a
    reminder is marked delivered only after the message went out. `owners`,
    if given, limits delivery to those names -- a supervisor passes the agents
    it can actually wake. Returns {owner: [tasks delivered]}.

    Safe to call from more than one process: the decision and the marking
    happen under the log's lock, so each reminder is delivered once.
    """
    p = path(team_dir)
    now = now or _now()
    try:
        st = p.stat()
    except OSError:
        return {}
    key = str(p)
    cached = _quiet.get(key)
    if (cached and cached[:2] == (st.st_mtime_ns, st.st_size)
            and (cached[2] is None or now < cached[2])):
        return {}

    delivered: dict[str, list[dict]] = {}
    with filelock.locked(p):
        state = snapshot(team_dir)
        by_owner: dict[str, list[dict]] = {}
        for t in _due_in(state, now):
            o = t.get("owner")
            if not o or (owners is not None and o not in owners):
                continue
            until = t.get("until")
            if until and not unreadable(t):
                if satisfied(until):
                    t = dict(t, note=(t.get("note") or "") +
                             f" [condition met: {_describe(until)}]")
                elif t.get("deadline") and t["deadline"] <= now:
                    t = dict(t, note=(t.get("note") or "") +
                             f" [deadline {t['deadline']} passed; condition NOT met: "
                             f"{_describe(until)}]")
                else:
                    # Not yet: look again later, and wake nobody.
                    every = t.get("poll_every") or "5m"
                    nxt = time.strftime(_FMT, time.gmtime(
                        time.time() + duration_seconds(every)))
                    _append(team_dir, {"id": t["id"], "kind": _KIND_SWEPT, "ts": now,
                                       "check_after": nxt})
                    continue
            by_owner.setdefault(o, []).append(t)
        for owner, owed in by_owner.items():
            try:
                result = send(owner, reminder_text(owed))
            except Exception:
                continue
            if isinstance(result, str) and result.lstrip().startswith("[error"):
                continue
            for t in owed:
                _append(team_dir, {"id": t["id"], "kind": _KIND_SWEPT,
                                   "ts": now, "check_after": None})
            delivered[owner] = owed
        remaining = [r for r in snapshot(team_dir).values()
                     if r.get("status") not in TERMINAL and r.get("check_after")]
        if not remaining:
            earliest = None
        elif any(unreadable(r) for r in remaining):
            earliest = ""           # never skip: those are due every pass
        else:
            earliest = min(r["check_after"] for r in remaining)
        try:
            st = p.stat()
            _quiet[key] = (st.st_mtime_ns, st.st_size, earliest)
        except OSError:
            _quiet.pop(key, None)
    return delivered


# --- command line -------------------------------------------------------------

def main(argv=None) -> int:
    """`python -m agyteam.tasks sweep|list` -- for hosts the supervisor does not drive.

    `sweep` is the whole of what a supervisor does for reminders, runnable by
    any scheduler a host already has (a systemd timer, cron, a loop in the
    host's own daemon): one entry for the whole team, not one per agent.
    """
    ap = argparse.ArgumentParser(prog="agyteam.tasks")
    ap.add_argument("command", choices=("sweep", "list", "waive"))
    ap.add_argument("task_id", nargs="?", help="for waive: the task")
    ap.add_argument("--by", default="user", help="for waive: who is waiving (default: user)")
    ap.add_argument("--reason", default="", help="for waive: why, for the record")
    ap.add_argument("--team-dir", default=None)
    a = ap.parse_args(argv)

    from . import scope
    td = scope.team_dir(a.team_dir)
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)

    if a.command == "waive":
        if not a.task_id:
            ap.error("waive needs a task id")
        try:
            entry = waive_review(td, a.task_id, by=a.by, reason=a.reason)
        except TaskError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"review waived for {a.task_id} by {entry['reviewer']}: {entry['findings']}")
        return 0

    if a.command == "list":
        for t in list_tasks(td):
            when = f"  check after {t['check_after']}" if t.get("check_after") else ""
            print(f"{t['id']}  {t.get('status'):<9} {t.get('owner') or '-':<10} "
                  f"({t.get('project')}) {t.get('title')}{when}")
        return 0

    from .transport import load as load_transport
    bus = load_transport("supervisor")
    try:
        delivered = sweep(td, bus.send)
    finally:
        if hasattr(bus, "close"):
            bus.close()
    for owner, owed in delivered.items():
        print(f"reminded {owner}: {', '.join(t['id'] for t in owed)}")
    if not delivered:
        print("no reminders due")
    return 0


if __name__ == "__main__":
    sys.exit(main())
