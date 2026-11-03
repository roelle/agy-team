"""What reaches an agent between the steps of a turn.

A turn can run for many minutes, and two things used to wait for it to end.
Mail: a correction from the user sat in the inbox while the agent finished the
wrong thing, unless it happened to call check_inbox. And a stuck agent: one
making the same call over and over had nobody to tell it so, and spent the
turn's budget learning nothing.

Both are notes put in front of the model at the next step. A note about mail
quotes the start of each message and points at check_inbox, which marks it read
and writes the receipt, so one path still takes mail out of an inbox. A note
about a repeat is advice: the call ran, and nothing stops it running again.
Waking mail only; a quiet channel post waits for a wake as it always has.

Runtimes differ in how a note gets there, so the bookkeeping lives here, on
disk, and each one does only the last step:

    agy CLI           hook_step, before each model call
    Claude            the runner's tool hook, after each tool call
    Antigravity SDK   a pre-tool hook, which can only allow or refuse: a note
                      that cannot wait refuses one call and says why
    any host          the team's own tool results, through mcp_base.serve

The supervisor can leave a note too (leave_note): memories recalled for a
turn after it had to start without them.

Each note is shown once however many of those a runtime has. An agent with no
turn in the supervisor's ledger gets nothing: this is about turns the team is
running, not a person's own session with the plugin installed. Nothing here may
break a turn, so a problem means no note.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import filelock
from .transport import Message
from .transport import load as load_transport

DIR = "steer"
#: Same call this many times running gets a note.
REPEATS = (3, 5, 8)
#: From here a runtime that can only refuse a call may refuse one.
URGENT_REPEAT = 5
#: A person's message always counts as urgent.
HUMAN = "user"
#: What a model writes about a call rather than what the call does. Measured
#: on agy: four `echo hi` calls differed only in toolSummary ("Echo hi fourth
#: time"), so nothing ever looked repeated. Claude's Bash `description` is the same.
COMMENTARY = ("toolSummary", "toolAction", "description")
PREVIEW_CHARS = 300
PREVIEW_MESSAGES = 3
ARGS_CHARS = 300


def _path(team_dir, agent: str) -> Path:
    return Path(team_dir) / DIR / f"{agent}.json"


def turn_of(team_dir, agent: str) -> dict | None:
    """The agent's turn in the supervisor's ledger, or None."""
    try:
        doc = json.loads((Path(team_dir) / "inflight.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    turn = doc.get(agent) if isinstance(doc, dict) else None
    return turn if isinstance(turn, dict) else None


def _mail_key(m: Message) -> str:
    raw = "\0".join(str(x) for x in (m.ts, m.sender, m.kind, m.content))
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def turn_mail(turn: dict | None) -> set[str]:
    """Keys of the mail a turn was started with, which is already in its prompt."""
    out = set()
    for d in (turn or {}).get("msgs") or []:
        try:
            out.add(_mail_key(Message.from_dict(d)))
        except (KeyError, TypeError, AttributeError):
            continue
    return out


def already_read(team_dir, agent: str, msgs: list[Message]) -> list[Message]:
    """`msgs` without what this turn's prompt already showed the agent.

    Pasted mail is acknowledged when the turn ends, so it is still in the
    inbox while the turn runs. Under inbox_pull nothing was pasted. A ledger
    that does not say is from a supervisor older than these servers, and
    hiding mail from an agent is the worse mistake.
    """
    turn = turn_of(team_dir, agent)
    if not turn or turn.get("pull") is not False:
        return msgs
    pasted = turn_mail(turn)
    return [m for m in msgs if _mail_key(m) not in pasted]


def _load(path: Path, turn: dict) -> dict:
    fresh = {"turn": turn.get("ts"), "call": None, "tool": "", "args": "",
             "count": 0, "due": 0, "told": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fresh
    return state if isinstance(state, dict) and state.get("turn") == fresh["turn"] else fresh


def _save(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state), encoding="utf-8")


def count_call(team_dir, agent: str, tool: str, args) -> None:
    """Note one tool call. Only an unbroken run of one call counts as a repeat."""
    try:
        turn = turn_of(team_dir, agent)
        if not turn:
            return
        kept = {k: v for k, v in (args or {}).items() if k not in COMMENTARY}
        text = json.dumps(kept, sort_keys=True, default=str)
        call = hashlib.sha1(f"{tool}\0{text}".encode("utf-8", "replace")).hexdigest()[:16]
        path = _path(team_dir, agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        with filelock.locked(path):
            state = _load(path, turn)
            if state["call"] == call:
                state["count"] += 1
            else:
                state.update(call=call, count=1, due=0, tool=tool, args=text[:ARGS_CHARS])
            if state["count"] in REPEATS:
                state["due"] = state["count"]
            _save(path, state)
    except Exception:
        pass


def _new_mail(team_dir, agent: str, turn: dict, transport) -> list[Message]:
    own = transport or load_transport(agent)
    try:
        waiting = own.peek() or []
    finally:
        if transport is None:
            own.close()
    pasted = turn_mail(turn)
    return [m for m in waiting if m.wakes and _mail_key(m) not in pasted]


def _mail_note(msgs: list[Message]) -> str:
    lines = []
    for m in msgs[:PREVIEW_MESSAGES]:
        text = " ".join(m.content.split())
        cut = text[:PREVIEW_CHARS] + ("..." if len(text) > PREVIEW_CHARS else "")
        where = f" in #{m.channel}" if m.channel else ""
        lines.append(f"from {m.sender}{where}: {cut}")
    more = len(msgs) - PREVIEW_MESSAGES
    if more > 0:
        lines.append(f"and {more} more")
    return ("[New mail while you work.\n" + "\n".join(lines) + "\n"
            "Call check_inbox to read it before you go on. It may change what "
            "you should do next.]")


def _repeat_note(state: dict) -> str:
    n, tool = state["due"], state["tool"]
    if n < URGENT_REPEAT:
        return (f"[You have made the same {tool} call {n} times in a row. Look at "
                f"what it last returned before you call it again: if nothing has "
                f"changed, it will return the same thing.]")
    return (f"[You have made the same {tool} call {n} times in a row, with "
            f"arguments {state['args']}. Change your approach, or end the turn "
            f"and say what is in the way.]")


def take_notes(team_dir, agent: str, transport=None, urgent_only: bool = False) -> str:
    """What the agent should see now, once; "" when there is nothing.

    `urgent_only` is for a runtime that has to refuse a call to say anything:
    a person's mail and a long repeat are worth that, and the rest waits for
    the next team tool result.
    """
    try:
        turn = turn_of(team_dir, agent)
        if not turn:
            return ""
        mail = _new_mail(team_dir, agent, turn, transport)
        path = _path(team_dir, agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        with filelock.locked(path):
            state = _load(path, turn)
            told = set(state["told"])
            fresh = [m for m in mail if _mail_key(m) not in told]
            if urgent_only and not any(m.sender == HUMAN for m in fresh):
                fresh = []
            due = state["due"] >= (URGENT_REPEAT if urgent_only else 1)
            notes = [_repeat_note(state)] if due else []
            if fresh:
                notes.append(_mail_note(fresh))
            left = [] if urgent_only else state.get("notes") or []
            notes += left
            if not notes:
                return ""
            state["told"] = sorted(told | {_mail_key(m) for m in fresh})
            if due:
                state["due"] = 0
            if left:
                state["notes"] = []
            _save(path, state)
        _record(agent, len(fresh), state["count"] if due else 0, len(left))
        return "\n\n".join(notes)
    except Exception:
        return ""


def leave_note(team_dir, agent: str, turn_ts: str, text: str) -> bool:
    """Put `text` in front of the agent at the next step of the turn that
    began at `turn_ts`. False, and nothing kept, when that turn has ended."""
    try:
        turn = turn_of(team_dir, agent)
        if not turn or turn.get("ts") != turn_ts:
            return False
        path = _path(team_dir, agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        with filelock.locked(path):
            state = _load(path, turn)
            state["notes"] = (state.get("notes") or []) + [text]
            _save(path, state)
        return True
    except Exception:
        return False


def _record(agent: str, mail: int, repeat: int, left: int = 0) -> None:
    try:
        from . import observer
        extra = {"left": left} if left else {}
        observer.load().record_event("steer", agent=agent, mail=mail, repeat=repeat, **extra)
    except Exception:
        pass
