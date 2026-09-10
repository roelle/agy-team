"""A second, independent memory store — SQLite instead of markdown files.

Written the way a third party would: it imports only the public interface
(agyteam.memory) and touches no agyteam internals. Its job is to prove the
storage seam is real, so moving memory to a shared or internal backend is a
config change rather than a fork.

Config (AGYTEAM_MEMORY_CONFIG): {"db": "/path/to.db"}
"""
import os
import sqlite3

from agyteam.memory import MemoryEntry, MemoryStore


class SqliteMemory(MemoryStore):
    label = "sqlite-fixture"

    def __init__(self, agent, config=None):
        super().__init__(agent, config)
        db = self.config.get("db") or os.environ.get("AGYTEAM_FIXTURE_MEM_DB")
        if not db:
            raise SystemExit("SqliteMemory needs config {'db': ...}")
        self.conn = sqlite3.connect(db, timeout=10)
        # Namespaced by agent: a shared backend must not let teammates read or
        # overwrite each other's memories.
        self.conn.execute("CREATE TABLE IF NOT EXISTS mem ("
                          "agent TEXT, name TEXT, description TEXT, content TEXT,"
                          "PRIMARY KEY (agent, name))")
        self.conn.commit()

    def save(self, name, description, content):
        cur = self.conn.execute("SELECT 1 FROM mem WHERE agent=? AND name=?",
                                (self.agent, name))
        is_new = cur.fetchone() is None
        self.conn.execute("INSERT OR REPLACE INTO mem VALUES (?,?,?,?)",
                          (self.agent, name, description, content))
        self.conn.commit()
        return is_new

    def read(self, name):
        row = self.conn.execute(
            "SELECT content FROM mem WHERE agent=? AND name=?",
            (self.agent, name)).fetchone()
        return row[0] if row else None

    def delete(self, name):
        cur = self.conn.execute("DELETE FROM mem WHERE agent=? AND name=?",
                                (self.agent, name))
        self.conn.commit()
        return cur.rowcount > 0

    def index(self):
        return [MemoryEntry(name=n, description=d) for n, d in self.conn.execute(
            "SELECT name, description FROM mem WHERE agent=? ORDER BY name",
            (self.agent,))]

    def close(self):
        self.conn.close()
