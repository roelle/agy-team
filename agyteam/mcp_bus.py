"""MCP stdio server giving one agent peer-to-peer (A2A) messaging.

Antigravity exposes no native peer messaging publicly — Teamwork coordinates
through workspace artifacts, and the SDK offers only subagents. This server
supplies the channel, and because it is MCP it works identically in the agy CLI
(plugin mcp_config.json), the desktop hub, and SDK agents.

The wire is pluggable (see agyteam/transport.py): the default file bus works
anywhere agents share a filesystem, and AGYTEAM_BUS_TRANSPORT swaps in a native
implementation without changing the agent-facing tools.

Delivery is inbox-based rather than push: when the harness owns the agent loop
nobody can force a peer to take a turn, so peers leave mail and agents read it.
The teammate/worker distinction stays structural — teammates are *messaged*
through this server; workers are *spawned* by the harness and never appear here.

Identity: `python -m agyteam.mcp_bus <team_dir> <agent_name>` (SDK path), or
omit the arguments and set AGYTEAM_AGENT (agy CLI path, where mcp_config.json is
static and the session supplies identity). Refuses to start nameless rather than
guessing, so messages can never be filed under the wrong agent.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .mcp_base import fit, serve, string, tool
from . import heartbeat, policy
from .transport import KINDS, Transport, load

# The repo this module lives in, for resolving relative proof paths and the
# project venv without baking any one machine's layout into the file.
_REPO_ROOT = Path(__file__).resolve().parent.parent

TOOLS = [
    tool("send_to_teammate",
         "Send a message to a persistent teammate (a peer agent with its own "
         "memory and role) or to 'user'. Delivery is asynchronous: it lands in "
         "their inbox and they act on it when they next check. Include full "
         "context and a concrete ask — they cannot see your conversation.",
         {"to": string("Teammate name from list_teammates, or 'user'"),
          "content": string("The message: context, the ask, where to put results"),
          "kind": {"type": "string", "enum": list(KINDS),
                   "description": "What this message is. deliverable, question, "
                                  "blocker, review and reminder wake the recipient "
                                  "(as does the default, work); ack, fyi and status "
                                  "are left in their inbox for their next wake and "
                                  "wake nobody -- use them for anything that needs "
                                  "no action"},
          "task_id": string("The task this is about, if any")},
         ["to", "content"]),
    tool("broadcast",
         "Send one message to every teammate at once. Use sparingly — for "
         "announcements, not for delegating work (delegate by name instead).",
         {"content": string("The announcement")}, ["content"]),
    tool("check_inbox",
         "Read and clear messages other agents have sent you, oldest first. "
         "Check at the start of a task and again before reporting a task "
         "finished — a teammate may have answered your question while you "
         "were working. If it says more are unread, call it again.",
         {}),
    tool("list_teammates",
         "List your teammates (persistent peers you can message) and their "
         "roles. Workers you spawn yourself are not teammates and are not "
         "listed here.", {}),
    tool("record_review",
         "Record a verification review of a feature, change, or task. "
         "Must specify what was reviewed, who authored the work, verdict "
         "('approved' or 'changes_requested'), proof_file (path to test file "
         "containing executable assertions), and findings. You cannot review "
         "your own work.",
         {"what": string("What was reviewed (feature, branch, PR, or task)"),
          "author": string("Agent whose work this review verifies (not you)"),
          "verdict": string("Verdict: 'approved' or 'changes_requested'"),
          "proof_file": string("Path to a test file containing executable assertions"),
          "findings": string("Observations, defect analysis, or behavior notes"),
          "task_id": string("The task this review verifies, if it has one; a task "
                            "that requires_review closes only on an approved "
                            "review naming it")},
         ["what", "author", "verdict", "proof_file"]),
    tool("list_reviews",
         "List durable verification reviews recorded for this team, newest "
         "first. A long history is cut short with a count of what was left "
         "out; pass task_id to see one task's reviews.",
         {"task_id": string("Only the reviews naming this task")}),
    tool("record_retro",
         "Record your answers to the three retrospective questions. Call this "
         "when asked to reflect, instead of (or as well as) writing the "
         "answers in your reply — the retrospective reads this record "
         "directly, and on some hosts your reply text never reaches it. "
         "Each answer must be a real sentence grounded in the work record; "
         "stubs like 'none' or 'n/a' are rejected.",
         {"went_well": string("What went well, grounded in the record"),
          "did_not": string("What did not go well, or cost more than it should"),
          "should_change": string(
              "One concrete change — a rule a gate could check, or an "
              "explicit 'no change, and here is why'")},
         ["went_well", "did_not", "should_change"]),
    tool("list_retros",
         "The team's retrospectives: the latest report's outcome and each "
         "teammate's most recent recorded answers. record_retro writes these; "
         "this reads them back.",
         {}),
]

# Roster mutation is off unless AGYTEAM_ROSTER_ADMIN=1 *and* the transport
# supports it. Team composition is the operator's call, not something an agent
# should do to itself mid-task.
ADMIN_TOOLS = [
    tool("roster_add",
         "Add a teammate to the roster. Takes effect for sessions started "
         "afterwards; existing sessions learn of them on their next start.",
         {"name": string("Short agent name, e.g. 'qa'"),
          "role": string("One-line description of what this agent owns")},
         ["name", "role"]),
    tool("roster_remove", "Remove a teammate from the roster.",
         {"name": string("Agent name to remove")}, ["name"]),
    tool("grant_workspace",
         "Grant a workspace directory path to the team or a specific agent.",
         {"workspace": string("Path to workspace directory"),
          "agent": string("Optional agent name. If omitted, granted to the whole team")},
         ["workspace"]),
    tool("revoke_workspace",
         "Revoke a workspace directory path from the team or a specific agent.",
         {"workspace": string("Path to workspace directory"),
          "agent": string("Optional agent name. If omitted, revoked from the whole team")},
         ["workspace"]),
    tool("stop_team",
         "Stop the running team by writing the .stop sentinel.",
         {"reason": string("Optional reason for stopping the team")}),
    tool("start_team",
         "Resume team execution by removing the .stop sentinel.",
         {}),
    tool("team_status",
         "Get current team configuration, agents, workspaces, and stop state.",
         {}),
]


def _list_teammates(t: Transport) -> str:
    rows = [f"- {a['name']}: {a.get('role', '')}" for a in t.teammates()]
    return "\n".join(rows + ["- user: the human you work for"])


def _check_inbox(t: Transport) -> str:
    msgs = t.fetch()
    if not msgs:
        return "[inbox empty]"
    # Oldest first, and only as many as fit inline; only those are marked
    # read, so the rest are still there on the next call.
    text, shown = fit([m.render() for m in msgs],
                      "[{n} more unread: call check_inbox again]")
    if hasattr(t, "acknowledge") and callable(t.acknowledge):
        t.acknowledge(msgs[:shown])
    return text


def _team_dir(t: Transport) -> Path:
    if hasattr(t, "dir") and t.dir:
        return Path(t.dir)
    env = os.environ.get("AGYTEAM_TEAM_DIR")
    if env:
        return Path(env)
    from . import scope
    return scope.load().team_dir()


def _reviews_path(t: Transport) -> Path:
    return _team_dir(t) / "reviews.jsonl"


from .activity import author_activity as _author_activity  # noqa: E402
from .activity import episode_start as _episode_start  # noqa: E402
from .activity import jsonl as _jsonl  # noqa: E402


def _extract_crash_detail(proc: subprocess.CompletedProcess) -> str:
    combined = f"{proc.stdout}\n{proc.stderr}".strip()
    if proc.returncode == 5 or "no tests ran" in combined:
        return "no tests collected"

    e_lines = [
        line.strip()[4:].strip()
        for line in combined.splitlines()
        if line.strip().startswith("E   ")
    ]
    if e_lines:
        first_line = e_lines[-1].splitlines()[0].strip()
        if first_line:
            return first_line[:120]

    for line in combined.splitlines():
        line_s = line.strip()
        for err_prefix in ("SyntaxError:", "ModuleNotFoundError:", "ImportError:", "NameError:", "TypeError:", "ValueError:"):
            if err_prefix in line_s:
                idx = line_s.index(err_prefix)
                return line_s[idx:].strip()[:120]

    for line in combined.splitlines():
        line_s = line.strip()
        if "ERROR collecting" in line_s:
            return line_s.strip("_ ")

    if "error during collection" in combined or "errors during collection" in combined:
        return "collection error"

    return f"exit code {proc.returncode}"


_PROOF_PY: tuple = ()          # resolved once; probing costs a subprocess


def _proof_interpreter() -> tuple:
    """An interpreter that can actually run pytest, and what was tried.

    The proof gate is the one thing in this server that needs a third-party
    package, and the plugin install is deliberately dependency-free -- pure
    stdlib under a bare system python3. Where those two meet, pytest is absent,
    and the gate used to report that as `proof_file did not pass (exit code
    1)`. Measured on an installed plugin: a proof file containing one passing
    test came back rejected, with the real reason -- "No module named pytest"
    -- on a stderr nobody reads.

    That is the failure this whole project is about, sitting inside its most
    load-bearing check: a check that could not run, reported as a check that
    found something. An agent reading that error does the rational thing and
    rewrites a proof that was fine.

    So: find an interpreter that can import pytest, or say plainly that the
    gate did not run. Never both silently.
    """
    global _PROOF_PY
    if _PROOF_PY:
        return _PROOF_PY

    candidates, tried = [], []
    env_py = os.environ.get("AGYTEAM_PROOF_PYTHON")
    if env_py:
        candidates.append(env_py)
    # The repo this module lives in, then the working tree the agent is in.
    # Never a machine-specific absolute path: this file travels, and one that
    # was baked in shipped to a machine where the path did not exist.
    candidates.append(str(_REPO_ROOT / ".venv" / "bin" / "python"))
    candidates.append(str(Path.cwd() / ".venv" / "bin" / "python"))
    candidates.append(sys.executable)

    for c in candidates:
        if c in tried:
            continue
        tried.append(c)
        if c != sys.executable and not Path(c).exists():
            continue
        try:
            probe = subprocess.run([c, "-c", "import pytest"],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            _PROOF_PY = (c, tried)
            return _PROOF_PY
    return (None, tried)


def _record_review(t: Transport, a: dict) -> str:
    what = a.get("what", "")
    if not isinstance(what, str) or not what.strip():
        return "[error: what is required and cannot be empty]"
    what = what.strip()

    # A review of your own work is not a review. The first cold-start
    # convergence run satisfied the gate exactly that way -- the manager
    # approved her own audit -- and nothing structural could see it, because
    # the record had a reviewer and no author. The reviewer's identity comes
    # from the session, not from this argument, so the check cannot be
    # satisfied by misstating who you are.
    #
    # Misstating who DID the work was the remaining hole, and it was not
    # theoretical: a manager recorded author="coder" for an episode in which
    # coder was never woken and made zero tool calls, and the literal string
    # "unknown" passed too. "Visible on the bus" is not the same as checked,
    # and nothing was reading the bus to check it. These three tests are that
    # reading -- on the roster, not yourself, and demonstrably active.
    author = a.get("author", "")
    if not isinstance(author, str) or not author.strip():
        return ("[error: author is required — name the agent whose work this "
                "review verifies]")
    author = author.strip()
    if author == t.me:
        return ("[error: you cannot review your own work — route it to a "
                "teammate for independent review]")

    # Teammates, not the whole roster: the author is by definition someone
    # other than you. An unreadable or single-agent roster names nobody this
    # could discriminate between, so the check is skipped rather than guessed.
    try:
        teammate_names = {a_["name"] for a_ in t.teammates()}
    except Exception:
        teammate_names = set()
    if teammate_names and author not in teammate_names:
        known = ", ".join(sorted(teammate_names))
        return (f"[error: '{author}' is not a teammate on this roster. Name "
                f"the agent whose work you are reviewing: {known}]")

    team_dir = _team_dir(t)
    since = _episode_start(team_dir)
    verifiable, evidence = _author_activity(team_dir, author, since)
    if verifiable and not evidence:
        window = f" since {since}" if since else ""
        return (f"[error: '{author}' has no recorded activity{window} — no bus "
                f"messages, no turns, no tool calls. A review names the agent "
                f"whose work it verifies; if you did this work yourself it "
                f"cannot be reviewed by you, and if a teammate did it, they "
                f"have not run yet]")
    author_verified = bool(evidence)

    verdict = a.get("verdict", "")
    if verdict not in ("approved", "changes_requested"):
        return f"[error: verdict must be 'approved' or 'changes_requested', got {verdict!r}]"

    proof_file = a.get("proof_file")
    if proof_file is None:
        return "[error: proof_file is required and cannot be empty]"
    if not isinstance(proof_file, str) or not proof_file.strip():
        return "[error: proof_file is required and cannot be empty]"
    proof_file = proof_file.strip()

    proof_path = Path(proof_file)
    if proof_path.is_absolute():
        if not proof_path.is_file():
            return f"[error: proof_file not found or not a file: '{proof_file}']"
    else:
        team_dir = _team_dir(t)
        if (Path.cwd() / proof_path).is_file():
            proof_path = (Path.cwd() / proof_path).resolve()
        elif (team_dir / proof_path).is_file():
            proof_path = (team_dir / proof_path).resolve()
        elif (_REPO_ROOT / proof_path).is_file():
            proof_path = (_REPO_ROOT / proof_path).resolve()
        else:
            return f"[error: proof_file not found or not a file: '{proof_file}']"

    python_bin, tried = _proof_interpreter()
    if python_bin is None:
        return ("[error: the review gate could not run: no interpreter with "
                "pytest was found, so this proof was never executed and the "
                "review has NOT been recorded. This is an environment failure, "
                "not a verdict on the work. Tried: " + ", ".join(tried) +
                ". Set AGYTEAM_PROOF_PYTHON to a python that can "
                "`import pytest`.]")

    try:
        proc = subprocess.run(
            [python_bin, "-m", "pytest", str(proof_path), "-q"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "[error: verification failed: proof_file timed out after 30s]"
    except Exception as e:
        return f"[error: verification failed: could not execute proof_file: {e}]"

    if verdict == "approved":
        if proc.returncode == 5 or "no tests ran" in proc.stdout:
            return "[error: verification failed: proof_file contained no tests]"
        if proc.returncode != 0 or "passed" not in proc.stdout:
            details = f"{proc.stdout}\n{proc.stderr}".strip()
            if details:
                return f"[error: verification failed: proof_file did not pass (exit code {proc.returncode})]\n{details}"
            return f"[error: verification failed: proof_file did not pass (exit code {proc.returncode})]"
    elif verdict == "changes_requested":
        if proc.returncode == 0:
            return "[error: proof_file passed cleanly; changes_requested requires a failing reproduction case]"
        if proc.returncode == 5 or "no tests ran" in proc.stdout:
            return "[error: proof_file crashed or failed collection (no tests collected); changes_requested requires an executed failing assertion]"

        has_collection_err = (
            "ERROR collecting" in proc.stdout
            or "error during collection" in proc.stdout
            or "errors during collection" in proc.stdout
            or "ERROR collecting" in proc.stderr
            or "error during collection" in proc.stderr
            or "errors during collection" in proc.stderr
        )
        has_failed = "FAILED" in proc.stdout or "failed" in proc.stdout

        if proc.returncode != 1 or has_collection_err or not has_failed:
            err_detail = _extract_crash_detail(proc)
            return f"[error: proof_file crashed or failed collection ({err_detail}); changes_requested requires an executed failing assertion]"

    cases_summary = proc.stdout.strip() or proc.stderr.strip()
    findings = a.get("findings", "")
    findings_str = findings.strip() if isinstance(findings, str) else str(findings)

    rev_path = _reviews_path(t)
    rev_path.parent.mkdir(parents=True, exist_ok=True)
    task_id = a.get("task_id") if isinstance(a.get("task_id"), str) else ""
    task_id = task_id.strip() or None
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": "work",
        "reviewer": t.me,
        "author": author,
        "task_id": task_id,
        # False means nothing could be read, not that the author was idle --
        # an idle author is refused above and never reaches this line.
        "author_verified": author_verified,
        "what": what,
        "verdict": verdict,
        "proof_file": proof_file,
        "cases_tried": cases_summary,
        "findings": findings_str,
    }
    with rev_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    caveat = "" if author_verified else (
        f" [{author}'s activity could not be verified: no bus, event or audit "
        f"record was readable]")

    # The verdict reaches the author as one message of kind "review", and
    # the task's creator if that is someone else -- instead of the reviewer
    # spending a turn to forward it and the author a turn to acknowledge.
    notice = (f"Review of '{what}': {verdict}."
              + (f" Findings: {findings_str}" if findings_str else "")
              + (f" (task {task_id})" if task_id else ""))
    told = []
    for who in dict.fromkeys([author, _task_creator(t, task_id)]):
        if who and who not in (t.me, "user"):
            try:
                t.send_kind(who, notice, kind="review", task_id=task_id)
                told.append(who)
            except Exception:
                pass
    told_note = f" [{', '.join(told)} notified]" if told else ""
    return (f"[review recorded: {verdict} for '{what}' with proof "
            f"{proof_file}]{caveat}{told_note}")


def _task_creator(t: Transport, task_id: str | None) -> str | None:
    if not task_id:
        return None
    try:
        from . import tasks as tasks_lib
        task = tasks_lib.get(_team_dir(t), task_id)
        return task.get("created_by") if task else None
    except Exception:
        return None


def _send(t: Transport, a: dict) -> str:
    """send_to_teammate: policy, the review gate on deliverables, and an
    honest status -- the message is in an inbox, and whether anyone will
    wake its recipient is a fact this process can check."""
    to, content = _args("send_to_teammate", a, "to", "content")
    kind = (a.get("kind") or "work").strip().lower() if isinstance(a.get("kind"), str) else "work"
    if kind not in KINDS:
        return (f"[error: kind must be one of {', '.join(KINDS)}, not {kind!r}]")
    task_id = a.get("task_id").strip() if isinstance(a.get("task_id"), str) else None
    refusal = policy.check_tool_policy(t.me, "send_to_teammate", {"to": to},
                                       team_dir=_team_dir(t), check_paths=False)
    if refusal:
        return refusal
    if to == "user" and task_id:
        try:
            from . import tasks as tasks_lib
            task = tasks_lib.get(_team_dir(t), task_id)
        except Exception:
            task = None
        if task and task.get("requires_review") and \
                not tasks_lib.approved_review_for(_team_dir(t), task_id):
            return (f"[refused: task {task_id} requires an approved review before "
                    f"its result goes to the user, and none names it. Have a "
                    f"teammate record_review(..., task_id='{task_id}') first]")
    out = t.send_kind(to, content, kind=kind, task_id=task_id or None)
    if not out.startswith("[error"):
        who = heartbeat.describe(_team_dir(t))
        if to == "user":
            out = "[queued for the user; they read it when they next look]"
        elif kind in ("ack", "fyi", "status"):
            out = f"[queued for {to} as {kind}; they will read it on their next wake, which this does not cause]"
        else:
            out = f"[queued for {to}; {who}]"
    return out


def _list_retros(t: Transport) -> str:
    from . import retro_store
    team_dir = _team_dir(t)
    lines = ["# Retrospectives", ""]
    report = team_dir / "retro.md"
    try:
        head = [l for l in report.read_text(encoding="utf-8").splitlines() if l.strip()]
        outcome = next((l for l in head if l.startswith("**Norm change") or
                        l.startswith("**No norm") or l.startswith("**FAILED")), None)
        lines.append(f"Latest report: {head[0] if head else report}"
                     + (f" — {outcome}" if outcome else ""))
    except OSError:
        lines.append("No retrospective report has been written yet.")
    answers = retro_store.latest_by_agent(team_dir)
    if not answers:
        lines.append("No recorded answers yet (record_retro writes them).")
    for agent, rec in sorted(answers.items()):
        lines.append(f"\n## {agent} ({rec.get('ts', '')}, {rec.get('role', 'participant')})")
        lines.append(retro_store.as_reflection(rec))
    return "\n".join(lines)


def _record_retro(t: Transport, a: dict) -> str:
    """Write one agent's retrospective answers where the retro can read them.

    Validation lives in retro_store and runs here, at recording time, so a
    stub comes back while the agent still has the context to answer properly.
    """
    from . import retro_store
    return retro_store.record(
        _team_dir(t), t.me,
        a.get("went_well", ""), a.get("did_not", ""),
        a.get("should_change", ""),
        role=str(a.get("role") or "participant"))


def _list_reviews(t: Transport, task_id: str = "") -> str:
    rev_path = _reviews_path(t)
    if not rev_path.exists():
        return "[no reviews recorded]"
    try:
        content = rev_path.read_text(encoding="utf-8").strip()
    except OSError as e:
        return f"[error reading reviews: {e}]"
    if not content:
        return "[no reviews recorded]"

    reviews = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            reviews.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if task_id:
        reviews = [r for r in reviews if r.get("task_id") == task_id]
        if not reviews:
            return f"[no reviews recorded for {task_id}]"
    if not reviews:
        return "[no reviews recorded]"

    # Newest first: the file only grows, and the recent ones are the ones
    # being asked about.
    blocks = []
    for r in reversed(reviews):
        lines = []
        ts = r.get("ts", "unknown")
        reviewer = r.get("reviewer", "unknown")
        what = r.get("what", "unknown")
        verdict = r.get("verdict", "unknown")
        proof = r.get("proof_file")
        cases = r.get("cases_tried", [])
        findings = r.get("findings", "")

        lines.append(f"- [{ts}] {reviewer} -> {verdict}: {what}")
        if proof:
            lines.append(f"  Proof file: {proof}")
        if isinstance(cases, list) and cases:
            lines.append("  Cases tried:")
            for c in cases:
                lines.append(f"  * {c}")
        elif cases:
            lines.append(f"  Cases tried: {cases}")
        if findings:
            lines.append(f"  Findings: {findings}")
        blocks.append("\n".join(lines))
    text, _ = fit(blocks, "[{n} older reviews not shown; pass task_id to narrow]",
                  head="# Review records, newest first\n\n")
    return text


def _handle_grant_workspace(t: Transport, a: dict) -> str:
    from . import lifecycle
    ws = a.get("workspace")
    if not ws:
        return "[error: workspace is required]"
    try:
        res = lifecycle.grant_workspace(
            workspace=ws,
            agent=a.get("agent"),
            team_dir=_team_dir(t),
        )
        return json.dumps(res)
    except Exception as e:
        return f"[error: {e}]"


def _handle_revoke_workspace(t: Transport, a: dict) -> str:
    from . import lifecycle
    ws = a.get("workspace")
    if not ws:
        return "[error: workspace is required]"
    try:
        res = lifecycle.revoke_workspace(
            workspace=ws,
            agent=a.get("agent"),
            team_dir=_team_dir(t),
        )
        return json.dumps(res)
    except Exception as e:
        return f"[error: {e}]"


def _handle_stop_team(t: Transport, a: dict) -> str:
    from . import lifecycle
    try:
        res = lifecycle.stop_team(
            reason=a.get("reason", ""),
            team_dir=_team_dir(t),
        )
        return json.dumps(res)
    except Exception as e:
        return f"[error: {e}]"


def _handle_start_team(t: Transport, a: dict) -> str:
    from . import lifecycle
    try:
        res = lifecycle.start_team(team_dir=_team_dir(t))
        return json.dumps(res)
    except Exception as e:
        return f"[error: {e}]"


def _handle_team_status(t: Transport, a: dict) -> str:
    from . import lifecycle
    try:
        res = lifecycle.team_status(team_dir=_team_dir(t))
        return json.dumps(res)
    except Exception as e:
        return f"[error: {e}]"


class _BadArgs(Exception):
    """A tool call the model can fix, phrased so it can fix it."""


def _args(tool_name: str, a: dict, *names: str):
    """Pull required arguments, or raise _BadArgs naming the call that works.

    A bare `a["to"]` raises KeyError('to'), which the stdio loop renders as
    "[error: 'to']" -- a string that names no tool, no argument and no
    remedy. A manager on a host that does not inject tool schemas burned an
    entire run on nine consecutive delegation attempts against exactly that
    message, cycling argument shapes it had to guess. The most-used tool in
    the system had the least useful error.
    """
    missing = [n for n in names
               if not isinstance(a.get(n), str) or not a.get(n, "").strip()]
    if missing:
        sig = ", ".join(f"{n}=..." for n in names)
        raise _BadArgs(
            f"[error: {tool_name} is missing required argument(s): "
            f"{', '.join(missing)}. Call it as {tool_name}({sig}); every "
            f"argument is a string and none may be empty. "
            f"Got: {sorted(a) or 'no arguments'}]")
    return tuple(a[n].strip() if n != "content" else a[n] for n in names)


def main(transport: Transport, admin: bool = False):
    def _broadcast(a):
        refusal = policy.check_tool_policy(transport.me, "broadcast", {},
                                           team_dir=_team_dir(transport),
                                           check_paths=False)
        return refusal or transport.broadcast(*_args("broadcast", a, "content"))

    handlers = {
        "send_to_teammate": lambda a: _send(transport, a),
        "broadcast": _broadcast,
        "check_inbox": lambda a: _check_inbox(transport),
        "list_teammates": lambda a: _list_teammates(transport),
        "record_review": lambda a: _record_review(transport, a),
        "list_reviews": lambda a: _list_reviews(transport, str(a.get("task_id") or "").strip()),
        "record_retro": lambda a: _record_retro(transport, a),
        "list_retros": lambda a: _list_retros(transport),
    }
    tools = list(TOOLS)
    if admin and transport.supports_roster_admin:
        tools += ADMIN_TOOLS
        handlers["roster_add"] = lambda a: transport.roster_add(
            *_args("roster_add", a, "name", "role"))
        handlers["roster_remove"] = lambda a: transport.roster_remove(
            *_args("roster_remove", a, "name"))
        handlers["grant_workspace"] = lambda a: _handle_grant_workspace(transport, a)
        handlers["revoke_workspace"] = lambda a: _handle_revoke_workspace(transport, a)
        handlers["stop_team"] = lambda a: _handle_stop_team(transport, a)
        handlers["start_team"] = lambda a: _handle_start_team(transport, a)
        handlers["team_status"] = lambda a: _handle_team_status(transport, a)

    def dispatch(name, args):
        fn = handlers.get(name)
        if not fn:
            return (f"[error: unknown tool '{name}'. This server provides: "
                    f"{', '.join(sorted(handlers))}]")
        try:
            return fn(args)
        except _BadArgs as e:
            return str(e)

    try:
        serve(f"agy-team-bus:{transport.me}", tools, dispatch)
    finally:
        transport.close()


if __name__ == "__main__":
    if len(sys.argv) == 3:                      # <team_dir> <agent_name>
        os.environ.setdefault("AGYTEAM_TEAM_DIR", sys.argv[1])
        me = sys.argv[2]
    else:
        me = os.environ.get("AGYTEAM_AGENT", "")
    if not me:
        sys.exit("agyteam.mcp_bus: no agent identity. Pass <team_dir> <agent_name> "
                 "or set AGYTEAM_AGENT (and optionally AGYTEAM_TEAM_DIR).")
    main(load(me), admin=os.environ.get("AGYTEAM_ROSTER_ADMIN") == "1")
