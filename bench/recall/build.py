"""Build the recall bench's data: real memories as targets, test prompts for
each, unrelated prompts, distractor memories, and triggers for all of them.

    python bench/recall/build.py [--distractors 10000] [--teams-root ~/agy-teams]

Everything lands in RECALL_BENCH_DIR (default ~/agy-lab-notes/recall-bench),
outside the repository: the targets are a person's real agent memories.
Model answers are cached there too, so a re-run costs nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))

import llm  # noqa: E402

DATA = Path(os.environ.get("RECALL_BENCH_DIR", "~/agy-lab-notes/recall-bench")).expanduser()
CACHE = DATA / "cache"


def real_memories(root: Path) -> list[dict]:
    from agyteam.memory import extract_provenance
    from agyteam.memory_file import FileMemory
    out = []
    for team in sorted(root.iterdir()):
        agents = team / "agents"
        for ag in sorted(agents.iterdir()) if agents.is_dir() else []:
            if not (ag / "memory").is_dir():
                continue
            store = FileMemory(ag.name, {"workspace": str(ag)})
            for e in store.index():
                when, why, body = extract_provenance(store.read(e.name) or "")
                if body.strip():
                    out.append({"id": f"{team.name}/{ag.name}/{e.name}", "agent": ag.name,
                                "name": e.name, "description": e.description, "body": body,
                                "why": why or "", "when": when or "", "real": True})
    return out


QUERY_PROMPT = """You are building a test of an AI agent's memory recall. The agent works on a team (software, operations, research, writing) and wrote this lesson into its own memory:

NAME: {name}
DESCRIPTION: {description}
LESSON:
{body}

Write messages the agent might receive LATER -- from its human operator or a teammate -- where recalling this lesson would make its next turn better. Write them as real requests, about a concrete piece of work, not about the lesson. Return JSON:
{{
 "direct": "a request that naturally uses some of the lesson's own key words",
 "paraphrase": "a request where the lesson applies, using NONE of these words: {avoid}",
 "oblique": "a request where the lesson applies but only someone who understood it would notice -- a different surface situation, different vocabulary",
 "decoys": ["a request that shares surface words with the lesson but where the lesson does NOT apply", "another such request"]
}}"""

UNRELATED_PROMPT = """Write {n} realistic messages an AI agent on a mixed software/operations/research team might receive from its operator or a teammate, on varied everyday work: {topics}. Each 1-3 sentences, concrete. Return JSON {{"messages": ["..."]}}"""

DISTRACTOR_PROMPT = """Write {n} different lessons that AI agents on a team learned and saved to their memories. Domain for this batch: {domain}. Make them concrete and specific, like real post-mortem notes: a short kebab-case name, a one-line description, and a body of 3-8 lines with the details (commands, file names, numbers, the reason). Vary the sub-topics widely. Return JSON {{"memories": [{{"name": "...", "description": "...", "body": "..."}}]}}"""

TRIGGER_PROMPT = """For each memory below, list the cues that should bring it to mind: 15-25 short terms or phrases -- the situations where it applies, the broader concepts it belongs to, synonyms and jargon, and the words someone would use in a request where this lesson would help. Favour words that are NOT already in the memory. Return JSON {{"triggers": {{"<id>": ["...", ...], ...}}}}

