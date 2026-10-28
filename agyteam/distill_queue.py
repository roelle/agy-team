"""Distillations asked of the supervisor that drives the team.

`--distill coder` from a terminal while a daemon drives the team woke coder
in a second process, alongside whatever turn the daemon had it running:
two turns in one conversation at once, the collision the dispatcher exists
to prevent. The command checked only its own process's turns, and it had
none. Now, when another supervisor drives the team, a distill, cycle or
sweep is queued here, and that supervisor runs it between the agent's turns
and records the result (event "distill_request_done").
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from . import filelock

FILE = "distill_requests.jsonl"
ACTIONS = ("distill", "cycle", "sweep", "review")


def path(team_dir) -> Path:
    return Path(team_dir) / FILE


def add(team_dir, action: str, agent: str | None = None, by: str = "operator",
        **options) -> str:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    rid = uuid.uuid4().hex[:12]
    row = {"id": rid, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action,
           "agent": agent, "by": by, **options}
    p = path(team_dir)
    with filelock.locked(p):
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    return rid


def pending(team_dir) -> list[dict]:
    try:
        lines = path(team_dir).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def take(team_dir, ready) -> list[dict]:
    """Remove and return the requests `ready(row)` says can run now; the
    rest stay queued."""
    p = path(team_dir)
    if not p.exists():
        return []
    with filelock.locked(p):
        rows = pending(team_dir)
        now = [r for r in rows if ready(r)]
        if now:
            later = [r for r in rows if r not in now]
            p.write_text("".join(json.dumps(r) + "\n" for r in later), encoding="utf-8")
        return now


def put_back(team_dir, row: dict) -> None:
    """Queue a request again as it was (same id), for one whose turn did not
    survive a restart: the command that asked is still waiting on that id."""
    p = path(team_dir)
    with filelock.locked(p):
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
