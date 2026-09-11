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
import re
import sys
import threading
import time
from pathlib import Path

from . import config
from . import memory as memory_lib
from . import persona
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

TENSION_ACCOUNTABLE = (
    "a retro led by the person accountable for the outcome is a weaker retro, "
    "and that tension is worth naming rather than hiding."
)

RETRO_PARTICIPANT_PROMPT = """You are participating in a team retrospective on recent work.
The retro is led by {leader}.
{tension_note}
Here is the durable record of recent work (episodes, reviews, and cost):

{record}

Reflect on this record and answer these three questions, in this exact order:
1. What went well
2. What did not
3. What should we change

Under "What should we change", provide concrete proposals or improvements for the team.
Be specific, grounded in the record above, and direct."""

RETRO_LEADER_PROMPT = """You are leading the team retrospective on recent work.
{tension_note}
Here is the durable record of recent work:

{record}

Teammate reflections:
{reflections}

As the retrospective leader, synthesize the discussion and produce the final retrospective report.
You must answer these three questions, in this exact order:
1. What went well
2. What did not
3. What should we change

MANDATORY REQUIREMENT FOR QUESTION 3:
Under "What should we change", you MUST produce either:
- A concrete written change to NORMS.md (e.g. proposed text to add or update in NORMS.md)
OR
- An explicit "no change, and here is why" explaining substantively why no norm change is needed.

A retro that produces neither has failed and will be rejected.
Be direct, constructive, and grounded in the record."""

Q1_RE = re.compile(
    r"(?im)^\s*(?:#+\s*)?(?:\*{0,2}\s*)?(?:1[\.\)]\s*)?(?:what\s+went\s+well)[\s\:\?\-\*]*"
)
Q2_RE = re.compile(
    r"(?im)^\s*(?:#+\s*)?(?:\*{0,2}\s*)?(?:2[\.\)]\s*)?(?:what\s+did\s+not(?:\s+go\s+well)?|what\s+went\s+wrong)[\s\:\?\-\*]*"
)
Q3_RE = re.compile(
    r"(?im)^\s*(?:#+\s*)?(?:\*{0,2}\s*)?(?:3[\.\)]\s*)?(?:what\s+should\s+we\s+change|what\s+to\s+change)[\s\:\?\-\*]*"
)

DODGE_PHRASES = {
    "none", "none.", "n/a", "tbd", "nothing", "nothing.",
    "no change", "no change.", "no changes", "no changes.",
    "no changes needed", "no changes needed.", "nil", "pass",
    "i don't know", "unsure",
}


class RetroResult(str):
    """Result of a retrospective execution. Subclasses str for convenient display."""

    def __new__(cls, content: str, success: bool, outcome: str,
                norm_change: str | None = None, leader: str = "tpm",
                retro_file: Path | None = None, error: str | None = None,
                refused: bool = False):
        obj = super().__new__(cls, content)
        obj.success = success
        obj.outcome = outcome
        obj.norm_change = norm_change
        obj.leader = leader
        obj.retro_file = retro_file
        obj.error = error
        obj.refused = refused
        return obj

    def __bool__(self) -> bool:
        return self.success


def parse_retro_sections(text: str) -> dict[str, str] | None:
    """Parse text into 3 ordered sections: q1, q2, q3.

    Returns dict with keys "q1", "q2", "q3", or None if sections are missing,
    out of order, or empty.
    """
    if not text or not isinstance(text, str):
        return None

    m1 = Q1_RE.search(text)
    m2 = Q2_RE.search(text)
    m3 = Q3_RE.search(text)

    if not (m1 and m2 and m3):
        return None

    # Strict order enforcement: Q1 before Q2 before Q3
    if not (m1.start() < m2.start() < m3.start()):
        return None

    q1_text = text[m1.end():m2.start()].strip()
    q2_text = text[m2.end():m3.start()].strip()
    q3_text = text[m3.end():].strip()

    if not q1_text or not q2_text or not q3_text:
        return None

    return {
        "q1": q1_text,
        "q2": q2_text,
        "q3": q3_text,
    }


def extract_norm_text(text: str) -> str:
    """Extract clean norm text from Question 3 response, trimming conversational preamble."""
    lines = text.strip().splitlines()
    heading_or_bullet_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("- ") or stripped.startswith("* "):
            heading_or_bullet_idx = i
            break
        if stripped.startswith("```"):
            heading_or_bullet_idx = i
            break

    if heading_or_bullet_idx is not None and heading_or_bullet_idx > 0:
        preamble = "\n".join(lines[:heading_or_bullet_idx]).lower()
        if any(w in preamble for w in ("propose", "add", "norms.md", "change", "following", "rule", "norm")):
            extracted = "\n".join(lines[heading_or_bullet_idx:]).strip()
            if extracted.startswith("```") and extracted.endswith("```"):
                extracted_lines = extracted.splitlines()[1:-1]
                extracted = "\n".join(extracted_lines).strip()
            return extracted

    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        inner = "\n".join(stripped.splitlines()[1:-1]).strip()
        return inner

    return stripped


