# Task: audit the session helpers

Sibling of `audit_telemetry`. Same four defect classes, different code. Give it
verbatim; do not mention that it is a sibling.

---

`bench/fixtures2/` contains three files from a session layer: a bounded cache,
some quota arithmetic, and the cache's tests.

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

This exists to be run *second*, against a team that has already done
`audit_telemetry` and cycled its learnings into memory. The convergence question
is whether the second score beats the first on a team that started empty. Using
the same task twice measures memorisation, not convergence.
