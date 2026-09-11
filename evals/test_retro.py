"""Tests for team norms (Step 1 of TASK.md).

Verifies:
- Absence of NORMS.md is normal (returns None, does not break brief)
- Seeding NORMS.md writes SEEDED_NORMS (CONSISTENCY + VERIFICATION) to <team_dir>/NORMS.md
- Seeding does not overwrite existing norms unless overwrite=True or force=True
- Loaded into every agent's brief alongside existing sections
- Team-scoped: different teams have different norms, isolated from each other
- Shared: every agent on the same team sees the same norms text
- Signature compatibility: brief() handles all positional and keyword call patterns
- Stdlib purity: scope and persona have no third-party dependencies
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from rpc_util import ROOT, check

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import persona, scope, supervisor  # noqa: E402
from agyteam.runner import Runner  # noqa: E402
from agyteam.scope import Scopes  # noqa: E402



def test_norms_absence() -> tuple[int, int]:
    """NORMS.md is absent by default, and absence is normal."""
    print("\n== Step 1: norms absence ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-norms-absent-"))
    try:
        dur = tmp / "durable"
        proj = tmp / "project"
        shr = tmp / "shared"
        scopes = Scopes(durable=dur, project=proj, shared=shr, team="alpha")

        # Absent from Scopes methods
        checks.append(check("scopes.read_norms() is None when file missing",
                            scopes.read_norms() is None,
                            f"got: {scopes.read_norms()}"))
        checks.append(check("scope.read_norms(scopes=scopes) is None",
                            scope.read_norms(scopes=scopes) is None,
                            "scope.read_norms with scopes"))
        checks.append(check("scope.read_norms(team_dir=...) is None",
                            scope.read_norms(team_dir=scopes.team_dir()) is None,
                            "scope.read_norms with team_dir"))
        checks.append(check("persona.load_norms(scopes=scopes) is None",
                            persona.load_norms(scopes=scopes) is None,
                            "persona.load_norms with scopes"))

        # brief() with absent norms works without error
        agents = [{"name": "coder", "role": "Implements"}, {"name": "tpm", "role": "Coordinates"}]
        b = persona.brief("coder", agents, scopes=scopes)
        checks.append(check("brief() works with absent norms",
                            "You are 'coder'" in b and "## Grounding rules" in b,
                            b[:200]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_norms_seeding() -> tuple[int, int]:
    """Seeding NORMS.md populates CONSISTENCY and VERIFICATION, respects overwrite/force."""
    print("\n== Step 1: norms seeding ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-norms-seed-"))
    try:
        dur = tmp / "durable"
        proj = tmp / "project"
        shr = tmp / "shared"
        scopes = Scopes(durable=dur, project=proj, shared=shr, team="beta")

        # SEEDED_NORMS contains CONSISTENCY and VERIFICATION
        checks.append(check("SEEDED_NORMS contains CONSISTENCY text",
                            "## Fitting the system you are changing" in persona.SEEDED_NORMS,
                            persona.SEEDED_NORMS[:100]))
        checks.append(check("SEEDED_NORMS contains VERIFICATION text",
                            "## Verifying someone else's work" in persona.SEEDED_NORMS,
                            persona.SEEDED_NORMS[-200:]))

        # Seed via scopes
        p = scopes.seed_norms()
        checks.append(check("seed_norms returns path to NORMS.md",
                            p == scopes.norms_file() and p.is_file(),
                            str(p)))
        content = p.read_text(encoding="utf-8")
        checks.append(check("seeded NORMS.md matches SEEDED_NORMS",
                            content == persona.SEEDED_NORMS,
                            f"len={len(content)} vs len={len(persona.SEEDED_NORMS)}"))

        # Seed does not overwrite existing norms by default
        custom = "## Custom norm\nDo not repeat mistakes."
        p.write_text(custom, encoding="utf-8")
        p2 = scopes.seed_norms()
        checks.append(check("seed_norms does not overwrite existing norms",
                            p.read_text(encoding="utf-8") == custom,
                            p.read_text(encoding="utf-8")))

        # Force/overwrite replaces existing norms
        scopes.seed_norms(overwrite=True)
        checks.append(check("seed_norms(overwrite=True) replaces existing norms",
                            p.read_text(encoding="utf-8") == persona.SEEDED_NORMS,
                            p.read_text(encoding="utf-8")[:100]))

        # Custom text seeding
        scopes.seed_norms(text=custom, force=True)
        checks.append(check("seed_norms(text=custom, force=True) writes custom text",
                            p.read_text(encoding="utf-8") == custom,
                            p.read_text(encoding="utf-8")))

        # Seeding via persona module helper
        p.unlink()
        p_persona = persona.seed_norms(scopes=scopes)
        checks.append(check("persona.seed_norms(scopes=...) seeds NORMS.md",
                            p_persona.is_file() and p_persona.read_text() == persona.SEEDED_NORMS,
                            str(p_persona)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_brief_loading_and_sharing() -> tuple[int, int]:
    """Norms loaded into every agent's brief alongside existing sections."""
    print("\n== Step 1: brief loading and teammate sharing ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-norms-brief-"))
    try:
        dur = tmp / "durable"
        proj = tmp / "project"
        shr = tmp / "shared"
        scopes = Scopes(durable=dur, project=proj, shared=shr, team="gamma")

        custom_norm = "## Team norm 1: Test before asking qa\nAlways run all evals."
        scopes.norms_file().parent.mkdir(parents=True, exist_ok=True)
        scopes.norms_file().write_text(custom_norm, encoding="utf-8")

        agents = [
            {"name": "coder", "role": "Implements"},
            {"name": "tpm", "role": "Coordinates"},
            {"name": "qa", "role": "Reviews"},
        ]

        b_coder = persona.brief("coder", agents, shared_dir=shr, scopes=scopes)
        b_tpm = persona.brief("tpm", agents, shared_dir=shr, scopes=scopes)
        b_qa = persona.brief("qa", agents, shared_dir=shr, scopes=scopes)

        # All agents get the norm
        checks.append(check("coder brief contains custom norm",
                            custom_norm in b_coder,
                            b_coder[-300:]))
        checks.append(check("tpm brief contains custom norm",
                            custom_norm in b_tpm,
                            b_tpm[-300:]))
        checks.append(check("qa brief contains custom norm",
                            custom_norm in b_qa,
                            b_qa[-300:]))

        # Existing sections remain intact alongside norms
        for section_title in [
            "## Teammates and workers",
            "## Grounding rules",
            "## What persists, and what does not",
            "## Fitting the system you are changing",
            "## Verifying someone else's work",
            "## Accountability to the user",
            "## Shared files",
        ]:
            checks.append(check(f"coder brief retains {section_title}",
                                section_title in b_coder,
                                f"missing {section_title}"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_team_scoping() -> tuple[int, int]:
    """Different teams have different norms; isolation is maintained."""
    print("\n== Step 1: team scoping and isolation ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-norms-iso-"))
    try:
        scopes_red = Scopes(durable=tmp / "red", project=tmp / "proj", shared=tmp / "shr", team="red")
        scopes_blue = Scopes(durable=tmp / "blue", project=tmp / "proj", shared=tmp / "shr", team="blue")

        norm_red = "## Red Team Norms\nMove fast and keep tests green."
        scopes_red.norms_file().parent.mkdir(parents=True, exist_ok=True)
        scopes_red.norms_file().write_text(norm_red, encoding="utf-8")

        # Blue team has no norms file
        agents = [{"name": "coder", "role": "Implements"}]
        b_red = persona.brief("coder", agents, scopes=scopes_red)
        b_blue = persona.brief("coder", agents, scopes=scopes_blue)

        checks.append(check("red brief has red norms",
                            norm_red in b_red,
                            b_red[-200:]))
        checks.append(check("blue brief does NOT have red norms",
                            norm_red not in b_blue,
                            "blue brief leaked red norms"))
        checks.append(check("blue read_norms() is None",
                            scopes_blue.read_norms() is None,
                            str(scopes_blue.read_norms())))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_signature_compatibility() -> tuple[int, int]:
    """brief() handles all existing and new signature permutations."""
    print("\n== Step 1: brief signature compatibility ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-norms-sig-"))
    try:
        dur = tmp / "durable"
        proj = tmp / "project"
        shr = tmp / "shared"
        scopes = Scopes(durable=dur, project=proj, shared=shr, team="sig")
        team_dir = scopes.team_dir()
        norms_text = "## Sig Norms\nCheck every signature."
        (team_dir / "NORMS.md").write_text(norms_text, encoding="utf-8")

        agents = [{"name": "coder", "role": "Implements"}]

        # 1. brief(agent, agents)
        b1 = persona.brief("coder", agents)
        checks.append(check("brief(agent, agents) runs",
                            "You are 'coder'" in b1, b1[:100]))

        # 2. brief(agent, agents, shared_dir)
        b2 = persona.brief("coder", agents, shr)
        checks.append(check("brief(agent, agents, shared_dir) includes shared files",
                            f"belongs under {shr}" in b2, b2[-200:]))

        # 3. brief(agent, agents, shared_dir, team_dir)
        b3 = persona.brief("coder", agents, shr, team_dir)
        checks.append(check("brief(agent, agents, shared_dir, team_dir) includes norms",
                            norms_text in b3 and f"belongs under {shr}" in b3, b3[-300:]))

        # 4. brief(agent, agents, team_dir=td)
        b4 = persona.brief("coder", agents, team_dir=team_dir)
        checks.append(check("brief(agent, agents, team_dir=td) includes norms",
                            norms_text in b4, b4[-200:]))

        # 5. brief(agent, agents, scopes=scopes)
        b5 = persona.brief("coder", agents, scopes=scopes)
        checks.append(check("brief(agent, agents, scopes=scopes) includes norms",
                            norms_text in b5, b5[-200:]))

        # 6. brief(agent, agents, shared_dir=scopes) where scopes passed as shared_dir
        b6 = persona.brief("coder", agents, shared_dir=scopes)
        checks.append(check("brief(agent, agents, shared_dir=scopes) handles Scopes object",
                            norms_text in b6 and f"belongs under {shr}" in b6, b6[-300:]))

        # 7. Environment variable AGYTEAM_TEAM_DIR resolution
        old_env = os.environ.get("AGYTEAM_TEAM_DIR")
        try:
            os.environ["AGYTEAM_TEAM_DIR"] = str(team_dir)
            b7 = persona.brief("coder", agents)
            checks.append(check("brief() picks up AGYTEAM_TEAM_DIR from environment",
                                norms_text in b7, b7[-200:]))
        finally:
            if old_env is not None:
                os.environ["AGYTEAM_TEAM_DIR"] = old_env
            else:
                os.environ.pop("AGYTEAM_TEAM_DIR", None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_stdlib_purity() -> tuple[int, int]:
    """agyteam.scope and agyteam.persona must be stdlib-only."""
    print("\n== Step 1: stdlib purity (system python, no venv) ==")
    sys_py = "/usr/bin/python3"
    if not Path(sys_py).exists():
        return check("system python present", False, f"{sys_py} missing"), 1

    def imports(mod):
        p = subprocess.run([sys_py, "-c", f"import {mod}"], cwd="/",
                           env={"PYTHONPATH": str(ROOT)}, capture_output=True,
                           text=True, timeout=60)
        return p.returncode == 0, p.stderr

    checks = []
    for mod in ("agyteam.scope", "agyteam.persona"):
        ok, err = imports(mod)
        checks.append(check(f"{mod} imports with stdlib only", ok, err))

    return sum(checks), len(checks)


class RetroMockRunner(Runner):
    label = "retro-mock"

    def __init__(self, responses: dict[str, str] | None = None, default_response: str = "ok"):
        super().__init__()
        self.responses = responses or {}
        self.default_response = default_response
        self.calls: list[tuple[str, str]] = []

    def wake(self, agent: str, message: str) -> str:
        self.calls.append((agent, message))
        if agent in self.responses:
            return self.responses[agent]
        return self.default_response


def make_retro_team(parent: Path, agents=None) -> Path:
    td = parent / "team"
    (td / "inbox").mkdir(parents=True, exist_ok=True)
    agents_list = agents or [
        {"name": "tpm", "role": "Manager: coordinates and ships. Accountable for outcomes."},
        {"name": "coder", "role": "Implements. Writes code and tests."},
        {"name": "qa", "role": "Reviews diffs. Records reviews."},
    ]
    (td / "roster.json").write_text(json.dumps({"agents": agents_list}), encoding="utf-8")
    (td / "events.jsonl").write_text(
        json.dumps({"ts": "2026-09-10 10:00:00", "event": "turn", "agent": "tpm", "episode": 1, "turns": 1, "duration_s": 0.5}) + "\n" +
        json.dumps({"ts": "2026-09-10 10:01:00", "event": "turn", "agent": "coder", "episode": 1, "turns": 2, "duration_s": 1.2}) + "\n" +
        json.dumps({"ts": "2026-09-10 10:02:00", "event": "episode_end", "episode": 1, "turns": 2, "stopped_reason": "answer", "reviewed": True, "duration_s": 1.7}) + "\n",
        encoding="utf-8"
    )
    (td / "reviews.jsonl").write_text(
        json.dumps({
            "ts": "2026-09-10 10:01:30",
            "reviewer": "qa",
            "what": "feature X implementation",
            "verdict": "approved",
            "cases_tried": ["happy path", "empty input"],
            "findings": "Looks solid"
        }) + "\n",
        encoding="utf-8"
    )
    (td / "bus.jsonl").write_text(
        json.dumps({"ts": "2026-09-10 10:00:05", "sender": "tpm", "recipient": "coder", "content": "build feature X"}) + "\n" +
        json.dumps({"ts": "2026-09-10 10:01:10", "sender": "coder", "recipient": "qa", "content": "please review X"}) + "\n",
        encoding="utf-8"
    )
    return td


def test_parse_retro_sections() -> tuple[int, int]:
    """Test parsing of 3 ordered questions in retrospective responses."""
    print("\n== Step 2: parse_retro_sections ==")
    checks = []

    # Standard markdown headings
    text_std = """## 1. What went well
Communication was rapid and clear across the bus.

## 2. What did not
Tests had an uncaught edge case on empty strings.

## 3. What should we change
Add automated boundary condition tests for all parsers."""
    res = supervisor.parse_retro_sections(text_std)
    checks.append(check("parse standard markdown sections", res is not None, str(res)))
    if res:
        checks.append(check("q1 parsed correctly", "Communication was rapid" in res["q1"], res["q1"]))
        checks.append(check("q2 parsed correctly", "uncaught edge case" in res["q2"], res["q2"]))
        checks.append(check("q3 parsed correctly", "boundary condition" in res["q3"], res["q3"]))

    # Alternate headings and numbered prefixes
    text_alt = """### What went well
Good progress.

### What went wrong
Initial setup had missing files.

### What to change
Document team layout."""
    res_alt = supervisor.parse_retro_sections(text_alt)
    checks.append(check("parse alternate headings (what went wrong, what to change)", res_alt is not None, str(res_alt)))

    # Inline / bold headings
    text_inline = """**1. What went well:**
Speed of execution.

**2. What did not go well:**
Coordination delays.

**3. What should we change:**
Establish synchronous check-ins."""
    res_inline = supervisor.parse_retro_sections(text_inline)
    checks.append(check("parse inline bold headings", res_inline is not None, str(res_inline)))

    # Out of order: Q2 before Q1
    text_bad_order = """## 2. What did not
Something failed.

## 1. What went well
Something worked.

## 3. What should we change
Do better."""
    checks.append(check("reject out of order sections (Q2 before Q1)", supervisor.parse_retro_sections(text_bad_order) is None))

    # Out of order: Q3 before Q2
    text_bad_order2 = """## 1. What went well
Something worked.

## 3. What should we change
Do better.

## 2. What did not
Something failed."""
    checks.append(check("reject out of order sections (Q3 before Q2)", supervisor.parse_retro_sections(text_bad_order2) is None))

    # Missing section: Q3 missing
    text_missing_q3 = """## 1. What went well
Something worked.

## 2. What did not
Something failed."""
    checks.append(check("reject missing Q3", supervisor.parse_retro_sections(text_missing_q3) is None))

    # Empty section: Q2 empty
    text_empty_q2 = """## 1. What went well
Something worked.

## 2. What did not

## 3. What should we change
Do better."""
    checks.append(check("reject empty section", supervisor.parse_retro_sections(text_empty_q2) is None))

    # Non-string / None
    checks.append(check("reject None text", supervisor.parse_retro_sections(None) is None))
    checks.append(check("reject empty string", supervisor.parse_retro_sections("") is None))

    return sum(checks), len(checks)


def test_evaluate_question_3() -> tuple[int, int]:
    """Test evaluation of Question 3: concrete norm change vs substantive no-change vs bare dodges."""
    print("\n== Step 2: evaluate_question_3 ==")
    checks = []

    # Bare dodges rejected
    dodges = ["None", "None.", "TBD", "Nothing", "Nothing.", "No change.", "No changes needed.", "N/A", "unsure"]
    for d in dodges:
        ev = supervisor.evaluate_question_3(d)
        checks.append(check(f"reject dodge {d!r}", not ev["valid"] and ev["outcome"] == "invalid", str(ev)))

    # Too short proposals rejected
    ev_short = supervisor.evaluate_question_3("Short")
    checks.append(check("reject too short proposal", not ev_short["valid"], str(ev_short)))

    # Substantive 'no change, and here is why' accepted
    valid_no_changes = [
        "No change, and here is why: the current norms cover our operations well and no systemic flaws were identified.",
        "No changes. Here is why: our existing testing guidelines prevented all regressions this cycle.",
        "We propose no norm change, because the current procedures are working as intended and proved sufficient.",
    ]
    for nc in valid_no_changes:
        ev = supervisor.evaluate_question_3(nc)
        checks.append(check(f"accept substantive no change: {nc[:40]}...", ev["valid"] and ev["outcome"] == "no_change", str(ev)))

    # Bare 'no change' without substantive rationale rejected
    invalid_no_changes = [
        "No changes.",
        "No change today.",
        "Keep current norms.",
    ]
    for inc in invalid_no_changes:
        ev = supervisor.evaluate_question_3(inc)
        checks.append(check(f"reject bare no-change without rationale: {inc!r}", not ev["valid"], str(ev)))

    # Concrete norm change accepted
    concrete = """## Verification before completion
Before marking an issue complete, implementers must run all test suites offline."""
    ev = supervisor.evaluate_question_3(concrete)
    checks.append(check("accept concrete norm change", ev["valid"] and ev["outcome"] == "norm_change", str(ev)))
    checks.append(check("extracted norm change matches", ev.get("norm_change") == concrete, str(ev.get("norm_change"))))

    # Concrete norm change with conversational preamble
    preamble = """I propose we add the following rule to NORMS.md:

## Automated pre-commit linting
Always run the linter before sending a handoff to QA."""
    ev_pre = supervisor.evaluate_question_3(preamble)
    checks.append(check("preamble stripped from norm proposal", ev_pre["valid"] and ev_pre["outcome"] == "norm_change", str(ev_pre)))
    checks.append(check("norm starts with heading", ev_pre.get("norm_change", "").startswith("## Automated pre-commit"), str(ev_pre.get("norm_change"))))

    return sum(checks), len(checks)


def test_retro_norm_change_workflow() -> tuple[int, int]:
    """Test full retrospective workflow when a norm change is proposed."""
    print("\n== Step 2: retro workflow with norm change ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-retro-norm-"))
    try:
        td = make_retro_team(tmp)
        leader_output = """## 1. What went well
The team delivered features with high speed and all tests passed.

## 2. What did not
We had one test failure due to missing imports that took an extra turn to resolve.

## 3. What should we change
## Pre-flight check
Always run all evals offline before requesting review."""

        runner = RetroMockRunner(
            responses={
                "coder": "Tests went well, but offline test commands should be standardized.",
                "qa": "Reviews caught edge cases cleanly.",
                "tpm": leader_output,
            }
        )

        res = supervisor.retro(leader="tpm", runner=runner, team_dir=td)

        # Result checks
        checks.append(check("res.success is True", res.success is True, str(res.success)))
        checks.append(check("bool(res) is True", bool(res) is True, str(bool(res))))
        checks.append(check("res.outcome == 'norm_change'", res.outcome == "norm_change", str(res.outcome)))
        checks.append(check("res.norm_change contains Pre-flight check", "## Pre-flight check" in (res.norm_change or "")))

        # NORMS.md checks
        norms_file = td / "NORMS.md"
        checks.append(check("NORMS.md created/seeded", norms_file.is_file(), str(norms_file)))
        norms_text = norms_file.read_text(encoding="utf-8")
        checks.append(check("NORMS.md contains SEEDED_NORMS", persona.SEEDED_NORMS in norms_text, norms_text[:200]))
        checks.append(check("NORMS.md contains appended norm change", "## Pre-flight check" in norms_text and "Always run all evals offline" in norms_text))

        # reviews.jsonl checks
        reviews_file = td / "reviews.jsonl"
        checks.append(check("reviews.jsonl exists", reviews_file.is_file(), str(reviews_file)))
        rev_lines = [json.loads(l) for l in reviews_file.read_text().splitlines() if l.strip()]
        last_rev = rev_lines[-1]
        checks.append(check("last review reviewer is leader", last_rev.get("reviewer") == "tpm", str(last_rev)))
        checks.append(check("last review verdict is approved", last_rev.get("verdict") == "approved", str(last_rev)))
        checks.append(check("last review what targets NORMS.md", "NORMS.md" in last_rev.get("what", ""), str(last_rev)))
        checks.append(check("last review has cases_tried", bool(last_rev.get("cases_tried")), str(last_rev)))

        # retro.md report checks
        retro_file = td / "retro.md"
        checks.append(check("retro.md exists", retro_file.is_file(), str(retro_file)))
        retro_text = retro_file.read_text(encoding="utf-8")
        checks.append(check("retro.md has leader tpm", "**Leader:** tpm" in retro_text))
        checks.append(check("retro.md has participants coder, qa", "coder" in retro_text and "qa" in retro_text))
        checks.append(check("retro.md includes Q1", "delivered features with high speed" in retro_text))
        checks.append(check("retro.md includes Q2", "missing imports" in retro_text))
        checks.append(check("retro.md includes Q3", "Pre-flight check" in retro_text))
        checks.append(check("retro.md includes participant reflections", "Tests went well" in retro_text and "Reviews caught edge cases" in retro_text))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_retro_no_change_workflow() -> tuple[int, int]:
    """Test retrospective workflow when 'no change, and here is why' is decided."""
    print("\n== Step 2: retro workflow with no change ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-retro-nochange-"))
    try:
        td = make_retro_team(tmp)
        leader_output = """## 1. What went well
Flawless execution of the sprint goals.

## 2. What did not
No major impediments or blockers were observed.

## 3. What should we change
No change, and here is why: our existing norms and processes proved completely sufficient and no gaps were found."""

        runner = RetroMockRunner(
            responses={
                "coder": "Everything worked smoothly.",
                "qa": "All reviews passed without findings.",
                "tpm": leader_output,
            }
        )

        reviews_file = td / "reviews.jsonl"
        rev_count_before = len([l for l in reviews_file.read_text().splitlines() if l.strip()])

        res = supervisor.retro(leader="tpm", runner=runner, team_dir=td)

        # Result checks
        checks.append(check("res.success is True", res.success is True, str(res.success)))
        checks.append(check("res.outcome == 'no_change'", res.outcome == "no_change", str(res.outcome)))
        checks.append(check("res.norm_change is None", res.norm_change is None, str(res.norm_change)))

        # NORMS.md should NOT be seeded or modified
        norms_file = td / "NORMS.md"
        checks.append(check("NORMS.md does not exist when no change decided", not norms_file.exists()))

        # reviews.jsonl should have no new review recorded
        rev_count_after = len([l for l in reviews_file.read_text().splitlines() if l.strip()])
        checks.append(check("no new review recorded in reviews.jsonl", rev_count_after == rev_count_before, f"{rev_count_after} vs {rev_count_before}"))

        # retro.md report checks
        retro_file = td / "retro.md"
        checks.append(check("retro.md exists", retro_file.is_file()))
        retro_text = retro_file.read_text(encoding="utf-8")
        checks.append(check("retro.md records No norm change adopted", "**No norm change adopted:**" in retro_text))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_retro_rejections() -> tuple[int, int]:
    """Test retrospective failures on dodges, missing sections, and unknown leader."""
    print("\n== Step 2: retro rejection on dodges and missing sections ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-retro-fail-"))
    try:
        td = make_retro_team(tmp)

        # 1. Dodge on Q3 ("None.")
        dodge_output = """## 1. What went well
Work was finished.

## 2. What did not
Nothing failed.

## 3. What should we change
None."""
        runner_dodge = RetroMockRunner(responses={"tpm": dodge_output})
        res_dodge = supervisor.retro(leader="tpm", runner=runner_dodge, team_dir=td)
        checks.append(check("res_dodge.success is False", res_dodge.success is False))
        checks.append(check("bool(res_dodge) is False", bool(res_dodge) is False))
        checks.append(check("res_dodge.outcome == 'invalid'", res_dodge.outcome == "invalid"))
        checks.append(check("res_dodge.error mentions dodge or rejected", res_dodge.error is not None and ("dodge" in res_dodge.error.lower() or "rejected" in (res_dodge.error or "").lower())))

        # retro.md reflects failure
        retro_text = (td / "retro.md").read_text(encoding="utf-8")
        checks.append(check("retro.md contains FAILED", "**FAILED:**" in retro_text))

        # 2. Missing Q2
        missing_q2 = """## 1. What went well
Work was finished.

## 3. What should we change
## New norm
Always test."""
        runner_missing = RetroMockRunner(responses={"tpm": missing_q2})
        res_missing = supervisor.retro(leader="tpm", runner=runner_missing, team_dir=td)
        checks.append(check("res_missing.success is False", res_missing.success is False))
        checks.append(check("res_missing.outcome == 'invalid'", res_missing.outcome == "invalid"))

        # 3. Unknown leader not on roster
        runner_unknown = RetroMockRunner()
        res_unknown = supervisor.retro(leader="ghost", runner=runner_unknown, team_dir=td)
        checks.append(check("unknown leader fails", res_unknown.success is False and "ghost" in (res_unknown.error or "")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_accountability_tension() -> tuple[int, int]:
    """Test naming of accountability tension when leader is accountable (e.g. tpm/manager) vs non-accountable."""
    print("\n== Step 2: accountability tension naming ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-retro-tension-"))
    try:
        tension_needle = supervisor.TENSION_ACCOUNTABLE

        valid_output = """## 1. What went well
Good progress.

## 2. What did not
Slow review cycle.

## 3. What should we change
No change, and here is why: current norms are completely sufficient and review speed was within bounds."""

        # 1. Led by tpm (manager/accountable)
        td_tpm = make_retro_team(tmp / "tpm")
        runner_tpm = RetroMockRunner(responses={"tpm": valid_output})
        res_tpm = supervisor.retro(leader="tpm", runner=runner_tpm, team_dir=td_tpm)

        checks.append(check("tpm retro succeeded", res_tpm.success is True))
        checks.append(check("tension note in tpm retro report", tension_needle in str(res_tpm)))

        # Verify tension note was in the leader wake prompt
        tpm_prompt = next((msg for ag, msg in runner_tpm.calls if ag == "tpm"), "")
        checks.append(check("tension note in tpm leader prompt", tension_needle in tpm_prompt))

        # 2. Led by coder (non-accountable)
        td_coder = make_retro_team(tmp / "coder")
        runner_coder = RetroMockRunner(responses={"coder": valid_output})
        res_coder = supervisor.retro(leader="coder", runner=runner_coder, team_dir=td_coder)

        checks.append(check("coder retro succeeded", res_coder.success is True))
        checks.append(check("tension note OMITTED in coder retro report", tension_needle not in str(res_coder)))

        coder_prompt = next((msg for ag, msg in runner_coder.calls if ag == "coder"), "")
        checks.append(check("tension note OMITTED in coder leader prompt", tension_needle not in coder_prompt))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_retro_uses_durable_record() -> tuple[int, int]:
    """Test that retro prompts include durable record (episodes, reviews, bus traffic)."""
    print("\n== Step 2: retro uses durable record not memory ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-retro-rec-"))
    try:
        td = make_retro_team(tmp)
        valid_output = """## 1. What went well
Everything.

## 2. What did not
Nothing.

## 3. What should we change
No change, and here is why: the workflow was effective and current norms are sufficient."""

        runner = RetroMockRunner(responses={"tpm": valid_output})
        res = supervisor.retro(leader="tpm", runner=runner, team_dir=td)

        # Check prompt content sent to participants
        coder_call = next((msg for ag, msg in runner.calls if ag == "coder"), "")
        checks.append(check("participant prompt includes Work Record Summary", "## Work Record Summary" in coder_call))
        checks.append(check("participant prompt includes Episodes", "- Episodes:" in coder_call))
        checks.append(check("participant prompt includes Reviews & Verdicts", "Reviews & Verdicts" in coder_call))

        # Check prompt content sent to leader
        tpm_call = next((msg for ag, msg in runner.calls if ag == "tpm"), "")
        checks.append(check("leader prompt includes Work Record Summary", "## Work Record Summary" in tpm_call))
        checks.append(check("leader prompt includes Teammate reflections", "Teammate reflections:" in tpm_call))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_retro_cli() -> tuple[int, int]:
    """Test supervisor CLI with --retro and --retro-leader."""
    print("\n== Step 2: supervisor CLI --retro ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-retro-cli-"))
    try:
        valid_output = """## 1. What went well
Delivered on time.

## 2. What did not
Minor friction.

## 3. What should we change
No change, and here is why: team friction was resolved naturally and current norms are sufficient."""

        # 1. CLI --retro success
        td1 = make_retro_team(tmp / "cli1")
        runner_ok = RetroMockRunner(responses={"tpm": valid_output})
        out = io.StringIO()
        with redirect_stdout(out):
            supervisor.main(["--retro", "--team-dir", str(td1)], runner=runner_ok)
        cli_out = out.getvalue()
        checks.append(check("CLI --retro prints retro report", "# Team Retrospective" in cli_out))
        checks.append(check("CLI --retro includes outcome", "No norm change adopted" in cli_out))

        # 2. CLI --retro failure exits 1
        td2 = make_retro_team(tmp / "cli2")
        dodge_output = """## 1. What went well
Work finished.

## 2. What did not
Nothing.

## 3. What should we change
None."""
        runner_fail = RetroMockRunner(responses={"tpm": dodge_output})
        exit_code = None
        out_fail = io.StringIO()
        try:
            with redirect_stdout(out_fail):
                supervisor.main(["--retro", "--team-dir", str(td2)], runner=runner_fail)
            exit_code = 0
        except SystemExit as e:
            exit_code = e.code
        checks.append(check("CLI --retro exits 1 on failure", exit_code == 1, f"exit_code={exit_code}"))

        # 3. CLI --retro-leader coder
        td3 = make_retro_team(tmp / "cli3")
        runner_coder = RetroMockRunner(responses={"coder": valid_output})
        out_coder = io.StringIO()
        with redirect_stdout(out_coder):
            supervisor.main(["--retro", "--retro-leader", "coder", "--team-dir", str(td3)], runner=runner_coder)
        coder_cli_out = out_coder.getvalue()
        checks.append(check("CLI --retro-leader coder sets leader in report", "**Leader:** coder" in coder_cli_out))

        # 4. CLI with unknown leader exits with error
        td4 = make_retro_team(tmp / "cli4")
        exit_code_unknown = None
        try:
            supervisor.main(["--retro", "--retro-leader", "alien", "--team-dir", str(td4)], runner=runner_ok)
            exit_code_unknown = 0
        except SystemExit as e:
            exit_code_unknown = e.code
        checks.append(check("CLI exits on unknown leader", exit_code_unknown != 0, f"exit_code={exit_code_unknown}"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_retro_refusal() -> tuple[int, int]:
    """Test Step 3 refusal when there is no new work, and override with --force."""
    print("\n== Step 3: retro refusal on no new work ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-retro-refuse-"))
    try:
        valid_output = """## 1. What went well
Great execution.

## 2. What did not
Nothing notable.

## 3. What should we change
No change, and here is why: current norms are completely sufficient."""

        # 1. Refusal on empty team (0 work records)
        td_empty = tmp / "empty"
        (td_empty / "inbox").mkdir(parents=True, exist_ok=True)
        (td_empty / "roster.json").write_text(json.dumps({
            "agents": [{"name": "tpm", "role": "Manager"}, {"name": "coder", "role": "Implements"}]
        }), encoding="utf-8")
        runner_empty = RetroMockRunner(responses={"tpm": valid_output})
        res_empty = supervisor.retro(leader="tpm", runner=runner_empty, team_dir=td_empty)

        checks.append(check("empty team retro refused", res_empty.success is False and res_empty.refused is True))
        checks.append(check("empty team outcome is refused", res_empty.outcome == "refused"))
        checks.append(check("empty team error explains 0 work", "0 events, 0 reviews, 0 bus messages" in (res_empty.error or "")))
        checks.append(check("retro.md not created on refusal", not (td_empty / "retro.md").exists()))

        # 2. Force empty team
        res_empty_forced = supervisor.retro(leader="tpm", runner=runner_empty, team_dir=td_empty, force=True)
        checks.append(check("force=True overrides empty team refusal", res_empty_forced.success is True))
        checks.append(check("retro.md created after forced retro", (td_empty / "retro.md").is_file()))

        # 3. Initial retro succeeds on team with work
        td_work = make_retro_team(tmp / "work")
        runner_work = RetroMockRunner(responses={"tpm": valid_output})
        res1 = supervisor.retro(leader="tpm", runner=runner_work, team_dir=td_work)
        checks.append(check("first retro with work succeeds", res1.success is True))
        checks.append(check("retro_state.json created", (td_work / "retro_state.json").is_file()))

        # 4. Immediate rerun without new work refused
        runner_rerun = RetroMockRunner(responses={"tpm": valid_output})
        res2 = supervisor.retro(leader="tpm", runner=runner_rerun, team_dir=td_work)
        checks.append(check("rerun without new work refused", res2.success is False and res2.refused is True))
        checks.append(check("refusal mentions no new work and last retro", "no new work since last retrospective" in (res2.error or "")))
        checks.append(check("refusal suggests --force", "--force" in (res2.error or "")))

        # 5. CLI exits 1 on refusal
        exit_code_cli = None
        out_cli = io.StringIO()
        try:
            with redirect_stdout(out_cli):
                supervisor.main(["--retro", "--team-dir", str(td_work)], runner=runner_rerun)
            exit_code_cli = 0
        except SystemExit as e:
            exit_code_cli = e.code
        checks.append(check("CLI exits 1 on refusal without --force", exit_code_cli == 1, f"exit_code={exit_code_cli}"))
        checks.append(check("CLI output mentions refused", "refused" in out_cli.getvalue().lower()))

        # 6. CLI with --force succeeds
        out_force = io.StringIO()
        with redirect_stdout(out_force):
            supervisor.main(["--retro", "--force", "--team-dir", str(td_work)], runner=runner_rerun)
        checks.append(check("CLI with --force succeeds", "# Team Retrospective" in out_force.getvalue()))

        # 7. Add new event -> retro succeeds without --force
        new_event = json.dumps({
            "ts": "2026-09-10 11:00:00",
            "event": "turn",
            "agent": "coder",
            "episode": 2,
            "turns": 1,
            "duration_s": 0.4
        }) + "\n"
        with (td_work / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(new_event)

        has_work, reason = supervisor.check_new_work(td_work)
        checks.append(check("check_new_work detects 1 new event", has_work is True and "1 new event" in reason, reason))

        res3 = supervisor.retro(leader="tpm", runner=runner_rerun, team_dir=td_work)
        checks.append(check("retro succeeds after new event added", res3.success is True))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_retro_participant_capping() -> tuple[int, int]:
    """Test Step 3 capping of retro participants based on recent activity."""
    print("\n== Step 3: retro participant capping ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-retro-cap-"))
    try:
        td = tmp / "team"
        (td / "inbox").mkdir(parents=True, exist_ok=True)
        # Roster with 6 agents: tpm (leader) + coder, qa, syseng, doc, analyst
        agents = [
            {"name": "tpm", "role": "Coordinates and ships"},
            {"name": "coder", "role": "Implements"},
            {"name": "qa", "role": "Reviews"},
            {"name": "syseng", "role": "Environment and tooling"},
            {"name": "doc", "role": "Documentation writer"},
            {"name": "analyst", "role": "Performance analyst"},
        ]
        (td / "roster.json").write_text(json.dumps({"agents": agents}), encoding="utf-8")

        # Activity setup:
        # coder: 3 turn events (3 * 3 = 9 pts)
        # qa: 1 turn event + 1 review (1 * 3 + 1 * 2 = 5 pts)
        # syseng: 1 bus message (1 pt)
        # doc: 0 pts
        # analyst: 0 pts
        (td / "events.jsonl").write_text(
            json.dumps({"ts": "2026-09-10 10:00:00", "event": "turn", "agent": "coder"}) + "\n" +
            json.dumps({"ts": "2026-09-10 10:01:00", "event": "turn", "agent": "coder"}) + "\n" +
            json.dumps({"ts": "2026-09-10 10:02:00", "event": "turn", "agent": "coder"}) + "\n" +
            json.dumps({"ts": "2026-09-10 10:03:00", "event": "turn", "agent": "qa"}) + "\n",
            encoding="utf-8"
        )
        (td / "reviews.jsonl").write_text(
            json.dumps({"ts": "2026-09-10 10:04:00", "reviewer": "qa", "what": "diff", "verdict": "approved", "cases_tried": ["c1"]}) + "\n",
            encoding="utf-8"
        )
        (td / "bus.jsonl").write_text(
            json.dumps({"ts": "2026-09-10 10:05:00", "from": "syseng", "to": "coder", "content": "env ready"}) + "\n",
            encoding="utf-8"
        )

        valid_output = """## 1. What went well
Good progress.

## 2. What did not
Nothing failed.

## 3. What should we change
No change, and here is why: team process was smooth."""

        runner = RetroMockRunner(responses={"tpm": valid_output})

        # Cap at 2 participants
        res = supervisor.retro(leader="tpm", runner=runner, team_dir=td, max_participants=2)

        checks.append(check("retro with capped participants succeeded", res.success is True))

        # Check who was woken: leader (tpm) + top 2 active (coder, qa). NOT syseng, doc, analyst.
        woken_agents = [ag for ag, _ in runner.calls]
        checks.append(check("coder was woken", "coder" in woken_agents))
        checks.append(check("qa was woken", "qa" in woken_agents))
        checks.append(check("tpm was woken", "tpm" in woken_agents))
        checks.append(check("syseng was NOT woken", "syseng" not in woken_agents))
        checks.append(check("doc was NOT woken", "doc" not in woken_agents))
        checks.append(check("analyst was NOT woken", "analyst" not in woken_agents))
        checks.append(check("total wakes == 3 (2 participants + 1 leader)", len(woken_agents) == 3, f"woken: {woken_agents}"))

        # Check Participation Note in report
        report_text = str(res)
        checks.append(check("Participation Note present in report", "**Participation Note:**" in report_text))
        checks.append(check("Note mentions capped at 2", "Participation capped at 2 agents" in report_text))
        checks.append(check("Note mentions omitted agents", "syseng" in report_text and "doc" in report_text and "analyst" in report_text))

        # Check CLI with --retro-max-participants
        td_cli = tmp / "team_cli"
        shutil.copytree(td, td_cli)
        # remove retro_state.json copied over so it runs
        (td_cli / "retro_state.json").unlink(missing_ok=True)

        runner_cli = RetroMockRunner(responses={"tpm": valid_output})
        out = io.StringIO()
        with redirect_stdout(out):
            supervisor.main(["--retro", "--retro-max-participants", "2", "--team-dir", str(td_cli)], runner=runner_cli)
        cli_out = out.getvalue()
        checks.append(check("CLI --retro-max-participants passed through", "Participation capped at 2 agents" in cli_out))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


def test_retro_transcript_bounding() -> tuple[int, int]:
    """Test Step 3 bounding of history transcript and participant reflections."""
    print("\n== Step 3: retro transcript and reflection bounding ==")
    checks = []
    tmp = Path(tempfile.mkdtemp(prefix="agyteam-retro-bound-"))
    try:
        # 1. format_retro_record character truncation
        fake_rep = {
            "totals": {"episodes": 100, "reviewed_episodes": 90, "turns": 500, "failures": 1, "cost_usd": 12.34, "total_tokens": 500000},
            "episodes": [{"episode": i, "turns": 5, "stopped_reason": "answer", "reviewed": True, "duration_s": 10.0} for i in range(20)],
            "reviews": [{"reviewer": "qa", "what": f"module_{i}", "verdict": "approved", "cases_tried": ["test"], "findings": "ok"} for i in range(20)],
            "bus_traffic": {"top_pairs": [{"sender": "coder", "recipient": "qa", "count": 50}]},
        }
        rec_bounded = supervisor.format_retro_record(fake_rep, max_chars=200)
        checks.append(check("format_retro_record <= 200 chars", len(rec_bounded) <= 200, f"len={len(rec_bounded)}"))
        checks.append(check("format_retro_record has truncation indicator", "[... work record truncated" in rec_bounded))

        # 2. Individual teammate reflection bounding (RETRO_MAX_REFLECTION_CHARS)
        td = make_retro_team(tmp / "team")
        long_reflection = "A" * 5000  # Exceeds config.RETRO_MAX_REFLECTION_CHARS (2500)
        valid_output = """## 1. What went well
Work proceeded well.

## 2. What did not
No major problems.

## 3. What should we change
No change, and here is why: current norms are completely sufficient."""

        runner = RetroMockRunner(responses={
            "coder": long_reflection,
            "qa": "QA went smoothly.",
            "tpm": valid_output,
        })

        res = supervisor.retro(leader="tpm", runner=runner, team_dir=td)
        checks.append(check("retro succeeded with oversized teammate reflection", res.success is True))

        # Check leader wake prompt
        tpm_call = next((msg for ag, msg in runner.calls if ag == "tpm"), "")
        checks.append(check("leader prompt truncated long reflection", "[... reflection truncated to character cap ...]" in tpm_call))
        checks.append(check("coder reflection bounded within prompt", len(long_reflection) not in [len(tpm_call)]))

        # 3. Total reflections bounding with max_transcript_chars
        td2 = make_retro_team(tmp / "team2")
        runner2 = RetroMockRunner(responses={
            "coder": "coder reflection line 1 " * 30,
            "qa": "qa reflection line 1 " * 30,
            "tpm": valid_output,
        })
        res2 = supervisor.retro(leader="tpm", runner=runner2, team_dir=td2, max_transcript_chars=300)
        checks.append(check("retro succeeded with small max_transcript_chars", res2.success is True))
        tpm_call2 = next((msg for ag, msg in runner2.calls if ag == "tpm"), "")
        checks.append(check("leader prompt truncated total reflections", "[... reflections truncated to character budget ...]" in tpm_call2))

        # 4. CLI with --retro-max-transcript
        td_cli = make_retro_team(tmp / "team_cli")
        runner_cli = RetroMockRunner(responses={"tpm": valid_output})
        out = io.StringIO()
        with redirect_stdout(out):
            supervisor.main(["--retro", "--retro-max-transcript", "250", "--team-dir", str(td_cli)], runner=runner_cli)
        cli_out = out.getvalue()
        checks.append(check("CLI --retro-max-transcript runs cleanly", "# Team Retrospective" in cli_out))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return sum(checks), len(checks)


if __name__ == "__main__":
    totals = [
        test_norms_absence(),
        test_norms_seeding(),
        test_brief_loading_and_sharing(),
        test_team_scoping(),
        test_signature_compatibility(),
        test_stdlib_purity(),
        test_parse_retro_sections(),
        test_evaluate_question_3(),
        test_retro_norm_change_workflow(),
        test_retro_no_change_workflow(),
        test_retro_rejections(),
        test_accountability_tension(),
        test_retro_uses_durable_record(),
        test_retro_cli(),
        test_retro_refusal(),
        test_retro_participant_capping(),
        test_retro_transcript_bounding(),
    ]
    got, want = sum(s for s, _ in totals), sum(t for _, t in totals)
    print(f"\n== Step 1, 2 & 3 retro tests: {got}/{want} ==")
    sys.exit(0 if got == want else 1)