def evaluate_question_3(q3_text: str) -> dict:
    """Evaluate Question 3 to determine whether it provides a norm change or explicit no-change.

    Returns dict with keys:
    - valid (bool)
    - outcome ("norm_change", "no_change", or "invalid")
    - norm_change (str or None)
    - reason (str)
    """
    cleaned = q3_text.strip()
    if not cleaned or len(cleaned) < 5:
        return {
            "valid": False,
            "outcome": "invalid",
            "norm_change": None,
            "reason": "Question 3 answer is empty or too short.",
        }

    lower_cleaned = cleaned.lower()
    if lower_cleaned in DODGE_PHRASES:
        return {
            "valid": False,
            "outcome": "invalid",
            "norm_change": None,
            "reason": f"Bare non-answer or dodge {cleaned!r} is not accepted. "
                      "Must produce a concrete norm change or an explicit 'no change, and here is why'.",
        }

    # Check for "no change" branch
    is_no_change_signal = bool(re.search(
        r"(?i)\bno\s+change(?:s)?\b|\bchange\s+nothing\b|\bno\s+norm\s+change\b|\bkeep\s+current\s+norms\b",
        cleaned
    ))

    if is_no_change_signal:
        has_here_is_why = bool(re.search(r"(?i)here\s+is\s+why", cleaned))
        words = cleaned.split()
        has_rationale = len(words) >= 7 and any(
            w in lower_cleaned for w in ("because", "since", "as", "why", "current", "sufficient", "working", "reason", "well")
        )
        if has_here_is_why or has_rationale:
            return {
                "valid": True,
                "outcome": "no_change",
                "norm_change": None,
                "reason": cleaned,
            }
        else:
            return {
                "valid": False,
                "outcome": "invalid",
                "norm_change": None,
                "reason": "Explicit 'no change' requires substantive reasoning ('and here is why'), not a bare refusal.",
            }

    # Proposing a change
    norm_candidate = extract_norm_text(cleaned)
    if len(norm_candidate) < 10 or norm_candidate.lower() in DODGE_PHRASES:
        return {
            "valid": False,
            "outcome": "invalid",
            "norm_change": None,
            "reason": f"Proposed norm change {norm_candidate!r} is too brief or insubstantial.",
        }

    return {
        "valid": True,
        "outcome": "norm_change",
        "norm_change": norm_candidate,
        "reason": "Concrete norm change proposed.",
    }


def get_work_stats(team_dir: Path | str) -> dict:
    """Return counts of work records (events, reviews, bus messages)."""
    team_dir = Path(team_dir)
    stats = {"events": 0, "reviews": 0, "bus": 0}
    for key, filename in [("events", "events.jsonl"), ("reviews", "reviews.jsonl"), ("bus", "bus.jsonl")]:
        file_path = team_dir / filename
        if file_path.exists():
            try:
                count = 0
                with file_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            count += 1
                stats[key] = count
            except OSError:
                pass
    return stats


def check_new_work(team_dir: Path | str) -> tuple[bool, str]:
    """Check whether there is new work recorded since the last retrospective.

    Returns (has_new_work, reason).
    Refuses if no work exists at all or if no events, reviews, or bus messages
    have been added since the last retro recorded in retro_state.json.
    """
    team_dir = Path(team_dir)
    curr = get_work_stats(team_dir)
    total_curr = curr["events"] + curr["reviews"] + curr["bus"]
    if total_curr == 0:
        return False, "no work has been recorded for this team yet (0 events, 0 reviews, 0 bus messages)"

    state_file = team_dir / "retro_state.json"
    if not state_file.exists():
        return True, f"initial retrospective: {curr['events']} events, {curr['reviews']} reviews, {curr['bus']} bus messages"

    try:
        prev = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True, "corrupted or unreadable previous retro state"

    prev_events = prev.get("events", 0)
    prev_reviews = prev.get("reviews", 0)
    prev_bus = prev.get("bus", 0)

    delta_events = curr["events"] - prev_events
    delta_reviews = curr["reviews"] - prev_reviews
    delta_bus = curr["bus"] - prev_bus

    if delta_events > 0 or delta_reviews > 0 or delta_bus > 0:
        parts = []
        if delta_events > 0:
            parts.append(f"{delta_events} new event(s)")
        if delta_reviews > 0:
            parts.append(f"{delta_reviews} new review(s)")
        if delta_bus > 0:
            parts.append(f"{delta_bus} new bus message(s)")
        return True, f"new work detected: {', '.join(parts)}"

    return False, (
        f"no new work since last retrospective on {prev.get('last_retro_ts', 'unknown')} "
        f"(events: {curr['events']}, reviews: {curr['reviews']}, bus: {curr['bus']})"
    )


def save_retro_state(team_dir: Path | str) -> Path:
    """Save current work stats to retro_state.json after a successful retrospective."""
    team_dir = Path(team_dir)
    curr = get_work_stats(team_dir)
    state = {
        "last_retro_ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "events": curr["events"],
        "reviews": curr["reviews"],
        "bus": curr["bus"],
    }
    state_file = team_dir / "retro_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state_file


def format_retro_record(rep: dict, max_chars: int | None = None) -> str:
    """Format durable history record into a concise summary for retro participants."""
    if max_chars is None:
        max_chars = config.RETRO_MAX_TRANSCRIPT_CHARS

    lines = []
    lines.append("## Work Record Summary")
    totals = rep.get("totals", {})
    cost_str = f"${totals['cost_usd']:.4f}" if totals.get("cost_usd") is not None else "n/a"
    tokens_str = str(totals["total_tokens"]) if totals.get("total_tokens") is not None else "n/a"
    lines.append(f"- Episodes: {totals.get('episodes', 0)} ({totals.get('reviewed_episodes', 0)} reviewed)")
    lines.append(f"- Total turns: {totals.get('turns', 0)} (failures: {totals.get('failures', 0)})")
    lines.append(f"- Cost: {cost_str} | Total tokens: {tokens_str}")

    episodes = rep.get("episodes", [])
    if episodes:
        lines.append("\n### Recent Episodes")
        for ep in episodes[-5:]:
            dur = f"{ep['duration_s']:.1f}s" if ep.get("duration_s") is not None else "n/a"
            lines.append(
                f"- Episode {ep.get('episode')}: {ep.get('turns')} turns, "
                f"stopped: {ep.get('stopped_reason')}, reviewed: {ep.get('reviewed')}, "
                f"duration: {dur}"
            )

    reviews = rep.get("reviews", [])
    if reviews:
        lines.append("\n### Reviews & Verdicts")
        for r in reviews[-5:]:
            lines.append(f"- [{r.get('verdict')}] by {r.get('reviewer')} on {r.get('what')}: {r.get('findings', '')}")
            if r.get("cases_tried"):
                lines.append(f"  Cases tried: {r.get('cases_tried')}")
    else:
        lines.append("\n### Reviews & Verdicts")
        lines.append("- No reviews recorded.")

    bus_traffic = rep.get("bus_traffic", {})
    if bus_traffic.get("top_pairs"):
        lines.append("\n### Communication Patterns")
        for pair in bus_traffic["top_pairs"][:5]:
            lines.append(f"- {pair['sender']} -> {pair['recipient']}: {pair['count']} messages")

    text = "\n".join(lines)
    if len(text) > max_chars:
        trunc_msg = "\n\n[... work record truncated to fit character budget ...]"
        keep_len = max(0, max_chars - len(trunc_msg))
        text = text[:keep_len].rstrip() + trunc_msg
    return text


