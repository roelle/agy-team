"""Which models the runtime can run, and refusing the ones it cannot.

Measured: a roster pinned to "gemini-3.1-pro-preview" (an SDK model id) on
the agy CLI failed every turn for manager and qa with "invalid model
selection". The CLI says what it offers (`agy models`); now agyteam asks.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import models, runner  # noqa: E402

AGY = "agyteam.runner_agy:AgyRunner"


@pytest.fixture
def fake_agy(tmp_path, monkeypatch):
    """An `agy` on PATH whose `models` prints what the real one prints."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "agy"
    exe.write_text("#!/bin/sh\n"
                   "if [ \"$1\" = models ]; then\n"
                   "  echo 'Fetching available models...'\n"
                   "  printf 'gemini-4-argon-high\\tGemini 4 Argon (High)\\n'\n"
                   "  printf 'gemini-3.8-flash-high\\tGemini 3.8 Flash (High)\\n'\n"
                   "  printf 'gemini-3.8-flash-low\\tGemini 3.8 Flash (Low)\\n'\n"
                   "  printf 'claude-opus-5-5-high\\tClaude Opus 5.5 (High)\\n'\n"
                   "fi\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("AGYTEAM_RUNNER", AGY)
    monkeypatch.setenv("AGYTEAM_MODEL_CHECK", "1")
    monkeypatch.delenv("AGYTEAM_RUNNER_CONFIG", raising=False)
    runner._MODEL_CACHE.clear()
    yield
    runner._MODEL_CACHE.clear()


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "gates", "model": "gemini-3.1-pro-preview"},
        {"name": "coder", "role": "implements", "model": "gemini-3.8-flash", "effort": "high"},
        {"name": "qa", "role": "reviews"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    return td


def test_the_cli_runner_reads_its_model_list(fake_agy):
    ids = [m["id"] for m in runner.list_models()]
    assert ids == ["gemini-4-argon-high", "gemini-3.8-flash-high", "gemini-3.8-flash-low",
                   "claude-opus-5-5-high"]


def test_a_family_name_counts_and_an_sdk_id_does_not(fake_agy):
    assert runner.model_available("gemini-3.8-flash") is True       # CLI default effort
    assert runner.model_available("gemini-3.8-flash", "high") is True
    assert runner.model_available("gemini-3.8-flash", "medium") is False
    assert runner.model_available("gemini-3.1-pro-preview") is False
    assert runner.model_available("gemini-3.8") is False, "a prefix is not a family"
    assert runner.model_available(None) is None


def test_a_runner_that_cannot_list_refuses_nothing(monkeypatch):
    monkeypatch.setenv("AGYTEAM_MODEL_CHECK", "1")
    monkeypatch.setenv("AGYTEAM_RUNNER", "agyteam.runner_host:HostRunner")
    monkeypatch.delenv("AGYTEAM_RUNNER_CONFIG", raising=False)
    runner._MODEL_CACHE.clear()
    assert runner.list_models() is None
    assert runner.model_available("anything") is None


def test_a_host_runner_lists_what_its_config_says(monkeypatch):
    monkeypatch.setenv("AGYTEAM_MODEL_CHECK", "1")
    monkeypatch.setenv("AGYTEAM_RUNNER", "agyteam.runner_host:HostRunner")
    monkeypatch.setenv("AGYTEAM_RUNNER_CONFIG", json.dumps({"models": ["fast", "smart"]}))
    runner._MODEL_CACHE.clear()
    assert runner.model_available("smart") is True and runner.model_available("x") is False


def test_setting_a_model_the_runner_lacks_is_refused_with_the_list(fake_agy, team):
    with pytest.raises(models.ModelError, match="gemini-4-argon-high"):
        models.set_model(team, "qa", "gemini-3.1-pro-preview")
    models.set_model(team, "qa", "gemini-4-argon-high")
    models.set_model(team, "qa", "made-up", check=False)          # --force


def test_the_record_flags_an_agent_pinned_to_an_unavailable_model(fake_agy, team):
    from agyteam.record import Record
    m = Record(team).models()
    assert m["manager"]["available"] is False
    assert m["coder"]["available"] is True and m["qa"]["available"] is None


def test_doctor_names_the_agent_whose_turns_will_fail(fake_agy, team, capsys):
    from agyteam import doctor
    r = doctor.Report()
    doctor.check_models(r, team)
    text = capsys.readouterr().out
    assert "manager" in text and "gemini-3.1-pro-preview" in text and r.failed


def test_the_cli_runner_records_the_model_it_actually_ran(fake_agy, team):
    """Recorded from the roster, a turn run on an override was logged as the
    roster's model."""
    from agyteam.runner_agy import AgyRunner
    models.set_model(team, "coder", "claude-opus-5-5-high")
    assert AgyRunner({})._model_for("coder") == "claude-opus-5-5-high"


def test_the_supervisor_warns_before_an_agent_lands_on_an_unavailable_model(fake_agy, team):
    from agyteam.supervisor import Supervisor
    from agyteam.transport import load as load_transport
    from fixture_runner import ScriptedRunner
    sup = Supervisor(["manager", "coder", "qa"], ScriptedRunner({}), quiet=True,
                     require_review=False)
    try:
        sup._apply_model_changes()                       # baseline
        doc = json.loads((team / "roster.json").read_text())
        doc["agents"][2]["model"] = "gemini-3.1-pro-preview"
        (team / "roster.json").write_text(json.dumps(doc))
        sup._apply_model_changes()
    finally:
        sup.close()
    told = [m for m in load_transport("user").peek() or [] if m.sender == "supervisor"]
    assert len(told) == 1 and "qa" in told[0].content and "does not offer" in told[0].content


def test_the_check_can_be_switched_off(fake_agy, monkeypatch):
    monkeypatch.setenv("AGYTEAM_MODEL_CHECK", "0")
    runner._MODEL_CACHE.clear()
    assert runner.model_available("gemini-3.1-pro-preview") is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
