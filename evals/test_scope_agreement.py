"""The module function and the Scopes method must name the same directory.

They did not. Scopes.team_dir() derived from `durable` and ignored
AGYTEAM_TEAM_DIR; the module-level helpers read AGYTEAM_TEAM_DIR first. Set
only the env var and an object-holding caller wrote the bus to one team while
a function-calling caller read it from another -- split-brain with no error,
which is the worst shape a bug can take. The supervisor hid it by setting both
env vars at startup, so it reproduced everywhere EXCEPT the place most likely
to be tested.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agyteam import scope  # noqa: E402


def _isolate(monkey: dict):
    for k in ("AGYTEAM_TEAM_DIR", "AGYTEAM_DURABLE_DIR", "AGYTEAM_TEAMS_ROOT",
              "AGYTEAM_TEAM"):
        os.environ.pop(k, None)
    os.environ.update(monkey)


def test_env_team_dir_is_honoured_by_both():
    """The case that was broken: only AGYTEAM_TEAM_DIR set."""
    saved = dict(os.environ)
    try:
        td = Path(tempfile.mkdtemp()) / "somewhere" / "team"
        _isolate({"AGYTEAM_TEAM_DIR": str(td)})
        assert scope.load().team_dir() == td
        assert scope.team_dir() == td
        assert scope.norms_file() == td / "NORMS.md"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_durable_only_still_derives_team_subdir():
    saved = dict(os.environ)
    try:
        dur = Path(tempfile.mkdtemp()) / "mine"
        _isolate({"AGYTEAM_DURABLE_DIR": str(dur)})
        assert scope.load().team_dir() == dur / "team"
        assert scope.team_dir() == dur / "team"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_team_dir_wins_over_durable_when_both_set():
    """Precedence is stated, not accidental: the more specific path wins."""
    saved = dict(os.environ)
    try:
        root = Path(tempfile.mkdtemp())
        _isolate({"AGYTEAM_DURABLE_DIR": str(root / "durable"),
                  "AGYTEAM_TEAM_DIR": str(root / "explicit" / "team")})
        expected = root / "explicit" / "team"
        assert scope.load().team_dir() == expected
        assert scope.team_dir() == expected
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_explicit_argument_beats_environment():
    saved = dict(os.environ)
    try:
        root = Path(tempfile.mkdtemp())
        _isolate({"AGYTEAM_TEAM_DIR": str(root / "env" / "team")})
        explicit = root / "arg" / "team"
        assert scope.team_dir(explicit) == explicit
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_two_teams_never_share_an_agent_memory_dir():
    """The silent one: agent_workspace derived only from `durable`.

    Two teams that each set only AGYTEAM_TEAM_DIR both landed on
    ~/agy-teams/default/agents/coder -- one memory directory, two teams,
    each reading and overwriting the other's learnings, and nothing anywhere
    reported a problem.
    """
    saved = dict(os.environ)
    try:
        root = Path(tempfile.mkdtemp())
        got = {}
        for team in ("teamA", "teamB"):
            _isolate({"AGYTEAM_TEAM_DIR": str(root / team / "team")})
            s = scope.load()
            got[team] = (s.team_dir(), s.agent_workspace("coder"))
        assert got["teamA"][0] != got["teamB"][0]
        assert got["teamA"][1] != got["teamB"][1], (
            f"both teams share {got['teamA'][1]}")
        # And agents live beside their own team dir, not somewhere unrelated.
        for team in ("teamA", "teamB"):
            assert got[team][1] == root / team / "agents" / "coder"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_cli_entry_point_and_library_agree_on_agent_memory():
    """Same team_dir must mean the same memory dir by either entry point.

    The supervisor CLI used to ALSO write AGYTEAM_DURABLE_DIR, so identical
    arguments produced different agent-memory locations depending on whether
    you arrived via the CLI or the library.
    """
    saved = dict(os.environ)
    try:
        root = Path(tempfile.mkdtemp())
        td = root / "myteam" / "team"

        _isolate({"AGYTEAM_TEAM_DIR": str(td)})
        library = scope.load().agent_workspace("coder")

        # What the CLI used to do in addition to setting the team dir.
        _isolate({"AGYTEAM_TEAM_DIR": str(td),
                  "AGYTEAM_DURABLE_DIR": str(td.parent)})
        cli = scope.load().agent_workspace("coder")

        assert library == cli, f"library {library} != cli {cli}"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_unconventional_team_dir_name_does_not_collide():
    """A team dir not named 'team' is its own root, not its parent's."""
    saved = dict(os.environ)
    try:
        root = Path(tempfile.mkdtemp())
        got = []
        for name in ("alpha", "beta"):
            _isolate({"AGYTEAM_TEAM_DIR": str(root / name)})
            got.append(scope.load().agent_workspace("coder"))
        assert got[0] != got[1], f"both resolved to {got[0]}"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_explicit_durable_dir_is_used_verbatim():
    """load() promises it; durable_root() silently overrode it.

    The fix for the cross-team memory collision derived the durable root from
    the team directory unconditionally, so AGYTEAM_DURABLE_DIR stopped having
    any effect the moment AGYTEAM_TEAM_DIR was also set. Nothing warned,
    because nothing downstream can tell the difference. The file contradicted
    itself two functions apart: "an explicit durable path wins outright and is
    used verbatim" sat above the code that ignored it.
    """
    saved = dict(os.environ)
    try:
        root = Path(tempfile.mkdtemp())
        _isolate({"AGYTEAM_TEAM_DIR": str(root / "teamA" / "team"),
                  "AGYTEAM_DURABLE_DIR": str(root / "CUSTOM")})
        s = scope.load()
        assert s.team_dir() == root / "teamA" / "team"
        assert s.agent_workspace("coder") == root / "CUSTOM" / "agents" / "coder"
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_derived_durable_still_follows_the_team_dir():
    """And the collision that change fixed stays fixed when durable is implicit."""
    saved = dict(os.environ)
    try:
        root = Path(tempfile.mkdtemp())
        got = []
        for name in ("teamA", "teamB"):
            _isolate({"AGYTEAM_TEAM_DIR": str(root / name / "team")})
            got.append(scope.load().agent_workspace("coder"))
        assert got == [root / "teamA" / "agents" / "coder",
                       root / "teamB" / "agents" / "coder"]
    finally:
        os.environ.clear()
        os.environ.update(saved)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