def record_review(team_dir: Path | str, reviewer: str, what: str, verdict: str,
                  cases_tried: list[str] | str, findings: str = "") -> dict:
    """Record a review entry in reviews.jsonl matching mcp_bus._record_review schema."""
    team_dir = Path(team_dir)
    reviews_file = team_dir / "reviews.jsonl"
    reviews_file.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(cases_tried, str):
        cases = [cases_tried]
    elif isinstance(cases_tried, list):
        cases = [str(c) for c in cases_tried]
    else:
        cases = [str(cases_tried)]
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "reviewer": reviewer,
        "what": what.strip(),
        "verdict": verdict,
        "cases_tried": cases,
        "findings": findings.strip() if isinstance(findings, str) else str(findings),
    }
    with reviews_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def format_retro_report(leader: str, participants: list[str], is_accountable: bool,
                        q1: str, q2: str, q3: str, outcome: str,
                        norm_change: str | None = None,
                        error: str | None = None,
                        raw_leader_output: str | None = None,
                        reflections: dict[str, str] | None = None,
                        capped_note: str | None = None) -> str:
    """Format the retrospective markdown document."""
    lines = []
    lines.append(f"# Team Retrospective ({time.strftime('%Y-%m-%d %H:%M:%S')})")
    lines.append(f"\n**Leader:** {leader}")
    parts_str = ", ".join(participants) if participants else "(solo retro)"
    lines.append(f"**Participants:** {parts_str}")
    if capped_note:
        lines.append(f"**Participation Note:** {capped_note}")

    if is_accountable:
        lines.append(f"\n> [!NOTE]\n> Note on facilitation: {TENSION_ACCOUNTABLE}")

    lines.append("\n## 1. What went well")
    lines.append(q1)

    lines.append("\n## 2. What did not")
    lines.append(q2)

    lines.append("\n## 3. What should we change")
    lines.append(q3)

    lines.append("\n## Outcome")
    if outcome == "norm_change":
        lines.append(f"**Norm change adopted and recorded:**\n\n```markdown\n{norm_change}\n```")
    elif outcome == "no_change":
        lines.append(f"**No norm change adopted:**\n\n{q3}")
    else:
        lines.append(f"**FAILED:** {error or 'Retrospective failed to produce a valid outcome.'}")
        if raw_leader_output:
            lines.append(f"\n### Raw Output\n```\n{raw_leader_output}\n```")

    if reflections:
        lines.append("\n## Teammate Reflections")
        for agent, text in reflections.items():
            lines.append(f"\n### {agent}")
            lines.append(text)

    return "\n".join(lines)



