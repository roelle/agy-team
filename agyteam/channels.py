"""Channels: shared rooms of agents (and the user) inside one team.

A channel has members. A post to it is logged once and copied to each
member's inbox. Posting does not wake anyone by itself: a member's copy is
quiet unless the post mentions them,

    @coder   that agent
    @here    every agent in the channel
    @all     every agent on the roster, members or not

and a quiet copy waits in the inbox until something else wakes its reader --
then it is read with everything else unread. So a busy channel costs no
turns, and a woken agent still sees what was said in its channels while it
was away.

A thread is the id of the message it hangs off; replies carry it.

Membership lives in <team_dir>/channels.json. A team with no channels file
has one implicit channel, "general", with every roster agent in it.

The user is not a member: the user reads every channel from the record
(bus.jsonl), and is never woken.
"""
import json
import os
import re
import tempfile
import time
from pathlib import Path

from . import filelock
from . import roster as roster_lib

FILE = "channels.json"
DEFAULT = "general"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9][\w-]*)")


class ChannelError(ValueError):
    pass


def path(team_dir) -> Path:
    return Path(team_dir) / FILE


def _roster_agents(team_dir) -> list[str]:
    try:
        doc = json.loads((Path(team_dir) / "roster.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [a["name"] for a in doc.get("agents", []) if isinstance(a, dict) and a.get("name")]


def load(team_dir) -> dict[str, dict]:
    """{name: {"members": [...], "topic": str, "created": ts}}."""
    try:
        doc = json.loads(path(team_dir).read_text(encoding="utf-8"))
        chans = doc.get("channels", {}) if isinstance(doc, dict) else {}
        if isinstance(chans, dict) and chans:
            return chans
    except (OSError, ValueError):
        pass
    return {DEFAULT: {"members": _roster_agents(team_dir), "topic": "everyone",
                      "created": None, "implicit": True}}


def _save(team_dir, chans: dict) -> None:
    p = path(team_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"channels": chans}, indent=2) + "\n")
    os.replace(tmp, p)


def _edit(team_dir, fn):
    with filelock.locked(path(team_dir)):
        chans = {k: v for k, v in load(team_dir).items()}
        for v in chans.values():
            v.pop("implicit", None)
        out = fn(chans)
        _save(team_dir, chans)
        return out


def create(team_dir, name: str, members: list[str] | None = None, topic: str = "") -> dict:
    name = (name or "").strip().lstrip("#").lower()
    if not _NAME.match(name):
        raise ChannelError("a channel name is lowercase letters, digits, - and _, "
                           "at most 40, e.g. 'billing' or 'sim-runs'")
    roster = set(_roster_agents(team_dir))
    unknown = sorted(set(members or []) - roster)
    if unknown:
        raise ChannelError(f"not on the roster: {', '.join(unknown)}")

    def do(chans):
        if name in chans:
            raise ChannelError(f"#{name} already exists")
        chans[name] = {"members": sorted(set(members or [])), "topic": topic.strip(),
                       "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        return chans[name]
    return _edit(team_dir, do)


def set_members(team_dir, name: str, add=(), remove=()) -> list[str]:
    roster = set(_roster_agents(team_dir))
    unknown = sorted(set(add) - roster)
    if unknown:
        raise ChannelError(f"not on the roster: {', '.join(unknown)}")

    def do(chans):
        if name not in chans:
            raise ChannelError(f"no channel #{name}")
        members = (set(chans[name].get("members", [])) | set(add)) - set(remove)
        chans[name]["members"] = sorted(members)
        return chans[name]["members"]
    return _edit(team_dir, do)


def set_topic(team_dir, name: str, topic: str) -> None:
    def do(chans):
        if name not in chans:
            raise ChannelError(f"no channel #{name}")
        chans[name]["topic"] = topic.strip()
    _edit(team_dir, do)


def members(team_dir, name: str) -> list[str]:
    chans = load(team_dir)
    if name not in chans:
        raise ChannelError(f"no channel #{name}; channels: "
                           f"{', '.join('#' + c for c in sorted(chans))}")
    return list(chans[name].get("members", []))


def of(team_dir, agent: str) -> list[str]:
    """The channels `agent` is a member of."""
    return sorted(n for n, c in load(team_dir).items() if agent in c.get("members", []))


def mentioned(content: str, channel_members: list[str], roster: list[str]) -> set[str]:
    """Who a post wakes: named agents on the roster, @here, @all. A name
    may drop the team prefix or its case (@Ada wakes at-Ada) when that
    leaves exactly one agent it can mean."""
    out: set[str] = set()
    known = set(roster)
    for name in _MENTION.findall(content or ""):
        low = name.lower()
        if low == "here":
            out |= set(channel_members)
        elif low in ("all", "everyone", "channel"):
            out |= known
        else:
            agent = roster_lib.resolve_name(name, roster)
            if agent:
                out.add(agent)
    return out


def post(transport, team_dir, channel: str, content: str, kind: str = "work",
         thread: str | None = None, task_id: str | None = None,
         author: str | None = None) -> dict:
    """Post as `transport.me`. Returns {"id", "channel", "woke": [...]}.

    An agent may post only to a channel it belongs to; the user may post
    anywhere. @all reaches roster agents outside the channel too: they get a
    copy, which wakes them.
    """
    chan = (channel or "").strip().lstrip("#")
    who = transport.me
    chan_members = members(team_dir, chan)
    if who != "user" and who not in chan_members:
        raise ChannelError(f"{who} is not in #{chan}; members: "
                           f"{', '.join(chan_members) or 'nobody'}")
    roster = _roster_agents(team_dir)
    woke = mentioned(content, chan_members, roster) - {who}
    recipients = sorted((set(chan_members) | woke) - {who})
    post_id = transport.post(chan, recipients, content, kind=kind, mentioned=woke,
                             task_id=task_id, thread=thread, author=author)
    return {"id": post_id, "channel": chan, "woke": sorted(woke),
            "delivered_to": recipients}
