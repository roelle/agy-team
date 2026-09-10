"""File-backed observer.

Default implementation of the observer seam. Records turn completions,
failures, and episodes as JSON lines in <team_dir>/events.jsonl.
Also maintains <team_dir>/usage.jsonl for backward compatibility with
mcp_self.py:my_activity so agents can introspect their own usage history.

Standard library only. Never raises on write failures: accounting must never
break a turn or supervisor loop.
"""
import json
import os
import time
from pathlib import Path

from .observer import Observer


class FileObserver(Observer):
    """File-backed observer writing JSONL records to the team directory."""

    label = "file"

    def __init__(self, config: dict | None = None):
        super().__init__(config)

    @property
    def team_dir(self) -> Path:
        if "team_dir" in self.config:
            return Path(self.config["team_dir"])
        env = os.environ.get("AGYTEAM_TEAM_DIR")
        if env:
            return Path(env)
        from . import scope
        return scope.load().team_dir()

    @property
    def events_path(self) -> Path:
        return self.team_dir / "events.jsonl"

    @property
    def usage_path(self) -> Path:
        return self.team_dir / "usage.jsonl"

    def _write_event(self, record: dict) -> None:
        try:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            pass  # accounting must never break a turn

    def record_turn(self, agent: str, conversation: str, duration_s: float,
                    input_tokens: int | None = None,
                    output_tokens: int | None = None,
                    cache_read_tokens: int | None = None,
                    total_tokens: int | None = None,
                    model: str | None = None,
                    **kwargs) -> None:
        event = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": "turn",
            "agent": agent,
            "conversation": conversation,
            "duration_s": round(duration_s, 2) if duration_s is not None else None,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "total_tokens": total_tokens,
            "model": model,
            **kwargs,
        }
        self._write_event(event)

        # Mirror turn usage to usage.jsonl so mcp_self:my_activity continues
        # to reflect turns across both AgyRunner and SdkRunner without modifying mcp_self.
        usage_entry = {
            "ts": event["ts"],
            "agent": agent,
            "conversation": conversation,
            "duration_s": event["duration_s"] if event["duration_s"] is not None else 0.0,
        }
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "total_tokens"):
            v = event.get(k)
            if v is not None:
                usage_entry[k] = v
        try:
            self.usage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_path.open("a") as f:
                f.write(json.dumps(usage_entry) + "\n")
        except OSError:
            pass

    def record_failure(self, agent: str, conversation: str, error: str,
                       duration_s: float | None = None, **kwargs) -> None:
        event = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": "failure",
            "agent": agent,
            "conversation": conversation,
            "error": error,
            "duration_s": round(duration_s, 2) if duration_s is not None else None,
            **kwargs,
        }
        self._write_event(event)

    def record_episode(self, turns: int, stopped_reason: str,
                       reviewed: bool = False,
                       duration_s: float | None = None, **kwargs) -> None:
        event = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": "episode",
            "turns": turns,
            "stopped_reason": stopped_reason,
            "reviewed": reviewed,
            "duration_s": round(duration_s, 2) if duration_s is not None else None,
            **kwargs,
        }
        self._write_event(event)

    def events(self, event_type: str | None = None) -> list[dict]:
        """Return recorded events, or [] if missing or empty."""
        try:
            if not self.events_path.exists():
                return []
            records = []
            for line in self.events_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if event_type is None or rec.get("event") == event_type:
                        records.append(rec)
                except json.JSONDecodeError:
                    continue
            return records
        except OSError:
            return []
