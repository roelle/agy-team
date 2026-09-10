"""Agy on the official google-antigravity SDK (Go localharness backend).

The harness assembles the prompt and runs the loop; our levers are:
- TemplatedSystemInstructions at session start (identity + contract + memory index)
- custom memory tools, whose RESULTS carry fresh memory state into context
  mid-session (the harness never re-reads system instructions)
- hooks for tracing and a destructive-command gate
"""
import datetime
import os
import platform
import sys
from pathlib import Path

from google.antigravity import (CapabilitiesConfig, LocalAgentConfig, types)
from google.antigravity.models import (GeminiAPIEndpoint, GeminiModelOptions,
                                       ModelTarget)
from google.antigravity.hooks import (on_compaction, policy, post_tool_call,
                                      pre_tool_call_decide)

from . import config as cfg
from .persona import CONTRACT
from .tools import Toolbox

DANGEROUS = ["rm -rf /", "git push -f", "git push --force", "mkfs", "> /dev/sd"]


def make_memory_tools(box: Toolbox):
    """Memory tools as plain callables (SDK builds schemas from signatures/docstrings).

    Unlike prior attempts, every write returns the updated index so the model's
    in-context view of memory never goes stale.
    """

    def save_memory(name: str, description: str, content: str) -> str:
        """Save a durable memory so future sessions of you know it. Use for user
        preferences, corrections, facts about systems/projects, lessons learned.
        Saving under an existing name overwrites it — read first and merge if unsure.

        Args:
            name: Short kebab-case topic name, e.g. 'user-preferences'.
            description: One line describing the memory, shown in your index.
            content: The memory content in markdown.
        """
        out = box.save_memory(name, description, content)
        return f"{out}\nYour current memory index:\n{(box.workspace / 'MEMORY.md').read_text()}"

    def read_memory(name: str) -> str:
        """Read one of your memory files by name (see your memory index).

        Args:
            name: Memory name from your index.
        """
        return box.read_memory(name)

    def delete_memory(name: str) -> str:
        """Delete a memory that is wrong or obsolete.

        Args:
            name: Memory name from your index.
        """
        out = box.delete_memory(name)
        return f"{out}\nYour current memory index:\n{(box.workspace / 'MEMORY.md').read_text()}"

    return [save_memory, read_memory, delete_memory]


class SessionFlags:
    """Mutable state shared between hooks and the driving CLI/orchestrator."""

    def __init__(self):
        self.compaction_pending = False


# The agy CLI encodes reasoning effort in the model name ("gemini-3.8-flash-high"),
# but those aliases exist only in the CLI — the SDK talks to the Gemini API,
# which rejects them. Effort lives in GeminiModelOptions instead. Without this,
# moving a roster from the CLI runner to the SDK silently drops the team from
# high effort to the default, which is a quality change nobody would see.
EFFORT = {"minimal": types.ThinkingLevel.MINIMAL, "low": types.ThinkingLevel.LOW,
          "medium": types.ThinkingLevel.MEDIUM, "high": types.ThinkingLevel.HIGH,
          "extra_high": types.ThinkingLevel.EXTRA_HIGH}


def _model_target(model: str, effort: str = ""):
    """A plain model name, or a ModelTarget carrying the requested effort."""
    base, _, suffix = model.rpartition("-")
    if not effort and suffix in EFFORT and base:
        model, effort = base, suffix     # accept CLI-style names too
    if not effort:
        return model
    level = EFFORT.get(effort.lower())
    if level is None:
        raise SystemExit(f"unknown effort {effort!r}; use one of "
                         f"{', '.join(sorted(EFFORT))}")
    return ModelTarget(name=model, endpoint=GeminiAPIEndpoint(
        api_key=cfg.api_key(), options=GeminiModelOptions(thinking_level=level)))


