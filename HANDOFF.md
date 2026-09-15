# Handoff

Assume the person who built this is gone and you have this repository, a
machine behind a firewall, and an API key. This file is what they would have
told you across a table. The README tells you how to run things; this tells
you which decisions are load-bearing, so you know what you can change freely
and what will quietly break the system's reason for existing if you "clean it
up".

## Read in this order

1. `README.md` — Quick Start through "The four seams". Stop there on day one.
2. This file.
3. `python -m agyteam.doctor` — before the first run on any host that is not
   the SDK. It is offline and takes seconds.
4. `agyteam/persona.py` — the briefs are the product as much as the code is.
5. `bench/README.md` — then run the bench against your own freshly started
   team before you trust it with anything.

## The one idea everything else serves

The dominant failure mode of agent teams — measured here over and over, in
our agents and in the human operating them — is **substitution**: the thing
under test gets replaced by a representation of it. A test that contains a
copy of the code under test. A review that reads the diff and imagines
running it. A benchmark that could only pass. A constant pasted where a
computation belonged, with a docstring describing the computation.

Substitution is not lying. Every instance we hit was produced in good faith
by an agent (or a person) doing the task as they understood it. The reward an
agent actually experiences is "produce output that would be accepted", and
under that reward, a convincing representation and a verified fact score the
same — unless something structural makes them score differently.

Everything unusual about this codebase is one of those structural somethings.
When you find a design choice that seems overbuilt, check it against this
list before simplifying it. Each one is a scar, not an ornament.

## Load-bearing decisions

**Roles are enforced by capability, not instruction.** The tpm cannot run a
shell command; qa cannot edit source; the schema for a disabled tool never
reaches the model (`tools_off` in `roster.json`). Prose that says "please
don't" decays under deadline pressure; a tool that does not exist in the
schema cannot be reached for. If you add a role, decide what it must *not*
be able to do and remove the capability — do not write the restriction into
the persona and consider it done.

**The manager is briefed as a principal, not a teammate.** `persona.py`
carries two briefs. The gatekeeper who reads the same teammate brief as the
people she gates gets socialised into the group she is meant to hold to
account — we watched it happen. If you add a second gatekeeping role, set
the flag (`--principal` / `--gatekeeper` on `agyteam.lifecycle add`); do not
rely on the name-sniffing fallback, which exists only for old rosters.

**The accountable role leads the retrospective, and the report says why that
is a compromise.** The default was the literal string `"tpm"`, and the
roster's `is_retro_leader` flag was consulted only when that default named
nobody — the one case it was not needed for. So the coordinator led every
retro on every team that had one, including the seeded one. Two separate
teams proposed the change unasked, with the reasoning the code already
carried: the norms a retro produces bind the team, and a level-1 manager
facilitating a retro on work she delegated is grading her own instructions.
The cost is real and stays visible — `TENSION_ACCOUNTABLE` puts "a retro led
by the person accountable for the outcome is a weaker retro" in the report
itself. Naming a tension beats choosing the facilitator who does not have to
own the result. `resolve_retro_leader` takes the explicit flag, then the
roster, then the accountable role.

**Oversight runs on a different model family from implementation.** Not
because one is better — because three reviewers with different blind spots
caught defects none found alone. Diversity is the mechanism, not capability.
Keep qa and the manager on a different family from coder even when a single
family would be cheaper or simpler to configure.

**Every test double must be as limited as the weakest runner you allow.**
The suite had fourteen runner doubles and every one returned prose from
`wake()`, while the Runner contract says a runner need only report that a
turn ended. So no test could detect a dependency on the optional half of the
contract, and one grew: the retrospective parsed reply text, produced
"(missing or invalid)" on a conforming runner, and never wrote NORMS.md —
team-level learning silently did not happen while agent-level learning
worked. The doubles were more capable than the thing they stood in for,
which is the substitution pattern aimed at our own tests.
`evals/test_runner_columns.py` runs the core flows over two columns, rich and
minimal. **A test that passes in `rich` and fails in `minimal` is an
undeclared capability requirement.** Keep the columns genuinely different; the
moment someone gives the minimal runner a reply string to make a test pass,
the matrix stops being one.

