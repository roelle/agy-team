"""Team platform: persistent peer agents on the google-antigravity SDK.

Structure that prevents the teammate/subagent confusion of prior attempts:
- TEAMMATES are long-lived SDK Agent sessions, each with its own workspace,
  identity, and memory. They can only be *messaged* (send_to_teammate).
- WORKERS are harness-native ephemeral subagents. They can only be *spawned*
  (builtin start_subagent), have no name on the bus, and no memory.

Roster lives in team/roster.json; add/remove agents by editing it via this
CLI (or by hand between runs). Bus messages persist to team/bus.jsonl.

Usage: .venv/bin/python -m agyteam.team [--team-dir DIR]
  Commands inside: say <agent> <msg> | broadcast <msg> | log [n] | roster
                   add <name> <role...> | remove <name> | distill [agent] | quit
  Or one-shot:     --say "<agent>: <message>" (runs until idle, then exits)
"""
import argparse
import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path

from google.antigravity import Agent, types

from . import config as cfg
from .sdk_agent import SessionFlags, build_config

MAX_HOPS = 16   # agent turns allowed per user stimulus before requiring input

# Per-agent roster options: "tools_off" (builtin tool values to disable, e.g.
# "run_command", "create_file", "edit_file") and "workers" (may spawn ephemeral
# subagents). The tpm is deliberately de-toothed: with no shell and no workers,
# delegation is its only way to get anything done.
DEFAULT_ROSTER = {
    "mission": "General-purpose engineering team for roelle.",
    "agents": [
        {"name": "tpm", "role": "Technical program manager. Decomposes user "
         "requests, delegates to specialists by name, tracks completion, and "
         "reports results back to the user. Does not do the technical work itself.",
         "model": cfg.DEFAULT_MODEL,
         "tools_off": ["run_command", "create_file", "edit_file"],
         "workers": False},
        {"name": "coder", "role": "Software engineer. Implements, runs, and "
         "verifies code changes.", "model": cfg.DEFAULT_MODEL, "workers": True},
        {"name": "syseng", "role": "Systems engineer. Environments, tooling, "
         "integration, and checking others' work end-to-end.",
         "model": cfg.DEFAULT_MODEL, "workers": True},
    ],
}

WORKER = types.SubagentConfig(
    name="worker",
    description=(
        "Ephemeral worker for one bounded task (a research question, a coding "
        "chore, an analysis). It has NO memory, NO teammates, and vanishes when "
        "done — brief it with complete context and exact deliverables. Use it "
        "for grunt work; use send_to_teammate for anything needing a peer's "
        "role, memory, or judgment."),
)


