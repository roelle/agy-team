"""CLI for a single Clawy agent: interactive REPL and one-shot mode."""
import argparse
import sys
from pathlib import Path

from . import config
from .agent import Agent


def _print_event(kind, payload):
    if kind == "tool":
        args = ", ".join(f"{k}={str(v)[:80]!r}" for k, v in payload["args"].items())
        print(f"  ⚙ {payload['name']}({args})", flush=True)
    elif kind == "tool_result":
        first = payload["output"].splitlines()[0] if payload["output"] else ""
        print(f"    ↳ {first[:120]}", flush=True)
    elif kind == "info":
        print(f"  ℹ {payload['msg']}", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="clawagy", description="Clawy — learning CLI agent on Gemini/Antigravity")
    ap.add_argument("-p", "--prompt", help="One-shot: run this prompt and exit")
    ap.add_argument("-w", "--workspace", default=str(config.PROJECT_ROOT / "workspace"),
                    help="Agent workspace dir (identity + memory live here)")
    ap.add_argument("-m", "--model", default=config.DEFAULT_MODEL)
    ap.add_argument("--name", default="Clawy")
    ap.add_argument("--quiet", action="store_true", help="Suppress tool-call trace")
    ap.add_argument("--no-distill", action="store_true",
                    help="Skip the end-of-session memory distillation pass")
    args = ap.parse_args(argv)

    agent = Agent(Path(args.workspace), model=args.model, name=args.name)
    on_event = (lambda k, p: None) if args.quiet else _print_event

    if args.prompt:
        print(agent.run_turn(args.prompt, on_event))
        if not args.no_distill:
            agent.distill(on_event)
        print(f"\n[usage] {agent.llm.usage.summary()}", file=sys.stderr)
        return

    print(f"{args.name} ready — model {args.model}, workspace {args.workspace}")
    print("Commands: /quit /cost /compact /distill /memory\n")
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            user = "/quit"
            print()
        if not user:
            continue
        if user == "/quit":
            if not args.no_distill and agent.history:
                print("distilling session to memory...")
                print(agent.distill(on_event))
            print(f"[usage] {agent.llm.usage.summary()}")
            break
        if user == "/cost":
            print(agent.llm.usage.summary())
            continue
        if user == "/compact":
            print(agent.compact())
            continue
        if user == "/distill":
            print(agent.distill(on_event))
            continue
        if user == "/memory":
            print((agent.workspace / "MEMORY.md").read_text())
            continue
        try:
            print(f"\n{agent.name}> {agent.run_turn(user, on_event)}\n")
        except Exception as e:
            print(f"[error: {e}]", file=sys.stderr)


if __name__ == "__main__":
    main()
