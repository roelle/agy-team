"""Defect patterns this team has actually produced, seeded into qa's memory.

A reviewer is only as good as what it knows to look for, and a fresh agent knows
nothing. These are not generic code-review platitudes — every one is a real
defect that shipped here with a green test suite and was caught by a human
reading the diff. Seeding them is the difference between a reviewer that asks
"do the tests pass" and one that asks "did you make the mistake we keep making".

    python -m agyteam.qa_seed            # seed the current team's qa
    python -m agyteam.qa_seed --agent r  # or another reviewer's name

Re-running is safe: memories are keyed by name, so this updates rather than
duplicates. Add to it whenever review finds something the list would have
missed — that is how the reviewer gets better rather than merely older.
"""
import argparse
import os
import sys

# (name, description, why, content)
PATTERNS = [
    ("defect-sibling-disagreement",
     "Components that answer an already-settled question differently",
     "Two features shipped where a module ignored a convention every sibling "
     "honoured; both passed their own tests",
     """# Sibling disagreement

The most common serious defect here. A new component answers a question the
codebase has already answered, and answers it differently.

Real instances:
- `mcp_self` resolved the team through `scope.load()` while the transport, the
  runner and the session launcher all honour `AGYTEAM_TEAM_DIR`. `whoami`
  reported the wrong team — from the one tool whose purpose is trustworthy
  identity.
- A save path degraded quietly to old behaviour when a store looked
  unfamiliar, in a codebase whose loaders all fail loudly by design.

How to check: for every configuration lookup, error path, and failure message
in the diff, find the nearest sibling module that does the same thing and
compare. Divergence without a comment explaining it is the finding.
"""),

    ("defect-test-matches-implementation",
     "Tests that prove the author's code does what the author wrote",
     "Three features passed full suites while half broken, because each test "
     "used the one input shape that worked",
     """# The test was chosen by someone who already believed it worked

A green suite is weak evidence. The author picks cases that fit the mental
model that produced the code.

Real instance: mutual-contradiction detection was tested with a pair differing
by `available`/`unavailable` — an explicit antonym, which took the one code
path that still functioned. Any contradiction without a table word was silently
missed, and the suite was green.

How to check: identify the input shape the implementation is *best* at, then
build a case deliberately outside it. Feed data through the real path — saved
through the real store, sent through the real server — rather than assembling
values by hand in a test. Hand-built inputs can differ from what the system
actually produces, which is itself a bug I have made while reviewing.
"""),

    ("defect-silent-degradation",
     "Fallbacks that hide the problem someone needs to see",
     "A three-level fallback silently discarded the data the feature existed "
     "to record, and swallowed TypeErrors from inside callee implementations",
     """# Silent degradation

A fallback that quietly drops data or downgrades behaviour converts a loud,
findable failure into a mystery.

Real instance: a save shim inspected the callee's signature, and on mismatch
called it without the new arguments — discarding provenance, the entire point
of the change. Its outer `try` also wrapped the call itself, so a genuine
`TypeError` raised *inside* a store implementation was caught, retried, and
downgraded.

How to check: for every `except`, ask what information is being destroyed and
whether the caller can tell the difference between success and degraded
success. Never wrap the call itself when you mean to guard the lookup.
"""),

    ("defect-metric-fabrication",
     "Zeros and defaults standing in for values that were never measured",
     "Cost accounting is used to choose between runtimes, so a fabricated zero "
     "corrupts the decision it exists to inform",
     """# Fabricated metrics

A metric that is absent must be recorded as absent, not as zero. A zero is a
measurement; `None` is an admission.

Why it matters here: usage records are used to compare runtimes. A missing
cache-read count recorded as `0` makes one runtime look cheaper than it is and
silently corrupts the decision.

How to check: for each recorded number, ask what happens on the path where the
source is unavailable. `getattr(x, 'field', 0)` is the smell; `getattr(x,
'field', None)` is usually right. Then verify against an independent total —
per-turn figures should sum to the cumulative one.
"""),

    ("defect-shared-state-not-isolated",
     "Destructive fixtures that inherit environment from whoever ran them",
     "A test fixture deleted a live team's directory mid-run because it took "
     "its target from an inherited environment variable",
     """# File isolation is not state isolation

Separate directories do not protect shared state. Anything that deletes must
not take its target from ambient configuration.

Real instance: `test_delivery.py` used `os.environ.setdefault("AGYTEAM_TEAM",
...)` and then `shutil.rmtree` on the resolved team directory. An agent ran the
test board with `AGYTEAM_TEAM` set to a live team, and the eval destroyed that
team's roster, bus log and usage history while it was running.

How to check: for every `rmtree`, `unlink`, or truncation in a test, trace where
the path comes from. If any part is inherited, that is the finding. Assign the
target explicitly and refuse to delete anything the fixture did not create.
"""),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agyteam.qa_seed",
                                 description="Seed a reviewer's defect memory")
    ap.add_argument("--agent", default="qa")
    args = ap.parse_args(argv)

    os.environ["AGYTEAM_AGENT"] = args.agent
    from . import memory as memory_lib
    store = memory_lib.load(args.agent)

    for name, description, why, content in PATTERNS:
        created = store.save(name, description, content, why=why)
        print(f"  {'saved  ' if created else 'updated'} {name}")
    print(f"\n{args.agent} now knows {len(store.index())} memories.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