def build_config(workspace: Path, model: str = cfg.DEFAULT_MODEL,
                 name: str = "Agy", interactive: bool = False, effort: str = "",
                 trace=None, extra_tools=None, extra_sections=None,
                 subagents=None, identity_default: str | None = None,
                 disabled_tools: list[str] | None = None,
                 use_mcp: bool = False, with_bus: bool = False,
                 flags: SessionFlags | None = None) -> LocalAgentConfig:
    workspace = Path(workspace)
    box = Toolbox(workspace)  # creates workspace/memory/
    ident_file = workspace / "IDENTITY.md"
    if not ident_file.exists():
        ident_file.write_text(identity_default or (
            f"# {name}\n\nYou are {name}, a persistent, learning agent on the "
            "Antigravity backend. Practical, direct, honest about uncertainty. "
            "You may edit this file to evolve your own identity.\n"))
    index = workspace / "MEMORY.md"
    if not index.exists():
        index.write_text("# Memory index\n")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    sections = [
        types.SystemInstructionSection(title="grounding_and_learning_contract",
                                       content=CONTRACT),
        types.SystemInstructionSection(
            title="memory_index",
            content=("Your memory files live in "
                     f"{box.memory_dir}. Index (use read_memory for content; "
                     "save_memory results include the refreshed index):\n"
                     + index.read_text())),
        types.SystemInstructionSection(
            title="session_context",
            content=(f"Date/time: {now}\nHost: {platform.node()} "
                     f"({platform.system()})\nWorkspace: {workspace}\n"
                     "Builtin file/shell tools are your ground truth for "
                     "anything about this machine.")),
    ]
    sections.extend(extra_sections or [])

    hooks = []

    @pre_tool_call_decide
    def block_dangerous(tool_call):
        cmd = str(tool_call.args.get("command", ""))
        if str(tool_call.name) == "run_command" and any(d in cmd for d in DANGEROUS):
            return types.HookResult(allow=False, message="Blocked: destructive command.")
        return types.HookResult(allow=True)

    hooks.append(block_dangerous)

    if flags is not None:
        @on_compaction
        def note_compaction(step):
            flags.compaction_pending = True
        hooks.append(note_compaction)

    if trace is not None:
        @post_tool_call
        def trace_tools(result):
            trace(result)
        hooks.append(trace_tools)

    mcp_servers = None
    if use_mcp:
        # Same servers the agy plugin mounts, with the same env-derived
        # identity — so behaviour verified here transfers to the plugin.
        mcp_env = {"PYTHONPATH": str(cfg.PROJECT_ROOT), "AGYTEAM_AGENT": name}
        for k in ("AGYTEAM_TEAM_DIR", "AGYTEAM_TEAM", "AGYTEAM_TEAMS_ROOT",
                  "AGYTEAM_DURABLE_DIR", "AGYTEAM_BUS_TRANSPORT",
                  "AGYTEAM_BUS_CONFIG", "AGYTEAM_MEMORY_STORE",
                  "AGYTEAM_MEMORY_CONFIG"):
            if os.environ.get(k):
                mcp_env[k] = os.environ[k]
        mcp_servers = [
            types.McpStdioServer(
                name="agyteam_memory", type="stdio", command=sys.executable,
                args=["-m", "agyteam.mcp_memory", str(workspace)], env=mcp_env),
        ]
        if with_bus:
            mcp_servers.append(types.McpStdioServer(
                name="agyteam_bus", type="stdio", command=sys.executable,
                args=["-m", "agyteam.mcp_bus"], env=mcp_env))

    off = [types.BuiltinTools(t) for t in (disabled_tools or [])]

    return LocalAgentConfig(
        system_instructions=types.TemplatedSystemInstructions(
            identity=ident_file.read_text(), sections=sections),
        tools=([] if use_mcp else make_memory_tools(box)) + list(extra_tools or []),
        subagents=subagents,
        mcp_servers=mcp_servers,
        capabilities=CapabilitiesConfig(
            enable_subagents=bool(subagents),
            max_subagent_depth=1 if subagents else None,
            disabled_tools=off or None,
            agent_behavior=(types.AgentBehavior.INTERACTIVE if interactive
                            else types.AgentBehavior.AUTONOMOUS),
            compaction_threshold=cfg.COMPACT_THRESHOLD_TOKENS,
        ),
        workspaces=[str(workspace), str(Path.cwd())],
        policies=[policy.allow_all()],
        hooks=hooks,
        model=_model_target(model, effort),
        api_key=cfg.api_key(),
        # Without this the SDK saves each conversation to a throwaway
        # tempfile.mkdtemp("antigravity_") and abandons it. Writing to the
        # CLI's store instead means one conversation store, not two: an SDK
        # agent can be joined with `agy --conversation <id>` and shows up in
        # Remote Control. cfg.CONVERSATION_DIR picks which surface.
        save_dir=str(cfg.conversation_dir()),
        budget_config=(types.BudgetConfig(max_model_calls=cfg.MAX_LOOP_STEPS)
                       if cfg.MAX_LOOP_STEPS else None),
    )
