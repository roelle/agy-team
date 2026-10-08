"""Stop hook for any host whose turns HostRunner waits on.

    python -m agyteam.hook_stop < turn.json

HostRunner knows a turn has ended when <signal_dir>/<conversation>.jsonl
gains a line. Every host needs something to write that line, and until now
every integrator wrote their own. This is that half, generic: it reads the
JSON the host's stop hook is given on stdin and appends one line,

    {"ts", "error"?, "input_tokens"?, "output_tokens"?, "cache_read_tokens"?,
     "total_tokens"?}

with each optional field present only when the payload had it. Accepted
payload keys:

    conversation:  "conversation_id" | "conversationId" | "session_id" |
                   "sessionId" | "conversation"
    error:         "error" (a string, or an object with "message")
    tokens:        snake_case or camelCase, at the top level or under
                   "usage" | "tokenUsage" | "tokens"

The signal directory is AGYTEAM_SIGNAL_DIR, or else the "signal_dir" (and
"signal_suffix") in AGYTEAM_RUNNER_CONFIG, so the hook and the runner can
share one setting.

Some hosts call their stop hook for pauses as well as turn ends. Set
AGYTEAM_STOP_IDLE_KEY to the payload key that says which (e.g. "fullyIdle"):
a payload where that key is false, and that carries no error, writes
nothing. An error is always written; a turn that failed is over.

It exits 0 whatever happens, with any problem on stderr: a stop hook that
fails can wedge the host's turn, and a missing line costs only a timeout.
"""
import json
import os
import sys
import time
from pathlib import Path

CONVERSATION_KEYS = ("conversation_id", "conversationId", "session_id",
                     "sessionId", "conversation")
TOKENS = {"input_tokens": ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"),
          "output_tokens": ("output_tokens", "outputTokens", "completion_tokens",
                            "completionTokens"),
          "cache_read_tokens": ("cache_read_tokens", "cacheReadTokens",
                                "cached_tokens", "cachedTokens"),
          "total_tokens": ("total_tokens", "totalTokens")}


def signal_file(conversation: str) -> Path | None:
    directory = os.environ.get("AGYTEAM_SIGNAL_DIR", "").strip()
    suffix = ".jsonl"
    try:
        config = json.loads(os.environ.get("AGYTEAM_RUNNER_CONFIG") or "{}")
    except ValueError:
        config = {}
    if isinstance(config, dict):
        directory = directory or str(config.get("signal_dir") or "")
        suffix = str(config.get("signal_suffix") or suffix)
    if not directory:
        return None
    return Path(directory).expanduser() / f"{conversation}{suffix}"


def line_for(payload: dict) -> dict | None:
    """The signal line for this payload, or None if it is not a turn end."""
    err = payload.get("error")
    if isinstance(err, dict):
        err = err.get("message") or json.dumps(err)
    idle_key = os.environ.get("AGYTEAM_STOP_IDLE_KEY", "").strip()
    if idle_key and payload.get(idle_key) is False and not err:
        return None
    line = {"ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    if err:
        line["error"] = str(err)
    sources = [payload] + [payload[k] for k in ("usage", "tokenUsage", "tokens")
                           if isinstance(payload.get(k), dict)]
    for field, names in TOKENS.items():
        for src in sources:
            value = next((src[n] for n in names if isinstance(src.get(n), (int, float))
                          and not isinstance(src.get(n), bool)), None)
            if value is not None:
                line[field] = int(value)
                break
    return line


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        print("[hook_stop: stdin was not JSON; nothing written]", file=sys.stderr)
        return 0
    if not isinstance(payload, dict):
        return 0
    conv = next((str(payload[k]) for k in CONVERSATION_KEYS if payload.get(k)), None)
    if not conv:
        print("[hook_stop: no conversation id in the payload; nothing written]",
              file=sys.stderr)
        return 0
    if "/" in conv or conv.startswith("."):
        print(f"[hook_stop: refusing conversation id {conv!r}]", file=sys.stderr)
        return 0
    line = line_for(payload)
    if line is None:
        return 0
    path = signal_file(conv)
    if path is None:
        print("[hook_stop: set AGYTEAM_SIGNAL_DIR, or signal_dir in "
              "AGYTEAM_RUNNER_CONFIG; nothing written]", file=sys.stderr)
        return 0
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # One write of one short line with O_APPEND: two hooks firing at
        # once cannot interleave inside it.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, (json.dumps(line) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as e:
        print(f"[hook_stop: could not write {path}: {e}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
