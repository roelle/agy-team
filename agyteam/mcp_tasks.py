"""MCP stdio server for tracking work that outlives a single turn.

Companion to `agyteam/tasks.py`, which is the record and holds every rule --
who may change a task, what a valid reminder is, what closing one requires,
and the lock that makes a claim mean something. This server only translates
tool calls into those functions and their refusals into sentences, and asks
`agyteam.policy` whether this agent may assign work to others at all.

Like agyteam/mcp_bus.py, agyteam/mcp_memory.py and agyteam/mcp_self.py, this is
pure Python standard library, built on agyteam.mcp_base.serve.

Identity: `python -m agyteam.mcp_tasks <team_dir> <agent_name>` (explicit), or
set AGYTEAM_AGENT (agy CLI path). Refuses to start nameless rather than
guessing, so a task can never be filed under the wrong owner.
"""
import os
import sys
from pathlib import Path

from .mcp_base import serve, string, tool
from . import heartbeat, policy
from . import roster as roster_lib
from . import scope
from . import tasks as tasks_lib

_STATUS = {"type": "string", "enum": sorted(tasks_lib.STATUSES),
           "description": "queued, claimed, running, blocked, done, failed or "
                          "cancelled (in_progress, waiting, completed and the like "
                          "are accepted and mapped)"}

TOOLS = [
    tool("create_task",
         "Start tracking work that will outlive this turn -- a simulation, a "
         "long build, anything you would otherwise have to remember to come "
         "back to. Owned by you unless you name a teammate. Pass check_after "
         "to be reminded at a time, or until to be woken only when something "
         "has happened (a file appears, a process exits).",
         {"project": string("Grouping key for related tasks, e.g. 'sim-sweep-42'"),
          "title": string("Short description of the task"),
          "owner": string("Teammate who owns it (default: you)"),
          "note": string("What you started, where its output will land, and "
                         "anything a later check-in will need"),
          "check_after": string(f"When to be reminded: {tasks_lib.WHEN_HELP}"),
          "until": string("Wake me only when this holds: 'file_exists:/path', "
                          "'file_contains:/path:text' or 'pid_exited:1234'. "
                          "Checked by the sweep itself, costing you no turns"),
          "poll_every": string("How often to re-check until, e.g. '10m' (default 5m)"),
          "deadline": string(f"With until: wake me anyway at this time if it has "
                             f"not happened: {tasks_lib.WHEN_HELP}"),
          "collaborators": string("Comma-separated teammates whose work this task "
                                  "also needs; it cannot close until each has "
                                  "recorded activity since it was created"),
          "requires_review": string("'true' if this task may not close, and its "
                                    "result may not go to the user, without an "
                                    "approved review naming it")},
         ["project", "title"]),
    tool("claim_task",
         "Take ownership of a task nobody owns yet. Refuses if a teammate "
         "already owns it; they can hand it over with update_task(owner=...).",
         {"task_id": string("Task id from create_task or list_tasks")},
         ["task_id"]),
    tool("update_task",
         "Change a task you own: its status, its note, who owns it, when you "
         "want to be reminded, or what it is waiting on. Set check_after or "
         "until when you are waiting on something slow and go do other work; "
         "you will get a message when it comes due.",
         {"task_id": string("Task id"),
          "status": _STATUS,
          "check_after": string(f"When to be reminded: {tasks_lib.WHEN_HELP}. "
                                "Or 'clear' to remove the reminder"),
          "until": string("Wake me only when this holds: 'file_exists:/path', "
                          "'file_contains:/path:text' or 'pid_exited:1234'"),
          "poll_every": string("How often to re-check until, e.g. '10m'"),
          "deadline": string("With until: wake me anyway at this time"),
          "owner": string("Hand the task to this teammate"),
          "collaborators": string("Comma-separated teammates whose work this needs"),
          "evidence": string("Where the result can be checked: a path, or a review"),
          "note": string("Replace the task's note with this")},
         ["task_id"]),
    tool("complete_task",
         "Close a task you own. The note is required and replaces the old one: "
         "say what was done and where the result is. Refused while a "
         "collaborator has not yet worked on it, or while a task that "
         "requires_review has no approved review naming it.",
         {"task_id": string("Task id"),
          "note": string("What was done and where the result is"),
          "evidence": string("Where it can be checked: a path, or 'review' "
                             "once one is recorded")},
         ["task_id", "note"]),
    tool("list_tasks",
         "List tasks, most recently updated last. Pass owner=<your name> to "
         "see only your own; no arguments shows the whole team's.",
         {"project": string("Filter to one project"),
          "owner": string("Filter to one owner"),
          "status": string("Filter to one status")}),
]

