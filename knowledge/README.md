# Knowledge Directory

This directory contains accumulated domain knowledge — files that grow over time as specialists do work.

**These are deployment artifacts, not framework files.** They are gitignored (except this README) and created at runtime by the agent.

## How It Works

Each specialist role reads and writes its own knowledge file(s) before and after every session. Knowledge accumulates across sessions rather than being re-discovered each time.

## Typical Files (created at runtime)

| File | Role | Contents |
|------|------|----------|
| `simulation_knowledge.md` | test_engineer | Simulation flags, their rationale, gotchas |
| `data_sources.md` | data_analyst | Table schemas, query patterns, known quirks |
| `modeling_patterns.md` | sim_engineer | Reusable Python patterns, library notes |
| `domain_a_requirements.md` | sys_eng_a | Requirements map, gaps, test matrix |
| `domain_b_requirements.md` | sys_eng_b | Requirements map, gaps, test matrix |
| `running_tasks.json` | tpm | Active task queue (runtime state) |
| `release_calendar.md` | tpm | Release dates and deliverables |

## Adding Knowledge Files

To define knowledge files for your deployment:
1. Reference them in the relevant `skills/*/SKILL.md` under "Knowledge File"
2. The agent will create them on first write via `update_knowledge()`
3. Customize file names in `team_config.local.json` if needed
