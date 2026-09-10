"""Configuration for agyteam. Reads .env from the project root."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_MODEL = "gemini-3.8-flash"
WORKER_MODEL = "gemini-3.5-flash-lite"   # cheap model for ephemeral workers
COMPACTION_MODEL = "gemini-3.5-flash-lite"

# Context management. antigravity-preview-05-2026 has a 131072-token input
# limit, so we compact well below that regardless of which model is active.
# Env-overridable so tests can force compaction cheaply.
COMPACT_THRESHOLD_TOKENS = int(os.environ.get("AGYTEAM_COMPACT_THRESHOLD", 80_000))
KEEP_RECENT_TURNS = 6            # Content entries preserved verbatim on compact

MAX_TOOL_OUTPUT_CHARS = 20_000   # tool results truncated beyond this
# Session-lifetime model-call cap. The SDK's BudgetConfig counts "across the
# session", NOT per turn, and our sessions now persist across every wake — so a
# cap here is a budget for the agent's whole life. We shipped 40, which an agent
# exhausted after a couple of real turns and then returned empty output forever,
# indistinguishable from an agent that simply did nothing.
#
# Unset by default: the SDK imposes no limit of its own, and the supervisor's
# hop budget already bounds a runaway. Set this only if you want a hard ceiling,
# and remember it is per session, not per turn.
MAX_LOOP_STEPS = int(os.environ["AGYTEAM_MAX_LOOP_STEPS"]) \
    if os.environ.get("AGYTEAM_MAX_LOOP_STEPS") else None

# Where conversations are stored. The agy CLI, the Antigravity IDE and the SDK
# are three clients of the same store, so this single choice decides which
# surface an SDK-run agent shows up in — and whether it can be joined at all.
# The SDK's own default is tempfile.mkdtemp("antigravity_"), i.e. a throwaway
# directory nobody can find again, which is why this is set explicitly.
CONVERSATION_DIRS = {
    "cli": Path.home() / ".gemini" / "antigravity-cli" / "conversations",
    "ide": Path.home() / ".gemini" / "antigravity" / "conversations",
}


def conversation_dir() -> Path:
    """Conversation store: AGYTEAM_CONVERSATION_DIR, or a 'cli'/'ide' alias."""
    raw = os.environ.get("AGYTEAM_CONVERSATION_DIR", "cli")
    path = CONVERSATION_DIRS.get(raw, Path(raw).expanduser())
    path.mkdir(parents=True, exist_ok=True)
    return path

# USD per 1M tokens: (input, output). Estimates for cost display only —
# update from https://ai.google.dev/pricing when it matters.
PRICING = {
    "gemini-3.8-flash": (0.30, 2.50),
    "gemini-3.5-flash-lite": (0.10, 0.40),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "antigravity-preview-05-2026": (2.00, 12.00),
}
DEFAULT_PRICE = (2.00, 12.00)


def _load_env() -> None:
    env = PROJECT_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


def api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise SystemExit("GEMINI_API_KEY not set (put it in .env at project root)")
    return key
