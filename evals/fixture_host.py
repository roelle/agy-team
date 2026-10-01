"""A fake agent host, for exercising runner_host without one.

Behaves like the narrow surface every hosted runtime has: `start` prints a
conversation id, `send` delivers a message, and a "stop hook" appends a line
to a signal file when the turn ends -- after a delay, from a detached
process, so the runner really has to poll. Every call is logged so a test can
check what the runner did and in what order. Deliver records whether the
conversation was registered in conversations.json at the moment it ran,
which is the whole of "register before you deliver".

    FAKE_HOST_LOG       where to log calls (jsonl)
    FAKE_HOST_SIGNALS   signal directory
    FAKE_HOST_DELAY     seconds before the turn "ends" (default 0.2)
    FAKE_HOST_HANG      if set, the turn never ends
    FAKE_HOST_FAIL      if set, `send` exits 1
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_counter = Path(os.environ.get("FAKE_HOST_SIGNALS", ".")) / ".counter"


def log(**rec):
    with open(os.environ["FAKE_HOST_LOG"], "a") as f:
        f.write(json.dumps({"ts": time.time(), **rec}) + "\n")


def argval(args, flag, default=""):
    return args[args.index(flag) + 1] if flag in args else default


def registered(conversation):
    td = os.environ.get("AGYTEAM_TEAM_DIR", "")
    try:
        return conversation in json.loads(Path(td, "conversations.json").read_text()).values()
    except (OSError, ValueError):
        return False


def end_turn_later(conversation):
    if os.environ.get("FAKE_HOST_HANG"):
        return
    subprocess.Popen([sys.executable, __file__, "complete", conversation],
                     start_new_session=True, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def main(argv):
    cmd, args = argv[0], argv[1:]
    if cmd == "start":
        n = int(_counter.read_text()) + 1 if _counter.exists() else 1
        _counter.write_text(str(n))
        conv = f"conv-{n}"
        log(call="start", conversation=conv, agent=argval(args, "--agent"),
            model=argval(args, "--model"), prompt=argval(args, "--prompt"),
            agent_env=os.environ.get("AGYTEAM_AGENT"))
        print(json.dumps({"conversation_id": conv, "status": "created"}))
        end_turn_later(conv)
    elif cmd == "send":
        conv = argval(args, "--conversation")
        message = argval(args, "--message") or sys.stdin.read()
        log(call="send", conversation=conv, message=message,
            registered=registered(conv), agent_env=os.environ.get("AGYTEAM_AGENT"))
        if os.environ.get("FAKE_HOST_FAIL"):
            sys.stderr.write("session not found\n")
            return 1
        end_turn_later(conv)
    elif cmd == "cancel":
        log(call="cancel", conversation=args[0])
    elif cmd == "complete":
        time.sleep(float(os.environ.get("FAKE_HOST_DELAY", "0.2")))
        sig = Path(os.environ["FAKE_HOST_SIGNALS"]) / f"{args[0]}.jsonl"
        with sig.open("a") as f:
            f.write(json.dumps({"turn": "ended", "input_tokens": 120,
                                "output_tokens": 30, "total_tokens": 150}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
