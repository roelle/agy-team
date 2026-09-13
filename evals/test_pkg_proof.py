import subprocess
import os

def test_run_status_no_key():
    """Verify run status works without API key."""
    env = os.environ.copy()
    if 'GEMINI_API_KEY' in env:
        del env['GEMINI_API_KEY']
    res = subprocess.run(["./run", "status"], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert "none" in res.stdout or "\n" in res.stdout

def test_pyproject_toml_exists():
    assert os.path.exists("pyproject.toml")
    with open("pyproject.toml") as f:
        content = f.read()
    assert "build-backend" in content

def test_gitignore_contains_build_dirs():
    with open(".gitignore") as f:
        content = f.read()
    assert "build/" in content
    assert ".venv" in content
