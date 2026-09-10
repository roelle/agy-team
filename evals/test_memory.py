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


def test_provenance() -> tuple[int, int]:
    """Test memory provenance (when and why) and backward compatibility with legacy files."""
    print("\n== memory provenance ==")
    ws = Path(tempfile.mkdtemp(prefix="agyteam-mem-prov-"))
    env = {"AGYTEAM_AGENT": "alice", "AGYTEAM_WORKSPACE": str(ws),
           "AGYTEAM_MEMORY_STORE": "agyteam.memory_file:FileMemory"}

    def call(calls):
        return rpc("agyteam.mcp_memory", [], calls, env=env)

    # 1. Save with why and explicit when
    r1 = call([
        ("save_memory", {
            "name": "tool-delegation",
            "description": "delegate provisioning when tools break",
            "content": "when tools are unavailable to coder, delegate provisioning to syseng",
            "why": "file-writing tools were unavailable due to sandboxing bug",
            "when": "2026-09-01 10:00:00"
        }),
        ("read_memory", {"name": "tool-delegation"}),
    ])[2:]
    saved1, read1 = r1[0], r1[1]
    t_read1 = text_of(read1)

    # 2. Save with why and auto-generated when
    import time
    today_prefix = time.strftime("%Y-%m-%d")
    r2 = call([
        ("save_memory", {
            "name": "auto-timestamp",
            "description": "auto timestamp test",
            "content": "auto timestamp content",
            "why": "testing automatic timestamp generation"
        }),
        ("read_memory", {"name": "auto-timestamp"}),
    ])[2:]
    saved2, read2 = r2[0], r2[1]
    t_read2 = text_of(read2)

    # 3. Legacy file compatibility: hand-seed an old memory file predating provenance
    legacy_file = ws / "memory" / "legacy-notes.md"
    legacy_file.write_text("# legacy-notes\n\nthis is pre-existing content without provenance\n")
    idx_file = ws / "MEMORY.md"
    idx_file.write_text(idx_file.read_text() + "- [legacy-notes] pre-existing notes\n")
    r3 = call([
        ("read_memory", {"name": "legacy-notes"}),
    ])[2:]
    t_read_legacy = text_of(r3[0])

    # 4. Updating existing memory updates provenance
    r4 = call([
        ("save_memory", {
            "name": "tool-delegation",
            "description": "updated delegation",
            "content": "updated delegation content",
            "why": "tools were fixed in v2.0",
            "when": "2026-09-10 12:00:00"
        }),
        ("read_memory", {"name": "tool-delegation"}),
    ])[2:]
    t_read_updated = text_of(r4[1])

    # 5. Conforming store: SqliteMemory conforms to MemoryStore and accepts save_memory with why/when
    sqlite_ws = Path(tempfile.mkdtemp(prefix="agyteam-mem-sql-prov-")) / "mem.db"
    sql_env = {"AGYTEAM_AGENT": "alice",
               "AGYTEAM_MEMORY_CONFIG": json.dumps({"db": str(sqlite_ws)}),
               "AGYTEAM_MEMORY_STORE": "fixture_memory:SqliteMemory"}
    r5 = rpc("agyteam.mcp_memory", [], [
        ("save_memory", {
            "name": "sqlite-test",
            "description": "sqlite conforming store test",
            "content": "sqlite content",
            "why": "verify conforming store accepts why and when without error",
            "when": "2026-09-10 12:00:00"
        }),
        ("read_memory", {"name": "sqlite-test"}),
    ], env=sql_env)[2:]
    t_sql_saved, t_sql_read = text_of(r5[0]), text_of(r5[1])

    # 6. Read-modify-save workflow: re-saving read content does not duplicate headings or metadata
    r6 = call([
        ("save_memory", {
            "name": "deploy",
            "description": "how we deploy",
            "content": "Use terraform apply.",
            "why": "manual path broke"
        }),
        ("read_memory", {"name": "deploy"}),
    ])[2:]
    first_deploy = text_of(r6[1])

    # Re-save with new why (incoming why supersedes old why)
    r7 = call([
        ("save_memory", {
            "name": "deploy",
            "description": "how we deploy",
            "content": first_deploy,
            "why": "re-saved after reading"
        }),
        ("read_memory", {"name": "deploy"}),
    ])[2:]
    second_deploy = text_of(r7[1])

    # Re-save again without why (preserving existing why)
    r8 = call([
        ("save_memory", {
            "name": "deploy",
            "description": "how we deploy",
            "content": second_deploy,
        }),
        ("read_memory", {"name": "deploy"}),
    ])[2:]
    third_deploy = text_of(r8[1])

    return sum([
        check("read_memory displays saved 'why'",
              "file-writing tools were unavailable" in t_read1, t_read1),
        check("read_memory displays explicit 'when'",
              "2026-09-01 10:00:00" in t_read1, t_read1),
        check("read_memory displays content",
              "when tools are unavailable to coder" in t_read1, t_read1),
        check("auto-generated when records current date",
              today_prefix in t_read2, t_read2),
        check("legacy memory file without provenance loads cleanly",
              "this is pre-existing content without provenance" in t_read_legacy and "[error" not in t_read_legacy,
              t_read_legacy),
        check("updating memory updates provenance",
              "tools were fixed in v2.0" in t_read_updated and "2026-09-10 12:00:00" in t_read_updated,
              t_read_updated),
        check("conforming store accepting why and when succeeds on save",
              "saved" in t_sql_saved and "sqlite-test" in t_sql_saved, t_sql_saved),
        check("conforming store round-trips content",
              "sqlite content" in t_sql_read, t_sql_read),
        check("read-modify-save has exactly one heading",
              second_deploy.count("# deploy") == 1, second_deploy),
        check("read-modify-save has exactly one - When: line",
              second_deploy.count("- When:") == 1, second_deploy),
        check("read-modify-save has exactly one - Why: line",
              second_deploy.count("- Why:") == 1, second_deploy),
        check("read-modify-save new why supersedes old why",
              "re-saved after reading" in second_deploy and "manual path broke" not in second_deploy,
              second_deploy),
        check("repeated re-save preserves single heading and metadata",
              third_deploy.count("# deploy") == 1 and
              third_deploy.count("- When:") == 1 and
              third_deploy.count("- Why:") == 1,
              third_deploy),
        check("re-save without why preserves existing why",
              "re-saved after reading" in third_deploy, third_deploy),
    ]), 14


