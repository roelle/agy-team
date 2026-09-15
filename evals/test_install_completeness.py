"""The list of modules an install needs must be derived, not remembered.

`plugin/install.sh` copied a hand-written list of module names. Two modules that
`mcp_bus` imports inside a function were added to the repo and never to the
list, so on the installed copy those tools raised ImportError on first call --
while every test here passed, because tests import from the repo.

The install's own self-test could not catch it either: importing a module does
not execute a function-level import inside it. So the check has to read the
source and follow both kinds of edge -- the `import` statement at any depth, and
the "agyteam.x:Class" spec strings the pluggable seams resolve at runtime.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agyteam import install_check as ic  # noqa: E402


def pkg(tmp_path, files: dict) -> Path:
    d = tmp_path / "agyteam"
    d.mkdir(exist_ok=True)
    for name, body in files.items():
        (d / f"{name}.py").write_text(body)
    return d


# --- the two edges a hand-written list misses --------------------------------

def test_an_import_inside_a_function_is_found():
    """Where the two missing modules were imported from, both times."""
    found = ic.local_imports(ic.SRC / "mcp_bus.py")
    assert "retro_store" in found
    assert "lifecycle" in found


def test_the_derived_list_contains_them():
    assert "retro_store.py" in ic.module_list()
    assert "lifecycle.py" in ic.module_list()


def test_a_runtime_spec_string_is_followed(tmp_path):
    """The transport is chosen by config at runtime; no import walk sees it."""
    d = pkg(tmp_path, {
        "mcp_bus": 'DEFAULT = "agyteam.transport_file:FileTransport"\n',
        "transport_file": "import json\n",
    })
    needed, _ = ic.required(d)
    assert "transport_file" in needed


def test_a_module_that_needs_a_dependency_is_excluded_when_optional(tmp_path):
    """The SDK runner is named in this package and never loaded on a CLI host.

    Copying it would put an unimportable module in a plugin that promises to
    run under a bare system python3.
    """
    d = pkg(tmp_path, {
        "mcp_bus": 'RUNNER = "agyteam.runner_sdk:SdkRunner"\n',
        "runner_sdk": "import google.genai\n",
    })
    needed, impure = ic.required(d)
    assert "runner_sdk" not in needed
    assert "runner_sdk" in impure


def test_a_dependency_reached_by_a_real_import_is_fatal(tmp_path):
    """Then the plugin has stopped being installable without vendoring, and
    shipping a module that will ImportError is not the honest answer."""
    d = pkg(tmp_path, {
        "mcp_bus": "from . import fancy\n",
        "fancy": "import numpy\n",
    })
    with pytest.raises(RuntimeError) as e:
        ic.required(d)
    assert "vendoring" in str(e.value) and "numpy" in str(e.value)


def test_the_real_package_is_still_installable_without_dependencies():
    """If this ever fails, the install stops being dependency-free -- which is
    a decision to make deliberately, not to discover on a user's machine."""
    needed, _ = ic.required()
    for mod in needed:
        path = ic.SRC / f"{mod}.py"
        if path.exists():
            assert not ic.third_party_imports(path), f"{mod} needs a dependency"


# --- what an install is missing, and whether it is this code -----------------

def test_a_required_module_that_is_absent_is_named(tmp_path):
    """The failure mode itself: the walk must be able to name a file that is
    not there, which a walk that only opens files it finds cannot do."""
    d = pkg(tmp_path, {"mcp_bus": "from . import retro_store\n"})
    assert "retro_store.py" in ic.missing(d.parent)


def test_a_complete_install_reports_nothing_missing(tmp_path):
    d = tmp_path / "agyteam"
    d.mkdir()
    for name in ic.module_list():
        src = ic.SRC / name
        if src.exists():
            (d / name).write_bytes(src.read_bytes())
    assert ic.missing(tmp_path) == []


def test_drift_is_measured_on_bytes_not_on_a_version_string(tmp_path):
    """A version number is a claim; the file contents are the fact. The install
    we found reported nothing at all -- there was nothing to report with."""
    d = tmp_path / "agyteam"
    d.mkdir()
    for name in ic.module_list():
        src = ic.SRC / name
        if src.exists():
            (d / name).write_bytes(src.read_bytes())
    assert ic.drift(tmp_path) == []
    (d / "mcp_bus.py").write_text("# yesterday's gate\n"
                                  + (ic.SRC / "mcp_bus.py").read_text())
    assert ic.drift(tmp_path) == ["mcp_bus.py"]


def test_an_absent_install_is_not_reported_as_a_clean_one(tmp_path):
    assert ic.missing(tmp_path / "nowhere") == []
    assert ic.drift(tmp_path / "nowhere") == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))
