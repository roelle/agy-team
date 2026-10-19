"""Four problems an integrator reported, each with the failure it caused.

- inbox_pull: a successful turn acknowledged its whole batch, deleting the
  check_inbox pages the agent never opened.
- Two slow turns ("timed out after 900s") counted as a broken conversation
  and retired a working agent's context; and every model change cycled the
  conversation, even on a runtime that switches model in place.
- Two supervisors could drive one team, each overwriting the other's
  heartbeat.
- A review could prove facts but not that a document did what was asked.
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import config, mcp_bus, models  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from agyteam.transport_file import FileTransport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["manager", "coder", "qa"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "gates"}, {"name": "coder", "role": "implements"},
        {"name": "qa", "role": "reviews"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    monkeypatch.setattr(config, "WAKE_BACKOFF_BASE_S", 0.0)
    monkeypatch.setattr(config, "WAKE_BACKOFF_MAX_S", 0.0)
    return td


def inbox(name):
    return load_transport(name).peek() or []


# --- inbox_pull --------------------------------------------------------------------

def test_inbox_pull_leaves_unread_pages_unread(team, monkeypatch):
    from agyteam import mcp_base
    monkeypatch.setattr(mcp_base, "OUTPUT_BYTES", 600)

    class ReadsOnePage(ScriptedRunner):
        inbox_pull = True

        def wake(self, agent, message):
            mcp_bus._check_inbox(FileTransport(agent))       # one page, then done
            return "ok"

    for i in range(12):
        load_transport("manager").send("coder", f"item {i:02d} " + "x" * 60)
    sup = Supervisor(AGENTS, ReadsOnePage({}), quiet=True, require_review=False)
    try:
        sup.step()
    finally:
        sup.close()
    left = [m.content[:7] for m in inbox("coder")]
    assert left and left[-1] == "item 11", "unread pages were acknowledged by the supervisor"
    assert "item 00" not in left, "the page the agent read was not marked read"


# --- timeouts and model switches -------------------------------------------------------------

class TimesOut(ScriptedRunner):
    def wake(self, agent, message):
        return f"[error: {agent} timed out after 900s]"


def test_slow_turns_do_not_retire_a_working_conversation(team):
    runner = TimesOut({})
    runner.remember_conversation("coder", "conv-working")
    sup = Supervisor(AGENTS, runner, quiet=True)
    try:
        for _ in range(3):
            if not inbox("coder"):
                load_transport("user").send("coder", "keep going")
            sup._backoff.clear()
            sup.step()
    finally:
        sup.close()
    assert runner.conversation_id("coder") == "conv-working"


class Switches(ScriptedRunner):
    switches_model_in_conversation = True

    def __init__(self):
        super().__init__({})
        self.resets, self.distills = [], []

    def wake(self, agent, message):
        if "memory" in message.lower():
            self.distills.append(agent)
        return "ok"

    def reset(self, agent=None):
        self.resets.append(agent)
        super().reset(agent)


def test_a_runtime_that_switches_in_place_keeps_the_conversation(team):
    runner = Switches()
    sup = Supervisor(AGENTS, runner, quiet=True, require_review=False)
    try:
        sup._apply_model_changes()
        runner.remember_conversation("coder", "conv-live")
        sup.observer.record_turn("coder", "conv-live", 1.0)
        models.set_model(team, "coder", "bigger-model", check=False)
        sup._apply_model_changes()
    finally:
        sup.close()
    assert runner.resets == [] and runner.distills == []
    assert runner.conversation_id("coder") == "conv-live"


def test_the_cli_runner_declares_it_switches_in_place_and_the_sdk_does_not():
    from agyteam.runner_agy import AgyRunner
    assert AgyRunner.switches_model_in_conversation is True
    pytest.importorskip("google.antigravity")
    from agyteam.runner_sdk import SdkRunner
    assert SdkRunner.switches_model_in_conversation is False


# --- one supervisor per team --------------------------------------------------------------------

HOLD = textwrap.dedent("""
    import sys, time, json, os
    sys.path.insert(0, {root!r}); sys.path.insert(0, {evals!r})
    os.environ["AGYTEAM_TEAM_DIR"] = {td!r}
    from agyteam.supervisor import Supervisor
    from fixture_runner import ScriptedRunner
    sup = Supervisor(["manager", "coder", "qa"], ScriptedRunner({{}}), quiet=True)
    assert sup._claim_team()
    sup._heartbeat("daemon")
    print("held", flush=True)
    time.sleep(30)
