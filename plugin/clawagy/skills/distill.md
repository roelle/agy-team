---
name: distill
description: Sweep durable learnings from this session into memory before context is lost
---

Review this session for anything durable you have not already saved:

- user preferences and standing instructions
- corrections you were given (and what you had wrong)
- facts about this machine, project, or codebase that you had to discover
- lessons from anything that failed

For each one, call `save_memory`. Check `memory_index` first and **update the
existing memory** rather than creating a near-duplicate. If a memory turned out
to be wrong, `delete_memory` it.

If there is genuinely nothing new, say so in one line. Otherwise reply with a
one-line summary of what you saved.
