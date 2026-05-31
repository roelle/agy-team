"""
bootstrap.py — Assemble system instructions from memory files.

Reads soul.md, reflexes.md, memory.md summary, and today's log summary
into a TemplatedSystemInstructions object for LocalAgentConfig.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from google.antigravity import types

PROJECT_DIR = Path(__file__).parent


def _read(path: Path, default: str = "") -> str:
    """Read a file, return default if missing."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return default


def _extract_memory_summary(memory_text: str) -> str:
    """Extract just the Summary block from memory.md to keep context lean."""
    # Look for ## Summary section
    match = re.search(r"## Summary\n(.*?)(?=\n## |\Z)", memory_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: return first 500 chars
    return memory_text[:500] + ("..." if len(memory_text) > 500 else "")


def _today_log_summary() -> str:
    """Get summary block from today's and yesterday's daily log, if exists."""
    today = datetime.now(timezone.utc)
    summaries = []

    for delta_days in [0, 1]:
        from datetime import timedelta
        day = today - timedelta(days=delta_days)
        log_path = PROJECT_DIR / "memory" / f"{day.strftime('%Y-%m-%d')}.md"
        if log_path.exists():
            content = log_path.read_text(encoding="utf-8")
            match = re.search(r"## Summary\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
            if match:
                label = "Today" if delta_days == 0 else "Yesterday"
                summaries.append(f"**{label} ({day.strftime('%Y-%m-%d')}):**\n{match.group(1).strip()}")

    return "\n\n".join(summaries) if summaries else ""


def assemble_system_instructions(role: str = "tpm") -> types.TemplatedSystemInstructions:
    """
    Assemble TemplatedSystemInstructions from soul, reflexes, and memory files.
    
    Args:
        role: The active role for this session (affects section labeling).
    
    Returns:
        TemplatedSystemInstructions ready for LocalAgentConfig.
    """
    import json

    soul = _read(PROJECT_DIR / "soul" / "soul.md")
    reflexes = _read(PROJECT_DIR / "soul" / "reflexes.md")
    memory_full = _read(PROJECT_DIR / "soul" / "memory.md")
    memory_summary = _extract_memory_summary(memory_full)
    today_log = _today_log_summary()

    # Load role-specific identity properties from roster
    roster_path = PROJECT_DIR / "soul" / "roster.json"
    name = f"Agent_{role}"
    origin = "Standard specialist agent."
    mission = "Executing specialized role-based goals."
    
    if roster_path.exists():
        try:
            roster = json.loads(roster_path.read_text(encoding="utf-8"))
            if role in roster:
                name = roster[role].get("name", name)
                origin = roster[role].get("origin", origin)
                mission = roster[role].get("mission", mission)
        except Exception:
            pass

    # Safe replace to avoid issues with unmatched curly braces in other parts of soul.md
    soul = (
        soul.replace("{NAME}", name)
        .replace("{ORIGIN}", origin)
        .replace("{MISSION}", mission)
    )

    sections = [
        types.SystemInstructionSection(
            title="Reflexes",
            content=reflexes,
        ),
        types.SystemInstructionSection(
            title="Memory Summary",
            content=memory_summary or "No memory summary yet — this may be a fresh start.",
        ),
    ]

    if today_log:
        sections.append(types.SystemInstructionSection(
            title="Recent Activity",
            content=today_log,
        ))

    sections.append(types.SystemInstructionSection(
        title="Session Context",
        content=(
            f"Active role: **{role}**\n"
            f"Session started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"Project directory: {PROJECT_DIR}\n"
            f"Knowledge files: {PROJECT_DIR}/knowledge/\n"
            f"Daily log: {PROJECT_DIR}/memory/{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.md"
        ),
    ))

    return types.TemplatedSystemInstructions(
        identity=soul,
        sections=sections,
    )
