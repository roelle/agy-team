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
import json
import os
import sys
import threading
import time
from pathlib import Path

from . import config
from . import memory as memory_lib
from . import roster as roster_lib
from . import runner as runner_lib
from . import scope
from .transport import Message
from .transport import load as load_transport

WAKE_PROMPT = """You have new messages from your team:

{messages}

Act on them now: do the work if it is yours, or delegate with send_to_teammate.

Send a message only when it carries something the recipient does not already
have — a deliverable, an answer, a question, a blocker, or a correction. Do NOT
send acknowledgements, thanks, "got it", or "standing by" notes. Every message
you send wakes a teammate and costs them a full turn, so an ack wakes someone up
to read nothing and provokes an ack in return. Silence means understood.

If these messages need no action from you, do nothing at all and send nothing.
That is a normal, common, correct outcome — not a failure to participate.

When the work you were asked for is done, send the result to whoever asked for
it, once. Anything you want a teammate or the user to see must go through
send_to_teammate; text you write here is seen by nobody."""

DISTILL_PROMPT = """Write down, using save_memory, what you have learned that a future session would need and does not yet have.

Record durable, specific lessons — what surprised you, what you got wrong, what you would tell your replacement — rather than a summary of the conversation.

Call save_memory now for each lesson (update existing memories rather than duplicating). If there is nothing durable to record that is not already in memory, do not save anything. Reply with what you saved, or confirm that nothing new needed saving."""


