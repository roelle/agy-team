"""Operator actions: what the human running a team can do to it, in one place.

Every client -- the command line, a chat app, a script -- changes the team
through these functions, so the same checks apply whichever one is used:

    post(team_dir, "billing", "@coder can you look at allocate?", author="matt")
    dm(team_dir, "coder", "quick question", author="matt")
    create_channel(team_dir, "sims", ["coder", "syseng"], topic="long runs")
    archive_channel(team_dir, "sims") / restore_channel / delete_channel
    create_task(team_dir, "billing", "fix allocate", owner="coder", channel="billing")
    update_task(team_dir, "task_ab12cd34", status="icebox")
    set_model(team_dir, "qa", "gemini-3.1-pro-preview", until="3d")
    stop(team_dir, "maintenance") / start(team_dir) / restart(team_dir)
    add_agent / update_agent / remove_agent      the roster
    norms / set_norms                            NORMS.md
    memories / save_memory / delete_memory       one agent's memories
    archived_memories / restore_memory / review_memory

Messages from here are sent as "user" -- the operator's identity on the bus,
which the review gate and the episode logic depend on -- and carry the
person's name as `author`, so a team with more than one human can tell them
apart without a migration.

Each refuses to run inside an agent's session (AGYTEAM_AGENT set): these are
the operator's, and an agent that could call them could speak as the user.
"""
import json
import os
import re
from pathlib import Path

from . import channels as channels_lib


class OperatorError(ValueError):
    pass


def _guard() -> None:
    inside = os.environ.get("AGYTEAM_AGENT", "").strip()
    if inside:
        raise OperatorError(f"operator actions are the operator's, and this is "
                            f"{inside}'s session")


def _user_transport(team_dir):
    from .transport_file import FileTransport
    from .transport import load
    if os.environ.get("AGYTEAM_BUS_TRANSPORT"):
        return load("user")
    return FileTransport("user", {"team_dir": str(team_dir)})


def post(team_dir, channel: str, content: str, author: str | None = None,
         thread: str | None = None, kind: str = "work") -> dict:
    """Post to a channel as the user. Wakes only the agents it mentions."""
    _guard()
    if not (content or "").strip():
        raise OperatorError("nothing to post")
    t = _user_transport(team_dir)
    try:
        return channels_lib.post(t, team_dir, channel, content, kind=kind,
                                 thread=thread, author=author)
    except channels_lib.ChannelError as e:
        raise OperatorError(str(e)) from None


def dm(team_dir, agent: str, content: str, author: str | None = None,
       thread: str | None = None, kind: str = "work") -> str:
    """A direct message from the user. It wakes the agent: it was addressed."""
    _guard()
    if not (content or "").strip():
        raise OperatorError("nothing to send")
    t = _user_transport(team_dir)
    meta = {"author": author, "thread": thread}
    try:
        out = t.send(agent, content, kind=kind, **{k: v for k, v in meta.items() if v})
    except TypeError:
        out = t.send_kind(agent, content, kind=kind)
    if out.startswith("[error"):
        raise OperatorError(out.strip("[]"))
    return out


def create_channel(team_dir, name: str, members=(), topic: str = "",
                   everyone: bool = False) -> dict:
    _guard()
    try:
        return channels_lib.create(team_dir, name, list(members), topic, everyone=everyone)
    except channels_lib.ChannelError as e:
        raise OperatorError(str(e)) from None


def set_members(team_dir, name: str, add=(), remove=(),
                everyone: bool | None = None) -> list[str]:
    _guard()
    try:
        return channels_lib.set_members(team_dir, name, add, remove, everyone=everyone)
    except channels_lib.ChannelError as e:
        raise OperatorError(str(e)) from None


def set_topic(team_dir, name: str, topic: str) -> None:
    _guard()
    try:
        channels_lib.set_topic(team_dir, name, topic)
    except channels_lib.ChannelError as e:
        raise OperatorError(str(e)) from None


def archive_channel(team_dir, name: str) -> dict:
    """Read-only and out of every agent's list; restore_channel undoes it."""
    _guard()
    try:
        return channels_lib.archive(team_dir, name)
    except channels_lib.ChannelError as e:
        raise OperatorError(str(e)) from None


def delete_channel(team_dir, name: str) -> dict:
    """Archive it and hide its history from clients. Its messages stay in
    bus.jsonl, the team's record; restore_channel brings it all back."""
    _guard()
    try:
        return channels_lib.delete(team_dir, name)
    except channels_lib.ChannelError as e:
        raise OperatorError(str(e)) from None


