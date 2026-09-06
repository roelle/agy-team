"""CLI for the SDK-backed Clawy. Same flags/contract as clawagy.cli so the
eval harness can drive either implementation."""
import argparse
import asyncio
import sys
from pathlib import Path

from google.antigravity import Agent

from . import config as cfg
from .sdk_agent import build_config

DISTILL_PROMPT = (
    "Session is ending. Review this conversation for anything durable you "
    "haven't saved yet (preferences, corrections, facts, lessons). Call "
    "save_memory / delete_memory as needed — update existing memories rather "
    "than duplicating. If nothing new, say so briefly.")


def _trace(result):
    args = getattr(result, "args", None)
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


async def amain(args):
    trace = None if args.quiet else _trace
    config = build_config(Path(args.workspace), model=args.model, name=args.name,
                          interactive=not args.prompt, trace=trace)
    async with Agent(config) as agent:
        if args.prompt:
            resp = await agent.chat(args.prompt)
            print(await resp.text())
            if not args.no_distill:
                await agent.chat(DISTILL_PROMPT)
            print(f"\n{_usage_line(agent, args.model)}", file=sys.stderr)
            return
        print(f"{args.name} (SDK/localharness) ready — model {args.model}")
        print("Commands: /quit /distill\n")
        while True:
            try:
                user = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                user = "/quit"
                print()
            if not user:
                continue
            if user == "/quit":
                if not args.no_distill:
                    print("distilling session to memory...")
                    print(await (await agent.chat(DISTILL_PROMPT)).text())
                print(_usage_line(agent, args.model))
                break
            if user == "/distill":
                print(await (await agent.chat(DISTILL_PROMPT)).text())
                continue
            resp = await agent.chat(user)
            print(f"\n{args.name}> {await resp.text()}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="clawagy-sdk",
                                 description="Clawy on the google-antigravity SDK")
    ap.add_argument("-p", "--prompt")
    ap.add_argument("-w", "--workspace", default=str(cfg.PROJECT_ROOT / "workspace"))
    ap.add_argument("-m", "--model", default=cfg.DEFAULT_MODEL)
    ap.add_argument("--name", default="Clawy")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-distill", action="store_true")
    asyncio.run(amain(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
