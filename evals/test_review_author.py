"""The review gate must check who did the work, not just who says they did.

`record_review` refused `author == reviewer` and nothing else, so the gate was
defeated by typing a different name. Measured on a real run: a manager
recorded author="coder" for an episode in which coder was never woken and made
zero tool calls, the supervisor's own warning fired forty seconds earlier, and
the literal string "unknown" passed the same way.

The comment in the code was one step short of the remedy — "misstating who did
the work is visible to everyone on the bus". Visible is not checked. These
tests are the checking.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam.mcp_bus import _record_review  # noqa: E402
from agyteam.transport_file import FileTransport  # noqa: E402

TS = "2026-09-14 12:00:00"


@pytest.fixture
def team(tmp_path, monkeypatch):
    """A team mid-episode: manager has worked, coder and qa have not."""
    d = tmp_path / "team"
    d.mkdir()
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(d))
    monkeypatch.delenv("AGYTEAM_AUDIT_LOG", raising=False)
    (d / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "gates"},
        {"name": "coder", "role": "implements"},
        {"name": "qa", "role": "reviews"}]}))
    (d / "bus.jsonl").write_text(json.dumps(
        {"ts": TS, "from": "manager", "to": "user", "content": "audit done"}) + "\n")
    (d / "events.jsonl").write_text(json.dumps(
        {"ts": TS, "event": "turn", "agent": "manager", "conversation": "c1"}) + "\n")
    (d / "proof.py").write_text("def test_ok():\n    assert 1 == 1\n")
    return d


def review(team, **over):
    args = {"what": "the audit", "author": "coder", "verdict": "approved",
            "proof_file": str(team / "proof.py")}
    args.update(over)
    return _record_review(FileTransport("manager"), args)


def activate(team, agent, event="turn"):
    with (team / "events.jsonl").open("a") as f:
        f.write(json.dumps({"ts": TS, "event": event, "agent": agent,
                            "conversation": "c2"}) + "\n")


def recorded(team):
    path = team / "reviews.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


# --- the three ways a name can be wrong -------------------------------------

def test_author_who_never_ran_is_refused(team):
    """The measured forgery, replayed."""
    out = review(team, author="coder")
    assert out.startswith("[error:") and "no recorded activity" in out
    assert recorded(team) == [], "a refused review must not be written"


def test_placeholder_author_is_refused(team):
    """"unknown" passed as a literal. It is not on the roster."""
    out = review(team, author="unknown")
    assert out.startswith("[error:") and "not a teammate" in out


def test_reviewing_your_own_work_is_still_refused(team):
    out = review(team, author="manager")
    assert out.startswith("[error:") and "your own work" in out


def test_missing_author_is_refused(team):
    assert review(team, author="").startswith("[error:")
    assert review(team, author="   ").startswith("[error:")


# --- and the way it can be right --------------------------------------------

def test_author_with_a_recorded_turn_is_accepted(team):
    activate(team, "coder")
    out = review(team, author="coder")
    assert out.startswith("[review recorded:"), out
    rows = recorded(team)
    assert len(rows) == 1
    assert rows[0]["author"] == "coder" and rows[0]["author_verified"] is True
    assert rows[0]["kind"] == "work"


def test_a_bus_message_is_enough_on_its_own(team):
    """An agent that published but was driven outside the supervisor."""
    with (team / "bus.jsonl").open("a") as f:
        f.write(json.dumps({"ts": TS, "from": "coder", "to": "manager",
                            "content": "done"}) + "\n")
    assert review(team, author="coder").startswith("[review recorded:")


def test_a_failed_turn_still_counts_as_activity(team):
    """Work that errored is work someone can review. Only silence is not."""
    activate(team, "coder", event="failure")
    assert review(team, author="coder").startswith("[review recorded:")


# --- the window -------------------------------------------------------------

def test_activity_before_the_last_episode_does_not_count(team):
    """Otherwise a long-lived team answers "has coder ever worked?" yes forever."""
    activate(team, "coder")                      # at TS
    with (team / "events.jsonl").open("a") as f:
        f.write(json.dumps({"ts": "2026-09-14 13:00:00", "event": "episode",
                            "turns": 4, "stopped_reason": "answered"}) + "\n")
    out = review(team, author="coder")
    assert out.startswith("[error:") and "since 2026-09-14 13:00:00" in out


# --- cannot look is not the same as nothing to find -------------------------

def test_unverifiable_author_is_recorded_as_unverified_not_refused(tmp_path,
                                                                   monkeypatch):
    """No bus, no events, no audit: the gate says so rather than guessing.

    The project's own rule, applied to its newest check. Refusing here would
    make the gate unusable on a runner that records nothing; passing silently
    would make an unchecked review indistinguishable from a checked one.
    """
    d = tmp_path / "bare"
    d.mkdir()
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(d))
    monkeypatch.delenv("AGYTEAM_AUDIT_LOG", raising=False)
    (d / "roster.json").write_text(json.dumps({"agents": [
        {"name": "manager", "role": "m"}, {"name": "coder", "role": "c"}]}))
    (d / "proof.py").write_text("def test_ok():\n    assert 1 == 1\n")
    out = review(d, author="coder")
    assert out.startswith("[review recorded:")
    assert "could not be verified" in out
    assert recorded(d)[0]["author_verified"] is False


def test_audit_log_counts_as_a_channel(team, monkeypatch, tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text(json.dumps(
        {"ts": TS, "agent": "coder", "tool": "run_command"}) + "\n")
    monkeypatch.setenv("AGYTEAM_AUDIT_LOG", str(audit))
    assert review(team, author="coder").startswith("[review recorded:")


# --- governance records must not satisfy a work gate ------------------------

def test_norm_adoption_is_not_a_work_review(tmp_path):
    """The retro's own record used to land as author: null and count.

    `_approved_reviews_count` is what the supervisor consults before letting
    an answer reach the user. A team that held a retrospective would arrive
    with an approved review it had never earned.
    """
    from agyteam.supervisor import Supervisor, record_review

    d = tmp_path / "team"
    d.mkdir()
    entry = record_review(d, reviewer="tpm", what="NORMS.md: no mocks",
                          verdict="approved", cases_tried=["consensus"])
    assert entry["kind"] == "norm"

    sup = Supervisor.__new__(Supervisor)
    sup.reviews_path = d / "reviews.jsonl"
    assert sup._approved_reviews_count() == 0

    record_review(d, reviewer="qa", what="the audit", verdict="approved",
                  cases_tried=["ran it"], kind="work", author="coder")
    assert sup._approved_reviews_count() == 1


def test_rows_written_before_the_kind_field_still_count_as_work(tmp_path):
    """They were work reviews; re-reading history as governance would be a lie."""
    from agyteam.supervisor import Supervisor

    d = tmp_path / "team"
    d.mkdir()
    (d / "reviews.jsonl").write_text(json.dumps(
        {"ts": TS, "reviewer": "qa", "author": "coder",
         "verdict": "approved", "what": "old"}) + "\n")
    sup = Supervisor.__new__(Supervisor)
    sup.reviews_path = d / "reviews.jsonl"
    assert sup._approved_reviews_count() == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
