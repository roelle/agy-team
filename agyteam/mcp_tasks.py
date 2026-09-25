"""MCP stdio server for tracking work that outlives a single turn.

Companion to `agyteam/tasks.py`, which is the actual record (an append-only
log an agent's tools never touch directly -- see that module's docstring for
why). This server is the agent-facing surface over it: create a task, claim
one, update its status or ask to be checked again later, list what exists.

Like agyteam/mcp_bus.py, agyteam/mcp_memory.py and agyteam/mcp_self.py, this is
pure Python standard library, built on agyteam.mcp_base.serve.

Identity: `python -m agyteam.mcp_tasks <team_dir> <agent_name>` (explicit), or
set AGYTEAM_AGENT (agy CLI path). Refuses to start nameless rather than
guessing, so a task can never be filed under the wrong owner.
"""
import os
import re
import sys
import time
from pathlib import Path

from .mcp_base import serve, string, tool
from . import roster as roster_lib
from . import scope
from . import tasks as tasks_lib

TOOLS = [
    tool("create_task",
         "Start tracking a piece of work that may span many turns -- a "
         "simulation run, a build, anything you would otherwise have to "
         "remember to come back to. Defaults to owned by you; pass owner to "
         "hand it to a teammate instead.",
         {"project": string("Grouping key for related tasks, e.g. 'sim-sweep-42'"),
          "title": string("Short description of the task"),
          "owner": string("Agent who owns it (default: you)"),
          "note": string("Optional detail: what you started, where its output "
                         "will land, anything a check-in will need")},
         ["project", "title"]),
    tool("claim_task",
         "Take ownership of an unclaimed or your own task, and mark it "
         "'claimed'. Refuses if another agent already owns it.",
         {"task_id": string("Task id from create_task or list_tasks")},
         ["task_id"]),
    tool("update_task",
         "Change a task's status, note, or when it should be checked again. "
         "Use status='blocked' with check_after when you are waiting on "
         "something external (a sim, a build) and want to be reminded "
         "instead of polling yourself -- the team will message you when "
         "check_after passes, so you are free to work on other tasks until "
         "then. check_after accepts a duration like '90m', '2h', '1d', or an "
         "ISO-8601 UTC timestamp. Only the task's owner may update it.",
         {"task_id": string("Task id"),
          "status": string("New status: queued, claimed, running, blocked, "
                           "done, failed, or cancelled"),
          "check_after": string("When to be reminded, e.g. '1h', or 'clear' "
                                "to stop waiting on a time"),
          "note": string("Replace the task's note with this")},
         ["task_id"]),
    tool("complete_task",
         "Mark a task done. Shorthand for update_task(status='done').",
         {"task_id": string("Task id"),
          "note": string("Optional final note, e.g. where the result landed")},
         ["task_id"]),
    tool("list_tasks",
         "List tasks, most recently updated last. Pass owner=<your name> to "
         "see only your own; pass no arguments to see the whole team's.",
         {"project": string("Filter to one project"),
          "owner": string("Filter to one owner"),
          "status": string("Filter to one status")}),
]

_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _resolve_context(team_dir: Path | None = None) -> tuple[scope.Scopes, Path]:
    env = os.environ.get("AGYTEAM_TEAM_DIR")
    if env or team_dir is not None:
        td = team_dir or Path(env).resolve()
        scopes = scope.load(durable=td.parent, team=td.parent.name)
        return scopes, td
    scopes = scope.load()
    return scopes, scopes.team_dir()


def _resolve_check_after(raw: str) -> str:
    """Accept a duration shorthand or a literal ISO-8601 UTC timestamp."""
    m = _DURATION.match(raw.strip())
    if not m:
        return raw.strip()
    n, unit = int(m.group(1)), m.group(2)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                          time.gmtime(time.time() + n * _UNIT_SECONDS[unit]))


