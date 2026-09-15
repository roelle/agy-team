"""The preflight must fail on the things that actually go wrong.

Three findings needed a host to discover and cannot be tested from this repo:
a tool whose signature the agent never receives, a brief missing that index,
and a bus server bound to a team it was spawned against hours earlier. All
three are cheap to self-diagnose, and the point of `agyteam.doctor` is that
the operator never has to discover any of them nine delegations in.

These tests check the preflight has teeth, which matters more than usual: a
preflight that always prints "clean" is worse than none, because it is
believed.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import doctor  # noqa: E402


def make_team(tmp_path, name="team"):
    td = tmp_path / name
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "gates", "tools_off": ["create_file"]},
        {"name": "coder", "role": "implements"}]}))
    return td


def run(team_dir, tmp_path, *args, env_extra=None):
    env = dict(os.environ,
               AGYTEAM_TEAM_DIR=str(team_dir),
               AGYTEAM_DURABLE_DIR=str(tmp_path / "durable"))
    env.pop("AGYTEAM_AUDIT_LOG", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "agyteam.doctor", "--brief-lines", "6", *args],
        cwd=ROOT, env=env, capture_output=True, text=True)


def test_a_wired_team_passes(tmp_path):
    td = make_team(tmp_path)
    p = run(td, tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "Preflight clean" in p.stdout


def test_the_probe_is_a_call_not_an_inventory(tmp_path):
    """A lazily-loaded MCP tool never appears in a tool list.

    So the preflight calls each one and prints the reply, rather than asking
    the agent what it has.
    """
    td = make_team(tmp_path)
    p = run(td, tmp_path)
    assert "send_to_teammate -> [delivered" in p.stdout, p.stdout
    assert "check_inbox returned the probe" in p.stdout
    assert "list_teammates -> 2 entries" in p.stdout
    assert "record_retro -> [retrospective answer recorded" in p.stdout


def test_the_probe_names_both_paths_when_it_lands(tmp_path):
    td = make_team(tmp_path)
    p = run(td, tmp_path)
    assert f"the probe is readable in {td / 'bus.jsonl'}" in p.stdout


def test_it_prints_the_resolved_runner_and_its_flags(tmp_path):
    td = make_team(tmp_path)
    p = run(td, tmp_path,
            env_extra={"AGYTEAM_RUNNER": "agyteam.runner_agy:AgyRunner"})
    assert "runner: agyteam.runner_agy:AgyRunner" in p.stdout
    assert "supports_audit = False" in p.stdout
    assert "supports_capability_scoping = False" in p.stdout
    assert "statement of intent, not a boundary" in p.stdout


def test_an_unloadable_runner_fails_rather_than_crashing(tmp_path):
    """load() raises SystemExit; the preflight must report, not inherit it."""
    td = make_team(tmp_path)
    p = run(td, tmp_path, env_extra={"AGYTEAM_RUNNER": "no.such:Runner"})
    assert p.returncode == 1
    assert "could not inspect runner" in p.stdout
    assert "PREFLIGHT FAILED" in p.stdout


def test_it_echoes_the_brief_so_a_missing_index_is_visible_now(tmp_path):
    td = make_team(tmp_path)
    p = run(td, tmp_path, "--brief-lines", "40")
    assert "the brief carries the bus tool signatures" in p.stdout
    assert "send_to_teammate(to, content)" in p.stdout, \
        "the default echo must reach far enough to show the tool index"


def test_an_unknown_agent_is_refused(tmp_path):
    td = make_team(tmp_path)
    p = run(td, tmp_path, "--agent", "nobody")
    assert p.returncode == 1
    assert "not on the roster" in p.stdout


def test_no_roster_exits_without_pretending(tmp_path):
    td = tmp_path / "empty"
    td.mkdir()
    p = run(td, tmp_path)
    assert p.returncode == 2
    assert "no roster" in p.stderr


# --- the stale bus server, which is the whole reason this file exists --------

class FakeReport(doctor.Report):
    def __init__(self):
        super().__init__()
        self.lines = []

    def line(self, mark, text):
        self.lines.append((mark.strip(), text))
        super().line(mark, text)

    def detail(self, text):
        pass


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="needs /proc")
def test_a_bus_server_bound_to_another_team_is_a_failure(tmp_path):
    """Measured: five servers alive from a previous team, agents delegating
    normally, every tool call returning success, mail landing in the old
    team's bus, and the new supervisor declaring the team idle."""
    this_team = make_team(tmp_path, "arena")
    other_team = make_team(tmp_path, "conv0916")

    proc = subprocess.Popen(
        [sys.executable, "-m", "agyteam.mcp_bus", str(other_team), "manager"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=dict(os.environ, AGYTEAM_TEAM_DIR=str(other_team)))
    try:
        r = FakeReport()
        doctor.check_stale_bus_servers(r, this_team)
        assert r.failed, [x for x in r.lines]
        assert any("DIFFERENT team" in text for _, text in r.lines)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="needs /proc")
def test_a_server_bound_to_this_team_is_fine(tmp_path):
    td = make_team(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-m", "agyteam.mcp_bus", str(td), "manager"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=dict(os.environ, AGYTEAM_TEAM_DIR=str(td)))
    try:
        r = FakeReport()
        doctor.check_stale_bus_servers(r, td)
        assert not r.failed, r.lines
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="needs /proc")
def test_a_process_merely_mentioning_the_module_is_not_a_server(tmp_path):
    """The first version of this check reported the shell running it.

    A check that cries wolf about the terminal you typed in is one people
    learn to skip, which makes it worse than absent.
    """
    td = make_team(tmp_path)
    proc = subprocess.Popen(
        ["/bin/sh", "-c", "echo agyteam.mcp_bus /somewhere/else >/dev/null; "
                          "sleep 30"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        r = FakeReport()
        doctor.check_stale_bus_servers(r, td)
        assert not r.failed, r.lines
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
