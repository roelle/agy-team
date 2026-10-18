"""Operator actions: what the human running a team can do to it, in one place.

Every client -- the command line, a chat app, a script -- changes the team
through these functions, so the same checks apply whichever one is used:

    post(team_dir, "billing", "@coder can you look at allocate?", author="matt")
    dm(team_dir, "coder", "quick question", author="matt")
    create_channel(team_dir, "sims", ["coder", "syseng"], topic="long runs")
    set_model(team_dir, "qa", "gemini-3.1-pro-preview", until="3d")
    stop(team_dir, "maintenance") / start(team_dir)

Messages from here are sent as "user" -- the operator's identity on the bus,
which the review gate and the episode logic depend on -- and carry the
person's name as `author`, so a team with more than one human can tell them
apart without a migration.

Each refuses to run inside an agent's session (AGYTEAM_AGENT set): these are
the operator's, and an agent that could call them could speak as the user.
"""
import os
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


def create_channel(team_dir, name: str, members=(), topic: str = "") -> dict:
    _guard()
    try:
        return channels_lib.create(team_dir, name, list(members), topic)
    except channels_lib.ChannelError as e:
        raise OperatorError(str(e)) from None


def set_members(team_dir, name: str, add=(), remove=()) -> list[str]:
    _guard()
    try:
        return channels_lib.set_members(team_dir, name, add, remove)
    except channels_lib.ChannelError as e:
        raise OperatorError(str(e)) from None


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