def _known_agents(team_dir: Path) -> set:
    try:
        return {a["name"] for a in roster_lib.load(team_dir / "roster.json").get("agents", [])}
    except Exception:
        return set()


def _render(rows: list[dict]) -> str:
    if not rows:
        return "[no tasks]"
    lines = []
    for t in rows:
        check = f", check after {t['check_after']}" if t.get("check_after") else ""
        lines.append(f"- {t['id']} [{t.get('status')}] ({t.get('project')}) "
                     f"{t.get('title')} — owner: {t.get('owner') or 'unclaimed'}{check}")
        if t.get("note"):
            lines.append(f"  note: {t['note']}")
    return "\n".join(lines)


def _create_task(agent: str, team_dir: Path, a: dict) -> str:
    project = (a.get("project") or "").strip()
    title = (a.get("title") or "").strip()
    if not project:
        return "[error: create_task is missing required argument: project]"
    if not title:
        return "[error: create_task is missing required argument: title]"
    owner = (a.get("owner") or "").strip() or agent
    known = _known_agents(team_dir)
    if known and owner not in known:
        return (f"[error: '{owner}' is not on this roster. Known agents: "
                f"{', '.join(sorted(known))}]")
    t = tasks_lib.create(team_dir, project, title, owner=owner,
                         created_by=agent, note=a.get("note") or "")
    return f"[task created: {t['id']}]\n{_render([t])}"


def _claim_task(agent: str, team_dir: Path, a: dict) -> str:
    task_id = (a.get("task_id") or "").strip()
    if not task_id:
        return "[error: claim_task is missing required argument: task_id]"
    current = tasks_lib.get(team_dir, task_id)
    if current is None:
        return f"[error: no task with id '{task_id}']"
    if current.get("owner") and current["owner"] != agent:
        return (f"[error: task {task_id} is already owned by "
                f"'{current['owner']}' — ask them to hand it off]")
    t = tasks_lib.update(team_dir, task_id, agent, status="claimed", owner=agent)
    return f"[claimed]\n{_render([t])}"


def _update_task(agent: str, team_dir: Path, a: dict) -> str:
    task_id = (a.get("task_id") or "").strip()
    if not task_id:
        return "[error: update_task is missing required argument: task_id]"
    current = tasks_lib.get(team_dir, task_id)
    if current is None:
        return f"[error: no task with id '{task_id}']"
    if current.get("owner") and current["owner"] != agent:
        return (f"[error: task {task_id} is owned by '{current['owner']}', "
                f"not you — claim_task first if they have handed it off]")

    status = a.get("status")
    if status is not None and status not in tasks_lib.STATUSES:
        return (f"[error: unknown status '{status}'. Use one of: "
                f"{', '.join(sorted(tasks_lib.STATUSES))}]")

    check_after_raw = a.get("check_after")
    check_after, clear = None, False
    if isinstance(check_after_raw, str) and check_after_raw.strip():
        if check_after_raw.strip().lower() == "clear":
            clear = True
        else:
            check_after = _resolve_check_after(check_after_raw)

    if status == "blocked" and not check_after and not current.get("check_after"):
        return ("[error: status='blocked' needs check_after so the team "
                "knows when to remind you — pass e.g. check_after='1h']")

    t = tasks_lib.update(team_dir, task_id, agent, status=status,
                         check_after=check_after, clear_check_after=clear,
                         note=a.get("note"))
    return f"[updated]\n{_render([t])}"


def _complete_task(agent: str, team_dir: Path, a: dict) -> str:
    a = dict(a)
    a["status"] = "done"
    return _update_task(agent, team_dir, a)


def _list_tasks(team_dir: Path, a: dict) -> str:
    rows = tasks_lib.list_tasks(team_dir, project=a.get("project"),
                                owner=a.get("owner"), status=a.get("status"))
    return _render(rows)


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
        return fn(args) if fn else f"[error: unknown tool '{name}']"

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