{items}"""

DOMAINS = [
    "Python packaging and virtualenvs", "SQL databases and migrations", "Kubernetes deployments",
    "CI pipelines and flaky tests", "frontend CSS and layout", "React state management",
    "data cleaning with pandas", "statistics and A/B testing", "machine learning training runs",
    "GPU and CUDA environments", "network debugging and DNS", "TLS certificates and security",
    "git workflows and merge conflicts", "API design and versioning", "technical writing and docs",
    "customer support replies", "project planning and estimates", "code review etiquette",
    "logging and observability", "incident response", "cloud billing and cost control",
    "spreadsheet modelling and finance", "legal contract review", "scientific literature search",
    "signal processing and filters", "embedded C and firmware", "Rust ownership and lifetimes",
    "Go concurrency", "shell scripting pitfalls", "time zones and date handling",
    "Unicode and text encoding", "caching and invalidation", "message queues and retries",
    "access control and permissions", "UX research and interviews", "accessibility",
    "performance profiling", "load testing", "backup and restore", "hiring and interviewing",
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--teams-root", default="~/agy-teams")
    ap.add_argument("--distractors", type=int, default=10000)
    ap.add_argument("--unrelated", type=int, default=300)
    ap.add_argument("--workers", type=int, default=12)
    a = ap.parse_args(argv)
    DATA.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(7)

    targets = real_memories(Path(a.teams_root).expanduser())
    print(f"{len(targets)} real memories")

    def queries(m):
        words = sorted({w for w in (m["name"].replace("-", " ") + " " + m["description"]).lower().split()
                        if len(w) > 4})[:12]
        q = llm.ask_json(QUERY_PROMPT.format(name=m["name"], description=m["description"],
                                             body=m["body"][:2500], avoid=", ".join(words)), CACHE)
        return {"id": m["id"], **q}
    qs = llm.pmap(queries, targets, a.workers)
    print("queries:", llm.cost())

    topics = ["deploys", "bugs", "meetings", "reports", "data requests", "docs", "hiring",
              "budgets", "customer questions", "infrastructure", "research", "scheduling"]
    batches = [llm.ask_json(UNRELATED_PROMPT.format(n=25, topics=", ".join(rnd.sample(topics, 4))),
                            CACHE / f"u{i}") for i in range(max(1, a.unrelated // 25))]
    unrelated = [m for b in batches for m in b.get("messages", [])][: a.unrelated]
    print(f"{len(unrelated)} unrelated prompts:", llm.cost())

    per = 25
    jobs = [(i, DOMAINS[i % len(DOMAINS)]) for i in range((a.distractors + per - 1) // per)]
    def gen(job):
        i, dom = job
        r = llm.ask_json(DISTRACTOR_PROMPT.format(n=per, domain=dom), CACHE / f"d{i}")
        return [{"id": f"synthetic/{i}/{j}-{m.get('name', '')}", "agent": "synthetic",
                 "name": m.get("name", f"m{i}-{j}"), "description": m.get("description", ""),
                 "body": m.get("body", ""), "real": False, "domain": dom}
                for j, m in enumerate(r.get("memories", []))]
    distractors = [m for batch in llm.pmap(gen, jobs, a.workers) for m in batch][: a.distractors]
    # A model can emit a lone surrogate; it cannot be encoded, so clean it out.
    clean = lambda v: v.encode("utf-8", "replace").decode("utf-8") if isinstance(v, str) else v  # noqa: E731
    distractors = [{k: clean(v) for k, v in m.items()} for m in distractors]
    print(f"{len(distractors)} distractors:", llm.cost())

    everyone = targets + distractors
    chunks = [everyone[i:i + 20] for i in range(0, len(everyone), 20)]
    def trig(chunk):
        items = "\n\n".join(f"<id>{m['id']}</id>\nNAME: {m['name']}\nDESCRIPTION: {m['description']}\n"
                            f"{m['body'][:1200]}" for m in chunk)
        return llm.ask_json(TRIGGER_PROMPT.format(items=items), CACHE).get("triggers", {})
    triggers = {}
    for t in llm.pmap(trig, chunks, a.workers):
        triggers.update({k: v for k, v in t.items() if isinstance(v, list)})
    print(f"triggers for {len(triggers)} of {len(everyone)}:", llm.cost())

    (DATA / "memories.json").write_text(json.dumps(everyone))
    (DATA / "queries.json").write_text(json.dumps(qs))
    (DATA / "unrelated.json").write_text(json.dumps(unrelated))
    (DATA / "triggers.json").write_text(json.dumps(triggers))
    print("wrote", DATA, "|", llm.cost())


if __name__ == "__main__":
    main()
