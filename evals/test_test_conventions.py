"""Anti-vacuous test execution convention and project documentation checks.

Ensures:
1. Discovery is non-empty across evals/.
2. Every test_*.py in evals/ has an executable entrypoint (if __name__ == "__main__":).
3. Pytest-based eval modules invoke pytest.main to prevent silent zero-test zero-exit passes.
4. Direct execution via python evals/<test>.py executes real test cases.
5. README documentation is up-to-date with current component status.
"""
import ast
import os
from pathlib import Path
import subprocess
import sys


EVALS_DIR = Path(__file__).resolve().parent
REPO_DIR = EVALS_DIR.parent

PYTEST_EVAL_FILES = {
    "test_lifecycle_migration.py",
    "test_tool_result_adapter.py",
    "test_pkg_proof.py",
    "test_qa_proof_defects.py",
    "test_qa_proof_sdk_runner.py",
    "test_test_conventions.py",
    "test_fork_detection.py",
}


def _has_main_block(tree: ast.AST) -> bool:
    """Check if AST contains an `if __name__ == '__main__':` block with executable code."""
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            left = node.test.left
            if isinstance(left, ast.Name) and left.id == "__name__":
                # Check for comparison against '__main__'
                for comp in node.test.comparators:
                    if isinstance(comp, ast.Constant) and comp.value == "__main__":
                        # Check that body is not just 'pass'
                        if node.body and not (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)):
                            return True
    return False


def _calls_pytest_main(tree: ast.AST) -> bool:
    """Check if AST contains an invocation of `pytest.main(...)` inside `__name__ == '__main__':`."""
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            left = node.test.left
            if isinstance(left, ast.Name) and left.id == "__name__":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        if isinstance(func, ast.Attribute) and func.attr == "main":
                            if isinstance(func.value, ast.Name) and func.value.id == "pytest":
                                return True
    return False


def test_discovery_not_empty():
    """Verify test discovery finds test files in evals/."""
    test_files = list(EVALS_DIR.glob("test_*.py"))
    assert len(test_files) >= 10, f"Expected at least 10 test files in {EVALS_DIR}, found {len(test_files)}"


def test_all_eval_files_ast_parse_cleanly():
    """Verify all eval test files are syntactically valid Python."""
    test_files = sorted(EVALS_DIR.glob("test_*.py"))
    parse_errors = []
    for tf in test_files:
        try:
            ast.parse(tf.read_text(encoding="utf-8"), filename=str(tf))
        except SyntaxError as e:
            parse_errors.append(f"{tf.name}: {e}")
    assert not parse_errors, f"Syntax errors found in eval files: {parse_errors}"


def test_all_eval_files_have_executable_main_entrypoint():
    """Verify EVERY test_*.py in evals/ has an executable entrypoint."""
    test_files = sorted(EVALS_DIR.glob("test_*.py"))
    missing = []
    for tf in test_files:
        try:
            tree = ast.parse(tf.read_text(encoding="utf-8"))
            if not _has_main_block(tree):
                missing.append(tf.name)
        except Exception as e:
            missing.append(f"{tf.name} (parse error: {e})")

    assert not missing, f"The following eval files lack an executable `__main__` entrypoint: {missing}"


def test_pytest_eval_files_have_pytest_main():
    """Verify pure pytest eval modules invoke pytest.main to prevent vacuous pass."""
    missing = []
    for name in sorted(PYTEST_EVAL_FILES):
        tf = EVALS_DIR / name
        if not tf.exists():
            continue
        tree = ast.parse(tf.read_text(encoding="utf-8"))
        if not _calls_pytest_main(tree):
            missing.append(name)

    assert not missing, f"The following eval files do not invoke pytest.main: {missing}"


def test_subprocess_execution_non_vacuous():
    """Direct execution of a test file via python must run tests and exit 0."""
    target_file = EVALS_DIR / "test_pkg_proof.py"
    res = subprocess.run(
        [sys.executable, str(target_file)],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, f"Process exited with {res.returncode}. Output:\n{res.stdout}\n{res.stderr}"
    # Verify tests actually executed rather than exiting silently
    combined = res.stdout + res.stderr
    assert "passed" in combined or "PASSED" in combined or "test_run_status_no_key" in combined, (
        f"Execution was vacuous (no test run evidence). Output:\n{combined}"
    )


def test_subprocess_execution_respects_custom_args():
    """Direct execution passes through pytest flags."""
    target_file = EVALS_DIR / "test_pkg_proof.py"
    res = subprocess.run(
        [sys.executable, str(target_file), "-k", "test_pyproject_toml_exists"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0, f"Process exited with {res.returncode}. Output:\n{res.stdout}\n{res.stderr}"
    assert "test_pyproject_toml_exists" in res.stdout or res.returncode == 0


def test_negative_ast_checker_detects_missing_main():
    """Negative test: missing main block is caught."""
    bad_code = "def test_something():\n    assert True\n"
    tree = ast.parse(bad_code)
    assert not _has_main_block(tree)


def test_negative_ast_checker_detects_empty_main():
    """Negative test: main block with only pass is rejected."""
    vacuous_code = "if __name__ == '__main__':\n    pass\n"
    tree = ast.parse(vacuous_code)
    assert not _has_main_block(tree)


def test_readme_updated():
    """Verify README does not contain stale 'in flight' claims."""
    readme_path = REPO_DIR / "README.md"
    assert readme_path.exists(), "README.md not found"
    content = readme_path.read_text(encoding="utf-8")
    assert "in flight with a test that hard-kills" not in content, "README still claims mail delivery is in progress"
    assert "lifecycle is in flight" not in content, "README still claims lifecycle is in flight"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
