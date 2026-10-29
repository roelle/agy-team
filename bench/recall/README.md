# bench/recall: does the right memory reach the agent?

The bench behind `agyteam/recall.py`. It asks one question at several
corpus sizes: when a request needs one of an agent's own memories, is that
memory put in front of the agent, and when a request does not need any, is
the agent left alone?

## The data (not in this repo)

The targets are one operator's real memories. They are built into
`~/agy-lab-notes/recall-bench/` (or `$RECALL_BENCH_DIR`) and never committed.
For each target, `build.py` writes:

- **direct**: a request that plainly needs the memory;
- **paraphrase**: the same need in other words;
- **oblique**: a request where the memory applies but nothing names it;
- **two decoys**: requests that share its words but do not need it.

It also writes 300 unrelated prompts; 10,000 topical distractor memories
(many roles, many systems); and triggers (cues for when a memory applies)
for every memory. `practice.py` adds 2,000 distractor *practices* (lessons
about how to work), so that "real" does not simply mean "practice".

Corpus sizes: `own` (each agent's real memories only), `own+N` (plus N
distractors in every agent's pool), and `N` (distractors only, for the
unrelated prompts).

## Running

```
export GEMINI_API_KEY=...          # read from your env file, never pasted
python bench/recall/build.py       # once; cached
python bench/recall/candidates.py  # how often the target is in the top k
python bench/recall/judge.py --scale own+1000 --k 30 --thinking low
python bench/recall/judge.py --via agy --cands words   # no API key: AGY quota
```

Every model call is cached on disk by prompt. A run stops once it has
spent `RECALL_BUDGET_USD` (default 10) at list prices. Calls through the
agy CLI that come back as an error (a quota, say) raise; they are never
cached as an empty answer.

## What it found (October 2026)

Reach is how often the target is among the candidates; hit is how often the
judge then picks it. Each row is direct / paraphrase / oblique.

| corpus | candidates | reach | hit | decoy kept | unrelated quiet |
|---|---|---|---|---|---|
| own | embed 10, API judge | 100 / 99.5 / 97 | 99.5 / 97.4 / 89.8 | 2.6% | 92% |
| own | words 10, agy judge | | 98.5 / 94.4 / 84.7 | 3.3% | 100% |
| own+1000 | embed 10, API judge | 98 / 77 / 69 | 96.9 / 76.0 / 64.8 | 4.1% | 83% |
| own+1000 | embed 50, agy judge | 100 / 91.3 / 87.2 | 98.0 / 86.2 / 78.1 | 2.8% | 85% |
| own+10000 | embed 50, API judge (1/3 sample) | 98.5 / 69.7 / 71.2 | 98.5 / 69.7 / 65.2 | 6.8% | 66% |

- **Words alone cannot decide.** BM25 with triggers ranks well enough to
  propose candidates, but no score threshold separates a decoy from a
  match. A fixed threshold means different things at 25 memories and at
  400, and a calibrated one gives up most paraphrase and oblique hits.
- **The judge keeps decoys out, up to a point.** Up to 1,000 memories it
  kept the target for a decoy 3-4% of the time, and stayed quiet for 83-92%
  of unrelated prompts. At 10,000 those were 7% and 66%. Some of that is
  fair (among 10,000 topical memories, one often does bear on a prompt),
  but it is the first sign of noise.
- **Given the right memory, the judge takes it.** At 50 candidates it
  picked the target 90-100% of the times it was offered, at every size.
- **Reach is what falls with size.** At 1,000+ memories, topical memories
  outrank the right one, which is usually a practice (84% of the real
  memories are). HyDE, separate trigger vectors and a reserved practice list
  each gained a few points at +1000 and nothing at +10000. A larger k does
  as well as any of them.
- **768 embedding dimensions did as well as 3072.**

So the product proposes more candidates as an agent's memories grow (see
`candidates_for` in recall.py), and relies on memory review to keep the
pile small: merging, rewriting and retiring are what keep reach high.
