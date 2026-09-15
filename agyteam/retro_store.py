"""Where retrospective answers land, so the retro does not depend on transcripts.

## Why this file exists

`distill()` and `retro()` are the two halves of the convergence loop, and only
one of them worked. Measured on a five-agent run: distill produced memories for
four of five agents; retro produced a report whose every section read
"(missing or invalid)", and NORMS.md was never written — so the loop

    retro -> NORMS.md -> brief -> changed behaviour

was inert, and team-level learning did not happen at all while agent-level
learning did.

The difference is the write path. `distill` measures files on disk. `retro`
parsed the string returned by `runner.wake()`, and the Runner contract says in
as many words that a runner need not be able to read what the agent said:

    "a runner that can only report 'done' loses nothing the rest of the
     system depends on"

That sentence was false for exactly one caller. Every test double in the suite
returns prose from `wake()`, so no test could detect the dependency — the
mocks were more capable than the contract, which is the substitution pattern
wearing a different hat.

So: agents publish their reflections over the bus like everything else, into an
append-only file, and the retro reads disk. Apply the mechanism that already
works rather than inventing a second one.

## Validation happens at recording time, not at parsing time

A stub rejected an hour later, in a report nobody reads, teaches nothing. A
stub rejected in the tool result arrives while the agent still has the context
to fix it and can simply answer again. The rejections are sentences the model
can act on, not error codes.
"""
import json
import os
import time
from pathlib import Path

FILENAME = "retro_inbox.jsonl"

# Answers that occupy the field without filling it. Matched on the whole
# stripped, lowercased value -- "none of the tests were run" is a real answer
# and must not trip this.
_STUBS = {
    "", "-", "--", "n/a", "na", "none", "nothing", "nil", "null", "todo",
    "tbd", "no", "yes", "ok", "fine", "good", "all good", "no comment",
    "nothing to add", "nothing to report", "no issues", "same as above",
    "see above", "as above", "n/a.", "none.", "nothing.",
}

MIN_CHARS = 12

FIELDS = ("went_well", "did_not", "should_change")

_ASK = {
    "went_well": "name something the record shows the team did that worked",
    "did_not": "name something the record shows went wrong, or cost more than "
               "it should have",
    "should_change": "propose one concrete change — a rule a gate could check, "
                     "or an explicit 'no change, and here is why'",
}


def path(team_dir: Path | str) -> Path:
    return Path(team_dir) / FILENAME


def validate(went_well: str, did_not: str, should_change: str) -> str | None:
    """Return an error the model can act on, or None if the answer is usable."""
    values = {"went_well": went_well, "did_not": did_not,
              "should_change": should_change}
    bad = []
    for field in FIELDS:
        v = values[field]
        if not isinstance(v, str):
            bad.append(f"{field} must be text")
            continue
        s = v.strip()
        if s.lower() in _STUBS:
            bad.append(f"{field} is {s or 'empty'!r}, which answers nothing — "
                       f"{_ASK[field]}")
        elif len(s) < MIN_CHARS:
            bad.append(f"{field} is {len(s)} characters — {_ASK[field]}")
    if not bad:
        return None
    return ("[error: this retrospective answer was not recorded. "
            + " Also, ".join(bad).replace(" Also, ", "; ") + "]")


def record(team_dir: Path | str, agent: str, went_well: str, did_not: str,
           should_change: str, role: str = "participant",
           probe: bool = False) -> str:
    """Append one agent's answers. Returns the tool-result string.

    `probe` is for a preflight exercising this path for real. It must write to
    the same file through the same code -- a probe against a copy proves
    nothing about the original -- but it must not be read back as though an
    agent had reflected, or the next retro treats "doctor probe: the bus
    round-tripped" as that agent's view of the week.
    """
    err = validate(went_well, did_not, should_change)
    if err:
        return err
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "agent": agent,
        "role": role,
        "kind": "probe" if probe else "retro",
        "went_well": went_well.strip(),
        "did_not": did_not.strip(),
        "should_change": should_change.strip(),
    }
    p = path(team_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return (f"[retrospective answer recorded for {agent}. The retro reads this "
            f"file directly; you do not need to repeat it in your reply.]")


def read(team_dir: Path | str, since: str = "") -> list[dict]:
    """Entries recorded at or after `since`. Unparseable lines are skipped."""
    p = path(team_dir)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and (rec.get("ts") or "") >= since:
            out.append(rec)
    return out


def latest_by_agent(team_dir: Path | str, since: str = "") -> dict[str, dict]:
    """Most recent real entry per agent, so a retry supersedes a first attempt.

    Preflight probes are skipped: they went through this file deliberately, and
    reading one back as a reflection would put a test string in a retro report.
    """
    out: dict[str, dict] = {}
    for rec in read(team_dir, since):
        agent = rec.get("agent")
        if agent and rec.get("kind", "retro") != "probe":
            out[agent] = rec
    return out


def as_reflection(rec: dict) -> str:
    """One entry rendered the way a transcript reflection would have read."""
    return (f"1. What went well\n{rec.get('went_well', '')}\n\n"
            f"2. What did not\n{rec.get('did_not', '')}\n\n"
            f"3. What should we change\n{rec.get('should_change', '')}")


def team_dir_from_env() -> Path:
    env = os.environ.get("AGYTEAM_TEAM_DIR")
    if env:
        return Path(env)
    from . import scope
    return scope.load().team_dir()
