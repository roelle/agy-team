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
import zlib
from pathlib import Path

from . import config
from . import filelock
from . import heartbeat
from . import memory as memory_lib


def repetition_ratio(text: str) -> float:
    """How repetitive a turn's output is, as a zlib compression ratio.

    This is the health signal the token count is not. Real agent output across
    188 messages measured 1.46x-3.25x; a model looping on itself compresses
    orders of magnitude harder. Short text compresses badly for reasons that
    have nothing to do with health, so anything small is reported as fine.
    """
    raw = (text or "").encode("utf-8", "replace")
    if len(raw) < 2000:
        return 1.0
    return len(raw) / max(len(zlib.compress(raw, 6)), 1)


def _rewrite(path: Path, text: str | bytes) -> None:
    """Replace `path` whole, so a concurrent reader sees old or new, never half."""
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with (os.fdopen(fd, "wb") if isinstance(text, bytes)
              else os.fdopen(fd, "w", encoding="utf-8")) as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def supervisor_running(team_dir: Path | str) -> dict | None:
    """The live supervisor's heartbeat, or None; see agyteam/heartbeat.py."""
    return heartbeat.running(team_dir)


from . import persona
from . import retro_store
from . import roster as roster_lib
from . import runner as runner_lib
from . import scope
from . import distill_queue
from . import receipts
from . import tasks as tasks_lib
from .transport import Message, wakes
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

PULL_PROMPT = """[wake] {n} message(s) in your inbox: {summary}

Call check_inbox to read them, then act: do the work if it is yours, or
delegate with send_to_teammate.

Send a message only when it carries something the recipient does not already
have — a deliverable, an answer, a question, a blocker, or a correction. Do NOT
send acknowledgements, thanks, "got it", or "standing by" notes; if you must
note that something was seen, send it with kind="ack", which wakes nobody.
Doing nothing is a normal, correct outcome. Anything you want a teammate or the
user to see must go through send_to_teammate; text you write here is seen by
nobody."""

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
Be specific, grounded in the record above, and direct.

