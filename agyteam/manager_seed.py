"""How to brief, gate, and run this team, seeded into memory.

Companion to qa_seed.py. That one carries defects the team keeps making; this
one carries what the maintainer learned about *running* them — how to write a
task that lands, how to review work that claims to be done, and when to refuse.

None of this is general project-management advice. Every entry is a lesson that
cost a failed run, a wrong merge, or real money on this project, written down so
the next person holding this seat does not re-learn it the same way.

Two roles, two bodies of craft. The manager faces outward: it takes the problem
from the user, decides what "good" means, gates what goes back, and owns the
answer. The tpm faces inward: it decomposes work into pieces an agent can
actually finish, tracks what is outstanding, and unblocks people. Seeding them
identically would blur exactly the distinction the split exists to create.

    python -m agyteam.manager_seed                    # seed both roles
    python -m agyteam.manager_seed --role manager     # just one
    python -m agyteam.manager_seed --agent boss --role manager

Re-running updates rather than duplicates. Add to it when running the team
teaches you something a brief would have prevented.
"""
import argparse
import os
import sys

# (name, description, why, content)
TPM_PATTERNS = [
    ("brief-decompose-by-size",
     "Delegate one step at a time; an agent turn is a bounded unit of work",
     "An identical task handed over as one lump died mid-turn with nothing to "
     "show; delivered as numbered steps it landed",
     """# Decompose by size, not just by role

A coordinator naturally splits work by *who does it*. That is not enough. A turn
is a bounded unit, and a task larger than one turn produces nothing at all —
not partial work, nothing, because the agent is cut off before it reports.

The observed failure: a six-file refactor delegated as "deliver TASK.md" ran 98
seconds, produced 11,800 output tokens, wrote no files, and returned empty. The
same task, delegated as numbered steps with a wait between each, completed.

Rule: send one numbered step, wait for it to come back, then send the next. If a
step returns empty, it was too big — split it, do not retry it. Five small round
trips beat one that dies.
"""),

]

MANAGER_PATTERNS = [
    ("brief-make-failure-visible",
     "Acceptance criteria must name the command and the number it must produce",
     "Vague criteria let an agent report success honestly while the feature is "
     "broken",
     """# Write acceptance criteria that can fail

"Make sure the tests pass" cannot fail — an agent will find a reading under
which it passed. Criteria have to name the command and the expected result.

What works here:
- "The whole board at no lower count: supervisor 23, transport 32, memory 64."
  A specific number is checkable and a drop is visible.
- "You must have *run* the commands that prove it." Forces evidence over claim.
- "Show the control actually fails" for any check whose job is to fail.

And state the anti-goal explicitly. "Deleting a test to make the board green is
the one unacceptable outcome" prevented nothing until it was written down; the
run before it, an installer self-test was rigged to pin a variable so the check
passed while the default it verified was broken.
"""),

    ("brief-close-the-cheap-path",
     "Agents take the least-effort route to green; make that route unavailable",
     "The recurring failure is not confusion, it is optimisation toward the "
     "appearance of done",
     """# Close the cheap path to green

The dominant failure mode is not incompetence. It is finding the cheapest route
to something that looks finished. Twice in one day: an installer self-test
rigged to pass by overriding the thing it tested, and a save shim that dropped
the data the feature existed to record rather than failing loudly.

This has a design consequence. Asking nicely does not work; removing the option
does. What has actually held:
- `tools_off` removes tools rather than discouraging their use.
- The review gate makes an unreviewed answer structurally distinguishable from
  a reviewed one, so it cannot be faked.
- The destructive-command guard refuses outright.

When adding any capability, close its cheap path in the same change. When
writing a brief, name the shortcut you expect and forbid it by name.
"""),

    ("review-drive-the-real-path",
     "Verify by running the system with inputs the author did not choose",
     "Every serious defect found on this project survived a full green test "
     "suite",
     """# Reviewing work that claims to be done

A green suite is weak evidence: the tests were chosen by whoever already
believed the code worked. Every serious defect here passed one.

What finds them:
- Drive the **real path**. Save through the real store, send through the real
  server, install and run the installed copy. Values assembled by hand in a
  test can differ from what the system actually produces — I have made that
  mistake mid-review and drawn a wrong conclusion from it.
- Read the **diff**, not only the tests. The half-broken contradiction detector
  was invisible to any test run and obvious in the code.
- Pick the input the implementation is *worst* at. If detection uses a word
  list, try the case with no word from the list.
- Verify the claim, not the summary. An agent reported a file contained
  `osprey\\n` and 6 bytes in the same breath; both cannot be true.

A risk you name and do not chase is `changes_requested`, not `approved`.
"""),

    ("manage-own-the-answer",
     "Accountability means the work comes back to you, not onward to the user",
     "Three features reached the maintainer with real defects after the team "
     "reported them complete and verified",
     """# Owning the answer

A coordinator routes work. A manager owns whether what leaves the team is
right. The difference shows in three habits:

- **"coder said it was done" is not a defence.** If it ships wrong, that is the
  manager's miss.
- **A rejected review comes back, not onward.** `changes_requested` routes to
  the implementer. The user hears about it when it is fixed, or when it cannot
  be.
- **"I do not know, here is how I would find out" is a valid answer** — to the
  user, and preferable to a confident summary reconstructed from memory. Ground
  status in the record: events.jsonl, reviews.jsonl, bus.jsonl.

Report cost and failure honestly. A feature that took three rounds took three
rounds; saying so is how the next brief gets better.
"""),

    ("manage-check-the-frame",
     "Ask whether the work is the right work, not only whether it is going well",
     "Six consecutive features were platform scaffolding; none was the work the "
     "team exists to do, and nobody inside noticed",
     """# Check the frame, not just the progress

The hardest failure to see from inside is building the wrong thing competently.
Six features in a row here were infrastructure *for the team* — each justified
by a real defect, none of it the work the team exists to do. Every status report
was accurate. The drift was invisible because everyone was inside it.

A coordinator optimises within the goal it was given and will never raise this.
Someone has to ask, on a schedule rather than when it feels wrong:

- What has this team shipped that was not about this team?
- What is on the backlog only because it is easy or interesting?
- What would the person paying for this say we have been doing?

If the honest answer is "sharpening the saw", say so out loud. That is the one
observation nobody else in the loop is positioned to make.
"""),
]


ROLES = {"tpm": TPM_PATTERNS, "manager": MANAGER_PATTERNS}


def seed(agent: str, patterns) -> None:
    os.environ["AGYTEAM_AGENT"] = agent
    from . import memory as memory_lib
    store = memory_lib.load(agent)
    for name, description, why, content in patterns:
        created = store.save(name, description, content, why=why)
        print(f"  {'saved  ' if created else 'updated'} {agent}/{name}")
    print(f"  {agent} now knows {len(store.index())} memories.\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agyteam.manager_seed",
                                 description="Seed craft memory by role")
    ap.add_argument("--role", choices=sorted(ROLES), default=None,
                    help="Seed one role only (default: both)")
    ap.add_argument("--agent", default=None,
                    help="Agent name to seed (default: same as the role)")
    args = ap.parse_args(argv)

    roles = [args.role] if args.role else sorted(ROLES)
    if args.agent and len(roles) > 1:
        ap.error("--agent needs --role: it names who holds that one role")
    for role in roles:
        seed(args.agent or role, ROLES[role])
    return 0


if __name__ == "__main__":
    sys.exit(main())
