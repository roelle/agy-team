"""Did an agent actually do anything? Read from the record, never asked.

Shared by the review gate (an author must have worked since the episode
began), task closure (every collaborator must have worked since the task was
created), and anything else that must not take an agent's word for it.
"""
import json
import os
from pathlib import Path


def jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, skipping anything unparseable. Missing file is []."""
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def episode_start(team_dir: Path) -> str:
    """Timestamp of the last completed episode, or "" for the whole history.

    Reviews are scoped to the episode in progress. Without a boundary the
    only available question is "has this agent ever done anything", which a
    long-lived team answers yes to forever.
    """
    eps = [e.get("ts") or "" for e in jsonl(team_dir / "events.jsonl")
           if e.get("event") == "episode"]
    return max(eps) if eps else ""


def author_activity(team_dir: Path, author: str, since: str) -> tuple[bool, list[str]]:
    """Did `author` actually do anything since `since`?

    Returns (verifiable, evidence). `verifiable` is False only when no channel
    could be read at all -- which is not the same as the author having been
    idle, and must not be reported as though it were.

    Three independent channels, because each can be absent for its own
    reason: the bus is always present but an agent can work without
    publishing; turn events exist whenever the supervisor drove the turn; the
    audit log exists only on a runner that records tool calls.

    `since` may be in either timestamp form the record uses ("YYYY-MM-DD
    HH:MM:SS" or ISO-8601 with a T); both compare as text once the T is
    normalised.
    """
    if since:
        if "T" in since or "Z" in since:
            try:
                from datetime import datetime
                raw_iso = since if "T" in since else since.replace(" ", "T")
                if not raw_iso.endswith("Z") and "+" not in raw_iso and "-" not in raw_iso[10:]:
                    raw_iso += "Z"
                dt = datetime.fromisoformat(raw_iso.replace("Z", "+00:00"))
                since = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                since = since.replace("T", " ").replace("Z", "")
        else:
            since = since.replace("T", " ").replace("Z", "")
    else:
        since = ""
    evidence, channels = [], 0

    bus = jsonl(team_dir / "bus.jsonl")
    if bus:
        channels += 1
        sent = [e for e in bus
                if (e.get("from") or e.get("frm") or e.get("sender")) == author
                and (e.get("ts") or "") >= since]
        if sent:
            evidence.append(f"{len(sent)} bus message(s)")

    events = jsonl(team_dir / "events.jsonl")
    if events:
        channels += 1
        turns = [e for e in events
                 if e.get("event") in ("turn", "failure")
                 and e.get("agent") == author and (e.get("ts") or "") >= since]
        if turns:
            evidence.append(f"{len(turns)} recorded turn(s)")

    audit_env = os.environ.get("AGYTEAM_AUDIT_LOG")
    if audit_env:
        audit = jsonl(Path(audit_env))
        if audit:
            channels += 1
            # One audit file can serve several teams whose agent names
            # collide; that is why each entry carries its team. Matching on
            # the name alone let another team's "coder" vouch for this one's.
            team = os.environ.get("AGYTEAM_TEAM", "")
            calls = [e for e in audit if e.get("agent") == author
                     and not (team and e.get("team") and e["team"] != team)
                     and (e.get("ts") or "").replace("T", " ") >= since]
            if calls:
                evidence.append(f"{len(calls)} tool call(s)")

    return bool(channels), evidence
