"""Upgrading a live team without waiting for an idle moment.

Upgrading used to mean waiting until every agent was idle at once, which a
team that keeps itself busy almost never is, or stopping it, which cancelled
the turns in flight. A restart request drains instead: no new turns, the
ones in flight finish, then the supervisor restarts itself on the new code.
`python -m agyteam.update` does the update and the restarts in one command.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import actions, config, heartbeat, update  # noqa: E402
from agyteam.supervisor import RESTART_FILE, Supervisor, request_restart  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["tpm", "coder", "qa"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "teams" / "demo" / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "coordinates"}, {"name": "coder", "role": "implements"},
        {"name": "qa", "role": "reviews"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    monkeypatch.setattr(config, "WAKE_BACKOFF_BASE_S", 0.0)
    monkeypatch.setattr(config, "WAKE_BACKOFF_MAX_S", 0.0)
    return td


class Gate(ScriptedRunner):
    """coder's turn runs until the test opens the gate."""

    def __init__(self):
        super().__init__({})
        self.gate = threading.Event()
        self.cancelled = []

    def wake(self, agent, message):
        self.woken.append(agent)
        if agent == "coder":
            self.gate.wait(10)
        return "ok"

    def cancel(self, handle):
        self.cancelled.append(handle.get("id"))
        self.gate.set()
        super().cancel(handle)


def inbox(name):
    return [m.content for m in load_transport(name).peek() or []]


def test_a_restart_drains_new_turns_wait_and_the_running_one_finishes(team):
    runner = Gate()
    sup = Supervisor(AGENTS, runner, quiet=True, require_review=False, poll=0.05)
    assert sup._claim_team()
    try:
        load_transport("user").send("coder", "long job")
        sup._pass()
        assert "coder" in sup.inflight
        request_restart(team, by="test")
        load_transport("user").send("qa", "arrived while draining")
        sup._pass()
        assert sup.draining is not None and "qa" not in sup.inflight, \
            "a new turn began while the supervisor was draining"
        assert json.loads((team / "supervisor.json").read_text())["draining"] is True
        runner.gate.set()
        for _ in range(100):
            sup._pass()
            if sup.restart_ready:
                break
            time.sleep(0.02)
        assert sup.restart_ready and sup.stopped == "restarting"
        assert runner.cancelled == [], "the running turn was cancelled, not finished"
        assert inbox("qa") == ["arrived while draining"], "mail that arrived while draining was lost"
        assert not (team / RESTART_FILE).exists()
    finally:
        sup.close()


def test_now_cancels_the_running_turn_and_gives_its_mail_back(team):
    runner = Gate()
    sup = Supervisor(AGENTS, runner, quiet=True, require_review=False)
    assert sup._claim_team()
    try:
        load_transport("user").send("coder", "long job")
        sup._pass()
        request_restart(team, now=True, by="test")
        sup._pass()
        assert sup.restart_ready and runner.cancelled
        assert inbox("coder") == ["long job"]
    finally:
        sup.close()


# --- a daemon restarts itself in place --------------------------------------------

RUNNER = '''
from fixture_runner import ScriptedRunner
class Quiet(ScriptedRunner):
    label = "quiet"
'''


def test_a_daemon_restarts_in_place_with_its_arguments(team, tmp_path):
    (tmp_path / "quiet_runner.py").write_text(RUNNER)
    doc = json.loads((team / "roster.json").read_text())
    doc["supervisor"] = {"runner": "quiet_runner:Quiet"}
    (team / "roster.json").write_text(json.dumps(doc))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(tmp_path), str(ROOT / "evals"), str(ROOT)]))
    log = open(tmp_path / "daemon.log", "wb")
    proc = subprocess.Popen([sys.executable, "-m", "agyteam.supervisor", "--daemon", "--quiet",
                             "--poll", "0.1", "--team-dir", str(team)],
                            env=env, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
    try:
        first = None
        for _ in range(100):
            first = heartbeat.running(team)
            if first:
                break
            time.sleep(0.1)
        assert first and first["restartable"], (tmp_path / "daemon.log").read_text()
        time.sleep(1.1)                    # started is to the second
        actions.restart(team, by="test")
        for _ in range(150):
            beat = heartbeat.running(team) or {}
            if beat.get("started") and beat["started"] != first["started"]:
                break
            time.sleep(0.1)
        assert beat.get("started") != first["started"], (tmp_path / "daemon.log").read_text()
        assert beat["pid"] == proc.pid == first["pid"], "it did not restart in place"
        assert beat["runner"] == "quiet" and proc.poll() is None
    finally:
        proc.kill()
        proc.wait()
        log.close()


def test_a_restart_needs_a_supervisor_that_knows_the_request(team):
    with pytest.raises(actions.OperatorError, match="no supervisor"):
        actions.restart(team)
    (team / "supervisor.json").write_text(json.dumps({
        "pid": os.getpid(), "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "daemon", "inflight": ["coder"]}))
    with pytest.raises(actions.OperatorError, match="predates graceful restart"):
        actions.restart(team)


def test_update_waits_for_an_idle_moment_before_stopping_an_older_supervisor(team):
    (team / "supervisor.json").write_text(json.dumps({
        "pid": os.getpid(), "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "daemon", "inflight": ["coder"]}))
    out = update.restart_team(team, legacy_wait_s=0, log=lambda *a: None)
    assert out["state"] == "busy" and "--now" in out["note"]
    assert not (team / ".stop").exists(), "it stopped a supervisor with a turn in flight"


def test_update_finds_the_teams_under_a_root(team):
    root = team.parent.parent
    (root / "empty").mkdir()
    assert update.teams([str(root)]) == [team]


# --- the code -------------------------------------------------------------------

def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def test_a_checkout_fast_forwards_and_a_dirty_one_is_refused(tmp_path):
    origin, work = tmp_path / "origin", tmp_path / "work"
    origin.mkdir()
    git("init", "-q", "-b", "main", cwd=origin)
    (origin / "f").write_text("1")
    git("add", "f", cwd=origin)
    git("commit", "-qm", "one", cwd=origin)
    git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    (origin / "f").write_text("2")
    git("commit", "-qam", "two", cwd=origin)
    p = update.plan(repo=work)
    assert p["kind"] == "git" and p["behind"] == 1 and not p["dirty"]
    (work / "f").write_text("local edit")
    with pytest.raises(update.UpdateError, match="local changes"):
        update.update_code(update.plan(repo=work))
    git("checkout", "--", "f", cwd=work)
    assert "fast-forwarded 1" in update.update_code(update.plan(repo=work))
    assert (work / "f").read_text() == "2"


def test_a_tag_checkout_moves_to_the_newest_release(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    for v in ("v3.9.0", "v3.10.0", "v3.2.0"):
        (repo / "f").write_text(v)
        git("add", "f", cwd=repo)
        git("commit", "-qm", v, cwd=repo)
        git("tag", v, cwd=repo)
    git("-c", "advice.detachedHead=false", "checkout", "-q", "v3.2.0", cwd=repo)
    p = update.plan(repo=repo)
    assert p["kind"] == "git-tag" and p["target"] == "v3.10.0", "tags sort as numbers"
    update.update_code(p)
    assert (repo / "f").read_text() == "v3.10.0"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
