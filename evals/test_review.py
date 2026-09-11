"""Tests for durable review records and the supervisor review gate.

Drives the MCP bus server over real JSON-RPC, and verifies that the supervisor
gates completion on an approved review without human polling.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from rpc_util import ROOT, check, rpc, text_of

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam.supervisor import Supervisor          # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner          # noqa: E402


def make_team(prefix="agyteam-rev-") -> Path:
    td = Path(tempfile.mkdtemp(prefix=prefix)) / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "coordinates"},
        {"name": "coder", "role": "implements"},
        {"name": "syseng", "role": "verifies"}]}))
    return td


def test_mcp_review_tools() -> tuple[int, int]:
    """Test record_review and list_reviews over real MCP JSON-RPC."""
    print("\n== MCP review tools (record_review, list_reviews) ==")
    td = make_team("agyteam-mcp-rev-")

    # Fixture test files for verification gate
    pass_file = td / "test_pass.py"
    pass_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    fail_file = td / "test_fail.py"
    fail_file.write_text("def test_ng():\n    assert False\n", encoding="utf-8")

    def call(agent, calls):
        return rpc("agyteam.mcp_bus", [], calls,
                   env={"AGYTEAM_AGENT": agent, "AGYTEAM_TEAM_DIR": str(td)})

    # 1. Missing reviews.jsonl is normal, not an error
    r_empty = call("syseng", [("list_reviews", {})])[2:]
    out_empty = text_of(r_empty[0])

    # 2. Validation failures: proof_file missing, empty, not found, invalid verdict, empty what, verification mismatches
    r_val = call("syseng", [
        ("record_review", {"what": "feature X", "verdict": "approved"}),
        ("record_review", {"what": "feature X", "verdict": "approved", "proof_file": ""}),
        ("record_review", {"what": "feature X", "verdict": "approved", "proof_file": "nonexistent_test.py"}),
        ("record_review", {"what": "feature X", "verdict": "rejected", "proof_file": str(pass_file)}),
        ("record_review", {"what": "", "verdict": "approved", "proof_file": str(pass_file)}),
        ("record_review", {"what": "feature X", "verdict": "approved", "proof_file": str(fail_file)}),
        ("record_review", {"what": "feature X", "verdict": "changes_requested", "proof_file": str(pass_file)}),
    ])[2:]
    out_no_proof = text_of(r_val[0])
    out_empty_proof = text_of(r_val[1])
    out_missing_file = text_of(r_val[2])
    out_bad_verdict = text_of(r_val[3])
    out_no_what = text_of(r_val[4])
    out_approved_failing = text_of(r_val[5])
    out_changes_passing = text_of(r_val[6])

    # 3. Successful recording: approved and changes_requested
    r_rec = call("syseng", [
        ("record_review", {
            "what": "review_memory tool",
            "verdict": "approved",
            "proof_file": str(pass_file),
            "findings": "all test suites pass, clean keyword detection"
        }),
        ("record_review", {
            "what": "flaky retry loop",
            "verdict": "changes_requested",
            "proof_file": str(fail_file),
            "findings": "loop hangs indefinitely without backoff"
        }),
        ("list_reviews", {}),
    ])[2:]
    out_approved = text_of(r_rec[0])
    out_changes = text_of(r_rec[1])
    out_list = text_of(r_rec[2])

    # 4. Check reviews.jsonl contents directly
    reviews_file = td / "reviews.jsonl"
    lines = [json.loads(line) for line in reviews_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    has_proof_and_cases = (
        len(lines) == 2 and
        lines[0].get("proof_file") == str(pass_file) and
        "passed" in lines[0].get("cases_tried", "") and
        lines[1].get("proof_file") == str(fail_file) and
        ("failed" in lines[1].get("cases_tried", "") or "FAILED" in lines[1].get("cases_tried", ""))
    )

    # 5. Team directory isolation
    td2 = make_team("agyteam-mcp-rev2-")
    r_iso = rpc("agyteam.mcp_bus", [], [("list_reviews", {})],
                env={"AGYTEAM_AGENT": "syseng", "AGYTEAM_TEAM_DIR": str(td2)})[2:]
    out_iso = text_of(r_iso[0])

    return sum([
        check("missing reviews.jsonl returns [no reviews recorded]",
              out_empty == "[no reviews recorded]", out_empty),
        check("missing proof_file rejected loudly",
              out_no_proof.startswith("[error:") and "proof_file is required" in out_no_proof,
              out_no_proof),
        check("empty string proof_file rejected loudly",
              out_empty_proof.startswith("[error:") and "proof_file is required" in out_empty_proof,
              out_empty_proof),
        check("non-existent proof_file rejected loudly",
              out_missing_file.startswith("[error:") and "proof_file not found" in out_missing_file,
              out_missing_file),
        check("invalid verdict rejected loudly",
              out_bad_verdict.startswith("[error:") and "verdict must be" in out_bad_verdict,
              out_bad_verdict),
        check("empty what rejected loudly",
              out_no_what.startswith("[error:") and "what is required" in out_no_what,
              out_no_what),
        check("approved with failing proof rejected loudly",
              out_approved_failing.startswith("[error: verification failed: proof_file did not pass"),
              out_approved_failing),
        check("changes_requested with passing proof rejected loudly",
              out_changes_passing.startswith("[error: proof_file passed cleanly"),
              out_changes_passing),
        check("approved review recorded successfully",
              "review recorded: approved" in out_approved and "review_memory tool" in out_approved and str(pass_file) in out_approved,
              out_approved),
        check("changes_requested recorded successfully",
              "review recorded: changes_requested" in out_changes and "flaky retry loop" in out_changes and str(fail_file) in out_changes,
              out_changes),
        check("reviews.jsonl contains proof_file and captured pytest output",
              has_proof_and_cases, str(lines)),
        check("list_reviews distinguishes approved from changes_requested",
              "approved" in out_list and "changes_requested" in out_list, out_list),
        check("list_reviews includes reviewer, proof file, and findings",
              "syseng" in out_list and str(pass_file) in out_list and "loop hangs" in out_list,
              out_list),
        check("team directory isolation: reviews do not leak to another team",
              out_iso == "[no reviews recorded]", out_iso),
    ]), 14


def test_supervisor_review_gate() -> tuple[int, int]:
    """Test that supervisor flags unreviewed answers and honors approved reviews."""
    print("\n== supervisor review gate ==")
    agents = ["tpm", "coder", "syseng"]

    # 1. Unreviewed answer: agent messages user directly without any review
    td1 = make_team("agyteam-sup-gate1-")
    os.environ["AGYTEAM_TEAM_DIR"] = str(td1)
    runner1 = ScriptedRunner({"script": {
        "tpm": [["user", "here is your feature, all done!"]]
    }})
    sup1 = Supervisor(agents, runner1, team_dir=td1, max_hops=10, quiet=True)
    load_transport("user").send("tpm", "please build feature A")
    sup1.run_until_idle()
    stopped1 = sup1.stopped
    sup1.close()

    # 2. changes_requested review: review was recorded, but NOT approved
    td2 = make_team("agyteam-sup-gate2-")
    os.environ["AGYTEAM_TEAM_DIR"] = str(td2)
    with (td2 / "reviews.jsonl").open("w") as f:
        f.write(json.dumps({
            "ts": "2026-09-10 10:00:00",
            "reviewer": "syseng",
            "what": "feature B",
            "verdict": "changes_requested",
            "proof_file": "tests/test_b.py",
            "cases_tried": "1 failed in 0.01s",
            "findings": "defect found"
        }) + "\n")
    runner2 = ScriptedRunner({"script": {
        "tpm": [["user", "here is your feature B!"]]
    }})
    sup2 = Supervisor(agents, runner2, team_dir=td2, max_hops=10, quiet=True)
    load_transport("user").send("tpm", "please build feature B")
    sup2.run_until_idle()
    stopped2 = sup2.stopped
    sup2.close()

    # 3. Approved review recorded during episode: answer is marked reviewed
    td3 = make_team("agyteam-sup-gate3-")
    os.environ["AGYTEAM_TEAM_DIR"] = str(td3)

    class ReviewingRunner(ScriptedRunner):
        def wake(self, agent: str, message: str) -> str:
            if agent == "syseng":
                with (td3 / "reviews.jsonl").open("a") as f:
                    f.write(json.dumps({
                        "ts": "2026-09-10 10:05:00",
                        "reviewer": "syseng",
                        "what": "feature C",
                        "verdict": "approved",
                        "proof_file": "tests/test_c.py",
                        "cases_tried": "1 passed in 0.01s",
                        "findings": "all passed"
                    }) + "\n")
            return super().wake(agent, message)

    runner3 = ReviewingRunner({"script": {
        "tpm": [["coder", "implement feature C"]],
        "coder": [["syseng", "please verify feature C"]],
        "syseng": [["user", "verified feature C, ready for production"]]
    }})
    sup3 = Supervisor(agents, runner3, team_dir=td3, max_hops=10, quiet=True)
    load_transport("user").send("tpm", "please build feature C")
    sup3.run_until_idle()
    stopped3 = sup3.stopped
    sup3.close()

    return sum([
        check("episode without review reported as unreviewed",
              stopped1 == "the user was answered (unreviewed)", stopped1),
        check("episode with changes_requested reported as unreviewed",
              stopped2 == "the user was answered (unreviewed)", stopped2),
        check("episode with approved review reported as normally answered",
              stopped3 == "the user was answered", stopped3),
    ]), 3


if __name__ == "__main__":
    totals = [test_mcp_review_tools(), test_supervisor_review_gate()]
    got, want = sum(s for s, _ in totals), sum(t for _, t in totals)
    print(f"\n== review tests: {got}/{want} ==")
    sys.exit(0 if got == want else 1)
