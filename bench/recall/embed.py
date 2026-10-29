"""Embeddings as a candidate generator, and blended with BM25, measured the
way candidates.py measures BM25: how often the target is in the top k.

Vectors are cached in RECALL_BENCH_DIR/emb.json, so a re-run costs nothing.
"""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
import eval as ev  # noqa: E402
import llm  # noqa: E402
from agyteam import recall  # noqa: E402

MODEL = "gemini-embedding-2"
KS = (1, 3, 5, 10, 20)
DIMS = 768                     # as the product stores them; as good as 3072 on the bench
CACHE = ev.DATA / f"emb{DIMS}.json"


def embed_all(texts: dict[str, str], task: str) -> dict[str, list[float]]:
    have = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [(k, t) for k, t in texts.items() if f"{task}|{k}" not in have]
    from google.genai import types
    c = llm.client()
    for i in range(0, len(todo), 100):
        chunk = todo[i:i + 100]
        for attempt in range(5):
            try:
                # One Content per text: a list of plain strings is embedded as
                # one multimodal item by gemini-embedding-2, giving one vector.
                r = c.models.embed_content(
                    model=MODEL, contents=[types.Content(parts=[types.Part(text=t[:6000])]) for _, t in chunk],
                    config=types.EmbedContentConfig(task_type=task, output_dimensionality=DIMS))
                if len(r.embeddings) != len(chunk):
                    raise RuntimeError(f"{len(r.embeddings)} vectors for {len(chunk)} texts")
                break
            except Exception as e:
                import time
                time.sleep(2 ** attempt)
                if attempt == 4:
                    raise
        for (k, _), e in zip(chunk, r.embeddings):
            have[f"{task}|{k}"] = e.values
        if i % 1000 == 0:
            CACHE.write_text(json.dumps(have))
    CACHE.write_text(json.dumps(have))
    return {k: have[f"{task}|{k}"] for k in texts}


def mem_text(m, trig, with_triggers=True):
    t = f"{m['name'].replace('-', ' ')}. {m['description']}\n{m['body'][:2000]}"
    if with_triggers and trig.get(m["id"]):
        t += "\nApplies to: " + "; ".join(trig[m["id"]])
    return t


def main():
    mems, qs, un, trig = ev.load()
    targets = [m for m in mems if m["real"]]
    distract = [m for m in mems if not m["real"]]
    mvec = embed_all({m["id"]: mem_text(m, trig) for m in mems}, "RETRIEVAL_DOCUMENT")
    qtexts = {f"{q['id']}|{k}": q.get(k, "") for q in qs for k in ("direct", "paraphrase", "oblique") if q.get(k)}
    qvec = embed_all(qtexts, "RETRIEVAL_QUERY")
    print("embedded:", len(mvec), "memories,", len(qvec), "prompts")
    norm = lambda v: v / (np.linalg.norm(v) + 1e-9)  # noqa: E731
    V = {k: norm(np.array(v, dtype=np.float32)) for k, v in mvec.items()}
    scales = sys.argv[1:] or ["own"]
    for scale in scales:
        pool_map, _ = ev.pools(targets, distract, scale)
        pool_of = lambda tid, pm=pool_map: pm[tid]  # noqa: E731
        cache = {}
        res = {g: defaultdict(lambda: defaultdict(list)) for g in ("bm25", "embed", "blend")}
        for q in qs:
            pool = pool_of(q["id"])
            key = id(pool)
            if key not in cache:
                cache.clear()                     # one pool's matrix at a time
                M = np.stack([V[m["id"]] for m in pool])
                cache[key] = (M, [m["id"] for m in pool],
                              recall.Index([ev.doc(m, trig, True) for m in pool]))
            M, ids, index = cache[key]
            for kind in ("direct", "paraphrase", "oblique"):
                qk = f"{q['id']}|{kind}"
                if qk not in qvec:
                    continue
                sims = M @ norm(np.array(qvec[qk]))
                emb_rank = [ids[i] for i in np.argsort(-sims)[:50]]
                bm_rank = [r["id"] for r in index.search(q[kind], 50)]
                rrf = defaultdict(float)
                for r, i in enumerate(emb_rank):
                    rrf[i] += 1 / (60 + r)
                for r, i in enumerate(bm_rank):
                    rrf[i] += 1 / (60 + r)
                blend = sorted(rrf, key=lambda i: -rrf[i])
                for g, ranked in (("bm25", bm_rank), ("embed", emb_rank), ("blend", blend)):
                    for k in KS:
                        res[g][kind][k].append(q["id"] in ranked[:k])
        for g, d in res.items():
            for kind, row in d.items():
                print(f"{g:6} {scale:>6} {kind:10} " + " ".join(f"@{k}:{100*sum(v)/len(v):5.1f}" for k, v in row.items()))
        print()
    print(llm.cost())


if __name__ == "__main__":
    main()
