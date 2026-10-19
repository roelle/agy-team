"""Did an agent actually do anything? Read from the record, never asked.

Shared by the review gate (an author must have worked since the episode
began), task closure (every collaborator must have worked since the task was
created), and anything else that must not take an agent's word for it.
"""
import json
import math
import os
from datetime import datetime, timedelta
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
    """When the episode a review recorded now belongs to began; "" for all history.

    Reviews are scoped to an episode. Without a boundary the only available
    question is "has this agent ever done anything", which a long-lived team
    answers yes to forever.

    The episode is the one in progress if there is one, otherwise the one
    that just finished. This used to be the *end* of the last completed
    episode, which left a review recorded after an episode -- the operator
    asking qa to look once the run is over -- unable to name any work done in
    it: everything the author did came before the boundary. It passed only
    when the work and the episode's end fell in the same second.

    The supervisor records "episode_start" when an episode begins and
    "episode" when it ends; events.jsonl is append-only, so which came last is
    read from file order, not from second-resolution timestamps that a start
    and the previous end can share. An episode recorded before starts were
    marked has its start derived from its duration, rounded earlier; with no
    duration either, its end is the boundary, which is strict rather than
    open-ended.
    """
    marks = [e for e in jsonl(team_dir / "events.jsonl")
             if e.get("event") in ("episode_start", "episode")]
    if not marks:
        return ""
    last = marks[-1]
    if last.get("event") == "episode_start":
        return last.get("ts") or ""
    if len(marks) > 1 and marks[-2].get("event") == "episode_start":
        return marks[-2].get("ts") or ""
    end = (last.get("ts") or "").replace("T", " ").replace("Z", "")
    dur = last.get("duration_s")
    if end and isinstance(dur, (int, float)) and not isinstance(dur, bool) and dur >= 0:
        try:
            began = datetime.strptime(end, "%Y-%m-%d %H:%M:%S") - timedelta(seconds=math.ceil(dur))
            return began.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return end


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
    since = (since or "").replace("T", " ").replace("Z", "")
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
            # A refused call is recorded, but it is not work.
            calls = [e for e in audit if e.get("agent") == author
                     and not e.get("refused")
                     and not (team and e.get("team") and e["team"] != team)
                     and (e.get("ts") or "").replace("T", " ") >= since]
            if calls:
                evidence.append(f"{len(calls)} tool call(s)")

    return bool(channels), evidence