Record your answers with the record_retro tool:
record_retro(went_well=..., did_not=..., should_change=...)
That record is what the retrospective reads. On some hosts your reply text
never reaches it, so an answer written only in your reply can be lost."""

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
Be direct, constructive, and grounded in the record.

Record your synthesis with the record_retro tool as well as writing it here:
record_retro(went_well=..., did_not=..., should_change=...)
with your NORMS.md change (or your reasoned "no change") in should_change.
That record is what the retrospective reads. On some hosts your reply text
never reaches it, so a synthesis written only in your reply can be lost."""

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
                  cases_tried: list[str] | str, findings: str = "",
                  kind: str = "norm", author: str | None = None) -> dict:
    """Record a review entry in reviews.jsonl matching mcp_bus._record_review schema.

    `kind` separates governance records from work reviews. This function's
    only caller adopts a norm out of a retrospective: a decision the team
    makes about itself, with no author whose work is being verified. Written
    without a kind it produced `author: null` rows that the self-review
    metric counted as authorless approvals and the review gate accepted as
    evidence that work had been checked -- one code path laundering consensus
    into verification. Anything scoring work reviews must filter on
    kind == "work"; nothing else may be written with it.
    """
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
        "kind": kind,
        "reviewer": reviewer,
        "author": author,
        "what": what.strip(),
        "verdict": verdict,
        "cases_tried": cases,
        "findings": findings.strip() if isinstance(findings, str) else str(findings),
    }
    with reviews_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def resolve_retro_leader(explicit: str | None, agents: list, team_dir: Path,
                         manager: str | None = None) -> str:
    """Who leads the retro: what was asked for, then the roster, then the role
    that is accountable for the outcome.

    The default used to be the literal string "tpm", so a roster that marked
    somebody else `is_retro_leader` was ignored whenever a tpm existed -- the
    flag only took effect if the default named nobody on the roster, which is
    the one case it was not needed for. Two separate teams proposed, unasked,
    that the accountable role should lead instead: the norms a retro produces
    bind the team, and a level-1 manager facilitating a retro on work she
    delegated is grading her own instructions.

    That has a cost, and the report names it rather than hiding it (see
    TENSION_ACCOUNTABLE): a retro led by the person accountable for the outcome
    is a weaker retro. Naming a tension beats picking the facilitator who does
    not have to own the result.
    """
    if explicit:
        return explicit
    try:
        roster = roster_lib.load(Path(team_dir) / "roster.json")
        entries = roster.get("agents", [])
    except Exception:
        entries = []

    for entry in entries:
        name = entry.get("name")
        if name in agents and (entry.get("is_retro_leader")
                               or entry.get("retro_leader")):
            return name
    for entry in entries:
        name = entry.get("name")
        if name in agents and (entry.get("is_principal")
                               or entry.get("principal")
                               or entry.get("is_gatekeeper")
                               or entry.get("gatekeeper")):
            return name
    if manager and manager in agents:
        return manager
    for fallback in ("manager", "tpm"):
        if fallback in agents:
            return fallback
    return agents[0] if agents else ""


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
                 require_review: bool = True, manager: str | None = None,
                 inbox_pull: bool | None = None, inspect_only: bool = False):
        self.agents = agents
        self.inspect_only = inspect_only
        self.runner = runner
        self.max_hops = max_hops
        self.poll = poll
        self.quiet = quiet
        self.stop_on_answer = stop_on_answer
        self.require_review = require_review
        self.manager = manager
        # One resolver, no reconciliation. This used to back-derive
        # AGYTEAM_DURABLE_DIR from AGYTEAM_TEAM_DIR (and vice versa) so that
        # Scopes and the module helpers would agree -- a fix that only held
        # inside this process, and silently wrote env vars its caller never
        # set. scope.team_dir() now honours AGYTEAM_TEAM_DIR directly, so
        # everyone agrees without anyone mutating the environment.
        if team_dir:
            os.environ["AGYTEAM_TEAM_DIR"] = str(Path(team_dir))
        self.team_dir = scope.team_dir(team_dir)
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
        self._purged_agents: set[str] = set()
        self.inbox_pull = bool(inbox_pull if inbox_pull is not None
                               else getattr(runner, "inbox_pull", False))
        self.inflight: dict[str, dict] = {}
        self._backoff: dict[str, float] = {}
        self._fail_streak: dict[str, int] = {}
        self._hard_streak: dict[str, int] = {}
        self._escalated: set[str] = self._load_escalations()
        self._distilled_above: dict[str, str] = {}
        self._heartbeat_at = 0.0
        self._step_lock = threading.RLock()
        # inflight.json is the driving supervisor's: loading it cancels,
        # resumes or writes off every turn in it and saves what is left. It
        # was loaded by every Supervisor built, so `--status` -- built with a
        # runner that resumes nothing -- recorded each live turn as died in
        # a restart and saved an empty ledger over the live supervisor's;
        # `--distill` and a supervisor refused the team did the same. Now
        # only one that can own the team reads it here, and one that
        # cannot leaves it alone and takes it up if it later claims the team.
        self._owns_ledger = not inspect_only and not self._team_driven_elsewhere()
        if self._owns_ledger:
            self._load_ledger()
        # The roster as last accepted, and who has had a turn end since the
        # roster was last checked; see _guard_roster.
        self._roster_path = self.team_dir / "roster.json"
        self._roster_pin = self._read_roster()
        self._roster_history_at = roster_lib.history_size(self._roster_path)
        self._roster_touched: set[str] = set()

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
        """Count approved work reviews recorded in reviews.jsonl.

        Governance records (kind == "norm", written when a retro adopts a
        norm) live in the same file and must not satisfy a gate that asks
        whether work was verified. Rows predating the field carry no kind and
        are counted as work, which is what they were.
        """
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
                    if (data.get("verdict") == "approved"
                            and data.get("kind", "work") == "work"):
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
        """Find the manager agent: explicit manager, or by declared capability / role in roster."""
        if self.manager and self.manager in self.transports:
            return self.manager
        try:
            roster = roster_lib.load(self.team_dir / "roster.json")
            entries = roster.get("agents", [])
            for entry in entries:
                name = entry.get("name", "")
                if name in self.transports:
                    if entry.get("is_principal") or entry.get("principal"):
                        return name
            for entry in entries:
                name = entry.get("name", "")
                if name in self.transports:
                    if entry.get("is_gatekeeper") or entry.get("gatekeeper"):
                        return name
            for entry in entries:
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

    def _sweep_due_tasks(self) -> None:
        """Deliver due task reminders as ordinary mail, before this pass's fetch.

        A reminder earns no special wake path -- it earns a message, so the
        per-agent fetch loop a few lines down delivers it like any other. The
        logic lives in tasks.sweep() rather than here because the supervisor
        is only one of the things that can call it; see that module.
        """
        if not tasks_lib.path(self.team_dir).exists():
            return
        bus = None

        def send(to, content, kind="reminder", task_id=None):
            nonlocal bus                # opened only if something is due
            if bus is None:
                bus = load_transport("supervisor")
            return bus.send_kind(to, content, kind=kind, task_id=task_id)

        try:
            tasks_lib.sweep(self.team_dir, send, owners=set(self.transports))
        except Exception as e:
            self._log(f"[supervisor] reminder sweep failed: {type(e).__name__}: {e}")
        finally:
            if bus is not None and hasattr(bus, "close"):
                bus.close()

    def _log_pending_reminders(self) -> None:
        """Say what this run is leaving behind that only a sweep can deliver.

        A run that goes idle and exits with reminders still set has delivered
        everything it can, and nothing else will deliver the rest unless
        something sweeps. Silence here would read as "nothing pending".
        """
        try:
            pending = tasks_lib.reminders(self.team_dir)
        except Exception:
            return
        if not pending:
            return
        nxt = min((t["check_after"] for t in pending
                   if not tasks_lib.unreadable(t)), default="now")
        self._log(f"[{len(pending)} task reminder(s) still set, next at {nxt}. "
                  f"They are delivered only while something sweeps: run the "
                  f"supervisor with --daemon, or schedule "
                  f"`python -m agyteam.tasks sweep`.]")

    @staticmethod
    def _has_user_mail(transport) -> bool:
        try:
            pending = transport.peek()
            if pending:
                for m in pending:
                    sender = getattr(m, "sender", None)
                    if sender is None and isinstance(m, dict):
                        sender = m.get("sender") or m.get("from")
                    if sender == "user":
                        return True
        except Exception:
            pass
        return False

    # --- dispatch ---------------------------------------------------------
    #
    # Turns run concurrently: an agent with mail is begun as soon as it has
    # some, and a twelve-minute turn for one agent holds nobody else up. The
    # runner's begin()/poll() contract is what makes that possible; a runner
    # that only has wake() gets a thread adapter from the base class.
    #
    # What is in flight is written to <team_dir>/inflight.json so a
    # supervisor that restarts knows which turns a host is still running
    # (resumable handles) and which died with it (thread turns, whose mail
    # is still in the inbox and is simply delivered again).

    @property
    def ledger_path(self) -> Path:
        return self.team_dir / "inflight.json"

    @property
    def heartbeat_path(self) -> Path:
        return heartbeat.path(self.team_dir)

    def _save_ledger(self) -> None:
        if not self._owns_ledger:
            return                  # another supervisor's, or none of ours
        doc = {}
        for agent, turn in self.inflight.items():
            doc[agent] = {"handle": turn["handle"], "ts": turn["ts"],
                          "hop": turn["hop"], "trigger": turn["trigger"],
                          "msgs": [m.as_dict() for m in turn["msgs"]]}
        try:
            with filelock.locked(self.ledger_path):
                _rewrite(self.ledger_path, json.dumps(doc, indent=2) + "\n")
        except OSError:
            pass                    # bookkeeping must not stop dispatch

    def _load_ledger(self) -> None:
        try:
            doc = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(doc, dict) or not doc:
            return
        for agent, turn in doc.items():
            handle = turn.get("handle") or {}
            msgs = [Message.from_dict({"to": agent, **m}) for m in turn.get("msgs", [])]
            stale = self._stale_turn(turn)
            if stale and getattr(self.runner, "resumable", False) and not handle.get("thread"):
                try:
                    self.runner.cancel(handle)
                except Exception:
                    pass
                self._log(f"[supervisor] {agent}'s turn from before the restart "
                          f"began {stale}; cancelled, and its mail will be "
                          f"delivered again")
                transport = self.transports.get(agent)
                if self.inbox_pull and transport is not None:
                    self._restore_pulled_mail(agent, transport, {
                        "ts": turn.get("ts", ""), "msgs": msgs})
                try:
                    self.observer.record_failure(
                        agent, handle.get("conversation", ""),
                        f"[error: turn from before the restart began {stale}; "
                        f"cancelled]", duration_s=None, hop=turn.get("hop"))
                except Exception:
                    pass
            elif getattr(self.runner, "resumable", False) and not handle.get("thread"):
                self.inflight[agent] = {
                    "handle": handle, "msgs": msgs, "ts": turn.get("ts", ""),
                    "hop": turn.get("hop", 0), "trigger": turn.get("trigger", {}),
                    "started": time.monotonic(), "failures_before": 0,
                    "resumed": True}
                self._log(f"[supervisor] resuming {agent}'s turn from before the restart")
            else:
                # The turn died with the process that ran it. Its mail was
                # never acknowledged, so the next pass delivers it again.
                self._log(f"[supervisor] {agent}'s turn did not survive the "
                          f"restart; its mail will be delivered again")
                try:
                    self.observer.record_failure(
                        agent, "", "[error: supervisor restarted mid-turn; "
                        "mail redelivered]", duration_s=None, hop=turn.get("hop"))
                except Exception:
                    pass
        self._save_ledger()

    # --- model changes ------------------------------------------------------
    #
    # An agent's model is the roster's, or an operator override from
    # agyteam.models. A change -- either kind -- is applied here, at the
    # agent's next idle moment, as a cycle: distill on the conversation it
    # has, retire it, and let the next wake start on the new model. What has
    # been applied is kept on disk, so a restart neither re-cycles everyone
    # nor misses a change made while it was down.

    @property
    def _models_applied_path(self) -> Path:
        return self.team_dir / "models_applied.json"

    def _wanted_models(self) -> dict[str, list]:
        from . import models
        try:
            specs = {a["name"]: a for a in roster_lib.load(self.team_dir / "roster.json")["agents"]}
        except Exception:
            specs = {}
        out = {}
        for agent in self.agents:
            spec = specs.get(agent, {})
            m, e, _ = models.resolve(self.team_dir, agent, spec.get("model"), spec.get("effort"))
            out[agent] = [m, e]
        return out

    def _apply_model_changes(self) -> None:
        try:
            wanted = self._wanted_models()
        except Exception:
            return
        try:
            applied = json.loads(self._models_applied_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            applied = None
        if applied is None:                       # first look: this is the baseline
            try:
                _rewrite(self._models_applied_path, json.dumps(wanted, indent=2) + "\n")
            except OSError:
                pass
            return
        changed = False
        for agent, now_on in wanted.items():
            was_on = applied.get(agent)
            if was_on is None:
                applied[agent] = now_on
                changed = True
                continue
            if list(was_on) == list(now_on) or agent in self.inflight:
                continue                          # unchanged, or not idle yet
            self._warn_unavailable_model(agent, now_on)
            has_conversation = bool(getattr(self.runner, "conversation_id",
                                            lambda a: None)(agent))
            distilled = False
            if getattr(self.runner, "switches_model_in_conversation", False) is True:
                # This runtime takes the model per turn and keeps the
                # conversation across a switch (measured on the agy CLI: a
                # conversation resumed on another model still knew what it
                # had been told). Cycling would throw that away for nothing.
                self._log(f"[supervisor] {agent}: model {was_on[0] or 'default'} -> "
                          f"{now_on[0] or 'default'} from its next turn, same conversation")
            elif has_conversation and self._worked_since_cycle(agent):
                self._log(f"[supervisor] {agent}: model {was_on[0] or 'default'} -> "
                          f"{now_on[0] or 'default'}; distilling, then a fresh conversation")
                self.cycle(agent)
                distilled = not getattr(self, "_last_distill_failed", False)
            elif has_conversation:
                self.runner.reset(agent)          # nothing to distill; just retire it
            try:
                self.observer.record_event("model_change", agent=agent, before=was_on,
                                           after=now_on, distilled=distilled)
            except Exception:
                pass
            applied[agent] = now_on
            changed = True
        if changed:
            try:
                _rewrite(self._models_applied_path, json.dumps(applied, indent=2) + "\n")
            except OSError:
                pass

    def _warn_unavailable_model(self, agent: str, now_on: list) -> None:
        """Tell the user, once per change, when an agent is about to run on a
        model the runner does not offer -- an override that expired back to a
        stale roster pin, say. It is not blocked: the runner's list can lag."""
        from . import runner as runner_lib
        try:
            ok = runner_lib.model_available(now_on[0], now_on[1], team_dir=self.team_dir)
        except Exception:
            ok = None
        if ok is not False:
            return
        text = (f"{agent} is moving to {now_on[0]!r}, which the configured runner "
                f"does not offer: its turns will fail. Choose one it does "
                f"(python -m agyteam.models available) and set it with "
                f"python -m agyteam.models set {agent} <model>, or fix the roster.")
        try:
            bus = load_transport("supervisor")
            try:
                bus.send_kind("user", text, kind="blocker")
            finally:
                if hasattr(bus, "close"):
                    bus.close()
            self._user_mail_at_start = self._user_mail_count()
            self._log(f"[supervisor] {text}")
        except Exception:
            pass

    def _model_allowance_alerts(self) -> None:
        """Tell the user once when a model passes 80% and 100% of its weekly
        allowance. Nothing is switched: that is the operator's call."""
        now = time.monotonic()
        if now - getattr(self, "_allowance_looked_at", 0.0) < 60:
            return
        self._allowance_looked_at = now
        from . import models
        try:
            alerts = models.due_alerts(self.team_dir)
        except Exception:
            return
        for a in alerts:
            saved = self._distill_before_allowance(a)
            text = (f"{a['model']} has used {a['percent']}% of its weekly allowance "
                    f"({a['used']:,} of {a['allowance']:,} tokens). Nothing has been "
                    f"switched. To move agents off it: python -m agyteam.models set "
                    f"<agent|*> <model> --until <when>."
                    + (f" Distilled {', '.join(saved)} while it lasts, so what they "
                       f"learned on it is in memory; their conversations are kept."
                       if saved else ""))
            try:
                bus = load_transport("supervisor")
                try:
                    bus.send_kind("user", text, kind="fyi")
                finally:
                    if hasattr(bus, "close"):
                        bus.close()
                # A notice from the supervisor is not the team answering.
                self._user_mail_at_start = self._user_mail_count()
                self._log(f"[supervisor] {text}")
            except Exception:
                pass

    def _distill_before_allowance(self, alert: dict) -> list[str]:
        """At the first allowance warning, distill the agents working on that
        model, keeping their conversations. A distill needs the model it
        distills on: left until the allowance or the account ran out, it
        failed exactly when it was needed -- before a model change, or for
        good. Roster policy "distill_before_allowance": false turns it off."""
        if alert.get("threshold", 100) >= 100:
            return []
        try:
            policy = (json.loads(self._roster_path.read_text(encoding="utf-8"))
                      .get("policy") or {})
        except (OSError, ValueError, AttributeError):
            policy = {}
        if policy.get("distill_before_allowance") is False:
            return []
        last_model: dict[str, str] = {}
        try:
            for ev in self.observer.events("turn"):
                if ev.get("agent") and ev.get("model"):
                    last_model[ev["agent"]] = ev["model"]
        except Exception:
            return []
        on_it = [a for a in self.agents if last_model.get(a) == alert["model"]
                 and a not in self.inflight and a not in self._purged_agents
                 and self._worked_since_cycle(a)]
        if not on_it:
            return []
        self._log(f"[supervisor] {alert['model']} is at {alert['percent']}% of its "
                  f"allowance; distilling {', '.join(on_it)} while it lasts")
        self.distill_all(on_it)
        return [a for a in on_it if a not in getattr(self, "distill_failed", set())]

    # --- one driver per team ----------------------------------------------
    #
    # Two supervisors on one team both dispatch every agent's mail, so every
    # turn can run twice, and each overwrote the other's heartbeat. The
    # driving loops take an exclusive lock on <team_dir>/supervisor.lock for
    # as long as they run; the kernel drops it if the process dies.

    def _team_driven_elsewhere(self) -> bool:
        """Does another supervisor hold this team's lock right now?"""
        if not filelock.fcntl:
            return False
        try:
            f = open(self.team_dir / "supervisor.lock", "a+")
        except OSError:
            return False
        try:
            filelock.fcntl.flock(f.fileno(), filelock.fcntl.LOCK_EX | filelock.fcntl.LOCK_NB)
        except OSError:
            return True
        finally:
            f.close()               # releases it, if we took it
        return False

    def _claim_team(self) -> bool:
        if self.inspect_only:
            self.stopped = "inspect-only: this supervisor does not drive the team"
            return False
        if getattr(self, "_team_lock", None) is not None:
            return True
        path = self.team_dir / "supervisor.lock"
        f = open(path, "a+")
        try:
            if filelock.fcntl:
                filelock.fcntl.flock(f.fileno(), filelock.fcntl.LOCK_EX | filelock.fcntl.LOCK_NB)
        except OSError:
            f.close()
            beat = heartbeat.running(self.team_dir) or {}
            who = f"pid {beat.get('pid')}" if beat.get("pid") else "another process"
            self.stopped = f"another supervisor ({who}) is already driving this team"
            self._log(f"[supervisor] {self.stopped}; not starting a second one")
            return False
        self._team_lock = f
        if not self._owns_ledger:
            self._owns_ledger = True
            self._load_ledger()
        self._pin_roster()
        return True

    # --- the roster is the operator's -------------------------------------
    #
    # Every permission an agent has is in roster.json, and an agent with a
    # shell runs as the same user who wrote it. Downstream, a test agent that
    # had been refused an edit built the roster's path in pieces, rewrote the
    # roster to grant itself access, and finished its task with it. No rule
    # in this process can stop that write. What the supervisor can do is not
    # let it stand: it keeps the roster it accepted, and a change it cannot
    # account for -- made while an agent's turn was running, and not written
    # by agyteam's own roster.save -- is put back, kept aside, recorded, and
    # reported to the operator. Changes made while no turn runs are the
    # operator's and are adopted.
    #
    # Detection, not a boundary: it cannot undo what a turn did with a
    # permission while it held it, and an agent can forge anything agyteam
    # writes. The boundary is an OS user or container that cannot write the
    # team directory.

    def _read_roster(self) -> bytes | None:
        try:
            return self._roster_path.read_bytes()
        except OSError:
            return None

    def _pin_roster(self) -> None:
        """Accept the roster as it is now: the operator just started us."""
        self._roster_pin = self._read_roster()
        self._roster_touched = set()
        if (self._roster_pin is not None and roster_lib.last_recorded(self._roster_path)
                != roster_lib.digest(self._roster_pin)):
            roster_lib.record_write(self._roster_path, self._roster_pin,
                                    by="supervisor: as found at start")
        self._roster_history_at = roster_lib.history_size(self._roster_path)

    def _guard_roster(self) -> None:
        """Adopt a roster change that can be accounted for; revert any other."""
        busy = sorted(set(self.inflight) | self._roster_touched)
        self._roster_touched = set()
        cur, pin = self._read_roster(), self._roster_pin
        if cur == pin:
            return
        if pin is None:
            self._roster_pin = cur          # nothing was accepted to defend
            return
        if cur is not None and roster_lib.digest(cur) in roster_lib.recorded_since(
                self._roster_path, self._roster_history_at):
            self._roster_pin = cur          # written by roster.save
            self._roster_history_at = roster_lib.history_size(self._roster_path)
            return
        if not busy:                        # the operator's
            if cur is not None:
                roster_lib.record_write(self._roster_path, cur,
                                        by="supervisor: changed while no turn was running")
            self._roster_pin = cur
            self._roster_history_at = roster_lib.history_size(self._roster_path)
            return

        changed = roster_lib.changed_entries(pin, cur)
        kept = None
        if cur is not None:
            kept = self.team_dir / f"roster.rejected-{time.strftime('%Y%m%d-%H%M%S')}.json"
            try:
                kept.write_bytes(cur)
            except OSError:
                kept = None
        try:
            _rewrite(self._roster_path, pin)
        except OSError as e:
            self._log(f"[WARNING: roster.json changed during a turn and could not "
                      f"be put back: {e}]")
            return
        who = ", ".join(busy)
        what = ", ".join(changed) or "the file"
        try:
            self.observer.record_event("roster_reverted", agents=busy, changed=changed,
                                       rejected=str(kept) if kept else None)
        except Exception:
            pass
        self._log(f"[WARNING: roster.json changed while {who} had a turn running "
                  f"({what}); put back]")
        text = (f"roster.json was changed while {who} had a turn running, and "
                f"agyteam did not write the change, so it has been put back. "
                f"Changed: {what}. "
                + (f"The rejected version is kept at {kept}. " if kept else "")
                + "If the change was yours, make it again while no turn is "
                  "running, or with `python -m agyteam.lifecycle`. If it was "
                  "not, an agent rewrote its own permissions: check what that "
                  "turn did with them.")
        try:
            bus = load_transport("supervisor")
            try:
                bus.send_kind("user", text, kind="blocker")
            finally:
                if hasattr(bus, "close"):
                    bus.close()
            # A notice from the supervisor is not the team answering the user.
            if self._user_mail_at_start is not None:
                self._user_mail_at_start += 1
        except Exception:
            pass

    def _release_team(self) -> None:
        f = getattr(self, "_team_lock", None)
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
            self._team_lock = None

    def _stale_turn(self, turn: dict) -> str:
        """How long ago a ledger turn began, if too long to resume; else ''.

        Resumed blindly, a turn orphaned hours before the restart sat in
        flight for one more full timeout before anything noticed it, and its
        agent's mail waited behind it.
        """
        timeout = getattr(self.runner, "timeout", None)
        try:
            limit = float(timeout) * config.INFLIGHT_STALE_MULTIPLIER
        except (TypeError, ValueError):
            return ""
        if limit <= 0:
            return ""
        started = (turn.get("handle") or {}).get("started")
        try:
            began = float(started) if started is not None else time.mktime(
                time.strptime(turn.get("ts", ""), "%Y-%m-%d %H:%M:%S"))
        except (TypeError, ValueError, OverflowError):
            return ""
        age = time.time() - began
        return f"{age / 60:.0f} minutes ago" if age > limit else ""

    def _heartbeat(self, mode: str = "running") -> None:
        """Say that a supervisor is alive, for tools that need to know.

        send_to_teammate reports whether anyone will wake the recipient, and
        doctor warns when reminders are due with nothing sweeping; both read
        this file. Written at most every few seconds.
        """
        now = time.monotonic()
        if now - self._heartbeat_at < 5 and mode == "running":
            return
        self._heartbeat_at = now
        if getattr(self, "_team_lock", None) is None:
            other = heartbeat.running(self.team_dir)
            if other and other.get("pid") not in (os.getpid(), None):
                return              # another live supervisor's; leave it
        try:
            _rewrite(self.heartbeat_path, json.dumps({
                "pid": os.getpid(), "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "mode": mode, "poll": self.poll,
                "inflight": sorted(self.inflight),
                # What this supervisor is running with, and where that came
                # from: a client can show it, and a wrong one is visible.
                "runner": getattr(self.runner, "label", type(self.runner).__name__),
                "runner_class": f"{type(self.runner).__module__}:{type(self.runner).__name__}",
                "manager": self._find_manager(),
                "source": getattr(self, "settings_source", {})}) + "\n")
        except OSError:
            pass

    @staticmethod
    def _fit_wake(msgs: list[Message]) -> tuple[list[Message], int]:
        """(messages to deliver now, how many quiet ones wait for later).

        Every waking message goes in. Quiet ones follow, oldest first, while
        they fit config.WAKE_PROMPT_BYTES; the rest stay in the inbox --
        unacknowledged, so nothing is lost -- and the prompt says how many.
        """
        budget = config.WAKE_PROMPT_BYTES
        loud = [m for m in msgs if m.wakes]
        quiet = [m for m in msgs if not m.wakes]
        if budget <= 0:
            return msgs, 0
        used = sum(len(m.render().encode("utf-8", "replace")) for m in loud)
        keep = []
        for m in quiet:
            size = len(m.render().encode("utf-8", "replace"))
            if used + size > budget:
                break
            keep.append(m)
            used += size
        chosen = set(map(id, loud + keep))
        return [m for m in msgs if id(m) in chosen], len(quiet) - len(keep)

    def wake_prompt(self, msgs: list[Message], deferred: int = 0) -> str:
        """What an agent is woken with.

        By default, the messages themselves. With inbox_pull, one line saying
        what is waiting and the agent reads it with check_inbox: nothing is
        pasted into the prompt, the mail is acknowledged by the agent as it
        reads, and a host can render the ping as a system note rather than a
        user message.
        """
        if not self.inbox_pull:
            body = "\n\n".join(m.render() for m in msgs)
            if deferred:
                body += (f"\n\n[{deferred} more unread message(s) -- channel posts "
                         f"that did not mention you, acks, fyi -- are still in your "
                         f"inbox. Read them with check_inbox if they may matter.]")
            return WAKE_PROMPT.format(messages=body)
        parts = []
        for m in msgs:
            bits = [f"from={m.sender}", f"kind={m.kind}"]
            if m.task_id:
                bits.append(f"task_id={m.task_id}")
            bits.append(f"bytes={len(m.content.encode('utf-8', 'replace'))}")
            parts.append(" ".join(bits))
        return PULL_PROMPT.format(n=len(msgs), summary="; ".join(parts))

    # Who has already been escalated, kept on disk: held only in memory, a
    # restarted daemon escalated the same broken agent again on its first
    # failure streak, and a crash-looping one did it every few minutes.

    @property
    def escalations_path(self) -> Path:
        return self.team_dir / "escalations.json"

    def _load_escalations(self) -> set[str]:
        try:
            doc = json.loads(self.escalations_path.read_text(encoding="utf-8"))
            return {str(a) for a in doc} if isinstance(doc, dict) else set()
        except (OSError, ValueError):
            return set()

    def _save_escalations(self, why: dict[str, str] | None = None) -> None:
        try:
            doc = json.loads(self.escalations_path.read_text(encoding="utf-8"))
            doc = doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            doc = {}
        doc = {a: v for a, v in doc.items() if a in self._escalated}
        for a, reason in (why or {}).items():
            doc[a] = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason[:300]}
        try:
            if doc:
                _rewrite(self.escalations_path, json.dumps(doc, indent=2) + "\n")
            elif self.escalations_path.exists():
                self.escalations_path.unlink()
        except OSError:
            pass

    def _restore_pulled_mail(self, agent: str, transport, turn: dict) -> int:
        """Give back the mail a failed turn took and did not finish with.

        Under inbox_pull the agent takes its mail with check_inbox, which
        marks it read as it goes, and writes a receipt for it first. A turn
        that then failed -- a crash, a 503, a timeout -- had lost that mail,
        including anything that arrived while it ran and that it pulled
        mid-turn; and once the turn had sent anything at all (an early
        "on it"), nothing was put back. Now everything on the receipt that
        is no longer in the inbox goes back, once, marked as redelivered
        with what the failed turn had already sent, so the agent can tell
        what is done from what is not. At-least-once, as everywhere else.

        Mail taken some other way than check_inbox leaves no receipt; from
        the wake's own batch, that goes back only when the turn sent
        nothing, as before.
        """
        taken = receipts.taken(self.team_dir, agent)
        sent = self._sent_during(agent, turn.get("ts", ""))
        candidates = list(taken)
        if not any(sent.values()):
            candidates += list(turn.get("msgs") or [])
        receipts.close_turn(self.team_dir, agent)
        if not candidates or not (hasattr(transport, "requeue") and callable(transport.requeue)):
            return 0
        try:
            waiting = transport.peek()
        except Exception:
            waiting = None
        if waiting is None:
            return 0            # cannot tell what is missing; do not duplicate
        key = lambda m: (m.ts, m.sender, m.kind, m.content)      # noqa: E731
        seen = {key(m) for m in waiting}
        missing = []
        for m in candidates:
            if key(m) not in seen:
                seen.add(key(m))
                missing.append(m)
        if not missing:
            return 0
        did = ", ".join(f"{n} {k}" for k, n in sorted(sent.items()) if n)
        note = (f"a turn that read this at {turn.get('ts') or 'an earlier wake'} failed "
                f"before it finished" + (f", after sending {did} message(s); check "
                                         f"what you already did before redoing it"
                                         if did else ", having sent nothing"))
        for m in missing:
            m.redelivered = note
        try:
            transport.requeue(missing)
        except TypeError:
            transport.requeue(agent, missing)
        self._log(f"    {agent}: turn failed; {len(missing)} pulled message(s) "
                  f"restored to its inbox")
        return len(missing)

    def _budget_exhausted(self, reply: str) -> bool:
        low = (reply or "").lower()
        return any(p in low for p in config.BUDGET_HALT_PATTERNS)

    def _halt_for_budget(self, agent: str, reply: str) -> None:
        """Stop the team once for an account-level failure, and say so once."""
        from . import lifecycle
        already = (self.team_dir / ".stop").exists()
        reason = f"budget exhausted ({agent}): {reply[:200]}"
        if not already:
            try:
                lifecycle.stop_team(reason=reason, team_dir=self.team_dir)
            except Exception:
                pass
            text = (f"Every agent on this team is about to fail the same way: "
                    f"{agent}'s turn failed with what looks like an exhausted "
                    f"account or quota, so the team has been stopped rather "
                    f"than let each agent fail and escalate in turn. Their "
                    f"mail is queued. Fix the account, then run `python -m "
                    f"agyteam.lifecycle start` (or delete {self.team_dir / '.stop'}). "
                    f"Error: {reply[:300]}")
            try:
                bus = load_transport("supervisor")
                try:
                    bus.send_kind("user", text, kind="blocker")
                finally:
                    if hasattr(bus, "close"):
                        bus.close()
                self._user_mail_at_start = self._user_mail_count()
            except Exception:
                pass
            try:
                self.observer.record_event("budget_halt", agent=agent, error=reply[:500])
            except Exception:
                pass
            self._log(f"[supervisor] {reason} -- team stopped; the user has been told")
        self.stopped = f"team stopped: {reason}"

    def _failure_count(self) -> int:
        try:
            return len(self.observer.events("failure"))
        except Exception:
            return 0

    def _sent_during(self, agent: str, since_ts: str) -> dict[str, int]:
        """Bus messages `agent` sent since `since_ts`, counted by kind."""
        out: dict[str, int] = {}
        try:
            for line in (self.team_dir / "bus.jsonl").read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("from") == agent and (e.get("ts") or "") >= since_ts:
                    k = e.get("kind") or "work"
                    out[k] = out.get(k, 0) + 1
        except OSError:
            pass
        return out

    def _begin_turns(self, respect_backoff: bool = True) -> list[str]:
        """Start a turn for every agent with waking mail and no turn running."""
        begun: list[str] = []
        now = time.monotonic()
        ordered = sorted(self.transports.items(),
                         key=lambda item: 0 if self._has_user_mail(item[1]) else 1)
        for agent, transport in ordered:
            if agent in self.inflight:
                continue
            if self.hops >= self.max_hops:
                break
            if respect_backoff and self._backoff.get(agent, 0.0) > now:
                continue
            # One agent's unreadable inbox is that agent's problem. Raising
            # here ended the pass for everyone, every pass, until someone
            # found the file -- the same isolation a failed turn gets.
            try:
                msgs = transport.fetch()
            except Exception as e:
                self._log(f"  ! could not read {agent}'s mail: {type(e).__name__}: {e}")
                continue
            if not msgs or not any(m.wakes for m in msgs):
                continue            # quiet mail waits for a waking cause
            msgs, deferred = self._fit_wake(msgs)
            senders = ", ".join(sorted({m.sender for m in msgs}))
            self.hops += 1
            trigger = {"senders": sorted({m.sender for m in msgs}),
                       "kinds": sorted({m.kind for m in msgs}),
                       "task_ids": sorted({m.task_id for m in msgs if m.task_id})}
            try:
                self.runner.turn_meta[agent] = {"trigger": trigger, "hop": self.hops}
            except Exception:
                pass
            self._log(f"  → waking {agent} ({len(msgs)} from {senders})")
            turn = {"msgs": msgs, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "started": time.monotonic(), "hop": self.hops,
                    "trigger": trigger, "failures_before": self._failure_count()}
            if deferred:
                turn["deferred"] = deferred
            if self.inbox_pull:
                receipts.open_turn(self.team_dir, agent)
            try:
                turn["handle"] = self.runner.begin(agent, self.wake_prompt(msgs, deferred))
            except Exception as e:          # the runner could not even start it
                turn["handle"] = {"raised": repr(e)}
                self._finish(agent, turn, f"[error: {type(e).__name__}: {e}]")
                continue
            self.inflight[agent] = turn
            begun.append(agent)
        if begun:
            self._save_ledger()
        return begun

    def _reap(self) -> int:
        """Finish every turn that has ended. Returns how many."""
        done = 0
        for agent, turn in list(self.inflight.items()):
            try:
                reply = self.runner.poll(turn["handle"])
            except Exception as e:
                reply = f"[error: {type(e).__name__}: {e}]"
                turn["handle"]["raised"] = repr(e)
            if reply is None:
                continue
            del self.inflight[agent]
            self._finish(agent, turn, reply if isinstance(reply, str) else "")
            done += 1
        if done:
            self._save_ledger()
        return done

    def _finish(self, agent: str, turn: dict, reply: str) -> None:
        """Acknowledge or requeue, account, and judge one ended turn."""
        self._roster_touched.add(agent)
        msgs = turn["msgs"]
        dur = time.monotonic() - turn["started"]
        failed = reply.startswith("[error:")
        transport = self.transports.get(agent)
        if failed:
            self._log(f"    {agent}: {reply[:200]}")
        elif not self.quiet:
            first = reply.strip().splitlines()[0] if reply.strip() else ""
            self._log(f"    {agent}: {first[:120]}")

        if transport is not None:
            if failed:
                # Pasted into the prompt, the mail was never acknowledged by
                # anyone, so it goes back. Pulled through check_inbox, the
                # agent's own acknowledgement is the record that it took
                # the mail; what it did not take is still in the inbox. Re-
                # queueing the snapshot here handed a timed-out turn's
                # already-processed messages back to the next wake.
                if not self.inbox_pull and hasattr(transport, "requeue") \
                        and callable(transport.requeue):
                    try:
                        transport.requeue(msgs)
                    except TypeError:
                        transport.requeue(agent, msgs)
                elif self.inbox_pull:
                    self._restore_pulled_mail(agent, transport, turn)
            elif self.inbox_pull:
                # Under inbox_pull the agent reads with check_inbox, a page at
                # a time, and that call marks read exactly what it returned.
                # Acknowledging the whole batch here deleted every page the
                # agent never opened. What it left is still waiting; what it
                # took is done with.
                receipts.close_turn(self.team_dir, agent)
            elif hasattr(transport, "acknowledge") and callable(transport.acknowledge):
                try:
                    transport.acknowledge(msgs)
                except TypeError:
                    transport.acknowledge()

        cid = ""
        try:
            cid = getattr(self.runner, "conversation_id", lambda a: "")(agent) or ""
        except Exception:
            pass

        if failed and self._budget_exhausted(reply):
            try:
                self.observer.record_failure(agent, cid, reply, duration_s=dur,
                                             hop=turn["hop"], trigger=turn["trigger"])
            except Exception:
                pass
            self._halt_for_budget(agent, reply)
            return

        if failed:
            self._fail_streak[agent] = self._fail_streak.get(agent, 0) + 1
            # A timeout is an agent that was working too long, not a broken
            # conversation: two slow turns retired a working agent's context.
            if runner_lib.is_retryable(reply) or "timed out" in reply.lower():
                self._hard_streak.pop(agent, None)
            else:
                self._hard_streak[agent] = self._hard_streak.get(agent, 0) + 1
            streak = self._fail_streak[agent]
            # A failing wake path returned in milliseconds, counted as a
            # dispatched turn, and the daemon never slept: two spawns a
            # second, for days, with nothing in events.jsonl. Back off, and
            # make sure the failure is on the record whoever reported it.
            delay = min(config.WAKE_BACKOFF_BASE_S * (2 ** (streak - 1)),
                        config.WAKE_BACKOFF_MAX_S)
            self._backoff[agent] = time.monotonic() + delay
            if turn["handle"].get("raised") or \
                    self._failure_count() <= turn["failures_before"]:
                try:
                    self.observer.record_failure(agent, cid, reply, duration_s=dur,
                                                 hop=turn["hop"],
                                                 trigger=turn["trigger"])
                except Exception:
                    pass
            hard = self._hard_streak.get(agent, 0)
            if config.RESET_AFTER_FAILURES > 0 and hard == config.RESET_AFTER_FAILURES:
                # The same non-transient failure, again: the conversation
                # itself is what is broken. It cannot distill, so it is
                # retired without that; the id stays findable.
                try:
                    self.runner.reset(agent)
                    self._log(f"[supervisor] {agent} failed {hard} turns in a row "
                              f"with no transient cause; its conversation was "
                              f"retired and the next wake starts a new one")
                    self.observer.record_event("reset", agent=agent, conversation=cid,
                                               failures=hard, error=reply[:300])
                except Exception:
                    pass
            if streak >= config.ESCALATE_AFTER_FAILURES and agent not in self._escalated:
                self._escalated.add(agent)
                self._save_escalations({agent: reply})
                self._escalate(agent, streak, reply)
        else:
            self._fail_streak.pop(agent, None)
            self._hard_streak.pop(agent, None)
            self._backoff.pop(agent, None)
            if agent in self._escalated:
                self._escalated.discard(agent)
                self._save_escalations()

        sent = self._sent_during(agent, turn["ts"])
        try:
            self.observer.record_event(
                "dispatch", agent=agent, conversation=cid, hop=turn["hop"],
                trigger=turn["trigger"], failed=failed, duration_s=round(dur, 2),
                sent=sent, ack_only=bool(sent) and all(not wakes(k) for k in sent),
                resumed=bool(turn.get("resumed")))
        except Exception:
            pass

        if not failed:
            self._check_anomaly(agent, reply, turn)

    def _escalate(self, agent: str, streak: int, reply: str) -> None:
        """One message, to whoever can act, after a run of failed wakes."""
        mgr = self._find_manager()
        to = mgr if mgr and mgr != agent else "user"
        text = (f"{agent} has failed {streak} turns in a row and is being "
                f"retried with backoff; nothing they were asked to do is "
                f"happening until the wake path works. Last error: "
                f"{reply[:300]}")
        try:
            bus = load_transport("supervisor")
            try:
                bus.send_kind(to, text, kind="blocker")
            finally:
                if hasattr(bus, "close"):
                    bus.close()
            if to == "user":
                # The supervisor telling the user that nothing is working is
                # not the team answering the user. Counted as an answer, it
                # tripped the review gate and bounced a "your answer went
                # out unreviewed" at an agent that had never managed a turn.
                self._user_mail_at_start = self._user_mail_count()
            self._log(f"[supervisor] escalated {agent}'s failures to {to}")
        except Exception:
            pass

    def _check_anomaly(self, agent: str, reply: str, turn: dict) -> None:
        anomaly_threshold = int(os.environ.get(
            "AGYTEAM_ANOMALY_OUTPUT_TOKENS", config.ANOMALY_OUTPUT_TOKENS_THRESHOLD))
        try:
            agent_turns = [ev for ev in self.observer.events("turn")
                           if ev.get("agent") == agent]
            latest_turn = agent_turns[-1] if agent_turns else None
        except Exception:
            latest_turn = None
        out_tok = (latest_turn or {}).get("output_tokens")
        if out_tok is None or out_tok <= anomaly_threshold:
            return
        # A big turn is expensive, which is worth saying out loud, but it
        # is not by itself a sick turn. This guard used to purge the
        # agent and halt the whole episode on volume alone; it did that
        # to syseng seconds after he finished a working build, because
        # 229 shell invocations legitimately cost 225k output tokens.
        # Volume is reported. Only repetition acts.
        rep = repetition_ratio(reply)
        self._log(f"[WARNING: {agent} produced {out_tok:,} output tokens "
                  f"(threshold {anomaly_threshold:,}), repetition {rep:.1f}x]")
        if rep < config.ANOMALY_REPETITION_RATIO:
            return                  # expensive, not broken: let it work
        err_msg = (f"anomaly detected for {agent}: {out_tok} output tokens at "
                   f"repetition {rep:.1f}x (limit "
                   f"{config.ANOMALY_REPETITION_RATIO}x) — output is degenerate")
        self._log(f"[WARNING: {err_msg}]")
        cid = (latest_turn.get("conversation")
               or getattr(self.runner, "conversation_id", lambda a: "")(agent) or "")
        dur = latest_turn.get("duration_s") or (time.monotonic() - turn["started"])
        self.purge_context(agent)
        try:
            self.observer.record_failure(agent, cid, err_msg, duration_s=dur)
        except Exception:
            pass
        self.stopped = err_msg

    def _pass(self, respect_backoff: bool = True) -> tuple[list[str], int]:
        """One scheduling pass: reap what ended, begin what can start.

        Returns (agents begun, turns reaped). Does not wait for anything.
        """
        with self._step_lock:
            stop_file = self.team_dir / ".stop"
            if stop_file.exists():
                try:
                    reason = stop_file.read_text(encoding="utf-8").strip()
                except OSError:
                    reason = ""
                reason = reason or "stop requested via lifecycle"
                self.stopped = f"team stopped via lifecycle: {reason}"
                self._log(f"[supervisor] team stopped via lifecycle: {reason}")
                return [], 0

            self._guard_roster()
            roster_path = self.team_dir / "roster.json"
            if roster_path.exists():
                try:
                    current_roster = roster_lib.load(roster_path)
                    current_roster = roster_lib.normalize(current_roster)
                    roster_agent_names = [a["name"] for a in current_roster.get("agents", [])]
                    if set(roster_agent_names) != set(self.transports.keys()):
                        for name in roster_agent_names:
                            if name not in self.transports:
                                self.transports[name] = load_transport(name)
                        for name in list(self.transports.keys()):
                            if name not in roster_agent_names:
                                t = self.transports.pop(name)
                                if hasattr(t, "close") and callable(t.close):
                                    try:
                                        t.close()
                                    except Exception:
                                        pass
                                # A turn in flight for an agent who has left
                                # finishes on its own; nothing will read its
                                # mail again, so it leaves the ledger now.
                                turn = self.inflight.pop(name, None)
                                if turn is not None:
                                    try:
                                        self.runner.cancel(turn["handle"])
                                    except Exception:
                                        pass
                                    self._save_ledger()
                        self.agents = roster_agent_names
                    if hasattr(self.runner, "sync_roster") and callable(self.runner.sync_roster):
                        self.runner.sync_roster(current_roster)
                except Exception:
                    pass

            self._heartbeat()
            self._apply_model_changes()
            self._model_allowance_alerts()
            self._sweep_due_tasks()
            reaped = self._reap()
            self._serve_distill_requests()
            begun = self._begin_turns(respect_backoff)
            return begun, reaped

    def _drain(self, agents: list[str] | None = None) -> int:
        """Wait for turns to end -- the given agents', or all -- reaping as they do."""
        reaped = 0
        while True:
            with self._step_lock:
                reaped += self._reap()
            waiting = [a for a in self.inflight if agents is None or a in agents]
            if not waiting:
                return reaped
            self.runner.wait_turn(self.poll)

    def step(self) -> int:
        """One pass, then wait for every turn it began to end.

        Returns the number of turns begun. Turns begun in the same pass run
        concurrently; the pass is over when the last of them is. Callers
        that want to overlap passes use run_until_idle or run_forever, which
        drive _pass() directly and never wait for a particular turn.

        Failure backoff is the loops' concern: a caller stepping by hand is
        pacing itself, and a step it asks for is a step it gets.
        """
        begun, _ = self._pass(respect_backoff=False)
        self._drain(begun)
        return len(begun)

    def run_until_idle(self) -> int:
        """Dispatch until the user is answered, or nobody has mail.

        Returns total turns taken; self.stopped says why we stopped.

        Each call is one episode, and everything that bounds or judges an
        episode starts fresh here. `--chat` calls this once per message on
        one Supervisor, and none of it used to be reset: `stopped` survived
        from the previous message, so every episode after the first ran a
        single pass and broke -- a round trip through a teammate came back
        one message late -- while the hop budget drained across the whole
        session, and the review baseline stayed where it was at start-up, so
        one approved review early on made every later unreviewed answer read
        as reviewed.
        """
        if not self._claim_team():
            return 0
        t0 = time.monotonic()
        total = 0
        bounced = False
        self.stopped = ""
        self.hops = 0
        self._user_mail_at_start = self._user_mail_count()
        self._approved_reviews_at_start = self._approved_reviews_count()
        # Where the review gate's window opens (activity.episode_start).
        # Taken from the episode's end instead, it opened after all the work
        # the episode did, so none of that work could be reviewed.
        try:
            self.observer.record_event("episode_start")
        except Exception:
            pass
        while True:
            begun, reaped = self._pass()
            total += len(begun)
            if self.stopped:
                self._drain()
                break
            # Answering the user ends the episode. Without this, agents who
            # have nothing left to do still owe each other a reply, and a
            # finished team keeps talking until the hop budget kills it.
            # Judged only once every turn in flight has ended: a verdict
            # taken mid-turn would stop the episode under an agent still
            # working on it.
            if self.stop_on_answer and self._user_was_answered():
                self._drain()
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
            if self.hops >= self.max_hops and not begun:
                self._drain()
                if not self.stopped:
                    self.stopped = f"hop budget of {self.max_hops} reached"
                    self._log(f"[hop budget of {self.max_hops} reached — stopping. "
                              f"Raise --max-hops or send a new instruction.]")
                break
            if not begun and not reaped:
                if self.inflight:
                    self.runner.wait_turn(self.poll)
                    continue
                stuck = [a for a in self.transports
                         if self._backoff.get(a, 0.0) > time.monotonic()
                         and self._has_waking_mail(self.transports[a])]
                if stuck:
                    # Mail is waiting on an agent in backoff. Give it time,
                    # up to a point: once everyone still holding mail has
                    # failed enough to be escalated, this episode is not
                    # going to finish, and a one-shot run that retried with
                    # backoff until the hop budget took hours to say so. A
                    # daemon keeps trying; an episode reports and ends.
                    limit = config.ESCALATE_AFTER_FAILURES
                    if all(self._fail_streak.get(a, 0) >= limit for a in stuck):
                        self.stopped = (f"{', '.join(stuck)} failed {limit} turns "
                                        f"in a row; their mail is still queued")
                        break
                    time.sleep(min(self.poll, 1.0))
                    continue
                self.stopped = "team went idle"
                break
        # The turns that ended the episode were reaped after the last pass
        # checked the roster; a change one of them made is judged now, while
        # it can still be attributed to them.
        with self._step_lock:
            self._guard_roster()
        if not self.stopped.startswith("hop budget"):
            self._log(f"[done after {total} turns — {self.stopped}]")
        self._log_pending_reminders()
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

    def _has_waking_mail(self, transport) -> bool:
        try:
            pending = transport.peek()
        except Exception:
            return False
        return bool(pending) and any(getattr(m, "wakes", wakes(getattr(m, "kind", "work")))
                                     for m in pending)

    def run_forever(self, stop: threading.Event | None = None) -> None:
        """React to mail as it arrives, indefinitely."""
        stop = stop or threading.Event()
        if not self._claim_team():
            return
        self._log(f"supervising {', '.join(self.agents)} "
                  f"via {self.runner.label} — Ctrl+C to stop")
        while not stop.is_set():
            if self.stopped:
                break
            begun, reaped = self._pass()
            if begun or reaped:
                # A long-running daemon should not inherit a budget meant to
                # bound one stimulus; the cap applies per burst of activity.
                if self.hops >= self.max_hops and not self.inflight:
                    self._log("[hop budget reached; resetting for the next burst]")
                    self.hops = 0
                    self.auto_cycle()
                continue
            if self.inflight:
                self.runner.wait_turn(self.poll)
            else:
                self.auto_cycle()
                self._maybe_distill_sweep()
                stop.wait(self.poll)

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

    def purge_context(self, agent: str) -> None:
        """Purge conversation context for an anomalous agent without distillation.

        Bypasses distill() completely to prevent poisoning memory/ or NORMS.md.
        """
        self._purged_agents.add(agent)
        self.runner.reset(agent)
        self._log(f"    {agent}: purged conversation context (bypassing distillation)")

    def distill(self, agent: str) -> tuple[int, int]:
        """Wake `agent` to distill learnings into memory.

        Returns (before_count, after_count).
        """
        if self._driver_elsewhere():
            return self._delegate("distill", [agent]).get(agent, (0, 0))
        if agent in self._purged_agents:
            self._log(f"    {agent}: distillation bypassed (agent context was purged due to anomaly)")
            before = self._memory_count(agent)
            return (before, before)
        if agent in self.inflight:
            self._log(f"    {agent}: not distilled (a turn is in flight)")
            self._last_distill_failed = True
            before = self._memory_count(agent)
            return (before, before)

        before_snap = self._memory_snapshot(agent)
        self._log(f"  → distilling {agent}")
        t0 = time.monotonic()
        try:
            reply = self.runner.wake(agent, DISTILL_PROMPT)
        except Exception as e:
            reply = self._distill_raised(agent, e, time.monotonic() - t0)
        before, after, failed = self._finish_distill(agent, before_snap, reply)
        self._last_distill_failed = failed
        return (before, after)

    def _distill_raised(self, agent: str, e: Exception, dur: float) -> str:
        reply = f"[error: {type(e).__name__}: {e}]"
        try:
            cid = getattr(self.runner, "conversation_id", lambda a: "")(agent) or ""
            self.observer.record_failure(agent, cid, reply, duration_s=dur)
        except Exception:
            pass
        return reply

    def _finish_distill(self, agent: str, before_snap: dict, reply: str) -> tuple[int, int, bool]:
        """Log and count one distillation; shared by distill and distill_all."""
        self._roster_touched.add(agent)
        failed = reply.startswith("[error:")
        if failed and self._budget_exhausted(reply):
            # Out of quota, the distill cannot run, and nothing is lost by
            # that: a failed distill never retires the conversation, so what
            # the agent learned is still in it for when the quota is back.
            self._log(f"    {agent}: not distilled -- the account is out of quota. Its "
                      f"conversation is kept, so nothing it learned is lost; distill it "
                      f"once there is quota again")
            try:
                self.observer.record_event("distill_deferred", agent=agent,
                                           error=reply[:300])
            except Exception:
                pass
        elif failed:
            self._log(f"    {agent}: {reply[:200]}")
        elif not self.quiet:
            first = reply.strip().splitlines()[0] if reply.strip() else ""
            self._log(f"    {agent}: {first[:120]}")
        before = len(before_snap)
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
        return before, after, failed

    def distill_all(self, agents: list[str] | None = None) -> dict[str, tuple[int, int]]:
        """Distill several agents at once. Returns {agent: (before, after)}.

        `--distill-all` used to wake them one after another, each blocking
        until its turn ended: a dozen agents at under a minute each was the
        best part of ten minutes. The turns are independent, so they run
        through the same begin/poll the dispatcher uses. An agent with a turn
        already in flight is skipped -- two turns in one conversation at once
        is the collision the dispatcher exists to prevent -- as is one whose
        context was purged. Agents whose distillation failed are in
        `self.distill_failed` afterwards.
        """
        targets = [a for a in (agents if agents is not None else self.agents)]
        if self._driver_elsewhere():
            return self._delegate("distill", targets)
        out: dict[str, tuple[int, int]] = {}
        self.distill_failed: set[str] = set()
        running: dict[str, tuple[dict, dict, float]] = {}
        for agent in targets:
            if agent in self.inflight:
                self._log(f"    {agent}: skipped (a turn is in flight)")
                continue
            if not self._worked_since_cycle(agent):
                # Nothing of its own to distill. Asked anyway, an agent that
                # had not taken a turn spent 2.1M tokens reading its
                # teammates' transcripts, memories and the bus for something
                # to call a lesson.
                self._log(f"    {agent}: skipped (no turns since its last cycle)")
                continue
            if agent in self._purged_agents:
                out[agent] = self.distill(agent)
                continue
            before_snap = self._memory_snapshot(agent)
            self._log(f"  → distilling {agent}")
            t0 = time.monotonic()
            try:
                handle = self.runner.begin(agent, DISTILL_PROMPT)
            except Exception as e:
                reply = self._distill_raised(agent, e, time.monotonic() - t0)
                before, after, failed = self._finish_distill(agent, before_snap, reply)
                out[agent] = (before, after)
                if failed:
                    self.distill_failed.add(agent)
                continue
            running[agent] = (handle, before_snap, t0)
        while running:
            for agent, (handle, before_snap, t0) in list(running.items()):
                try:
                    reply = self.runner.poll(handle)
                except Exception as e:
                    reply = self._distill_raised(agent, e, time.monotonic() - t0)
                if reply is None:
                    continue
                del running[agent]
                before, after, failed = self._finish_distill(
                    agent, before_snap, reply if isinstance(reply, str) else "")
                out[agent] = (before, after)
                if failed:
                    self.distill_failed.add(agent)
            if running:
                self.runner.wait_turn(self.poll)
        return out

    # --- the scheduled sweep ---------------------------------------------------
    #
    # auto_cycle distills an agent only once its context passes the threshold,
    # so an agent with light days never distilled at all, and on a host that
    # does not report tokens nobody did. Teams ran a nightly script that
    # queued the distill prompt to everyone -- and had to get right, outside
    # the supervisor, the four things that make it safe: once per day across
    # restarts, never into a turn in flight, only agents that did something,
    # and not on the scarce models. Those belong here.

    SWEEP_DEFAULTS = {"enabled": True, "hour_utc": 9, "min_turns_24h": 2,
                      "idempotency_hours": 20.0, "exclude_models": [],
                      "exclude_agents": []}

    def distill_schedule(self) -> dict | None:
        """roster "nightly_distill" (top level or under "policy"), or None."""
        try:
            doc = json.loads((self.team_dir / "roster.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        cfg = doc.get("nightly_distill") or (doc.get("policy") or {}).get("nightly_distill")
        hour = os.environ.get("AGYTEAM_NIGHTLY_DISTILL_HOUR_UTC", "").strip()
        if not cfg and hour:
            cfg = {"hour_utc": int(hour)}
        if not isinstance(cfg, dict) or cfg.get("enabled") is False:
            return None
        return {**self.SWEEP_DEFAULTS, **cfg}

    @staticmethod
    def _epoch(ts: str) -> float | None:
        try:
            return time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
        except (TypeError, ValueError, OverflowError):
            return None

    def _last_sweep(self) -> float | None:
        try:
            rows = self.observer.events("distill_sweep")
        except Exception:
            return None
        stamps = [r.get("epoch") or self._epoch(r.get("ts", "")) for r in rows]
        stamps = [float(x) for x in stamps if x]
        return max(stamps) if stamps else None

    def sweep_candidates(self, cfg: dict) -> tuple[list[str], dict[str, str]]:
        """(agents to distill, {agent: why not}) under this schedule."""
        since = time.time() - 24 * 3600
        try:
            turns = self.observer.events("turn")
        except Exception:
            turns = []
        counts: dict[str, int] = {}
        for t in turns:
            at = self._epoch(t.get("ts", ""))
            if at is not None and at >= since:
                counts[t.get("agent")] = counts.get(t.get("agent"), 0) + 1
        try:
            specs = {a["name"]: a for a in roster_lib.load(self.team_dir / "roster.json")["agents"]}
        except Exception:
            specs = {}
        take, skip = [], {}
        for agent in self.agents:
            model = str(specs.get(agent, {}).get("model") or "")
            if agent in (cfg.get("exclude_agents") or []):
                skip[agent] = "excluded"
            elif model and model in (cfg.get("exclude_models") or []):
                skip[agent] = f"model {model} excluded"
            elif agent in self.inflight:
                skip[agent] = "a turn is in flight"
            elif agent in self._purged_agents:
                skip[agent] = "context purged"
            elif counts.get(agent, 0) < int(cfg.get("min_turns_24h", 2)):
                skip[agent] = f"{counts.get(agent, 0)} turn(s) in 24h"
            else:
                take.append(agent)
        return take, skip

    def distill_sweep(self, scheduled: bool = False, force: bool = False,
                      dry_run: bool = False, min_turns: int | None = None) -> dict:
        """Distill every agent that worked in the last day, at most once a day.

        `scheduled` is the daemon asking: it also requires the configured
        UTC hour to have come. `force` ignores the once-a-day guard. The
        sweep is recorded (event "distill_sweep") before it runs, under a
        lock, so two supervisors on one team cannot both start it.
        """
        if not dry_run and not scheduled and self._driver_elsewhere():
            opts = {"force": force, "min_turns": min_turns}
            return self._delegate("sweep", [None], **opts).get(None) or {
                "ran": False, "reason": "still queued for the supervisor driving this team"}
        cfg = self.distill_schedule()
        if cfg is None:
            if scheduled:
                return {"ran": False, "reason": "no nightly_distill in the roster"}
            cfg = dict(self.SWEEP_DEFAULTS)
        if min_turns is not None:
            cfg["min_turns_24h"] = min_turns
        now = time.time()
        if scheduled and time.gmtime(now).tm_hour < int(cfg.get("hour_utc", 9)):
            return {"ran": False, "reason": "not yet the hour"}
        window = float(cfg.get("idempotency_hours", 20.0)) * 3600
        take, skip = self.sweep_candidates(cfg)
        if dry_run:
            last = self._last_sweep()
            return {"ran": False, "reason": "dry run", "agents": take, "skipped": skip,
                    "last_sweep_hours_ago": None if last is None else round((now - last) / 3600, 1)}
        with filelock.locked(self.team_dir / "distill_sweep"):
            last = self._last_sweep()
            if not force and last is not None and now - last < window:
                return {"ran": False, "reason": f"swept {(now - last) / 3600:.1f}h ago"}
            if not take:
                return {"ran": False, "reason": "nobody qualified", "skipped": skip}
            try:
                self.observer.record_event("distill_sweep", epoch=now, agents=take,
                                           skipped=skip)
            except Exception:
                pass
        self._log(f"[supervisor] distill sweep: {', '.join(take)}"
                  + (f" (skipped: {', '.join(f'{a} -- {w}' for a, w in skip.items())})"
                     if skip else ""))
        results = self.distill_all(take)
        failed = sorted(getattr(self, "distill_failed", set()))
        try:
            self.observer.record_event("distill_sweep_done", agents=take, failed=failed,
                                       memories={a: list(c) for a, c in results.items()})
        except Exception:
            pass
        return {"ran": True, "agents": take, "skipped": skip, "failed": failed,
                "results": results}

    def _maybe_distill_sweep(self) -> None:
        """From the daemon's idle loop: at most one look a minute."""
        now = time.monotonic()
        if now - getattr(self, "_sweep_looked_at", 0.0) < 60:
            return
        self._sweep_looked_at = now
        if self.inflight:
            return
        try:
            self.distill_sweep(scheduled=True)
        except Exception as e:
            self._log(f"[supervisor] distill sweep failed: {type(e).__name__}: {e}")

    # --- distills asked from outside the driving supervisor ----------------

    def _driver_elsewhere(self) -> bool:
        """Is the team driven by a supervisor other than this one?"""
        return getattr(self, "_team_lock", None) is None and self._team_driven_elsewhere()

    def _delegate(self, action: str, agents: list, **options) -> dict:
        """Ask the driving supervisor to `action` each agent, and wait for it.

        Returns {agent: (before, after)} -- for a sweep, {None: its result}
        -- for what it finished within AGYTEAM_DISTILL_WAIT_S; anything
        still queued then is in self.distill_failed, and it still runs.
        """
        ids = {distill_queue.add(self.team_dir, action, a, **options): a for a in agents}
        who = heartbeat.running(self.team_dir) or {}
        names = ", ".join(a for a in agents if a) or "the team"
        self._log(f"  → a supervisor (pid {who.get('pid', '?')}) is driving this team; "
                  f"asked it to {action} {names} between turns")
        deadline = time.monotonic() + config.DISTILL_REQUEST_WAIT_S
        done: dict[str, dict] = {}
        while True:
            try:
                for ev in self.observer.events("distill_request_done"):
                    if ev.get("id") in ids:
                        done[ev["id"]] = ev
            except Exception:
                pass
            if len(done) == len(ids) or time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        out: dict = {}
        self.distill_failed = set()
        for rid, agent in ids.items():
            ev = done.get(rid)
            if ev is None:
                self._log(f"    {agent or action}: still queued; the driving supervisor "
                          f"will run it when {agent or 'the team'} is between turns")
                self.distill_failed.add(agent)
            elif action == "sweep":
                out[None] = ev.get("result") or {}
            else:
                out[agent] = (ev.get("before", 0), ev.get("after", 0))
                if ev.get("failed"):
                    self.distill_failed.add(agent)
                self._log(f"    {agent}: {action} " + (f"failed ({ev.get('reason')})"
                          if ev.get("failed") else
                          f"done ({ev.get('before', 0)} → {ev.get('after', 0)} memories)"))
        self._last_distill_failed = bool(self.distill_failed)
        return out

    def _serve_distill_requests(self) -> int:
        """Run what other processes asked of this driver, for agents that
        are between turns. Blocks the pass while it runs, as the sweep does;
        turns already running carry on."""
        if not distill_queue.path(self.team_dir).exists():
            return 0
        rows = distill_queue.take(
            self.team_dir,
            lambda r: (not self.inflight) if r.get("action") == "sweep"
            else r.get("agent") not in self.inflight)
        for r in rows:
            agent, action = r.get("agent"), r.get("action")
            ev = {"id": r.get("id"), "action": action, "agent": agent, "by": r.get("by")}
            if action == "sweep":
                res = self.distill_sweep(force=bool(r.get("force")), min_turns=r.get("min_turns"))
                ev["result"] = {k: v for k, v in res.items() if k != "results"}
            elif agent not in self.agents:
                ev.update(failed=True, reason="not on the roster")
            else:
                before, after = (self.cycle if action == "cycle" else self.distill)(agent)
                failed = bool(getattr(self, "_last_distill_failed", False))
                ev.update(before=before, after=after, failed=failed)
                if failed:
                    ev["reason"] = "the distill failed; see the supervisor's log"
            try:
                self.observer.record_event("distill_request_done", **ev)
            except Exception:
                pass
        return len(rows)

    def _worked_since_cycle(self, agent: str) -> bool:
        """Has `agent` taken a turn since its conversation was last retired?"""
        try:
            turns = [t for t in self.observer.events("turn") if t.get("agent") == agent]
        except Exception:
            return True                 # cannot tell: do not skip
        if not turns:
            return False
        try:
            retired = [r.get("ts", "") for r in self.runner.retired_conversations()
                       if r.get("agent") == agent]
        except Exception:
            retired = []
        last = max(retired, default="")
        return any((t.get("ts") or "") > last for t in turns)

    def cycle_all(self, agents: list[str] | None = None) -> dict[str, tuple[int, int]]:
        """distill_all, then a fresh conversation for each agent that distilled."""
        if self._driver_elsewhere():
            return self._delegate("cycle", list(agents if agents is not None else self.agents))
        counts = self.distill_all(agents)
        for agent in counts:
            if agent in self.distill_failed:
                self._log(f"    {agent}: cycle aborted because distillation failed")
            elif agent in self._purged_agents:
                self._log(f"    {agent}: cycle aborted (agent context was purged due to anomaly)")
            else:
                self.runner.reset(agent)
                self._log(f"    {agent}: conversation reset")
        return counts

    def cycle(self, agent: str) -> tuple[int, int]:
        """Distill learnings into memory, then reset conversation for `agent`.

        Cycling without distilling first is not permitted.
        Returns (before_count, after_count).
        """
        if self._driver_elsewhere():
            return self._delegate("cycle", [agent]).get(agent, (0, 0))
        if agent in self._purged_agents:
            self._log(f"    {agent}: cycle aborted (agent context was purged due to anomaly)")
            before = self._memory_count(agent)
            return (before, before)

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
        anomaly_threshold = int(os.environ.get("AGYTEAM_ANOMALY_OUTPUT_TOKENS", config.ANOMALY_OUTPUT_TOKENS_THRESHOLD))
        mode = str(getattr(self.runner, "auto_cycle_mode", None)
                   or config.AUTO_CYCLE_MODE).strip().lower()
        if mode == "off":
            return []
        try:
            turn_events = self.observer.events("turn")
        except Exception:
            return []
        if not turn_events:
            return []

        cycled = []
        for agent in self.agents:
            if agent in self._purged_agents:
                continue

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
            out_tok = latest_ev.get("output_tokens")
            # Volume alone does not condemn a turn here either. step() has
            # already seen the reply text and purged if it was degenerate, so by
            # the time we get here an expensive agent is an expensive agent --
            # and the right treatment for a big healthy context is the cycle we
            # were about to skip, not a purge that throws the learning away.
            if out_tok is not None and out_tok > anomaly_threshold:
                self._log(f"  → {agent}: large turn ({out_tok:,} output tokens); "
                          f"cycling rather than purging (step() found no degeneration)")

            # context_tokens, when a runner reports it, is how full the
            # window is; input_tokens is the fallback, and on some hosts it
            # is per-step rather than per-context.
            size = latest_ev.get("context_tokens")
            label = "context_tokens"
            if size is None:
                size, label = latest_ev.get("input_tokens"), "input_tokens"
            if size is None or size <= threshold:
                if self._distilled_above.get(agent) == conv_id:
                    del self._distilled_above[agent]     # compacted below: re-arm
                continue

            pending_msgs = self.pending().get(agent) or []
            if pending_msgs:
                self._log(f"  → skipping auto-cycle for {agent}: work outstanding ({len(pending_msgs)} pending messages)")
                continue

            if mode == "distill_only":
                # The host compacts this conversation itself, and a reset
                # would throw away the pane the operator works in. Distill
                # once each time the context crosses the threshold, not on
                # every turn it stays above it.
                if self._distilled_above.get(agent) == conv_id:
                    continue
                self._log(f"  → auto-distilling {agent} in place ({label} "
                          f"{size:,} > threshold {threshold:,})")
                self.distill(agent)
                if not getattr(self, "_last_distill_failed", False):
                    self._distilled_above[agent] = conv_id
                    cycled.append(agent)
                continue

            self._log(f"  → auto-cycling {agent} (latest turn {label} {size:,} > threshold {threshold:,})")
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
                    if (entry.get("is_retro_leader") or entry.get("retro_leader") or
                        entry.get("is_principal") or entry.get("principal") or
                        entry.get("is_gatekeeper") or entry.get("gatekeeper")):
                        return True
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

    def _retro_no_content_error(self, leader: str, leader_reply: str,
                                channels: dict) -> str:
        """Say WHICH channel was empty, not just that the report failed.

        The project's own rule about silent zeros, applied to itself. A
        turn-completion signal means the agent answered and the supervisor
        could not hear it; that is not the same as the agent saying nothing,
        and a report that conflates them sends whoever reads it looking for a
        model problem that is not there.
        """
        got_text = bool(leader_reply.strip()) and not leader_reply.startswith("[error:")
        recorded = retro_store.path(self.team_dir).exists()
        if not got_text and not recorded:
            return (f"{leader} produced neither a recorded answer nor reply "
                    f"text. The runner reported the turn ended but returned "
                    f"nothing readable, and {retro_store.FILENAME} was never "
                    f"written — so it cannot be told from here whether the "
                    f"leader answered. If this runner cannot return "
                    f"transcripts, check that record_retro reached the same "
                    f"team directory the supervisor is reading "
                    f"({self.team_dir}).")
        if not got_text:
            return (f"{leader} returned no reply text and recorded no "
                    f"retrospective answer of its own this run "
                    f"({channels['store']} teammate answer(s) were recorded). "
                    f"Ask the leader to call record_retro.")
        return ("Leader response failed to provide the three required "
                "sections in order (1. What went well, 2. What did not, "
                "3. What should we change), and no record_retro answer was "
                "recorded to fall back on.")

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

        # Everything recorded from here on belongs to this retro. Taken before
        # the first wake so a slow turn cannot land outside its own window.
        retro_since = time.strftime("%Y-%m-%d %H:%M:%S")
        channels = {"store": 0, "reply": 0}

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

            # Disk first, transcript second. A runner that cannot return text
            # is explicitly allowed by the Runner contract, and this was the
            # one caller that quietly required it.
            recorded = retro_store.latest_by_agent(self.team_dir, retro_since).get(agent)
            if recorded:
                reply = retro_store.as_reflection(recorded)
                channels["store"] += 1
            elif reply.strip() and not reply.startswith("[error:"):
                channels["reply"] += 1

            if len(reply) > config.RETRO_MAX_REFLECTION_CHARS:
                trunc_msg = "\n[... reflection truncated to character cap ...]"
                keep_chars = max(0, config.RETRO_MAX_REFLECTION_CHARS - len(trunc_msg))
                reply = reply[:keep_chars].rstrip() + trunc_msg

            reflections[agent] = reply
            if not self.quiet:
                first = reply.strip().splitlines()[0] if reply.strip() else ""
                via = "recorded" if recorded else "reply text"
                self._log(f"    {agent} ({via}): {first[:120]}")

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

        # Same precedence as the reflections: what the leader recorded beats
        # what the leader said, because only one of the two is guaranteed to
        # exist. Five agents woken and four minutes of model time produced a
        # report reading "(missing or invalid)" three times over, on a runner
        # doing exactly what the contract permits.
        sections = parse_retro_sections(leader_reply)
        leader_record = retro_store.latest_by_agent(
            self.team_dir, retro_since).get(leader)
        if leader_record:
            sections = parse_retro_sections(retro_store.as_reflection(leader_record))
            channels["store"] += 1
        elif sections:
            channels["reply"] += 1
        if not sections:
            err = self._retro_no_content_error(leader, leader_reply, channels)
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
        for agent, turn in list(self.inflight.items()):
            if not getattr(self.runner, "resumable", False):
                try:
                    self.runner.cancel(turn["handle"])
                except Exception:
                    pass
        self._save_ledger()
        # Only our own heartbeat: a supervisor that was refused the team must
        # not erase the live one's.
        try:
            beat = json.loads(self.heartbeat_path.read_text(encoding="utf-8"))
            if beat.get("pid") in (os.getpid(), None):
                self.heartbeat_path.unlink()
        except (OSError, ValueError):
            pass
        self._release_team()
        self.runner.close()
        for t in self.transports.values():
            if hasattr(t, "close") and callable(t.close):
                t.close()
        if hasattr(self.user_transport, "close") and callable(self.user_transport.close):
            self.user_transport.close()
        try:
            self.observer.close()
        except Exception:
            pass


def distill(agent: str, runner=None, team_dir=None) -> tuple[int, int]:
    """Wake `agent` to distill learnings into memory, returning (before_count, after_count)."""
    r = runner or load_team_runner(team_dir)
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
    r = runner or load_team_runner(team_dir)
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

    r = runner or load_team_runner(team_dir)
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

    # Work reviews only. Norm adoptions share the file (kind == "norm") and
    # are a decision the team made about itself, not verification of anyone's
    # work; counting them is the laundering record_review's docstring names.
    # Rows predating the field are work, as _approved_reviews_count reads them.
    approved_work = [r for r in reviews if r.get("verdict") == "approved"
                     and r.get("kind", "work") == "work"]

    raw_episodes = [ev for ev in events if ev.get("event") == "episode"]
    episodes_data = []
    prev_ts = ""
    for idx, ep in enumerate(raw_episodes, 1):
        ep_reviewed = ep.get("reviewed")
        ep_ts = ep.get("ts") or ""
        if ep_reviewed is None:
            # Older episodes carry no flag. A review can only have verified an
            # episode if it was recorded during it -- after the previous one
            # ended and by the time this one did. This used to count any
            # approved review anywhere in the history, so one approval marked
            # every unflagged episode, before it and after, as reviewed.
            ep_reviewed = bool(ep_ts) and any(
                prev_ts < (r.get("ts") or "") <= ep_ts for r in approved_work)
        else:
            ep_reviewed = bool(ep_reviewed)
        prev_ts = ep_ts or prev_ts

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
                "kind": r.get("kind", "work"),
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
        work = [r for r in reviews if r.get("kind", "work") == "work"]
        appr = sum(1 for r in work if r.get("verdict") == "approved")
        norms = len(reviews) - len(work)
        lines.append(f"  Work reviews: {len(work)} ({appr} approved, "
                     f"{len(work) - appr} other)"
                     + (f"; norm adoptions: {norms}" if norms else ""))
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


def _find_principal(team_dir: Path | str | None, agents: list[str]) -> str:
    """Find the principal agent to address user queries to.

    Checks declared capabilities (is_principal/principal, then is_gatekeeper/gatekeeper)
    in roster.json, then falls back to 'manager', 'tpm', or agents[0].
    """
    if not agents:
        return ""
    td = Path(team_dir) if team_dir else None
    if td:
        try:
            roster_path = td / "roster.json"
            if roster_path.exists():
                roster = roster_lib.load(roster_path)
                entries = roster.get("agents", [])
                for entry in entries:
                    name = entry.get("name")
                    if name in agents:
                        if entry.get("is_principal") or entry.get("principal"):
                            return name
                for entry in entries:
                    name = entry.get("name")
                    if name in agents:
                        if entry.get("is_gatekeeper") or entry.get("gatekeeper"):
                            return name
                for entry in entries:
                    name = entry.get("name")
                    if name in agents:
                        role = entry.get("role", "").lower()
                        if "manager" in role or "faces outward" in role:
                            return name
        except Exception:
            pass
    if "manager" in agents:
        return "manager"
    if "tpm" in agents:
        return "tpm"
    return agents[0]


def _run_chat_loop(sup: "Supervisor", agents: list[str]) -> None:
    default_to = _find_principal(sup.team_dir, agents)
    print("=== AgyTeam Interactive Chat ===")
    print(f"Messages default to '{default_to}'. To address a specific agent, prefix with 'agent:' (e.g. 'coder: ...').")
    print("Type '/quit' or 'exit' or press Ctrl+C to leave.\n")
    user_t = load_transport("user")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting chat.")
            break
        if not line:
            continue
        if line.lower() in ("/quit", "/exit", "exit", "quit"):
            print("Exiting chat.")
            break

        if ":" in line:
            target, _, text = line.partition(":")
            target, text = target.strip(), text.strip()
            if target in agents:
                to, content = target, text
            else:
                to, content = default_to, line
        else:
            to, content = default_to, line

        if not content:
            continue

        user_t.send(to, content)
        sup.run_until_idle()
        user_msgs = user_t.fetch()
        for m in user_msgs:
            print(f"\n[{m.sender} → you] {m.content}")
        if hasattr(user_t, "acknowledge") and callable(user_t.acknowledge):
            try:
                user_t.acknowledge(user_msgs)
            except TypeError:
                user_t.acknowledge()
        print()


def _agents_from_roster(team_dir: Path) -> list[str]:
    return [a["name"] for a in roster_lib.load(team_dir / "roster.json")["agents"]]


def team_settings(team_dir) -> dict:
    """The roster's "supervisor" block: how this team is meant to be run.

        "supervisor": {"runner": "agyteam.runner_host:HostRunner",
                       "runner_config": {...}, "manager": "at-Ada",
                       "inbox_pull": true}

    Kept with the team so every way of starting its supervisor -- a
    terminal, a scheduler, a chat app's Start button -- runs it the same way.
    Started from a client with none of the launcher's environment, a team
    fell back to the default runner, its wakes failed, its agents lost their
    conversations, and the manager was guessed.
    """
    try:
        doc = json.loads((Path(team_dir) / "roster.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    block = doc.get("supervisor") if isinstance(doc, dict) else None
    return dict(block) if isinstance(block, dict) else {}


def apply_team_settings(team_dir, runner_flag: str | None = None) -> dict:
    """Make the team's runner (runner.team_runner) this process's: exported
    as AGYTEAM_RUNNER / AGYTEAM_RUNNER_CONFIG for runner.load() and for the
    agents' processes it starts."""
    settings = team_settings(team_dir) if team_dir is not None else {}
    spec, config, where = runner_lib.team_runner(team_dir, runner_flag)
    if where in ("--runner", "roster"):
        os.environ["AGYTEAM_RUNNER"] = spec
        if config:
            os.environ["AGYTEAM_RUNNER_CONFIG"] = json.dumps(config)
        else:
            os.environ.pop("AGYTEAM_RUNNER_CONFIG", None)
    source = {"runner": where}
    if settings.get("manager"):
        source["manager"] = "roster"
    settings["_source"] = source
    return settings


def load_team_runner(team_dir=None, runner_flag: str | None = None):
    """The runner this team is meant to run on (see apply_team_settings)."""
    if team_dir is None and os.environ.get("AGYTEAM_TEAM_DIR"):
        team_dir = Path(os.environ["AGYTEAM_TEAM_DIR"])
    apply_team_settings(team_dir, runner_flag=runner_flag)
    return runner_lib.load()


def main(argv=None, runner=None):
    ap = argparse.ArgumentParser(
        prog="agyteam.supervisor",
        description="Wake agents when teammates message them")
    from . import __version__
    ap.add_argument("--version", action="version", version=f"agyteam {__version__}")
    ap.add_argument("--distill", metavar="AGENT",
                    help="Distill learnings into memory for AGENT, then exit")
    ap.add_argument("--cycle", metavar="AGENT",
                    help="Distill learnings and reset conversation for AGENT, then exit")
    ap.add_argument("--distill-all", action="store_true",
                    help="Distill learnings for all agents on the roster, then exit")
    ap.add_argument("--cycle-all", action="store_true",
                    help="Distill and reset conversations for all agents on the roster, then exit")
    ap.add_argument("--nightly-distill", action="store_true",
                    help="Distill every agent with recent turns, at most once a day "
                         "(the roster's nightly_distill settings; --force to ignore "
                         "the once-a-day guard), then exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="With --nightly-distill: say who would be distilled, and stop")
    ap.add_argument("--min-turns", type=int, default=None,
                    help="With --nightly-distill: turns in the last 24h an agent needs")
    ap.add_argument("--say", metavar="'agent: message'",
                    help="Send this, then run until the team goes idle")
    ap.add_argument("--chat", action="store_true",
                    help="Interactive chat session with the team")
    ap.add_argument("--daemon", action="store_true",
                    help="Stay up and react to mail as it arrives")
    ap.add_argument("--status", action="store_true",
                    help="Show who has mail waiting, then exit")
    ap.add_argument("--cost", action="store_true",
                    help="Show team usage and cost summary, then exit")
    ap.add_argument("--retro", action="store_true",
                    help="Run structured retrospective over recent work, then exit")
    ap.add_argument("--retro-leader", metavar="AGENT", default=None,
                    help="Agent leading the retrospective (default: the "
                         "roster's is_retro_leader, else the accountable role)")
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
                    help="Agent acting as manager (default: the roster's "
                         "supervisor.manager, else auto-detect)")
    ap.add_argument("--runner", metavar="MODULE:CLASS", default=None,
                    help="Runner for this run (default: the roster's supervisor.runner, "
                         "else AGYTEAM_RUNNER, else the agy CLI)")
    ap.add_argument("--waive-review", metavar="TASK_ID", default=None,
                    help="Operator's 'ship it without review' for a requires_review "
                         "task; recorded as a waiver, never as a review. Pairs with "
                         "--by and --reason")
    ap.add_argument("--by", default="user", help="who is waiving (with --waive-review)")
    ap.add_argument("--reason", default="", help="why (with --waive-review)")
    ap.add_argument("--inbox-pull", action="store_true",
                    help="Wake agents with a one-line summary and let them read "
                         "their mail with check_inbox, instead of pasting it "
                         "into the prompt")
    args = ap.parse_args(argv)

    # Set the team directory and nothing else. This used to also write
    # AGYTEAM_DURABLE_DIR, which meant the SAME team_dir put agent memory in
    # two different places depending on whether you came through the CLI (which
    # set durable) or the library (which did not), with no error either way.
    # scope now derives everything from the team directory, so one variable is
    # the whole story.
    team_dir = scope.team_dir(args.team_dir)
    os.environ["AGYTEAM_TEAM_DIR"] = str(team_dir)

    if args.waive_review:
        try:
            entry = tasks_lib.waive_review(team_dir, args.waive_review,
                                           by=args.by, reason=args.reason)
        except tasks_lib.TaskError as e:
            sys.exit(f"error: {e}")
        print(f"review waived for {args.waive_review} by {entry['reviewer']}: "
              f"{entry['findings']}")
        return

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
        sup = Supervisor(agents, _InspectOnly(), team_dir=team_dir, quiet=True,
                         inspect_only=True)
        try:
            try:        # read, never loaded: loading acts on it
                running = json.loads(sup.ledger_path.read_text(encoding="utf-8"))
                running = running if isinstance(running, dict) else {}
            except (OSError, ValueError):
                running = {}
            for agent, msgs in sup.pending().items():
                turn = (f", turn running since {running[agent].get('ts', '?')}"
                        if isinstance(running.get(agent), dict) else "")
                if msgs is None:
                    print(f"  {agent}: (transport cannot report queue depth){turn}")
                else:
                    senders = ", ".join(sorted({m.sender for m in msgs}))
                    print(f"  {agent}: {len(msgs)} waiting from {senders}{turn}"
                          if msgs else f"  {agent}: {'busy' if turn else 'idle'}{turn}")
        finally:
            sup.close()
        return

    settings = apply_team_settings(team_dir, runner_flag=args.runner)
    r = runner or runner_lib.load()
    sup = Supervisor(agents, r, team_dir=team_dir,
                     max_hops=args.max_hops, poll=args.poll, quiet=args.quiet,
                     stop_on_answer=not args.no_stop_on_answer,
                     require_review=not args.no_require_review,
                     manager=args.manager or settings.get("manager"),
                     inbox_pull=True if args.inbox_pull else settings.get("inbox_pull"))
    sup.settings_source = settings.get("_source", {})
    try:
        if args.retro:
            retro_leader = resolve_retro_leader(args.retro_leader, agents,
                                                sup.team_dir, sup._find_manager())
            if retro_leader not in agents:
                sys.exit(f"unknown retro leader "
                         f"{args.retro_leader or retro_leader!r}; roster has: "
                         f"{', '.join(agents)}")
            res = sup.retro(
                leader=retro_leader,
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
        elif args.nightly_distill:
            out = sup.distill_sweep(force=args.retro_force, dry_run=args.dry_run,
                                    min_turns=args.min_turns)
            print(json.dumps({k: v for k, v in out.items() if k != "results"}, indent=2))
        elif args.distill_all:
            sup.distill_all(agents)
        elif args.cycle_all:
            sup.cycle_all(agents)
        elif args.chat:
            _run_chat_loop(sup, agents)
        elif args.say:
            if ":" in args.say:
                cand_to, _, cand_content = args.say.partition(":")
                cand_to, cand_content = cand_to.strip(), cand_content.strip()
                if cand_to in agents:
                    to, content = cand_to, cand_content
                else:
                    to = _find_principal(sup.team_dir, agents)
                    content = args.say.strip()
            else:
                to = _find_principal(sup.team_dir, agents)
                content = args.say.strip()

            if to not in agents:
                sys.exit(f"unknown agent {to!r}; roster has: {', '.join(agents)}")
            load_transport("user").send(to, content)
            sup.run_until_idle()
            # The agents' stdout is just a self-summary; what they actually
            # addressed to the user is the real answer, so show it.
            user_t = load_transport("user")
            user_msgs = user_t.fetch()
            for m in user_msgs:
                print(f"\n[{m.sender} → you] {m.content}")
            if hasattr(user_t, "acknowledge") and callable(user_t.acknowledge):
                try:
                    user_t.acknowledge(user_msgs)
                except TypeError:
                    user_t.acknowledge()
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
