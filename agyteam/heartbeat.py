"""Is a supervisor driving this team right now?

The supervisor writes <team_dir>/supervisor.json every few seconds while it
runs and removes it on a clean exit. Tools that promise something will happen
later -- a message will be read, a reminder will fire -- read it, so they can
say which. "[delivered to X]" was read by a manager as X having received it;
eleven times in seven minutes it waited on a teammate nothing was going to
wake.
"""
import calendar
import json
import os
import time
from pathlib import Path

FILENAME = "supervisor.json"
STALE_AFTER_S = 120


def path(team_dir: Path | str) -> Path:
    return Path(team_dir) / FILENAME


def running(team_dir: Path | str) -> dict | None:
    """The live supervisor's heartbeat, or None.

    Stale if the process is gone or the file is older than two minutes.
    """
    try:
        doc = json.loads(path(team_dir).read_text(encoding="utf-8"))
        ts = time.strptime(doc["ts"], "%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if time.time() - calendar.timegm(ts) > STALE_AFTER_S:
        return None
    pid = doc.get("pid")
    if isinstance(pid, int) and pid != os.getpid():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass
    return doc


def describe(team_dir: Path | str) -> str:
    """One clause for a tool result: who, if anyone, will act on this."""
    beat = running(team_dir)
    if beat:
        return "a supervisor is running and will deliver it"
    return ("no supervisor is running, so nothing will deliver it until one "
            "is started (python -m agyteam.supervisor --daemon)")
