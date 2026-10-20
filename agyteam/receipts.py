"""What an agent took from its inbox during a supervised turn.

Under inbox_pull an agent reads its mail with check_inbox, which marks it
read as it goes -- it has to, or the next page would be the same page. But
a turn can still fail after that: a crash, a 503, a timeout. The mail it
pulled, including mail that arrived while the turn was running, was gone.

So the supervisor opens a receipt when it starts a pull-mode turn, and
check_inbox writes down each message before marking it read. A turn that
succeeds closes its receipt. A turn that fails gives back what it took and
did not leave in the inbox. A receipt exists only while a supervised turn
is open, so an agent run by hand, with no supervisor, keeps none.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import filelock
from .transport import Message

DIR = "receipts"


def path(team_dir, agent: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", agent)
    return Path(team_dir) / DIR / f"{safe}.jsonl"


def open_turn(team_dir, agent: str) -> None:
    p = path(team_dir, agent)
    p.parent.mkdir(parents=True, exist_ok=True)
    with filelock.locked(p):
        p.write_text("", encoding="utf-8")


def record(team_dir, agent: str, msgs) -> None:
    """Write down `msgs` as taken, if a supervised turn is open. Called
    before they are marked read."""
    p = path(team_dir, agent)
    if not msgs or not p.exists():
        return
    with filelock.locked(p):
        if not p.exists():
            return
        with p.open("a", encoding="utf-8") as f:
            for m in msgs:
                f.write(json.dumps(m.as_dict()) + "\n")


def taken(team_dir, agent: str) -> list[Message]:
    try:
        text = path(team_dir, agent).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            out.append(Message.from_dict(json.loads(line)))
        except (ValueError, TypeError):
            continue
    return out


def close_turn(team_dir, agent: str) -> None:
    p = path(team_dir, agent)
    with filelock.locked(p):
        try:
            p.unlink()
        except OSError:
            pass
