"""The agent: system prompt assembly, tool loop, compaction, distillation."""
import datetime
import platform
from pathlib import Path

from google.genai import types

from . import config
from .llm import LLM
from .tools import BASE_DECLARATIONS, Toolbox

# Fixed contract, deliberately NOT agent-editable (identity/memory files are).
CONTRACT = """\
## Grounding rules (non-negotiable)
1. Never state file contents, command output, or facts about this machine unless
   they appear in a tool result in this conversation. If you haven't looked, look
   first or say you don't know.
2. If a tool fails or returns nothing, report that plainly. Never invent plausible
   output or pretend a tool ran.
3. Facts about the user or past sessions must come from your memory files (index
   below, read_memory for details). If memory doesn't cover it, say "I don't have
   that in memory" — do not guess.
4. Wrong answers are worse than no answers. "I don't know, here's how I'd find
   out" is always an acceptable reply.

## Learning rules
- You wake with no conversation memory. Your files ARE your memory.
- When you learn something durable (a user preference, a correction, a fact about
  a system or project, a lesson from a mistake), call save_memory immediately —
  do not wait for the end of the session.
- When the user corrects you, update or delete the wrong memory right away.
- Keep memories small and topical; update existing ones rather than piling up
  near-duplicates.
"""


class Agent:
    def __init__(self, workspace: Path, model: str = config.DEFAULT_MODEL,
                 llm: LLM | None = None, extra_declarations=None,
                 extra_handlers=None, name: str | None = None):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.llm = llm or LLM()
        self.tools = Toolbox(self.workspace)
        self.declarations = list(BASE_DECLARATIONS) + list(extra_declarations or [])
        if extra_handlers:
            self.tools.handlers.update(extra_handlers)
        self.name = name or "Agy"
        self.history: list[types.Content] = []
        self._ensure_workspace_files()

    # ---- prompt assembly -------------------------------------------------

    def _ensure_workspace_files(self):
        ident = self.workspace / "IDENTITY.md"
        if not ident.exists():
            ident.write_text(
                f"# {self.name}\n\nYou are {self.name}, a persistent, learning "
                "command-line agent running on the Gemini/Antigravity backend. "
                "You are practical, direct, and honest about uncertainty. "
                "You may edit this file to evolve your own identity.\n")
        index = self.workspace / "MEMORY.md"
        if not index.exists():
            index.write_text("# Memory index\n")

    def system_prompt(self) -> str:
        identity = (self.workspace / "IDENTITY.md").read_text()
        index = (self.workspace / "MEMORY.md").read_text()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        return (f"{identity}\n{CONTRACT}\n"
                f"## Context\n"
                f"- Date/time: {now}\n"
                f"- Host: {platform.node()} ({platform.system()})\n"
                f"- Workspace (your private files): {self.workspace}\n"
                f"- Working directory for bash: {self.tools.workdir}\n\n"
                f"## Memory index (read_memory for full content)\n{index}")

    # ---- main loop -------------------------------------------------------

    def run_turn(self, user_text: str, on_event=None) -> str:
        """One user turn: model may chain tool calls; returns final text.

        on_event(kind, payload) gets 'tool' and 'tool_result' notifications.
        """
        emit = on_event or (lambda k, p: None)
        self._maybe_compact(emit)
        self.history.append(types.Content(role="user",
                                          parts=[types.Part(text=user_text)]))
        for _ in range(config.MAX_LOOP_STEPS):
            resp = self.llm.generate(self.model, self.history,
                                     system=self.system_prompt(),
                                     tools=self.declarations)
            content = resp.candidates[0].content
            # Append the model's exact Content: preserves thoughtSignature parts,
            # which Gemini 3 requires for coherent multi-step tool use.
            self.history.append(content)
            calls = [p.function_call for p in (content.parts or []) if p.function_call]
            if not calls:
                return resp.text or ""
            result_parts = []
            for fc in calls:
                args = dict(fc.args or {})
                emit("tool", {"name": fc.name, "args": args})
                out = self.tools.call(fc.name, args)
                emit("tool_result", {"name": fc.name, "output": out})
                result_parts.append(types.Part.from_function_response(
                    name=fc.name, response={"result": out}))
            self.history.append(types.Content(role="user", parts=result_parts))
        return "[stopped: exceeded max tool steps for one turn]"

    # ---- context management ---------------------------------------------

    def _maybe_compact(self, emit):
        if self.llm.usage.last_prompt > config.COMPACT_THRESHOLD_TOKENS:
            emit("info", {"msg": "context large, compacting..."})
            self.compact()

    def compact(self) -> str:
        """Summarize old history; keep recent turns verbatim."""
        if len(self.history) <= config.KEEP_RECENT_TURNS:
            return "[nothing to compact]"
        old = self.history[:-config.KEEP_RECENT_TURNS]
        recent = self.history[-config.KEEP_RECENT_TURNS:]
        transcript = []
        for c in old:
            for p in (c.parts or []):
                if p.text:
                    transcript.append(f"{c.role}: {p.text[:2000]}")
                elif p.function_call:
                    transcript.append(f"{c.role} called {p.function_call.name}"
                                      f"({dict(p.function_call.args or {})})")
                elif p.function_response:
                    r = str(p.function_response.response)[:500]
                    transcript.append(f"tool result: {r}")
        summary = self.llm.text(
            config.COMPACTION_MODEL,
            "Summarize this agent session so the agent can continue seamlessly. "
            "Preserve: open tasks, decisions made, key facts discovered via tools, "
            "user instructions still in force. Be dense and factual.\n\n"
            + "\n".join(transcript))
        self.history = [
            types.Content(role="user", parts=[types.Part(text=
                "[Context was compacted. Summary of the earlier conversation:]\n"
                + summary)]),
            types.Content(role="model", parts=[types.Part(text=
                "Understood — continuing from that summary.")]),
        ] + recent
        return f"[compacted to summary + last {config.KEEP_RECENT_TURNS} messages]"

    def distill(self, on_event=None) -> str:
        """End-of-session reflection: push durable learnings into memory files."""
        return self.run_turn(
            "Session is ending. Review this conversation for anything durable you "
            "haven't saved yet (preferences, corrections, facts, lessons). Call "
            "save_memory / delete_memory as needed — update existing memories "
            "rather than duplicating. If nothing new, say so. Reply with a "
            "one-line summary of what you saved.", on_event)
