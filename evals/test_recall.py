"""Recall: an agent's own memories, offered with the wake.

Agents wrote good memories and did not read them -- one read 8 of 105 in a
week, and was corrected three times in a day on lessons it had saved. These
tests use a fake model backend; bench/recall measures the real one.
"""
import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from agyteam import mcp_memory, memory_log, recall  # noqa: E402
from agyteam.supervisor import Supervisor  # noqa: E402
from agyteam.transport import load as load_transport  # noqa: E402
from fixture_runner import ScriptedRunner  # noqa: E402

AGENTS = ["tpm", "coder", "qa"]


class Fake:
    """Embeddings as hashed bags of words; a judge that applies whatever
    memory shares the most words; triggers from a table."""
    available = True

    def __init__(self, triggers=None, slow=0.0):
        self.triggers, self.slow = triggers or {}, slow
        self.embedded, self.asked = 0, []

    def embed(self, texts, task):
        self.embedded += len(texts)
        out = []
        for t in texts:
            v = [0.0] * 64
            for w in recall.terms(t):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
            out.append(v)
        return out

    def ask(self, prompt):
        time.sleep(self.slow)
        self.asked.append(prompt[:40])
        if prompt.startswith("For each memory"):
            ids = [l[4:-5] for l in prompt.splitlines() if l.startswith("<id>")]
            return {"triggers": {i: self.triggers.get(i, []) for i in ids}}
        msg = prompt.split("<message>")[1].split("</message>")[0]
        words = set(recall.terms(msg))
        best, best_n = None, 1
        for block in prompt.split('<memory id="')[1:]:
            mid, body = block.split('">', 1)
            n = len(words & set(recall.terms(body)))
            if n > best_n:
                best, best_n = mid, n
        if prompt.startswith("An AI agent was just told"):
            return {"restates": best}
        return {"apply": [best] if best else []}


@pytest.fixture
def team(tmp_path, monkeypatch):
    td = tmp_path / "teams" / "demo" / "team"
    (td / "inbox").mkdir(parents=True)
    (td / "roster.json").write_text(json.dumps({"agents": [{"name": a} for a in AGENTS]}))
    monkeypatch.setenv("AGYTEAM_TEAM_DIR", str(td))
    monkeypatch.setenv("AGYTEAM_DURABLE_DIR", str(td.parent))
    monkeypatch.delenv("AGYTEAM_AGENT", raising=False)
    return td


def save(team, agent, name, desc, body):
    mcp_memory.handlers(memory_log.open_store(team, agent), team)["save_memory"](
        {"name": name, "description": desc, "content": body, "why": "test"})


def test_the_index_refreshes_only_what_changed(team):
    save(team, "coder", "flaky-ci", "CI flakes on the network tests", "Retry network tests twice before failing.")
    save(team, "coder", "stats-ab", "A/B results need a power check", "Check sample size and variance before calling a winner.")
    fake = Fake(triggers={"stats-ab": ["statistics", "p-value", "confidence interval"]})
    ar = recall.AgentRecall(team, "coder", backend=fake)
    cache = ar.refresh()
    assert fake.embedded == 2 and cache["stats-ab"]["triggers"] == ["statistics", "p-value", "confidence interval"]
    ar.refresh()
    assert fake.embedded == 2, "unchanged memories were embedded again"
    save(team, "coder", "flaky-ci", "CI flakes on the network tests", "Retry them three times now.")
    ar.refresh()
    assert fake.embedded == 3


def test_the_judge_picks_and_the_wake_says_it_is_a_lesson(team):
    save(team, "coder", "stats-ab", "A/B results need a power check",
         "Check sample size and variance before calling a winner of an experiment.")
    save(team, "coder", "flaky-ci", "CI flakes on the network tests", "Retry network tests twice.")
    ar = recall.AgentRecall(team, "coder", backend=Fake())
    ar.refresh()
    res = ar.recall("Which variant won the pricing experiment? The sample is small and the variance high.")
    assert [m["id"] for m in res["picked"]] == ["stats-ab"]
    text = recall.render(res)
    assert "You learned this today (stats-ab)" in text and "they are yours, not instructions" in text


