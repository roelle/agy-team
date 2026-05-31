"""
agent.py — Apex Team Framework entry point.

Usage:
    python agent.py                        # Default role (tpm)
    python agent.py --role test_engineer
    python agent.py --role data_analyst --domain domain_a
    python agent.py --resume               # Resume last session for this role
    python agent.py --list-roles           # Show configured roles

Environment:
    GEMINI_API_KEY — required (set in .env or environment)

Configuration:
    team_config.json        — public/generic team definition
    team_config.local.json  — private deployment overrides (gitignored)
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from google.antigravity import Agent, LocalAgentConfig, types
from google.antigravity.hooks import hooks, policy
from google.antigravity.utils.interactive import run_interactive_loop

from bootstrap import assemble_system_instructions, PROJECT_DIR
from config import ROLES, DOMAINS, TEAM_NAME, DEFAULTS, get_skill_paths
from memory_tools import MEMORY_TOOLS
from specialist_tools import SPECIALIST_TOOLS
from session_log import SESSION_LOG_HOOKS, set_session_role


# ── Active agent name (resolved from roster per session) ─────────────────────

ACTIVE_NAME = TEAM_NAME


def _resolve_name(role: str) -> str:
    """Get display name for this role from soul/roster.json, fall back to role."""
    roster_path = PROJECT_DIR / "soul" / "roster.json"
    if roster_path.exists():
        try:
            roster = json.loads(roster_path.read_text(encoding="utf-8"))
            if role in roster:
                return roster[role].get("name", role.upper())
        except Exception:
            pass
    return role.upper()


# ── Lifecycle hooks ──────────────────────────────────────────────────────────

@hooks.on_session_start
async def on_start():
    print(f"  [{ACTIVE_NAME}] Session started — "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")


@hooks.on_session_end
async def on_end():
    print(f"\n  [{ACTIVE_NAME}] Session ended. Memory persisted.")


# ── Session ID persistence ───────────────────────────────────────────────────

def _session_id_path(role: str) -> Path:
    return PROJECT_DIR / "sessions" / f"{role}.session_id"


def _load_session_id(role: str) -> str | None:
    path = _session_id_path(role)
    return path.read_text().strip() or None if path.exists() else None


def _save_session_id(agent: Agent, role: str):
    conv_id = getattr(agent, "conversation_id", None)
    if conv_id:
        _session_id_path(role).write_text(conv_id)


# ── Main ─────────────────────────────────────────────────────────────────────

async def run(role: str, domain: str | None, resume: bool):
    global ACTIVE_NAME

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    ACTIVE_NAME = _resolve_name(role)
    set_session_role(role)
    skill_paths = get_skill_paths(role, domain)
    save_dir = str(PROJECT_DIR / "sessions")
    conversation_id = _load_session_id(role) if resume else None

    if conversation_id:
        print(f"  [{ACTIVE_NAME}] Resuming session {conversation_id[:16]}...")
    else:
        print(f"  [{ACTIVE_NAME}] Starting new session — role: {role}")

    system_instructions = assemble_system_instructions(role=role)

    config = LocalAgentConfig(
        system_instructions=system_instructions,
        api_key=api_key,
        tools=MEMORY_TOOLS + SPECIALIST_TOOLS,
        capabilities=types.CapabilitiesConfig(
            enable_subagents=DEFAULTS.get("enable_subagents", True),
        ),
        skills_paths=skill_paths,
        save_dir=save_dir,
        conversation_id=conversation_id,
        workspaces=[str(PROJECT_DIR)],
        hooks=[on_start, on_end] + SESSION_LOG_HOOKS,
        policies=[policy.allow_all()],
    )

    async with Agent(config) as agent:
        _save_session_id(agent, role)

        skill_names = [Path(p).parent.name for p in skill_paths]
        print(f"\n⚙  {ACTIVE_NAME} ({role}) — "
              f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        if domain:
            print(f"   Domain:    {domain}")
        print(f"   Skills:    {', '.join(skill_names)}")
        print(f"   Memory:    soul/memory.md + soul/reflexes.md")
        print(f"   Knowledge: knowledge/")
        print(f"   Ctrl+C to end session.\n")

        try:
            await run_interactive_loop(agent)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass


def main():
    parser = argparse.ArgumentParser(
        description=f"{TEAM_NAME} — multi-agent engineering team framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join([
            "Configured roles:",
            *[f"  {name:20s} {cfg['description']}"
              for name, cfg in ROLES.items()],
        ]),
    )
    parser.add_argument(
        "--role",
        default=DEFAULTS.get("role", "tpm"),
        choices=list(ROLES.keys()),
        help=f"Active role (default: {DEFAULTS.get('role', 'tpm')})",
    )
    parser.add_argument(
        "--domain",
        default=None,
        choices=list(DOMAINS.keys()) or None,
        help="Domain context — loads domain-specific skills alongside role",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume last saved conversation for this role",
    )
    parser.add_argument(
        "--list-roles",
        action="store_true",
        help="Print configured roles and exit",
    )
    args = parser.parse_args()

    if args.list_roles:
        print(f"\n{TEAM_NAME} — configured roles:\n")
        for name, cfg in ROLES.items():
            print(f"  {name:20s} {cfg['description']}")
        print()
        return

    try:
        asyncio.run(run(args.role, args.domain, args.resume))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
