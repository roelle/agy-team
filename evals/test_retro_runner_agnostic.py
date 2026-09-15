"""The retrospective must work on a runner that cannot return reply text.

The Runner contract says this in as many words:

    A runner must be able to detect that a turn ended. It need not be able to
    read what the agent said. ... a runner that can only report "done" loses
    nothing the rest of the system depends on.

`retro()` was the one caller for which that sentence was false. It assigned
`wake()`'s return value straight into the reflections dict and parsed the
leader's return value for its three sections, so on a runner that reports
completion and nothing else, five agents were woken, four minutes of model
time was spent, and the report read "(missing or invalid)" three times. Then
NORMS.md was NOT WRITTEN — which makes the whole convergence loop

    retro -> NORMS.md -> brief -> changed behaviour

inert, while distill() carried on working because it measures disk.

No existing test could catch this: every double in the suite — ScriptedRunner,
MockRunner, RetroMockRunner, DaisyChainRunner, FanoutRunner and the rest —
returns prose from wake(). They all satisfy the strongest reading of the
contract, so none of them exercises the optional part of it. The mocks were
more capable than the thing they stood in for.

So the runner here is deliberately the weakest legal one: it delivers the
message, lets the agent use its tools, marks the turn ended, and returns "".
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import retro_store, supervisor  # noqa: E402
from agyteam.runner import Runner  # noqa: E402

ANSWERS = {
    "coder": ("The proof gate caught two suites that collected no tests.",
              "Three delegations failed on argument shape before anyone noticed.",
              "Add a rule: a delegation that errors twice goes to the manager."),
    "qa": ("Reviews were recorded before the user was answered.",
           "I approved work I had not executed, twice.",
           "No approval without a proof file that imports its target."),
    "tpm": ("The team delegated and reviewed without being told to.",
            "The retro itself produced nothing readable last run.",
            "Add to NORMS.md: record retro answers with record_retro, "
            "not only in reply text."),
}


class MinimalRunner(Runner):
    """The weakest runner the contract allows: no transcript, ever.

    Everything the agent produces goes through the bus, which is what the
    contract says the rest of the system depends on. `wake` returns "" for a
    genuine success, exactly as agyteam/runner_template.py instructs.
    """
    label = "minimal"
    supports_audit = False
    supports_containment = False

    def __init__(self, team_dir: Path, answers=None, silent=()):
        super().__init__()
        self.td = Path(team_dir)
        self.answers = answers if answers is not None else ANSWERS
        self.silent = set(silent)
        self.woken: list[str] = []

    def wake(self, agent: str, message: str) -> str:
        self.woken.append(agent)
        if agent not in self.silent and agent in self.answers:
            # The agent calls record_retro. On a real host this is an MCP
            # tool call; here it is the same function that tool dispatches to.
            retro_store.record(self.td, agent, *self.answers[agent])
        return ""


class ProseRunner(Runner):
    """The doubles the suite already had: reply text and nothing recorded."""
    label = "prose"

    def __init__(self, replies: dict[str, str]):
        super().__init__()
        self.replies = replies

    def wake(self, agent: str, message: str) -> str:
        return self.replies.get(agent, "ok")


LEADER_PROSE = """1. What went well
The team delegated without being told to.

2. What did not
The retro produced nothing readable.

