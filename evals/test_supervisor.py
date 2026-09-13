"""Reactive dispatch tests: does a message actually cause work?

Runs offline with a scripted runner, so the cascade, the hop budget, and
failure isolation are all checked deterministically and for free.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

from rpc_util import ROOT, check

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam.supervisor import Supervisor          # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner          # noqa: E402

AGENTS = ["tpm", "coder", "syseng"]


def make_team() -> Path:
    td = Path(tempfile.mkdtemp(prefix="agyteam-sup-")) / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "coordinates"},
        {"name": "coder", "role": "implements"},
        {"name": "syseng", "role": "verifies"}]}))
    import os
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)
    return td


def test_cascade() -> tuple[int, int]:
    """One instruction to tpm should ripple through the team unattended."""
    print("\n== reactive cascade (no human polling) ==")
    make_team()
    # tpm delegates to coder; coder reports to syseng; syseng answers the user.
    runner = ScriptedRunner({"script": {
        "tpm": [["coder", "please build X"]],
        "coder": [["syseng", "built X, please verify"]],
        "syseng": [["user", "verified X"]]}})
    sup = Supervisor(AGENTS, runner, max_hops=32, quiet=True)
    load_transport("user").send("tpm", "get X built and verified")
    turns = sup.run_until_idle()
    sup.close()

    return sum([
        check("the initial message woke tpm without anyone checking mail",
              runner.woken and runner.woken[0] == "tpm", str(runner.woken)),
        check("tpm's delegation woke coder", "coder" in runner.woken, str(runner.woken)),
        check("coder's handoff woke syseng", "syseng" in runner.woken, str(runner.woken)),
        check("the chain ran in order",
              runner.woken == ["tpm", "coder", "syseng"], str(runner.woken)),
        check("one instruction produced three turns", turns == 3, str(turns)),
        check("supervisor stopped once the team went idle", turns == len(runner.woken)),
    ]), 6


def test_hop_budget() -> tuple[int, int]:
    """Two agents talking forever must not run forever."""
    print("\n== runaway protection ==")
    make_team()

    class PingPong(ScriptedRunner):
        def wake(self, agent, message):
            self.woken.append(agent)
            other = "coder" if agent == "tpm" else "tpm"
            bus = load_transport(agent)
            bus.send(other, "and another thing")
            bus.close()
            return "looping"

    runner = PingPong({})
    sup = Supervisor(["tpm", "coder"], runner, max_hops=6, quiet=True)
    load_transport("user").send("tpm", "start arguing")
    sup.run_until_idle()
    sup.close()
    return sum([
        check("stops at the hop budget", len(runner.woken) == 6, str(len(runner.woken))),
        check("budget is reported as reached", sup.hops >= sup.max_hops),
    ]), 2


def test_failure_isolation() -> tuple[int, int]:
    """One agent failing must not take the team down."""
    print("\n== failure isolation ==")
    make_team()
    runner = ScriptedRunner({"fail": ["coder"],
                             "script": {"tpm": [["coder", "do it"],
                                                ["syseng", "also do it"]]}})
    sup = Supervisor(AGENTS, runner, max_hops=32, quiet=True)
    load_transport("user").send("tpm", "delegate to both")
    sup.run_until_idle()
    sup.close()
    return sum([
        check("the failing agent was attempted", "coder" in runner.woken),
        check("the healthy agent still ran", "syseng" in runner.woken, str(runner.woken)),
    ]), 2


def test_peek_is_nondestructive() -> tuple[int, int]:
    """Checking status must not eat the queue."""
    print("\n== status does not consume mail ==")
    make_team()
    load_transport("user").send("coder", "still here?")
    sup = Supervisor(AGENTS, ScriptedRunner({}), max_hops=8, quiet=True)
    first = sup.pending()["coder"]
    second = sup.pending()["coder"]
    delivered = sup.step()
    sup.close()
    return sum([
        check("peek reports the waiting message", len(first) == 1, str(first)),
        check("peeking twice still shows it", len(second) == 1, str(second)),
        check("the message was still there to deliver", delivered == 1, str(delivered)),
    ]), 3


class AckLoop(ScriptedRunner):
    """Reproduces the observed pathology: teammates acking each other forever.

    tpm answers the user on its first turn, then everyone keeps politely
    replying. Without a terminator this runs until the hop budget.
    """

    def wake(self, agent, message):
        self.woken.append(agent)
        bus = load_transport(agent)
        if agent == "tpm":
            if len(self.woken) == 1:
                bus.send("user", "the team is assembled and standing by")
            bus.send("coder", "acknowledged, standing by")
        else:
            bus.send("tpm", "acknowledged, standing by")
        bus.close()
        return "ack"


def test_stop_on_answer() -> tuple[int, int]:
    """Answering the user ends the episode instead of an ack spiral."""
    print("\n== answering the user ends the episode ==")
    make_team()
    runner = AckLoop({})
    sup = Supervisor(["tpm", "coder"], runner, max_hops=32, quiet=True)
    load_transport("user").send("tpm", "introduce yourself to the team")
    turns = sup.run_until_idle()
    stopped, woken = sup.stopped, len(runner.woken)
    sup.close()

    make_team()
    loose = AckLoop({})
    sup2 = Supervisor(["tpm", "coder"], loose, max_hops=12, quiet=True,
                      stop_on_answer=False)
    load_transport("user").send("tpm", "introduce yourself to the team")
    sup2.run_until_idle()
    unbounded = len(loose.woken)
    sup2.close()

    return sum([
        check("the ack spiral is cut short", turns <= 4, f"{turns} turns"),
        check("it stopped because the user was answered",
              stopped.startswith("the user was answered"), stopped),
        check("well under the hop budget", woken < 12, str(woken)),
        check("opting out lets it run on (the old behaviour)",
              unbounded >= 12, str(unbounded)),
    ]), 4


def test_user_mail_survives() -> tuple[int, int]:
    """Detecting the answer must not consume the answer."""
    print("\n== the user's answer is still readable ==")
    make_team()
    sup = Supervisor(["tpm", "coder"], AckLoop({}), max_hops=32, quiet=True)
    load_transport("user").send("tpm", "introduce yourself")
    sup.run_until_idle()
    sup.close()
    delivered = load_transport("user").fetch()
    return sum([
        check("the user actually receives the answer", len(delivered) >= 1,
              str(delivered)),
        check("the answer has the expected content",
              any("standing by" in m.content for m in delivered),
              str([m.content for m in delivered])),
    ]), 2


def test_loader() -> tuple[int, int]:
    print("\n== runner loader safety ==")
    import subprocess, os
    from rpc_util import PY

    def run(env):
        p = subprocess.run(
            [str(PY), "-c", "from agyteam.runner import load; load()"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
            env={**os.environ, "PYTHONPATH": str(ROOT), **env})
        return p.returncode, (p.stderr or "") + (p.stdout or "")

    rc1, o1 = run({"AGYTEAM_RUNNER": "nosuchmodule:Thing"})
    rc2, o2 = run({"AGYTEAM_RUNNER": "agyteam.runner_agy"})
    rc3, o3 = run({"AGYTEAM_RUNNER": "agyteam.scope:Scopes"})
    # the default runner must load even where the agy binary is absent, and
    # report that clearly rather than crashing the supervisor
    from agyteam.runner_agy import AgyRunner
    missing = AgyRunner({"binary": "definitely-not-installed-xyz"})
    msg = missing.wake("tpm", "hello")
    return sum([
        check("missing module fails loudly", rc1 != 0 and "cannot load" in o1, o1),
        check("malformed spec rejected", rc2 != 0 and "module:Class" in o2, o2),
        check("non-Runner class rejected", rc3 != 0 and "not a" in o3, o3),
        check("absent agy binary is a clear message, not a crash",
              msg.startswith("[error:") and "not found on PATH" in msg, msg),
    ]), 4


def test_anomaly_detection_and_context_purge() -> tuple[int, int]:
    """Anomalous token explosion triggers immediate purge and halts the episode without distillation."""
    print("\n== anomaly detection and context purge ==")
    # Pin the threshold for this test rather than inheriting the shipped
    # default. The fixture below used to be chosen to exceed whatever config
    # said, so retuning the production constant broke the test -- which is
    # backwards: a test of the mechanism should not depend on the tuning.
    from agyteam import config as _cfg
    _saved_threshold = _cfg.ANOMALY_OUTPUT_TOKENS_THRESHOLD
    _cfg.ANOMALY_OUTPUT_TOKENS_THRESHOLD = 4_000
    make_team()

    class AnomalyRunner(ScriptedRunner):
        def wake(self, agent, message):
            self.woken.append(agent)
            cid = f"conv-{agent}-123"
            self.remember_conversation(agent, cid)
            if agent == "coder":
                self.observer.record_turn(
                    agent, cid, duration_s=0.5,
                    input_tokens=200, output_tokens=5000, total_tokens=5200,
                )
                return "repeating " * 500
            self.observer.record_turn(
                agent, cid, duration_s=0.1,
                input_tokens=100, output_tokens=50, total_tokens=150,
            )
            bus = load_transport(agent)
            bus.send("coder", "do work")
            bus.close()
            return "delegated"

    runner = AnomalyRunner({})
    sup = Supervisor(["tpm", "coder"], runner, max_hops=10, quiet=True)
    load_transport("user").send("tpm", "kick off task")

    turns = sup.run_until_idle()

    stopped = sup.stopped
    coder_cid_after = runner.conversation_id("coder")
    convs_after = runner.conversations()

    events = sup.observer.events()
    failures = [
        ev for ev in events
        if ev.get("event") == "failure" and ev.get("agent") == "coder"
    ]
    cycled = sup.auto_cycle()
    distill_counts = sup.distill("coder")

    sup.close()

    _cfg.ANOMALY_OUTPUT_TOKENS_THRESHOLD = _saved_threshold   # leave no global set
    return sum([
        check("anomaly halts the episode immediately", turns == 2, f"turns: {turns}"),
        check("supervisor reports anomaly detection in stopped reason",
              "anomaly detected for coder" in stopped and "degenerate" in stopped, stopped),
        check("purged agent conversation context was cleared from runner",
              coder_cid_after is None and "coder" not in convs_after, str(convs_after)),
        check("observer recorded failure event for anomaly",
              len(failures) == 1 and "anomaly detected" in failures[0].get("error", ""), str(failures)),
        check("the shipped threshold is restored after the test",
              _cfg.ANOMALY_OUTPUT_TOKENS_THRESHOLD == _saved_threshold),
        check("distillation was completely bypassed on purged agent",
              distill_counts[0] == distill_counts[1] and "coder" not in cycled),
    ]), 6



def test_user_mail_priority() -> tuple[int, int]:
    """User mail takes precedence both across agents and within an inbox."""
    print("\n== user mail priority ==")
    make_team()

    wake_log = []

    class PriorityRunner(ScriptedRunner):
        def wake(self, agent, message):
            self.woken.append(agent)
            wake_log.append((agent, message))
            return "ack"

    runner = PriorityRunner({})
    # Initial agents registered in order: tpm, coder, syseng
    sup = Supervisor(["tpm", "coder", "syseng"], runner, max_hops=10, quiet=True)

    # 1. tpm (first in transports) receives only peer mail
    load_transport("coder").send("tpm", "peer status for tpm")

    # 2. coder receives peer mail, then user mail, then peer mail
    load_transport("tpm").send("coder", "routine task 1")
    load_transport("user").send("coder", "urgent user interrupt")
    load_transport("tpm").send("coder", "routine task 2")

    # 3. syseng (last in transports) receives user mail
    load_transport("user").send("syseng", "urgent user question for syseng")

    # Run one supervisor step
    dispatched = sup.step()
    sup.close()

    coder_wake_msg = next(m for a, m in wake_log if a == "coder")
    u_pos = coder_wake_msg.find("urgent user interrupt")
    p1_pos = coder_wake_msg.find("routine task 1")
    p2_pos = coder_wake_msg.find("routine task 2")
    first_sender_line = next(line for line in coder_wake_msg.splitlines() if "] from " in line)

    # Defensive: transport whose peek() raises an exception does not crash step()
    class BrokenPeekTransport:
        def peek(self):
            raise RuntimeError("peek failed")

        def fetch(self):
            return []

        def close(self):
            pass

    sup2 = Supervisor([], runner, max_hops=10, quiet=True)
    sup2.transports = {"broken": BrokenPeekTransport()}
    step_res = sup2.step()
    sup2.close()

    return sum([
        check("all three agents dispatched in one step", dispatched == 3, f"dispatched: {dispatched}"),
        check("agents with user mail dispatched before agent with only peer mail",
              runner.woken.index("coder") < runner.woken.index("tpm")
              and runner.woken.index("syseng") < runner.woken.index("tpm"),
              str(runner.woken)),
        check("within-inbox user message rendered before peer messages",
              0 <= u_pos < p1_pos < p2_pos,
              coder_wake_msg),
        check("first message in wake prompt is from user",
              "from user" in first_sender_line,
              first_sender_line),
        check("peek exception handled defensively without crashing",
              step_res == 0,
              f"step_res: {step_res}"),
    ]), 5


def test_turn_failure_requeues_mail() -> tuple[int, int]:
    """Messages are not lost when an agent turn fails (exception or [error: reply)."""
    print("\n== turn failure requeues unconsumed mail ==")
    import os

    class FlakyRunner(ScriptedRunner):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.turn_count = 0
            self.woken_bodies = []

        def wake(self, agent, message):
            self.woken.append(agent)
            self.woken_bodies.append(message)
            self.turn_count += 1
            if self.turn_count == 1:
                raise RuntimeError("transient network 429")
            elif self.turn_count == 2:
                return "[error: context length exceeded]"
            else:
                return "ack: work completed"

    # 1. FileTransport (default)
    make_team()
    load_transport("user").send("coder", "urgent user instructions")
    load_transport("tpm").send("coder", "peer follow-up")

    runner1 = FlakyRunner({})
    sup1 = Supervisor(["coder"], runner1, max_hops=10, quiet=True)

    # Turn 1: Runner raises exception
    sup1.step()
    p1 = sup1.transports["coder"].peek()

    # Turn 2: Runner returns [error: ...]
    sup1.step()
    p2 = sup1.transports["coder"].peek()

    # Turn 3: Runner succeeds
    sup1.step()
    p3 = sup1.transports["coder"].peek()
    sup1.close()

    turn3_prompt = runner1.woken_bodies[2] if len(runner1.woken_bodies) >= 3 else ""

    # 2. SqliteTransport (independent fixture)
    sqlite_db = Path(tempfile.mkdtemp(prefix="agyteam-fixture-sqlsup-")) / "bus.db"
    sql_cfg = json.dumps({"db": str(sqlite_db), "roster": [{"name": "coder", "role": "implements"}]})
    saved_trans = os.environ.get("AGYTEAM_BUS_TRANSPORT")
    saved_cfg = os.environ.get("AGYTEAM_BUS_CONFIG")
    os.environ["AGYTEAM_BUS_TRANSPORT"] = "fixture_transport:SqliteTransport"
    os.environ["AGYTEAM_BUS_CONFIG"] = sql_cfg
    try:
        sql_user_trans = load_transport("user")
        sql_user_trans.send("coder", "urgent sqlite user instruction")
        sql_peer_trans = load_transport("tpm")
        sql_peer_trans.send("coder", "sqlite peer task")

        runner2 = FlakyRunner({})
        sup2 = Supervisor(["coder"], runner2, max_hops=10, quiet=True)

        sup2.step()
        sql_p1 = sup2.transports["coder"].peek()

        sup2.step()
        sql_p2 = sup2.transports["coder"].peek()

        sup2.step()
        sql_p3 = sup2.transports["coder"].peek()
        sup2.close()

        sql_turn3_prompt = runner2.woken_bodies[2] if len(runner2.woken_bodies) >= 3 else ""
    finally:
        if saved_trans is not None:
            os.environ["AGYTEAM_BUS_TRANSPORT"] = saved_trans
        else:
            os.environ.pop("AGYTEAM_BUS_TRANSPORT", None)
        if saved_cfg is not None:
            os.environ["AGYTEAM_BUS_CONFIG"] = saved_cfg
        else:
            os.environ.pop("AGYTEAM_BUS_CONFIG", None)
        shutil.rmtree(sqlite_db.parent, ignore_errors=True)

    return sum([
        check("file transport retains 2 messages after wake exception",
              p1 is not None and len(p1) == 2, str(p1)),
        check("file transport retains 2 messages after [error: wake reply",
              p2 is not None and len(p2) == 2, str(p2)),
        check("file transport drains inbox on successful turn",
              p3 is not None and len(p3) == 0, str(p3)),
        check("file transport third turn received both preserved messages in prompt",
              "urgent user instructions" in turn3_prompt and "peer follow-up" in turn3_prompt,
              turn3_prompt),
        check("sqlite transport retains 2 messages after wake exception",
              sql_p1 is not None and len(sql_p1) == 2, str(sql_p1)),
        check("sqlite transport retains 2 messages after [error: wake reply",
              sql_p2 is not None and len(sql_p2) == 2, str(sql_p2)),
        check("sqlite transport drains inbox on successful turn",
              sql_p3 is not None and len(sql_p3) == 0, str(sql_p3)),
        check("sqlite transport third turn received both preserved messages in prompt",
              "urgent sqlite user instruction" in sql_turn3_prompt and "sqlite peer task" in sql_turn3_prompt,
              sql_turn3_prompt),
    ]), 8


def test_sdk_runner_close_timeout() -> tuple[int, int]:
    """SdkRunner.close() and reset() gracefully time out if session __aexit__ hangs."""
    print("\n== sdk runner close timeout ==")
    import asyncio
    import os
    import threading
    import time
    from agyteam.runner_sdk import SdkRunner

    td = Path(tempfile.mkdtemp(prefix="agyteam-sdkclose-")) / "team"
    td.mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "coder", "role": "implements"},
        {"name": "syseng", "role": "verifies"},
    ]}))
    saved_team_dir = os.environ.get("AGYTEAM_TEAM_DIR")
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)

    class StuckCM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await asyncio.get_running_loop().create_future()

    try:
        runner = SdkRunner()

        # 1. Test reset timeout with stuck CM
        runner._cms["coder"] = StuckCM()
        runner._agents["coder"] = "fake_agent"
        t0_reset = time.monotonic()
        t_reset = threading.Thread(target=lambda: runner.reset("coder"))
        t_reset.start()
        t_reset.join(timeout=6.0)
        reset_dur = time.monotonic() - t0_reset
        reset_finished = not t_reset.is_alive()

        # 2. Test close timeout with stuck CM
        runner._cms["syseng"] = StuckCM()
        runner._agents["syseng"] = "fake_agent"
        t0_close = time.monotonic()
        t_close = threading.Thread(target=runner.close)
        t_close.start()
        t_close.join(timeout=6.0)
        close_dur = time.monotonic() - t0_close
        close_finished = not t_close.is_alive()

        thread_alive = runner._thread.is_alive()
    finally:
        if saved_team_dir is not None:
            os.environ["AGYTEAM_TEAM_DIR"] = saved_team_dir
        else:
            os.environ.pop("AGYTEAM_TEAM_DIR", None)
        shutil.rmtree(td.parent, ignore_errors=True)

    return sum([
        check("reset with hanging session returns within bounded timeout",
              reset_finished and 1.8 <= reset_dur < 5.0, f"reset_dur: {reset_dur:.2f}s, finished: {reset_finished}"),
        check("session removed from runner after reset",
              "coder" not in runner._cms and "coder" not in runner._agents),
        check("close with hanging session returns within bounded timeout",
              close_finished and 1.8 <= close_dur < 5.0, f"close_dur: {close_dur:.2f}s, finished: {close_finished}"),
        check("runner background thread terminated on close",
              not thread_alive, f"thread_alive: {thread_alive}"),
    ]), 4


def test_busy_turn_is_not_an_anomaly() -> tuple[int, int]:
    """A big turn is expensive, not sick. Volume alone must not purge.

    This is a regression test with a date on it: on 2026-09-11 the guard purged
    syseng and halted the episode seconds after he finished a working build,
    because 229 shell invocations legitimately cost 225,493 output tokens. The
    work survived only because it was already on disk.
    """
    print("\n== a busy turn is not a sick turn ==")
    from agyteam import config as _cfg
    saved = _cfg.ANOMALY_OUTPUT_TOKENS_THRESHOLD
    _cfg.ANOMALY_OUTPUT_TOKENS_THRESHOLD = 4_000
    make_team()

    class BusyRunner(ScriptedRunner):
        def wake(self, agent, message):
            self.woken.append(agent)
            cid = f"conv-{agent}-busy"
            self.remember_conversation(agent, cid)
            self.observer.record_turn(agent, cid, duration_s=1.0, input_tokens=200,
                                      output_tokens=99_000, total_tokens=99_200)
            if agent == "tpm":
                bus = load_transport(agent)
                bus.send("coder", "please build the thing")
                bus.close()
                return "delegating the build"
            # Large and varied, the way a real build report is: distinct
            # commands, paths and numbers rather than one phrase repeated.
            return "\n".join(
                f"step {i}: ran /venv/bin/python sweep_{i}.py --alpha {i/997:.6f} "
                f"-> converged in {i * 7 % 991} samples, residual {i * 13 % 877}e-9"
                for i in range(600))

    runner = BusyRunner({})
    sup = Supervisor(["tpm", "coder"], runner, max_hops=10, quiet=True)
    load_transport("user").send("tpm", "kick off the build")
    turns = sup.run_until_idle()
    stopped, cid_after = sup.stopped, runner.conversation_id("coder")
    sup.close()
    _cfg.ANOMALY_OUTPUT_TOKENS_THRESHOLD = saved

    from agyteam.supervisor import repetition_ratio
    healthy = repetition_ratio("\n".join(
        f"step {i}: ran sweep_{i}.py --alpha {i/997:.6f}" for i in range(600)))
    spew = repetition_ratio("the quick brown fox. " * 8000)

    return sum([
        check("the episode was not halted by volume alone",
              "anomaly" not in stopped, stopped),
        check("both agents still ran", turns == 2, f"turns: {turns}"),
        check("the busy agent kept its context", cid_after == "conv-coder-busy",
              str(cid_after)),
        check("varied output scores as healthy",
              healthy < _cfg.ANOMALY_REPETITION_RATIO, f"{healthy:.2f}x"),
        check("repeated output still scores as degenerate",
              spew > _cfg.ANOMALY_REPETITION_RATIO, f"{spew:.2f}x"),
    ]), 5


if __name__ == "__main__":
    totals = [test_cascade(), test_hop_budget(), test_failure_isolation(),
              test_peek_is_nondestructive(), test_stop_on_answer(),
              test_user_mail_survives(), test_loader(),
              test_anomaly_detection_and_context_purge(),
              test_user_mail_priority(),
              test_turn_failure_requeues_mail(),
              test_sdk_runner_close_timeout(),
              test_busy_turn_is_not_an_anomaly()]
    got, want = sum(s for s, _ in totals), sum(t for _, t in totals)
    print(f"\n== supervisor: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
