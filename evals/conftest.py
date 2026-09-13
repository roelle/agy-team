"""Make the offline suites genuinely offline.

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
"""
import os

os.environ.setdefault("GEMINI_API_KEY", "offline-tests-never-call-a-model")
