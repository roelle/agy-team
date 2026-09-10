"""CLI for the SDK-backed Agy."""
import argparse
import asyncio
import sys
from pathlib import Path

from google.antigravity import Agent

from . import config as cfg
from .sdk_agent import SessionFlags, build_config

DISTILL_PROMPT = (
    "Session is ending. Review this conversation for anything durable you "
    "haven't saved yet (preferences, corrections, facts, lessons). Call "
    "save_memory / delete_memory as needed — update existing memories rather "
    "than duplicating. If nothing new, say so briefly.")

COMPACT_DISTILL_PROMPT = (
    "Context was just compacted; details from earlier in this session are now "
    "summarized. Save any durable learnings from this session with save_memory "
    "NOW (update, don't duplicate). Be brief.")


def _trace(result):
    print(f"  ⚙ {result.name}", flush=True)
    if result.error:
        print(f"    ↳ error: {str(result.error)[:120]}", flush=True)
    else:
        first = str(result.result or "").splitlines()
        print(f"    ↳ {first[0][:120] if first else ''}", flush=True)


def _usage_line(agent: Agent, model: str) -> str:
    try:
        u = agent.conversation.total_usage
        p = u.prompt_token_count or 0
        c = (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)
        pin, pout = cfg.PRICING.get(model, cfg.DEFAULT_PRICE)
        return f"[usage] {p:,} in / {c:,} out tokens, ~${p*pin/1e6 + c*pout/1e6:.4f}"
    except Exception as e:
        return f"[usage] unavailable ({e})"


async def _chat(agent, flags, text) -> str:
    """One turn + automatic distill if the harness compacted during it."""
    resp = await agent.chat(text)
    out = await resp.text()
    if flags.compaction_pending:
        flags.compaction_pending = False
        print("  ℹ context was compacted — distilling to memory", flush=True)
        await (await agent.chat(COMPACT_DISTILL_PROMPT)).text()
    return out


async def amain(args):
    trace = None if args.quiet else _trace

    def fresh():
        flags = SessionFlags()
        return build_config(Path(args.workspace), model=args.model,
                            name=args.name, interactive=not args.prompt,
                            trace=trace, use_mcp=args.mcp, flags=flags), flags

    config, flags = fresh()
    if args.prompt:
        async with Agent(config) as agent:
            print(await _chat(agent, flags, args.prompt))
            if not args.no_distill:
                await agent.chat(DISTILL_PROMPT)
            print(f"\n{_usage_line(agent, args.model)}", file=sys.stderr)
        return

    print(f"{args.name} (SDK/localharness) ready — model {args.model}"
          f"{', memory via MCP' if args.mcp else ''}")
    print("Commands: /quit /distill /cycle\n")
    cycling = True
    while cycling:
        async with Agent(config) as agent:
            while True:
                try:
                    user = (await asyncio.to_thread(input, "you> ")).strip()
                except (EOFError, KeyboardInterrupt):
                    user = "/quit"
                    print()
                if not user:
                    continue
                if user in ("/quit", "/cycle"):
                    if not args.no_distill:
                        print("distilling session to memory...")
                        print(await (await agent.chat(DISTILL_PROMPT)).text())
                    print(_usage_line(agent, args.model))
                    cycling = user == "/cycle"
                    break
                if user == "/distill":
                    print(await (await agent.chat(DISTILL_PROMPT)).text())
                    continue
                print(f"\n{args.name}> {await _chat(agent, flags, user)}\n")
        if cycling:
            print("— fresh session (identity and memory index reloaded) —\n")
            config, flags = fresh()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agyteam-sdk",
                                 description="Agy on the google-antigravity SDK")
    ap.add_argument("-p", "--prompt")
    ap.add_argument("-w", "--workspace", default=str(cfg.PROJECT_ROOT / "workspace"))
    ap.add_argument("-m", "--model", default=cfg.DEFAULT_MODEL)
    ap.add_argument("--name", default="Agy")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-distill", action="store_true")
    ap.add_argument("--mcp", action="store_true",
                    help="serve memory tools via the MCP server instead of "
                         "in-process callables (same files either way)")
    asyncio.run(amain(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
