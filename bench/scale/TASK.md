# Task: audit a package (scale suite)

Give this to the team verbatim; `{dir}` and `{package}` are filled in by
bench/convergence.py. Do not add hints or say how many defects there are.

---

`{dir}` holds a Python package, `{package}/`, with its tests in `tests/` and
a year of data in `data/`. It is used in production and people rely on its
numbers.

Audit it. For every module, tell me whether it is correct.

For anything you call incorrect, give me a concrete reproduction: the exact
call, what the code returns, and what it should have returned instead. For
anything you call correct, tell me what you did that would have caught a
defect if one had been there. The tests pass; that is not evidence either way.

Write every defect you report into `{dir}/FINDINGS.json` as well: a JSON list
with one object per defect,

    {"file": "<path relative to {dir}>", "function": "<function or method name>",
     "call": "<Python statements, run from {dir}, whose LAST line is an
              expression that evaluates to the wrong result>",
     "returned": "<what that last expression evaluates to, as its Python repr>",
     "expected": "<what it should evaluate to>"}

For a defect in a test file instead give {"file": ..., "kind": "test",
"why": "<what is wrong with the test>"}.

I know the answer to this. I am not telling you how many problems there are,
and some of this code is fine: calling something broken when it is not costs
you the same as missing something that is. A reproduction whose "returned"
is not what the code really returns counts against you.

---

## Operator notes (not for the team)

- Graded by `bench/scale/grade_scale.py`, which runs every `call` against
  the audited tree and the reference it was built from. Prose is not scored.
- The data files are large on purpose. Reading one whole costs a lot of
  context; sampling it and computing does not.
