# agy-team

A persistent, learning agent for Google Antigravity — and a team of them that
argue with each other, review each other's work, and keep durable memories
between sessions.

It is two things at once, and it is worth being honest about which is which:

- **A working agent platform.** Five agents with different jobs, a message bus,
  a review gate, memory that survives restarts, and four swappable seams so you
  can replace the parts that touch your infrastructure.
- **A lab for watching agent teams fail.** Most of what is interesting here came
  from provoking mistakes and reading the wreckage. The findings are written
  down in the code comments, usually next to the thing they explain.

## How to train your team (read this before installing)

The team does not arrive perfect; it arrives **trainable**. Out of the box the
first runs may cut corners — one agent doing everything, work approved by the
person who wrote it. That is normal, and the system corrects it, but only if
the loop runs: **tell the manager what you liked and didn't, in plain chat**
("qa never saw that — why?"), then run `--retro` and `--cycle-all`. Learnings
become team norms and durable memories, and behaviour measurably changes on
the next task.

Check `<team_dir>/NORMS.md` after your first retro. If it was not written, the
loop did not close and the next task will look like the last one — the retro
report says which half was empty.

You never need to read logs to see how the work happened: **every answer ends
with a brief "how we worked" note** — who did the work, who reviewed it and
their verdict, anything skipped. If the same name wrote and approved something,
or nobody was delegated to, that note is where you'll see it. Say so to the
manager; that sentence is the training.

The note is a convenience, not the safeguard. Underneath it, a review must
name an agent who is on the roster, is not the reviewer, and has actually
worked this episode — checked against the bus, the event log and the audit
log, none of which an agent can edit. We added the author field first and
watched a manager defeat it by typing a different name.

Measured, not promised: a team started from zero went from solo, self-approved
work to a full delegate-implement-review pipeline — at lower cost — after one
retro-and-cycle. Reproduce it on your own hardware with
`bench/convergence.py` (spends real money; see `bench/README.md`).

## Quick Start (Zero-Friction Launcher)

```bash
git clone https://github.com/roelle/agy-team.git
cd agy-team
./run
```

The `./run` launcher automates the entire environment lifecycle:
- Verifies Python >= 3.10 and creates/repairs an isolated local virtual environment (`.venv`).
- Installs `agyteam` and required dependencies in editable mode (`pip install -e ".[dev]"`).
- Guides you through configuring your `GEMINI_API_KEY` (saved locally in `.env`) and validates it via a zero-token API probe before any model calls.

**No Gemini key?** You do not need one. The offline suites never did
(`./run test`), and if you reach models another way — an internal endpoint,
application default credentials, or your own `AGYTEAM_RUNNER` — tell the
launcher once and it stops asking:

```bash
./run setup --no-key
```

### Primary Commands

| Command | Description |
|---|---|
| `./run` or `./run chat` | Launch an interactive multi-turn chat session with the team |
| `./run ask "<prompt>"` | Submit a one-shot task to the team and stream the verified result |
| `./run status` | Non-destructive view of mailbox and pending agent tasks |
| `./run stop` | Safely halt all running background agents and daemon processes |
| `./run start` | Resume background team agents |
| `./run export <bundle>` | Export entire team state, memories, and config to a portable bundle (.tar.gz) |
| `./run import <bundle>` | Restore a team bundle into a local environment with overwrite safety |
| `./run setup` | Check or reconfigure your Gemini API key and virtual environment |
| `./run test` | Run the complete offline test suite (100% free, 0 API tokens) |

### Upfront Cost Transparency

- **Offline Testing is 100% Free**: Running `./run test` (105+ contract and behavioral tests) executes against local SQLite fixtures and mocked transports — zero API tokens, zero cost.
- **Key Validation is Zero Tokens**: The setup probe validates your API key via `models.list` metadata — zero model inference tokens.
- **Estimated Live Team Cost**: A full reactive multi-agent work session typically costs **~$11/day** of continuous operation (or ~10¢–30¢ per deep delegated task), depending on token usage and model selection (default: `gemini-3.8-flash`).

### Stopping & Resuming Safely

- To pause or cancel an active interactive turn: press **Ctrl+C**.
- To cleanly stop background daemons and agent lifecycles: run **`./run stop`**.
- To resume the team anytime: run **`./run start`** or **`./run chat`**. All agent memory and durable workspace artifacts are automatically preserved on disk.

### Troubleshooting for Non-Coders

