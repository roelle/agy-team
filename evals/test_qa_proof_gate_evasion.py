import json
import os
import shutil
import tempfile
from pathlib import Path

from evals.fixture_transport import SqliteTransport
from agyteam.mcp_bus import _record_review
from agyteam.transport import load as load_transport

ROOT = Path(__file__).resolve().parent.parent

def check(name, cond, msg=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}")
    if not cond and msg:
        print(f"       detail: {msg.strip()}")
    assert cond, f"{name} failed: {msg}"
    return (1, 1)

def test_qa_review_gate_evasion() -> tuple[int, int]:
    print("\n== test_qa_review_gate_evasion ==")
    # 1. Setup workspace
    sqlite_db = Path(tempfile.mkdtemp(prefix="agyteam-qa-qa-")) / "bus.db"
    team_dir = sqlite_db.parent
    sql_cfg = json.dumps({"db": str(sqlite_db), "roster": [{"name": "qa", "role": "verifies"}]})
    
    saved_env = {
        "AGYTEAM_TEAM_DIR": os.environ.get("AGYTEAM_TEAM_DIR"),
        "AGYTEAM_BUS_TRANSPORT": os.environ.get("AGYTEAM_BUS_TRANSPORT"),
        "AGYTEAM_BUS_CONFIG": os.environ.get("AGYTEAM_BUS_CONFIG"),
    }
    os.environ["AGYTEAM_TEAM_DIR"] = str(team_dir)
    os.environ["AGYTEAM_BUS_TRANSPORT"] = "fixture_transport:SqliteTransport"
    os.environ["AGYTEAM_BUS_CONFIG"] = sql_cfg

    try:
        t = load_transport("qa")
        
        # Paths to evasion files we created earlier
        syntax = ROOT / "evals" / "test_qa_proof_syntax.py"
        collection = ROOT / "evals" / "test_qa_proof_collection.py"
        empty = ROOT / "evals" / "test_qa_proof_empty.py"
        assertion = ROOT / "evals" / "test_qa_proof_assertion.py"
        passing = ROOT / "evals" / "test_qa_proof_pass.py"
        
        syntax.write_text("def test_syntax():\n    assert 1 == 1 +\n")
        collection.write_text("import non_existent_module\ndef test_col():\n    assert 1 == 1\n")
        empty.write_text("def not_a_test():\n    assert 1 == 1\n")
        assertion.write_text("def test_fail():\n    assert 1 == 2\n")
        passing.write_text("def test_pass():\n    assert 1 == 1\n")

        checks = []

        # 2. Rejects evasions for changes_requested
        r_syntax = _record_review(t, {"what": "syntax", "verdict": "changes_requested", "cases_tried": "1", "findings": "1", "proof_file": str(syntax.relative_to(ROOT))})
        checks.append(check("Syntax error rejected", "[error:" in r_syntax and "SyntaxError" in r_syntax, r_syntax))
        
        r_col = _record_review(t, {"what": "col", "verdict": "changes_requested", "cases_tried": "1", "findings": "1", "proof_file": str(collection.relative_to(ROOT))})
        checks.append(check("Collection error rejected", "[error:" in r_col and "ModuleNotFoundError: No module named 'non_existent_module'" in r_col, r_col))
        
        r_empty = _record_review(t, {"what": "empty", "verdict": "changes_requested", "cases_tried": "1", "findings": "1", "proof_file": str(empty.relative_to(ROOT))})
        checks.append(check("Empty (exit code 5) rejected", "[error:" in r_empty and "no tests collected" in r_empty, r_empty))
        
        # 3. Valid assertion failure
        r_ast = _record_review(t, {"what": "assert", "verdict": "changes_requested", "cases_tried": "1", "findings": "1", "proof_file": str(assertion.relative_to(ROOT))})
        checks.append(check("Assertion failure accepted", "[review recorded:" in r_ast, r_ast))

        # 4. Valid passing test
        r_pass_appr = _record_review(t, {"what": "pass", "verdict": "approved", "cases_tried": "1", "findings": "1", "proof_file": str(passing.relative_to(ROOT))})
        checks.append(check("Passing test accepted for approved", "[review recorded:" in r_pass_appr, r_pass_appr))
        
        r_pass_req = _record_review(t, {"what": "pass", "verdict": "changes_requested", "cases_tried": "1", "findings": "1", "proof_file": str(passing.relative_to(ROOT))})
        checks.append(check("Passing test rejected for changes_requested", "[error:" in r_pass_req and "passed cleanly" in r_pass_req, r_pass_req))

        got = sum(s for s, _ in checks)
        want = sum(n for _, n in checks)
        
        assert got == want, f"got {got}/{want}"

    finally:
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        shutil.rmtree(team_dir, ignore_errors=True)
        # Cleanup proof files
        syntax.unlink(missing_ok=True)
        collection.unlink(missing_ok=True)
        empty.unlink(missing_ok=True)
        assertion.unlink(missing_ok=True)
        passing.unlink(missing_ok=True)

    return got, want

if __name__ == "__main__":
    test_qa_review_gate_evasion()
