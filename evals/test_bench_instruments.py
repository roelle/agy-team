"""The instruments must be at least as honest as the teams they measure.

Every finding here was produced by running the bench eight times back to back
and reading what it said against what had happened. None of them needed a
model or a network; all of them were reachable from a clean clone.

The pattern they share: the script had already computed the fact that would
have stopped it, and read something else instead.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name):
    """bench/ is scripts, not a package."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "bench" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


conv = _load("convergence")


def write(path: Path, *records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def bus(ts, frm, to, content=""):
    return {"ts": ts, "from": frm, "to": to, "content": content}


def review(ts, reviewer, author, verdict="approved", **extra):
    return {"ts": ts, "kind": "work", "reviewer": reviewer, "author": author,
            "verdict": verdict, **extra}


# --- U5: the runner is not hardcoded ----------------------------------------

def test_runner_is_taken_from_the_environment(monkeypatch):
    """Hardcoded in the same commit that made runners pluggable."""
    monkeypatch.delenv("AGYTEAM_RUNNER", raising=False)
    assert conv.runner_spec() == conv.DEFAULT_RUNNER
    monkeypatch.setenv("AGYTEAM_RUNNER", "some.module:OtherRunner")
    assert conv.runner_spec() == "some.module:OtherRunner"


# --- U6: staging is not containment -----------------------------------------

def test_refuses_to_stage_a_key_behind_a_disclaimed_guarantee(tmp_path,
                                                              monkeypatch):
    """"the keys stay unreachable" is exactly what the flag says does not work.

    The staging step points each agent's `workspaces` at the fixtures. On a
    runner declaring supports_containment = False, roster workspaces do
    nothing, the keys sit readable in the repo, and the run produces a perfect
    score that means nothing.
    """
    monkeypatch.setenv("AGYTEAM_RUNNER", "agyteam.runner_agy:AgyRunner")
    with pytest.raises(SystemExit) as e:
        conv.check_containment(tmp_path)
    msg = str(e.value)
    assert "supports_containment = False" in msg
    assert "not a benchmark" in msg


def test_refuses_when_containment_cannot_be_determined(tmp_path, monkeypatch):
    monkeypatch.setenv("AGYTEAM_RUNNER", "no.such.module:Nope")
    with pytest.raises(SystemExit) as e:
        conv.check_containment(tmp_path)
    assert "could not be inspected" in str(e.value)


def test_allows_a_runner_that_contains(tmp_path, monkeypatch):
    pytest.importorskip("google.antigravity")
    monkeypatch.setenv("AGYTEAM_RUNNER", "agyteam.runner_sdk:SdkRunner")
    conv.check_containment(tmp_path)        # must not raise


# --- U8: the cost the comparison tells you to judge on ----------------------

def test_episode_summary_reports_turns_and_seconds(tmp_path):
    """"judge on process and cost" was printed beside no cost at all."""
    write(tmp_path / "events.jsonl",
          {"ts": "2026-09-14 10:00:00", "event": "turn", "agent": "coder",
           "duration_s": 12.5},
          {"ts": "2026-09-14 10:00:30", "event": "turn", "agent": "qa",
           "duration_s": 7.5},
          {"ts": "2026-09-14 10:01:00", "event": "episode", "turns": 8,
           "stopped_reason": "answered", "duration_s": 351.0})
    got = conv.episode_summary(tmp_path, "2026-09-14 00:00")
    assert got == {"turns": 8, "model_seconds": 20.0, "episode_seconds": 351.0}


def test_episode_summary_falls_back_to_counting_turns(tmp_path):
    """A run cut short records no episode; it still took turns."""
    write(tmp_path / "events.jsonl",
          {"ts": "2026-09-14 10:00:00", "event": "turn", "agent": "coder",
           "duration_s": 3.0})
    assert conv.episode_summary(tmp_path, "2026-09-14 00:00")["turns"] == 1


# --- U12: one boolean carrying two questions, answering neither -------------

def test_gate_jumping_and_final_review_are_separate(tmp_path):
    """Measured False 8/8 in BOTH arms, so it discriminated nothing.

    Arm one: the manager answers early, the supervisor bounces it, the team
    deliberates, qa approves, the manager re-answers. That team recovered.
    """
    write(tmp_path / "bus.jsonl",
          bus("2026-09-14 10:00:00", "manager", "user", "here is the audit"),
          bus("2026-09-14 10:05:00", "manager", "user", "reviewed; here it is"))
    write(tmp_path / "reviews.jsonl",
          review("2026-09-14 10:03:00", "qa", "coder"))
    m = conv.process_metrics(tmp_path, "2026-09-14 00:00")
    assert m["answered_before_any_review"] is True, "it did jump the gate"
    assert m["final_answer_was_reviewed"] is True, "and it did recover"
    assert m["answers_to_user"] == 2


def test_a_team_that_never_recovers_scores_differently(tmp_path):
    """Arm two: answered once, ungated, and stopped. Used to look identical."""
    write(tmp_path / "bus.jsonl",
          bus("2026-09-14 10:00:00", "manager", "user", "here is the audit"))
    write(tmp_path / "reviews.jsonl")
    m = conv.process_metrics(tmp_path, "2026-09-14 00:00")
    assert m["answered_before_any_review"] is True
    assert m["final_answer_was_reviewed"] is False


def test_a_team_that_reviews_before_answering_scores_clean(tmp_path):
    write(tmp_path / "bus.jsonl",
          bus("2026-09-14 10:05:00", "manager", "user", "reviewed; here it is"))
    write(tmp_path / "reviews.jsonl",
          review("2026-09-14 10:03:00", "qa", "coder"))
    m = conv.process_metrics(tmp_path, "2026-09-14 00:00")
    assert m["answered_before_any_review"] is False
    assert m["final_answer_was_reviewed"] is True


def test_norm_records_do_not_count_as_work_reviews(tmp_path):
    write(tmp_path / "bus.jsonl",
          bus("2026-09-14 10:05:00", "manager", "user", "here it is"))
    write(tmp_path / "reviews.jsonl",
          {"ts": "2026-09-14 10:00:00", "kind": "norm", "reviewer": "tpm",
           "author": None, "verdict": "approved"})
    m = conv.process_metrics(tmp_path, "2026-09-14 00:00")
    assert m["final_answer_was_reviewed"] is False
    assert m["answered_before_any_review"] is True


def test_unverified_authors_are_counted_separately(tmp_path):
    write(tmp_path / "bus.jsonl",
          bus("2026-09-14 10:05:00", "manager", "user", "here it is"))
    write(tmp_path / "reviews.jsonl",
          review("2026-09-14 10:00:00", "qa", "coder", author_verified=False))
    m = conv.process_metrics(tmp_path, "2026-09-14 00:00")
    assert m["unverified_author_approvals"] == 1
    assert m["self_or_authorless_approvals"] == 0


# --- U7: a run that did not happen ------------------------------------------

def test_an_idle_episode_is_visible_in_the_metrics(tmp_path):
    """The supervisor exits 0 on "team went idle", so returncode says nothing.

    Eight such runs completed in sixteen minutes and produced a full set of
    scores. Both facts needed to stop it were already being computed.
    """
    write(tmp_path / "events.jsonl",
          {"ts": "2026-09-14 10:00:00", "event": "episode", "turns": 1,
           "stopped_reason": "team went idle", "duration_s": 4.0})
    write(tmp_path / "bus.jsonl")
    m = conv.process_metrics(tmp_path, "2026-09-14 00:00")
    e = conv.episode_summary(tmp_path, "2026-09-14 00:00")
    assert e["turns"] <= 1 and not m["answered_user"]


# --- U14: two runs sharing one global audit log -----------------------------

def test_audit_slices_do_not_bleed_between_runs(tmp_path):
    """--since is minute-granular; back-to-back runs shared a window.

    Measured over 8 reps: every rep's window contained foreign stage-directory
    references, one of them 31 own rows against 3 borrowed. Harmless there
    only because the affected checks were saturated; for any A/B use of the
    bench it is a cross-arm leak in the scoring path.
    """
    grade = _load("grade")
    audit = tmp_path / "audit.jsonl"
    same_minute = "2026-09-14 10:00:0"
    write(audit,
          *[{"ts": f"{same_minute}{i}", "agent": "coder", "tool": "run_command",
             "args": {"CommandLine": f"pytest run_a_{i}"}} for i in range(3)],
          *[{"ts": f"{same_minute}{i+3}", "agent": "coder",
             "tool": "run_command",
             "args": {"CommandLine": f"pytest run_b_{i}"}} for i in range(3)])

    everything = grade.rows(audit, "2026-09-14 00:00")
    assert len(everything) == 6, "the contaminated view"

    run_a = grade.rows(audit, "2026-09-14 00:00", 0, 3)
    run_b = grade.rows(audit, "2026-09-14 00:00", 3, 6)
    assert len(run_a) == 3 and len(run_b) == 3
    assert all("run_a" in r["args"]["CommandLine"] for r in run_a)
    assert all("run_b" in r["args"]["CommandLine"] for r in run_b)


def test_line_count_of_a_missing_log_is_zero(tmp_path):
    assert conv.line_count(tmp_path / "nope.jsonl") == 0
    assert _load("grade").line_count(tmp_path / "nope.jsonl") == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