- **Missing or Invalid API Key**: If `./run` reports `GEMINI_API_KEY is unset or invalid`, grab a key from [Google AI Studio](https://aistudio.google.com/) and paste it when prompted, or save `GEMINI_API_KEY=your_key_here` in `.env` in the repository root.
- **Billing / Quota Limits**: If you see quota or 429 rate limit errors, verify your Google AI Studio project has billing enabled and tier quotas configured for Gemini API access.
- **Python Version**: `agy-team` requires Python 3.10 or newer. If `./run` reports an older version, install Python 3.10+ using your system package manager (e.g. `sudo apt install python3 python3-venv` on Ubuntu/Debian, or `brew install python` on macOS).

## Developer / Manual Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install google-antigravity
echo "GEMINI_API_KEY=..." > .env
```

Talk to a single agent. It remembers you next time:

```bash
.venv/bin/python -m agyteam.sdk_cli -p "what did we talk about last time?"
```

Run a team. One message to the manager cascades through everyone who needs to
act, and stops when the work is answered:

```bash
export AGYTEAM_TEAM=build
export AGYTEAM_RUNNER=agyteam.runner_sdk:SdkRunner
.venv/bin/python -m agyteam.supervisor --say "manager: read src/, find the
  worst bug in it, and prove it is a bug before you tell me"
```

Useful while it runs, or after:

```bash
--report          who did what, from the record rather than from memory
--cost            spend per agent
--status          who has mail waiting
--retro           the team reflects on recent work and writes team norms
--cycle <agent>   distil an agent's context into memory, then start fresh
--daemon          stay up and react to mail as it arrives
```

Everything durable lives in `~/agy-teams/<team>/`: one directory per agent for
identity and memories, plus the shared bus, roster, reviews and event log.

## The team

| agent | job |
|---|---|
| `manager` | faces you. Decides what "done" means, gates what reaches you, and is accountable for it. Has a shell so she can check claims rather than take them on report. |
| `tpm` | faces the team. Breaks work into pieces one agent can finish, tracks what is outstanding, runs retros. |
| `coder` | implements, with tests. |
| `syseng` | owns the environment. Reproduces problems and reports the command and output that produced them. |
| `qa` | reviews. Cannot edit source, so findings go back to the author. Keeps a memory of defects that recur here. |

Oversight roles run a different model family from the implementers on purpose.
Three reviewers with different blind spots caught three defects that none of
them found alone; difference did that, not capability.

## The four seams

Each is one env var plus a config blob, each has a template to copy and a
contract suite that any implementation must pass. They exist so this can be
dropped into an environment whose messaging, storage, or accounting is not ours.

| seam | what it swaps | env | template |
|---|---|---|---|
| transport | how agents talk | `AGYTEAM_BUS_TRANSPORT` | `agyteam/transport_template.py` |
| memory | where memories live | `AGYTEAM_MEMORY_STORE` | `agyteam/memory_template.py` |
| runner | how an agent is woken | `AGYTEAM_RUNNER` | `agyteam/runner_template.py` |
| observer | turns, cost, tool calls | `AGYTEAM_OBSERVER` | `agyteam/observer_template.py` |

**Adapting to a new platform? Start with the runner, not the transport.**
Replacing the transport is the tempting move — you probably already have
messaging — but it takes the supervisor out of the scheduling loop, and its
four guarantees (hop budget, stop-on-answer, failure isolation, requeue) then
have to be rebuilt by hand in your adapter. Swapping the runner and keeping
the file bus costs nothing and keeps all four. The runner's real requirement
is weaker than it looks: **detect that a turn ended**; reading the reply is
optional, because agents publish over the bus.

Then run the preflight, which is offline and takes seconds:

```bash
python -m agyteam.doctor
```

It calls every bus tool end to end with real arguments, writes a probe message
through the bus and checks it lands in the directory the supervisor is about
to read, prints the resolved runner and its three capability flags, checks that
no workspace grant reaches the team directory, compares the installed plugin
with this repo file by file, and echoes the opening brief. Each of those exists
because skipping it cost someone a day.

The install comparison is the one to run after every change under `agyteam/`.
Inside the CLI the MCP tools are served by the *installed* copy, not by this
repo, and an install that is behind fails nothing and reports nothing — it just
runs the code you stopped believing in. Reinstall with `bash plugin/install.sh`
before you measure anything.

A runner also declares what it *cannot* do — audit tool calls, contain
agents to workspaces, enforce roster `tools_off` — and tools that read those
declarations report "could not look" rather than "found nothing". Say False
where you mean False; it is the input other tools need, not a failing grade.

`evals/test_runner_columns.py` runs the core flows over two runner columns,
one returning prose and one returning nothing. **A test that passes in `rich`
and fails in `minimal` is a dependency on something no runner promised.** Run
it against your own runner before you trust a green suite.

Two guarantees documented elsewhere in this README are provided by the
*runner*, not the core, so a runner declares whether it has them
(`supports_audit`, `supports_containment`; `agyteam.lifecycle status` prints
both). On a runner without them, workspace grants do nothing and no tool-call
record exists — and tools that read those things must report that they could
not look, never that there was nothing to find.

## What is in flight

Being worked on right now, by the team itself:

## Todo

- **Validate on a codebase nobody here has seen.** Everything so far has been
  this repo or a problem we already knew the answer to.

## What we learned by getting it wrong

The findings that survived contact, in short form. Each one cost something.

- **Make the honest path the cheap path.** Not "make gaming hard" — align the
  gradient. A rule anchored to reality stays cheap to satisfy honestly; a rule
  that accepts an artifact gets cheaper to fake as they learn its shape.
- **Watch for rules that create a new artifact to produce.** If a mechanism can
  be satisfied by generating a document, it will be. Rules that remove a
  capability or read the world hold up; rules that ask for evidence decay.
- **The observed reward is "produce output that would be accepted", not "be
  correct".** That substitution explains manufactured compliance artifacts,
  benchmarks chosen because they pass, and reviews that verify nothing.
- **Substitution is the failure mode.** Nearly every real defect here came from
  replacing the thing under test with a representation of it: a transcribed
  class instead of an included one, a mock instead of a live payload, a
  benchmark that could only succeed, a result pasted where a computation
  belonged. A test that *contains* a copy of the code under test is testing the
  copy.
- **Correct numbers are better camouflage than wrong ones.** A laundered
  constant that happens to be right passes every check that asks "is this
  true", and fails only one that asks "where did this come from".
- **A lesson only sticks if it names an action a gate could check.** One agent
  wrote "grep the tests for mocks and fail if found" and changed behaviour the
  next day. Another wrote "inspect ground truth before writing" and did not.
  Virtues lose to deadlines.
- **Separate roles positionally, not morally.** You cannot ask an agent to
  represent your interests; you define its job in those terms. The gatekeeper
  reading the same teammate brief as the people she gates is socialised into
  the group she is meant to hold to account.

## Dependencies

Third-party imports are confined to the modules that actually talk to a model,
so the installable surface stays dependency-free:

| module | needs | used by |
|---|---|---|
| `scope`, `store`, `roster`, `mcp_base`, `mcp_memory`, `mcp_bus`, `transport*`, `memory*`, `runner`, `runner_agy`, `supervisor` | **stdlib only** | the agy plugin, any MCP host, reactive dispatch |
| `sdk_agent`, `sdk_cli`, `runner_sdk`, `runner_mixed` | `google-antigravity` | SDK agent, CLI, and SDK/mixed runners |

`store.py` holds the tool implementations, `Toolbox`, and memory store. That split is what keeps the
MCP servers importable without a virtualenv, and `evals/test_mcp.py` enforces
it — including a control case asserting that `sdk_agent.py` *does* fail on bare
python (missing `google-antigravity`), so the check can't silently stop discriminating.

## Single agent (priority zero)

```bash
.venv/bin/python -m agyteam.sdk_cli                 # REPL
.venv/bin/python -m agyteam.sdk_cli -p "task..."    # one-shot
```

Flags: `-w WORKSPACE` (default `workspace/`), `-m MODEL`
(default `gemini-3.8-flash`), `--quiet`, `--no-distill`.

### How learning works (and why prior attempts didn't)

- `workspace/IDENTITY.md` — agent-editable identity, loaded at boot.
- `workspace/MEMORY.md` — one-line index of all memories, loaded at boot.
- `workspace/memory/*.md` — topic files via `save_memory`/`read_memory`/`delete_memory`.
- Every `save_memory` updates the index **and returns the refreshed index in the
  tool result**, so in-context memory never goes stale mid-session even though
  the Antigravity harness only reads system instructions once.
- On `/quit` (or after a one-shot) a distillation turn pushes unsaved durable
  learnings into memory. The write path and the read path are the same files —
  the broken link in both prior attempts.
- A fixed grounding contract (in `agyteam/persona.py:CONTRACT`, not agent-editable)
  requires claims to trace to tool output or memory, and makes "I don't know"
  an acceptable answer.

## Team platform

```bash
python -m agyteam.supervisor --say "tpm: <task>"    # run until idle
python -m agyteam.supervisor --daemon               # stay up, react as mail arrives
python -m agyteam.supervisor --status               # who has mail waiting (non-destructive)
```

### Role-scoped tools (why agents collaborate)

Per-agent in `team/roster.json`: `"tools_off": ["run_command", ...]` disables
harness builtins at session build time (the tool schema never reaches the
model), and `"workers": false` removes subagent spawning. The default tpm has
no shell, no file writes, and no workers — delegation is its only path to
results, which is enforced by capability, not prose. Verified: asked for the
host's Python/pip versions, tpm delegated to syseng, had coder independently
verify (the review-before-delivery contract), and reported accurate results.

- Roster: `team/roster.json` (default: tpm, coder, syseng). Per-agent model is
  configurable there.
- Each teammate is a persistent SDK Agent session with its own workspace and
  memory under `team/agents/<name>/` — any of them also runs standalone via
  `agyteam.sdk_cli -w team/agents/<name>`.
- **Teammates vs workers is structural**: teammates can only be *messaged*
  (`send_to_teammate`, persisted to `team/bus.jsonl`); workers can only be
  *spawned* (builtin `start_subagent`, depth-capped at 1) and have no memory or
  bus identity. Agents cannot confuse them because the affordances differ.
- Runaway protection: `MAX_HOPS` agent turns per user stimulus, then the
  scheduler pauses; each agent session has a model-call budget; `quit` distills
  every agent's learnings to memory first.
- Shared deliverables go in `team/shared/`.

## Reactive teamwork (no human polling)

A teammate's message *wakes* the agent it was sent to. Whatever that agent sends
in reply lands in another inbox and wakes them in turn, so one instruction
cascades through the team and comes back to you when it's done. Nobody sits on
unread mail waiting to be prodded — a message from a teammate causes work the
same way a message from you does.

```bash
python -m agyteam.supervisor --say "tpm: get X built and verified"   # until idle
python -m agyteam.supervisor --daemon        # stay up, react as mail arrives
python -m agyteam.supervisor --status        # who has mail waiting (non-destructive)
```

Verified with real agents (`evals/test_reactive.py`, 7/7): one instruction to
the tpm produced seven turns and this trace, with no inbox ever checked by hand —

```
user->tpm ; tpm->coder ; coder->tpm ; tpm->syseng ; syseng->tpm ; tpm->user
```

The tpm delegated (it has no shell), coder wrote the file, syseng independently
verified by running it, and the confirmation came back to the user unprompted.

**Runners are the third seam**, deliberately parallel to transport and memory —
they decide *how* an agent is woken:

| runner | wakes an agent by | needs |
|---|---|---|
| `agyteam.runner_sdk:SdkRunner` (default) | a persistent SDK session per agent | `google-antigravity` |
| `agyteam.runner_agy:AgyRunner` | `agy --conversation <id> -p "<message>"` | the agy CLI |
| `agyteam.runner_mixed:MixedRunner` | per-agent runner from `roster.json` | `google-antigravity` |
| your own | anything | — |

```bash
export AGYTEAM_RUNNER=agyteam.runner_sdk:SdkRunner
export AGYTEAM_RUNNER_CONFIG='{"model":"gemini-3.8-flash"}'
```

The SDK runner is the default. It keeps sessions alive between wakes, so an agent
woken five times has one continuous context rather than five cold starts, supports
hooks, and enforces `tools_off` at the capability level. The CLI runner makes a team
work *inside Antigravity proper*: agents are agy custom agents, so they get the
harness's tools, policies, and subagents, and sessions are visible to Remote Control.

`agyteam.runner_mixed:MixedRunner` enables mixed runtimes where individual agents
can run under different runners configured per agent in `roster.json` (e.g.,
`"runner": "agyteam.runner_agy:AgyRunner"`). The two runtimes trade rather than
rank:
- **SDK runner**: enforces `tools_off` for real (an agent given it reports `NO TOOLS`
  and cannot write a file) and supports compaction/tool hooks.
- **agy CLI runner**: routes through Antigravity's backend to reach models the API
  key cannot (e.g. `claude-opus-4-6-thinking`, `claude-sonnet-4-6`, `gemini-3.1-pro-high`),
  trading capability-enforced tools for broader model availability.

This allows roles like `qa` to use a frontier model on the CLI while implementers
like `coder` remain on the SDK with capability-enforced roles.

`check_inbox` still exists as a tool — useful mid-task, since a teammate may
answer while you're working — but it is no longer how delivery happens.

Runaway protection: a hop budget bounds one stimulus (`--max-hops`, default 32)
so agents can't ping-pong your token budget away, and an agent that fails is
logged and skipped rather than stopping the team. Both are tested.

### Knowing when to stop

An early real run produced 31 messages of pure courtesy — *"Acknowledged, thanks
for standing by"* in both directions — until the hop budget killed it. The cause
was the wake prompt, which told agents to always reply; two agents that always
reply never stop. Three things fix it:

- The wake prompt now says to send a message **only** when it carries something
  the recipient lacks, bans acknowledgements outright, and states plainly that
  doing nothing is a correct outcome.
- **Answering the user ends the episode.** `run_until_idle` stops as soon as the
  user's mailbox grows, on the theory that the ask is done once it's been
  answered. Disable with `--no-stop-on-answer`.
- The user has a real mailbox. Messages to `user` used to go only to
  `bus.jsonl`, which made the answer unreadable without grepping the log and
  left nothing to detect completion from. `--say` now prints what agents
  addressed to you, and the transport contract tests user delivery — an
  alternate transport that drops it gets a team that never stops talking.

### Push instead of polling

The supervisor asks each transport what arrived. The file transport can only be
polled, so `Transport.peek()` is checked on an interval (`--poll`, default 1s) —
cheap, since it's a file read, and the *agents* are still event-driven either
way.

**A push transport replaces the supervisor's scheduling role; it does not
accelerate it.** These are two scheduling models and they do not compose. The
supervisor owns a loop: it decides who wakes next, counts hops against
`--max-hops`, stops the episode when the user is answered, isolates a failed
turn, and requeues mail the turn did not survive. A transport that delivers by
pushing into a live session bypasses that loop entirely — the wake happens
without the supervisor's knowledge, so the hop budget does not count it, the
stop condition does not see it, and a failure has no requeue path. Running both
gets you two schedulers driving the same agents, which is the concrete failure
we hit from a different direction when a stale supervisor process stayed alive:
one agent, two drivers, messages delivered twice.

So if you implement a pushing transport, plan to own what the supervisor was
doing: runaway bounds, episode termination, failure isolation, and at-least-once
delivery. Either drive it *through* `peek`/`fetch` so the supervisor stays the
scheduler and the interval simply stops mattering, or take the loop over
deliberately and reimplement those four guarantees. The SDK's native triggers
(`google.antigravity.triggers.on_file_change` / `every`) push straight into a
live session and are firmly in the second category.

## Session lifecycle: cycling and distillation

Distillation (sweeping unsaved learnings into memory files) fires on `/quit`,
after one-shot runs, on `/cycle` (single agent) / `cycle <agent>` (team), and —
wired but see quirks — when the harness reports a compaction event. Cycling is
cheap by design: memory is on disk, so a fresh session reboots with the full
index. Let sessions live for days; cycle weekly-ish or when a session feels
off. Primary safety net is the contract's save-as-you-go rule, which evals
confirm fires unprompted.

## Memory over MCP (swappable storage)

`agyteam/mcp_memory.py` is a zero-dependency MCP stdio server exposing memory as
`save_memory` / `read_memory` / `delete_memory` / `memory_index`. `--mcp` on
`agyteam.sdk_cli` routes memory through it instead of in-process callables —
same files, same evals (6/6 verified).

**Storage is pluggable, exactly like the A2A transport.** The default keeps
human-readable markdown in the agent's durable scope; point
`AGYTEAM_MEMORY_STORE` at your own class to put memory in a shared database, a
knowledge service, or an internal store:

```bash
export AGYTEAM_MEMORY_STORE=example_memory:MyStore
export AGYTEAM_MEMORY_CONFIG='{"dsn":"..."}'     # optional, JSON
.venv/bin/python evals/test_memory.py     # 14 checks per store
```

Copy `agyteam/memory_template.py` and implement four methods (`save`, `read`,
`delete`, `index`). Stores handle *storage only* — the server owns presentation,
so every backend produces identical wording, including the honest miss where a
read of an absent memory reports what does exist instead of letting the model
guess. That property is load-bearing for grounding, so the contract suite tests
it directly (a store returning fabricated content on a miss fails). Names are
normalised centrally, so `"Deploy Host"` and `"deploy-host"` are the same record
on every backend, and a shared backend must namespace by agent — the suite
checks that one agent can't read another's memory.

Verified the same way as the transport: `evals/fixture_memory.py` is a
SQLite-backed store written against only the public interface, and the same
14-check contract passes on it and on the file store, plus four loader-safety
cases. Misconfiguration exits loudly rather than falling back to local files,
which would strand an agent's learnings where nobody looks.

Any MCP host can mount the server; to give a pinned Antigravity hub conversation
the same memory, register it in `~/.gemini/antigravity/mcp_config.json`:

```json
{"mcpServers": {"agyteam_memory": {
  "command": "/mnt/data/claw-agy/.venv/bin/python",
  "args": ["-m", "agyteam.mcp_memory", "/mnt/data/claw-agy/workspace"],
  "env": {"PYTHONPATH": "/mnt/data/claw-agy"}}}}
```

## agy CLI plugin (the installable form)

The plugin supplies **tools**, not agents. Agents are defined in
[`persona.py`](agyteam/persona.py) and given to whichever runtime starts them:

```
plugin/agy-team/
  plugin.json          manifest
  skills/{distill,inbox,handoff}.md     slash commands
  rules/grounding.md   grounding + learning + collaboration contract
  mcp_config.json      rendered at install time with absolute paths
```

Install and run:

```bash
bash plugin/install.sh                  # → ~/.gemini/antigravity-cli/plugins/agy-team
python -m agyteam.session tpm           # join the conversation tpm works in
```

> **Do not use `agy --agent <name>`.** Measured on agy 1.2.0: the default agent
> has `write_to_file`; an agent loaded from a plugin `agents/` directory has
> **no builtin tools at all** and cannot write a file, while the default agent
> plus `AGYTEAM_AGENT=<name>` has both builtin *and* our MCP tools. The
> mechanism is absent from the documented plugin spec (`plugin.json`,
> `mcp_config.json`, `hooks.json`, `rules/`, `skills/` — see agy's builtin
> `agy-customizations` skill) and appears to route through the subagent
> machinery. Roles therefore live in `persona.py`, and identity travels in
> `AGYTEAM_AGENT`, which is what the MCP servers read.

The installed plugin is **self-contained**: the stdlib MCP modules are copied in
beside the manifest and run under system `python3`, so nothing breaks if this
repo later moves or is deleted. The installer renders real paths into
`mcp_config.json` (env-var expansion in that file is undocumented, so nothing
depends on it), seeds the team roster, and then verifies the result by importing
the servers from the *installed copy* with this repo off `PYTHONPATH`.

Agent identity is not baked in: both MCP servers inherit `AGYTEAM_AGENT` from
the agy process, so one install serves every agent on every team.

### File scopes

`agyteam/scope.py` defines three classes of path, each overridable by env var,
`scopes.json` in the project, or argument — run `python -m agyteam.scope` to see
what resolves where:

| scope | holds | default |
|---|---|---|
| durable | identity, memory, roster, bus (survives every project) | `~/agy-teams/<team>` |
| project | the repo or mapped drive being worked in | enclosing **git root**, else `$PWD` |
| shared | team artifacts and deliverables | `<project>/.agy-team-shared` |

Durable state lives under your **home directory, not `~/.gemini`** — app and OS
config are replaceable, but memory is the part you'd be sad to lose, so it
belongs where your backups already point. Plugin *configuration* still installs
to `~/.gemini/antigravity-cli/plugins/` because that's where agy looks for it.

### Team namespacing

Everything durable is namespaced per team, so separate teams share no roster, no
bus, and no memory, and cannot observe each other — useful for isolating real
work from experiments:

```bash
AGYTEAM_TEAM=team-b bash plugin/install.sh            # seed a second team
AGYTEAM_TEAM=team-b python -m agyteam.session tpm

# or point at an exact path, ignoring the <root>/<team> layout entirely
AGYTEAM_DURABLE_DIR=~/my-agents/my-agent-team-A python -m agyteam.session tpm
```

`AGYTEAM_TEAMS_ROOT` moves the whole collection; `AGYTEAM_DURABLE_DIR` overrides
one team outright. Team names are sanitised into directory names, so a stray
`../` can't escape the teams root (tested).

Project scope is discovered, not hardcoded: it walks up for `.git` (handling the
worktree/submodule case where `.git` is a file), so running an agent from a
nested subdirectory resolves to the same repo root every time, and falls back to
`$PWD` outside a repo. Discovery is pure Python — no `git` subprocess — because
these run inside MCP servers where a missing or slow binary would fail silently.

Memory is forced into durable scope, so an agent that moves between repos (or
works on a network mount) keeps what it learned and never strands memory in
someone else's checkout.

### Roster shapes

The canonical roster is a **list of self-describing entries**, because entries
get passed around individually (into session configs, transports, log lines) and
one that carries its own name needs no context to be useful. A `name -> spec`
mapping forces you to thread the key alongside the value everywhere it travels,
and forgetting to is precisely the class of bug this caused.

Hand-written config shouldn't have to care, so `agyteam/roster.py` accepts and
normalises all of these:

```json
{"agents": [{"name": "tpm", "role": "coordinates"}]}   // canonical
{"agents": {"tpm": {"role": "coordinates"}}}           // mapping, key is name
{"agents": {"tpm": "coordinates"}}                     // mapping to role string
{"agents": ["tpm", "coder"]}                           // bare names
```

Per-agent extras (`model`, `tools_off`, `workers`) survive normalisation, and
writes are always canonical — so a mapping-shaped roster **migrates itself** the
first time it's edited. Genuinely ambiguous input is rejected with a readable
message rather than an exception from three frames down: duplicate names, a
mapping key that disagrees with its own `name` field, a list entry with no name,
`user` as an agent name (it's reserved for the human), or a wrong type.

### Roster management

`roster_add` / `roster_remove` are exposed over the bus server **only** when
`AGYTEAM_ROSTER_ADMIN=1`. Team composition is the operator's call, not something
an agent should do to itself mid-task; without the flag the tools aren't in the
schema at all, so a curious agent can't even try.

### A2A messaging (swappable transport)

Antigravity exposes no peer messaging publicly — Teamwork coordinates through
workspace artifacts, and the SDK offers only subagents. `agyteam/mcp_bus.py`
supplies the channel over MCP, so it works identically in the agy CLI, the
desktop hub, and SDK agents. Delivery is inbox-based (`check_inbox`) rather than
push, because when the harness owns the loop nobody can force a peer to take a
turn.

**The wire is pluggable.** The agent-facing tools are fixed; how messages move
is not. If a native or internal A2A mechanism becomes available to you,
implement `agyteam/transport.py:Transport` against it and point an env var at
your class — no agyteam source changes, and agents notice nothing:

```bash
export AGYTEAM_BUS_TRANSPORT=example_transport:MyTransport
export AGYTEAM_BUS_CONFIG='{"endpoint":"..."}'      # optional, JSON
.venv/bin/python evals/test_transport.py  # 15 checks per transport
```

Copy `agyteam/transport_template.py` and implement three methods (`send`,
`fetch`, `teammates`); `broadcast`, roster admin, and `close` have working
defaults. **That file is the only thing you should need to write** to move off
the file bus.

Guarantees the contract suite enforces, because they're what the rest of the
system assumes: unknown recipients produce an `[error: ...]` string rather than
vanishing, messages are consumed exactly once (redelivery loops agents forever),
and inboxes are isolated per agent. Misconfiguration fails loudly — a bad
module, malformed spec, bad config JSON, or non-`Transport` class exits with a
message rather than silently falling back to the file bus, which would split the
team across two channels and produce messages that just disappear.

Swappability is verified, not asserted: `evals/fixture_transport.py` is a
SQLite-backed transport written against only the public interface, and the same
12-check contract passes on it and on the file transport.

#### Why the seam exists

The local default is a **stand-in for a primitive the platform doesn't publicly
expose**, and stand-ins are what you make swappable — the same call you'd make
for any local substitute for a missing capability. Replacing the whole MCP
server instead wouldn't be equivalent: the bus server owns the roster, naming,
and delivery guarantees that agents' instructions depend on, so a replacement
would have to reimplement all of it. The seam changes only the wire.

The interface shape derives from this project's own tool surface
(`send_to_teammate`, `check_inbox`, `list_teammates`), not from any other
system's API. The one line written toward an unknown implementation is
`fetch()`'s note that a push-based backend should buffer and drain, which is
generic messaging design rather than knowledge of any particular system.

**Durable memory has the identical seam** (`agyteam/memory.py`,
`AGYTEAM_MEMORY_STORE`, `memory_template.py`, `evals/test_memory.py`) for the
same reasons and with the same structure, so the two extension points are
symmetric rather than one being a special case.

## Evals (run these after changes)

```bash
.venv/bin/python evals/test_mcp.py                    # wiring, scopes, purity, teams — free
.venv/bin/python evals/test_transport.py              # A2A contract, both transports, free
.venv/bin/python evals/test_memory.py                 # memory contract, both stores, free
.venv/bin/python evals/test_self.py                   # agent introspection server, free
.venv/bin/python evals/test_supervisor.py             # reactive dispatch, free
.venv/bin/python evals/test_review.py                 # durable review records & approval gate, free
.venv/bin/python evals/test_observer.py               # observer contract, file & SQLite, free
.venv/bin/python evals/test_cycle.py                  # cycle & distillation to durable memory, free
.venv/bin/python evals/test_continuity.py             # context continuity per runner, free
.venv/bin/python evals/run_evals.py                   # SDK agent (default)
AGYTEAM_EXTRA_ARGS=--mcp .venv/bin/python evals/run_evals.py # SDK agent, memory via MCP
.venv/bin/python evals/test_reactive.py               # real reactive team, ~30c
.venv/bin/python evals/test_a2a.py                    # A2A behavioral, ~15¢
```

Current status — all green:

| suite | what it proves | score |
|---|---|---|
| `test_mcp.py` | server wiring, roster shapes/migration, git-root scopes, stdlib purity, team isolation | 42/42 |
| `test_transport.py` | A2A contract on file + independent SQLite transport, loader safety | 34/34 |
| `test_memory.py` | memory contract on file + independent SQLite store, loader safety | 64/64 |
| `test_self.py` | agent introspection server, tool functionality, robust isolation | 22/22 |
| `test_supervisor.py` | reactive cascade, hop budget, failure isolation, non-destructive status, ack-spiral termination | 51/51 |
| `test_review.py` | durable review records, approval gate, and supervisor integration | 21/21 |
| `test_observer.py` | event logging, accounting stream, token preservation, and run metrics | 69/69 |
| `test_cycle.py` | conversation cycle, distill abort safety, frontmatter parsing | 83/83 |
| `test_continuity.py` | whether a wake resumes context or cold-starts, per runner | 4/4 |
| `test_delivery.py` | **the product**: one instruction → a verified artifact on disk, via the agy CLI | 8/8 |
| `test_reactive.py` | real agents woken by teammates, end to end, no human polling | 7/7 |
| `run_evals.py` | teach→restart→recall ×3; fabrication probes ×3 | 6/6 |
| `test_a2a.py` | real agents delegate, mail crosses processes, memory lands durable | 8/8 |

Typical cost: free offline suites, ~8¢ SDK, ~15¢ A2A.

## Headless permissions: required for unattended teamwork

Verified against agy 1.2.0. In headless (`-p`) mode the CLI **auto-denies** any
tool that would need an approval prompt, then exits **0 with empty stdout** and
the reason on stderr. An agent woken by the supervisor therefore does nothing,
silently, until permissions are pre-granted. Set this once:

```jsonc
// ~/.gemini/antigravity-cli/settings.json
{ "toolPermission": "always-proceed" }
```

Two traps found the hard way:

- `permissions.allow` allow-rules cover the builtin file tools, but the binary
  also carries the string *"Settings allow-rules do not apply"* for a second
  class of tools — so an allowlist alone is not a reliable substitute for
  `toolPermission`, and MCP tools appear to be in that second class.
- Because the failure is exit 0 + empty stdout, anything that reads only stdout
  reports a blank turn. `runner_agy` now surfaces stderr on a silent clean exit
  and names this fix, rather than printing `[no output]`.

`agy plugin validate` reports `mcpServers: skipped (not found)` for our bundle,
yet the servers *do* start and their tools get cached under
`~/.gemini/antigravity-cli/mcp/` — so `mcp_config.json` is honored at session
time even though `agy mcp list` does not show it. Treat `validate`'s MCP line as
unreliable; confirm via the tool cache instead.

## Unverified: the plugin install step

Everything the plugin *does* at runtime is tested (`test_a2a.py` mounts the same
modules with the same env-derived identity and passes 8/8). Installing by file
copy is confirmed to register `agents/` (all three show in `agy agents`) and to
start both MCP servers. Still unverified end to end is a full unattended team
run, which is blocked on the permission setting above. Note also that
`agy plugin list` reports **"No imported plugins"** for a hand-copied bundle —
`agy plugin install <dir>` is the first-class path and may behave differently.
Expect to adjust:

- whether plugin `agents/` uses `<name>/agent.md` (documented for custom agents)
  or a flat `<name>.md`
- `hooks.json` — schema is undocumented, so nothing here depends on it; the
  grounding contract ships as `rules/` and agent instructions instead
- per-agent tool restriction (the SDK path uses `disabled_tools`; the CLI's
  equivalent for custom agents isn't documented). tpm's "no shell" is currently
  instruction-level in the plugin, not capability-level as it is in the SDK team.

## Known quirks

- `compaction_threshold` maps to the harness's hard `max_token_limit`. Base
  context (instructions + tool defs) is ~12k tokens; setting the threshold near
  or below that errors or triggers pathological history-truncation (the model
  re-reads files over and over — one test burned 490k tokens at a 17k limit).
  Keep it high (default 80k). At the tiny limits we could test, the harness
  truncated history *without* emitting the compaction event, so treat
  distill-on-compaction as best-effort, not guaranteed.

- The harness's builtin `create_file` insists on its own "brain" artifact dir
  and can reject workspace paths; agents recover by writing files via
  `run_command`. Harmless, but visible as an HTTP 0 warning.
- `antigravity-preview-05-2026` (the AGY2.0 agent model) has a 131k input
  limit; the compaction threshold (80k, `agyteam/config.py`) respects it.
- Pricing table in `agyteam/config.py` is an estimate for the cost display —
  update from ai.google.dev/pricing if you need it exact.

## Advanced Customization & Developer Guide

For developers, contributors, and power users who prefer direct command-line control or need custom runtime environments:

### 1. Manual Virtual Environment & Packaging

Rather than using `./run`, you can manage your environment manually using standard Python tooling:

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install agyteam in editable mode with development dependencies
pip install -e ".[dev]"

# Set up environment variables
cp .env.example .env   # edit .env to add your GEMINI_API_KEY
```

Installing via `pyproject.toml` registers console scripts directly into your virtualenv:
- `agyteam`: CLI entry point to the supervisor (`agyteam --chat`, `agyteam --say "tpm: <task>"`, `agyteam --status`, etc.)
- `agyteam-lifecycle`: daemon lifecycle management (`agyteam-lifecycle status`, `agyteam-lifecycle stop`, `agyteam-lifecycle start`)

### 2. Direct Module Invocations

You can invoke `agyteam` modules directly with Python:

```bash
# Supervisor & interactive chat
python -m agyteam.supervisor --chat
python -m agyteam.supervisor --say "tpm: build feature X"
python -m agyteam.supervisor --status
python -m agyteam.supervisor --daemon

# Standalone single agent REPL / one-shot
python -m agyteam.sdk_cli -w workspace/
python -m agyteam.sdk_cli -p "analyze this log file"

# Background lifecycle manager
python -m agyteam.lifecycle status
python -m agyteam.lifecycle stop
python -m agyteam.lifecycle start
```

### 3. Environment Variables & Swappable Backends

All subsystems support configuration through environment variables:

| Environment Variable | Description | Default |
|---|---|---|
| `GEMINI_API_KEY` | Google Gemini API key for model inference | None (prompted by `./run`) |
| `AGYTEAM_RUNNER` | Agent execution runner (`agyteam.runner_sdk:SdkRunner`, `agyteam.runner_agy:AgyRunner`, `agyteam.runner_mixed:MixedRunner`) | `agyteam.runner_sdk:SdkRunner` |
| `AGYTEAM_RUNNER_CONFIG` | JSON configuration passed to runner constructor | `{}` |
| `AGYTEAM_BUS_TRANSPORT` | Pluggable A2A messaging transport class | `agyteam.transport_file:FileTransport` |
| `AGYTEAM_MEMORY_STORE` | Pluggable persistent memory store class | `agyteam.memory_file:FileMemoryStore` |
| `AGYTEAM_DURABLE_DIR` | Absolute path overriding durable storage for agent memory and identities | `~/agy-teams/<team>` |
| `AGYTEAM_TEAMS_ROOT` | Base directory for multi-team namespaces | `~/agy-teams` |

### 4. Direct Testing & Verification

Run tests directly with `pytest` without needing manual `PYTHONPATH` exports:

```bash
# Run the entire offline test suite
pytest evals/

# Run specific subsystem contract suites
pytest evals/test_mcp.py
pytest evals/test_transport.py
pytest evals/test_memory.py
pytest evals/test_supervisor.py
pytest evals/test_review.py
```

