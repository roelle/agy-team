"""Practices and facts: does giving practices their own candidate list keep
recall stable as memories pile up?

At 10,000 memories the right memory lost to ones that matched the request's
surface topic (S3, CSV, webhooks) -- and the right one was usually a
practice: how to work ("run the proof before the review"), which applies
whatever the topic, so topic similarity cannot find it. Facts are what
accumulate by the thousand; practices stay few.

This script
  1. generates practice distractors (how-to-work lessons, many roles), so
     the split is not "real = practice, distractor = fact";
  2. labels every memory practice or fact with a fast model;
  3. measures candidates as the judge would see them: the top k overall,
     plus the top p practices.

    python bench/recall/practice.py own own+1000 own+10000 [--practices 2000]
"""
import argparse
import json
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

PRACTICE_PROMPT = """Write {n} different lessons that AI agents working as {role} on a team learned about HOW TO WORK -- habits, checks, ways of reviewing, handing off, communicating, planning, verifying -- not facts about a particular system. Each a short kebab-case name, a one-line description, and a 3-6 line body with the reasoning and an example. Vary them widely. Return JSON {{"memories": [{{"name": "...", "description": "...", "body": "..."}}]}}"""
ROLES = ["software engineers", "QA reviewers", "project managers", "technical writers", "researchers",
         "data analysts", "site reliability engineers", "designers", "support engineers", "security reviewers"]
LABEL_PROMPT = """For each memory below, say whether it is a PRACTICE (a lesson about how to work -- a habit, check or way of doing things that applies across many topics) or a FACT (knowledge about a particular system, tool, dataset or situation). Return JSON {{"kinds": {{"<id>": "practice" | "fact", ...}}}}

{items}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scales", nargs="*", default=["own", "own+1000", "own+10000"])
    ap.add_argument("--practices", type=int, default=2000)
    a = ap.parse_args()
    mems, qs, un, trig = ev.load()
    per = 25
    jobs = [(i, ROLES[i % len(ROLES)]) for i in range((a.practices + per - 1) // per)]

    def gen(job):
        i, role = job
        r = llm.ask_json(PRACTICE_PROMPT.format(n=per, role=role), ev.DATA / "cache" / f"p{i}")
        return [{"id": f"practice/{i}/{j}-{m.get('name', '')}", "agent": "synthetic", "name": m.get("name", f"p{i}-{j}"),
                 "description": m.get("description", ""), "body": m.get("body", ""), "real": False, "practice": True}
                for j, m in enumerate(r.get("memories", []))]
    clean = lambda v: v.encode("utf-8", "replace").decode("utf-8") if isinstance(v, str) else v  # noqa: E731
    pract = [{k: clean(v) for k, v in m.items()} for b in llm.pmap(gen, jobs, 16) for m in b][: a.practices]
    everyone = mems + pract
    print(f"{len(pract)} practice distractors:", llm.cost())

    chunks = [everyone[i:i + 50] for i in range(0, len(everyone), 50)]

    def label(chunk):
        items = "\n\n".join(f"<id>{m['id']}</id>\n{m['name']}: {m['description']}\n{m['body'][:400]}" for m in chunk)
        r = llm.ask_json(LABEL_PROMPT.format(items=items), ev.DATA / "label-cache", thinking="low")
        return (r.get("kinds") if isinstance(r, dict) else {}) or {}
    kinds = {}
    for k in llm.pmap(label, chunks, 16):
        kinds.update(k)
    print("labelled:", llm.cost())
    real = [m for m in everyone if m["real"]]
    share = lambda xs: 100 * sum(kinds.get(m["id"]) == "practice" for m in xs) / max(1, len(xs))  # noqa: E731
    print(f"practice share: real {share(real):.0f}%, topical distractors "
          f"{share([m for m in mems if not m['real']]):.0f}%, practice distractors {share(pract):.0f}%")

    mvec = em.embed_all({m["id"]: em.mem_text(m, trig) for m in everyone}, "RETRIEVAL_DOCUMENT")
    qvec = em.embed_all({f"{q['id']}|{k}": q[k] for q in qs for k in ("direct", "paraphrase", "oblique") if q.get(k)},
                        "RETRIEVAL_QUERY")
    n = lambda v: v / (np.linalg.norm(v) + 1e-9)  # noqa: E731
    V = {k: n(np.array(v, dtype=np.float32)) for k, v in mvec.items()}
    targets = [m for m in mems if m["real"]]
    noise_all = [m for m in mems if not m["real"]] + pract
    random.Random(11).shuffle(noise_all)
    print(f"targets labelled practice: {share(targets):.0f}%")
    for scale in a.scales:
        extra = int(scale[4:]) if scale.startswith("own+") else 0
        noise = noise_all[:extra]
        groups = defaultdict(list)
        for m in targets:
            groups[(m["id"].split("/")[0], m["agent"])].append(m)
        res = defaultdict(lambda: defaultdict(list))
        for key, group in groups.items():
            pool = group + noise
            ids = [m["id"] for m in pool]
            M = np.stack([V[i] for i in ids])
            is_p = np.array([kinds.get(i) == "practice" for i in ids])
            for q in qs:
                if q["id"] not in {m["id"] for m in group}:
                    continue
                for kind in ("direct", "paraphrase", "oblique"):
                    k2 = f"{q['id']}|{kind}"
                    if k2 not in qvec:
                        continue
                    s = M @ n(np.array(qvec[k2], dtype=np.float32))
                    order = np.argsort(-s)
                    for k, p in ((10, 0), (20, 0), (30, 0), (50, 0), (100, 0), (30, 20)):
                        top = list(order[:k])
                        if p:
                            top += [i for i in order if is_p[i] and i not in set(top)][:p]
                        res[(k, p)][kind].append(q["id"] in {ids[i] for i in top})
        for (k, p), d in res.items():
            label_ = f"top{k}" + (f"+{p} practices" if p else "")
            print(f"{scale:>9} {label_:20} " + "  ".join(f"{kind} {100*sum(v)/len(v):5.1f}" for kind, v in d.items()))
        print()
    print(llm.cost())
    (ev.DATA / "kinds.json").write_text(json.dumps(kinds))


if __name__ == "__main__":
    main()