_DELIVERY = ("[reminder set for {when}: it arrives as a message once that time "
             "passes and the sweep next runs; {who}.]")
_WAITING = ("[waiting on {what}, checked every {every} with no turn spent; "
            "you will be messaged when it holds{deadline}; {who}.]")


def _resolve_context(team_dir: Path | None = None) -> tuple[scope.Scopes, Path]:
    env = os.environ.get("AGYTEAM_TEAM_DIR")
    if env or team_dir is not None:
        td = team_dir or Path(env).resolve()
        scopes = scope.load(durable=td.parent, team=td.parent.name)
        return scopes, td
    scopes = scope.load()
    return scopes, scopes.team_dir()


def _known_agents(team_dir: Path) -> set:
    try:
        return {a["name"] for a in
                roster_lib.load(team_dir / "roster.json").get("agents", [])}
    except Exception:
        return set()


def _check_teammates(team_dir: Path, names) -> str | None:
    known = _known_agents(team_dir)
    bad = [n for n in names if known and n not in known]
    if bad:
        return (f"[error: {', '.join(bad)} not on this roster. Known agents: "
                f"{', '.join(sorted(known))}]")
    return None


def _text(a: dict, key: str) -> str:
    v = a.get(key)
    return v.strip() if isinstance(v, str) else ""


def _names(a: dict, key: str) -> list[str] | None:
    raw = _text(a, key)
    if not raw:
        return None
    return [n.strip() for n in raw.split(",") if n.strip()]


def _render(rows: list[dict]) -> str:
    if not rows:
        return "[no tasks]"
    lines = []
    for t in rows:
        extra = []
        if t.get("check_after"):
            extra.append(f"reminder at {t['check_after']}")
        if t.get("until"):
            extra.append(f"waiting on {tasks_lib._describe(t['until'])}")
        if t.get("collaborators"):
            extra.append(f"with {', '.join(t['collaborators'])}")
        if t.get("requires_review"):
            extra.append("requires review")
        tail = f" ({'; '.join(extra)})" if extra else ""
        lines.append(f"- {t['id']} [{t.get('status')}] ({t.get('project')}) "
                     f"{t.get('title')} — owner: {t.get('owner') or 'unclaimed'}{tail}")
        if t.get("note"):
            lines.append(f"  note: {t['note']}")
        if t.get("evidence"):
            lines.append(f"  evidence: {t['evidence']}")
    return "\n".join(lines)


def _with_delivery(team_dir: Path, t: dict, text: str) -> str:
    if not t:
        return text
    who = heartbeat.describe(team_dir)
    if t.get("until") and t.get("status") not in tasks_lib.TERMINAL:
        dl = f", or at {t['deadline']} regardless" if t.get("deadline") else ""
        return f"{text}\n" + _WAITING.format(
            what=tasks_lib._describe(t["until"]), every=t.get("poll_every") or "5m",
            deadline=dl, who=who)
    if t.get("check_after"):
        return f"{text}\n" + _DELIVERY.format(when=t["check_after"], who=who)
    return text


def _policy(agent: str, team_dir: Path, tool_name: str, args: dict) -> str | None:
    return policy.check_tool_policy(agent, tool_name, args, team_dir=team_dir,
                                    check_paths=False)


def _create_task(agent: str, team_dir: Path, a: dict) -> str:
    project, title = _text(a, "project"), _text(a, "title")
    if not project or not title:
        missing = [n for n, v in (("project", project), ("title", title)) if not v]
        return (f"[error: create_task is missing required argument(s): "
                f"{', '.join(missing)}]")
    owner = _text(a, "owner") or agent
    collaborators = _names(a, "collaborators") or []
    err = _check_teammates(team_dir, [owner, *collaborators])
    if err:
        return err
    refusal = _policy(agent, team_dir, "create_task", {"owner": owner})
    if refusal:
        return refusal
    try:
        until = tasks_lib.parse_until(_text(a, "until")) if _text(a, "until") else None
    except tasks_lib.TaskError as e:
        return f"[error: {e}]"
    t = tasks_lib.create(team_dir, project, title, owner=owner, created_by=agent,
                         note=_text(a, "note"),
                         check_after=_text(a, "check_after") or None,
                         collaborators=collaborators,
                         requires_review=_text(a, "requires_review").lower() in ("true", "yes", "1"),
                         until=until, poll_every=_text(a, "poll_every") or None,
                         deadline=_text(a, "deadline") or None)
    return _with_delivery(team_dir, t, f"[task created: {t['id']}]\n{_render([t])}")


