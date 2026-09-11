"""File scope management: where each *class* of file belongs.

Three scopes, because they have different lifetimes and backup stories:

  durable  — survives every project: agent identity, memory, roster, bus.
             Lives under your home directory rather than ~/.gemini, because the
             OS and app config are replaceable but this is the part you'd be
             sad to lose — put it where your backups already point.
             Default: ~/agy-teams/<team>
             (override: AGYTEAM_DURABLE_DIR for an exact path, or
              AGYTEAM_TEAMS_ROOT + AGYTEAM_TEAM to compose one)
  project  — the repo / mapped drive the work happens in. Disposable per job;
             may be a network mount, may be someone else's git checkout.
             Default: the enclosing git repository root, falling back to $PWD
             when not in a repo    (override: AGYTEAM_PROJECT_DIR)
  shared   — team coordination artifacts and deliverables handed between agents.
             Default: <project>/.agy-team-shared  (override: AGYTEAM_SHARED_DIR)

Teams are namespaced: everything durable for a team lives under its own
directory, so separate teams share no roster, no bus, and no memory and cannot
observe each other. Switch teams with AGYTEAM_TEAM=<name> (default "default"),
or point AGYTEAM_DURABLE_DIR straight at e.g. ~/my-agents/my-agent-team-A.

Resolution order (first wins): explicit argument, environment variable,
scopes.json in the project dir, then the default.
"""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "scopes.json"
DEFAULT_TEAMS_ROOT = "~/agy-teams"
DEFAULT_TEAM = "default"


def _safe_team(name: str) -> str:
    """Team names become directory names, so keep them boring."""
    clean = re.sub(r"[^A-Za-z0-9._-]", "-", name.strip()).strip(".-")
    if not clean:
        raise SystemExit(f"AGYTEAM_TEAM={name!r} is not a usable team name")
    return clean


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
    team: str = DEFAULT_TEAM

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

    def norms_file(self) -> Path:
        """Team norms file — durable and shared across all agents on this team."""
        return self.team_dir() / "NORMS.md"

    def read_norms(self) -> str | None:
        """Read NORMS.md if present, else None. Absence is normal."""
        return read_norms(scopes=self)

    def seed_norms(self, text: str | None = None, overwrite: bool = False, force: bool = False) -> Path:
        """Seed NORMS.md if absent (or forced/overwritten). Returns the norms path."""
        return seed_norms(scopes=self, text=text, overwrite=overwrite, force=force)

    def describe(self) -> str:
        in_repo = (self.project / ".git").exists()
        return (f"team:    {self.team}\n"
                f"durable (identity/memory, survives projects): {self.durable}\n"
                f"project (the repo/drive you are working in): {self.project}"
                f"{' [git root]' if in_repo else ' [not a git repo]'}\n"
                f"shared  (team artifacts and deliverables):    {self.shared}")

    def as_dict(self) -> dict:
        return {"team": self.team, "durable": str(self.durable),
                "project": str(self.project), "shared": str(self.shared)}


def load(durable=None, project=None, shared=None, team=None) -> Scopes:
    explicit = project or os.environ.get("AGYTEAM_PROJECT_DIR")
    proj = _expand(explicit) if explicit else (git_root() or Path.cwd().resolve())

    cfg = {}
    cfg_file = proj / CONFIG_NAME
    if cfg_file.exists():
        try:
            cfg = json.loads(cfg_file.read_text())
        except json.JSONDecodeError:
            cfg = {}

    tm = _safe_team(team or os.environ.get("AGYTEAM_TEAM")
                    or cfg.get("team") or DEFAULT_TEAM)

    # An explicit durable path wins outright and is used verbatim — that's how
    # you get ~/my-agents/my-agent-team-A without adopting the layout below.
    exact = durable or os.environ.get("AGYTEAM_DURABLE_DIR") or cfg.get("durable")
    if exact:
        dur = _expand(exact)
    else:
        root = _expand(os.environ.get("AGYTEAM_TEAMS_ROOT")
                       or cfg.get("teams_root") or DEFAULT_TEAMS_ROOT)
        dur = root / tm

    shr = _expand(shared or os.environ.get("AGYTEAM_SHARED_DIR")
                  or cfg.get("shared") or proj / ".agy-team-shared")
    dur.mkdir(parents=True, exist_ok=True)
    return Scopes(durable=dur, project=proj, shared=shr, team=tm)


def write_config(project: Path, scopes: Scopes) -> Path:
    """Persist scope choices so every agent in this project agrees on them."""
    path = Path(project) / CONFIG_NAME
    path.write_text(json.dumps(
        {"team": scopes.team, "durable": str(scopes.durable),
         "shared": str(scopes.shared)}, indent=2))
    return path


def norms_file(team_dir: Path | str | None = None, scopes: Scopes | None = None) -> Path:
    """Path to NORMS.md for a given team_dir, scopes, or current environment."""
    if team_dir is not None:
        return Path(team_dir) / "NORMS.md"
    if scopes is not None:
        return scopes.norms_file()
    env = os.environ.get("AGYTEAM_TEAM_DIR")
    if env:
        return Path(env) / "NORMS.md"
    return load().norms_file()


def read_norms(team_dir: Path | str | None = None, scopes: Scopes | None = None) -> str | None:
    """Read NORMS.md if present, else None. Absence is normal."""
    p = norms_file(team_dir=team_dir, scopes=scopes)
    if p.is_file():
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return None
    return None


def seed_norms(
    team_dir: Path | str | None = None,
    scopes: Scopes | None = None,
    text: str | None = None,
    overwrite: bool = False,
    force: bool = False,
) -> Path:
    """Seed NORMS.md if absent (or forced/overwritten). Returns the norms path."""
    p = norms_file(team_dir=team_dir, scopes=scopes)
    if not p.exists() or overwrite or force:
        if text is None:
            from .persona import SEEDED_NORMS
            text = SEEDED_NORMS
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return p


if __name__ == "__main__":   # python -m agyteam.scope → show resolved scopes
    print(load().describe())
