"""The scale bench's instruments: every planted defect is real, and the
grader credits only what it can reproduce.

A bench whose key is wrong fails honest work, and one that scores prose
rewards confident invention; the first cold run of the small bench scored a
grader's own false positive as the team's mistake. So the build proves each
defect by execution before any team sees it, and the grader runs every claim.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "scale"))

import build as scale_build  # noqa: E402
import grade_scale  # noqa: E402

TASKS = ["billing", "inventory", "scheduling"]


@pytest.fixture(scope="module", params=TASKS)
def built(request, tmp_path_factory):
    out = tmp_path_factory.mktemp(request.param) / "tree"
    return request.param, out, scale_build.build(request.param, out)


def spec(task):
    return json.loads((ROOT / "bench" / "scale" / task / "defects.json").read_text())


def test_every_planted_code_defect_is_real(built):
    task, out, summary = built
    code = [d for d in spec(task)["defects"] if not d.get("decoy")]
    assert set(summary["witnesses"]) == {d["id"] for d in code}
    for w in summary["witnesses"].values():
        assert w["reference"] != w["buggy"]


def test_the_tree_carries_no_answers(built):
    _, out, _ = built
    names = {p.name for p in out.rglob("*")}
    assert not names & {"defects.json", "reference", "decoys", "build.py", "grade_scale.py"}


def write(out, findings):
    (out / "FINDINGS.json").write_text(json.dumps(findings))


def witness_findings(task):
    """A perfect report: every defect, with the output the code really gives."""
    out, s = [], spec(task)
    for d in s["defects"]:
        if d.get("decoy"):
            out.append({"file": d["file"], "kind": "test",
                        "why": d["summary"]})
        else:
            out.append({"file": d["file"], "function": d["function"],
                        "call": d["witness"], "returned": None})
    return out


def test_a_perfect_report_finds_everything(built):
    task, out, summary = built
    findings = witness_findings(task)
    for f in findings:
        if f.get("call"):
            d = next(x for x in spec(task)["defects"] if x.get("witness") == f["call"])
            f["returned"] = summary["witnesses"][d["id"]]["buggy"]
    write(out, findings)
    r = grade_scale.grade(task, out)
    assert r["found"] == r["planted"], r
    assert not r["false_alarms"] and not r["invented"], r


def test_an_invented_output_is_not_credited(built):
    task, out, summary = built
    d = next(x for x in spec(task)["defects"] if not x.get("decoy"))
    write(out, [{"file": d["file"], "function": d["function"], "call": d["witness"],
                 "returned": summary["witnesses"][d["id"]]["reference"]}])
    r = grade_scale.grade(task, out)
    assert r["found"] == 0 and len(r["invented"]) == 1


def test_calling_a_control_broken_is_a_false_alarm(built):
    task, out, _ = built
    c = spec(task)["controls"][0]
    mod = c["file"][:-3].replace("/", ".")
    write(out, [{"file": c["file"], "function": c["function"],
                 "call": f"import {mod}\n{mod}.__name__", "returned": repr(mod)}])
    r = grade_scale.grade(task, out)
    assert r["found"] == 0 and r["controls_hit"] == [c["id"]]


def test_a_vague_reason_for_a_bad_test_is_not_enough(built):
    task, out, _ = built
    d = next(x for x in spec(task)["defects"] if x.get("decoy"))
    write(out, [{"file": d["file"], "kind": "test", "why": "flaky"}])
    assert grade_scale.grade(task, out)["found"] == 0


def test_edits_to_the_audited_tree_do_not_change_the_grade(built, tmp_path):
    """A team fixed every bug in place; graded against its own edited tree,
    every true reproduction read as a false alarm and an invented output."""
    import shutil
    task, out, summary = built
    pristine = tmp_path / "pristine"
    shutil.copytree(out, pristine)
    d = next(x for x in spec(task)["defects"] if not x.get("decoy"))
    write(out, [{"file": d["file"], "function": d["function"], "call": d["witness"],
                 "returned": summary["witnesses"][d["id"]]["buggy"]}])
    fixed = (ROOT / "bench" / "scale" / task / "reference" / d["file"]).read_text()
    (out / d["file"]).write_text(fixed)              # the team "fixes" it
    try:
        r = grade_scale.grade(task, out, pristine=pristine)
        assert r["found"] == 1 and not r["invented"] and not r["false_alarms"]
        assert f"changed {d['file']}" in r["modified"]
    finally:
        shutil.copy(pristine / d["file"], out / d["file"])


def test_a_coverage_gap_in_an_unplanted_test_is_a_note_not_a_false_alarm(built):
    task, out, _ = built
    planted = {x["file"] for x in spec(task)["defects"] if x.get("decoy")}
    other = next(p for p in sorted((out / "tests").glob("test_*.py"))
                 if f"tests/{p.name}" not in planted)
    write(out, [{"file": f"tests/{other.name}", "kind": "test",
                 "why": "never exercises the boundary case"}])
    r = grade_scale.grade(task, out)
    assert not r["false_alarms"] and len(r["notes"]) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
