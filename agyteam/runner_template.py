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

Set the two capability flags honestly. They default to False, and False is not
a failing grade — it is the input other tools need in order to tell "we could
not look" apart from "there was nothing to find". A grader that reports a team
checked nothing, when really the runner cannot observe tool calls, has produced
the exact class of false finding this project exists to eliminate.

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
                conv = self._start(agent, message)
                self.remember_conversation(agent, conv)
            else:
                self._deliver(conv, message)
            self._await_turn_end(conv)
        except Exception as e:                    # noqa: BLE001 - never raise
            return f"[error: {agent} failed: {type(e).__name__}: {e}]"
        # No transcript available from this host. An empty string means "the
        # turn ended and there is nothing to log", which is not a failure.
        return ""

    # --- host-specific; everything below is yours to write -----------------

    def _start(self, agent: str, message: str) -> str:
        """Create a session for `agent`, seeded with its brief. Return its id.

        Send the opening brief here: persona.brief(agent, roster["agents"]).
        Build an argv LIST for any subprocess — never an interpolated string.
        """
        raise NotImplementedError

    def _deliver(self, conv: str, message: str) -> None:
        """Send a message into an existing session. Fire-and-forget is fine."""
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
