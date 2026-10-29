"""A small Gemini client for the recall bench: JSON out, cached on disk, and
counted, so a re-run costs nothing and a report can say what a run cost.

The key comes from GEMINI_API_KEY, else from the env file BENCH_ENV_FILE
names, read into this process only.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MODEL = os.environ.get("RECALL_BENCH_MODEL", "gemini-3.8-flash")
# Per million tokens; a rough figure for the report, not a bill.
PRICE_IN, PRICE_OUT = float(os.environ.get("RECALL_PRICE_IN", "0.30")), float(os.environ.get("RECALL_PRICE_OUT", "2.50"))

_lock = threading.Lock()
USAGE = {"calls": 0, "cached": 0, "in": 0, "out": 0}
# A run stops itself before it spends more than this (US dollars, at list
# prices): a prepaid balance went negative because nothing here was counting.
BUDGET = float(os.environ.get("RECALL_BUDGET_USD", "10"))


def spent() -> float:
    return USAGE["in"] / 1e6 * PRICE_IN + USAGE["out"] / 1e6 * PRICE_OUT


class OverBudget(RuntimeError):
    pass


def _key() -> str:
    k = os.environ.get("GEMINI_API_KEY")
    if k:
        return k
    env = os.environ.get("BENCH_ENV_FILE")
    for line in (Path(env).read_text().splitlines() if env else []):
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no GEMINI_API_KEY (set it, or BENCH_ENV_FILE to an env file holding it)")


_client = None


def client():
    global _client
    if _client is None:
        from google import genai
        _client = genai.Client(api_key=_key())
    return _client


def ask_json(prompt: str, cache_dir: Path, model: str = MODEL, retries: int = 4,
             thinking: str | None = None):
    """The model's answer to `prompt`, parsed as JSON; from the cache if seen.
    `thinking`: a thinking level ("low", ...) or None for the model's default."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    # The thinking level joins the key only when set, so answers cached
    # before the option existed are still found.
    key = hashlib.sha256((f"{model}\n{thinking}\n{prompt}" if thinking
                          else f"{model}\n{prompt}").encode()).hexdigest()[:32]
    hit = cache_dir / f"{key}.json"
    if hit.exists():
        with _lock:
            USAGE["cached"] += 1
        return json.loads(hit.read_text())["answer"]
    if spent() >= BUDGET:
        raise OverBudget(f"this run has spent ~${spent():.2f} of its ${BUDGET:.2f} budget "
                         f"(RECALL_BUDGET_USD); stopping before the next call")
    from google.genai import types
    last = None
    for attempt in range(retries):
        try:
            r = client().models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", temperature=0.7,
                    **({"thinking_config": types.ThinkingConfig(thinking_level=thinking)} if thinking else {})))
            answer = json.loads(r.text)
            u = r.usage_metadata
            with _lock:
                USAGE["calls"] += 1
                USAGE["in"] += u.prompt_token_count or 0
                USAGE["out"] += (u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
            hit.write_text(json.dumps({"model": model, "answer": answer}))
            return answer
        except Exception as e:                       # rate limit, bad JSON: back off, retry
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"model call failed: {last}")


def pmap(fn, items, workers: int = 8):
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


def cost() -> str:
    usd = spent()
    return (f"{USAGE['calls']} calls ({USAGE['cached']} cached), {USAGE['in']:,} in / "
            f"{USAGE['out']:,} out tokens, ~${usd:.2f} at {MODEL} list prices")


JUDGE_SCHEMA = None


def ask_agy(prompt: str, cache_dir: Path, model: str = "gemini-3.8-flash-low",
            schema: dict | None = None, timeout: int = 120):
    """The same, through the agy CLI (`agy -p`), on the AGY quota instead of
    an API key. Cached like ask_json. Run from a scratch directory so the
    agent has nothing to look at but the prompt."""
    import subprocess
    import tempfile
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"agy\n{model}\n{prompt}".encode()).hexdigest()[:32]
    hit = cache_dir / f"{key}.json"
    if hit.exists():
        with _lock:
            USAGE["cached"] += 1
        return json.loads(hit.read_text())["answer"]
    cmd = ["agy", "-p", prompt, "--model", model, "--output-format", "json", "--disable-slash-commands"]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    last = None
    for attempt in range(3):
        with tempfile.TemporaryDirectory() as d:
            try:
                r = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=timeout)
                doc = json.loads(r.stdout.strip().splitlines()[-1])
                if doc.get("status") == "ERROR" or doc.get("error"):
                    if "quota" in str(doc.get("error", "")).lower():
                        raise OverBudget(f"agy: {doc.get('error')}")
                    raise RuntimeError(f"agy: {doc.get('error')}")
                answer = doc.get("structured_output") or json.loads(doc.get("response") or "{}")
                u = doc.get("usage") or {}
                with _lock:
                    USAGE["calls"] += 1
                    USAGE["in"] += u.get("input_tokens", 0)
                    USAGE["out"] += u.get("output_tokens", 0) + u.get("thinking_tokens", 0)
                hit.write_text(json.dumps({"model": f"agy:{model}", "answer": answer}))
                return answer
            except OverBudget:
                raise
            except Exception as e:
                last = e
                time.sleep(2 ** attempt)
    raise RuntimeError(f"agy call failed: {last}")
