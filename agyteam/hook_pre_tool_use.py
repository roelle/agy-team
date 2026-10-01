"""Pre-tool-call hook for any host that can run a command and read its exit code.

    python -m agyteam.hook_pre_tool_use < call.json

Reads one JSON object describing the call from stdin, asks
agyteam.policy.check_tool_policy, and exits 0 to allow or 2 to block, with the
refusal on stdout (as {"decision": "block", "reason": ...}) and stderr. Hosts
differ in what they send; the common key names are accepted:

    tool:   "tool_name" | "tool" | "name"
    args:   "tool_input" | "args" | "arguments" | "input" | "params"
    agent:  AGYTEAM_AGENT in the environment, else "agent" | "agent_name" in the
            JSON, else the conversation id ("conversation_id" | "session_id" |
            "conversation") looked up in <team_dir>/conversations.json, live or
            retired.

If no agent can be identified the call is allowed and a note goes to stderr:
policy that cannot tell who is asking has nothing to apply, and blocking
every call on a host whose payload this does not understand would stop the
team rather than protect it. Map the host's payload to these keys in a
two-line wrapper if it uses others. AGYTEAM_TEAM_DIR names the team.
"""
import json
import os
import sys
from pathlib import Path

from . import policy


def agent_for(payload: dict, team_dir: Path) -> str | None:
    agent = os.environ.get("AGYTEAM_AGENT") or payload.get("agent") or payload.get("agent_name")
    if agent:
        return str(agent)
    conv = payload.get("conversation_id") or payload.get("session_id") or payload.get("conversation")
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


def decide(payload: dict, team_dir: Path) -> tuple[int, str]:
    tool = payload.get("tool_name") or payload.get("tool") or payload.get("name") or ""
    args = None
    for k in ("tool_input", "args", "arguments", "input", "params"):
        if isinstance(payload.get(k), dict):
            args = payload[k]
            break
    agent = agent_for(payload, team_dir)
    if not agent:
        return 0, "[hook: no agent identified for this call; policy not applied]"
    refusal = policy.check_tool_policy(agent, str(tool), args or {}, team_dir=team_dir)
    if refusal:
        return 2, refusal
    return 0, ""


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
    if code == 0:
        if text:
            print(text, file=sys.stderr)
        return 0
    print(json.dumps({"decision": "block", "reason": text}))
    print(text, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
