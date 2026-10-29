"""Channels, threads and mentions; the record API; operator actions.

The rule being built: a channel post wakes only the agents it mentions; a
member it does not mention gets a quiet copy that waits in its inbox, and is
read with everything else unread the next time that agent is woken.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import actions, channels, mcp_bus  # noqa: E402
from agyteam.record import Feed, Record  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from agyteam.transport_file import FileTransport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["tpm", "coder", "qa", "syseng"]


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": a, "role": "works"} for a in AGENTS]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    monkeypatch.delenv("AGYTEAM_BUS_TRANSPORT", raising=False)
    channels.create(td, "billing", ["coder", "qa"], topic="the invoices")
    return td


def inbox(name):
    return load_transport(name).peek() or []


class Recording(ScriptedRunner):
    def __init__(self):
        super().__init__({})
        self.prompts = {}

    def wake(self, agent, message):
        self.woken.append(agent)
        self.prompts[agent] = message
        return "ok"


def step(runner):
    sup = Supervisor(AGENTS, runner, quiet=True, require_review=False)
    try:
        return sup.step()
    finally:
        sup.close()


# --- posting -----------------------------------------------------------------------

def test_a_team_with_no_channels_file_has_general_with_everyone(tmp_path):
    td = tmp_path / "t"
    td.mkdir()
    (td / "roster.json").write_text(json.dumps({"agents": [{"name": "a"}, {"name": "b"}]}))
    assert channels.load(td)["general"]["members"] == ["a", "b"]


def test_an_unmentioned_post_wakes_nobody_but_reaches_every_member(team):
    actions.post(team, "billing", "the March invoices look off", author="matt")
    for name in ("coder", "qa"):
        [m] = inbox(name)
        assert m.channel == "billing" and m.quiet and not m.wakes and m.author == "matt"
    assert inbox("tpm") == []
    runner = Recording()
    assert step(runner) == 0 and runner.woken == []


def test_a_mention_wakes_only_who_it_names(team):
    actions.post(team, "billing", "@coder can you check allocate?", author="matt")
    assert [m.quiet for m in inbox("coder")] == [False]
    assert [m.quiet for m in inbox("qa")] == [True]
    runner = Recording()
    step(runner)
    assert runner.woken == ["coder"]


def test_a_woken_agent_reads_everything_unread_not_just_the_mention(team):
    actions.post(team, "billing", "fyi: totals were wrong in March", author="matt")
    channels.create(team, "sims", ["coder", "syseng"])
    actions.post(team, "sims", "run 42 finished", author="matt")
    actions.post(team, "billing", "@coder please look now", author="matt")
    runner = Recording()
    step(runner)
    prompt = runner.prompts["coder"]
    assert "totals were wrong" in prompt and "run 42 finished" in prompt
    assert "please look now" in prompt and "in #billing" in prompt and "in #sims" in prompt
    assert inbox("coder") == []                    # all of it was delivered and read


# --- members every post wakes ----------------------------------------------------

def test_a_channel_can_wake_a_member_on_every_post(team):
    actions.set_channel_wake(team, "billing", ["qa"])
    r = actions.post(team, "billing", "the March invoices look off", author="matt")
    assert r["woke"] == ["qa"]
    assert [m.wakes for m in inbox("qa")] == [True]
    assert [m.wakes for m in inbox("coder")] == [False]
    runner = Recording()
    step(runner)
    assert runner.woken == ["qa"]
    entry = json.loads((team / "bus.jsonl").read_text().splitlines()[-1])
    assert "mentions" not in entry, "being woken by every post is not a mention"


def test_an_agents_post_wakes_the_listener_but_never_its_author(team):
    actions.set_channel_wake(team, "billing", ["coder", "qa"])
    coder = FileTransport("coder")
    try:
        r = channels.post(coder, team, "billing", "allocate is fixed, please document it")
    finally:
        coder.close()
    assert r["woke"] == ["qa"] and inbox("coder") == []
    assert [m.wakes for m in inbox("qa")] == [True]


def test_quiet_kinds_wake_no_listener(team):
    actions.set_channel_wake(team, "billing", ["qa"])
    r = actions.post(team, "billing", "fyi: invoices run tonight", kind="fyi", author="matt")
    assert r["woke"] == [] and [m.wakes for m in inbox("qa")] == [False]


def test_only_members_can_be_woken_by_every_post(team):
    with pytest.raises(actions.OperatorError, match="not in #billing: tpm"):
        actions.set_channel_wake(team, "billing", ["tpm"])
    actions.set_channel_wake(team, "billing", ["qa"])
    actions.set_members(team, "billing", remove=["qa"])
    assert "wake" not in channels.load(team)["billing"]
    actions.set_channel_wake(team, "billing", ["coder"])
    actions.set_channel_wake(team, "billing", [])
    assert "wake" not in channels.load(team)["billing"]


def test_list_channels_says_who_every_post_wakes(team):
    actions.set_channel_wake(team, "billing", ["qa"])
    for me, says in (("coder", "every post wakes qa"), ("qa", "every post wakes you")):
        t = FileTransport(me)
        try:
            assert says in mcp_bus._list_channels(t)
        finally:
            t.close()


def test_a_transport_without_the_wake_list_still_wakes_listeners(team):
    class Old(FileTransport):
        def post(self, channel, members, content, kind="work", mentioned=None,
                 task_id=None, thread=None, author=None):
            return super().post(channel, members, content, kind=kind, mentioned=mentioned,
                                task_id=task_id, thread=thread, author=author)
    actions.set_channel_wake(team, "billing", ["qa"])
    t = Old("user")
    try:
        channels.post(t, team, "billing", "anything", author="matt")
    finally:
        t.close()
    assert [m.wakes for m in inbox("qa")] == [True]


def test_here_wakes_the_channel_and_all_wakes_the_team(team):
    actions.post(team, "billing", "@here standup in 5", author="matt")
    assert {n for n in AGENTS if any(not m.quiet for m in inbox(n))} == {"coder", "qa"}
    for n in AGENTS:
        load_transport(n).acknowledge(inbox(n))
    actions.post(team, "billing", "@all freeze merges", author="matt")
    assert all(any(not m.quiet for m in inbox(n)) for n in AGENTS), \
        "@all must reach agents outside the channel too"


def test_a_post_is_logged_once_with_its_mentions(team):
    out = actions.post(team, "billing", "@qa review please", author="matt")
    rows = [json.loads(l) for l in (team / "bus.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["to"] == "#billing" and rows[0]["mentions"] == ["qa"]
    assert rows[0]["id"] == out["id"] and out["woke"] == ["qa"]


def test_an_agent_posts_only_where_it_is_a_member(team):
    out = mcp_bus._post_to_channel(FileTransport("tpm"), {"channel": "billing", "content": "hi"})
    assert out.startswith("[refused:") and "not in #billing" in out
    out = mcp_bus._post_to_channel(FileTransport("qa"), {"channel": "billing",
                                                         "content": "@coder found it"})
    assert "wakes coder" in out
    assert [m.quiet for m in inbox("coder")] == [False]
    assert inbox("qa") == [], "a poster is not sent its own post"


def test_list_channels_shows_only_mine(team):
    out = mcp_bus._list_channels(FileTransport("coder"))
    assert "#billing" in out and "the invoices" in out
    tpm = mcp_bus._list_channels(FileTransport("tpm"))
    assert "#general" in tpm and "#billing" not in tpm, \
        "creating the first channel keeps the implicit #general, with everyone"


def test_a_busy_channel_does_not_flood_the_wake_prompt(team, monkeypatch):
    """Every waking message goes in; quiet ones only while they fit, and the
    rest stay unread rather than being dropped."""
    from agyteam import config
    monkeypatch.setattr(config, "WAKE_PROMPT_BYTES", 2000)
    for i in range(40):
        actions.post(team, "billing", f"chatter {i:02d} " + "x" * 80, author="matt")
    actions.post(team, "billing", "@coder this one matters", author="matt")
    runner = Recording()
    step(runner)
    prompt = runner.prompts["coder"]
    assert "this one matters" in prompt and "chatter 00" in prompt
    assert "chatter 39" not in prompt and "more unread message(s)" in prompt
    left = inbox("coder")
    assert left and all(m.quiet for m in left), "deferred mail was lost or woke"
    assert len(left) + prompt.count("chatter ") == 40


# --- threads ---------------------------------------------------------------------------

def test_replies_carry_their_thread_and_the_record_groups_them(team):
    root = actions.post(team, "billing", "@coder why is allocate wrong?", author="matt")
    mcp_bus._post_to_channel(FileTransport("coder"), {
        "channel": "billing", "content": "remainder cents are dropped", "thread": root["id"]})
    actions.post(team, "billing", "thanks", author="matt", thread=root["id"])
    rec = Record(team)
    thread = rec.thread(root["id"])
    assert [r["content"] for r in thread] == ["@coder why is allocate wrong?",
                                              "remainder cents are dropped", "thanks"]
    assert rec.reply_counts()[root["id"]] == 2


def test_a_thread_id_is_shown_to_the_agent_so_it_can_reply(team):
    root = actions.post(team, "billing", "@coder ping", author="matt")
    [m] = inbox("coder")
    assert f"[id {root['id']}" in m.render()


def test_a_dm_can_reply_in_a_thread(team):
    root = actions.dm(team, "coder", "start on it")
    first = json.loads((team / "bus.jsonl").read_text().splitlines()[0])
    out = mcp_bus._send(FileTransport("coder"), {"to": "user", "content": "on it",
                                                 "thread": first["id"]})
    assert "queued" in out
    assert [r["content"] for r in Record(team).thread(first["id"])] == ["start on it", "on it"]



def test_a_reply_to_a_reply_goes_under_the_thread_s_first_message(team):
    """An agent answered the operator's reply by that reply's id. Stored as
    is, it hung off a message no client shows a thread under, and nobody
    saw it. It now goes in the thread, by a channel post or a DM."""
    root = actions.post(team, "billing", "@coder why is allocate wrong?", author="matt")
    reply = actions.post(team, "billing", "@coder and the rounding?", author="matt", thread=root["id"])
    mcp_bus._post_to_channel(FileTransport("coder"), {
        "channel": "billing", "content": "both fixed", "thread": reply["id"]})
    rows = [json.loads(line) for line in (team / "bus.jsonl").read_text().splitlines()]
    assert rows[-1]["content"] == "both fixed" and rows[-1]["thread"] == root["id"]
    rec = Record(team)
    assert [r["content"] for r in rec.thread(root["id"])][-1] == "both fixed"
    assert rec.reply_counts() == {root["id"]: 2}
    actions.dm(team, "coder", "start on it")
    first_id = json.loads((team / "bus.jsonl").read_text().splitlines()[-1])["id"]
    mcp_bus._send(FileTransport("coder"), {"to": "user", "content": "on it", "thread": first_id})
    on_it = json.loads((team / "bus.jsonl").read_text().splitlines()[-1])
    actions.dm(team, "coder", "thanks", thread=on_it["id"])
    assert [r["content"] for r in Record(team).thread(first_id)] == ["start on it", "on it", "thanks"]


def test_replies_already_stored_under_a_reply_are_shown_in_their_thread(team):
    """Logs written before the fix: the record puts them where they belong."""
    rows = [{"id": "r00t", "ts": "2026-10-29 10:00:00", "from": "user", "to": "#billing",
             "channel": "billing", "content": "question"},
            {"id": "rep1", "ts": "2026-10-29 10:01:00", "from": "user", "to": "#billing",
             "channel": "billing", "thread": "r00t", "content": "more"},
            {"id": "rep2", "ts": "2026-10-29 10:02:00", "from": "coder", "to": "#billing",
             "channel": "billing", "thread": "rep1", "content": "answer"}]
    (team / "bus.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    rec = Record(team)
    assert [r["content"] for r in rec.thread("r00t")] == ["question", "more", "answer"]
    assert rec.reply_counts() == {"r00t": 2}
    assert {r["thread"] for r in rec.messages(channel="billing") if r.get("thread")} == {"r00t"}

# --- delivery bookkeeping ------------------------------------------------------------------------

def test_a_failed_turn_requeues_channel_copies_still_quiet(team):
    actions.post(team, "billing", "no mention", author="matt")
    t = load_transport("qa")
    msgs = t.fetch()
    t.acknowledge(msgs)
    t.requeue(msgs)
    [m] = inbox("qa")
    assert m.quiet and m.channel == "billing" and m.id == msgs[0].id


# --- the record --------------------------------------------------------------------------------------

def test_messages_by_channel_and_by_conversation(team):
    actions.post(team, "billing", "in the channel", author="matt")
    actions.dm(team, "coder", "just for you", author="matt")
    rec = Record(team)
    assert [r["content"] for r in rec.messages(channel="billing")] == ["in the channel"]
    assert [r["content"] for r in rec.messages(dm=("user", "coder"))] == ["just for you"]
    assert ("coder", "user") in rec.conversations()


def test_presence_is_offline_without_a_supervisor_and_running_when_in_flight(team):
    rec = Record(team)
    assert {p["state"] for p in rec.presence().values()} == {"offline"}
    (team / "inflight.json").write_text(json.dumps({"coder": {"ts": "2026-10-18 12:00:00",
                                                              "trigger": {"senders": ["user"]}}}))
    assert rec.presence()["coder"]["state"] == "running"


def test_the_feed_returns_what_was_appended_and_never_half_a_line(team):
    feed = Feed(team)
    assert feed.poll() == []
    actions.post(team, "billing", "first", author="matt")
    got = feed.poll()
    assert [g["item"]["content"] for g in got if g["stream"] == "bus"] == ["first"]
    with (team / "bus.jsonl").open("a") as f:
        f.write('{"id": "x", "content": "half')
    assert [g for g in feed.poll() if g["stream"] == "bus"] == []
    with (team / "bus.jsonl").open("a") as f:
        f.write(' done", "from": "user", "to": "coder", "ts": "t"}\n')
    assert [g["item"]["content"] for g in feed.poll() if g["stream"] == "bus"] == ["half done"]


def test_the_feed_notices_snapshot_changes(team):
    feed = Feed(team)
    channels.create(team, "sims", ["coder"])
    assert {"stream": "changed", "item": {"file": "channels.json"}} in feed.poll()


def test_search_finds_messages_and_tasks(team):
    from agyteam import tasks
    actions.post(team, "billing", "the kingfisher invoice is late", author="matt")
    tasks.create(team, "p", "chase the kingfisher invoice", owner="coder", created_by="coder")
    kinds = {h["type"] for h in Record(team).search("KINGFISHER")}
    assert kinds == {"message", "task"}


# --- operator actions ----------------------------------------------------------------------------

def test_operator_actions_are_refused_inside_an_agents_session(team, monkeypatch):
    monkeypatch.setenv("AGYTEAM_AGENT", "coder")
    for call in (lambda: actions.post(team, "billing", "hi"),
                 lambda: actions.dm(team, "qa", "hi"),
                 lambda: actions.create_channel(team, "x"),
                 lambda: actions.stop(team)):
        with pytest.raises(actions.OperatorError, match="coder's session"):
            call()


def test_a_dm_from_the_operator_wakes_and_names_its_author(team):
    actions.dm(team, "coder", "please start", author="matt")
    [m] = inbox("coder")
    assert m.wakes and m.author == "matt" and m.sender == "user"


def test_channel_names_and_members_are_checked(team):
    with pytest.raises(actions.OperatorError, match="lowercase"):
        actions.create_channel(team, "Bad Name!")
    with pytest.raises(actions.OperatorError, match="not on the roster"):
        actions.create_channel(team, "ok", ["ghost"])
    with pytest.raises(actions.OperatorError, match="already exists"):
        actions.create_channel(team, "billing")
    assert actions.set_members(team, "billing", add=["tpm"], remove=["qa"]) == ["coder", "tpm"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
