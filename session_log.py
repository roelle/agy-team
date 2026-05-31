"""
session_log.py — Automatic conversation transcript logging.

Captures every turn to logs/YYYY-MM-DD.jsonl (raw audit trail).
Writes a session summary to the daily memory log at session end.

Logs are gitignored and pruned after LOG_RETENTION_DAYS.
They are never loaded into agent context — that's memory.md's job.
"""

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from google.antigravity.hooks import hooks
from google.antigravity import types

PROJECT_DIR = Path(__file__).parent
LOGS_DIR = PROJECT_DIR / "logs"
LOG_RETENTION_DAYS = 30

# Session state
_session: dict = {}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _log_path() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return LOGS_DIR / f"{today}.jsonl"


def _memory_path() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return PROJECT_DIR / "memory" / f"{today}.md"


def _append_log(entry: dict):
    LOGS_DIR.mkdir(exist_ok=True)
    with _log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _prune_old_logs():
    """Delete log files older than LOG_RETENTION_DAYS."""
    if not LOGS_DIR.exists():
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOG_RETENTION_DAYS)
    pruned = 0
    for log_file in LOGS_DIR.glob("*.jsonl"):
        try:
            file_date = datetime.strptime(log_file.stem, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            if file_date < cutoff:
                log_file.unlink()
                pruned += 1
        except ValueError:
            pass
    if pruned:
        print(f"  [log] Pruned {pruned} log file(s) older than {LOG_RETENTION_DAYS} days.")


def _append_memory(content: str):
    """Append a note to today's memory log."""
    mem = _memory_path()
    if not mem.exists():
        mem.parent.mkdir(exist_ok=True)
        mem.write_text(
            f"# Daily Log — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
            f"## Summary\n_(not yet distilled)_\n\n## Raw Log\n\n",
            encoding="utf-8",
        )
    with mem.open("a", encoding="utf-8") as f:
        f.write(content)


# ── Hooks ────────────────────────────────────────────────────────────────────

@hooks.on_session_start
async def log_session_start():
    _prune_old_logs()
    _session.clear()
    _session.update({
        "started_at": datetime.now(timezone.utc).isoformat(),
        "turn_count": 0,
        "tools_called": [],
        "role": "unknown",   # populated by agent.py after config resolves
    })
    _append_log({
        "event": "session_start",
        "ts": _session["started_at"],
    })


@hooks.pre_turn
async def log_pre_turn(data: str) -> types.HookResult:
    _session["_current_user_turn"] = data
    _session["_turn_start"] = time.monotonic()
    return types.HookResult(allow=True)


@hooks.post_turn
async def log_post_turn(data: str):
    turn = _session.get("turn_count", 0) + 1
    _session["turn_count"] = turn
    elapsed = time.monotonic() - _session.pop("_turn_start", time.monotonic())

    entry = {
        "event": "turn",
        "ts": datetime.now(timezone.utc).isoformat(),
        "turn": turn,
        "role": _session.get("role", "unknown"),
        "elapsed_s": round(elapsed, 1),
        "user": _session.pop("_current_user_turn", ""),
        "agent": data,
    }
    _append_log(entry)


@hooks.post_tool_call
async def log_tool_call(data: Any):
    tool_name = getattr(data, "name", str(data))
    _session.setdefault("tools_called", [])
    if tool_name not in _session["tools_called"]:
        _session["tools_called"].append(tool_name)
    _append_log({
        "event": "tool_call",
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "result_preview": str(getattr(data, "result", ""))[:200],
    })


@hooks.on_session_end
async def log_session_end():
    ended_at = datetime.now(timezone.utc)
    started = _session.get("started_at", ended_at.isoformat())
    try:
        start_dt = datetime.fromisoformat(started)
        duration_min = round((ended_at - start_dt).total_seconds() / 60, 1)
    except Exception:
        duration_min = 0

    _append_log({
        "event": "session_end",
        "ts": ended_at.isoformat(),
        "turns": _session.get("turn_count", 0),
        "duration_min": duration_min,
        "tools_used": _session.get("tools_called", []),
    })

    # Write session summary to daily memory log
    tools_used = _session.get("tools_called", [])
    turns = _session.get("turn_count", 0)
    role = _session.get("role", "unknown")
    summary = (
        f"\n### [Session end {ended_at.strftime('%H:%M UTC')}] "
        f"role={role} turns={turns} duration={duration_min}min"
        + (f" tools=[{', '.join(tools_used)}]" if tools_used else "")
        + "\n"
    )
    _append_memory(summary)


# ── Public: set role after config resolves ───────────────────────────────────

def set_session_role(role: str):
    """Call this from agent.py once the role is known."""
    _session["role"] = role


SESSION_LOG_HOOKS = [
    log_session_start,
    log_pre_turn,
    log_post_turn,
    log_tool_call,
    log_session_end,
]
