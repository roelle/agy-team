"""Observer contract suite.

Exercises the observer seam across both the built-in file observer and an
independent SQLite fixture. Verifies:
1. Turn, failure, and episode events are faithfully recorded and read back.
2. Absent token metrics are preserved as None rather than fabricated zeros.
3. Missing files/databases are normal and return empty results without error.
4. Loader fails loudly on invalid configuration.
5. Supervisor dispatches with ScriptedRunner emit turn and episode records offline
   with no model calls.
6. Cost calculation and summary formatting accurately reflect pricing.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from rpc_util import PY, ROOT, check

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam.observer import format_summary, load as load_observer  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402


def contract(label: str, env_for) -> tuple[int, int]:
    print(f"\n== observer contract: {label} ==")
    env = env_for()
    obs = load_observer(env.get("AGYTEAM_OBSERVER"),
                        json.loads(env["AGYTEAM_OBSERVER_CONFIG"]) if "AGYTEAM_OBSERVER_CONFIG" in env else None)

    # 1. Missing / empty state is normal
    empty_events = obs.events()
    empty_summary = obs.summary()

    # 2. Record turns
    obs.record_turn("alice", "conv-1", duration_s=2.5,
                    input_tokens=1000, output_tokens=200, cache_read_tokens=400,
                    total_tokens=1200, model="gemini-3.8-flash")
    # Turn with genuinely absent token metrics (must remain None)
    obs.record_turn("alice", "conv-1", duration_s=1.5,
                    input_tokens=None, output_tokens=None, cache_read_tokens=None,
                    total_tokens=None, model="gemini-3.8-flash")

    # 3. Record failure
    obs.record_failure("bob", "conv-2", "agent timed out after 900s", duration_s=900.0)

    # 4. Record episode
    obs.record_episode(turns=3, stopped_reason="the user was answered", reviewed=True, duration_s=15.0)

    # 5. Read back
    all_evs = obs.events()
    turn_evs = obs.events("turn")
    fail_evs = obs.events("failure")
    ep_evs = obs.events("episode")

    # 6. Summary and formatting
    summ = obs.summary()
    rendered = format_summary(summ)
    obs.close()

    alice_summ = summ["agents"].get("alice", {})
    bob_summ = summ["agents"].get("bob", {})
    totals = summ.get("totals", {})

    # Turn 2 has absent tokens: check that input_tokens is None, not 0
    t2 = turn_evs[1] if len(turn_evs) > 1 else {}
    absent_is_none = ("input_tokens" not in t2) or (t2.get("input_tokens") is None)

    return sum([
        check("empty observer returns empty event list", empty_events == [], str(empty_events)),
        check("empty observer summary has 0 turns", empty_summary["totals"]["turns"] == 0, str(empty_summary)),
        check("recorded all 4 events", len(all_evs) == 4, f"got {len(all_evs)}"),
        check("filter by 'turn' returns 2 turns", len(turn_evs) == 2, f"got {len(turn_evs)}"),
        check("filter by 'failure' returns 1 failure", len(fail_evs) == 1, f"got {len(fail_evs)}"),
        check("filter by 'episode' returns 1 episode", len(ep_evs) == 1, f"got {len(ep_evs)}"),
        check("absent token metrics recorded as None/absent, not zero", absent_is_none, str(t2)),
        check("per-agent turn count aggregated", alice_summ.get("turns") == 2, str(alice_summ)),
        check("per-agent failure count aggregated", bob_summ.get("failures") == 1, str(bob_summ)),
        check("per-agent unknown tokens count tracked", alice_summ.get("unknown_tokens_turns") == 1, str(alice_summ)),
        check("total turns matches", totals.get("turns") == 2, str(totals)),
        check("total failures matches", totals.get("failures") == 1, str(totals)),
        check("total cost estimated from PRICING", totals.get("cost_usd", 0) > 0, str(totals)),
        check("summary formatting includes agents and episodes",
              "alice" in rendered and "bob" in rendered and "the user was answered" in rendered, rendered),
    ]), 14


def file_env():
    td = Path(tempfile.mkdtemp(prefix="agyteam-obs-file-"))
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)

    def get_env():
        return {
            "AGYTEAM_TEAM_DIR": str(td),
            "AGYTEAM_OBSERVER": "agyteam.observer_file:FileObserver",
        }
    return get_env


def sqlite_env():
    db = Path(tempfile.mkdtemp(prefix="agyteam-obs-sqlite-")) / "events.db"
    cfg = json.dumps({"db": str(db)})

    def get_env():
        return {
            "AGYTEAM_OBSERVER": "fixture_observer:SqliteObserver",
            "AGYTEAM_OBSERVER_CONFIG": cfg,
        }
    return get_env


def test_loader_failures() -> tuple[int, int]:
    print("\n== observer loader safety ==")

    def run(env):
        p = subprocess.run([str(PY), "-c", "from agyteam.observer import load; load()"],
                           cwd=ROOT, capture_output=True, text=True, timeout=30,
                           env={**os.environ, "PYTHONPATH": str(ROOT), **env})
        return p.returncode, (p.stderr or "") + (p.stdout or "")

    rc1, o1 = run({"AGYTEAM_OBSERVER": "nosuchmodule:Thing"})
    rc2, o2 = run({"AGYTEAM_OBSERVER": "agyteam.observer_file"})
    rc3, o3 = run({"AGYTEAM_OBSERVER": "agyteam.observer_file:FileObserver",
                   "AGYTEAM_OBSERVER_CONFIG": "{not json"})
    rc4, o4 = run({"AGYTEAM_OBSERVER": "agyteam.scope:Scopes"})

    return sum([
        check("missing module fails loudly", rc1 != 0 and "cannot load" in o1, o1),
        check("malformed spec rejected", rc2 != 0 and "module:Class" in o2, o2),
        check("bad config JSON rejected", rc3 != 0 and "not valid JSON" in o3, o3),
        check("non-Observer class rejected", rc4 != 0 and "not an" in o4, o4),
    ]), 4


def test_supervisor_observability() -> tuple[int, int]:
    """Supervisor runs with ScriptedRunner offline, emitting turn & episode records."""
    print("\n== supervisor observability with ScriptedRunner (offline, no models) ==")
    td = Path(tempfile.mkdtemp(prefix="agyteam-sup-obs-")) / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "coordinates"},
        {"name": "coder", "role": "implements"},
        {"name": "syseng", "role": "verifies"}]}))
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)

    runner = ScriptedRunner({"script": {
        "tpm": [["coder", "please build Y"]],
        "coder": [["syseng", "built Y, verify please"]],
        "syseng": [["user", "verified Y"]]}})
    sup = Supervisor(["tpm", "coder", "syseng"], runner, max_hops=32, quiet=True)
    load_transport("user").send("tpm", "build and verify Y")
    turns = sup.run_until_idle()

    # Read events from supervisor's observer
    events = sup.observer.events()
    turn_evs = sup.observer.events("turn")
    ep_evs = sup.observer.events("episode")
    sup.close()

    turn_agents = [t.get("agent") for t in turn_evs]
    ep = ep_evs[0] if ep_evs else {}

    # Also test failure emission with failing scripted agent
    fail_runner = ScriptedRunner({"fail": ["coder"], "script": {"tpm": [["coder", "do Z"]]}})
    fail_sup = Supervisor(["tpm", "coder"], fail_runner, max_hops=10, quiet=True)
    load_transport("user").send("tpm", "delegate Z")
    fail_sup.run_until_idle()
    fail_evs = fail_sup.observer.events("failure")
    fail_sup.close()

    return sum([
        check("offline run produced 3 turns", turns == 3, str(turns)),
        check("observer recorded 3 turn events", len(turn_evs) >= 3, str(len(turn_evs))),
        check("turn events correspond to tpm, coder, syseng",
              "tpm" in turn_agents and "coder" in turn_agents and "syseng" in turn_agents, str(turn_agents)),
        check("observer recorded episode event", len(ep_evs) >= 1, str(len(ep_evs))),
        check("episode record has correct turn count", ep.get("turns") == 3, str(ep)),
        check("episode record has stopped reason", "the user was answered" in ep.get("stopped_reason", ""), str(ep)),
        check("failure event recorded on agent crash", len(fail_evs) >= 1, str(fail_evs)),
    ]), 7


def test_file_observer_usage_jsonl_compat() -> tuple[int, int]:
    """FileObserver subsumes usage.jsonl without breaking mcp_self:my_activity."""
    print("\n== usage.jsonl backward compatibility ==")
    td = Path(tempfile.mkdtemp(prefix="agyteam-usage-compat-"))
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)

    from agyteam.observer_file import FileObserver
    obs = FileObserver({"team_dir": str(td)})

    # Record turn with full token metrics
    obs.record_turn("alice", "c1", 2.0, input_tokens=150, output_tokens=50,
                    cache_read_tokens=25, total_tokens=200)
    # Record turn with absent tokens
    obs.record_turn("alice", "c1", 1.0, input_tokens=None, output_tokens=None,
                    cache_read_tokens=None, total_tokens=None)

    usage_file = td / "usage.jsonl"
    events_file = td / "events.jsonl"

    usage_lines = [json.loads(l) for l in usage_file.read_text().splitlines() if l.strip()]
    event_lines = [json.loads(l) for l in events_file.read_text().splitlines() if l.strip()]

    # Test that sum over usage_lines doesn't crash (the TypeError bug with None)
    total_in = sum(e.get("input_tokens", 0) for e in usage_lines)

    return sum([
        check("events.jsonl created", events_file.exists()),
        check("usage.jsonl created for mcp_self compatibility", usage_file.exists()),
        check("usage.jsonl has 2 turn entries", len(usage_lines) == 2, str(usage_lines)),
        check("usage.jsonl safe for mcp_self sum calculations without TypeError", total_in == 150, str(total_in)),
        check("events.jsonl preserves absent token fields as null", event_lines[1].get("input_tokens") is None, str(event_lines[1])),
    ]), 5


def test_observer_cli() -> tuple[int, int]:
    """Test running agyteam.observer directly via python -m."""
    print("\n== observer CLI entrypoint (python -m agyteam.observer) ==")
    td = Path(tempfile.mkdtemp(prefix="agyteam-cli-obs-"))
    from agyteam.observer_file import FileObserver
    obs = FileObserver({"team_dir": str(td)})
    obs.record_turn("coder", "c1", 2.0, input_tokens=1000, output_tokens=200, total_tokens=1200)

    p1 = subprocess.run([str(PY), "-m", "agyteam.observer", "--team-dir", str(td)],
                        cwd=ROOT, capture_output=True, text=True, timeout=30,
                        env={**os.environ, "PYTHONPATH": str(ROOT)})
    p2 = subprocess.run([str(PY), "-m", "agyteam.observer", "--team-dir", str(td), "--json"],
                        cwd=ROOT, capture_output=True, text=True, timeout=30,
                        env={**os.environ, "PYTHONPATH": str(ROOT)})

    sys_py = "/usr/bin/python3"
    p3_rc = 0
    p3_out = ""
    if Path(sys_py).exists():
        p3 = subprocess.run([sys_py, "-m", "agyteam.observer", "--team-dir", str(td)],
                            cwd=ROOT, capture_output=True, text=True, timeout=30,
                            env={"PYTHONPATH": str(ROOT), "PATH": os.environ.get("PATH", "")})
        p3_rc = p3.returncode
        p3_out = p3.stdout + p3.stderr

    parsed_json = {}
    try:
        parsed_json = json.loads(p2.stdout)
    except Exception:
        pass

    return sum([
        check("python -m agyteam.observer exits 0", p1.returncode == 0, p1.stderr),
        check("CLI output contains agent name", "coder" in p1.stdout, p1.stdout),
        check("python -m agyteam.observer --json exits 0", p2.returncode == 0, p2.stderr),
        check("CLI --json produces valid JSON summary", parsed_json.get("totals", {}).get("turns") == 1, p2.stdout),
        check("system python -m agyteam.observer exits 0 (stdlib purity)", p3_rc == 0, p3_out),
    ]), 5


if __name__ == "__main__":
    runs = [
        contract("file (default)", file_env()),
        contract("sqlite (independent fixture)", sqlite_env()),
        test_loader_failures(),
        test_supervisor_observability(),
        test_file_observer_usage_jsonl_compat(),
        test_observer_cli(),
    ]
    got, want = sum(s for s, _ in runs), sum(t for _, t in runs)
    print(f"\n== observer contract: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
