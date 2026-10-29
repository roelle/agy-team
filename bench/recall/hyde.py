"""Bridging the gap between a request and a lesson, at scale.

A request is written in the words of a task; a memory in the words of a
lesson. Two ways to meet in the middle, measured as embed.py measures
candidates (how often the target is in the top k):

  hyde      a fast model writes the lessons an experienced agent would
            want to recall for the message; those are embedded with it,
            and a memory scores its best similarity to any of them
  trigvec   each memory's triggers -- the situations it applies to -- get
            their own vector, and a memory scores the better of its text
            and its triggers

    python bench/recall/hyde.py own own+1000 own+10000
"""
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

KS = (3, 10, 20)
HYDE = """An AI agent on a team has just received this message:

<message>
{message}
</message>

Before acting, it would search the lessons it saved from past work. Write 3 lessons, one sentence each, that an experienced agent might have saved and would want in front of it for THIS message -- what to check, what to watch out for, how this kind of work goes wrong. Return JSON {{"lessons": ["...", "...", "..."]}}"""


def main():
    mems, qs, un, trig = ev.load()
    targets = [m for m in mems if m["real"]]
    distract = [m for m in mems if not m["real"]]
    mvec = em.embed_all({m["id"]: em.mem_text(m, trig) for m in mems}, "RETRIEVAL_DOCUMENT")
    tvec = em.embed_all({f"trig|{m['id']}": "Situations where this applies: " + "; ".join(trig.get(m["id"], []))
                         for m in mems}, "RETRIEVAL_DOCUMENT")
    kinds = ("paraphrase", "oblique", "direct")
    qtexts = {f"{q['id']}|{k}": q[k] for q in qs for k in kinds if q.get(k)}
    qvec = em.embed_all(qtexts, "RETRIEVAL_QUERY")

    def hyde(key):
        ans = llm.ask_json(HYDE.format(message=qtexts[key]), ev.DATA / "hyde-cache", thinking="low")
        lessons = ans.get("lessons") if isinstance(ans, dict) else ans
        return key, [l for l in (lessons or []) if isinstance(l, str)][:3]
    lessons = dict(llm.pmap(hyde, list(qtexts), 16))
    lvec = em.embed_all({f"{k}|{i}": l for k, ls in lessons.items() for i, l in enumerate(ls)}, "RETRIEVAL_QUERY")
    print("hyde:", llm.cost())

    n = lambda v: v / (np.linalg.norm(v) + 1e-9)  # noqa: E731
    V = {k: n(np.array(v, dtype=np.float32)) for k, v in mvec.items()}
    T = {k[5:]: n(np.array(v, dtype=np.float32)) for k, v in tvec.items()}
    for scale in sys.argv[1:] or ["own"]:
        pool_of, _ = ev.pools(targets, distract, scale)
        res = {g: defaultdict(lambda: defaultdict(list)) for g in ("embed", "trigvec", "hyde", "hyde+trig")}
        last = None
        for q in qs:
            pool = pool_of[q["id"]]
            if last is not pool:
                ids = [m["id"] for m in pool]
                M = np.stack([V[i] for i in ids])
                MT = np.stack([T[i] for i in ids])
                last = pool
            for kind in kinds:
                key = f"{q['id']}|{kind}"
                if key not in qvec:
                    continue
                qv = n(np.array(qvec[key], dtype=np.float32))
                base, tri = M @ qv, MT @ qv
                hv = [n(np.array(lvec[f"{key}|{i}"], dtype=np.float32)) for i in range(len(lessons.get(key, [])))
                      if f"{key}|{i}" in lvec]
                hy = np.max(np.stack([M @ h for h in hv] + [base]), axis=0) if hv else base
                hyt = np.max(np.stack([MT @ h for h in hv] + [hy, tri]), axis=0) if hv else np.maximum(base, tri)
                for g, s in (("embed", base), ("trigvec", np.maximum(base, tri)), ("hyde", hy), ("hyde+trig", hyt)):
                    top = [ids[i] for i in np.argsort(-s)[: max(KS)]]
                    for k in KS:
                        res[g][kind][k].append(q["id"] in top[:k])
        for g, d in res.items():
            for kind in kinds:
                row = d[kind]
                print(f"{g:9} {scale:>9} {kind:10} " + " ".join(f"@{k}:{100*sum(v)/len(v):5.1f}" for k, v in row.items()))
        print()
    print(llm.cost())


if __name__ == "__main__":
    main()