class Team:
    def __init__(self, team_dir: Path, quiet: bool = False):
        self.dir = Path(team_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.roster_path = self.dir / "roster.json"
        if not self.roster_path.exists():
            self.roster_path.write_text(json.dumps(DEFAULT_ROSTER, indent=2))
        self.roster = json.loads(self.roster_path.read_text())
        self.bus_log = self.dir / "bus.jsonl"
        self.queues: dict[str, list[dict]] = {}
        self.agents: dict[str, Agent] = {}
        self.cms: dict[str, Agent] = {}      # the un-entered Agent context managers
        self.flags: dict[str, SessionFlags] = {}
        self.quiet = quiet

    # ---- bus -------------------------------------------------------------

    def post(self, sender: str, to: str, content: str) -> str:
        entry = {"ts": time.strftime("%H:%M:%S"), "from": sender,
                 "to": to, "content": content}
        with self.bus_log.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        if to == "user":
            print(f"\n[{sender} → you] {content}\n", flush=True)
            return "[delivered to user]"
        if to not in self.queues:
            return (f"[error: no teammate named '{to}'. Teammates: "
                    f"{', '.join(self.queues)}]")
        self.queues[to].append(entry)
        if not self.quiet:
            print(f"  ✉ {sender} → {to}: {content[:100]}", flush=True)
        return f"[delivered to {to}]"

    # ---- per-agent tool closures ----------------------------------------

    def _bus_tools(self, me: str):
        def send_to_teammate(to: str, content: str) -> str:
            """Send a message to a teammate (a persistent peer agent) or to
            'user'. Teammates keep their own memory and act on messages in their
            next turn. Address the user with to='user' for reports/questions.

            Args:
                to: Teammate name from your roster, or 'user'.
                content: The message. Include full context and concrete asks.
            """
            return self.post(me, to, content)

        def list_teammates() -> str:
            """List your teammates and their roles."""
            return "\n".join(f"- {a['name']}: {a['role']}"
                             for a in self.roster["agents"] if a["name"] != me) \
                   + "\n- user: the human you all work for"
        return [send_to_teammate, list_teammates]

    def _sections(self, me: str, role: str):
        peers = "\n".join(f"- {a['name']}: {a['role']}"
                          for a in self.roster["agents"] if a["name"] != me)
        return [types.SystemInstructionSection(
            title="team_contract",
            content=(
                f"Mission: {self.roster['mission']}\n\n"
                f"You are '{me}'. Your role: {role}\n\nYour TEAMMATES (persistent "
                f"peers with their own memory — coordinate via send_to_teammate):\n"
                f"{peers}\n- user: the human. Send final results and questions to "
                "'user'.\n\nWORKERS: spawn a 'worker' subagent for bounded grunt "
                "tasks. Workers are not teammates: no memory, no bus, gone when "
                "done. Never ask a worker to coordinate; never spawn a worker to "
                "do another teammate's role — message the teammate instead.\n\n"
                "Collaboration rules: stay in your role; delegate across roles by "
                "messaging the teammate by name with complete context; when you "
                "finish an assigned task, report back to whoever assigned it; "
                "don't loop — if an exchange isn't converging in 2 rounds, "
                "escalate to 'user'. Being stuck and not asking is the failure "
                "mode; asking is the job. If a task is outside your role, or two "
                "attempts haven't worked, you MUST message the relevant teammate "
                "or the user before continuing. Nothing goes to 'user' as a "
                "finished deliverable until another teammate has verified it. "
                "Some tools are deliberately unavailable to your role — that is "
                "your cue to delegate, not to work around it. Shared files "
                f"belong under the team dir: {self.dir / 'shared'}")),
        ]

    # ---- lifecycle -------------------------------------------------------

    async def start(self):
        (self.dir / "shared").mkdir(exist_ok=True)
        for spec in self.roster["agents"]:
            await self._start_agent(spec)
        names = ", ".join(self.queues)
        print(f"team up: {names} (workspaces in {self.dir / 'agents'})")

    async def _start_agent(self, spec):
        name = spec["name"]
        ws = self.dir / "agents" / name
        trace = None if self.quiet else (
            lambda r, n=name: print(f"  ⚙ [{n}] {r.name}", flush=True))
        flags = SessionFlags()
        config = build_config(
            ws, model=spec.get("model", cfg.DEFAULT_MODEL), name=name,
            interactive=False, trace=trace, extra_tools=self._bus_tools(name),
            extra_sections=self._sections(name, spec["role"]),
            subagents=[WORKER] if spec.get("workers", True) else None,
            disabled_tools=spec.get("tools_off"), flags=flags,
            identity_default=(f"# {name}\n\nYou are {name}, a persistent "
                              f"member of an engineering team. {spec['role']}\n"))
        cm = Agent(config)
        self.agents[name] = await cm.__aenter__()
        self.cms[name] = cm
        self.flags[name] = flags
        self.queues.setdefault(name, [])

    async def _close_agent(self, name: str, distill: bool = True):
        agent, cm = self.agents.pop(name, None), self.cms.pop(name, None)
        self.flags.pop(name, None)
        if agent is None:
            return
        if distill:
            with contextlib.suppress(Exception):
                if agent.conversation.turn_count > 0:
                    print(f"  distilling {name}...")
                    await agent.chat(
                        "Session ending. Save durable learnings with "
                        "save_memory (update, don't duplicate). Be brief.")
        with contextlib.suppress(Exception):
            await cm.__aexit__(None, None, None)

    async def cycle_agent(self, name: str):
        """Distill + restart one agent fresh on the same workspace/memory."""
        spec = next((a for a in self.roster["agents"] if a["name"] == name), None)
        if spec is None or name not in self.agents:
            print(f"  no running agent '{name}'")
            return
        await self._close_agent(name)
        await self._start_agent(spec)
        print(f"  {name}: fresh session, memory index reloaded")

    async def stop(self, distill: bool = True):
        for name in list(self.agents):
            await self._close_agent(name, distill=distill)

    # ---- scheduling ------------------------------------------------------

    async def run_until_idle(self):
        hops = 0
        while hops < MAX_HOPS:
            pending = [n for n, q in self.queues.items() if q]
            if not pending:
                return
            name = pending[0]
            batch, self.queues[name] = self.queues[name], []
            msgs = "\n\n".join(f"[from {m['from']}] {m['content']}" for m in batch)
            resp = await self.agents[name].chat(
                f"New messages:\n\n{msgs}\n\nHandle them now: do the work, use "
                "send_to_teammate to delegate/reply (including to 'user'), or "
                "spawn a worker for grunt tasks. Anything you write outside "
                "send_to_teammate is visible to nobody.")
            await resp.text()  # drain the turn
            if self.flags[name].compaction_pending:
                self.flags[name].compaction_pending = False
                print(f"  ℹ {name}: context compacted — distilling to memory")
                await (await self.agents[name].chat(
                    "Context was just compacted. Save durable learnings from "
                    "this session with save_memory NOW (update, don't "
                    "duplicate). Be brief.")).text()
            hops += 1
        print(f"[paused after {MAX_HOPS} agent turns — messages still queued: "
              f"{ {n: len(q) for n, q in self.queues.items() if q} }. "
              "Any command resumes.]")

    def usage_summary(self) -> str:
        lines = []
        for name, agent in self.agents.items():
            with contextlib.suppress(Exception):
                u = agent.conversation.total_usage
                p, c = u.prompt_token_count or 0, \
                    (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
                pin, pout = cfg.PRICING.get(cfg.DEFAULT_MODEL, cfg.DEFAULT_PRICE)
                lines.append(f"  {name}: {p:,} in / {c:,} out ~${p*pin/1e6+c*pout/1e6:.4f}")
        return "\n".join(lines) or "  (no usage yet)"


async def repl(team: Team, one_shot: str | None):
    await team.start()
    try:
        if one_shot:
            to, _, content = one_shot.partition(":")
            team.post("user", to.strip(), content.strip())
            await team.run_until_idle()
            return
        print("commands: say <agent> <msg> | broadcast <msg> | log [n] | roster |"
              " add <name> <role> | remove <name> | cycle <agent> | distill |"
              " usage | quit\n")
        while True:
            try:
                line = await asyncio.to_thread(input, "team> ")
            except (EOFError, KeyboardInterrupt):
                line = "quit"
                print()
            cmd, _, rest = line.strip().partition(" ")
            if not cmd:
                continue
            if cmd == "quit":
                break
            elif cmd == "say":
                to, _, content = rest.partition(" ")
                team.post("user", to, content)
                await team.run_until_idle()
            elif cmd == "broadcast":
                for n in list(team.queues):
                    team.post("user", n, rest)
                await team.run_until_idle()
            elif cmd == "log":
                n = int(rest) if rest.strip().isdigit() else 20
                lines = team.bus_log.read_text().splitlines()[-n:] \
                    if team.bus_log.exists() else []
                for l in lines:
                    e = json.loads(l)
                    print(f"  {e['ts']} {e['from']} → {e['to']}: {e['content'][:120]}")
            elif cmd == "roster":
                for a in team.roster["agents"]:
                    print(f"  {a['name']}: {a['role']}")
            elif cmd == "add":
                nm, _, role = rest.partition(" ")
                if not nm or not role:
                    print("usage: add <name> <role description>")
                    continue
                spec = {"name": nm, "role": role, "model": cfg.DEFAULT_MODEL}
                team.roster["agents"].append(spec)
                team.roster_path.write_text(json.dumps(team.roster, indent=2))
                await team._start_agent(spec)
                print(f"  added {nm} (teammates learn of them next session; "
                      "or broadcast an introduction now)")
            elif cmd == "remove":
                nm = rest.strip()
                team.roster["agents"] = [a for a in team.roster["agents"]
                                         if a["name"] != nm]
                team.roster_path.write_text(json.dumps(team.roster, indent=2))
                team.queues.pop(nm, None)
                await team._close_agent(nm)
                print(f"  removed {nm} from roster and bus")
            elif cmd == "cycle":
                await team.cycle_agent(rest.strip())
            elif cmd == "distill":
                targets = [rest.strip()] if rest.strip() else list(team.agents)
                for n in targets:
                    if n in team.agents:
                        r = await team.agents[n].chat(
                            "Checkpoint: save durable learnings with save_memory "
                            "now (update, don't duplicate). Be brief.")
                        await r.text()
            elif cmd == "usage":
                print(team.usage_summary())
            else:
                print("unknown command")
    finally:
        summary = team.usage_summary()
        await team.stop(distill=one_shot is None)
        print(summary)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agy-team-shared")
    ap.add_argument("--team-dir", default=str(cfg.PROJECT_ROOT / "team"))
    ap.add_argument("--say", help="One-shot: '<agent>: <message>', run until idle, exit")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    asyncio.run(repl(Team(Path(args.team_dir), quiet=args.quiet), args.say))


if __name__ == "__main__":
    main()
