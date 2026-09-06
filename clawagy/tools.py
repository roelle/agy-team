"""Tool implementations + Gemini function declarations.

Every tool returns a plain string; errors are returned as text (never raised)
so the model always sees what actually happened.
"""
import subprocess
import urllib.request
from pathlib import Path

from google.genai import types

from . import config


def _truncate(s: str, limit: int = config.MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n...[truncated {len(s) - limit} chars]"


class Toolbox:
    """Tools bound to one agent's workspace. Memory lives in workspace/memory/."""

    def __init__(self, workspace: Path, workdir: Path | None = None):
        self.workspace = Path(workspace)
        self.workdir = Path(workdir) if workdir else self.workspace
        self.memory_dir = self.workspace / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.handlers = {
            "bash": self.bash,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "list_dir": self.list_dir,
            "save_memory": self.save_memory,
            "read_memory": self.read_memory,
            "delete_memory": self.delete_memory,
            "web_fetch": self.web_fetch,
        }

    # ---- implementations -------------------------------------------------

    def bash(self, command: str, timeout: int = 60) -> str:
        try:
            r = subprocess.run(command, shell=True, cwd=self.workdir,
                               capture_output=True, text=True,
                               timeout=min(int(timeout), 300))
            out = r.stdout + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
            if r.returncode != 0:
                out += f"\n[exit code: {r.returncode}]"
            return _truncate(out) or "[no output, exit 0]"
        except subprocess.TimeoutExpired:
            return f"[error: command timed out after {timeout}s]"
        except Exception as e:
            return f"[error: {e}]"

    def read_file(self, path: str) -> str:
        try:
            return _truncate(Path(path).expanduser().read_text())
        except Exception as e:
            return f"[error: {e}]"

    def write_file(self, path: str, content: str) -> str:
        try:
            p = Path(path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return f"[wrote {len(content)} chars to {p}]"
        except Exception as e:
            return f"[error: {e}]"

    def list_dir(self, path: str = ".") -> str:
        try:
            p = Path(path).expanduser()
            if not p.is_absolute():
                p = self.workdir / p
            entries = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())
            return "\n".join(entries) or "[empty directory]"
        except Exception as e:
            return f"[error: {e}]"

    # ---- memory ----------------------------------------------------------

    def _mem_path(self, name: str) -> Path:
        name = name.strip().replace(" ", "-").lower()
        name = "".join(c for c in name if c.isalnum() or c in "-_")
        return self.memory_dir / f"{name}.md"

    def save_memory(self, name: str, description: str, content: str) -> str:
        p = self._mem_path(name)
        existed = p.exists()
        p.write_text(f"# {name}\n\n{content}\n")
        self._update_index(p.stem, description)
        return f"[memory '{p.stem}' {'updated' if existed else 'saved'}]"

    def read_memory(self, name: str) -> str:
        p = self._mem_path(name)
        if not p.exists():
            have = ", ".join(sorted(f.stem for f in self.memory_dir.glob("*.md"))) or "none"
            return f"[no memory named '{p.stem}'. Existing memories: {have}]"
        return p.read_text()

    def delete_memory(self, name: str) -> str:
        p = self._mem_path(name)
        if not p.exists():
            return f"[no memory named '{p.stem}']"
        p.unlink()
        self._update_index(p.stem, None)
        return f"[memory '{p.stem}' deleted]"

    def _update_index(self, stem: str, description: str | None) -> None:
        """Keep MEMORY.md as one '- [stem] description' line per memory file."""
        index = self.workspace / "MEMORY.md"
        lines = index.read_text().splitlines() if index.exists() else ["# Memory index", ""]
        lines = [l for l in lines if not l.startswith(f"- [{stem}]")]
        if description is not None:
            lines.append(f"- [{stem}] {description}")
        index.write_text("\n".join(lines) + "\n")

    # ---- web -------------------------------------------------------------

    def web_fetch(self, url: str) -> str:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "clawagy/0.1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read(500_000).decode("utf-8", errors="replace")
            return _truncate(body)
        except Exception as e:
            return f"[error: {e}]"

    # ---- dispatch --------------------------------------------------------

    def call(self, name: str, args: dict) -> str:
        fn = self.handlers.get(name)
        if fn is None:
            return f"[error: unknown tool '{name}']"
        try:
            return fn(**args)
        except TypeError as e:
            return f"[error: bad arguments for {name}: {e}]"


def _decl(name, description, params, required):
    return types.FunctionDeclaration(
        name=name, description=description,
        parameters=types.Schema(type="OBJECT", properties=params, required=required))


def _s(desc):  # string param
    return types.Schema(type="STRING", description=desc)


BASE_DECLARATIONS = [
    _decl("bash", "Run a shell command and return stdout/stderr. Working directory is your workdir.",
          {"command": _s("The shell command to run"),
           "timeout": types.Schema(type="INTEGER", description="Seconds before kill (default 60, max 300)")},
          ["command"]),
    _decl("read_file", "Read a text file and return its contents.",
          {"path": _s("Absolute or ~-relative path")}, ["path"]),
    _decl("write_file", "Write content to a file (overwrites; creates parent dirs).",
          {"path": _s("Absolute or ~-relative path"), "content": _s("Full file content")},
          ["path", "content"]),
    _decl("list_dir", "List entries in a directory.",
          {"path": _s("Directory path (default: your workdir)")}, []),
    _decl("save_memory",
          "Save a durable memory to your workspace so future sessions of you know it. "
          "Use for user preferences, corrections, facts about systems/projects, and lessons learned. "
          "Saving under an existing name overwrites it — re-read first and merge if unsure.",
          {"name": _s("Short kebab-case topic name, e.g. 'user-preferences'"),
           "description": _s("One line: what's in this memory (shown in your index)"),
           "content": _s("The memory content in markdown")},
          ["name", "description", "content"]),
    _decl("read_memory", "Read one of your memory files by name.",
          {"name": _s("Memory name from your index")}, ["name"]),
    _decl("delete_memory", "Delete a memory that is wrong or obsolete.",
          {"name": _s("Memory name")}, ["name"]),
    _decl("web_fetch", "Fetch a URL and return the raw response body (truncated).",
          {"url": _s("http(s) URL")}, ["url"]),
]
