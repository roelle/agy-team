"""Pre-tool-call hook for any host that can run a command and read its exit code.

    python -m agyteam.hook_pre_tool_use < call.json

Reads one JSON object describing the call from stdin, asks
agyteam.policy.check_tool_policy, and exits 0 to allow or 2 to block, with the
verdict on stdout and the refusal on stderr. Hosts
differ in what they send; the common key names are accepted:

    tool:   "tool_name" | "tool" | "name", or nested as
            "toolCall" | "tool_call": {"name": ..., "args": ...}
    args:   "tool_input" | "args" | "arguments" | "input" | "params"
    agent:  AGYTEAM_AGENT in the environment, else "agent" | "agent_name" in the
            JSON, else the conversation id ("conversation_id" | "conversationId"
            | "session_id" | "sessionId" | "conversation") looked up in
            <team_dir>/conversations.json, live or retired.

The verdict on stdout is {"decision": "deny", "reason": ...} with exit 2, or
{"decision": "allow"} with exit 0; "block": true accompanies a deny for hosts
that read that word.

With AGYTEAM_AUDIT_LOG set, every call is also appended there, refused or
not, in the line format the SDK runner writes: {"ts", "team", "agent",
"tool", "args"} with each argument cut to 2,000 characters, plus "refused"
when it was. The review gate counts an agent's audited calls as evidence it
worked; until this hook wrote them, only the SDK runner's agents could
produce that evidence. Refused calls are recorded but never counted.

If no agent can be identified the call is allowed and a note goes to stderr:
policy that cannot tell who is asking has nothing to apply, and blocking
every call on a host whose payload this does not understand would stop the
team rather than protect it. Map the host's payload to these keys in a
two-line wrapper if it uses others. AGYTEAM_TEAM_DIR names the team.
"""
import datetime
import json
import os
import sys
from pathlib import Path

from . import policy


def agent_for(payload: dict, team_dir: Path) -> str | None:
    agent = os.environ.get("AGYTEAM_AGENT") or payload.get("agent") or payload.get("agent_name")
    if agent:
        return str(agent)
    conv = next((payload[k] for k in ("conversation_id", "conversationId", "session_id",
                                      "sessionId", "conversation") if payload.get(k)), None)
    if not conv:
        return None
    try:
        live = json.loads((team_dir / "conversations.json").read_text())
        for name, cid in live.items():
            if cid == conv:
                return name
    except (OSError, ValueError):
        pass
    try:
        for line in reversed((team_dir / "conversations_retired.jsonl").read_text().splitlines()):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("conversation") == conv:
                return rec.get("agent")
    except OSError:
        pass
    return None


def parse(payload: dict, team_dir: Path) -> tuple[str | None, str, dict]:
    """(agent, tool, args) from whichever shape this host sends."""
    call = payload.get("toolCall") or payload.get("tool_call")
    if isinstance(call, dict):
        payload = {**payload, **{k: v for k, v in call.items() if k not in payload}}
    tool = payload.get("tool_name") or payload.get("tool") or payload.get("name") or ""
    args = None
    for k in ("tool_input", "args", "arguments", "input", "params"):
        if isinstance(payload.get(k), dict):
            args = payload[k]
            break
    return agent_for(payload, team_dir), str(tool), args or {}


def decide(payload: dict, team_dir: Path) -> tuple[int, str]:
    agent, tool, args = parse(payload, team_dir)
    if not agent:
        return 0, "[hook: no agent identified for this call; policy not applied]"
    refusal = policy.check_tool_policy(agent, tool, args, team_dir=team_dir)
    if refusal:
        return 2, refusal
    return 0, ""


def audit(agent: str | None, tool: str, args: dict, refusal: str = "") -> None:
    """Append one line to AGYTEAM_AUDIT_LOG, if set. Never raises."""
    path = os.environ.get("AGYTEAM_AUDIT_LOG", "").strip()
    if not path:
        return
    entry = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
             "team": os.environ.get("AGYTEAM_TEAM", ""),
             "agent": agent, "tool": tool,
             "args": {str(k): str(v)[:2000] for k, v in args.items()}}
    if refusal:
        entry["refused"] = refusal[:500]
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass                        # a full disk must not stop the team


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        print("[hook: stdin was not JSON; call allowed]", file=sys.stderr)
        return 0
    if not isinstance(payload, dict):
        return 0
    from . import scope
    team_dir = scope.team_dir(os.environ.get("AGYTEAM_TEAM_DIR"))
    code, text = decide(payload, team_dir)
    agent, tool, args = parse(payload, team_dir)
    audit(agent, tool, args, text if code else "")
    if code == 0:
        if text:
            print(text, file=sys.stderr)
        print(json.dumps({"decision": "allow"}))
        return 0
    print(json.dumps({"decision": "deny", "block": True, "reason": text}))
    print(text, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
