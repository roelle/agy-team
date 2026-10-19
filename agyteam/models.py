"""Which model each agent runs on: the roster's choice, or the operator's.

    python -m agyteam.models set coder gemini-3.1-pro-preview --until 3d --reason "hard week"
    python -m agyteam.models set '*' gemini-3.8-flash --until 2026-10-24T00:00:00Z
    python -m agyteam.models clear coder
    python -m agyteam.models list
    python -m agyteam.models usage

The roster's `model` (and `effort`) is the default. An override, set here,
wins over it for one agent or for every agent ("*"; a named agent's own
override wins over "*"), until it is cleared or its `until` passes. Every
change is a row in <team_dir>/models.jsonl, so the record says who moved
which agent to what, when, and why.

Changing an agent's model is changing its conversation. Some runtimes bind
the model when a conversation starts (the SDK session, a hosted UI), others
take it per turn, and a conversation carried from one model to another is
neither fresh nor continuous. So the supervisor applies a change -- this
file's or the roster's -- at the agent's next idle moment as a cycle: the
agent distills what it learned into memory, its conversation is retired, and
the next wake starts on the new model with the memories. Never mid-turn.
Before this, editing the roster's model on the SDK runner silently ended the
session with nothing distilled.

## Usage and allowances

`usage()` totals the recorded turns per model over a window (a week by
default). A roster `"policy": {"model_allowance": {"<model>": <tokens per
week>}}` gives a model a weekly budget; the supervisor tells the user, once,
when a model passes 80% of it and again at 100%. It never changes a model on
its own: running out of the good model is a decision about the work, and the
operator makes it.

An override is the operator's, like a review waiver: it is refused from
inside an agent's session (AGYTEAM_AGENT set), and no agent is offered a tool
for it.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import filelock

FILE = "models.jsonl"
ALERTS = "model_alerts.json"
THRESHOLDS = (80, 100)
WEEK = 7 * 24 * 3600


class ModelError(ValueError):
    pass


def path(team_dir) -> Path:
    return Path(team_dir) / FILE


def _now_iso(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


def _roster(team_dir) -> dict:
    try:
        return json.loads((Path(team_dir) / "roster.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _agent_names(team_dir) -> set[str]:
    return {a.get("name") for a in _roster(team_dir).get("agents", []) if isinstance(a, dict)}


def _rows(team_dir) -> list[dict]:
    try:
        text = path(team_dir).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _append(team_dir, row: dict) -> dict:
    p = path(team_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with filelock.locked(p):
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    return row


def _operator_only(by: str | None) -> str:
    inside = os.environ.get("AGYTEAM_AGENT", "").strip()
    if inside:
        raise ModelError(f"choosing a model is the operator's, and this is {inside}'s "
                         f"session; ask the operator")
    return (by or os.environ.get("USER") or "operator").strip()


def check_available(model: str, effort: str | None = None, spec: str | None = None) -> None:
    """Refuse a model the configured runner says it cannot run.

    A name one runtime knows and another does not fails every turn: the
    seeded roster's SDK model id failed every CLI turn for two agents. When
    the runner cannot say what it offers, nothing is refused.
    """
    from . import runner as runner_lib
    if runner_lib.model_available(model, effort, spec) is False:
        offered = [m["id"] for m in runner_lib.list_models(spec) or []]
        cls = runner_lib.runner_class(spec)
        raise ModelError(f"{model!r} is not a model the configured runner "
                         f"({getattr(cls, 'label', 'runner')}) can run; it offers: "
                         f"{', '.join(offered[:20])}{' ...' if len(offered) > 20 else ''}")


def set_model(team_dir, agent: str, model: str, effort: str | None = None,
              until: str | None = None, reason: str = "", by: str | None = None,
              now: float | None = None, check: bool = True) -> dict:
    """Put `agent` (or "*" for everyone) on `model`, optionally until a time
    (a duration like '3d' or an ISO time with a zone). With `check`, a model
    the configured runner does not offer is refused."""
    who = _operator_only(by)
    agent = (agent or "").strip()
    if agent != "*" and agent not in _agent_names(team_dir):
        raise ModelError(f"no agent named {agent!r} on the roster")
    if not (model or "").strip():
        raise ModelError("give the model to switch to (clear removes an override)")
    if check:
        check_available(model.strip(), effort)
    when = None
    if until:
        from .tasks import TaskError, parse_when
        try:
            when = parse_when(until, now=now)
        except TaskError as e:
            raise ModelError(str(e).replace("check_after", "until")) from None
    return _append(team_dir, {"ts": _now_iso(now), "agent": agent, "model": model.strip(),
                              "effort": (effort or "").strip() or None, "until": when,
                              "reason": reason.strip(), "by": who})


def clear(team_dir, agent: str, reason: str = "", by: str | None = None,
          now: float | None = None) -> dict:
    who = _operator_only(by)
    return _append(team_dir, {"ts": _now_iso(now), "agent": agent.strip(), "model": None,
                              "until": None, "reason": reason.strip(), "by": who})


def overrides(team_dir, now: float | None = None) -> dict[str, dict]:
    """{agent or '*': row} for every override in force now."""
    now_iso = _now_iso(now)
    latest: dict[str, dict] = {}
    for row in _rows(team_dir):
        if row.get("agent"):
            latest[row["agent"]] = row
    return {a: r for a, r in latest.items()
            if r.get("model") and not (r.get("until") and r["until"] <= now_iso)}


def resolve(team_dir, agent: str, model: str | None, effort: str | None = None,
            now: float | None = None) -> tuple[str | None, str | None, str]:
    """(model, effort, source) for `agent`: its own override, else '*', else
    the given roster model and effort. An override replaces effort too; a
    model chosen by name is run as named."""
    if team_dir is None:
        return model, effort, "roster"
    active = overrides(team_dir, now)
    row = active.get(agent) or active.get("*")
    if row:
        return row["model"], row.get("effort"), f"override ({row.get('by')}: {row.get('reason') or 'no reason given'})"
    return model, effort, "roster"


# --- usage -------------------------------------------------------------------

def _epoch(ts: str) -> float | None:
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError, OverflowError):
        return None


def usage(team_dir, days: float = 7, now: float | None = None) -> dict[str, dict]:
    """{model: {turns, input_tokens, output_tokens, tokens}} over the window."""
    now = time.time() if now is None else now
    since = now - days * 24 * 3600
    out: dict[str, dict] = {}
    try:
        text = (Path(team_dir) / "events.jsonl").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        if '"turn"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") != "turn":
            continue
        at = _epoch(e.get("ts", ""))
        if at is None or at < since:
            continue
        m = e.get("model") or "unknown"
        u = out.setdefault(m, {"turns": 0, "input_tokens": 0, "output_tokens": 0, "tokens": 0})
        i, o = int(e.get("input_tokens") or 0), int(e.get("output_tokens") or 0)
        u["turns"] += 1
        u["input_tokens"] += i
        u["output_tokens"] += o
        u["tokens"] += i + o
    return out


def allowances(team_dir) -> dict[str, int]:
    raw = (_roster(team_dir).get("policy") or {}).get("model_allowance") or {}
    return {m: int(v) for m, v in raw.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0}


def due_alerts(team_dir, now: float | None = None) -> list[dict]:
    """Allowance crossings not yet reported in this week, each once.

    Returns [{model, percent, threshold, used, allowance}] and records them,
    so the caller sends each exactly once per threshold per week.
    """
    limits = allowances(team_dir)
    if not limits:
        return []
    now = time.time() if now is None else now
    week = int(now // WEEK)
    used = usage(team_dir, days=7, now=now)
    state_path = Path(team_dir) / ALERTS
    out = []
    with filelock.locked(state_path):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        for model, limit in limits.items():
            tokens = used.get(model, {}).get("tokens", 0)
            pct = tokens * 100 / limit
            for t in THRESHOLDS:
                key = f"{model}:{t}:{week}"
                if pct >= t and not state.get(key):
                    state[key] = _now_iso(now)
                    out.append({"model": model, "percent": round(pct), "threshold": t,
                                "used": tokens, "allowance": limit})
        if out:
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return out


# --- command line --------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agyteam.models",
                                 description="Which model each agent runs on.")
    ap.add_argument("--team-dir", default=None)
    sub = ap.add_subparsers(dest="command", required=True)
    s = sub.add_parser("set", help="put an agent (or '*') on a model")
    s.add_argument("agent")
    s.add_argument("model")
    s.add_argument("--effort", default=None)
    s.add_argument("--until", default=None, help="a duration like 3d, or an ISO time with a zone")
    s.add_argument("--reason", default="")
    s.add_argument("--force", action="store_true",
                   help="set it even if the configured runner does not list it")
    c = sub.add_parser("clear", help="remove an agent's (or '*') override")
    c.add_argument("agent")
    c.add_argument("--reason", default="")
    sub.add_parser("list", help="every agent's model and where it comes from")
    sub.add_parser("available", help="the models the configured runner can run")
    u = sub.add_parser("usage", help="tokens per model over the last week")
    u.add_argument("--days", type=float, default=7)
    a = ap.parse_args(argv)

    from . import scope
    td = scope.team_dir(a.team_dir or os.environ.get("AGYTEAM_TEAM_DIR"))
    try:
        if a.command == "set":
            row = set_model(td, a.agent, a.model, a.effort, a.until, a.reason,
                            check=not a.force)
            print(f"{row['agent']} -> {row['model']}"
                  + (f" until {row['until']}" if row["until"] else "")
                  + "; the supervisor applies it at the agent's next idle moment "
                    "(distill, then a fresh conversation)")
        elif a.command == "clear":
            clear(td, a.agent, a.reason)
            print(f"{a.agent}: override cleared; back to the roster's model")
        elif a.command == "available":
            from . import runner as runner_lib
            offered = runner_lib.list_models()
            cls = runner_lib.runner_class()
            if offered is None:
                print(f"the configured runner ({getattr(cls, 'label', '?')}) cannot list its models")
            for m in offered or []:
                print(f"  {m['id']:32} {m['label']}")
        elif a.command == "list":
            from . import runner as runner_lib
            roster = {x["name"]: x for x in _roster(td).get("agents", []) if isinstance(x, dict)}
            for name, spec in roster.items():
                model, effort, source = resolve(td, name, spec.get("model"), spec.get("effort"))
                ok = runner_lib.model_available(model, effort)
                flag = "   ** not offered by the configured runner **" if ok is False else ""
                print(f"  {name:10} {model or '(runner default)'}"
                      f"{'-' + effort if effort else ''}   [{source}]{flag}")
            for agent, row in overrides(td).items():
                if row.get("until"):
                    print(f"  ({agent} override expires {row['until']})")
        elif a.command == "usage":
            limits = allowances(td)
            used = usage(td, a.days)
            for model in sorted(set(used) | set(limits)):
                u = used.get(model, {"turns": 0, "tokens": 0})
                lim = limits.get(model)
                pct = f"  {u['tokens'] * 100 / lim:5.1f}% of {lim:,}/week" if lim else ""
                print(f"  {model:32} {u['turns']:5} turns  {u['tokens']:>13,} tokens{pct}")
    except ModelError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
