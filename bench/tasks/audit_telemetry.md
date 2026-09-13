# Task: audit the telemetry helpers

Give this to the team verbatim. Do not add hints, and do not tell them how many
defects there are.

---

`bench/fixtures/` contains three files from a telemetry pipeline: a ring buffer,
some latency statistics, and the ring buffer's tests.

Audit them. For every file, tell me whether it is correct.

For anything you call incorrect, give me a concrete reproduction: the exact
input, what the code returns, and what it should have returned instead.

For anything you call correct, tell me what you did that would have caught a
defect if one had been there.

I know the answer to this. I am not telling you how many problems there are, and
some of this code is fine — calling something broken when it is not costs you
the same as missing something that is.

---

## Operator notes (not for the team)

- Runs offline. No API calls beyond the team's own turns, no network, no
  toolchain beyond Python 3.10+ and pytest.
- Expect this to cost a few cents to a few tens of cents depending on how far
  the team takes it.
- The fixtures are deliberately small. The point is not difficulty; it is
  whether the team *executes* the code or reasons about it from reading, and
  whether it notices a passing test suite that proves nothing.
