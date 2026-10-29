"""Recall: which of an agent's own memories to put in front of it, per turn.

Agents wrote good memories and did not read them: one read 8 of its 105 in
a week, and was corrected three times in a day on lessons it had saved.
Reading depended on the agent choosing to call memory_index, and the index
was too long to read inline. So the read path searches instead: before a
turn, the incoming mail is the query, and the memories that score well are
offered with the wake.

Scoring is BM25F over a memory's fields, and the fields are not only its
own words. A memory carries **triggers**: cues written for it off the hot
path (when it is distilled or reviewed) -- the situations, concepts and
words of a request where it would help -- and **learned** cues, the words
of requests where it was needed and missed (a correction restated it).
So a prompt about variance and confidence intervals can reach a memory
about "statistics" that never uses those words.

A score is reported raw and as a fraction of the query's weight it matched
(`norm`). The bench (bench/recall) chooses the policy -- how high a norm,
how many at most -- for the property that matters most: that it behaves
the same with a hundred memories and with ten thousand.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

STOP = set("""a about above after again against all also am an and any are as at be because been
before being below between both but by can could did do does doing down during each few for from
further had has have having he her here hers him his how i if in into is it its itself just let me
more most my no nor not now of off on once only or other our ours out over own please same she
should so some such than that the their them then there these they this those through to too under
until up very was we were what when where which while who whom why will with would you your yours
yourself hey hi thanks thank ok okay need needs want wants could would should get got make made
take look looking sure quick quickly also still yet one two new use using used""".split())

_WORD = re.compile(r"[a-z0-9]+")
_SUFFIXES = ("ations", "ation", "ings", "ing", "ness", "ments", "ment", "ities", "ity",
             "ies", "ied", "ers", "er", "ed", "es", "ly", "s")


def stem(w: str) -> str:
    for suf in _SUFFIXES:
        if len(w) > len(suf) + 3 and w.endswith(suf):
            w = w[: -len(suf)]
            if suf in ("ies", "ied", "ities"):
                w += "y"
            break
    return w


def terms(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall((text or "").lower())
            if w not in STOP and len(w) > 1 and not w.isdigit()]


FIELDS = {"name": 2.0, "description": 2.5, "body": 1.0, "triggers": 1.5, "learned": 2.0}


class Index:
    """BM25F over memories. A memory is a dict with id and any of FIELDS
    (triggers and learned may be lists of strings)."""

    def __init__(self, docs: list[dict], weights: dict | None = None,
                 k1: float = 1.2, b: float = 0.75):
        self.weights = dict(FIELDS, **(weights or {}))
        self.k1, self.b = k1, b
        self.ids: list[str] = []
        self.postings: dict[str, list[tuple[int, dict]]] = defaultdict(list)
        lens = defaultdict(list)
        for i, d in enumerate(docs):
            self.ids.append(d["id"])
            per_field = {}
            for f in self.weights:
                v = d.get(f) or ""
                toks = terms(" ".join(v) if isinstance(v, list) else str(v))
                per_field[f] = Counter(toks)
                lens[f].append(len(toks))
            for t in set().union(*per_field.values()):
                self.postings[t].append((i, {f: c[t] for f, c in per_field.items() if c[t]}))
            self._lens = lens
        self.n = len(self.ids)
        self.avg = {f: (sum(v) / len(v) if v else 0.0) for f, v in lens.items()}
        self.idf = {t: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5))
                    for t, p in self.postings.items()}

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """[{id, score, norm, matched}] best first. norm is the share of the
        query's term weight the memory matched, saturated as BM25 saturates."""
        q = Counter(terms(query))
        if not q or not self.n:
            return []
        acc: dict[int, float] = defaultdict(float)
        hits: dict[int, list[str]] = defaultdict(list)
        mass = 0.0
        for t, qtf in q.items():
            idf = self.idf.get(t)
            if idf is None:
                continue
            mass += idf * (self.k1 + 1)
            for i, tf in self.postings[t]:
                w = 0.0
                for f, c in tf.items():
                    L = self._lens[f][i]
                    avg = self.avg[f] or 1.0
                    w += self.weights[f] * c / (1 - self.b + self.b * L / avg)
                acc[i] += idf * w * (self.k1 + 1) / (w + self.k1)
                hits[i].append(t)
        if not acc:
            return []
        best = sorted(acc.items(), key=lambda x: -x[1])[:limit]
        return [{"id": self.ids[i], "score": s, "norm": s / mass if mass else 0.0,
                 "matched": hits[i]} for i, s in best]


