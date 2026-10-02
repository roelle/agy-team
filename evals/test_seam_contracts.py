"""Run the seam contracts under pytest, where they were not being run at all.

The transport, memory and observer suites each define their main check as a
function called `contract`, run against the default implementation and an
independent SQLite one. pytest collects functions named `test_*`, so it never
ran `contract`: `pytest evals/` was green while the transport contract stood
at 30 of 34, after a tool result's wording changed and nothing said so.
These are the checks behind "the seams are swappable"; a suite that skips
them is not checking the claim.

Each case returns the suite's (passed, total) score, which evals/conftest.py
turns into a failure when it is short.
"""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

CASES = [(suite, impl) for suite in ("test_transport", "test_memory", "test_observer")
         for impl in ("file", "sqlite")]


@pytest.mark.parametrize("suite, impl", CASES, ids=[f"{s[5:]}-{i}" for s, i in CASES])
def test_contract(suite, impl):
    mod = importlib.import_module(suite)
    env_for = mod.file_env() if impl == "file" else mod.sqlite_env()
    label = "file (default)" if impl == "file" else "sqlite (independent fixture)"
    return mod.contract(label, env_for)


def test_every_contract_function_is_reached_by_pytest():
    """The guard: a suite that grows a `contract` must be listed above."""
    have = {p.stem for p in (ROOT / "evals").glob("test_*.py")
            if "\ndef contract(" in p.read_text()}
    assert have == {suite for suite, _ in CASES}, have


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
