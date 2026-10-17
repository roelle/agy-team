"""Talk straight to a specialist: does the team still work like a team?

The delivery eval starts every request at the coordinator. People do not. They
open the coder's window and ask the coder, and the failure that follows is
quiet: the specialist answers from its own view, nobody checks the work, and
the coordinator never learns the work happened -- so the next thing it plans
is planned without it.

This gives the default seeded team (manager, tpm, coder, syseng, qa) one
request addressed to `coder` and nobody else. The request says nothing about
review or about the tpm, on purpose: what is being measured is whether the
team's own norms carry it there, not whether an agent follows an instruction
to. It passes only if

- the work is right, by checks the team never sees;
- a teammate other than the author verified it with a recorded, approved
  review whose proof file runs;
- the tpm was told, by someone, unprompted;
- the user was answered, and only after that review.

Usage: .venv/bin/python evals/test_direct_specialist.py   (live; ~20-60c)
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from rpc_util import ROOT, check

sys.path.insert(0, str(ROOT))

TEAM = "direct-eval"
MARKER = "direct-eval team, safe to wipe"
SHARED = ROOT / "evals" / "direct_shared"
TARGET = SHARED / "slug.py"

ASK = (f"I need a function slugify(title) in {TARGET} that I can import. "
       "Lowercase; letters with accents become their plain ASCII letter; any "
       "other run of characters that is not a letter or digit becomes a single "
       "hyphen; no hyphen at either end; and at most 60 characters, cutting "
       "at a hyphen rather than in the middle of a word (a single word longer "
       "than 60 is cut at 60). Let me know when it's ready.")

# Never shown to the team.
CASES = [
    ("Hello, World!", "hello-world"),
    ("  Crème brûlée -- à la carte  ", "creme-brulee-a-la-carte"),
    ("Ünïcödé___slugs", "unicode-slugs"),
    ("---", ""),
    ("a" * 70, "a" * 60),
    ("word " * 20, "-".join(["word"] * 12)),
    ("one two three four five six seven eight nine ten eleven twelvee",
     "one-two-three-four-five-six-seven-eight-nine-ten-eleven"),
    ("Version 2.0 released", "version-2-0-released"),
]


def default_roster() -> dict:
    """The roster `plugin/install.sh` seeds: the product's own defaults."""
    text = (ROOT / "plugin" / "install.sh").read_text()
    body = re.search(r"<<JSON\n(.*?)\nJSON\n", text, re.S).group(1)
    doc = json.loads(body.replace("$TEAM", TEAM))
    doc["mission"] = MARKER
    return doc


def hidden_checks() -> tuple[int, list[str]]:
    if not TARGET.exists():
        return 0, ["no file"]
    proc = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util, json, sys\n"
         f"spec = importlib.util.spec_from_file_location('slug', {str(TARGET)!r})\n"
         "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
         f"cases = {CASES!r}\n"
         "out = []\n"
         "for given, want in cases:\n"
         "    try: got = m.slugify(given)\n"
         "    except Exception as e: got = f'raised {type(e).__name__}'\n"
         "    out.append([given, want, got])\n"
         "print(json.dumps(out))"],
        capture_output=True, text=True, timeout=60)
    try:
        rows = json.loads(proc.stdout)
    except ValueError:
        return 0, [proc.stderr[-300:]]
    bad = [f"{g!r}: want {w!r}, got {o!r}" for g, w, o in rows if o != w]
    return len(rows) - len(bad), bad


