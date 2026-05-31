"""
memory_tools.py — Custom memory tools for Karel.

These Python functions are registered as agent tools, giving Karel
structured ways to write to memory without raw file access hacks.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).parent


def append_to_log(content: str) -> str:
    """Append a note to today's daily log.

    Use this to record: decisions made, things learned, task status updates,
    important observations. Write important things here — they get distilled
    into memory.md during the daily summary.

    Args:
        content: The note to append. Markdown ok. Include context.

    Returns:
        Confirmation with the log file path.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = PROJECT_DIR / "memory" / f"{today}.md"

    timestamp = datetime.now(timezone.utc).strftime("%H:%M UTC")

    if not log_path.exists():
        log_path.write_text(
            f"# Daily Log — {today}\n\n## Summary\n_(not yet distilled)_\n\n## Raw Log\n\n",
            encoding="utf-8",
        )

    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"\n### [{timestamp}]\n{content}\n")

    return f"Appended to {log_path}"


def update_task(
    task_id: str,
    status: str,
    notes: str = "",
    next_step: str = "",
    output_file: str = "",
) -> str:
    """Update or create a task entry in running_tasks.json.

    Use this when: starting a new task, updating progress, marking complete,
    or recording a failure.

    Args:
        task_id: Unique identifier for the task (e.g., 'sim_domain_a_001').
        status: One of: pending, running, complete, failed, blocked.
        notes: Optional context to add to the task entry.
        next_step: Role to delegate to on completion (e.g., 'data_analyst').
        output_file: Path where task output was/will be written.

    Returns:
        Confirmation with current task state.
    """
    tasks_path = PROJECT_DIR / "knowledge" / "running_tasks.json"

    tasks = []
    if tasks_path.exists():
        try:
            tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            tasks = []

    # Find existing entry or create new one
    existing = next((t for t in tasks if t.get("task_id") == task_id), None)

    now = datetime.now(timezone.utc).isoformat()

    if existing:
        existing["status"] = status
        existing["updated_at"] = now
        if notes:
            existing["notes"] = notes
        if next_step:
            existing["next_step"] = next_step
        if output_file:
            existing["output_file"] = output_file
        entry = existing
    else:
        entry = {
            "task_id": task_id,
            "status": status,
            "created_at": now,
            "updated_at": now,
            "notes": notes,
            "next_step": next_step or None,
            "output_file": output_file or None,
        }
        tasks.append(entry)

    tasks_path.write_text(json.dumps(tasks, indent=2), encoding="utf-8")
    return f"Task '{task_id}' → {status}. Entry: {json.dumps(entry, indent=2)}"


def list_tasks(status_filter: str = "") -> str:
    """List tasks from running_tasks.json.

    Args:
        status_filter: Optional filter by status (e.g., 'running', 'pending').
                       Empty string returns all tasks.

    Returns:
        Formatted task list.
    """
    tasks_path = PROJECT_DIR / "knowledge" / "running_tasks.json"

    if not tasks_path.exists():
        return "No tasks file found."

    try:
        tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "Tasks file is malformed."

    if status_filter:
        tasks = [t for t in tasks if t.get("status") == status_filter]

    if not tasks:
        return f"No tasks{' with status=' + status_filter if status_filter else ''}."

    lines = []
    for t in tasks:
        lines.append(
            f"- [{t.get('status', '?')}] {t.get('task_id', '?')}"
            + (f" → next: {t['next_step']}" if t.get("next_step") else "")
            + (f" | {t.get('notes', '')[:60]}" if t.get("notes") else "")
        )
    return "\n".join(lines)


def read_knowledge(filename: str) -> str:
    """Read a knowledge file from the knowledge/ directory.

    Use this to load domain-specific accumulated knowledge before starting
    work in that area.

    Args:
        filename: Filename within knowledge/ (e.g., 'sim_flags.md', 'sql_tables.md').

    Returns:
        File contents, or an error message if not found.
    """
    knowledge_path = PROJECT_DIR / "knowledge" / filename
    if not knowledge_path.exists():
        return f"Knowledge file '{filename}' not found in {PROJECT_DIR / 'knowledge'}/"
    return knowledge_path.read_text(encoding="utf-8")


def update_knowledge(filename: str, section_title: str, content: str) -> str:
    """Append a new section to a knowledge file.

    Use this to record new learnings. For example, after figuring out a new
    simulation flag, call update_knowledge('sim_flags.md', 'Flag: --my-flag', ...).

    Args:
        filename: Knowledge file to update (e.g., 'sim_flags.md').
        section_title: The heading for the new section.
        content: The content to add under that heading.

    Returns:
        Confirmation.
    """
    knowledge_path = PROJECT_DIR / "knowledge" / filename
    if not knowledge_path.exists():
        knowledge_path.write_text(
            f"# {filename.replace('.md', '').replace('_', ' ').title()}\n\n",
            encoding="utf-8",
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with knowledge_path.open("a", encoding="utf-8") as f:
        f.write(f"\n## {section_title} — {timestamp}\n{content}\n")

    return f"Updated {filename} with section '{section_title}'"


# Export all tools as a list for easy registration
MEMORY_TOOLS = [
    append_to_log,
    update_task,
    list_tasks,
    read_knowledge,
    update_knowledge,
]
