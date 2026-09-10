# Agy-Team

Persistent, learning agents — and a team platform of them — for Google
Antigravity 2.0: as an installable **agy CLI plugin**, or driven directly
through the **google-antigravity SDK**.

## Setup

The plugin path needs **nothing installed** — its MCP servers are pure standard
library and run under system `python3` (see [Dependencies](#dependencies)):

```bash
bash plugin/install.sh
```

The SDK and eval paths need a virtualenv:

```bash
python3 -m venv .venv
.venv/bin/pip install google-genai google-antigravity
cp .env.example .env   # or create .env with GEMINI_API_KEY=...
```

## Provenance of claims about Antigravity

This README asserts a few things about Antigravity's internals that aren't in
the product documentation. All of them came from two public sources, and each is
reproducible:

1. **The shipped SDK source.** `google-antigravity` is on PyPI under Apache-2.0;
   its Python source installs into your virtualenv. Statements like "`PreTurnHook`
   is decide-only, it cannot inject context", "`compaction_threshold` maps to a
   hard `max_token_limit`", and the `mcp_config.json` field list are from reading
   that installed package (`.venv/lib/python3.12/site-packages/google/antigravity/`).
2. **Observed behavior on this machine**, running the SDK against a personal
   Gemini API key. The compaction-without-event note, the `create_file` "brain"
   artifact-directory quirk, and token/cost figures are empirical, and the tests
   that produced them are in `evals/`.

Feature-availability claims ("no public peer-to-peer messaging", "Teamwork
coordinates through workspace artifacts", "remote harness is roadmap") are from
the public docs at antigravity.google, checked 2026-09-09. Nothing here is
derived from non-public information, and no design decision below encodes
knowledge of any unreleased or internal capability — see the note on the
transport seam in [A2A messaging](#a2a-messaging-swappable-transport).

## Dependencies

Third-party imports are confined to the modules that actually talk to a model,
so the installable surface stays dependency-free:

| module | needs | used by |
|---|---|---|
| `scope`, `store`, `roster`, `mcp_base`, `mcp_memory`, `mcp_bus`, `transport*`, `memory*`, `runner`, `runner_agy`, `supervisor` | **stdlib only** | the agy plugin, any MCP host, reactive dispatch |
| `tools`, `llm`, `agent`, `cli` | `google-genai` | clean-room agent (cheap eval rig) |
| `sdk_agent`, `sdk_cli`, `team`, `runner_sdk` | `google-antigravity` | SDK agent + Python team orchestrator |

`store.py` holds the tool implementations and memory store; `tools.py` holds
only the Gemini function declarations for them. That split is what keeps the
MCP servers importable without a virtualenv, and `evals/test_mcp.py` enforces
it — including a control case asserting that `llm.py` *does* fail on bare
python, so the check can't silently stop discriminating.

## Single agent (priority zero)

```bash
# SDK/localharness-backed agent (the Antigravity one):
.venv/bin/python -m agyteam.sdk_cli                 # REPL
.venv/bin/python -m agyteam.sdk_cli -p "task..."    # one-shot

# Lightweight clean-room agent (same memory design, direct Gemini API,
# ~4-5x cheaper per turn; useful for cheap experiments):
.venv/bin/python -m agyteam                         # REPL / -p one-shot
```

Shared flags: `-w WORKSPACE` (default `workspace/`), `-m MODEL`
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
- A fixed grounding contract (in `agyteam/agent.py:CONTRACT`, not agent-editable)
  requires claims to trace to tool output or memory, and makes "I don't know"
  an acceptable answer.

## Team platform

```bash
.venv/bin/python -m agyteam.team                    # REPL
.venv/bin/python -m agyteam.team --say "tpm: <task>" # one-shot until idle
```

REPL commands: `say <agent> <msg>`, `broadcast <msg>`, `log [n]`, `roster`,
`add <name> <role>`, `remove <name>`, `cycle <agent>`, `distill [agent]`,
`usage`, `quit`.

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
| `agyteam.runner_agy:AgyRunner` (default) | `agy --agent <name> -p "<message>"` | the agy CLI |
| `agyteam.runner_sdk:SdkRunner` | a persistent SDK session per agent | `google-antigravity` |
| your own | anything | — |

```bash
export AGYTEAM_RUNNER=agyteam.runner_sdk:SdkRunner
export AGYTEAM_RUNNER_CONFIG='{"model":"gemini-3.8-flash"}'
```

The CLI runner is the one that makes a team work *inside Antigravity proper*:
agents are agy custom agents, so they get the harness's tools, policies, and
subagents, and the sessions are visible to Remote Control. The SDK runner keeps
sessions alive between wakes, so an agent woken five times has one continuous
context rather than five cold starts.

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
way. A transport backed by a system that can push should override `watch()`
semantics in its own `fetch`/`peek` implementation and the interval disappears.
On the SDK path the same effect is available natively through triggers
(`google.antigravity.triggers.on_file_change` / `every`), which push straight
into a live session.

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
.venv/bin/python evals/test_memory.py            # must pass 14/14
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

Remote Control drives **agy CLI sessions**, not SDK programs — so the
installable artifact is a CLI plugin, and that is what `plugin/` contains:

```
plugin/agy-team/
  plugin.json          manifest
  agents/{tpm,coder,syseng}/agent.md    peer roles (markdown + frontmatter)
  skills/{distill,inbox,handoff}.md     slash commands
  rules/grounding.md   grounding + learning + collaboration contract
  mcp_config.json      rendered at install time with absolute paths
```

Install and run:

```bash
bash plugin/install.sh                  # → ~/.gemini/antigravity-cli/plugins/agy-team
AGYTEAM_AGENT=tpm agy --agent tpm       # identity must match --agent
```

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
AGYTEAM_TEAM=team-b AGYTEAM_AGENT=tpm agy --agent tpm

# or point at an exact path, ignoring the <root>/<team> layout entirely
AGYTEAM_DURABLE_DIR=~/my-agents/my-agent-team-A AGYTEAM_AGENT=tpm agy --agent tpm
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
.venv/bin/python evals/test_transport.py            # must pass 12/12
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
.venv/bin/python evals/test_supervisor.py             # reactive dispatch, free
.venv/bin/python evals/test_reactive.py               # real reactive team, ~30c
.venv/bin/python evals/run_evals.py                   # clean-room agent
AGYTEAM_IMPL=agyteam.sdk_cli .venv/bin/python evals/run_evals.py       # SDK agent
AGYTEAM_IMPL=agyteam.sdk_cli AGYTEAM_EXTRA_ARGS=--mcp \
  .venv/bin/python evals/run_evals.py                 # SDK agent, memory via MCP
.venv/bin/python evals/test_a2a.py                    # A2A behavioral, ~15¢
```

Current status — all green as of 2026-09-09:

| suite | what it proves | score |
|---|---|---|
| `test_mcp.py` | server wiring, roster shapes/migration, git-root scopes, stdlib purity, team isolation | 40/40 |
| `test_transport.py` | A2A contract on file + independent SQLite transport, loader safety | 28/28 |
| `test_memory.py` | memory contract on file + independent SQLite store, loader safety | 32/32 |
| `test_supervisor.py` | reactive cascade, hop budget, failure isolation, non-destructive status, ack-spiral termination | 23/23 |
| `test_reactive.py` | real agents woken by teammates, end to end, no human polling | 7/7 |
| `run_evals.py` | teach→restart→recall ×3; fabrication probes ×3 | 6/6 on all three paths |
| `test_a2a.py` | real agents delegate, mail crosses processes, memory lands durable | 8/8 |

Typical cost: free, ~2¢ clean-room, ~8¢ SDK, ~15¢ A2A.

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
