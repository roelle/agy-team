"""Day-one Git hygiene and worktree fork detection.

Detects silent branch and worktree divergence before it compounds:
1. Audits all active git worktrees via `git worktree list --porcelain`.
2. Computes commit divergence (ahead/behind) between active worktrees.
3. Checks upstream tracking configuration and unpushed commits against remote.
4. Generates copy-pasteable remediation commands for developers.
5. Pure standard library (subprocess, pathlib, shutil, json).
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


def _run_git(args: list[str], cwd: Path | None = None, timeout: float = 5.0) -> tuple[int, str, str]:
    """Execute git command returning (exit_code, stdout, stderr)."""
    git_bin = shutil.which("git")
    if not git_bin:
        return 127, "", "git command not found in PATH"
    try:
        proc = subprocess.run(
            [git_bin, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"git command timed out after {timeout}s: {' '.join(args)}"
    except Exception as e:
        return 1, "", str(e)


def find_git_root(path: str | Path | None = None) -> Path | None:
    """Find nearest enclosing git repository or worktree root."""
    target_dir = Path(path).expanduser().resolve() if path else Path.cwd().resolve()
    code, stdout, _ = _run_git(["rev-parse", "--show-toplevel"], cwd=target_dir)
    if code == 0 and stdout:
        return Path(stdout).resolve()

    # Fallback to filesystem traversal
    cur = target_dir
    for d in (cur, *cur.parents):
        if (d / ".git").exists():
            return d.resolve()
    return None


def get_current_branch(repo_dir: str | Path | None = None) -> str | None:
    """Get current active branch name or None if detached/unknown."""
    cwd = Path(repo_dir).resolve() if repo_dir else None
    code, stdout, _ = _run_git(["branch", "--show-current"], cwd=cwd)
    if code == 0 and stdout:
        return stdout
    code, stdout, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    if code == 0 and stdout and stdout != "HEAD":
        return stdout
    return None


def get_current_head(repo_dir: str | Path | None = None) -> str | None:
    """Get current HEAD commit SHA."""
    cwd = Path(repo_dir).resolve() if repo_dir else None
    code, stdout, _ = _run_git(["rev-parse", "HEAD"], cwd=cwd)
    return stdout if code == 0 and stdout else None


def get_upstream_branch(repo_dir: str | Path | None = None, branch: str | None = None) -> str | None:
    """Get upstream tracking branch (e.g. 'origin/main'), or None if not configured."""
    cwd = Path(repo_dir).resolve() if repo_dir else None
    ref = f"{branch}@{{u}}" if branch else "@{u}"
    code, stdout, _ = _run_git(["rev-parse", "--abbrev-ref", ref], cwd=cwd)
    return stdout if code == 0 and stdout else None


def get_unpushed_commits(repo_dir: str | Path | None = None) -> dict[str, Any]:
    """Audit unpushed commits and upstream tracking status.

    Checks:
    - Upstream configured branch (@{u}).
    - If missing, checks existence of remote branch origin/<branch> or default origin/main.
    - Counts commits ahead and behind.
    - Returns actionable warnings and remediation commands.
    """
    cwd = Path(repo_dir).resolve() if repo_dir else None
    branch = get_current_branch(cwd)
    head = get_current_head(cwd)

    result: dict[str, Any] = {
        "branch": branch,
        "head": head,
        "has_upstream": False,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
        "commits": [],
        "warnings": [],
        "remediations": [],
    }

    if not head:
        return result

    if not branch:
        result["warnings"].append("HEAD is detached from any local branch")
        return result

    upstream = get_upstream_branch(cwd, branch)
    if upstream:
        result["has_upstream"] = True
        result["upstream"] = upstream
        code, stdout, _ = _run_git(["rev-list", "--left-right", "--count", f"{upstream}...HEAD"], cwd=cwd)
        if code == 0 and stdout:
            parts = stdout.split()
            if len(parts) >= 2:
                behind = int(parts[0])
                ahead = int(parts[1])
                result["behind"] = behind
                result["ahead"] = ahead

        if result["ahead"] > 0:
            c_code, c_out, _ = _run_git(["log", "--oneline", f"{upstream}..HEAD"], cwd=cwd)
            if c_code == 0 and c_out:
                for line in c_out.splitlines():
                    tokens = line.split(" ", 1)
                    result["commits"].append({
                        "hash": tokens[0],
                        "subject": tokens[1] if len(tokens) > 1 else "",
                    })
            result["warnings"].append(
                f"Local branch '{branch}' has {result['ahead']} unpushed commit(s) ahead of '{upstream}'"
            )
            result["remediations"].append(f"git push")

        if result["behind"] > 0:
            result["warnings"].append(
                f"Local branch '{branch}' is {result['behind']} commit(s) behind '{upstream}'"
            )
            result["remediations"].append(f"git pull --rebase")
    else:
        # No upstream tracking configured
        result["has_upstream"] = False
        # Check if origin/<branch> exists
        code, _, _ = _run_git(["rev-parse", "--verify", f"origin/{branch}"], cwd=cwd)
        if code == 0:
            # origin/<branch> exists remotely!
            code, stdout, _ = _run_git(["rev-list", "--left-right", "--count", f"origin/{branch}...HEAD"], cwd=cwd)
            if code == 0 and stdout:
                parts = stdout.split()
                if len(parts) >= 2:
                    result["behind"] = int(parts[0])
                    result["ahead"] = int(parts[1])
            result["warnings"].append(
                f"Branch '{branch}' has NO upstream tracking set, but 'origin/{branch}' exists remotely"
            )
            result["remediations"].append(f"git branch --set-upstream-to=origin/{branch} {branch}")
            if result["ahead"] > 0:
                result["remediations"].append(f"git push origin {branch}")
        else:
            # Check default remote branch origin/main or origin/master
            code_main, _, _ = _run_git(["rev-parse", "--verify", "origin/main"], cwd=cwd)
            compare_ref = "origin/main" if code_main == 0 else None
            if not compare_ref:
                code_master, _, _ = _run_git(["rev-parse", "--verify", "origin/master"], cwd=cwd)
                if code_master == 0:
                    compare_ref = "origin/master"

            if compare_ref:
                code, stdout, _ = _run_git(["rev-list", "--count", f"{compare_ref}..HEAD"], cwd=cwd)
                if code == 0 and stdout:
                    try:
                        result["ahead"] = int(stdout)
                    except ValueError:
                        pass
                result["warnings"].append(
                    f"Branch '{branch}' has no remote tracking branch (diverged from {compare_ref} by {result['ahead']} commits)"
                )
            else:
                result["warnings"].append(
                    f"Branch '{branch}' has no upstream configured and no remote origin found"
                )
            result["remediations"].append(f"git push -u origin {branch}")

    return result


def get_worktree_divergence(repo_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """Discover all active git worktrees and measure divergence from current HEAD.

    Parses `git worktree list --porcelain`.
    For each worktree:
    - Identifies path, branch, and HEAD.
    - Evaluates commit divergence against current worktree HEAD using rev-list --left-right.
    - Flags forks between worktrees.
    """
    cwd = Path(repo_dir).resolve() if repo_dir else None
    root = find_git_root(cwd)
    if not root:
        return []

    cur_head = get_current_head(root)
    cur_branch = get_current_branch(root)

    code, stdout, _ = _run_git(["worktree", "list", "--porcelain"], cwd=root)
    if code != 0 or not stdout:
        return []

    worktrees: list[dict[str, Any]] = []
    blocks = stdout.strip().split("\n\n")

    for block in blocks:
        if not block.strip():
            continue
        entry: dict[str, str] = {}
        for line in block.strip().splitlines():
            tokens = line.split(" ", 1)
            key = tokens[0]
            val = tokens[1] if len(tokens) > 1 else ""
            entry[key] = val

        wt_path_str = entry.get("worktree")
        if not wt_path_str:
            continue
        wt_path = Path(wt_path_str).resolve()
        wt_head = entry.get("HEAD", "")
        branch_ref = entry.get("branch", "")
        if branch_ref.startswith("refs/heads/"):
            wt_branch = branch_ref[len("refs/heads/"):]
        elif branch_ref:
            wt_branch = branch_ref
        elif "detached" in entry:
            wt_branch = "(detached)"
        elif "bare" in entry:
            wt_branch = "(bare)"
        else:
            wt_branch = "unknown"

        is_current = (wt_path == root)

        other_ahead = 0
        cur_ahead = 0
        divergent = False
        summary = "in sync"
        remediation = None

        if cur_head and wt_head:
            if cur_head == wt_head:
                if not is_current and wt_branch != cur_branch:
                    divergent = True
                    summary = f"same commit as HEAD but on different branch '{wt_branch}'"
                    remediation = f"Review worktree branches: {cur_branch} vs {wt_branch}"
            else:
                divergent = True
                code, lr_out, _ = _run_git(["rev-list", "--left-right", "--count", f"{wt_head}...{cur_head}"], cwd=root)
                if code == 0 and lr_out:
                    parts = lr_out.split()
                    if len(parts) >= 2:
                        other_ahead = int(parts[0])
                        cur_ahead = int(parts[1])

                if other_ahead > 0 and cur_ahead > 0:
                    summary = f"forked: {other_ahead} commits in worktree, {cur_ahead} commits in current workspace"
                    remediation = f"git diff {wt_branch or wt_head[:8]}...{cur_branch or cur_head[:8]}"
                elif cur_ahead > 0:
                    summary = f"{cur_ahead} commits behind current HEAD"
                    remediation = f"git diff {wt_head[:8]}..{cur_head[:8]}"
                elif other_ahead > 0:
                    summary = f"{other_ahead} commits ahead of current HEAD"
                    remediation = f"git diff {cur_head[:8]}..{wt_head[:8]}"

        worktrees.append({
            "path": str(wt_path),
            "head": wt_head,
            "branch": wt_branch,
            "is_current": is_current,
            "divergent": divergent,
            "other_ahead": other_ahead,
            "current_ahead": cur_ahead,
            "summary": summary,
            "remediation": remediation,
        })

    return worktrees


def check_git_hygiene(repo_dir: str | Path | None = None, strict: bool = False) -> dict[str, Any]:
    """Perform a full git hygiene audit across branch tracking and worktree divergence.

    Returns structured dictionary with hygiene evaluation, detected warnings,
    and concrete remediation steps.
    """
    try:
        cwd = Path(repo_dir).resolve() if repo_dir else Path.cwd().resolve()
        root = find_git_root(cwd)

        if not root:
            return {
                "is_git": False,
                "repo_root": None,
                "current_branch": None,
                "current_head": None,
                "clean": True,
                "divergent": False,
                "uncommitted": False,
                "uncommitted_files": 0,
                "worktrees": [],
                "unpushed": None,
                "warnings": ["Not inside a git repository (git hygiene audit skipped)."],
                "remediations": [],
            }

        branch = get_current_branch(root)
        head = get_current_head(root)
        unpushed_info = get_unpushed_commits(root)
        worktrees = get_worktree_divergence(root)

        # Check uncommitted changes
        code, status_out, _ = _run_git(["status", "--porcelain"], cwd=root)
        dirty_lines = [line for line in status_out.splitlines() if line.strip()] if code == 0 else []
        uncommitted = len(dirty_lines) > 0

        warnings: list[str] = []
        remediations: list[str] = []

        # Aggregate unpushed warnings & remediations
        for w in unpushed_info.get("warnings", []):
            warnings.append(w)
        for r in unpushed_info.get("remediations", []):
            if r not in remediations:
                remediations.append(r)

        # Aggregate worktree divergence
        other_divergent = [wt for wt in worktrees if not wt["is_current"] and wt["divergent"]]
        divergent = bool(other_divergent) or (unpushed_info.get("ahead", 0) > 0) or not unpushed_info.get("has_upstream", False)

        for wt in other_divergent:
            warnings.append(
                f"Worktree divergence detected at '{wt['path']}' (branch: {wt['branch']}, HEAD: {wt['head'][:8]}): {wt['summary']}"
            )
            if wt.get("remediation") and wt["remediation"] not in remediations:
                remediations.append(wt["remediation"])

        if uncommitted:
            warnings.append(f"{len(dirty_lines)} uncommitted file(s) in working directory")
            remediations.append("git status")

        # Clean is True only when:
        # 1. Inside git repo
        # 2. Upstream tracking configured
        # 3. 0 commits ahead or behind upstream
        # 4. 0 worktree divergence
        # 5. Clean working directory
        clean = (
            unpushed_info.get("has_upstream", False)
            and unpushed_info.get("ahead", 0) == 0
            and unpushed_info.get("behind", 0) == 0
            and not other_divergent
            and not uncommitted
        )

        return {
            "is_git": True,
            "repo_root": str(root),
            "current_branch": branch,
            "current_head": head,
            "clean": clean,
            "divergent": divergent,
            "uncommitted": uncommitted,
            "uncommitted_files": len(dirty_lines),
            "worktrees": worktrees,
            "unpushed": unpushed_info,
            "warnings": warnings,
            "remediations": remediations,
        }
    except Exception as e:
        return {
            "is_git": False,
            "repo_root": None,
            "current_branch": None,
            "current_head": None,
            "clean": False,
            "divergent": False,
            "uncommitted": False,
            "uncommitted_files": 0,
            "worktrees": [],
            "unpushed": None,
            "error": str(e),
            "warnings": [f"Git hygiene audit failed: {e}"],
            "remediations": [],
        }


def format_hygiene_report(status: dict[str, Any], verbose: bool = False) -> str:
    """Format git hygiene status dictionary into terminal-friendly markdown report."""
    if status.get("error"):
        lines = [
            "=" * 68,
            "⚠️  GIT HYGIENE ERROR: Audit failed with exception!",
            "=" * 68,
            f"Error: {status['error']}",
        ]
        if status.get("warnings"):
            lines.append("\nIssues Detected:")
            for w in status["warnings"]:
                lines.append(f"  • {w}")
        lines.append("=" * 68)
        return "\n".join(lines)

    if not status.get("is_git"):
        return "[git-hygiene] Not a git repository (hygiene audit skipped)."

    branch = status.get("current_branch") or "(detached)"
    head = (status.get("current_head") or "")[:8]
    clean = status.get("clean", False)
    warnings = status.get("warnings", [])
    remediations = status.get("remediations", [])
    worktrees = status.get("worktrees", [])

    lines: list[str] = []

    if clean:
        lines.append(f"[git-hygiene] ✓ Clean: branch '{branch}' @ {head} is in sync. No worktree divergence.")
        return "\n".join(lines)

    lines.append("=" * 68)
    lines.append("⚠️  GIT HYGIENE ALERT: Potential Branch/Worktree Fork Detected!")
    lines.append("=" * 68)
    lines.append(f"Repository Root: {status.get('repo_root')}")
    lines.append(f"Current Workspace: branch '{branch}' (HEAD: {head})")

    if worktrees and len(worktrees) > 1:
        lines.append("\nActive Worktrees:")
        for wt in worktrees:
            curr_marker = " [CURRENT]" if wt.get("is_current") else ""
            lines.append(
                f"  • {wt['path']} (branch: {wt['branch']}, HEAD: {wt['head'][:8]}){curr_marker} — {wt['summary']}"
            )

    if warnings:
        lines.append("\nIssues Detected:")
        for w in warnings:
            lines.append(f"  • {w}")

    if remediations:
        lines.append("\nSuggested Remediation Commands:")
        for r in remediations:
            lines.append(f"  $ {r}")

    lines.append("=" * 68)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for git hygiene check: python -m agyteam.git_hygiene"""
    parser = argparse.ArgumentParser(
        prog="python -m agyteam.git_hygiene",
        description="Audit git hygiene, unpushed commits, and worktree divergence.",
    )
    parser.add_argument("--repo-dir", help="Path to repository or worktree root")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if issues/fork detected")
    parser.add_argument("--quiet", action="store_true", help="Only output if warnings/issues detected")
    parser.add_argument("--verbose", action="store_true", help="Include detailed worktree information")

    args = parser.parse_args(argv)

    status = check_git_hygiene(repo_dir=args.repo_dir, strict=args.strict)

    if args.json:
        print(json.dumps(status, indent=2))
    else:
        if not args.quiet or not status.get("clean"):
            print(format_hygiene_report(status, verbose=args.verbose))

    if args.strict and not status.get("clean"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
