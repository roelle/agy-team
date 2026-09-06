"""Configuration for clawagy. Reads .env from the project root."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_MODEL = "gemini-3.8-flash"
WORKER_MODEL = "gemini-3.5-flash-lite"   # cheap model for ephemeral workers
COMPACTION_MODEL = "gemini-3.5-flash-lite"

# Context management. antigravity-preview-05-2026 has a 131072-token input
# limit, so we compact well below that regardless of which model is active.
COMPACT_THRESHOLD_TOKENS = 80_000
KEEP_RECENT_TURNS = 6            # Content entries preserved verbatim on compact

MAX_TOOL_OUTPUT_CHARS = 20_000   # tool results truncated beyond this
MAX_LOOP_STEPS = 40              # tool-use steps per user turn before forced stop

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