def test_contradiction_detection() -> tuple[int, int]:
    """Test contradiction and overlap detection on write."""
    print("\n== contradiction and overlap detection ==")
    ws = Path(tempfile.mkdtemp(prefix="agyteam-mem-conflict-"))
    env = {"AGYTEAM_AGENT": "alice", "AGYTEAM_WORKSPACE": str(ws),
           "AGYTEAM_MEMORY_STORE": "agyteam.memory_file:FileMemory"}

    def call(calls):
        return rpc("agyteam.mcp_memory", [], calls, env=env)

    # Seed base memories
    call([
        ("save_memory", {
            "name": "tool-delegation",
            "description": "when tools are unavailable delegate to syseng",
            "content": "when tools are unavailable to coder, delegate provisioning to syseng",
            "why": "file-writing tools were unavailable"
        }),
        ("save_memory", {
            "name": "cache-service",
            "description": "query caching",
            "content": "redis query cache is enabled in production",
            "why": "reduce database load"
        }),
        ("save_memory", {
            "name": "prod-database",
            "description": "primary database",
            "content": "primary postgresql database host: db.internal port: 5432",
            "why": "production connection info"
        }),
    ])

    # 1. Direct contradiction on TASK.md example: opposing assertions ('available' vs 'unavailable')
    r1 = call([
        ("save_memory", {
            "name": "coder-tools",
            "description": "tools are available to coder",
            "content": "tools are available to coder; provision files directly, do not delegate to syseng",
            "why": "tool bug was resolved"
        })
    ])[2:]
    out1 = text_of(r1[0])

    # 2. Antonym pair contradiction ('enabled' vs 'disabled')
    r2 = call([
        ("save_memory", {
            "name": "cache-policy",
            "description": "caching settings",
            "content": "redis query cache is disabled in production",
            "why": "caching caused stale data bugs"
        })
    ])[2:]
    out2 = text_of(r2[0])

    # 3. Conflicting attribute value (port 5432 vs port 5433)
    r3 = call([
        ("save_memory", {
            "name": "db-target",
            "description": "database port config",
            "content": "primary postgresql database host: db.internal port: 5433",
            "why": "port changed"
        })
    ])[2:]
    out3 = text_of(r3[0])

    # 4. Near-duplicate name overlap ('tool-delegation' vs 'tool-delegations')
    r4 = call([
        ("save_memory", {
            "name": "tool-delegations",
            "description": "rules on delegating tools",
            "content": "general guidelines for delegating tasks",
            "why": "team coordination"
        })
    ])[2:]
    out4 = text_of(r4[0])

    # 5. Unrelated memory produces no false positive conflict warning
    r5 = call([
        ("save_memory", {
            "name": "python-formatting",
            "description": "code style and linting",
            "content": "use ruff and black for python codebase formatting",
            "why": "maintain consistent code style across team"
        })
    ])[2:]
    out5 = text_of(r5[0])

    # 6. Re-saving / updating the same memory does not conflict with itself
    r6 = call([
        ("save_memory", {
            "name": "python-formatting",
            "description": "code style and linting updated",
            "content": "use ruff, black, and isort for python codebase formatting",
            "why": "added isort"
        })
    ])[2:]
    out6 = text_of(r6[0])

    return sum([
        check("TASK.md example: detects contradiction and names 'tool-delegation'",
              "conflict warning" in out1 and "tool-delegation" in out1, out1),
        check("TASK.md example: identifies opposing terms or negation",
              ("available" in out1 and "unavailable" in out1) or "delegate" in out1, out1),
        check("antonym contradiction: detects 'enabled' vs 'disabled' and names 'cache-service'",
              "conflict warning" in out2 and "cache-service" in out2 and "enabled" in out2 and "disabled" in out2, out2),
        check("attribute conflict: detects conflicting port and names 'prod-database'",
              "conflict warning" in out3 and "prod-database" in out3 and "port" in out3, out3),
        check("near-duplicate name: warns on 'tool-delegations' vs 'tool-delegation'",
              "conflict warning" in out4 and "tool-delegation" in out4, out4),
        check("unrelated memory does not trigger false conflict warning",
              "conflict warning" not in out5, out5),
        check("updating same memory does not conflict with itself",
              "conflict warning" not in out6 and "updated" in out6, out6),
    ]), 7


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
                test_loader_failures(),
                test_provenance(),
                test_contradiction_detection()]
    got, want = sum(s for s, _ in runs), sum(t for _, t in runs)
    print(f"\n== memory contract: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
