# Changelog

All notable changes to agy-team. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) from 3.0.0 on; see "Versioning" in
the README for what counts as the public surface.

## [Unreleased]

## [3.15.0] - 2026-10-29

### Changed
- **A running team's hop budget is a rate.** It allows `--max-hops` wakes
  in any `AGYTEAM_HOP_WINDOW` seconds (default 600). It used to reset only
  once no turn was in flight, which a busy team, or one long turn, may
  never reach. Until it did, no agent woke, and nothing said why. A
  one-shot episode still counts its whole run.
- **The user's messages are never held by the hop budget.** The budget
  bounds agents waking agents. A message from the user is a new stimulus,
  so it wakes its recipient even when the budget is spent.

### Added
- **`held` presence.** `Record.presence()` reports `held` for an agent
  whose waking mail the hop budget is holding back, with how much of the
  budget is used. The supervisor's heartbeat carries `held` and `hops`, and
  logs once when it starts holding mail.

## [3.14.0] - 2026-10-29

### Added
- **`Record.recall_stats(agent)`**: how recall has gone for an agent, from
  its `recall` and `recall_miss` events. Per day for the last two weeks:
  wakes, wakes offered memories, memories offered, misses, judge errors.
  Per memory, over all time: times offered, hinted and missed. Also the
  last ten misses, with the correction and what was learned. agy-chat
  draws it on its Memories page.

### Fixed
- **A reply to a reply disappeared.** An agent answering the operator's
  reply in a thread named that reply's id, as the tools invited. Stored
  as is, it hung off a message no client shows a thread under. It reached
  neither the channel nor the thread, and the agent believed it had
  answered. A reply now goes under its thread's first message, whichever
  message of the thread it names: channel posts, direct messages,
  agents' and the operator's alike. The record shows replies already
  stored under a reply in their thread too, so lost answers reappear. The
  `thread` parameter of `send_to_teammate` and `post_to_channel` now says
  that any message in the thread will do.

## [3.13.0] - 2026-10-29

### Added
- **Recall: an agent's own memories are put in front of it, per turn.**
  Agents saved good memories and did not read them back. Now, before a
  turn, the incoming mail is the query: candidates come from embeddings
  (with `GEMINI_API_KEY`) or from words and cues (without one). A Flash
  judge, through the API or the agy CLI, picks at most three that apply.
  They come with the wake as "You learned this N days ago (id): ...".
  Without a judge, only names are offered. Recall prepares off the dispatch
  loop and never holds up another agent. A correction that restates a
  memory the agent was not shown is a `recall_miss`, and teaches that
  memory the missed request's words. Off by default: `"memory_recall": true`
  in the roster. See "Recall" in the README and `bench/recall/`, which
  measured it on real memories with up to 10,000 distractors.
- `bench/recall/`: the recall benchmark. Its data stays outside the
  repository.

### Fixed
- **`./run` with a stale virtual environment.** After the system Python was
  upgraded (3.12 to 3.14), `.venv/bin/python` pointed at nothing, and `./run`
  failed. It now notices and rebuilds the environment.

## [3.12.0] - 2026-10-28

### Changed
- **Distills asked of a running supervisor run in parallel, alongside the
  team's work.** `--distill-all` or `--cycle-all` with a daemon running used
  to distill one agent after another, and the daemon's dispatch loop waited
  on each, so no other turn could start. Each requested distill or cycle now
  starts as a turn of its own when its agent is between turns, and is
  recorded when it ends. If the supervisor restarts mid-distill on a runtime
  that cannot resume turns, the request goes back on the queue, so the
  command waiting for it still gets an answer.

## [3.11.1] - 2026-10-27

### Fixed
- **An @mention in a direct message reached nobody.** A DM, or a reply in a
  thread under one, went only to the agent it was addressed to; `@ada can you
  help?` in it did not wake Ada, though the same words in a channel did.
  `actions.dm` now gives each agent it mentions (by name, alias, any case, or
  past a team prefix) a copy, which wakes it, saying it was mentioned in the
  operator's message to the other agent. Group words (`@all`, `@here`) do
  nothing in a DM.

## [3.11.0] - 2026-10-27

### Added
- **The task tree.** Tasks have a `parent` (one each, cycles refused), a
  `level` (set, or worked out from what is under it; names from roster
  `task_levels`, default task/project/program/portfolio), a `priority`
  (P0-P4), `auto_close`, and `archived` (the operator's; it hides a whole
  branch). `tasks.tree()` and `Record.task_tree()` give each task's path,
  closed/total under it, and tokens over its subtree.