class Supervisor:
    def __init__(self, agents: list[str], runner, team_dir=None,
                 max_hops: int = 32, poll: float = 1.0, quiet: bool = False,
                 stop_on_answer: bool = True, observer=None):
        self.agents = agents
        self.runner = runner
        self.max_hops = max_hops
        self.poll = poll
        self.quiet = quiet
        self.stop_on_answer = stop_on_answer
        if team_dir:
            self.team_dir = Path(team_dir)
            os.environ["AGYTEAM_TEAM_DIR"] = str(self.team_dir)
            if self.team_dir.name == "team":
                os.environ["AGYTEAM_DURABLE_DIR"] = str(self.team_dir.parent)
        elif "AGYTEAM_TEAM_DIR" in os.environ:
            self.team_dir = Path(os.environ["AGYTEAM_TEAM_DIR"]).resolve()
            if self.team_dir.name == "team":
                os.environ.setdefault("AGYTEAM_DURABLE_DIR", str(self.team_dir.parent))
        else:
            self.team_dir = scope.load().team_dir()
        from . import observer as observer_lib
        self.observer = observer or getattr(runner, "_observer", None) or observer_lib.load()
        if hasattr(runner, "observer"):
            runner.observer = self.observer
        self.reviews_path = self.team_dir / "reviews.jsonl"
        self._approved_reviews_at_start = self._approved_reviews_count()
        # One transport per agent: each reads its own mail, exactly as the
        # agent's own MCP server would.
        self.transports = {a: load_transport(a) for a in agents}
        # The user is not an agent and is never woken, but their inbox is the
        # episode's finish line: once someone has answered, the ask is done.
        self.user_transport = load_transport("user")
        self._user_mail_at_start = self._user_mail_count()
        self.hops = 0
        self.stopped = ""

    def _user_mail_count(self) -> int | None:
        """How much mail the user is holding. None if peek is unsupported."""
        waiting = self.user_transport.peek()
        return None if waiting is None else len(waiting)

    def _user_was_answered(self) -> bool:
        now = self._user_mail_count()
        if now is None or self._user_mail_at_start is None:
            return False        # can't tell; fall back to idle/hop budget
        return now > self._user_mail_at_start

    def _approved_reviews_count(self) -> int:
        """Count approved reviews recorded in reviews.jsonl."""
        if not self.reviews_path.exists():
            return 0
        count = 0
        try:
            for line in self.reviews_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("verdict") == "approved":
                        count += 1
                except json.JSONDecodeError:
                    continue
        except OSError:
            return 0
        return count

    def _has_new_approved_review(self) -> bool:
        """True if an approved review was recorded during this episode."""
        return self._approved_reviews_count() > self._approved_reviews_at_start

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
            t0 = time.monotonic()
            try:
                reply = self.runner.wake(agent, WAKE_PROMPT.format(messages=body))
            except Exception as e:                      # one agent must not
                dur = time.monotonic() - t0
                reply = f"[error: {type(e).__name__}: {e}]"   # stop the team
                try:
                    cid = getattr(self.runner, "conversation_id", lambda a: "")(agent) or ""
                    self.observer.record_failure(agent, cid, reply, duration_s=dur)
                except Exception:
                    pass
            if reply.startswith("[error:"):
                self._log(f"    {agent}: {reply[:200]}")
            elif not self.quiet:
                first = reply.strip().splitlines()[0] if reply.strip() else ""
                self._log(f"    {agent}: {first[:120]}")
            dispatched += 1
        return dispatched

    def run_until_idle(self) -> int:
        """Dispatch until the user is answered, or nobody has mail.

        Returns total turns taken; self.stopped says why we stopped.
        """
        t0 = time.monotonic()
        total = 0
        while self.hops < self.max_hops:
            n = self.step()
            total += n
            if n == 0:
                self.stopped = "team went idle"
                break
            # Answering the user ends the episode. Without this, agents who
            # have nothing left to do still owe each other a reply, and a
            # finished team keeps talking until the hop budget kills it.
            if self.stop_on_answer and self._user_was_answered():
                # Design Decision: Flagging vs Blocking Unreviewed Answers
                #
                # We choose to FLAG unreviewed answers prominently rather than BLOCK them.
                # Rationale:
                # 1. Autonomous execution safety: Blocking an answer when a review is missing
                #    risks hanging the team or causing runaways that consume the entire hop budget,
                #    particularly if a designated reviewer agent crashes, encounters an error,
                #    or is slow to respond.
                # 2. Operator visibility: The supervisor's finish line is answering the user.
                #    Reporting "the user was answered (unreviewed)" alongside a logged warning
                #    gives the human operator immediate, unambiguous transparency about review
                #    status without stranding the supervisor loop in deadlocks.
                if self._has_new_approved_review():
                    self.stopped = "the user was answered"
                else:
                    self.stopped = "the user was answered (unreviewed)"
                    self._log("[WARNING: the user was answered without an approved review recorded in reviews.jsonl]")
                break
        if self.hops >= self.max_hops:
            self.stopped = f"hop budget of {self.max_hops} reached"
            self._log(f"[hop budget of {self.max_hops} reached — stopping. "
                      f"Raise --max-hops or send a new instruction.]")
        else:
            self._log(f"[done after {total} turns — {self.stopped}]")
        try:
            dur = time.monotonic() - t0
            self.observer.record_episode(
                turns=total,
                stopped_reason=self.stopped,
                reviewed=self._has_new_approved_review(),
                duration_s=dur,
            )
        except Exception:
            pass
        self.auto_cycle()
        return total

    def run_forever(self, stop: threading.Event | None = None) -> None:
        """React to mail as it arrives, indefinitely."""
        stop = stop or threading.Event()
        self._log(f"supervising {', '.join(self.agents)} "
                  f"via {self.runner.label} — Ctrl+C to stop")
        while not stop.is_set():
            if self.step() == 0:
                self.auto_cycle()
                stop.wait(self.poll)
            # A long-running daemon should not inherit a budget meant to bound
            # one stimulus; the cap applies per burst of activity.
            elif self.hops >= self.max_hops:
                self._log(f"[hop budget reached; resetting for the next burst]")
                self.hops = 0
                self.auto_cycle()

    def _memory_count(self, agent: str) -> int:
        store = memory_lib.load(agent)
        try:
            return len(store.index())
        finally:
            store.close()

    def _memory_snapshot(self, agent: str) -> dict[str, tuple[str, str | None]]:
        store = memory_lib.load(agent)
        try:
            return {
                entry.name: (entry.description, store.read(entry.name))
                for entry in store.index()
            }
        finally:
            store.close()

    def distill(self, agent: str) -> tuple[int, int]:
        """Wake `agent` to distill learnings into memory.

        Returns (before_count, after_count).
        """
        self._last_distill_failed = False
        before_snap = self._memory_snapshot(agent)
        before = len(before_snap)
        self._log(f"  → distilling {agent}")
        t0 = time.monotonic()
        try:
            reply = self.runner.wake(agent, DISTILL_PROMPT)
        except Exception as e:
            self._last_distill_failed = True
            dur = time.monotonic() - t0
            reply = f"[error: {type(e).__name__}: {e}]"
            try:
                cid = getattr(self.runner, "conversation_id", lambda a: "")(agent) or ""
                self.observer.record_failure(agent, cid, reply, duration_s=dur)
            except Exception:
                pass

        if reply.startswith("[error:"):
            self._last_distill_failed = True
            self._log(f"    {agent}: {reply[:200]}")
        elif not self.quiet:
            first = reply.strip().splitlines()[0] if reply.strip() else ""
            self._log(f"    {agent}: {first[:120]}")

        after_snap = self._memory_snapshot(agent)
        after = len(after_snap)
        new_count = len(set(after_snap) - set(before_snap))
        updated_count = sum(
            1 for k in set(before_snap) & set(after_snap)
            if before_snap[k] != after_snap[k]
        )
        if new_count > 0 and updated_count > 0:
            self._log(f"    {agent}: distilled {new_count} new, {updated_count} updated memories ({before} → {after})")
        elif new_count > 0:
            self._log(f"    {agent}: distilled {new_count} new memories ({before} → {after})")
        elif updated_count > 0:
            self._log(f"    {agent}: distilled {updated_count} updated memories ({before} → {after})")
        elif after < before:
            self._log(f"    {agent}: memory count changed from {before} to {after}")
        else:
            self._log(f"    {agent}: 0 memories saved ({before} before, {after} after)")
        return (before, after)

    def cycle(self, agent: str) -> tuple[int, int]:
        """Distill learnings into memory, then reset conversation for `agent`.

        Cycling without distilling first is not permitted.
        Returns (before_count, after_count).
        """
        counts = self.distill(agent)
        if getattr(self, "_last_distill_failed", False):
            self._log(f"    {agent}: cycle aborted because distillation failed")
            return counts
        self.runner.reset(agent)
        self._log(f"    {agent}: conversation reset")
        return counts

    def auto_cycle(self) -> list[str]:
        """Check latest turns for agents and auto-cycle those exceeding CYCLE_THRESHOLD_TOKENS.

        Never cycles mid-episode when an agent has outstanding work (pending inbox messages).
        Returns list of cycled agent names.
        """
        threshold = int(os.environ.get("AGYTEAM_CYCLE_THRESHOLD", config.CYCLE_THRESHOLD_TOKENS))
        try:
            turn_events = self.observer.events("turn")
        except Exception:
            return []
        if not turn_events:
            return []

        cycled = []
        for agent in self.agents:
            conv_id = getattr(self.runner, "conversation_id", lambda a: None)(agent)
            if not conv_id:
                continue

            agent_turns = [
                ev for ev in turn_events
                if ev.get("agent") == agent and ev.get("conversation") == conv_id
            ]
            if not agent_turns:
                continue

            latest_ev = agent_turns[-1]
            input_tokens = latest_ev.get("input_tokens")
            if input_tokens is None or input_tokens <= threshold:
                continue

            pending_msgs = self.pending().get(agent) or []
            if pending_msgs:
                self._log(f"  → skipping auto-cycle for {agent}: work outstanding ({len(pending_msgs)} pending messages)")
                continue

            self._log(f"  → auto-cycling {agent} (latest turn input_tokens {input_tokens:,} > threshold {threshold:,})")
            self.cycle(agent)
            if not getattr(self, "_last_distill_failed", False):
                cycled.append(agent)

        return cycled

    def reset(self, agent: str | None = None):
        """Reset is not permitted without distilling first."""
        raise RuntimeError(
            f"cycling without distilling first is not permitted: use cycle('{agent or ''}')"
        )

    def close(self):
        self.runner.close()
        for t in self.transports.values():
            t.close()
        self.user_transport.close()
        try:
            self.observer.close()
        except Exception:
            pass


