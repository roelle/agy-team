"""Which modules an install needs, derived from the source instead of listed.

The plugin install copies a subset of this package next to the manifest, so the
MCP servers keep working when this repo moves or is deleted. That subset was a
hand-maintained list in `plugin/install.sh`, and a hand-maintained list of
imports drifts the moment someone adds an import -- which is exactly what
happened. Two modules that `mcp_bus` imports inside a function were never
copied, so the tools that use them raised ImportError on the installed copy
while every test in this repo passed, because tests import from the repo.

The install self-test did not catch it either: it imports the servers, and a
lazy import inside a function is not exercised by importing its module.

So derive the list. `required()` walks the import graph from the server
entrypoints, at any nesting depth, and follows the `"agyteam.x:Class"` spec
strings that the pluggable seams resolve at runtime -- the transport, memory
store, observer and runner are all loaded that way and are invisible to an
import walk that only reads `import` statements.

    python -m agyteam.install_check --list            # module names, one per line
    python -m agyteam.install_check --verify <dir>    # is that install complete?
    python -m agyteam.install_check --drift <dir>     # does it match this repo?

`--drift` answers the question that cost the most: not "is the install broken"
but "is the install *this code*". An install three commits behind fails nothing
and reports nothing; it simply runs the code you stopped believing in. We found
one serving a review gate from before the gate checked authorship at all, which
had been silently accepting self-reviews for days.
"""
import argparse
import ast
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent

# Where an installed server starts. The three MCP servers are what the plugin
# mounts; supervisor/session are what the operator runs by hand next to them;
# doctor and this module are what they run when something is wrong, which is
# exactly when the repo may not be on the machine.
SEEDS = ("mcp_memory", "mcp_bus", "mcp_self", "supervisor", "session",
         "runner_agy", "doctor", "install_check")

# Not imported by anything -- copied so whoever adapts a seam on that machine
# has the contract in front of them rather than in a repo they may not have.
TEMPLATES = ("transport_template", "memory_template", "observer_template",
             "runner_template")

_SPEC = re.compile(r"agyteam\.([a-z_][a-z0-9_]*):[A-Za-z_]")


def local_imports(path: Path) -> set:
    """Package-relative imports in one module, at any nesting depth.

    Nesting is the point: the two modules that went missing are imported
    inside function bodies, which is where imports go when a seam is optional
    or a cycle needs breaking, and where a grep for a top-of-file import block
    will never find them.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module:
                out.add(node.module.split(".")[0])
            else:
                out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("agyteam."):
                    out.add(a.name.split(".")[1])
    return out


def spec_references(path: Path) -> set:
    """Modules named in "agyteam.x:Class" strings -- the pluggable seams.

    These are resolved by importlib at runtime from a config value, so no
    import walk can see them. Leaving them out produced an install that
    imported cleanly and then failed on the first tool call.
    """
    try:
        return set(_SPEC.findall(path.read_text(encoding="utf-8")))
    except OSError:
        return set()


def third_party_imports(path: Path) -> set:
    """Imports that are neither stdlib nor part of this package.

    The install is dependency-free on purpose -- pure stdlib under a bare
    system python3 -- so this is the property that decides what may be copied,
    rather than a list someone remembers to update.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    local = {p.stem for p in path.parent.glob("*.py")}
    out = set()
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        for n in names:
            if n != "agyteam" and n not in local and n not in sys.stdlib_module_names:
                out.add(n)
    return out