- When a parent's last open child closes, its owner (or the operator) is
  told once, or, with `auto_close`, the parent closes itself (not over a
  failed child) and the check climbs.
- `create_task(parent, level, priority, auto_close)`. With no parent, a new
  task goes under the one task the agent is working on, if there is exactly
  one, and the result says so. `update_task` takes the same fields;
  `list_tasks(under=<id>)` shows a branch as a tree and hides archived
  branches unless asked.
- A wake about a task includes what it is part of, and the top task's note.
- The brief has a short section on how work is organised.
- `actions.migrate_project_labels`: each project label becomes a level-1
  parent task, its tasks under it; all-closed labels become closed, archived
  parents. `actions.create_task` and `update_task` take the tree fields.

### Changed
- `project` is optional on `create_task` (a subtask takes its parent's).
  Existing callers that pass it are unaffected.

## [3.10.1] - 2026-10-27

### Fixed
- **`Record.task_usage` counted no inferred turns on real teams.** Tasks are
  stamped in UTC ISO and turns in local time, and the two were compared as
  text. The 3.9.0 memory-review evidence compared failed tasks the same
  way. Both now compare instants (`agyteam.record.instant`, which reads
  either kind).
- **An alias set through `actions.update_agent` was never used.** It wrote an
  `aliases` list, but the roster's field is `alias`, a single name the agent
  is shown by and answers to as @alias. `AGENT_FIELDS` now has `alias`.

## [3.10.0] - 2026-10-27

### Added
- **Tokens per task**: `Record.task_usage()`. A turn woken by a message
  naming tasks counts toward them ("direct"). Any other turn counts toward
  the task its agent owned and was working (claimed, running, review) when
  the turn ended ("inferred"). Each task also gets a per-agent breakdown.
- **Bookmarks**: on channels (`actions.set_channel_bookmarks`, kept in
  `channels.json`) and on tasks (`links`, `actions.set_task_links`), each a
  list of `{title, url}`.
- **Pictures** for agents and the operator: `actions.set_avatar`,
  `Record.avatars()`, files in `<team_dir>/avatars/<name>.<ext>`.
- **Attachments**: `actions.attach` keeps a file the operator shares in
  `<team_dir>/attachments/` and returns its absolute path for the message.

## [3.9.0] - 2026-10-23

### Added
- **`memory_model` / `memory_effort`** (roster, or one agent's entry): run
  distill, memory review and retro turns on a stronger model while work
  turns stay on the agent's own. It needs a runtime that switches models
  inside a conversation (the agy CLI does); otherwise memory turns run on the
  agent's model and the supervisor says so once. A model the runner does not
  offer is skipped. Runners gain `set_turn_model` / `clear_turn_model`,
  honoured by `resolved_model`. `actions.update_agent` and `add_agent` take
  `memory_model` and `memory_effort`.
- **Memory reviews see outcomes**: reviews that asked the agent for changes,
  its failed or cancelled tasks, and what the operator told it since its
  last review. Failed turns are left out, as runtime noise.

### Changed
- Distill and review prompts, the brief, and `save_memory` / `merge_memories`
  descriptions ask for lessons that carry over to the next task rather than
  values true only for this one, and for descriptions that say when a memory
  applies. A review treats a never-read memory as possibly unfindable and
  fixes its description first. From a memory-writing benchmark (Letta's
  FrontierMemory): memories that recorded one task's file sizes did not
  transfer, and one lesson split four ways was never found.

## [3.8.0] - 2026-10-22

### Added
- **Memory history**: the memory server logs every save, read, delete and
  merge to `memory_log.jsonl`, whatever the store. `Record.memory_stats(agent)`
  gives age, revisions, reads and last read; for history from before the
  log, it counts the event log's `read_memory` calls.
- **`merge_memories`** (a memory tool): replaces several memories with one
  the agent writes, archives the sources, and keeps their history.
- **Memory archive**: `delete_memory` and merges copy what they remove to
  `memory_archive/<agent>/`. `actions.archived_memories` and
  `actions.restore_memory` list and restore them.
- **Memory review turns**: an agent goes over its own memories with their
  history and `review_memory`'s flags, and merges, rewrites and deletes. The
  daemon queues one at an agent's first 10 memories, at 25/50/100/200/400/800,
  and weekly after changes, and runs it between the agent's turns.
  `--review-memory`, `--review-memory-all` and `actions.review_memory` ask
  for one. Each is recorded as a `memory_review` event. Configure with
  roster `memory_review` (or `false`).
- **Norm candidates**: `Record.norm_candidates()` finds the same lesson in
  three or more agents' memories, and retros are shown them.
- `Record.memory_archive(agent)`; `memory_log.open_store(team_dir, agent)`.

### Fixed
- The operator's memory actions opened an agent's store by the default
  layout. Where `AGYTEAM_DURABLE_DIR` puts agents elsewhere, they edited
  memories the agent never reads. They now open the store the agent's own
  server does.

## [3.7.1] - 2026-10-20

### Fixed
- The implicit `#general` (a team with no `channels.json`) was written down
  as a fixed member list the first time any channel changed, so agents added
  after that were not in it. It is now saved as an `everyone` channel and
  keeps following the roster.

## [3.7.0] - 2026-10-20

### Added
- **Graceful restart.** A restart request (`./run restart`,
  `python -m agyteam.supervisor --restart`, `actions.restart`, or SIGHUP)
  makes the driving supervisor drain: it starts no new turns, mail keeps
  queuing, and the turns in flight finish. Then a daemon restarts itself in
  place (same pid, arguments and environment) on the installed code.
  `--now` cancels the turns in flight instead, and their mail goes back. The
  heartbeat now records `version`, `started`, `restartable` and `draining`.
- **`python -m agyteam.update` / `./run update`**: one command that updates
  agy-team where it is installed (a git checkout fast-forwards, a pip
  install from git moves to the newest release tag), reinstalls the plugin,
  and gracefully restarts every running team. For a supervisor from before
  graceful restart, it waits for a moment with no turn in flight, then stops
  and starts it. `--check`, `--to`, `--now`, `--no-code`, `--no-restart`,
  `--no-wait`, `--team-dir`, `--json`.
- **Channels that follow the roster**: `everyone=True` on `create_channel`
  and `set_members`. An agent added later is a member without anyone adding
  it.
- **Admin actions** for clients: `add_agent` (which joins every open channel
  by default), `update_agent` (role, model, effort, principal, tools_off,
  aliases), `remove_agent` (which also removes it from channels), `norms` /
  `set_norms` (recorded as a `norms_edited` event), and `memories` /
  `save_memory` / `delete_memory` for one agent's store, opened for that
  team. Roster edits go through the roster's history, so a running
  supervisor adopts them instead of putting them back.

### Changed
- README: `./run stop` cancels the turns in flight; to keep them, use
  restart or update.

## [3.6.0] - 2026-10-20

### Added
- **Distills asked from outside go through the driving supervisor.**
  `--distill`, `--cycle`, `--distill-all`, `--cycle-all` and
  `--nightly-distill` (and `agyteam.supervisor.distill`/`cycle`) queue the
  request (`distill_requests.jsonl`) when another supervisor drives the
  team. That supervisor runs it when the agent is between turns and records
  `distill_request_done`. The command waits for the result, up to
  `AGYTEAM_DISTILL_WAIT_S` (900 s); anything not done by then still runs.
- **Distill ahead of an allowance running out.** At the first warning that a
  model is near its weekly `model_allowance`, the agents working on it
  distill and keep their conversations, while the model can still run a
  distill. Turn it off with roster `"policy": {"distill_before_allowance": false}`.
- `boolean` arguments in the tool servers, with `mcp_base.flag` and
  `mcp_base.check_args`.

### Changed
- `create_task`'s `requires_review` is declared a boolean. The words for one
  (`"true"`, `"no"`, ...) are still accepted.
- Tool arguments are checked against their declared types before a handler
  sees them. A number or boolean sent for a text field becomes its text,
  rather than being dropped as if it were absent.

### Fixed
- **`--distill` ran into a live turn.** While a daemon drove the team, it
  woke the agent in a second process alongside the daemon's turn, because it
  checked only its own process's turns. `distill` also never checked for a
  turn in flight at all; now it skips the agent.
- **`requires_review: true` made an ungated task.** A JSON boolean, or a typo
  like `"ttur"`, was read as false without a word. Now `true` is true, and
  anything that is not a yes or a no is an error the agent sees.
- A distill that fails because the account is out of quota says so: the
  conversation is kept, so nothing is lost, and a `distill_deferred` event
  records it.
- README: training is described as it works. Most of it is conversation that
  lands in each agent's memories; a retro writes only norms and is optional.
  The distillation triggers are listed in full. Model examples no longer use
  names the CLI refuses.
- The brief no longer tells agents their memory index is "below"; they read
  it with `memory_index`.

## [3.5.0] - 2026-10-20

### Added
- **Channels can be archived, deleted and restored**
  (`actions.archive_channel` / `delete_channel` / `restore_channel`,
  `channels.archive` / `delete` / `restore`).
  - Archived: read-only, and out of every agent's `list_channels` and
    channel membership.
  - Deleted: archived, and hidden from clients (`Record.search` skips it).
    Its messages stay in `bus.jsonl`, the team's record, and the name cannot
    be reused until it is restored.
  - `channels.active()` lists the open ones.
- **Tasks name the channel they are discussed in** (`channel=` on
  `create_task` / `update_task`, the tools included; `""` unlinks). A status
  change on a linked task is noted in that channel as a quiet `status`
  line.
- **Board statuses.** `icebox` is parked work: not claimable, reminders
  paused, and only the operator takes it back out. `review` means done and
  waiting on an approved review; `complete_task` still requires the review.
  Common spellings map to them (`backlog`, `in_review`, ...). `rank` orders a
  task within a column.
- **The operator moves any card**: `actions.create_task` and
  `actions.update_task` (status, owner, title, project, note, channel,
  deadline, rank); `tasks.update(by="user")` is no longer limited to unowned
  tasks. The owner is told, waking them when they should stop (iced or
  cancelled mid-work) or start (handed the task).
- **Aliases.** An agent's roster `alias` resolves wherever a name is taken
  (`send_to_teammate`, @mentions), is shown in the brief and in
  `list_teammates`, and is for clients to display. `roster.aliases()`, and
  `resolve_name(..., aliases)`.
- **The operator's handle.** The roster's `operator: {"name", "handles"}`
  tells agents who the user is. `send_to_teammate(to="Matt")` reaches the
  user, and `@Matt` in a channel is recorded in the post's `mentions` as
  `user` without waking anyone. The user stays `user` on the bus.
  `Record.operator()`.

### Changed
- The roster refuses a name, alias or operator handle that would mean two
  things (ignoring case), and an alias that is not a handle.
- @mentions may contain a dot inside the name (`@a.b`), not at its end.

## [3.4.0] - 2026-10-20

### Added
- `runner.team_runner(team_dir, runner_flag=None)` -> `(spec, config,
  source)`: the one resolver for which runner a team runs on (`--runner`,
  then the roster's `supervisor` block, then `AGYTEAM_RUNNER`, then the
  default). `list_models`, `model_available`, `runner_class` and
  `capabilities` take `team_dir=` to answer for that team.
- `Supervisor(..., inspect_only=True)`: a supervisor that reports and never
  drives the team or touches its ledger.
- `--status` also shows whose turn is running, read from `inflight.json`
  without loading it.

### Fixed
- **`--status` wiped `inflight.json`.** It builds a Supervisor to peek at
  the queues, and `Supervisor.__init__` loaded the ledger with a runner that
  resumes nothing, before anything asked whether this process drives the
  team. Every live turn was recorded as a failure ("supervisor restarted
  mid-turn") and an empty ledger was saved over the live supervisor's.
  `--distill`, a retro, and a supervisor about to be refused the team did
  the same. A Supervisor now loads and saves the ledger only when no other
  process holds the team's lock, or once it claims the team itself.
- **Which models the team's runner offers was asked of the environment.**
  `Record.models()`, `agyteam.doctor`, `agyteam.models` (`available`,
  `list`, and the check on `set`), `lifecycle status` and `my_capabilities`
  never read the roster's `supervisor` block. From any process without
  `AGYTEAM_RUNNER` / `AGYTEAM_RUNNER_CONFIG` exported, such as a chat
  client, they described the default runner or nothing, and a model change
  was checked against the wrong runner. They now use `team_runner`, and
  doctor prints where the runner came from.

## [3.3.0] - 2026-10-19

### Fixed
- **Under `--inbox-pull`, mail pulled mid-turn is no longer lost when the
  turn fails.** Reported downstream: an agent sends an early progress note,
  a teammate's mail arrives while the turn runs, and the agent's
  `check_inbox` marks it read; if the turn then crashed, that mail was
  gone. The supervisor restored only the batch it had woken the agent with,
  and only if the turn had sent nothing. Now `check_inbox` writes a receipt
  before marking anything read during a supervised turn. A failed turn gives
  back everything on its receipt that is no longer in the inbox, once, and
  a successful turn discards the receipt.

### Added
- `Message.redelivered` (`"redelivered"` in the inbox record): set on mail a
  failed turn took, saying what that turn had already sent, and shown first
  when the agent reads it, so a turn that answered before it died is not
  simply redone.
- `receipts/<agent>.jsonl` in the team directory: one exists only while a
  supervised pull-mode turn is open, so an agent run by hand keeps none.

## [3.2.0] - 2026-10-19

### Added
- **The supervisor guards the roster.** Every permission an agent has is in
  `roster.json`, and an agent with a shell runs as the user who wrote it.
  Reported from downstream testing: a test agent given no permissions was
  refused an edit, built the roster's path in pieces to get past the check,
  granted itself access, edited the test script and finished its task. The
  supervisor now keeps the roster it accepted. A change made while an
  agent's turn is running, and not written by agyteam itself, is put back
  and kept as `roster.rejected-<time>.json`. A `roster_reverted` event names
  the agents that were running, and the operator gets a `blocker` message,
  which does not count as an answer to the user. A change made while no turn
  runs is adopted. This is detection, not a boundary: see Known limits in
  the README.
- `roster_history.jsonl`: `roster.save` records each write by content hash
  before making it. This is how the supervisor tells `lifecycle` and the
  roster admin tools apart from an agent editing the file.
- `agyteam.doctor` warns when `roster.json` is writable by the user it runs
  as, which on most setups means by the agents too.

### Fixed
- **No agent writes the team directory, confined or not.** The rule was
  enforced only for agents with a declared workspace, so an agent given no
  permissions was the one agent that could rewrite them. File tools and
  shell writes (redirects, `cp`/`mv` destinations, `sed -i`, ...) into it are
  refused, on the SDK runner too. The refusal asks the agent to request a
  missing permission rather than work around it.
- **`agyteam.doctor` no longer eats a live agent's mail.** It probed
  `check_inbox` as the first agent on the roster, which marked that agent's
  real messages read and printed them only to the preflight. It now sees and
  acknowledges only its own probe, and sends the probe as `fyi` so a running
  supervisor does not wake the agent for it.
- `roster.save` writes atomically. A reader between the truncate and the
  write saw an empty roster.

## [3.1.0] - 2026-10-18

### Added
- **The roster's `supervisor` block** (`runner`, `runner_config`, `manager`,
  `inbox_pull`): how a team is run, kept with the team, so a supervisor
  started by a client with none of your environment runs it the same way.
  `--runner` on the supervisor command line; the heartbeat records the
  runner, the manager and where each came from.
- **Team-prefixed handles**: `@Ada` and `send_to_teammate(to="Ada")` reach
  `at-Ada` when exactly one agent can be meant (exact name, then case, then a
  name ending in `-Ada`, `_Ada` or `.Ada`). `agyteam.roster.resolve_name`.
- **Document-fit reviews**: `record_review(..., deliverable, criteria)` --
  each requirement either quoted verbatim from the deliverable (checked) or
  marked unmet with a reason. Approval needs every criterion met; a request
  for changes needs at least one unmet. Code reviews still need a proof.
- **`switches_model_in_conversation`** on runners: a model change on a
  runtime that switches in place (the agy CLI, measured) keeps the
  conversation instead of distilling and cycling it. Host runners declare it
  in `capabilities`.
- **One supervisor per team**: a lock (`supervisor.lock`); a second
  supervisor refuses the team and leaves the live one's heartbeat alone.

### Fixed
- A supervisor started by a client used the client's runner and a guessed
  manager: wakes failed and conversations were lost.
- Turns that time out no longer count toward retiring a conversation; a slow
  agent kept its context.
- Under `inbox_pull`, a successful turn no longer marks the whole batch read;
  only the pages the agent opened are.
- **Review gate window**: an author's work now counts from the start of the
  episode in progress, or of the one that just finished when no new one has
  begun. It used to count only from the end of the last finished episode,
  so a review recorded after an episode could not name work done in it, and
  passed only when the work and the episode's end fell in the same second.
  Work from before that episode still does not count. The supervisor records
  an `episode_start` event in `events.jsonl` to mark where the window opens.
  For episodes recorded before this change, the start is worked out from
  their `duration_s`.

## [3.0.0] - 2026-10-18

The first release under semantic versioning. `v1.0` and `v2.0` were
milestone tags on the early (claw-agy) code and are not semver releases;
anything installed between them and this tag reported itself as `0.1.0`.
Everything below is relative to `v2.0`.

### Added
- **Teams of persistent agents on Antigravity**: the agy CLI plugin (four MCP
  servers: bus, memory, introspection, tasks), a reactive supervisor that
  wakes agents when mail arrives, and runners for the agy CLI (default), the
  Antigravity SDK, a mixed roster, and any host that can start, deliver and
  signal a turn (`HostRunner`, with generic `hook_pre_tool_use` and
  `hook_stop`).
- **Concurrent dispatch** with backoff, escalation, a persisted in-flight
  ledger that survives restarts, and a budget halt that stops the team once
  when the account is exhausted.
- **Channels, threads and mention-only waking** (`post_to_channel`,
  `list_channels`, `thread=`): unmentioned members read posts on their next
  wake; a byte budget keeps a busy channel out of the wake prompt.
- **Tasks and reminders** (`check_after`, `until` conditions, deadlines,
  evidence-gated closure, `requires_review`, operator waivers) and a sweep any
  scheduler can run.
- **Review gate**: proof-backed reviews by a teammate who is not the author
  and who did no work outside the record.
- **Learning**: memory with provenance and correction, retrospectives that
  write team norms, distill-then-cycle (auto, all-at-once in parallel, and a
  scheduled daily sweep), and `distill_only` for hosts that compact.
- **Model choice**: `python -m agyteam.models` overrides per agent or for
  everyone, with expiry, applied as a cycle; weekly usage and allowances;
  each runner's model list, with unavailable models refused and flagged.
- **One policy for every runtime** (`tools_off`, `allowed_send_to`,
  `assigns_tasks`, workspaces, `refuse_paths`, private agent workspaces,
  detach/forbidden commands, MCP-server scoping, inbound message caps).
- **A client API**: `agyteam.record` (read and follow the record) and
  `agyteam.actions` (operator actions); [agy-chat](https://github.com/roelle/agy-chat)
  is built on it.
- **Instruments**: an offline suite of ~600 tests, live evals, a cold-start
  convergence bench with an execution-graded scale suite run in Docker, and
  `agyteam.doctor`.

### Changed
- The Antigravity SDK is an optional extra: `pip install "agyteam[sdk]"`. The
  core is standard library only.
- The seeded roster pins no model; choose them with `agyteam.models`.
- No runner declares `supports_containment`: an agent with a shell is
  contained only by what contains the process.
- Licensed under MIT.

## [2.0] - 2026-09-08
Milestone tag (pre-semver): session cycling, role-scoped tools, an optional
MCP memory server.

## [1.0] - 2026-09-08
Milestone tag (pre-semver).

[Unreleased]: https://github.com/roelle/agy-team/compare/v3.15.0...HEAD
[3.15.0]: https://github.com/roelle/agy-team/compare/v3.14.0...v3.15.0
[3.14.0]: https://github.com/roelle/agy-team/compare/v3.13.0...v3.14.0
[3.13.0]: https://github.com/roelle/agy-team/compare/v3.12.0...v3.13.0
[3.12.0]: https://github.com/roelle/agy-team/compare/v3.11.1...v3.12.0
[3.11.1]: https://github.com/roelle/agy-team/compare/v3.11.0...v3.11.1
[3.11.0]: https://github.com/roelle/agy-team/compare/v3.10.1...v3.11.0
[3.10.1]: https://github.com/roelle/agy-team/compare/v3.10.0...v3.10.1
[3.10.0]: https://github.com/roelle/agy-team/compare/v3.9.0...v3.10.0
[3.9.0]: https://github.com/roelle/agy-team/compare/v3.8.0...v3.9.0
[3.8.0]: https://github.com/roelle/agy-team/compare/v3.7.1...v3.8.0
[3.7.1]: https://github.com/roelle/agy-team/compare/v3.7.0...v3.7.1
[3.7.0]: https://github.com/roelle/agy-team/compare/v3.6.0...v3.7.0
[3.6.0]: https://github.com/roelle/agy-team/compare/v3.5.0...v3.6.0
[3.5.0]: https://github.com/roelle/agy-team/compare/v3.4.0...v3.5.0
[3.4.0]: https://github.com/roelle/agy-team/compare/v3.3.0...v3.4.0
[3.3.0]: https://github.com/roelle/agy-team/compare/v3.2.0...v3.3.0
[3.2.0]: https://github.com/roelle/agy-team/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/roelle/agy-team/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/roelle/agy-team/compare/v2.0...v3.0.0
[2.0]: https://github.com/roelle/agy-team/compare/v1.0...v2.0
[1.0]: https://github.com/roelle/agy-team/releases/tag/v1.0
