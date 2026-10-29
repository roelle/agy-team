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

A channel can also name members that every post wakes, mention or not
(its "wake" list): an agent that owns a channel's work -- docs in #docs --
picks up whatever is posted there. A post never wakes its own author, and
quiet kinds (fyi, status, ack) wake nobody.

A thread is the id of the message it hangs off; replies carry it.

Membership lives in <team_dir>/channels.json. A team with no channels file
has one implicit channel, "general", with every roster agent in it.

The user is not a member: the user reads every channel from the record
(bus.jsonl), and is never woken. An @handle of the operator's (roster
`operator`) is recorded in the post's `mentions` as "user", so a client can
show it as a mention; it wakes nobody.

A channel can be archived -- read-only, out of every agent's list, kept --
and restored. Deleting one is archiving it and hiding its history from
clients: its messages stay in bus.jsonl, which is the team's record, and a
deleted name cannot be reused until it is restored.
"""
import inspect
import json
import os
import re
import tempfile
import time
from pathlib import Path

from . import filelock
from . import roster as roster_lib
from .transport import wakes

FILE = "channels.json"
DEFAULT = "general"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9](?:[\w-]|\.(?=\w))*)")


class ChannelError(ValueError):
    pass


def path(team_dir) -> Path:
    return Path(team_dir) / FILE


def _roster_doc(team_dir) -> dict:
    try:
        doc = json.loads((Path(team_dir) / "roster.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _roster_agents(team_dir) -> list[str]:
    doc = _roster_doc(team_dir)
    return [a["name"] for a in doc.get("agents", []) if isinstance(a, dict) and a.get("name")]


def closed(chan: dict) -> str | None:
    """"deleted" or "archived" if the channel is either, else None."""
    return "deleted" if chan.get("deleted") else "archived" if chan.get("archived") else None


def active(team_dir) -> dict[str, dict]:
    """The channels agents can see and post to: not archived, not deleted."""
    return {n: c for n, c in load(team_dir).items() if not closed(c)}


def load(team_dir) -> dict[str, dict]:
    """{name: {"members": [...], "topic": str, "created": ts}}.

    A channel with "everyone": true has the whole roster as members, kept
    up to date: an agent added later is in it without anyone ticking a box.
    """
    try:
        doc = json.loads(path(team_dir).read_text(encoding="utf-8"))
        chans = doc.get("channels", {}) if isinstance(doc, dict) else {}
        if isinstance(chans, dict) and chans:
            if any(c.get("everyone") for c in chans.values() if isinstance(c, dict)):
                roster = _roster_agents(team_dir)
                for c in chans.values():
                    if isinstance(c, dict) and c.get("everyone"):
                        c["members"] = sorted(roster)
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
            # The implicit #general is everyone; written down, it stays so.
            if v.pop("implicit", None):
                v["everyone"] = True
        out = fn(chans)
        _save(team_dir, chans)
        return out


def create(team_dir, name: str, members: list[str] | None = None, topic: str = "",
           everyone: bool = False) -> dict:
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
            state = closed(chans[name])
            raise ChannelError(f"#{name} already exists" + (
                f" ({state}; restore it, or pick another name)" if state else ""))
        chans[name] = {"members": sorted(roster if everyone else set(members or [])),
                       "topic": topic.strip(),
                       "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        if everyone:
            chans[name]["everyone"] = True
        return chans[name]
    return _edit(team_dir, do)


def set_members(team_dir, name: str, add=(), remove=(),
                everyone: bool | None = None) -> list[str]:
    """Add and remove members. Removing anyone from an "everyone" channel
    makes its list explicit; everyone=True makes it follow the roster."""
    roster = set(_roster_agents(team_dir))
    unknown = sorted(set(add) - roster)
    if unknown:
        raise ChannelError(f"not on the roster: {', '.join(unknown)}")

    def do(chans):
        if name not in chans:
            raise ChannelError(f"no channel #{name}")
        if everyone:
            chans[name]["everyone"] = True
            chans[name]["members"] = sorted(roster)
            return chans[name]["members"]
        if remove or everyone is False:
            chans[name].pop("everyone", None)
        members = (set(chans[name].get("members", [])) | set(add)) - set(remove)
        chans[name]["members"] = sorted(members)
        wake = set(chans[name].pop("wake", None) or ()) & members
        if wake:                            # only members are woken by every post
            chans[name]["wake"] = sorted(wake)
        return chans[name]["members"]
    return _edit(team_dir, do)


def set_wake(team_dir, name: str, agents) -> list[str]:
    """Which members every post to the channel wakes, mention or not."""
    agents = sorted(set(agents or ()))

    def do(chans):
        if name not in chans:
            raise ChannelError(f"no channel #{name}")
        outside = [a for a in agents if a not in chans[name].get("members", [])]
        if outside:
            raise ChannelError(f"not in #{name}: {', '.join(outside)}; add them first")
        if agents:
            chans[name]["wake"] = agents
        else:
            chans[name].pop("wake", None)
        return agents
    return _edit(team_dir, do)


def set_topic(team_dir, name: str, topic: str) -> None:
    def do(chans):
        if name not in chans:
            raise ChannelError(f"no channel #{name}")
        chans[name]["topic"] = topic.strip()
    _edit(team_dir, do)


def archive(team_dir, name: str, by: str = "user") -> dict:
    """Read-only and out of every list; reversible with restore()."""
    def do(chans):
        if name not in chans:
            raise ChannelError(f"no channel #{name}")
        chans[name]["archived"] = time.strftime("%Y-%m-%d %H:%M:%S")
        chans[name]["archived_by"] = by
        return chans[name]
    return _edit(team_dir, do)


def delete(team_dir, name: str, by: str = "user") -> dict:
    """Archive, and hide the history from clients. The messages stay in
    bus.jsonl: that is the team's record, and reviews and reports read it."""
    def do(chans):
        if name not in chans:
            raise ChannelError(f"no channel #{name}")
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        chans[name].setdefault("archived", now)
        chans[name]["deleted"] = now
        chans[name]["deleted_by"] = by
        return chans[name]
    return _edit(team_dir, do)


