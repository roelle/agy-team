"""The file transport must not lose mail to a concurrent sender.

acknowledge() reads the inbox, drops what was acknowledged, and writes the
rest back. A message appended between that read and that write was
overwritten -- delivered to nobody, logged in bus.jsonl as sent. Senders are
separate processes (every agent's bus server, the user's CLI, the reminder
sweep), so "between" is a real window, not a theoretical one.

And one malformed line in an inbox made every read of it raise, forever:
the supervisor's step() died on it each pass, which stops the whole team,
not just the agent whose inbox it was.
"""
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "coordinates"},
        {"name": "coder", "role": "implements"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    return td


def test_no_message_is_lost_while_the_reader_acknowledges(team):
    total = 400
    received = []
    done = threading.Event()

    def sender():
        bus = load_transport("tpm")
        for i in range(total):
            bus.send("coder", f"msg {i}")
        done.set()

    def reader():
        inbox = load_transport("coder")
        while True:
            finished = done.is_set()
            msgs = inbox.fetch()
            if msgs:
                inbox.acknowledge(msgs)
                received.extend(m.content for m in msgs)
            elif finished:
                return

    threads = [threading.Thread(target=sender), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(received, key=lambda s: int(s.split()[1])) == \
        [f"msg {i}" for i in range(total)], \
        f"{total - len(received)} of {total} messages were lost"


def test_a_malformed_line_does_not_wedge_the_inbox(team):
    bus = load_transport("tpm")
    bus.send("coder", "before")
    with (team / "inbox" / "coder.jsonl").open("a") as f:
        f.write('{"ts": "x", "from": "tp\n')          # a torn write
    bus.send("coder", "after")
    got = [m.content for m in load_transport("coder").fetch()]
    assert got == ["before", "after"]


def test_one_broken_inbox_does_not_stop_the_team(team, monkeypatch):
    load_transport("user").send("tpm", "hello")
    runner = ScriptedRunner({})
    sup = Supervisor(["tpm", "coder"], runner, quiet=True)
    try:
        def broken():
            raise OSError("inbox unreadable")
        monkeypatch.setattr(sup.transports["coder"], "fetch", broken)
        sup.step()
    finally:
        sup.close()
    assert runner.woken == ["tpm"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
