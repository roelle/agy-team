"""What a chat client and a board need from the team, from the operator's first week.

- Channels pile up: archive one (read-only, out of every list, reversible),
  or delete it (archived, and hidden from clients; the messages stay in
  bus.jsonl, which is the record).
- A task and the channel it is discussed in know each other: the task names
  the channel, and status changes are noted there quietly.
- A board needs a place for parked work (icebox) and for work waiting on its
  review (review), an order within a column (rank), and an operator who can
  move any card -- with the owner told.
- People are called what they are called: an agent's alias ("Ada" for
  "at-Ada", or any nickname) and the operator's own handles ("@Matt").
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import actions, channels, mcp_bus, persona, roster, tasks  # noqa: E402
from agyteam.transport_file import FileTransport  # noqa: E402


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({
        "operator": {"name": "Matt", "handles": ["roellem"]},
        "agents": [{"name": "manager", "role": "coordinates"},
                   {"name": "at-Ada", "role": "implements", "alias": "Ada"},
                   {"name": "qa", "role": "reviews", "alias": "Grace"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    monkeypatch.delenv("AGYTEAM_BUS_TRANSPORT", raising=False)
    channels.create(td, "billing", ["manager", "at-Ada"], "money")
    return td


def bus(td):
    p = td / "bus.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def inbox(td, who):
    p = td / "inbox" / f"{who}.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# --- channels ---------------------------------------------------------------------

def test_an_archived_channel_takes_no_posts_and_leaves_every_list(team):
    actions.archive_channel(team, "billing")
    with pytest.raises(channels.ChannelError, match="archived"):
        channels.post(FileTransport("manager"), team, "billing", "hello")
    with pytest.raises(actions.OperatorError, match="archived"):
        actions.post(team, "billing", "hello", author="matt")
    assert "billing" not in channels.of(team, "manager")
    assert "billing" not in mcp_bus._list_channels(FileTransport("manager"))
    assert channels.load(team)["billing"]["archived"]
    actions.restore_channel(team, "billing")
    assert channels.post(FileTransport("manager"), team, "billing", "back")["id"]


def test_deleting_hides_a_channel_but_keeps_the_record(team):
    actions.post(team, "billing", "the invoice total is wrong", author="matt")
    actions.delete_channel(team, "billing")
    from agyteam.record import Record
    rec = Record(team)
    assert rec.channels()["billing"]["deleted"]
    assert rec.search("invoice") == [], "a deleted channel's history surfaced in search"
    assert any(m.get("channel") == "billing" for m in bus(team)), "the record lost it"
    with pytest.raises(channels.ChannelError, match="restore it"):
        channels.create(team, "billing", ["manager"])
    actions.restore_channel(team, "billing")
    assert rec.search("invoice")


# --- tasks and their channel ------------------------------------------------------

def test_a_task_names_its_channel_and_its_changes_are_noted_there_quietly(team):
    t = tasks.create(team, "billing", "fix allocate", owner="at-Ada",
                     created_by="manager", channel="#billing")
    assert t["channel"] == "billing"
    tasks.update(team, t["id"], "at-Ada", status="running")
    notes = [m for m in bus(team) if m.get("channel") == "billing" and m.get("task_id") == t["id"]]
    assert [m["kind"] for m in notes] == ["status", "status"]
    assert "queued → running" in notes[-1]["content"]
    assert all(m.get("quiet") or m["kind"] == "status" for m in inbox(team, "manager"))


def test_a_task_cannot_name_a_channel_that_is_not_open(team):
    with pytest.raises(tasks.TaskError, match="no open channel #sims"):
        tasks.create(team, "x", "y", channel="sims")
    actions.archive_channel(team, "billing")
    with pytest.raises(tasks.TaskError, match="#billing"):
        tasks.create(team, "x", "y", channel="billing")


def test_unlinking_a_task(team):
    t = tasks.create(team, "billing", "fix", owner="at-Ada", channel="billing")
    assert tasks.update(team, t["id"], "at-Ada", channel="")["channel"] is None


# --- the board's statuses ---------------------------------------------------------

def test_iced_work_is_not_claimed_reminded_or_taken_back_out_by_an_agent(team):
    t = tasks.create(team, "p", "someday", status="icebox", check_after="1m")
    with pytest.raises(tasks.TaskError, match="icebox"):
        tasks.claim(team, t["id"], "at-Ada")
    assert tasks.due(team, now="2999-01-01T00:00:00Z") == []
    owned = tasks.create(team, "p", "parked mine", owner="at-Ada")
    tasks.update(team, owned["id"], "at-Ada", status="parked")
    with pytest.raises(tasks.TaskError, match="only they take it back out"):
        tasks.update(team, owned["id"], "at-Ada", status="running")
    assert actions.update_task(team, owned["id"], status="queued")["status"] == "queued"


def test_review_is_a_status_and_closing_still_needs_the_review(team):
    t = tasks.create(team, "p", "audit", owner="at-Ada", requires_review=True)
    assert tasks.update(team, t["id"], "at-Ada", status="awaiting_review")["status"] == "review"
    with pytest.raises(tasks.TaskError):
        tasks.complete(team, t["id"], "at-Ada", "done")


def test_the_operator_moves_any_card_and_the_owner_is_told(team):
    t = tasks.create(team, "p", "build", owner="at-Ada")
    tasks.update(team, t["id"], "at-Ada", status="running")
    with pytest.raises(tasks.TaskError, match="owned by 'at-Ada'"):
        tasks.update(team, t["id"], "qa", status="done")
    actions.update_task(team, t["id"], rank=2.5)
    actions.update_task(team, t["id"], status="icebox")
    told = [m for m in inbox(team, "at-Ada") if m.get("task_id") == t["id"]]
    assert told[-1].get("kind", "work") == "work" and "Stop work on it" in told[-1]["content"], \
        "iced while they were on it must wake them, or they keep going"
    actions.update_task(team, t["id"], owner="qa", status="queued")
    assert inbox(team, "qa")[-1].get("kind", "work") == "work"
    assert inbox(team, "at-Ada")[-1]["kind"] == "fyi"
    assert tasks.get(team, t["id"])["rank"] == 2.5


def test_the_operator_can_start_a_task_on_ice_and_nobody_is_woken(team):
    t = actions.create_task(team, "p", "maybe later", owner="at-Ada", status="icebox")
    assert t["status"] == "icebox" and inbox(team, "at-Ada") == []


# --- what people are called -------------------------------------------------------

def test_an_alias_and_the_operators_handles_resolve(team):
    doc = roster.load(team / "roster.json")
    names = [a["name"] for a in doc["agents"]]
    al = roster.aliases(doc)
    assert roster.resolve_name("Grace", names, al) == "qa"
    assert roster.resolve_name("ada", names, al) == "at-Ada"
    assert roster.resolve_name("Matt", names, al) == "user"
    assert roster.resolve_name("roellem", names, al) == "user"


def test_send_to_an_alias_or_to_the_operator_by_name(team):
    t = FileTransport("manager")
    mcp_bus._send(t, {"to": "Grace", "content": "please review"})
    assert inbox(team, "qa")[-1]["content"] == "please review"
    out = mcp_bus._send(t, {"to": "Matt", "content": "it is done"})
    assert "user" in out and inbox(team, "user")[-1]["content"] == "it is done"


def test_at_matt_in_a_channel_is_a_mention_of_the_user_and_wakes_nobody(team):
    out = channels.post(FileTransport("at-Ada"), team, "billing", "@Matt can you approve this?")
    assert out["mentions_user"] and out["woke"] == []
    assert bus(team)[-1]["mentions"] == ["user"]
    assert inbox(team, "user") == [], "the user's inbox is the episode's finish line"


def test_the_brief_says_who_the_operator_is_and_what_agents_are_called(team):
    doc = roster.load(team / "roster.json")
    text = persona.brief("manager", doc["agents"], team_dir=team)
    assert "the human, Matt" in text and "@roellem" in text
    assert "at-Ada (also 'Ada')" in text
    me = persona.brief("at-Ada", doc["agents"], team_dir=team)
    assert "the team calls you 'Ada'" in me


@pytest.mark.parametrize("doc,match", [
    ({"agents": [{"name": "a", "alias": "b"}, {"name": "b"}]}, "would mean both"),
    ({"operator": {"handles": ["qa"]}, "agents": [{"name": "qa"}]}, "would mean both"),
    ({"agents": [{"name": "a", "alias": "here"}]}, "reserved"),
    ({"agents": [{"name": "a", "alias": "has space"}]}, "must be a handle"),
])
def test_a_handle_means_one_thing(doc, match):
    with pytest.raises(roster.RosterError, match=match):
        roster.normalize(doc)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
