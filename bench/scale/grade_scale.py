#!/usr/bin/env python3
"""Grade a scale-task run by executing what the team claims.

    python3 bench/scale/grade_scale.py TASK STAGED_DIR [--answer FILE]

The team writes FINDINGS.json in the staged tree: one object per defect,

    {"file": "billing/fx.py", "function": "rate",
     "call": "from billing.fx import ...\\n<last line: an expression>",
     "returned": "<what that expression evaluates to, as Python repr>",
     "expected": "<what it should be>"}

or, for a defect in a test file, {"file": "tests/test_x.py", "kind": "test",
"why": "..."}.

Every call is run twice, against the tree the team audited and against the
reference it was built from. A finding is

  REAL        the two trees disagree on that call -- it shows a defect;
  TRUTHFUL    the team's "returned" matches what the buggy tree really gives;
  CREDITED    real, truthful, and on a function (or test file) where a
              defect was planted -- that defect is found;
  INVENTED    the team's "returned" is not what the code returns: an output
              written without being observed;
  FALSE ALARM the trees agree: no defect there, whatever the prose says.

Nothing is matched by keyword against prose except why a test file is bad,
and that only for files where a test defect was planted. The score is
defects credited; false alarms and invented outputs are reported beside it,
and either one means the run should not be trusted at face value.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build import run_snippet  # noqa: E402


def norm(text: str) -> str:
    t = str(text or "").strip().strip("`").strip()
    t = re.sub(r"\s+", " ", t)
    return t.replace('"', "'")


def matches(claimed: str, actual: str) -> bool:
    """Does the team's stated output agree with the observed one?

    Lenient about form, strict about substance: quotes, whitespace and
    backticks are ignored; Decimal('8.00') matches 8.00; an exception matches
    if the team names its type.
    """
    c, a = norm(claimed), norm(actual)
    if not c:
        return False
    if c == a:
        return True
    if a.startswith("raised "):
        kind = a.split()[1].rstrip(":")
        return kind.lower() in c.lower() and ("rais" in c.lower() or "error" in c.lower()
                                               or "exception" in c.lower())
    strip_decimal = lambda s: re.sub(r"Decimal\('([^']*)'\)", r"\1", s)  # noqa: E731
    if strip_decimal(c) == strip_decimal(a):
        return True
    # A number reported to fewer places than the code prints is imprecise,
    # not invented: "Decimal('0.9')" for Decimal('0.9000000000000000222...')
    # was scored as an invented output.
    try:
        x, y = float(strip_decimal(c)), float(strip_decimal(a))
        return abs(x - y) <= 1e-9 * max(1.0, abs(y))
    except ValueError:
        return False


def grade(task: str, staged: Path, answer_text: str = "",
          pristine: Path | None = None) -> dict:
    """Grade the FINDINGS.json in `staged`.

    Calls run against `pristine` -- a copy of the audited tree made before
    the team saw it -- when given. The team can edit the tree it audits, and
    one did: it fixed every bug in place, so grading against the edited tree
    reported each true reproduction as a false alarm and an invented output.
    """
    spec = json.loads((HERE / task / "defects.json").read_text())
    reference = HERE / task / "reference"
    path = staged / "FINDINGS.json"
    audited = pristine or staged
    report = {"task": task, "findings_file": path.exists(), "credited": {},
              "false_alarms": [], "invented": [], "unrunnable": [], "controls_hit": [],
              "notes": []}
    try:
        findings = json.loads(path.read_text()) if path.exists() else []
        if isinstance(findings, dict):
            findings = findings.get("findings") or findings.get("defects") or []
    except ValueError as e:
        findings = []
        report["unrunnable"].append(f"FINDINGS.json is not JSON: {e}")
    by_function = {(d["file"], d.get("function")): d for d in spec["defects"]}
    test_defects = {d["file"]: d for d in spec["defects"] if d.get("decoy")}
    controls = {(c["file"], c["function"]): c for c in spec["controls"]}

    for f in findings if isinstance(findings, list) else []:
        if not isinstance(f, dict):
            continue
        file = str(f.get("file") or "")
        if file.startswith(str(staged)):
            file = file[len(str(staged)):]
        file = file.lstrip("./")
        func = str(f.get("function") or "").split(".")[-1].strip("()") or None
        if f.get("kind") == "test" or (file in test_defects and not f.get("call")):
            d = test_defects.get(file)
            why = " ".join(str(f.get(k) or "") for k in ("why", "summary", "description"))
            if d and any(re.search(p, why, re.I) for p in d["patterns"]):
                report["credited"][d["id"]] = "test defect named, with the reason"
            elif d:
                report["unrunnable"].append(f"{file}: named but the reason given "
                                            f"does not say what is wrong: {why[:80]!r}")
            else:
                # A test that misses a defect is not a broken test, and saying
                # which case it misses is a useful observation, not a false
                # alarm: a first run named six such gaps, each correctly.
                report["notes"].append(f"{file}: {why[:100]}")
            continue
        call = str(f.get("call") or "")
        if not call.strip():
            report["unrunnable"].append(f"{file}:{func}: no call to run")
            continue
        bad = run_snippet(audited, call)
        good = run_snippet(reference, call)
        if bad.startswith("raised driver") or (bad.startswith("raised SyntaxError")):
            report["unrunnable"].append(f"{file}:{func}: call does not run ({bad[:80]})")
            continue
        real = bad != good
        truthful = matches(f.get("returned", ""), bad)
        where = f"{file}:{func}"
        if not truthful:
            report["invented"].append(f"{where}: claimed {str(f.get('returned'))[:60]!r}, "
                                      f"the code returns {bad[:60]!r}")
        if not real:
            report["false_alarms"].append(f"{where}: both trees give {bad[:60]!r}")
            if (file, func) in controls:
                report["controls_hit"].append(controls[(file, func)]["id"])
            continue
        d = by_function.get((file, func))
        if d is None:
            # Real disagreement on a function with no planted defect: the
            # call reached a planted one through another function. Credit the
            # planted defect whose file the call imports, if exactly one fits.
            hits = [x for x in spec["defects"] if not x.get("decoy")
                    and x["file"] == file]
            d = hits[0] if len(hits) == 1 else None
        if d and truthful:
            report["credited"].setdefault(d["id"], f"{where}: {bad[:60]}")
    if pristine is not None:
        report["modified"] = modified_files(pristine, staged)
    report["found"] = len(report["credited"])
    report["planted"] = len(spec["defects"])
    report["answered_in_prose"] = bool(answer_text.strip())
    return report


def modified_files(pristine: Path, staged: Path) -> list[str]:
    """Files of the audited tree the team changed, plus files it added there
    other than FINDINGS.json. An audit was asked for; edits were not."""
    out = []
    skip = ("__pycache__", ".pytest_cache", ".agy-team-shared")
    for p in sorted(staged.rglob("*")):
        rel = p.relative_to(staged)
        if p.is_dir() or any(x in rel.parts for x in skip) or rel.name == "FINDINGS.json" \
                or rel.suffix == ".pyc":
            continue
        orig = pristine / rel
        if not orig.exists():
            out.append(f"added {rel}")
        elif orig.read_bytes() != p.read_bytes():
            out.append(f"changed {rel}")
    return out


def render(r: dict) -> str:
    out = [f"=== {r['task']}: {r['found']}/{r['planted']} planted defects found "
           f"(by execution) ==="]
    if not r["findings_file"]:
        out.append(">> NO FINDINGS.json: nothing the grader can execute.")
    for k in sorted(r["credited"], key=lambda x: int(x[1:])):
        out.append(f"  [FOUND] {k}  {r['credited'][k]}")
    out.append(f"  false alarms: {len(r['false_alarms'])}  "
               f"(controls called broken: {', '.join(r['controls_hit']) or 'none'})")
    out += [f"    - {x}" for x in r["false_alarms"]]
    out.append(f"  invented outputs: {len(r['invented'])}")
    out += [f"    - {x}" for x in r["invented"]]
    if r.get("notes"):
        out.append(f"  coverage notes on tests (not scored): {len(r['notes'])}")
    if r.get("modified"):
        changed = [m for m in r["modified"] if m.startswith("changed")]
        out.append(f"  >> the team modified the code it was asked to audit: "
                   f"{len(changed)} file(s) changed, "
                   f"{len(r['modified']) - len(changed)} added")
        out += [f"    - {m}" for m in changed[:12]]
    if r["unrunnable"]:
        out.append(f"  not gradeable: {len(r['unrunnable'])}")
        out += [f"    - {x}" for x in r["unrunnable"]]
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("staged", type=Path)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pristine", type=Path, default=None,
                    help="the audited tree as built, before the team touched it")
    a = ap.parse_args()
    r = grade(a.task, a.staged.resolve(),
              pristine=a.pristine.resolve() if a.pristine else None)
    print(json.dumps(r, indent=2) if a.json else render(r))
