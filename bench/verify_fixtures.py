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

EXPECTED_CHECKS = 7
checks: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    checks.append((label, bool(ok), detail))


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
p = subprocess.run([sys.executable, "-m", "pytest",
                    str(HERE / "fixtures/test_ringbuffer.py"), "-q"],
                   capture_output=True, text=True)
check("D4 present: transcribed tests pass while the real code is broken",
      p.returncode == 0, (p.stdout or p.stderr).strip().splitlines()[-1][:70])

# D4b -- and run directly, the file does nothing at all
p = subprocess.run([sys.executable, str(HERE / "fixtures/test_ringbuffer.py")],
                   capture_output=True, text=True)
check("D4 present: direct run exits 0 having run nothing",
      p.returncode == 0 and not p.stdout.strip(),
      f"exit={p.returncode}, stdout={p.stdout.strip()!r}")

for label, ok, detail in checks:
    print(f"  [{'OK  ' if ok else 'GONE'}] {label}" + (f"  -- {detail}" if detail else ""))

failed = [c for c in checks if not c[1]]
if len(checks) != EXPECTED_CHECKS:
    print(f"\nBENCH BROKEN: ran {len(checks)} checks, expected {EXPECTED_CHECKS}. "
          "Something is not executing.")
    sys.exit(2)
if failed:
    print(f"\nBENCH ROTTED: {len(failed)} of {len(checks)} planted properties are "
          "gone. The key no longer describes the fixtures.")
    sys.exit(1)
print(f"\n{len(checks)}/{EXPECTED_CHECKS} planted properties intact.")
