"""File scope management: where each *class* of file belongs.

Three scopes, because they have different lifetimes and backup/sync stories:

  durable  — survives every project. Agent identity and memory live here, so an
             agent that moves between repos keeps what it learned.
             Default: ~/.gemini/clawagy   (override: CLAWAGY_DURABLE_DIR)
  project  — the repo / mapped drive the work happens in. Disposable per job;
             may be a network mount, may be someone else's git checkout.
             Default: the enclosing git repository root, falling back to $PWD
             when not in a repo    (override: CLAWAGY_PROJECT_DIR)
  shared   — team coordination artifacts and deliverables handed between agents.
             Default: <project>/.clawagy-team  (override: CLAWAGY_SHARED_DIR)

Resolution order (first wins): explicit argument, environment variable,
scopes.json in the project dir, then the default. Mirrors Antigravity's own
global (~/.gemini/config) vs workspace (.agents/) split, so a plugin install
lands in the same places the CLI already expects.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "scopes.json"


def _expand(p) -> Path:
    return Path(os.path.expandvars(str(p))).expanduser().resolve()


def git_root(start: Path | None = None) -> Path | None:
    """Nearest enclosing git repository root, or None.

    Walks up checking for `.git`, which is a directory in a normal clone and a
    file in a worktree or submodule — `.exists()` covers both. Deliberately no
    subprocess: this runs inside MCP servers where a missing/slow git binary
    would be a silent failure mode.
    """
    cur = (start or Path.cwd()).resolve()
    for d in (cur, *cur.parents):
        if (d / ".git").exists():
            return d
    return None


@dataclass
class Scopes:
    durable: Path
    project: Path
    shared: Path

    def agent_workspace(self, agent: str) -> Path:
        """Identity + memory for one agent — always durable, never in the repo."""
        ws = self.durable / "agents" / agent
        (ws / "memory").mkdir(parents=True, exist_ok=True)
        return ws

    def team_dir(self) -> Path:
        """Roster and message bus — durable, so history survives project churn."""
        d = self.durable / "team"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def shared_dir(self) -> Path:
        """Deliverables agents hand each other — lives with the work."""
        self.shared.mkdir(parents=True, exist_ok=True)
        return self.shared

    def describe(self) -> str:
        in_repo = (self.project / ".git").exists()
        return (f"durable (identity/memory, survives projects): {self.durable}\n"
                f"project (the repo/drive you are working in): {self.project}"
                f"{' [git root]' if in_repo else ' [not a git repo]'}\n"
                f"shared  (team artifacts and deliverables):    {self.shared}")

    def as_dict(self) -> dict:
        return {"durable": str(self.durable), "project": str(self.project),
                "shared": str(self.shared)}


def load(durable=None, project=None, shared=None) -> Scopes:
    explicit = project or os.environ.get("CLAWAGY_PROJECT_DIR")
    proj = _expand(explicit) if explicit else (git_root() or Path.cwd().resolve())

    cfg = {}
    cfg_file = proj / CONFIG_NAME
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text())
        except json.JSONDecodeError:
            cfg = {}

    dur = _expand(durable or os.environ.get("CLAWAGY_DURABLE_DIR")
                  or cfg.get("durable") or Path.home() / ".gemini" / "clawagy")
    shr = _expand(shared or os.environ.get("CLAWAGY_SHARED_DIR")
                  or cfg.get("shared") or proj / ".clawagy-team")
    dur.mkdir(parents=True, exist_ok=True)
    return Scopes(durable=dur, project=proj, shared=shr)


def write_config(project: Path, scopes: Scopes) -> Path:
    """Persist scope choices so every agent in this project agrees on them."""
    path = Path(project) / CONFIG_NAME
    path.write_text(json.dumps(
        {"durable": str(scopes.durable), "shared": str(scopes.shared)}, indent=2))
    return path


if __name__ == "__main__":   # python -m clawagy.scope → show resolved scopes
    print(load().describe())
