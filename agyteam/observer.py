"""Pluggable observability seam.

Fourth seam, following the exact pattern of transport.py (how messages move),
memory.py (where knowledge lives), and runner.py (how agents are woken).
Observability records turn completions, turn failures, and episode endings across
any runner (AgyRunner, SdkRunner, or custom).

    AGYTEAM_OBSERVER=agyteam.observer_file:FileObserver     # default
    AGYTEAM_OBSERVER_CONFIG='{"team_dir": "..."}'          # optional, JSON

Why this exists: we need to account for token usage, latency, and costs across
both CLI-run and SDK-run teams uniformly, and track the trajectory of entire
episodes without burdening agents with token-consuming MCP introspection tools.
Observability is runner/supervisor-side plumbing.

Events recorded:
- turn: agent, conversation, duration_s, input_tokens, output_tokens,
  cache_read_tokens, total_tokens, model.
- failure: agent, conversation, error, duration_s.
- episode: turns, stopped_reason, reviewed, duration_s.

Read back per-agent and per-episode totals and cost estimates:
    python -m agyteam.observer
    python -m agyteam.supervisor --cost
"""
import importlib
import json
import os
from abc import ABC, abstractmethod

DEFAULT_OBSERVER = "agyteam.observer_file:FileObserver"


class Observer(ABC):
    """Observer interface for recording agent and supervisor events."""

    label = "observer"

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    def record_turn(self, agent: str, conversation: str, duration_s: float,
                    input_tokens: int | None = None,
                    output_tokens: int | None = None,
                    cache_read_tokens: int | None = None,
                    total_tokens: int | None = None,
                    model: str | None = None,
                    **kwargs) -> None:
        """Record a completed agent turn.

        Absent token fields should be recorded as None rather than fabricated
        zeros to prevent skewing comparisons between runtimes.
        """

    @abstractmethod
    def record_failure(self, agent: str, conversation: str, error: str,
                       duration_s: float | None = None, **kwargs) -> None:
        """Record a turn failure with error details."""

    @abstractmethod
    def record_episode(self, turns: int, stopped_reason: str,
                       reviewed: bool = False,
                       duration_s: float | None = None, **kwargs) -> None:
        """Record the conclusion of a supervisor episode."""

    def events(self, event_type: str | None = None) -> list[dict]:
        """Return recorded events, optionally filtered by event_type.

        Returns [] when no events have been recorded. Must not raise on missing data.
        """
        return []

    def summary(self) -> dict:
        """Aggregate totals per-agent and per-episode.

        Returns a structured dict with per-agent activity, episode breakdown,
        and overall totals including estimated USD costs from PRICING.
        """
        all_events = self.events()
        agent_data: dict[str, dict] = {}
        episodes: list[dict] = []
        failures = 0
        total_duration = 0.0
        total_input = 0
        total_output = 0
        total_cache = 0
        total_tokens = 0
        total_cost = 0.0
        turns_count = 0
        has_unknown_tokens = False

        for ev in all_events:
            etype = ev.get("event")
            if etype == "turn":
                turns_count += 1
                agent = ev.get("agent", "unknown")
                if agent not in agent_data:
                    agent_data[agent] = {
                        "turns": 0,
                        "failures": 0,
                        "duration_s": 0.0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "total_tokens": 0,
                        "cost_usd": 0.0,
                        "unknown_tokens_turns": 0,
                    }
                ag = agent_data[agent]
                ag["turns"] += 1
                dur = ev.get("duration_s") or 0.0
                ag["duration_s"] += dur
                total_duration += dur

                in_tok = ev.get("input_tokens")
                out_tok = ev.get("output_tokens")
                cached_tok = ev.get("cache_read_tokens")
                tot_tok = ev.get("total_tokens")
                model = ev.get("model")

                if in_tok is None and out_tok is None and tot_tok is None:
                    ag["unknown_tokens_turns"] += 1
                    has_unknown_tokens = True
                else:
                    if in_tok is not None:
                        ag["input_tokens"] += in_tok
                        total_input += in_tok
                    if out_tok is not None:
                        ag["output_tokens"] += out_tok
                        total_output += out_tok
                    if cached_tok is not None:
                        ag["cache_read_tokens"] += cached_tok
                        total_cache += cached_tok
                    if tot_tok is not None:
                        ag["total_tokens"] += tot_tok
                        total_tokens += tot_tok
                    elif in_tok is not None or out_tok is not None:
                        calc_tot = (in_tok or 0) + (out_tok or 0)
                        ag["total_tokens"] += calc_tot
                        total_tokens += calc_tot

                cost = calculate_cost(in_tok, out_tok, model)
                ag["cost_usd"] += cost
                total_cost += cost

            elif etype == "failure":
                failures += 1
                agent = ev.get("agent", "unknown")
                if agent not in agent_data:
                    agent_data[agent] = {
                        "turns": 0,
                        "failures": 0,
                        "duration_s": 0.0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "total_tokens": 0,
                        "cost_usd": 0.0,
                        "unknown_tokens_turns": 0,
                    }
                agent_data[agent]["failures"] += 1
                dur = ev.get("duration_s") or 0.0
                agent_data[agent]["duration_s"] += dur
                total_duration += dur

            elif etype == "episode":
                episodes.append({
                    "ts": ev.get("ts"),
                    "turns": ev.get("turns", 0),
                    "stopped_reason": ev.get("stopped_reason", ""),
                    "reviewed": ev.get("reviewed", False),
                    "duration_s": ev.get("duration_s", 0.0),
                })

        for ag in agent_data.values():
            ag["duration_s"] = round(ag["duration_s"], 2)
            ag["cost_usd"] = round(ag["cost_usd"], 4)

        return {
            "agents": agent_data,
            "episodes": episodes,
            "totals": {
                "turns": turns_count,
                "failures": failures,
                "duration_s": round(total_duration, 2),
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cache_read_tokens": total_cache,
                "total_tokens": total_tokens,
                "cost_usd": round(total_cost, 4),
                "has_unknown_tokens": has_unknown_tokens,
            },
        }

    def close(self) -> None:
        """Release resources. Default is a no-op."""