**Reviews must be able to fail, and "my check did not run" must not count as
a verdict.** The proof gate runs the reviewer's proof file and rejects both
the file that collects no tests and the file that errors on import
(`evals/test_qa_proof_gate_evasion.py` attacks it from six directions). The
general rule: any check that can pass vacuously eventually will, and a check
that passes vacuously is worse than no check because you believe you have
one. `bench/verify_fixtures.py` counts its own assertions for the same
reason.

The rule has a second edge that cost us more: **a check that could not run
must never be reported as a check that found something.** The proof gate
shells out to pytest, the plugin install is deliberately dependency-free, and
where those met the gate returned `proof_file did not pass (exit code 1)` for
a proof file containing one passing test — the real reason, "No module named
pytest", went to a stderr nobody reads. An agent seeing that rewrites a proof
that was already correct, and a team that can never record a review looks
exactly like a team that never reviews. It now resolves an interpreter that
can actually import pytest, and says plainly that the gate did not run when
none can be found, recording nothing. `AGYTEAM_PROOF_PYTHON` is the override.

**What the team ran is recorded where the team cannot reach it —
on a runner that supports it.** The pre-tool-call hook appends every command
to an audit log outside all agent workspaces (`AGYTEAM_AUDIT_LOG`). Grading,
succession scoring, and any "did they actually check?" question reads that log
against the bus. Never grade a team on its own account of what it did.

The hook is installed by the SDK session builder, so this holds on
`SdkRunner` and **not** on runners that drive an external host. Check before
relying on it — `Runner.supports_audit`, surfaced by
`agyteam.lifecycle status`. When it is False, a tool that cannot see evidence
must report that it could not look, never that the team produced nothing: a
silent zero and a real zero must not look alike. `bench/grade.py` refuses to
score its evidence section rather than scoring it zero, for exactly this
reason.

**Agents never see answer keys, and workspace grants are the leak vector —
where grants exist at all.** `bench/keys/` must never be in a team's
workspace, and note that a team whose workspace includes this repo root can
read the keys, so the shipped bench tasks are burned for that team (see
`bench/README.md`). We learned this by granting a team a directory that
contained the paper it was being asked to derive. Grant the narrowest
directory that works, per agent, and audit grants when a task involves
anything the team is being measured on.

**The team directory is the record, and no agent is ever granted write
access to it.** `bus.jsonl`, `reviews.jsonl`, `retro_inbox.jsonl`, `NORMS.md`,
`roster.json` and `conversations.json` are written by agyteam processes and
read back as the account of what the team did. Agents reach them through the
MCP servers, which are *separate processes* with their own filesystem access —
so no agent needs a grant there to do anything it is supposed to do, and a
grant that reaches it costs every gate built on those files at once. An agent
that can append a line to `reviews.jsonl` has skipped the proof gate and the
self-review check together; one that can append to `retro_inbox.jsonl` can put
words in a teammate's retrospective; one that can edit `roster.json` can grant
itself the rest. This closes retro-inbox forgery by construction rather than by
validation, which is the cheaper kind of closed. `lifecycle.grant_workspace`
refuses a path containing the team directory, and `agyteam.doctor` fails on one
already in the roster. Keep the team directory out of every working tree — if
they are co-located, move it with `AGYTEAM_DURABLE_DIR`, because a record
inside the workspace is a record its subject can edit.

**Read this part before you trust a grant.** Workspace confinement is
enforced by `SdkRunner`. Runners that drive an external host have no
workspace mechanism at all — no grants, no confinement — and agents are
bounded only by whatever permission model that host applies, which agyteam
neither configures nor can observe. `Runner.supports_containment` declares
this and `agyteam.lifecycle status` prints it beside the grant list,
because a list of granted directories otherwise reads as a boundary that is
not there.

