"""Scripted scalability test suite for 12-agent agyteam configurations.

Probes and evaluates:
1. Retro participation:
   - Behavior with 12 agents under default RETRO_MAX_PARTICIPANTS (4).
   - Omission of 7 out of 11 candidate peers (63.6% peer exclusion, 66.7% total exclusion).
   - Full participation override (max_participants=11) and transcript character budget pressure
     (RETRO_MAX_TRANSCRIPT_CHARS=10_000 truncation drops late-arriving reflections).
2. Hop budget & cascade degradation:
   - 12-agent daisy-chain consumption (12 hops forward).
   - 12-agent fan-out consumption (13 hops baseline: 1 manager + 11 workers + 1 aggregation).
   - Hop budget exhaustion on multi-turn cycle / verification loop (reaching MAX_HOPS=32).
3. System prompt & token scaling:
   - Expansion of persona.roster_lines() and persona.brief() from 5 agents to 12 agents.
   - Character and token growth metrics per turn and cumulative per episode.
4. Role collisions & ambiguous delegation:
   - High-overlap specialized engineering roles (backend_api vs backend_core, qa_unit vs qa_e2e, etc.).
   - Routing ambiguity and multi-dispatch risk under naive keyword/semantic matching.

Offline only: uses MockRunner/ScriptedRunner without external model API calls.
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
ROOT = EVALS_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

from rpc_util import check
from agyteam import config, persona, supervisor
from agyteam.runner import Runner
from agyteam.transport import load as load_transport


# ---------------------------------------------------------------------------
# Test Runners
# ---------------------------------------------------------------------------

class RetroMockRunner(Runner):
    """Offline runner for retro reflections and leader synthesis."""
    label = "retro-mock"

    def __init__(self, responses: dict[str, str] | None = None, default_response: str = "ok"):
        super().__init__()
        self.responses = responses or {}
        self.default_response = default_response
        self.calls: list[tuple[str, str]] = []
        self.woken: list[str] = []

    def wake(self, agent: str, message: str) -> str:
        self.woken.append(agent)
        self.calls.append((agent, message))
        if agent in self.responses:
            return self.responses[agent]
        return self.default_response


class DaisyChainRunner(Runner):
    """Offline runner executing a daisy-chain cascade across ordered agents."""
    label = "daisy-runner"

    def __init__(self, agent_names: list[str]):
        super().__init__()
        self.agent_names = agent_names
        self.woken: list[str] = []

    def wake(self, agent: str, message: str) -> str:
        self.woken.append(agent)
        idx = self.agent_names.index(agent)
        t = load_transport(agent)
        if idx < len(self.agent_names) - 1:
            next_agent = self.agent_names[idx + 1]
            t.send(next_agent, f"handoff from {agent} to {next_agent}")
        else:
            t.send("user", f"pipeline completed by {agent}")
        t.close()
        return f"{agent} forwarded"


class FanoutRunner(Runner):
    """Offline runner simulating 1 manager fanning out to N workers, who reply."""
    label = "fanout-runner"

    def __init__(self, manager: str, workers: list[str]):
        super().__init__()
        self.manager = manager
        self.workers = workers
        self.woken: list[str] = []
        self.manager_turns = 0

    def wake(self, agent: str, message: str) -> str:
        self.woken.append(agent)
        t = load_transport(agent)
        if agent == self.manager:
            self.manager_turns += 1
            if self.manager_turns == 1:
                # Dispatch subtasks to all workers
                for w in self.workers:
                    t.send(w, f"subtask for {w}")
            else:
                # Aggregate results and respond to user
                t.send("user", "aggregated final answer from all workers")
        else:
            # Worker finishes and replies to manager
            t.send(self.manager, f"result from {agent}")
        t.close()
        return f"{agent} finished turn"


class MultiPassDaisyRunner(Runner):
    """Offline runner cycling across 12 agents forward, backward, and forward again."""
    label = "multipass-runner"

    def __init__(self, agent_names: list[str]):
        super().__init__()
        self.agent_names = agent_names
        self.woken: list[str] = []

    def wake(self, agent: str, message: str) -> str:
        self.woken.append(agent)
        idx = self.agent_names.index(agent)
        t = load_transport(agent)
        # Pass 1 (turns 1-12): 0 -> 1 -> ... -> 11
        # Pass 2 (turns 13-23): 11 -> 10 -> ... -> 0
        # Pass 3 (turns 24-35): 0 -> 1 -> ... -> 11 -> user
        turn_count = len(self.woken)
        if turn_count <= 12:
            if idx < 11:
                t.send(self.agent_names[idx + 1], "pass 1 fwd")
            else:
                t.send(self.agent_names[idx - 1], "pass 2 rev")
        elif turn_count <= 23:
            if idx > 0:
                t.send(self.agent_names[idx - 1], "pass 2 rev")
            else:
                t.send(self.agent_names[idx + 1], "pass 3 fwd")
        else:
            if idx < 11:
                t.send(self.agent_names[idx + 1], "pass 3 fwd")
            else:
                t.send("user", "pipeline finished after pass 3")
        t.close()
        return f"{agent} processed turn {turn_count}"


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def make_12_agent_team(prefix="agyteam-scale12-") -> tuple[Path, list[str], list[dict]]:
    """Create a temporary team directory with 12 agents."""
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    td = tmp / "team"
    (td / "inbox").mkdir(parents=True, exist_ok=True)
    os.environ["AGYTEAM_TEAM_DIR"] = str(td)
    os.environ["AGYTEAM_DURABLE_DIR"] = str(tmp)

    agents = [
        {"name": "tpm", "role": "Coordinates and facilitates retros. Faces inward."},
        {"name": "manager", "role": "Faces outward. Takes problem from user, gatekeeper, accountable."},
        {"name": "architect", "role": "Defines module contracts, component interfaces, and system boundaries."},
        {"name": "backend_core", "role": "Implements core backend domain logic, database models, and data persistence."},
        {"name": "backend_api", "role": "Implements backend API endpoints, HTTP request routing, and service responses."},
        {"name": "frontend_ui", "role": "Implements frontend UI visual layouts, HTML/CSS styling, and components."},
        {"name": "frontend_state", "role": "Implements frontend client state store, UI data flow, and API fetching."},
        {"name": "syseng", "role": "Owns host environment, runtime virtualenvs, dependencies, and packaging."},
        {"name": "devops", "role": "Maintains deployment infrastructure, CI/CD pipelines, and container runtime."},
        {"name": "qa_unit", "role": "Reviews and verifies test suites, unit test coverage, and code assertions."},
        {"name": "qa_e2e", "role": "Reviews and verifies integration test suites, end-to-end user workflows, and regressions."},
        {"name": "secops", "role": "Audits security boundaries, secrets management, and access permissions."},
    ]
    (td / "roster.json").write_text(json.dumps({"agents": agents}), encoding="utf-8")
    agent_names = [a["name"] for a in agents]
    return td, agent_names, agents


# ---------------------------------------------------------------------------
# Test 1: Retro 12-Participant Cutoff
# ---------------------------------------------------------------------------

def test_retro_12_participant_cutoff() -> tuple[int, int]:
    """Verify that RETRO_MAX_PARTICIPANTS=4 cuts off 7 out of 11 candidate peers."""
    print("\n== 1. Retro Participation: 12-Agent Default Cutoff ==")
    checks = []
    td, agent_names, agents = make_12_agent_team(prefix="agyteam-retro-cut-")
    try:
        # Generate activity in events.jsonl so all 11 peers have non-zero score.
        # leader is tpm. 11 candidate peers: manager + 10 specialists.
        candidates = [a for a in agent_names if a != "tpm"]
        events_lines = []
        for i, c in enumerate(candidates):
            # Stagger turns so ranking is deterministic
            turn_count = len(candidates) - i
            for t in range(turn_count):
                events_lines.append(json.dumps({
                    "ts": f"2026-09-12 10:{i:02d}:{t:02d}",
                    "event": "turn",
                    "agent": c,
                }))
        (td / "events.jsonl").write_text("\n".join(events_lines) + "\n", encoding="utf-8")

        valid_retro_output = (
            "## 1. What went well\nGood execution.\n\n"
            "## 2. What did not\nCoordination bottlenecks at 12 agents.\n\n"
            "## 3. What should we change\nNo change, and here is why: process remained sound."
        )

        runner = RetroMockRunner(responses={"tpm": valid_retro_output})
        res = supervisor.retro(leader="tpm", runner=runner, team_dir=td)

        checks.append(check("Retro execution succeeded", res.success is True))

        # Check who was woken: leader (tpm) + top 4 active candidate peers
        woken_participants = [ag for ag in runner.woken if ag != "tpm"]
        omitted_candidates = [ag for ag in candidates if ag not in woken_participants]

        checks.append(check("Exactly 4 candidate peers woken for reflections",
                            len(woken_participants) == 4,
                            f"woken: {woken_participants}"))
        checks.append(check("Exactly 7 candidate peers omitted from reflections",
                            len(omitted_candidates) == 7,
                            f"omitted: {omitted_candidates}"))
        checks.append(check("Total wakes == 5 (4 participants + 1 leader)",
                            len(runner.woken) == 5,
                            f"total wakes: {len(runner.woken)}"))

        # Check Participation Note in generated retro report
        report_text = str(res)
        checks.append(check("Participation Note present in retro markdown",
                            "**Participation Note:**" in report_text))
        checks.append(check("Note states participation capped at 4 agents",
                            "Participation capped at 4 agents" in report_text))

        all_omitted_named = all(om in report_text for om in omitted_candidates)
        checks.append(check("Note lists all 7 omitted agents",
                            all_omitted_named,
                            f"report: {report_text[:400]}"))

        # Exclusion rate quantification
        exclusion_rate = len(omitted_candidates) / len(candidates)
        checks.append(check("Peer exclusion rate is exactly 7/11 (63.6%)",
                            abs(exclusion_rate - (7 / 11)) < 1e-4,
                            f"exclusion rate: {exclusion_rate:.1%}"))

    finally:
        shutil.rmtree(td.parent, ignore_errors=True)
    return sum(checks), len(checks)


# ---------------------------------------------------------------------------
# Test 2: Full Participation Transcript Overflow
# ---------------------------------------------------------------------------

def test_retro_12_full_participation_transcript_overflow() -> tuple[int, int]:
    """Verify that lifting participant cap to 11 hits RETRO_MAX_TRANSCRIPT_CHARS ceiling."""
    print("\n== 2. Retro Participation: Full 11-Peer Reflection Transcript Bounding ==")
    checks = []
    td, agent_names, agents = make_12_agent_team(prefix="agyteam-retro-bound-")
    try:
        candidates = [a for a in agent_names if a != "tpm"]
        events_lines = [json.dumps({"ts": f"2026-09-12 10:00:{i:02d}", "event": "turn", "agent": c})
                        for i, c in enumerate(candidates)]
        (td / "events.jsonl").write_text("\n".join(events_lines) + "\n", encoding="utf-8")

        # Each candidate submits a realistic reflection of 1,200 chars (within 2,500 reflection cap)
        # Total reflection chars = 11 * 1,200 = 13,200 chars.
        # This exceeds config.RETRO_MAX_TRANSCRIPT_CHARS = 10,000.
        responses = {"tpm": "## 1. What went well\nSolid.\n\n## 2. What did not\nNone.\n\n## 3. What should we change\nNo change, and here is why: verified."}
        for c in candidates:
            responses[c] = f"Detailed reflection from {c}: " + ("invariants and metrics " * 75)

        runner = RetroMockRunner(responses=responses)
        res = supervisor.retro(leader="tpm", runner=runner, team_dir=td, max_participants=11)

        checks.append(check("Retro with max_participants=11 succeeded", res.success is True))
        checks.append(check("All 11 candidate peers were woken for reflections",
                            len([ag for ag in runner.woken if ag != "tpm"]) == 11))
        checks.append(check("Total wakes == 12 (11 peers + 1 leader)",
                            len(runner.woken) == 12))

        # Inspect leader wake prompt
        leader_prompt = next(prompt for ag, prompt in runner.calls if ag == "tpm")
        checks.append(check("Leader prompt contains character budget truncation notice",
                            "[... reflections truncated to character budget ...]" in leader_prompt))

        # Check which agents were included vs truncated
        early_peers_present = all(f"Detailed reflection from {c}" in leader_prompt for c in candidates[:6])
        late_peers_dropped = not any(f"Detailed reflection from {c}" in leader_prompt for c in candidates[-3:])

        checks.append(check("First 6 peers have reflections present in leader prompt",
                            early_peers_present))
        checks.append(check("Last 3 peers (e.g. qa_e2e, secops) reflections dropped by transcript ceiling",
                            late_peers_dropped))

    finally:
        shutil.rmtree(td.parent, ignore_errors=True)
    return sum(checks), len(checks)


# ---------------------------------------------------------------------------
# Test 3: Daisy-Chain 12 Hop Consumption
# ---------------------------------------------------------------------------

def test_daisy_chain_12_hop_consumption() -> tuple[int, int]:
    """Measure hop consumption for a sequential forward pass through all 12 agents."""
    print("\n== 3. Hop Budget: 12-Agent Daisy-Chain Sequential Cascade ==")
    checks = []
    td, agent_names, agents = make_12_agent_team(prefix="agyteam-daisy-")
    try:
        # Seed initial prompt to first agent
        t_user = load_transport("user")
        t_user.send(agent_names[0], "start sequential pipeline")
        t_user.close()

        runner = DaisyChainRunner(agent_names)
        sup = supervisor.Supervisor(
            agents=agent_names,
            runner=runner,
            team_dir=td,
            max_hops=32,
            require_review=False,
        )
        turns = sup.run_until_idle()

        checks.append(check("Supervisor finished with user answered",
                            "the user was answered" in sup.stopped,
                            f"stopped: {sup.stopped}"))
        checks.append(check("Single daisy-chain pass consumed exactly 12 hops",
                            sup.hops == 12,
                            f"hops: {sup.hops}"))
        checks.append(check("All 12 agents woken in strict sequential order",
                            runner.woken == agent_names,
                            f"woken: {runner.woken}"))

        remaining_headroom = sup.max_hops - sup.hops
        checks.append(check("Remaining hop headroom is exactly 20 hops (62.5% remaining)",
                            remaining_headroom == 20,
                            f"headroom: {remaining_headroom}"))

    finally:
        shutil.rmtree(td.parent, ignore_errors=True)
    return sum(checks), len(checks)


# ---------------------------------------------------------------------------
# Test 4: Fan-out Cascade and Hop Budget
# ---------------------------------------------------------------------------

def test_fanout_cascade_and_hop_budget() -> tuple[int, int]:
    """Measure hop consumption for 1 manager fanning out to 11 workers simultaneously."""
    print("\n== 4. Hop Budget: 12-Agent Broadcast / Fan-Out Tree ==")
    checks = []
    td, agent_names, agents = make_12_agent_team(prefix="agyteam-fanout-")
    try:
        manager = "manager"
        workers = [a for a in agent_names if a != manager]

        t_user = load_transport("user")
        t_user.send(manager, "delegate subtasks to all 11 specialists")
        t_user.close()

        runner = FanoutRunner(manager=manager, workers=workers)
        sup = supervisor.Supervisor(
            agents=agent_names,
            runner=runner,
            team_dir=td,
            max_hops=32,
            require_review=False,
        )
        turns = sup.run_until_idle()

        checks.append(check("Fan-out execution answered user",
                            "the user was answered" in sup.stopped,
                            f"stopped: {sup.stopped}"))
        checks.append(check("Fan-out consumed exactly 13 hops (1 mgr + 11 workers + 1 aggregation)",
                            sup.hops == 13,
                            f"hops: {sup.hops}"))
        checks.append(check("Manager was woken exactly twice (dispatch + aggregate)",
                            runner.woken.count(manager) == 2,
                            f"mgr wakes: {runner.woken.count(manager)}"))
        checks.append(check("All 11 workers were woken once",
                            all(runner.woken.count(w) == 1 for w in workers)))
        checks.append(check("Remaining hop headroom after fan-out is 19 hops (59.4% remaining)",
                            sup.max_hops - sup.hops == 19))

    finally:
        shutil.rmtree(td.parent, ignore_errors=True)
    return sum(checks), len(checks)


# ---------------------------------------------------------------------------
# Test 5: Hop Budget Exhaustion on Multi-Pass Cycle
# ---------------------------------------------------------------------------

def test_hop_budget_exhaustion_on_12_agent_cycle() -> tuple[int, int]:
    """Prove that a multi-pass or remediation cycle across 12 agents depletes MAX_HOPS=32."""
    print("\n== 5. Hop Budget: Exhaustion on Multi-Pass Cascade ==")
    checks = []
    td, agent_names, agents = make_12_agent_team(prefix="agyteam-exhaust-")
    try:
        t_user = load_transport("user")
        t_user.send(agent_names[0], "execute multi-phase rollout across 12 agents")
        t_user.close()

        runner = MultiPassDaisyRunner(agent_names)
        sup = supervisor.Supervisor(
            agents=agent_names,
            runner=runner,
            team_dir=td,
            max_hops=32,
            require_review=False,
        )
        turns = sup.run_until_idle()

        checks.append(check("Supervisor halted due to hop budget exhaustion",
                            sup.stopped == "hop budget of 32 reached",
                            f"stopped: {sup.stopped}"))
        checks.append(check("Supervisor reached max_hops (32 hops)",
                            sup.hops == 32,
                            f"hops: {sup.hops}"))
        checks.append(check("Exactly 32 turns were executed before hard termination",
                            turns == 32,
                            f"turns: {turns}"))

        # Verify that the user NEVER received the final answer
        user_inbox = load_transport("user").fetch()
        checks.append(check("User received 0 messages due to budget exhaustion starvation",
                            len(user_inbox) == 0,
                            f"user inbox msgs: {len(user_inbox)}"))

    finally:
        shutil.rmtree(td.parent, ignore_errors=True)
    return sum(checks), len(checks)


# ---------------------------------------------------------------------------
# Test 6: Brief and Token Scaling for 12 Agents
# ---------------------------------------------------------------------------

def test_brief_token_scaling_12_agents() -> tuple[int, int]:
    """Quantify system prompt and roster expansion between 5-agent and 12-agent teams."""
    print("\n== 6. System Prompt & Token Scaling: 5 vs 12 Agents ==")
    checks = []

    agents_5 = [
        {"name": "manager", "role": "Faces outward. Takes problem from user, gatekeeper, accountable."},
        {"name": "tpm", "role": "Faces inward. Decomposes work, tracks outstanding items, leads retros."},
        {"name": "coder", "role": "Implements. Writes changes and unit tests, runs them before handoff."},
        {"name": "qa", "role": "Reviews diffs, constructs unwritten cases, records review verdict."},
        {"name": "syseng", "role": "Owns environment and tooling, runs offline eval board, checks stdlib purity."},
    ]

    _, _, agents_12 = make_12_agent_team(prefix="agyteam-token-")
    try:
        r5 = persona.roster_lines("manager", agents_5)
        r12 = persona.roster_lines("manager", agents_12)
        b5 = persona.brief("manager", agents_5)
        b12 = persona.brief("manager", agents_12)

        len_r5, len_r12 = len(r5), len(r12)
        len_b5, len_b12 = len(b5), len(b12)

        roster_ratio = len_r12 / len_r5
        brief_diff = len_b12 - len_b5

        # Checks
        checks.append(check("5-agent roster has 5 lines (4 peers + user)",
                            len(r5.splitlines()) == 5))
        checks.append(check("12-agent roster has 12 lines (11 peers + user)",
                            len(r12.splitlines()) == 12))
        checks.append(check("Roster character size expands by >2.4x (5 vs 12 agents)",
                            roster_ratio >= 2.4,
                            f"ratio: {roster_ratio:.2f} ({len_r12} vs {len_r5} chars)"))
        checks.append(check("System prompt brief expands by >500 characters",
                            brief_diff >= 500,
                            f"diff: {brief_diff} chars"))

        # Cumulative token impact across a 25-turn episode
        # Assuming ~4 chars per token
        tokens_per_wake_5 = len_r5 / 4.0
        tokens_per_wake_12 = len_r12 / 4.0
        episode_overhead_12 = 25 * tokens_per_wake_12
        episode_overhead_5 = 25 * tokens_per_wake_5

        checks.append(check("Cumulative roster token overhead exceeds 5,000 tokens for 25 turns",
                            episode_overhead_12 >= 5000,
                            f"12-agent overhead: {episode_overhead_12:.0f} tokens"))
        checks.append(check("Roster token overhead is >2.4x higher per episode",
                            episode_overhead_12 / episode_overhead_5 >= 2.4))

    finally:
        pass
    return sum(checks), len(checks)


# ---------------------------------------------------------------------------
# Test 7: Role Collisions and Ambiguity Detection
# ---------------------------------------------------------------------------

def test_role_collision_and_ambiguity_detection() -> tuple[int, int]:
    """Detect domain/keyword collisions and ambiguous routing across 12 specialized roles."""
    print("\n== 7. Role Collisions & Routing Ambiguity in 12-Agent Roster ==")
    checks = []
    _, _, agents_12 = make_12_agent_team(prefix="agyteam-roles-")

    # Stopwords to filter out generic terms
    stopwords = {"and", "the", "for", "from", "with", "of", "in", "to", "a", "an", "is", "faces", "takes"}

    def extract_role_keywords(role_text: str) -> set[str]:
        words = set(re.findall(r"[a-z0-9]+", role_text.lower()))
        return {w for w in words if w not in stopwords and len(w) > 2}

    agent_keywords = {
        a["name"]: extract_role_keywords(a["name"] + " " + a.get("role", ""))
        for a in agents_12
    }

    # Find pairwise Jaccard similarities and shared token overlaps
    collisions = []
    names = [a["name"] for a in agents_12]
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a1, a2 = names[i], names[j]
            kw1, kw2 = agent_keywords[a1], agent_keywords[a2]
            intersection = kw1 & kw2
            union = kw1 | kw2
            jaccard = len(intersection) / len(union) if union else 0.0
            if jaccard >= 0.10 or len(intersection) >= 2:
                collisions.append((a1, a2, jaccard, sorted(intersection)))

    checks.append(check("Identified sibling role collisions in 12-agent team",
                        len(collisions) >= 3,
                        f"found {len(collisions)} collision pairs: {collisions}"))

    # Sibling collisions to verify
    collision_pairs = {(c[0], c[1]) for c in collisions} | {(c[1], c[0]) for c in collisions}

    backend_collision = ("backend_core", "backend_api") in collision_pairs
    checks.append(check("Detected backend_core <-> backend_api role collision",
                        backend_collision,
                        f"pairs: {collision_pairs}"))

    frontend_collision = ("frontend_ui", "frontend_state") in collision_pairs
    checks.append(check("Detected frontend_ui <-> frontend_state role collision",
                        frontend_collision,
                        f"pairs: {collision_pairs}"))

    qa_collision = ("qa_unit", "qa_e2e") in collision_pairs
    checks.append(check("Detected qa_unit <-> qa_e2e role collision",
                        qa_collision,
                        f"pairs: {collision_pairs}"))

    # Ambiguous routing simulation
    # An incoming dispatch request targeting shared domain responsibilities
    ambiguous_task = "Reviews and verifies test suites"
    task_tokens = extract_role_keywords(ambiguous_task)

    match_scores = {}
    for name, kws in agent_keywords.items():
        score = len(kws & task_tokens)
        if score > 0:
            match_scores[name] = score

    sorted_matches = sorted(match_scores.items(), key=lambda x: x[1], reverse=True)
    top_score = sorted_matches[0][1] if sorted_matches else 0
    top_matches = [m for m in sorted_matches if m[1] == top_score]

    checks.append(check("Ambiguous task produces tied match scores across multiple agents",
                        len(top_matches) >= 2,
                        f"tied top matches: {top_matches}"))

    return sum(checks), len(checks)


# ---------------------------------------------------------------------------
# Main Suite Execution
# ---------------------------------------------------------------------------

def run_scalability_suite() -> bool:
    print("=" * 70)
    print("12-AGENT AGYTEAM SCALABILITY EVALUATION SUITE")
    print("=" * 70)

    suites = [
        test_retro_12_participant_cutoff,
        test_retro_12_full_participation_transcript_overflow,
        test_daisy_chain_12_hop_consumption,
        test_fanout_cascade_and_hop_budget,
        test_hop_budget_exhaustion_on_12_agent_cycle,
        test_brief_token_scaling_12_agents,
        test_role_collision_and_ambiguity_detection,
    ]

    total_passed = 0
    total_checks = 0

    for suite in suites:
        p, c = suite()
        total_passed += p
        total_checks += c

    print("\n" + "=" * 70)
    print(f"SCALABILITY SUITE TOTALS: {total_passed}/{total_checks} CHECKS PASSED")
    print("=" * 70)

    return total_passed == total_checks


if __name__ == "__main__":
    ok = run_scalability_suite()
    sys.exit(0 if ok else 1)