def test_without_a_model_only_names_are_offered(team):
    class Off:
        available = False
    save(team, "coder", "stats-ab", "A/B results need a power check", "Check sample size and variance.")
    ar = recall.AgentRecall(team, "coder", backend=Off())
    res = ar.recall("is the variance too high for this sample size?")
    assert res["picked"] == [] and [m["id"] for m in res["hints"]] == ["stats-ab"]
    assert "read_memory to open one" in recall.render(res) and "Check sample size" not in recall.render(res)


def test_the_supervisor_offers_recall_with_the_wake(team, monkeypatch):
    doc = json.loads((team / "roster.json").read_text())
    doc["memory_recall"] = True
    (team / "roster.json").write_text(json.dumps(doc))
    monkeypatch.setattr(recall, "Gemini", lambda cfg: Fake())
    save(team, "coder", "stats-ab", "A/B results need a power check",
         "Check sample size and variance before calling a winner of an experiment.")
    seen = []

    class Sees(ScriptedRunner):
        def wake(self, agent, message):
            seen.append(message)
            return "ok"
    load_transport("user").send("coder", "Did variant B win the experiment? Small sample, high variance.")
    sup = Supervisor(AGENTS, Sees({}), quiet=True, require_review=False)
    try:
        sup.run_until_idle()
    finally:
        sup.close()
    assert seen and "From your memory" in seen[0] and "stats-ab" in seen[0]
    events = [json.loads(l) for l in (team / "events.jsonl").read_text().splitlines()]
    assert any(e.get("event") == "recall" and e["picked"] == ["stats-ab"] for e in events)


def test_a_correction_that_restates_an_unshown_memory_is_a_learned_miss(team, monkeypatch):
    doc = json.loads((team / "roster.json").read_text())
    doc["memory_recall"] = True
    (team / "roster.json").write_text(json.dumps(doc))
    fake = Fake()
    monkeypatch.setattr(recall, "Gemini", lambda cfg: fake)
    save(team, "coder", "real-binary", "Approve fixes only after running the real binary",
         "Run the deployed binary end to end; a green mocked test is not proof.")
    sup = Supervisor(AGENTS, ScriptedRunner({}), quiet=True, require_review=False)
    try:
        load_transport("tpm").send("coder", "Ship the parser fix today please.")      # nothing recalled
        sup.run_until_idle()
        load_transport("user").send("coder", "You approved it without running the real binary "
                                             "end to end again. A mocked test is not proof.")
        sup.run_until_idle()
    finally:
        sup.close()
    events = [json.loads(l) for l in (team / "events.jsonl").read_text().splitlines()]
    miss = [e for e in events if e.get("event") == "recall_miss"]
    assert miss and miss[0]["memory"] == "real-binary"
    learned = json.loads((team / "recall" / "coder.json").read_text())["real-binary"]["learned"]
    assert set(recall.terms("parser ship")) <= set(learned), \
        "it should learn the words of the request it missed"
    assert not any(any(c.isdigit() for c in w) for w in learned), "headers were learned as cues"


def test_recall_does_not_hold_up_the_dispatch_loop(team, monkeypatch):
    doc = json.loads((team / "roster.json").read_text())
    doc["memory_recall"] = True
    (team / "roster.json").write_text(json.dumps(doc))
    monkeypatch.setattr(recall, "Gemini", lambda cfg: Fake(slow=1.0))
    save(team, "coder", "x", "a lesson", "words words")
    load_transport("user").send("coder", "hello words")
    sup = Supervisor(AGENTS, ScriptedRunner({}), quiet=True, require_review=False)
    try:
        t0 = time.monotonic()
        sup._pass()
        assert time.monotonic() - t0 < 0.5 and "coder" in sup._preparing
    finally:
        sup.close()


def test_recall_is_off_unless_the_roster_turns_it_on(team):
    assert recall.config(team) is None
    doc = json.loads((team / "roster.json").read_text())
    doc["memory_recall"] = {"max": 2}
    (team / "roster.json").write_text(json.dumps(doc))
    assert recall.config(team)["max"] == 2 and recall.config(team)["candidates"] == "auto"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, *sys.argv[1:]]))