**And the same is true of `tools_off`.** Roster capability scoping is the
mechanism behind "roles are enforced by capability, not instruction", and it
is implemented in one place: the SDK runner passes it to the session builder
so the schema for a withheld tool never reaches the model. Everywhere else
the roster entry is a description. We found this the way you would expect —
a manager whose roster said `tools_off: [create_file, edit_file]` and *"Does
not write the work"* ran `sed -i` on the fixture it was auditing.
`Runner.supports_capability_scoping` declares it, and on a runner that does
not enforce it the introspection server now tells the agent the restriction
is its team's expectation rather than a boundary, and asks it to hold to the
expectation anyway. That is the best available answer, not a good one: if a
role must be *unable* to do something, remove the capability where the
process lives.

Containment belongs to whoever owns the process, and agyteam should say so
rather than imply a guarantee it is not making. A denylist of dangerous
commands is not containment either: structured write tools can be gated
because their target is an explicit argument, but a shell cannot, since
deciding what an arbitrary command writes requires running it. If you need
an agent to be *unable* to read something on such a runner, enforce it where
the process actually lives — filesystem permissions, a container, a separate
account — and treat anything agyteam says about grants as advisory.

**Memory is files, and the write path is the read path.** Both prior
attempts at a learning agent failed on the same broken link: learnings were
written somewhere that was never read back. Here, `save_memory` updates the
index *and returns the refreshed index in the tool result*, and distillation
on quit/cycle pushes unsaved learnings into the same files that boot loads.
If you swap the memory store (the seam exists), preserve that property; the
contract suite checks it.

**A lesson only counts when it names an action a gate could check.** "Grep
the tests for mocks and fail if found" changed an agent's behaviour the next
day. "Inspect ground truth before writing" did not, ever. When a retro
produces a learning, convert it into either a removed capability or a check
that can fail, and treat the prose version as not yet done. Behaviour is
measurable; learning is academic.

**Talk to the team; don't forensically read its files.** The manager exists
to answer "what is the team doing?" truthfully from the record. Asking her
exercises the muscle the whole succession plan depends on; grepping the bus
yourself atrophies it. Grep is for auditing after the fact, not for
operating.

## Before a run, on a host that is not the SDK

Run `python -m agyteam.doctor`. It exercises the join between agyteam and
whatever is driving the agents — the seam this repo's tests cannot reach —
and prints what is actually wired: the resolved runner and its three
capability flags, every bus tool called end to end with real arguments, a
probe message written through the bus and then looked for in the directory
the supervisor is about to read, whether any workspace grant reaches the team
directory, whether the installed plugin is this code, and the first lines of a
generated brief.

Two host behaviours it exists to catch, both of which cost a day:

- **The agent's own memory system competes with `agents/<name>/memory/`.**
  A host with ambient rules about where notes go will send the agent there,
  and the brief does not say otherwise. Ours was stopped only by an external
  clamp, after which the agent recovered on its own. State the agent's memory
  root in its brief and expect to enforce it outside agyteam.

- **A live MCP bus server never rebinds, and the failure is silent.** Where
  the bus is mounted as an MCP server, the team directory reaches it as an
  environment variable, and that environment is read once — when the host
  spawns the process. Rewriting the mount config to point at a different team
  does not rebind a server that is already running, and nothing anywhere
  reports the mismatch. We watched five servers from a previous team stay
  alive while agents delegated normally and every tool call returned success;
  the mail went into the old team's bus, the new supervisor saw an empty bus,
  declared the team idle after one turn and exited 0. Eight runs finished in
  sixteen minutes and produced a full set of meaningless scores. It reads as
  an agent failure — `[done after 1 turns — team went idle]`, reason
  `NO_TOOL_CALL` — which is what makes it expensive.

  **The rule: after changing which team the mounts point at, kill the bus
  server processes.** Verified — killing all five brought each back bound to
  the new team; no host restart needed. `agyteam.doctor` reads
  `/proc/<pid>/environ` for every running bus server and fails if any is bound
  elsewhere, and `bench/convergence.py` now refuses to grade an episode with
  one turn and an empty bus.

