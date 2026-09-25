"""Every run_until_idle call is its own episode, as --chat assumes.

`--chat` drives one Supervisor through many run_until_idle calls, one per
message. None of the per-episode state used to be reset between them, so:
the second message's cascade stopped after one pass and its answer arrived a
message late; the hop budget drained across the whole session; and one
approved review early on satisfied the review gate for every later episode.
"""
import json
import sys
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
        {"name": "tpm", "role": "coordinates", "is_principal": True},
        {"name": "coder", "role": "implements"}]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    return td


class RoundTrip(ScriptedRunner):
    """user -> tpm -> coder -> tpm -> user: needs two passes of step(), since
    tpm is visited before coder in each pass."""

    def wake(self, agent, message):
        self.woken.append(agent)
        bus = load_transport(agent)
        try:
            if agent == "coder":
                bus.send("tpm", "built it")
            elif "built it" in message:
                bus.send("user", "done")
            else:
                bus.send("coder", "please build it")
        finally:
            bus.close()
        return "ok"


def ask(sup, text):
    user = load_transport("user")
    try:
        user.send("tpm", text)
        sup.run_until_idle()
        got = user.fetch()
        user.acknowledge(got)
        return got
    finally:
        user.close()


def test_every_message_in_a_chat_gets_its_own_answer(team):
    runner = RoundTrip({})
    sup = Supervisor(["tpm", "coder"], runner, require_review=False, quiet=True)
    try:
        for i in range(3):
            answers = ask(sup, f"task {i}")
            assert len(answers) == 1, f"message {i} got {len(answers)} answers"
    finally:
        sup.close()


def test_the_hop_budget_bounds_one_episode_not_a_session(team):
    runner = RoundTrip({})
    sup = Supervisor(["tpm", "coder"], runner, require_review=False,
                     max_hops=4, quiet=True)
    try:
        for i in range(4):            # 3 hops each; 12 in all, budget 4
            assert len(ask(sup, f"task {i}")) == 1
            assert "hop budget" not in sup.stopped
    finally:
        sup.close()


def test_a_review_from_an_earlier_episode_does_not_pass_the_gate(team):
    runner = RoundTrip({})
    sup = Supervisor(["tpm", "coder"], runner, require_review=True, quiet=True)
    try:
        ask(sup, "first")
        # An approved review lands between episodes, e.g. from the first task.
        with (team / "reviews.jsonl").open("a") as f:
            f.write(json.dumps({"kind": "work", "verdict": "approved",
                                "reviewer": "qa", "author": "coder",
                                "what": "first"}) + "\n")
        ask(sup, "second")
        # Nothing reviewed the second answer. What gets recorded is what the
        # report and the next retrospective read, so that is what is checked.
        episodes = [json.loads(l) for l in
                    (team / "events.jsonl").read_text().splitlines()
                    if l.strip() and json.loads(l).get("event") == "episode"]
        assert len(episodes) == 2
        assert episodes[-1]["reviewed"] is False, episodes[-1]
        assert sup.stopped == "the user was answered (unreviewed)", sup.stopped
    finally:
        sup.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
