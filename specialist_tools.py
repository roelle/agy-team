"""
specialist_tools.py — Briefed sub-agent spawning.

spawn_specialist() is the Phase 2 core: every sub-agent gets a full
context bundle (soul + memory + role SKILL.md) before it touches a task.
No specialist ever starts cold.
"""

import os
from pathlib import Path

from google.antigravity import Agent, LocalAgentConfig, types
from google.antigravity.hooks import policy

from config import PROJECT_DIR, get_skill_paths
from memory_tools import MEMORY_TOOLS


async def spawn_specialist(
    role: str,
    task: str,
    domain: str = "",
    output_file: str = "",
) -> str:
    """Spawn a specialist agent with full memory context and a specific task.

    This is the correct way to delegate. The specialist receives:
    - Full soul (identity, principles)
    - Current memory summary and reflexes
    - Role-specific SKILL.md loaded via skills_paths
    - Domain context if specified
    - Explicit task and output instructions

    Do NOT rely on the built-in sub-agent mechanism for delegation —
    that skips the memory bootstrap. Always use this tool.

    Args:
        role: Specialist role to spawn. Must be a known role in team_config.json.
              Options: tpm, test_engineer, sim_engineer, data_analyst,
                       tech_writer, sys_eng_a, sys_eng_b, qa_reviewer
        task: Full task description. Be specific — the specialist won't ask
              clarifying questions unless you leave explicit gaps.
        domain: Optional domain context key (e.g., 'domain_a', 'domain_b').
                Loads the domain's SKILL.md alongside the role's SKILL.md.
        output_file: If set, instructs the specialist where to write results.
                     Relative to the project directory.

    Returns:
        The specialist's final response text.

    Example:
        result = await spawn_specialist(
            role="test_engineer",
            task="Run a baseline simulation for domain_a scenario X. "
                 "Document flag choices with rationale.",
            domain="domain_a",
            output_file="outputs/sim_domain_a_baseline_001.md",
        )
    """
    from bootstrap import assemble_system_instructions

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return "ERROR: GEMINI_API_KEY not set."

    valid_roles = list(__import__("config").ROLES.keys())
    if role not in valid_roles:
        return f"ERROR: Unknown role '{role}'. Valid roles: {valid_roles}"

    # Build full context for this specialist
    skill_paths = get_skill_paths(role, domain or None)
    system_instructions = assemble_system_instructions(role=role)

    # Inject output protocol into the task if output_file is specified
    full_task = task
    if output_file:
        full_task = (
            f"{task}\n\n"
            f"**Output protocol:** Write your results to `{output_file}`. "
            f"Include a summary at the top. Update any relevant knowledge files "
            f"(knowledge/sim_flags.md, knowledge/sql_tables.md, etc.) if you "
            f"learned something new."
        )
    else:
        full_task = (
            f"{task}\n\n"
            f"**Output protocol:** Write your results to an appropriate file in "
            f"`outputs/`. Name it descriptively. Update any relevant knowledge "
            f"files if you learned something new."
        )

    config = LocalAgentConfig(
        system_instructions=system_instructions,
        api_key=api_key,
        tools=MEMORY_TOOLS,
        capabilities=types.CapabilitiesConfig(
            enable_subagents=False,  # Specialists don't spawn further agents
        ),
        skills_paths=skill_paths,
        workspaces=[str(PROJECT_DIR)],
        policies=[policy.allow_all()],
    )

    async with Agent(config) as specialist:
        response = await specialist.chat(full_task)
        return await response.text()


async def qa_review(
    artifact_path: str,
    artifact_type: str = "engineering_report",
    audience: str = "",
) -> str:
    """Spawn a QA reviewer to review a completed artifact.

    The reviewer gets the artifact content and a rubric. It does NOT get
    the original task context — fresh eyes catch different errors.

    Args:
        artifact_path: Path to the artifact file (relative to project dir).
        artifact_type: Type for rubric selection. Options:
                       engineering_report, requirements_doc, code, analysis
        audience: Who the artifact is for (affects review focus).

    Returns:
        Review notes as markdown text.
    """
    artifact_full = PROJECT_DIR / artifact_path
    if not artifact_full.exists():
        return f"ERROR: Artifact not found at {artifact_full}"

    artifact_content = artifact_full.read_text(encoding="utf-8")

    task = (
        f"Review this {artifact_type}.\n"
        + (f"Audience: {audience}\n" if audience else "")
        + f"\n---\n{artifact_content}\n---\n\n"
        f"Apply the {artifact_type} rubric from your SKILL.md. "
        f"Be specific about issues — location + why it's a problem + suggested fix. "
        f"Output: verdict (Approve/Revise/Reject), summary, categorized issues."
    )

    return await spawn_specialist(
        role="qa_reviewer",
        task=task,
        output_file=f"outputs/review_{Path(artifact_path).stem}.md",
    )


# Export all specialist tools
SPECIALIST_TOOLS = [spawn_specialist, qa_review]
