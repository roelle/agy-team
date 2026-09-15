"""A conversation must be attributable before its first tool call, not after.

Anything keyed on "which agent is this conversation" -- an audit hook, a
permission policy, a workspace grant -- can only bind once
`conversations.json` says so. A runner that registers after the turn returns
leaves a hole exactly one turn wide, and the first turn is the longest one an
agent ever takes: it carries the brief and does the most exploring.

Measured on a host that binds policy this way: the same read was allowed while
the conversation was unregistered and denied once it was registered, and none
of those calls reached the audit log. The run looked normal from the outside.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam.runner import Runner  # noqa: E402


class Host:
    """A host that answers "whose conversation is this?" the way a hook would.

    It asks the runner at the moment work arrives, which is the only moment
    that matters: an answer that becomes correct after the turn is no use to a
    policy that had to decide during it.
    """

    def __init__(self, runner):
        self.runner = runner
        self.attribution = []      # (conv_id, agent-or-None) per delivery
        self.next_id = 0

    def create(self):
        self.next_id += 1
        return f"conv-{self.next_id}"

    def deliver(self, conv, message):
        self.attribution.append((conv, self.runner.agent_for_conversation(conv)))


class TemplateRunner(Runner):
    """Shaped exactly like agyteam/runner_template.py: create, register, deliver."""
    label = "template"

    def __init__(self, config=None, observer=None):
        super().__init__(config, observer=observer)
        self.host = Host(self)

    def _brief(self, agent):
        return f"you are {agent}"

    def wake(self, agent: str, message: str) -> str:
        conv = self.conversation_id(agent)
        if conv is None:
            conv = self.host.create()
            self.remember_conversation(agent, conv)
            self.host.deliver(conv, f"{self._brief(agent)}\n\n---\n\n{message}")
        else:
            self.host.deliver(conv, message)
        return ""


class LateRunner(TemplateRunner):
    """The shape the template used to have, and both shipped runners still do:
    the id is not known until the work has already run."""

    def wake(self, agent: str, message: str) -> str:
        conv = self.conversation_id(agent)
        if conv is None:
            conv = self.host.create()
            self.host.deliver(conv, message)
            self.remember_conversation(agent, conv)
        else:
            self.host.deliver(conv, message)
        return ""


@pytest.fixture
def team(tmp_path, monkeypatch):
    d = tmp_path / "team"
    d.mkdir()
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(d))
    return d


def test_the_first_turn_is_attributable(team):
    r = TemplateRunner()
    r.wake("coder", "build the thing")
    assert r.host.attribution == [("conv-1", "coder")]


def test_the_shape_this_replaces_is_not(team):
    """Without this the test above passes for both orderings and proves
    nothing -- the ordering is invisible once the turn is over."""
    r = LateRunner()
    r.wake("coder", "build the thing")
    assert r.host.attribution == [("conv-1", None)]


def test_later_turns_were_never_the_problem(team):
    r = LateRunner()
    r.wake("coder", "first")
    r.wake("coder", "second")
    assert r.host.attribution[1] == ("conv-1", "coder")


def test_registration_survives_the_process(team):
    """The hook runs in a different process, so in-memory state is no use."""
    TemplateRunner().wake("coder", "go")
    assert Runner.agent_for_conversation(TemplateRunner(), "conv-1") == "coder"


def test_the_brief_goes_in_after_registration_too(team):
    """The brief is the biggest single message an agent ever gets and names
    every workspace it has; it should not land in an unattributed session."""
    r = TemplateRunner()
    r.wake("coder", "go")
    conv, who = r.host.attribution[0]
    assert who == "coder"


def test_the_template_keeps_this_shape():
    """The template is what anyone adapting a host actually copies, so the
    ordering has to be right *there*, not only in prose about it."""
    src = (ROOT / "agyteam" / "runner_template.py").read_text()
    body = src[src.index("def wake("):src.index("# --- host-specific")]
    create = body.index("_create_session")
    register = body.index("remember_conversation")
    deliver = body.index("_deliver")
    assert create < register < deliver, \
        "create, register, then deliver -- in that order"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