class Supervisor:
    def __init__(self, agents: list[str], runner, team_dir=None,
                 max_hops: int = 32, poll: float = 1.0, quiet: bool = False,
                 stop_on_answer: bool = True, observer=None,
                 require_review: bool = True, manager: str | None = None):
        self.agents = agents
        self.runner = runner
        self.max_hops = max_hops
        self.poll = poll
        self.quiet = quiet
        self.stop_on_answer = stop_on_answer
        self.require_review = require_review
        self.manager = manager
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

    def _find_manager(self) -> str | None:
        """Find the manager agent: explicit manager, or by role/name in roster."""
        if self.manager and self.manager in self.transports:
            return self.manager
        try:
            roster = roster_lib.load(self.team_dir / "roster.json")
            for entry in roster.get("agents", []):
                name = entry.get("name", "")
                role = entry.get("role", "").lower()
                if ("manager" in role or name.lower() == "manager") and name in self.transports:
                    return name
        except Exception:
            pass
        return None

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
        bounced = False
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
                if self._has_new_approved_review():
                    self.stopped = "the user was answered"
                    break

                mgr = self._find_manager()
                # Rationale for choosing exactly ONE bounce:
                # Bouncing once gives the team an opportunity to complete the review
                # cycle autonomously: the manager is notified that its answer went out
                # without an approved review, allowing it to delegate to qa and obtain
                # an approved verdict in reviews.jsonl before reporting to the user.
                # However, this must be strictly bounded. Allowing unbounded bounces
                # risks an infinite loop (e.g., if a reviewer is missing, repeatedly
                # rejects, or encounters errors, or if the manager repeatedly answers
                # without review), rapidly burning the hop budget and tokens.
                # Bouncing exactly once balances autonomous recovery against runaway
                # resource exhaustion: if the manager answers again still unreviewed,
                # the supervisor terminates the episode and flags it explicitly.
                if self.require_review and mgr and not bounced:
                    bounced = True
                    bus = load_transport("supervisor")
                    try:
                        bus.send(
                            mgr,
                            "Your answer to the user went out without an approved review "
                            "recorded in reviews.jsonl. Work must be verified with an "
                            "approved review before reporting to the user.",
                        )
                    finally:
                        bus.close()
                    # Reset the baseline so subsequent turns while obtaining a review
                    # are not mistaken for a new answer to the user.
                    self._user_mail_at_start = self._user_mail_count()
                    self._log(f"[supervisor] unreviewed answer bounced back to {mgr}")
                    continue

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

    def _is_leader_accountable(self, leader: str) -> bool:
        """Check if the retro leader holds accountability for shipping/managing."""
        if not leader:
            return False
        leader_lower = leader.lower()
        if leader_lower in ("tpm", "manager"):
            return True
        if self.manager and leader_lower == self.manager.lower():
            return True
        mgr = self._find_manager()
        if mgr and leader_lower == mgr.lower():
            return True
        try:
            roster = roster_lib.load(self.team_dir / "roster.json")
            for entry in roster.get("agents", []):
                if entry.get("name", "").lower() == leader_lower:
                    role = entry.get("role", "").lower()
                    if any(k in role for k in ("coordinate", "accountab", "ship", "manage", "lead")):
                        return True
        except Exception:
            pass
        return False

    def _apply_norm_change(self, norm_change: str) -> Path:
        """Seed NORMS.md if absent, and append the proposed norm change."""
        norms_path = persona.seed_norms(team_dir=self.team_dir)
        existing = norms_path.read_text(encoding="utf-8")
        clean_norm = norm_change.strip()
        new_content = f"{existing.rstrip()}\n\n{clean_norm}\n"
        norms_path.write_text(new_content, encoding="utf-8")
        return norms_path

    def _record_norm_review(self, reviewer: str, norm_change: str) -> dict:
        """Record an approved review in reviews.jsonl for the adopted norm change."""
        first_line = norm_change.strip().splitlines()[0].lstrip("#*- ").strip()
        what = f"NORMS.md: {first_line[:80]}" if first_line else "NORMS.md: retrospective norm change"
        return record_review(
            team_dir=self.team_dir,
            reviewer=reviewer,
            what=what,
            verdict="approved",
            cases_tried=["retrospective consensus", "evaluated against work record"],
            findings=f"Adopted in retro led by {reviewer}: {norm_change.strip()}",
        )

    def _select_retro_participants(self, leader: str,
                                   max_participants: int | None = None) -> tuple[list[str], list[str]]:
        """Select retro participants, capping count and ranking by recent activity."""
        if max_participants is None:
            max_participants = config.RETRO_MAX_PARTICIPANTS

        candidates = [a for a in self.agents if a != leader]
        if len(candidates) <= max_participants:
            return candidates, []

        # Count activity per candidate from durable history:
        # turns (from events.jsonl) * 3 + bus messages * 1 + reviews * 2
        scores = {a: 0 for a in candidates}

        # 1. Events
        events_file = self.team_dir / "events.jsonl"
        if events_file.exists():
            try:
                for line in events_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            ev = json.loads(line)
                            ag = ev.get("agent")
                            if ag in scores:
                                scores[ag] += 3
                        except json.JSONDecodeError:
                            pass
            except OSError:
                pass

        # 2. Bus messages
        bus_file = self.team_dir / "bus.jsonl"
        if bus_file.exists():
            try:
                for line in bus_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            msg = json.loads(line)
                            sender = msg.get("from")
                            recip = msg.get("to")
                            if sender in scores:
                                scores[sender] += 1
                            if recip in scores and recip != sender:
                                scores[recip] += 1
                        except json.JSONDecodeError:
                            pass
            except OSError:
                pass

        # 3. Reviews
        reviews_file = self.team_dir / "reviews.jsonl"
        if reviews_file.exists():
            try:
                for line in reviews_file.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            rev = json.loads(line)
                            reviewer = rev.get("reviewer")
                            if reviewer in scores:
                                scores[reviewer] += 2
                        except json.JSONDecodeError:
                            pass
            except OSError:
                pass

        # Rank candidates by (score descending, candidate order)
        ranked = sorted(candidates, key=lambda a: (scores.get(a, 0), -candidates.index(a)), reverse=True)
        selected_set = set(ranked[:max_participants])

        # Preserve original roster order among selected
        selected = [a for a in candidates if a in selected_set]
        omitted = [a for a in candidates if a not in selected_set]
        return selected, omitted

    def retro(self, leader: str = "tpm",
              max_participants: int | None = None,
              max_transcript_chars: int | None = None,
              force: bool = False) -> RetroResult:
        """Run a structured retrospective over completed work."""
        if leader not in self.agents:
            err = f"retro leader {leader!r} is not on the team roster: {', '.join(self.agents)}"
            report_text = format_retro_report(
                leader=leader,
                participants=[],
                is_accountable=False,
                q1="(none)",
                q2="(none)",
                q3="(none)",
                outcome="invalid",
                error=err,
            )
            retro_file = self.team_dir / "retro.md"
            try:
                retro_file.write_text(report_text, encoding="utf-8")
            except OSError:
                pass
            return RetroResult(report_text, success=False, outcome="invalid", leader=leader, retro_file=retro_file, error=err)

        if not force:
            has_new_work, reason = check_new_work(self.team_dir)
            if not has_new_work:
                err = f"Retrospective refused: {reason}. Use --force to run anyway."
                if not self.quiet:
                    self._log(f"  → {err}")
                return RetroResult(
                    err,
                    success=False,
                    outcome="refused",
                    leader=leader,
                    retro_file=self.team_dir / "retro.md",
                    error=err,
                    refused=True,
                )

        is_accountable = self._is_leader_accountable(leader)
        tension_note = f"Note on facilitation: {TENSION_ACCOUNTABLE}\n" if is_accountable else ""

        rep = generate_report(self.team_dir)
        record_text = format_retro_record(rep, max_chars=max_transcript_chars)

        participants, omitted = self._select_retro_participants(leader, max_participants=max_participants)
        capped_note = None
        if omitted:
            capped_note = f"Participation capped at {len(participants)} agents (omitted inactive: {', '.join(omitted)})."
            if not self.quiet:
                self._log(f"  → {capped_note}")

        # Teammate reflections turn
        reflections = {}
        for agent in participants:
            prompt = RETRO_PARTICIPANT_PROMPT.format(
                leader=leader,
                tension_note=tension_note,
                record=record_text,
            )
            self._log(f"  → retro reflection: waking {agent}")
            t0 = time.monotonic()
            try:
                reply = self.runner.wake(agent, prompt)
            except Exception as e:
                dur = time.monotonic() - t0
                reply = f"[error: {type(e).__name__}: {e}]"
                try:
                    cid = getattr(self.runner, "conversation_id", lambda a: "")(agent) or ""
                    self.observer.record_failure(agent, cid, reply, duration_s=dur)
                except Exception:
                    pass

            if len(reply) > config.RETRO_MAX_REFLECTION_CHARS:
                trunc_msg = "\n[... reflection truncated to character cap ...]"
                keep_chars = max(0, config.RETRO_MAX_REFLECTION_CHARS - len(trunc_msg))
                reply = reply[:keep_chars].rstrip() + trunc_msg

            reflections[agent] = reply
            if not self.quiet:
                first = reply.strip().splitlines()[0] if reply.strip() else ""
                self._log(f"    {agent}: {first[:120]}")

        # Leader synthesis turn
        if reflections:
            reflections_text = "\n\n".join(f"### {ag}\n{txt}" for ag, txt in reflections.items())
            max_refl = max_transcript_chars if max_transcript_chars is not None else config.RETRO_MAX_TRANSCRIPT_CHARS
            if len(reflections_text) > max_refl:
                trunc_msg = "\n\n[... reflections truncated to character budget ...]"
                reflections_text = reflections_text[:max(0, max_refl - len(trunc_msg))].rstrip() + trunc_msg
        else:
            reflections_text = "(Solo retro: no other participants on roster)"

        leader_prompt = RETRO_LEADER_PROMPT.format(
            tension_note=tension_note,
            record=record_text,
            reflections=reflections_text,
        )
        self._log(f"  → retro leader synthesis: waking {leader}")
        t0 = time.monotonic()
        try:
            leader_reply = self.runner.wake(leader, leader_prompt)
        except Exception as e:
            dur = time.monotonic() - t0
            leader_reply = f"[error: {type(e).__name__}: {e}]"
            try:
                cid = getattr(self.runner, "conversation_id", lambda a: "")(leader) or ""
                self.observer.record_failure(leader, cid, leader_reply, duration_s=dur)
            except Exception:
                pass

        if not self.quiet:
            first = leader_reply.strip().splitlines()[0] if leader_reply.strip() else ""
            self._log(f"    {leader}: {first[:120]}")

        retro_file = self.team_dir / "retro.md"

        sections = parse_retro_sections(leader_reply)
        if not sections:
            err = "Leader response failed to provide the three required sections in order (1. What went well, 2. What did not, 3. What should we change)."
            report_text = format_retro_report(
                leader=leader,
                participants=participants,
                is_accountable=is_accountable,
                q1="(missing or invalid)",
                q2="(missing or invalid)",
                q3="(missing or invalid)",
                outcome="invalid",
                error=err,
                raw_leader_output=leader_reply,
                reflections=reflections,
                capped_note=capped_note,
            )
            try:
                retro_file.write_text(report_text, encoding="utf-8")
            except OSError:
                pass
            return RetroResult(report_text, success=False, outcome="invalid", leader=leader, retro_file=retro_file, error=err)

        q1 = sections["q1"]
        q2 = sections["q2"]
        q3 = sections["q3"]

        eval_res = evaluate_question_3(q3)
        if not eval_res["valid"]:
            err = eval_res["reason"]
            report_text = format_retro_report(
                leader=leader,
                participants=participants,
                is_accountable=is_accountable,
                q1=q1,
                q2=q2,
                q3=q3,
                outcome="invalid",
                error=err,
                raw_leader_output=leader_reply,
                reflections=reflections,
                capped_note=capped_note,
            )
            try:
                retro_file.write_text(report_text, encoding="utf-8")
            except OSError:
                pass
            return RetroResult(report_text, success=False, outcome="invalid", leader=leader, retro_file=retro_file, error=err)

        outcome = eval_res["outcome"]
        norm_change = eval_res.get("norm_change")

        if outcome == "norm_change" and norm_change:
            self._apply_norm_change(norm_change)
            self._record_norm_review(leader, norm_change)

        save_retro_state(self.team_dir)

        report_text = format_retro_report(
            leader=leader,
            participants=participants,
            is_accountable=is_accountable,
            q1=q1,
            q2=q2,
            q3=q3,
            outcome=outcome,
            norm_change=norm_change,
            reflections=reflections,
            capped_note=capped_note,
        )
        try:
            retro_file.write_text(report_text, encoding="utf-8")
        except OSError:
            pass

        return RetroResult(
            report_text,
            success=True,
            outcome=outcome,
            norm_change=norm_change,
            leader=leader,
            retro_file=retro_file,
        )

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


