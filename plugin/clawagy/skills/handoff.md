---
name: handoff
description: Delegate a task to a teammate with complete context
---

Delegate the task at hand to the right teammate.

First call `list_teammates` and pick the one whose role actually owns this work.
Then send them a message containing, explicitly:

1. What you need done, stated as a deliverable rather than a topic.
2. Everything they need to know — they cannot see your conversation.
3. Where results should go (a file path under the shared directory, or a reply
   to you).
4. How they will know it is correct.

Then call `save_memory` if this handoff taught you something about who owns what.

If no teammate owns it, say so and escalate to `user` instead of doing it
yourself out of role.
