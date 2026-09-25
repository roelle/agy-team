"""MCP stdio server for tracking work that outlives a single turn.

Companion to `agyteam/tasks.py`, which is the record and holds every rule --
who may change a task, what a valid reminder is, and the lock that makes a
claim mean something. This server only translates tool calls into those
functions and their refusals into sentences.

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
from . import roster as roster_lib
from . import scope
from . import tasks as tasks_lib

TOOLS = [
    tool("create_task",
         "Start tracking work that will outlive this turn -- a simulation, a "
         "long build, anything you would otherwise have to remember to come "
         "back to. Owned by you unless you name a teammate. Pass check_after "
         "to be reminded.",
         {"project": string("Grouping key for related tasks, e.g. 'sim-sweep-42'"),
          "title": string("Short description of the task"),
          "owner": string("Teammate who owns it (default: you)"),
          "note": string("What you started, where its output will land, and "
                         "anything a later check-in will need"),
          "check_after": string(f"When to be reminded: {tasks_lib.WHEN_HELP}")},
         ["project", "title"]),
    tool("claim_task",
         "Take ownership of a task nobody owns yet. Refuses if a teammate "
         "already owns it; they can hand it over with update_task(owner=...).",
         {"task_id": string("Task id from create_task or list_tasks")},
         ["task_id"]),
    tool("update_task",
         "Change a task you own: its status, its note, who owns it, or when "
         "you want to be reminded about it. Set check_after when you are "
         "waiting on something (a running sim, a build) and go do other work; "
         "you will get a message when it comes due, on any unfinished task.",
         {"task_id": string("Task id"),
          "status": string("queued, claimed, running, blocked, done, failed, "
                           "or cancelled"),
          "check_after": string(f"When to be reminded: {tasks_lib.WHEN_HELP}. "
                                "Or 'clear' to remove the reminder"),
          "owner": string("Hand the task to this teammate"),
          "note": string("Replace the task's note with this")},
         ["task_id"]),
    tool("complete_task",
         "Mark a task you own done, which also removes its reminder.",
         {"task_id": string("Task id"),
          "note": string("Optional final note, e.g. where the result landed")},
         ["task_id"]),
    tool("list_tasks",
         "List tasks, most recently updated last. Pass owner=<your name> to "
         "see only your own; no arguments shows the whole team's.",
         {"project": string("Filter to one project"),
          "owner": string("Filter to one owner"),
          "status": string("Filter to one status")}),
]

# What a reminder depends on, said where it is set. A reminder nothing
# delivers is indistinguishable from one that is simply not due yet, so the
# agent is told the condition rather than left to assume it.
_DELIVERY = ("[reminder set for {when}. It arrives as a message once that time "
             "passes and the team's supervisor (or a scheduled `python -m "
             "agyteam.tasks sweep`) next runs.]")


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


def _check_teammate(team_dir: Path, name: str) -> str | None:
    known = _known_agents(team_dir)
    if known and name not in known:
        return (f"[error: '{name}' is not on this roster. Known agents: "
                f"{', '.join(sorted(known))}]")
    return None


def _text(a: dict, key: str) -> str:
    v = a.get(key)
    return v.strip() if isinstance(v, str) else ""


def _render(rows: list[dict]) -> str:
    if not rows:
        return "[no tasks]"
    lines = []
    for t in rows:
        check = f", reminder at {t['check_after']}" if t.get("check_after") else ""
        lines.append(f"- {t['id']} [{t.get('status')}] ({t.get('project')}) "
                     f"{t.get('title')} — owner: {t.get('owner') or 'unclaimed'}{check}")
        if t.get("note"):
            lines.append(f"  note: {t['note']}")
    return "\n".join(lines)


def _with_delivery(t: dict, text: str) -> str:
    if t and t.get("check_after"):
        return f"{text}\n{_DELIVERY.format(when=t['check_after'])}"
    return text


def _create_task(agent: str, team_dir: Path, a: dict) -> str:
    project, title = _text(a, "project"), _text(a, "title")
    if not project or not title:
        missing = [n for n, v in (("project", project), ("title", title)) if not v]
        return (f"[error: create_task is missing required argument(s): "
                f"{', '.join(missing)}]")
    owner = _text(a, "owner") or agent
    err = _check_teammate(team_dir, owner)
    if err:
        return err
    t = tasks_lib.create(team_dir, project, title, owner=owner, created_by=agent,
                         note=_text(a, "note"),
                         check_after=_text(a, "check_after") or None)
    return _with_delivery(t, f"[task created: {t['id']}]\n{_render([t])}")


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
    if owner:
        err = _check_teammate(team_dir, owner)
        if err:
            return err
    raw = _text(a, "check_after")
    clear = raw.lower() == "clear"
    t = tasks_lib.update(team_dir, task_id, agent,
                         status=_text(a, "status") or None,
                         owner=owner,
                         check_after=None if clear or not raw else raw,
                         clear_check_after=clear,
                         note=a.get("note") if isinstance(a.get("note"), str) else None)
    if t is None:
        return f"[error: no task with id '{task_id}']"
    out = f"[updated]\n{_render([t])}"
    return _with_delivery(t, out) if raw and not clear else out


def _complete_task(agent: str, team_dir: Path, a: dict) -> str:
    return _update_task(agent, team_dir, {**a, "status": "done", "check_after": ""})


def _list_tasks(team_dir: Path, a: dict) -> str:
    return _render(tasks_lib.list_tasks(team_dir, project=_text(a, "project") or None,
                                        owner=_text(a, "owner") or None,
                                        status=_text(a, "status") or None))


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