def retro(leader: str = "tpm", runner=None, team_dir=None,
          max_participants: int | None = None,
          max_transcript_chars: int | None = None,
          force: bool = False) -> RetroResult:
    """Run a structured retrospective led by `leader` over recorded history."""
    if team_dir is None:
        env = os.environ.get("AGYTEAM_TEAM_DIR")
        if env:
            team_dir = Path(env)
        else:
            team_dir = scope.load().team_dir()
    else:
        team_dir = Path(team_dir)
    team_dir = team_dir.resolve()

    try:
        agents = _agents_from_roster(team_dir)
    except Exception:
        agents = []

    r = runner or runner_lib.load()
    sup = Supervisor(agents, r, team_dir=team_dir)
    try:
        return sup.retro(
            leader=leader,
            max_participants=max_participants,
            max_transcript_chars=max_transcript_chars,
            force=force,
        )
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



def _check_unread_mail(agent: str, team_dir: Path) -> tuple[bool | None, int | None]:
    """Check if an agent currently holds unread mail (peek only, non-destructive)."""
    try:
        t = load_transport(agent, config={"team_dir": str(team_dir)})
        try:
            peeked = t.peek()
            if peeked is not None:
                return (len(peeked) > 0, len(peeked))
        finally:
            t.close()
    except Exception:
        pass
    inbox_file = team_dir / "inbox" / f"{agent}.jsonl"
    if inbox_file.exists():
        try:
            lines = [l for l in inbox_file.read_text().splitlines() if l.strip()]
            return (len(lines) > 0, len(lines))
        except OSError:
            return (None, None)
    return (False, 0)