def select(results: list[dict], min_norm: float = 0.0, min_score: float = 0.0,
           rel: float = 0.0, k: int = 3) -> list[dict]:
    """The memories to offer: at most k, each above the floors, and within
    `rel` of the best (rel=0.6: at least 60% of the top score)."""
    if not results:
        return []
    top = results[0]["score"]
    return [r for r in results[:k]
            if r["norm"] >= min_norm and r["score"] >= min_score and r["score"] >= rel * top]


def calibrate(index: Index, background: list[str], quantile: float = 0.9) -> float:
    """The norm a memory must reach to be offered, set from this index: the
    `quantile` of the best norms that prompts with nothing to recall reach.
    A fixed norm meant different things at 25 memories and at 400 -- most
    unrelated prompts cleared it in a small index -- so the bar is measured
    on each index instead, and the share of unrelated prompts that bring
    in a memory stays near 1 - quantile whatever its size."""
    tops = sorted(r[0]["norm"] for r in (index.search(t, 1) for t in background) if r)
    if not tops:
        return 0.0
    i = min(len(tops) - 1, max(0, int(round(quantile * (len(tops) - 1)))))
    return tops[i]


# --- the read path: what an agent is reminded of before a turn ----------------------
#
# Measured on bench/recall (an agent's own memories; prompts written to need
# a memory directly, in other words, or only obliquely; decoys that share its
# words; unrelated prompts): embeddings proposing ten candidates and a fast
# model choosing among them kept the right memory 99/97/90% of the time,
# kept it for a decoy 3% of the time, and offered nothing for 92% of
# unrelated prompts. Word search alone could not tell a decoy from a match,
# and a fixed score threshold meant different things at 25 memories and at
# 400. So: embeddings when a key is configured, words when not; a judge when
# a model is configured; and without a judge, only a short list of names
# the agent may open, never bodies it did not ask for. With a thousand
# memories and more, ten candidates no longer reach the right one, and the
# judge held up at fifty, so the count grows with the pile (candidates_for).

import hashlib
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

API = "https://generativelanguage.googleapis.com/v1beta/"
DEFAULTS = {"embed": "gemini-embedding-2", "judge": "gemini-3.8-flash", "dims": 768,
            "candidates": "auto", "max": 3, "body_chars": 1200, "key_env": "GEMINI_API_KEY",
            "timeout": 20, "judge_via": "auto", "agy_model": "gemini-3.8-flash-low",
            "agy_binary": "agy"}

JUDGE_PROMPT = """An AI agent on a team is about to handle this message:

<message>
{message}
</message>

Here are lessons it saved in its own memory earlier. Choose the ones that would change how a careful agent handles THIS message -- a lesson that applies to what is being asked, not one that merely shares words with it. Most messages need none; choose none unless one clearly applies. Choose at most {max}.

{candidates}

Return JSON {{"apply": ["<id>", ...]}} with the ids you choose, or an empty list."""

TRIGGER_PROMPT = """For each memory below, list the cues that should bring it to mind: 15-25 short terms or phrases -- the situations where it applies, the broader concepts it belongs to, synonyms and jargon, and the words someone would use in a request where this lesson would help. Favour words that are NOT already in the memory. Return JSON {{"triggers": {{"<id>": ["...", ...], ...}}}}

{items}"""

MISS_PROMPT = """An AI agent was just told this:

<message>
{message}
</message>

Does the message correct or remind the agent of something one of these lessons it had already saved says? Answer with the id of that lesson, or null if none does.

{candidates}

Return JSON {{"restates": "<id>" or null}}"""


