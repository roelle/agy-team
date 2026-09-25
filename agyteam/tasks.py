"""Where long-running work is tracked, so an agent never has to hold it in its head.

## Why this file exists

The team was running simulations that take hours to finish, and the pattern
that emerged around that was an agent scheduling an external cron job to poke
itself later. That works, but it puts the schedule outside every record this
project keeps: `agyteam.doctor` cannot see it, a restart does not know it
existed, and a second agent asking "is anything still running?" has nowhere to
look but the first agent's memory of having set it up.

The fix already exists in this codebase under a different name. `retro_store.py`
solved the identical problem for retrospective answers: write to an append-only
file instead of holding state in a place only one process (or one cron job)
knows about, and let everything else -- including a process that starts back up
after a power cycle -- read the same file. A task here is exactly that: one more
append-only record, folded to current state the same way `retro_store.read()`
folds to `latest_by_agent()`.

## Why a sweep instead of a scheduler

An agent does not need to poll for "is it time yet" -- that is the cron kludge
with extra steps, just moved inside the process. Instead, a task that is
`blocked` may carry a `check_after` timestamp, and `due()` returns exactly the
tasks whose time has come. `Supervisor.step()` calls `due()` on every pass (it
already polls at `self.poll` seconds in `run_forever`) and, for anything due,
sends the owner an ordinary bus message -- the exact mechanism that already
wakes an agent for a teammate's message. No new wake path, no new daemon: a
due task is mail, and `step()` already knows how to deliver mail.

## Why no separate project file

A project is a grouping string on a task (`project`), not a second record with
its own lifecycle. Two append-only logs that must agree with each other is a
consistency problem this file does not need to have; if project-level metadata
(an owner, a description) turns out to be needed later, it can be added as a
field on the first task that names a new project, read the same way `owner` or
`note` already are.
"""
import json
import time
import uuid
from pathlib import Path

FILENAME = "tasks.jsonl"

STATUSES = {"queued", "claimed", "running", "blocked", "done", "failed", "cancelled"}

_KIND_CREATED = "created"
_KIND_UPDATED = "updated"
_KIND_SWEPT = "swept"

# Fields an "updated" or "swept" entry may carry into the folded state. `id`,
# `kind`, `ts` and `by` describe the log entry itself, not the task, and must
# never be merged into it -- an update's own `ts` would otherwise become
# indistinguishable from a task's `check_after` if a caller got the argument
# order wrong.
_MUTABLE_FIELDS = ("project", "title", "owner", "status", "note", "check_after")


def path(team_dir: Path | str) -> Path:
    return Path(team_dir) / FILENAME


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id() -> str:
    return "task_" + uuid.uuid4().hex[:8]


def _append(team_dir: Path | str, entry: dict) -> None:
    p = path(team_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read(team_dir: Path | str) -> list[dict]:
    """Every log entry in file order. Unparseable lines are skipped, not fatal --
    a task log a person can still read after a bad line is more useful than one
    that refuses to load."""
    p = path(team_dir)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
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
    that had been running the entire time would have -- that is what "return
    to a project after a power cycle" means here: there is nothing else to
    restore.
    """
    state: dict[str, dict] = {}
    for rec in read(team_dir):
        tid = rec["id"]
        kind = rec.get("kind", _KIND_CREATED)
        if kind == _KIND_CREATED:
            state[tid] = {k: rec.get(k) for k in
                          ("id", "project", "title", "owner", "status",
                           "created_by", "note", "check_after")}
            state[tid]["created_at"] = rec.get("ts")
            state[tid]["updated_at"] = rec.get("ts")
        elif tid in state:
            # An update naming a task never created is dropped, not invented --
            # the log can only be replayed forward, and a mutation with nothing
            # to mutate is evidence of a bug, not a task the fold should conjure.
            for k in _MUTABLE_FIELDS:
                if k in rec:
                    state[tid][k] = rec[k]
            state[tid]["updated_at"] = rec.get("ts")
    return state


def get(team_dir: Path | str, task_id: str) -> dict | None:
    return snapshot(team_dir).get(task_id)


def create(team_dir: Path | str, project: str, title: str,
           owner: str | None = None, created_by: str = "",
           note: str = "") -> dict:
    """Append a new task and return its current state."""
    if not project or not project.strip():
        raise ValueError("project is required")
    if not title or not title.strip():
        raise ValueError("title is required")
    entry = {
        "id": _new_id(),
        "kind": _KIND_CREATED,
        "ts": _ts(),
        "project": project.strip(),
        "title": title.strip(),
        "owner": owner,
        "status": "queued",
        "created_by": created_by,
        "note": note.strip() if note else "",
        "check_after": None,
    }
    _append(team_dir, entry)
    return get(team_dir, entry["id"])


def update(team_dir: Path | str, task_id: str, by: str, *,
           status: str | None = None, owner: str | None = None,
           check_after: str | None = None, clear_check_after: bool = False,
           note: str | None = None) -> dict | None:
    """Append a mutation. Returns the new state, or None if task_id is unknown.

    `check_after=None` means "leave it alone" (the default, for calls that
    only touch status or note); pass `clear_check_after=True` to explicitly
    null it out once a blocked task has been checked and is not going back to
    sleep. That split exists so a caller cannot clear the field by omitting it.
    """
    if get(team_dir, task_id) is None:
        return None
    if status is not None and status not in STATUSES:
        raise ValueError(f"unknown status {status!r}; use one of {sorted(STATUSES)}")
    entry = {"id": task_id, "kind": _KIND_UPDATED, "ts": _ts(), "by": by}
    if status is not None:
        entry["status"] = status
    if owner is not None:
        entry["owner"] = owner
    if clear_check_after:
        entry["check_after"] = None
    elif check_after is not None:
        entry["check_after"] = check_after
    if note is not None:
        entry["note"] = note.strip()
    _append(team_dir, entry)
    return get(team_dir, task_id)


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


def due(team_dir: Path | str, now: str | None = None) -> list[dict]:
    """Blocked tasks whose check_after has passed -- what the sweep wakes.

    ISO-8601 UTC (`_ts()`'s own format) sorts lexicographically the same as it
    sorts chronologically, so this is a plain string comparison, not a parse.
    """
    now = now or _ts()
    return [r for r in snapshot(team_dir).values()
            if r.get("status") == "blocked" and r.get("check_after")
            and r["check_after"] <= now]


def mark_swept(team_dir: Path | str, task_id: str) -> None:
    """Record that a due task was surfaced, and clear check_after.

    Without this, the same due task would be swept again on every subsequent
    poll tick until the owner sets a new check_after -- one due sim would
    become one message per poll interval instead of one.
    """
    _append(team_dir, {"id": task_id, "kind": _KIND_SWEPT, "ts": _ts(),
                        "check_after": None})