**The installed plugin is a second copy of this code, and it is the one the
agents run.** Where agents run inside the CLI, the MCP tools are served by
`~/.gemini/antigravity-cli/plugins/agy-team/agyteam/`, not by this repo.
Nothing in this suite can see that copy. We found one pinned to a commit from
before the review gate checked authorship at all — it recorded a self-review,
with no `author` field, as approved — while every test here asserted the gate
held. Its proof gate also pointed at an absolute venv path from a machine it
had been built on, so it rejected every proof it was given. `agyteam.doctor`
now compares the install byte for byte with the repo and fails on drift, and
`plugin/install.sh` derives its file list from the source instead of a
hand-written one: the list had already gone stale, missing two modules that
`mcp_bus` imports *inside a function*, so those tools raised ImportError on the
installed copy alone. **After changing anything in `agyteam/`, reinstall before
you measure anything.**

## Known sharp edges

- **`python -m` puts the current directory ahead of `PYTHONPATH`.** Launched
  from the wrong directory, you will import a different checkout's `agyteam`
  than you think, and your fix will silently not be running. Always launch
  from the repo you mean.
- **An editable install beats both.** `pip install -e` works through a
  `sys.meta_path` finder, and meta_path is consulted before `sys.path` — so
  inside a second checkout, `import agyteam.x` can resolve to the *first*
  one even with the second at the front of the path. It bites exactly when you
  are being careful: checking that a new test fails against an unpatched tree.
  Confirm with `print(module.__file__)` rather than by reasoning about paths.
- The cost display is an estimate from a pricing table in
  `agyteam/config.py`; it has never matched the real balance. Reconcile
  against your billing page, not the display.
- Headless agy auto-denies tools that would prompt, then exits 0 with empty
  stdout. Set `toolPermission: "always-proceed"` before any unattended run,
  or every agent will silently do nothing (README, "Headless permissions").
- The anomaly guard's repetition threshold was calibrated once against real
  degeneration and missed it (7.0x observed vs 8.0x threshold). Volume
  caught it. Treat both signals as necessary; if you retune, tune against a
  captured real spew, not synthetic text.

## What is measured, and what is not yet

- **Cold-start convergence: measured once, and it held.** 2026-09-13, a
  team with nothing but a roster scored perfectly on content but worked
  process-broken — solo, unreviewed, then self-approved under supervisor
  pressure. After one retro + cycle-all, the sibling task ran through the
  full delegate-implement-review pipeline, with the independent review
  recorded *before* the user was answered, at lower cost. No human steered
  between the runs. That is one data point from a strong cold start:
  replicate it on your infrastructure with `bench/convergence.py` (fresh
  team name; it refuses to reuse one), and re-measure after any
  significant change to briefs, norms, or the review gate. The claim is
  load-bearing; never let it drift back to asserted.
- **The review gate now checks who did the work, and this took two goes.**
  The first fix added an `author` field and refused `author == reviewer`.
  That was defeated by typing a different name: a manager recorded
  `author="coder"` for an episode in which coder was never woken and made
  zero tool calls, and the literal string `"unknown"` passed too. The code
  comment beside the check was exactly right about the problem and one step
  short of the remedy — *"misstating who did the work is visible to everyone
  on the bus"*. Visible is not checked. The author must now be a teammate,
  not you, and demonstrably active since the last episode boundary, against
  the bus, the event log and the audit log. Where no channel is readable the
  record says `author_verified: false` rather than counting clean. The
  behavioural mitigation stays: every answer ends with a "how we worked" note
  naming who did and who reviewed. If the notes stop naming names, that
  mitigation has decayed — but the gate no longer depends on it.
- The bench has one sibling pair and measures execute-vs-read plus basic
  process (delegation, review-before-answer). It does not yet measure
  lateral consultation (historically near zero) or cost discipline.
- The grader misread correct answers three times in one day — brittle
  regexes reading form instead of substance, each fixed in a commit that
  says so. When the instrument and unambiguous substance disagree, fix the
  instrument, in the open.

## If you keep one sentence

Reference the artifact; never restate it. Every expensive day this project
had traces back to a copy standing in for the thing itself.
