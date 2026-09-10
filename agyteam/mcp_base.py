"""Tiny dependency-free MCP stdio server scaffold (JSON-RPC 2.0 over stdin/stdout).

Shared by agyteam's memory and bus servers so both speak identical protocol.
"""
import json
import sys

PROTOCOL_VERSION = "2025-06-18"


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