def set_bookmarks(team_dir, name: str, bookmarks: list) -> list[dict]:
    """Replace a channel's bookmarks: [{title, url}], kept in channels.json."""
    from .tasks import _links
    marks = _links(bookmarks)

    def do(chans):
        if name not in chans:
            raise ChannelError(f"no channel #{name}")
        if marks:
            chans[name]["bookmarks"] = marks
        else:
            chans[name].pop("bookmarks", None)
        return marks
    return _edit(team_dir, do)


def restore(team_dir, name: str) -> dict:
    def do(chans):
        if name not in chans:
            raise ChannelError(f"no channel #{name}")
        for k in ("archived", "archived_by", "deleted", "deleted_by"):
            chans[name].pop(k, None)
        return chans[name]
    return _edit(team_dir, do)


def members(team_dir, name: str) -> list[str]:
    chans = load(team_dir)
    if name not in chans or chans[name].get("deleted"):
        live = active(team_dir)
        raise ChannelError(f"no channel #{name}; channels: "
                           f"{', '.join('#' + c for c in sorted(live)) or 'none'}")
    if chans[name].get("archived"):
        raise ChannelError(f"#{name} is archived and takes no posts; ask the "
                           f"operator to restore it if it is still needed")
    return list(chans[name].get("members", []))


def of(team_dir, agent: str) -> list[str]:
    """The open channels `agent` is a member of."""
    return sorted(n for n, c in active(team_dir).items() if agent in c.get("members", []))


def mentioned(content: str, channel_members: list[str], roster: list[str],
              aliases: dict[str, str] | None = None) -> set[str]:
    """Who a post mentions: named agents on the roster, @here, @all, and
    "user" for an operator handle. A name may drop the team prefix or its
    case (@Ada wakes at-Ada) when that leaves exactly one agent it can mean,
    or be an agent's alias. "user" is never woken; the caller sees to that."""
    out: set[str] = set()
    known = set(roster)
    for name in _MENTION.findall(content or ""):
        low = name.lower()
        if low == "here":
            out |= set(channel_members)
        elif low in ("all", "everyone", "channel"):
            out |= known
        else:
            agent = roster_lib.resolve_name(name, roster, aliases)
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
    doc = _roster_doc(team_dir)
    roster = _roster_agents(team_dir)
    named = mentioned(content, chan_members, roster, roster_lib.aliases(doc)) - {who}
    listening = (set(load(team_dir).get(chan, {}).get("wake") or ()) & set(chan_members)) - {who}
    woke = (named - {"user"}) | listening
    recipients = sorted((set(chan_members) | woke) - {who, "user"})
    if "wake" in inspect.signature(transport.post).parameters:
        post_id = transport.post(chan, recipients, content, kind=kind, mentioned=named,
                                 task_id=task_id, thread=thread, author=author,
                                 wake=listening)
    else:                       # a transport from before the wake list
        post_id = transport.post(chan, recipients, content, kind=kind,
                                 mentioned=named | listening,
                                 task_id=task_id, thread=thread, author=author)
    if not wakes(kind):
        woke = set()
    return {"id": post_id, "channel": chan, "woke": sorted(woke),
            "delivered_to": recipients, "mentions_user": "user" in named}


def notice(team_dir, channel: str, who: str, content: str,
           task_id: str | None = None) -> str | None:
    """A quiet line in a channel about something done elsewhere -- a task
    linked to it changed. Logged as `who`, copied to members as "status",
    so it wakes nobody; skipped for a closed channel. Never raises: a note
    about a change must not undo the change."""
    try:
        c = load(team_dir).get(channel)
        if not c or closed(c):
            return None
        from .transport import load as load_transport
        from .transport_file import FileTransport
        t = (load_transport(who) if os.environ.get("AGYTEAM_BUS_TRANSPORT")
             else FileTransport(who, {"team_dir": str(team_dir)}))
        try:
            return t.post(channel, [m for m in c.get("members", []) if m != who],
                          content, kind="status", task_id=task_id)
        finally:
            if hasattr(t, "close"):
                t.close()
    except Exception:
        return None
