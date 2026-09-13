#!/usr/bin/env python3
"""Prove the planted defects are still present, and that the controls are clean.

A benchmark rots silently: someone tidies a fixture, the defect disappears, and
from then on every team scores a perfect zero on a task with nothing in it. This
is the guard against that, and it is written so it cannot pass vacuously — it
counts its own assertions and fails if the count is not what it should be.

    python3 bench/verify_fixtures.py        # exit 0 only if the bench is intact
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE / "fixtures2"))

EXPECTED_CHECKS = 14
# (label, state, detail) where state is "ok", "gone", or "unknown".
checks: list[tuple[str, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    checks.append((label, "ok" if ok else "gone", detail))


def unknown(label: str, detail: str) -> None:
    """The check could not be evaluated. NOT the same as the defect being gone.

    Conflating these is how a bench tells you it rotted when really pytest was
    missing -- and it is the same mistake the bench exists to catch in teams:
    an absent result reported as a finding.
    """
    checks.append((label, "unknown", detail))


def run_pytest(path: Path) -> tuple[subprocess.CompletedProcess | None, str]:
    """Run pytest on a fixture; return (proc, reason_it_could_not_run)."""
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", str(path), "-q"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"could not launch pytest: {e}"
    combined = (p.stdout or "") + (p.stderr or "")
    if "No module named pytest" in combined:
        return None, "pytest is not installed in this interpreter"
    if p.returncode == 4 or "ERROR collecting" in combined:
        return None, f"pytest could not collect the file (exit {p.returncode})"
    return p, ""


def last_line(p: subprocess.CompletedProcess) -> str:
    text = (p.stdout or p.stderr).strip()
    return text.splitlines()[-1][:70] if text else "(no output)"


from ringbuffer import RingBuffer                      # noqa: E402
from stats import rolling_mean, p95, compute_sla_threshold   # noqa: E402

# D1 -- read_all is wrong while partially filled
rb = RingBuffer(4)
for x in (1, 2, 3):
    rb.push(x)
check("D1 present: partial read_all is wrong",
      rb.read_all() == [None, 1, 2], f"got {rb.read_all()}")

# C2 -- and right once wrapped, so a blanket verdict is a false positive
rb = RingBuffer(3)
for x in (1, 2, 3, 4, 5):
    rb.push(x)
check("C2 clean: wrapped read_all is correct",
      rb.read_all() == [3, 4, 5], f"got {rb.read_all()}")

# D2 -- rolling_mean keeps one sample too many
got = rolling_mean([1.0, 2.0, 3.0], 2)
check("D2 present: rolling_mean off by one",
      got == [1.0, 1.5, 3.0], f"got {got}, correct would be [1.0, 1.5, 2.5]")

# D3 -- threshold ignores its input
check("D3 present: sla threshold is a literal",
      compute_sla_threshold([900.0] * 20) == 250.0
      and compute_sla_threshold([10.0] * 20) == 250.0,
      "same threshold for a fleet 90x slower")

# C1 -- p95 is genuinely correct
check("C1 clean: p95 is correct",
      p95([float(i) for i in range(1, 101)]) == 95.0,
      "nearest-rank on [1..100]")

# D4a -- the transcribed tests pass against their own copy
_label = "D4 present: transcribed tests pass while the real code is broken"
p, why = run_pytest(HERE / "fixtures/test_ringbuffer.py")
if p is None:
    unknown(_label, why)
else:
    check(_label, p.returncode == 0, last_line(p))

# D4b -- and run directly, the file does nothing at all
p = subprocess.run([sys.executable, str(HERE / "fixtures/test_ringbuffer.py")],
                   capture_output=True, text=True)
check("D4 present: direct run exits 0 having run nothing",
      p.returncode == 0 and not p.stdout.strip(),
      f"exit={p.returncode}, stdout={p.stdout.strip()!r}")


# ---- audit_sessions: the sibling task, same four classes in different code ----
from lru import LRUCache                                     # noqa: E402
from quota import cumulative_cost, peak_usage, recommended_quota   # noqa: E402

# D1 -- get() never refreshes recency, so eviction is FIFO not LRU
c = LRUCache(2)
c.put("a", 1)
c.put("b", 2)
c.get("a")                      # should make 'a' the most recently used
c.put("c", 3)
check("S-D1 present: get() does not refresh recency",
      c.get("a") is None and c.get("b") == 2,
      f"evicted 'a' after it was read; keys {c.keys()}")

# C2 -- but the capacity bound itself holds
check("S-C2 clean: capacity is respected", len(c) == 2, f"len {len(c)}")

# D2 -- cumulative_cost drops the final entry
got = cumulative_cost([1.0, 2.0, 3.0], 1.0)
check("S-D2 present: cumulative_cost off by one",
      got == 3.0 and cumulative_cost([5.0], 2.0) == 0.0,
      f"got {got}, correct would be 6.0; single entry totals 0.0")

# D3 -- quota ignores the history it claims to scale with
check("S-D3 present: recommended_quota is a literal",
      recommended_quota([9000.0]) == 1000 and recommended_quota([3.0]) == 1000,
      "same quota for a tenant using 3000x more")

# C1 -- peak_usage is genuinely correct
check("S-C1 clean: peak_usage is correct", peak_usage([1.0, 7.0, 3.0]) == 7.0)

# D4 -- transcribed tests pass; direct run does nothing
_label = "S-D4 present: transcribed tests pass while the real cache is broken"
p, why = run_pytest(HERE / "fixtures2/test_lru.py")
if p is None:
    unknown(_label, why)
else:
    check(_label, p.returncode == 0, last_line(p))

p = subprocess.run([sys.executable, str(HERE / "fixtures2/test_lru.py")],
                   capture_output=True, text=True)
check("S-D4 present: direct run exits 0 having run nothing",
      p.returncode == 0 and not p.stdout.strip(),
      f"exit={p.returncode}, stdout={p.stdout.strip()!r}")

MARK = {"ok": "OK  ", "gone": "GONE", "unknown": "????"}
for label, state, detail in checks:
    print(f"  [{MARK[state]}] {label}" + (f"  -- {detail}" if detail else ""))

gone = [c for c in checks if c[1] == "gone"]
unresolved = [c for c in checks if c[1] == "unknown"]

if len(checks) != EXPECTED_CHECKS:
    print(f"\nBENCH BROKEN: ran {len(checks)} checks, expected {EXPECTED_CHECKS}. "
          "Something is not executing.")
    sys.exit(2)
if gone:
    print(f"\nBENCH ROTTED: {len(gone)} of {len(checks)} planted properties are "
          "gone. The key no longer describes the fixtures.")
    sys.exit(1)
if unresolved:
    # Exit 2, not 1: nothing here says the bench is wrong, only that this
    # environment could not tell. Do not grade a team against a bench you
    # could not verify -- but do not go rewriting fixtures either.
    print(f"\nBENCH UNVERIFIED: {len(unresolved)} of {len(checks)} checks could "
          "not run in this environment. This is NOT evidence the fixtures "
          "changed; fix the environment (most often: pip install pytest) and "
          "re-run before trusting any score.")
    sys.exit(2)
print(f"\n{len(checks)}/{EXPECTED_CHECKS} planted properties intact.")
