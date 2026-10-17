"""Tiny dependency-free MCP stdio server scaffold (JSON-RPC 2.0 over stdin/stdout).

Shared by agyteam's memory and bus servers so both speak identical protocol.
"""
import json
import os
import sys

PROTOCOL_VERSION = "2025-06-18"

#: Ceiling, in UTF-8 bytes, on what a listing tool returns inline. The agy
#: CLI writes a tool result over about 4,000 bytes to a file and hands the
#: agent the path instead, so a long inbox or task list costs a second tool
#: call to read -- and on a confined agent that file is outside its
#: workspace, so the read is refused and the content never arrives. Listings
#: stop under it and say how many items they left out. 0 means no ceiling.
#: 3,200 leaves room under the spill points measured on hosts (3,500-4,000).
OUTPUT_BYTES = int(os.environ.get("AGYTEAM_TOOL_OUTPUT_BYTES", 3200))


def truncate_utf8(text: str, max_bytes: int) -> str:
    """`text` cut to at most `max_bytes` UTF-8 bytes, never inside a character."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max(0, max_bytes)].decode("utf-8", errors="ignore")


def fit(blocks: list[str], more: str, sep: str = "\n\n", head: str = "",
        limit: int | None = None) -> tuple[str, int]:
    """Join as many of `blocks`, in order, as fit in `limit` bytes.

    Returns (text, how many blocks it holds). The first block is always
    included; if it alone is over the limit it is cut, on a character
    boundary, with a note saying so. It used to be returned whole on the
    theory that a spilled result is still readable by its agent -- but a
    confined agent is refused the spill file, and then it read nothing. A
    caller whose block must not be lost (check_inbox) saves it elsewhere
    first. When blocks are left out, `more` -- formatted with n, the number
    left out -- is appended, and counted against the limit.
    """
    limit = OUTPUT_BYTES if limit is None else limit
    size = lambda t: len(t.encode("utf-8"))              # noqa: E731
    if limit <= 0 or not blocks:
        return head + sep.join(blocks), len(blocks)
    used = 1
    while used < len(blocks):
        rest = len(blocks) - used - 1
        candidate = head + sep.join(blocks[:used + 1])
        if rest:
            candidate += sep + more.format(n=rest)
        if size(candidate) > limit:
            break
        used += 1
    tail = sep + more.format(n=len(blocks) - used) if used < len(blocks) else ""
    text = head + sep.join(blocks[:used]) + tail
    if used == 1 and size(text) > limit:
        note = f"\n[... cut at the {limit}-byte inline limit]"
        budget = max(64, limit - size(head) - size(tail) - size(note))
        text = head + truncate_utf8(blocks[0], budget) + note + tail
    return text, used


def tool(name: str, description: str, properties: dict | None = None,
         required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {},
                            **({"required": required} if required else {})}}


def string(description: str) -> dict:
    return {"type": "string", "description": description}


def serve(server_name: str, tools: list[dict], dispatch) -> None:
    """Run the stdio loop. `dispatch(tool_name, args) -> str`."""
    def reply(msg_id, result=None, error=None):
        body = {"jsonrpc": "2.0", "id": msg_id}
        body.update({"error": error} if error else {"result": result})
        print(json.dumps(body), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, msg_id = msg.get("method"), msg.get("id")
        if msg_id is None:          # notification — no response expected
            continue
        if method == "initialize":
            reply(msg_id, {
                "protocolVersion": msg.get("params", {}).get(
                    "protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": server_name, "version": "0.1"}})
        elif method == "tools/list":
            reply(msg_id, {"tools": tools})
        elif method == "tools/call":
            p = msg.get("params", {})
            try:
                out = dispatch(p.get("name", ""), p.get("arguments", {}) or {})
                err = False
            except Exception as e:
                out, err = f"[error: {e}]", True
            reply(msg_id, {"content": [{"type": "text", "text": out}],
                           "isError": err})
        elif method == "ping":
            reply(msg_id, {})
        else:
            reply(msg_id, error={"code": -32601,
                                 "message": f"method not found: {method}"})
