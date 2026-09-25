"""Make the offline suites genuinely offline, and genuinely checked.

## Offline

A handful of evals construct a real SdkRunner to drive hooks and adapters.
They never make a model call -- they build a local session, hand it a
synthetic ToolCall, and inspect what comes back -- but building one reaches
config.api_key, which exits if GEMINI_API_KEY is unset.

That turned a missing key into a hang rather than an error: api_key raises
SystemExit, SystemExit is a BaseException, raising it inside a coroutine on
the runner's background loop killed the loop, and the waiting future was
never resolved. A fresh clone with no key hung on `pytest evals/` with no
output at all.

The placeholder below is the honest fix, because these tests genuinely do not
need credentials. Anything that would really call a model belongs in
run_evals.py, which asks for a key properly. If you ever see this value in a
request, a test that claims to be offline is not.

## Checked

The older suites are scripts: each test function prints PASS/FAIL lines via
rpc_util.check() and *returns* a (passed, total) score for its own __main__
to add up. pytest collects those same functions, discards the return value,
and reports them passed. So `pytest evals/` said green while ten of
test_review.py's twenty-five checks were failing -- every proof-gate case had
been rejected for naming an author who was not a teammate, and so tested
nothing about proof files at all. A check whose failure cannot be seen is the
vacuous check this project keeps warning about, pointed at its own suite.

The hook below reads that score. A test that returns (passed, total) with
passed < total fails, with the count; the FAIL lines it printed are in the
captured output.
"""
import functools
import inspect
import os

os.environ.setdefault("GEMINI_API_KEY", "offline-tests-never-call-a-model")


def _score(result):
    return (isinstance(result, tuple) and len(result) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in result))


def pytest_collection_modifyitems(items):
    for item in items:
        fn = getattr(item, "obj", None)
        if not callable(fn) or inspect.iscoroutinefunction(fn):
            continue

        @functools.wraps(fn)
        def checked(*args, __fn=fn, **kwargs):
            result = __fn(*args, **kwargs)
            if _score(result):
                passed, total = result
                assert passed == total, (
                    f"{total - passed} of {total} checks failed; the FAIL "
                    f"lines are in the captured stdout above")
                return None
            return result

        item.obj = checked