def restore_channel(team_dir, name: str) -> dict:
    _guard()
    try:
        return channels_lib.restore(team_dir, name)
    except channels_lib.ChannelError as e:
        raise OperatorError(str(e)) from None


# --- tasks: what moving a card does -----------------------------------------
#
# The operator may change any task. Whoever owns it is told: quietly, except
# when the change means they should stop (iced or cancelled while they were
# on it) or start (it was handed to them), which wakes them.

_BUSY = {"claimed", "running", "blocked", "review"}


def create_task(team_dir, project: str, title: str, owner: str | None = None,
                note: str = "", channel: str | None = None, status: str | None = None,
                deadline: str | None = None, requires_review: bool = False,
                rank: float | None = None) -> dict:
    _guard()
    from . import tasks
    try:
        t = tasks.create(team_dir, project, title, owner=owner or None, created_by="user",
                         note=note, channel=channel, status=status, deadline=deadline,
                         requires_review=requires_review, rank=rank)
    except tasks.TaskError as e:
        raise OperatorError(str(e)) from None
    if t.get("owner") and t.get("status") not in tasks.PAUSED:
        _tell(team_dir, t["owner"], t, f"You have a new task from the operator: "
                                       f"{t['id']} \u201c{t['title']}\u201d.", wake=True)
    return t


def update_task(team_dir, task_id: str, **changes) -> dict:
    """status, owner, title, project, note, channel ("" unlinks), deadline, rank."""
    _guard()
    from . import tasks
    allowed = {"status", "owner", "title", "project", "note", "channel", "deadline", "rank"}
    unknown = set(changes) - allowed
    if unknown:
        raise OperatorError(f"cannot change {', '.join(sorted(unknown))} from here")
    before = tasks.get(team_dir, task_id)
    if before is None:
        raise OperatorError(f"no task {task_id}")
    try:
        t = tasks.update(team_dir, task_id, "user", **{k: v for k, v in changes.items()
                                                      if v is not None})
    except tasks.TaskError as e:
        raise OperatorError(str(e)) from None
    old_owner, owner = before.get("owner"), t.get("owner")
    if owner and owner != old_owner:
        _tell(team_dir, owner, t, f"The operator handed you task {t['id']} "
                                  f"\u201c{t['title']}\u201d ({t['status']}).",
              wake=t["status"] not in tasks.PAUSED | tasks.TERMINAL)
        if old_owner:
            _tell(team_dir, old_owner, t, f"The operator handed your task {t['id']} "
                                          f"\u201c{t['title']}\u201d to {owner}.", wake=False)
    elif owner and t.get("status") != before.get("status"):
        stop = (t["status"] in tasks.PAUSED | {"cancelled"} and before.get("status") in _BUSY)
        _tell(team_dir, owner, t, f"The operator moved your task {t['id']} "
                                  f"\u201c{t['title']}\u201d from {before.get('status')} to "
                                  f"{t['status']}." + (" Stop work on it." if stop else ""),
              wake=stop)
    return t


def _tell(team_dir, agent: str, t: dict, text: str, wake: bool) -> None:
    try:
        tr = _user_transport(team_dir)
        tr.send_kind(agent, text, kind="work" if wake else "fyi", task_id=t["id"])
    except Exception:
        pass


def set_model(team_dir, agent: str, model: str, until: str | None = None,
              reason: str = "", by: str | None = None) -> dict:
    from . import models
    try:
        return models.set_model(team_dir, agent, model, until=until, reason=reason, by=by)
    except models.ModelError as e:
        raise OperatorError(str(e)) from None


def clear_model(team_dir, agent: str, by: str | None = None) -> dict:
    from . import models
    try:
        return models.clear(team_dir, agent, by=by)
    except models.ModelError as e:
        raise OperatorError(str(e)) from None


def stop(team_dir, reason: str = "") -> dict:
    _guard()
    from . import lifecycle
    return lifecycle.stop_team(reason=reason, team_dir=team_dir)


def start(team_dir) -> dict:
    _guard()
    from . import lifecycle
    return lifecycle.start_team(team_dir=team_dir)


def waive_review(team_dir, task_id: str, by: str, reason: str = "") -> dict:
    from . import tasks
    try:
        return tasks.waive_review(team_dir, task_id, by=by, reason=reason)
    except tasks.TaskError as e:
        raise OperatorError(str(e)) from None


def restart(team_dir, now: bool = False, by: str = "user") -> dict:
    """Ask the team's supervisor to drain and restart on the installed code:
    no new turns, the ones in flight finish (or, with `now`, are cancelled
    and their mail goes back), then it restarts itself. Refused when no
    supervisor runs, or when the one running predates graceful restart."""
    _guard()
    from . import heartbeat
    from .supervisor import request_restart
    beat = heartbeat.running(team_dir)
    if not beat:
        raise OperatorError("no supervisor is running this team; start it instead")
    if not beat.get("restartable"):
        raise OperatorError("the supervisor running this team predates graceful restart; "
                            "stop it and start it again (or run python -m agyteam.update)")
    return request_restart(team_dir, now=now, by=by)



