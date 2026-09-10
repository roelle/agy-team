"""Tests for the manager gate loop and review enforcement.

Tests the Step 1 gate loop offline with scripted runners, verifying:
- An unreviewed answer bounces back to the manager exactly once
- Episode continues so the team can get an approved review
- A second unreviewed answer ends the episode labeled (unreviewed)
- An approved review allows the answer without a bounce
- require_review=False restores the unbounced behaviour
- Manager auto-detection from roster vs explicit manager configuration
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from rpc_util import ROOT, check

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam.supervisor import (  # noqa: E402
    Supervisor,
    format_report,
    generate_report,
    main,
)
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402


def make_team(agents=None, prefix="agyteam-mgr-") -> Path:
    td = Path(tempfile.mkdtemp(prefix=prefix)) / "team"
    (td / "inbox").mkdir(parents=True)
    agents = agents or [
        {"name": "tpm", "role": "manager — gatekeeper, accountable"},
        {"name": "coder", "role": "implements"},
        {"name": "qa", "role": "reviews"},
    ]
    (td / "roster.json").write_text(json.dumps({"agents": agents}))
    return td


class MultiTurnRunner(ScriptedRunner):
    def __init__(self, turns, config=None, observer=None, on_wake=None):
        super().__init__(config, observer=observer)
        self.turns = {k: list(v) for k, v in turns.items()}
        self.received: list[tuple[str, str]] = []
        self.on_wake = on_wake

    def wake(self, agent: str, message: str) -> str:
        self.received.append((agent, message))
        if self.on_wake:
            self.on_wake(agent, message)
        sends = self.turns.get(agent, [])
        next_sends = sends.pop(0) if sends else []
        self.woken.append(agent)
        if next_sends:
            bus = load_transport(agent)
            for to, content in next_sends:
                bus.send(to, content)
            bus.close()
        return f"{agent} woke"


def test_manager_gate_loop() -> tuple[int, int]:
    print("\n== Step 1: manager gate loop ==")
    checks = []

    # 1. Unreviewed answer bounces once and allows recovery
    td1 = make_team()
    os.environ["AGYTEAM_TEAM_DIR"] = str(td1)

    def on_wake1(agent: str, msg: str):
        if agent == "qa":
            with (td1 / "reviews.jsonl").open("a") as f:
                f.write(json.dumps({
                    "ts": "2026-09-10 12:00:00",
                    "reviewer": "qa",
                    "what": "feature X",
                    "verdict": "approved",
                    "cases_tried": ["test_ok"],
                    "findings": "looks good",
                }) + "\n")

    runner1 = MultiTurnRunner(
        turns={
            "tpm": [
                [["user", "feature X is done!"]],  # Turn 1: premature answer
                [["qa", "please review feature X"]],  # Turn 2: after supervisor bounce
                [["user", "feature X is verified and done!"]],  # Turn 4: final answer after QA review
            ],
            "coder": [],
            "qa": [
                [["tpm", "review recorded, verdict approved"]],  # Turn 3: QA informs TPM
            ],
        },
        on_wake=on_wake1,
    )
    sup1 = Supervisor(["tpm", "coder", "qa"], runner1, team_dir=td1, max_hops=10, quiet=True)
    load_transport("user").send("tpm", "build feature X")
    total_turns1 = sup1.run_until_idle()

    # Verify that TPM received the bounce message
    tpm_msgs1 = [msg for agent, msg in runner1.received if agent == "tpm"]
    bounced_msg = tpm_msgs1[1] if len(tpm_msgs1) > 1 else ""

    checks.append(check("unreviewed answer bounces back to manager",
                        "went out without an approved review" in bounced_msg,
                        bounced_msg))
    checks.append(check("episode continues after bounce and completes normally once reviewed",
                        sup1.stopped == "the user was answered",
                        sup1.stopped))
    checks.append(check("recovery took multiple turns",
                        total_turns1 >= 4,
                        f"total turns: {total_turns1}"))
    sup1.close()

    # 2. Second unreviewed answer ends the episode (bounce exactly once)
    td2 = make_team()
    os.environ["AGYTEAM_TEAM_DIR"] = str(td2)
    runner2 = MultiTurnRunner(
        turns={
            "tpm": [
                [["user", "feature Y is done!"]],  # Turn 1: unreviewed answer -> supervisor bounces
                [["user", "feature Y is really done!"]],  # Turn 2: still unreviewed -> supervisor ends
            ],
        }
    )
    sup2 = Supervisor(["tpm", "coder", "qa"], runner2, team_dir=td2, max_hops=10, quiet=True)
    load_transport("user").send("tpm", "build feature Y")
    total_turns2 = sup2.run_until_idle()

    tpm_wakes2 = [agent for agent in runner2.woken if agent == "tpm"]
    checks.append(check("second unreviewed answer ends episode as unreviewed",
                        sup2.stopped == "the user was answered (unreviewed)",
                        sup2.stopped))
    checks.append(check("bounced exactly once (tpm woken twice total: initial + after bounce)",
                        len(tpm_wakes2) == 2,
                        f"tpm woken {len(tpm_wakes2)} times"))
    sup2.close()

    # 3. Approved review before user answer -> no bounce
    td3 = make_team()
    os.environ["AGYTEAM_TEAM_DIR"] = str(td3)
    def on_wake3(agent: str, msg: str):
        if agent == "qa":
            with (td3 / "reviews.jsonl").open("a") as f:
                f.write(json.dumps({
                    "ts": "2026-09-10 12:00:00",
                    "reviewer": "qa",
                    "what": "feature Z",
                    "verdict": "approved",
                    "cases_tried": ["test_z"],
                    "findings": "passed",
                }) + "\n")

    runner3 = MultiTurnRunner(
        turns={
            "tpm": [
                [["coder", "do Z"]],
                [["user", "Z is done!"]],
            ],
            "coder": [
                [["qa", "test Z"]],
            ],
            "qa": [
                [["tpm", "QA approved Z"]],
            ],
        },
        on_wake=on_wake3,
    )
    sup3 = Supervisor(["tpm", "coder", "qa"], runner3, team_dir=td3, max_hops=10, quiet=True)
    load_transport("user").send("tpm", "build feature Z")
    sup3.run_until_idle()

    tpm_msgs3 = [msg for agent, msg in runner3.received if agent == "tpm"]
    has_bounce_msg3 = any("went out without an approved review" in msg for msg in tpm_msgs3)

    checks.append(check("approved review means no bounce",
                        sup3.stopped == "the user was answered" and not has_bounce_msg3,
                        f"stopped={sup3.stopped}, bounced={has_bounce_msg3}"))
    sup3.close()

    # 4. require_review=False restores old behaviour (no bounce)
    td4 = make_team()
    os.environ["AGYTEAM_TEAM_DIR"] = str(td4)
    runner4 = MultiTurnRunner(
        turns={
            "tpm": [
                [["user", "quick answer without review"]],
                [["user", "should never be reached"]],
            ],
        }
    )
    sup4 = Supervisor(["tpm", "coder", "qa"], runner4, team_dir=td4, max_hops=10,
                      quiet=True, require_review=False)
    load_transport("user").send("tpm", "quick question")
    sup4.run_until_idle()

    tpm_wakes4 = [agent for agent in runner4.woken if agent == "tpm"]
    checks.append(check("require_review=False ends immediately as unreviewed without bounce",
                        sup4.stopped == "the user was answered (unreviewed)" and len(tpm_wakes4) == 1,
                        f"stopped={sup4.stopped}, wakes={len(tpm_wakes4)}"))
    sup4.close()

    # 5. Explicit manager flag routes bounce
    td5 = make_team(agents=[
        {"name": "lead", "role": "coordinator"},
        {"name": "coder", "role": "implements"},
    ])
    os.environ["AGYTEAM_TEAM_DIR"] = str(td5)
    runner5 = MultiTurnRunner(
        turns={
            "lead": [
                [["user", "answer from lead"]],
                [["user", "lead answer 2"]],
            ],
        }
    )
    sup5 = Supervisor(["lead", "coder"], runner5, team_dir=td5, max_hops=10,
                      quiet=True, manager="lead")
    load_transport("user").send("lead", "ask lead")
    sup5.run_until_idle()

    lead_msgs5 = [msg for agent, msg in runner5.received if agent == "lead"]
    has_bounce_msg5 = any("went out without an approved review" in msg for msg in lead_msgs5)
    checks.append(check("explicit manager argument routes bounce to specified agent",
                        has_bounce_msg5 and sup5.stopped == "the user was answered (unreviewed)",
                        f"has_bounce={has_bounce_msg5}, stopped={sup5.stopped}"))
    sup5.close()

    # 6. Team without manager does not bounce
    td6 = make_team(agents=[
        {"name": "coder", "role": "implements"},
        {"name": "syseng", "role": "verifies"},
    ])
    os.environ["AGYTEAM_TEAM_DIR"] = str(td6)
    runner6 = MultiTurnRunner(
        turns={
            "coder": [
                [["user", "direct coder answer"]],
            ],
        }
    )
    sup6 = Supervisor(["coder", "syseng"], runner6, team_dir=td6, max_hops=10, quiet=True)
    load_transport("user").send("coder", "do something")
    sup6.run_until_idle()

    checks.append(check("team with no manager exits as unreviewed without crashing",
                        sup6.stopped == "the user was answered (unreviewed)",
                        sup6.stopped))
    sup6.close()

    return sum(checks), len(checks)


def test_supervisor_report() -> tuple[int, int]:
    print("\n== Step 2: --report and --report-json deterministic status ==")
    checks = []

    # 1. Populated team directory
    td = make_team()
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)

    # Populate reviews.jsonl
    (td / "reviews.jsonl").write_text(json.dumps({
        "ts": "2026-09-10 14:00:00",
        "reviewer": "qa",
        "what": "unit tests and lint",
        "verdict": "approved",
        "cases_tried": ["test_evals"],
        "findings": "all passed",
    }) + "\n")

    # Populate events.jsonl with turns and episode
    events = [
        {
            "event": "turn",
            "ts": "2026-09-10 14:01:00",
            "agent": "coder",
            "duration_s": 2.5,
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_read_tokens": 500,
            "total_tokens": 1200,
            "model": "claude-sonnet-4-6",
        },
        {
            "event": "turn",
            "ts": "2026-09-10 14:02:00",
            "agent": "qa",
            "duration_s": 1.5,
            # Unmeasured turn: tokens omitted
        },
        {
            "event": "episode",
            "ts": "2026-09-10 14:03:00",
            "turns": 2,
            "stopped_reason": "the user was answered",
            "reviewed": True,
            "duration_s": 4.0,
        },
    ]
    with (td / "events.jsonl").open("w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    # Put a message in coder's inbox
    bus_user = load_transport("user")
    bus_user.send("coder", "hello coder")
    bus_user.close()

    inbox_coder = td / "inbox" / "coder.jsonl"
    inbox_content_before = inbox_coder.read_text() if inbox_coder.exists() else ""

    rep = generate_report(td)

    # Verify inbox was NOT drained by report (peek only)
    inbox_content_after = inbox_coder.read_text() if inbox_coder.exists() else ""
    checks.append(check("generate_report did not drain inbox (peek-only)",
                        inbox_content_before == inbox_content_after and len(inbox_content_before) > 0,
                        f"before: {len(inbox_content_before)} bytes, after: {len(inbox_content_after)} bytes"))

    # Verify report structure
    checks.append(check("report has_history is True",
                        rep.get("has_history") is True,
                        str(rep.get("has_history"))))
    checks.append(check("episodes reported with stopped_reason and reviewed status",
                        len(rep.get("episodes", [])) == 1
                        and rep["episodes"][0]["stopped_reason"] == "the user was answered"
                        and rep["episodes"][0]["reviewed"] is True,
                        str(rep.get("episodes"))))

    coder_ag = rep.get("agents", {}).get("coder", {})
    checks.append(check("coder agent turn and tokens reported",
                        coder_ag.get("turns") == 1
                        and coder_ag.get("input_tokens") == 1000,
                        str(coder_ag)))
    # This fixture turn runs on claude-sonnet-4-6, which we have no price for.
    # The old assertion expected a positive cost, which only held because every
    # unknown model was silently given a default price -- a confident figure for
    # something nobody measured, in the accounting that exists to compare
    # runtimes. Unpriced work must be visible as unpriced, never as free.
    checks.append(check("a model we have no price for is reported unpriced, not free",
                        coder_ag.get("unpriced_turns") == 1
                        and coder_ag.get("cost_usd") == 0,
                        str(coder_ag)))
    checks.append(check("coder agent unread mail detected via peek",
                        coder_ag.get("unread_mail") is True and coder_ag.get("unread_count") == 1,
                        f"unread_mail={coder_ag.get('unread_mail')}, count={coder_ag.get('unread_count')}"))

    qa_ag = rep.get("agents", {}).get("qa", {})
    checks.append(check("qa agent unmeasured turn has None tokens and cost (no invented zeros)",
                        qa_ag.get("turns") == 1
                        and qa_ag.get("input_tokens") is None
                        and qa_ag.get("cost_usd") is None
                        and qa_ag.get("unmeasured_turns") == 1,
                        str(qa_ag)))

    totals = rep.get("totals", {})
    checks.append(check("totals aggregated across agents",
                        totals.get("episodes") == 1
                        and totals.get("reviewed_episodes") == 1
                        and totals.get("turns") == 2
                        and totals.get("input_tokens") == 1000,
                        str(totals)))

    # Verify human-readable formatting
    text_rep = format_report(rep)
    checks.append(check("format_report includes header, episode status, and agent details",
                        "=== Team Status & Activity Report ===" in text_rep
                        and "stopped: the user was answered (reviewed)" in text_rep
                        and "coder" in text_rep
                        and "qa" in text_rep,
                        text_rep))
    checks.append(check("format_report handles unmeasured tokens as unknown rather than zero",
                        "unknown" in text_rep,
                        text_rep))

    # 2. Empty team / no history
    td_empty = Path(tempfile.mkdtemp(prefix="agyteam-empty-")) / "team"
    td_empty.mkdir(parents=True)
    rep_empty = generate_report(td_empty)
    text_empty = format_report(rep_empty)

    checks.append(check("empty team reports has_history=False and no invented zeros",
                        rep_empty.get("has_history") is False
                        and rep_empty.get("totals", {}).get("cost_usd") is None,
                        str(rep_empty)))
    checks.append(check("empty team text format reports '(no team history recorded)'",
                        text_empty == "(no team history recorded)",
                        text_empty))

    # 3. CLI execution via main()
    stdout_buf = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf):
        main(["--report", "--team-dir", str(td)])
    cli_out = stdout_buf.getvalue()
    checks.append(check("main --report outputs formatted report",
                        "=== Team Status & Activity Report ===" in cli_out and "coder" in cli_out,
                        cli_out))

    stdout_buf_json = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf_json):
        main(["--report-json", "--team-dir", str(td)])
    cli_json_raw = stdout_buf_json.getvalue()
    try:
        cli_parsed = json.loads(cli_json_raw)
        cli_json_ok = cli_parsed.get("has_history") is True and len(cli_parsed.get("episodes", [])) == 1
    except Exception:
        cli_json_ok = False
    checks.append(check("main --report-json outputs valid JSON report",
                        cli_json_ok,
                        cli_json_raw[:200]))

    stdout_buf_empty = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf_empty):
        main(["--report", "--team-dir", str(td_empty)])
    cli_empty_out = stdout_buf_empty.getvalue().strip()
    checks.append(check("main --report on empty team outputs '(no team history recorded)'",
                        cli_empty_out == "(no team history recorded)",
                        cli_empty_out))

    stdout_buf_empty_json = io.StringIO()
    with contextlib.redirect_stdout(stdout_buf_empty_json):
        main(["--report-json", "--team-dir", str(td_empty)])
    cli_empty_json = stdout_buf_empty_json.getvalue()
    try:
        cli_empty_parsed = json.loads(cli_empty_json)
        cli_empty_json_ok = cli_empty_parsed.get("has_history") is False
    except Exception:
        cli_empty_json_ok = False
    checks.append(check("main --report-json on empty team outputs has_history: false",
                        cli_empty_json_ok,
                        cli_empty_json[:200]))

    return sum(checks), len(checks)


def test_manager_persona_and_roster() -> tuple[int, int]:
    print("\n== Step 3: manager persona and seeded roster ==")
    checks = []

    from agyteam.persona import ACCOUNTABILITY, brief

    # 1. Check ACCOUNTABILITY tenets
    checks.append(check(
        "ACCOUNTABILITY defines 'work is not done because someone said it was'",
        "work is not done because someone said it was" in ACCOUNTABILITY.lower(),
        ACCOUNTABILITY[:200],
    ))
    checks.append(check(
        "ACCOUNTABILITY defines 'rejected review comes back rather than being reported'",
        "rejected review comes back" in ACCOUNTABILITY.lower(),
        ACCOUNTABILITY[:200],
    ))
    checks.append(check(
        "ACCOUNTABILITY defines 'I do not know, here is how I would find out'",
        "i do not know, here is how i would find out" in ACCOUNTABILITY.lower(),
        ACCOUNTABILITY[:200],
    ))

    # 2. Check brief() includes ACCOUNTABILITY
    agents = [
        {"name": "tpm", "role": "Manager: gatekeeper"},
        {"name": "coder", "role": "Implements"},
    ]
    tpm_brief = brief("tpm", agents)
    checks.append(check(
        "brief() incorporates ## Accountability to the user",
        "## Accountability to the user" in tpm_brief and "coder said it was done" in tpm_brief,
        tpm_brief[-500:],
    ))

    # 3. Check seeded roster in plugin/install.sh
    install_script = (ROOT / "plugin" / "install.sh").read_text()
    marker_start = 'cat > "$DURABLE/team/roster.json" <<JSON'
    marker_end = "\nJSON"
    has_markers = marker_start in install_script and marker_end in install_script
    checks.append(check("install.sh contains roster heredoc markers", has_markers, "markers found"))

    if has_markers:
        raw_json = install_script.split(marker_start)[1].split(marker_end)[0]
        json_clean = raw_json.replace("($TEAM)", "(test)").strip()
        try:
            roster_data = json.loads(json_clean)
            json_valid = True
        except Exception:
            roster_data = {}
            json_valid = False
        checks.append(check("seeded roster is valid JSON", json_valid, str(json_clean[:100])))

        tpm_entry = next((a for a in roster_data.get("agents", []) if a.get("name") == "tpm"), {})
        tpm_role = tpm_entry.get("role", "").lower()

        checks.append(check("tpm role states Gatekeeper property",
                            "gatekeeper" in tpm_role and "reviewed" in tpm_role,
                            tpm_entry.get("role", "")))
        checks.append(check("tpm role states Final check property",
                            "final check" in tpm_role and "comes back to the manager" in tpm_role,
                            tpm_entry.get("role", "")))
        checks.append(check("tpm role states Accountable property",
                            "accountable" in tpm_role and "coder said it was done" in tpm_role,
                            tpm_entry.get("role", "")))
        checks.append(check("tpm role states Interrupt-driven property",
                            "interrupt-driven" in tpm_role and "grounded in the record" in tpm_role,
                            tpm_entry.get("role", "")))
        checks.append(check("tpm preserves tools_off and workers=false",
                            tpm_entry.get("tools_off") == ["run_command", "create_file", "edit_file"]
                            and tpm_entry.get("workers") is False,
                            f"tools_off={tpm_entry.get('tools_off')}, workers={tpm_entry.get('workers')}"))
        checks.append(check("install.sh self-test isolates AGYTEAM_RUNNER with env -u",
                            "env -u AGYTEAM_RUNNER" in install_script,
                            "env -u AGYTEAM_RUNNER check"))

    return sum(checks), len(checks)


if __name__ == "__main__":
    totals = [
        test_manager_gate_loop(),
        test_supervisor_report(),
        test_manager_persona_and_roster(),
    ]
    got, want = sum(s for s, _ in totals), sum(t for _, t in totals)
    print(f"\n== manager gate loop & report tests: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
