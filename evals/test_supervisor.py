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


if __name__ == "__main__":
    totals = [test_cascade(), test_hop_budget(), test_failure_isolation(),
              test_peek_is_nondestructive(), test_stop_on_answer(),
              test_user_mail_survives(), test_loader()]
    got, want = sum(s for s, _ in totals), sum(t for _, t in totals)
    print(f"\n== supervisor: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
