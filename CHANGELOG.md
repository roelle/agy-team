# Changelog

All notable changes to agy-team. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) from 3.0.0 on; see "Versioning" in
the README for what counts as the public surface.

## [Unreleased]

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

[Unreleased]: https://github.com/roelle/agy-team/compare/v3.0.0...HEAD
[3.0.0]: https://github.com/roelle/agy-team/compare/v2.0...v3.0.0
[2.0]: https://github.com/roelle/agy-team/compare/v1.0...v2.0
[1.0]: https://github.com/roelle/agy-team/releases/tag/v1.0
