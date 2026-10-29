"""Evaluate recall functions on the bench built by build.py.

    python bench/recall/eval.py [--scales own,500,2000,10000] [--grid]

For each corpus size, each target memory is buried among distractors, and
each of its test prompts is scored:

  hit      the target was offered (direct / paraphrase / oblique prompts)
  decoy    the target was offered for a prompt that only shares its words
  offered  how many memories a prompt brought in
  stray    share of unrelated prompts that brought in anything

"own" is the realistic size: only the memories of the agent that wrote the
target. "learned" variants add cues from a simulated correction -- the
paraphrase prompt's words are learned for its target -- and are scored on
the *oblique* prompt, which the learning never saw.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from agyteam import recall  # noqa: E402

DATA = Path(os.environ.get("RECALL_BENCH_DIR", "~/agy-lab-notes/recall-bench")).expanduser()


def load():
    mems = json.loads((DATA / "memories.json").read_text())
    qs = json.loads((DATA / "queries.json").read_text())
    un = json.loads((DATA / "unrelated.json").read_text())
    trig = json.loads((DATA / "triggers.json").read_text())
    return mems, qs, un, trig


def doc(m, trig, use_triggers, learned=None):
    d = {"id": m["id"], "name": m["name"].replace("-", " "), "description": m["description"],
         "body": m["body"]}
    if use_triggers:
        d["triggers"] = trig.get(m["id"], [])
    if learned and m["id"] in learned:
        d["learned"] = learned[m["id"]]
    return d


def learned_cues(qs, index_for_idf, top: int = 12) -> dict[str, list[str]]:
    """What a correction would teach: the most distinctive words of the
    prompt that missed (here, the paraphrase)."""
    out = {}
    for q in qs:
        ts = set(recall.terms(q.get("paraphrase", "")))
        ranked = sorted(ts, key=lambda t: -index_for_idf.idf.get(t, 0.0))
        out[q["id"]] = ranked[:top]
    return out


def resolve(policy, index, background):
    """A policy with "calib": q has its min_norm set from this index."""
    if "calib" not in policy:
        return policy
    p = {k: v for k, v in policy.items() if k != "calib"}
    p["min_norm"] = max(p.get("min_norm", 0.0), recall.calibrate(index, background, policy["calib"]))
    return p


def pools(targets, distract, scale):
    """{target id: the memories it is searched among}, and the pools to draw
    unrelated prompts against. Scales:
      own      each agent's own memories (what an agent has today)
      own+N    each agent's own memories plus the same N distractors (an
               agent that has accumulated N more)
      N        every target together plus distractors up to N (harder: your
               agents' memories include near-twins of each other's)"""
    from collections import defaultdict as _dd
    groups = _dd(list)
    for m in targets:
        groups[(m["id"].split("/")[0], m["agent"])].append(m)
    if scale == "own" or scale.startswith("own+"):
        extra = int(scale[4:]) if scale.startswith("own+") else 0
        noise = random.Random(11).sample(distract, min(extra, len(distract))) if extra else []
        built = {k: g + noise for k, g in groups.items()}
        pool_of = {m["id"]: built[(m["id"].split("/")[0], m["agent"])] for m in targets}
        big = [built[k] for k, g in groups.items() if len(g) >= 10]
        return pool_of, big
    pool = targets + random.Random(11).sample(distract, max(0, int(scale) - len(targets)))
    return {m["id"]: pool for m in targets}, [pool]


def run(mems, qs, un, trig, scale, variant, policy, rnd):
    # Half the unrelated prompts calibrate; the other half measure strays.
    shuffled = un[:]
    random.Random(5).shuffle(shuffled)
    background, held_out = shuffled[: len(un) // 2], shuffled[len(un) // 2:]
    targets = [m for m in mems if m["real"]]
    distract = [m for m in mems if not m["real"]]
    by_id = {m["id"]: m for m in mems}
    use_trig = variant in ("triggers", "learned")
    stats = defaultdict(list)

    def score(index, q, target_id, kinds, policy):
        for kind in kinds:
            texts = q.get(kind) if kind != "decoys" else q.get("decoys", [])
            for text in (texts if isinstance(texts, list) else [texts]):
                if not text:
                    continue
                got = recall.select(index.search(text, 20), **policy)
                ids = [g["id"] for g in got]
                stats["decoy" if kind == "decoys" else kind].append(target_id in ids)
                stats["offered"].append(len(ids))

    kinds = ["direct", "paraphrase", "oblique", "decoys"]
    if scale == "own":
        by_agent = defaultdict(list)
        for m in targets:
            by_agent[(m["id"].split("/")[0], m["agent"])].append(m)
        for (team, agent), group in by_agent.items():
            base = recall.Index([doc(m, trig, use_trig) for m in group])
            learned = learned_cues([q for q in qs if q["id"] in {m["id"] for m in group}], base) \
                if variant == "learned" else None
            index = recall.Index([doc(m, trig, use_trig, learned) for m in group]) if learned else base
            pol = resolve(policy, index, background)
            for q in qs:
                if q["id"] in {m["id"] for m in group}:
                    score(index, q, q["id"], kinds, pol)
            for text in rnd.sample(held_out, min(20, len(held_out))):
                stats["stray"].append(bool(recall.select(index.search(text, 20), **pol)))
    else:
        n = int(scale)
        pool = targets + rnd.sample(distract, max(0, min(len(distract), n - len(targets))))
        base = recall.Index([doc(m, trig, use_trig) for m in pool])
        learned = learned_cues(qs, base) if variant == "learned" else None
        index = recall.Index([doc(m, trig, use_trig, learned) for m in pool]) if learned else base
        pol = resolve(policy, index, background)
        for q in qs:
            if q["id"] in by_id:
                score(index, q, q["id"], kinds, pol)
        for text in held_out:
            stats["stray"].append(bool(recall.select(index.search(text, 20), **pol)))
    pct = lambda xs: 100 * sum(xs) / len(xs) if xs else float("nan")  # noqa: E731
    return {"direct": pct(stats["direct"]), "paraphrase": pct(stats["paraphrase"]),
            "oblique": pct(stats["oblique"]), "decoy": pct(stats["decoy"]),
            "offered": sum(stats["offered"]) / max(1, len(stats["offered"])),
            "stray": pct(stats["stray"]), "corpus": scale}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", default="own,500,2000,10000")
    ap.add_argument("--variants", default="content,triggers,learned")
    ap.add_argument("--grid", action="store_true", help="sweep thresholds")
    ap.add_argument("--calib", action="store_true", help="thresholds calibrated per index")
    a = ap.parse_args(argv)
    mems, qs, un, trig = load()
    n_all = len(mems)
    scales = [s for s in a.scales.split(",") if s == "own" or int(s) <= n_all]
    policies = {"top3 (no floor)": dict(k=3)}
    if a.calib:
        for cq in (0.8, 0.9, 0.95):
            policies[f"k3 calibrated q={cq}"] = dict(k=3, calib=cq)
    if a.grid:
        for mn in (0.15, 0.25, 0.35, 0.45):
            for rel in (0.0, 0.6):
                policies[f"k3 norm>={mn} rel>={rel}"] = dict(k=3, min_norm=mn, rel=rel)
    print(f"{len(mems)} memories ({sum(m['real'] for m in mems)} real), {len(qs)} targets, "
          f"{len(un)} unrelated prompts\n")
    print(f"{'variant':9} {'policy':24} {'corpus':>6} {'direct':>7} {'paraph':>7} {'oblique':>7} "
          f"{'decoy':>6} {'offered':>7} {'stray':>6}")
    for v in a.variants.split(","):
        for pname, pol in policies.items():
            for s in scales:
                r = run(mems, qs, un, trig, s, v, pol, random.Random(11))
                print(f"{v:9} {pname:24} {s:>6} {r['direct']:7.1f} {r['paraphrase']:7.1f} "
                      f"{r['oblique']:7.1f} {r['decoy']:6.1f} {r['offered']:7.2f} {r['stray']:6.1f}")
        print()


if __name__ == "__main__":
    main()