def required(src: Path = SRC) -> tuple:
    """(modules to install, modules excluded for pulling in dependencies).

    Two kinds of edge reach a module, and they are not equally binding. A
    static import is a hard requirement: if one of those needs a third-party
    package, the plugin has stopped being installable without vendoring, and
    that is worth saying out loud rather than quietly shipping a module that
    will ImportError on first use. A spec string is an *option* -- the SDK
    runner is named in this package and never loaded on a CLI host -- so a
    dependency reached only that way is excluded, not fatal.
    """
    seen, hard = set(), set()
    frontier = [(m, True) for m in SEEDS]
    while frontier:
        mod, is_static = frontier.pop()
        # A module already accounted for is revisited only when it turns out to
        # be statically reachable after all: hardness has to propagate down the
        # chain, or a module imported by an optional seam inherits the seam's
        # optionality and is wrongly called optional itself.
        already = mod in seen or mod in hard
        if already and not (is_static and mod not in hard):
            continue
        if is_static:
            # Recorded before the existence check, deliberately: a required
            # module that is *absent* is the whole failure this reports, and a
            # walk that only collects files it can open cannot ever name one.
            hard.add(mod)
        path = src / f"{mod}.py"
        if not path.exists():
            continue
        seen.add(mod)
        statics = local_imports(path)
        frontier += [(m, is_static) for m in statics]
        frontier += [(m, False) for m in spec_references(path) - statics]

    impure = {m for m in seen if third_party_imports(src / f"{m}.py")}
    blocking = sorted(impure & hard)
    if blocking:
        raise RuntimeError(
            "the plugin can no longer be installed without vendoring: "
            + ", ".join(f"{m} imports "
                        f"{', '.join(sorted(third_party_imports(src / f'{m}.py')))}"
                        for m in blocking))
    return ((seen | hard) - impure) | {"__init__"}, impure


def module_list(src: Path = SRC) -> list:
    """Filenames to copy: everything needed, plus the templates, sorted.

    Filenames rather than module names, here and in `missing()` and `drift()`:
    all three are answers about files on a disk, and one list of bare names
    beside two lists of filenames is an invitation to concatenate the wrong
    one.
    """
    needed, _ = required(src)
    return sorted(f"{m}.py" for m in needed | set(TEMPLATES))


def missing(install_dir: Path, src: Path = SRC) -> list:
    """Modules the install needs and does not have.

    Computed against the *installed* sources, not this repo's: an install is
    complete or not on its own terms, and an older install may legitimately
    need a different set.
    """
    pkg = install_dir / "agyteam" if (install_dir / "agyteam").is_dir() else install_dir
    if not pkg.is_dir():
        return []
    have = {p.name for p in pkg.glob("*.py")}
    need, _ = required(pkg) if (pkg / "mcp_bus.py").exists() else required(src)
    return sorted(f"{m}.py" for m in need if f"{m}.py" not in have)


def drift(install_dir: Path, src: Path = SRC) -> list:
    """Installed files that differ from this repo's, byte for byte.

    Deliberately not a version number: a version number is a claim, and the
    file contents are the fact.
    """
    pkg = install_dir / "agyteam" if (install_dir / "agyteam").is_dir() else install_dir
    if not pkg.is_dir():
        return []
    out = []
    for name in module_list(src):
        here, there = src / name, pkg / name
        if not here.exists():
            continue
        if not there.exists():
            out.append(f"{name} (absent from the install)")
        elif here.read_bytes() != there.read_bytes():
            out.append(name)
    return out


def default_install_dir() -> Path:
    """The same default plugin/install.sh writes to."""
    import os
    env = os.environ.get("AGYTEAM_PLUGIN_DIR")
    if env:
        return Path(env)
    return Path.home() / ".gemini" / "antigravity-cli" / "plugins" / "agy-team"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agyteam.install_check")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true",
                   help="module names an install needs, one per line")
    g.add_argument("--verify", metavar="DIR", help="fail if that install is incomplete")
    g.add_argument("--drift", metavar="DIR", help="list installed files that differ")
    a = ap.parse_args(argv)

    if a.list:
        print("\n".join(module_list()))
        return 0
    if a.verify:
        gaps = missing(Path(a.verify))
        if gaps:
            print(f"incomplete install at {a.verify}: missing "
                  f"{', '.join(gaps)}", file=sys.stderr)
            return 1
        print(f"install at {a.verify} has every module its own sources import")
        return 0
    changed = drift(Path(a.drift))
    if changed:
        print(f"{len(changed)} file(s) differ from this repo: "
              f"{', '.join(changed)}", file=sys.stderr)
        return 1
    print(f"install at {a.drift} matches this repo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
