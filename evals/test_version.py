"""The version is written once, and the changelog and tag agree with it.

Downstream asked for releases with semantic versioning; a release whose
package, changelog and tag say three different things is not one.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agyteam  # noqa: E402

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_the_version_is_semver():
    assert SEMVER.match(agyteam.__version__), agyteam.__version__


def test_pyproject_reads_it_rather_than_repeating_it():
    text = (ROOT / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in text and 'attr = "agyteam.__version__"' in text
    project = text.split("[project]", 1)[1].split("\n[", 1)[0]
    assert not re.search(r'^version\s*=', project, re.M), "[project] repeats the version"


def test_the_changelog_has_an_entry_for_it():
    text = (ROOT / "CHANGELOG.md").read_text()
    released = re.findall(r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}", text, re.M)
    assert released and released[0] == agyteam.__version__, \
        f"newest changelog release {released[:1]} != {agyteam.__version__}"
    assert "## [Unreleased]" in text


def test_a_tag_for_this_version_points_at_a_commit_that_has_it():
    """If vX.Y.Z exists, the agyteam/__init__.py at that tag carries X.Y.Z."""
    tag = f"v{agyteam.__version__}"
    p = subprocess.run(["git", "-C", str(ROOT), "show", f"{tag}:agyteam/__init__.py"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        pytest.skip(f"{tag} is not tagged yet (or this is not a git checkout)")
    assert f'__version__ = "{agyteam.__version__}"' in p.stdout


def test_the_command_line_reports_it():
    out = subprocess.run([sys.executable, "-m", "agyteam.supervisor", "--version"],
                         capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.stdout.strip() == f"agyteam {agyteam.__version__}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
