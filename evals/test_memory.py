"""Store-agnostic contract suite for durable memory.

Runs the identical scenario against every configured store, so a new backend
(shared database, knowledge service, internal store) can be validated before it
is trusted. This is the suite agyteam/memory_template.py tells you to run.

  .venv/bin/python evals/test_memory.py
  AGYTEAM_MEMORY_STORE=example_memory:MyStore \
  AGYTEAM_MEMORY_CONFIG='{"dsn":"..."}' .venv/bin/python evals/test_memory.py

With no arguments it tests the built-in file store plus an independent SQLite
fixture — if both pass, the seam is real and not file-specific.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rpc_util import PY, ROOT, check, rpc, text_of


def contract(label: str, env_for) -> tuple[int, int]:
    """env_for(agent) -> env dict selecting the store for that agent."""
    print(f"\n== memory contract: {label} ==")

    def call(agent, calls):
        return rpc("agyteam.mcp_memory", [], calls, env=env_for(agent))

    r = call("alice", [
        ("memory_index", {}),
        ("save_memory", {"name": "deploy-host", "description": "where we deploy",
                         "content": "horta, ssh port 2222"}),
        ("read_memory", {"name": "deploy-host"}),
        ("read_memory", {"name": "Deploy Host"}),          # normalisation
        ("read_memory", {"name": "never-written"}),        # the honest miss
        ("save_memory", {"name": "deploy-host", "description": "updated desc",
                         "content": "horta, ssh port 2200"}),
        ("memory_index", {}),
        ("delete_memory", {"name": "deploy-host"}),
        ("delete_memory", {"name": "deploy-host"}),        # idempotent
        ("memory_index", {}),
    ])[2:]

    # a second agent must not see the first one's memory
    call("alice", [("save_memory", {"name": "private-note", "description": "a",
                                    "content": "alice only"})])
    bob = call("bob", [("memory_index", {}), ("read_memory", {"name": "private-note"})])[2:]

    empty, saved, read1, read2, miss, updated, idx2, del1, del2, idx3 = r
    return sum([
        check("empty index says so rather than looking broken",
              "empty" in text_of(empty).lower(), text_of(empty)),
        check("save reports 'saved' and returns the refreshed index",
              "saved" in text_of(saved) and "deploy-host" in text_of(saved),
              text_of(saved)),
        check("read round-trips content", "2222" in text_of(read1), text_of(read1)),
        check("name normalisation: 'Deploy Host' finds 'deploy-host'",
              "2222" in text_of(read2), text_of(read2)),
        check("miss is honest and lists what exists",
              "no memory named" in text_of(miss) and "deploy-host" in text_of(miss),
              text_of(miss)),
        check("miss does not fabricate content",
              "2222" not in text_of(miss), text_of(miss)),
        check("re-save reports 'updated', not 'saved'",
              "updated" in text_of(updated), text_of(updated)),
        check("overwrite does not duplicate the index entry",
              text_of(idx2).count("deploy-host") == 1, text_of(idx2)),
        check("overwrite replaces the description",
              "updated desc" in text_of(idx2), text_of(idx2)),
        check("delete confirms", "deleted" in text_of(del1), text_of(del1)),
        check("deleting a missing memory is honest, not a crash",
              "no memory named" in text_of(del2), text_of(del2)),
        check("index empty after delete",
              "deploy-host" not in text_of(idx3), text_of(idx3)),
        check("another agent's index does not leak",
              "private-note" not in text_of(bob[0]), text_of(bob[0])),
        check("another agent cannot read it either",
              "alice only" not in text_of(bob[1]), text_of(bob[1])),
    ]), 14


def file_env():
    base = Path(tempfile.mkdtemp(prefix="agyteam-mem-file-"))

    def env_for(agent):
        ws = base / agent
        ws.mkdir(parents=True, exist_ok=True)
        return {"AGYTEAM_AGENT": agent, "AGYTEAM_WORKSPACE": str(ws),
                "AGYTEAM_MEMORY_STORE": "agyteam.memory_file:FileMemory"}
    return env_for


def sqlite_env():
    db = Path(tempfile.mkdtemp(prefix="agyteam-mem-sqlite-")) / "mem.db"
    cfg = json.dumps({"db": str(db)})

    def env_for(agent):
        return {"AGYTEAM_AGENT": agent, "AGYTEAM_MEMORY_CONFIG": cfg,
                "AGYTEAM_MEMORY_STORE": "fixture_memory:SqliteMemory"}
    return env_for


def test_loader_failures() -> tuple[int, int]:
    """A misconfigured store must fail loudly, never silently fall back."""
    print("\n== loader safety ==")

    def run(env):
        p = subprocess.run([str(PY), "-m", "agyteam.mcp_memory"], cwd=ROOT,
                           input="", capture_output=True, text=True, timeout=30,
                           env={**os.environ, "PYTHONPATH": str(ROOT),
                                "AGYTEAM_AGENT": "alice", **env})
        return p.returncode, (p.stderr or "") + (p.stdout or "")

    rc1, o1 = run({"AGYTEAM_MEMORY_STORE": "nosuchmodule:Thing"})
    rc2, o2 = run({"AGYTEAM_MEMORY_STORE": "agyteam.memory_file"})
    rc3, o3 = run({"AGYTEAM_MEMORY_STORE": "agyteam.memory_file:FileMemory",
                   "AGYTEAM_MEMORY_CONFIG": "{not json"})
    rc4, o4 = run({"AGYTEAM_MEMORY_STORE": "agyteam.scope:Scopes"})
    return sum([
        check("missing module fails loudly", rc1 != 0 and "cannot load" in o1, o1),
        check("malformed spec rejected", rc2 != 0 and "module:Class" in o2, o2),
        check("bad config JSON rejected", rc3 != 0 and "not valid JSON" in o3, o3),
        check("non-MemoryStore class rejected", rc4 != 0 and "not an" in o4, o4),
    ]), 4


if __name__ == "__main__":
    spec = os.environ.get("AGYTEAM_MEMORY_STORE")
    if spec:
        cfg = os.environ.get("AGYTEAM_MEMORY_CONFIG", "")
        runs = [contract(spec, lambda agent: {
            "AGYTEAM_AGENT": agent, "AGYTEAM_MEMORY_STORE": spec,
            "AGYTEAM_MEMORY_CONFIG": cfg})]
    else:
        runs = [contract("file (default)", file_env()),
                contract("sqlite (independent fixture)", sqlite_env()),
                test_loader_failures()]
    got, want = sum(s for s, _ in runs), sum(t for _, t in runs)
    print(f"\n== memory contract: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