def distill(agent: str, runner=None, team_dir=None) -> tuple[int, int]:
    """Wake `agent` to distill learnings into memory, returning (before_count, after_count)."""
    r = runner or runner_lib.load()
    sup = Supervisor([agent], r, team_dir=team_dir)
    try:
        return sup.distill(agent)
    finally:
        if runner is not None:
            for t in sup.transports.values():
                t.close()
            sup.user_transport.close()
            try:
                sup.observer.close()
            except Exception:
                pass
        else:
            sup.close()


def cycle(agent: str, runner=None, team_dir=None) -> tuple[int, int]:
    """Distill learnings and reset conversation for `agent`, returning (before_count, after_count)."""
    r = runner or runner_lib.load()
    sup = Supervisor([agent], r, team_dir=team_dir)
    try:
        return sup.cycle(agent)
    finally:
        if runner is not None:
            for t in sup.transports.values():
                t.close()
            sup.user_transport.close()
            try:
                sup.observer.close()
            except Exception:
                pass
        else:
            sup.close()


def _agents_from_roster(team_dir: Path) -> list[str]:
    return [a["name"] for a in roster_lib.load(team_dir / "roster.json")["agents"]]


def main(argv=None, runner=None):
    ap = argparse.ArgumentParser(
        prog="agyteam.supervisor",
        description="Wake agents when teammates message them")
    ap.add_argument("--distill", metavar="AGENT",
                    help="Distill learnings into memory for AGENT, then exit")
    ap.add_argument("--cycle", metavar="AGENT",
                    help="Distill learnings and reset conversation for AGENT, then exit")
    ap.add_argument("--distill-all", action="store_true",
                    help="Distill learnings for all agents on the roster, then exit")
    ap.add_argument("--cycle-all", action="store_true",
                    help="Distill and reset conversations for all agents on the roster, then exit")
    ap.add_argument("--say", metavar="'agent: message'",
                    help="Send this, then run until the team goes idle")
    ap.add_argument("--daemon", action="store_true",
                    help="Stay up and react to mail as it arrives")
    ap.add_argument("--status", action="store_true",
                    help="Show who has mail waiting, then exit")
    ap.add_argument("--cost", action="store_true",
                    help="Show team usage and cost summary, then exit")
    ap.add_argument("--team-dir", default=None)
    ap.add_argument("--max-hops", type=int, default=32)
    ap.add_argument("--poll", type=float, default=1.0,
                    help="Seconds between checks when idle (daemon mode)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-stop-on-answer", action="store_true",
                    help="Keep dispatching after the user is answered "
                         "(default: an answer to the user ends the episode)")
    args = ap.parse_args(argv)

    team_dir = Path(args.team_dir) if args.team_dir else scope.load().team_dir()
    os.environ["AGYTEAM_TEAM_DIR"] = str(team_dir)
    if team_dir.name == "team":
        os.environ["AGYTEAM_DURABLE_DIR"] = str(team_dir.parent)

    if args.cost:
        from . import observer as observer_lib
        obs = observer_lib.load()
        print(observer_lib.format_summary(obs.summary()))
        return
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

    r = runner or runner_lib.load()
    sup = Supervisor(agents, r, team_dir=team_dir,
                     max_hops=args.max_hops, poll=args.poll, quiet=args.quiet,
                     stop_on_answer=not args.no_stop_on_answer)
    try:
        if args.distill:
            if args.distill not in agents:
                sys.exit(f"unknown agent {args.distill!r}; roster has: {', '.join(agents)}")
            sup.distill(args.distill)
        elif args.cycle:
            if args.cycle not in agents:
                sys.exit(f"unknown agent {args.cycle!r}; roster has: {', '.join(agents)}")
            sup.cycle(args.cycle)
        elif args.distill_all:
            for a in agents:
                sup.distill(a)
        elif args.cycle_all:
            for a in agents:
                sup.cycle(a)
        elif args.say:
            to, _, content = args.say.partition(":")
            to, content = to.strip(), content.strip()
            if to not in agents:
                sys.exit(f"unknown agent {to!r}; roster has: {', '.join(agents)}")
            load_transport("user").send(to, content)
            sup.run_until_idle()
            # The agents' stdout is just a self-summary; what they actually
            # addressed to the user is the real answer, so show it.
            for m in load_transport("user").fetch():
                print(f"\n[{m.sender} → you] {m.content}")
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
