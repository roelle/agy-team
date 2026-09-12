"""A second, independent A2A transport — SQLite instead of files.

Deliberately written the way a third party would: it imports only the public
interface (agyteam.transport) and touches no agyteam internals. Its job is to
prove the transport seam is real, so that swapping in a native/internal A2A
implementation is a config change rather than a fork.

Config (AGYTEAM_BUS_CONFIG): {"db": "/path/to.db", "roster": [{"name","role"}]}
"""
import json
import os
import sqlite3
import time

from agyteam.transport import Message, Transport


class SqliteTransport(Transport):
    label = "sqlite-fixture"
    supports_roster_admin = True

    def __init__(self, me, config=None):
        super().__init__(me, config)
        db = self.config.get("db") or os.environ.get("AGYTEAM_FIXTURE_DB")
        if not db:
            raise SystemExit("SqliteTransport needs config {'db': ...}")
        self.conn = sqlite3.connect(db, timeout=10)
        self.conn.execute("CREATE TABLE IF NOT EXISTS msg ("
                          "id INTEGER PRIMARY KEY, ts TEXT, sender TEXT, "
                          "recipient TEXT, content TEXT, consumed INT DEFAULT 0)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS roster ("
                          "name TEXT PRIMARY KEY, role TEXT)")
        for a in self.config.get("roster", []):
            self.conn.execute("INSERT OR IGNORE INTO roster VALUES (?,?)",
                              (a["name"], a.get("role", "")))
        self.conn.commit()

    def _names(self):
        return [r[0] for r in self.conn.execute("SELECT name FROM roster")]

    def teammates(self):
        return [{"name": n, "role": r} for n, r in
                self.conn.execute("SELECT name, role FROM roster ORDER BY name")
                if n != self.me]

    def send(self, to, content):
        known = self._names()
        if to != "user" and known and to not in known:
            return (f"[error: no teammate named '{to}'. Teammates: "
                    f"{', '.join(known)}, user]")
        self.conn.execute(
            "INSERT INTO msg (ts, sender, recipient, content) VALUES (?,?,?,?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), self.me, to, content))
        self.conn.commit()
        return (f"[delivered to {to}]" if to != "user"
                else "[delivered to the user — they will see it in the team log]")

    def peek(self):
        rows = list(self.conn.execute(
            "SELECT id, ts, sender, recipient, content FROM msg "
            "WHERE recipient=? AND consumed=0 ORDER BY (CASE WHEN sender='user' THEN 0 ELSE 1 END), id", (self.me,)))
        return [Message(ts=r[1], sender=r[2], to=r[3], content=r[4], id=r[0]) for r in rows]

    def fetch(self):
        rows = list(self.conn.execute(
            "SELECT id, ts, sender, recipient, content FROM msg "
            "WHERE recipient=? AND consumed=0 ORDER BY (CASE WHEN sender='user' THEN 0 ELSE 1 END), id", (self.me,)))
        return [Message(ts=r[1], sender=r[2], to=r[3], content=r[4], id=r[0]) for r in rows]

    def acknowledge(self, msgs=None):
        if msgs is None:
            self.conn.execute(
                "UPDATE msg SET consumed=1 WHERE recipient=? AND consumed=0",
                (self.me,),
            )
            self.conn.commit()
            return
        ids = []
        fallback_msgs = []
        for m in msgs:
            msg_id = getattr(m, "id", None)
            if msg_id is not None:
                ids.append(msg_id)
            else:
                fallback_msgs.append(m)
        if ids:
            placeholders = ",".join("?" * len(ids))
            self.conn.execute(
                f"UPDATE msg SET consumed=1 WHERE id IN ({placeholders})",
                ids,
            )
        for m in fallback_msgs:
            sender = getattr(m, "sender", getattr(m, "from", "unknown"))
            ts = getattr(m, "ts", None)
            content = getattr(m, "content", "")
            self.conn.execute(
                "UPDATE msg SET consumed=1 WHERE id IN ("
                "SELECT id FROM msg WHERE recipient=? AND sender=? AND ts=? AND content=? AND consumed=0 LIMIT 1)",
                (self.me, sender, ts, content),
            )
        self.conn.commit()

    def requeue(self, msgs_or_role, msgs=None):
        if msgs is None:
            if isinstance(msgs_or_role, str):
                return
            messages = list(msgs_or_role or [])
            explicit_role = None
        else:
            explicit_role = msgs_or_role
            messages = list(msgs or [])
        if not messages:
            return

        for m in messages:
            recip = explicit_role or getattr(m, "to", None) or self.me
            sender = getattr(m, "sender", getattr(m, "from", "unknown"))
            ts = getattr(m, "ts", time.strftime("%Y-%m-%d %H:%M:%S"))
            content = getattr(m, "content", "")
            unconsumed = self.conn.execute(
                "SELECT id FROM msg WHERE recipient=? AND sender=? AND ts=? AND content=? AND consumed=0 LIMIT 1",
                (recip, sender, ts, content),
            ).fetchone()
            if unconsumed:
                continue
            row = self.conn.execute(
                "SELECT id FROM msg WHERE recipient=? AND sender=? AND ts=? AND content=? AND consumed=1 ORDER BY id DESC LIMIT 1",
                (recip, sender, ts, content),
            ).fetchone()
            if row:
                self.conn.execute("UPDATE msg SET consumed=0 WHERE id=?", (row[0],))
            else:
                self.conn.execute(
                    "INSERT INTO msg (ts, sender, recipient, content, consumed) VALUES (?,?,?,?,0)",
                    (ts, sender, recip, content),
                )
        self.conn.commit()

    def roster_add(self, name, role):
        if name in self._names():
            return f"[error: '{name}' is already on the roster]"
        self.conn.execute("INSERT INTO roster VALUES (?,?)", (name, role))
        self.conn.commit()
        return f"[added '{name}'. Roster: {', '.join(sorted(self._names()))}]"

    def roster_remove(self, name):
        if name not in self._names():
            return f"[error: no teammate named '{name}']"
        self.conn.execute("DELETE FROM roster WHERE name=?", (name,))
        self.conn.commit()
        return f"[removed '{name}'. Roster: {', '.join(sorted(self._names())) or 'nobody'}]"

    def close(self):
        self.conn.close()