def config(team_dir) -> dict | None:
    """The roster's "memory_recall" settings, or None when it is off (the
    default: turn it on with "memory_recall": true or a settings object)."""
    try:
        doc = json.loads((Path(team_dir) / "roster.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cfg = doc.get("memory_recall", (doc.get("policy") or {}).get("memory_recall"))
    if not cfg:
        return None
    return {**DEFAULTS, **(cfg if isinstance(cfg, dict) else {})}


def candidates_for(n: int) -> int:
    """How many memories to put before the judge, for an agent with n.
    Reach -- the right memory among the candidates -- is what falls as
    memories pile up, while the judge stays as careful with 50 as with 10
    (bench/recall). Ten find nearly everything in a hundred-odd memories;
    a thousand needs about fifty to stay near 90% for a paraphrase."""
    return 10 if n < 150 else 20 if n < 600 else 30 if n < 2500 else 50


class Gemini:
    """Embeddings and short JSON answers over the REST API, standard
    library only. The key travels in a header, never in a URL."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.key = os.environ.get(cfg.get("key_env") or "GEMINI_API_KEY", "")

    @property
    def available(self) -> bool:
        return bool(self.key)

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "x-goog-api-key": self.key})
        with urllib.request.urlopen(req, timeout=self.cfg.get("timeout", 20)) as r:
            return json.loads(r.read())

    def embed(self, texts: list[str], task: str) -> list[list[float]]:
        out = []
        model = self.cfg["embed"]
        for i in range(0, len(texts), 100):
            r = self._post(f"models/{model}:batchEmbedContents", {"requests": [
                {"model": f"models/{model}", "content": {"parts": [{"text": t[:6000]}]},
                 "taskType": task, "outputDimensionality": self.cfg.get("dims", 768)}
                for t in texts[i:i + 100]]})
            out += [e["values"] for e in r["embeddings"]]
        return out

    def ask(self, prompt: str) -> dict:
        r = self._post(f"models/{self.cfg['judge']}:generateContent", {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "thinkingConfig": {"thinkingLevel": "low"}}})
        return _as_object(json.loads(r["candidates"][0]["content"]["parts"][0]["text"]))


class AgyJudge:
    """Short JSON answers through the agy CLI (`agy -p --json-schema`), on
    the AGY quota rather than an API key. About six seconds a call, run in
    an empty directory so the model has nothing to look at but the prompt.
    It cannot embed: with only this, candidates come from words."""

    SCHEMA = {"type": "object", "additionalProperties": True}

    def __init__(self, cfg: dict):
        import shutil
        self.cfg = cfg
        self.binary = shutil.which(cfg.get("agy_binary") or "agy")

    @property
    def available(self) -> bool:
        return bool(self.binary)

    def embed(self, texts, task):
        raise RuntimeError("the agy CLI does not embed")

    def ask(self, prompt: str) -> dict:
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([self.binary, "-p", prompt, "--model", self.cfg["agy_model"],
                                "--output-format", "json", "--disable-slash-commands",
                                "--json-schema", json.dumps(self.SCHEMA)],
                               cwd=d, capture_output=True, text=True,
                               timeout=3 * self.cfg.get("timeout", 20))
        doc = json.loads(r.stdout.strip().splitlines()[-1])
        if doc.get("status") == "ERROR" or doc.get("error"):
            # Quota and other failures come back as a document with an
            # empty response; that is no answer, not "nothing applies".
            raise RuntimeError(f"agy: {doc.get('error') or 'error'}")
        ans = doc.get("structured_output")
        if ans is None:
            ans = json.loads(doc.get("response") or "{}")
        return _as_object(ans)


class _Pair:
    """An embedder and a judge as one backend: embed() from the first,
    ask() from the second."""

    def __init__(self, embedder, judge):
        self.embedder, self.judge = embedder, judge

    @property
    def available(self) -> bool:
        return bool(self.judge and self.judge.available)

    @property
    def can_embed(self) -> bool:
        return bool(self.embedder and self.embedder.available)

    def embed(self, texts, task):
        if not self.can_embed:
            raise RuntimeError("no embedder")
        return self.embedder.embed(texts, task)

    def ask(self, prompt):
        return self.judge.ask(prompt)


def backend_for(cfg: dict):
    """Embeddings from the API when a key is set. The judge: "api" (needs the
    key), "agy" (the agy CLI), or "auto" -- the API with a key, else agy."""
    api = Gemini(cfg)
    via = cfg.get("judge_via", "auto")
    if via == "api" or (via == "auto" and api.available):
        judge = api
    else:
        judge = AgyJudge(cfg)
    return _Pair(api if api.available else None, judge)


def _as_object(ans) -> dict:
    """A model asked for {"apply": [...]} sometimes answers with the bare
    list; take that as the list, and anything else as no answer."""
    if isinstance(ans, dict):
        return ans
    if isinstance(ans, list):
        return {"apply": [x for x in ans if isinstance(x, str)]}
    return {}


def _can_embed(backend) -> bool:
    return bool(getattr(backend, "can_embed", getattr(backend, "available", False)))


def _norm(v):
    s = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / s for x in v]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


class AgentRecall:
    """One agent's memories, made searchable: a vector and triggers per
    memory, kept in <team_dir>/recall/<agent>.json and refreshed for the
    memories that changed. `backend` is a Gemini (or anything with
    available/embed/ask); without one, recall is by words alone."""

    def __init__(self, team_dir, agent: str, cfg: dict | None = None, backend=None, store=None):
        self.team_dir, self.agent = Path(team_dir), agent
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.backend = backend if backend is not None else backend_for(self.cfg)
        self._store = store
        self.path = self.team_dir / "recall" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', agent)}.json"
        self._lock = threading.Lock()

    def _open_store(self):
        if self._store is not None:
            return self._store
        from . import memory_log
        return memory_log.open_store(self.team_dir, self.agent)

    def _cache(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def memories(self) -> list[dict]:
        from .memory import extract_provenance
        store = self._open_store()
        try:
            out = []
            for e in store.index():
                raw = store.read(e.name) or ""
                when, why, body = extract_provenance(raw)
                out.append({"id": e.name, "name": e.name, "description": e.description,
                            "body": body, "when": when or getattr(e, "when", "") or ""})
            return out
        finally:
            if self._store is None:
                store.close()

    def refresh(self) -> dict:
        """Bring the cache up to date: triggers and a vector for each memory
        that is new or changed; drop the ones that are gone. Off the hot
        path when it can be (the supervisor calls it between turns)."""
        with self._lock:
            mems = self.memories()
            cache = self._cache()
            fresh = {}
            todo = []
            for m in mems:
                # "replace": a lone surrogate in a memory must not stop recall.
                h = hashlib.sha1(f"{m['description']}\n{m['body']}".encode("utf-8", "replace")).hexdigest()[:16]
                old = cache.get(m["id"])
                if old and old.get("h") == h:
                    fresh[m["id"]] = old
                else:
                    fresh[m["id"]] = {"h": h, "learned": (old or {}).get("learned", [])}
                    todo.append(m)
            if todo and self.backend.available:
                for i in range(0, len(todo), 20):
                    chunk = todo[i:i + 20]
                    try:
                        items = "\n\n".join(f"<id>{m['id']}</id>\nNAME: {m['name']}\nDESCRIPTION: "
                                            f"{m['description']}\n{m['body'][:1200]}" for m in chunk)
                        trig = self.backend.ask(TRIGGER_PROMPT.format(items=items)).get("triggers", {})
                    except Exception:
                        trig = {}
                    for m in chunk:
                        fresh[m["id"]]["triggers"] = [str(t) for t in (trig.get(m["id"]) or [])][:30]
                try:
                    if not _can_embed(self.backend):
                        raise RuntimeError("no embedder")
                    vecs = self.backend.embed([self._doc_text(m, fresh[m["id"]]) for m in todo],
                                              "RETRIEVAL_DOCUMENT")
                    for m, v in zip(todo, vecs):
                        fresh[m["id"]]["vec"] = [round(x, 5) for x in _norm(v)]
                except Exception:
                    pass
            if fresh != cache:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(fresh), encoding="utf-8")
                os.replace(tmp, self.path)
            return fresh

    @staticmethod
    def _doc_text(m: dict, c: dict) -> str:
        t = f"{m['name'].replace('-', ' ')}. {m['description']}\n{m['body'][:2000]}"
        cues = (c.get("triggers") or []) + (c.get("learned") or [])
        return t + ("\nApplies to: " + "; ".join(cues) if cues else "")

    def candidates(self, query: str, k: int | None = None) -> list[dict]:
        """The memories most like the query, best first: by meaning when
        vectors exist, else by words (with triggers and learned cues)."""
        mems = {m["id"]: m for m in self.memories()}
        k = k or self.cfg["candidates"]
        if not isinstance(k, int) or isinstance(k, bool) or k < 1:
            k = candidates_for(len(mems))
        cache = self._cache()
        with_vec = [mid for mid in mems if (cache.get(mid) or {}).get("vec")]
        if with_vec and _can_embed(self.backend):
            try:
                q = _norm(self.backend.embed([query[:6000]], "RETRIEVAL_QUERY")[0])
                ranked = sorted(with_vec, key=lambda mid: -_dot(q, cache[mid]["vec"]))[:k]
                return [{**mems[mid], "score": _dot(q, cache[mid]["vec"]), "how": "meaning"}
                        for mid in ranked]
            except Exception:
                pass
        index = Index([{**m, "name": m["name"].replace("-", " "),
                        "triggers": (cache.get(m["id"]) or {}).get("triggers", []),
                        "learned": (cache.get(m["id"]) or {}).get("learned", [])}
                       for m in mems.values()])
        return [{**mems[r["id"]], "score": r["score"], "norm": r["norm"], "how": "words"}
                for r in index.search(query, k)]

    def recall(self, query: str) -> dict:
        """{"picked": [memory], "hints": [memory], "candidates": [names], "how", "ms"}.
        A judge picks memories whose text is shown; with no judge, the few
        best word matches are only named, for the agent to open if it wants."""
        t0 = time.monotonic()
        cands = self.candidates(query)
        out = {"picked": [], "hints": [], "candidates": [c["id"] for c in cands],
               "how": cands[0]["how"] if cands else "none"}
        if cands and self.backend.available:
            try:
                text = "\n\n".join(f"<memory id=\"{c['id']}\">\n{c['name']}: {c['description']}\n"
                                   f"{c['body'][:600]}\n</memory>" for c in cands)
                ans = self.backend.ask(JUDGE_PROMPT.format(message=query[:6000], candidates=text,
                                                           max=self.cfg["max"]))
                keep = [i for i in (ans.get("apply") or []) if i in {c["id"] for c in cands}]
                out["picked"] = [c for c in cands if c["id"] in keep][: self.cfg["max"]]
                out["how"] += "+judge"
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {e}"
                out["hints"] = cands[:3]
        elif cands:
            out["hints"] = [c for c in cands if c.get("norm", 1) >= 0.2][:3]
        out["ms"] = int(1000 * (time.monotonic() - t0))
        return out

    def learn(self, memory_id: str, text: str, top: int = 12) -> list[str]:
        """Add the distinctive words of `text` -- a request where this memory
        was needed and missed -- to its learned cues, and re-embed it."""
        with self._lock:
            cache = self._cache()
            entry = cache.get(memory_id)
            if entry is None:
                return []
            mems = {m["id"]: m for m in self.memories()}
            index = Index([{**m, "triggers": (cache.get(m["id"]) or {}).get("triggers", [])}
                           for m in mems.values()])
            words = sorted({t for t in terms(text) if not (len(t) > 6 and any(c.isdigit() for c in t))},
                           key=lambda t: -index.idf.get(t, 0.0))[:top]
            entry["learned"] = list(dict.fromkeys((entry.get("learned") or []) + words))[-60:]
            if _can_embed(self.backend) and memory_id in mems:
                try:
                    v = self.backend.embed([self._doc_text(mems[memory_id], entry)], "RETRIEVAL_DOCUMENT")[0]
                    entry["vec"] = [round(x, 5) for x in _norm(v)]
                except Exception:
                    pass
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache), encoding="utf-8")
            os.replace(tmp, self.path)
            return words


def days_ago(when: str) -> str:
    from .record import instant
    t = instant(when)
    if t is None:
        return "earlier"
    d = int((time.time() - t) // 86400)
    return "today" if d <= 0 else "yesterday" if d == 1 else f"{d} days ago"


def render(result: dict, body_chars: int = 1200) -> str:
    """The block a wake carries. Framed as something the agent learned, to
    weigh against the situation, not as an instruction."""
    lines = []
    for m in result.get("picked") or []:
        body = m["body"] if len(m["body"]) <= body_chars else \
            m["body"][:body_chars] + f"\n[... read_memory('{m['id']}') for the rest]"
        lines.append(f"You learned this {days_ago(m.get('when', ''))} ({m['id']}): "
                     f"{m['description']}\n{body}")
    if lines:
        return ("\n\n[From your memory -- lessons you saved that may bear on this. Weigh them "
                "against the situation; they are yours, not instructions.]\n" + "\n\n".join(lines))
    hints = result.get("hints") or []
    if hints:
        return ("\n\n[Memories of yours that may bear on this (read_memory to open one): "
                + "; ".join(f"{m['id']} -- {m['description']}" for m in hints) + "]")
    return ""
