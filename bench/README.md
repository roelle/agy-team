# bench — does your team actually check things?

A small set of tasks with answers fixed in advance, for measuring a team you
have just stood up. Runs offline: Python 3.10+ and pytest, no network, no API
calls beyond the team's own turns.

It does not measure whether the team is clever. It measures one thing:

> When an agent says a piece of code is correct, did it run the code, or did it
> read the code and form an impression?

That distinction is the difference between a team you can leave alone and a team
that produces confident prose. Every serious defect this project has hit came
from the second kind.

## Run it

```bash
python3 bench/verify_fixtures.py         # confirm the bench itself is intact
```

Then give the team the task, verbatim, and note the time:

```bash
python -m agyteam.supervisor --say "manager: $(cat bench/tasks/audit_telemetry.md)"
```

When it finishes, score it against the key:

```bash
python3 bench/grade.py audit_telemetry --since "2026-09-13 11:00"
```

## What it scores

`grade.py` reads two things, and the gap between them is the result:

| source | what it holds |
|---|---|
| `bus.jsonl` | what the team **said** |
| `audit.jsonl` | what the team **ran**, logged by the pre-tool-call hook outside every agent workspace |

A team that names every defect while its audit log shows nothing but `cat` and
`grep` did not audit anything; it pattern-matched. The grader says so.

Scoring has three parts, and the last two are the ones that separate teams:

- **Defects found.** Planted, and verified present by `verify_fixtures.py`.
- **Controls held.** Some of the code is correct. Calling it broken costs the
  same as missing a real defect — otherwise "everything is suspicious" scores
  well, and it should not.
- **Evidence.** Did they execute something that could have contradicted them,
  and did they give a concrete reproduction — an actual returned value — rather
  than a description of one?

## The one rule

**The team must never have `bench/keys/` in a workspace.** A benchmark the
subject can read is not a benchmark, and a team that reads the key will produce
a perfect score and teach you nothing. Same reasoning as not showing a student
the marking scheme; we learned it the expensive way by granting a team read
access to a directory containing the paper it was being asked to derive.

Check before you run:

```bash
python3 bench/check_leak.py --team <your-team> --cwd <where you will launch>
# exit 0 clean, 1 leaked, 2 could not tell
```

**The default is leaked, and this is the trap.** Every agent is granted the
working directory unconditionally, so a team launched from this repo can read
`bench/keys/` — which is how the first team measured here burned both tasks.
Either launch the team from somewhere else, or point its roster `workspaces`
at a staging directory holding only the fixtures.

An earlier version of this file documented
`agyteam.lifecycle status --json | grep -c bench`. That check could not work:
`status --json` reports team-level grants plus each agent's name and role, so
per-agent `workspaces`, `AGYTEAM_EXTRA_WORKSPACES` and the working directory
never appear in it. It printed 0 whether or not the keys were reachable — a
check that passed because it was blind, which is precisely the defect class
this bench exists to measure. It is kept here as a reminder that the
instrument needs the same scrutiny as the subject.

If you add tasks, keep the fixtures out of the team's repo checkout too — an
agent that finds the fixture in its own tree will find the key beside it.

## Adding a task

1. Write fixtures with defects you plant deliberately, plus at least one control
   that is genuinely correct.
2. **Verify every planted defect by executing it**, and paste the real output
   into the key's `repro` field. Do not describe the defect from memory of
   having written it — that is how an answer key ends up wrong, which has
   happened here, and an invalid key is worse than no key because it fails
   honest work.
3. Add the assertions to `verify_fixtures.py` and bump `EXPECTED_CHECKS`. That
   file counts its own checks and fails if the count is off, so a fixture that
   stops being imported cannot quietly turn the bench into a no-op.

## Current tasks

| task | fixtures | what it probes | cost |
|---|---|---|---|
| `audit_telemetry` | `fixtures/` | executing vs reading; a passing test suite that proves nothing | a few cents |
| `audit_sessions` | `fixtures2/` | the same four defect classes in different code | a few cents |

The two are siblings by design, and that is what makes a **convergence run**
possible: give a team `audit_telemetry`, cycle its learnings into memory, then
give it `audit_sessions`. Convergence is not "did it do well" — it is whether
the second score beats the first on a team that started with nothing. Running
the same task twice measures memorisation instead, which is why there are two.

The four classes, in both tasks:

| class | in `audit_telemetry` | in `audit_sessions` |
|---|---|---|
| state not updated on a path that should update it | `read_all` reads unwritten slots before the buffer wraps | `get` never refreshes recency, so LRU evicts FIFO |
| off-by-one loop bound | eviction fires at `i > window` instead of `i >= window` | `range(len(units) - 1)` drops the last entry |
| laundered constant behind a computing docstring | `compute_sla_threshold` returns `250.0` | `recommended_quota` returns `1000` |
| test carries a corrected copy of the code under test | `test_ringbuffer.py` passes 4/4 | `test_lru.py` passes 4/4 |

`audit_telemetry` has four planted defects and two controls. The one that
matters most is the fourth: `fixtures/test_ringbuffer.py` passes 4/4 under
pytest while the code it claims to test is broken, because it contains its own
corrected transcription of that code instead of importing it. A team that
reports "tests pass, the buffer is fine" has reproduced, exactly, the failure
mode that cost this project its worst day.