# --- the roster --------------------------------------------------------------
#
# Written through roster.save, which records each write, so a running
# supervisor adopts these as the operator's rather than putting them back.

_AGENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
_RESERVED = {"user", "supervisor", "here", "all", "everyone", "channel"}
# What an operator may set on an agent from a client. Policy fields
# (workspaces, refuse_paths, allowed_send_to, ...) stay in the roster file,
# where they are reviewed as what they are.
AGENT_FIELDS = ("role", "model", "effort", "memory_model", "memory_effort",
                "is_principal", "tools_off", "aliases")


def _roster_path(team_dir) -> Path:
    return Path(team_dir) / "roster.json"


def _agent_fields(fields: dict) -> dict:
    bad = sorted(set(fields) - set(AGENT_FIELDS))
    if bad:
        raise OperatorError(f"cannot set {', '.join(bad)} here; settable: "
                            f"{', '.join(AGENT_FIELDS)}")
    out = {}
    for k, v in fields.items():
        if k in ("role", "model", "effort", "memory_model", "memory_effort"):
            out[k] = str(v or "").strip()
        elif k == "is_principal":
            out[k] = bool(v)
        elif k in ("tools_off", "aliases"):
            items = v.split(",") if isinstance(v, str) else list(v or [])
            out[k] = [str(x).strip() for x in items if str(x).strip()]
    return out


def add_agent(team_dir, name: str, role: str = "", channels="all", **fields) -> dict:
    """Put an agent on the roster. `channels`: "all" (every open channel),
    a list of channel names, or None. Channels that follow the roster
    include it either way."""
    _guard()
    from . import lifecycle
    name = (name or "").strip()
    if not _AGENT_NAME.match(name) or name.lower() in _RESERVED:
        raise OperatorError(f"{name!r} cannot be an agent's name: letters, digits, "
                            f"'.', '_' and '-', not one of {', '.join(sorted(_RESERVED))}")
    extra = {k: v for k, v in _agent_fields(fields).items() if v not in ("", [], None)}
    try:
        out = lifecycle.add_agent(name, role=(role or "").strip(), team_dir=team_dir, **extra)
    except ValueError as e:
        raise OperatorError(str(e)) from None
    names = (list(channels_lib.active(team_dir)) if channels == "all"
             else list(channels or []))
    for chan in names:
        try:
            channels_lib.set_members(team_dir, chan, add=[name])
        except channels_lib.ChannelError:
            pass
    out["channels"] = channels_lib.of(team_dir, name)
    return out


def update_agent(team_dir, name: str, **fields) -> dict:
    """Change an agent's role, default model, effort, memory model and
    effort, principal flag, tools_off or aliases. An empty value removes
    the field."""
    _guard()
    from . import roster as roster_lib
    changes = _agent_fields(fields)
    path = _roster_path(team_dir)
    doc = roster_lib.load(path)
    entry = next((a for a in doc.get("agents", []) if a.get("name") == name), None)
    if entry is None:
        raise OperatorError(f"no teammate named {name!r}")
    for k, v in changes.items():
        if v in ("", [], None) or (k == "is_principal" and v is False):
            entry.pop(k, None)
        else:
            entry[k] = v
    try:
        roster_lib.normalize(doc, source=str(path))
        roster_lib.aliases(doc)
    except roster_lib.RosterError as e:
        raise OperatorError(str(e)) from None
    roster_lib.save(path, doc)
    return entry


def remove_agent(team_dir, name: str) -> dict:
    """Take an agent off the roster and out of every channel. Its memories
    and history stay on disk; a running supervisor cancels its turn."""
    _guard()
    from . import lifecycle
    try:
        out = lifecycle.remove_agent(name, team_dir=team_dir)
    except ValueError as e:
        raise OperatorError(str(e)) from None
    for chan, c in channels_lib.load(team_dir).items():
        if name in c.get("members", []) and not c.get("everyone"):
            try:
                channels_lib.set_members(team_dir, chan, remove=[name])
            except channels_lib.ChannelError:
                pass
    return out


# --- norms ------------------------------------------------------------------