def _claim_task(agent: str, team_dir: Path, a: dict) -> str:
    task_id = _text(a, "task_id")
    if not task_id:
        return "[error: claim_task is missing required argument: task_id]"
    t = tasks_lib.claim(team_dir, task_id, agent)
    if t is None:
        return f"[error: no task with id '{task_id}']"
    return f"[claimed]\n{_render([t])}"


def _update_task(agent: str, team_dir: Path, a: dict) -> str:
    task_id = _text(a, "task_id")
    if not task_id:
        return "[error: update_task is missing required argument: task_id]"
    owner = _text(a, "owner") or None
    collaborators = _names(a, "collaborators")
    err = _check_teammates(team_dir, [n for n in [owner, *(collaborators or [])] if n])
    if err:
        return err
    if owner:
        refusal = _policy(agent, team_dir, "update_task", {"owner": owner})
        if refusal:
            return refusal
    raw = _text(a, "check_after")
    clear = raw.lower() == "clear"
    try:
        until = tasks_lib.parse_until(_text(a, "until")) if _text(a, "until") else None
    except tasks_lib.TaskError as e:
        return f"[error: {e}]"
    t = tasks_lib.update(team_dir, task_id, agent,
                         status=_text(a, "status") or None,
                         owner=owner,
                         check_after=None if clear or not raw else raw,
                         clear_check_after=clear,
                         note=a.get("note") if isinstance(a.get("note"), str) else None,
                         evidence=_text(a, "evidence") or None,
                         collaborators=collaborators,
                         until=until, poll_every=_text(a, "poll_every") or None,
                         deadline=_text(a, "deadline") or None)
    if t is None:
        return f"[error: no task with id '{task_id}']"
    out = f"[updated]\n{_render([t])}"
    return _with_delivery(team_dir, t, out) if (raw and not clear) or until else out


def _complete_task(agent: str, team_dir: Path, a: dict) -> str:
    task_id, note = _text(a, "task_id"), _text(a, "note")
    if not task_id:
        return "[error: complete_task is missing required argument: task_id]"
    if not note:
        return ("[error: complete_task needs a closing note: what was done and "
                "where the result is. It replaces the task's old note]")
    t = tasks_lib.complete(team_dir, task_id, agent, note,
                           evidence=_text(a, "evidence") or None)
    if t is None:
        return f"[error: no task with id '{task_id}']"
    out = f"[completed]\n{_render([t])}"
    creator = t.get("created_by")
    if creator and creator not in (agent, t.get("owner")) and creator != "user":
        try:
            from .transport import load as load_transport
            bus = load_transport(agent)
            try:
                bus.send_kind(creator, f"Task {t['id']} ({t.get('title')}) is done: "
                                       f"{note}", kind="deliverable", task_id=t["id"])
            finally:
                if hasattr(bus, "close"):
                    bus.close()
            out += f"\n[{creator}, who created it, has been told]"
        except Exception:
            pass
    return out


def _list_tasks(team_dir: Path, a: dict) -> str:
    return _render(tasks_lib.list_tasks(
        team_dir, project=_text(a, "project") or None, owner=_text(a, "owner") or None,
        status=tasks_lib.normalize_status(_text(a, "status") or None)))


def main(agent: str, team_dir: Path | None = None):
    scopes, td = _resolve_context(team_dir)
    handlers = {
        "create_task": lambda a: _create_task(agent, td, a),
        "claim_task": lambda a: _claim_task(agent, td, a),
        "update_task": lambda a: _update_task(agent, td, a),
        "complete_task": lambda a: _complete_task(agent, td, a),
        "list_tasks": lambda a: _list_tasks(td, a),
    }

    def dispatch(name, args):
        fn = handlers.get(name)
        if not fn:
            return (f"[error: unknown tool '{name}'. This server provides: "
                    f"{', '.join(sorted(handlers))}]")
        try:
            return fn(args)
        except tasks_lib.TaskError as e:
            return f"[error: {e}]"

    serve(f"agy-team-tasks:{agent}", TOOLS, dispatch)


if __name__ == "__main__":
    td_arg = None
    if len(sys.argv) == 3:                      # <team_dir> <agent_name>
        td_arg = Path(sys.argv[1]).resolve()
        os.environ["AGYTEAM_TEAM_DIR"] = str(td_arg)
        me = sys.argv[2]
    elif len(sys.argv) == 2:                    # <agent_name>
        me = sys.argv[1]
    else:
        me = os.environ.get("AGYTEAM_AGENT", "")
    if not me:
        sys.exit("agyteam.mcp_tasks: no agent identity. Pass <team_dir> "
                 "<agent_name> or set AGYTEAM_AGENT (and optionally "
                 "AGYTEAM_TEAM_DIR).")
    main(me, team_dir=td_arg)
