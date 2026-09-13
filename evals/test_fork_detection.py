"""Tests for day-one Git hygiene and worktree fork detection mechanism.

Validates:
1. Clean repo detection with healthy upstream tracking.
2. Detection of unpushed commits ahead of upstream.
3. Detection of missing upstream tracking when remote branch exists.
4. Detection of new branch without remote tracking.
5. Detection of worktree divergence across active git worktrees.
6. Non-git directory graceful fallback.
7. CLI entrypoint behavior (--strict, --json, exit codes).
8. Executable entrypoint convention (__name__ == "__main__").
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from agyteam.git_hygiene import (
    check_git_hygiene,
    find_git_root,
    format_hygiene_report,
    get_current_branch,
    get_current_head,
    get_unpushed_commits,
    get_upstream_branch,
    get_worktree_divergence,
    main,
)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Helper to run git commands in synthetic test environments."""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc


def _init_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Set up a local repo and a bare remote with initial commit on main."""
    remote_dir = tmp_path / "remote.git"
    _run_git(["init", "--bare", str(remote_dir)], cwd=tmp_path)

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    _run_git(["init", "-b", "main"], cwd=local_dir)
    _run_git(["config", "user.name", "AgyTeam Tester"], cwd=local_dir)
    _run_git(["config", "user.email", "tester@example.com"], cwd=local_dir)

    # Initial commit
    (local_dir / "README.md").write_text("initial commit\n", encoding="utf-8")
    _run_git(["add", "README.md"], cwd=local_dir)
    _run_git(["commit", "-m", "initial commit"], cwd=local_dir)

    # Setup remote and push tracking
    _run_git(["remote", "add", "origin", str(remote_dir)], cwd=local_dir)
    _run_git(["push", "-u", "origin", "main"], cwd=local_dir)

    return local_dir, remote_dir


def test_clean_repo_hygiene(tmp_path: Path):
    """Clean repo with healthy upstream tracking passes hygiene checks."""
    local_dir, _ = _init_repo_with_remote(tmp_path)

    status = check_git_hygiene(local_dir)

    assert status["is_git"] is True
    assert status["clean"] is True
    assert status["divergent"] is False
    assert status["current_branch"] == "main"
    assert status["warnings"] == []
    assert status["remediations"] == []
    assert status["unpushed"]["has_upstream"] is True
    assert status["unpushed"]["ahead"] == 0
    assert status["unpushed"]["behind"] == 0

    report = format_hygiene_report(status)
    assert "✓ Clean" in report
    assert "in sync" in report


def test_unpushed_commits_detected(tmp_path: Path):
    """Unpushed commits are detected with actionable git push remediation."""
    local_dir, _ = _init_repo_with_remote(tmp_path)

    # Create two local commits without pushing
    (local_dir / "file1.txt").write_text("commit 1\n", encoding="utf-8")
    _run_git(["add", "file1.txt"], cwd=local_dir)
    _run_git(["commit", "-m", "feat: first unpushed change"], cwd=local_dir)

    (local_dir / "file2.txt").write_text("commit 2\n", encoding="utf-8")
    _run_git(["add", "file2.txt"], cwd=local_dir)
    _run_git(["commit", "-m", "feat: second unpushed change"], cwd=local_dir)

    status = check_git_hygiene(local_dir)

    assert status["clean"] is False
    assert status["unpushed"]["ahead"] == 2
    assert len(status["unpushed"]["commits"]) == 2
    assert any("2 unpushed commit(s) ahead of 'origin/main'" in w for w in status["warnings"])
    assert "git push" in status["remediations"]

    report = format_hygiene_report(status)
    assert "GIT HYGIENE ALERT" in report
    assert "2 unpushed commit(s)" in report
    assert "$ git push" in report


def test_missing_upstream_tracking_detected(tmp_path: Path):
    """Branch with remote peer but missing tracking config triggers set-upstream remediation."""
    local_dir, _ = _init_repo_with_remote(tmp_path)

    # Create branch and push WITHOUT -u
    _run_git(["checkout", "-b", "feature/divergence"], cwd=local_dir)
    (local_dir / "feat.txt").write_text("feature branch\n", encoding="utf-8")
    _run_git(["add", "feat.txt"], cwd=local_dir)
    _run_git(["commit", "-m", "feat: branch commit"], cwd=local_dir)

    # Push to origin without setting upstream tracking
    _run_git(["push", "origin", "feature/divergence"], cwd=local_dir)

    # Confirm upstream is indeed not set in git config
    code = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "feature/divergence@{u}"],
        cwd=local_dir,
        capture_output=True,
    ).returncode
    assert code != 0

    status = check_git_hygiene(local_dir)

    assert status["clean"] is False
    assert status["unpushed"]["has_upstream"] is False
    assert any("NO upstream tracking set, but 'origin/feature/divergence' exists remotely" in w for w in status["warnings"])
    assert any("git branch --set-upstream-to=origin/feature/divergence feature/divergence" in r for r in status["remediations"])


def test_missing_upstream_and_no_remote_branch(tmp_path: Path):
    """Local branch that has never been pushed alerts with push -u recommendation."""
    local_dir, _ = _init_repo_with_remote(tmp_path)

    _run_git(["checkout", "-b", "local-only-branch"], cwd=local_dir)
    (local_dir / "local.txt").write_text("local only\n", encoding="utf-8")
    _run_git(["add", "local.txt"], cwd=local_dir)
    _run_git(["commit", "-m", "feat: local commit"], cwd=local_dir)

    status = check_git_hygiene(local_dir)

    assert status["clean"] is False
    assert any("has no remote tracking branch" in w for w in status["warnings"])
    assert any("git push -u origin local-only-branch" in r for r in status["remediations"])


def test_worktree_divergence_detected(tmp_path: Path):
    """Worktree divergence between two checkouts is discovered with exact diff command."""
    local_dir, _ = _init_repo_with_remote(tmp_path)

    # Create a second worktree
    wt_dir = tmp_path / "second-worktree"
    _run_git(["worktree", "add", "-b", "team/alternate", str(wt_dir), "main"], cwd=local_dir)

    # Configure committer in worktree
    _run_git(["config", "user.name", "AgyTeam Tester"], cwd=wt_dir)
    _run_git(["config", "user.email", "tester@example.com"], cwd=wt_dir)

    # Commit on second worktree
    (wt_dir / "alt.txt").write_text("alternate worktree content\n", encoding="utf-8")
    _run_git(["add", "alt.txt"], cwd=wt_dir)
    _run_git(["commit", "-m", "feat: commit in second worktree"], cwd=wt_dir)

    # Commit on local main as well to create true fork
    (local_dir / "main.txt").write_text("main repo content\n", encoding="utf-8")
    _run_git(["add", "main.txt"], cwd=local_dir)
    _run_git(["commit", "-m", "feat: commit in primary repo"], cwd=local_dir)

    # Check worktree divergence from local_dir
    worktrees = get_worktree_divergence(local_dir)
    assert len(worktrees) == 2

    # Check primary vs secondary
    primary = next(wt for wt in worktrees if wt["is_current"])
    secondary = next(wt for wt in worktrees if not wt["is_current"])

    assert secondary["divergent"] is True
    assert secondary["other_ahead"] == 1
    assert secondary["current_ahead"] == 1
    assert "forked:" in secondary["summary"]
    assert "git diff" in secondary["remediation"]

    # Full hygiene check
    status = check_git_hygiene(local_dir)
    assert status["clean"] is False
    assert status["divergent"] is True
    assert any("Worktree divergence detected" in w for w in status["warnings"])
    assert any("git diff" in r for r in status["remediations"])


def test_non_git_directory_fallback(tmp_path: Path):
    """Non-git directory does not crash and returns graceful skipped status."""
    non_git = tmp_path / "empty_dir"
    non_git.mkdir()

    root = find_git_root(non_git)
    assert root is None

    status = check_git_hygiene(non_git)
    assert status["is_git"] is False
    assert status["clean"] is True
    assert status["divergent"] is False
    assert "Not inside a git repository" in status["warnings"][0]

    report = format_hygiene_report(status)
    assert "Not a git repository" in report


def test_cli_strict_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """CLI exits 0 on clean repo, 1 on hygiene/fork violations when --strict is passed."""
    local_dir, _ = _init_repo_with_remote(tmp_path)

    # Clean repo -> returns 0
    rc_clean = main(["--strict", "--repo-dir", str(local_dir)])
    assert rc_clean == 0

    # Non-strict on dirty repo -> returns 0
    (local_dir / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    _run_git(["add", "dirty.txt"], cwd=local_dir)
    _run_git(["commit", "-m", "feat: unpushed"], cwd=local_dir)

    rc_non_strict = main(["--repo-dir", str(local_dir)])
    assert rc_non_strict == 0

    # Strict on dirty repo -> returns 1
    rc_strict = main(["--strict", "--repo-dir", str(local_dir)])
    assert rc_strict == 1


def test_cli_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """CLI produces valid structured JSON when --json flag is specified."""
    local_dir, _ = _init_repo_with_remote(tmp_path)

    rc = main(["--json", "--repo-dir", str(local_dir)])
    assert rc == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["is_git"] is True
    assert payload["clean"] is True
    assert payload["current_branch"] == "main"


def test_git_hygiene_error_handling(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch):
    """Hygiene check failure returns clean=False with error message formatted loudly."""
    local_dir, _ = _init_repo_with_remote(tmp_path)

    def _exploding_branch(*args, **kwargs):
        raise RuntimeError("git execution failed with segfault")

    monkeypatch.setattr("agyteam.git_hygiene.get_current_branch", _exploding_branch)

    status = check_git_hygiene(local_dir)
    assert status["is_git"] is False
    assert status["clean"] is False
    assert "git execution failed with segfault" in status["error"]
    assert any("git execution failed with segfault" in w for w in status["warnings"])

    report = format_hygiene_report(status)
    assert "GIT HYGIENE ERROR: Audit failed with exception!" in report
    assert "git execution failed with segfault" in report

    # Verify CLI strict exit code on error
    rc = main(["--strict", "--repo-dir", str(local_dir)])
    assert rc == 1


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
