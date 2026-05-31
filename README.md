# Apex Team Framework

A persistent, self-modifying multi-agent engineering team built on the [Google Antigravity SDK](https://github.com/google-antigravity/antigravity-sdk-python).

Solves the three core problems with standard agent setups:
1. **Amnesia** — agents forget everything between sessions
2. **Cold sub-agents** — delegated agents start without context, fail or ask for help
3. **No accumulation** — agents re-discover the same things every run

---

## What It Does

A coordinated team of specialists — TPM, simulation engineer, data analyst, test engineer, systems engineer, technical writer, QA reviewer — each with persistent memory and accumulated domain knowledge. The TPM coordinates; specialists execute; knowledge files grow over time.

```
python agent.py                          # Start as TPM
python agent.py --role test_engineer     # Start as test engineer
python agent.py --role data_analyst --domain domain_a
python agent.py --resume                 # Resume last session for this role
python agent.py --list-roles             # Show configured team
```

---

## Setup

```bash
pip install google-antigravity python-dotenv
cp .env.example .env
# Edit .env — add your GEMINI_API_KEY
# Get one at: https://aistudio.google.com/app/api-keys
```

---

## Configuration

### Generic configuration: `team_config.json`
Defines roles, domains, and defaults. Committed to the repo.

### Deployment-specific overrides: `team_config.local.json`
Gitignored. Override role descriptions, add domain-specific skill paths,
rename domains. Example:

```json
{
  "team_name": "My Team",
  "domains": {
    "domain_a": {
      "skill_path": "skills/sys_eng_a",
      "description": "Our primary engineering domain"
    }
  }
}
```

---

## Memory System

| File | Purpose | Written by |
|------|---------|-----------|
| `soul/soul.md` | Personality, principles, identity template | You / agent |
| `soul/reflexes.md` | Fast operating priors, loaded every session | You / agent |
| `soul/memory.md` | Long-term facts: user, projects, infra | Agent (session end) |
| `soul/roster.json` | Per-role names and origins | Agent / you |
| `memory/YYYY-MM-DD.md` | Daily logs | Agent (throughout session) |
| `knowledge/sim_flags.md` | Accumulated simulation flag knowledge | test_engineer |
| `knowledge/sql_tables.md` | Database table schemas and quirks | data_analyst |
| `knowledge/python_sim_patterns.md` | Simulation modeling patterns | sim_engineer |
| `knowledge/requirements_a.md` | Domain A requirements map | sys_eng_a |
| `knowledge/requirements_b.md` | Domain B requirements map | sys_eng_b |
| `knowledge/running_tasks.json` | Active task queue | tpm |
| `knowledge/release_calendar.md` | Release dates and deliverables | tpm |

Memory files are loaded at session start via `TemplatedSystemInstructions`.
Knowledge files accumulate over time — the agent reads and updates them.

---

## Sub-Agent Delegation (Phase 2)

Use `spawn_specialist` instead of relying on built-in sub-agent spawning.
Every specialist gets full memory context before touching the task:

```
TPM: spawn_specialist("test_engineer", "Run baseline sim for scenario X", domain="domain_a")
  → test_engineer receives: soul + memory + reflexes + role SKILL.md + task
  → test_engineer writes output to outputs/
  → test_engineer updates knowledge/sim_flags.md
  → returns result to TPM
```

---

## Project Structure

```
├── agent.py                  # Entry point
├── bootstrap.py              # Assembles system instructions from memory files
├── config.py                 # Loads team_config.json + local overrides
├── memory_tools.py           # append_to_log, update_task, read_knowledge, etc.
├── specialist_tools.py       # spawn_specialist, qa_review
├── team_config.json          # Generic team definition (public)
├── team_config.local.json    # Private deployment overrides (gitignored)
├── soul/
│   ├── soul.md               # Identity template ({NAME}, {ORIGIN}, {MISSION})
│   ├── reflexes.md           # Fast operating priors
│   ├── memory.md             # Long-term memory
│   └── roster.json           # Per-role name/origin/mission mappings
├── skills/                   # Role SKILL.md files (native SDK format)
│   ├── tpm/SKILL.md
│   ├── test_engineer/SKILL.md
│   ├── sim_engineer/SKILL.md
│   ├── data_analyst/SKILL.md
│   ├── tech_writer/SKILL.md
│   ├── sys_eng_a/SKILL.md
│   ├── sys_eng_b/SKILL.md
│   └── qa_reviewer/SKILL.md
├── knowledge/                # Accumulated domain knowledge (grows over time)
├── memory/                   # Daily logs (YYYY-MM-DD.md)
├── outputs/                  # Specialist work products
└── sessions/                 # Conversation persistence (save_dir)
```

---

## Porting to Other Backends

The memory system, skill files, and knowledge accumulation pattern are backend-agnostic. To port:
- `bootstrap.py` → whatever your backend uses for system instruction injection
- `memory_tools.py` → register as tools in your backend's tool system
- `specialist_tools.py` → reimplement spawn using your backend's sub-agent API
- `soul/`, `knowledge/`, `skills/` → files are plain markdown, fully portable

---

## License

Apache 2.0