def calculate_cost(input_tokens: int | None, output_tokens: int | None,
                   model: str | None = None) -> float:
    """Calculate estimated USD cost for input/output tokens according to PRICING."""
    try:
        from .config import DEFAULT_PRICE, PRICING
    except ImportError:
        PRICING = {}
        DEFAULT_PRICE = (2.00, 12.00)
    price = PRICING.get(model, DEFAULT_PRICE) if model else DEFAULT_PRICE
    in_cost = ((input_tokens or 0) / 1_000_000) * price[0]
    out_cost = ((output_tokens or 0) / 1_000_000) * price[1]
    return in_cost + out_cost


def format_summary(data: dict) -> str:
    """Format summary data into an easily readable terminal report."""
    lines = ["=== Team Observability & Cost Summary ==="]
    agents = data.get("agents", {})
    if not agents:
        lines.append("(no agent activity recorded)")
    else:
        lines.append("\nPer-Agent Activity:")
        lines.append(f"  {'Agent':<12} {'Turns':<7} {'Fails':<7} {'Duration':<10} {'Tokens (In / Out / Cached / Total)':<38} {'Est. Cost':<10}")
        lines.append("  " + "-" * 86)
        for name, ag in sorted(agents.items()):
            dur = f"{ag['duration_s']:.1f}s"
            tokens_str = f"{ag['input_tokens']} / {ag['output_tokens']} / {ag['cache_read_tokens']} / {ag['total_tokens']}"
            if ag.get("unknown_tokens_turns", 0) > 0:
                tokens_str += f" (+{ag['unknown_tokens_turns']} unk)"
            cost_str = f"${ag['cost_usd']:.4f}"
            lines.append(f"  {name:<12} {ag['turns']:<7} {ag['failures']:<7} {dur:<10} {tokens_str:<38} {cost_str:<10}")

    episodes = data.get("episodes", [])
    lines.append("\nEpisodes:")
    if not episodes:
        lines.append("  (no episodes recorded)")
    else:
        lines.append(f"  Total episodes: {len(episodes)}")
        reviewed_cnt = sum(1 for ep in episodes if ep.get("reviewed"))
        lines.append(f"  Reviewed episodes: {reviewed_cnt}/{len(episodes)}")
        for i, ep in enumerate(episodes, 1):
            rev_label = "reviewed" if ep.get("reviewed") else "unreviewed"
            dur_val = ep.get("duration_s")
            dur_str = f" in {dur_val:.1f}s" if dur_val is not None else ""
            lines.append(f"  #{i}: {ep.get('turns', 0)} turns, stopped: {ep.get('stopped_reason', 'unknown')} ({rev_label}){dur_str}")

    totals = data.get("totals", {})
    lines.append("\nTotals:")
    lines.append(f"  Turns:       {totals.get('turns', 0)}")
    lines.append(f"  Failures:    {totals.get('failures', 0)}")
    lines.append(f"  Duration:    {totals.get('duration_s', 0.0):.2f}s")
    lines.append(f"  Tokens:      {totals.get('input_tokens', 0)} input, {totals.get('output_tokens', 0)} output, "
                 f"{totals.get('cache_read_tokens', 0)} cached ({totals.get('total_tokens', 0)} total)")
    lines.append(f"  Est. Cost:   ${totals.get('cost_usd', 0.0):.4f}")
    return "\n".join(lines)


def load(spec: str | None = None, config: dict | None = None) -> Observer:
    """Instantiate the configured observer. Fails loudly on misconfiguration."""
    spec = spec or os.environ.get("AGYTEAM_OBSERVER") or DEFAULT_OBSERVER
    if config is None:
        raw = os.environ.get("AGYTEAM_OBSERVER_CONFIG", "")
        try:
            config = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise SystemExit(f"AGYTEAM_OBSERVER_CONFIG is not valid JSON: {e}")
    if ":" not in spec:
        raise SystemExit(f"AGYTEAM_OBSERVER must be 'module:Class', got {spec!r}")
    mod_name, _, cls_name = spec.partition(":")
    try:
        cls = getattr(importlib.import_module(mod_name), cls_name)
    except (ImportError, AttributeError) as e:
        raise SystemExit(f"cannot load observer {spec!r}: {e}")
    if not (issubclass(cls, Observer) or any(b.__name__ == "Observer" for b in cls.__mro__)):
        raise SystemExit(f"{spec} is not an agyteam.observer.Observer subclass")
    return cls(config)


# Wire cost and usage reporting to both `python -m agyteam.observer` and supervisor `--cost`.
# Rationale:
# Having `__main__` on `observer.py` allows offline inspection of any team directory without
# starting a supervisor loop. Having `--cost` on `supervisor.py` gives operators immediate,
# turnkey cost accounting directly from the supervisor CLI command.
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        prog="agyteam.observer",
        description="Display team usage and cost summary")
    parser.add_argument("--team-dir", help="Path to team directory")
    parser.add_argument("--json", action="store_true", help="Output summary as JSON")
    args = parser.parse_args()
    if args.team_dir:
        os.environ["AGYTEAM_TEAM_DIR"] = args.team_dir
    import agyteam.observer as _obs
    obs = _obs.load()
    if args.json:
        print(json.dumps(obs.summary(), indent=2))
    else:
        print(_obs.format_summary(obs.summary()))
