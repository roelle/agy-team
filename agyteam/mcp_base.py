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
OUTPUT_BYTES = int(os.environ.get("AGYTEAM_TOOL_OUTPUT_BYTES", 3800))


def fit(blocks: list[str], more: str, sep: str = "\n\n", head: str = "",
        limit: int | None = None) -> tuple[str, int]:
    """Join as many of `blocks`, in order, as fit in `limit` bytes.

    Returns (text, how many blocks it holds). The first block is always
    returned whole, however long: an item cut in half is worse than one that
    spills. When blocks are left out, `more` -- formatted with n, the number
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
    text = head + sep.join(blocks[:used])
    if used < len(blocks):
        text += sep + more.format(n=len(blocks) - used)
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
