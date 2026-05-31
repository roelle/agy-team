"""
config.py — Team configuration loader.

Loads team_config.json (generic/public) and optionally merges
team_config.local.json (private, gitignored) on top.

Use team_config.local.json for deployment-specific overrides:
domain names, role descriptions, specialist knowledge paths, etc.
"""

import json
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).parent


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        print(f"  [config] Warning: could not parse {path}: {e}")
        return {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_team_config() -> dict:
    """
    Load team config, merging local overrides on top of the base config.

    Resolution order (later wins):
      1. team_config.json       — public/generic base
      2. team_config.local.json — private deployment overrides (gitignored)
    """
    base = _load_json(PROJECT_DIR / "team_config.json")
    local = _load_json(PROJECT_DIR / "team_config.local.json")
    return _deep_merge(base, local)


# Module-level singleton — loaded once on import
TEAM_CONFIG: dict[str, Any] = load_team_config()

ROLES: dict[str, dict] = TEAM_CONFIG.get("roles", {})
DOMAINS: dict[str, dict] = TEAM_CONFIG.get("domains", {})
TEAM_NAME: str = TEAM_CONFIG.get("team_name", "Apex")
DEFAULTS: dict = TEAM_CONFIG.get("defaults", {})


def get_skill_paths(role: str, domain: str | None = None) -> list[str]:
    """
    Return absolute skill paths for a role + optional domain.

    Args:
        role: Role name (must be in ROLES).
        domain: Optional domain key (must be in DOMAINS).

    Returns:
        List of absolute path strings for skills_paths in LocalAgentConfig.
    """
    paths = []

    role_cfg = ROLES.get(role)
    if role_cfg:
        paths.append(str(PROJECT_DIR / role_cfg["skill_path"]))

    if domain:
        domain_cfg = DOMAINS.get(domain)
        if domain_cfg:
            domain_path = str(PROJECT_DIR / domain_cfg["skill_path"])
            if domain_path not in paths:
                paths.append(domain_path)

    return paths
