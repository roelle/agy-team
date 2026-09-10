"""Reactive dispatcher: turns bus traffic into agent turns, with no human poll.

A message arriving for an agent wakes that agent. Whatever it sends in response
lands in another agent's inbox and wakes them in turn, so one instruction to the
tpm cascades through the team on its own and comes back to you when it's done.
Agents never sit on unread mail waiting to be prodded.

The three seams stay independent: the supervisor asks a *transport* what arrived
and a *runner* to wake somebody. Swap either — a push-based bus, agents run
through the agy CLI or the SDK — without changing this file.

    python -m agyteam.supervisor --say "tpm: <task>"   # run until the team idles
    python -m agyteam.supervisor --daemon              # stay up, react forever
    python -m agyteam.supervisor --status              # who has mail waiting

Safety: a hop budget bounds one stimulus (agents cannot ping-pong your token
budget away), and a failing agent is logged and skipped rather than stopping
the team.
"""
import argparse
import os
import sys
import threading
from pathlib import Path

from . import roster as roster_lib
from . import runner as runner_lib
from . import scope
from .transport import Message
from .transport import load as load_transport

WAKE_PROMPT = """You have new messages from your team:

{messages}

Act on them now. Do the work if it is yours to do, or delegate with
send_to_teammate. Reply to whoever wrote to you when you are done — an
unanswered message stalls the person waiting on it. Anything you want a
teammate or the user to see must go through send_to_teammate; text you write
here is seen by nobody."""


class Supervisor:
    def __init__(self, agents: list[str], runner, team_dir=None,
                 max_hops: int = 32, poll: float = 1.0, quiet: bool = False):
        self.agents = agents
        self.runner = runner
        self.max_hops = max_hops
        self.poll = poll
        self.quiet = quiet
        if team_dir:
            os.environ.setdefault("AGYTEAM_TEAM_DIR", str(team_dir))
        # One transport per agent: each reads its own mail, exactly as the
        # agent's own MCP server would.
        self.transports = {a: load_transport(a) for a in agents}
        self.hops = 0

    def _log(self, msg: str):
        if not self.quiet:
            print(msg, flush=True)

    def pending(self) -> dict[str, list[Message] | None]:
        """Mail waiting, without consuming it. None where unsupported."""
        return {a: t.peek() for a, t in self.transports.items()}

    def step(self) -> int:
        """One pass: wake every agent that has mail. Returns turns dispatched."""
        dispatched = 0
        for agent, transport in self.transports.items():
            if self.hops >= self.max_hops:
                return dispatched
            msgs = transport.fetch()
            if not msgs:
                continue
            senders = ", ".join(sorted({m.sender for m in msgs}))
            self._log(f"  → waking {agent} ({len(msgs)} from {senders})")
            body = "\n\n".join(m.render() for m in msgs)
            self.hops += 1
            try:
                reply = self.runner.wake(agent, WAKE_PROMPT.format(messages=body))
            except Exception as e:                      # one agent must not
                reply = f"[error: {type(e).__name__}: {e}]"   # stop the team
            if reply.startswith("[error:"):
                self._log(f"    {agent}: {reply[:200]}")
            elif not self.quiet:
                first = reply.strip().splitlines()[0] if reply.strip() else ""
                self._log(f"    {agent}: {first[:120]}")
            dispatched += 1
        return dispatched

    def run_until_idle(self) -> int:
        """Dispatch until nobody has mail. Returns total turns taken."""
        total = 0
        while self.hops < self.max_hops:
            n = self.step()
            total += n
            if n == 0:
                break
        if self.hops >= self.max_hops:
            self._log(f"[hop budget of {self.max_hops} reached — stopping. "
                      f"Raise --max-hops or send a new instruction.]")
        return total

    def run_forever(self, stop: threading.Event | None = None) -> None:
        """React to mail as it arrives, indefinitely."""
        stop = stop or threading.Event()
        self._log(f"supervising {', '.join(self.agents)} "
                  f"via {self.runner.label} — Ctrl+C to stop")
        while not stop.is_set():
            if self.step() == 0:
                stop.wait(self.poll)
            # A long-running daemon should not inherit a budget meant to bound
            # one stimulus; the cap applies per burst of activity.
            elif self.hops >= self.max_hops:
                self._log(f"[hop budget reached; resetting for the next burst]")
                self.hops = 0

    def close(self):
        self.runner.close()
        for t in self.transports.values():
            t.close()


def _agents_from_roster(team_dir: Path) -> list[str]:
    return [a["name"] for a in roster_lib.load(team_dir / "roster.json")["agents"]]


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="agyteam.supervisor",
        description="Wake agents when teammates message them")
    ap.add_argument("--say", metavar="'agent: message'",
                    help="Send this, then run until the team goes idle")
    ap.add_argument("--daemon", action="store_true",
                    help="Stay up and react to mail as it arrives")
    ap.add_argument("--status", action="store_true",
                    help="Show who has mail waiting, then exit")
    ap.add_argument("--team-dir", default=None)
    ap.add_argument("--max-hops", type=int, default=32)
    ap.add_argument("--poll", type=float, default=1.0,
                    help="Seconds between checks when idle (daemon mode)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    team_dir = Path(args.team_dir) if args.team_dir else scope.load().team_dir()
    os.environ.setdefault("AGYTEAM_TEAM_DIR", str(team_dir))
    agents = _agents_from_roster(team_dir)
    if not agents:
        sys.exit(f"no agents on the roster at {team_dir / 'roster.json'}")

    if args.status:
        # peek(), never fetch() — reporting on the queue must not empty it.
        sup = Supervisor(agents, _InspectOnly(), team_dir=team_dir, quiet=True)
        try:
            for agent, msgs in sup.pending().items():
                if msgs is None:
                    print(f"  {agent}: (transport cannot report queue depth)")
                else:
                    senders = ", ".join(sorted({m.sender for m in msgs}))
                    print(f"  {agent}: {len(msgs)} waiting from {senders}"
                          if msgs else f"  {agent}: idle")
        finally:
            sup.close()
        return

    sup = Supervisor(agents, runner_lib.load(), team_dir=team_dir,
                     max_hops=args.max_hops, poll=args.poll, quiet=args.quiet)
    try:
        if args.say:
            to, _, content = args.say.partition(":")
            to, content = to.strip(), content.strip()
            if to not in agents:
                sys.exit(f"unknown agent {to!r}; roster has: {', '.join(agents)}")
            load_transport("user").send(to, content)
            sup.run_until_idle()
        elif args.daemon:
            try:
                sup.run_forever()
            except KeyboardInterrupt:
                print("\nstopped")
        else:
            sup.run_until_idle()
    finally:
        sup.close()


class _InspectOnly(runner_lib.Runner):
    """Runner for --status: never wakes anybody."""
    label = "inspect-only"

    def wake(self, agent: str, message: str) -> str:
        return "[status mode: no agents were woken]"


if __name__ == "__main__":
    main()
