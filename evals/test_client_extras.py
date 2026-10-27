"""What a client shows beside the conversation: where tokens went per task,
bookmarks on channels and tasks, pictures for agents and the operator, and
files the operator shares."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import actions, channels, tasks  # noqa: E402
from agyteam.record import Record  # noqa: E402

AGENTS = ["tpm", "coder", "qa"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "teams" / "demo" / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [{"name": a} for a in AGENTS]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    return td


def turn(team, agent, ts, tokens, task_ids=()):
    with (team / "events.jsonl").open("a") as f:
        f.write(json.dumps({"ts": ts, "event": "turn", "agent": agent, "input_tokens": tokens,
                            "output_tokens": 0, "trigger": {"task_ids": list(task_ids)}}) + "\n")


def log(team, rec):
    with (team / "tasks.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")


def utc(local: str) -> str:
    """The task log's stamp (UTC ISO) for a local wall-clock time."""
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.mktime(time.strptime(local, "%Y-%m-%d %H:%M:%S"))))


def test_tokens_go_to_the_task_a_turn_was_for(team):
    """Tasks are stamped in UTC ISO and turns in local time, as the real
    record writes them: compared as text they disagreed by the UTC offset
    and no turn was ever counted."""
    log(team, {"id": "task_a", "kind": "created", "ts": utc("2026-10-01 09:00:00"), "title": "A",
               "owner": "coder", "status": "queued"})
    log(team, {"id": "task_a", "kind": "updated", "ts": utc("2026-10-01 10:00:00"), "status": "running"})
    log(team, {"id": "task_a", "kind": "updated", "ts": utc("2026-10-01 12:00:00"), "status": "done"})
    log(team, {"id": "task_b", "kind": "created", "ts": utc("2026-10-01 09:00:00"), "title": "B",
               "owner": "qa", "status": "running"})
    turn(team, "coder", "2026-10-01 09:30:00", 999)                    # before it started: nobody's
    turn(team, "coder", "2026-10-01 11:00:00", 100)                    # working on A: inferred
    turn(team, "qa", "2026-10-01 11:00:00", 50, ["task_a", "task_b"])  # named both: direct, split
    turn(team, "coder", "2026-10-01 13:00:00", 700)                    # after A was done: nobody's
    u = Record(team).task_usage()
    assert u["task_a"]["tokens"] == 125 and u["task_a"]["inferred"] == 1 and u["task_a"]["direct"] == 1
    assert u["task_a"]["agents"] == {"coder": 100, "qa": 25}
    assert u["task_b"]["tokens"] == 25


def test_both_kinds_of_timestamp_are_the_same_instant():
    from agyteam.record import instant
    assert instant(utc("2026-10-27 12:01:18")) == instant("2026-10-27 12:01:18")
    assert instant("garbage") is None


def test_bookmarks_on_a_channel_and_links_on_a_task(team):
    actions.create_channel(team, "ops")
    actions.set_channel_bookmarks(team, "ops", [{"title": "runbook", "url": "go/runbook"},
                                                {"url": "https://example.com"}, {"url": ""}])
    assert channels.load(team)["ops"]["bookmarks"] == [
        {"title": "runbook", "url": "go/runbook"},
        {"title": "https://example.com", "url": "https://example.com"}]
    actions.set_channel_bookmarks(team, "ops", [])
    assert "bookmarks" not in channels.load(team)["ops"]
    t = tasks.create(team, "p", "ship it", created_by="user")
    actions.set_task_links(team, t["id"], [{"title": "PR", "url": "https://example.com/pr/1"}])
    assert tasks.get(team, t["id"])["links"] == [{"title": "PR", "url": "https://example.com/pr/1"}]


def test_pictures_for_agents_and_the_operator(team):
    actions.set_avatar(team, "coder", b"\x89PNG...", ".png")
    actions.set_avatar(team, "user", b"<svg/>", "svg")
    assert Record(team).avatars() == {"coder": "coder.png", "user": "user.svg"}
    actions.set_avatar(team, "coder", b"GIF89a", ".gif")              # replaces, not adds
    assert Record(team).avatars()["coder"] == "coder.gif"
    actions.set_avatar(team, "coder", None)
    assert "coder" not in Record(team).avatars()
    with pytest.raises(actions.OperatorError):
        actions.set_avatar(team, "nobody", b"x", ".png")
    with pytest.raises(actions.OperatorError):
        actions.set_avatar(team, "qa", b"x", ".exe")


def test_an_attachment_is_kept_where_the_team_can_read_it(team):
    out = actions.attach(team, b"\x89PNG", "../../etc/screen shot.png")
    p = Path(out["path"])
    assert p.parent == (team / "attachments").resolve() and p.read_bytes() == b"\x89PNG"
    assert p.name.endswith("screen_shot.png") and ".." not in p.name
    assert Path(actions.attach(team, b"x", "screen shot.png")["path"]) != p
    with pytest.raises(actions.OperatorError):
        actions.attach(team, b"", "empty.png")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