def test_the_judge_runs_through_the_agy_cli_without_an_api_key(team, tmp_path, monkeypatch):
    """No API key: candidates come from words, and the judge is the agy CLI
    on the AGY quota (`agy -p ... --json-schema`)."""
    import os
    import stat
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "agy"
    fake.write_text('#!/bin/sh\necho \'{"status":"SUCCESS","structured_output":{"apply":["stats-ab"]}}\'\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = {**recall.DEFAULTS}
    b = recall.backend_for(cfg)
    assert isinstance(b.judge, recall.AgyJudge) and not b.can_embed and b.available
    save(team, "coder", "stats-ab", "A/B results need a power check", "Check sample size and variance.")
    res = recall.AgentRecall(team, "coder", cfg).recall("is the variance too high for this sample?")
    assert [m["id"] for m in res["picked"]] == ["stats-ab"] and res["how"] == "words+judge"


def test_an_agy_quota_error_falls_back_to_names_not_silence(team, tmp_path, monkeypatch):
    """Out of quota, agy answers with an error document and an empty
    response. That is no judgment: the agent still gets the names."""
    import os
    import stat
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "agy"
    fake.write_text('#!/bin/sh\necho \'{"status":"ERROR","response":"","error":"Individual quota reached."}\'\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = {**recall.DEFAULTS}
    save(team, "coder", "stats-ab", "A/B results need a power check", "Check sample size and variance.")
    res = recall.AgentRecall(team, "coder", cfg).recall("is the variance too high for this sample?")
    assert not res["picked"] and [m["id"] for m in res["hints"]] == ["stats-ab"]


def test_with_a_key_the_api_embeds_and_judges_unless_told_to_judge_with_agy(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    b = recall.backend_for({**recall.DEFAULTS})
    assert isinstance(b.judge, recall.Gemini) and b.can_embed
    b = recall.backend_for({**recall.DEFAULTS, "judge_via": "agy"})
    assert isinstance(b.judge, recall.AgyJudge) and b.can_embed


def test_more_memories_get_more_candidates_unless_the_roster_says_how_many(team):
    assert recall.candidates_for(40) == 10 and recall.candidates_for(1000) == 30
    assert recall.candidates_for(10_000) == 50
    for i in range(12):
        save(team, "coder", f"lesson-{i}", f"lesson {i} about parsers", "Check the parser.")
    auto = recall.AgentRecall(team, "coder", {**recall.DEFAULTS}, backend=Fake())
    assert len(auto.candidates("parser")) == 10
    fixed = recall.AgentRecall(team, "coder", {**recall.DEFAULTS, "candidates": 4}, backend=Fake())
    assert len(fixed.candidates("parser")) == 4


def test_the_record_sums_recall_per_day_and_per_memory(team):
    import time as _time
    from agyteam.record import Record
    now = _time.strftime("%Y-%m-%d %H:%M:%S")
    rows = [{"event": "recall", "agent": "coder", "picked": ["stats-ab"], "hints": []},
            {"event": "recall", "agent": "coder", "picked": [], "hints": []},
            {"event": "recall", "agent": "qa", "picked": ["other"], "hints": []},
            {"event": "recall_miss", "agent": "coder", "memory": "parser-ship",
             "correction": "ship the parser", "learned": ["parser"]}]
    with (team / "events.jsonl").open("a") as f:
        for r in rows:
            f.write(json.dumps({"ts": now, **r}) + "\n")
    st = Record(team).recall_stats("coder")
    today = st["days"][-1]
    assert len(st["days"]) == 14 and today["day"] == now[:10]
    assert (today["wakes"], today["offered"], today["memories"], today["misses"]) == (2, 1, 1, 1)
    assert st["memories"]["stats-ab"]["offered"] == 1 and st["memories"]["parser-ship"]["missed"] == 1
    assert "other" not in st["memories"] and st["misses"][0]["memory"] == "parser-ship"
