"""The status report must not call work reviewed that nothing reviewed.

Two ways it did. For an episode recorded without a `reviewed` flag, any
approved review anywhere in the history marked it reviewed -- including one
from before the episode began, and one from a later episode. And a retro's
norm adoption, which is written to reviews.jsonl as kind "norm" with no
author, counted as an approved review: governance laundered into verification.
The report feeds the retrospective, so both reached the team as fact.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam.supervisor import format_report, generate_report  # noqa: E402


def write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def team(tmp_path, events, reviews):
    td = tmp_path / "team"
    td.mkdir()
    (td / "roster.json").write_text(json.dumps({"agents": [{"name": "coder"}]}))
    write(td / "events.jsonl", events)
    write(td / "reviews.jsonl", reviews)
    return td


def ep(ts):
    return {"ts": ts, "event": "episode", "turns": 3, "stopped_reason": "idle"}


def rev(ts, kind="work", verdict="approved"):
    return {"ts": ts, "kind": kind, "verdict": verdict, "reviewer": "qa",
            "author": "coder", "what": "x"}


def test_a_review_counts_only_for_the_episode_it_happened_in(tmp_path):
    td = team(tmp_path,
              [ep("2026-01-01 10:00:00"), ep("2026-01-01 11:00:00"),
               ep("2026-01-01 12:00:00")],
              [rev("2026-01-01 10:30:00")])        # during episode 2 only
    reviewed = [e["reviewed"] for e in generate_report(td)["episodes"]]
    assert reviewed == [False, True, False]


def test_a_norm_adoption_is_not_a_review(tmp_path):
    td = team(tmp_path, [ep("2026-01-01 10:00:00")],
              [rev("2026-01-01 09:30:00", kind="norm")])
    rep = generate_report(td)
    assert rep["episodes"][0]["reviewed"] is False
    text = format_report(rep)
    assert "Work reviews: 0 (0 approved" in text and "norm adoptions: 1" in text


def test_an_episode_flag_is_taken_as_recorded(tmp_path):
    flagged = dict(ep("2026-01-01 10:00:00"), reviewed=True)
    td = team(tmp_path, [flagged], [])
    assert generate_report(td)["episodes"][0]["reviewed"] is True


def test_the_cost_summary_does_not_print_unpriced_work_as_free(tmp_path):
    """`--cost` printed $0.0000 for 1.4 million tokens on a model the pricing
    table does not list."""
    from agyteam.observer import format_summary
    from agyteam.observer_file import FileObserver
    td = tmp_path / "team"
    td.mkdir()
    obs = FileObserver({"team_dir": str(td)})
    obs.record_turn("coder", "c1", 10.0, input_tokens=1_000_000, output_tokens=50_000,
                    model="some-model-nobody-priced")
    text = format_summary(obs.summary())
    assert "$0.0000" not in text
    assert "unknown" in text and "some-model-nobody-priced" in text

    obs.record_turn("qa", "c2", 5.0, input_tokens=1_000_000, output_tokens=0,
                    model="gemini-3.8-flash")
    text = format_summary(obs.summary())
    assert "+1?" in text, text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
