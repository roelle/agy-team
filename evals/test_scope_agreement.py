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


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
