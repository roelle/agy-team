"""How busy each agent is, so an agent can weigh itself against the team.

Asked for: an engineer with a pile of work could have handed part of it to an
idle tech-docs agent, but nothing told it who was busy. Record.load() times
the turns in the record; list_teammates shows it, with the calling agent's
own line, and says to hand work to whoever has room.
"""
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam.mcp_bus import TOOLS, _list_teammates  # noqa: E402
from agyteam.record import Record  # noqa: E402
from agyteam.transport_file import FileTransport  # noqa: E402

NOW = time.time()


def ts(ago_s):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - ago_s))


@pytest.fixture(autouse=True)
def now():
    """list_teammates reads the clock: the record is written relative to it."""
    global NOW
    NOW = time.time()


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "eng", "role": "builds"},
        {"name": "docs", "role": "writes docs"},
        {"name": "qa", "role": "reviews"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    events = [
        # eng: a 30-minute turn that ended 10 minutes ago
        {"ts": ts(600), "event": "turn", "agent": "eng", "duration_s": 1800},
        # eng: a turn two days ago, outside both windows
        {"ts": ts(2 * 86400), "event": "turn", "agent": "eng", "duration_s": 600},
        {"ts": ts(60), "event": "tool_call", "agent": "docs", "duration_s": 50},
    ]
    (td / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    # qa: a turn in flight for 15 minutes
    (td / "inflight.json").write_text(json.dumps({"qa": {"ts": ts(900)}}))

    def mail(to, ago, **extra):
        with (td / "inbox" / f"{to}.jsonl").open("a") as f:
            f.write(json.dumps({"ts": ts(ago), "from": "user", "to": to,
                                "content": "x", **extra}) + "\n")
    mail("docs", 30)
    mail("docs", 20)
    mail("docs", 10, quiet=True)             # a channel post that wakes nobody
    mail("qa", 1000)                         # what its turn is working on
    mail("qa", 100)                          # arrived since
    return td


def test_busy_shares_and_waiting_mail(team):
    load = Record(team).load(now=NOW)
    assert load["eng"]["busy"] == {"hour": 0.5, "day": round(1800 / 86400, 3)}
    assert load["eng"]["turns"] == 2 and load["eng"]["busy_s"] == 2400
    assert load["eng"]["working"] is False
    assert load["docs"]["busy"] == {"hour": 0.0, "day": 0.0}, "a tool call is not a turn"
    assert load["docs"]["waiting"] == 2, "a quiet copy does not count"
    assert load["qa"]["working"] and load["qa"]["busy"]["hour"] == 0.25
    assert load["qa"]["waiting"] == 1, "mail its turn is on is not waiting"


def test_list_teammates_shows_load_and_where_you_stand(team):
    t = FileTransport("eng")
    try:
        out = _list_teammates(t)
    finally:
        t.close()
    assert "- docs: writes docs -- idle, 2 waiting; busy 0% of the last hour, 0% of the day" in out
    assert "- qa: reviews -- working, 1 waiting; busy 25% of the last hour" in out
    assert "- you (eng) -- idle; busy 50% of the last hour, 2% of the day" in out
    assert out.splitlines()[-1] == "Over the last hour you were busier than 2 of your 2 teammates."
    assert out.index("- user:") < out.index("- you (eng)")


def test_the_tool_says_to_spread_the_work_both_ways():
    desc = next(t for t in TOOLS if t["name"] == "list_teammates")["description"]
    assert "among the busiest" in desc and "hand that part" in desc
    assert "among the least busy" in desc and "offer the busiest your help" in desc


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
