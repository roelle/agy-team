"""Gemini function declarations for the workspace tools.

Requires google-genai. The tool implementations live in store.py, which
stays dependency-free so the MCP servers can run without a virtualenv.
"""
from google.genai import types

from .store import Toolbox, _truncate  # re-exported for existing callers

__all__ = ["Toolbox", "_truncate", "BASE_DECLARATIONS"]


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