""")


def test_a_second_supervisor_refuses_the_team_and_leaves_its_heartbeat(team):
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD.format(root=str(ROOT), evals=str(ROOT / "evals"), td=str(team))],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        beat_before = (team / "supervisor.json").read_text()
        sup = Supervisor(AGENTS, ScriptedRunner({}), quiet=True)
        try:
            assert sup.run_until_idle() == 0
            assert "already driving this team" in sup.stopped and str(holder.pid) in sup.stopped
            sup._heartbeat_at = 0
            sup._heartbeat()
        finally:
            sup.close()
        assert (team / "supervisor.json").read_text() == beat_before, \
            "the refused supervisor rewrote or removed the live one's heartbeat"
    finally:
        holder.kill()
        holder.wait()
    sup = Supervisor(AGENTS, ScriptedRunner({}), quiet=True)
    try:
        sup.run_until_idle()
        assert "already driving" not in sup.stopped, "the lock outlived its process"
    finally:
        sup.close()


# --- document-fit reviews ----------------------------------------------------------------------

@pytest.fixture
def report(team, tmp_path):
    doc = tmp_path / "report.md"
    doc.write_text("# Q3 audit\n\nRevenue fell 4% because three invoices were double-counted.\n\n"
                   "Recommendation: dedupe on invoice number before summing.\n")
    load_transport("coder").send("manager", "report is at " + str(doc))    # coder worked
    return doc


def review(doc, verdict="approved", **extra):
    args = {"what": "Q3 audit report", "author": "coder", "verdict": verdict,
            "deliverable": str(doc)}
    args.update(extra)
    return mcp_bus._record_review(FileTransport("qa"), args)


def test_a_document_review_is_recorded_when_every_quote_is_in_it(team, report):
    out = review(report, criteria=[
        {"requirement": "explain the drop", "quote": "three invoices were double-counted"},
        {"requirement": "recommend a fix", "quote": "dedupe on invoice number before summing"}])
    assert out.startswith("[review recorded:") and "2 of 2" in out
    row = json.loads((team / "reviews.jsonl").read_text().splitlines()[-1])
    assert row["proof_file"] is None and len(row["criteria"]) == 2


def test_a_quote_that_is_not_in_the_document_fails(team, report):
    out = review(report, criteria=[
        {"requirement": "say who approved", "quote": "approved by the finance director"}])
    assert "not in report.md" in out and not (team / "reviews.jsonl").exists()


def test_approval_needs_every_criterion_met_and_a_rejection_needs_one_unmet(team, report):
    unmet = {"requirement": "give a dollar figure", "met": False, "why": "only a percentage"}
    met = {"requirement": "explain the drop", "quote": "three invoices were double-counted"}
    assert "unmet criteria" in review(report, criteria=[met, unmet])
    assert "needs at least one criterion" in review(report, "changes_requested", criteria=[met])
    assert review(report, "changes_requested", criteria=[met, unmet]).startswith("[review recorded:")


def test_criteria_must_be_real_and_the_author_rules_still_apply(team, report):
    assert "needs a quote" in review(report, criteria=[{"requirement": "x", "quote": "Revenue"}])
    out = mcp_bus._record_review(FileTransport("coder"), {
        "what": "mine", "author": "coder", "verdict": "approved", "deliverable": str(report),
        "criteria": [{"requirement": "x", "quote": "three invoices were double-counted"}]})
    assert "cannot review your own work" in out


def test_code_reviews_still_need_a_proof(team, report):
    out = mcp_bus._record_review(FileTransport("qa"), {
        "what": "the fix", "author": "coder", "verdict": "approved"})
    assert "proof_file is required" in out and "deliverable and criteria" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