def generate_report(team_dir: Path | str | None = None) -> dict:
    """Generate structured team activity and status report from recorded history.

    Strictly read-only and deterministic: no model calls or runner wakes.
    Unmeasured values are reported as None (null in JSON), never as invented zeros.
    """
    if team_dir is None:
        env = os.environ.get("AGYTEAM_TEAM_DIR")
        if env:
            team_dir = Path(env)
        else:
            team_dir = scope.load().team_dir()
    else:
        team_dir = Path(team_dir)
    team_dir = team_dir.resolve()

    events_file = team_dir / "events.jsonl"
    reviews_file = team_dir / "reviews.jsonl"
    bus_file = team_dir / "bus.jsonl"
    conv_file = team_dir / "conversations.json"
    roster_file = team_dir / "roster.json"

    events = []
    if events_file.exists():
        try:
            for line in events_file.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass

    reviews = []
    if reviews_file.exists():
        try:
            for line in reviews_file.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        reviews.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass

    bus_msgs = []
    if bus_file.exists():
        try:
            for line in bus_file.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        bus_msgs.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        except OSError:
            pass

    conversations = {}
    if conv_file.exists():
        try:
            conversations = json.loads(conv_file.read_text())
            if not isinstance(conversations, dict):
                conversations = {}
        except (OSError, json.JSONDecodeError):
            pass

    roster_agents = []
    if roster_file.exists():
        try:
            doc = json.loads(roster_file.read_text())
            roster_agents = [a.get("name") for a in doc.get("agents", []) if a.get("name")]
        except (OSError, json.JSONDecodeError):
            pass

    inbox_agents = []
    inbox_dir = team_dir / "inbox"
    if inbox_dir.exists():
        try:
            for p in inbox_dir.glob("*.jsonl"):
                stem = p.stem
                if stem != "user":
                    inbox_agents.append(stem)
        except OSError:
            pass

    has_history = bool(events or reviews or bus_msgs or conversations)
    if not has_history:
        return {
            "has_history": False,
            "team_dir": str(team_dir),
            "episodes": [],
            "agents": {},
            "totals": {
                "episodes": 0,
                "reviewed_episodes": 0,
                "turns": 0,
                "failures": 0,
                "duration_s": None,
                "input_tokens": None,
                "output_tokens": None,
                "cache_read_tokens": None,
                "total_tokens": None,
                "cost_usd": None,
            },
        }

    all_agent_names = set(roster_agents)
    for ev in events:
        ag = ev.get("agent")
        if ag and ag != "user":
            all_agent_names.add(ag)
    for ag in conversations.keys():
        if ag and ag != "user":
            all_agent_names.add(ag)
    for b in bus_msgs:
        for party in (b.get("from"), b.get("to")):
            if party and party != "user":
                all_agent_names.add(party)
    for ag in inbox_agents:
        all_agent_names.add(ag)

    approved_reviews_count = sum(1 for r in reviews if r.get("verdict") == "approved")

    raw_episodes = [ev for ev in events if ev.get("event") == "episode"]
    episodes_data = []
    for idx, ep in enumerate(raw_episodes, 1):
        ep_reviewed = ep.get("reviewed")
        if ep_reviewed is None:
            ep_ts = ep.get("ts")
            if ep_ts and any(r.get("verdict") == "approved" and r.get("ts", "") <= ep_ts for r in reviews):
                ep_reviewed = True
            elif approved_reviews_count > 0:
                ep_reviewed = True
            else:
                ep_reviewed = False
        else:
            ep_reviewed = bool(ep_reviewed)

        episodes_data.append({
            "episode": idx,
            "ts": ep.get("ts"),
            "turns": ep.get("turns"),
            "stopped_reason": ep.get("stopped_reason") or "unknown",
            "reviewed": ep_reviewed,
            "duration_s": ep.get("duration_s"),
        })

    from .observer import calculate_cost  # returns None when the model is unpriced

    agents_data = {}
    total_turns = 0
    total_failures = 0
    total_duration = 0.0
    total_in_tokens = 0
    total_out_tokens = 0
    total_cache_tokens = 0
    total_toks = 0
    total_cost = 0.0
    has_any_tokens = False
    has_any_duration = False

    for agent in sorted(all_agent_names):
        ag_turns = [ev for ev in events if ev.get("event") == "turn" and ev.get("agent") == agent]
        ag_fails = [ev for ev in events if ev.get("event") == "failure" and ev.get("agent") == agent]

        turn_count = len(ag_turns)
        fail_count = len(ag_fails)
        total_turns += turn_count
        total_failures += fail_count

        ag_durations = [ev.get("duration_s") for ev in ag_turns if ev.get("duration_s") is not None]
        if ag_durations:
            ag_dur = round(sum(ag_durations), 2)
            total_duration += ag_dur
            has_any_duration = True
        else:
            ag_dur = None

        token_turns = [
            ev for ev in ag_turns
            if ev.get("input_tokens") is not None or ev.get("output_tokens") is not None or ev.get("total_tokens") is not None
        ]
        unmeasured_turns = turn_count - len(token_turns)

        if token_turns:
            has_any_tokens = True
            in_tok = sum(ev.get("input_tokens") or 0 for ev in token_turns if ev.get("input_tokens") is not None)
            out_tok = sum(ev.get("output_tokens") or 0 for ev in token_turns if ev.get("output_tokens") is not None)
            cache_tok = sum(ev.get("cache_read_tokens") or 0 for ev in token_turns if ev.get("cache_read_tokens") is not None)
            tot_tok = sum(ev.get("total_tokens") or ((ev.get("input_tokens") or 0) + (ev.get("output_tokens") or 0)) for ev in token_turns)

            # calculate_cost returns None for a model we have no price for.
            # Sum what we can price and count what we cannot, so the report can
            # say "$X plus N unpriced turns" instead of quietly implying that
            # unpriced work was free.
            priced = [calculate_cost(ev.get("input_tokens"), ev.get("output_tokens"),
                                     ev.get("model")) for ev in token_turns]
            unpriced = sum(1 for c in priced if c is None)
            cost_usd = round(sum(c for c in priced if c is not None), 4)

            total_in_tokens += in_tok
            total_out_tokens += out_tok
            total_cache_tokens += cache_tok
            total_toks += tot_tok
            total_cost += cost_usd
        else:
            in_tok = None
            out_tok = None
            cache_tok = None
            tot_tok = None
            cost_usd = None
            unpriced = 0

        unread_mail, unread_count = _check_unread_mail(agent, team_dir)

        ag_dict = {
            "turns": turn_count,
            "failures": fail_count,
            "duration_s": ag_dur,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_read_tokens": cache_tok,
            "total_tokens": tot_tok,
            "cost_usd": cost_usd,
            "unpriced_turns": unpriced,
            "unread_mail": unread_mail,
            "unread_count": unread_count,
            "conversation": conversations.get(agent),
        }
        if unmeasured_turns > 0:
            ag_dict["unmeasured_turns"] = unmeasured_turns
        agents_data[agent] = ag_dict

    totals_data = {
        "episodes": len(episodes_data),
        "reviewed_episodes": sum(1 for ep in episodes_data if ep.get("reviewed")),
        "turns": total_turns,
        "failures": total_failures,
        "duration_s": round(total_duration, 2) if has_any_duration else None,
        "input_tokens": total_in_tokens if has_any_tokens else None,
        "output_tokens": total_out_tokens if has_any_tokens else None,
        "cache_read_tokens": total_cache_tokens if has_any_tokens else None,
        "total_tokens": total_toks if has_any_tokens else None,
        "cost_usd": round(total_cost, 4) if has_any_tokens else None,
    }

    report = {
        "has_history": True,
        "team_dir": str(team_dir),
        "episodes": episodes_data,
        "agents": agents_data,
        "totals": totals_data,
    }
    if reviews:
        report["reviews"] = [
            {
                "ts": r.get("ts"),
                "reviewer": r.get("reviewer"),
                "what": r.get("what"),
                "verdict": r.get("verdict"),
            }
            for r in reviews
        ]
    return report


