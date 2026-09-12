"""A second, independent observer implementation — SQLite instead of JSONL.

Deliberately written the way a third party would: it imports only the public
interface (agyteam.observer) and touches no agyteam internals. Its job is to
prove the observer seam is real, so swapping in a custom telemetry backend is
a config change rather than a code rewrite.

Config (AGYTEAM_OBSERVER_CONFIG): {"db": "/path/to/events.db"}
"""
import json
import os
import sqlite3
import time

from agyteam.observer import Observer


class SqliteObserver(Observer):
    label = "sqlite-fixture"

    def __init__(self, config=None):
        super().__init__(config)
        db = self.config.get("db") or os.environ.get("AGYTEAM_FIXTURE_OBSERVER_DB")
        if not db:
            raise SystemExit("SqliteObserver needs config {'db': ...}")
        self.db_path = db
        self.conn = sqlite3.connect(db, timeout=10)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, event TEXT, agent TEXT, "
            "conversation TEXT, duration_s REAL, input_tokens INTEGER, output_tokens INTEGER, "
            "cache_read_tokens INTEGER, total_tokens INTEGER, model TEXT, error TEXT, "
            "turns INTEGER, stopped_reason TEXT, reviewed INTEGER, "
            "tool TEXT, args TEXT, result TEXT, extra TEXT)"
        )
        for col, col_type in [("tool", "TEXT"), ("args", "TEXT"), ("result", "TEXT")]:
            try:
                self.conn.execute(f"ALTER TABLE events ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def record_turn(self, agent: str, conversation: str, duration_s: float,
                    input_tokens: int | None = None,
                    output_tokens: int | None = None,
                    cache_read_tokens: int | None = None,
                    total_tokens: int | None = None,
                    model: str | None = None,
                    **kwargs) -> None:
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            extra = json.dumps(kwargs, default=str) if kwargs else None
            self.conn.execute(
                "INSERT INTO events (ts, event, agent, conversation, duration_s, "
                "input_tokens, output_tokens, cache_read_tokens, total_tokens, model, extra) "
                "VALUES (?, 'turn', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, agent, conversation, round(duration_s, 2) if duration_s is not None else None,
                 input_tokens, output_tokens, cache_read_tokens, total_tokens, model, extra)
            )
            self.conn.commit()
        except Exception:
            pass

    def record_tool_call(self, agent: str, conversation: str, tool: str,
                         args: dict | None = None,
                         result: str | None = None,
                         error: str | None = None,
                         duration_s: float | None = None,
                         **kwargs) -> None:
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            truncated_res = result
            if truncated_res is not None:
                if not isinstance(truncated_res, str):
                    try:
                        truncated_res = json.dumps(truncated_res, default=str)
                    except Exception:
                        truncated_res = str(truncated_res)
                if len(truncated_res) > 20_000:
                    truncated_res = (
                        truncated_res[:20_000]
                        + f"\n...[truncated {len(truncated_res) - 20_000} chars]"
                    )
            args_json = json.dumps(args, default=str) if args is not None else None
            extra = json.dumps(kwargs, default=str) if kwargs else None
            error_str = str(error) if error is not None else None
            self.conn.execute(
                "INSERT INTO events (ts, event, agent, conversation, tool, args, result, error, duration_s, extra) "
                "VALUES (?, 'tool_call', ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, agent, conversation, tool, args_json, truncated_res, error_str,
                 round(duration_s, 2) if duration_s is not None else None, extra)
            )
            self.conn.commit()
        except Exception:
            pass

    def record_failure(self, agent: str, conversation: str, error: str,
                       duration_s: float | None = None, **kwargs) -> None:
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            extra = json.dumps(kwargs, default=str) if kwargs else None
            error_str = str(error) if error is not None else ""
            self.conn.execute(
                "INSERT INTO events (ts, event, agent, conversation, duration_s, error, extra) "
                "VALUES (?, 'failure', ?, ?, ?, ?, ?)",
                (ts, agent, conversation, round(duration_s, 2) if duration_s is not None else None,
                 error_str, extra)
            )
            self.conn.commit()
        except Exception:
            pass

    def record_episode(self, turns: int, stopped_reason: str,
                       reviewed: bool = False,
                       duration_s: float | None = None, **kwargs) -> None:
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            extra = json.dumps(kwargs, default=str) if kwargs else None
            self.conn.execute(
                "INSERT INTO events (ts, event, turns, stopped_reason, reviewed, duration_s, extra) "
                "VALUES (?, 'episode', ?, ?, ?, ?, ?)",
                (ts, turns, stopped_reason, 1 if reviewed else 0,
                 round(duration_s, 2) if duration_s is not None else None, extra)
            )
            self.conn.commit()
        except Exception:
            pass

    def events(self, event_type: str | None = None) -> list[dict]:
        try:
            query = ("SELECT ts, event, agent, conversation, duration_s, input_tokens, "
                     "output_tokens, cache_read_tokens, total_tokens, model, error, "
                     "turns, stopped_reason, reviewed, tool, args, result, extra FROM events")
            params = []
            if event_type:
                query += " WHERE event = ?"
                params.append(event_type)
            query += " ORDER BY id"
            cursor = self.conn.execute(query, params)
            records = []
            for row in cursor.fetchall():
                parsed_args = None
                if row[15] is not None:
                    try:
                        parsed_args = json.loads(row[15])
                    except (ValueError, TypeError):
                        parsed_args = row[15]
                ev = {
                    "ts": row[0],
                    "event": row[1],
                    "agent": row[2],
                    "conversation": row[3],
                    "duration_s": row[4],
                    "input_tokens": row[5],
                    "output_tokens": row[6],
                    "cache_read_tokens": row[7],
                    "total_tokens": row[8],
                    "model": row[9],
                    "error": row[10],
                    "turns": row[11],
                    "stopped_reason": row[12],
                    "reviewed": bool(row[13]) if row[13] is not None else False,
                    "tool": row[14],
                    "args": parsed_args,
                    "result": row[16],
                }
                records.append({k: v for k, v in ev.items() if v is not None})
                # restore event type
                records[-1]["event"] = row[1]
                if row[1] == "episode":
                    records[-1]["reviewed"] = bool(row[13])
            return records
        except sqlite3.Error:
            return []

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass
