"""Minimal MCP (Model Context Protocol) stdio server exposing Clawy's memory.

Zero dependencies: newline-delimited JSON-RPC 2.0 over stdin/stdout.
Any MCP host can mount the same workspace memory the CLI/team agents use —
the google-antigravity SDK (McpStdioServer), the Antigravity hub
(~/.gemini/antigravity/mcp_config.json), Claude Code, etc.

Usage: python -m clawagy.mcp_memory <workspace_dir>
"""
import json
import sys
from pathlib import Path

from .tools import Toolbox

PROTOCOL_VERSION = "2025-06-18"

TOOL_DEFS = [
    {"name": "save_memory",
     "description": ("Save a durable memory so future sessions of this agent "
                     "know it: user preferences, corrections, facts about "
                     "systems/projects, lessons learned. Overwrites an existing "
                     "name — read first and merge if unsure. Returns the "
                     "refreshed memory index."),
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Short kebab-case topic name"},
         "description": {"type": "string", "description": "One line for the index"},
         "content": {"type": "string", "description": "Markdown content"}},
         "required": ["name", "description", "content"]}},
    {"name": "read_memory",
     "description": "Read one memory file by name; lists available names on a miss.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Memory name from the index"}},
         "required": ["name"]}},
    {"name": "delete_memory",
     "description": "Delete a memory that is wrong or obsolete.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Memory name"}},
         "required": ["name"]}},
    {"name": "memory_index",
     "description": "Return the full memory index (name + one-line description each).",
     "inputSchema": {"type": "object", "properties": {}}},
]


def serve(workspace: Path):
    box = Toolbox(workspace)

    def dispatch(tool: str, args: dict) -> str:
        index = lambda: (box.workspace / "MEMORY.md").read_text()
        if tool == "save_memory":
            return f"{box.save_memory(**args)}\nCurrent memory index:\n{index()}"
        if tool == "read_memory":
            return box.read_memory(**args)
        if tool == "delete_memory":
            return f"{box.delete_memory(**args)}\nCurrent memory index:\n{index()}"
        if tool == "memory_index":
            return index()
        return f"[error: unknown tool '{tool}']"

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, msg_id = msg.get("method"), msg.get("id")
        if msg_id is None:      # notification (e.g. notifications/initialized)
            continue
        if method == "initialize":
            result = {"protocolVersion": msg.get("params", {}).get(
                          "protocolVersion", PROTOCOL_VERSION),
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "clawagy-memory", "version": "0.1"}}
        elif method == "tools/list":
            result = {"tools": TOOL_DEFS}
        elif method == "tools/call":
            p = msg.get("params", {})
            try:
                out = dispatch(p.get("name", ""), p.get("arguments", {}) or {})
                result = {"content": [{"type": "text", "text": out}],
                          "isError": False}
            except Exception as e:
                result = {"content": [{"type": "text", "text": f"[error: {e}]"}],
                          "isError": True}
        elif method == "ping":
            result = {}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {
                "code": -32601, "message": f"method not found: {method}"}}),
                flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}),
              flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m clawagy.mcp_memory <workspace_dir>")
    serve(Path(sys.argv[1]))
