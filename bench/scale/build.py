#!/usr/bin/env python3
"""Build a scale task's buggy tree from its reference, and prove every defect.

    python3 bench/scale/build.py billing OUT_DIR

The buggy tree is the reference with each planted edit applied exactly once,
and each decoy test file swapped in. Nothing about a defect is taken on
trust; the build fails unless all of these hold:

  - every `find` occurs exactly once in the reference;
  - every code defect's witness returns something different in the two trees
    (the defect is real, and the witness shows it);
  - the reference passes its own tests;
  - the buggy tree passes ITS tests -- the decoys are green, as in real life;
  - the reference's honest tests FAIL on the buggy code for every defect a
    decoy hides, so each decoy is hiding something.

Prints a JSON summary of each witness's two results, which graders compare
claims against.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run_snippet(tree: Path, code: str, timeout: int = 30) -> str:
    """Exec `code` with `tree` importable; repr of the last expression's
    value, or 'raised <Type>: <message>'."""
    driver = (
        "import ast, sys\n"
        f"sys.path.insert(0, {str(tree)!r})\n"
        "src = sys.stdin.read()\n"
        "mod = ast.parse(src)\n"
        "last = mod.body.pop() if mod.body and isinstance(mod.body[-1], ast.Expr) else None\n"
        "ns = {}\n"
        "try:\n"
        "    exec(compile(mod, '<call>', 'exec'), ns)\n"
        "    value = eval(compile(ast.Expression(last.value), '<call>', 'eval'), ns) if last else None\n"
        "    print(repr(value))\n"
        "except BaseException as e:\n"
        "    print(f'raised {type(e).__name__}: {e}')\n")
    p = subprocess.run([sys.executable, "-c", driver], input=code, cwd=tree,
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0 and not p.stdout:
        return f"raised driver error: {p.stderr.strip()[-200:]}"
    return p.stdout.strip()


def pytest(tree: Path, tests: Path | None = None) -> tuple[bool, str]:
    target = str(tests or tree / "tests")
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        target], cwd=tree, capture_output=True, text=True, timeout=300)
    return p.returncode == 0, p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-200:]


def build(task: str, out: Path) -> dict:
    root = HERE / task
    spec = json.loads((root / "defects.json").read_text())
    ref = root / "reference"
    # The data files are generated, deterministically, rather than committed.
    if not (ref / "data").exists() and (root / "make_data.py").exists():
        subprocess.run([sys.executable, str(root / "make_data.py")], check=True)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ref, out, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    problems = []
    for d in spec["defects"]:
        target = out / d["file"]
        if d.get("decoy"):
            shutil.copy(root / d["decoy"], target)
            continue
        text = target.read_text()
        n = text.count(d["find"])
        if n != 1:
            problems.append(f"{d['id']}: `find` occurs {n} times in {d['file']}")
            continue
        target.write_text(text.replace(d["find"], d["replace"]))

    witnesses = {}
    for d in spec["defects"]:
        if d.get("decoy") or d["id"] in [p.split(":")[0] for p in problems]:
            continue
        good, bad = run_snippet(ref, d["witness"]), run_snippet(out, d["witness"])
        witnesses[d["id"]] = {"reference": good, "buggy": bad}
        if good == bad:
            problems.append(f"{d['id']}: witness gives {good} in both trees; not a defect")
        if good.startswith("raised driver") or bad.startswith("raised driver"):
            problems.append(f"{d['id']}: witness did not run: {good} / {bad}")

    ok, line = pytest(ref)
    if not ok:
        problems.append(f"reference fails its own tests: {line}")
    ok, line = pytest(out)
    if not ok:
        problems.append(f"buggy tree's tests are not green (decoys must pass): {line}")
    for d in spec["defects"]:
        if d.get("decoy"):
            honest = ref / d["file"]
            scratch = out / "tests" / f"_honest_{Path(d['file']).name}"
            shutil.copy(honest, scratch)
            ok, line = pytest(out, scratch)
            scratch.unlink()
            if ok:
                problems.append(f"{d['id']}: the honest {d['file']} passes on the buggy "
                                f"code, so the decoy hides nothing")
    for c in out.rglob("__pycache__"):
        shutil.rmtree(c, ignore_errors=True)
    if problems:
        raise SystemExit("build failed:\n  " + "\n  ".join(problems))
    return {"task": spec["task"], "out": str(out), "witnesses": witnesses}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(json.dumps(build(sys.argv[1], Path(sys.argv[2]).resolve()), indent=2))