def norms(team_dir) -> str:
    try:
        return (Path(team_dir) / "NORMS.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def set_norms(team_dir, text: str, by: str = "user") -> dict:
    """Replace NORMS.md. Agents read it when a conversation starts, so a
    change reaches each agent on its next fresh conversation (cycle to make
    that now)."""
    _guard()
    import tempfile
    import time
    path = Path(team_dir) / "NORMS.md"
    text = (text or "").replace("\r\n", "\n")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".NORMS.md.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") or not text else text + "\n")
    os.replace(tmp, path)
    try:
        with (Path(team_dir) / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "event": "norms_edited", "by": by,
                                "bytes": len(text.encode("utf-8"))}) + "\n")
    except OSError:
        pass
    return {"path": str(path), "bytes": len(text.encode("utf-8"))}


# --- memories -----------------------------------------------------------------

def _memory_store(team_dir, agent: str):
    from . import memory_log
    try:
        return memory_log.open_store(team_dir, agent)
    except LookupError as e:
        raise OperatorError(str(e)) from None


def memories(team_dir, agent: str) -> list[dict]:
    """[{name, description, content}] for one agent."""
    store = _memory_store(team_dir, agent)
    try:
        return [{"name": e.name, "description": e.description, "content": store.read(e.name)}
                for e in store.index()]
    finally:
        store.close()


def save_memory(team_dir, agent: str, name: str, content: str, description: str = "",
                by: str = "user") -> dict:
    """Write one of an agent's memories, as the operator. The agent reads it
    as its own; `why` records who wrote it."""
    _guard()
    from . import memory as memory_lib
    try:
        key = memory_lib.normalize_name(name)
    except Exception as e:
        raise OperatorError(str(e)) from None
    if not (content or "").strip():
        raise OperatorError("a memory needs content")
    from . import memory_log
    store = _memory_store(team_dir, agent)
    try:
        new = store.save(key, (description or "").strip() or key, content,
                         why=f"written by the operator ({by})")
    finally:
        store.close()
    memory_log.record(team_dir, agent, "save", key, new=bool(new), by=by)
    return {"agent": agent, "name": key, "created": bool(new)}


def delete_memory(team_dir, agent: str, name: str, by: str = "user") -> dict:
    """Delete one of an agent's memories; a copy goes to the archive."""
    _guard()
    from . import memory_log
    store = _memory_store(team_dir, agent)
    try:
        content = store.read(name)
        if content is None:
            raise OperatorError(f"{agent} has no memory {name!r}")
        desc = next((e.description for e in store.index() if e.name == name), "")
        memory_log.archive(team_dir, agent, name, desc, content, reason="deleted", by=by)
        store.delete(name)
    finally:
        store.close()
    memory_log.record(team_dir, agent, "delete", name, by=by)
    return {"agent": agent, "name": name, "deleted": True, "archived": True}


def archived_memories(team_dir, agent: str) -> list[dict]:
    """What was deleted or merged away from an agent's memory, newest first."""
    from . import memory_log
    _memory_store(team_dir, agent).close()          # checks the agent
    return memory_log.archived(team_dir, agent)


def restore_memory(team_dir, agent: str, archive_id: str, by: str = "user") -> dict:
    """Put an archived memory back. Refused if a memory of that name exists
    now: read both and save the one you want instead."""
    _guard()
    from . import memory_log
    store = _memory_store(team_dir, agent)
    try:
        doc = next((d for d in memory_log.archived(team_dir, agent)
                    if d.get("id") == archive_id), None)
        if doc is None:
            raise OperatorError(f"nothing archived as {archive_id!r}")
        if store.read(doc["name"]) is not None:
            raise OperatorError(f"{agent} has a memory named {doc['name']!r} now; "
                                f"delete or rename it first")
        memory_log.take_archived(team_dir, agent, archive_id)
        _, why, body = _provenance(doc.get("content") or "")
        store.save(doc["name"], doc.get("description") or doc["name"], body or doc["content"],
                   why=why or f"restored by the operator ({by})")
    finally:
        store.close()
    memory_log.record(team_dir, agent, "save", doc["name"], new=True, by=by, restored=archive_id)
    return {"agent": agent, "name": doc["name"], "restored": True}


def _provenance(text: str):
    from .memory import extract_provenance
    return extract_provenance(text)


def review_memory(team_dir, agent: str, by: str = "user") -> dict:
    """Ask the supervisor driving the team to run a memory review for
    `agent` between its turns. Refused when no supervisor runs."""
    _guard()
    from . import distill_queue, heartbeat
    _memory_store(team_dir, agent).close()
    if not heartbeat.running(team_dir):
        raise OperatorError("no supervisor is running this team; a review is a turn, "
                            "so start the team first")
    rid = distill_queue.add(team_dir, "review", agent, by=by)
    return {"queued": rid, "agent": agent}
