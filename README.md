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

The team does not arrive perfect; it arrives **trainable**, and you train it
by talking to it. Out of the box the first runs may cut corners: one agent
doing everything, or work approved by the agent who wrote it. When that
happens, **say so in plain chat, to the agent it concerns** ("qa never saw
that — why?"). That sentence is the training. Where it goes:

- **Memories, which is where most training lands.** Each agent keeps its
  own memories (`~/agy-teams/<team>/agents/<agent>/memory/`). An agent told
  something durable, such as a preference, a correction or a lesson from a
  mistake, is instructed to `save_memory` on the spot, and to fix a memory
  you have just shown is wrong. Whatever it did not write down is caught when
  it is *distilled*. That happens automatically: when its conversation grows
  long and is cycled, before a model change that needs a fresh conversation,
  ahead of a model's weekly allowance running out, and once a day if you turn
  that on (see [Session lifecycle](#session-lifecycle-cycling-and-distillation)).
  Memories belong to one agent: what you tell the manager is the manager's
  memory. Say it to the agent concerned (a direct message, or `@qa` in a
  channel), or ask the manager to pass it on.
- **Norms, the rules for the whole team.** `<team_dir>/NORMS.md` is part of
  every agent's brief. It is written by a retrospective (`--retro`) or by
  you, by hand; agents cannot write it. A brief is read when a conversation
  starts, so after a retro or an edit, `--cycle-all` gives every agent a
  fresh conversation that has the new norms.

**You do not need `--retro` to train a team**, and most teams will not run
it. What it produces is a norm, not memories: each participant answers what
went well, what did not and what to change, and the leader turns that into one
change to `NORMS.md` (or a reasoned "no change"). The answers are kept
(`list_retros`), but `NORMS.md` is what changes behaviour. A retro does not
cycle anyone; that is the `--cycle-all` above. It is worth a turn from every
participant when what went wrong is how the team works together rather than
one agent's habit, for example the same failure from different agents or a
step everyone skips. Its report, `<team_dir>/retro.md`, says what came of it,
including when no norm was written.

You never need to read logs to see how the work happened: **every answer ends
with a brief "how we worked" note** — who did the work, who reviewed it and
their verdict, anything skipped. If the same name wrote and approved something,
or nobody was delegated to, that note is where you'll see it. Say so to the
agent responsible; that is the training.

The note is a convenience, not the safeguard. Underneath it, a review must
name an agent who is on the roster, is not the reviewer, and has actually
worked this episode — checked against the bus, the event log and the audit
log, none of which an agent can edit. We added the author field first and
watched a manager defeat it by typing a different name.

What has been measured is the retro route, because it is the one a script can
drive. A team started from zero went from solo, self-approved work to a full
delegate-implement-review pipeline, at lower cost, after one retro and cycle.
Reproduce it on your own hardware with `bench/convergence.py` (it spends real
money; see `bench/README.md`). Training through conversation is how the team
is meant to be used, and it has no benchmark yet.

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
- Seeds the default five-agent team at `~/agy-teams/default/team/roster.json` the first time, if there is no roster yet. Edit that file to change the team.

**Which runtime wakes the agents.** There are two, and the launcher picks by
what you gave it:

- **You configured a Gemini API key** → the SDK runner
  (`agyteam.runner_sdk:SdkRunner`), which uses that key and enforces roles by
  capability.
- **You have the Antigravity `agy` CLI instead** → run `./run setup --no-key`
  once. The library's own default is the CLI runner
  (`agyteam.runner_agy:AgyRunner`), which needs the `agy` binary on your PATH
  and the permission setting described under
  [Headless permissions](#headless-permissions-required-for-unattended-teamwork).
  Then install the tools the agents call: `bash plugin/install.sh`.
- **Something else** (an internal endpoint, a hosted runtime) → set
  `AGYTEAM_RUNNER` yourself; see [The four seams](#the-four-seams).

The offline tests need none of this: `./run test` runs with no key and no CLI.

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

- **Offline Testing is 100% Free**: Running `./run test` (about 700 contract and behavioral tests, under two minutes) executes against local SQLite fixtures and mocked transports — zero API tokens, zero cost.
- **Key Validation is Zero Tokens**: The setup probe validates your API key via `models.list` metadata — zero model inference tokens.
- **Estimated Live Team Cost**: With every agent on the default model (`gemini-3.8-flash`) on the SDK runner, a delegated task has typically cost 10¢–30¢ and a day of continuous operation about $11. Putting `manager` and `qa` on a Pro model, as recommended below, costs more than that; the figures in the eval tables below are Flash-priced as well. Treat all of them as rough: they depend on the model and on how much each agent reads. `--cost` shows what was actually recorded, and says "unknown" rather than $0 for a model it has no price for.

### Stopping & Resuming Safely

- To pause or cancel an active interactive turn: press **Ctrl+C**.
- To cleanly stop background daemons and agent lifecycles: run **`./run stop`**. A stop cancels the turns in flight (their mail is delivered again when the team starts); to restart without losing them, use `./run restart` or `./run update` below.
- To resume the team anytime: run **`./run start`** or **`./run chat`**. All agent memory and durable workspace artifacts are automatically preserved on disk.

### Updating a running team

```bash
./run update          # or: python -m agyteam.update
```

One command updates agy-team where it is installed (a git checkout
fast-forwards; a pip install from git moves to the newest release tag),
reinstalls the agy CLI plugin if there is one, and restarts every running
team under `~/agy-teams` **gracefully**: each supervisor starts no new
turns, lets the ones in flight finish, then restarts itself on the new
code. Mail keeps queuing while it drains, so nothing is lost and you do not
have to catch the team idle. `--check` says what would change, `--now`
cancels the turns in flight instead of waiting (their mail goes back),
`--no-code` only restarts, `--team-dir` picks teams. `./run restart` (or
`python -m agyteam.supervisor --restart`, `actions.restart`, or `kill -HUP`
on the daemon) restarts one team the same way without updating.

A supervisor started before this existed does not know the request. For
one of those, `update` waits for a moment when it has no turn in flight,
stops it and starts it again as `--daemon --team-dir <team>`. Only the
host runner can resume a turn across a restart; on the CLI and SDK runners
a turn lives in the supervisor's process, which is why the restart drains.

### Troubleshooting for Non-Coders

- **Missing or Invalid API Key**: If `./run` reports `GEMINI_API_KEY is unset or invalid`, grab a key from [Google AI Studio](https://aistudio.google.com/) and paste it when prompted, or save `GEMINI_API_KEY=your_key_here` in `.env` in the repository root.
- **Billing / Quota Limits**: If you see quota or 429 rate limit errors, verify your Google AI Studio project has billing enabled and tier quotas configured for Gemini API access. A failure that means the account itself is out (`insufficient_quota`, HTTP 402, "credit balance is too low"; see `AGYTEAM_BUDGET_HALT_PATTERNS`) stops the whole team once and tells you, instead of every agent failing in turn; the mail stays queued, and `python -m agyteam.lifecycle start` resumes.
- **Python Version**: `agy-team` requires Python 3.10 or newer. If `./run` reports an older version, install Python 3.10+ using your system package manager (e.g. `sudo apt install python3 python3-venv` on Ubuntu/Debian, or `brew install python` on macOS).

## Developer / Manual Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[sdk]"      # the core alone: pip install -e .
echo "GEMINI_API_KEY=..." > .env
```

The core (supervisor, MCP servers, transports, the CLI and host runners, the
client API) is standard library only. The `sdk` extra adds the Antigravity
SDK, which only the SDK runner needs; `dev` adds it and pytest. To use agyteam
as a library from another project:

```bash
pip install "agyteam @ git+https://github.com/roelle/agy-team.git"           # core
pip install "agyteam[sdk] @ git+https://github.com/roelle/agy-team.git"      # with the SDK runner
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
--chat            talk to the team, one message at a time
--report          who did what, from the record rather than from memory
--cost            spend per agent
--status          who has mail waiting
--distill <agent> have an agent write what it has learned into memory
--cycle <agent>   distil an agent's context into memory, then start fresh
--retro           optional: the team reflects and writes one team norm
--daemon          stay up and react to mail as it arrives
```

Everything durable lives in `~/agy-teams/<team>/`: one directory per agent for
identity and memories, plus the shared bus, roster, reviews and event log.

## The team

| agent | job |
|---|---|
| `manager` | faces you. Decides what "done" means, gates what reaches you, and is accountable for it. Has a shell so she can check claims rather than take them on report. Leads retrospectives. |
| `tpm` | faces the team. Breaks work into pieces one agent can finish, tracks what is outstanding, unblocks people. |
| `coder` | implements, with tests. |
| `syseng` | owns the environment. Reproduces problems and reports the command and output that produced them. |
| `qa` | reviews. Cannot edit source, so findings go back to the author. Keeps a memory of defects that recur here. |

Oversight roles are meant to run a different model family from the
implementers: three reviewers with different blind spots caught three defects
that none of them found alone; difference did that, not capability. The seed
does not pin a model, because model names belong to the runtime (the SDK and
the `agy` CLI know different ones, and an unknown name fails every turn), so
choose them once you know which you are on:

```bash
python -m agyteam.models set manager <your strongest model>
python -m agyteam.models set qa <your strongest model>
```

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

`python -m agyteam.lifecycle status` prints the three flags for the runner you
have configured, with a plain warning for each one it lacks.

## Known limits

Stated here so you do not have to find them:

- **No runner contains an agent that has a shell.** The SDK runner confines
  its file tools to each agent's workspaces, but `run_command` runs with the
  supervisor's own access to the machine: in a measured bench run an agent
  ran `find /tmp -type f`, found the bench checkout, and read the answer key.
  If an agent must not reach something, withhold `run_command` from it or run
  the team in a container or under a separate account. Every runner now
  declares `supports_containment = False`.
- **An agent with a shell can rewrite its own permissions.** They are in
  `roster.json`, and it runs as the user who wrote that file. The supervisor
  puts back a roster changed during a turn and tells you, so a self-granted
  permission does not outlast the turn. It cannot undo what the turn did with
  the permission while it held it, and an agent that forges agyteam's own
  history file as well gets past it. The boundary is an OS account or
  container for the agents that cannot write the team directory.

- **Not validated on a codebase nobody here has seen.** Everything so far has
  been this repo or a problem we already knew the answer to.
- **The review gate runs agent-written code with the bus server's access.**
  `record_review` executes the proof file a reviewer names, outside the
  agent's workspace containment. A proof file can therefore write the team's
  records. Run proofs in a sandbox if your reviewers are not trusted; see
  HANDOFF.md.
- **`--daemon` has no review gate.** "The answer went out unreviewed, send it
  back" happens in `--say` and `--chat`, which run one episode at a time. A
  daemon reacts to mail forever and records no episodes.
- **The CLI runner passes the whole prompt as a command-line argument.** A
  very large prompt (over about 128 KB on Linux) will fail to start, and the
  text is visible in the process list.
- **Only the SDK runner withholds tools and confines agents.** On the others,
  roster policy is refused at the hook and in the team's own servers, which
  holds only as far as the host runs the hook.
- **`HostRunner` has been run against a fake host only** (`evals/fixture_host.py`).
- **Two agents can still write the same file.** There are no file leases.

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
| everything else: the four MCP servers (`mcp_bus`, `mcp_memory`, `mcp_tasks`, `mcp_self`), `supervisor`, `tasks`, `policy`, `hook_pre_tool_use`, `hook_stop`, `runner`, `runner_agy`, `runner_host`, `transport*`, `memory*`, `observer*`, `doctor`, `lifecycle`, … | **stdlib only** | the agy plugin, any MCP host, reactive dispatch |
| `sdk_agent`, `sdk_cli`, `runner_sdk`, `runner_mixed` | `google-antigravity` | SDK agent, CLI, and SDK/mixed runners |

`python -m agyteam.install_check --list` prints the stdlib-only set exactly; it
is derived from the import graph, and the plugin installer copies that list.

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
python -m agyteam.supervisor --say "manager: <task>" # run until idle
python -m agyteam.supervisor --daemon               # stay up, react as mail arrives
python -m agyteam.supervisor --status               # who has mail waiting, whose turn is running (read-only)
```

### Role-scoped tools (why agents collaborate)

Per agent in the roster (`~/agy-teams/<team>/team/roster.json`):
`"tools_off": ["run_command", ...]` disables harness builtins at session build
time on the SDK runner (the tool schema never reaches the model), and
`"workers": false` removes subagent spawning. The default tpm has no shell, no
file writes, and no workers — delegation is its only path to results, which is
enforced by capability, not prose.

- The default team is manager, tpm, coder, syseng and qa. Per-agent `model` is
  set in the roster, and moved without editing it by `python -m agyteam.models`
  (below).
- Each teammate keeps its identity and memory under
  `~/agy-teams/<team>/agents/<name>/`; any of them also runs standalone via
  `agyteam.sdk_cli -w <that directory>`.
- **Teammates vs workers is structural**: teammates can only be *messaged*
  (`send_to_teammate`, logged to the team's `bus.jsonl`); workers can only be
  *spawned* (builtin `start_subagent`, depth-capped at 1) and have no memory or
  bus identity. Agents cannot confuse them because the affordances differ.
- Runaway protection: a hop budget per user stimulus (`--max-hops`), a
  wall-clock ceiling per turn (`AGYTEAM_TURN_TIMEOUT`, default 10 minutes), and
  backoff on an agent whose turns keep failing.
- Shared deliverables go in the shared directory (`python -m agyteam.scope`
  prints where that is).
- **The team directory is the record and no agent is granted it.** Reviews,
  the bus, the roster and the task log are written by agyteam's own processes;
  agents reach them through the MCP servers. `grant_workspace` refuses a path
  that contains it and `agyteam.doctor` fails on one. A file tool or a shell
  command that writes into it is refused for every agent, confined or not.
- **The roster is the operator's.** Every permission an agent has is in
  `roster.json`, so the supervisor keeps the roster it accepted. A change made
  while an agent's turn is running, and not written by agyteam itself
  (`lifecycle`, the roster admin tools), is put back. The rejected version is
  kept as `roster.rejected-<time>.json`, a `roster_reverted` event names the
  agents that were running, and you get a `blocker` message. A change made
  while no turn runs is yours and is adopted. Each write is recorded by hash
  in `roster_history.jsonl`. This came from a test agent given no permissions:
  it was refused an edit, built the roster's path in pieces, granted itself
  access, and finished its task with it. See Known limits for what this
  does not do.

A team can replace a section of the brief, or add to one agent's, without
forking `persona.py`: `<team_dir>/persona/teamwork.md` (or `principal`,
`contract`, `continuity`, `consistency`, `verification`, `accountability`)
replaces that section for everyone; `<team_dir>/persona/coder.md` is appended
to coder's brief. `NORMS.md` stays the place for rules the team adopts in
retrospectives.

**Policy holds outside the SDK too.** `agyteam/policy.py` is one function,
`check_tool_policy(agent, tool, args)`, driven by roster fields and asked from
three places with the same answer: the SDK session's pre-tool hook, a generic
hook any host with hook support can run (`python -m agyteam.hook_pre_tool_use`
reads the call as JSON on stdin and exits 2 with the refusal, and with
`AGYTEAM_AUDIT_LOG` set appends each call to the audit log the review gate
reads), and inside the bus and task servers themselves, so what those servers
do holds on a host with no hooks at all. Per agent: `tools_off` (names, seen
through a host's generic MCP-call tool too), `allowed_send_to` (who it may
message; `user` only if listed), `assigns_tasks: false` (no creating tasks for
others, no hand-offs), `workspaces` (with the team's, the only paths its file
tools may touch — on a hosted UI a file tool outside the workspace opens a
dialog nobody can click and the turn hangs to its timeout), `confined: false`
to opt a platform-maintaining role out of that, `refuse_paths` for the deny
form (a team whose agents roam, but must never touch one checkout; it also
refuses a shell command that names the path, after expanding `~` and `$HOME`
and removing quotes — a text match that stops an agent reaching for the path,
not one that builds it at run time), and
`allowed_mcp_servers` where the host's generic MCP call names its server, and
`max_inbound_bytes` on a recipient that reads only its inbox (a longer message
to it is refused, with a pointer to the shared directory). Every agent's
durable workspace is its own to write: a file tool or a shell command that
writes into a teammate's (a redirect, the destination of `cp`/`mv`, `rm`,
`sed -i`, `tee`, ...) is refused, confined or not; reading one is fine, and
`"policy": {"private_workspaces": false}` turns it off. Team-wide, `"policy": {"detach_commands": [regex...]}` refuses a foreground
run of a command that takes hours unless it is already detached, and
`"forbidden_commands": [{"pattern", "message"}]` refuses outright with that
message. The generic hook accepts the nested `toolCall`/`conversationId`
payload shape too and answers `{"decision": "deny"|"allow", "reason"}`.

## Reactive teamwork (no human polling)

A teammate's message *wakes* the agent it was sent to. Whatever that agent sends
in reply lands in another inbox and wakes them in turn, so one instruction
cascades through the team and comes back to you when it's done. Nobody sits on
unread mail waiting to be prodded — a message from a teammate causes work the
same way a message from you does.

```bash
python -m agyteam.supervisor --say "manager: get X built and verified"   # until idle
python -m agyteam.supervisor --daemon        # stay up, react as mail arrives
python -m agyteam.supervisor --status        # who has mail waiting, whose turn is running (read-only)
```

Verified with real agents (`evals/test_reactive.py`, which uses a three-agent
team of its own): one instruction to the tpm produced seven turns and this
trace, with no inbox ever checked by hand —

```
user->tpm ; tpm->coder ; coder->tpm ; tpm->syseng ; syseng->tpm ; tpm->user
```

The tpm delegated (it has no shell), coder wrote the file, syseng independently
verified by running it, and the confirmation came back to the user unprompted.

**Runners are the third seam**, deliberately parallel to transport and memory —
they decide *how* an agent is woken:

| runner | wakes an agent by | needs |
|---|---|---|
| `agyteam.runner_agy:AgyRunner` (library default) | `agy --conversation <id> -p "<message>"` | the agy CLI |
| `agyteam.runner_sdk:SdkRunner` (what `./run` uses with a Gemini key) | a persistent SDK session per agent | `google-antigravity` |
| `agyteam.runner_mixed:MixedRunner` | per-agent runner from `roster.json` | `google-antigravity` |
| `agyteam.runner_host:HostRunner` | a host's own start / deliver / stop-hook commands, from config | any hosted runtime |
| your own | anything | — |

**`HostRunner` is for a runtime that owns the agent loop** — an IDE or hosted
agent platform whose whole surface is "start a conversation", "deliver a
message", and a hook that runs when a turn ends, with no transcript coming
back. Configure its argv templates, a signal directory the stop hook appends
to, and a model map; see the module docstring. The stop hook itself ships:
point the host's at `python -m agyteam.hook_stop`, which reads the host's
turn-end JSON, takes the signal directory from `AGYTEAM_SIGNAL_DIR` or the
runner config, and writes the line the runner counts (with token counts and
any error). Hosts that also call their stop hook on a pause name the key that
tells the two apart in `AGYTEAM_STOP_IDLE_KEY`. It creates each conversation
with a content-free primer and registers the id *before* delivering the brief,
so no turn ever runs unattributed, and it cancels a turn that outlives its
timeout. Turns the operator takes in the host's own UI are recorded too,
marked `started_by: host`, since the stop hook sees every turn end.
`evals/test_runner_host.py` drives it against a fake host.

**Turns run concurrently.** The supervisor dispatches with the runner's
`begin()`/`poll()` and reaps turns as they end, so four agents with mail take
one turn's time rather than four, and a twelve-minute turn for one agent holds
nobody else up. A runner that only implements `wake()` gets `begin`/`poll` from
the base class (one thread per turn). What is in flight is in
`inflight.json` in the team directory; a restarted supervisor resumes turns the host is still
running and redelivers the mail of turns that died with it. A wake path that
fails is retried with exponential backoff rather than at process speed, every
failure is an event, and three in a row send one message to the manager.
`--inbox-pull` wakes an agent with a one-line summary and lets it read its
mail with `check_inbox`, for hosts that would rather not see every message
body pasted into a prompt. `check_inbox` marks mail read as it returns it,
so during a supervised turn it first writes a receipt
(`receipts/<agent>.jsonl`). If the turn fails, everything it took and that
is no longer waiting goes back once, including mail that arrived mid-turn.
Each message is marked `redelivered`, with what the failed turn had already
sent, so the agent can tell what is done from what is not.

```bash
export AGYTEAM_RUNNER=agyteam.runner_sdk:SdkRunner
export AGYTEAM_RUNNER_CONFIG='{"model":"gemini-3.8-flash"}'
```

The CLI runner is the default when `AGYTEAM_RUNNER` is unset, because it is the
one with no dependencies; it makes a team work *inside Antigravity proper*, so
agents get the harness's tools, policies and subagents, and sessions are
visible to Remote Control. The SDK runner keeps sessions alive between wakes,
supports hooks, records an audit log, confines its file tools (not its shell)
to the agents' workspaces, and enforces `tools_off` at the capability level.

`agyteam.runner_mixed:MixedRunner` enables mixed runtimes where individual agents
can run under different runners configured per agent in `roster.json` (e.g.,
`"runner": "agyteam.runner_agy:AgyRunner"`). The two runtimes trade rather than
rank:
- **SDK runner**: enforces `tools_off` for real (an agent given it reports `NO TOOLS`
  and cannot write a file) and supports compaction/tool hooks.
- **agy CLI runner**: routes through Antigravity's backend to reach models the API
  key cannot (Claude models among them; `python -m agyteam.models available` lists them),
  trading capability-enforced tools for broader model availability.

This allows roles like `qa` to use a frontier model on the CLI while implementers
like `coder` remain on the SDK with capability-enforced roles.

`check_inbox` still exists as a tool — useful mid-task, since a teammate may
answer while you're working — but it is no longer how delivery happens.

**Messages have a kind.** `send_to_teammate(to, content, kind=..., task_id=...)`:
`deliverable`, `question`, `blocker`, `review` and `reminder` wake the
recipient, as does the default `work`; `ack`, `fyi` and `status` are left in
the inbox for the recipient's next wake and wake nobody. One review used to
cost six turns (request, forward, verdict, cc, user, cc); now
`record_review(..., task_id=)` delivers the verdict to the author as one
`review` message, `complete_task` tells the task's creator, and an ack costs
nothing. The tool result says `[queued for X; ...]` and whether a supervisor
is running to deliver it — never "delivered", which a manager once read as
received and waited on eleven times in seven minutes. `list_retros` reads
back what `record_retro` wrote.

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

### Long-running work: tasks, projects, and time-based check-ins

Statuses: `icebox`, `queued`, `claimed`, `running`, `blocked`, `review`,
`done`, `failed`, `cancelled`.
- `icebox` is parked: agents cannot claim it, its reminders wait, and only
  the operator takes it back out.
- `review` is done and waiting on an approved review. `complete_task` still
  needs that review where the task requires one.

A task may name the `channel` it is discussed in (`create_task(...,
channel=...)`, `update_task(..., channel="")` to unlink). The link lives on
the task only, and a status change on a linked task is noted in the channel
as a quiet `status` line. `rank` orders tasks within a board column. The
operator may change any task through `actions.create_task` and
`actions.update_task`. The owner is told: quietly, except when the change
means stop (iced or cancelled while they were on it) or start (handed to
them), which wakes them.

An agent running a sim that takes hours does not need to poll it, and it does
not need to schedule an external cron job to poke itself later either — that
just moves the schedule somewhere nothing here can see it. Instead:

```
create_task(project, title, owner=None, note=None, check_after=None,
            until=None, poll_every=None, deadline=None,
            collaborators=None, requires_review=None)
claim_task(task_id)                                   # take an unowned task
update_task(task_id, status=None, check_after=None, until=None, ...)
complete_task(task_id, note, evidence=None)           # the note is required
list_tasks(project=None, owner=None, status=None, task_id=None)  # newest first
```

`list_tasks`, `list_reviews` and `check_inbox` return at most
`AGYTEAM_TOOL_OUTPUT_BYTES` (default 3,200) inline and end with a count of
what they left out: the `agy` CLI moves a longer tool result into a file, and
a confined agent may not be allowed to read it. A single item longer than
that is cut on a character boundary and says so. `check_inbox` marks read
only the messages it returned, and saves a message too long to show in full
to the agent's own workspace first, naming the file. `list_tasks` notes "in flight with <agent> since
<time>" on a task whose reminder or message started the turn running now.

**Distillation without a token threshold.** `auto_cycle` distills an agent
only once its context passes `AGYTEAM_CYCLE_THRESHOLD`, so a lightly used
agent may never distill. A roster `"nightly_distill": {"hour_utc": 9,
"min_turns_24h": 2, "exclude_models": [...], "exclude_agents": [...]}` has the
daemon distill, once a day after that hour and only while it is idle, every
agent with that many turns in the last day and no turn in flight. The once-a-
day guard is recorded in `events.jsonl`, so it holds across restarts.
`python -m agyteam.supervisor --nightly-distill [--dry-run] [--force]` runs
the same sweep from any scheduler. `--distill-all` and `--cycle-all` run their
turns concurrently. On a host that compacts its own conversations, set
`AGYTEAM_AUTO_CYCLE_MODE=distill_only` (or `"auto_cycle_mode"` in the runner
config) so auto-cycle distills without retiring the conversation, once per
time the context crosses the threshold; `off` disables it.

**Waiting on a sim without spending turns on it.** `until="file_exists:/path"`
(or `file_contains:/path:text`, `pid_exited:1234`) makes the sweep check the
condition itself, every `poll_every` (default 5m), and wake the owner once:
when it holds, or at `deadline` if it never does. Twenty wakes in seventy
minutes to learn "still running" twenty times was the measurement that led
here. Conditions read files and process tables; none runs a command, because
the sweep runs with the supervisor's privileges.

**Closing carries evidence.** `complete_task` requires a closing note (it
replaces the old one — three closed tasks used to still read "actively
triaging"), takes an `evidence` path or review, refuses while a
`collaborators` entry has no recorded activity since the task was created,
and on a `requires_review` task refuses until an approved review names it
(`record_review(..., task_id=)`); such a task's result cannot be sent to the
user either until then. The operator can waive that:
`python -m agyteam.supervisor --waive-review <task_id> --by user --reason ...`
writes a `waiver` row that satisfies the gate and is never counted as a
review. No agent is offered this as a tool, a waiver signed with a teammate's
name is refused, and so is one issued from inside an agent's session. That
stops an agent reaching for the command, not one determined to forge it: any
agent with a shell can run it, on every runner, so add it to
`policy.forbidden_commands` (or withhold `run_command`) where that matters. Status spellings like `in_progress`, `waiting` and `completed` are
accepted and mapped.

Start the sim, then `update_task(task_id, status="running", check_after="2h")`,
and go work on something else. When the time passes, the reminder arrives as an
ordinary message to the task's owner, through the same transport a teammate's
message uses, so it wakes the agent the same way. Several reminders for one
owner arrive as one message. Nothing for a restarted process to lose track of:
what an agent is waiting on is a field on a task in the team's `tasks.jsonl`, not
memory a process held.

`check_after` takes a duration (`90m`, `2h`, `1.5 hours`, `1d`) or an ISO-8601
time **with a zone** (`2026-09-26T05:00:00Z`, `…-07:00`). Anything else is
refused when it is set, rather than stored and never fired. Only a task's owner
can change it; `update_task(owner=...)` hands it on; claims are decided under a
file lock, so of any number of agents claiming at once exactly one wins.

**Something has to sweep.** The supervisor delivers reminders on every pass, so
a team under `--daemon` needs nothing more. A `--say` run that goes idle and
exits, a push transport, or agents driven some other way do not sweep, and
say so: `run_until_idle` reports the reminders it leaves behind, the tool that
sets one says what delivers it, and `agyteam.doctor` warns when reminders are
overdue. On such a host, run the sweep from whatever scheduler it already has
— one entry for the team, not one per agent:

```bash
python -m agyteam.tasks sweep     # deliver what is due, then exit
python -m agyteam.tasks list      # what is tracked, and when each reminder fires
```

A `project` is a grouping string on a task, not a separate object — if
project-level metadata turns out to be needed later, it can be added as a
field the same way `note` was.

## Session lifecycle: cycling and distillation

**Distilling** an agent asks it to write down, with `save_memory`, what it has
learned that is not yet in memory. **Cycling** distills and then retires the
conversation, so the agent's next wake starts fresh with its memories and the
current `NORMS.md`. A distill that fails never retires a conversation: what
the agent learned is still in it. That includes a distill that fails because
the account is out of quota; the log says so.

With a team, it happens:

- **when a conversation grows long**: past `AGYTEAM_CYCLE_THRESHOLD` tokens
  (100,000 by default). `AGYTEAM_AUTO_CYCLE_MODE` is `cycle`, `distill_only`
  (for a host that compacts its own conversations) or `off`;
- **before a model change** on a runtime that cannot switch models inside a
  conversation. The agy CLI can, and keeps the conversation;
- **ahead of an allowance running out**: at the first warning that a model is
  near its weekly `model_allowance`, the agents working on it distill and keep
  their conversations, because a distill needs the model it distills on. Turn
  it off with `"policy": {"distill_before_allowance": false}`;
- **once a day**, if the roster has `nightly_distill`;
- **when you ask**: `--distill <agent>`, `--cycle <agent>`, `--distill-all`,
  `--cycle-all`, `--nightly-distill`. While a supervisor is driving the team,
  these are handed to it and run when the agent is between turns, never
  alongside one; the command waits for the result (up to
  `AGYTEAM_DISTILL_WAIT_S`, 900 s by default) and what is not done by then
  still runs.

A single agent (`agyteam.sdk_cli`) distills on `/quit`, after a one-shot run,
on `/cycle`, and, on a best-effort basis (see quirks), when the harness
reports a compaction. Cycling is cheap by design: memory is on disk, so a
fresh conversation starts with all of it. Let conversations live for days.
The main safety net is not distillation but the contract's save-as-you-go
rule, which evals confirm agents follow unprompted.

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
.venv/bin/python evals/test_memory.py     # the contract, on every store
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
contract passes on it and on the file store, plus loader-safety cases. Misconfiguration exits loudly rather than falling back to local files,
which would strand an agent's learnings where nobody looks.

Any MCP host can mount the server; to give a pinned Antigravity hub conversation
the same memory, register it in `~/.gemini/antigravity/mcp_config.json`:

```json
{"mcpServers": {"agyteam_memory": {
  "command": "/path/to/agy-team/.venv/bin/python",
  "args": ["-m", "agyteam.mcp_memory", "/path/to/agent/workspace"],
  "env": {"PYTHONPATH": "/path/to/agy-team"}}}}
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
python -m agyteam.session manager       # join the conversation the manager works in
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

It mounts four servers: `agyteam_bus` (messaging, reviews, retros),
`agyteam_tasks`, `agyteam_memory` and `agyteam_self` (introspection). Agent
identity is not baked in: each inherits `AGYTEAM_AGENT` from the agy process,
so one install serves every agent on every team.

**Reinstall after every change under `agyteam/`.** Agents on the CLI call the
installed copy, not this repo; `python -m agyteam.doctor` compares the two
file by file and fails when they differ.

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
AGYTEAM_TEAM=team-b python -m agyteam.session manager

# or point at an exact path, ignoring the <root>/<team> layout entirely
AGYTEAM_DURABLE_DIR=~/my-agents/my-agent-team-A python -m agyteam.session manager
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

### Channels, threads and mentions

Besides direct messages, a team can have channels: named rooms of agents
(`<team_dir>/channels.json`; a team without one has an implicit `#general`
with everyone). Agents post with `post_to_channel(channel, content, [kind],
[thread], [task_id])` and see theirs with `list_channels()`.

**A post wakes only the agents it mentions** -- `@coder`, `@here` (the
channel), `@all` (the whole roster, members or not). Every other member gets
a quiet copy that waits in its inbox and is read with everything else unread
the next time that agent is woken for anything. A busy channel costs no
turns, and nobody misses what was said while they were away. Every message
has an `id`; a reply passes `thread=<id>` (direct messages too), and agents
see the ids on what they receive. The post is logged to `bus.jsonl` once,
addressed to `#<channel>`, with its mentions.

A channel created with `everyone` (`actions.create_channel(..., everyone=True)`)
has the whole roster as members and keeps it: an agent added later is in it.
Removing someone from it makes its member list explicit.

A name can drop its team prefix and its case: `@Ada` wakes `at-Ada`, and
`send_to_teammate(to="Ada")` reaches it too, provided exactly one agent is
named `Ada` (ignoring case) or ends in `-Ada`, `_Ada` or `.Ada`. An exact name
always wins; a handle two agents could answer to reaches neither, so add the
prefix. An agent's roster `alias` works the same way, for any nickname, and
clients show it in place of the name. The roster's `operator` names you:

```json
{"operator": {"name": "Matt", "handles": ["roellem"]}, "agents": [...]}
```

Agents are told who that is. `send_to_teammate(to="Matt")` reaches `user`,
and `@Matt` in a channel is recorded as a mention of the user without waking
anyone. You stay `user` on the bus, which the review gate and the episode
logic key on. A name, an alias and an operator handle must each mean one
thing, ignoring case; the roster refuses one that would mean two.

**Archive and delete.** `actions.archive_channel` makes a channel read-only
and takes it out of every agent's `list_channels`. `actions.delete_channel`
also hides its history from clients. The messages stay in `bus.jsonl`,
because that is the team's record, and a deleted name cannot be reused.
`actions.restore_channel` undoes either.

### Building a client: the record and operator actions

[agy-chat](https://github.com/roelle/agy-chat) is a work-chat client built
this way: channels, threads, DMs and agent-to-agent conversations beside live
presence, models and the task board, in a local web app with no build step.


`agyteam.record` reads the team's record for any client -- `Record(team_dir)`
gives messages by channel or conversation, threads and reply counts, tasks,
reviews, events, presence (running, idle, failing, offline), each agent's
model and where it comes from, and search; `Feed(team_dir).poll()` returns
what has been appended since the last poll, never half a line.
`agyteam.actions` is everything the operator can do -- post to a channel or
message an agent as the user (with the person's name as `author`), create
channels and change their members, set or clear a model, waive a review,
stop, start and gracefully restart the team, add, change and remove agents
(through the roster's history, so a running supervisor adopts the change),
replace `NORMS.md`, and read, write and delete an agent's memories -- each
refused from inside an agent's session. A UI built on these two modules
needs nothing else from agyteam.

### Choosing models without editing the roster

The roster's `model` is each agent's default. When the strong model is scarce
and the weaker one is not, move agents between them as the work changes:

```bash
python -m agyteam.models available # what the team's runner offers; a model it lacks is refused
python -m agyteam.models set qa gemini-4-argon-high --until 3d --reason "release week"
python -m agyteam.models set '*' gemini-3.8-flash --until 2026-10-24T00:00:00Z
python -m agyteam.models clear qa
python -m agyteam.models list      # each agent's model, and whether it is the roster's or an override
python -m agyteam.models usage     # tokens per model over the last week
```

A named agent's override beats `*`, and `--until` puts it back by itself.
Every change is a row in `models.jsonl` in the team directory. The supervisor
applies a change — an override, or an edit to the roster's `model` — at the
agent's next idle moment, never mid-turn, as a cycle: the agent distills what
it learned into memory, its conversation is retired, and the next wake starts
on the new model with those memories. (Editing the roster's model on the SDK
runner used to end the session with nothing distilled.) Every runner resolves
the model through the same override, and each turn records the model that ran
it. Overrides are the operator's: they are refused from inside an agent's
session, and no agent is offered a tool for them.

`"policy": {"model_allowance": {"<model>": <tokens per week>}}` in the roster
gives a model a weekly budget; the supervisor tells you once when it passes 80%
and again at 100%. It never switches a model on its own.

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

Per-agent extras (`model`, `tools_off`, `workers`, the policy fields) survive normalisation, and
writes are always canonical — so a mapping-shaped roster **migrates itself** the
first time it's edited. Genuinely ambiguous input is rejected with a readable
message rather than an exception from three frames down: duplicate names, a
mapping key that disagrees with its own `name` field, a list entry with no name,
`user` as an agent name (it's reserved for the human), or a wrong type.

### How a team is run: the roster's `supervisor` block

A team that needs a particular runner or manager says so in its roster, so
the supervisor runs it the same way whoever starts it -- a terminal, a cron
job, or a client's Start button that knows nothing of your environment:

```json
{"agents": [...],
 "supervisor": {"runner": "agyteam.runner_host:HostRunner",
                "runner_config": {"start": "...", "deliver": "..."},
                "manager": "at-Boss",
                "inbox_pull": true}}
```

Every field is optional. The runner is `--runner`, else the roster's, else
`AGYTEAM_RUNNER`, else the agy CLI; `runner_config` goes with the roster's
runner, and an `AGYTEAM_RUNNER_CONFIG` meant for some other runner is not
handed to it. The manager is `--manager`, else the roster's, else detected
from roles. `distill`, `cycle` and `retro` use the same runner, so a cycle
never resets a conversation on a runner that never had it. The heartbeat
(`supervisor.json`) records the runner, the manager and where each came from.

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
.venv/bin/python evals/test_transport.py  # the contract, on every transport
```

Copy `agyteam/transport_template.py` and implement three methods (`send`,
`fetch`, `teammates`); `broadcast`, roster admin, and `close` have working
defaults. **That file is the only thing you should need to write** to move off
the file bus.

Guarantees the contract suite enforces, because they're what the rest of the
system assumes: unknown recipients produce an `[error: ...]` string rather than
vanishing, an acknowledged message is not delivered again (redelivery loops
agents forever), and inboxes are isolated per agent. Misconfiguration fails loudly — a bad
module, malformed spec, bad config JSON, or non-`Transport` class exits with a
message rather than silently falling back to the file bus, which would split the
team across two channels and produce messages that just disappear.

Swappability is verified, not asserted: `evals/fixture_transport.py` is a
SQLite-backed transport written against only the public interface, and the same
contract passes on it and on the file transport.

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
./run test        # every offline test; no key, no CLI, about a minute
.venv/bin/python -m pytest      # the same thing, by hand
```

That is the one to run. A green result means every check passed: the older
suites are scripts that print PASS/FAIL and return a score, and
`evals/conftest.py` fails any of them whose score is short (pytest used to
report those as passed whatever they printed).

It is safe to run beside a working team. `evals/conftest.py` points the
ambient team at a throwaway directory before every test, so an exported
`AGYTEAM_TEAM` is ignored and nothing in the suite can write to your team's
record. (It did not always: one test used to retire a live team's
conversations.)

The script suites also run on their own, which prints each check by name:

| suite | what it proves | checks |
|---|---|---|
| `test_mcp.py` | server wiring, roster shapes/migration, git-root scopes, stdlib purity, team isolation | 42 |
| `test_transport.py` | A2A contract on file + independent SQLite transport, loader safety | 34 |
| `test_memory.py` | memory contract on file + independent SQLite store, loader safety | 64 |
| `test_self.py` | agent introspection server, tool functionality, robust isolation | 22 |
| `test_supervisor.py` | reactive cascade, hop budget, failure isolation, non-destructive status, ack-spiral termination | 51 |
| `test_review.py` | durable review records, approval gate, and supervisor integration | 25 |
| `test_manager.py` | the manager's gate loop, the status report, the seeded roster | 39 |
| `test_observer.py` | event logging, accounting stream, token preservation, and run metrics | 69 |
| `test_cycle.py` | conversation cycle, distill abort safety, frontmatter parsing | 83 |

```bash
.venv/bin/python evals/test_supervisor.py
```

**Live evals call real models and cost money.** None of them runs under
`pytest`. Each builds a team of its own and drops whatever team your shell
exports before it does (three of them used to write their roster into an
exported `AGYTEAM_TEAM_DIR`).

They run real agents that can reach this checkout: as the working directory
on the CLI, as a workspace on the SDK. An agent may decide to "fix" something
it reads here, so run them on a clean tree and look at `git status`
afterwards. (One run edited `agyteam/activity.py` unasked.) `git status` does
not show everything: gitignored directories and the installed copy of the
plugin are within reach too, so finish with
`python -m agyteam.install_check --drift <installed plugin dir>`.

| eval | what it proves | needs | rough cost |
|---|---|---|---|
| `test_delivery.py` | **the product**: one instruction → a file written by one agent and independently verified by another, then reported to you | the configured runner (`agy` CLI by default) | 15–25¢ |
| `test_reactive.py` | real agents woken by teammates, end to end, no human polling | SDK + Gemini key | ~30¢ |
| `test_a2a.py` | real agents delegate, mail crosses processes, memory lands durable | SDK + Gemini key | ~15¢ |
| `run_evals.py` | teach → restart → recall; fabrication probes | SDK + Gemini key | ~8¢ |
| `test_continuity.py` | whether a second wake remembers the first, per runner; a runner that cannot run is reported as not measured | whichever runners are available | 10–20¢ |
| `bench/convergence.py` | whether a retro-and-cycle changes how the team works | see `bench/README.md` | dollars |

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

## What has been run for real, and what has not

- **The CLI path, end to end.** `evals/test_delivery.py` gives one instruction
  to a three-agent team driven through the `agy` CLI with the installed
  plugin: a file is written by one agent, independently verified by another,
  and reported to the user, with no human in the loop. It needs the permission
  setting above.
- **The SDK path** is what the measured cold-start convergence run used
  (`bench/README.md`). It needs a Gemini API key.
- **`HostRunner`** has only been run against the fake host in
  `evals/fixture_host.py`. The first real host will find things the fake did not.
- **Per-agent tool restriction on the CLI.** The SDK withholds a disabled
  tool's schema. The CLI has no documented equivalent, so there `tools_off`
  is enforced only where the host runs `agyteam.hook_pre_tool_use`; without a
  hook it is an instruction, and `my_capabilities` tells the agent which.
- `agy plugin list` reports **"No imported plugins"** for a bundle copied in by
  `plugin/install.sh`, though its servers start and its tools work.
  `agy plugin install <dir>` is the first-class path and may behave differently.

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
- `agyteam`: CLI entry point to the supervisor (`agyteam --chat`, `agyteam --say "manager: <task>"`, `agyteam --status`, etc.)
- `agyteam-lifecycle`: daemon lifecycle management (`agyteam-lifecycle status`, `agyteam-lifecycle stop`, `agyteam-lifecycle start`)

### 2. Direct Module Invocations

You can invoke `agyteam` modules directly with Python:

```bash
# Supervisor & interactive chat
python -m agyteam.supervisor --chat
python -m agyteam.supervisor --say "manager: build feature X"
python -m agyteam.supervisor --status
python -m agyteam.supervisor --daemon

# Standalone single agent REPL / one-shot
python -m agyteam.sdk_cli -w workspace/
python -m agyteam.sdk_cli -p "analyze this log file"

# Background lifecycle manager
python -m agyteam.lifecycle status
python -m agyteam.lifecycle stop
python -m agyteam.lifecycle start

# Preflight, and task reminders on a host with no supervisor daemon
python -m agyteam.doctor
python -m agyteam.tasks sweep
```

These module invocations do not go through `./run`, so nothing chooses a
runner or seeds a roster for you: set `AGYTEAM_RUNNER` (the default is the agy
CLI runner) and seed a team with `bash plugin/install.sh` or `./run ask` once.

### 3. Environment Variables & Swappable Backends

All subsystems support configuration through environment variables:

| Environment Variable | Description | Default |
|---|---|---|
| `GEMINI_API_KEY` | Google Gemini API key for model inference | None (prompted by `./run`) |
| `AGYTEAM_RUNNER` | Agent execution runner (`agyteam.runner_agy:AgyRunner`, `agyteam.runner_sdk:SdkRunner`, `agyteam.runner_mixed:MixedRunner`, `agyteam.runner_host:HostRunner`, or your own) | `agyteam.runner_agy:AgyRunner`; `./run` selects the SDK runner when a Gemini key is configured |
| `AGYTEAM_RUNNER_CONFIG` | JSON configuration passed to runner constructor | `{}` |
| `AGYTEAM_BUS_TRANSPORT` | Pluggable A2A messaging transport class | `agyteam.transport_file:FileTransport` |
| `AGYTEAM_MEMORY_STORE` | Pluggable persistent memory store class | `agyteam.memory_file:FileMemory` |
| `AGYTEAM_OBSERVER` | Pluggable event and cost recorder | `agyteam.observer_file:FileObserver` |
| `AGYTEAM_TEAM` | Team name; selects `<teams root>/<team>` | `default` |
| `AGYTEAM_TEAM_DIR` | Exact team directory (roster, bus, reviews, tasks), overriding the layout | `<durable>/team` |
| `AGYTEAM_DURABLE_DIR` | Absolute path overriding durable storage for agent memory and identities | `~/agy-teams/<team>` |
| `AGYTEAM_TEAMS_ROOT` | Base directory for multi-team namespaces | `~/agy-teams` |
| `AGYTEAM_AUDIT_LOG` | Where every tool call is recorded, by the SDK runner or by `hook_pre_tool_use` on a host; keep it outside all workspaces | unset (nothing recorded) |
| `AGYTEAM_TOOL_OUTPUT_BYTES` | Ceiling on what `list_tasks`, `list_reviews` and `check_inbox` return inline (`0` = none) | `3200` |
| `AGYTEAM_SIGNAL_DIR` / `AGYTEAM_STOP_IDLE_KEY` | For `hook_stop`: where to write turn-end lines (else the runner config's `signal_dir`), and the payload key whose `false` means "paused, not finished" | unset |
| `AGYTEAM_PROOF_PYTHON` | Interpreter the review gate uses to run proof files; must be able to `import pytest` | the repo's `.venv`, then the current interpreter |
| `AGYTEAM_TURN_TIMEOUT` | Wall-clock ceiling on one turn, seconds (`0` = none). SDK runner only: the `agy` CLI and host runners take `"timeout"` in `AGYTEAM_RUNNER_CONFIG` (default `900`) | `600` |
| `AGYTEAM_CYCLE_THRESHOLD` | Input tokens on an agent's latest turn above which it is distilled and given a fresh conversation once the episode ends | `100000` |
| `AGYTEAM_WAKE_BACKOFF_BASE` / `AGYTEAM_WAKE_BACKOFF_MAX` | Seconds to wait before retrying an agent whose turn failed; doubles per failure up to the max | `5` / `300` |
| `AGYTEAM_ESCALATE_AFTER` | Consecutive failed turns before the supervisor tells the manager (or you); remembered across restarts in `escalations.json` | `3` |
| `AGYTEAM_RESET_AFTER_FAILURES` | Consecutive non-transient failures after which an agent's conversation is retired and the next wake starts a new one (`0` = never) | `2` |
| `AGYTEAM_INFLIGHT_STALE_MULTIPLIER` | A turn left in flight by a stopped supervisor is resumed on restart unless it began more than this many turn timeouts ago | `2.0` |
| `AGYTEAM_BUDGET_HALT_PATTERNS` | Comma-separated error text that means the account is out; a failed turn matching one stops the team | `insufficient_quota`, HTTP 402, … |
| `AGYTEAM_AUTO_CYCLE_MODE` | What auto-cycle does past the threshold: `cycle`, `distill_only`, or `off` | `cycle` |
| `AGYTEAM_MODEL_CHECK` | `0` stops agyteam asking the runtime which models it offers (`agy models`, the SDK's model list), so model choices are not checked against it | on |
| `AGYTEAM_WAKE_PROMPT_BYTES` | Budget for messages pasted into a wake prompt; waking ones always go in, quiet ones while they fit, the rest stay unread | `24000` |
| `AGYTEAM_NIGHTLY_DISTILL_HOUR_UTC` | Enables the daily distill sweep at this hour when the roster has no `nightly_distill` | unset |
| `AGYTEAM_PROJECT_DIR` / `AGYTEAM_SHARED_DIR` | The project root and the directory for deliverables agents hand each other | the git root / `<project>/.agy-team-shared` |
| `AGYTEAM_EXTRA_WORKSPACES` | Extra directories (path-separated) every SDK agent may read: an observation grant, for transcripts and the like | unset |
| `AGYTEAM_RETRY_ATTEMPTS` / `AGYTEAM_RETRY_BASE` | Tries, and base seconds of jittered backoff, for a model call that fails transiently | `4` / `5` |
| `AGYTEAM_OBSERVER_CONFIG` | JSON handed to the observer named by `AGYTEAM_OBSERVER` | unset |
| `AGYTEAM_MIXED_DEFAULT` | Runner for agents with no `runner` of their own under `runner_mixed` | the SDK runner |
| `AGYTEAM_CONVERSATION_DIR` | Where SDK sessions are stored: `cli`, `ide`, or a path | `cli` |
| `AGYTEAM_PLUGIN_DIR` | The installed plugin copy `install_check` compares against | the `agy` CLI's plugin directory |
| `AGYTEAM_NO_API_KEY` | `1` tells `./run` you supply model access another way: it stops asking for a Gemini key and does not switch to the SDK runner on its own | unset |

The remaining tuning knobs (compaction, anomaly, retro and loop limits) are in
`agyteam/config.py`, each with the measurement that set its default.

### 4. Direct Testing & Verification

Run tests with the project's own interpreter; no `PYTHONPATH` export is
needed. A `pytest` from outside the venv will fail to import
`google.antigravity`, which a handful of the offline tests build sessions with
(they never call a model).

```bash
# Run the entire offline test suite (same as ./run test)
.venv/bin/python -m pytest

# Run specific subsystem contract suites
.venv/bin/python -m pytest evals/test_mcp.py
.venv/bin/python -m pytest evals/test_transport.py
.venv/bin/python -m pytest evals/test_memory.py
.venv/bin/python -m pytest evals/test_supervisor.py
.venv/bin/python -m pytest evals/test_review.py
```


## Versioning

Releases follow [Semantic Versioning](https://semver.org/), tagged
`vX.Y.Z`, with notes in [CHANGELOG.md](CHANGELOG.md); how a release is cut is
in [RELEASING.md](RELEASING.md). Pin a tag rather than `main`:

```bash
pip install "agyteam @ git+https://github.com/roelle/agy-team.git@v3.7.1"
python -m agyteam.supervisor --version
```

The version promises this surface, and only this:

- **What agents call**: the MCP tools of the bus, memory, introspection and
  tasks servers -- their names, arguments and result conventions.
- **What operators run**: the command lines of `agyteam.supervisor`,
  `agyteam.models`, `agyteam.tasks`, `agyteam.lifecycle`, `agyteam.doctor`,
  `agyteam.install_check`, and the environment variables documented above.
- **What hosts and clients depend on**: the roster schema; the team
  directory's record (`bus.jsonl`, `tasks.jsonl`, `reviews.jsonl`,
  `events.jsonl`, `channels.json`, `models.jsonl`); `agyteam.record` and
  `agyteam.actions`; the `Runner`, `Transport`, memory and observer seams; and
  the payloads `hook_pre_tool_use` and `hook_stop` accept and emit.

Not covered: other modules' internals, the wording of prompts and briefs
given to agents, `evals/` and `bench/`.

## License

MIT. See [LICENSE](LICENSE).