def main() -> int:
    from rpc_util import forget_ambient_team
    forget_ambient_team()
    os.environ["AGYTEAM_TEAM"] = TEAM
    shutil.rmtree(SHARED, ignore_errors=True)
    SHARED.mkdir(parents=True)
    os.environ["AGYTEAM_SHARED_DIR"] = str(SHARED)

    from agyteam import scope
    team_dir = scope.load().team_dir()
    # It wipes the whole durable directory -- memories included, or the team
    # would not start cold -- so only one it can prove it made.
    durable = team_dir.parent
    existing = team_dir / "roster.json"
    try:
        mission = json.loads(existing.read_text()).get("mission", "") if existing.exists() else None
    except ValueError:
        mission = ""
    others = [p for p in durable.iterdir() if p.name != "team"] if durable.exists() else []
    if (mission is not None and mission != MARKER) or (mission is None and others):
        sys.exit(f"refusing to wipe {durable}: it is not this eval's "
                 f"(mission={mission!r}). Move it aside or pick another team.")
    shutil.rmtree(durable, ignore_errors=True)
    team_dir.mkdir(parents=True)
    (team_dir / "inbox").mkdir()
    roster = default_roster()
    (team_dir / "roster.json").write_text(json.dumps(roster, indent=2))
    os.environ["AGYTEAM_TEAM_DIR"] = str(team_dir)
    os.environ.setdefault("AGYTEAM_AUDIT_LOG", str(durable.parent / f"{TEAM}-audit.jsonl"))

    from agyteam import runner as runner_lib
    from agyteam.runner_agy import AgyRunner
    from agyteam.supervisor import Supervisor
    from agyteam.transport import load as load_transport

    runner = runner_lib.load()
    if isinstance(runner, AgyRunner) and not runner.available():
        print("agy not on PATH — skipping")
        return 0
    print(f"  runner: {runner.label}")

    print(f"\n== direct to a specialist ({TEAM}) ==")
    names = [a["name"] for a in roster["agents"]]
    sup = Supervisor(names, runner, team_dir=team_dir, max_hops=24)
    load_transport("user").send("coder", ASK)
    turns = sup.run_until_idle()
    sup.close()

    hops = [json.loads(l) for l in (team_dir / "bus.jsonl").read_text().splitlines()]
    trace = " ; ".join(f"{h['from']}->{h['to']}({h.get('kind', 'work')})" for h in hops)
    reviews = []
    if (team_dir / "reviews.jsonl").exists():
        reviews = [json.loads(l) for l in (team_dir / "reviews.jsonl").read_text().splitlines()
                   if l.strip()]
    independent = [r for r in reviews if r.get("kind", "work") == "work"
                   and r.get("verdict") == "approved"
                   and r.get("author") == "coder" and r.get("reviewer") not in ("coder", None)]
    to_user = [h for h in hops if h["to"] == "user" and h["from"] != "supervisor"]
    first_review = min((r["ts"] for r in independent), default=None)
    told_tpm = [h for h in hops if h["to"] == "tpm" and h["from"] != "supervisor"]
    passed, bad = hidden_checks()

    print(f"\n  trace: {trace}\n  turns: {turns}\n  reviews: "
          f"{[(r.get('reviewer'), r.get('author'), r.get('verdict')) for r in reviews]}\n")
    score = sum([
        check("the work passes checks the team never saw",
              passed == len(CASES), f"{passed}/{len(CASES)}; " + "; ".join(bad[:4])),
        check("coder did not work alone",
              len({h["from"] for h in hops} - {"user", "supervisor", "coder"}) >= 1, trace),
        check("a teammate independently approved coder's work with a recorded review",
              bool(independent), str(reviews)[:300]),
        check("the tpm was looped in, unprompted", bool(told_tpm), trace),
        check("the user was answered", bool(to_user), trace),
        check("the answer came after the review, not before",
              bool(to_user and first_review and to_user[0]["ts"] >= first_review),
              f"first answer {to_user[0]['ts'] if to_user else None}, "
              f"first independent review {first_review}"),
        check("the episode ended on the answer, not the hop budget",
              sup.stopped.startswith("the user was answered"), sup.stopped),
    ])
    print(f"\n== direct to a specialist: {score}/7 ==")
    return 0 if score == 7 else 1


if __name__ == "__main__":
    sys.exit(main())
