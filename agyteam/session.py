"""Open an interactive agy session *as* a teammate.

    python -m agyteam.session tpm
    python -m agyteam.session coder -- --effort high

`agy --agent tpm` alone is not enough: the plugin's MCP servers take their
identity from AGYTEAM_AGENT, so without it both refuse to start and you get an
agent with no memory and no mailbox — which looks like a personality problem
rather than a configuration one. This sets the identity, points at the right
team, checks the name against the roster, and hands off to agy.

Mail sent to you during the session lands in your inbox as usual; teammates are
only woken by the supervisor, so run `--daemon` alongside if you want replies
to be acted on while you talk.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import roster as roster_lib
from . import scope


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="agyteam.session",
        description="Interactive agy session as one of your teammates")
    ap.add_argument("agent", help="Teammate name, as listed in roster.json")
    ap.add_argument("--team-dir", default=None)
    ap.add_argument("--binary", default="agy")
    ap.add_argument("--fresh", action="store_true",
                    help="Start a new conversation instead of joining the "
                         "one the agent is working in")
    # Split on "--" by hand rather than argparse.REMAINDER: REMAINDER swallows
    # everything after the agent name, so `session tpm --binary x` would
    # silently forward "--binary" to agy instead of honouring it here.
    argv = list(sys.argv[1:] if argv is None else argv)
    passthrough: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, passthrough = argv[:cut], argv[cut + 1:]
    args = ap.parse_args(argv)

    # Same precedence the transport and runner use, so all three agree about
    # which team you are talking to: flag, then env, then scope defaults.
    env_dir = os.environ.get("AGYTEAM_TEAM_DIR")
    if args.team_dir:
        team_dir = Path(args.team_dir)
    elif env_dir:
        team_dir = Path(env_dir)
    else:
        team_dir = scope.load().team_dir()
    roster_path = team_dir / "roster.json"
    names = [a["name"] for a in roster_lib.load(roster_path)["agents"]]
    if args.agent not in names:
        sys.exit(f"unknown agent {args.agent!r}; roster at {roster_path} "
                 f"has: {', '.join(names) or 'nobody'}")

    resolved = shutil.which(args.binary)
    if not resolved:
        sys.exit(f"'{args.binary}' not found on PATH — install the Antigravity "
                 f"CLI, or pass --binary /path/to/agy")

    env = {**os.environ,
           "AGYTEAM_AGENT": args.agent,
           "AGYTEAM_TEAM_DIR": str(team_dir)}
    # No --agent: that mechanism strips builtin tools (see persona.py). The
    # agent's role lives in the conversation we are joining; identity for the
    # MCP servers comes from AGYTEAM_AGENT below.
    cmd = [resolved]
    # Join the conversation the agent is actually working in, rather than a
    # fresh copy of it that knows nothing about what the team just did.
    conv_id = None
    if not args.fresh:
        try:
            conv_id = json.loads(
                (team_dir / "conversations.json").read_text()).get(args.agent)
        except (OSError, ValueError):
            conv_id = None
    if conv_id:
        cmd += ["--conversation", conv_id]
    cmd += passthrough
    where = f"conversation {conv_id[:8]}" if conv_id else "new conversation"
    print(f"[{args.agent} — team {team_dir} — {where}]", file=sys.stderr)
    os.execve(resolved, cmd, env)   # replace this process; agy owns the tty


if __name__ == "__main__":
    sys.exit(main())
