"""Skeleton for a runner against an external agent host. Copy, rename, fill in.

The runner is usually the right seam to adapt. It is tempting to replace the
Transport instead — "our platform already has messaging" — but that takes the
supervisor out of the scheduling loop, and its four guarantees (hop budget,
stop-on-answer, failure isolation, requeue) then have to be rebuilt by hand in
the adapter. Adapting the runner and keeping the file transport costs nothing
and keeps all four.

    cp agyteam/runner_template.py example_runner.py
    # implement wake()
    export AGYTEAM_RUNNER=example_runner:MyRunner
    export AGYTEAM_RUNNER_CONFIG='{"binary": "..."}'   # optional
    .venv/bin/python evals/test_continuity.py          # does a wake resume context?

## What you must be able to do

**Detect that a turn ended.** That is the whole requirement. You do NOT need to
read what the agent said: agents publish to teammates and to the user over the
bus, so the return value of `wake` is for logging only. A host whose entire
programmatic surface is fire-and-forget verbs, with no way to return a
transcript, can still satisfy this — with a lifecycle hook, a status file, a
queue message, or a line appended to a log.

If you have no reply text, return "" once the turn has genuinely ended. Do not
invent one, and do not return "[error: ...]" for a turn that merely produced no
readable output: that string means failure, the supervisor records it as one,
and the retry wrapper may re-run a turn that already succeeded.

## What you must declare

Set the three capability flags honestly — `supports_audit`,
`supports_containment`, `supports_capability_scoping`. They default to False,
and False is not a failing grade: it is the input other tools need in order to
tell "we could not look" apart from "there was nothing to find". A grader that
reports a team checked nothing, when really the runner cannot observe tool
calls, has produced the exact class of false finding this project exists to
eliminate. The third one is the easiest to get wrong by omission: unless you
withhold the schema for a tool listed in a roster's `tools_off`, the answer is
False, and an agent will be told its restrictions are an expectation rather
than a boundary.

## Prove it with the eval matrix

`evals/test_runner_columns.py` runs the core flows over two runner columns,
one returning prose and one returning "". Point the minimal column at your
runner and run the suite: **anything that passes under the rich column and
fails under yours is a dependency on something no runner promised**, and so
far every one of those has been a defect in agyteam rather than a shortcoming
of the host. Report it rather than working around it.

## Register before you deliver

`remember_conversation(agent, conv_id)` goes between creating the session and
sending the first message, never after the turn. Everything keyed on "which
agent is this conversation" — an audit hook, a permission policy, a workspace
grant — can only bind once that mapping exists, so registering afterwards
leaves a hole exactly one turn wide, and the first turn is the longest one an
agent ever takes. Measured on a host that binds policy this way: the same read
was allowed while the conversation was unregistered and denied once it was
registered, and none of those calls reached the audit log.

If your host assigns the id only when the turn returns, you cannot do this, and
the honest response is to bind identity somewhere else — one server mount per
agent, or an environment variable on the process — and to leave the capability
flags False so nothing downstream assumes a boundary that is not there.

## Two details that will bite you

**Build argv lists, never shell strings.** An agent message contains newlines,
quotes and backticks. Interpolating one into a shell command is both a quoting
bug and a command injection, and it will look fine until the first message
containing a backtick.

**To wait for completion, count lines in an append-only file. Never
delete-then-wait.** The obvious sentinel design — remove the marker, wait for it
to reappear — races: a previous turn's completion can land after your delete, so
`wake` returns having observed someone else's turn, and the supervisor then
schedules into an agent that is still working. Record the count before you
start, poll until it grows.

## If the host is a separate process

- **Probe capability by calling, never by asking for an inventory.** Lazily
  loaded tools do not appear when an agent lists what it has. An agent
  reporting "I don't have that tool" while the tool works is a very expensive
  hour.
- **Hooks and policies usually bind when a session is created.** A session that
  existed before you installed a clamp is not governed by it. This is a useful
  safety property rather than a bug, but it means "I added the guard" and "the
  guard is live for this agent" are different claims.
- **If the host cannot vary a server's environment per session, identity has to
  be baked in at mount time** — one mount per agent, each told which mount is
  its own. Get this wrong and messages are attributed to the wrong agent, which
  at least shows up in the bus log: ugly, but detectable rather than silent.
"""
import time
from pathlib import Path

from .runner import Runner


class MyRunner(Runner):
    label = "example"

    # Be honest here; see the module docstring. Say False unless you have
    # actually implemented the mechanism and watched it work.
    supports_audit = False
    supports_containment = False
    supports_capability_scoping = False

    def __init__(self, config=None, observer=None):
        super().__init__(config, observer=observer)
        self.timeout = float(self.config.get("timeout", 900))

    def wake(self, agent: str, message: str) -> str:
        """Deliver `message` to `agent` and block until its turn ends."""
        conv = self.conversation_id(agent)        # None on the first wake
        try:
            if conv is None:
                # Create, register, *then* deliver. Registering after the work
                # would leave the first turn unattributable -- see "Register
                # before you deliver" above.
                conv = self._create_session(agent)
                self.remember_conversation(agent, conv)
                self._deliver(conv, f"{self._brief(agent)}\n\n---\n\n{message}")
            else:
                self._deliver(conv, message)
            self._await_turn_end(conv)
        except Exception as e:                    # noqa: BLE001 - never raise
            return f"[error: {agent} failed: {type(e).__name__}: {e}]"
        # No transcript available from this host. An empty string means "the
        # turn ended and there is nothing to log", which is not a failure.
        return ""

    # --- host-specific; everything below is yours to write -----------------

    def _create_session(self, agent: str) -> str:
        """Create an empty session for `agent` and return its id.

        No work goes in here. Splitting creation from delivery is what makes
        it possible to register the conversation before the agent can act on
        anything; if your host only offers create-and-send as one call, see
        "Register before you deliver" above and bind identity another way.
        """
        raise NotImplementedError

    def _brief(self, agent: str) -> str:
        """The opening brief: persona.brief(agent, roster["agents"])."""
        raise NotImplementedError

    def _deliver(self, conv: str, message: str) -> None:
        """Send a message into an existing session. Fire-and-forget is fine.

        Build an argv LIST for any subprocess — never an interpolated string.
        """
        raise NotImplementedError

    def _await_turn_end(self, conv: str) -> None:
        """Block until the turn finishes. Count-based, so it cannot race."""
        marker = self._signal_path(conv)
        before = self._line_count(marker)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self._line_count(marker) > before:
                return
            time.sleep(0.5)
        raise TimeoutError(f"turn did not finish within {self.timeout}s")

    def _signal_path(self, conv: str) -> Path:
        """An append-only file the host writes one line to per completed turn."""
        raise NotImplementedError

    @staticmethod
    def _line_count(path: Path) -> int:
        try:
            with path.open("rb") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0        # not yet created is zero, not an error