3. What should we change
Add to NORMS.md: record retro answers with record_retro.
"""


@pytest.fixture
def team(tmp_path):
    td = tmp_path / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "Leads. Accountable for outcomes."},
        {"name": "coder", "role": "Implements."},
        {"name": "qa", "role": "Reviews."}]}))
    (td / "events.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"ts": "2026-09-14 10:00:00", "event": "turn", "agent": "coder",
         "conversation": "c1", "duration_s": 12.0},
        {"ts": "2026-09-14 10:01:00", "event": "turn", "agent": "qa",
         "conversation": "c2", "duration_s": 9.0},
        {"ts": "2026-09-14 10:02:00", "event": "episode", "turns": 2,
         "stopped_reason": "answered"}]) + "\n")
    (td / "bus.jsonl").write_text(json.dumps(
        {"ts": "2026-09-14 10:00:30", "from": "coder", "to": "tpm",
         "content": "done"}) + "\n")
    return td


# --- the store's own contract -----------------------------------------------

def test_store_round_trips(tmp_path):
    assert retro_store.read(tmp_path) == []
    retro_store.record(tmp_path, "coder", *ANSWERS["coder"])
    rows = retro_store.read(tmp_path)
    assert len(rows) == 1 and rows[0]["agent"] == "coder"
    assert rows[0]["should_change"] == ANSWERS["coder"][2]


def test_store_is_append_only_and_last_wins(tmp_path):
    """A retry supersedes a first attempt without erasing the evidence of it."""
    retro_store.record(tmp_path, "coder", *ANSWERS["coder"])
    retro_store.record(tmp_path, "coder", "second go at it here",
                       "the first answer was too vague", "propose nothing new")
    assert len(retro_store.read(tmp_path)) == 2
    assert retro_store.latest_by_agent(tmp_path)["coder"]["went_well"] == \
        "second go at it here"


@pytest.mark.parametrize("stub", ["", "   ", "none", "N/A", "nothing", "-",
                                  "TBD", "all good", "see above", "ok"])
def test_stubs_are_rejected_at_recording_time(tmp_path, stub):
    """Rejected while the agent still has the context to answer properly.

    A stub caught an hour later, in a report nobody reads, teaches nothing.
    """
    out = retro_store.record(tmp_path, "coder", stub, *ANSWERS["coder"][1:])
    assert out.startswith("[error:"), out
    assert "went_well" in out
    assert retro_store.read(tmp_path) == [], "a rejected answer must not land"


def test_the_rejection_says_what_to_do_instead(tmp_path):
    out = retro_store.record(tmp_path, "coder", "none", "none", "none")
    for field in ("went_well", "did_not", "should_change"):
        assert field in out
    assert "propose one concrete change" in out


def test_a_real_negative_answer_is_not_a_stub(tmp_path):
    """"none of the tests were run" is an answer; "none" is not."""
    out = retro_store.record(
        tmp_path, "qa", "None of the reviews were rubber-stamped this run.",
        "No tests were executed before the approval was recorded.",
        "No change: the gate already covers this and it held.")
    assert not out.startswith("[error:"), out


# --- the seam -----------------------------------------------------------------

def test_retro_succeeds_on_a_runner_that_returns_nothing(team):
    """The measured failure, replayed and fixed."""
    runner = MinimalRunner(team)
    res = supervisor.retro(leader="tpm", runner=runner, team_dir=team,
                           force=True)
    assert res.success, str(res)
    assert res.outcome != "invalid"
    assert "(missing or invalid)" not in str(res)
    assert "record_retro" in str(res) or "NORMS.md" in str(res)


def test_norms_are_written_so_the_loop_closes(team):
    """retro -> NORMS.md -> brief. Without the file the loop is inert."""
    supervisor.retro(leader="tpm", runner=MinimalRunner(team), team_dir=team,
                     force=True)
    norms = team / "NORMS.md"
    assert norms.exists(), "NORMS.md NOT WRITTEN — the convergence loop is open"
    assert "record_retro" in norms.read_text()


def test_teammate_reflections_come_from_the_store(team):
    runner = MinimalRunner(team)
    res = supervisor.retro(leader="tpm", runner=runner, team_dir=team,
                           force=True)
    assert "coder" in runner.woken and "qa" in runner.woken
    assert "I approved work I had not executed" in str(res)


def test_reply_text_still_works_and_is_unchanged(team):
    """SdkRunner behaviour must not regress; the store is a fallback, not a
    replacement."""
    runner = ProseRunner({"tpm": LEADER_PROSE,
                          "coder": "1. What went well\nfine\n",
                          "qa": "1. What went well\nfine\n"})
    res = supervisor.retro(leader="tpm", runner=runner, team_dir=team,
                           force=True)
    assert res.success, str(res)
    assert "record retro answers with record_retro" in str(res)


def test_a_recorded_answer_beats_reply_text(team):
    """Both channels live: disk wins, because only disk is guaranteed."""
    runner = MinimalRunner(team)
    prose = ProseRunner({"tpm": LEADER_PROSE})

    class Both(Runner):
        label = "both"

        def wake(self, agent, message):
            runner.wake(agent, message)
            return prose.wake(agent, message)

    res = supervisor.retro(leader="tpm", runner=Both(), team_dir=team,
                           force=True)
    assert res.success, str(res)
    # the recorded synthesis, not the prose one
    assert "not only in reply text" in str(res)


# --- and when there is genuinely nothing -------------------------------------

def test_silence_on_both_channels_names_the_channel(team):
    """"I could not hear the answer" and "there was no answer" are different.

    A turn-completion signal means the agent answered and the supervisor could
    not read it. Reporting that as a failed leader response sends whoever
    reads the report looking for a model problem that is not there.
    """
    runner = MinimalRunner(team, silent=("tpm", "coder", "qa"))
    res = supervisor.retro(leader="tpm", runner=runner, team_dir=team,
                           force=True)
    assert not res.success
    assert "neither a recorded answer nor reply text" in res.error, res.error
    assert str(team) in res.error, "must name the directory it read"


def test_a_silent_leader_with_recording_teammates_says_so(team):
    runner = MinimalRunner(team, silent=("tpm",))
    res = supervisor.retro(leader="tpm", runner=runner, team_dir=team,
                           force=True)
    assert not res.success
    assert "no reply text and recorded no retrospective answer" in res.error
    assert "2 teammate answer(s) were recorded" in res.error, res.error


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
