"""How often a target is among the top k candidates, before any threshold:
what a second stage (a judge) would have to choose from."""
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import eval as ev  # noqa: E402
from agyteam import recall  # noqa: E402

KS = (1, 3, 5, 10, 20)


def at_k(index_for, qs, kinds=("direct", "paraphrase", "oblique")):
    got = defaultdict(lambda: defaultdict(list))
    for q in qs:
        index = index_for(q["id"])
        for kind in kinds:
            ids = [r["id"] for r in index.search(q.get(kind, ""), max(KS))]
            for k in KS:
                got[kind][k].append(q["id"] in ids[:k])
    return {kind: {k: 100 * sum(v) / len(v) for k, v in d.items()} for kind, d in got.items()}


def main():
    mems, qs, un, trig = ev.load()
    targets = [m for m in mems if m["real"]]
    distract = [m for m in mems if not m["real"]]
    scales = ["own"] + [s for s in sys.argv[1:] if int(s) <= len(mems)]
    for variant in ("content", "triggers"):
        use = variant == "triggers"
        for scale in scales:
            if scale == "own":
                groups = defaultdict(list)
                for m in targets:
                    groups[(m["id"].split("/")[0], m["agent"])].append(m)
                built = {key: recall.Index([ev.doc(m, trig, use) for m in g]) for key, g in groups.items()}
                owner = {m["id"]: (m["id"].split("/")[0], m["agent"]) for m in targets}
                res = at_k(lambda tid: built[owner[tid]], qs)
            else:
                pool = targets + random.Random(11).sample(distract, max(0, int(scale) - len(targets)))
                index = recall.Index([ev.doc(m, trig, use) for m in pool])
                res = at_k(lambda tid: index, qs)
            for kind, row in res.items():
                print(f"{variant:9} {scale:>6} {kind:10} " + " ".join(f"@{k}:{v:5.1f}" for k, v in row.items()))
        print()


if __name__ == "__main__":
    main()
