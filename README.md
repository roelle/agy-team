# Clawy-AGY

An OpenClaw-style persistent, learning agent — and a team platform of them —
on the **google-antigravity SDK** (Antigravity 2.0 / Gemini backend).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install google-genai google-antigravity
cp .env.example .env   # or create .env with GEMINI_API_KEY=...
```

## Single agent (priority zero)

```bash
# SDK/localharness-backed agent (the Antigravity one):
.venv/bin/python -m clawagy.sdk_cli                 # REPL
.venv/bin/python -m clawagy.sdk_cli -p "task..."    # one-shot

# Lightweight clean-room agent (same memory design, direct Gemini API,
# ~4-5x cheaper per turn; useful for cheap experiments):
.venv/bin/python -m clawagy                         # REPL / -p one-shot
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
- A fixed grounding contract (in `clawagy/agent.py:CONTRACT`, not agent-editable)
  requires claims to trace to tool output or memory, and makes "I don't know"
  an acceptable answer.

## Team platform

```bash
.venv/bin/python -m clawagy.team                    # REPL
.venv/bin/python -m clawagy.team --say "tpm: <task>" # one-shot until idle
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
  `clawagy.sdk_cli -w team/agents/<name>`.
- **Teammates vs workers is structural**: teammates can only be *messaged*
  (`send_to_teammate`, persisted to `team/bus.jsonl`); workers can only be
  *spawned* (builtin `start_subagent`, depth-capped at 1) and have no memory or
  bus identity. Agents cannot confuse them because the affordances differ.
- Runaway protection: `MAX_HOPS` agent turns per user stimulus, then the
  scheduler pauses; each agent session has a model-call budget; `quit` distills
  every agent's learnings to memory first.
- Shared deliverables go in `team/shared/`.

## Session lifecycle: cycling and distillation

Distillation (sweeping unsaved learnings into memory files) fires on `/quit`,
after one-shot runs, on `/cycle` (single agent) / `cycle <agent>` (team), and —
wired but see quirks — when the harness reports a compaction event. Cycling is
cheap by design: memory is on disk, so a fresh session reboots with the full
index. Let sessions live for days; cycle weekly-ish or when a session feels
off. Primary safety net is the contract's save-as-you-go rule, which evals
confirm fires unprompted.

## Memory over MCP (optional, not a fork)

`clawagy/mcp_memory.py` is a zero-dependency MCP stdio server exposing the same
workspace memory (`save_memory`/`read_memory`/`delete_memory`/`memory_index`).
`--mcp` on `clawagy.sdk_cli` routes memory through it instead of in-process
callables — same files, same evals (6/6 verified). Any MCP host can mount it;
to give a pinned Antigravity hub conversation the same memory, register it in
`~/.gemini/antigravity/mcp_config.json`:

```json
{"mcpServers": {"clawagy_memory": {
  "command": "/mnt/data/claw-agy/.venv/bin/python",
  "args": ["-m", "clawagy.mcp_memory", "/mnt/data/claw-agy/workspace"],
  "env": {"PYTHONPATH": "/mnt/data/claw-agy"}}}}
```

## agy CLI plugin (the installable form)

Remote Control drives **agy CLI sessions**, not SDK programs — so the
installable artifact is a CLI plugin, and that is what `plugin/` contains:

```
plugin/clawagy/
  plugin.json          manifest
  agents/{tpm,coder,syseng}/agent.md    peer roles (markdown + frontmatter)
  skills/{distill,inbox,handoff}.md     slash commands
  rules/grounding.md   grounding + learning + collaboration contract
  mcp_config.json      rendered at install time with absolute paths
```

Install and run:

```bash
bash plugin/install.sh                  # → ~/.gemini/antigravity-cli/plugins/clawagy
CLAWAGY_AGENT=tpm agy --agent tpm       # identity must match --agent
```

The installer renders real paths into `mcp_config.json` (env-var expansion in
that file is undocumented, so nothing depends on it) and seeds the durable team
roster. Agent identity is *not* baked in: both MCP servers inherit
`CLAWAGY_AGENT` from the agy process, so one install serves every agent.

### File scopes

`clawagy/scope.py` defines three classes of path, each overridable by env var,
`scopes.json` in the project, or argument — run `python -m clawagy.scope` to see
what resolves where:

| scope | holds | default |
|---|---|---|
| durable | identity + memory (survives every project) | `~/.gemini/clawagy` |
| project | the repo or mapped drive being worked in | `$PWD` |
| shared | team artifacts and deliverables | `<project>/.clawagy-team` |

Memory is forced into durable scope, so an agent that moves between repos (or
works on a network mount) keeps what it learned and never strands memory in
someone else's checkout.

### A2A messaging

Antigravity has no native peer messaging — its Teamwork mode coordinates through
workspace artifacts, and the SDK offers only subagents. `clawagy/mcp_bus.py`
supplies the missing channel over MCP, so it works identically in the agy CLI,
the desktop hub, and SDK agents. Delivery is inbox-based (`check_inbox`) rather
than push, because when the harness owns the loop nobody can force a peer to
take a turn.

## Evals (run these after changes)

```bash
.venv/bin/python evals/test_mcp.py                    # 22 protocol/scope tests, free
.venv/bin/python evals/run_evals.py                   # clean-room agent
CLAWAGY_IMPL=clawagy.sdk_cli .venv/bin/python evals/run_evals.py       # SDK agent
CLAWAGY_IMPL=clawagy.sdk_cli CLAWAGY_EXTRA_ARGS=--mcp \
  .venv/bin/python evals/run_evals.py                 # SDK agent, memory via MCP
.venv/bin/python evals/test_a2a.py                    # A2A behavioral, ~15¢
```

Current status — all green as of 2026-09-09:

| suite | what it proves | score |
|---|---|---|
| `test_mcp.py` | MCP protocol, memory semantics, bus delivery, scope resolution | 22/22 |
| `run_evals.py` | teach→restart→recall ×3; fabrication probes ×3 | 6/6 on all three paths |
| `test_a2a.py` | real agents delegate, mail crosses processes, memory lands durable | 8/8 |

Typical cost: free, ~2¢ clean-room, ~8¢ SDK, ~15¢ A2A.

## Unverified: the plugin install step

Everything the plugin *does* at runtime is tested (`test_a2a.py` mounts the same
modules with the same env-derived identity and passes 8/8). What is **not**
verified is `agy` itself loading the bundle — the CLI is not installed on this
machine, so the manifest/agents/skills layout follows the published docs but has
never been loaded by the real thing. Expect to adjust:

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
  limit; the compaction threshold (80k, `clawagy/config.py`) respects it.
- Pricing table in `clawagy/config.py` is an estimate for the cost display —
  update from ai.google.dev/pricing if you need it exact.
