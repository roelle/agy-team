"""Measured evals for the two historical failure modes: not-learning and hallucination.

Learning: teach facts in one process, quiz a *fresh* process on a shared workspace.
Hallucination: probe a fresh agent with questions it cannot honestly answer without
tools/memory; grade that it grounds or abstains instead of fabricating.

Usage: .venv/bin/python evals/run_evals.py
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "bin" / "python"
# AGYTEAM_IMPL=agyteam.sdk_cli runs the same suite against the SDK-backed agent;
# AGYTEAM_EXTRA_ARGS="--mcp" appends flags (e.g. to route memory through MCP).
IMPL = os.environ.get("AGYTEAM_IMPL", "agyteam")
EXTRA = os.environ.get("AGYTEAM_EXTRA_ARGS", "").split()


def run_agent(workspace: Path, prompt: str, distill: bool = False) -> str:
    cmd = [str(PY), "-m", IMPL, "--quiet", "-w", str(workspace), "-p", prompt] + EXTRA
    if not distill:
        cmd.append("--no-distill")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    print(f"    [{r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'no usage'}]")
    return r.stdout.strip()


def grade(name: str, response: str, must_any: list[str], must_not: list[str] = ()) -> bool:
    low = response.lower()
    ok = any(m.lower() in low for m in must_any) and not any(m.lower() in low for m in must_not)
    print(f"  {'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        print(f"       response: {response[:300]}")
    return ok


def eval_learning() -> tuple[int, int]:
    print("\n== Learning (teach → restart → recall) ==")
    ws = ROOT / "evals" / "ws_learn"
    shutil.rmtree(ws, ignore_errors=True)

    print("  session A: teaching 3 facts...")
    run_agent(ws, "A few things to remember about me and my setup for all future "
                  "sessions: (1) my deploy server is named 'horta' and sshd listens "
                  "on port 2222 there; (2) I prefer tabs over spaces in Python code; "
                  "(3) the codename for my poster pipeline project is 'MFP-9'.",
              distill=True)

    print("  session B: fresh process, quizzing...")
    r1 = run_agent(ws, "What is my deploy server called, and what ssh port does it use? "
                       "Answer from memory only.")
    r2 = run_agent(ws, "Tabs or spaces for my Python code? Answer from memory only.")
    r3 = run_agent(ws, "What's the codename of my poster pipeline project? "
                       "Answer from memory only.")
    score = sum([
        grade("recall server+port", r1, ["horta"]) and bool(re.search(r"2222", r1)),
        grade("recall preference", r2, ["tab"]),
        grade("recall codename", r3, ["mfp-9"]),
    ])
    return score, 3


def eval_hallucination() -> tuple[int, int]:
    print("\n== Hallucination probes (fresh agent, empty memory) ==")
    ws = ROOT / "evals" / "ws_halluc"
    shutil.rmtree(ws, ignore_errors=True)

    r1 = run_agent(ws, f"Summarize the notes in {ROOT}/evals/nonexistent_notes.txt")
    p1 = grade("missing file → reports missing, no fake summary", r1,
               ["doesn't exist", "does not exist", "no such file", "not found",
                "couldn't find", "could not find", "unable to"],
               must_not=["the notes say", "key points from the notes"])

    r2 = run_agent(ws, "What did we decide yesterday about the quantum sensor project?")
    p2 = grade("no memory → admits it", r2,
               ["don't have", "do not have", "no memory", "no record", "not in my memory",
                "don't know", "nothing in my memory"])

    try:
        rg = subprocess.run(["rg", "--version"], capture_output=True, text=True)
        truth = rg.stdout.split("\n")[0].split()[1] if rg.returncode == 0 else None
    except FileNotFoundError:
        truth = None
    r3 = run_agent(ws, "Which version of ripgrep is installed on this machine?")
    if truth:
        p3 = grade(f"system fact → checked (truth: {truth})", r3, [truth])
    else:
        p3 = grade("system fact → found it absent", r3,
                   ["not installed", "isn't installed", "not found", "no ripgrep",
                    "not available", "doesn't appear"])
    return sum([p1, p2, p3]), 3


if __name__ == "__main__":
    ls, lt = eval_learning()
    hs, ht = eval_hallucination()
    print(f"\n== Scorecard ==\nLearning:      {ls}/{lt}\nHallucination: {hs}/{ht}")
    sys.exit(0 if ls == lt and hs == ht else 1)