def format_report(data: dict) -> str:
    """Format team status report into a clean, human-readable terminal string."""
    if not data.get("has_history", True) or (not data.get("episodes") and not data.get("agents")):
        return "(no team history recorded)"

    lines = ["=== Team Status & Activity Report ==="]
    td = data.get("team_dir")
    if td:
        lines.append(f"Team directory: {td}")

    episodes = data.get("episodes", [])
    lines.append("\nEpisodes:")
    if not episodes:
        lines.append("  (no episodes recorded)")
    else:
        reviewed_cnt = sum(1 for ep in episodes if ep.get("reviewed"))
        lines.append(f"  Total episodes: {len(episodes)}")
        lines.append(f"  Reviewed episodes: {reviewed_cnt}/{len(episodes)}")
        for ep in episodes:
            num = ep.get("episode", "?")
            t_val = ep.get("turns")
            turns_str = f"{t_val} turns" if t_val is not None else "unknown turns"
            stopped = ep.get("stopped_reason") or "unknown"
            rev_label = "reviewed" if ep.get("reviewed") else "unreviewed"
            dur_val = ep.get("duration_s")
            dur_str = f" in {dur_val:.1f}s" if dur_val is not None else ""
            lines.append(f"  #{num}: {turns_str}, stopped: {stopped} ({rev_label}){dur_str}")

    agents = data.get("agents", {})
    lines.append("\nPer-Agent Status:")
    if not agents:
        lines.append("  (no agents active)")
    else:
        lines.append(f"  {'Agent':<12} {'Turns':<7} {'Duration':<10} {'Tokens (In / Out / Cached / Total)':<38} {'Est. Cost':<11} {'Unread Mail':<12}")
        lines.append("  " + "-" * 92)
        for name, ag in sorted(agents.items()):
            dur_val = ag.get("duration_s")
            dur = f"{dur_val:.1f}s" if dur_val is not None else "unknown"

            in_t = ag.get("input_tokens")
            if in_t is not None:
                tok_str = f"{in_t} / {ag.get('output_tokens', 0)} / {ag.get('cache_read_tokens', 0)} / {ag.get('total_tokens', 0)}"
                unmeas = ag.get("unmeasured_turns", 0)
                if unmeas > 0:
                    tok_str += f" (+{unmeas} unk)"
            else:
                tok_str = "unknown"

            cost_val = ag.get("cost_usd")
            unpriced = ag.get("unpriced_turns") or 0
            if cost_val is None:
                cost_str = "unknown"
            elif unpriced:
                # Never let unpriced work read as free.
                cost_str = f"${cost_val:.4f} +{unpriced}?"
            else:
                cost_str = f"${cost_val:.4f}"

            unread = ag.get("unread_mail")
            unread_cnt = ag.get("unread_count")
            if unread is True:
                unread_str = f"yes ({unread_cnt})" if unread_cnt is not None else "yes"
            elif unread is False:
                unread_str = "no"
            else:
                unread_str = "unknown"

            lines.append(f"  {name:<12} {ag.get('turns', 0):<7} {dur:<10} {tok_str:<38} {cost_str:<11} {unread_str:<12}")

    reviews = data.get("reviews", [])
    if reviews:
        lines.append("\nReviews:")
        appr = sum(1 for r in reviews if r.get("verdict") == "approved")
        lines.append(f"  Total reviews: {len(reviews)} ({appr} approved, {len(reviews) - appr} other)")
        latest = reviews[-1]
        lines.append(f"  Latest: [{latest.get('reviewer', 'unknown')}: {latest.get('verdict', 'unknown')}] {latest.get('what', '')}")

    totals = data.get("totals", {})
    lines.append("\nTotals:")
    lines.append(f"  Episodes:    {totals.get('episodes', 0)} ({totals.get('reviewed_episodes', 0)} reviewed)")
    lines.append(f"  Turns:       {totals.get('turns', 0)}")
    if totals.get("failures", 0) > 0:
        lines.append(f"  Failures:    {totals.get('failures', 0)}")

    dur_tot = totals.get("duration_s")
    lines.append(f"  Duration:    {dur_tot:.2f}s" if dur_tot is not None else "  Duration:    unknown")

    in_tot = totals.get("input_tokens")
    if in_tot is not None:
        lines.append(f"  Tokens:      {in_tot} input, {totals.get('output_tokens', 0)} output, "
                     f"{totals.get('cache_read_tokens', 0)} cached ({totals.get('total_tokens', 0)} total)")
    else:
        lines.append("  Tokens:      unknown")

    cost_tot = totals.get("cost_usd")
    if cost_tot is not None:
        lines.append(f"  Est. Cost:   ${cost_tot:.4f}")
    else:
        lines.append("  Est. Cost:   unknown")

    return "\n".join(lines)


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
    ap.add_argument("--retro", action="store_true",
                    help="Run structured retrospective over recent work, then exit")
    ap.add_argument("--retro-leader", metavar="AGENT", default="tpm",
                    help="Agent leading the retrospective (default: tpm)")
    ap.add_argument("--retro-max-participants", type=int, default=None,
                    help="Maximum participants in retro (default: config.RETRO_MAX_PARTICIPANTS)")
    ap.add_argument("--retro-max-transcript", type=int, default=None,
                    help="Maximum characters of history transcript in retro prompts (default: config.RETRO_MAX_TRANSCRIPT_CHARS)")
    ap.add_argument("--retro-force", "--force", action="store_true", dest="retro_force",
                    help="Force retrospective even if no new work has been recorded")
    ap.add_argument("--report", action="store_true",
                    help="Show team status and activity report from history, then exit")
    ap.add_argument("--report-json", action="store_true",
                    help="Output team status and activity report as JSON, then exit")
    ap.add_argument("--team-dir", default=None)
    ap.add_argument("--max-hops", type=int, default=32)
    ap.add_argument("--poll", type=float, default=1.0,
                    help="Seconds between checks when idle (daemon mode)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-stop-on-answer", action="store_true",
                    help="Keep dispatching after the user is answered "
                         "(default: an answer to the user ends the episode)")
    ap.add_argument("--no-require-review", action="store_true",
                    help="Do not bounce unreviewed answers back to the manager")
    ap.add_argument("--manager", metavar="AGENT", default=None,
                    help="Agent acting as manager (default: auto-detect from roster)")
    args = ap.parse_args(argv)

    team_dir = Path(args.team_dir) if args.team_dir else scope.load().team_dir()
    os.environ["AGYTEAM_TEAM_DIR"] = str(team_dir)
    if team_dir.name == "team":
        os.environ["AGYTEAM_DURABLE_DIR"] = str(team_dir.parent)

    if args.report_json:
        rep = generate_report(team_dir)
        print(json.dumps(rep, indent=2))
        return

    if args.report:
        rep = generate_report(team_dir)
        print(format_report(rep))
        return

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
                     stop_on_answer=not args.no_stop_on_answer,
                     require_review=not args.no_require_review,
                     manager=args.manager)
    try:
        if args.retro:
            if args.retro_leader not in agents:
                sys.exit(f"unknown retro leader {args.retro_leader!r}; roster has: {', '.join(agents)}")
            res = sup.retro(
                leader=args.retro_leader,
                max_participants=args.retro_max_participants,
                max_transcript_chars=args.retro_max_transcript,
                force=args.retro_force,
            )
            print(str(res))
            if not res.success:
                sys.exit(1)
            return
        elif args.distill:
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
