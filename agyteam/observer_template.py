"""Skeleton for a custom observer backend. Copy, rename, fill in.

Fourth seam, matching transport_template, memory_template, and runner_template.
Use this when you want to stream telemetry, costs, and supervisor events to an
external observability service (e.g. OpenTelemetry, Datadog, Prometheus, BigQuery,
or internal metrics pipelines) instead of or in addition to local JSONL files.

    cp agyteam/observer_template.py example_observer.py
    # implement the three recording methods and events query
    export AGYTEAM_OBSERVER=example_observer:MyObserver
    export AGYTEAM_OBSERVER_CONFIG='{"endpoint": "..."}'
    .venv/bin/python evals/test_observer.py

Contract requirements:
- Must inherit from agyteam.observer.Observer.
- record_turn, record_failure, record_episode must be implemented.
- Recording failures must not crash the caller (safe exception handling).
- Absent token fields should remain None rather than 0.
- events(event_type=None) returns list of event dicts or []. Missing storage is normal.
"""
from .observer import Observer


class CustomObserver(Observer):
    label = "custom"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        # self.config holds parsed AGYTEAM_OBSERVER_CONFIG JSON.
        # Initialize client/connection here:
        # self.client = MetricClient(endpoint=self.config.get("endpoint"))
        raise NotImplementedError("implement CustomObserver before using it")

    def record_turn(self, agent: str, conversation: str, duration_s: float,
                    input_tokens: int | None = None,
                    output_tokens: int | None = None,
                    cache_read_tokens: int | None = None,
                    total_tokens: int | None = None,
                    model: str | None = None,
                    **kwargs) -> None:
        """Record a completed agent turn.

        Keep absent token counts as None. Never raise: turn execution must
        not fail because telemetry failed.
        """
        raise NotImplementedError

    def record_failure(self, agent: str, conversation: str, error: str,
                       duration_s: float | None = None, **kwargs) -> None:
        """Record a turn failure with error description."""
        raise NotImplementedError

    def record_episode(self, turns: int, stopped_reason: str,
                       reviewed: bool = False,
                       duration_s: float | None = None, **kwargs) -> None:
        """Record the conclusion of an episode dispatched by the supervisor."""
        raise NotImplementedError

    def events(self, event_type: str | None = None) -> list[dict]:
        """Return recorded events, optionally filtered by event_type.

        Return [] if empty or not yet created.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Flush and release resources."""
        pass
