"""A gate that cannot run must say so, not return a verdict.

Measured on the installed plugin: a proof file containing one passing test came
back `[error: verification failed: proof_file did not pass (exit code 1)]`. The
proof was fine. The interpreter had no pytest, and the real reason -- "No module
named pytest" -- went to a stderr nobody reads.

That is this project's central failure sitting inside its most load-bearing
check. It is also structural rather than unlucky: the plugin install is
deliberately dependency-free, pure stdlib under a bare system python3, and the
proof gate is the one thing in the bus server that needs a third-party package.
Every install meets this.

The damage is not just a bad error message. An agent reading "your proof did not
pass" does the rational thing and rewrites a proof that was already correct,
and a team that can never record a review looks like a team that never reviews.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import mcp_bus  # noqa: E402
from agyteam.transport_file import FileTransport  # noqa: E402

TS = "2026-09-15 05:00:00"


@pytest.fixture(autouse=True)
def no_cached_interpreter(monkeypatch):
    """The resolved interpreter is cached; tests must not inherit each other's."""
    monkeypatch.setattr(mcp_bus, "_PROOF_PY", ())
    monkeypatch.delenv("AGYTEAM_PROOF_PYTHON", raising=False)


@pytest.fixture
def team(tmp_path, monkeypatch):
    d = tmp_path / "team"
    d.mkdir()
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(d))
    monkeypatch.delenv("AGYTEAM_AUDIT_LOG", raising=False)
    (d / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "gates"},
        {"name": "coder", "role": "implements"}]}))
    (d / "events.jsonl").write_text(json.dumps(
        {"ts": TS, "event": "turn", "agent": "coder", "conversation": "c1"}) + "\n")
    (d / "proof.py").write_text("def test_ok():\n    assert 1 == 1\n")
    return d


def review(team, **over):
    args = {"what": "the work", "author": "coder", "verdict": "approved",
            "proof_file": str(team / "proof.py")}
    args.update(over)
    return mcp_bus._record_review(FileTransport("manager"), args)


def strand(monkeypatch, tmp_path):
    """Put every interpreter candidate on a python that cannot import pytest.

    A fake binary rather than the system python: whether /usr/bin/python3 has
    pytest is a property of the machine, and a test that passes because of one
    is not testing anything.
    """
    fake = tmp_path / "python-without-pytest"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(mcp_bus, "_REPO_ROOT", tmp_path / "nowhere")
    monkeypatch.setattr(mcp_bus.sys, "executable", str(fake))
    monkeypatch.chdir(tmp_path)
    return fake


# --- the measured failure ----------------------------------------------------

def test_no_pytest_is_reported_as_an_environment_failure(team, tmp_path,
                                                         monkeypatch):
    strand(monkeypatch, tmp_path)
    out = review(team)
    assert "the review gate could not run" in out, out
    assert "did not pass" not in out, \
        "'could not run' must never be phrased as a verdict on the proof"


def test_it_says_the_review_was_not_recorded(team, tmp_path, monkeypatch):
    """Silence about what happened to the review is how a team gets confused
    about whether the gate is holding."""
    strand(monkeypatch, tmp_path)
    out = review(team)
    assert "has NOT been recorded" in out
    assert not (team / "reviews.jsonl").exists()


def test_it_names_what_it_tried_and_how_to_fix_it(team, tmp_path, monkeypatch):
    fake = strand(monkeypatch, tmp_path)
    out = review(team)
    assert str(fake) in out, "name the interpreters, or the operator guesses"
    assert "AGYTEAM_PROOF_PYTHON" in out


def test_a_changes_requested_verdict_cannot_be_manufactured_by_a_broken_env(
        team, tmp_path, monkeypatch):
    """The dangerous direction. Without pytest every proof 'fails', which is
    precisely what changes_requested requires -- so a broken environment could
    otherwise be used to reject a teammate's work without executing anything."""
    strand(monkeypatch, tmp_path)
    out = review(team, verdict="changes_requested")
    assert "could not run" in out
    assert not (team / "reviews.jsonl").exists()


# --- and it still works where pytest exists ----------------------------------

def test_a_real_interpreter_is_found_and_the_review_records(team):
    out = review(team)
    assert out.startswith("[review recorded:"), out
    assert json.loads((team / "reviews.jsonl").read_text().splitlines()[0])["author"] == "coder"


def test_the_env_override_is_honoured(team, tmp_path, monkeypatch):
    """The escape hatch has to work on a host where nothing is discoverable."""
    monkeypatch.setattr(mcp_bus, "_REPO_ROOT", tmp_path / "nowhere")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGYTEAM_PROOF_PYTHON", sys.executable)
    assert review(team).startswith("[review recorded:")


def test_the_repo_venv_is_preferred_over_a_bare_interpreter(tmp_path, monkeypatch):
    """Order matters: the venv is the one with the project installed."""
    venv_py = ROOT / ".venv" / "bin" / "python"
    if not venv_py.exists():
        pytest.skip("no repo venv on this machine")
    monkeypatch.setattr(mcp_bus, "_PROOF_PY", ())
    chosen, tried = mcp_bus._proof_interpreter()
    assert chosen == str(venv_py), tried


def test_the_probe_is_a_call_not_a_path_check(tmp_path, monkeypatch):
    """A file at .venv/bin/python proves nothing about pytest being importable.

    The first version of this resolution checked only that the path existed --
    which is exactly how the installed plugin came to point at a venv that was
    not there, and then at a python that could not import pytest.
    """
    fake_venv = tmp_path / ".venv" / "bin"
    fake_venv.mkdir(parents=True)
    fake = fake_venv / "python"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(mcp_bus, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(mcp_bus, "_PROOF_PY", ())
    chosen, tried = mcp_bus._proof_interpreter()
    assert str(fake) in tried, "it must have been tried"
    assert chosen != str(fake), "and rejected: it cannot import pytest"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
