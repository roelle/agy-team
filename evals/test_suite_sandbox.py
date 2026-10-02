"""The offline suite must not be able to reach a real team.

`pytest evals/` once retired a live team's conversations: a test built a
runner with no arguments, the runner resolved its team from AGYTEAM_TEAM, and
recycling `tpm` on it wrote to that team's conversations.json. conftest.py
now points the ambient team at a throwaway directory before every test.
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import scope  # noqa: E402
from agyteam.runner import Runner  # noqa: E402


def sandbox():
    import conftest
    return Path(conftest.SANDBOX).resolve()


def test_the_ambient_team_is_a_throwaway_directory():
    assert sandbox() in scope.load().team_dir().resolve().parents


def test_a_runner_built_with_no_arguments_writes_inside_it():
    class Bare(Runner):
        def wake(self, agent, message):
            return ""

    runner = Bare()
    runner.remember_conversation("tpm", "conv-1")
    runner.reset("tpm")
    assert sandbox() in runner.retired_path.resolve().parents


def test_an_exported_team_does_not_survive_into_a_test():
    """The fixture runs before each test, so a module that sets AGYTEAM_TEAM
    at import -- as the delivery eval did -- cannot redirect the rest."""
    assert "AGYTEAM_TEAM" not in os.environ and "AGYTEAM_TEAM_DIR" not in os.environ


def test_collecting_the_live_evals_leaves_the_environment_alone():
    before = dict(os.environ)
    import importlib
    importlib.reload(importlib.import_module("test_delivery"))
    assert {k: v for k, v in os.environ.items() if k.startswith("AGYTEAM")} == \
           {k: v for k, v in before.items() if k.startswith("AGYTEAM")}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
