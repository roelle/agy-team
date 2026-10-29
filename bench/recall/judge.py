"""The second stage: a fast model chooses, from the top candidates, the
memories that apply to the message -- and may choose none.

    python bench/recall/judge.py [--scale own|N] [--k 10] [--model gemini-3.8-flash]

Measured on the same prompts as the first stage:
  hit      the target was among the candidates and the judge kept it
  reach    the target was among the candidates at all (the ceiling for hit)
  decoy    the judge kept the target for a decoy prompt
  picks    memories kept per prompt
  quiet    share of unrelated prompts where the judge kept nothing
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
import embed as em  # noqa: E402
import eval as ev  # noqa: E402
import llm  # noqa: E402

JUDGE = """An AI agent on a team is about to handle this message:

<message>
{message}
</message>

Here are lessons it saved in its own memory earlier. Choose the ones that would change how a careful agent handles THIS message -- a lesson that applies to what is being asked, not one that merely shares words with it. Most messages need none; choose none unless one clearly applies. Choose at most 3.

{candidates}

Return JSON {{"apply": ["<id>", ...]}} with the ids you choose, or an empty list."""


def cand_text(m):
    return f"<memory id=\"{m['id']}\">\n{m['name']}: {m['description']}\n{m['body'][:600]}\n</memory>"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", default="own")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--model", default=llm.MODEL)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--thinking", default=None, help="thinking level for the judge, e.g. low")
    ap.add_argument("--via", default="api", choices=["api", "agy"], help="call the judge through the API or the agy CLI")
    ap.add_argument("--cands", default="embed", choices=["embed", "words"],
                    help="candidates by embeddings, or by words with triggers (no API key)")
    ap.add_argument("--sample", type=int, default=1,
                    help="judge every Nth target and unrelated prompt, to bound cost at large k")
    a = ap.parse_args(argv)
    mems, qs, un, trig = ev.load()
    by_id = {m["id"]: m for m in mems}
    targets = [m for m in mems if m["real"]]
    distract = [m for m in mems if not m["real"]]
    from agyteam import recall
    if a.cands == "embed":
        mvec = em.embed_all({m["id"]: em.mem_text(m, trig) for m in mems}, "RETRIEVAL_DOCUMENT")
    texts = {f"{q['id']}|{k}": q.get(k, "") for q in qs for k in ("direct", "paraphrase", "oblique") if q.get(k)}
    texts.update({f"{q['id']}|decoy{i}": d for q in qs for i, d in enumerate(q.get("decoys", []))})
    shuffled = un[:]
    random.Random(5).shuffle(shuffled)
    held_out = shuffled[len(un) // 2:]
    texts.update({f"unrelated|{i}": t for i, t in enumerate(held_out)})
    qvec = em.embed_all(texts, "RETRIEVAL_QUERY") if a.cands == "embed" else {k: None for k in texts}
    norm = lambda v: v / (np.linalg.norm(v) + 1e-9)  # noqa: E731
    V = {k: norm(np.array(v, dtype=np.float32)) for k, v in mvec.items()} if a.cands == "embed" else {}
    import threading
    lock = threading.Lock()

    pool_of, big = ev.pools(targets, distract, a.scale)
    mats = {}

    def candidates(pool, text_key):
        if a.cands == "words":
            key = ("w", id(pool))
            with lock:
                if key not in mats:
                    mats.clear()
                    mats[key] = recall.Index([ev.doc(m, trig, True) for m in pool])
                index = mats[key]
            return [r["id"] for r in index.search(texts[text_key], a.k)]
        key = id(pool)
        with lock:
            if key not in mats:
                mats.clear()                      # one pool's matrix at a time
                mats[key] = (np.stack([V[m["id"]] for m in pool]), [m["id"] for m in pool])
            M, ids = mats[key]
        sims = M @ norm(np.array(qvec[text_key], dtype=np.float32))
        return [ids[i] for i in np.argsort(-sims)[: a.k]]

    jobs = []
    for q in qs[:: a.sample]:
        for kind in ("direct", "paraphrase", "oblique"):
            if f"{q['id']}|{kind}" in qvec:
                jobs.append((kind, q["id"], f"{q['id']}|{kind}", pool_of[q["id"]]))
        for i, _ in enumerate(q.get("decoys", [])):
            jobs.append(("decoy", q["id"], f"{q['id']}|decoy{i}", pool_of[q["id"]]))
    rnd = random.Random(3)
    for i, _ in list(enumerate(held_out))[:: a.sample]:
        jobs.append(("unrelated", None, f"unrelated|{i}", rnd.choice(big)))

    # Candidates first, one pool after another, so each big matrix is built
    # once; then only the judge calls run in parallel.
    jobs.sort(key=lambda j: id(j[3]))
    jobs = [(kind, target, key, candidates(pool, key)) for kind, target, key, pool in jobs]

    def run(job):
        kind, target, key, cands = job
        prompt = JUDGE.format(message=texts[key], candidates="\n\n".join(cand_text(by_id[c]) for c in cands))
        if a.via == "agy":
            ans = llm.ask_agy(prompt, ev.DATA / "judge-cache-agy", model=a.model,
                              schema={"type": "object", "properties": {"apply": {"type": "array", "items": {"type": "string"}}},
                                      "required": ["apply"]})
        else:
            ans = llm.ask_json(prompt, ev.DATA / "judge-cache", model=a.model, thinking=a.thinking)
        raw = ans.get("apply") if isinstance(ans, dict) else ans if isinstance(ans, list) else []
        picks = [p for p in (raw or []) if isinstance(p, str) and p in cands][:3]
        return kind, target, cands, picks

    rows = llm.pmap(run, jobs, a.workers)
    stats = defaultdict(list)
    for kind, target, cands, picks in rows:
        stats[f"{kind}:picks"].append(len(picks))
        if kind == "unrelated":
            stats["quiet"].append(not picks)
        elif kind == "decoy":
            stats["decoy"].append(target in picks)
        else:
            stats[f"{kind}:reach"].append(target in cands)
            stats[f"{kind}:hit"].append(target in picks)
    pct = lambda v: 100 * sum(v) / len(v) if v else float("nan")  # noqa: E731
    print(f"judge {a.model} via {a.via} (thinking {a.thinking or 'default'}), {a.cands} top-{a.k}, corpus {a.scale}"
          + (f", every {a.sample}th target" if a.sample > 1 else ""))
    for kind in ("direct", "paraphrase", "oblique"):
        print(f"  {kind:10} hit {pct(stats[kind + ':hit']):5.1f}  (reach {pct(stats[kind + ':reach']):5.1f})  "
              f"picks {sum(stats[kind + ':picks']) / max(1, len(stats[kind + ':picks'])):.2f}")
    print(f"  decoy      kept {pct(stats['decoy']):5.1f}   picks {sum(stats['decoy:picks']) / max(1, len(stats['decoy:picks'])):.2f}")
    print(f"  unrelated  quiet {pct(stats['quiet']):5.1f}  picks {sum(stats['unrelated:picks']) / max(1, len(stats['unrelated:picks'])):.2f}")
    print(llm.cost())


if __name__ == "__main__":
    main()
