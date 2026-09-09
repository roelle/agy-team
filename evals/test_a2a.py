"""Behavioral test of the plugin's runtime wiring, using real models.

Mounts the memory and bus MCP servers exactly as plugin/mcp_config.template.json
does — same modules, same env-derived identity (CLAWAGY_AGENT) — but through the
SDK, since the agy CLI is not installed here. What this proves transfers to the
plugin: env identity resolves, A2A messages cross *separate agent processes*,
memory lands in durable scope, and the roles behave.

Usage: .venv/bin/python evals/test_a2a.py
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

from google.antigravity import Agent, CapabilitiesConfig, LocalAgentConfig, types
from google.antigravity.hooks import policy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clawagy import config as cfg          # noqa: E402
from clawagy import scope                  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
SANDBOX = ROOT / "evals" / "a2a_sandbox"
RULES = (ROOT / "plugin" / "clawagy" / "rules" / "grounding.md").read_text()


def agent_config(name: str, durable: Path, team_dir: Path) -> LocalAgentConfig:
    """Mirror of the installed plugin: agent.md identity + both MCP servers."""
    agent_md = (ROOT / "plugin" / "clawagy" / "agents" / name / "agent.md").read_text()
    identity = agent_md.split("---", 2)[2].strip()   # strip YAML frontmatter
    env = {"PYTHONPATH": str(ROOT), "CLAWAGY_AGENT": name,
           "CLAWAGY_TEAM_DIR": str(team_dir), "CLAWAGY_DURABLE_DIR": str(durable)}
    return LocalAgentConfig(
        system_instructions=types.TemplatedSystemInstructions(
            identity=identity,
            sections=[types.SystemInstructionSection(
                title="grounding_and_collaboration_rules", content=RULES)]),
        mcp_servers=[
            types.McpStdioServer(name="clawagy_memory", type="stdio",
                                 command=PY, args=["-m", "clawagy.mcp_memory"],
                                 env=env),
            types.McpStdioServer(name="clawagy_bus", type="stdio",
                                 command=PY, args=["-m", "clawagy.mcp_bus"],
                                 env=env),
        ],
        capabilities=CapabilitiesConfig(enable_subagents=False),
        workspaces=[str(SANDBOX)], policies=[policy.allow_all()],
        model=cfg.DEFAULT_MODEL, api_key=cfg.api_key(),
        budget_config=types.BudgetConfig(max_model_calls=25))


async def run(name: str, prompt: str, durable: Path, team_dir: Path) -> str:
    """One agent, one turn, then the process (and its MCP servers) go away."""
    async with Agent(agent_config(name, durable, team_dir)) as a:
        return await (await a.chat(prompt)).text()


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'} {label}")
    if not ok and detail:
        print(f"       {detail[:400]}")
    return ok


async def main() -> int:
    durable = ROOT / "evals" / "a2a_durable"
    for d in (durable, SANDBOX):
        shutil.rmtree(d, ignore_errors=True)
    SANDBOX.mkdir(parents=True)
    scopes = scope.load(durable=durable, project=SANDBOX)
    team_dir = scopes.team_dir()
    (team_dir / "roster.json").write_text(json.dumps({"agents": [
        {"name": "tpm", "role": "Coordinates and delegates; no shell."},
        {"name": "coder", "role": "Implements and verifies code."},
        {"name": "syseng", "role": "Environment owner and independent verifier."}]}))

    print("\n== A2A over MCP, across separate agent processes ==")
    print("  [1/4] tpm delegates...")
    r1 = await run("tpm",
                   "The user wants a file 'hello.txt' in the shared sandbox "
                   f"({SANDBOX}) containing the single word 'ostrich'. You have "
                   "no shell. Delegate this to the right teammate now, with "
                   "complete context including the exact path and content.",
                   durable, team_dir)

    print("  [2/4] coder reads inbox in a fresh process and does the work...")
    r2 = await run("coder",
                   "Check your inbox and carry out whatever it asks. When done, "
                   "reply to whoever sent it.", durable, team_dir)

    print("  [3/4] tpm checks for the reply...")
    r3 = await run("tpm", "Check your inbox and summarize what you got.",
                   durable, team_dir)

    print("  [4/4] syseng independently verifies + saves a memory...")
    r4 = await run("syseng",
                   f"Independently verify whether {SANDBOX}/hello.txt exists and "
                   "what it contains — run a command, don't take anyone's word. "
                   "Then save a memory named 'sandbox-path' recording where this "
                   "team's shared sandbox lives. Report exactly what you observed.",
                   durable, team_dir)

    bus = (team_dir / "bus.jsonl")
    hops = [json.loads(l) for l in bus.read_text().splitlines()] if bus.exists() else []
    hop_str = " ; ".join(f"{h['from']}->{h['to']}" for h in hops)
    target = SANDBOX / "hello.txt"
    mem_dir = durable / "agents" / "syseng" / "memory"

    print()
    score = sum([
        check("tpm delegated over the bus (did not do it itself)",
              any(h["from"] == "tpm" and h["to"] in ("coder", "syseng") for h in hops),
              hop_str),
        check("coder received mail across processes and acted",
              target.exists(), f"file missing; coder said: {r2[:200]}"),
        check("deliverable content correct",
              target.exists() and "ostrich" in target.read_text().lower(),
              target.read_text() if target.exists() else "no file"),
        check("coder replied to sender",
              any(h["from"] == "coder" for h in hops), hop_str),
        check("tpm saw the reply", "ostrich" in r3.lower() or "hello.txt" in r3.lower(),
              r3[:300]),
        check("syseng verified by execution and reported truthfully",
              "ostrich" in r4.lower(), r4[:300]),
        check("memory written to DURABLE scope, not the project dir",
              mem_dir.exists() and any(mem_dir.glob("*.md")),
              f"looked in {mem_dir}"),
        check("no memory leaked into the project sandbox",
              not (SANDBOX / "memory").exists()),
    ])
    print(f"\n  bus trace: {hop_str}")
    print(f"\n== A2A behavioral: {score}/8 ==")
    return 0 if score == 8 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
