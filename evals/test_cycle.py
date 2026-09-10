"""Tests for cycle and distill: conversation to durable memory.

Runs offline with scripted runners and test memory stores, no model calls.
Tests the Step 1 requirements and sets up coverage for the subsequent steps.
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
ROOT = EVALS_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

from rpc_util import ROOT, check

from agyteam import config                        # noqa: E402
from agyteam import memory as memory_lib          # noqa: E402
from agyteam.runner import Runner                 # noqa: E402
from agyteam.supervisor import Supervisor, distill, cycle, main, DISTILL_PROMPT  # noqa: E402
from fixture_memory import SqliteMemory           # noqa: E402


class MockRunner(Runner):
    """Offline runner allowing customizable wake behavior."""
    label = "mock"

    def __init__(self, on_wake=None, fail=False, conv_id="conv-123"):
        super().__init__()
        self.on_wake = on_wake
        self.fail = fail
        self.conv_id = conv_id
        self.woken = []
        self.prompts = []
        self.resets = []
        self.closed = False

    def conversation_id(self, agent: str) -> str | None:
        if self.conv_id is not None:
            return self.conv_id
        return super().conversation_id(agent)

    def wake(self, agent: str, message: str) -> str:
        self.woken.append(agent)
        self.prompts.append(message)
        if self.fail:
            raise RuntimeError(f"{agent} failed during wake")
        if self.on_wake:
            return self.on_wake(agent, message)
        return "Nothing new to save."

    def reset(self, agent: str | None = None) -> None:
        self.resets.append(agent)
        self.conv_id = None
        super().reset(agent)

    def close(self) -> None:
        self.closed = True
        super().close()


def make_temp_team(prefix="agyteam-cycle-") -> Path:
    td = Path(tempfile.mkdtemp(prefix=prefix)) / "team"
    td.mkdir(parents=True)
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "coder", "role": "implements"},
        {"name": "tpm", "role": "coordinates"},
    ]}))
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)
    os.environ["AGYTEAM_DURABLE_DIR"] = str(td.parent)
    return td


def test_distill_prompt_guidance() -> tuple[int, int]:
    """DISTILL_PROMPT must ask for durable, specific lessons via save_memory."""
    print("\n== distill prompt requirements ==")
    low = DISTILL_PROMPT.lower()
    return sum([
        check("mentions save_memory tool", "save_memory" in DISTILL_PROMPT),
        check("asks for what future session needs", "future session" in low),
        check("asks for surprises or mistakes", "surprised" in low and "wrong" in low),
        check("asks what to tell replacement", "replacement" in low),
        check("rejects mere conversation summary", "rather than a summary" in low),
    ]), 5


def test_distill_saves_memories() -> tuple[int, int]:
    """Distilling wakes the agent and records count before and after."""
    print("\n== distill saves memories ==")
    td = make_temp_team()

    def saving_wake(agent, prompt):
        store = memory_lib.load(agent)
        try:
            store.save("lesson-offline", "An offline finding",
                       "Always check error returns.", why="unit test")
        finally:
            store.close()
        return "Saved 1 lesson into memory."

    runner = MockRunner(on_wake=saving_wake)
    sup = Supervisor(["coder"], runner, team_dir=td, quiet=True)
    before, after = sup.distill("coder")
    sup.close()

    # Module-level convenience function check with custom team_dir
    def saving_wake_2(agent, prompt):
        store = memory_lib.load(agent)
        try:
            store.save("lesson-second", "A second offline finding",
                       "Keep memory entries small.", why="unit test")
        finally:
            store.close()
        return "Saved second lesson."

    runner2 = MockRunner(on_wake=saving_wake_2)
    b2, a2 = distill("coder", runner=runner2, team_dir=td)

    return sum([
        check("agent was woken", "coder" in runner.woken),
        check("distill prompt passed to agent", len(runner.prompts) == 1 and "save_memory" in runner.prompts[0]),
        check("count before was 0", before == 0, str(before)),
        check("count after was 1", after == 1, str(after)),
        check("distill convenience before matches previous after", b2 == 1, str(b2)),
        check("distill convenience after is 2", a2 == 2, str(a2)),
    ]), 6


def test_distill_honest_reporting() -> tuple[int, int]:
    """If agent saved nothing, distill must report so honestly rather than fabricate success."""
    print("\n== distill honest reporting ==")
    td = make_temp_team()

    # Agent wakes, says something, but saves 0 memories
    runner = MockRunner(on_wake=lambda a, p: "Confirmed: no new durable lessons.")
    sup = Supervisor(["coder"], runner, team_dir=td, quiet=False)

    buf = io.StringIO()
    with redirect_stdout(buf):
        before, after = sup.distill("coder")
    sup.close()
    out = buf.getvalue()

    return sum([
        check("count before equals count after", before == 0 and after == 0, f"({before}, {after})"),
        check("honest log reports 0 memories saved", "0 memories saved" in out, out),
        check("honest log includes before and after counts", "(0 before, 0 after)" in out, out),
    ]), 3


def test_distill_updates_existing_memories() -> tuple[int, int]:
    """Distillation that updates existing memories must report them rather than 0 memories saved."""
    print("\n== distill updates existing memories ==")
    td = make_temp_team()

    # Seed 1 memory beforehand
    store = memory_lib.load("coder")
    try:
        store.save("lesson-original", "Original description",
                   "Original content before distill.", why="seed")
    finally:
        store.close()

    # Step 1: Agent updates the existing memory during distill
    def updating_wake(agent, prompt):
        st = memory_lib.load(agent)
        try:
            st.save("lesson-original", "Updated description",
                    "Consolidated and updated content.", why="distill consolidation")
        finally:
            st.close()
        return "Consolidated existing lesson."

    runner = MockRunner(on_wake=updating_wake)
    sup = Supervisor(["coder"], runner, team_dir=td, quiet=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        before, after = sup.distill("coder")
    sup.close()
    out = buf.getvalue()

    # Read back to verify content genuinely changed
    st_check = memory_lib.load("coder")
    try:
        updated_content = st_check.read("lesson-original")
    finally:
        st_check.close()

    # Step 2: Combined distill - agent updates 1 existing and creates 1 new memory
    def combined_wake(agent, prompt):
        st = memory_lib.load(agent)
        try:
            st.save("lesson-original", "Updated again",
                    "Second update content.", why="distill second update")
            st.save("lesson-brand-new", "Brand new lesson",
                    "New learning from episode.", why="new discovery")
        finally:
            st.close()
        return "Updated 1 existing and added 1 new lesson."

    runner_comb = MockRunner(on_wake=combined_wake)
    sup_comb = Supervisor(["coder"], runner_comb, team_dir=td, quiet=False)
    buf_comb = io.StringIO()
    with redirect_stdout(buf_comb):
        b_comb, a_comb = sup_comb.distill("coder")
    sup_comb.close()
    out_comb = buf_comb.getvalue()

    return sum([
        check("update only: count before was 1", before == 1, str(before)),
        check("update only: count after was 1", after == 1, str(after)),
        check("update only: log reports 1 updated memories", "distilled 1 updated memories (1 → 1)" in out, out),
        check("update only: memory content genuinely changed", "Consolidated and updated content." in (updated_content or ""), str(updated_content)),
        check("combined: count before was 1", b_comb == 1, str(b_comb)),
        check("combined: count after was 2", a_comb == 2, str(a_comb)),
        check("combined: log reports 1 new and 1 updated", "distilled 1 new, 1 updated memories (1 → 2)" in out_comb, out_comb),
    ]), 7


def test_distill_failure_isolation() -> tuple[int, int]:
    """Runner crash during distill is caught and reports counts without raising."""
    print("\n== distill failure isolation ==")
    td = make_temp_team()

    runner = MockRunner(fail=True)
    sup = Supervisor(["coder"], runner, team_dir=td, quiet=False)

    buf = io.StringIO()
    with redirect_stdout(buf):
        before, after = sup.distill("coder")
    sup.close()
    out = buf.getvalue()

    return sum([
        check("counts return safely on failure", before == 0 and after == 0, f"({before}, {after})"),
        check("error logged", "[error: RuntimeError:" in out, out),
        check("0 memories saved reported", "0 memories saved" in out, out),
    ]), 3


def test_distill_with_sqlite_fixture() -> tuple[int, int]:
    """Distill works with alternative MemoryStore backends (pluggable memory seam)."""
    print("\n== distill with sqlite fixture ==")
    td = make_temp_team()
    db_path = td.parent / "test_mem.db"
    old_store = os.environ.get("AGYTEAM_MEMORY_STORE")
    old_cfg = os.environ.get("AGYTEAM_MEMORY_CONFIG")

    os.environ["AGYTEAM_MEMORY_STORE"] = "fixture_memory:SqliteMemory"
    os.environ["AGYTEAM_MEMORY_CONFIG"] = json.dumps({"db": str(db_path)})

    try:
        def saving_wake(agent, prompt):
            store = memory_lib.load(agent)
            try:
                store.save("sql-lesson", "Sqlite memory finding",
                           "Pluggable stores work seamlessly.", why="sqlite test")
            finally:
                store.close()
            return "Saved to sqlite."

        runner = MockRunner(on_wake=saving_wake)
        sup = Supervisor(["coder"], runner, team_dir=td, quiet=True)
        before, after = sup.distill("coder")
        sup.close()

        def updating_wake(agent, prompt):
            store = memory_lib.load(agent)
            try:
                store.save("sql-lesson", "Sqlite memory finding updated",
                           "Updated content in sqlite backend.", why="sqlite test update")
            finally:
                store.close()
            return "Updated sqlite."

        runner2 = MockRunner(on_wake=updating_wake)
        sup2 = Supervisor(["coder"], runner2, team_dir=td, quiet=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            b2, a2 = sup2.distill("coder")
        sup2.close()
        out2 = buf.getvalue()

        return sum([
            check("sqlite fixture: count before is 0", before == 0, str(before)),
            check("sqlite fixture: count after is 1", after == 1, str(after)),
            check("sqlite fixture update: count before is 1", b2 == 1, str(b2)),
            check("sqlite fixture update: count after is 1", a2 == 1, str(a2)),
            check("sqlite fixture update: log reports 1 updated", "distilled 1 updated memories (1 → 1)" in out2, out2),
        ]), 5
    finally:
        if old_store is not None:
            os.environ["AGYTEAM_MEMORY_STORE"] = old_store
        else:
            os.environ.pop("AGYTEAM_MEMORY_STORE", None)
        if old_cfg is not None:
            os.environ["AGYTEAM_MEMORY_CONFIG"] = old_cfg
        else:
            os.environ.pop("AGYTEAM_MEMORY_CONFIG", None)


def test_cycle_resets_conversation() -> tuple[int, int]:
    """Cycling distills learnings and resets conversation for agent."""
    print("\n== cycle resets conversation ==")
    td = make_temp_team()

    def saving_wake(agent, prompt):
        store = memory_lib.load(agent)
        try:
            store.save("lesson-cycle", "A cycle finding",
                       "Always distill before resetting.", why="test")
        finally:
            store.close()
        return "Saved 1 lesson before reset."

    runner = MockRunner(on_wake=saving_wake, conv_id="conv-before")
    sup = Supervisor(["coder"], runner, team_dir=td, quiet=True)
    before, after = sup.cycle("coder")
    sup.close()

    # Module-level cycle convenience function
    runner2 = MockRunner(on_wake=lambda a, p: "Nothing new.", conv_id="conv-2")
    b2, a2 = cycle("coder", runner=runner2, team_dir=td)

    return sum([
        check("distill was called during cycle", "coder" in runner.woken),
        check("memories saved during cycle", before == 0 and after == 1, f"({before}, {after})"),
        check("runner.reset was called for coder", runner.resets == ["coder"]),
        check("conversation id cleared after reset", runner.conversation_id("coder") is None),
        check("convenience cycle called reset", runner2.resets == ["coder"]),
    ]), 5


def test_cycle_without_distilling_prevented() -> tuple[int, int]:
    """Cycling or resetting without distilling first is prohibited."""
    print("\n== cycling without distilling prevented ==")
    td = make_temp_team()
    runner = MockRunner()
    sup = Supervisor(["coder"], runner, team_dir=td, quiet=True)

    raised_msg = ""
    try:
        sup.reset("coder")
    except RuntimeError as e:
        raised_msg = str(e)
    finally:
        sup.close()

    return sum([
        check("sup.reset raises RuntimeError", "cycling without distilling first is not permitted" in raised_msg, raised_msg),
        check("error directs to cycle()", "use cycle('coder')" in raised_msg, raised_msg),
        check("runner was not reset", runner.resets == []),
    ]), 3


def test_cycle_aborts_reset_on_distill_failure() -> tuple[int, int]:
    """If distillation fails, cycle aborts and does not reset conversation."""
    print("\n== cycle aborts reset on distill failure ==")
    td = make_temp_team()
    runner = MockRunner(fail=True, conv_id="conv-precious")
    sup = Supervisor(["coder"], runner, team_dir=td, quiet=False)

    buf = io.StringIO()
    with redirect_stdout(buf):
        before, after = sup.cycle("coder")
    sup.close()
    out = buf.getvalue()

    return sum([
        check("cycle aborted message logged", "cycle aborted because distillation failed" in out, out),
        check("runner reset was NOT called", runner.resets == [], str(runner.resets)),
        check("conversation was preserved", runner.conv_id == "conv-precious"),
    ]), 3


def test_cli_distill_single() -> tuple[int, int]:
    """CLI --distill wakes specified agent, logs results honestly, and cleans up."""
    print("\n== CLI --distill single agent ==")
    td = make_temp_team()

    def saving_wake(agent, prompt):
        store = memory_lib.load(agent)
        try:
            store.save("cli-distill-lesson", "Distill via CLI",
                       "Testing CLI distill flag.", why="cli test")
        finally:
            store.close()
        return "Saved CLI distill lesson."

    runner = MockRunner(on_wake=saving_wake)
    buf = io.StringIO()
    with redirect_stdout(buf):
        main(["--team-dir", str(td), "--distill", "coder"], runner=runner)
    out = buf.getvalue()

    return sum([
        check("coder was woken", runner.woken == ["coder"]),
        check("distilling logged", "→ distilling coder" in out, out),
        check("saved memory count logged", "distilled 1 new memories (0 → 1)" in out, out),
        check("resources cleaned up", runner.closed is True),
    ]), 4


def test_cli_cycle_single() -> tuple[int, int]:
    """CLI --cycle distills and resets conversation for single agent."""
    print("\n== CLI --cycle single agent ==")
    td = make_temp_team()

    runner = MockRunner(conv_id="conv-cli")
    buf = io.StringIO()
    with redirect_stdout(buf):
        main(["--team-dir", str(td), "--cycle", "coder"], runner=runner)
    out = buf.getvalue()

    return sum([
        check("coder was woken", runner.woken == ["coder"]),
        check("coder was reset", runner.resets == ["coder"]),
        check("honest reporting for 0 memories", "0 memories saved" in out, out),
        check("conversation reset logged", "conversation reset" in out, out),
        check("resources cleaned up", runner.closed is True),
    ]), 5


def test_cli_distill_all() -> tuple[int, int]:
    """CLI --distill-all distills all agents on the roster in order."""
    print("\n== CLI --distill-all ==")
    td = make_temp_team()

    runner = MockRunner()
    buf = io.StringIO()
    with redirect_stdout(buf):
        main(["--team-dir", str(td), "--distill-all"], runner=runner)
    out = buf.getvalue()

    return sum([
        check("all agents woken in roster order", runner.woken == ["coder", "tpm"]),
        check("coder logged", "→ distilling coder" in out, out),
        check("tpm logged", "→ distilling tpm" in out, out),
        check("honest reporting for both", out.count("0 memories saved") == 2, out),
        check("resources cleaned up", runner.closed is True),
    ]), 5


def test_cli_cycle_all() -> tuple[int, int]:
    """CLI --cycle-all distills and resets all agents on the roster in order."""
    print("\n== CLI --cycle-all ==")
    td = make_temp_team()

    runner = MockRunner()
    buf = io.StringIO()
    with redirect_stdout(buf):
        main(["--team-dir", str(td), "--cycle-all"], runner=runner)
    out = buf.getvalue()

    return sum([
        check("all agents woken in roster order", runner.woken == ["coder", "tpm"]),
        check("all agents reset in roster order", runner.resets == ["coder", "tpm"]),
        check("both conversation resets logged", out.count("conversation reset") == 2, out),
        check("resources cleaned up", runner.closed is True),
    ]), 4


def test_cli_unknown_agent_error_and_cleanup() -> tuple[int, int]:
    """Unknown agent on CLI raises SystemExit and guarantees clean resource teardown."""
    print("\n== CLI unknown agent error and cleanup ==")
    td = make_temp_team()

    runner_distill = MockRunner()
    distill_err = ""
    try:
        main(["--team-dir", str(td), "--distill", "ghost"], runner=runner_distill)
    except SystemExit as e:
        distill_err = str(e)

    runner_cycle = MockRunner()
    cycle_err = ""
    try:
        main(["--team-dir", str(td), "--cycle", "ghost"], runner=runner_cycle)
    except SystemExit as e:
        cycle_err = str(e)

    return sum([
        check("distill unknown agent exits with clean error",
              distill_err == "unknown agent 'ghost'; roster has: coder, tpm", distill_err),
        check("distill cleans up resources on error path", runner_distill.closed is True),
        check("cycle unknown agent exits with clean error",
              cycle_err == "unknown agent 'ghost'; roster has: coder, tpm", cycle_err),
        check("cycle cleans up resources on error path", runner_cycle.closed is True),
        check("no agent was woken", runner_distill.woken == [] and runner_cycle.woken == []),
    ]), 5


def test_auto_cycle_under_threshold_left_alone() -> tuple[int, int]:
    """Agent whose latest turn is under threshold is left alone."""
    print("\n== auto-cycle under threshold left alone ==")
    td = make_temp_team()

    runner = MockRunner(conv_id="conv-under")
    sup = Supervisor(["coder"], runner, team_dir=td, quiet=True)
    sup.observer.record_turn(agent="coder", conversation="conv-under", duration_s=1.0,
                             input_tokens=50_000)

    cycled = sup.auto_cycle()
    sup.close()

    return sum([
        check("auto_cycle returns empty list", cycled == []),
        check("runner was not reset", runner.resets == []),
        check("agent was not woken", runner.woken == []),
    ]), 3


def test_auto_cycle_over_threshold_cycled_after_episode() -> tuple[int, int]:
    """Agent exceeding CYCLE_THRESHOLD_TOKENS is automatically cycled after episode."""
    print("\n== auto-cycle over threshold after episode ==")
    td = make_temp_team()

    runner = MockRunner(conv_id="conv-large")
    sup = Supervisor(["coder"], runner, team_dir=td, quiet=True)
    sup.observer.record_turn(agent="coder", conversation="conv-large", duration_s=1.0,
                             input_tokens=105_000)

    # Calling run_until_idle completes the episode and triggers auto_cycle
    sup.run_until_idle()

    # Verify cycling happened: distill was run, runner was reset
    cycled_once = runner.resets == ["coder"]
    woken_once = "coder" in runner.woken

    # Calling run_until_idle again with no new turns must NOT re-cycle
    sup.run_until_idle()
    no_recycle = runner.resets == ["coder"]

    sup.close()

    return sum([
        check("agent over threshold cycled on episode completion", cycled_once),
        check("agent woken for distillation", woken_once),
        check("not re-cycled without new turns", no_recycle),
    ]), 3


def test_auto_cycle_never_mid_episode_with_work_outstanding() -> tuple[int, int]:
    """Agent over threshold is not cycled mid-episode if work is outstanding."""
    print("\n== auto-cycle never mid-episode with work outstanding ==")
    td = make_temp_team()

    runner = MockRunner(conv_id="conv-busy")
    sup = Supervisor(["coder"], runner, team_dir=td, quiet=False)
    sup.observer.record_turn(agent="coder", conversation="conv-busy", duration_s=1.0,
                             input_tokens=110_000)

    # Put a pending message into coder's inbox
    sup.user_transport.send("coder", "Please fix bug #42")

    buf = io.StringIO()
    with redirect_stdout(buf):
        cycled = sup.auto_cycle()
    out = buf.getvalue()
    resets_while_busy = list(runner.resets)

    # Now consume/clear coder's pending mail to simulate work completed
    sup.transports["coder"].fetch()
    with redirect_stdout(buf):
        cycled_after_work = sup.auto_cycle()
    sup.close()

    return sum([
        check("auto_cycle returns empty while work outstanding", cycled == []),
        check("runner was not reset while busy", resets_while_busy == []),
        check("skip logged with pending count", "skipping auto-cycle for coder: work outstanding (1 pending messages)" in out, out),
        check("cycled once work is no longer outstanding", cycled_after_work == ["coder"]),
        check("runner reset after idle", runner.resets == ["coder"]),
    ]), 5


def test_auto_cycle_env_threshold_override() -> tuple[int, int]:
    """AGYTEAM_CYCLE_THRESHOLD environment variable overrides the default threshold."""
    print("\n== auto-cycle env threshold override ==")
    td = make_temp_team()

    old_env = os.environ.get("AGYTEAM_CYCLE_THRESHOLD")
    os.environ["AGYTEAM_CYCLE_THRESHOLD"] = "50000"
    try:
        runner = MockRunner(conv_id="conv-env")
        sup = Supervisor(["coder"], runner, team_dir=td, quiet=True)
        # 60,000 is under the default (100,000) but over the custom 50,000 threshold
        sup.observer.record_turn(agent="coder", conversation="conv-env", duration_s=1.0,
                                 input_tokens=60_000)
        cycled = sup.auto_cycle()
        sup.close()

        # Test higher override prevents cycling
        os.environ["AGYTEAM_CYCLE_THRESHOLD"] = "200000"
        runner2 = MockRunner(conv_id="conv-env2")
        sup2 = Supervisor(["coder"], runner2, team_dir=td, quiet=True)
        sup2.observer.record_turn(agent="coder", conversation="conv-env2", duration_s=1.0,
                                  input_tokens=150_000)
        cycled2 = sup2.auto_cycle()
        sup2.close()

        return sum([
            check("config.CYCLE_THRESHOLD_TOKENS default is 100_000",
                  config.CYCLE_THRESHOLD_TOKENS == 100_000),
            check("cycled when exceeding custom lower threshold", cycled == ["coder"]),
            check("runner was reset under custom threshold", runner.resets == ["coder"]),
            check("not cycled when below custom higher threshold", cycled2 == []),
            check("runner was not reset under higher threshold", runner2.resets == []),
        ]), 5
    finally:
        if old_env is not None:
            os.environ["AGYTEAM_CYCLE_THRESHOLD"] = old_env
        else:
            os.environ.pop("AGYTEAM_CYCLE_THRESHOLD", None)


def test_auto_cycle_across_supervisor_restarts() -> tuple[int, int]:
    """Auto-cycle does not re-cycle agents across fresh supervisor restarts or new conversations."""
    print("\n== auto-cycle across supervisor restarts ==")
    td = make_temp_team()

    # Run 1: Agent coder has a large turn in conv-1, supervisor cycles coder
    runner1 = MockRunner(conv_id="conv-1")
    sup1 = Supervisor(["coder"], runner1, team_dir=td, quiet=True)
    sup1.observer.record_turn(agent="coder", conversation="conv-1", duration_s=1.0, input_tokens=150_000)
    sup1.run_until_idle()
    run1_resets = list(runner1.resets)
    sup1.close()

    # Run 2: Fresh supervisor instance after reset (conv_id is None)
    runner2 = MockRunner(conv_id=None)
    sup2 = Supervisor(["coder"], runner2, team_dir=td, quiet=True)
    sup2.run_until_idle()
    run2_resets = list(runner2.resets)
    run2_woken = list(runner2.woken)
    sup2.close()

    # Run 3: Fresh supervisor instance with a new conversation conv-2 with low tokens
    runner3 = MockRunner(conv_id="conv-2")
    sup3 = Supervisor(["coder"], runner3, team_dir=td, quiet=True)
    sup3.observer.record_turn(agent="coder", conversation="conv-2", duration_s=1.0, input_tokens=5_000)
    sup3.run_until_idle()
    run3_resets = list(runner3.resets)
    sup3.close()

    return sum([
        check("agent cycled in initial run with high token conversation", run1_resets == ["coder"]),
        check("fresh supervisor restart with reset agent does not re-cycle", run2_resets == []),
        check("reset agent is not woken across restart", run2_woken == []),
        check("new conversation with low tokens is not cycled despite historical turns in log", run3_resets == []),
    ]), 4


if __name__ == "__main__":
    totals = [
        test_distill_prompt_guidance(),
        test_distill_saves_memories(),
        test_distill_honest_reporting(),
        test_distill_updates_existing_memories(),
        test_distill_failure_isolation(),
        test_distill_with_sqlite_fixture(),
        test_cycle_resets_conversation(),
        test_cycle_without_distilling_prevented(),
        test_cycle_aborts_reset_on_distill_failure(),
        test_cli_distill_single(),
        test_cli_cycle_single(),
        test_cli_distill_all(),
        test_cli_cycle_all(),
        test_cli_unknown_agent_error_and_cleanup(),
        test_auto_cycle_under_threshold_left_alone(),
        test_auto_cycle_over_threshold_cycled_after_episode(),
        test_auto_cycle_never_mid_episode_with_work_outstanding(),
        test_auto_cycle_env_threshold_override(),
        test_auto_cycle_across_supervisor_restarts(),
    ]
    got, want = sum(s for s, _ in totals), sum(t for _, t in totals)
    print(f"\n== cycle/distill: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
